"""Read-only client-window capture and exact keyword OCR using local Windows OCR."""
from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

if os.name != "nt":
    raise SystemExit("window_ocr.py requires Windows")

CAPTURE_TIMEOUT_SECONDS = 4.0
TOTAL_TIMEOUT_SECONDS = 15.0
CLIENT_MIN_SIZE = 100
CLIENT_MAX_SIZE = 6000
REGION_MIN_SIZE = 16


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _emit(value: dict[str, Any], code: int) -> int:
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
    return code


def _parse_region(raw: str | None) -> tuple[int, int, int, int] | None:
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("--region must have exactly 4 comma-separated integers: x,y,w,h")
    x, y, w, h = (int(p) for p in parts)
    if x < 0 or y < 0 or w < REGION_MIN_SIZE or h < REGION_MIN_SIZE:
        raise ValueError(f"--region requires x>=0, y>=0, w>={REGION_MIN_SIZE}, h>={REGION_MIN_SIZE}")
    return x, y, w, h


def _word_box(word: Any) -> tuple[float, float, float, float]:
    rect = word.bounding_rect
    return float(rect.x), float(rect.y), float(rect.width), float(rect.height)


def _match_ocr_lines(
    lines: Any,
    keywords: list[str],
    offset_x: int = 0,
    offset_y: int = 0,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for line in lines:
        words = list(line.words)
        for term in keywords:
            has_ws = any(ch in term for ch in (" ", "\t", "\r", "\n"))
            if has_ws:
                needle = term.split()
                if not needle or len(needle) > len(words):
                    continue
                for i in range(len(words) - len(needle) + 1):
                    if all(str(words[i + j].text).lower() == needle[j].lower() for j in range(len(needle))):
                        boxes = [_word_box(words[i + j]) for j in range(len(needle))]
                        x1 = min(b[0] for b in boxes)
                        y1 = min(b[1] for b in boxes)
                        x2 = max(b[0] + b[2] for b in boxes)
                        y2 = max(b[1] + b[3] for b in boxes)
                        hits.append(
                            {
                                "keyword": term,
                                "rect": {
                                    "x": round(x1 + offset_x, 2),
                                    "y": round(y1 + offset_y, 2),
                                    "width": round(x2 - x1, 2),
                                    "height": round(y2 - y1, 2),
                                },
                            }
                        )
                continue

            term_lower = term.lower()
            for i in range(len(words)):
                joined = ""
                last = min(len(words) - 1, i + len(term) - 1)
                for j in range(i, last + 1):
                    if j > i:
                        px, _, pw, ph = _word_box(words[j - 1])
                        cx, _, _, ch = _word_box(words[j])
                        gap = cx - (px + pw)
                        max_gap = max(8.0, max(ph, ch) * 0.4)
                        if gap < -2.0 or gap > max_gap:
                            break
                    joined += str(words[j].text)
                    joined_lower = joined.lower()
                    if joined_lower == term_lower:
                        boxes = [_word_box(words[k]) for k in range(i, j + 1)]
                        x1 = min(b[0] for b in boxes)
                        y1 = min(b[1] for b in boxes)
                        x2 = max(b[0] + b[2] for b in boxes)
                        y2 = max(b[1] + b[3] for b in boxes)
                        hits.append(
                            {
                                "keyword": term,
                                "rect": {
                                    "x": round(x1 + offset_x, 2),
                                    "y": round(y1 + offset_y, 2),
                                    "width": round(x2 - x1, 2),
                                    "height": round(y2 - y1, 2),
                                },
                            }
                        )
                        break
                    if not term_lower.startswith(joined_lower):
                        break
    return hits


def _recognize_in_memory(
    bgra_bytes: bytes,
    width: int,
    height: int,
    keywords: list[str],
    language_tag: str,
    offset_x: int = 0,
    offset_y: int = 0,
) -> dict[str, Any]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    tags = [str(lang.language_tag) for lang in OcrEngine.available_recognizer_languages]
    avail = {
        "zh-Hans": any(t.lower() == "zh-hans" or t.lower().startswith("zh-hans-") for t in tags),
        "en": any(t.lower() == "en" or t.lower().startswith("en-") for t in tags),
    }
    req = language_tag.lower()
    selected_tag = next((t for t in tags if t.lower() == req or t.lower().startswith(f"{req}-")), None)
    if selected_tag is None:
        return {
            "status": "language-unavailable",
            "language": language_tag,
            "availableLanguages": avail,
            "region": {"left": offset_x, "top": offset_y, "width": width, "height": height},
            "hits": [],
        }

    engine = OcrEngine.try_create_from_language(Language(selected_tag))
    if engine is None:
        return {
            "status": "language-unavailable",
            "language": selected_tag,
            "availableLanguages": avail,
            "region": {"left": offset_x, "top": offset_y, "width": width, "height": height},
            "hits": [],
        }

    async def _run() -> Any:
        writer = DataWriter()
        writer.write_bytes(bgra_bytes)
        buf = writer.detach_buffer()
        bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.IGNORE)
        bmp.copy_from_buffer(buf)
        try:
            return await engine.recognize_async(bmp)
        finally:
            bmp.close()

    ocr_result = asyncio.run(_run())
    hits = _match_ocr_lines(ocr_result.lines, keywords, offset_x=offset_x, offset_y=offset_y)
    return {
        "status": "ok" if hits else "no-hit",
        "language": selected_tag,
        "availableLanguages": avail,
        "region": {"left": offset_x, "top": offset_y, "width": width, "height": height},
        "hits": hits,
    }


