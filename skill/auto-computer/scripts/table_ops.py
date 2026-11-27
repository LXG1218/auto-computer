from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

from _common import configure_utf8_stdio, emit_json

_COL_REF_RE = re.compile(r"^([A-Za-z]+)")
_ROW_REF_RE = re.compile(r"^[A-Za-z]+(\d+)$")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _col_to_index(col_letters: str) -> int:
    idx = 0
    for ch in col_letters.upper():
        if "A" <= ch <= "Z":
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _split_cell_ref(ref: str) -> int | None:
    m = _COL_REF_RE.match(ref or "")
    return _col_to_index(m.group(1)) if m else None


def _extract_si_text(elem: Any) -> str:
    parts: list[str] = []

    def walk(node: Any, in_rph: bool) -> None:
        name = _local_name(node.tag)
        if name == "rPh":
            return
        if name == "t" and not in_rph and node.text is not None:
            parts.append(node.text)
        for child in node:
            walk(child, in_rph or (name == "rPh"))

    walk(elem, False)
    return "".join(parts)


def _normalize_zip_target(target: str) -> str:
    import posixpath

    t = target.replace("\\", "/")
    if t.startswith("/"):
        return t.lstrip("/")
    return posixpath.normpath(posixpath.join("xl", t))


def _parse_row_number(row_elem: Any, fallback_row_num: int) -> int:
    r_attr = (row_elem.attrib.get("r") or "").strip()
    if r_attr.isdigit():
        num = int(r_attr)
        if num >= 1:
            return num
    for cell in row_elem:
        if _local_name(cell.tag) != "c":
            continue
        m = _ROW_REF_RE.match((cell.attrib.get("r") or "").strip())
        if m:
            num = int(m.group(1))
            if num >= 1:
                return num
    return fallback_row_num


def _read_xlsx(path: Path, sheet_name: str | None = None) -> tuple[list[str], list[tuple[int, list[str]]], str]:
    import xml.etree.ElementTree as ET
    import zipfile

    with zipfile.ZipFile(path, "r") as zf:
        namelist = set(zf.namelist())

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in namelist:
            root_ss = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root_ss:
                if _local_name(si.tag) == "si":
                    shared_strings.append(_extract_si_text(si))

        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        sheets: list[tuple[str, str]] = []
        for elem in wb_root.iter():
            if _local_name(elem.tag) == "sheet":
                name = elem.attrib.get("name", "")
                rid = ""
                for k, v in elem.attrib.items():
                    if k.endswith("}id") or k == "id":
                        rid = v
                        break
                sheets.append((name, rid))

        rels_map: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in namelist:
            rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in rels_root:
                if _local_name(rel.tag) == "Relationship":
                    rid = rel.attrib.get("Id", "")
                    tgt = rel.attrib.get("Target", "")
                    if rid and tgt:
                        rels_map[rid] = _normalize_zip_target(tgt)

        if not sheets:
            raise ValueError(f"No worksheets found in {path}")

        chosen_name, chosen_rid = sheets[0]
        if sheet_name:
            found = [(n, r) for n, r in sheets if n == sheet_name]
            if not found:
                raise ValueError(f"Sheet '{sheet_name}' not found. Available: {[n for n, _ in sheets]}")
            chosen_name, chosen_rid = found[0]

        sheet_path = rels_map.get(chosen_rid, "xl/worksheets/sheet1.xml")
        if sheet_path not in namelist:
            raise ValueError(f"Worksheet XML not found inside archive: {sheet_path}")

        ws_root = ET.fromstring(zf.read(sheet_path))
        row_map: dict[int, list[str]] = {}
        next_row_num = 1
        max_cols = 0

        for elem in ws_root.iter():
            if _local_name(elem.tag) != "row":
                continue
            row_num = _parse_row_number(elem, next_row_num)
            next_row_num = row_num + 1
            row_vals: dict[int, str] = {}
            next_col = 0
            for cell in elem:
                if _local_name(cell.tag) != "c":
                    continue
                ref = cell.attrib.get("r", "")
                col_idx = _split_cell_ref(ref)
                if col_idx is None:
                    col_idx = next_col
                next_col = col_idx + 1

                ctype = cell.attrib.get("t", "")
                val = ""
                v_elem = None
                is_elem = None
                for ch in cell:
                    lname = _local_name(ch.tag)
                    if lname == "v":
                        v_elem = ch
                    elif lname == "is":
                        is_elem = ch

                if ctype == "s":
                    if v_elem is not None and v_elem.text is not None:
                        idx = int(v_elem.text)
                        val = shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
                elif ctype == "inlineStr":
                    if is_elem is not None:
                        val = _extract_si_text(is_elem)
                elif ctype == "b":
                    if v_elem is not None and v_elem.text is not None:
                        val = "TRUE" if v_elem.text.strip() == "1" else "FALSE"
                else:
                    if v_elem is not None and v_elem.text is not None:
                        val = v_elem.text

                row_vals[col_idx] = val
                if col_idx + 1 > max_cols:
                    max_cols = col_idx + 1

            if row_vals:
                row_len = max(row_vals.keys()) + 1
                row_map[row_num] = [row_vals.get(i, "") for i in range(row_len)]
            else:
                row_map[row_num] = []

        numbered_rows = sorted(row_map.items(), key=lambda kv: kv[0])
        for _, r in numbered_rows:
            if len(r) < max_cols:
                r.extend([""] * (max_cols - len(r)))

        return [n for n, _ in sheets], numbered_rows, chosen_name


