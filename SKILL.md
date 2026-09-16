---
name: ops-review
description: 运营数据复盘。给一份运营数据表（CSV/Excel），先由脚本算准环比、达成率、漏斗转化率并标出异常波动，再追问归因，最后输出一份能直接发给领导的复盘文档。当用户说"帮我写本周复盘"、"这周数据怎么样"、"写个周报"、"复盘一下"、"数据看下有什么问题"、"月报/双周复盘"时使用。支持面板数据（城市×周/渠道×周）、渠道单位经济（CAC+补贴−ARPU 算单用户盈亏）、差分法 DiD（事件净效应+累计缺口）、反 AI 味自检（黑话/空结论/无责任人）。数字必须由脚本算，模型只负责归因和表达。
description_zh: "给一份运营数据表，产出一份能交差的复盘"
description_en: "Turn operations data tables into review reports with real computed numbers"
---

# ops-review

给运营数据表，产出能直接发出去的复盘。

**一句话原则：脚本负责算数字，模型负责说人话。** 数字一个都不许模型写。

## 为什么要有这个 Skill

直接让 AI 写周报，它会编出"环比增长约 15%"这种看着像真的假数字，
领导一眼看穿。复盘的价值不在文笔，在**数字是真的、归因是具体的、动作是能追责的**。

## 工作流程

> 以下命令均以**本 Skill 所在目录**为工作目录。

### 第一步：读表，别急着算

```bash
python3 scripts/read_table.py <文件路径>
```

看清楚：有哪些列、多少行、每列大概是什么。**列名看不懂就问用户**，不要猜。

**判断数据形态（决定后面用哪种算法）：**
- **单实体时间序列**：每行一个周期（如「周报.csv」，一行一周）→ 直接进第三步。
- **面板数据（panel）**：每行是「实体 × 周期」（如「城市 × 周」「渠道 × 周」「门店 × 周」）→ **必须记下实体列名（city / channel / 门店）**，第三步用 `--group-by` 指定。不指定会静默把不同实体混成一条序列，算出跨实体的假环比。

### 第二步：先跑脚本，再动态追问（不许拿固定清单蒙用户）

复盘最忌两件事：模型自己编归因、问一堆和用户数据无关的通用问题。正确顺序是**先看数据、再针对缺口追问**：

1. **先把数据跑起来**——单表用 `calc_metrics.py`，整套目录用 `run_review.py`，拿到真实结果（不要在这一步就开口问）。
2. **用 `questions.py` 把脚本发现的缺口翻译成按优先级排好的问题：**
   ```bash
   python3 scripts/questions.py data/review_output.json --top 6 --text
   ```
   它会从「口径变更周 / 单组极端离群 / 大盘异常 / 全平台同步变动 / 缺失格 / 单位经济最亏渠道 / 比率列定义」里，挑出**用户最可能没给到、但报告必须知道**的信息来问——而不是问"你的目标是什么"这种模板问题。
3. **把这些问题抛给用户**（用 AskUserQuestion 或口头），拿到答案后**回灌**到分析里：口径变更周标「不可比」、确认是数据问题的离群按异常值处理、归因写上业务方给的原因。

> 原则：**用户没说的、脚本又绕不开的，才去问。** 能脚本算的、能日历反标的，不要拿去烦用户。宁可多问一句，也别编。
>
> 对照 `references/metric-glossary.md`：若用户答了指标口径（分母/分子/剔除规则），按他的业务校准，并把特例补进词典。

### 第三步：算（脚本算，模型不许插手）

**单实体（默认）：**
```bash
python3 scripts/calc_metrics.py <文件路径>
python3 scripts/calc_metrics.py <文件路径> --date-col 日期 --metrics GMV,下单数
python3 scripts/calc_metrics.py <文件路径> --target GMV=1250000
python3 scripts/calc_metrics.py <文件路径> --funnel 曝光,点击,下单,支付
python3 scripts/calc_metrics.py <文件路径> --anomaly 15   # 异常阈值，默认 ±20%
```

