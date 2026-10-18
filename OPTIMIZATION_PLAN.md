# `kline-structure-chart` 第三轮复查：走势判断、LLM 研判与一键安装优化方案

- 复查日期：2026-10-17
- 复查范围：`kline-structure-chart/` 全部脚本、`SKILL.md`、`references/*`、`README.md`、`AGENTS.md`、既有产物 `artifacts/*`
- 复查方法：
  1. 通读结构层、策略、风控、预测、回测与报告代码。
  2. 在 `tests/fixtures/recorded/` 的真实录制行情上做滚动截面实验（不联网、不使用任何合成数据）。
  3. 对照开源技能仓库的安装与文档做法，以及 LLM 概率预测的校准研究。
- 执行进度（2026-10-18）：阶段 0（O3-01~O3-05）与阶段 1（O3-10~O3-18）已完成并发布为 5.2.0，验证记录见 `SKILL_REVIEW_CHECKLIST.md` 第 6 节；决策 1 选 A、决策 3 选 A、决策 4 用默认目录、决策 5 暂不接入。阶段 2~4 尚未开始。

---

## 0. 结论速览

你的判断是对的，而且问题比"ABC 推演都一样"更深一层：

1. **三条推演是固定模板。** 不管什么股票、什么形态、规则分是 16 还是 89，永远输出"上攻 / 震荡 / 下探"三条线。三条线的折点时间槽完全相同（第 8、17、28 根），而且三条线等权画出，没有任何可能性高低之分。
2. **ABC 只有一种数法。** A 浪起点永远锚定在近 120 日的最高点或最低点，只保留一个计数，不考虑推动 5 浪、三角形整理，也不给备选计数。
3. **形态识别有系统性偏差。** 轨道总是取凸包的"最后一条边"，几何上天然偏向"上轨向下、下轨向上"，所以约 85% 的截面被判成收敛形态，其中对称三角形占四成以上。
4. **触发位离现价太近，"震荡"路径几乎不会发生。** 触发位距现价中位数只有 0.8~0.9 个 ATR，15 日内只有约 6% 的情况走成"震荡"；还有 24% 的情况上攻、下探两个触发位都被收盘越过。
5. **LLM 没有参与任何判断。** `SKILL.md` 规定 agent 只能转述脚本结果；`debate_and_veto` 里的"多空辩论"其实是字符串模板。系统里也没有任何经过历史检验的概率。
6. **文档偏薄，安装步骤多。** 5 个 `references` 文件合计约 130 行，没有字段字典、解读指南和示例；README 的克隆命令还留着 `<仓库地址>` 占位符。安装需要手动执行 clone、`uv sync`、软链三步，没有一键脚本、卸载、更新或自检。

优化方向分三层：

- **第一层**：把单一模板改成按结构条件生成的**多情景引擎**。
- **第二层**：为每个情景计算**历史条件频率**，带样本数和 95% 区间。这是可以检验的概率。
- **第三层**：让 LLM 在历史频率的基础上做**有界的主观研判**。脚本校验 LLM 的输出，每次研判都记入日志，事后用 Brier 分数检验 LLM 有没有带来增益。

安装方面，做到一行命令安装到 Claude Code、Codex、Gemini CLI、Cursor 等，同时兼容 `npx skills add` 和 Claude Code 插件市场。

> 需要说清楚的边界：15 个交易日的涨跌方向接近抛硬币。本次实验所有截面里，15 日后收盘上涨的占 51%。任何方法（包括 LLM）都不可能做到"准确预测"。可以做到的是：**给出校准过的概率，并持续用真实结果打分**，用数字说明它比"历史基准率"好多少，或者并不更好。

---

## 1. 实测证据

### 1.1 实验设置

| 项目 | 实验 A：结构层 | 实验 B：完整流水线 |
| --- | --- | --- |
| 数据 | `fixtures/recorded/sina_daily` 11 个序列（A 股指数、个股、ETF） | 11 个标的：A 股 7 个（含 2 个指数），港股 2 个，美股 2 个；个股配对各自市场基准 |
| 截面 | 自第 450 根起每 10 根取 1 个截面，共 616 个 | 同上，共 704 个；每个截面取最近 760 根，与线上取数一致 |
| 调用 | `structure.compute_structure_layer` | `analysis.analyze_bundle`（与线上同一流水线） |
| 前瞻期 | 15 根 | 15 根 |

局限：

- 相邻截面的结果期部分重叠，统计量不独立。
- 新浪录制数据为不复权价，除权缺口会污染个别标的的前瞻收益（如 `002594.SZ` 2025-07 的 10 送 20）。
- 以下数字用于说明问题的方向和量级，不作为策略绩效结论。

### 1.2 结果