def _read_csv(path: Path) -> list[list[str]]:
    import csv

    raw = path.read_bytes()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    default_delim = "\t" if path.suffix.lower() == ".tsv" else ","
    sample = text[:8192]
    delim = default_delim
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delim = dialect.delimiter
    except Exception:
        pass

    rows = [list(r) for r in csv.reader(text.splitlines(), delimiter=delim)]
    max_cols = max((len(r) for r in rows), default=0)
    for r in rows:
        if len(r) < max_cols:
            r.extend([""] * (max_cols - len(r)))
    return rows


def _detect_header_row(numbered_rows: list[tuple[int, list[str]]], expected_columns: list[str] | None = None) -> tuple[int, list[str], list[list[str]]]:
    non_empty_rows = [(rnum, cells) for rnum, cells in numbered_rows if any(str(c).strip() != "" for c in cells)]
    if not non_empty_rows:
        return 1, [], []
    candidates = non_empty_rows[:15]
    clean_expected = [c.strip() for c in (expected_columns or []) if c and c.strip()]
    if clean_expected:
        best_match = None
        for rnum, cells in candidates:
            cell_set = {str(c).strip() for c in cells if str(c).strip()}
            hits = sum(1 for col in clean_expected if col in cell_set)
            if hits > 0 and (best_match is None or hits > best_match[0]):
                best_match = (hits, rnum, cells)
        if best_match is not None:
            _, rnum, raw_headers = best_match
            data_rows = [cells for rn, cells in numbered_rows if rn > rnum]
            return rnum, raw_headers, data_rows

    def _is_num_like(s: str) -> bool:
        t = s.replace(",", "").replace("¥", "").replace("$", "").replace("%", "").strip()
        try:
            float(t)
            return True
        except ValueError:
            return False

    best_score = None
    best_row = candidates[0]
    for rnum, cells in candidates:
        vals = [str(c).strip() for c in cells if str(c).strip()]
        if not vals:
            continue
        text_labels = sum(1 for v in vals if not _is_num_like(v) and len(v) <= 40)
        distinct_cnt = len(set(vals))
        score = (1 if distinct_cnt >= 2 else 0, text_labels, distinct_cnt, -rnum)
        if best_score is None or score > best_score:
            best_score = score
            best_row = (rnum, cells)
    rnum, raw_headers = best_row
    data_rows = [cells for rn, cells in numbered_rows if rn > rnum]
    return rnum, raw_headers, data_rows