def _capture_and_ocr_worker(
    hwnd: int,
    pid: int,
    output: Path | None,
    keywords: list[str] | None = None,
    language_tag: str = "zh-Hans-CN",
    region: tuple[int, int, int, int] | None = None,
    direct_ocr: bool = False,
) -> int:
    import numpy as np
    import win32gui
    import win32ui
    from PIL import Image, ImageStat

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import win_background as background
    from desktop_policy import ensure_unprotected_pid

    ensure_unprotected_pid(pid)

    dpi_api = getattr(background.user32, "SetProcessDpiAwarenessContext", None)
    if dpi_api is not None:
        dpi_api.argtypes = [ctypes.c_void_p]
        dpi_api.restype = ctypes.c_bool
        dpi_api(ctypes.c_void_p(-4))
    background.user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    background.user32.PrintWindow.restype = ctypes.c_bool

    window = background._verify_target(hwnd, pid)
    before, watch = background._begin_observation()
    started = time.perf_counter()
    raw_bytes: bytes | None = None
    crop_x, crop_y, crop_w, crop_h = 0, 0, 0, 0
    try:
        state = background._state(window)
        _, _, width, height = win32gui.GetClientRect(window)
        result: dict[str, Any] = {"window_state": state, "client_size": [width, height]}
        if CLIENT_MIN_SIZE <= width <= CLIENT_MAX_SIZE and CLIENT_MIN_SIZE <= height <= CLIENT_MAX_SIZE:
            if region is not None:
                crop_x, crop_y, crop_w, crop_h = region
                if crop_x + crop_w > width or crop_y + crop_h > height:
                    raise ValueError(f"region {region} exceeds client_size {[width, height]}")
            else:
                crop_x, crop_y, crop_w, crop_h = 0, 0, width, height

            window_dc = win32gui.GetWindowDC(window)
            source = win32ui.CreateDCFromHandle(window_dc)
            memory = source.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(source, width, height)
            prior_bitmap = memory.SelectObject(bitmap)
            try:
                for flags in (3, 1):
                    if not background.user32.PrintWindow(
                        background.HWND(window), ctypes.c_void_p(memory.GetSafeHdc()), flags
                    ):
                        continue
                    pixels = bitmap.GetBitmapBits(True)
                    if region is not None:
                        arr = np.frombuffer(pixels, dtype=np.uint8).reshape((height, width, 4))
                        cropped = np.ascontiguousarray(arr[crop_y : crop_y + crop_h, crop_x : crop_x + crop_w, :])
                        target_bytes = cropped.tobytes()
                    else:
                        target_bytes = pixels
                    image = Image.frombytes("RGB", (crop_w, crop_h), target_bytes, "raw", "BGRX", 0, 1)
                    deviation = round(max(ImageStat.Stat(image).stddev), 2)
                    result.update({"print_window_flags": flags, "pixel_deviation": deviation})
                    if deviation > 2:
                        raw_bytes = target_bytes
                        result["capture_saved"] = True
                        if not direct_ocr and output is not None:
                            with output.open("xb") as file:
                                image.save(file, format="PNG")
                        break
            finally:
                if prior_bitmap is not None:
                    memory.SelectObject(prior_bitmap)
                memory.DeleteDC()
                win32gui.DeleteObject(bitmap.GetHandle())
                win32gui.ReleaseDC(window, window_dc)

        result.update(background._observe(before, watch))
        capture_done = time.perf_counter()
        result["capture_ms"] = round((capture_done - started) * 1000, 1)

        if direct_ocr and result.get("capture_saved") and raw_bytes is not None and keywords is not None:
            try:
                ocr = _recognize_in_memory(
                    raw_bytes, crop_w, crop_h, keywords, language_tag, offset_x=crop_x, offset_y=crop_y
                )
                result["ocr"] = ocr
                result["backend"] = "winrt-direct"
                result["ocr_ms"] = round((time.perf_counter() - capture_done) * 1000, 1)
            except ImportError:
                if output is not None:
                    image = Image.frombytes("RGB", (crop_w, crop_h), raw_bytes, "raw", "BGRX", 0, 1)
                    with output.open("xb") as file:
                        image.save(file, format="PNG")
                result["fallback_ps1"] = True
                result["backend"] = "ps1-fallback"

        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0 if result.get("capture_saved") else 2
    finally:
        watch.close()


