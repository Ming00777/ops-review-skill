# ops-review — 运营数据复盘 Skill

> 给运营数据表，产出数字经得起对账的复盘。脚本算数字，模型只负责归因和表达。

## 这是什么

`ops-review` 是一个面向运营 / 策略同学的复盘 Skill。把一份运营数据表（或一整套「城市×周 / 渠道×周 / 分群留存 / 商家漏斗 / 运营日历」目录）喂进去，先用**零依赖 Python 脚本**把环比、达成率、漏斗转化、异常波动、单位经济、差分效应**算准**，再追问业务归因，最后产出一份能直接发给领导的复盘文档。

核心铁律：**数字一个都不许模型编，归因不许糊弄，没问到的原因如实写「待确认」**。

## 给 Agent 的安装说明

本 Skill 遵循通用 Skill 目录约定（根目录放 `SKILL.md`），放到 Agent 的 skills 目录即可被自动发现：

```bash
# 方式一：克隆到 skills 目录（推荐）
git clone <本仓库地址> ~/.workbuddy/skills/ops-review

# 方式二：手动拷贝（把本仓库整体复制过去，目录名保持 ops-review）
cp -r ops-review ~/.workbuddy/skills/ops-review
```

安装后，当用户的意图匹配以下关键词时即可调用本 Skill：

> 「帮我写本周复盘」「这周数据怎么样」「写个周报」「复盘一下」「数据看下有什么问题」「月报」「双周复盘」

## 一次复盘怎么跑

```bash
# 0. 单表：先读表看列名与行数
python3 scripts/read_table.py 数据.csv

# 1. 整套目录：一键编排，自动识别 5 类表、跨表关联运营日历、标出口径不可比周
python3 scripts/run_review.py <数据目录>          # 产出 review_output.json

# 2. 动态追问：把脚本发现的缺口翻成按优先级排好的针对性问题，拿去问用户补全缺失信息
python3 scripts/questions.py data/review_output.json --top 6 --text

# 3.（按需）渠道单位经济 / 差分法 DiD / 反 AI 味自检
python3 scripts/unit_economics.py 渠道表.csv 分群表.csv --group-by channel
python3 scripts/did.py 漏斗.csv --group-by city --from signed_merchants --to onboarded_merchants \
    --treated 武汉,西安,长沙 --control 北京,上海,广州,深圳,成都,杭州 --event-week W5
python3 scripts/lint_report.py 复盘报告.md
```

所有脚本**零依赖**（仅 Python 标准库），纯本地运行，**不上传任何数据**。

## 能力清单

- **面板数据支持**：按实体（city / channel）分组聚合，比率列（online_rate 等）自动跳过求和
- **数据质量体检**：缺失格 / 重复键 / 单组极端离群（疑似污染）/ 全平台同步变动（疑似口径或大促）
- **渠道单位经济**：单用户净亏 = CAC + 人均补贴 − 30 日 ARPU，按渠道排序
- **差分法 DiD**：事件净效应 + 反事实累计缺口（处理组 vs 对照组）
- **动态追问**：先跑数据、再看缺口反推该问用户什么，而非拿固定问卷蒙人
- **反 AI 味自检**：黑话 / 空结论 / 无指代归因 / 动作无责任人

## 文件结构

```
ops-review/
├── SKILL.md                 # Skill 主文档（触发词、工作流、铁律）
├── README.md                # 本文件
├── references/
│   ├── anti-slop.md         # 黑话禁用表、写作结构、自检清单
│   └── metric-glossary.md   # 指标口径词典（用前按业务校准）
└── scripts/
    ├── read_table.py        # CSV / TSV / XLSX → JSON
    ├── calc_metrics.py      # 环比 / 达成率 / 漏斗 / 异常 / 数据质量体检
    ├── unit_economics.py    # 渠道单位经济
    ├── did.py               # 差分法 DiD
    ├── questions.py         # 动态追问生成
    ├── lint_report.py       # 反 AI 味自检
    └── run_review.py        # 总编排入口
```

## 它做不到的（诚实边界）

- **归因要你告诉我**：脚本只报「跌了 21%」，不知道为什么，原因来自业务方。
- **口径要你确认**：分母是 30 天还是 90 天、GMV 含不含补贴——词典在 `references/metric-glossary.md`，用前按业务校准。
- **同步变动 ≠ 业务结论**：检测到多数城市同步跳变，只说明「大概率口径变更或大促」，必须翻日历确认。
