#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
questions.py — 把脚本真实跑出来的结果，翻译成「该问用户的问题」。

设计动机：复盘最怕两件事——(1) 模型自己编归因；(2) 问一堆和用户数据无关的
通用问题。本脚本让 Skill 变成「先看数据、再针对缺口追问」：

  吃 run_review.py 产出的 review_output.json，
  从 数据质量 / 异常波动 / 口径变更 / 单位经济 里，
  生成一份按优先级排好的问题清单，每条都带「为什么问」的上下文。

用户（或上层 Agent）拿这份清单去问业务方，把没给到的信息补回来，
再喂回 calc_metrics / did / 报告，才算完成一次复盘。

数字全部来自脚本输出，本脚本只做「问题生成」，不编造任何结论。

用法：
    python3 scripts/questions.py <review_output.json>      # 默认排序输出全部
    python3 scripts/questions.py <review_output.json> --top 5   # 只取最关键的 5 条
    python3 scripts/questions.py <review_output.json> --text     # 纯文本清单（方便直接贴给用户）

输出 JSON：
    questions: [{priority(1最高), type, question, context, suggested_options}]
"""

import argparse
import json
import sys
from pathlib import Path


def add(qs, priority, qtype, question, context, options=None):
    qs.append({
        "priority": priority,
        "type": qtype,
        "question": question,
        "context": context,
        "suggested_options": options or [],
    })


def build(data):
    qs = []
    steps = data.get("steps", {})

    # ---------- 1) 口径变更周（影响可比性，最高优先级） ----------
    caliber = data.get("calendar_link", {}).get("caliber_events_from_calendar", [])
    for e in caliber:
        w = e.get("week", "")
        desc = e.get("desc", "")
        add(qs, 1, "caliber",
            "{} 周发生了口径变更（{}）。这几周是否要从环比/同比对比里剔除，"
            "还是标注为「不可比」后照常展示？".format(w, desc),
            "口径变更会让相邻周失去可比性，不处理会算出假波动。",
            ["剔除后对比", "标注不可比但保留", "维持原样"])

    # ---------- 2) 单组极端离群（数据质量红线） ----------
    # 注意：小基数指标（churned_merchants 等）百分比天然晃得多，且尖峰常伴随
    # 次周「回落腿」，二者是同一根因。这里做去重（同 城市×指标 只留波动最大一条）
    # + 绝对波动 <25% 的 trivial 波动跳过 + 按远超中位数倍数取 Top，避免把用户淹没。
    dq = steps.get("city", {}).get("data_quality", {}) if "city" in steps else {}
    seen = {}
    for o in dq.get("extreme_outlier_groups", []):
        g, m = o.get("group"), o.get("metric")
        key = (g, m)
        if key not in seen or abs(o.get("group_delta_pct", 0)) > abs(seen[key].get("group_delta_pct", 0)):
            seen[key] = o
    outliers = sorted(seen.values(),
                      key=lambda x: x.get("ratio_vs_median", 0), reverse=True)
    for o in outliers[:3]:
        dl = o.get("group_delta_pct")
        if dl is None or abs(dl) < 25:
            continue
        g, m, f, t = o.get("group"), o.get("metric"), o.get("from"), o.get("to")
        add(qs, 1, "outlier",
            "{} 的 {} 在 {}→{} 变动 {}%（远超其余城市中位数约 {} 倍），"
            "这是已知的数据问题（重复写入/口径错配），还是真实业务事件？".format(
                g, m, f, t, dl, o.get("ratio_vs_median")),
            "方向错了会写成假结论：若真是数据污染却当成增长，领导会信以为真。",
            ["数据问题，按异常值处理", "真实事件，保留并归因", "待核查"])

    # ---------- 3) 大盘异常波动（anomaly=true） ----------
    for m in steps.get("city", {}).get("total_metrics", []):
        if m.get("anomaly"):
            add(qs, 2, "anomaly",
                "大盘 {} 在 {} 环比 {}%，可能的原因是什么（上线/活动/投放/外部）？".format(
                    m.get("name", "").replace("TOTAL_", ""),
                    m.get("latest_period"), m.get("wow_pct")),
                "脚本只报「跌了」，不知道为什么，归因必须来自业务方。",
                ["上线/改版", "活动/大促", "投放变化", "外部事件", "待确认"])

    # ---------- 4) 全平台同步变动（疑似大促/口径） ----------
    for s in dq.get("synchronized_weeks", []):
        add(qs, 2, "synchronized",
            "多数城市 {} 在 {}→{} 同步变动 {}%，是否对应某次全平台活动或口径调整？".format(
                s.get("metric"), s.get("from"), s.get("to"),
                s.get("median_group_delta_pct")),
            "同步变动通常说明是「全局动作」而非单城业务，需结合运营日历确认。",
            ["全平台大促", "口径调整", "全局策略", "其他"])

    # ---------- 5) 缺失格 ----------
    for miss in dq.get("missing_cells", []):
        add(qs, 2, "missing",
            "{} 在 {} 的数据缺失，能否补全，或确认是采集/同步失败？".format(
                miss.get("group"), miss.get("period")),
            "缺失会让该实体序列断裂，影响环比与大盘聚合。",
            ["可补全", "确认缺失", "用前后均值插补"])

    # ---------- 6) 单位经济最亏渠道 ----------
    ue = steps.get("unit_economics", {})
    worst = ue.get("worst_channel")
    if worst and worst.get("unit_loss", 0) > 0:
        add(qs, 3, "unit_econ",
            "{} 单用户净亏 {} 元（CAC+补贴−ARPU），下一步怎么处理？".format(
                worst.get("channel"), worst.get("unit_loss")),
            "持续净亏的渠道要么优化单位经济，要么收缩预算。",
            ["优化转化/复购", "缩减预算", "维持观察", "关停"])

    # ---------- 7) 比率列口径确认（用户常没给） ----------
    ratio_cols = steps.get("city", {}).get("skipped_ratio_columns_in_total", [])
    if ratio_cols:
        add(qs, 3, "definition",
            "以下比率列的定义请确认（分母/分子各是什么，是否带剔除规则）：{}。".format(
                "、".join(ratio_cols)),
            "比率口径是 90% 分歧的来源，模型不该自己假定。",
            ["按通用口径", "我来补充定义"])

    # 排序：优先级数字小的大在前；同优先级保持出现顺序
    qs.sort(key=lambda x: x["priority"])
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("review_json")
    ap.add_argument("--top", type=int, help="只输出前 N 条最关键的")
    ap.add_argument("--text", action="store_true", help="输出纯文本清单（便于直接贴给用户）")
    args = ap.parse_args()

    p = Path(args.review_json)
    if not p.is_file():
        print(json.dumps({"error": "文件不存在: {}".format(p)}))
        return 1
    data = json.loads(p.read_text(encoding="utf-8"))
    qs = build(data)
    if args.top:
        qs = qs[: args.top]

    if args.text:
        for i, q in enumerate(qs, 1):
            print("【P{} · {}】{}".format(q["priority"], q["type"], q["question"]))
            if q["context"]:
                print("   背景：{}".format(q["context"]))
            if q["suggested_options"]:
                print("   可选：{}".format(" / ".join(q["suggested_options"])))
            print()
    else:
        print(json.dumps({
            "total": len(qs),
            "questions": qs,
            "rule": "这些问题由脚本发现的缺口自动生成；拿到答案后再喂回分析，"
                    "不要凭空替用户回答。",
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