def load_table(
    path_str: str,
    sheet_name: str | None = None,
    header_row: int | None = None,
    header_auto: bool = False,
    expected_columns: list[str] | None = None,
) -> dict[str, Any]:
    path = Path(path_str).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Table file not found: {path}")

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        sheets, numbered_rows, active_sheet = _read_xlsx(path, sheet_name)
    elif ext in (".csv", ".tsv", ".txt"):
        sheets = [path.name]
        numbered_rows = [(idx + 1, row) for idx, row in enumerate(_read_csv(path))]
        active_sheet = path.name
    else:
        raise ValueError(f"Unsupported table format: {ext}")

    max_cols = max((len(r) for _, r in numbered_rows), default=0)

    if header_row is not None:
        max_row_num = numbered_rows[-1][0] if numbered_rows else 0
        if header_row < 1 or header_row > max_row_num:
            raise ValueError(f"header_row {header_row} out of range (1..{max_row_num})")
        row_map = dict(numbered_rows)
        detected_hr = header_row
        raw_headers = row_map.get(header_row, [""] * max_cols)
        data_rows = [cells for rnum, cells in numbered_rows if rnum > header_row]
    elif header_auto:
        detected_hr, raw_headers, data_rows = _detect_header_row(numbered_rows, expected_columns=expected_columns)
        if not raw_headers and not data_rows:
            return {"sheets": sheets, "sheet": active_sheet, "headers": [], "rows": [], "detected_header_row": detected_hr}
    else:
        first_idx = None
        for idx, (_, r) in enumerate(numbered_rows):
            if any(c.strip() != "" for c in r):
                first_idx = idx
                break
        if first_idx is None:
            return {"sheets": sheets, "sheet": active_sheet, "headers": [], "rows": [], "detected_header_row": 1}
        detected_hr = numbered_rows[first_idx][0]
        raw_headers = numbered_rows[first_idx][1]
        data_rows = [cells for _, cells in numbered_rows[first_idx + 1 :]]

    headers: list[str] = []
    seen_hdr: dict[str, int] = {}
    for idx, h in enumerate(raw_headers):
        clean = str(h).strip() or f"col_{idx + 1}"
        if clean in seen_hdr:
            seen_hdr[clean] += 1
            clean = f"{clean}_{seen_hdr[clean]}"
        else:
            seen_hdr[clean] = 1
        headers.append(clean)

    records: list[dict[str, str]] = []
    for row in data_rows:
        if all(str(c).strip() == "" for c in row):
            continue
        rec = {headers[i]: str(row[i] if i < len(row) else "").strip() for i in range(len(headers))}
        records.append(rec)

    return {"sheets": sheets, "sheet": active_sheet, "headers": headers, "rows": records, "detected_header_row": detected_hr}


def _match_where(rec: dict[str, str], expr: str) -> bool:
    for op in ("!=", "~", "="):
        if op in expr:
            col, val = expr.split(op, 1)
            col = col.strip()
            val = val.strip()
            cell = rec.get(col, "")
            if op == "=":
                return cell == val
            if op == "!=":
                return cell != val
            if op == "~":
                return val in cell
    raise ValueError(f"Invalid --where expression '{expr}'. Use col=val, col!=val, or col~val.")


def _values_equal(v1: str, v2: str, is_numeric: bool, tolerance: Any) -> bool:
    s1 = v1.strip()
    s2 = v2.strip()
    if s1 == s2:
        return True
    if is_numeric:
        from decimal import Decimal, InvalidOperation

        try:
            d1 = Decimal(s1.replace(",", ""))
            d2 = Decimal(s2.replace(",", ""))
            return abs(d1 - d2) <= tolerance
        except InvalidOperation:
            return False
    return False


