#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
read_table.py — 把 CSV / TSV / XLSX 读成结构化 JSON，供后续指标计算使用。

零依赖，只用标准库。xlsx 用 zipfile + XML 解析（不装 openpyxl）。
**纯本地运行，无任何网络请求。**

用法：
    python3 scripts/read_table.py 数据.csv
    python3 scripts/read_table.py 数据.xlsx --limit 5
    python3 scripts/read_table.py 数据.xlsx --sheet 2

输出 JSON：{"columns": [...], "rows": [...], "row_count": N, "source": "..."}
"""

import argparse
import csv
import json
import sys
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def col_index(ref):
    """'AB12' -> 27（列序号，从 0 开始）。"""
    letters = "".join(c for c in ref if c.isalpha())
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_csv(path, delimiter=","):
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter)
        rows = list(reader)
    if not rows:
        return [], []
    columns = rows[0]
    data = []
    for r in rows[1:]:
        row = {}
        for i, col in enumerate(columns):
            row[col] = r[i] if i < len(r) else ""
        data.append(row)
    return columns, data


def read_xlsx(path, sheet_no=1):
    """零依赖解析 xlsx：读共享字符串表 + 指定 sheet 的单元格。"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))

        sheet_path = f"xl/worksheets/sheet{sheet_no}.xml"
        if sheet_path not in names:
            sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
            if not sheets:
                raise ValueError("找不到工作表")
            sheet_path = sheets[0]

        root = ET.fromstring(z.read(sheet_path))

    grid = {}
    max_row = 0
    for row_el in root.iter(f"{NS}row"):
        # 单元格 ref 有可能省略行号（部分生成器会这么写），此时退回用 row 的 r 属性
        row_attr = row_el.get("r")
        fallback_ri = int(row_attr) - 1 if row_attr and row_attr.isdigit() else None

        for c in row_el.findall(f"{NS}c"):
            ref = c.get("r") or ""
            ci = col_index(ref) if ref else 0

            digits = "".join(ch for ch in ref if ch.isdigit())
            if digits:
                ri = int(digits) - 1
            elif fallback_ri is not None:
                ri = fallback_ri
            else:
                ri = 0
            t = c.get("t")
            v = c.find(f"{NS}v")
            is_el = c.find(f"{NS}is")

            if t == "s" and v is not None:
                try:
                    value = shared[int(v.text)]
                except (ValueError, IndexError):
                    value = ""
            elif is_el is not None:
                value = "".join(x.text or "" for x in is_el.iter(f"{NS}t"))
            elif v is not None:
                value = v.text or ""
            else:
                value = ""

            grid[(ri, ci)] = value
            max_row = max(max_row, ri)

    if not grid:
        return [], []

    ncols = max(ci for _, ci in grid.keys()) + 1
    raw_header = [str(grid.get((0, i), "")).strip() or f"col{i+1}" for i in range(ncols)]

    data = []
    for ri in range(1, max_row + 1):
        row = {}
        empty = True
        for ci in range(ncols):
            val = grid.get((ri, ci), "")
            if val != "":
                empty = False
            row[raw_header[ci]] = val
        if not empty:
            data.append(row)

    return raw_header, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="CSV / TSV / XLSX 文件路径")
    ap.add_argument("--sheet", type=int, default=1, help="xlsx 工作表序号，默认 1")
    ap.add_argument("--limit", type=int, help="只输出前 N 行")
    args = ap.parse_args()

    path = Path(args.file).expanduser()
    if not path.is_file():
        print(json.dumps({"error": "文件不存在: {}".format(path)}, ensure_ascii=False))
        return 1

    suffix = path.suffix.lower()
    try:
        if suffix in (".csv",):
            columns, rows = read_csv(path, ",")
        elif suffix in (".tsv", ".txt"):
            columns, rows = read_csv(path, "\t")
        elif suffix in (".xlsx", ".xlsm"):
            columns, rows = read_xlsx(path, args.sheet)
        else:
            print(json.dumps({"error": "不支持的扩展名: {}".format(suffix)}, ensure_ascii=False))
            return 1
    except Exception as e:
        print(json.dumps({"error": "读取失败: {}".format(e)}, ensure_ascii=False))
        return 1

    if args.limit:
        rows = rows[:args.limit]

    print(json.dumps({
        "source": str(path),
        "columns": columns,
        "row_count": len(rows),
        "rows": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
