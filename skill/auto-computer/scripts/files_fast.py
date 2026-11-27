from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from _common import configure_utf8_stdio, emit_json, parse_json_arg


def _read_text_safe(path: Path, max_bytes: int = 2_000_000) -> str:
    with path.open("rb") as fh:
        data = fh.read(max(1, int(max_bytes)))
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def find_files(
    root: str,
    globs: list[str] | None = None,
    regex: str | None = None,
    contains: str | None = None,
    exts: list[str] | None = None,
    recursive: bool = False,
    max_depth: int | None = None,
) -> list[dict[str, Any]]:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"Root directory not found: {root}")

    norm_exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (exts or []) if e}
    rx = re.compile(regex) if regex else None
    results: list[dict[str, Any]] = []

    def walk_dir(current: Path, depth: int) -> None:
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.is_dir():
                if recursive and (max_depth is None or depth < max_depth):
                    walk_dir(entry, depth + 1)
                continue
            if not entry.is_file():
                continue
            name = entry.name
            if norm_exts and entry.suffix.lower() not in norm_exts:
                continue
            if globs and not any(fnmatch.fnmatch(name.lower(), g.lower()) for g in globs):
                continue
            if rx and not rx.search(name):
                continue
            if contains is not None:
                text = _read_text_safe(entry)
                if contains not in text:
                    continue
            st = entry.stat()
            results.append(
                {
                    "path": str(entry),
                    "name": name,
                    "size": st.st_size,
                    "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                }
            )

    walk_dir(root_path, 0)
    return results


def _unique_rename_path(dst: Path) -> Path:
    stem, suffix = dst.stem, dst.suffix
    idx = 1
    while True:
        cand = dst.with_name(f"{stem} ({idx}){suffix}")
        if not cand.exists():
            return cand
        idx += 1


def _resolve_target(op: str, src: Path, raw_dst: str) -> Path:
    dst = Path(raw_dst)
    if op in ("move", "copy") and (
        dst.is_dir() or raw_dst.endswith(("/", "\\"))
    ):
        return dst / src.name
    return dst


def execute_plan(
    actions: list[dict[str, Any]],
    on_conflict: str = "fail",
    dry_run: bool = False,
    manifest_path: str | None = None,
) -> dict[str, Any]:
    resolved_ops: list[dict[str, Any]] = []
    undo_ops: list[dict[str, Any]] = []

    for item in actions:
        op = str(item.get("op", "")).lower()
        if op not in ("move", "copy", "rename"):
            return {"ok": False, "error": f"Unsupported op: {op}"}
        src = Path(item["src"]).resolve()
        if not src.exists() or not src.is_file():
            return {"ok": False, "error": f"Source file missing: {src}"}

        dst = _resolve_target(op, src, str(item["dst"])).resolve()
        if src == dst:
            resolved_ops.append({"op": op, "src": str(src), "dst": str(dst), "status": "noop"})
            continue

        if dst.exists():
            if on_conflict == "fail":
                return {"ok": False, "error": f"Destination already exists: {dst}"}
            if on_conflict == "skip":
                resolved_ops.append({"op": op, "src": str(src), "dst": str(dst), "status": "skipped"})
                continue
            if on_conflict == "rename":
                dst = _unique_rename_path(dst)

        src_size = src.stat().st_size
        resolved_ops.append(
            {"op": op, "src": str(src), "dst": str(dst), "size": src_size, "status": "planned"}
        )
        undo_ops.append(
            {
                "op": op,
                "src": str(src),
                "dst": str(dst),
                "existed_before": dst.exists(),
            }
        )

    if dry_run:
        return {"ok": True, "dry_run": True, "operations": resolved_ops}

    manifest_file = (
        Path(manifest_path).resolve()
        if manifest_path
        else Path(tempfile.gettempdir()) / f"win_fast_ops_undo_{int(time.time() * 1000)}.json"
    )
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "operations": undo_ops,
    }
    manifest_file.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    executed: list[dict[str, Any]] = []
    for entry in resolved_ops:
        if entry["status"] != "planned":
            executed.append(entry)
            continue
        op = entry["op"]
        src = Path(entry["src"])
        dst = Path(entry["dst"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        if op in ("move", "rename"):
            shutil.move(str(src), str(dst))
        elif op == "copy":
            shutil.copy2(str(src), str(dst))

        if not dst.exists() or dst.stat().st_size != entry["size"]:
            return {
                "ok": False,
                "error": f"Post-op verification failed on destination: {dst}",
                "manifest": str(manifest_file),
                "operations": executed,
            }
        if op in ("move", "rename") and src.exists():
            return {
                "ok": False,
                "error": f"Post-op verification failed, source still exists: {src}",
                "manifest": str(manifest_file),
                "operations": executed,
            }
        entry["status"] = "verified"
        executed.append(entry)

    return {
        "ok": True,
        "dry_run": False,
        "manifest": str(manifest_file),
        "count": sum(1 for e in executed if e["status"] == "verified"),
        "operations": executed,
    }


def undo_manifest(manifest_path: str) -> dict[str, Any]:
    p = Path(manifest_path).resolve()
    if not p.exists():
        return {"ok": False, "error": f"Manifest not found: {p}"}
    payload = json.loads(p.read_text(encoding="utf-8-sig"))
    ops = payload.get("operations", [])
    restored: list[dict[str, Any]] = []

    for entry in reversed(ops):
        op = entry["op"]
        src = Path(entry["src"])
        dst = Path(entry["dst"])
        if op in ("move", "rename"):
            if not dst.exists():
                return {"ok": False, "error": f"Cannot undo, destination missing: {dst}"}
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            if not src.exists() or dst.exists():
                return {"ok": False, "error": f"Undo verification failed for {src}"}
            restored.append({"op": op, "restored_to": str(src)})
        elif op == "copy":
            if not entry.get("existed_before", False) and dst.exists():
                dst.unlink()
            restored.append({"op": op, "removed_copy": str(dst)})

    return {"ok": True, "count": len(restored), "restored": restored}


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Fast headless Windows file operations")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find")
    p_find.add_argument("--root", required=True)
    p_find.add_argument("--glob", action="append", default=[])
    p_find.add_argument("--regex")
    p_find.add_argument("--contains")
    p_find.add_argument("--ext", default="")
    p_find.add_argument("--recursive", action="store_true")
    p_find.add_argument("--max-depth", type=int)

    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--plan", required=True)
    p_apply.add_argument("--on-conflict", choices=["fail", "skip", "rename", "overwrite"], default="fail")
    p_apply.add_argument("--dry-run", action="store_true")
    p_apply.add_argument("--manifest")

    p_mm = sub.add_parser("move-match")
    p_mm.add_argument("--root", required=True)
    p_mm.add_argument("--dst", required=True)
    p_mm.add_argument("--glob", action="append", default=[])
    p_mm.add_argument("--regex")
    p_mm.add_argument("--contains")
    p_mm.add_argument("--ext", default="")
    p_mm.add_argument("--recursive", action="store_true")
    p_mm.add_argument("--max-depth", type=int)
    p_mm.add_argument("--allow-multiple", action="store_true")
    p_mm.add_argument("--on-conflict", choices=["fail", "skip", "rename", "overwrite"], default="fail")
    p_mm.add_argument("--dry-run", action="store_true")
    p_mm.add_argument("--manifest")

    p_undo = sub.add_parser("undo")
    p_undo.add_argument("--manifest", required=True)

    args = parser.parse_args()
    t0 = time.perf_counter()

    try:
        if args.cmd == "find":
            exts = [x.strip() for x in args.ext.split(",") if x.strip()]
            matches = find_files(args.root, args.glob, args.regex, args.contains, exts, args.recursive, args.max_depth)
            emit_json({"ok": True, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2), "count": len(matches), "matches": matches})

        elif args.cmd == "apply":
            raw = parse_json_arg(args.plan)
            actions = raw["actions"] if isinstance(raw, dict) else raw
            res = execute_plan(actions, args.on_conflict, args.dry_run, args.manifest)
            res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            emit_json(res, 0 if res.get("ok") else 1)

        elif args.cmd == "move-match":
            exts = [x.strip() for x in args.ext.split(",") if x.strip()]
            matches = find_files(args.root, args.glob, args.regex, args.contains, exts, args.recursive, args.max_depth)
            if not matches:
                emit_json({"ok": False, "error": "no_matches", "count": 0}, 1)
            if len(matches) > 1 and not args.allow_multiple:
                emit_json(
                    {
                        "ok": False,
                        "error": "multiple_matches",
                        "count": len(matches),
                        "matches": matches,
                    },
                    1,
                )
            dst_dir = Path(args.dst).resolve()
            if not args.dry_run:
                dst_dir.mkdir(parents=True, exist_ok=True)
            actions = [{"op": "move", "src": m["path"], "dst": str(dst_dir / m["name"])} for m in matches]
            res = execute_plan(actions, args.on_conflict, args.dry_run, args.manifest)
            res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            emit_json(res, 0 if res.get("ok") else 1)

        elif args.cmd == "undo":
            res = undo_manifest(args.manifest)
            res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            emit_json(res, 0 if res.get("ok") else 1)

    except Exception as exc:
        emit_json({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