| 编号 | 检查项 | 结果 | 说明 |
| --- | --- | --- | --- |
| E-1 | 推演折线的时间槽 | 616 个截面**只有 1 种**：上攻、下探都是第 0/8/17/28 根，震荡是第 0/12/22/28 根 | 路径形状与标的、形态、波动、规则分完全无关。目标落在第 28 根，超出 15 根的预测期和时间屏障 |
| E-2 | 形态分类分布（实验 B） | 对称三角形 292（41.5%），上升楔形 113，下降楔形 98，上升三角形 65，下降三角形 34；**收敛类合计 85.5%**；上升/下降通道合计 7.3% | 实验 A 同样是收敛类 85.2%、对称三角形 43.8%。根因见 2.2 |
| E-3 | 当前 6 份产物的形态 | 5/6 为"对称三角形"，1/6 为"上升楔形" | `artifacts/verify-1016/*`、`artifacts/{hk,us}/*` |
| E-4 | ABC 方向（实验 A） | 下跌 ABC 51%，上涨 ABC 33%，无 ABC 16%；每个截面只保留 1 个计数 | 当前 6 份产物里，3 份写着"B 浪可能仍在延续，C 浪尚未确认" |
| E-5 | 触发位距现价 | 中位数：上攻 0.81 个 ATR，下探 0.88 个 ATR；34% 的截面不到 0.5 个 ATR | 触发位基本落在日常波动噪声里 |
| E-6 | 15 日内哪条路径先被收盘触发（实验 A） | 上攻 48.7%，下探 45.1%，**震荡 6.2%** | "震荡"路径名义上是三选一，实际几乎不会发生 |
| E-7 | 15 日内上攻、下探都被触发 | 24% | 来回扫损的比例很高 |
| E-8 | 触发后先到目标还是先失效 | 上攻：到达目标 49%，失效 39%，到期 11%；下探：到达目标 58%，失效 32%，到期 11% | 这正是应该给用户看的**历史条件频率**，目前完全没有输出 |
| E-9 | `rule_score` 与 15 日收益的秩相关（IC） | 全部截面 0.101，去重叠后 0.156；**分标的从 −0.236（SPY）到 +0.257（创业板指），符号不稳定** | 合并后的 IC 可能来自标的之间的差异，不能说明规则分在单只股票上有排序能力 |
| E-10 | 动作分布与前瞻收益 | 防守回避 39%，观望 30%，确认做多 19%，观察做多 12%；风控否决 58%。"观望"截面的平均 15 日收益（+2.23%）高于"确认做多"（+1.34%） | 动作标签和后续收益之间没有稳定的对应关系 |
| E-11 | 市场状态标签与前瞻收益 | "高波失序"截面平均 +3.70%，"多头趋势"截面 +1.55%，"Squeeze 蓄势"截面 −0.18% | 状态标签只描述当下，不携带方向信息 |
| E-12 | 已有回测产物 | `NVDA.US`：9 笔，胜率 33%，累计 −24.18%，同期买入持有 +23.82%；`600519.SH`：6 笔，累计 −4.19%，同期买入持有 −9.68% | 样本都少于 30 笔，只能作为警示 |

---

## 2. 根因分析

### 2.1 推演是模板，不是推演（`structure.compute_projections`）

- 永远生成 `bull` / `neutral` / `bear` 三条路径。
- 触发位取"现价上下方最近的轨道或重要位"（`_trigger`），目标位取"阶梯里的下一个位"。
- 折点时间按固定比例 `0.26 / 0.52 / 0.86 × future_slots` 生成，而 `future_slots = max(28, …)` 与预测期 `horizon_bars` 无关。
- 路径没有与形态类型、ABC 所处浪段、市场状态、规则分发生任何联动：
  - 下降楔形和上升楔形输出同样的对称三线；
  - C 浪末端和 B 浪进行中也没有区别。
- 渲染层（PNG、HTML）把三条线等权画出，用户自然会觉得"每只股票的未来走势都一样"。

### 2.2 轨道拟合偏向收敛（`structure._hull` + `_pick_edge`）

- 上凸包各边的斜率从左到右单调递减，下凸包各边的斜率单调递增。
- `_pick_edge` 取"最近一条跨度 ≥ 10 根的边"，也就是：
  - 上轨取斜率最小的一类边，偏向向下；
  - 下轨取斜率最大的一类边，偏向向上。
- 两者叠加，形态天然偏向"上轨降、下轨升"的收敛形态，这就是 E-2 里 85% 收敛的来源。
- 真正的上升或下降通道很难被识别出来（E-2 中只占 7.3%）。

### 2.3 ABC 只有一个锚点、一种数法（`structure._abc_one` / `abc_scenario`）

- A 浪起点被固定为结构窗口的最高点（下跌 ABC）或最低点（上涨 ABC）。
- 在两种方向里取"起点更近"的一个，只保留 1 个计数，不输出备选计数。
- 不识别推动 5 浪（也就没有"浪 2 回撤不破浪 1 起点、浪 3 不是最短、浪 4 不与浪 1 重叠"等硬规则），不识别 ABCDE 三角形，也不识别"结构不清"。
- ABC 的测算位只被当作支撑压力阶梯里的价位来源，浪段状态（B 浪中、C 浪末端等）不影响推演。

