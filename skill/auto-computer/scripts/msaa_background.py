"""Read-only, target-window-scoped MSAA lookup for Windows.

This module intentionally exposes only exact name/role discovery. It never
reads accValue, invokes an accessible action, focuses a window, sends input,
reads/writes the clipboard, or captures pixels.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _common import ensure_comtypes_overlay
from desktop_policy import ensure_unprotected_pid

OBJID_CLIENT = 0xFFFFFFFC
CHILDID_SELF = 0
VT_I4 = 3
VT_DISPATCH = 9

# MSAA role identifiers from oleacc.h. Canonical names make role lookup stable
# across Windows display languages.
ROLE_NAMES = {
    1: "titlebar", 2: "menubar", 3: "scrollbar", 4: "grip", 5: "sound",
    6: "cursor", 7: "caret", 8: "alert", 9: "window", 10: "client",
    11: "menupopup", 12: "menuitem", 13: "tooltip", 14: "application",
    15: "document", 16: "pane", 17: "chart", 18: "dialog", 19: "border",
    20: "grouping", 21: "separator", 22: "toolbar", 23: "statusbar",
    24: "table", 25: "columnheader", 26: "rowheader", 27: "column",
    28: "row", 29: "cell", 30: "link", 31: "helpballoon", 32: "character",
    33: "list", 34: "listitem", 35: "outline", 36: "outlineitem",
    37: "pagetab", 38: "propertypage", 39: "indicator", 40: "graphic",
    41: "statictext", 42: "text", 43: "pushbutton", 44: "checkbutton",
    45: "radiobutton", 46: "combobox", 47: "droplist", 48: "progressbar",
    49: "dial", 50: "hotkeyfield", 51: "slider", 52: "spinbutton",
    53: "diagram", 54: "animation", 55: "equation", 56: "buttondropdown",
    57: "buttonmenu", 58: "buttondropdowngrid", 59: "whitespace",
    60: "pagetablist", 61: "clock", 62: "splitbutton", 63: "ipaddress",
    64: "outlinebutton",
}
ROLE_ALIASES = {
    "button": "pushbutton", "edit": "text", "textbox": "text",
    "static": "statictext", "label": "statictext", "check box": "checkbutton",
    "checkbox": "checkbutton", "radio button": "radiobutton",
    "radio": "radiobutton", "combo box": "combobox", "list item": "listitem",
    "menu item": "menuitem", "tab": "pagetab", "tab list": "pagetablist",
}


@dataclass
class WindowInfo:
    hwnd: str
    pid: int
    class_name: str


@dataclass
class Match:
    hwnd: str
    pid: int
    class_name: str
    name: str | None
    role: str | None
    role_id: int | None
    child_id: int
    path: list[str]


def _parse_int(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use decimal or 0x-prefixed integer") from exc


def _normal_name(value: str) -> str:
    return value.casefold()


def _normal_role(value: str) -> str:
    text = value.casefold().strip().replace("role_system_", "")
    text = text.replace("_", "").replace("-", "")
    return ROLE_ALIASES.get(text, text)


def _windows_api() -> tuple[Any, Any, Any]:
    if os.name != "nt":
        raise RuntimeError("msaa_background.py is available only on Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    oleacc = ctypes.WinDLL("oleacc", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.IsChild.restype = wintypes.BOOL
    return user32, oleacc, wintypes


def _window_info(user32: Any, hwnd: int) -> WindowInfo:
    if not user32.IsWindow(hwnd):
        raise ValueError(f"HWND 0x{hwnd:X} is not a live window")
    pid = wintypes.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not tid or not pid.value:
        raise ValueError(f"could not resolve PID for HWND 0x{hwnd:X}")
    ensure_unprotected_pid(int(pid.value))
    class_buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_buf, len(class_buf))
    return WindowInfo(hwnd=f"0x{hwnd:X}", pid=int(pid.value), class_name=class_buf.value)


def _same_pid_child_hwnds(user32: Any, root: int, expected_pid: int, cap: int) -> list[int]:
    """Enumerate native descendants, never sibling/top-level windows."""
    result: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == expected_pid:
            result.append(int(hwnd))
            if len(result) >= cap:
                return False
        return True

    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.EnumChildWindows(root, visit, 0)
    return result


def _get_client_accessible(oleacc: Any, hwnd: int, interface: Any) -> Any | None:
    iid = interface._iid_
    iid_value = ctypes.cast(ctypes.pointer(iid), ctypes.c_void_p)
    result = ctypes.c_void_p()
    fn = oleacc.AccessibleObjectFromWindow
    fn.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    fn.restype = ctypes.c_long
    hr = fn(hwnd, OBJID_CLIENT, iid_value, ctypes.byref(result))
    if hr < 0 or not result.value:
        return None
    return ctypes.cast(result, ctypes.POINTER(interface))


def _accessible_window(oleacc: Any, accessible: Any) -> int | None:
    hwnd = wintypes.HWND()
    fn = oleacc.WindowFromAccessibleObject
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.HWND)]
    fn.restype = ctypes.c_long
    hr = fn(ctypes.cast(accessible, ctypes.c_void_p), ctypes.byref(hwnd))
    return int(hwnd.value) if hr >= 0 and hwnd.value else None


def _is_in_scope(user32: Any, root_hwnd: int, expected_pid: int, candidate_hwnd: int) -> bool:
    if not user32.IsWindow(candidate_hwnd):
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(candidate_hwnd, ctypes.byref(pid))
    return pid.value == expected_pid and (candidate_hwnd == root_hwnd or bool(user32.IsChild(root_hwnd, candidate_hwnd)))


def _role_text(oleacc: Any, role_id: int | None) -> str | None:
    if role_id is None:
        return None
    canonical = ROLE_NAMES.get(role_id)
    if canonical:
        return canonical
    buf = ctypes.create_unicode_buffer(128)
    fn = oleacc.GetRoleTextW
    fn.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.UINT]
    fn.restype = wintypes.UINT
    n = fn(role_id, buf, len(buf))
    return buf.value[:n] if n else None


def _property(accessible: Any, prop: str, child_id: int) -> Any:
    """Read only accName/accRole; intentionally never query accValue."""
    try:
        member = getattr(accessible, prop)
        value = member if prop == "accChildCount" else member[child_id]
    except Exception:
        return None
    if prop == "accRole":
        try:
            if hasattr(value, "value"):
                value = value.value
            return int(value)
        except (TypeError, ValueError):
            return None
    if value is None:
        return None
    return str(value)


def _children(oleacc: Any, accessible: Any, count: int, interface: Any) -> tuple[list[Any], bool]:
    from comtypes.automation import VARIANT

    if count <= 0:
        return [], False
    capped = min(count, _MAX_CHILDREN_PER_NODE)
    variants = (VARIANT * capped)()
    obtained = wintypes.LONG()
    fn = oleacc.AccessibleChildren
    fn.argtypes = [ctypes.c_void_p, wintypes.LONG, wintypes.LONG,
                   ctypes.POINTER(VARIANT), ctypes.POINTER(wintypes.LONG)]
    fn.restype = ctypes.c_long
    hr = fn(ctypes.cast(accessible, ctypes.c_void_p), 0, capped, variants, ctypes.byref(obtained))
    if hr < 0:
        return [], count > capped

    children: list[Any] = []
    for variant in variants[:max(0, min(obtained.value, capped))]:
        vt = int(variant.vt)
        if vt == VT_I4:
            # Simple MSAA children are represented by a child ID on the parent.
            children.append((accessible, int(variant.value)))
        elif vt == VT_DISPATCH:
            # Complex children expose an IDispatch; recurse only if it supports IAccessible.
            try:
                child = variant.value.QueryInterface(interface)
            except Exception:
                continue
            children.append((child, CHILDID_SELF))
    return children, count > capped


_MAX_CHILDREN_PER_NODE = 512


def find_matches(hwnd: int, expected_pid: int, exact_name: str | None, exact_role: str | None,
                 *, max_nodes: int = 1500, max_depth: int = 12,
                 max_child_hwnds: int = 96, expected_class: str | None = None) -> dict[str, Any]:
    user32, oleacc, _ = _windows_api()
    root = _window_info(user32, hwnd)
    if root.pid != expected_pid:
        raise ValueError(f"PID mismatch: HWND 0x{hwnd:X} belongs to {root.pid}, expected {expected_pid}")
    if expected_class is not None and root.class_name != expected_class:
        raise ValueError(f"class mismatch: HWND class is {root.class_name!r}, expected {expected_class!r}")

    # Ensure staged pure-Python comtypes overlay is mounted if venv lacks it.
    ensure_comtypes_overlay()
    from comtypes import CoInitialize, CoUninitialize
    from comtypes.gen.Accessibility import IAccessible

    CoInitialize()
    matches: list[Match] = []
    visited = 0
    truncated = False
    query_name = _normal_name(exact_name) if exact_name is not None else None
    query_role = _normal_role(exact_role) if exact_role is not None else None
    try:
        hwnds = [hwnd]
        native = _same_pid_child_hwnds(user32, hwnd, expected_pid, max_child_hwnds)
        hwnds.extend(h for h in native if h != hwnd)
        truncated = len(native) >= max_child_hwnds

        for native_hwnd in hwnds:
            native_info = _window_info(user32, native_hwnd)
            accessible = _get_client_accessible(oleacc, native_hwnd, IAccessible)
            if accessible is None:
                continue
            queue = deque([(accessible, CHILDID_SELF, 0, [native_info.class_name], native_hwnd)])
            while queue and visited < max_nodes:
                current, child_id, depth, path, node_hwnd = queue.popleft()
                visited += 1
                name = _property(current, "accName", child_id)
                role_id = _property(current, "accRole", child_id)
                role = _role_text(oleacc, role_id)
                if ((query_name is None or (name is not None and _normal_name(name) == query_name))
                        and (query_role is None or (role is not None and _normal_role(role) == query_role))):
                    matches.append(Match(
                        hwnd=f"0x{node_hwnd:X}", pid=expected_pid,
                        class_name=_window_info(user32, node_hwnd).class_name,
                        name=name, role=role, role_id=role_id, child_id=child_id, path=path[-max_depth:]))

                # VT_I4 children are simple elements described by a child ID on
                # their parent; they do not own a nested IAccessible tree.
                if child_id != CHILDID_SELF:
                    continue
                if depth >= max_depth:
                    truncated = truncated or bool(_property(current, "accChildCount", CHILDID_SELF) or 0)
                    continue
                child_count_raw = _property(current, "accChildCount", CHILDID_SELF)
                try:
                    child_count = max(0, int(child_count_raw or 0))
                except (TypeError, ValueError):
                    child_count = 0
                children, child_truncated = _children(oleacc, current, child_count, IAccessible)
                truncated = truncated or child_truncated
                for child, child_id_or_self in children:
                    if child_id_or_self == CHILDID_SELF and child is not current:
                        child_hwnd = _accessible_window(oleacc, child)
                        if child_hwnd is not None and not _is_in_scope(user32, hwnd, expected_pid, child_hwnd):
                            continue
                        # Some embedded MSAA descendants have no native HWND;
                        # retain the exact parent HWND scope in that case.
                        if child_hwnd is None:
                            child_hwnd = node_hwnd
                    else:
                        child_hwnd = node_hwnd
                    next_role_id = _property(child, "accRole", child_id_or_self)
                    next_role = _role_text(oleacc, next_role_id)
                    label = next_role or f"child:{child_id_or_self}"
                    queue.append((child, child_id_or_self, depth + 1, path + [label], child_hwnd))
            if visited >= max_nodes:
                truncated = True
                break
    finally:
        CoUninitialize()

    return {
        "target": asdict(root),
        "query": {"exact_name": exact_name, "exact_role": exact_role},
        "visited": visited,
        "truncated": truncated,
        "matches": [asdict(match) for match in matches],
        "ambiguous": len(matches) > 1,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only exact MSAA name/role lookup for one HWND and PID.")
    parser.add_argument("--hwnd", required=True, type=_parse_int, help="exact target HWND (decimal or 0x-prefixed)")
    parser.add_argument("--pid", required=True, type=int, help="expected process ID; must match the target HWND")
    query = parser.add_mutually_exclusive_group(required=True)
    query.add_argument("--name", help="exact accessible name (case-insensitive; no substring matching)")
    query.add_argument("--role", help="exact MSAA role name, e.g. pushbutton, text, ROLE_SYSTEM_PUSHBUTTON")
    parser.add_argument("--expect-class", help="optional exact root window class guard")
    parser.add_argument("--max-nodes", type=int, default=1500, help="maximum visited accessible nodes (1..10000)")
    parser.add_argument("--max-depth", type=int, default=12, help="maximum accessible-tree depth (0..32)")
    parser.add_argument("--max-child-hwnds", type=int, default=96, help="maximum same-PID native child HWNDs (1..512)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.pid <= 0 or not 1 <= args.max_nodes <= 10000 or not 0 <= args.max_depth <= 32 or not 1 <= args.max_child_hwnds <= 512:
        parser.error("pid and traversal bounds must be positive and within the documented limits")
    if args.name == "":
        parser.error("--name must not be empty")
    if args.role == "":
        parser.error("--role must not be empty")
    started = time.perf_counter()
    try:
        result = find_matches(args.hwnd, args.pid, args.name, args.role,
                              max_nodes=args.max_nodes, max_depth=args.max_depth,
                              max_child_hwnds=args.max_child_hwnds, expected_class=args.expect_class)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    # Escaping Unicode keeps the JSON contract intact in legacy PowerShell hosts.
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0 if result["matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

