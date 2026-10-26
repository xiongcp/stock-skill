# 更新日志

格式参照 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号与 `kline-structure-chart/SKILL.md`、`pyproject.toml`、`assets/analysis.schema.json`、`scripts/contract_v6.py`、`.claude-plugin/marketplace.json` 五处保持同步。

## [Unreleased]

### 文档

- 新增项目主页 <https://xiongcp.github.io/stock-skill/>：`site/` 下的中英双语单页，`.github/workflows/pages.yml` 在 push 到 main 时把它和 `examples/` 的结构图、离线交互看板一起发布到 GitHub Pages；README 顶部加入主页链接。
- README 改为中英双语：`README.md`（中文）开头新增"快速了解"（能做什么、能带来什么、30 秒上手）与英文摘要，新增完整英文版 `README.en.md`，两者互相链接、章节编号一一对应。

### 新增与增强（v6.1 演进）

- **契约变更（新增实验字段，`x-stability: experimental`）**：
  - `indicators.qlib_factors` 新增 `vpa_pullback_dryup`（近 10 日高点回落未破前低、近 3 日量能萎缩且最新 K 线具备下影或日内中上半区收盘承接）、`vpa_upthrust_distribution`（近 20 日高位放量上冲回落 UTAD 派发识别）与 `vwap20_deviation_atr`（相对 20 日滚动 VWAP 的 ATR 偏离度）。
  - `verdict.anti_quant_traps` 同步新增 `vpa_pullback_dryup` 与 `vpa_upthrust_distribution` 布尔标识及对应 `warnings_cn` 提示。
  - `plan.long.market_microstructure` 新增 `min_commission_per_leg` / `min_commission_note`（单笔最低佣金，券商口径假设）与 `regulatory_levies_buy_bps` / `regulatory_levies_sell_bps` / `regulatory_levies_note`（每边交易所/监管规费，按生效日期取值）。
- **契约变更（删除字段，经用户确认的 6.x 例外）**：删除 `meta.request.account_equity` 与 `plan.long.position_sizing.account_equity`，产物（含 HTML 看板与 Cloudflare 导出包）不再写出账户净值；`report.md` 的仓位句不再引用账户金额。仓位计算仍使用 `--account-equity` 参数。
- **微观交易摩擦与小额账户保护**：
  - `scripts/market_rules.py` 新增 `compute_round_trip_cost_detail`，只读 `market_microstructure` 的费率字段：A 股过户费（2022-04-29 起 0.001% 双向）、港股交易费/证监会/会财局征费与股份交收费（2025-06-30 起每边合计 1.27 bps）、美股 SEC Section 31 卖方费（2026-04-04 起 0.206 bps），以及券商口径的单笔最低佣金假设（A 股 5 元、港股 15 港元、美股 0.5 美元）。来源与生效日期见 `references/{cn,hk,us}-market.md`。
  - `scripts/risk.py` 在小额账户触发单笔最低佣金时自动下调推荐整手股数，确保含固定最低佣金的最大止损金额绝不突破 `risk_budget_amount`。
- **除权除息日盘中对齐与腾讯真实日线容灾**：
  - `scripts/data_gateway.py` 修复除权日盘中运行时因拆分未收盘 K 线导致当日除权因子漏算的断层问题（此时计划价已是除权后可成交口径，仓位计算不再按最后一根已收盘K线的复权因子换回除权前价格），并在主源网络/限流故障时自动回退至腾讯真实未复权日线（`TENCENT_FAILOVER_DAILY`）。