### 2.4 风控只有做多一侧，"辩论"是模板（`risk.py`）

- `compute_triple_barrier_plan` 只生成做多计划：
  - 结论为"防守回避"时，对持仓者没有离场/减仓计划；
  - 港股和美股可以做空，也没有做空计划。
- `debate_and_veto.bull_case` / `bear_case` 是固定句式加形态名称的拼接，没有比较证据强弱，也没有给出结论。

### 2.5 规则分与状态分的权重从未经过检验（`strategies.py`）

- 4 组各 25 分，组内加减分（如 +6.5、−5、±3.5）和 ADX、BBW 阈值都是手工设定。
- 回测里没有报告 IC 或分层收益，E-9、E-10 显示规则分和动作标签的方向信息不稳定。
- 这类分数可以保留为"描述性"指标，但不应该驱动"确认做多"这样的结论，除非能在样本外证明有效。

### 2.6 预测区间本身没有问题，但缺少条件分布（`forecast.py`）

- 以最新收盘价为中心（鞅假设）、按期限做 split-conformal 校准，这是目前代码里最严谨的部分，应该保留为默认。
- 欠缺的是"如果走情景 X，价格大概会落在哪里"的条件分布。现有的 `historical_analogues` 只做了披露，没有用于情景频率。

### 2.7 LLM 没有角色，也没有检验闭环

- `SKILL.md` 和 `failure-modes.md` 把"编造概率"列为禁止事项。这一条本身是对的，因为没有依据的概率就是编造。
- 但系统里也没有任何**有依据的**概率，LLM 能做的只剩转述。
- 研究结论（见文末来源）：
  - LLM 直接给出的概率普遍校准不佳，在经济、金融类问题上尤其差；
  - 在预测市场基准上，多数模型的 Brier 技能分没有超过"直接报基准率"，推理增强有时还会让校准变差。
- 结论：**LLM 不能凭空给概率，但可以在有历史频率作锚点、调整幅度受限、并且事后打分的前提下参与判断。**

### 2.8 文档与安装

| 问题 | 位置 |
| --- | --- |
| `references` 5 个文件合计约 130 行；`cn/hk/us-market.md` 各 10~17 行 | `references/` |
| 没有 `result.json` 字段字典；agent 被追问某个字段时只能读代码 | — |
| 没有解读指南、情景手册和完整示例（报告 + 正确回复） | — |
| 不支持中文名称查代码：用户说"茅台""小米"，agent 只能靠记忆把名称换成代码；内置表 `KNOWN_INSTRUMENTS` 只有少数标的 | `market_rules.resolve_instrument` |
| README 克隆命令为 `git clone <仓库地址>` 占位符；安装需要手动执行三步 | `README.md` 第 3 节 |
| 没有一键安装、卸载、更新、环境自检脚本；没有 CI；没有 CHANGELOG | 仓库根目录 |
| `failure-modes.md` 第二节仍围绕 v4.1 的 `legacy_summary.small_model_copy_blocks` 编写，与 v5 的 `*_report.md` 回复骨架并存，容易让 agent 混用两套模板 | `references/failure-modes.md` |

---

## 3. 目标架构：三层判断

```text
真实 OHLCV（新浪 / Yahoo / 腾讯）
        │
        ▼
L1 确定性计算层（Python，已有 + 改造）
   指标 · 修正后的轨道 · 多候选浪型计数 · 情景库匹配 · 支撑压力阶梯 · 保形区间
        │  输出：适用情景 2~5 个（触发 / 目标 / 失效 / 时间窗 都来自结构）
        ▼
L2 经验概率层（Python，新增 scripts/base_rates.py）
   在该标的自身历史（含基准）上做逐点无前视的事件研究
   → 每个情景：历史样本数 n、先到目标频率、95% Wilson 区间、到达用时分布
        │  输出：*_judgment_pack.json（证据编号 E1…En + 情景基准频率）
        ▼
L3 LLM 研判层（由运行技能的 agent 自己完成，不需要 API Key）
   按 references/llm-judgment-protocol.md：从基准频率出发 → 引用证据编号做有界调整
   → 写 *_judgment.json → scripts/validate_judgment.py 校验 → 合入报告
        │
        ▼
评估闭环：judgment_log.jsonl + scripts/score_judgments.py
   到期后自动打分：Brier / 对数损失 / 可靠性分桶；LLM 相对 L2 基准的技能分 BSS
```

### 3.1 L1：多情景结构引擎（新增 `scripts/scenarios.py`，改造 `structure.py`）

**(1) 修正轨道拟合**

- 不再固定取凸包最后一条边，改为给候选边打分后选择：
  - 触及拐点数；
  - 跨度；
  - 末端距今的远近；
  - 被收盘价穿越的次数（作为惩罚）。
- 拐点充足时，另用 Theil-Sen 稳健回归拟合高点线和低点线做交叉校验。两种方法分类不一致时，输出"形态不明确"，而不是硬判。
- 分类阈值改为按 ATR 归一化，不再使用固定的 `FLAT_SLOPE_PCT`。