def reconcile_tables(
    left_path: str,
    right_path: str,
    key_spec: str,
    left_sheet: str | None = None,
    right_sheet: str | None = None,
    columns_spec: str | None = None,
    numeric_cols_spec: str | None = None,
    tolerance: float = 0.0,
    out_csv: str | None = None,
    out_json: str | None = None,
    left_header_row: int | None = None,
    right_header_row: int | None = None,
    header_auto: bool = False,
) -> dict[str, Any]:
    l_expected: list[str] = []
    r_expected: list[str] = []
    if ":" in key_spec:
        lk, rk = [x.strip() for x in key_spec.split(":", 1)]
        l_expected.append(lk)
        r_expected.append(rk)
    else:
        l_expected.append(key_spec.strip())
        r_expected.append(key_spec.strip())
    if columns_spec:
        for token in columns_spec.split(","):
            t = token.strip()
            if not t:
                continue
            if ":" in t:
                lc, rc = [x.strip() for x in t.split(":", 1)]
                l_expected.append(lc)
                r_expected.append(rc)
            else:
                l_expected.append(t)
                r_expected.append(t)

    left_tbl = load_table(left_path, left_sheet, header_row=left_header_row, header_auto=header_auto, expected_columns=l_expected)
    right_tbl = load_table(right_path, right_sheet, header_row=right_header_row, header_auto=header_auto, expected_columns=r_expected)

    if ":" in key_spec:
        l_key, r_key = [x.strip() for x in key_spec.split(":", 1)]
    else:
        l_key = r_key = key_spec.strip()

    if l_key not in left_tbl["headers"]:
        raise ValueError(f"Left key column '{l_key}' not in {left_tbl['headers']}")
    if r_key not in right_tbl["headers"]:
        raise ValueError(f"Right key column '{r_key}' not in {right_tbl['headers']}")

    col_pairs: list[tuple[str, str]] = []
    if columns_spec:
        for token in columns_spec.split(","):
            t = token.strip()
            if not t:
                continue
            if ":" in t:
                lc, rc = [x.strip() for x in t.split(":", 1)]
            else:
                lc = rc = t
            if lc not in left_tbl["headers"]:
                raise ValueError(f"Left column '{lc}' not in {left_tbl['headers']}")
            if rc not in right_tbl["headers"]:
                raise ValueError(f"Right column '{rc}' not in {right_tbl['headers']}")
            col_pairs.append((lc, rc))
    else:
        r_set = set(right_tbl["headers"])
        for h in left_tbl["headers"]:
            if h != l_key and h != r_key and h in r_set:
                col_pairs.append((h, h))

    num_cols = {c.strip() for c in (numeric_cols_spec or "").split(",") if c.strip()}
    if num_cols:
        from decimal import Decimal

        tol_dec: Any = Decimal(str(tolerance))
    else:
        tol_dec = None

    left_index: dict[str, list[dict[str, str]]] = {}
    for row in left_tbl["rows"]:
        k = str(row.get(l_key, "")).strip()
        left_index.setdefault(k, []).append(row)

    right_index: dict[str, list[dict[str, str]]] = {}
    for row in right_tbl["rows"]:
        k = str(row.get(r_key, "")).strip()
        right_index.setdefault(k, []).append(row)

    left_keys = set(left_index.keys())
    right_keys = set(right_index.keys())

    only_in_left = [{"key": k, "row": left_index[k][0]} for k in sorted(left_keys - right_keys)]
    only_in_right = [{"key": k, "row": right_index[k][0]} for k in sorted(right_keys - left_keys)]

    dup_left = [
        {"key": k, "count": len(rows), "rows": rows}
        for k, rows in sorted(left_index.items())
        if len(rows) > 1
    ]
    dup_right = [
        {"key": k, "count": len(rows), "rows": rows}
        for k, rows in sorted(right_index.items())
        if len(rows) > 1
    ]

    field_diffs: list[dict[str, Any]] = []
    matched_exact = 0
    for k in sorted(left_keys & right_keys):
        l_row = left_index[k][0]
        r_row = right_index[k][0]
        diffs: list[dict[str, Any]] = []
        for lc, rc in col_pairs:
            lv = l_row.get(lc, "")
            rv = r_row.get(rc, "")
            is_num = (lc in num_cols) or (rc in num_cols)
            if not _values_equal(lv, rv, is_num, tol_dec):
                diffs.append({"left_col": lc, "right_col": rc, "left": lv, "right": rv})
        if diffs:
            field_diffs.append({"key": k, "diffs": diffs})
        else:
            if len(left_index[k]) == 1 and len(right_index[k]) == 1:
                matched_exact += 1

    anomaly_count = (
        len(only_in_left)
        + len(only_in_right)
        + len(dup_left)
        + len(dup_right)
        + len(field_diffs)
    )

    result: dict[str, Any] = {
        "ok": True,
        "left_file": str(Path(left_path).resolve()),
        "right_file": str(Path(right_path).resolve()),
        "left_rows": len(left_tbl["rows"]),
        "right_rows": len(right_tbl["rows"]),
        "compared_columns": [f"{lc}:{rc}" if lc != rc else lc for lc, rc in col_pairs],
        "matched_exact": matched_exact,
        "only_in_left_count": len(only_in_left),
        "only_in_right_count": len(only_in_right),
        "duplicate_keys_left_count": len(dup_left),
        "duplicate_keys_right_count": len(dup_right),
        "field_diffs_count": len(field_diffs),
        "anomaly_count": anomaly_count,
        "only_in_left": only_in_left,
        "only_in_right": only_in_right,
        "duplicate_keys_left": dup_left,
        "duplicate_keys_right": dup_right,
        "field_diffs": field_diffs,
    }

    if out_csv:
        import csv

        csv_path = Path(out_csv).resolve()
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["anomaly_type", "key", "column", "left_value", "right_value", "detail"])
            for item in only_in_left:
                writer.writerow(["only_in_left", item["key"], "", "", "", json.dumps(item["row"], ensure_ascii=False)])
            for item in only_in_right:
                writer.writerow(["only_in_right", item["key"], "", "", "", json.dumps(item["row"], ensure_ascii=False)])
            for item in dup_left:
                writer.writerow(["duplicate_left", item["key"], "", str(item["count"]), "", "duplicate key in left table"])
            for item in dup_right:
                writer.writerow(["duplicate_right", item["key"], "", "", str(item["count"]), "duplicate key in right table"])
            for item in field_diffs:
                for d in item["diffs"]:
                    col_label = d["left_col"] if d["left_col"] == d["right_col"] else f"{d['left_col']}:{d['right_col']}"
                    writer.writerow(["field_diff", item["key"], col_label, d["left"], d["right"], ""])
        result["out_csv"] = str(csv_path)

    if out_json:
        jp = Path(out_json).resolve()
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["out_json"] = str(jp)

    return result


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Zero-dependency Excel/CSV reader and reconciler")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info")
    p_info.add_argument("--file", required=True)
    p_info.add_argument("--sheet")
    p_info.add_argument("--header-row", type=int)
    p_info.add_argument("--header-auto", action="store_true")

    p_query = sub.add_parser("query")
    p_query.add_argument("--file", required=True)
    p_query.add_argument("--sheet")
    p_query.add_argument("--header-row", type=int)
    p_query.add_argument("--header-auto", action="store_true")
    p_query.add_argument("--where", action="append", default=[])
    p_query.add_argument("--columns")
    p_query.add_argument("--limit", type=int, default=50)

    p_rec = sub.add_parser("reconcile")
    p_rec.add_argument("--left", required=True)
    p_rec.add_argument("--right", required=True)
    p_rec.add_argument("--key", required=True)
    p_rec.add_argument("--left-sheet")
    p_rec.add_argument("--right-sheet")
    p_rec.add_argument("--header-row", type=int)
    p_rec.add_argument("--header-auto", action="store_true")
    p_rec.add_argument("--left-header-row", type=int)
    p_rec.add_argument("--right-header-row", type=int)
    p_rec.add_argument("--columns")
    p_rec.add_argument("--numeric-cols")
    p_rec.add_argument("--tolerance", type=float, default=0.0)
    p_rec.add_argument("--out-csv")
    p_rec.add_argument("--out-json")

    args = parser.parse_args()
    t0 = time.perf_counter()

    try:
        if args.cmd == "info":
            tbl = load_table(args.file, args.sheet, header_row=args.header_row, header_auto=args.header_auto)
            emit_json(
                {
                    "ok": True,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "sheets": tbl["sheets"],
                    "sheet": tbl["sheet"],
                    "detected_header_row": tbl.get("detected_header_row"),
                    "row_count": len(tbl["rows"]),
                    "headers": tbl["headers"],
                    "sample_rows": tbl["rows"][:5],
                }
            )

        elif args.cmd == "query":
            expected_cols = [c.strip() for c in args.columns.split(",") if c.strip()] if args.columns else None
            tbl = load_table(args.file, args.sheet, header_row=args.header_row, header_auto=args.header_auto, expected_columns=expected_cols)
            rows = tbl["rows"]
            for expr in args.where:
                rows = [r for r in rows if _match_where(r, expr)]
            cols = [c.strip() for c in args.columns.split(",") if c.strip()] if args.columns else tbl["headers"]
            projected = [{c: r.get(c, "") for c in cols} for r in rows[: args.limit]]
            emit_json(
                {
                    "ok": True,
                    "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
                    "detected_header_row": tbl.get("detected_header_row"),
                    "total_matched": len(rows),
                    "returned": len(projected),
                    "rows": projected,
                }
            )

        elif args.cmd == "reconcile":
            l_hr = args.left_header_row if args.left_header_row is not None else args.header_row
            r_hr = args.right_header_row if args.right_header_row is not None else args.header_row
            res = reconcile_tables(
                args.left,
                args.right,
                args.key,
                args.left_sheet,
                args.right_sheet,
                args.columns,
                args.numeric_cols,
                args.tolerance,
                args.out_csv,
                args.out_json,
                left_header_row=l_hr,
                right_header_row=r_hr,
                header_auto=args.header_auto,
            )
            res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            emit_json(res)

    except Exception as exc:
        emit_json({"ok": False, "error": str(exc)}, 1)


if __name__ == "__main__":
    main()
