#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_review.py — ops-review 的总编排入口（场景层）。

把「一整个数据目录」吃进去，按表类型自动识别并算，再做两件单表算不了的事：
  1. 跨表关联运营日历：把 05 日历里的事件，反标到检测到的异常周上
     （如 detect 到 W6→W7 补贴 +89% 同步变动 → 标「= 818 大促」）
  2. 口径变更登记册：读 caliber_changelog.json，把口径变更周标记为「不可比」

输出：<数据目录>/review_output.json（结构化）+ 控制台摘要。

数字全部由子脚本（calc_metrics / did / unit_economics）算出，本脚本只做编排与关联。

用法：
    python3 scripts/run_review.py <数据目录> [--caliber-log caliber_changelog.json]

数据目录里放这几类表即可（文件名随意，按列识别）：
    城市×周表  （含 city / week / gmv）
    渠道×周表  （含 channel / spend）
    新客分群表  （含 d1_retention / arpu_30d）
    商家漏斗表  （含 leads / signed_merchants / onboarded_merchants）
    运营日历表  （含 event_type）—— 05 开头那种
"""

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

STAGE_TOKENS = ["leads", "signed", "onboarded", "first_order", "active_90d"]


def load_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def has_all(cols, *tokens):
    return all(t in cols for t in tokens)


def detect_tables(files):
    """按列签名识别表类型。返回 {type: path}。"""
    found = {}
    for p in files:
        if p.suffix.lower() != ".csv":
            continue
        rows = load_csv(p)
        if not rows:
            continue
        cols = list(rows[0].keys())
        low = [c.lower() for c in cols]
        if has_all(low, "event_type", "week", "description"):
            found.setdefault("calendar", p)
        elif has_all(low, "leads", "signed_merchants", "onboarded_merchants"):
            found.setdefault("funnel", p)
        elif has_all(low, "d1_retention", "arpu_30d") or "cohort_week" in low:
            found.setdefault("cohort", p)
        elif has_all(low, "channel", "spend") or has_all(low, "channel", "spend_wan"):
            found.setdefault("channel", p)
        elif has_all(low, "city", "week") and any("gmv" in c for c in low):
            found.setdefault("city", p)
    return found


def run_calc(path, extra):
    """调用 calc_metrics.py，返回解析后的 JSON 或 None。"""
    script = Path(__file__).parent / "calc_metrics.py"
    cmd = [sys.executable, str(script), str(path)] + extra
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return {"_error": r.stderr.strip()[:500]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"_error": "calc 输出非 JSON"}


def funnel_stages(cols):
    return [c for c in cols if any(t in c.lower() for t in STAGE_TOKENS)]


def parse_calendar(path):
    rows = load_csv(path)
    week_events = {}
    caliber = []
    for r in rows:
        w = (r.get("week") or "").strip()
        etype = (r.get("event_type") or "").strip()
        desc = (r.get("description") or "").strip()
        scope = (r.get("scope") or "").strip()
        week_events.setdefault(w, []).append({"type": etype, "scope": scope, "desc": desc})
        if etype in ("数据", "产品") and any(k in desc for k in ("口径", "放宽", "并入", "调整", "校验")):
            caliber.append({"week": w, "desc": desc, "scope": scope})
    return week_events, caliber


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir")
    ap.add_argument("--caliber-log", help="口径变更登记册 JSON（可选）")
    ap.add_argument("--anomaly", type=float, default=25.0)
    args = ap.parse_args()

    d = Path(args.data_dir)
    if not d.is_dir():
        print(json.dumps({"error": "不是目录: {}".format(d)}, ensure_ascii=False))
        return 1

    files = sorted(d.glob("*.csv"))
    tables = detect_tables(files)
    print("[编排] 识别到表：", {k: v.name for k, v in tables.items()}, file=sys.stderr)

    result = {"tables_detected": {k: v.name for k, v in tables.items()}, "steps": {}}

    # 1) 城市表
    if "city" in tables:
        out = run_calc(tables["city"], ["--group-by", "city", "--total", "--quality",
                                       "--anomaly", str(args.anomaly)])
        result["steps"]["city"] = out

    # 2) 渠道表
    if "channel" in tables:
        out = run_calc(tables["channel"], ["--group-by", "channel", "--total",
                                          "--anomaly", str(args.anomaly)])
        result["steps"]["channel"] = out

    # 3) 漏斗表
    if "funnel" in tables:
        cols = list(load_csv(tables["funnel"])[0].keys())
        ent = "city" if "city" in cols else ("channel" if "channel" in cols else None)
        stages = funnel_stages(cols)
        if ent and stages:
            out = run_calc(tables["funnel"], ["--group-by", ent, "--funnel", ",".join(stages)])
            result["steps"]["funnel"] = out

    # 4) 单位经济（渠道 + 分群 联动）
    if "channel" in tables and "cohort" in tables:
        script = Path(__file__).parent / "unit_economics.py"
        r = subprocess.run([sys.executable, str(script), str(tables["channel"]),
                            str(tables["cohort"]), "--group-by", "channel"],
                           capture_output=True, text=True, timeout=120)
        try:
            result["steps"]["unit_economics"] = json.loads(r.stdout) if r.returncode == 0 else {"_error": r.stderr[:300]}
        except json.JSONDecodeError:
            result["steps"]["unit_economics"] = {"_error": "unit_economics 输出非 JSON"}

    # 5) 跨表关联：日历反标
    annotated = []
    caliber_from_calendar = []
    week_events = {}
    if "calendar" in tables:
        week_events, caliber_from_calendar = parse_calendar(tables["calendar"])
        # 把城市表的同步变动 / 大盘异常，按周挂上日历事件
        city = result["steps"].get("city", {})
        signals = []
        for s in city.get("data_quality", {}).get("synchronized_weeks", []):
            signals.append((s["from"], s["to"], "同步变动·" + s["metric"]))
        for m in city.get("total_metrics", []):
            if m.get("anomaly"):
                signals.append((m.get("latest_period"), m.get("latest_period"),
                               "大盘异常·" + m["name"]))
        for a, b, label in signals:
            evs = week_events.get(b, []) + week_events.get(a, [])
            annotated.append({
                "signal": label, "from": a, "to": b,
                "matched_calendar": [e["desc"] for e in evs],
            })
    result["calendar_link"] = {
        "annotated_signals": annotated,
        "caliber_events_from_calendar": caliber_from_calendar,
    }

    # 6) 口径变更登记册
    non_comparable = []
    if args.caliber_log and Path(args.caliber_log).is_file():
        clog = json.loads(Path(args.caliber_log).read_text(encoding="utf-8"))
        for e in clog.get("entries", []):
            non_comparable.append(e)
    elif (d / "caliber_changelog.json").is_file():
        clog = json.loads((d / "caliber_changelog.json").read_text(encoding="utf-8"))
        for e in clog.get("entries", []):
            non_comparable.append(e)
    result["non_comparable_weeks"] = [e.get("affected_weeks") for e in non_comparable]

    # 写盘
    out_path = d / "review_output.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print("\n" + "=" * 60)
    print("ops-review 编排完成")
    print("=" * 60)
    if "city" in result["steps"]:
        cm = result["steps"]["city"]
        print("大盘：", ", ".join(f"{m['name'].replace('TOTAL_','')}={m['latest']}({m['latest_period']})"
                                 for m in cm.get("total_metrics", [])[:3]))
        q = cm.get("data_quality", {})
        print("数据质量：缺失", len(q.get("missing_cells", [])),
              "格 | 极端离群", len(q.get("extreme_outlier_groups", [])),
              "处 | 同步变动", len(q.get("synchronized_weeks", [])), "周")
    if "unit_economics" in result["steps"] and "ranking_by_unit_loss" in result["steps"]["unit_economics"]:
        rk = result["steps"]["unit_economics"]["ranking_by_unit_loss"]
        if rk:
            wl = rk[0]["unit_loss"]
            wl_word = "亏" if wl > 0 else "赚"
            print("单位经济{}渠道：{}（单用户{} {} 元）".format("最亏" if wl > 0 else "最赚",
                                                             rk[0]["channel"], wl_word, abs(wl)))
    print("日历关联：{} 个信号已反标".format(len(annotated)))
    if non_comparable:
        print("口径不可比周：", result["non_comparable_weeks"])
    print("完整结果 ->", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
