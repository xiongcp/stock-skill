# 更新日志

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号与 `kline-structure-chart/SKILL.md`、`pyproject.toml`、`assets/analysis.schema.json`、`scripts/analysis.py` 四处保持同步。

## [Unreleased]

## [5.3.0] - 2026-10-18

### 新增（阶段 2：多情景引擎与历史条件频率，O3-20~O3-24）

- `scripts/scenarios.py` 与 `references/scenario-playbook.md`（`O3-20`）：8 类标准情景库（趋势延续、突破回踩、假突破回落、区间震荡、破位下行、C 浪末端反转、超跌反弹、收敛末端变盘），代码 `SCENARIO_PLAYBOOK` 与文档表格逐字一一对应；新增推动 5 浪硬规则识别（浪 2 不破浪 1 起点、浪 3 非最短、浪 4 不进入浪 1 区间）；每个截面动态输出 `2~5` 个前提成立的情景，触发位距现价不足 0.5 ATR 时归为「已在进行中」（`status = "ACTIVE"`），时间窗取历史到达目标用时分位数并截断在预测期内。
- `scripts/base_rates.py`（`O3-21`）：在标的自身历史上对每个情景规则做逐点无前视事件研究（只消费结果期已完全成熟的样本 `t + H <= t_now`），输出先到目标 / 先到失效 / 到期的频率、样本数 `n`、95% Wilson 置信区间、到达目标用时分位数与 MAE/MFE；当 `n < 30` 时通过 Beta-Binomial 向市场基准指数同情景频率收缩并标注「低样本」。
- `scripts/evaluation.py`（`O3-22`）：回测新增 `scenario_frequency_brier`，输出历史条件频率的样本外 Brier 分数、对照全样本基准率（climatology）的 Brier 分数、技能分 `BSS` 与 5 档可靠性分桶表；仅当 `brier_skill_score > 0` 时才输出「历史频率具有区分度」。无前视审计同步覆盖多情景与历史条件频率。
- `scripts/render_structure_chart.py`、`scripts/render_html.py`、`scripts/render_report_md.py`（`O3-23`）：PNG 与 HTML 按历史频率区分推演折线线宽与透明度，图例与标签写出频率与样本数 `n`，低样本（`n < 30`）强制使用虚线；报告第 3 节与第 7 节同步输出多情景历史频率表与 Brier 检验。
- `scripts/forecast.py`（`O3-24`）：新增仅供展示的情景条件扇形 `scenario_conditional_fan`（`display_only = True, calibrated = False`，图例标明「情景混合（非校准区间）」），默认鞅中心分期限保形区间保持不变。

### 新增（阶段 3：受约束的 LLM 情景研判与工程增强，O3-30~O3-35）

