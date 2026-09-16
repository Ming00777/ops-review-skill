#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
did.py — 准自然实验 / 差分法（Difference-in-Differences）确定性计算。

场景：有一个「处理组」和一个「对照组」，在某个事件周（如人力抽调、策略上线）
之后，处理组的指标掉了，但对照组没掉。DiD 用来把「事件本身的净效应」从
「时间趋势」里抠出来，并算出累计缺口（反事实）。

数字全部由脚本算，模型只负责拿结果去归因。

用法：
    # 计算「签->上架」转化率受 W5 人力抽调的净影响
    python3 scripts/did.py 商家漏斗.csv \
        --group-by city --date-col week \
        --from signed_merchants --to onboarded_merchants \
        --treated 武汉,西安,长沙 --control 北京,上海,广州,深圳,成都,杭州 \
        --event-week W5

    # 直接用一个绝对量指标（不折算比率）
    python3 scripts/did.py 城市周报.csv \
        --group-by city --date-col week --metric gmv_wan \
        --treated 武汉 --control 北京,上海 --event-week W5

输出 JSON：
    did_effect_pp      处理组净效应（百分点）
    cumulative_gap     事件后累计缺口（绝对计数，仅比率模式）
    per_group          每个组的事件前后均值
"""

import argparse
import csv
import json
import sys
from pathlib import Path


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def load(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def weeks_sorted(rows, date_col):
    return sorted({r[date_col] for r in rows if r.get(date_col)})


def ratio_series(rows, gcol, date_col, from_col, to_col):
    """返回 {group: {week: (to, from, ratio)}}"""
    out = {}
    for r in rows:
        g = r.get(gcol)
        w = r.get(date_col)
        a, b = f(r.get(from_col)), f(r.get(to_col))
        if a in (0, None) or b is None:
            continue
        out.setdefault(g, {})[w] = (b, a, b / a)
    return out


def series_abs(rows, gcol, date_col, metric):
    out = {}
    for r in rows:
        g, w, v = r.get(gcol), r.get(date_col), f(r.get(metric))
        if v is None:
            continue
        out.setdefault(g, {})[w] = v
    return out


def avg(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--group-by", required=True)
    ap.add_argument("--date-col", required=True)
    ap.add_argument("--from", dest="from_col", help="比率分子分母之一（如 signed_merchants）")
    ap.add_argument("--to", dest="to_col", help="另一项（如 onboarded_merchants），ratio = to/from")
    ap.add_argument("--metric", help="绝对量指标模式：直接用一个计数/金额列")
    ap.add_argument("--treated", required=True, help="处理组，逗号分隔")
    ap.add_argument("--control", required=True, help="对照组，逗号分隔")
    ap.add_argument("--event-week", required=True)
    ap.add_argument("--baseline", help="事件前基线周（默认事件周前一周）")
    args = ap.parse_args()

    rows = load(args.file)
    ws = weeks_sorted(rows, args.date_col)
    if args.event_week not in ws:
        print(json.dumps({"error": f"event-week {args.event_week} 不在周期里: {ws}"}, ensure_ascii=False))
        return 1
    idx = ws.index(args.event_week)
    baseline = args.baseline or (ws[idx - 1] if idx > 0 else ws[0])
    post = ws[idx:]

    treated = [x.strip() for x in args.treated.split(",") if x.strip()]
    control = [x.strip() for x in args.control.split(",") if x.strip()]

    mode = "ratio" if (args.from_col and args.to_col) else "abs"
    if mode == "ratio":
        data = ratio_series(rows, args.group_by, args.date_col, args.from_col, args.to_col)
    else:
        data = series_abs(rows, args.group_by, args.date_col, args.metric)

    def grp_avg(groups, week):
        vals = []
        for g in groups:
            rec = data.get(g, {}).get(week)
            if rec is None:
                continue
            # ratio 模式存的是元组 (to, from, ratio)，DiD 取比率；abs 模式直接是数值
            vals.append(rec[2] if mode == "ratio" else rec)
        return avg(vals)

    # 事件前后比率均值（DiD 用比率）
    t_pre = grp_avg(treated, baseline)
    t_post = avg([grp_avg(treated, w) for w in post])
    c_pre = grp_avg(control, baseline)
    c_post = avg([grp_avg(control, w) for w in post])

    did_effect = None
    if None not in (t_pre, t_post, c_pre, c_post):
        did_effect = round((t_post - t_pre) - (c_post - c_pre), 2)

    # 累计缺口（仅比率模式）：处理组实际 to 值 vs 反事实 to 值
    # 反事实 = 处理组 from × 对照组当周比率
    cumulative_gap = None
    gap_detail = []
    if mode == "ratio":
        ctrl_ratio_by_week = {w: grp_avg(control, w) for w in post}
        gap = 0.0
        for w in post:
            cr = ctrl_ratio_by_week.get(w)
            if cr is None:
                continue
            for g in treated:
                rec = data.get(g, {}).get(w)
                if not rec:
                    continue
                to_v, from_v = rec[0], rec[1]
                counter = from_v * cr
                gap += (counter - to_v)
                gap_detail.append({"group": g, "week": w,
                                   "actual_to": round(to_v, 1),
                                   "counterfactual_to": round(counter, 1),
                                   "gap": round(counter - to_v, 1)})
        cumulative_gap = round(gap, 1)

    out = {
        "mode": mode,
        "event_week": args.event_week,
        "baseline_week": baseline,
        "post_weeks": post,
        "treated": treated,
        "control": control,
        "group_means": {
            "treated_pre": t_pre, "treated_post": t_post,
            "control_pre": c_pre, "control_post": c_post,
        },
        "did_effect_pp": did_effect,
        "cumulative_gap": cumulative_gap,
        "gap_detail": gap_detail,
        "note": "did_effect_pp = (处理组后-前) - (对照组后-前)；"
                "cumulative_gap 为事件后处理组相对对照反事实的累计缺口（绝对计数）。",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