def _run_capture_or_direct(
    hwnd: int,
    pid: int,
    png: Path,
    keywords: list[str],
    language_tag: str,
    region: tuple[int, int, int, int] | None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_capture-worker",
        "--hwnd",
        hex(hwnd),
        "--pid",
        str(pid),
        "--png",
        str(png),
        "--language-tag",
        language_tag,
        "--direct-ocr",
    ]
    for kw in keywords:
        command.extend(["--keyword", kw])
    if region is not None:
        command.extend(["--region", ",".join(str(v) for v in region)])

    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TOTAL_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Capture/OCR worker exceeded {TOTAL_TIMEOUT_SECONDS:.1f}s") from exc
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise RuntimeError("PrintWindow worker returned invalid JSON") from exc
    if proc.returncode != 0 or not result.get("capture_saved"):
        raise RuntimeError(f"PrintWindow capture failed: {result}")
    return result


def _run_ocr_ps1(png: Path, keywords: list[str], language_tag: str, timeout: float) -> dict[str, Any]:
    helper = Path(__file__).resolve().with_name("local_ocr.ps1")
    if not helper.is_file():
        raise FileNotFoundError(f"local OCR helper is missing: {helper.name}")
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32",
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    if not os.path.isfile(powershell):
        powershell = "powershell.exe"
    keyword_array = ",".join(_ps_quote(item) for item in keywords)
    ps = (
        "$utf8=[System.Text.UTF8Encoding]::new($false); $OutputEncoding=$utf8; [Console]::OutputEncoding=$utf8; "
        f"& {_ps_quote(str(helper))} -Png {_ps_quote(str(png))} "
        f"-Keyword @({keyword_array}) -LanguageTag {_ps_quote(language_tag)}"
    )
    encoded = base64.b64encode(ps.encode("utf-16le")).decode("ascii")
    command = [
        powershell,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "RemoteSigned",
        "-EncodedCommand",
        encoded,
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(0.1, timeout),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("local OCR exceeded the observation time limit") from exc
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise RuntimeError("local OCR helper returned invalid JSON") from exc
    if proc.returncode not in (0, 2):
        raise RuntimeError(f"local OCR helper failed with exit code {proc.returncode}")
    return {
        "status": result.get("status"),
        "language": result.get("language"),
        "availableLanguages": result.get("availableLanguages"),
        "region": result.get("region"),
        "hits": result.get("hits", []),
    }


def _observe_begin() -> tuple[dict[str, Any], Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import win_background as background

    return background._begin_observation()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hwnd", required=True, type=lambda value: int(value, 0))
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--language-tag", default="zh-Hans-CN")
    parser.add_argument("--region", help="optional client crop rectangle x,y,w,h")
    parser.add_argument("--_capture-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_ocr-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--direct-ocr", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--png", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        region = _parse_region(args.region)
    except ValueError as exc:
        return _emit({"ok": False, "error": str(exc)}, 1)

    if args._capture_worker or args._ocr_worker:
        if not args.direct_ocr and args.png is None:
            return _emit({"ok": False, "error": "capture worker requires --png"}, 1)
        try:
            return _capture_and_ocr_worker(
                args.hwnd,
                args.pid,
                args.png,
                keywords=args.keyword,
                language_tag=args.language_tag,
                region=region,
                direct_ocr=bool(args.direct_ocr or args._ocr_worker),
            )
        except Exception as exc:
            return _emit({"ok": False, "error": str(exc)}, 1)

    if args.pid <= 0 or not args.keyword or any(not item.strip() for item in args.keyword):
        return _emit({"ok": False, "error": "PID must be positive and keywords must be nonempty"}, 1)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from desktop_policy import ensure_unprotected_pid

        ensure_unprotected_pid(args.pid)
    except Exception as exc:
        return _emit({"ok": False, "error": str(exc)}, 1)
    started = time.monotonic()
    try:
        before, watch = _observe_begin()
    except Exception as exc:
        return _emit({"ok": False, "error": str(exc)}, 1)
    try:
        with tempfile.TemporaryDirectory(prefix="window-ocr-") as temp_dir:
            png = Path(temp_dir) / "client.png"
            worker = _run_capture_or_direct(args.hwnd, args.pid, png, args.keyword, args.language_tag, region)
            backend = worker.get("backend", "winrt-direct")
            if "ocr" in worker:
                ocr = worker["ocr"]
            else:
                remaining = TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("total observation exceeded its time limit before OCR")
                t_ps = time.perf_counter()
                ocr = _run_ocr_ps1(png, args.keyword, args.language_tag, remaining)
                worker["ocr_ms"] = round((time.perf_counter() - t_ps) * 1000, 1)
                backend = "ps1-fallback"
                if region is not None:
                    rx, ry, _, _ = region
                    for h in ocr.get("hits", []):
                        h["rect"]["x"] = round(float(h["rect"]["x"]) + rx, 2)
                        h["rect"]["y"] = round(float(h["rect"]["y"]) + ry, 2)
                    if isinstance(ocr.get("region"), dict):
                        ocr["region"]["left"] = rx
                        ocr["region"]["top"] = ry

            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import win_background as background

            observation = background._observe(before, watch)
            capture_info = {
                k: worker.get(k)
                for k in (
                    "window_state",
                    "client_size",
                    "print_window_flags",
                    "pixel_deviation",
                    "capture_ms",
                    "ocr_ms",
                )
            }
            capture_info["backend"] = backend
            result = {
                "ok": ocr["status"] in {"ok", "no-hit"},
                "operation": "window-ocr-observe",
                "hwnd": f"0x{args.hwnd:X}",
                "pid": args.pid,
                "language": ocr["language"],
                "status": ocr["status"],
                "hits": ocr["hits"],
                "region": ocr["region"],
                "availableLanguages": ocr["availableLanguages"],
                "capture": capture_info,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                **observation,
            }
            return _emit(result, 0 if result["ok"] else 2)
    except Exception as exc:
        return _emit(
            {
                "ok": False,
                "operation": "window-ocr-observe",
                "hwnd": f"0x{args.hwnd:X}",
                "pid": args.pid,
                "error": str(exc),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            },
            1,
        )
    finally:
        try:
            watch.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
