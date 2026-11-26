#!/usr/bin/env python3
"""Click-through aurora edge glow, frosted floating capsule, and LL keyboard hook abort guard."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import time
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BANNER_TEXT = "Auto Computer 正在借用前台 · 按 Esc 立即急停"
ABORT_BANNER_TEXT = "已收到急停 · 正在立即中止操作"
VK_ESCAPE = 0x1B
VK_PAUSE = 0x13
GLOW_DEPTH = 68
BANNER_W = 580
BANNER_H = 50
BANNER_TOP_MARGIN = 22
TARGET_FPS = 30.0
FADE_IN_SECONDS = 0.22
ABORT_FLASH_SECONDS = 0.18

_WS_POPUP = 0x80000000
_WS_EX_TOPMOST = 0x00000008
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000

_SW_SHOWNOACTIVATE = 4
_HWND_TOPMOST = wintypes.HWND(-1)
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040

_WM_DESTROY = 0x0002
_WM_ERASEBKGND = 0x0014
_WM_MOUSEACTIVATE = 0x0021
_WM_NCHITTEST = 0x0084
_HTTRANSPARENT = -1
_MA_NOACTIVATE = 3

_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WH_KEYBOARD_LL = 13
_LLKHF_INJECTED = 0x00000010

_ULW_ALPHA = 0x00000002
_AC_SRC_OVER = 0x00
_AC_SRC_ALPHA = 0x01
_DIB_RGB_COLORS = 0
_BI_RGB = 0
_WDA_EXCLUDEFROMCAPTURE = 0x00000011


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    ]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def _write_abort(abort_file: Path | None) -> None:
    if abort_file is None:
        return
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


def _aurora_rgb_from_phase(phase: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample cyclic cool-aurora gradient (#22D3EE -> #3B82F6 -> #818CF8 -> #22D3EE) as RGB float32 [0..255]."""
    p = np.mod(phase, 1.0).astype(np.float32) * 3.0
    seg = np.floor(p).astype(np.int32) % 3
    f = (p - np.floor(p)).astype(np.float32)
    f = f * f * (3.0 - 2.0 * f)

    palette_r = np.array([34.0, 59.0, 129.0, 34.0], dtype=np.float32)
    palette_g = np.array([211.0, 130.0, 140.0, 211.0], dtype=np.float32)
    palette_b = np.array([238.0, 246.0, 248.0, 238.0], dtype=np.float32)

    r = palette_r[seg] + (palette_r[seg + 1] - palette_r[seg]) * f
    g = palette_g[seg] + (palette_g[seg + 1] - palette_g[seg]) * f
    b = palette_b[seg] + (palette_b[seg + 1] - palette_b[seg]) * f
    return r, g, b


def _display_scale(mon_w: int, mon_h: int) -> float:
    return max(1.0, min(int(mon_w), int(mon_h)) / 1080.0)