- **If-Then 交易想法 6 维双向风控审计与到期对账闭环**：
  - `scripts/risk.py` 新增 `audit_trade_idea`、`append_idea_to_ledger` 及 CLI 入口，支持 LONG/SHORT 双向审计与整数关口/支撑位过近（读 `structure.key_levels.supports`）扫损预警；目标价只与正常波动范围（q10~q90）做对照，不把 q75/q90 当止盈位；门禁未通过时不给股数与建仓指令。
  - `scripts/evaluation.py` 新增 `reconcile_trade_ideas` 及 CLI 入口，支持 T+1（按 `can_intraday_roundtrip`）、一字涨跌停锁死（涨跌幅档位按 `market_rules`）、跳空穿仓真实滑点与保本棘轮保护（`RATCHET_PROTECTED`）对账；观察期不足 `horizon_bars` 且未出结果的想法记为 `PENDING`，不计入统计。
- **离线交互看板 2.0、Cloudflare Worker 导出器与 30 组对抗评测集**：
  - `scripts/render_html.py` 新增三模态副图切换（`成交量 & RVOL` / `RSI(14)` / `MACD Hist`）、侧栏「If-Then 交互式净盈亏比与摩擦试算器」（费率参数与预填价位只读 `result.json`，风控否决时不预填价位）及 `export_cloudflare_worker_bundle`（绑定 `*.taylor-link.xyz` + WebP 压缩）。
  - `scripts/doctor.py` 与 `install.sh` 支持探测 `~/.hermes/skills` 分类子目录、Profile 路径与脚本集合一致性校验。

### 修复（合入后代码审查）

- 交互看板时间回放：拖到"最新截面"时图形不再变形。
  1. 最新截面与历史回放显示同样根数（回放快照窗口长度，默认 60 根），K 线宽度不再在两种视图间减半；完整序列仍在 `chart_data.json` 中。
  2. 纵轴只按 K 线、均线、80% 正常波动范围与真实收盘确定，止盈止损与情景目标不再拉伸纵轴；超出画面的价位在边缘标 ▲/▼ 并写出价格，相关线条裁剪在主图内。
  3. 右侧价位标签与情景标签按纵坐标错开，不再重叠。
- `examples/` 用录制行情 `--as-of 2026-10-15` 重新生成：报告、回测与结构图不变，结果 JSON 补齐 `[Unreleased]` 新增的实验字段并不再写出账户净值，看板按上述修复重新渲染。
- 回复校验（`scripts/lint_response.py`）：
  1. "回测 + 95% 区间"的例外只对"胜率"本身生效。此前同句只要出现这两个词，任何概率、频率、占比数字都会放行（如"历史条件频率 55%（回测样本，95% 区间…）"）。
  2. 认可"95% 置信区间"的写法，此前合规的回测胜率句会被误拦。
  3. "占比 / 比例"改为只在挂接涨跌、盈亏、命中、到达等结果事件时按频率数字拦截（上涨占比、先到目标的比例），不再用枚举前缀排除；"成交量占比 30%"等不再误拦。
- 命令行：result.json / chart_data.json 未通过契约自检时，报 `INTERNAL_ERROR`、退出码 1（新的 `contracts.ContractViolationError`），不再误报为参数错误；改为先投影并自检、通过后才写任何产物，失败时输出目录不会混入新旧文件。
- "历史长度不足"的提示补充"数据源本次只返回了部分历史（可稍后重试）"，避免把厂商截断响应说成上市太短；SKILL.md / README 的退出码表同步。
- 看板过渡适配层按情景 `id` 合并路径点，不再依赖两份列表的顺序。
- 测试：`UPDATE_V6_KEYPATHS=1` 改写快照后测试以失败结束，避免快照被静默改写；新增 `backtest.json` 的键路径快照 `tests/fixtures/contract/backtest_v6_keypaths_600519.json`。

## [6.0.0] - 2026-10-24

### 契约变更（破坏性）

- `*_result.json` 升级为 v6 稳定契约，顶层改为 `schema_version`、`meta`、`quality`、`verdict`、`market_context`、`structure`、`indicators`、`scenarios`、`plan`、`volatility_range`、`quotable_prices`、`files`、`reproducibility`；v5 → v6 字段对照见 `references/output-fields.md`。
- 新增 `*_chart_data.json`（渲染数据，非稳定接口）；删除 `*_judgment_pack.json`。
- 命令行标准输出改为 JSON 信封；退出码 2 / 3 / 4 分别对应 `INVALID_ARGUMENT` / `DATA_UNAVAILABLE` / `INSUFFICIENT_HISTORY`（此前一律为 1），stderr 前缀不变。
- 从 6.0.0 起：6.x 内只新增字段，不删除、不改名、不改语义。