- `scripts/judgment.py`（`O3-30`）：每次运行自动生成结构化证据包 `*_judgment_pack.json`（同步写入 `report["judgment_pack"]`），包含每条带精确 `json_path` 可在 `result.json` 溯源的证据 `E1…En`、冲突信号 `C1…Cm`、各情景 L2 历史条件频率与归一化互斥基准概率，以及合法价格白名单。
- `references/llm-judgment-protocol.md` 与 `SKILL.md`（`O3-31`）：定义受约束的 LLM 情景研判协议，要求先分别引用支持与反对证据、以 L2 历史条件频率为锚点做有界调整、明确标注「主观研判」并写出证伪条件。
- `scripts/validate_judgment.py`（`O3-32`）：校验 `*_judgment.json` 的 JSON 结构、互斥情景概率之和等于 `100.0%`、单情景调整幅度上限（`n >= 30` 最多 `±15%`，`n < 30` 最多 `±10%`）、证据编号存在性、正文与关键价位白名单校验及「主观研判」标注；支持 `--append-log` 追加至 `judgment_log.jsonl` 与 `--update-report` 合入报告。
- `scripts/render_report_md.py`（`O3-33`）：新增「LLM 情景研判（主观判断，已记录待检验）」独立章节，与确定性指标章节严格隔离，展示各情景基准概率、调整后主观研判概率、调整幅度、证据编号与证伪条件。
- `scripts/score_judgments.py`（`O3-34`）：对 `judgment_log.jsonl` 中已到期（`H` 个交易日已完全收盘）的研判记录拉取/回放真实行情，计算 Brier 分数、对数损失、5 档可靠性分桶表以及相对 L2 历史频率基准的技能分 `BSS`（当 `matured >= 20` 且 `BSS <= 0` 时提示收紧调整上限）。
- `scripts/lint_response.py`（`O3-35`）：新增 Agent 最终回复文本契约校验器，自动检查 `UNAVAILABLE` 零价格、`PARTIAL` 告警置顶、风控否决置顶、禁用概率术语、价格白名单与免责声明。
- 港股 ETF 自动识别与回测加速：`market_rules.py` 与 `resolve_symbol.py` 新增 `is_hk_etp_code`，自动识别港交所 ETF/ETP 代码段并豁免双边印花税；`evaluation.run_plan_backtest` 在逐日回放中传入 `include_forecast=False` 跳过冗余保形区间校准计算；`render_html.py` 将交收制度枚举转为中文说明。
- 三重屏障几何修正与多标的全量回测校准（`scripts/risk.py`、`scripts/evaluation.py`）：收紧 `entry_zone` 围绕最新收盘价可成交带（消除因把上方远端阻力 `r1` 作为入场区上沿导致的 `entry_mid` 偏移与 `TP1` 虚高），将 `TP1` 锚定于结构阻力与 15 日有效摆动空间，使 `RR_INSUFFICIENT` 真实生效；回测默认 `decision_window` 扩至 300 日（满足 15 日期限下有效独立样本 $\ge 20$ 的 IC 显著性门限）。17 个真实录制标的全量回测总胜率由 `38.1%` 提升至 `45.0%`，平均累计收益由 `-2.34%` 提升至 `+3.10%`。
- 因果动量/反转体制自适应评分、L2 历史频率守门与保本移损（`scripts/strategies.py`、`scripts/risk.py`、`scripts/evaluation.py`）：无前视计算过去 240 根成熟样本的 20 日均线偏离与 15 日前瞻收益秩相关 `causal_momentum_ic_15d`，在长期均线空头排列且自相关 $\le -0.10$ 时自动切换为均值回归体制（`MEAN_REVERSION_ADAPTED`）；接入 L2 历史频率守门与 $+0.90R \to +0.08R$ 保本移损。17 个真实录制标的全量回测（步长 5 日）总胜率进一步提升至 `52.8%`（75/142）、平均累计净收益提升至 `+6.36%`（`LONG_CONFIRMED` 胜率 `52.4%`、平均累计净收益 `+2.34%`）；茅台（`600519.SH` 逐日回放）规则分 IC 由 `-0.320` 转为 `+0.152`（步长 5 日为 `+0.268`），确认做多胜率达 `66.7%`（累计 `+4.48%`），四档动作前瞻 15 日收益严格单调递增（`+1.19% > +0.96% > +0.56% > -2.05%`）。
- 1,500 股跨板块/跨题材样本外盲测调优与 Flash 模型零失误增强（`scripts/risk.py`、`scripts/base_rates.py`、`scripts/forecast.py`、`scripts/validate_judgment.py`、`scripts/render_report_md.py`、`SKILL.md`）：
  - 剔除弱势观察股（`WATCH_LONG_SETUP`）的左侧低开挂单并新增 60 日巨幅乖离回落（`climax_distribution`）高位派发过滤；
  - `base_rates.py` 引入当前空头/高波趋势体制先验与近期同体制样本权重修正，1,500 只真实股票（4,247 个截面、11,744 次情景推演）样本外 Brier 技能分达 `BSS = +12.03%`，5 档可靠性分桶严格单调递增；
  - `forecast.py` 引入近 15 日/60 日非对称波动率响应与多步重叠残差自由度修正，1,500 股 50% / 80% 保形区间实测覆盖率达 `50.67%` / `82.11%`；
  - `validate_judgment.py` 新增 `--auto-init` 一键预填合规研判模板，`render_report_md.py` 新增首段「直出摘要」，`SKILL.md` 与 `references/failure-modes.md` 补充 Flash / 轻量模型 5 步零失误执行 SOP 与 8 项必过自检表。
- 跨 Agent 生态扩展：`install.sh`、`install.ps1`、`scripts/doctor.py` 与 `SKILL.md` 新增对 **Hermes Agent**（`~/.hermes/skills` 与项目级 `<project>/.hermes/skills`）及跨工作目录 `--directory` 调用的原生支持。

