from __future__ import annotations

import argparse
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

from _common import configure_utf8_stdio, emit_json, parse_json_arg

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _estimate_bg_color(rgb_arr: np.ndarray) -> np.ndarray:
    h, w, _ = rgb_arr.shape
    bw = max(1, min(3, h // 10, w // 10))
    border = np.concatenate(
        [
            rgb_arr[:bw, :, :].reshape(-1, 3),
            rgb_arr[-bw:, :, :].reshape(-1, 3),
            rgb_arr[:, :bw, :].reshape(-1, 3),
            rgb_arr[:, -bw:, :].reshape(-1, 3),
        ],
        axis=0,
    )
    return np.median(border, axis=0)


def detect_slots(ref_path: str, min_area: int = 2500, bg_tol: int = 18) -> tuple[list[dict[str, int]], list[int]]:
    img = Image.open(ref_path).convert("RGB")
    w, h = img.size
    arr = np.asarray(img, dtype=np.int16)
    bg = _estimate_bg_color(arr).astype(np.int16)

    scale = min(1.0, 400.0 / max(w, h))
    sw = max(1, int(round(w * scale)))
    sh = max(1, int(round(h * scale)))
    small_img = img.resize((sw, sh), Image.Resampling.BILINEAR)
    small_arr = np.asarray(small_img, dtype=np.int16)

    diff = np.max(np.abs(small_arr - bg[None, None, :]), axis=2)
    mask = diff > bg_tol

    visited = np.zeros((sh, sw), dtype=bool)
    min_small_area = max(16, int(min_area * (scale ** 2)))
    raw_boxes: list[dict[str, int]] = []

    ys, xs = np.nonzero(mask)
    for sy0, sx0 in zip(ys.tolist(), xs.tolist()):
        if visited[sy0, sx0]:
            continue
        q = deque([(sy0, sx0)])
        visited[sy0, sx0] = True
        min_x = max_x = sx0
        min_y = max_y = sy0
        pix_count = 0
        while q:
            cy, cx = q.popleft()
            pix_count += 1
            if cx < min_x:
                min_x = cx
            if cx > max_x:
                max_x = cx
            if cy < min_y:
                min_y = cy
            if cy > max_y:
                max_y = cy
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < sh and 0 <= nx < sw and not visited[ny, nx] and mask[ny, nx]:
                    visited[ny, nx] = True
                    q.append((ny, nx))
        if pix_count < min_small_area:
            continue
        rx = max(0, int(round(min_x / scale)))
        ry = max(0, int(round(min_y / scale)))
        rx2 = min(w, int(round((max_x + 1) / scale)))
        ry2 = min(h, int(round((max_y + 1) / scale)))
        rw = rx2 - rx
        rh = ry2 - ry
        if rw * rh >= min_area:
            raw_boxes.append({"x": rx, "y": ry, "w": rw, "h": rh})

    # Row-major sort (group slots within half average height as same row)
    if raw_boxes:
        row_tol = max(20, int(np.median([b["h"] for b in raw_boxes]) * 0.5))
        raw_boxes.sort(key=lambda b: (b["y"] // row_tol, b["x"]))

    return raw_boxes, [int(bg[0]), int(bg[1]), int(bg[2])]


def _fit_image(asset: Image.Image, target_w: int, target_h: int, fit_mode: str, bg_rgb: tuple[int, int, int]) -> Image.Image:
    rgb = asset.convert("RGB")
    if fit_mode == "cover":
        return ImageOps.fit(rgb, (target_w, target_h), method=Image.Resampling.LANCZOS)
    contained = ImageOps.contain(rgb, (target_w, target_h), method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), bg_rgb)
    ox = (target_w - contained.width) // 2
    oy = (target_h - contained.height) // 2
    canvas.paste(contained, (ox, oy))
    return canvas


def _similarity_score(ref_crop: Image.Image, cand_img: Image.Image, fit_mode: str, bg_rgb: tuple[int, int, int]) -> float:
    fitted = _fit_image(cand_img, ref_crop.width, ref_crop.height, fit_mode, bg_rgb)
    ref_small = np.asarray(ref_crop.convert("L").resize((64, 64), Image.Resampling.BILINEAR), dtype=np.float32)
    cand_small = np.asarray(fitted.convert("L").resize((64, 64), Image.Resampling.BILINEAR), dtype=np.float32)

    r_zm = ref_small - ref_small.mean()
    c_zm = cand_small - cand_small.mean()
    denom = float(np.linalg.norm(r_zm) * np.linalg.norm(c_zm))
    if denom > 1e-6:
        ncc = float(np.sum(r_zm * c_zm) / denom)
    else:
        ncc = 1.0 if float(np.max(np.abs(ref_small - cand_small))) < 2.0 else 0.0
    ncc_norm = max(0.0, min(1.0, (ncc + 1.0) / 2.0))

    r_rgb = np.asarray(ref_crop.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR), dtype=np.uint8)
    c_rgb = np.asarray(fitted.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR), dtype=np.uint8)
    r_hist = np.concatenate([np.histogram(r_rgb[:, :, ch], bins=16, range=(0, 256))[0] for ch in range(3)]).astype(np.float32)
    c_hist = np.concatenate([np.histogram(c_rgb[:, :, ch], bins=16, range=(0, 256))[0] for ch in range(3)]).astype(np.float32)
    h_denom = float(np.linalg.norm(r_hist) * np.linalg.norm(c_hist))
    hist_cos = float(np.dot(r_hist, c_hist) / h_denom) if h_denom > 1e-6 else 0.0
    hist_cos = max(0.0, min(1.0, hist_cos))

    return round(0.65 * ncc_norm + 0.35 * hist_cos, 6)


def _hungarian_max(score_matrix: np.ndarray) -> list[tuple[int, int]]:
    """Solve rectangular maximum-weight bipartite assignment via O(n^2 m) Hungarian algorithm."""
    n_rows, n_cols = score_matrix.shape
    transposed = False
    if n_rows > n_cols:
        score_matrix = score_matrix.T
        n_rows, n_cols = n_cols, n_rows
        transposed = True

    cost = -score_matrix
    u = np.zeros(n_rows + 1, dtype=np.float64)
    v = np.zeros(n_cols + 1, dtype=np.float64)
    p = np.zeros(n_cols + 1, dtype=np.int64)
    way = np.zeros(n_cols + 1, dtype=np.int64)

    for i in range(1, n_rows + 1):
        p[0] = i
        j0 = 0
        minv = np.full(n_cols + 1, np.inf, dtype=np.float64)
        used = np.zeros(n_cols + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = np.inf
            j1 = 0
            for j in range(1, n_cols + 1):
                if not used[j]:
                    cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n_cols + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    pairs: list[tuple[int, int]] = []
    for j in range(1, n_cols + 1):
        if p[j] != 0:
            r_idx = int(p[j] - 1)
            c_idx = int(j - 1)
            pairs.append((c_idx, r_idx) if transposed else (r_idx, c_idx))
    pairs.sort(key=lambda x: x[0])
    return pairs


def _load_assets(assets_dir: str) -> list[tuple[Path, Image.Image]]:
    d = Path(assets_dir).resolve()
    if not d.is_dir():
        raise FileNotFoundError(f"Assets directory not found: {d}")
    files = sorted([p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], key=lambda p: p.name.lower())
    if not files:
        raise ValueError(f"No image assets found in {d}")
    loaded: list[tuple[Path, Image.Image]] = []
    for f in files:
        with Image.open(f) as im:
            loaded.append((f, im.convert("RGB")))
    return loaded


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Match and compose product layout from reference image")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_det = sub.add_parser("detect-slots")
    p_det.add_argument("--reference", required=True)
    p_det.add_argument("--min-area", type=int, default=2500)
    p_det.add_argument("--bg-tol", type=int, default=18)

    p_mr = sub.add_parser("match-region")
    p_mr.add_argument("--reference", required=True)
    p_mr.add_argument("--region", required=True, help="X,Y,W,H")
    p_mr.add_argument("--assets", required=True)
    p_mr.add_argument("--output")
    p_mr.add_argument("--fit", choices=["cover", "contain"], default="cover")

    p_comp = sub.add_parser("compose")
    p_comp.add_argument("--reference", required=True)
    p_comp.add_argument("--assets", required=True)
    p_comp.add_argument("--output", required=True)
    group = p_comp.add_mutually_exclusive_group(required=True)
    group.add_argument("--slots")
    group.add_argument("--auto-slots", action="store_true")
    p_comp.add_argument("--min-area", type=int, default=2500)
    p_comp.add_argument("--bg-tol", type=int, default=18)
    p_comp.add_argument("--fit", choices=["cover", "contain"], default="cover")
    p_comp.add_argument("--canvas-bg", choices=["ref", "auto", "white"], default="ref")

    args = parser.parse_args()
    t0 = time.perf_counter()

    try:
        if args.cmd == "detect-slots":
            slots, bg = detect_slots(args.reference, args.min_area, args.bg_tol)
            emit_json(
                {
                    "ok": True,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "bg_rgb": bg,
                    "count": len(slots),
                    "slots": slots,
                }
            )

        elif args.cmd == "match-region":
            x, y, rw, rh = [int(v.strip()) for v in args.region.split(",")]
            ref_img = Image.open(args.reference).convert("RGB")
            bg = tuple(int(v) for v in _estimate_bg_color(np.asarray(ref_img, dtype=np.int16)))
            crop = ref_img.crop((x, y, x + rw, y + rh))
            assets = _load_assets(args.assets)

            ranked = []
            for path, im in assets:
                sc = _similarity_score(crop, im, args.fit, bg)
                ranked.append({"asset": str(path), "name": path.name, "score": sc})
            ranked.sort(key=lambda r: r["score"], reverse=True)

            out_path = None
            if args.output and ranked:
                best_path = Path(ranked[0]["asset"])
                with Image.open(best_path) as bim:
                    fitted = _fit_image(bim, rw, rh, args.fit, bg)
                op = Path(args.output).resolve()
                op.parent.mkdir(parents=True, exist_ok=True)
                fitted.save(op)
                out_path = str(op)

            emit_json(
                {
                    "ok": True,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "best": ranked[0] if ranked else None,
                    "ranked": ranked,
                    "output": out_path,
                }
            )

        elif args.cmd == "compose":
            ref_img = Image.open(args.reference).convert("RGB")
            ref_arr = np.asarray(ref_img, dtype=np.int16)
            auto_bg = tuple(int(v) for v in _estimate_bg_color(ref_arr))

            if args.auto_slots:
                slots, _ = detect_slots(args.reference, args.min_area, args.bg_tol)
            else:
                raw_slots = parse_json_arg(args.slots)
                slots = raw_slots["slots"] if isinstance(raw_slots, dict) else raw_slots

            if not slots:
                emit_json({"ok": False, "error": "No slots provided or detected"}, 1)

            assets = _load_assets(args.assets)
            if len(assets) < len(slots):
                emit_json({"ok": False, "error": f"Need at least {len(slots)} assets, found {len(assets)}"}, 1)

            scores = np.zeros((len(slots), len(assets)), dtype=np.float64)
            for si, slot in enumerate(slots):
                sx, sy, sw, sh = int(slot["x"]), int(slot["y"]), int(slot["w"]), int(slot["h"])
                crop = ref_img.crop((sx, sy, sx + sw, sy + sh))
                for ai, (_, aim) in enumerate(assets):
                    scores[si, ai] = _similarity_score(crop, aim, args.fit, auto_bg)

            pairs = _hungarian_max(scores)
            if args.canvas_bg == "ref":
                canvas = ref_img.copy()
                fill_bg = auto_bg
            elif args.canvas_bg == "white":
                fill_bg = (255, 255, 255)
                canvas = Image.new("RGB", ref_img.size, fill_bg)
            else:
                fill_bg = auto_bg
                canvas = Image.new("RGB", ref_img.size, fill_bg)

            assignments: list[dict[str, Any]] = []
            for si, ai in pairs:
                slot = slots[si]
                sx, sy, sw, sh = int(slot["x"]), int(slot["y"]), int(slot["w"]), int(slot["h"])
                apath, aim = assets[ai]
                fitted = _fit_image(aim, sw, sh, args.fit, fill_bg)
                canvas.paste(fitted, (sx, sy))
                assignments.append(
                    {
                        "slot_index": si,
                        "slot": slot,
                        "asset": apath.name,
                        "asset_path": str(apath),
                        "score": round(float(scores[si, ai]), 6),
                    }
                )

            out_p = Path(args.output).resolve()
            out_p.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(out_p)
            emit_json(
                {
                    "ok": True,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "output": str(out_p),
                    "output_size": list(canvas.size),
                    "assignments": assignments,
                }
            )

    except Exception as exc:
        emit_json({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