### 删除（去预测化）

- 高置信反转层、L3 受约束研判（`judgment.py`、`validate_judgment.py`、`score_judgments.py`、`llm-judgment-protocol.md`）、L2 历史条件频率与到达用时分位（`base_rates.py`）、情景混合扇形。
- 风控中的 L2 守门随之删除，评级只由规则分、结构与风控否决决定。在录制行情上对 5 个标的（600519.SH、000001.SH、00700.HK、NVDA.US、SPY.US）每 5 个交易日取一个决策日、共 509 个决策日做 v5.3.0 与 6.0.0 的逐日对照：评级变化 5 个（1.0%），全部是 `WATCH_LONG_SETUP` → `LONG_CONFIRMED`（去掉 L2 守门后由条件观察升为确认做多），其余 504 个完全一致，`DEFENSIVE_AVOID` 与 `NEUTRAL_HOLD_WAIT` 没有任何变化。
- 情景折点时间改按扩散尺度估计（只影响画图位置）。
- `momentum.rsi14_prev` 保留为普通指标（此前供高置信反转层判断 RSI 拐头）。

### 变更

- 15 日分位区间改称"正常波动范围"（`volatility_range`，q50/q75 去掉方向性命名）；PNG、报告与代码注释中面向用户的"保形区间 / 保形预测"同步改称"正常波动范围"，公式说明里仍保留 split-conformal 方法名。
- 回复校验：概率类数字只放行带 95% 区间的回测胜率；可引用价位改读 `quotable_prices`；数字措辞里新增「占比」「比例」（如「上涨占比 53.8%」「先到目标的比例为 62%」），实体占比、风险比例、斐波那契比例等因子或参数用语不受影响。
- `report.md` 不再输出各决策动作后 15 日的「上涨占比」表（`backtest.action_forward_returns`、`rule_score_buckets` 仍写在 `backtest.json` 里，只供维护者诊断）。
- 输入行情包（`--input`）的 `schema_version` 字面量现为 6.0.0；输入文件只检查该键是否存在、不比对取值，旧的 5.3.0 行情包仍可加载。
- 单次分析耗时（本机，录制行情，`run_pipeline` 含出图与看板，不含回测）：5 个标的合计 v5.3.0 6.67 秒、6.0.0 6.03 秒（各标的 v5.3.0 1.13~1.68 秒、6.0.0 1.09~1.29 秒，每次分析约快 0.1 秒，差异在单次运行的波动范围内）。

### 6.0.0 开发期间先行合入的变更（部分已被上文"删除"取代）

以下条目在 6.0.0 之前已合入、尚未发布，仍然有效的保留原文；涉及已删除能力的只各留一行说明。

#### 已被 6.0.0 取代的条目（仅留说明）

- 高置信反转层（暂停输出与 `HC_PAUSE_REASONS`、`decision_probability`、触发口径与 `high_conviction_suppressed_subtype`、`math-patterns.md` 第 8 节）：6.0.0 已整体删除；`momentum.rsi14_prev` 保留为普通指标。
- `lint_response.py` 拦截"校准先验"与反转层百分比：对应内容已删除；回复校验现统一为只放行带 95% 区间的回测胜率。
- L2 历史条件频率的展示口径与 Brier 检验（`scenario_frequency_brier`）、风控 L2 守门、`scenario_conditional_fan` 的量纲修复：6.0.0 已删除，不再适用。
- L3 受约束研判（`--auto-init` 预填模板、`origin` 记录、到期打分修复、证伪条件文案）与 `*_judgment.json` 示例：6.0.0 已删除，不再适用。
- 研究脚本（H2 前向跟踪、H1 验收、开发区回测 v2 及其提速与缓存）：已随 `research/` 移至 `research/archive` 分支。
- `examples/` 此前按旧输出生成，6.0.0 按 v6 契约重新生成。

