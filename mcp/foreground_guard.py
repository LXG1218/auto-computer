#!/usr/bin/env python3
"""Minimal click-through screen border glow, top banner, and Esc abort watcher for foreground handoffs."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time
from typing import Any

BANNER_TEXT = "Auto Computer 正在短暂借用前台 · 按 Esc 立即停止"
VK_ESCAPE = 0x1B
EDGE_PX = 6
BANNER_W = 430
BANNER_H = 30
GLOW_ALPHA = 185
BANNER_ALPHA = 225


def _write_abort(abort_file: Path) -> None:
    try:
        abort_file.parent.mkdir(parents=True, exist_ok=True)
        abort_file.write_text("esc\n", encoding="utf-8")
    except OSError:
        pass


def _parent_alive(kernel32: Any, pid: int | None) -> bool:
    if not pid or pid <= 0:
        return True
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return int(code.value) == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def run_guard(abort_file: Path, parent_pid: int | None = None) -> int:
    if os.name != "nt":
        return 0

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    if hasattr(user32, "SetProcessDpiAwarenessContext"):
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
    gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
    gdi32.CreateFontW.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
    ]
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetBkMode.restype = ctypes.c_int
    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    gdi32.SetTextColor.restype = wintypes.COLORREF
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL

    WS_POPUP = 0x80000000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    LWA_ALPHA = 0x00000002
    SW_SHOWNOACTIVATE = 4
    PM_REMOVE = 0x0001
    WM_PAINT = 0x000F
    DT_CENTER = 0x00000001
    DT_VCENTER = 0x00000004
    DT_SINGLELINE = 0x00000020
    TRANSPARENT_BK = 1

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON),
        ]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hdc", wintypes.HDC), ("fErase", wintypes.BOOL), ("rcPaint", wintypes.RECT),
            ("fRestore", wintypes.BOOL), ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32),
        ]

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.BeginPaint.restype = wintypes.HDC
    user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.EndPaint.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
    user32.FillRect.restype = ctypes.c_int
    user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT]
    user32.DrawTextW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT

    hinst = kernel32.GetModuleHandleW(None)
    glow_brush = gdi32.CreateSolidBrush(0x00F6823B)  # COLORREF BGR for RGB(59, 130, 246)
    banner_brush = gdi32.CreateSolidBrush(0x003B2418)
    font = gdi32.CreateFontW(-14, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 5, 0, "Microsoft YaHei UI")
    banner_hwnd_ref = [0]

    @WNDPROC
    def wnd_proc(hwnd: int, msg: int, wp: int, lp: int) -> int:
        if msg == WM_PAINT and hwnd == banner_hwnd_ref[0]:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            if hdc:
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                user32.FillRect(hdc, ctypes.byref(rect), banner_brush)
                old_font = gdi32.SelectObject(hdc, font) if font else None
                gdi32.SetBkMode(hdc, TRANSPARENT_BK)
                gdi32.SetTextColor(hdc, 0x00FFFFFF)
                user32.DrawTextW(hdc, BANNER_TEXT, -1, ctypes.byref(rect), DT_CENTER | DT_VCENTER | DT_SINGLELINE)
                if old_font:
                    gdi32.SelectObject(hdc, old_font)
                user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    cls_glow = f"ACGlow_{os.getpid()}"
    cls_banner = f"ACBanner_{os.getpid()}"
    for cls_name, brush in ((cls_glow, glow_brush), (cls_banner, banner_brush)):
        wc = WNDCLASSEXW(
            cbSize=ctypes.sizeof(WNDCLASSEXW), style=0, lpfnWndProc=wnd_proc,
            hInstance=hinst, hbrBackground=brush, lpszClassName=cls_name,
        )
        user32.RegisterClassExW(ctypes.byref(wc))

    vx = int(user32.GetSystemMetrics(76))
    vy = int(user32.GetSystemMetrics(77))
    vw = int(user32.GetSystemMetrics(78)) or int(user32.GetSystemMetrics(0))
    vh = int(user32.GetSystemMetrics(79)) or int(user32.GetSystemMetrics(1))
    px = 0
    py = 0
    pw = int(user32.GetSystemMetrics(0)) or vw

    ex_style = WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
    rects = [
        (cls_glow, vx, vy, vw, EDGE_PX, GLOW_ALPHA),
        (cls_glow, vx, vy + vh - EDGE_PX, vw, EDGE_PX, GLOW_ALPHA),
        (cls_glow, vx, vy + EDGE_PX, EDGE_PX, max(1, vh - 2 * EDGE_PX), GLOW_ALPHA),
        (cls_glow, vx + vw - EDGE_PX, vy + EDGE_PX, EDGE_PX, max(1, vh - 2 * EDGE_PX), GLOW_ALPHA),
        (cls_banner, px + max(0, (pw - BANNER_W) // 2), py + EDGE_PX + 2, min(BANNER_W, pw), BANNER_H, BANNER_ALPHA),
    ]
    hwnds: list[int] = []
    for idx, (cname, x, y, w, h, alpha) in enumerate(rects):
        hwnd = user32.CreateWindowExW(ex_style, cname, "", WS_POPUP, x, y, w, h, 0, 0, hinst, 0)
        if hwnd:
            if idx == 4:
                banner_hwnd_ref[0] = int(hwnd)
            user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            hwnds.append(int(hwnd))

    try:
        msg = wintypes.MSG()
        while True:
            if not _parent_alive(kernel32, parent_pid):
                break
            if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                _write_abort(abort_file)
                break
            while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.015)
    finally:
        for h in hwnds:
            user32.DestroyWindow(h)
        for cname in (cls_glow, cls_banner):
            user32.UnregisterClassW(cname, hinst)
        for obj in (glow_brush, banner_brush, font):
            if obj:
                gdi32.DeleteObject(obj)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abort-file", required=True)
    parser.add_argument("--parent-pid", type=int, default=None)
    args = parser.parse_args()
    return run_guard(Path(args.abort_file), args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
