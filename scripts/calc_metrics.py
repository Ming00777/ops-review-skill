#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calc_metrics.py — 运营指标确定性计算。**数字必须是真的，不许模型编。**

零依赖，只用标准库。纯本地运行，无网络请求。

两种数据形态都能算：

  A. 单实体时间序列（默认）
     每行一个周期，例如「周报.csv」：一行一周。直接跑即可。

  B. 面板数据（panel）—— 本任务最常见
     每行是「实体 × 周期」，例如「城市 × 周」「渠道 × 周」。
     必须用 --group-by 指定实体列（city / channel / 门店），否则会
     把不同实体的数据混成一条序列，算出跨实体的假环比。
     加 --total 会额外算一份跨实体聚合的「大盘」序列（比率型列自动跳过）。

用法：
    # 单实体（默认）
    python3 scripts/calc_metrics.py 周报.csv

    # 面板：按城市分组，并额外算大盘聚合
    python3 scripts/calc_metrics.py 城市周报.csv --group-by city --total

    # 面板 + 数据质量体检（缺失/重复/口径突变）
    python3 scripts/calc_metrics.py 城市周报.csv --group-by city --total --quality

    # 漏斗（面板下按实体各自算）
    python3 scripts/calc_metrics.py 漏斗.csv --group-by city --funnel leads,signed,onboarded,first_order,active90

    # 指标 / 目标 / 阈值 等同原接口
    python3 scripts/calc_metrics.py 城市周报.csv --group-by city --metrics gmv_wan,orders --target gmv_wan=9000 --anomaly 15
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

DATE_HINTS = ("日期", "时间", "周", "天", "月", "统计", "date", "time", "period", "day", "week", "month")
ENTITY_HINTS = ("city", "channel", "region", "门店", "城市", "渠道", "地区", "分组", "site", "store", "entity", "分组")
RATE_HINTS = ("rate", "share", "ratio", "pct", "aov", "比例", "占比", "率", "留存", "retention")


def to_number(raw):
    """把 '1,286,000' / '¥128.6万' / '12.3%' / '-' 之类转成 float。失败返回 None。"""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "--", "N/A", "NA", "null", "None"):
        return None
    s = s.replace(",", "").replace("，", "")
    s = re.sub(r"[¥$€£\s]", "", s)
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except ValueError:
        return None


def pick_date_col(columns, rows):
    """按列名猜日期列。"""
    for col in columns:
        low = col.lower()
        if any(h in low for h in DATE_HINTS):
            return col
    return None


def pick_numeric_cols(columns, rows, exclude):
    """大部分值能转成数字，且不含中文字符的列，视为数值列。"""
    if not rows:
        return []
    numeric = []
    for col in columns:
        if col == exclude:
            continue
        vals = [to_number(r.get(col)) for r in rows]
        hit = [v for v in vals if v is not None]
        if len(hit) >= max(1, int(len(rows) * 0.6)):
            numeric.append(col)
    return numeric


def is_ratio_column(col, rows):
    """判断是否是比率型列（大盘聚合时不应直接求和）。"""
    low = col.lower()
    if any(h in low for h in RATE_HINTS):
        return True
    vals = [to_number(r.get(col)) for r in rows]
    hit = [v for v in vals if v is not None]
    # 若全部落在 (0,1)（且非全 0），极可能是比率
    if hit and all(0 < v < 1 for v in hit):
        return True
    return False


def load_table(path, sheet, extra_args=()):
    """调 read_table.py 拿结构化数据。"""
    script = Path(__file__).parent / "read_table.py"
    cmd = [sys.executable, str(script), str(path)]
    if sheet:
        cmd += ["--sheet", str(sheet)]
    cmd += list(extra_args)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return None, p.stderr.strip()[:300]
    try:
        return json.loads(p.stdout), None
    except json.JSONDecodeError as e:
        return None, "解析失败: {}".format(e)


def pct_change(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / abs(prev) * 100, 2)


def build_series(rows, date_col, col):
    """取出按日期排序的 (period, value) 序列。"""
    out = []
    for r in rows:
        v = to_number(r.get(col))
        if v is None:
            continue
        out.append({"period": str(r.get(date_col, "")).strip() if date_col else "", "value": v})
    if date_col:
        out.sort(key=lambda x: x["period"])
    return out