#### 变更（研究代码移出 main）

- `research/`（长周期回测、预注册、H1 验收与 H2 前向跟踪脚本）移到长期分支 `research/archive`，main 只保留技能。冻结提交打了标签：`h2-frozen`（H2 服务器运行的提交）、`main-plan-h1-v3-frozen`（主做多计划 H1 预注册 v3）、`v5.3.0-final`（移出之前 main 的最后一个提交）。H2 服务器按 `H2_RUNBOOK.md` 检出冻结提交运行，不受影响。研究设计文档仍在 `docs/superpowers/specs/`。

#### 修复（Yahoo 港股占位K线）

- `scripts/data_gateway.py` 新增 `drop_placeholder_bars`：Yahoo 日线（标的与基准）取数后剔除"成交量为 0 且开高低收四价相同"的K线。这类K线多为港股半日市当天缺数、停牌或停市日按前收盘价补出，不是真实成交；此前未过滤，会压低 ATR、扭曲 RSI 与 RVOL、被识别为十字星，回测也可能在无成交的日子成交。只剔除、只披露，不用其他来源补数：剔除数量与日期写入 `quality.notes`，完整日期写入新字段 `quality.placeholder_bars_removed.{target,benchmark}`（`references/output-fields.md`、`references/hk-market.md` 同步）。录制数据 `0700.HK` 剔除 7 根（2022-01-31、2024-12-24、2024-12-31、2025-01-28、2025-12-24、2025-12-31、2026-02-16），NVDA、SPY、^HSI 不受影响。
- 研究缓存 `research/common.py` 新增 `clean_bars`，`load_bars`、`adjusted_bars` 以及 `run_backtest.py`、`export_bundle.py` 的基准序列走同一函数，回测与线上口径一致。
- `_load_single_series` 返回值增加第 4 项（剔除的占位K线日期）。

#### 修复（资金计算与 K 线计算深度审查）

- 仓位计算（`scripts/risk.py`、`scripts/render_report_md.py`、`scripts/render_html.py`）：
  1. 风控否决或评级不是 `LONG_CONFIRMED` / `WATCH_LONG_SETUP` 时不再输出建议股数（`recommended_shares = null` 并给出 `reason`）。此前截至 2026-10-15 的 10 个录制标的全部被否决，报告仍加粗给出股数（如 510300.SH 给出 27,300 股），与"否决时只给观察位"冲突。
  2. 每股风险改按入场区上沿（区内最差成交价）计算：`入场区上沿 − 止损 + 上沿 × 往返费率`（`sizing_basis = ENTRY_HIGH_WORST_FILL`）。此前按入场区中值计算，在区内高位成交时实际亏损可能超出风险预算；现金上限同样按入场区上沿含费用计算。
  3. 新增 `max_loss_at_stop`（按止损价成交的亏损）与 `gap_risk_note`（跳空、跌停封板、A 股 T+1 当日不可卖出时亏损可能超出预算）。
  4. `scripts/lint_response.py` 新增检查：报告未给出建议股数时，回复中出现「买入 300 股」「仓位 … 100 股」等买入股数即拦截（「每手 100 股」等制度描述不受影响）。`examples/` 中三份报告的仓位行、茅台 `result.json` 与看板、`reply.md` 同步更新（其余内容用录制行情重新生成后逐字节一致）。
