#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unit_economics.py — 渠道单位经济确定性计算。

把「渠道投放表」和「新客分群表」对起来，算每个获客渠道的单位经济：
CAC、30 日 ARPU、人均补贴、单用户净亏、补贴占 ARPU 比、各期留存。

回答一个最常被问的问题：「这个渠道到底赚不赚钱？」

数字全部由脚本算，模型只负责拿结果做渠道排序和决策。

用法：
    python3 scripts/unit_economics.py 渠道表.csv 分群表.csv --group-by channel

输出 JSON：每个渠道一行，含 cac / arpu_30d / subsidy_per_user / unit_loss / net_after_subsidy /
          d1/d7/d30 / subsidy_over_arpu_pct，并附一行全渠道排序结论。
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("channel_csv")
    ap.add_argument("cohort_csv")
    ap.add_argument("--group-by", default="channel")
    ap.add_argument("--cac-col", default="cac")
    ap.add_argument("--arpu-col", default="arpu_30d")
    ap.add_argument("--subsidy-col", default="subsidy_per_user")
    ap.add_argument("--d1-col", default="d1_retention")
    ap.add_argument("--d7-col", default="d7_retention")
    ap.add_argument("--d30-col", default="d30_retention")
    args = ap.parse_args()

    chan = load(args.channel_csv)
    coh = load(args.cohort_csv)
    gcol = args.group_by

    # 渠道表：CAC 取所有周均值（或用最新周，这里用均值更稳）
    cac = {}
    for r in chan:
        g = r.get(gcol)
        v = f(r.get(args.cac_col))
        if g is not None and v:
            cac.setdefault(g, []).append(v)
    cac = {g: round(sum(v) / len(v), 2) for g, v in cac.items()}

    # 分群表：按渠道聚合留存/ARPU/补贴
    agg = {}
    for r in coh:
        g = r.get(gcol)
        if g is None:
            continue
        a = agg.setdefault(g, {"d1": [], "d7": [], "d30": [], "arpu": [], "sub": [], "fo": []})
        a["d1"].append(f(r.get(args.d1_col)))
        a["d7"].append(f(r.get(args.d7_col)))
        a["d30"].append(f(r.get(args.d30_col)))
        a["arpu"].append(f(r.get(args.arpu_col)))
        a["sub"].append(f(r.get(args.subsidy_col)))
        a["fo"].append(f(r.get("first_order_rate")))

    out = []
    for g, a in agg.items():
        arpu = round(sum(a["arpu"]) / len(a["arpu"]), 1)
        sub = round(sum(a["sub"]) / len(a["sub"]), 1)
        c = cac.get(g)
        rec = {
            "channel": g,
            "cac": c,
            "arpu_30d": arpu,
            "subsidy_per_user": sub,
            "d1": round(sum(a["d1"]) / len(a["d1"]), 4),
            "d7": round(sum(a["d7"]) / len(a["d7"]), 4),
            "d30": round(sum(a["d30"]) / len(a["d30"]), 4),
            "first_order_rate": round(sum(a["fo"]) / len(a["fo"]), 4) if a["fo"] else None,
        }
        if c is not None:
            # 单用户经济账：净亏 = CAC + 人均补贴 − 30日ARPU（为正=每拉一个新客净亏；为负=单用户赚钱）
            unit_loss = round(c + sub - arpu, 1)
            rec["gross_profit_per_user"] = round(arpu - c - sub, 1)  # 单用户毛利，= −unit_loss
            rec["unit_loss"] = unit_loss
            rec["subsidy_over_arpu_pct"] = round(sub / arpu * 100, 1) if arpu else None
        out.append(rec)

    # 全渠道排序：按 unit_loss 降序（最亏=正值最大 排最前；最赚=负值 排最后）
    profitable = [r for r in out if r.get("unit_loss") is not None]
    profitable.sort(key=lambda x: x["unit_loss"], reverse=True)
    ranking = [{"channel": r["channel"], "unit_loss": r["unit_loss"],
                "cac": r["cac"], "arpu_30d": r["arpu_30d"]} for r in profitable]
    worst = ranking[0] if ranking else None
    best = ranking[-1] if ranking else None

    print(json.dumps({
        "per_channel": out,
        "ranking_by_unit_loss": ranking,
        "worst_channel": worst,
        "best_channel": best,
        "rule": "单用户净亏 unit_loss = CAC + 人均补贴 - 30日ARPU，为正即每拉一个新客净亏这么多；为负即单用户赚钱。subsidy_over_arpu 越高说明 GMV 越靠补贴堆。",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
