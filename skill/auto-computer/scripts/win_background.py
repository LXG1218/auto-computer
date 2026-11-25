"""Small, no-activation Win32 adapter for explicitly owned window handles.

This module intentionally does not synthesize system keyboard/mouse input,
change the clipboard, or call foreground/focus APIs.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from typing import Any

if os.name != "nt":
    raise SystemExit("win_background.py requires Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HWND = wintypes.HWND
DWORD = wintypes.DWORD
UINT = wintypes.UINT
WPARAM = wintypes.WPARAM
LPARAM = wintypes.LPARAM
LRESULT = wintypes.LPARAM
ULONG_PTR = ctypes.c_size_t

WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
EM_GETPASSWORDCHAR = 0x00D2
GWL_STYLE = -16
ES_PASSWORD = 0x0020
GA_ROOT = 2
GA_ROOTOWNER = 3
SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
SW_SHOWMINNOACTIVE = 7
SWP_NOZORDER = 0x0004
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SWP_ASYNCWINDOWPOS = 0x4000
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
PM_REMOVE = 0x0001
MAX_TEXT_UNITS = 16_384
SEND_TIMEOUT_MS = 2_000
MAX_ANCESTOR_DEPTH = 32

user32.IsWindow.argtypes = [HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetAncestor.argtypes = [HWND, UINT]
user32.GetAncestor.restype = HWND
user32.GetParent.argtypes = [HWND]
user32.GetParent.restype = HWND
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
user32.GetWindowThreadProcessId.restype = DWORD
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = HWND
user32.IsWindowVisible.argtypes = [HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindowAsync.argtypes = [HWND, ctypes.c_int]
user32.ShowWindowAsync.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = DWORD
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SendMessageTimeoutW.argtypes = [HWND, UINT, WPARAM, LPARAM, UINT, UINT, ctypes.POINTER(ULONG_PTR)]
user32.SendMessageTimeoutW.restype = ULONG_PTR

WINEVENTPROC = ctypes.WINFUNCTYPE(None, HWND, DWORD, HWND, ctypes.c_long, ctypes.c_long, DWORD, DWORD)
user32.SetWinEventHook.argtypes = [DWORD, DWORD, wintypes.HMODULE, WINEVENTPROC, DWORD, DWORD, DWORD]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.UnhookWinEvent.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, UINT, UINT, UINT]
user32.PeekMessageW.restype = wintypes.BOOL
kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = DWORD


def _hwnd(value: str) -> int:
    return int(value, 16) if value.lower().startswith("0x") else int(value, 10)


def _hex(hwnd: int | None) -> str | None:
    return f"0x{hwnd:X}" if hwnd else None


def _foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def _cursor() -> list[int] | None:
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    return [int(point.x), int(point.y)]


def _state(hwnd: int) -> dict[str, Any]:
    return {"visible": bool(user32.IsWindowVisible(HWND(hwnd))), "minimized": bool(user32.IsIconic(HWND(hwnd)))}


class _ForegroundWatch:
    """Collect foreground change events delivered to this process thread."""
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._callback = WINEVENTPROC(self._on_event)
        self._hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, self._callback,
            0, 0, WINEVENT_OUTOFCONTEXT,
        )
        self.installed = bool(self._hook)

    def _on_event(self, _hook: int, event: int, hwnd: int, _id_object: int,
                  _id_child: int, _thread: int, _time: int) -> None:
        self.events.append({"event": int(event), "hwnd": _hex(int(hwnd or 0))})

    def drain(self) -> None:
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            # The helper has no windows or queued application messages to dispatch.
            pass

    def close(self) -> None:
        self.drain()
        if self._hook:
            user32.UnhookWinEvent(self._hook)
            self._hook = None


def _verify_target(root: int, expected_pid: int, child: int | None = None) -> int:
    """Require an exact top-level target and (if supplied) an exact descendant."""
    if expected_pid <= 0:
        raise ValueError("expected PID must be a positive integer")
    if not root or not user32.IsWindow(HWND(root)):
        raise ValueError("top-level HWND is invalid")
    root_pid = DWORD()
    user32.GetWindowThreadProcessId(HWND(root), ctypes.byref(root_pid))
    if int(root_pid.value) != expected_pid:
        raise ValueError(f"top-level HWND PID mismatch: expected {expected_pid}, got {root_pid.value}")
    if int(user32.GetAncestor(HWND(root), GA_ROOT) or 0) != root:
        raise ValueError("target HWND must be a top-level window")
    target = child or root
    if not user32.IsWindow(HWND(target)):
        raise ValueError("control HWND is invalid")
    target_pid = DWORD()
    user32.GetWindowThreadProcessId(HWND(target), ctypes.byref(target_pid))
    if int(target_pid.value) != expected_pid:
        raise ValueError(f"control HWND PID mismatch: expected {expected_pid}, got {target_pid.value}")
    if target != root:
        current = target
        for _ in range(MAX_ANCESTOR_DEPTH):
            current = int(user32.GetParent(HWND(current)) or 0)
            if not current:
                break
            ancestor_pid = DWORD()
            user32.GetWindowThreadProcessId(HWND(current), ctypes.byref(ancestor_pid))
            if int(ancestor_pid.value) != expected_pid:
                raise ValueError("control ancestor PID differs from the expected PID")
            if current == root:
                break
        if current != root:
            raise ValueError(f"control HWND is not within the specified window tree (depth <= {MAX_ANCESTOR_DEPTH})")
    return target


def _observe(before: dict[str, Any], watch: _ForegroundWatch) -> dict[str, Any]:
    watch.drain()
    after_fg = _foreground()
    after_cursor = _cursor()
    after_clipboard_seq = int(user32.GetClipboardSequenceNumber())
    return {
        "foreground_before": _hex(before["foreground"]),
        "foreground_after": _hex(after_fg),
        "foreground_observed": bool(before["foreground"] and after_fg),
        "foreground_hook_installed": watch.installed,
        "foreground_events": list(watch.events),
        "pointer_before": before["pointer"],
        "pointer_after": after_cursor,
        "clipboard_sequence_before": before["clipboard_sequence"],
        "clipboard_sequence_after": after_clipboard_seq,
    }


def _begin_observation() -> tuple[dict[str, Any], _ForegroundWatch]:
    watch = _ForegroundWatch()
    return ({"foreground": _foreground(), "pointer": _cursor(),
             "clipboard_sequence": int(user32.GetClipboardSequenceNumber())}, watch)


def _foreground_target_matches(root: int, foreground: int) -> bool:
    if not foreground:
        return False
    return foreground == root or int(user32.GetAncestor(HWND(foreground), GA_ROOTOWNER) or 0) == root


def _foreground_exact_matches(root: int, foreground: int) -> bool:
    return bool(foreground and foreground == root)


def foreground_write_gate(root: int, before: dict[str, Any], watch: _ForegroundWatch,
                          foreground_demo: bool = False) -> dict[str, Any]:
    """Fail closed when foreground state is unavailable or changes before a write."""
    watch.drain()
    initial = int(before.get("foreground") or 0)
    current = _foreground()
    observed = bool(watch.installed and initial and current)
    initial_target = (_foreground_exact_matches(root, initial) if foreground_demo else
                      _foreground_target_matches(root, initial))
    current_target = (_foreground_exact_matches(root, current) if foreground_demo else
                      _foreground_target_matches(root, current))
    events_clear = not watch.events
    stable = initial == current
    expected_target = bool(foreground_demo)
    allowed = observed and events_clear and stable and initial_target == expected_target and current_target == expected_target
    if not watch.installed:
        reason = "foreground-hook-unavailable"
    elif not initial or not current:
        reason = "foreground-unobserved"
    elif not events_clear or not stable:
        reason = "foreground-changed-before-action"
    elif foreground_demo and not current_target:
        reason = "foreground-demo-requires-target-window-foreground"
    elif not foreground_demo and current_target:
        reason = "target-window-is-foreground"
    else:
        reason = "authorized"
    return {"allowed": allowed, "mode": "foreground-demo" if foreground_demo else "background-only",
            "reason": reason, "target_foreground": current_target,
            "foreground_initial": _hex(initial), "foreground_current": _hex(current),
            "foreground_observed": observed, "foreground_hook_installed": watch.installed,
            "foreground_events_before_action": list(watch.events)}


def _wait_state(hwnd: int, *, visible: bool, minimized: bool,
                watch: _ForegroundWatch, timeout_ms: int = 500) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_ms / 1000
    current = _state(hwnd)
    while current["visible"] != visible or current["minimized"] != minimized:
        if time.monotonic() >= deadline:
            break
        watch.drain()
        time.sleep(0.01)
        current = _state(hwnd)
    watch.drain()
    return current


def show_noactivate(root: int, expected_pid: int) -> dict[str, Any]:
    _verify_target(root, expected_pid)
    before_state = _state(root)
    if before_state["visible"] and not before_state["minimized"]:
        return {"ok": True, "operation": "show-noactivate", "hwnd": _hex(root),
                "pid": expected_pid, "action": "already-visible", "state_before": before_state,
                "state_after": before_state, "effect": "no-op"}
    before, watch = _begin_observation()
    try:
        gate = foreground_write_gate(root, before, watch, False)
        if not gate["allowed"]:
            observed = _observe(before, watch)
            return {"ok": False, "operation": "show-noactivate", "hwnd": _hex(root),
                    "pid": expected_pid, "action": "paused", "action_attempted": False,
                    "foreground_write_gate": gate, "state_before": before_state,
                    "state_after": _state(root), **observed}
        if before_state["minimized"]:
            api_return = bool(user32.ShowWindowAsync(HWND(root), SW_SHOWNOACTIVATE))
            action = "restore-noactivate"
        else:
            flags = (SWP_SHOWWINDOW | SWP_NOACTIVATE | SWP_NOZORDER | SWP_NOMOVE |
                     SWP_NOSIZE | SWP_ASYNCWINDOWPOS)
            api_return = bool(user32.SetWindowPos(HWND(root), None, 0, 0, 0, 0, flags))
            action = "show-hidden-noactivate"
        after_state = _wait_state(root, visible=True, minimized=False, watch=watch)
        observed = _observe(before, watch)
    finally:
        watch.close()
    state_reached = after_state["visible"] and not after_state["minimized"]
    after_hwnd = int(observed["foreground_after"], 16) if observed.get("foreground_after") else 0
    after_is_target = _foreground_target_matches(root, after_hwnd)
    fg_stable = bool(observed.get("foreground_observed") and
                     observed["foreground_before"] == observed["foreground_after"] and
                     not observed["foreground_events"])
    return {"ok": state_reached and fg_stable and not after_is_target, "operation": "show-noactivate", "hwnd": _hex(root),
            "pid": expected_pid, "action": action, "api_return": api_return,
            "action_attempted": True, "foreground_write_gate": gate,
            "foreground_after_is_target": after_is_target, "foreground_unchanged": fg_stable,
            "api_return_semantics": "async-request-accepted",
            "state_before": before_state, "state_after": after_state,
            "effect": "state-reached" if state_reached else "state-not-reached", **observed}


def set_noactivate_state(root: int, expected_pid: int, state: str) -> dict[str, Any]:
    """Hide or minimize a non-foreground target without activating another window."""
    _verify_target(root, expected_pid)
    if state not in {"hidden", "minimized"}:
        raise ValueError("state must be hidden or minimized")
    before_state = _state(root)
    if (state == "hidden" and not before_state["visible"]) or (
            state == "minimized" and before_state["minimized"]):
        return {"ok": True, "operation": f"{state}-noactivate", "hwnd": _hex(root),
                "pid": expected_pid, "action": "already-in-state", "state_before": before_state,
                "state_after": before_state, "effect": "no-op"}
    before, watch = _begin_observation()
    try:
        gate = foreground_write_gate(root, before, watch, False)
        if not gate["allowed"]:
            observed = _observe(before, watch)
            return {"ok": False, "operation": f"{state}-noactivate", "hwnd": _hex(root),
                    "pid": expected_pid, "action": "paused", "action_attempted": False,
                    "foreground_write_gate": gate, "state_before": before_state,
                    "state_after": _state(root), **observed}
        if state == "hidden":
            flags = (SWP_HIDEWINDOW | SWP_NOACTIVATE | SWP_NOZORDER | SWP_NOMOVE |
                     SWP_NOSIZE | SWP_ASYNCWINDOWPOS)
            api_return = bool(user32.SetWindowPos(HWND(root), None, 0, 0, 0, 0, flags))
            after_state = _wait_state(root, visible=False, minimized=False, watch=watch)
        else:
            api_return = bool(user32.ShowWindowAsync(HWND(root), SW_SHOWMINNOACTIVE))
            after_state = _wait_state(root, visible=True, minimized=True, watch=watch)
        observed = _observe(before, watch)
    finally:
        watch.close()
    state_reached = (not after_state["visible"]) if state == "hidden" else after_state["minimized"]
    after_hwnd = int(observed["foreground_after"], 16) if observed.get("foreground_after") else 0
    after_is_target = _foreground_target_matches(root, after_hwnd)
    fg_stable = bool(observed.get("foreground_observed") and
                     observed["foreground_before"] == observed["foreground_after"] and
                     not observed["foreground_events"])
    return {"ok": state_reached and fg_stable and not after_is_target, "operation": f"{state}-noactivate", "hwnd": _hex(root),
            "pid": expected_pid, "api_return": api_return, "api_return_semantics": "async-request-accepted",
            "action_attempted": True, "foreground_write_gate": gate,
            "foreground_after_is_target": after_is_target, "foreground_unchanged": fg_stable,
            "state_before": before_state, "state_after": after_state,
            "effect": "state-reached" if state_reached else "state-not-reached", **observed}


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    n = user32.GetClassNameW(HWND(hwnd), buf, len(buf))
    return buf.value[:n] if n else ""


def _send(hwnd: int, message: int, wparam: int, lparam: int) -> tuple[bool, int]:
    result = ULONG_PTR()
    ok = bool(user32.SendMessageTimeoutW(HWND(hwnd), message, WPARAM(wparam), LPARAM(lparam),
                                         SMTO_BLOCK | SMTO_ABORTIFHUNG, SEND_TIMEOUT_MS,
                                         ctypes.byref(result)))
    return ok, int(result.value)


def set_native_edit(root: int, edit: int, expected_pid: int, text: str,
                    foreground_demo: bool = False) -> dict[str, Any]:
    target = _verify_target(root, expected_pid, edit)
    units = len(text.encode("utf-16-le")) // 2
    if units > MAX_TEXT_UNITS:
        raise ValueError(f"text exceeds {MAX_TEXT_UNITS} UTF-16 code units")
    if "\x00" in text:
        raise ValueError("text must not contain NUL")
    klass = _class_name(target)
    supported = {"Edit", "RichEdit20A", "RichEdit20W", "RichEdit50W", "RICHEDIT50W", "RichEditD2DPT"}
    if klass not in supported:
        raise ValueError(f"unsupported native edit class: {klass!r}")
    style = int(user32.GetWindowLongPtrW(HWND(target), GWL_STYLE)) & 0xFFFFFFFF
    if style & ES_PASSWORD:
        raise ValueError("password edit controls are not supported")
    # EM_GETPASSWORDCHAR catches password controls whose style is not exposed consistently.
    ok_pw, password_char = _send(target, EM_GETPASSWORDCHAR, 0, 0)
    if ok_pw and password_char:
        raise ValueError("password edit controls are not supported")

    before, watch = _begin_observation()
    try:
        gate = foreground_write_gate(root, before, watch, foreground_demo)
        if not gate["allowed"]:
            observed = _observe(before, watch)
            return {"ok": False, "operation": "set-native-edit", "top_hwnd": _hex(root),
                    "hwnd": _hex(target), "pid": expected_pid, "class": klass,
                    "action_attempted": False, "foreground_write_gate": gate,
                    "effect": "paused-before-mutation", **observed}
        # Recheck immediately at the action boundary to catch a foreground change
        # during validation or the first snapshot.
        gate = foreground_write_gate(root, before, watch, foreground_demo)
        if not gate["allowed"]:
            observed = _observe(before, watch)
            return {"ok": False, "operation": "set-native-edit", "top_hwnd": _hex(root),
                    "hwnd": _hex(target), "pid": expected_pid, "class": klass,
                    "action_attempted": False, "foreground_write_gate": gate,
                    "effect": "paused-before-mutation", **observed}
        source = ctypes.create_unicode_buffer(text)
        sent, set_result = _send(target, WM_SETTEXT, 0, ctypes.addressof(source))
        length_ok, length = _send(target, WM_GETTEXTLENGTH, 0, 0)
        capacity = min(max(length + 1, 1), MAX_TEXT_UNITS + 1)
        output = ctypes.create_unicode_buffer(capacity)
        read_ok, read_chars = _send(target, WM_GETTEXT, capacity, ctypes.addressof(output))
        readback = output.value
        observed = _observe(before, watch)
    finally:
        watch.close()
    matched = sent and set_result != 0 and length_ok and read_ok and readback == text
    observation_hwnd = int(observed["foreground_after"], 16) if observed.get("foreground_after") else 0
    foreground_match_after = (_foreground_exact_matches(root, observation_hwnd) if foreground_demo else
                              not _foreground_target_matches(root, observation_hwnd))
    foreground_stable = bool(observed.get("foreground_observed") and
                              observed["foreground_before"] == observed["foreground_after"] and
                              not observed["foreground_events"])
    return {"ok": bool(matched and foreground_match_after and foreground_stable), "operation": "set-native-edit", "top_hwnd": _hex(root),
            "hwnd": _hex(target), "pid": expected_pid, "class": klass,
            "api_accepted": sent, "window_result": set_result, "readback_ok": read_ok,
            "action_attempted": True, "foreground_write_gate": gate,
            "foreground_after_matches_mode": foreground_match_after,
            "foreground_unchanged": foreground_stable,
            "readback_chars": read_chars, "text_units": units,
            "readback_matches": readback == text, "effect": "readback-matched" if matched else "write-or-readback-failed",
            **observed}


def main() -> int:
    parser = argparse.ArgumentParser(description="No-focus, HWND-scoped native window operations")
    sub = parser.add_subparsers(dest="command", required=True)
    p_show = sub.add_parser("show-noactivate")
    p_show.add_argument("--hwnd", required=True)
    p_show.add_argument("--pid", required=True, type=int)
    p_hide = sub.add_parser("hide-noactivate")
    p_hide.add_argument("--hwnd", required=True)
    p_hide.add_argument("--pid", required=True, type=int)
    p_min = sub.add_parser("minimize-noactivate")
    p_min.add_argument("--hwnd", required=True)
    p_min.add_argument("--pid", required=True, type=int)
    p_edit = sub.add_parser("set-native-edit")
    p_edit.add_argument("--hwnd", required=True, help="top-level HWND")
    p_edit.add_argument("--control-hwnd", required=True)
    p_edit.add_argument("--pid", required=True, type=int)
    p_edit.add_argument("--text", required=True)
    p_edit.add_argument("--foreground-demo", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "show-noactivate":
            result = show_noactivate(_hwnd(args.hwnd), args.pid)
        elif args.command == "hide-noactivate":
            result = set_noactivate_state(_hwnd(args.hwnd), args.pid, "hidden")
        elif args.command == "minimize-noactivate":
            result = set_noactivate_state(_hwnd(args.hwnd), args.pid, "minimized")
        else:
            result = set_native_edit(_hwnd(args.hwnd), _hwnd(args.control_hwnd), args.pid, args.text,
                                     args.foreground_demo)
        print(json.dumps(result))
        return 0 if result["ok"] else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