- 资金参数校验（`scripts/analyze_and_forecast.py`）：`--max-risk-pct` 限定 (0, 0.05]，`--account-equity` 必须为大于 0 的有限数，取数前校验，越界以 `[INVALID_ARGUMENT]` 退出。此前把"最多亏 1%"误传为 `--max-risk-pct 1` 时，单笔风险预算等于 100% 净值，股数只受现金上限约束。
- 计划几何不变量（`scripts/risk.py`）：止损不低于入场区下沿或 TP1 不高于入场区上沿时新增 `PLAN_GEOMETRY_INVALID` 否决。真实录制行情 963 个回放决策日未出现该情形，作为防护保留。
- 回测涨跌停撮合（`scripts/evaluation.py`、`scripts/market_rules.py`）：新增 `daily_limit_prices`，按交易所规则（前收 × (1 ± 比例) 按价位四舍五入，十进制计算）求涨跌停价，复权序列换算回当日原始价口径后比较。此前用 0.1% 比例容差，低价股漏判：ST信通（600289.SH）录制数据中 53 次开盘即涨停漏判 8 次、18 次一字跌停漏判 4 次，回测会在无法成交的一字板上按开盘价买入或止损卖出（例：2024-04-16 一字跌停被当成按 1.18 止损成交，实际只能在 04-17 以 1.12 离场）。
- 低价品种指标快照精度（`scripts/indicators.py`、`scripts/strategies.py`）：均线、VWAP、Supertrend、布林带按价格量级保留小数（`max(2, ⌈4 − log10(价格)⌉)`），ATR 与 MACD 多保留 1 位；价格 ≥ 100 时仍为 2 位，输出不变。此前一律 2 位小数后再参与 `(收盘 − EMA20)/ATR`、EMA20 与 EMA50、收盘与布林带的比较，510300.SH 在 763 个交易日中有 17 天"EMA20 > EMA50"、3 天"偏离 > 2ATR"的判断因舍入翻转。RVOL 为 0 时不再被 `or 1.0` 改写。
- 文档与实现对齐：`references/math-patterns.md` 第 7 节"区间观察"的入场区与止损公式改为与代码一致（此前写的是 `C − 0.40 ATR` 与 `C − 1.45 ATR`，实现为按阴阳线分支、止损 `C − 1.40 ATR`），并补充仓位公式与 `PLAN_GEOMETRY_INVALID`；`references/cn-market.md` 补充涨跌停价取整规则。

#### 新增（对话安装；uv 改为推荐而非必需）

- 新增仓库根目录 `INSTALL_FOR_AGENTS.md`：给 AI 助手的安装指引（选技能目录、检查前置、一键脚本或 venv + pip、doctor 自检、安全边界）。README 新增 3.0 节，用户把一句提示词发给助手即可完成安装。
- `SKILL.md` 新增「运行环境」一节：有 uv 用 `uv run python`；没有 uv 时在技能目录建 `.venv` 并 `pip install -r requirements.txt`，命令改用 `.venv/bin/python`。新增 `kline-structure-chart/requirements.txt`（只含 Pillow，版本与 `uv.lock` 一致，由文档契约测试校验）。
- `numpy` 从运行期依赖移到 dev 组：运行期脚本从未导入 numpy（只有 TA-Lib 金标准测试使用），运行期依赖只剩 Pillow。
- `scripts/doctor.py`：没有 uv 但当前 Python 与依赖可用时不再判为失败，修复建议补充 venv + pip 路径。
- 一键安装脚本缺 uv 时不再直接退出（`install.sh`、`install.ps1`）：未加 `--install-uv` / `-InstallUv` 且不安装 uv 时（非交互运行或回答 N），依次查找 `python3.13`、`python3.12`、`python3`（Windows：`py -3.13`、`py -3.12`、`python`）中第一个 ≥ 3.12 的解释器，在技能目录建 `.venv` 并 `pip install -r requirements.txt`，跳过 `uv sync`；安装后自检跳过 pytest（不在 `requirements.txt` 中），改用 `.venv` 里的解释器运行 `doctor`；`--dry-run` 打印上述命令。找不到 3.12+ 时退出并提示安装 Python 3.12+ 或加 `--install-uv`。README 3.1 / 3.2 与 `INSTALL_FOR_AGENTS.md` 同步；`tests/test_tools.py` 新增在 PATH 中去掉 uv、临时 HOME 下的 dry-run 回归测试。