**(2) 多候选浪型计数**

- 锚点从"窗口极值"扩展为任意已确认的 ZigZag 拐点。
- 候选计数类型：
  - 下跌 ABC、上涨 ABC；
  - 上升推动 5 浪、下降推动 5 浪；
  - ABCDE 收敛三角形；
  - 结构不清。
- 推动浪的硬规则逐条检查：
  - 浪 2 不破浪 1 起点；
  - 浪 3 不是最短；
  - 浪 4 不进入浪 1 区间。
- 每个候选给出规则符合度、斐波那契比例拟合度和时间比例。输出前 3 名作为"主计数 + 备选计数"，并写明每个计数的**否定条件**，即价格走到哪里说明这个数法错了。

**(3) 情景库**

情景定义写入新文件 `references/scenario-playbook.md`，代码与文档一一对应：

| 情景 | 适用前提（由 L1 状态判定） | 目标位来源 | 失效条件 |
| --- | --- | --- | --- |
| 趋势延续 | 通道或推动浪 3/5 进行中，收盘在通道中轨之上 | 通道上轨外推 / 浪 3 = 1.618×浪 1 | 收盘跌破通道下轨 |
| 突破回踩 | 刚收盘突破轨道或颈线 ≤ 5 根 | 形态高度测量 | 收盘跌回轨道内超过 0.5 个 ATR |
| 假突破回落 | 突破后量能不足，或收盘重新回到轨道内 | 轨道另一侧 | 再次收盘站上突破位 |
| 区间震荡 | 收敛或箱体内，距两侧都超过 1 个 ATR | 区间两端 | 收盘越出任一侧 |
| 破位下行 | 收盘跌破下轨、颈线或前低 | 形态高度 / 前低 | 收盘收回破位位 |
| C 浪末端反转 | C 浪达到 1.0×A 附近，且出现背离或反转形态 | B 浪终点 | C 浪继续创新低超过 1.618×A |
| 超跌反弹 | RSI 低于 30、偏离 MA20 超过 2 个 ATR | MA20 / 0.382 反弹位 | 再创新低 |
| 收敛末端变盘 | 距收敛顶点 ≤ 10 根 | 双向，形态起点高度 | — |

规则：

- 只输出前提成立的情景，数量 2~5 个，不再固定为 3 个。
- 时间窗取历史"到达目标用时"分布的中位数和四分位数，并截断在 `horizon_bars` 以内。
- 触发位与现价至少相距 0.5 个 ATR；距离不足时，该情景归为"已在进行中"，不再作为待触发条件。

### 3.2 L2：经验概率层（新增 `scripts/base_rates.py`）

**计算方法**

1. 对 L1 输出的每个情景，把它的前提和触发条件写成可以逐日判定的规则。在该标的的完整历史上逐日重放，只使用当日及以前的数据，找出所有触发日。
2. 对每个触发日，用三重屏障打标签，看 H 根以内的结果：
   - 先到目标；
   - 先到失效位；
   - 到期。
3. 统计以下各项：
   - 样本数 n；
   - 各结果的频率与 95% Wilson 区间；
   - 到达用时的中位数；
   - 最大不利偏移（MAE）和最大有利偏移（MFE）。
4. 小样本收缩：n < 30 时，用 Beta-Binomial 向市场基准指数同一情景的频率收缩。原始频率和收缩后频率都输出，并标注"低样本"。

**输出与对外表述**

- 写入 `rule_scenarios[*].historical_frequency`。
- 对外统一叫"历史条件频率（n = …，95% 区间 …）"，不叫胜率，也不叫上涨概率。
- 统计中所有标签只使用当时已经成熟的结果期（与 `calibration_audit` 同一原则）。

**检验**

- 在 `*_backtest.json` 中报告这些频率作为预测时的 Brier 分数，对照对象是"全样本基准率"（climatology）。
- 同时输出可靠性分桶表（预测 30% 的事件实际发生了多少）。
- 只有样本外 Brier 技能分大于 0 时，报告中才允许出现"历史频率具有区分度"这样的表述。

### 3.3 L3：LLM 研判层（agent 侧，有界、可校验、可打分）

**为什么由 agent 自己做**

运行技能的 Claude Code、Codex 或 Gemini 本身就是 LLM。由它按协议完成研判，不需要额外的 API Key，也不会把行情发到第三方。

**协议**（新增 `references/llm-judgment-protocol.md`，在 `SKILL.md` 中引用）

1. 读取 `*_judgment_pack.json`，里面包含情景、历史频率、证据编号 E1…En、相互冲突的信号和数据质量。
2. **先报基准**：逐个情景写出 L2 的历史频率。
3. 列出支持证据和反对证据，**只能引用证据编号**，不得引入 JSON 以外的价格。
4. 有界调整：
   - 每个情景相对基准频率最多调整 ±15 个百分点；
   - n < 30 时最多 ±10 个百分点；
   - 所有互斥情景的概率之和必须等于 100%；
   - 每一处调整都要写明理由和所引用的证据编号。
