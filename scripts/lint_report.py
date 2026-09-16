#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lint_report.py — 复盘成稿的反 AI 味自检。

把 SKILL.md 里「铁律 / 自检清单」里能机器判定的部分自动化：
  1. 黑话检测（命中 anti-slop.md 的禁用词直接标红）
  2. 空结论检测：带「提升/下降/增长/减少/向好/优化」等词的句子，若没有数字 → 不合格
  3. 归因空话检测：出现「市场环境/大环境/大趋势」这类无指代归因 → 不合格
  4. 动作缺责任人检测：出现「建议/下一步/优化」且本句没有「负责人/截止/时间/周」类词 → 提示

输出 JSON：每条违规带行号 + 类型 + 命中内容 + 建议。

注意：这是「机器能判的硬指标」，过审不等于写得好——主观表达仍需人看。
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 与 anti-slop.md 规则二保持一致（删了意思不变的空词）
BANNED = ["赋能", "抓手", "闭环", "打法", "心智", "颗粒度", "拉通", "对齐", "沉淀",
          "组合拳", "阵地", "链路", "精细化运营", "提质增效", "打通", "卡位", "全链路",
          "沉淀方法论", "室内 brewing", "曝光 Harbor"]

# 空结论敏感词：出现即要求本句带数字
EMPTY_TRIGGER = ["明显提升", "显著提升", "持续提升", "明显增长", "大幅增长", "显著增长",
                "持续向好", "氛围向好", "稳步提升", "进一步优化", "有所改善", "有效提升",
                "持续向好", "明显好转", "不断改善"]

# 无指代归因
VAGUE_ATTR = ["受市场环境影响", "受大环境影响", "受整体环境影响", "受行业影响",
              "大环境不好", "市场因素", "宏观因素", "大趋势所致"]

# 动作/建议触发词（需带责任人或时间）。只收强建议动词；用「需要」而非裸「需」，避免误伤「需求/还需/均需」
ACTION_TRIGGER = ["建议", "下一步", "应该", "应当", "需要", "务必", "尽快",
                "推动", "落地", "跟进", "牵头"]
RESP_TRIGGER = ["负责人", "owner", "牵头", "@", "本周", "下周", "周会",
                "周三", "周四", "周五", "周一", "周二", "截止", "前完成", "前上线",
                "前交付", "排期"]

HAS_DIGIT = re.compile(r"\d")


def lint_line(num, text):
    hits = []
    for w in BANNED:
        if w in text:
            hits.append({"type": "blacklist", "word": w, "msg": "黑话，删掉意思不变"})
    for w in EMPTY_TRIGGER:
        if w in text and not HAS_DIGIT.search(text):
            hits.append({"type": "empty_claim", "word": w,
                         "msg": "带「{}」但本句无数字，按规则一应删或补数字".format(w)})
    for w in VAGUE_ATTR:
        if w in text:
            hits.append({"type": "vague_attribution", "word": w,
                         "msg": "无指代归因，需具体到渠道/动作/时间（规则四）"})
    if any(t in text for t in ACTION_TRIGGER):
        if not any(r in text for r in RESP_TRIGGER):
            hits.append({"type": "action_no_owner", "msg": "出现动作建议但无负责人/时间（规则五）"})
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report_md")
    args = ap.parse_args()
    path = Path(args.report_md)
    if not path.is_file():
        print(json.dumps({"error": "文件不存在: {}".format(path)}, ensure_ascii=False))
        return 1
    lines = path.read_text(encoding="utf-8").splitlines()
    violations = []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        # 跳过代码块 / 表格行 / 标题 / 引用 / 表格分隔行，只查正文句子
        if s.startswith("```") or s.startswith("|") or s.startswith("#") \
           or s.startswith(">") or set(s) <= {"-", "|", " "}:
            continue
        for h in lint_line(i, line):
            violations.append({"line": i, "text": s[:80], **h})

    by_type = {}
    for v in violations:
        by_type[v["type"]] = by_type.get(v["type"], 0) + 1

    print(json.dumps({
        "file": str(path),
        "total_violations": len(violations),
        "by_type": by_type,
        "violations": violations,
        "rule": "机器只查硬指标；过审不等于写得好，主观表达仍需人审。",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