def analyze_metric(col, series, target, anomaly_threshold):
    if not series:
        return {"name": col, "error": "无有效数值"}
    values = [s["value"] for s in series]
    latest = values[-1]
    previous = values[-2] if len(values) >= 2 else None
    numbers = [v for v in values if isinstance(v, (int, float))]
    result = {
        "name": col,
        "latest": latest,
        "latest_period": series[-1]["period"],
        "previous": previous,
        "wow_pct": pct_change(latest, previous),
        "mean": round(sum(numbers) / len(numbers), 2) if numbers else None,
        "max": max(numbers) if numbers else None,
        "min": min(numbers) if numbers else None,
        "history": series,
    }
    if target is not None:
        result["target"] = target
        result["achievement_pct"] = round(latest / target * 100, 2) if target else None
    wow = result["wow_pct"]
    result["anomaly"] = wow is not None and abs(wow) >= anomaly_threshold
    if result["anomaly"]:
        result["anomaly_note"] = "环比 {}%，超过阈值 {}%".format(
            "+" + str(wow) if wow > 0 else wow, anomaly_threshold)
    return result


def analyze_funnel(rows, funnel_cols, date_col=None):
    """单实体漏斗：取最后一行算各相邻环节转化率。"""
    rows = sorted(rows, key=lambda r: str(r.get(date_col, "")).strip()) if date_col else rows
    last = rows[-1] if rows else {}
    stages = []
    for col in funnel_cols:
        v = to_number(last.get(col))
        stages.append({"stage": col, "value": v})
    for i in range(1, len(stages)):
        prev, cur = stages[i - 1]["value"], stages[i]["value"]
        stages[i]["step_rate_pct"] = round(cur / prev * 100, 2) if prev else None
        stages[i]["drop_pct"] = round((1 - cur / prev) * 100, 2) if prev else None
    first = stages[0]["value"] if stages else None
    last_v = stages[-1]["value"] if stages else None
    overall = round(last_v / first * 100, 2) if first else None
    return {"stages": stages, "overall_rate_pct": overall}


def analyze_funnel_panel(rows, funnel_cols, group_col, date_col):
    """面板漏斗：每个实体各自一条时间序列，输出每期 step 率与端到端率。"""
    by_group = defaultdict(list)
    for r in rows:
        by_group[r.get(group_col)].append(r)
    out = {}
    for g, g_rows in by_group.items():
        g_rows = sorted(g_rows, key=lambda r: str(r.get(date_col, "")).strip())
        periods = []
        for r in g_rows:
            step = {}
            vals = [to_number(r.get(c)) for c in funnel_cols]
            for i in range(1, len(funnel_cols)):
                pv, cv = vals[i - 1], vals[i]
                step[funnel_cols[i]] = {
                    "step_rate_pct": round(cv / pv * 100, 2) if pv else None,
                }
            first, last = vals[0], vals[-1]
            end2end = round(last / first * 100, 2) if first else None
            periods.append({"period": str(r.get(date_col, "")).strip(),
                            "step": step, "end2end_pct": end2end})
        out[str(g)] = periods
    return out