def build_edge_geometry(
    side: str,
    mon_w: int,
    mon_h: int,
    depth: int = GLOW_DEPTH,
    scale: float = 1.0,
) -> dict[str, Any]:
    """Precompute 45-degree mitered geometry matrices for one monitor edge."""
    d = max(4, min(int(depth), mon_w // 4, mon_h // 4))
    perim = float(max(1, 2 * (mon_w + mon_h)))

    if side == "top":
        w, h = mon_w, d
        gx = np.arange(w, dtype=np.int32)[None, :]
        gy = np.arange(h, dtype=np.int32)[:, None]
        inward = gy
        miter = (inward <= gx) & (inward <= (mon_w - 1 - gx))
        u_line = gx.astype(np.float32) / perim
        rel_x, rel_y = 0, 0
    elif side == "right":
        w, h = d, mon_h
        lx = np.arange(w, dtype=np.int32)[None, :]
        gy = np.arange(h, dtype=np.int32)[:, None]
        inward = (d - 1) - lx
        miter = (gy > inward) & ((mon_h - 1 - gy) > inward)
        u_line = (float(mon_w) + gy.astype(np.float32)) / perim
        rel_x, rel_y = mon_w - d, 0
    elif side == "bottom":
        w, h = mon_w, d
        gx = np.arange(w, dtype=np.int32)[None, :]
        ly = np.arange(h, dtype=np.int32)[:, None]
        inward = (d - 1) - ly
        miter = (inward <= gx) & (inward <= (mon_w - 1 - gx))
        u_line = (float(mon_w + mon_h) + (mon_w - 1 - gx).astype(np.float32)) / perim
        rel_x, rel_y = 0, mon_h - d
    elif side == "left":
        w, h = d, mon_h
        lx = np.arange(w, dtype=np.int32)[None, :]
        gy = np.arange(h, dtype=np.int32)[:, None]
        inward = lx
        miter = (gy > inward) & ((mon_h - 1 - gy) > inward)
        u_line = (float(2 * mon_w + mon_h) + (mon_h - 1 - gy).astype(np.float32)) / perim
        rel_x, rel_y = 0, 0
    else:
        raise ValueError(f"Unsupported edge side: {side}")

    in_f = inward.astype(np.float32)
    norm_d = np.clip(in_f / float(max(d - 1, 1)), 0.0, 1.0)
    core_px = max(3, min(d // 2, round(4.5 * max(1.0, scale))))
    core_mask = inward < core_px
    base_alpha = np.where(
        core_mask,
        0.95 - (in_f / float(max(core_px, 1))) * 0.11,
        0.82 * np.power(1.0 - norm_d, 1.85),
    )
    base_alpha = np.where(miter, np.clip(base_alpha, 0.0, 1.0), 0.0).astype(np.float32)
    core_highlight = np.where(
        core_mask & miter,
        0.42 * (1.0 - in_f / float(max(core_px, 1))),
        0.0,
    ).astype(np.float32)

    return {
        "side": side,
        "rel_x": rel_x,
        "rel_y": rel_y,
        "w": w,
        "h": h,
        "u": u_line.copy(),
        "base_alpha": np.broadcast_to(base_alpha, (h, w)).copy(),
        "core_highlight": np.broadcast_to(core_highlight, (h, w)).copy(),
        "miter": np.broadcast_to(miter, (h, w)).copy(),
    }


def render_edge_bgra(
    geom: dict[str, Any],
    t: float,
    *,
    aborted: bool = False,
    fade: float = 1.0,
    out_bgra: np.ndarray | None = None,
) -> np.ndarray:
    """Render premultiplied BGRA edge glow frame into `out_bgra` (H x W x 4, uint8)."""
    h, w = geom["h"], geom["w"]
    if out_bgra is None:
        out_bgra = np.empty((h, w, 4), dtype=np.uint8)

    u = geom["u"]
    base_alpha = geom["base_alpha"]
    core_hl = geom["core_highlight"]
    fade_c = max(0.0, min(1.0, fade))

    if aborted:
        pulse = 0.78 + 0.22 * math.sin(t * 36.0)
        rc = (239.0 + 16.0 * pulse) / 255.0
        gc = (58.0 + 40.0 * pulse) / 255.0
        bc = (74.0 + 44.0 * pulse) / 255.0
        alpha_f = np.minimum(base_alpha * (1.10 * fade_c), 1.0)
        out_bgra[..., 0] = (alpha_f * (bc * 255.0) + 0.5).astype(np.uint8)
        out_bgra[..., 1] = (alpha_f * (gc * 255.0) + 0.5).astype(np.uint8)
        out_bgra[..., 2] = (alpha_f * (rc * 255.0) + 0.5).astype(np.uint8)
        out_bgra[..., 3] = (alpha_f * 255.0 + 0.5).astype(np.uint8)
        return out_bgra

    # Evaluate aurora gradient & orbital waves on 1D perimeter axis (~100x faster than 2D)
    r1, g1, b1 = _aurora_rgb_from_phase(u - t * 0.22)
    orbital = np.power(np.abs(np.cos((u - t * 0.34) * (2.0 * math.pi))), 6.0).astype(np.float32)
    orb_boost = orbital * 0.65
    r1 = r1 + (205.0 - r1) * orb_boost
    g1 = g1 + (248.0 - g1) * orb_boost
    b1 = b1 + (255.0 - b1) * orb_boost

    breath = 0.82 + 0.18 * math.sin(t * 2.8)
    alpha_f = base_alpha * ((breath + 0.22 * orbital) * fade_c)
    np.clip(alpha_f, 0.0, 1.0, out=alpha_f)

    alpha_u8 = (alpha_f * 255.0 + 0.5).astype(np.uint8)
    core_w = core_hl * alpha_f
    rest_w = alpha_f - core_w
    out_bgra[..., 0] = np.clip(b1 * rest_w + 255.0 * core_w + 0.5, 0.0, 255.0).astype(np.uint8)
    out_bgra[..., 1] = np.clip(g1 * rest_w + 248.0 * core_w + 0.5, 0.0, 255.0).astype(np.uint8)
    out_bgra[..., 2] = np.clip(r1 * rest_w + 205.0 * core_w + 0.5, 0.0, 255.0).astype(np.uint8)
    out_bgra[..., 3] = alpha_u8
    return out_bgra


def _load_ui_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for fname in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "arialbd.ttf"):
        candidate = win_fonts / fname
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size_px)
            except OSError:
                pass
    return ImageFont.load_default()


def prepare_banner_assets(
    width: int = BANNER_W,
    height: int = BANNER_H,
    scale: float = 1.0,
    max_width: int | None = None,
) -> dict[str, Any]:
    """Precompute 2x supersampled capsule geometry & crisp cached RGBA text layers."""
    ss = 2
    s = max(1.0, float(scale))
    font = _load_ui_font(max(18, round(18 * s)) * ss)
    dummy_draw = ImageDraw.Draw(Image.new("RGBA", (16, 16), (0, 0, 0, 0)))
    max_tw = max(
        dummy_draw.textbbox((0, 0), lbl, font=font)[2] - dummy_draw.textbbox((0, 0), lbl, font=font)[0]
        for lbl in (BANNER_TEXT, ABORT_BANNER_TEXT)
    )
    pad_left = int(height * 0.62 * ss + round(18 * s) * ss)
    pad_right = int(round(26 * s) * ss)
    needed_w = (pad_left + max_tw + pad_right + ss - 1) // ss
    if needed_w > width:
        width = min(max_width, needed_w) if max_width else needed_w

    sw, sh = width * ss, height * ss
    radius = sh // 2 - 2
    border_px = max(4, round(4.5 * s))

    outer_img = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(outer_img).rounded_rectangle([1, 1, sw - 2, sh - 2], radius=radius, fill=255)
    inner_img = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(inner_img).rounded_rectangle(
        [1 + border_px, 1 + border_px, sw - 2 - border_px, sh - 2 - border_px],
        radius=max(4, radius - border_px),
        fill=255,
    )

    outer = np.asarray(outer_img, dtype=np.float32) / 255.0
    inner = np.asarray(inner_img, dtype=np.float32) / 255.0
    ring = np.clip(outer - inner, 0.0, 1.0)

    dot_cx = float(height * 0.56 * ss)

    def _text_layer(label: str) -> np.ndarray:
        txt = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(txt)
        bbox = draw.textbbox((0, 0), label, font=font)
        th = bbox[3] - bbox[1]
        tw = bbox[2] - bbox[0]
        tx = int(dot_cx + round(18 * s) * ss)
        if tx + tw > sw - int(14 * s * ss):
            tx = max(int(dot_cx + 10 * ss), (sw - tw) // 2)
        ty = int((sh - th) * 0.5 - bbox[1] - 1)
        draw.text((tx, ty), label, font=font, fill=(244, 248, 255, 255))
        small = txt.resize((width, height), Image.Resampling.LANCZOS)
        return np.asarray(small, dtype=np.float32) / 255.0

    # Downsample 2x geometric masks once cleanly
    def _down2(arr: np.ndarray) -> np.ndarray:
        img = Image.fromarray((np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8), mode="L")
        return np.asarray(img.resize((width, height), Image.Resampling.LANCZOS), dtype=np.float32) / 255.0

    yy1, xx1 = np.mgrid[0:height, 0:width].astype(np.float32)
    ang1 = (np.arctan2(yy1 - (height - 1) * 0.5, xx1 - (width - 1) * 0.5) / (2.0 * math.pi) + 0.5).astype(np.float32)
    dot1 = np.hypot(xx1 - float(height * 0.56), yy1 - (height - 1) * 0.5).astype(np.float32)

    return {
        "w": width,
        "h": height,
        "scale": s,
        "inner_mask": _down2(inner),
        "ring_mask": _down2(ring),
        "ang_u": ang1,
        "dot_dist": dot1,
        "text_normal": _text_layer(BANNER_TEXT),
        "text_abort": _text_layer(ABORT_BANNER_TEXT),
    }


def render_banner_bgra(
    assets: dict[str, Any],
    t: float,
    *,
    aborted: bool = False,
    fade: float = 1.0,
    out_bgra: np.ndarray | None = None,
) -> np.ndarray:
    """Render premultiplied BGRA floating capsule into `out_bgra` (H x W x 4, uint8)."""
    w, h = assets["w"], assets["h"]
    if out_bgra is None:
        out_bgra = np.empty((h, w, 4), dtype=np.uint8)

    inner = assets["inner_mask"]
    ring = assets["ring_mask"]
    dot_dist = assets["dot_dist"]
    fade_c = max(0.0, min(1.0, fade))

    s = float(assets.get("scale", 1.0))
    if aborted:
        bg_r, bg_g, bg_b, bg_a = 68.0, 14.0, 22.0, 0.94
        ring_r = np.full((h, w), 251.0, dtype=np.float32)
        ring_g = np.full((h, w), 88.0, dtype=np.float32)
        ring_b = np.full((h, w), 108.0, dtype=np.float32)
        dot_rgb = (255.0, 96.0, 114.0)
        text_rgba = assets["text_abort"]
        dot_radius = 6.8 * s
    else:
        bg_r, bg_g, bg_b, bg_a = 11.0, 20.0, 38.0, 0.89
        ring_r, ring_g, ring_b = _aurora_rgb_from_phase(assets["ang_u"] - t * 0.26)
        dot_rgb = (45.0, 232.0, 252.0)
        text_rgba = assets["text_normal"]
        dot_radius = (5.4 + 1.2 * math.sin(t * 4.2)) * s

    comp_a = np.clip(inner * bg_a + ring * 0.95, 0.0, 1.0)
    comp_r = inner * bg_a * bg_r + ring * 0.95 * ring_r
    comp_g = inner * bg_a * bg_g + ring * 0.95 * ring_g
    comp_b = inner * bg_a * bg_b + ring * 0.95 * ring_b

    # Glowing status dot
    dot_core = np.clip(1.0 - (dot_dist - dot_radius) * 0.85, 0.0, 1.0)
    dot_halo = np.exp(-np.square(dot_dist / (dot_radius + 3.6))) * 0.55
    dot_a = np.clip(dot_core + dot_halo, 0.0, 1.0)
    comp_r = comp_r * (1.0 - dot_a) + dot_rgb[0] * dot_a
    comp_g = comp_g * (1.0 - dot_a) + dot_rgb[1] * dot_a
    comp_b = comp_b * (1.0 - dot_a) + dot_rgb[2] * dot_a
    comp_a = np.clip(comp_a + dot_a * (1.0 - comp_a), 0.0, 1.0)

    # Composite sharp cached text
    ta = text_rgba[..., 3]
    comp_r = comp_r * (1.0 - ta) + text_rgba[..., 0] * 255.0 * ta
    comp_g = comp_g * (1.0 - ta) + text_rgba[..., 1] * 255.0 * ta
    comp_b = comp_b * (1.0 - ta) + text_rgba[..., 2] * 255.0 * ta
    comp_a = np.clip(comp_a + ta * (1.0 - comp_a), 0.0, 1.0)

    comp_a = np.clip(comp_a * fade_c, 0.0, 1.0)
    out_bgra[..., 0] = (comp_b * fade_c + 0.5).clip(0.0, 255.0).astype(np.uint8)
    out_bgra[..., 1] = (comp_g * fade_c + 0.5).clip(0.0, 255.0).astype(np.uint8)
    out_bgra[..., 2] = (comp_r * fade_c + 0.5).clip(0.0, 255.0).astype(np.uint8)
    out_bgra[..., 3] = (comp_a * 255.0 + 0.5).astype(np.uint8)
    return out_bgra


class _LayeredSurface:
    """32-bit premultiplied BGRA DIB surface pushed via UpdateLayeredWindow."""

    def __init__(self, user32: Any, gdi32: Any, screen_dc: int, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        self.user32 = user32
        self.gdi32 = gdi32
        self.screen_dc = screen_dc
        self.hwnd = int(hwnd)
        self.x = int(x)
        self.y = int(y)
        self.w = int(w)
        self.h = int(h)

        self.mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.w
        bmi.bmiHeader.biHeight = -self.h  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = _BI_RGB

        bits_ptr = ctypes.c_void_p()
        self.hdib = gdi32.CreateDIBSection(
            screen_dc,
            ctypes.byref(bmi),
            _DIB_RGB_COLORS,
            ctypes.byref(bits_ptr),
            None,
            0,
        )
        if not self.hdib or not bits_ptr.value:
            raise OSError("CreateDIBSection failed for layered surface")
        self.old_obj = gdi32.SelectObject(self.mem_dc, self.hdib)
        raw_buf = (ctypes.c_uint8 * (self.h * self.w * 4)).from_address(bits_ptr.value)
        self.array = np.ctypeslib.as_array(raw_buf).reshape((self.h, self.w, 4))

        self._pt_dst = wintypes.POINT(self.x, self.y)
        self._pt_src = wintypes.POINT(0, 0)
        self._sz = _SIZE(self.w, self.h)
        self._blend = _BLENDFUNCTION(_AC_SRC_OVER, 0, 255, _AC_SRC_ALPHA)

    def commit(self) -> None:
        self.user32.UpdateLayeredWindow(
            self.hwnd,
            self.screen_dc,
            ctypes.byref(self._pt_dst),
            ctypes.byref(self._sz),
            self.mem_dc,
            ctypes.byref(self._pt_src),
            0,
            ctypes.byref(self._blend),
            _ULW_ALPHA,
        )

    def close(self) -> None:
        if self.mem_dc:
            if self.old_obj:
                self.gdi32.SelectObject(self.mem_dc, self.old_obj)
                self.old_obj = None
            self.gdi32.DeleteDC(self.mem_dc)
            self.mem_dc = None
        if self.hdib:
            self.gdi32.DeleteObject(self.hdib)
            self.hdib = None


def _enum_monitors(user32: Any) -> list[tuple[int, int, int, int]]:
    monitors: list[tuple[int, int, int, int]] = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    @MONITORENUMPROC
    def _cb(_hmon: int, _hdc: int, lprc: Any, _data: int) -> bool:
        r = lprc.contents
        w, h = int(r.right - r.left), int(r.bottom - r.top)
        if w > 0 and h > 0:
            monitors.append((int(r.left), int(r.top), w, h))
        return True

    user32.EnumDisplayMonitors(None, None, _cb, 0)
    if not monitors:
        w = int(user32.GetSystemMetrics(0)) or 1920
        h = int(user32.GetSystemMetrics(1)) or 1080
        monitors.append((0, 0, w, h))
    return monitors


def run_guard(abort_file: Path | None, parent_pid: int | None = None, duration: float | None = None) -> int:
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

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
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
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT), ctypes.POINTER(_SIZE),
        wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
        ctypes.POINTER(_BLENDFUNCTION), wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT
    if hasattr(user32, "SetWindowDisplayAffinity"):
        user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL

    hinst = kernel32.GetModuleHandleW(None)
    screen_dc = user32.GetDC(0)

    @WNDPROC
    def wnd_proc(hwnd: int, msg: int, wp: int, lp: int) -> int:
        if msg == _WM_NCHITTEST:
            return _HTTRANSPARENT
        if msg == _WM_MOUSEACTIVATE:
            return _MA_NOACTIVATE
        if msg == _WM_ERASEBKGND:
            return 1
        if msg == _WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    cls_glow = f"ACGlow_{os.getpid()}"
    cls_banner = f"ACBanner_{os.getpid()}"
    for cls_name in (cls_glow, cls_banner):
        wc = WNDCLASSEXW(
            cbSize=ctypes.sizeof(WNDCLASSEXW),
            style=0,
            lpfnWndProc=wnd_proc,
            hInstance=hinst,
            lpszClassName=cls_name,
        )
        user32.RegisterClassExW(ctypes.byref(wc))

    ex_style = _WS_EX_TOPMOST | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_LAYERED | _WS_EX_NOACTIVATE
    monitors = _enum_monitors(user32)

    edge_entries: list[tuple[dict[str, Any], _LayeredSurface]] = []
    hwnds: list[int] = []

    def _make_win(cname: str, x: int, y: int, w: int, h: int) -> int:
        hw = int(user32.CreateWindowExW(ex_style, cname, "", _WS_POPUP, x, y, w, h, 0, 0, hinst, 0) or 0)
        if hw:
            if hasattr(user32, "SetWindowDisplayAffinity"):
                try:
                    user32.SetWindowDisplayAffinity(hw, _WDA_EXCLUDEFROMCAPTURE)
                except Exception:
                    pass
            user32.ShowWindow(hw, _SW_SHOWNOACTIVATE)
            hwnds.append(hw)
        return hw

    surfaces: list[_LayeredSurface] = []
    try:
        for mx, my, mw, mh in monitors:
            m_scale = _display_scale(mw, mh)
            m_depth = max(GLOW_DEPTH, round(GLOW_DEPTH * m_scale))
            for side in ("top", "right", "bottom", "left"):
                geom = build_edge_geometry(side, mw, mh, m_depth, scale=m_scale)
                gx = mx + geom["rel_x"]
                gy = my + geom["rel_y"]
                hw = _make_win(cls_glow, gx, gy, geom["w"], geom["h"])
                if hw:
                    surf = _LayeredSurface(user32, gdi32, screen_dc, hw, gx, gy, geom["w"], geom["h"])
                    surfaces.append(surf)
                    edge_entries.append((geom, surf))

        pmx, pmy, pmw, pmh = monitors[0]
        p_scale = _display_scale(pmw, pmh)
        bw_cap = max(240, pmw - 40)
        bh = max(BANNER_H, round(BANNER_H * p_scale))
        bw_init = min(max(BANNER_W, round(BANNER_W * p_scale)), bw_cap)
        banner_assets = prepare_banner_assets(bw_init, bh, scale=p_scale, max_width=bw_cap)
        bw = int(banner_assets["w"])
        bx = pmx + max(0, (pmw - bw) // 2)
        by = pmy + round(BANNER_TOP_MARGIN * p_scale)
        banner_hw = _make_win(cls_banner, bx, by, bw, bh)
        banner_surf = _LayeredSurface(user32, gdi32, screen_dc, banner_hw, bx, by, bw, bh) if banner_hw else None
        if banner_surf:
            surfaces.append(banner_surf)

        abort_state = {"triggered": False, "at": 0.0}

        def _trigger_abort() -> None:
            if not abort_state["triggered"]:
                abort_state["triggered"] = True
                abort_state["at"] = time.monotonic()
                _write_abort(abort_file)

        @HOOKPROC
        def _kb_proc(n_code: int, wp: int, lp: int) -> int:
            if n_code >= 0 and wp in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                kb = ctypes.cast(lp, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                is_injected = (int(kb.flags) & _LLKHF_INJECTED) != 0
                if not is_injected and int(kb.vkCode) in (VK_ESCAPE, VK_PAUSE):
                    _trigger_abort()
                    return 1
            return user32.CallNextHookEx(0, n_code, wp, lp)

        hook_handle = user32.SetWindowsHookExW(_WH_KEYBOARD_LL, _kb_proc, hinst, 0)
        msg = wintypes.MSG()
        start_t = time.monotonic()
        last_topmost = start_t
        frame_interval = 1.0 / TARGET_FPS

        try:
            while True:
                now = time.monotonic()
                elapsed = now - start_t

                if duration is not None and duration > 0 and elapsed >= duration and not abort_state["triggered"]:
                    break
                if not _parent_alive(kernel32, parent_pid):
                    break

                if not hook_handle:
                    if (user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000) or (user32.GetAsyncKeyState(VK_PAUSE) & 0x8000):
                        _trigger_abort()

                while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

                aborted = abort_state["triggered"]
                raw_fade = min(1.0, elapsed / FADE_IN_SECONDS)
                fade = 1.0 - (1.0 - raw_fade) ** 2
                if aborted:
                    fade = 1.0

                for geom, surf in edge_entries:
                    render_edge_bgra(geom, elapsed, aborted=aborted, fade=fade, out_bgra=surf.array)
                    surf.commit()
                if banner_surf is not None:
                    render_banner_bgra(banner_assets, elapsed, aborted=aborted, fade=fade, out_bgra=banner_surf.array)
                    banner_surf.commit()

                if aborted and (now - abort_state["at"]) >= ABORT_FLASH_SECONDS:
                    break

                if (now - last_topmost) >= 0.45:
                    last_topmost = now
                    flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW
                    for hw in hwnds:
                        user32.SetWindowPos(hw, _HWND_TOPMOST, 0, 0, 0, 0, flags)

                step_cost = time.monotonic() - now
                sleep_s = max(0.005, frame_interval - step_cost)
                time.sleep(sleep_s)
        finally:
            if hook_handle:
                user32.UnhookWindowsHookEx(hook_handle)
    finally:
        for surf in surfaces:
            surf.close()
        for hw in hwnds:
            user32.DestroyWindow(hw)
        for cname in (cls_glow, cls_banner):
            user32.UnregisterClassW(cname, hinst)
        if screen_dc:
            user32.ReleaseDC(0, screen_dc)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abort-file", default=None)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--duration", type=float, default=None)
    args = parser.parse_args()
    abort_path = Path(args.abort_file) if args.abort_file else None
    return run_guard(abort_path, args.parent_pid, args.duration)


if __name__ == "__main__":
    raise SystemExit(main())
