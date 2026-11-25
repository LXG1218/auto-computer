from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from _common import configure_utf8_stdio, emit_json, parse_json_arg

_REF_PREV_RE = re.compile(r"^\$prev\.([A-Za-z0-9_]+)$")
_REF_STEP_RE = re.compile(r"^\$step\[(\d+)\]\.([A-Za-z0-9_]+)$")


def _hash(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _persist(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _move_no_replace(src: Path, dst: Path, expected_sha256: str, *, preverified: bool = False) -> None:
    """Move without replacing the destination; preserve metadata on same-volume moves."""
    if not src.is_file() or (not preverified and _hash(src) != expected_sha256):
        raise RuntimeError(f"source changed before move: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.rename(src, dst)  # Windows rename fails when dst already exists.
        else:
            os.link(src, dst)  # Atomic no-replace creation on the same filesystem.
            src.unlink()
        return
    except OSError as exc:
        import errno

        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise

    # Cross-volume fallback: exclusively create destination, verify, then unlink source.
    import hashlib
    import shutil

    created = False
    created_identity: tuple[int, int] | None = None
    copied_hash = hashlib.sha256()
    try:
        with src.open("rb") as source, dst.open("xb") as target:
            created = True
            fstat = os.fstat(target.fileno())
            created_identity = (fstat.st_dev, fstat.st_ino)
            for block in iter(lambda: source.read(1024 * 1024), b""):
                target.write(block)
                copied_hash.update(block)
        shutil.copystat(src, dst)
        if _hash(dst) != expected_sha256 or _hash(src) != expected_sha256:
            raise RuntimeError("source or destination changed during move")
        src.unlink()
    except Exception:
        if created and created_identity is not None and dst.is_file():
            fstat = dst.stat()
            if ((fstat.st_dev, fstat.st_ino) == created_identity
                    and _hash(dst) == copied_hash.hexdigest()):
                dst.unlink()
        raise


def _is_reparse(path: Path) -> bool:
    import stat

    info = path.stat(follow_symlinks=False)
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _reject_reparse_path(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"matched path escapes source root: {path}") from exc
    current = root
    for part in (".", *relative.parts):
        if part != ".":
            current = current / part
        if _is_reparse(current):
            raise ValueError(f"source path contains a symlink or reparse point: {current}")


def _find(args: argparse.Namespace) -> dict[str, Any]:
    from files_fast import find_files

    matches = find_files(args.root, args.glob or None, args.regex, args.contains,
                         args.ext or None, args.recursive, args.max_depth)
    return {"ok": True, "count": len(matches), "matches": matches}


def _move(args: argparse.Namespace) -> dict[str, Any]:
    from files_fast import find_files

    raw_root = Path(args.root).absolute()
    if raw_root.exists() and _is_reparse(raw_root):
        raise ValueError(f"source root is a symlink or reparse point: {raw_root}")
    root = raw_root.resolve()
    dest_dir = Path(args.destination).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"source root is not a directory: {root}")
    matches = find_files(str(root), args.glob or None, args.regex, args.contains,
                         args.ext or None, args.recursive, args.max_depth)
    if len(matches) > 1 and not args.allow_multiple:
        return {"ok": False, "error": "multiple matches; rerun with --allow-multiple to move all",
                "count": len(matches), "matches": matches, "moved": []}
    if not matches:
        return {"ok": False, "error": "no matching files", "count": 0, "moved": []}

    from datetime import datetime, timezone

    items = []
    planned_stats: list[tuple[int, int]] = []
    for match in matches:
        src = Path(match["path"]).absolute()
        _reject_reparse_path(src, root)
        resolved_src = src.resolve()
        try:
            resolved_src.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"matched path escapes source root: {src}") from exc
        src = resolved_src
        dst = dest_dir / src.name
        if src == dst:
            raise ValueError(f"source and destination are identical: {src}")
        st = src.stat()
        planned_stats.append((st.st_size, st.st_mtime_ns))
        items.append({"source": str(src), "destination": str(dst), "size": st.st_size,
                      "sha256": _hash(src), "status": "planned"})
    collisions = [it["destination"] for it in items if Path(it["destination"]).exists()]
    if len(set(it["destination"].casefold() for it in items)) != len(items):
        raise FileExistsError("multiple matches would map to the same destination filename")
    if collisions:
        return {"ok": False, "error": "destination exists; no files moved", "collisions": collisions,
                "moved": []}

    manifest = Path(args.manifest).resolve() if args.manifest else (
        dest_dir / f"auto-computer-move-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.json")
    manifest_tmp = manifest.with_suffix(manifest.suffix + ".tmp")
    mapped_paths = {Path(it[key]).resolve() for it in items for key in ("source", "destination")}
    if manifest.exists() or manifest_tmp.exists():
        raise FileExistsError(f"manifest or temporary manifest path already exists: {manifest}")
    if manifest in mapped_paths or manifest_tmp in mapped_paths:
        raise ValueError("manifest or temporary manifest path overlaps a source/destination")
    record: dict[str, Any] = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                              "operation": "file-move", "items": items}
    # Persist planned state once before mutating, then once at finish (or immediately on failure).
    _persist(manifest, record)
    dest_dir.mkdir(parents=True, exist_ok=True)
    aborted = False
    for item, planned_stat in zip(items, planned_stats):
        src, dst = Path(item["source"]), Path(item["destination"])
        src_changed = not src.is_file() or dst.exists()
        if not src_changed:
            cur_st = src.stat()
            if (cur_st.st_size, cur_st.st_mtime_ns) != planned_stat:
                src_changed = _hash(src) != item["sha256"]
        if src_changed:
            item["status"] = "precondition_failed"
            _persist(manifest, record)
            return {"ok": False, "error": "source changed or destination appeared during move",
                    "manifest": str(manifest), "items": items}
        try:
            _move_no_replace(src, dst, item["sha256"], preverified=True)
            item["status"] = "moved"
            item["destination_sha256"] = _hash(dst)
            item["destination_size"] = dst.stat().st_size
            item["verified"] = item["destination_sha256"] == item["sha256"] and not src.exists()
            if not item["verified"]:
                item["status"] = "verification_failed"
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
        if item["status"] != "moved" or not item["verified"]:
            _persist(manifest, record)
            aborted = True
            break
    if not aborted:
        _persist(manifest, record)
    ok = all(it["status"] == "moved" and it.get("verified") for it in items)
    return {"ok": ok, "manifest": str(manifest), "count": len(items), "items": items}