#### 修复（深度审查 P0 / P1）

- 文档命令路径：`SKILL.md` Flash SOP 中的产物文件名改为脚本实际生成的 `600519.SH_result.json`（`sanitize_filename` 保留代码中的点）。新增文档契约测试守护两类错误。
- 均值回归体制规则分披露（`scripts/strategies.py`、`scripts/render_report_md.py`）：`multi_timeframe_score` 新增 `components_total`（四组分项合计）与 `regime_adjustment`（调整前后分数、动量 IC 与 `note_cn`）；体制调整时 `rating_cn` 改为「均值回归体制评分偏多/偏空/中性」，不再沿用「多周期共振/承压」；直出摘要与结论速览同步写明调整。
- 回测与线上计划同源对齐（`scripts/risk.py`、`scripts/evaluation.py`、`scripts/render_report_md.py`、`assets/analysis.schema.json`）：`triple_barrier_plan` 新增 `breakeven_ratchet` 字段（`+0.90R` 盘中触发、当日收盘 `≥ +0.30R` 确认、自次日起保护性止损上移至 `+0.08R` 保本位及对应价格），回测直接读取计划中的 `breakeven_ratchet` 参数并修正 `entry_rule` 中“无移动止损”的错误描述；统一硬止损屏障 `stop_loss_barrier` 为盘中最低价触及离场（A 股受 T+1 约束自入场次日起生效），结构触发位与减仓位保持日线收盘判定。
- 多情景前提诚实判定与标注（`scripts/structure.py`、`scripts/scenarios.py`）：修正 `scenarios.py` 中 `st_code == "breakout_up"` / `"breakdown_down"` 与 `structure.py`（实际为 `"above"` / `"below"`）不一致的缺陷；移除将补足情景强制改写为 `precondition_met = true` 的逻辑，不足 2 个时补充的备选情景保持 `precondition_met = false` 并标注 `selection_role = "CONDITIONAL_FALLBACK"` 与 `status_cn = "待触发（前提未成立，条件备选）"`。
- `--as-of` 当日未收盘 K 线隔离（`scripts/data_gateway.py`）：无论是否指定 `--as-of`（含本地文件输入），统一执行 `split_incomplete_last_bar`，确保盘中未收盘 K 线绝不进入已收盘计算序列。
- 科创板、创业板及双创 ETF 涨跌幅限制修正（`scripts/market_rules.py`、`references/cn-market.md`）：新增 `is_cn_20pct_etf`，识别上交所 `588xxx`/`589xxx.SH` 科创板/双创 ETF 与深交所跟踪创业板/双创指数的 `159xxx.SZ` ETF（代码表与名称匹配），自 `2020-08-24` 起按 `±20%` 计算涨跌停限制。
- 移除无脚本/无数据可复现的“1,500 股盲测”调参与引用（`scripts/forecast.py`、`scripts/strategies.py`、`SKILL.md`、`references/interpretation-guide.md`）：恢复 `forecast.py` 为 `references/math-patterns.md` 第 5 节定义的 60 日对数收益 RMS 与 $\lceil (n+1)\alpha \rceil$ 次序统计量，清理文档中无法核实的盲测统计数字。
- `scripts/lint_response.py` 加强：拦截「上涨概率 70%」「大概率」「胜算」「七成」等未经校验的概率措辞（否定式说明与带 95% 区间的回测胜率除外）；价格检查扩展到交易动作前后的数字（如「1330 附近买入」「目标 1440」）；风控否决须出现在回复前 400 个字符内；价格白名单不再收录频率、百分比、根数、比值等非价格字段，并补收反抽上限、回踩位、区间与缺口区等价格字段。`SKILL.md` 自己列出的错误回复示例现在会被拦截。同时修正价格识别会把「1 个交易日」等中文单位前的数字误当价格的问题，并让「规则分不是胜率或上涨概率」之类的否定说明不再误报。
- 全量金融与量化审查修复（`scripts/data_gateway.py`、`scripts/analysis.py`、`scripts/forecast.py`、`scripts/scenarios.py`、`scripts/evaluation.py`、`scripts/market_rules.py`、`scripts/render_html.py`、`scripts/render_structure_chart.py`）：
  1. 复权动作过滤 `ex_date > as_of`，本地 bundle 输入统一执行 `as_of` 截断与未收盘 K 线隔离，历史回放截面按当时截止日重算基准状态分与相对强弱；
  2. 消除 `scenarios.py` 中触发状态、目标位与失效位的自相矛盾（如看空情景下现价高于失效位、已过期交叉形态触发 `APEX_BREAKOUT` 等）；
  3. 无前视回放审计 `run_causal_replay_audit` 改为真实对比截断序列与追加极端未来 K 线后的序列；
  4. `UNAVAILABLE` 时 PNG 与 HTML 屏蔽具体价格点位，并区分 `PARTIAL` 水印文案；
  5. 回测按 `LONG_CONFIRMED` 与 `WATCH_LONG_SETUP` 分桶独立维护持仓锁，补齐 A 股 T+1 首日保本上移与到期一字跌停顺延；
  6. 支持港美股半日市/提前收市判定与 A 股 T+0 ETF 识别。