5. 给出主情景、备选情景、需要观察的关键价位（只能从 JSON 中选），以及"什么情况说明我判断错了"。
6. 把以上内容写入 `*_judgment.json`。

**校验**（新增 `scripts/validate_judgment.py`）

- JSON 结构合规，概率之和等于 1。
- 每个情景的调整幅度不超过上限。
- 引用的证据编号都存在。
- 文中出现的每个价格都能在 `result.json` 中找到。
- 必须包含"主观研判"标注。

校验失败时脚本以非零码退出，agent 必须修正后才能回答。

**展示**

`render_report_md.py` 新增一节"LLM 情景研判（主观判断，已记录待检验）"，与确定性计算部分明确分开，并显示调整前和调整后的数值。

**检验闭环**

- 每次研判追加写入 `judgment_log.jsonl`，记录截止日、情景、概率与证据编号。
- 新增 `scripts/score_judgments.py`：对已到期的研判拉取真实行情，打出 Brier 分数和对数损失，给出 LLM 相对 L2 基准的技能分（BSS）。
- 累计样本 ≥ 30 之后，报告中显示："LLM 调整相对历史频率的技能分为 X（n = …）"。
- 如果 BSS ≤ 0，报告明确写出"LLM 调整尚未显示增益"，并在协议里收紧调整上限。

**依然禁止**

- 看图猜点位（AGENTS.md 铁律 1 保持不变）。
- 生成任何价格数字（所有价格只能来自 JSON）。
- 把新闻、资金面当作已复核的事实。可以引入用户提供的或工具查到的消息面信息，但必须标注"未复核"，并且只能作为调整理由之一，受同样的幅度上限约束。

### 3.4 预测区间

- 默认保持鞅中心和分期限保形区间不变。
- 新增"情景条件扇形"，**只用于展示**：按 L2 频率加权各情景的条件分布，图例注明"情景混合，非校准区间"。
- 只有样本外 CRPS 显著优于随机游走（用 Diebold-Mariano 检验）时，才允许把带方向的中心晋级为默认。
- 可选的研究插件 `--model kronos`：Kronos 是开源的 K 线基础模型，MIT 许可。它的输出走同一套保形审计，**默认不启用**。它依赖 PyTorch，体积大，只适合作为研究选项。

### 3.5 风控与策略

- 在做多计划之外增加：
  - **持仓防守计划**：A 股不能做空，给出减仓或离场的条件与价位；
  - **做空计划**：港股、美股，标注"需融券，实际可借券数量请向券商核对"。
- 否决原因做频率统计，写入回测，用来检查"净盈亏比 < 1.20"之类的阈值是否过严。E-10 显示否决率达 58%。
- 规则分：回测中输出逐标的的 IC 和分层收益。如果 IC 不稳定，报告中注明规则分只用于描述，不再让它单独决定"确认做多"。
- `debate_and_veto`：保留风控否决，多空辩论部分由 L3 研判替代。

---

## 4. 文档方案