def _undo(args: argparse.Namespace) -> dict[str, Any]:
    manifest = Path(args.manifest).resolve()
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if record.get("operation") != "file-move" or record.get("version") != 1:
        raise ValueError("unsupported restore manifest")
    items = [it for it in record["items"] if it.get("status") == "moved"]
    conflicts = []
    recovered_planned = False
    for item in record["items"]:
        if item.get("status") != "planned":
            continue
        src, dst = Path(item["source"]), Path(item["destination"])
        if not src.exists() and dst.is_file() and _hash(dst) == item.get("sha256"):
            item["status"] = "moved"
            item["destination_sha256"] = item["sha256"]
            item["destination_size"] = dst.stat().st_size
            item["verified"] = True
            items.append(item)
            recovered_planned = True
        elif not src.exists() or dst.exists():
            conflicts.append({"source": str(src), "destination": str(dst),
                              "reason": "planned operation has ambiguous state; restore cannot be proven"})
    if recovered_planned:
        _persist(manifest, record)
    if conflicts:
        return {"ok": False, "error": "restore refused; planned operation state is ambiguous", "conflicts": conflicts}
    for item in items:
        src, dst = Path(item["source"]), Path(item["destination"])
        if src.exists() or not dst.is_file() or _hash(dst) != item.get("destination_sha256"):
            conflicts.append({"source": str(src), "destination": str(dst),
                              "reason": "source occupied or moved file changed/missing"})
    if conflicts:
        return {"ok": False, "error": "restore refused; state no longer matches manifest", "conflicts": conflicts}
    restored = []
    for item in items:
        src, dst = Path(item["source"]), Path(item["destination"])
        src.parent.mkdir(parents=True, exist_ok=True)
        _move_no_replace(dst, src, item["destination_sha256"], preverified=True)
        verified = src.is_file() and not dst.exists() and _hash(src) == item["sha256"]
        item["status"] = "restored" if verified else "restore_verification_failed"
        restored.append({"source": str(src), "destination": str(dst), "verified": verified})
    _persist(manifest, record)
    return {"ok": all(x["verified"] for x in restored), "manifest": str(manifest), "restored": restored}