## [5.3.0] - 2026-10-18

### 新增（阶段 2：多情景引擎与历史条件频率，O3-20~O3-24）

- `scripts/scenarios.py` 与 `references/scenario-playbook.md`（`O3-20`）：8 类标准情景库（趋势延续、突破回踩、假突破回落、区间震荡、破位下行、C 浪末端反转、超跌反弹、收敛末端变盘），代码 `SCENARIO_PLAYBOOK` 与文档表格逐字一一对应；新增推动 5 浪硬规则识别（浪 2 不破浪 1 起点、浪 3 非最短、浪 4 不进入浪 1 区间）；每个截面动态输出 `2~5` 个前提成立的情景，触发位距现价不足 0.5 ATR 时归为「已在进行中」（`status = "ACTIVE"`），时间窗取历史到达目标用时分位数并截断在预测期内。
- `scripts/base_rates.py`（`O3-21`）：在标的自身历史上对每个情景规则做逐点无前视事件研究（只消费结果期已完全成熟的样本 `t + H <= t_now`），输出先到目标 / 先到失效 / 到期的频率、样本数 `n`、95% Wilson 置信区间、到达目标用时分位数与 MAE/MFE；当 `n < 30` 时通过 Beta-Binomial 向市场基准指数同情景频率收缩并标注「低样本」。
- `scripts/evaluation.py`（`O3-22`）：回测新增 `scenario_frequency_brier`，输出历史条件频率的样本外 Brier 分数、对照各情景自身基准率（per-scenario climatology）的 Brier 分数、技能分 `BSS` 与 5 档可靠性分桶表；仅当 `brier_skill_score > 0` 时才输出「历史频率具有区分度」。无前视审计同步覆盖多情景与历史条件频率。
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
- 因果动量/反转体制自适应评分、L2 历史频率守门与保本移损（`scripts/strategies.py`、`scripts/risk.py`、`scripts/evaluation.py`）：无前视计算过去 240 根成熟样本的 20 日均线偏离与 15 日前瞻收益秩相关 `causal_momentum_ic_15d`，在长期均线空头排列且自相关 $\le -0.10$ 时自动切换为均值回归体制（`MEAN_REVERSION_ADAPTED`）；接入 L2 历史频率守门与 $+0.90R \to +0.08R$ 保本移损。
- 高位派发过滤与 Flash 模型零失误增强（`scripts/risk.py`、`scripts/validate_judgment.py`、`scripts/render_report_md.py`、`SKILL.md`）：
  - 新增 60 日巨幅乖离回落（`climax_distribution`）高位派发过滤；
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