**面板数据（城市/渠道 × 周 等）—— 必须用 --group-by：**
```bash
# 按城市分组，并额外算一份跨城市聚合的「大盘」序列
python3 scripts/calc_metrics.py 城市周报.csv --group-by city --total

# 面板 + 数据质量体检（缺失格 / 重复 / 单组极端离群 / 全平台同步变动）
python3 scripts/calc_metrics.py 城市周报.csv --group-by city --total --quality

# 面板漏斗：每个城市各自一条时间序列
python3 scripts/calc_metrics.py 商家漏斗.csv --group-by city \
    --funnel leads,signed,onboarded,first_order,active90
```

面板模式输出结构：
- `total_metrics`：大盘聚合序列（比率型列如 online_rate / complaint_rate **自动跳过求和**，因为不能直接相加）
- `per_group_latest_wow`：每个实体最新环比，方便定位「是哪个城市/渠道的异常」
- `data_quality.extreme_outlier_groups`：**单实体变动远超其余中位数（>3×）→ 多半是数据污染/重复写入**，这是数据质量的红线
- `data_quality.synchronized_weeks`：多数实体同向且幅度较大 → 疑似口径变更 / 全平台统一活动（如大促），**需结合运营日历确认，不能当业务结论**

> ⚠️ 同步变动检测默认阈值 25%，轻度口径变更（如活跃商家口径放宽 ~20%）可能不触发。要抓这类，用 `--anomaly 15` 调低。

输出里 `metrics` / `total_metrics` 数组的数字**全部是算出来的，一个字都不许改**。
`anomaly: true` 的项就是这周真正该聊的东西。

### 第四步：追问归因（最关键，也最容易被跳过）

脚本只知道"支付转化率跌了 21.64%"，**它不知道为什么**——因为原因不在数据里。

必须问用户：

- 这周有什么上线/改动？（落地页、价格、活动、策略）
- 投放有变化吗？（渠道、预算、素材）
- 有没有外部事件？（节假日、竞品、天气、政策）
- 你自己觉得可能是什么原因？

**不问就不写归因。** 写"受市场环境影响"这种话等于没写，对照 `anti-slop.md` 会被判不合格。

### 第五步：写复盘

按 `references/anti-slop.md` 的结构：

```
## 核心结论        ≤3 条，每条带数字
## 异常说明        围绕脚本标出的 anomaly 展开
## 做得对的部分    有效动作要说清，因为要复制
## 下周动作        每条都带负责人 + 截止时间 + 预期效果
## 需要支持/风险
```

### 第六步：自检

写完全文，对着 `anti-slop.md` 的自检清单过一遍：

- 每个结论后面都有数字吗？
- 有黑话吗？（赋能、抓手、闭环这类）
- 下跌的指标写了吗，还是只挑好看的写？
- 归因具体到渠道/动作/时间了吗？
- 下一步有负责人和截止时间吗？

不合格就改，**不要直接交出去**。

### 第七步：一键编排（有完整数据目录时）

如果手头是一个**整套表目录**（城市×周 / 渠道×周 / 分群留存 / 商家漏斗 / 运营日历 各一份），直接跑总入口，它会自动按列识别表类型、调子脚本、并把上面四件事一次性做完：

```bash
python3 scripts/run_review.py <数据目录>            # 自动识别 5 类表，输出 review_output.json
```

输出 `review_output.json` 含：城市大盘聚合、数据质量体检、漏斗、单位经济排序、**运营日历反标**（把检测到的异常周挂上对应运营事件）、口径不可比周。

### 进阶工具：四种单表算不了的分析

下面四个脚本解决「单张表算不出来、但复盘必须回答」的问题。数字同样由脚本算，模型只拿结果去归因。

**1. 单位经济（渠道赚不赚钱）—— `unit_economics.py`**
把渠道投放表（CAC、花费、新客）和新客分群表（ARPU30d、人均补贴、留存）对起来：
```bash
python3 scripts/unit_economics.py 渠道表.csv 分群表.csv --group-by channel
```
输出每渠道 `unit_loss = CAC + 人均补贴 − 30日ARPU`（为正=每拉一个新客净亏；为负=单用户赚钱）、`subsidy_over_arpu_pct`（补贴占 ARPU 比，越高说明 GMV 越靠补贴堆），按 `unit_loss` 降序排。