def _reconcile_from_params(params: dict[str, Any]) -> dict[str, Any]:
    from table_ops import reconcile_tables

    left = Path(params["left"]).resolve()
    right = Path(params["right"]).resolve()
    inputs = {left, right}
    out_csv = params.get("out_csv")
    out_json = params.get("out_json")
    outputs = [Path(p).resolve() for p in (out_csv, out_json) if p]
    if len(set(outputs)) != len(outputs):
        raise ValueError("--out-csv and --out-json must use different paths")
    if any(out in inputs for out in outputs):
        raise ValueError("reconcile output path must not overwrite either input")
    existing = [str(out) for out in outputs if out.exists()]
    if existing:
        raise FileExistsError(f"reconcile output already exists: {', '.join(existing)}")
    header_row = params.get("header_row")
    l_hr = params.get("left_header_row") if params.get("left_header_row") is not None else header_row
    r_hr = params.get("right_header_row") if params.get("right_header_row") is not None else header_row
    return reconcile_tables(
        str(left),
        str(right),
        params["key"],
        params.get("left_sheet"),
        params.get("right_sheet"),
        params.get("columns"),
        params.get("numeric_cols"),
        float(params.get("tolerance", 0.0)),
        out_csv,
        out_json,
        left_header_row=l_hr,
        right_header_row=r_hr,
    )


def _resolve_refs(obj: Any, steps_out: list[dict[str, Any]]) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_refs(v, steps_out) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_refs(v, steps_out) for v in obj]
    if isinstance(obj, str):
        m_prev = _REF_PREV_RE.match(obj)
        if m_prev:
            if not steps_out:
                raise ValueError(f"Reference {obj!r} used on first batch step")
            key = m_prev.group(1)
            if key not in steps_out[-1]:
                raise ValueError(f"Previous step output does not contain {key!r}")
            return steps_out[-1][key]
        m_step = _REF_STEP_RE.match(obj)
        if m_step:
            idx = int(m_step.group(1))
            key = m_step.group(2)
            if idx < 0 or idx >= len(steps_out):
                raise ValueError(f"Step reference {obj!r} out of range")
            if key not in steps_out[idx]:
                raise ValueError(f"Step[{idx}] output does not contain {key!r}")
            return steps_out[idx][key]
    return obj


def _as_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    return [str(val)]