参照 [Agent Skills 规范](https://agentskills.io/specification)的渐进式披露原则：`SKILL.md` 保持简洁（500 行以内），细节放到 `references/`，按需读取。

| 文件 | 改造内容 |
| --- | --- |
| `SKILL.md` | 增加"问题类型 → 命令参数"决策表；第一步改为"名称转代码"；运行后必检；L3 研判协议入口；回复模板与一正一反两个示例；更新"概率"相关铁律（见第 7 节决策 1） |
| `references/output-fields.md`（新） | `result.json`、`backtest.json`、`judgment.json` 字段字典：含义、单位、取值范围、是否可能为 null、对外怎么说 |
| `references/interpretation-guide.md`（新） | 每种形态、浪型、情景、市场状态的解读要点和常见误读 |
| `references/scenario-playbook.md`（新） | 第 3.1 节情景库的完整定义，与代码一一对应，并由测试守护 |
| `references/llm-judgment-protocol.md`（新） | 第 3.3 节协议、调整上限、证据引用格式、校验规则 |
| `references/{cn,hk,us}-market.md` | 补充交易时段（含集合竞价、港股收市竞价、美股盘前盘后）、节假日来源、北交所与科创板细则、港股碎股、美股融券与卖空提价测试规则（Reg SHO），每条附生效日期和来源 |
| `references/failure-modes.md` | 改为以 v5 的 `*_report.md` 和 `*_judgment.json` 为骨架；v4.1 的 `small_model_copy_blocks` 移到附录或删除 |
| `examples/`（新） | 用真实行情（注明截止日）生成一套完整示例：报告、研判 JSON 和对应的标准回复 |
| `README.md` | 一行命令安装；支持的 agent 对照表；更新与卸载；FAQ；真实截图；去掉 `<仓库地址>` 占位符 |
| `CHANGELOG.md`（新） | 版本记录，与四处版本号同步 |
| `AGENTS.md` | 更新目录树、铁律 5 的表述，新增脚本加入门禁 |

---

## 5. 一键安装方案

### 5.1 参考做法

| 方式 | 代表仓库 | 适用 |
| --- | --- | --- |
| `curl … install.sh \| bash` | [oaustegard/claude-skills](https://github.com/oaustegard/claude-skills)、host-uk/core | 所有 agent，最通用 |
| `npx skills add owner/repo` | [vercel-labs/skills](https://github.com/vercel-labs/skills) | 自动识别已安装的 agent；支持 `-g` 全局安装、`-a` 指定 agent、`--copy` 复制而不软链 |
| Claude Code 插件市场：`/plugin marketplace add owner/repo` | [anthropics/skills](https://github.com/anthropics/skills) | Claude Code 用户；能自动更新 |

仓库 `xiongcp/stock-skill` 是公开仓库，默认分支 `main`，三种方式都能用。

### 5.2 `install.sh`（macOS / Linux）设计

用户只需要一行：

```bash
curl -fsSL https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.sh | bash
```

脚本步骤（每一步先打印要做什么；`--dry-run` 只打印不执行）：

1. **检查依赖**
   - `git`。
   - `uv`：缺失时提示并询问是否用官方安装器安装；非交互环境需要显式传 `--install-uv`。
2. **获取代码**
   - 克隆到 `~/.local/share/stock-skill`，路径可用 `--dir` 修改。
   - 已存在时执行 `git pull --ff-only`，所以重复执行同一条命令就是更新。
3. **安装依赖**：`uv sync --frozen --python 3.12 --directory kline-structure-chart`，按 `uv.lock` 锁定版本。
4. **检查中文字体**：Linux 缺少中文字体时，打印对应发行版的安装命令。不自动执行 `sudo`。
5. **注册到 agent**
   - 自动探测已安装的 `claude`、`codex`、`gemini`、`cursor-agent`、`opencode`，把技能目录软链到各自的技能目录：
     - Claude Code：`~/.claude/skills/`
     - Codex：`~/.agents/skills/`
     - Gemini CLI：`~/.gemini/skills/`
     - Cursor：`~/.cursor/skills/`
     - OpenCode：`~/.config/opencode/skills/`
   - 可用 `--agents claude,codex` 指定 agent，用 `--project <目录>` 只装到某个项目，用 `--copy` 改为复制。
   - 目标位置已有同名的非软链目录时，先备份再替换。
6. **自检**：运行离线测试的快速子集（文档契约和契约校验，约 10 秒）和 `doctor`（见下）。`--no-test` 跳过。
7. **打印下一步**：新开会话，试问"帮我分析 600519.SH"。

其他子命令：

- `install.sh --uninstall`：只删除自己创建的软链和代码目录，不动其他技能。
- `install.sh --update`：等同重新执行安装。

### 5.3 其他入口

| 文件 | 作用 |
| --- | --- |
| `install.ps1` | Windows：用 PowerShell 完成同样的流程。软链失败时自动改为复制 |
| `.claude-plugin/marketplace.json` | 支持 `/plugin marketplace add xiongcp/stock-skill` 和 `/plugin install kline-structure-chart@stock-skill`。参照 `anthropics/skills` 的写法，插件条目设为 `"source": "./"`、`"strict": false`、`"skills": ["./kline-structure-chart"]`，**不需要挪动目录** |
| `npx skills add xiongcp/stock-skill` | 现有目录结构已能被识别（递归查找 `SKILL.md`），只需在 README 中写明并实测 |
| `kline-structure-chart/scripts/doctor.py` | 环境自检：Python 与 `uv` 版本、依赖、中文字体、新浪/Yahoo/腾讯三个数据源能否连通、技能软链是否有效；输出逐项 ✅/❌ 与修复建议 |
| `.github/workflows/ci.yml` | 推送和 PR 时自动运行 `pytest` 与跨窗口不变性检查（只用录制数据，不联网） |

---

## 6. 优化清单

- 优先级：P0 为判断失真，需要先修；P1 为核心能力；P2 为体验和文档；P3 为研究性。
- 每一项都必须在真实录制行情上有回归测试，并通过 AGENTS.md 3.3 节的两项门禁。

### 阶段 0：修正现有判断的失真（预计 2~3 天）

| ID | 优先级 | 位置 | 内容 | 验收标准 |
| --- | --- | --- | --- | --- |
| O3-01 | P0 | `structure._pick_edge` / `_classify` | 改为候选边打分，并用 Theil-Sen 交叉校验；坡度阈值按 ATR 归一化 | 录制行情上：(a) 核平滑回归 R² ≥ 0.9 且斜率 > 0 的截面，不得被判为对称三角形或下降形态；(b) 收敛类占比明显下降（目标不超过 60%，以实测为准） |
| O3-02 | P0 | `structure.compute_projections` | 折点时间改为基于历史到达用时并截断在 `horizon_bars` 以内；触发位与现价至少相距 0.5 个 ATR | 所有路径的最后一个折点 ≤ H；E-5 中"不足 0.5 个 ATR"的占比降为 0 |
| O3-03 | P0 | `structure._abc_one` / `abc_scenario` | 锚点扩展到全部已确认拐点；输出主计数加最多 2 个备选计数，各带否定条件 | 结果 JSON 中出现 `wave_counts[]`；回放测试验证无前视 |
| O3-04 | P1 | `risk.py` | 增加持仓防守计划和港股、美股做空计划；回测统计否决原因 | 报告第 4 节按市场显示对应计划；`backtest.json` 中有 `veto_reason_counts` |
| O3-05 | P1 | `evaluation.py` | 回测输出逐标的的规则分 IC、分层收益和动作标签的前瞻收益 | `backtest.json` 中有 `rule_score_ic` 和 `action_forward_returns`，报告转述 |

### 阶段 1：一键安装与文档（预计 2~3 天，可与阶段 0 并行）

| ID | 优先级 | 位置 | 内容 | 验收标准 |
| --- | --- | --- | --- | --- |
| O3-10 | P1 | `install.sh` | 按 5.2 节实现，含 `--dry-run`、`--agents`、`--project`、`--copy`、`--uninstall`、`--no-test` | 在 macOS 和 Ubuntu 的干净环境中一行命令装完，并能被 Claude Code 和 Codex 识别；重复执行即更新；卸载后不残留 |
| O3-11 | P2 | `install.ps1` | Windows 安装 | Windows 11 实测通过（目前从未实测） |
| O3-12 | P1 | `scripts/doctor.py` | 环境自检 | 断网时数据源一项为 ❌ 并给出提示；其余项可离线通过 |
| O3-13 | P2 | `.claude-plugin/` | 插件市场清单 | `claude plugin validate .` 通过，`/plugin install` 实测可用 |
| O3-14 | P1 | `scripts/resolve_symbol.py` | 中文名称或拼音转代码。数据源用腾讯证券搜索接口（真实接口，需实测确认可用并录制 fixture）；有多个结果时列出让用户选 | "茅台" 得到 `600519.SH`；"小米" 得到 `01810.HK`；找不到时以 `[INVALID_ARGUMENT]` 退出 |
| O3-15 | P1 | `README.md` | 一行安装、agent 对照表、更新与卸载、FAQ、真实截图；去掉占位符 | `test_docs_contract` 增加"无占位符"断言 |
| O3-16 | P1 | `references/output-fields.md` | 字段字典 | 测试断言：`analysis.schema.json` 的每个顶层字段都在字典中出现 |
| O3-17 | P2 | `references/interpretation-guide.md`、`{cn,hk,us}-market.md` | 解读指南与制度细则扩充 | 每条制度规则都有生效日期和来源 |
| O3-18 | P2 | `examples/`、`CHANGELOG.md`、`.github/workflows/ci.yml` | 示例、版本记录、CI | CI 在 PR 上运行通过 |

### 阶段 2：多情景引擎与历史条件频率（预计 5~7 天）

| ID | 优先级 | 位置 | 内容 | 验收标准 |
| --- | --- | --- | --- | --- |
| O3-20 | P1 | `scripts/scenarios.py`、`references/scenario-playbook.md` | 情景库：前提判定、触发、目标、失效、时间窗 | 录制行情上同一截面输出 2~5 个情景；不同形态和浪段输出的情景组合不同（可用情景组合的种类数衡量）；文档与代码的情景表一致（测试守护） |
| O3-21 | P1 | `scripts/base_rates.py` | 逐点无前视的事件研究；Wilson 区间；Beta-Binomial 收缩 | 截断未来数据后频率不变（无前视测试）；n、区间与收缩值都输出 |
| O3-22 | P1 | `evaluation.py` | 用 Brier 分数、可靠性分桶和 BSS 检验历史频率 | `backtest.json` 中有 `scenario_frequency_brier`，并与全样本基准率对比 |
| O3-23 | P1 | `render_structure_chart.py`、`render_html.py` | 按频率区分线宽和透明度；图例写出频率与 n；低样本情景用虚线 | 浏览器检查与 PNG 目视检查（按 AGENTS.md 4.5 节） |
| O3-24 | P2 | `forecast.py` | 情景条件扇形（只用于展示） | 图例写明"非校准区间"；默认区间保持不变 |

### 阶段 3：LLM 研判层（预计 4~5 天）

| ID | 优先级 | 位置 | 内容 | 验收标准 |
| --- | --- | --- | --- | --- |
| O3-30 | P1 | `analysis.py` → `*_judgment_pack.json` | 证据编号与情景基准频率打包 | 每条证据都能追溯到 JSON 中的字段路径 |
| O3-31 | P1 | `references/llm-judgment-protocol.md`、`SKILL.md` | 研判协议与回复模板 | 按协议实跑 Claude Code、Codex 各 3 只标的，`validate_judgment.py` 一次通过率 ≥ 80% |
| O3-32 | P1 | `scripts/validate_judgment.py` | 结构、概率之和、调整幅度、证据编号、价格白名单校验 | 违反任一规则的样例都被拒绝（需要单元测试） |
| O3-33 | P1 | `render_report_md.py` | 新增"LLM 情景研判"章节，与确定性部分分开 | 报告中同时显示调整前后的数值和证据编号 |
| O3-34 | P1 | `judgment_log.jsonl`、`scripts/score_judgments.py` | 研判日志与到期打分 | 能用录制行情回放的历史研判打出 Brier 和 BSS |

### 阶段 4：评估与研究（持续）

| ID | 优先级 | 内容 |
| --- | --- | --- |
| O3-40 | P2 | 每月汇总 LLM 研判与历史频率的 BSS，写入 `SKILL_REVIEW_CHECKLIST.md`；BSS ≤ 0 时收紧调整上限 |
| O3-41 | P3 | 可选的 Kronos 或其他时间序列基础模型插件，走同一套保形审计与 Diebold-Mariano 检验，默认关闭 |
| O3-42 | P3 | 周线级别的结构层（目前只有日线），用于"大级别浪型"背景 |
| O3-43 | P3 | 既有遗留项：ST 历史状态、港股 ETF 自动识别、财报日数据源、A 股 2027 年休市表（见 `SKILL_REVIEW_CHECKLIST.md` 5.3 节） |

**建议顺序**：阶段 0 和阶段 1 并行 → 阶段 2 → 阶段 3 → 阶段 4。阶段 3 依赖阶段 2 的历史频率作为锚点；如果跳过阶段 2 直接做 LLM 概率，就会回到"编造概率"的老问题。

---

## 7. 需要你拍板的决策

| # | 决策 | 选项 | 建议 |
| --- | --- | --- | --- |
| 1 | 是否允许对外输出"概率" | A. 两类都允许：脚本算出的"历史条件频率"（带 n 和区间），以及 LLM 的"主观研判概率"（锚定历史频率、幅度受限、记录待检验）；B. 只允许历史频率；C. 保持现状，不输出概率 | **A**。需要同步修改 AGENTS.md 铁律 5、SKILL.md 铁律 3/6 和 `failure-modes.md` 第 5 条 |
| 2 | LLM 研判由谁执行 | A. 由运行技能的 agent 自己完成（不需要 Key）；B. 脚本调用 Claude API（需要 Key，可脱离 agent 在命令行运行） | **A 为默认**，B 作为后续可选项 |
| 3 | 支持哪些安装入口 | A. `install.sh` + `install.ps1` + Claude Code 插件市场 + `npx skills add`；B. 只做 `install.sh` | **A**。插件市场清单可以直接指向现有的 `kline-structure-chart/` 目录，不需要调整目录结构 |
| 4 | 默认安装位置 | `~/.local/share/stock-skill`（XDG 规范）或 `~/.stock-skill` | `~/.local/share/stock-skill` |
| 5 | 是否接入深度学习基础模型（Kronos 等） | 接入（需要 PyTorch，体积大）或暂不接入 | 暂不接入，列为 P3 研究项 |

---

## 8. 风险与边界

- **不能承诺准确。** 即使做完全部优化，15 日方向判断的提升空间也有限。本方案的目标是"概率有依据、判断可检验、错了能看出来"，而不是"预测更准"。
- **过拟合风险。** 情景库和阈值越多，越容易在少数标的上拟合出好看的回测。所有阈值都要在新增功能前固定，并在至少 3 个市场、10 个以上标的上做样本外检验。
- **LLM 的不确定性。** 不同模型、不同版本的研判质量会不同，所以必须有日志和打分闭环。只有 BSS 持续为正，报告里才能说 LLM 研判"有增益"。
- **数据真实性铁律不变。** 所有新增模块（名称查询、事件研究、示例、安装自检）都只用真实行情或真实接口；接口取不到就报错，不估算、不补数。

---

## 来源

- Agent Skills 安装 CLI：[vercel-labs/skills](https://github.com/vercel-labs/skills)、[antfu/skills-cli](https://github.com/antfu/skills-cli)
- Anthropic 官方技能仓库与插件市场：[anthropics/skills](https://github.com/anthropics/skills)、[Claude Code 插件市场文档](https://code.claude.com/docs/en/plugin-marketplaces)
- curl 安装脚本示例：[oaustegard/claude-skills installing-skills](https://skills.cat/skills/oaustegard/claude-skills/installing-skills)
- LLM 预测校准：[KalshiBench（arXiv 2512.16030）](https://arxiv.org/pdf/2512.16030)、[Pitfalls in Evaluating LM Forecasters（arXiv 2506.00723）](https://arxiv.org/pdf/2506.00723)、[Evaluating LLMs on Real-World Forecasting（arXiv 2507.04562）](https://arxiv.org/pdf/2507.04562)
- K 线基础模型：[Kronos（arXiv 2508.02739）](https://arxiv.org/html/2508.02739v1)