**2. 准自然实验 / 差分法（DiD）—— `did.py`**
当「处理组 vs 对照组」在某个事件周后走势分叉（如某几城被抽调人力、某策略只在一部分上线），用 DiD 把事件净效应从时间趋势里抠出来，并算累计缺口（反事实）：
```bash
python3 scripts/did.py 商家漏斗.csv --group-by city --date-col week \
    --from signed_merchants --to onboarded_merchants \
    --treated 武汉,西安,长沙 --control 北京,上海,广州,深圳,成都,杭州 --event-week W5
```
输出 `did_effect_pp`（处理组净效应，百分点）和 `cumulative_gap`（事件后处理组相对对照反事实的累计缺口，绝对计数）。

**3. 反 AI 味自检 —— `lint_report.py`**
成稿后跑一遍，机器能判的硬指标全查：黑话、空结论（带「提升」但无数字）、无指代归因（「受市场环境影响」）、动作建议无负责人/时间。
```bash
python3 scripts/lint_report.py 复盘报告.md
```
输出每条违规的行号 + 类型 + 命中内容。注意：机器只查硬指标，过审不等于写得好，主观表达仍需人审。

**4. 动态追问 —— `questions.py`**
把 `run_review.py` 产出的 `review_output.json` 喂进去，自动生成**按优先级排好、且针对本次数据缺口**的追问清单（口径变更周怎么处理、某离群是数据问题还是真事件、同步变动是否对应大促、缺失格能否补全、最亏渠道怎么办、比率列定义确认）。这是把 Skill 从「固定问卷」变成「看数据再问」的关键一环。
```bash
python3 scripts/questions.py data/review_output.json --top 6 --text   # 取最关键 6 条，纯文本直接贴给用户
python3 scripts/questions.py data/review_output.json                  # 全量 JSON（带优先级/类型/可选答案）
```
拿到用户回答后回灌分析，不要凭空替用户作答。

> 数据质量体检（缺失格 / 重复键 / 单组极端离群 / 全平台同步变动）已内置在 `calc_metrics.py --quality`，由 `run_review.py` 自动调用，不必单独跑。

## 铁律

1. **数字不许编。** 只写 `calc_metrics.py` / `did.py` / `unit_economics.py` 算出来的值。表里没有的数，宁可不写。
2. **口径不许假定。** 问清楚再算。
3. **归因不许糊弄。** 没问到原因就如实写"待确认"，不要编"受市场影响"。
4. **不许只报喜。** 数据往下走的必须写。
5. **任何动作建议必须带负责人 + 截止时间。** 否则 `lint_report.py` 会判不合格。

## 参考文件

- `references/metric-glossary.md` — 指标口径词典。**用之前先按用户业务校准**，这本词典是整个 Skill 最值钱的部分
- `references/anti-slop.md` — 黑话禁用表、写作结构、自检清单

## 脚本说明

| 脚本 | 作用 | 联网 |
|---|---|---|
| `scripts/read_table.py` | 读 CSV/TSV/XLSX → JSON。xlsx 用 zipfile+XML 零依赖解析 | 无 |
| `scripts/calc_metrics.py` | 环比、均值、极值、达成率、漏斗转化率、异常波动检测；面板模式 + 数据质量体检 | 无 |
| `scripts/unit_economics.py` | 渠道单位经济：CAC + 补贴 − ARPU 算单用户盈亏，按渠道排序 | 无 |
| `scripts/did.py` | 准自然实验 / 差分法：事件净效应 + 累计缺口（反事实） | 无 |
| `scripts/lint_report.py` | 成稿反 AI 味自检：黑话 / 空结论 / 无指代归因 / 动作无责任人 | 无 |
| `scripts/questions.py` | 动态追问：把 review_output.json 的缺口翻成按优先级排好的针对性问题 | 无 |
| `scripts/run_review.py` | 总编排：自动识别 5 类表、调子脚本、跨表关联运营日历 | 无 |

全部脚本零依赖（只用标准库），纯本地运行，**不上传任何数据**。
