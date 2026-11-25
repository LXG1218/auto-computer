"""Read Windows foreground and system pointer state for before/after checks."""

import argparse
import ctypes
import json
import sys
import time
from ctypes import wintypes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=lambda value: int(value, 0), help="Target HWND in decimal or 0x-prefixed hexadecimal")
    parser.add_argument("--retries", type=int, default=3, help="Retry count for transient zero foreground handle")
    parser.add_argument("--retry-ms", type=int, default=40, help="Delay between retries in milliseconds")
    args = parser.parse_args()
    target = args.window

    if sys.platform != "win32":
        print(json.dumps({"ok": False, "error": "Windows desktop required"}))
        return 1

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

    point = wintypes.POINT()
    foreground = 0
    cursor_ok = False
    attempts = 1 + max(0, args.retries)
    for attempt in range(attempts):
        foreground = user32.GetForegroundWindow()
        cursor_ok = bool(user32.GetCursorPos(ctypes.byref(point)))
        if foreground and cursor_ok:
            break
        if attempt + 1 < attempts:
            time.sleep(max(0, args.retry_ms) / 1000.0)

    if not foreground or not cursor_ok:
        reason = "no_foreground_window" if not foreground else "cursor_read_failed"
        print(json.dumps({"ok": False, "error": "Could not read active Windows desktop state", "reason": reason}))
        return 1

    state = {
        "ok": True,
        "foreground_hwnd": hex(foreground),
        "cursor": {"x": point.x, "y": point.y},
        "clipboard_sequence": user32.GetClipboardSequenceNumber(),
    }
    if target is not None:
        if target <= 0 or target >= 1 << (8 * ctypes.sizeof(wintypes.HWND)) or not user32.IsWindow(target):
            print(json.dumps({"ok": False, "error": "Target Windows handle is unavailable"}))
            return 1
        state["window"] = {
            "hwnd": hex(target),
            "minimized": bool(user32.IsIconic(target)),
            "is_foreground": target == foreground,
        }

    print(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
