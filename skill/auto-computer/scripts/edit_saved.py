"""Edit a saved UTF-8 text file or CSV with a verified, reversible backup."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EditError(Exception):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EditError(f"Cannot read {path}: {exc}") from exc


def _atomic_write(path: Path, data: bytes, expected_current: bytes | None = None,
                  *, do_fsync: bool = True) -> None:
    """Replace a file atomically after writing a sibling temporary file."""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(data)
            handle.flush()
            if do_fsync:
                os.fsync(handle.fileno())
        # Recheck immediately before replacement to catch ordinary concurrent edits.
        # Filesystems do not provide a portable compare-and-swap for path contents.
        if expected_current is not None and _read(path) != expected_current:
            raise EditError(f"Source changed before replacement: {path}")
        os.replace(temp_path, path)
    except EditError:
        raise
    except OSError as exc:
        raise EditError(f"Cannot atomically write {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _decode(data: bytes) -> tuple[str, bytes]:
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    payload = data[len(bom):]
    try:
        return payload.decode("utf-8"), bom
    except UnicodeDecodeError as exc:
        raise EditError("Only UTF-8 text files are supported.") from exc


def _text_edit(data: bytes, before: str, after: str) -> tuple[bytes, dict[str, Any]]:
    text, bom = _decode(data)
    count = text.count(before)
    if count != 1:
        raise EditError(f"Expected `before` to occur exactly once, found {count} matches.")
    updated = text.replace(before, after, 1)
    new_data = bom + updated.encode("utf-8")
    return new_data, {"kind": "text", "before": before, "after": after}


def _csv_edit(data: bytes, key_column: str, key_value: str, column: str,
              before: str, after: str, *, header_row: int = 1) -> tuple[bytes, dict[str, Any]]:
    import csv
    import io

    text, bom = _decode(data)
    try:
        detected = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
    except csv.Error:
        detected = csv.excel
    if (detected.delimiter != "," or detected.quotechar != '"' or
            detected.escapechar is not None):
        raise EditError("Only strict comma-delimited CSV with double-quote escaping is supported.")

    # Parse semantically with the standard library and retain physical line spans.
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=",", quotechar='"',
                        doublequote=True, strict=True)
    records: list[list[str]] = []
    line_spans: list[tuple[int, int]] = []
    previous_line = 0
    try:
        for record in reader:
            records.append(record)
            line_spans.append((previous_line + 1, reader.line_num))
            previous_line = reader.line_num
    except csv.Error as exc:
        raise EditError(f"Malformed strict CSV: {exc}") from exc
    spans = _csv_token_spans(text)
    if len(spans) != len(records) or any(len(a) != len(b) for a, b in zip(spans, records)):
        raise EditError("CSV layout is unsupported or ambiguous; source was not changed.")
    if not records:
        raise EditError("CSV is empty.")
    if isinstance(header_row, bool) or not isinstance(header_row, int) or header_row < 1:
        raise EditError("header_row must be a positive integer (1-based).")
    if header_row > len(records):
        raise EditError(f"CSV has only {len(records)} records; header_row={header_row} is out of range.")
    header_idx = header_row - 1
    headers = records[header_idx]
    if len(headers) != len(set(headers)):
        raise EditError("CSV contains duplicate headers, so the requested column is ambiguous.")
    if key_column not in headers or column not in headers:
        raise EditError(f"CSV headers do not contain {key_column!r} and {column!r}.")
    key_index = headers.index(key_column)
    value_index = headers.index(column)
    data_records = records[header_idx + 1:]
    matches = [row for row in data_records if key_index < len(row) and row[key_index] == key_value]
    if len(matches) != 1:
        raise EditError(f"Expected one CSV row for {key_column}={key_value!r}, found {len(matches)}.")
    row_number = next(i for i, candidate in enumerate(data_records, header_idx + 1) if candidate is matches[0])
    row = matches[0]
    if value_index >= len(row):
        raise EditError("The selected CSV row does not contain the requested column.")
    actual = row[value_index]
    if actual != before:
        raise EditError(f"CSV cell changed: expected {before!r}, found {actual!r}.")
    start, end = spans[row_number][value_index]
    old_token = text[start:end]
    quoted = old_token.startswith('"') and old_token.endswith('"')
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=",", quotechar='"', doublequote=True,
                        quoting=csv.QUOTE_ALL if quoted else csv.QUOTE_MINIMAL,
                        lineterminator="")
    writer.writerow([after])
    replacement = output.getvalue()
    new_text = text[:start] + replacement + text[end:]
    new_data = bom + new_text.encode("utf-8")
    # Verify exactly one semantic cell changed and every other parsed cell stayed put.
    check = csv.reader(io.StringIO(new_text, newline=""), delimiter=",", quotechar='"',
                       doublequote=True, strict=True)
    try:
        reread = list(check)
    except csv.Error as exc:
        raise EditError(f"Edited CSV did not parse: {exc}") from exc
    expected = [record.copy() for record in records]
    expected[row_number][value_index] = after
    if reread != expected:
        raise EditError("Edited CSV semantic verification failed; source was not changed.")
    return new_data, {"kind": "csv", "key_column": key_column, "key": key_value,
                      "column": column, "before": before, "after": after,
                      "record_lines": list(line_spans[row_number]),
                      **({"header_row": header_row} if header_row != 1 else {})}


def _csv_token_spans(text: str) -> list[list[tuple[int, int]]]:
    """Return raw field-token offsets for strict comma CSV, retaining all lexical bytes."""
    rows: list[list[tuple[int, int]]] = []
    fields: list[tuple[int, int]] = []
    i = 0
    n = len(text)
    if not text:
        return rows
    while i < n:
        start = i
        if text[i] == '"':
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            else:
                raise EditError("Malformed strict CSV: unterminated quoted field.")
            if i < n and text[i] not in ",\r\n":
                raise EditError("Unsupported CSV syntax after a closing quote.")
        else:
            while i < n and text[i] not in ",\r\n":
                if text[i] == '"':
                    raise EditError("Unsupported quote inside an unquoted CSV field.")
                i += 1
        fields.append((start, i))
        if i == n:
            rows.append(fields)
            break
        if text[i] == ",":
            i += 1
            if i == n:
                fields.append((i, i))
                rows.append(fields)
            continue
        rows.append(fields)
        fields = []
        if text.startswith("\r\n", i):
            i += 2
        else:
            i += 1
    return rows


def _make_audit_paths(backup_root: Path, source: Path) -> tuple[Path, Path]:
    backup_root.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}_{source.stem}_{uuid.uuid4().hex[:8]}"
    return backup_root / f"{stem}.bak", backup_root / f"{stem}.json"


def edit(path_string: str, backup_root_string: str, *, kind: str, before: str,
         after: str, key_column: str | None = None, key: str | None = None,
         column: str | None = None, header_row: int = 1) -> dict[str, Any]:
    source = Path(path_string).expanduser().resolve(strict=True)
    if not source.is_file():
        raise EditError(f"Source is not a regular file: {source}")
    if kind == "csv" and None in (key_column, key, column):
        raise EditError("CSV edit requires --key-column, --key, and --column.")
    original = _read(source)
    if kind == "text":
        updated, change = _text_edit(original, before, after)
    elif kind == "csv":
        updated, change = _csv_edit(original, key_column or "", key or "", column or "",
                                    before, after, header_row=header_row)
    else:
        raise EditError(f"Unsupported edit kind: {kind}")
    if updated == original:
        raise EditError("The requested edit would not change the source file.")

    backup_root = Path(backup_root_string).expanduser().resolve()
    backup_path, audit_path = _make_audit_paths(backup_root, source)
    # Exclusive backup creation avoids replacing another history entry.
    with backup_path.open("xb") as backup_file:
        backup_file.write(original)
        backup_file.flush()
        os.fsync(backup_file.fileno())
    if _read(backup_path) != original:
        raise EditError(f"Backup verification failed: {backup_path}")

    if _read(source) != original:
        raise EditError(f"Source changed after its backup was made; edit stopped. Backup: {backup_path}")
    audit = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
             "status": "prepared", "source": str(source), "backup": str(backup_path),
             "before_sha256": _digest(original), "after_sha256": _digest(updated),
             "change": change, "bytes_before": len(original), "bytes_after": len(updated)}
    # Persist recovery metadata before the source replacement, so a crash after
    # the rename still leaves enough verified hashes and backup data to recover.
    _atomic_write(audit_path, json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8"),
                  do_fsync=False)
    _atomic_write(source, updated, expected_current=original, do_fsync=True)
    actual = _read(source)
    if actual != updated:
        # Ownership is uncertain: never replace bytes that fail the exact readback.
        raise EditError(f"Readback mismatch; source was not overwritten during recovery. Backup: {backup_path}")

    audit["status"] = "verified"
    audit["verified_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(audit_path, json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8"),
                  do_fsync=False)
    return {"ok": True, "status": "verified", "source": str(source),
            "backup": str(backup_path), "audit": str(audit_path), "change": change,
            "before_sha256": audit["before_sha256"], "after_sha256": audit["after_sha256"]}


def restore(audit_path_string: str) -> dict[str, Any]:
    audit_path = Path(audit_path_string).expanduser().resolve(strict=True)
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EditError(f"Cannot read audit record: {exc}") from exc
    if audit.get("schema_version") != 1 or audit.get("status") not in {"prepared", "verified"}:
        raise EditError("Audit is not a prepared or verified edit record supported by this version.")
    source = Path(audit["source"]).resolve(strict=True)
    backup = Path(audit["backup"]).resolve(strict=True)
    before = _read(backup)
    current = _read(source)
    if _digest(before) != audit.get("before_sha256"):
        raise EditError("Backup hash does not match the audit record.")
    if _digest(current) == audit.get("before_sha256"):
        raise EditError("Source still matches the original; restore is unnecessary and made no changes.")
    if _digest(current) != audit.get("after_sha256"):
        raise EditError("Source changed after this edit; restore stopped to preserve later changes.")
    if _read(source) != current:
        raise EditError("Source changed during restore check; restore stopped.")
    _atomic_write(source, before, expected_current=current)
    if _read(source) != before:
        raise EditError("Restore readback failed; inspect the source and audit before retrying.")
    audit["status"] = "restored"
    audit["restored_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(audit_path, json.dumps(audit, ensure_ascii=False, indent=2).encode("utf-8"),
                  expected_current=audit_path.read_bytes(), do_fsync=False)
    return {"ok": True, "status": "restored", "source": str(source),
            "restored_from": str(backup), "audit": str(audit_path)}


def _parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    text_cmd = sub.add_parser("text", help="replace one unique exact substring")
    text_cmd.add_argument("--file", required=True)
    text_cmd.add_argument("--backup-root", required=True)
    text_cmd.add_argument("--before", required=True)
    text_cmd.add_argument("--after", required=True)
    csv_cmd = sub.add_parser("csv", help="replace one CSV cell selected by a unique row key")
    csv_cmd.add_argument("--file", required=True)
    csv_cmd.add_argument("--backup-root", required=True)
    csv_cmd.add_argument("--key-column", required=True)
    csv_cmd.add_argument("--key", required=True)
    csv_cmd.add_argument("--column", required=True)
    csv_cmd.add_argument("--before", required=True)
    csv_cmd.add_argument("--after", required=True)
    csv_cmd.add_argument("--header-row", type=int, default=1)
    restore_cmd = sub.add_parser("restore", help="restore only if the source still matches this edit")
    restore_cmd.add_argument("--audit", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "restore":
            result = restore(args.audit)
        else:
            extra = ({"key_column": args.key_column, "key": args.key, "column": args.column,
                      "header_row": args.header_row}
                     if args.command == "csv" else {})
            result = edit(args.file, args.backup_root, kind=args.command,
                          before=args.before, after=args.after, **extra)
        code = 0
    except (EditError, OSError, ValueError) as exc:
        result, code = {"ok": False, "error": str(exc)}, 2
    # ASCII escaping keeps JSON safe when Windows PowerShell uses a legacy code page.
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
