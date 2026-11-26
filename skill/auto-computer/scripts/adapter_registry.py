#!/usr/bin/env python3
"""Per-machine registry for app adapter bindings.

This module stores declarative selectors only. It never launches commands or
performs UI actions; callers resolve a binding and invoke the named local
adapter with their own exact HWND/PID checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _common import emit_json, get_auto_computer_home

_REGISTRY_OVERRIDE = os.environ.get("AUTO_COMPUTER_ADAPTERS_DIR", "").strip()
REGISTRY_DIR = Path(_REGISTRY_OVERRIDE).expanduser() if _REGISTRY_OVERRIDE else get_auto_computer_home() / "adapters"
APP_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
ACTION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
ROUTES = {"uia", "msaa", "win32"}
SELECTOR_KEYS = {
    "uia": {"name", "automation_id", "control_type", "expect_control_type"},
    "msaa": {"name", "role", "expect_class"},
    "win32": {"class_name", "control_id", "message"},
}
MAX_TEXT = 512


def _valid_text(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field} must be a non-empty string of at most {MAX_TEXT} characters")
    return value.strip()


def _key(app: str, action: str) -> str:
    raw = f"{app.casefold()}\0{action.casefold()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _path(app: str, action: str, root: Path | None = None) -> Path:
    if not APP_RE.fullmatch(app) or not ACTION_RE.fullmatch(action):
        raise ValueError("app and action must use letters, numbers, dot, underscore, or hyphen")
    base = (root or REGISTRY_DIR).resolve()
    path = (base / f"{_key(app, action)}.json").resolve()
    if path.parent != base:
        raise ValueError("registry path escaped adapters directory")
    return path


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def record_binding(app: str, action: str, route: str, selector: dict[str, Any], *,
                   verification: str = "detected", evidence: str | None = None,
                   root: Path | None = None) -> dict[str, Any]:
    """Save a binding. `tested_readback` is caller-attested and requires evidence."""
    app = _valid_text(app, "app")  # type: ignore[assignment]
    action = _valid_text(action, "action")  # type: ignore[assignment]
    if not APP_RE.fullmatch(app) or not ACTION_RE.fullmatch(action):
        raise ValueError("app and action must use letters, numbers, dot, underscore, or hyphen")
    if route not in ROUTES:
        raise ValueError("route must be uia, msaa, or win32")
    if verification not in {"detected", "tested_readback", "tested_background"}:
        raise ValueError("verification must be detected, tested_readback, or tested_background")
    if not isinstance(selector, dict) or not selector:
        raise ValueError("selector must be a non-empty object")
    allowed = SELECTOR_KEYS[route]
    if set(selector) - allowed:
        raise ValueError(f"unsupported {route} selector fields: {', '.join(sorted(set(selector) - allowed))}")
    normalized = {k: _valid_text(v, f"selector.{k}") for k, v in selector.items()}
    evidence = _valid_text(evidence, "evidence", optional=True)
    if verification != "detected" and evidence is None:
        raise ValueError("tested bindings require a short readback/background evidence note")
    item = {
        "schema_version": 1, "app": app, "action": action,
        "route": route, "selector": normalized, "verification": verification,
        "evidence": evidence,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _atomic_json(_path(app, action, root), item)
    return item


def get_binding(app: str, action: str, *, root: Path | None = None) -> dict[str, Any] | None:
    path = _path(app, action, root)
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(item, dict) or item.get("schema_version") != 1:
        return None
    return item


def list_bindings(*, root: Path | None = None) -> list[dict[str, Any]]:
    base = (root or REGISTRY_DIR).resolve()
    if not base.is_dir():
        return []
    result = []
    for path in sorted(base.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if (isinstance(item, dict) and item.get("schema_version") == 1
                    and item.get("route") in ROUTES and isinstance(item.get("selector"), dict)):
                result.append(item)
        except (OSError, json.JSONDecodeError):
            continue
    return result


def window_owner(hwnd: int) -> tuple[int, str]:
    """Return the PID and executable stem for one HWND without reading other windows."""
    import ctypes
    from ctypes import wintypes

    import win32process

    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        image = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(image))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        return pid, Path(image.value).stem
    finally:
        kernel32.CloseHandle(handle)


def discover_windows() -> list[dict[str, Any]]:
    """Passively enumerate visible top-level windows and owning executable on Windows."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    results: list[dict[str, Any]] = []

    def visit(hwnd: int, _param: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            image = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        finally:
            kernel32.CloseHandle(handle)
        if not image:
            return True
        length = int(user32.GetWindowTextLengthW(hwnd))
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, len(title))
        results.append({"hwnd": f"0x{int(hwnd):X}", "pid": int(pid.value),
                        "executable": buf.value, "title": title.value})
        return True

    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="passively list visible app windows")
    sub.add_parser("list", help="list registered app/action bindings")
    get = sub.add_parser("get", help="retrieve one app/action binding")
    get.add_argument("app"); get.add_argument("action")
    rec = sub.add_parser("record", help="record a declarative adapter binding")
    rec.add_argument("app"); rec.add_argument("action"); rec.add_argument("route", choices=sorted(ROUTES))
    rec.add_argument("selector", help="JSON object containing exact route selector fields")
    rec.add_argument("--verification", choices=("detected", "tested_readback", "tested_background"), default="detected")
    rec.add_argument("--evidence")
    args = parser.parse_args()
    try:
        if args.command == "discover": result = {"ok": True, "windows": discover_windows()}
        elif args.command == "list": result = {"ok": True, "bindings": list_bindings()}
        elif args.command == "get": result = {"ok": True, "binding": get_binding(args.app, args.action)}
        else:
            selector = json.loads(args.selector)
            result = {"ok": True, "binding": record_binding(args.app, args.action, args.route, selector,
                                                                verification=args.verification, evidence=args.evidence)}
        emit_json(result)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        emit_json({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