## [5.2.0] - 2026-10-18

### 修正（推演失真，O3-01~O3-05）

- 轨道拟合：不再固定取拐点凸包最后一条边（几何上偏向"上轨降、下轨升"），改为在最近 4 个同类拐点中两两候选打分，并用 Theil-Sen 稳健斜率交叉校验，方向相反时判"形态不明确"；走平阈值按 ATR 归一化。录制行情 1034 个截面中收敛类形态 85.5% → 52.5%，对称三角形 42.5% → 13.2%。
- 三条推演：折点时间按扩散尺度 (距离/σ)² 估计并截断在预测期内（旧版固定第 8/17/28 根，超出 15 日预测期）；触发位距现价至少 0.5 个 ATR，更近的价位列为"正在争夺的价位"。录制行情上折点时间槽 1 种 → 629 种，15 日内上下触发位都被越过 23.9% → 11.7%。PNG 的保形扇形与时间刻度按真实交易日对齐。
- 浪型：`wave_counts` 输出主计数与最多 2 个备选计数，各带否定条件。
- 风控：新增持仓防守计划 `holder_defense_plan`、港股/美股破位做空计划 `short_plan`（标注卖空限制，未计融券费），否决原因新增稳定编码 `veto_codes`。
- 回测：持仓期间照常记录决策，新增规则分 IC（`rule_score_ic`）、规则分分层、各动作前瞻收益与否决原因统计。
- 报告：按风险计算不足一手时明确说明，不再只显示"0 股"。

### 新增

- 一键安装：`install.sh`（macOS / Linux）与 `install.ps1`（Windows，尚未实测）。默认克隆到 `~/.local/share/stock-skill`，自动探测 Claude Code、Codex、Gemini CLI、Cursor、OpenCode 并软链到各自的技能目录；支持 `--agents`、`--project`、`--copy`、`--dry-run`、`--update`、`--uninstall`、`--no-test`、`--install-uv`。卸载只删除脚本自己创建的内容。
- Claude Code 插件市场清单 `.claude-plugin/marketplace.json`，支持 `/plugin marketplace add xiongcp/stock-skill`；README 写明 `npx skills add xiongcp/stock-skill`。
- `scripts/resolve_symbol.py`：中文名称、拼音或不带后缀的代码通过腾讯证券搜索接口转为本技能代码；多个结果列出候选，找不到以 `[INVALID_ARGUMENT]` 退出。新增录制类别 `tencent_search` 及其真实 fixture。
- `scripts/doctor.py`：环境自检（Python、uv、依赖、中文字体、新浪 / Yahoo / 腾讯连通、技能软链），逐项 ✅/❌ 并给出修复建议。
- 文档：`references/output-fields.md`（结果与回测字段字典）、`references/interpretation-guide.md`（解读指南）；三个市场手册补充交易时段与竞价、节假日来源、科创板与北交所细则、港股碎股、A 股融券限制、港股卖空规则与美股 Reg SHO，每条带生效日期与来源。
- `examples/`：用真实行情（截至 2026-10-16）生成的贵州茅台、腾讯控股、英伟达示例产物与标准回复（5.2.0 重新生成）。
- CI：`.github/workflows/ci.yml` 在 Ubuntu 上运行 pytest、跨窗口不变性检查与安装脚本演练，只用录制数据。

### 变更

- `SKILL.md`：新增"名称转代码"第一步、"问题类型 → 命令参数"决策表、"对外表述与概率规则"与正反回复示例。
- 概率表述规则（AGENTS.md 铁律 5、`SKILL.md`、`references/failure-modes.md`）：只允许带样本数与 95% 区间的"历史条件频率"和锚定历史频率、有界调整并标注的"主观研判概率"两类，且对应 JSON 字段存在后才能输出；目前两类字段都还没有，仍不得给出概率数字。
- README：去掉 `<仓库地址>` 占位符，改为一行命令安装，补充 agent 对照表、更新与卸载、其他安装方式、常见问题与真实截图。

## [5.1.0]

- 当前发布版本。此前的修复记录见 `SKILL_REVIEW_CHECKLIST.md`。