def _run_batch(raw_steps: Any) -> dict[str, Any]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("batch --steps must be a non-empty JSON array")

    steps_out: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError(f"batch step[{idx}] must be a JSON object")
        step = _resolve_refs(raw, steps_out)
        op = str(step.get("op") or step.get("cmd") or "").strip()
        try:
            if op == "file-find":
                ns = argparse.Namespace(
                    root=step["root"],
                    glob=_as_list(step.get("glob")),
                    regex=step.get("regex"),
                    contains=step.get("contains"),
                    ext=_as_list(step.get("ext")),
                    recursive=bool(step.get("recursive", False)),
                    max_depth=step.get("max_depth"),
                )
                res = _find(ns)
            elif op == "file-move":
                ns = argparse.Namespace(
                    root=step["root"],
                    destination=step["destination"],
                    glob=_as_list(step.get("glob")),
                    regex=step.get("regex"),
                    contains=step.get("contains"),
                    ext=_as_list(step.get("ext")),
                    recursive=bool(step.get("recursive", False)),
                    max_depth=step.get("max_depth"),
                    allow_multiple=bool(step.get("allow_multiple", False)),
                    manifest=step.get("manifest"),
                )
                res = _move(ns)
            elif op == "file-undo":
                res = _undo(argparse.Namespace(manifest=step["manifest"]))
            elif op == "table-reconcile":
                res = _reconcile_from_params(step)
            elif op in ("edit-text", "edit-csv"):
                from edit_saved import edit

                kind = "text" if op == "edit-text" else "csv"
                res = edit(
                    step["file"],
                    step["backup_root"],
                    kind=kind,
                    before=step["before"],
                    after=step["after"],
                    key_column=step.get("key_column"),
                    key=step.get("key"),
                    column=step.get("column"),
                )
            elif op == "edit-restore":
                from edit_saved import restore

                res = restore(step["audit"])
            elif op == "read-text":
                p = Path(step["file"]).resolve(strict=True)
                max_bytes = int(step.get("max_bytes", 262144))
                data = p.read_bytes()
                if len(data) > max_bytes:
                    data = data[:max_bytes]
                text = data.decode("utf-8", errors="replace")
                contains = step.get("contains")
                matched = (contains in text) if contains is not None else True
                res = {"ok": bool(matched), "file": str(p), "bytes": p.stat().st_size, "matched": matched}
                if contains is None:
                    res["text"] = text
                elif not matched:
                    res["error"] = f"Expected text not found: {contains!r}"
            else:
                raise ValueError(f"Unsupported batch step op: {op!r}")
        except Exception as exc:
            steps_out.append({"index": idx, "op": op, "ok": False, "error": str(exc)})
            return {
                "ok": False,
                "failed_index": idx,
                "completed_count": idx,
                "count": len(steps_out),
                "error": str(exc),
                "steps": steps_out,
            }

        res["index"] = idx
        res["op"] = op
        steps_out.append(res)
        if not res.get("ok", False):
            return {
                "ok": False,
                "failed_index": idx,
                "completed_count": idx,
                "count": len(steps_out),
                "error": res.get("error", "step returned ok=false"),
                "steps": steps_out,
            }

    return {"ok": True, "count": len(steps_out), "steps": steps_out}


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="One-call common file and table tasks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("file-find", "file-move"):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
        p.add_argument("--glob", action="append", default=[])
        p.add_argument("--regex")
        p.add_argument("--contains")
        p.add_argument("--ext", action="append", default=[])
        p.add_argument("--recursive", action="store_true")
        p.add_argument("--max-depth", type=int)
        if name == "file-move":
            p.add_argument("--destination", required=True, help="destination directory")
            p.add_argument("--allow-multiple", action="store_true")
            p.add_argument("--manifest")
    undo = sub.add_parser("file-undo")
    undo.add_argument("--manifest", required=True)
    rec = sub.add_parser("table-reconcile")
    rec.add_argument("--left", required=True)
    rec.add_argument("--right", required=True)
    rec.add_argument("--key", required=True)
    rec.add_argument("--left-sheet")
    rec.add_argument("--right-sheet")
    rec.add_argument("--header-row", type=int)
    rec.add_argument("--left-header-row", type=int)
    rec.add_argument("--right-header-row", type=int)
    rec.add_argument("--columns")
    rec.add_argument("--numeric-cols")
    rec.add_argument("--tolerance", type=float, default=0.0)
    rec.add_argument("--out-csv")
    rec.add_argument("--out-json")
    bt = sub.add_parser("batch", help="run multiple file/table/edit steps in a single Python process")
    bt.add_argument("--steps", required=True, help="JSON array string or path to JSON file")
    args = parser.parse_args()
    start = time.perf_counter()
    try:
        if args.cmd == "file-find":
            result = _find(args)
        elif args.cmd == "file-move":
            result = _move(args)
        elif args.cmd == "file-undo":
            result = _undo(args)
        elif args.cmd == "table-reconcile":
            result = _reconcile_from_params({
                "left": args.left,
                "right": args.right,
                "key": args.key,
                "left_sheet": args.left_sheet,
                "right_sheet": args.right_sheet,
                "header_row": args.header_row,
                "left_header_row": args.left_header_row,
                "right_header_row": args.right_header_row,
                "columns": args.columns,
                "numeric_cols": args.numeric_cols,
                "tolerance": args.tolerance,
                "out_csv": args.out_csv,
                "out_json": args.out_json,
            })
        elif args.cmd == "batch":
            result = _run_batch(parse_json_arg(args.steps))
        else:
            raise ValueError(f"Unknown command: {args.cmd}")
        result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
        emit_json(result, 0 if result.get("ok") else 1)
    except Exception as exc:
        emit_json({"ok": False, "error": str(exc),
                   "elapsed_ms": round((time.perf_counter() - start) * 1000, 2)}, 1)


if __name__ == "__main__":
    main()