def detect_quality(rows, group_col, date_col, numeric_cols, anomaly_threshold):
    """面板数据体检：缺失格、重复、疑似口径突变。"""
    groups = sorted({r.get(group_col) for r in rows})
    periods = sorted({str(r.get(date_col, "")).strip() for r in rows if r.get(date_col)})

    # 1) 缺失矩阵
    present = {(str(r.get(group_col)), str(r.get(date_col, "")).strip()) for r in rows}
    missing = [{"group": g, "period": p} for g in groups for p in periods if (g, p) not in present]

    # 2) 重复：同一 group+period 出现多次
    seen = defaultdict(int)
    for r in rows:
        seen[(str(r.get(group_col)), str(r.get(date_col, "")).strip())] += 1
    duplicates = [{"group": g, "period": p, "count": c} for (g, p), c in seen.items() if c > 1]

    # 3) 单组极端离群（数据质量红线）+ 同步变动（疑似口径/全局活动）
    outliers = []
    caliber = []
    for col in numeric_cols:
        if is_ratio_column(col, rows):
            continue
        per_group_value = defaultdict(dict)
        for r in rows:
            v = to_number(r.get(col))
            if v is None:
                continue
            p = str(r.get(date_col, "")).strip()
            g = str(r.get(group_col))
            per_group_value[g][p] = v
        for i in range(1, len(periods)):
            p0, p1 = periods[i - 1], periods[i]
            deltas = {}
            for g, d in per_group_value.items():
                a, b = d.get(p0), d.get(p1)
                if a in (None, 0) or b is None:
                    continue
                dl = pct_change(b, a)
                if dl is not None:
                    deltas[g] = dl
            if not deltas:
                continue
            absd = [abs(v) for v in deltas.values()]
            med = sorted(absd)[len(absd) // 2]
            # 极端离群：某实体变动幅度 > 3× 中位数 → 多半是数据污染/重复写入
            for g, dl in deltas.items():
                if med > 0 and abs(dl) > 3 * med:
                    outliers.append({
                        "metric": col, "group": g, "from": p0, "to": p1,
                        "group_delta_pct": dl, "median_abs_delta_pct": round(med, 2),
                        "ratio_vs_median": round(abs(dl) / med, 1),
                        "interpretation": "单实体变动远超其余实体中位数，疑似数据污染/重复写入，需核查原始数据",
                    })
            # 同步变动：剔除极端离群后，多数实体仍同向且变动幅度够大
            # → 口径变更或全平台统一活动（小波动的正常增长不报）
            aligned = 0
            total_g = 0
            non_outlier = [dl for g, dl in deltas.items()
                           if not (med > 0 and abs(dl) > 3 * med)]
            if len(non_outlier) >= 2:
                med_delta = sorted(non_outlier)[len(non_outlier) // 2]
                for dl in non_outlier:
                    total_g += 1
                    if dl >= 0:
                        aligned += 1
                if total_g >= 2 and aligned / total_g >= 0.8 \
                        and abs(med_delta) >= anomaly_threshold:
                    caliber.append({
                        "metric": col, "from": p0, "to": p1,
                        "median_group_delta_pct": med_delta,
                        "aligned_groups_ratio": round(aligned / total_g, 2),
                        "groups_compared": total_g,
                        "interpretation": "多数实体同向且幅度较大 → 疑似口径变更 / 全平台统一活动，需结合运营日历确认",
                    })
    return {"missing_cells": missing, "duplicate_keys": duplicates,
            "extreme_outlier_groups": outliers,
            "synchronized_weeks": caliber}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--sheet", type=int)
    ap.add_argument("--date-col", help="日期列名，不填自动猜")
    ap.add_argument("--group-by", help="面板模式：实体列名（city/channel/门店）。不填则按单实体处理")
    ap.add_argument("--total", action="store_true", help="面板模式：额外算跨实体聚合的大盘序列")
    ap.add_argument("--quality", action="store_true", help="面板模式：输出数据质量体检")
    ap.add_argument("--metrics", help="要算的指标列，逗号分隔。不填自动识别数值列")
    ap.add_argument("--target", help="目标值，格式 --target gmv=1250000，可重复")
    ap.add_argument("--funnel", help="漏斗列，按顺序逗号分隔")
    ap.add_argument("--anomaly", type=float, default=20.0, help="环比波动异常阈值(%)，默认 20")
    args = ap.parse_args()

    try:
        targets = dict(
            (kv.split("=", 1)[0], float(kv.split("=", 1)[1]))
            for kv in re.split(r"[,\s]+", args.target.strip())
            if "=" in kv
        ) if args.target else {}
    except ValueError:
        targets = {}

    table, err = load_table(args.file, args.sheet)
    if err:
        print(json.dumps({"error": err}, ensure_ascii=False, indent=2))
        return 1

    columns, rows = table["columns"], table["rows"]
    if not rows:
        print(json.dumps({"error": "表里没有数据行"}, ensure_ascii=False, indent=2))
        return 1

    date_col = args.date_col or pick_date_col(columns, rows)
    if date_col and date_col not in columns:
        date_col = None

    if args.metrics:
        metric_cols = [c.strip() for c in args.metrics.split(",") if c.strip()]
        missing = [c for c in metric_cols if c not in columns]
        if missing:
            print(json.dumps({"error": "这些列不在表里: {}".format("、".join(missing)),
                              "available_columns": columns}, ensure_ascii=False, indent=2))
            return 1
    else:
        metric_cols = pick_numeric_cols(columns, rows, date_col)

    # ============ 单实体模式 ============
    if not args.group_by:
        metrics = []
        for col in metric_cols:
            series = build_series(rows, date_col, col)
            if series:
                metrics.append(analyze_metric(col, series, targets.get(col), args.anomaly))

        anomalies = [m for m in metrics if m.get("anomaly")]
        funnel = None
        if args.funnel:
            funnel_cols = [c.strip() for c in args.funnel.split(",") if c.strip()]
            missing = [c for c in funnel_cols if c not in columns]
            funnel = {"error": "漏斗列缺失: {}".format("、".join(missing))} if missing else \
                analyze_funnel(rows, funnel_cols, date_col)

        hint = _anomaly_hint(anomalies, args.anomaly) if anomalies else "没有超过阈值的异常波动，按正常节奏复盘即可。"
        print(json.dumps({
            "mode": "single-entity",
            "source": table["source"], "row_count": table["row_count"],
            "date_col": date_col, "columns": columns,
            "metrics": metrics, "funnel": funnel,
            "anomaly_count": len(anomalies), "hint": hint,
            "rule": "下面的数字全部由本脚本算出，写复盘时不得修改。模型只负责归因和表达。",
        }, ensure_ascii=False, indent=2))
        return 0

    # ============ 面板模式 ============
    gcol = args.group_by
    if gcol not in columns:
        print(json.dumps({"error": "分组列不在表里: {}".format(gcol),
                          "available_columns": columns}, ensure_ascii=False, indent=2))
        return 1

    groups = sorted({r.get(gcol) for r in rows if r.get(gcol) is not None})
    periods = sorted({str(r.get(date_col, "")).strip() for r in rows if r.get(date_col)})

    # 每个实体各自的序列
    per_group_series = {col: {} for col in metric_cols}
    for col in metric_cols:
        for g in groups:
            g_rows = [r for r in rows if r.get(gcol) == g]
            s = build_series(g_rows, date_col, col)
            if s:
                per_group_series[col][str(g)] = [x["value"] for x in s]

    out = {
        "mode": "panel",
        "source": table["source"], "row_count": table["row_count"],
        "date_col": date_col, "group_col": gcol,
        "groups": groups, "periods": periods,
        "columns": columns,
    }

    # 大盘聚合
    if args.total:
        total_metrics = []
        skipped_ratio = [c for c in metric_cols if is_ratio_column(c, rows)]
        for col in metric_cols:
            if is_ratio_column(col, rows):
                continue
            series = []
            for p in periods:
                vals = [to_number(r.get(col)) for r in rows
                        if str(r.get(date_col, "")).strip() == p and to_number(r.get(col)) is not None]
                if vals:
                    series.append({"period": p, "value": round(sum(vals), 2)})
            if series:
                total_metrics.append(analyze_metric("TOTAL_" + col, series, targets.get(col), args.anomaly))
        anomalies = [m for m in total_metrics if m.get("anomaly")]
        out["total_metrics"] = total_metrics
        out["skipped_ratio_columns_in_total"] = skipped_ratio
        out["anomaly_count"] = len(anomalies)
        out["hint"] = _anomaly_hint(anomalies, args.anomaly) if anomalies else \
            "大盘聚合序列未触发异常阈值。"
    else:
        out["anomaly_count"] = 0
        out["hint"] = "面板模式未算大盘聚合（未加 --total）。如需大盘请加 --total。"

    # 每个实体最新环比
    per_group_latest = {}
    for col in metric_cols:
        per_group_latest[col] = {}
        for g in groups:
            s = per_group_series[col].get(g)
            if s and len(s) >= 2:
                per_group_latest[col][g] = {
                    "latest": s[-1], "previous": s[-2],
                    "wow_pct": pct_change(s[-1], s[-2]),
                }
    out["per_group_latest_wow"] = per_group_latest

    # 漏斗
    if args.funnel:
        funnel_cols = [c.strip() for c in args.funnel.split(",") if c.strip()]
        missing = [c for c in funnel_cols if c not in columns]
        out["funnel_panel"] = {"error": "漏斗列缺失: {}".format("、".join(missing))} if missing else \
            analyze_funnel_panel(rows, funnel_cols, gcol, date_col)

    # 数据质量
    if args.quality:
        out["data_quality"] = detect_quality(rows, gcol, date_col, metric_cols, args.anomaly)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _anomaly_hint(anomalies, threshold):
    return ("发现 {} 个异常波动（环比 ≥{}%）：{}。复盘要围绕它们做归因，"
            "别把数据平铺一遍。").format(
        len(anomalies), threshold,
        "、".join("{} {}%".format(a["name"], a["wow_pct"]) for a in anomalies))


if __name__ == "__main__":
    sys.exit(main())
