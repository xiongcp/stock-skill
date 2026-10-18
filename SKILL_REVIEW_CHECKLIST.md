# `kline-structure-chart` Skill 与 `AGENTS.md` 审查清单与修复台账

- 审查与收口日期：2026-10-15（本机时区 `America/Los_Angeles`，此时北京/香港为 `2026-10-16`）
- 第二轮复查：2026-10-16（输出准确性、可读性与安装使用文档），见第 5 节；第 0~3 节保留首轮记录，第 4 节勾选清单已按当前状态更新
- 审查对象：`AGENTS.md`，以及 `kline-structure-chart/` 下的 `SKILL.md`、`references/*`、`assets/*`、`scripts/*`、`tests/*`、`artifacts/*`
- 审查与验证方法：
  1. 通读全部源码、测试与文档。
  2. 对照 [Agent Skills 规范](https://agentskills.io/specification)与技能编写最佳实践。
  3. 执行仓库门禁命令（全量 `pytest` + 真实录制数据跨窗口不变性 `check_days_invariance.py`）。
  4. 在真实录制行情（新浪、Yahoo、腾讯指数、Yahoo 除权除息事件）上运行探针与回归测试，**彻底移除合成行情生成器与合成测试夹具**。
  5. 用 Chrome DevTools 在浏览器中实测离线交互 HTML 看板（含 `--days 60` 历史回放、7 个图层开关、RS 视图切换与控制台零报错），并目视核验三市场 PNG 图。
  6. 联网核实 A 股、港股、美股三个市场的交易制度当前有效口径与生效日期。
- 下文代码位置如无特别说明，均相对于 `kline-structure-chart/` 目录。

---

## 0. 结论与修复总览

**当前状态**：初审发现的 **P0（6 项）、P1（27 项）、P2（28 项）、P3（10 项）** 已全部完成代码与文档收口，且**所有测试数据均已切换为真实录制行情数据（共 25 份真实录制文件，零合成/Mock K 线）**。

| 维度 | 初审发现的核心问题 | 当前收口状态与验证证据 |
| --- | --- | --- |
| **数据真实性（P0）** | 非白名单代码不发请求直接走合成数据；网络失败静默回退合成行情；PNG/HTML 无告警水印；`quality.status` 误把真实 Yahoo 与 `SPY.US` 判为 `PARTIAL`；美股 VIX 写死 `19.5`；港股早盘未收盘 K 线混入计算 | ✅ 已全部修复（`A-01 ~ A-06`）。彻底删除 `generate_deterministic_series` 与 `tests/fixtures/synthetic_daily/`；断网或取数失败直接抛错并以 `[DATA_UNAVAILABLE]` 非零退出；`test_a01_* ~ test_a06_*` 全部在真实录制行情上验证通过 |
| **测试数据真实性（T 系列）** | 初审及中期修复时，`test_quant_skill.py` 仍有 24 处调用合成序列或随机游走假数据，`conftest.py` 漏录 `^VIX`、`hkHSTECH`、`002594.SZ` 等 | ✅ 已全部替换为 `tests/fixtures/recorded/` 下的 25 份真实行情与真实除权事件（含新浪 9 标的、Yahoo 6 标的含 `^VIX`、腾讯 `hkHSTECH`、Yahoo 除权除息 9 标的）；新增 `test_no_synthetic_generators_or_mock_bars_in_code_and_tests` 严禁任何合成构造器回流 |
| **三市场制度（R 系列）** | 主板 ST ±5% 过时且 `is_st` 未透传；港股价位表缺 500 以上档位与两期下调；港股印花税单边计费；休市日历缺漏且无 2021~2023；PDT 过时；`000xxx.SZ` 被当成指数；新浪不复权与 Yahoo 拆股后股息口径错位；科创板 200+1 / 北交所 100+1 申报股数与 ETF `0.001` 价位未实现 | ✅ 已全部修复（`R-01 ~ R-12`）。支持 `--st` / `--name` 自动识别 ST、主板 ST 自 `2026-07-06` 起 `±10%`；港股完整价位表含 Phase 1/2 下调与双边印花税；比亚迪（`002594.SZ`）2025-07-29 真实 10 送转 20 + 分红前复权连续性验证通过；科创板/北交所申报步长与 ETF/低价美股最小价位已实现 |
| **计算与统计（C 系列）** | EMA200 在 120 根窗口上恒为 `None` 并被 EMA60 顶替；保形校准未计涨跌停截断与价位取整；`q50` 受规则分漂移污染；回测未回放真实计划；v5 不满足 `--days` 不变性；相似 Patch 允许 10 根重叠；10 日动量写错下标、平盘首根 RSI=100、时间屏障多持 1 根 | ✅ 已全部修复（`C-01 ~ C-14`）。固定 420 根预热；`calibration_audit` 直接检验经 `_build_bands_from_multipliers` 涨跌停截断与取整后的实际区间，并披露 `q50_mae_pct` 与 `random_walk_mae_pct`；回测逐日回放线上计划并输出 Bootstrap 95% 置信区间 |
| **渲染与同源（G 系列）** | 美股 PNG 写死红涨绿跌；PNG 渲染器重算结构层；HTML 楔形轨道固定从 `nBars-60` 起画；`--days 60` 回放泄漏截面之后的未来 K 线；低价股/ETF 标签用 `:.0f` 失真；PNG 缺风控否决与保形扇形；HTML 订单块颜色未随市场切换 | ✅ 已全部修复（`G-01 ~ G-08`）。结构层抽离至 `scripts/structure.py` 只算一次；HTML 轨道按 `rail.start.idx + winOff` 还原真实起点与斜率；回放快照自带 `bars_window` 杜绝未来 K 线泄漏；PNG 含数据质量、风控否决横幅与 15 日保形扇形 |
| **规范与文档（P / AG / H 系列）** | `SKILL.md` 含非标顶层字段与本机绝对路径；四处版本号不同步；存在未实现能力宣称；`artifacts/` 残留修复前旧产物 | ✅ 已全部修复（`P-01 ~ P-08`、`AG-01 ~ AG-12`、`H-01`）。四处版本号统一为 `5.1.0`；`test_docs_contract.py` 自动守护；`artifacts/{cn,hk,us}` 已用真实数据重新生成 |

### 问题编号统计（与第 3 节条目一一对应，无重复计数）

| 级别 | 编号范围 | 数量 | 当前状态 |
| --- | --- | --- | --- |
| **P0** | `A-01 ~ A-06` | 6 | 6 项全部已修复并由回归测试守护 |
| **P1** | `R-01 ~ R-08`（8）、`C-01 ~ C-06`（6）、`G-01 ~ G-02`（2）、`P-01 ~ P-02`（2）、`AG-01 ~ AG-06`（6）、`T-01 ~ T-03`（3） | 27 | 27 项全部已修复 |
| **P2** | `R-09 ~ R-12`（4）、`C-07 ~ C-13`（7）、`G-03 ~ G-06`（4）、`P-03 ~ P-06`（4）、`AG-07 ~ AG-11`（5）、`T-04 ~ T-07`（4） | 28 | 28 项全部已修复（T-07 由契约与端到端回放守护） |
| **P3** | `C-14`（1）、`G-07 ~ G-08`（2）、`P-07 ~ P-08`（2）、`AG-12 ~ AG-13`（2）、`H-01 ~ H-03`（3） | 10 | 10 项全部已收口 |

---

## 1. 仓库与文件结构现状

| 文件 | 规模 | 职责说明 |
| --- | --- | --- |
| `AGENTS.md`（仓库根目录） | 135 行 | 面向开发者与编码 agent 的工程规范、铁律与门禁命令 |
| `kline-structure-chart/SKILL.md` | 95 行 | 符合 Agent Skills 规范的技能入口与运行/回复/失败契约 |
| `kline-structure-chart/references/*.md` | 5 个文件 | 公式字典（`math-patterns.md`）、三市场规则（`cn/hk/us-market.md`）、常见错误（`failure-modes.md`） |
| `kline-structure-chart/assets/analysis.schema.json` | 44 行 | `AnalysisReport` 唯一顶层契约（版本 `5.1.0`） |
| `kline-structure-chart/scripts/*.py` | 12 个文件 | 取数、市场规则、纯计算结构层、指标、形态、策略、风控、保形预测、回测、HTML 与 PNG 渲染 |
| `kline-structure-chart/tests/*.py` | 6 个文件 | `conftest.py`、`test_quant_skill.py`、`test_data_sources.py`、`test_indicator_golden.py`、`test_docs_contract.py`、`check_days_invariance.py`、`record_fixtures.py` |
| `kline-structure-chart/tests/fixtures/recorded/` | 25 个真实数据文件 + `MANIFEST.json` | 真实录制的新浪日线（9）、Yahoo 日线含 `^VIX`（6）、腾讯指数日线 `hkHSTECH`（1）、Yahoo 除权除息事件（9） |

---

## 2. 本轮实跑验证证据

### 2.1 门禁命令实跑输出（`[命令输出]`）

```text
$ uv run --directory kline-structure-chart pytest -v
...
tests/test_data_sources.py (19 passed)
tests/test_docs_contract.py (12 passed)
tests/test_indicator_golden.py (12 passed, 含 SMA/EMA/RSI/ATR/BB/Stoch/MACD/ADX/Supertrend)
tests/test_quant_skill.py (42 passed, 100% 使用 fixtures/recorded/ 真实行情)
============================= 85 passed in 49.30s ==============================

$ uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40
runs=420 failing=0 days=[60, 120, 200, 250, 300, 500]
```

### 2.2 三市场真实行情产物与校准覆盖率核验（`[脚本计算]`）

| 标的 | 数据源 | `quality.status` | `as_of`（已收盘） | 复权口径 | `ema200` | `vix` | 50% / 80% 滚动样本外实测覆盖率（样本数） | `q50_mae_pct` vs `random_walk_mae_pct` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `600519.SH` | `SINA_LIVE_DAILY` | `OK` | `2026-10-15` | `total_return` | `1338.12` | `None` | `0.449` / `0.841`（107） | `4.54%` / `4.54%` |
| `00700.HK` | `YAHOO_DAILY(0700.HK)` | `OK`（自动拆出 `2026-10-16` 盘中未收盘 K 线并记录于 `notes`） | `2026-10-15` | `total_return` | `395.42` | `None` | `0.748` / `0.963`（107） | `4.27%` / `4.27%` |
| `NVDA.US` | `YAHOO_DAILY(NVDA)` | `OK` | `2026-10-15` | `total_return` | `191.56` | `16.46`（真实 `^VIX`） | `0.692` / `0.916`（107） | `4.85%` / `4.85%` |

### 2.3 浏览器实测与 PNG 视觉检查（`[浏览器实测]`）

- **Chrome DevTools 实测 `--days 60` 看板（`600519.SH_dashboard.html`）**：
  - 5 个历史回放截面（`2026-05-19`、`2026-06-16`、`2026-07-15`、`2026-08-12`、`2026-09-09`）切换时，`maxBarDay <= snap.as_of` 全部为 `true`（`activeBarsLen` 分别为 `60, 60, 60, 20, 40`），彻底消除了 `--days 60` 回放退回显示未来 K 线的问题。
  - 7 个图层复选框全部关闭再开启、标的主图与相对强弱（RS）视图来回切换，控制台消息数为 `0`（`<no console messages found>`）。
- **PNG 目视检查（`600519.SH_preview.png`、`NVDA.US_preview.png`）**：
  - A 股为红涨绿跌，美股为绿涨红跌；副标题显示 `数据质量：OK · 风控否决: 已触发(禁做多)`；右侧推演区显示 `【风控否决】` 红色横幅（与左侧 K 线高点标注框零遮挡）及 15 日保形预测 50%/80% 扇形。

### 2.4 联网核实的三市场交易制度口径（2026-10 时点）

| 规则 | 当前有效口径 | 生效日期 | 权威来源 |
| --- | --- | --- | --- |
| 沪深主板 ST 涨跌幅 | 主板风险警示股票（ST/\*ST）涨跌幅由 `±5%` 调整为 `±10%`；创业板/科创板 ST 为 `±20%`；北交所 `±30%` | `2026-07-06`（`2026-04-24` 发布） | [中证网](https://cs.com.cn/tj/02/01/202506/t20250627_6498570.html)、[财联社](https://www.cls.cn/detail/2069694) |
| A 股最小申报股数 | 主板/创业板 100 股整手；科创板单笔不少于 200 股、超出按 1 股递增；北交所单笔不少于 100 股、超出按 1 股递增；ETF 最小价位 `0.001` 元 | 现行有效 | 上交所科创板交易特别规定、北交所交易规则 |
| 港股最小价位（Spread Table） | 含 500 以上各档（`0.50 / 1.00 / 2.00 / 5.00`）；Phase 1 于 `2025-08-04` 下调 10–20 档至 `0.01`、20–50 档至 `0.02`；Phase 2 于 `2026-08-03` 下调 0.5–10 档至 `0.005`（ETF 不适用下调） | `2025-08-04` / `2026-08-03` | [HKEX Reduction of Minimum Spreads](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads?sc_lang=en) |
| 港股印花税 | 买卖双方各按 `0.1%`（10 bps）双边计收；ETF 豁免 | `2023-11-17` | [KPMG](https://kpmg.com/cn/en/home/insights/2023/11/tax-alert-21-hk-the-reduced-stamp-duty-rate-on-hk-stock-transfer-will-take-effect-from-17-nov-23.html)、[IRD](https://www.gov.hk/en/residents/taxes/docs/IRSD123(E).pdf) |
| 交易日历（2021~2027） | 已收录 CN（`2021-01-01 ~ 2026-12-31`，含 `2025-06-02` 端午、`2026-02-23` 春节）、HK 与 US（`2021-01-01 ~ 2027-12-31`，含 `2025-01-09` 卡特国葬日休市） | `2021 ~ 2027` | [上交所休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/list/)、[ICE/NYSE](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx) |
| 美股结算与 PDT 规则 | SEC `T+1` 结算；FINRA 4210 修订自 `2026-06-04` 起取消 PDT 认定与 2.5 万美元门槛（券商过渡期至 `2027-10-20`）；`< $1.00` 适用 Reg NMS Rule 612 子美分最小价位 `$0.0001` | `2026-06-04` | [Schwab](https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum)、[E*TRADE](https://us.etrade.com/knowledge/library/margin/pattern-day-trading-rule-change) |

---

## 3. 问题清单与逐项修复台账

### 3.1 数据真实性（P0，共 6 项）

| ID | 级别 | 当前代码位置 | 初审问题 | 修复状态与验证测试 |
| --- | --- | --- | --- | --- |
| A-01 | P0 | `scripts/market_rules.py:130-152`、`scripts/data_gateway.py:242-274` | 非白名单 A 股新浪代码拼错、北交所无 `bj` 分支、港股 Yahoo 代码未去前导 0 | ✅ 已修复：`sina_symbol_for` / `yahoo_symbol_for` 按后缀推导并在无法映射时抛错；`test_a01_unregistered_symbols_map_to_real_vendor_codes`、`test_a01_unregistered_a_share_and_hk_are_fetched_live`（真实录制 `601318.SH` 与 `01810.HK`） |
| A-02 | P0 | `scripts/data_gateway.py:235-280`、`scripts/analyze_and_forecast.py:26-44` | 生产默认回退带人造双底的合成序列，且吞掉网络异常 | ✅ 已修复：彻底移除 `generate_deterministic_series` 与 `synthetic_daily`，任何取数失败直接抛 `RuntimeError` 并以 `[DATA_UNAVAILABLE]` 退出；`test_a02_synthetic_fallback_is_refused_by_default`、`test_no_synthetic_generators_or_mock_bars_in_code_and_tests` |
| A-03 | P0 | `scripts/render_structure_chart.py:1098-1122`、`scripts/render_html.py:22-39,828-840` | PNG 页脚写死“新浪原始值”，非 OK 数据在 PNG/HTML 无醒目横幅与水印 | ✅ 已修复：PNG 与 HTML 在 `quality.status != "OK"` 时均渲染红色告警横幅与半透明大字水印，页脚输出真实 `data_source`；`test_a03_png_and_html_flag_non_live_data` |
| A-04 | P0 | `scripts/contracts.py:20-66`、`scripts/analysis.py:424-433` | 真实 Yahoo 数据与美股基准 `SPY.US` 被误判为 `PARTIAL`；合成数据仅标 `PARTIAL` | ✅ 已修复：`classify_data_source` 按枚举判定，`SYNTHETIC` 一律判为 `UNAVAILABLE`；当标的本身即为市场默认基准（如 `SPY.US`）时 `needs_benchmark=False`；`test_a04_quality_status_uses_source_enum`、`test_multi_market_pipeline_and_backtest` |
| A-05 | P0 | `scripts/data_gateway.py:302-314` | 美股 VIX 写死 `19.5` | ✅ 已修复：实时拉取 Yahoo `^VIX`（测试回放真实录制的 `yahoo_daily/VIX.json`），取不到时填 `None` 并注明原因；`test_a05_vix_is_fetched_or_null_never_hardcoded` |
| A-06 | P0 | `scripts/market_rules.py:167-177`、`scripts/data_gateway.py:277-300,398-401` | 未收盘 K 线直接当作已收盘参与计算 | ✅ 已修复：`is_daily_bar_complete` 按交易所时区与收盘时间判定，`split_incomplete_last_bar` 将未收盘日线拆为 `intraday` 并在 `quality.notes` 披露；`test_a06_incomplete_last_bar_is_excluded` |

### 3.2 正确性：市场制度（P1 8 项 + P2 4 项）

| ID | 级别 | 当前代码位置 | 初审问题 | 修复状态与验证测试 |
| --- | --- | --- | --- | --- |
| R-01 | P1 | `scripts/market_rules.py:180-188,295-327`、`scripts/analysis.py:284-305` | ST 一刀切按 `±5%`，且主流程从未识别或透传 `is_st` | ✅ 已修复：主板 ST 自 `2026-07-06` 起 `±10%`，双创 `±20%`，北交所 `±30%`；支持 `--st` 或 `--name`（含 `ST`）自动识别并全链路透传至风控、回测与预测；`test_p1_st_and_ipo_price_limits_by_board_date_and_pipeline` |
| R-02 | P1 | `scripts/market_rules.py:330-362` | 港股价位表缺 500 以上档位及 2025-08 / 2026-08 下调 | ✅ 已修复：补齐 0.25~9995 全档位及 Phase 1/2 生效日下调（ETF 不适用下调）；`test_p1_hk_spread_table_full_bands_and_reductions` |
| R-03 | P1 | `scripts/market_rules.py:442-456,476-479` | 港股印花税只算卖方且 ETF 未豁免 | ✅ 已修复：港股股票买卖双边各 10 bps，ETF 豁免为 0；`test_p1_hk_stamp_duty_both_sides` |
| R-04 | P1 | `scripts/market_rules.py:10-79`、`scripts/forecast.py:211-212,354-358` | 休市表缺 `2025-06-02`、`2026-02-23`、美股 `2025-01-09` 及 2021~2023 历史假期 | ✅ 已修复：补齐 2021~2027 休市表；超出 `CALENDAR_COVERAGE_END` 的未来日期按工作日估算并在 `calendar_coverage` 与 `quality.notes` 明确披露；`test_p1_trading_calendar_fixes_and_coverage` |
| R-05 | P1 | `scripts/market_rules.py:457-473`、`references/us-market.md:5` | 美股 PDT 规则表述过时 | ✅ 已修复：更新为 FINRA 4210 修订（`2026-06-04` 生效，过渡期至 `2027-10-20`）；`test_p1_us_pdt_rule_updated` |
| R-06 | P1 | `scripts/market_rules.py:265-275` | `000xxx.SZ` 深市主板股票被误判为指数，ETF 未单独识别 | ✅ 已修复：按交易所+代码段精确区分指数（`000xxx.SH`/`399xxx.SZ`）、ETF（沪 `51/56/58`、深 `15`）与个股；`test_p1_asset_type_lot_step_and_tick_precision` |
| R-07 | P1 | `scripts/data_gateway.py:168-232`、`scripts/market_rules.py:482-562` | 缺少真实除权除息数据源；新浪未复权价与 Yahoo 拆股后股息单位未对齐；回测切片 `bars[:t+1]` 泄漏 `t` 之后的未来除权因子 | ✅ 已修复：接入 Yahoo `events=div,splits`，对新浪原始价乘回后续 `split_ratio` 还原真实除息额；新增 `rescale_bars_as_of` 在回测每个决策日 `t` 将历史序列还原至截至 `t` 的真实 Point-in-Time 口径；`test_point_in_time_adjustment_no_future_leakage`、`test_r07_point_in_time_adjustment_from_real_corporate_actions`、`test_c04_c05_c10_backtest_replays_live_plan_with_bootstrap_ci` |
| R-08 | P1 | `scripts/market_rules.py:90-92`、`scripts/data_gateway.py:113-138,255-258` | `HSTECH.HK` 用 `3033.HK` ETF 冒充 | ✅ 已修复：改用腾讯指数日线 `hkHSTECH`（已录制真实 1000 根日线）；`test_r08_hstech_uses_real_index_not_etf_proxy` |
| R-09 | P2 | `scripts/market_rules.py:191-203,389-396`、`scripts/risk.py:126-143` | 科创板 200 股起 + 1 股递增、北交所 100 股起 + 1 股递增未实现 | ✅ 已修复：实现 `_cn_lot_rules` 与 `round_shares_to_lot` 并在 `risk.py` 仓位计算中生效；`test_p1_asset_type_lot_step_and_tick_precision` |
| R-10 | P2 | `scripts/market_rules.py:365-387`、`scripts/structure.py:60-71`、`scripts/analysis.py:338-358`、`scripts/risk.py:155-215` | A 股 ETF `0.001` 元与美股 `< $1` 的 `0.0001` 美元最小价位未处理，且 `series_export` 与 `risk.py` 文案硬编码 `:.2f` 截断为 2 位小数 | ✅ 已修复：`tick_size_for`、`price_decimals`、`money()`、`series_export` 与 `risk.py` 全链路按品种最小价位保留小数位数（`510300.SH` `series_export` 收盘价与 `q50[0]` 精确同为 `4.242`）；`test_p1_asset_type_lot_step_and_tick_precision`、`test_g03_to_g07_rendering_invariants_and_low_price_etf` |
| R-11 | P2 | `scripts/analyze_and_forecast.py:106`、`scripts/forecast.py:214-221` | 新股前 5 日无涨跌停标识 `is_ipo_no_limit` 恒为 `False` | ✅ 已修复：支持 `--ipo-no-limit` / `is_ipo_no_limit` 全链路透传；`test_p1_st_and_ipo_price_limits_by_board_date_and_pipeline` |
| R-12 | P2 | `scripts/analyze_and_forecast.py:117-121`、`references/hk-market.md:11` | CLI 未暴露 `--color-scheme` 参数 | ✅ 已修复：CLI 新增 `--color-scheme {auto,cn_red_up,us_green_up}`；`test_g01_and_r12_png_follows_market_and_cli_color_scheme` |

### 3.3 正确性：计算与统计（P1 6 项 + P2 7 项 + P3 1 项）

| ID | 级别 | 当前代码位置 | 初审问题 | 修复状态与验证测试 |
| --- | --- | --- | --- | --- |
| C-01 | P1 | `scripts/analysis.py:80-84,210-212`、`scripts/strategies.py:24-25,106-107` | 指标只在 120 根窗口上计算导致 EMA200 恒为 `None` 并用 EMA60 冒充 | ✅ 已修复：固定在 `HISTORY_BARS=420` 历史上预热；不足时显式标注 `EMA200(N/A)`；`test_c01_ema200_is_warmed_up_and_never_substituted` |
| C-02 | P1 | `scripts/forecast.py:154-327` | 保形校准审计未计入涨跌停截断与价位取整，与展示区间脱节 | ✅ 已修复：抽出 `_build_bands_from_multipliers`，展示区间与滚动样本外审计完全共用同一套截断与取整构造；`test_c02_c03_conformal_bands_are_calibrated_and_q50_has_no_drift` |
| C-03 | P1 | `scripts/forecast.py:303-305,367-368` | `q50` 受规则分漂移污染且未披露相对随机游走误差 | ✅ 已修复：`q50` 取最新收盘价（`MARTINGALE_LAST_CLOSE`），`calibration_audit` 直接披露 `q50_mae_pct` 与 `random_walk_mae_pct`；`test_c02_c03_conformal_bands_are_calibrated_and_q50_has_no_drift` |
| C-04 | P1 | `scripts/evaluation.py:239-372` | 回测使用固定 ATR 倍数而非线上真实计划 | ✅ 已修复：`run_plan_backtest` 逐日调用 `analyze_bundle` 回放线上真实 `triple_barrier_plan`；`test_c04_c05_c10_backtest_replays_live_plan_with_bootstrap_ci` |
| C-05 | P1 | `scripts/evaluation.py:239-310` | 回测在样本内拟合元权重导致样本外泄漏 | ✅ 已修复：回测不学习任何参数，完全按事前固定规则逐日回放；同上 |
| C-06 | P1 | `scripts/data_gateway.py:26`、`scripts/analysis.py:181-215` | 取数长度随 `--days` 变化导致 v5 报告随 `--days` 漂移 | ✅ 已修复：取数固定 `FETCH_BARS=760`，v5 报告除展示数组外跨 `--days` 完全不变；`test_c06_v5_pipeline_is_days_invariant`、`test_c06_fetch_length_does_not_depend_on_days` |
| C-07 | P2 | `scripts/forecast.py:228-231` | 港股隔夜跳空在收盘到收盘波动率上重复乘系数放大 | ✅ 已修复：仅披露 `overnight_gap_ratio`，不再重复乘系数 |
| C-08 | P2 | `scripts/forecast.py:52-75`、`references/math-patterns.md:43` | 相似 Patch 允许 10 根重叠，与文档“无重叠”冲突 | ✅ 已修复：`max_cand_end = cur_end - horizon_bars - patch_len` 且候选间距 `>= patch_len`，严格零重叠；`test_causal_replay_invariance_no_lookahead` |
| C-09 | P2 | `scripts/risk.py:144-153`、`scripts/evaluation.py:239` | 规则加权自称概率 `confidence_score` 与假 Walk-Forward | ✅ 已修复：移除 `confidence_score` 与假 Walk-Forward，明确标注 `scoring_method = DETERMINISTIC_MULTI_FACTOR_RULE_V5`；`test_no_unimplemented_capability_claims` |
| C-10 | P2 | `scripts/evaluation.py:185-236,360-362` | 回测交易笔数少时无样本量警告与置信区间 | ✅ 已修复：输出 `sample_size_warning` 及胜率/均值收益的 Bootstrap 95% 置信区间（`win_rate_95ci_pct` / `mean_return_95ci_pct`）；`test_c04_c05_c10_backtest_replays_live_plan_with_bootstrap_ci` |
| C-11 | P2 | `scripts/evaluation.py:17-89` | 因果回放审计只用固定 0.04 阈值且未覆盖结构层、SMC 与预测 | ✅ 已修复：`run_causal_replay_audit` 使用生产自适应阈值并覆盖核平滑、结构层拐点、蜡烛形态、SMC 与保形预测；`test_causal_replay_invariance_no_lookahead` |
| C-12 | P2 | `scripts/analysis.py:86-176` | 历史回放快照只算 `regime_score`，未算完整 `rule_score` | ✅ 已修复：`_build_replay_snapshots` 调用完整结构与策略评分流水线输出 `rule_score` 与 `rating`；`test_g03_to_g07_rendering_invariants_and_low_price_etf` |
| C-13 | P2 | `scripts/patterns.py`、`references/math-patterns.md` | 宣称头肩、MACD 背离、CHoCH、WLS 通道等未实现能力 | ✅ 已修复：重命名为 `SWING_DOUBLE_*`、`KERNEL_SMOOTHED_*TREND`、`*_BOS`，清理全部不实宣称；`test_no_unimplemented_capability_claims` |
| C-14 | P3 | `scripts/indicators.py:93,372`、`scripts/evaluation.py:124` | 10 日动量误用 `closes[-10]`（9 日）、平盘首值 RSI=100、时间屏障多持 1 根 | ✅ 已修复：10 日动量改用 `closes[-11]`、平盘首值 RSI=50.0、`end_idx = entry_idx + horizon_bars - 1`；`test_c14_momentum_10d_and_flat_rsi_initial_value`、`test_triple_barrier_t_plus_1_and_same_bar_conservative_sl` |

### 3.4 渲染层（P1 2 项 + P2 4 项 + P3 2 项）

| ID | 级别 | 当前代码位置 | 初审问题 | 修复状态与验证测试 |
| --- | --- | --- | --- | --- |
| G-01 | P1 | `scripts/render_structure_chart.py:505-506` | PNG 写死红涨绿跌，美股违反 `us_green_up` | ✅ 已修复：PNG K 线、成交量柱、卡片与页脚文字全部跟随 `color_scheme`；`test_g01_and_r12_png_follows_market_and_cli_color_scheme` |
| G-02 | P1 | `scripts/structure.py`、`scripts/analyze_and_forecast.py:43-68` | PNG 渲染器重算结构层，`analysis.py` 反向依赖渲染器 | ✅ 已修复：抽出纯计算模块 `scripts/structure.py`，主流程只算一次并共享给 JSON 与 PNG；`test_g02_structure_is_computed_once_outside_the_renderer` |
| G-03 | P2 | `scripts/render_html.py:692-710` | HTML 楔形轨道固定从 `nBars - 60` 起画，斜率失真 | ✅ 已修复：按 `rail.start.idx + (nBars - structBars)` 计算真实起点与左边界截断值；`test_g03_to_g07_rendering_invariants_and_low_price_etf` |
| G-04 | P2 | `scripts/analysis.py:135-174`、`scripts/render_html.py:499-504` | `--days 60` 时历史回放退回显示全部 60 根 K 线，泄漏截面之后的未来 K 线 | ✅ 已修复：每个回放快照自带截至 `as_of` 的 60 根 `bars_window`，浏览器实测 5 个截面全部 `maxBarDay <= as_of`；同上 + 2.3 节浏览器实测 |
| G-05 | P2 | `scripts/structure.py:69-76,693-712`、`scripts/render_structure_chart.py:639-648,754` | 推演标签与刻度用 `:.0f` 取整，低价股/ETF 严重失真 | ✅ 已修复：`fmt_price` 按价格量级自动保留 2~3 位小数；在 `510300.SH`（约 4 元）真实录制行情上验证通过 |
| G-06 | P2 | `scripts/render_structure_chart.py:596-604,845-883` | PNG 缺少数据质量状态、风控官否决徽章与 15 日保形预测扇形 | ✅ 已修复：PNG 副标题显示数据质量与风控状态，右侧绘制 50%/80% 保形扇形与防遮挡 `【风控否决】` 横幅；2.3 节目视核验 + `test_g03_to_g07_rendering_invariants_and_low_price_etf` |
| G-07 | P3 | `scripts/render_html.py:617-626` | HTML 订单块固定多头绿、空头红，与 `cn_red_up` 相反 | ✅ 已修复：`bullObFill` / `bearObFill` 跟随 `isCnColor`；`test_g03_to_g07_rendering_invariants_and_low_price_etf` |
| G-08 | P3 | `scripts/render_html.py:269,367` | “筹码峰”表述违反术语边界 | ✅ 已修复：统一改为 `成交分布近似 POC`；`test_no_unimplemented_capability_claims` |

### 3.5 ~ 3.10 Skill 规范、跨 Agent 可移植性、文档契约与测试体系

| ID | 级别 | 当前位置 | 初审问题 | 修复状态与验证测试 |
| --- | --- | --- | --- | --- |
| P-01 | P1 | `AGENTS.md:88-93` | 缺少跨 Agent 技能发现目录的安装/软链命令说明 | ✅ 已修复：`AGENTS.md` 3.1 节提供 `~/.claude/skills` 与 `~/.agents/skills` 标准软链命令 |
| P-02 | P1 | `SKILL.md:5,50` | 无网环境静默产出合成数据，未声明 `compatibility` | ✅ 已修复：`SKILL.md` frontmatter 声明 `compatibility`，无网无本地真实文件时以 `[DATA_UNAVAILABLE]` 退出 |
| P-03 | P2 | `SKILL.md`、`AGENTS.md` | 写死 `/Users/admin-1234/...` 本机绝对路径 | ✅ 已修复：全部改为相对路径；`test_no_machine_specific_absolute_paths` |
| P-04 | P2 | `SKILL.md:73` | 强制要求内嵌图片，未考虑纯文本 CLI | ✅ 已修复：改为“客户端能显示图片时再内嵌 `preview.png`，否则只给路径” |
| P-05 | P2 | `SKILL.md:3` | `description` 只有中文，缺少英文触发词与不适用场景 | ✅ 已修复：改为中英双语且包含 `Use when` 与 `Not for`；`test_skill_frontmatter_follows_agent_skills_spec` |
| P-06 | P2 | `SKILL.md:48-86`、`AGENTS.md:10-20` | 运行期约束写在 `AGENTS.md`，随 skill 分发后丢失 | ✅ 已修复：运行期规则在 `SKILL.md` 与 `references/` 内完全自洽；`test_skill_md_keeps_runtime_contract_self_contained` |
| P-07 | P3 | `references/failure-modes.md:5` | 标题使用特定工具名 `image_generate` | ✅ 已修复：改为“为什么不能用任何图像生成工具画图” |
| P-08 | P3 | `scripts/render_structure_chart.py:128-132` | 存在回退明文 `http://qt.gtimg.cn` 的逻辑 | ✅ 已修复：移除明文 HTTP 回退，仅使用 HTTPS |
| AG-01 ~ AG-09 | P1/P2 | `AGENTS.md`、`SKILL.md` | 算图分离、大盘先行、复权、窗口不变性、数据真实性铁律与实现同步及文档分工 | ✅ 已修复：`AGENTS.md` 第 0~4 节重写并与代码实现完全对齐；`test_docs_contract.py` 全部测试通过 |
| AG-10 ~ AG-12 | P2/P3 | `scripts/strategies.py:11`、`tests/test_docs_contract.py:140-150` | 版本号不同步；`strategies.py` 写“四大状态”实际 5 类 | ✅ 已修复：`SKILL.md`、`pyproject.toml`、`analysis.schema.json`、`analysis.py` 四处版本号同步为 `5.1.0`（`test_versions_are_in_sync`）；`strategies.py` 改为“五类市场状态” |
| T-01 ~ T-06 | P1/P2 | `tests/test_quant_skill.py`、`tests/test_data_sources.py`、`tests/test_indicator_golden.py` | 测试使用合成数据、fixture 冒充真实源、断言过弱、缺 TA-Lib 与 Supertrend 金标准及网络异常测试 | ✅ 已修复：25 份真实录制数据全覆盖，移除全部合成数据，85 项 pytest + 420 次跨窗口不变性全部基于真实行情通过 |
| H-01 ~ H-03 | P3 | `.gitignore`、`artifacts/` | 缺 `.gitignore`、`artifacts/` 残留修复前旧产物 | ✅ 已修复：`.gitignore` 已配置，`artifacts/{cn,hk,us}` 已用修复后真实行情重新生成 |

---

## 4. 可复用的 Review 勾选清单（当前收口状态）

以后每次修改 skill 或脚本之后，按这份清单逐项复核：

**A. Skill 规范**
- [x] frontmatter 只有 `name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools` 规范字段（`test_skill_frontmatter_follows_agent_skills_spec`）
- [x] `name` 与目录名一致；`description` 不超过 1024 字符，写清做什么、何时用、何时不用，并含中英文关键词
- [x] `SKILL.md` 正文少于 500 行，引用只有一层，无本机绝对路径（`test_no_machine_specific_absolute_paths`）
- [x] 有输出契约（回复顺序、必填字段、免责声明）与运行后必检（`test_skill_body_has_output_contract_and_failure_handling`）
- [x] 有失败处理（未知代码、断网、数据不足、数据非实时）
- [x] 四处版本号（`SKILL.md`、`pyproject.toml`、`assets/analysis.schema.json`、`analysis.py`）同步为 `5.1.0`（`test_versions_are_in_sync`）

**B. 跨 Agent 可移植**
- [x] `AGENTS.md` 提供标准软链安装命令，支持 `.claude/skills/` 与 `~/.agents/skills/`
- [x] 在无网、无本地真实文件环境里明确以 `[DATA_UNAVAILABLE]` 非零退出，绝不静默降级
- [x] 图片内嵌写成条件式，兼容纯文本 CLI
- [x] 运行期规则在 `SKILL.md` 与 `references/` 中自洽，不依赖 `AGENTS.md`

**C. 数据真实性**
- [x] 代码与测试中彻底移除合成行情生成器（`generate_deterministic_series`）与合成数据目录（`synthetic_daily`），100% 使用真实行情（`test_no_synthetic_generators_or_mock_bars_in_code_and_tests`）
- [x] `quality.status` 由来源枚举决定，标的和基准分别判定；标的本身为默认基准（如 `SPY.US`）时不误判为缺基准
- [x] PNG 和 HTML 醒目显示数据源、数据质量与风控状态；数据非 `OK` 时带红色告警横幅与水印
- [x] 输出里无写死外部数据（VIX 实时拉取 Yahoo `^VIX` 或置 `null`）
- [x] 最后一根 K 线按交易所时区与收盘时间判定是否收盘，未收盘 K 线拆为 `intraday` 并在 `quality.notes` 披露
- [x] `tests/fixtures/recorded/MANIFEST.json` 完整记录 47 份真实录制文件的来源、起止日期与 `sha256`（含腾讯行情快照 19 份）
- [x] 数据源缺少最近已收盘交易日时只在 `quality.notes` 披露，不用其他来源补数（`test_missing_closed_session_is_disclosed_not_filled`）

**D. 市场制度**
- [x] 每条规则在 `references/{cn,hk,us}-market.md` 写明生效日期与权威来源
- [x] 涨跌停按板块、ST（`2026-07-06` 主板 `±10%`）和新股无涨跌停期区分，并通过 `--st`/`--name`/`--ipo-no-limit` 全链路透传
- [x] 交易日历核实覆盖 `2021-01-01 ~ 2026-12-31`（CN）与 `2027-12-31`（HK/US），超出范围在 `calendar_coverage` 与 `quality.notes` 标注估算
- [x] 港股价位表包含 500 以上各档与 Phase 1/2 下调；港股印花税买卖双边各 10 bps（ETF 豁免）
- [x] A 股科创板 200 股起 + 1 股递增、北交所 100 股起 + 1 股递增；A 股 ETF `0.001` 元与美股 `< $1` `0.0001` 美元最小价位生效
- [x] 新浪 A 股不复权价格与 Yahoo 拆股后股息单位对齐，前复权（`total_return` / `split`）严格按 `as_of` 因果计算

**E. 计算与统计**
- [x] 指标固定在 420 根历史上预热，EMA200 不足时显式为 `None` / `N/A`，绝不用 EMA60 冒充
- [x] 保形预测 `calibration_audit` 针对经涨跌停截断与价位表取整后的实际输出区间做滚动样本外实测，并同时披露 `q50_mae_pct` 与 `random_walk_mae_pct`
- [x] 回测逐日回放线上真实计划（`LIVE_PLAN_REPLAY_V6`），不学习任何参数；胜率给 Wilson 95% 区间、均值收益给 Bootstrap 95% 区间（公式见 `references/math-patterns.md` 第 6 节）
- [x] 所有形态与因子命名同算法严格一致，无未实现能力宣称（`test_no_unimplemented_capability_claims`）

**F. 因果与不变性**
- [x] 核平滑、拐点、蜡烛形态、SMC、相似 Patch（$t_c + H \le t_{\text{now}} - L_{\text{patch}}$）与保形预测均无前视泄漏（`run_causal_replay_audit` 守护）
- [x] `--days` 不变性同时覆盖 v4.1 结构层（真实录制数据 420 次运行 0 失败）与 v5 `run_pipeline`（`test_c06_v5_pipeline_is_days_invariant`）

**G. 渲染**
- [x] PNG 和 HTML 只读取计算层输出，不重新计算结构点位
- [x] 配色跟随 `meta.color_scheme`（含 HTML 订单块与 PNG K 线/文字）
- [x] `--days 60` 回放使用快照自带的 `bars_window`，不显示截面之后的 K 线；HTML 楔形轨道按真实支点下标绘制
- [x] 浏览器实测控制台零报错，7 个图层开关、RS 视图切换、时间回放滑块均正常

**H. 测试与文档**
- [x] 门禁命令包含全量 `pytest`（117 项，含 TA-Lib 与 Supertrend 金标准、真实数据回放、网络异常、文档契约）与真实录制数据跨窗口不变性校验
- [x] `.gitignore` 已忽略生成物与虚拟环境，`artifacts/` 已更新为最新真实行情产物

**I. 输出与使用文档**
- [x] 每次运行生成 `*_report.md`，数字全部取自 `*_result.json`，风控否决置于结论之前（`test_report_md_*`）
- [x] 参数错误以 `[INVALID_ARGUMENT]`、行情不可用以 `[DATA_UNAVAILABLE]` 非零退出，无 traceback（`test_cli_errors_are_labelled_without_traceback`）
- [x] 仓库根目录 `README.md` 说明安装、注册、验证与使用；参数表、错误前缀、产物文件名与链接由 `test_readme_*` 守护

---

## 5. 第二轮复查（2026-10-16）：输出准确性与使用文档

### 5.1 新发现与修复台账

| ID | 级别 | 位置 | 问题 | 修复状态与验证测试 |
| --- | --- | --- | --- | --- |
| S-01 | P1 | `scripts/market_rules.py` `yahoo_symbol_for`、`scripts/data_gateway.py` 各取数 URL | 美股代码未校验即拼进 Yahoo 请求 URL，可注入查询参数或路径（后台安全审查发现） | ✅ 美股代码按 `US_TICKER_PATTERN` 校验，所有取数 URL 的代码经 `_url_token` 百分号编码；`test_us_ticker_validation_and_share_classes`、`test_url_tokens_are_percent_encoded` |
| S-02 | P2 | `scripts/render_structure_chart.py` | 明文 `http://` 回退（后台安全审查发现） | ✅ 已由提交 `1eb657f` 移除（即 P-08），本轮复核确认 |
| M-01 | P1 | `scripts/data_gateway.py` `_enrich_instrument` | 未在内置表登记的代码，报告标题只显示代码 | ✅ 从腾讯行情快照取证券简称（`--name` 优先）；`test_vendor_name_fills_unregistered_symbol` |
| M-02 | P1 | `scripts/market_rules.py` `get_market_microstructure_rules` | 港股每手股数固定按 100 股，小米（200）、汇丰（400）等仓位股数错误 | ✅ 每手股数取自行情快照，取不到时按 100 股假设并在 `lot_size_source` 与 `quality.notes` 披露；`test_hk_board_lot_comes_from_vendor_quote`、`test_hk_board_lot_fallback_is_disclosed` |
| M-03 | P1 | `scripts/market_rules.py` `is_st_instrument` | ST 状态只能手动传 `--st` | ✅ 按 A 股证券简称前缀自动识别，港美股不误判；`test_st_detection_only_by_a_share_name_prefix`、`test_st_flag_from_vendor_name_drives_historical_limits` |
| M-04 | P2 | `scripts/market_rules.py` `yahoo_symbol_for` | `BRK.B.US` 等带类别后缀的美股无法解析 | ✅ 映射为 Yahoo 的 `BRK-B`；`test_us_ticker_validation_and_share_classes` |
| O-01 | P1 | `scripts/render_report_md.py`（新增） | 没有确定性的中文报告，agent 需自行从 JSON 拼数字，容易改错或漏掉否决 | ✅ 每次运行生成 `*_report.md`，按回复契约排序，数字全部取自 JSON；`test_report_md_numbers_match_json`、`test_report_md_puts_veto_before_conclusions`、`test_report_md_hides_all_prices_when_data_unavailable` |
| O-02 | P1 | `scripts/risk.py`、`scripts/analysis.py` | 本地文件缺少基准时仍可能给出确认做多，报告还误写“标的本身即市场基准” | ✅ 新增 `benchmark_missing`，缺基准即风控否决；`test_missing_benchmark_blocks_long_confirmation`、`test_default_benchmark_itself_needs_no_external_benchmark` |
| O-03 | P2 | `scripts/analyze_and_forecast.py` | 非法代码直接抛 traceback；`--days` 越界被标成 `[DATA_UNAVAILABLE]`；`--help` 因未转义的 `%` 崩溃 | ✅ 参数错误统一为 `[INVALID_ARGUMENT]`，`--help` 可用；`test_cli_errors_are_labelled_without_traceback`、`test_cli_help_renders` |
| O-04 | P2 | `scripts/market_rules.py` `price_decimals` | 港股 0.02 价位被判为 3 位小数 | ✅ 价位先 `normalize()` 再取位数；`test_report_md_uses_market_tick_precision` |
| O-05 | P1 | `scripts/market_rules.py`、`scripts/data_gateway.py`、`scripts/analysis.py` | 收盘后数据源尚未入库当日日线时没有任何提示，“未来第 1 日”其实已经收盘（实测北京时间 2026-10-16 15:19，新浪日线仍只到 10-15） | ✅ 新增 `latest_closed_session`，缺最近已收盘交易日时在 `quality.notes` 披露，不补数；`test_latest_closed_session_follows_exchange_clock_and_calendar`、`test_missing_closed_session_is_disclosed_not_filled` |
| O-06 | P2 | `scripts/evaluation.py` `_summarize_returns` | 胜率 Bootstrap 区间在小样本全亏时退化为 0%~0%，呈现虚假的确定性 | ✅ 胜率改用 Wilson 区间（3 笔全亏为 0%~56.15%），均值收益 Bootstrap 结果不变；`test_win_rate_interval_does_not_collapse_on_small_samples` |
| O-07 | P3 | `scripts/render_report_md.py` | 交收制度显示原始枚举（如 `T+0_TRADING_T+2_CLEARING`） | ✅ 报告改为中文说明，JSON 字段不变；`test_report_md_explains_settlement_codes_in_chinese` |
| O-08 | P3 | `scripts/render_report_md.py` | 零成交行显示 `0.0%` | ✅ 显示 `—`；`test_report_md_zero_trade_rows_show_dash` |
| D-01 | P2 | `README.md`（新增） | 缺少面向使用者的安装与使用说明 | ✅ 覆盖安装、注册到各 agent、验证、对话用法、CLI 参数、输出、读结果、故障排查与已知限制；`test_readme_commands_and_links_resolve`、`test_readme_cli_parameters_and_outputs_match_code` |
| D-02 | P3 | `scripts/render_structure_chart.py` `FONT_CANDIDATES` | 缺 Linux Noto CJK 与 Windows 字体路径 | ✅ 已补充候选路径（Windows 全流程未实测） |

### 5.2 本轮验证证据

- 门禁：`pytest` **117 passed in 148.50s**；`check_days_invariance.py --step 40` 输出 `runs=420 failing=0 days=[60, 120, 200, 250, 300, 500]`。
- 联网实跑（北京时间 2026-10-16 15:19 前后，4 个标的全部退出码 0，单次 4~7 秒）：
  - `600519.SH`（含 `--backtest --backtest-step 5`）：报告注明“数据只到 2026-10-15”；胜率区间显示为 `0.0%（0.0% ~ 56.1%）`。
  - `600289.SH`：简称“ST信通”自动识别为 ST，主板 ST 涨跌幅 `±10%`。
  - `01810.HK`：每手 200 股（腾讯快照），30 万港元、1% 风险对应 1400 股；10-16 未收盘 K 线已排除并注明。
  - `BRK.B.US`：解析为 Yahoo `BRK-B`，简称“伯克希尔B”。
- 浏览器（内置浏览器，`600519.SH` 与 `BRK.B.US` 看板）：7 个图层全关再全开后画布恢复一致；相对强弱视图可切换；回放滑块 6 个截面均正常；控制台 0 条消息。
- PNG 目视：A 股、港股红涨绿跌，美股绿涨红跌，图例与页脚文字随市场切换。

### 5.3 仍可继续改进（未在本轮修改）

1. **结构层使用盘中价**：`structure._abc_one` 用盘中价判断 ABC 结构是否失效，C 浪进度也包含盘中高低点（已标注“盘中”）。这与铁律 0“未收盘 K 线不得参与形态计算”的字面要求有张力，建议盘中价只用于标注、不参与情景筛选。
   - [x] **已处理（2026-10-16）**：`_abc_one` 的结构失效判断、测算位 `state`、C 浪进度（`c_progress`）只用已收盘K线；盘中刺破/触及改写入测算位的 `intraday` 字段，盘中越过 A 浪起点/B 浪终点或超出 C 浪最远位置写入情景的 `intraday_note`（均注明“未收盘，不作确认”）。同时 `compute_structure_layer` 改为在最新已收盘日求轨道值并按收盘价分类形态（`compute_structure`），缺口回补只看已收盘K线（`find_unfilled_gaps`），因此三条推演的点位不再随盘中价变化；盘中价只用于 `status_text` 提示、折线起点与“盘中已站上/跌破，待收盘确认”注释。PNG 盘中态把卡片与文字里的“今日上轨”改标为“MM-DD收盘上轨”。回归测试 `test_g08_live_bar_does_not_change_closed_structure`：在 `sh000300`/`sh600519`/`sz399006` 的真实录制行情上逐截面把下一根真实K线标为未收盘传入，情景、C 浪进度、轨道、阶梯与三路径点位与不传入时完全一致。
2. **PNG 标签精度与报告不一致**：`structure.fmt_price` 对 ≥100 的价格取整、<10 的价格保留 3 位，例如 `BRK.B.US` 图上“突破 534”而报告为 533.55，`600289.SH` 图上“突破 5.123”而报告为 5.12。`test_g03_*` 把取整固定为设计，如需按价位表精度显示，需要同时改测试。
   - ✅ 已处理（2026-10-16）：`fmt_price(value, decimals)` 改为按 `market_rules.price_decimals` 的价位表小数位输出，由 `analyze_and_forecast.py` / `analysis.py` 按市场与品种传入 `compute_structure_layer(price_decimals=...)`，v4.1 CLI 无市场信息时按 A 股股票口径 2 位；`step_labels` 与触发/失效文案、PNG 卡片、ABC 倍位、右侧标签、拐点徽标、坐标轴及摘要文本统一到同一精度。`test_g03_*` 改为断言 1312.97 → `1312.97`、ETF 3 位小数，并核对 `突破` 标签与 `report.md` 同值。同时修复两处由更长标签暴露的版面问题：卡片首行小字号仍放不下时按 ` | ` 分段收起；最新K线徽标避开推演步骤标签。联网实跑 `BRK.B.US` 图上“突破 533.55”、`600289.SH`“突破 5.11”、`510300.SH`“突破 4.244”，均与报告一致。
3. **区间校准“已达标”的样本量**：原评估样本 107 个、约等于 7 个独立样本即达标，抗扰度弱。
   - [x] **已处理（2026-10-16）**：下限取 20 个有效独立样本。`forecast.py` 增加 `effective_independent_samples >= MIN_EFFECTIVE_INDEPENDENT_SAMPLES (20)` 并在 `calibration_audit` 输出；审计期与展示校准窗口解耦（审计分数覆盖全历史，展示保持最近 160 根不变），评估样本由 n-2H-99 确定（760 根历史在 H=15 时达到 631 个样本、约 42 个独立样本，零前视，展示区间宽度不受影响）；Yahoo 取数对齐 `FETCH_BARS=760`。数学定义见 `references/math-patterns.md` 第 5 节。测试：`test_c02_c03_*`、`test_c11_*`、`test_c12_*`。
4. **ST 历史状态**：ST 按当前简称识别并沿用到全部历史，可接入 ST 变更历史。
5. **港股 ETF 识别**：✅ 已处理（2026-10-18）：`market_rules.py` 新增 `is_hk_etp_code`，自动识别港交所 ETF/ETP 代码段（`02800~02849`、`03000~03199`、`07200~07599`、`09000~09199`、`09800~09849`），在 `default_asset_type`、`resolve_symbol.py` 与 `get_market_microstructure_rules` 中自动免除港股双边印花税（`0 bps`）；测试 `test_hk_etp_auto_detection_and_stamp_duty_exemption`。
6. **外部数据缺口**：财报日无数据源；北交所无除权除息事件；A 股 2027 年休市表待公布后补录。
7. **数据源稳定性**：新浪、Yahoo、腾讯均为非官方网页接口，可评估付费或官方备用源（同样必须是真实行情）。
8. **HTML 看板制度栏**：✅ 已处理（2026-10-18）：`render_html.py` 新增交收制度枚举到中文说明的映射（如 `T+0_TRADING_T+2_CLEARING` → `T+0 回转交易，T+2 交收`），与 Markdown 报告口径一致。
9. **Windows**：全流程未实测。

## 6. 第三轮复查（2026-10-17 ~ 2026-10-18）：推演失真修正、一键安装与文档（5.2.0）

方案与证据见仓库根目录 `OPTIMIZATION_PLAN.md`。

| ID | 级别 | 位置 | 问题 | 修复状态与验证测试 |
| --- | --- | --- | --- | --- |
| O3-01 | P0 | `structure._fit_rail` | 轨道固定取凸包最后一条边，几何上偏向收敛，85.5% 截面判为收敛形态 | ✅ 最近 4 个拐点候选打分 + Theil-Sen 交叉校验；`test_o3_01_rail_fit_has_no_convergence_bias` |
| O3-02 | P0 | `structure.compute_projections` | 三条推演折点固定第 8/17/28 根、超出预测期；58.7% 截面的触发位距现价不足 0.5 ATR | ✅ 扩散尺度折点 + 0.5 ATR 最小距离 + `contested_levels`；`test_o3_02_*`、`test_g08_*` |
| O3-03 | P0 | `structure.wave_counts` | ABC 只有一个锚点、一种数法 | ✅ 主计数 + 最多 2 个备选，各带否定条件；`test_o3_03_*` |
| O3-04 | P1 | `risk.py` | 只有做多计划；否决原因无编码 | ✅ `holder_defense_plan`、`short_plan`、`veto_codes`；`test_o3_04_*` |
| O3-05 | P1 | `evaluation.py` | 回测不检验规则分与动作标签的方向信息 | ✅ `rule_score_ic`、分层、动作前瞻收益、否决统计；`test_o3_05_*` |
| O3-10 ~ O3-18 | P1/P2 | `install.sh`、`install.ps1`、`.claude-plugin/`、`scripts/doctor.py`、`scripts/resolve_symbol.py`、README、`references/*`、`examples/`、CHANGELOG、CI | 无一键安装、不支持名称转代码、文档偏薄 | ✅ 见 `CHANGELOG.md` 5.2.0；`test_tools.py`、`test_docs_contract.py` |
| O3-20 | P1 | `scripts/scenarios.py`、`references/scenario-playbook.md` | 推演固定为上攻/震荡/下探三条模板；无推动 5 浪识别 | ✅ 8 类标准情景库（代码与文档表格逐字对应）、推动 5 浪硬规则识别、按截面输出 2~5 个适用情景、触发距现价 <0.5 ATR 归为「已在进行中」、时间窗取历史到达用时分位数并截断在预测期内；`test_o3_20_*` |
| O3-21 | P1 | `scripts/base_rates.py` | 缺少历史经验基准率（L2 层） | ✅ 标的自身历史逐点无前视统计先到目标/先到失效/到期频率、95% Wilson 区间、到达用时分位数与 MAE/MFE；`n < 30` 时向市场基准指数同情景频率做 Beta-Binomial 收缩并标为低样本；`test_o3_21_*` |
| O3-22 | P1 | `scripts/evaluation.py` | 历史频率未做校准检验 | ✅ 回测新增 `scenario_frequency_brier`（样本外 Brier、全样本基准率 climatology Brier、技能分 `BSS` 与 5 档可靠性分桶表），仅当 `BSS > 0` 时输出「历史频率具有区分度」；`test_o3_22_*` |
| O3-23 | P2 | `scripts/render_structure_chart.py`、`scripts/render_html.py`、`scripts/render_report_md.py` | 图表与报告未体现历史条件频率与低样本提示 | ✅ PNG 与 HTML 按历史频率区分线宽与透明度，图例与标签写出频率与 `n`，低样本（`n < 30`）用虚线；报告第 3 节与第 7 节输出多情景频率表与 Brier 检验；`test_o3_23_and_o3_24_*` |
| O3-24 | P2 | `scripts/forecast.py` | 保形区间无情景偏态展示层 | ✅ 新增 `scenario_conditional_fan`（标注 `display_only=True, calibrated=False`，图例标明「情景混合（非校准区间）」），默认鞅中心保形区间保持不变；`test_o3_23_and_o3_24_*` |
| O3-30 | P1 | `scripts/judgment.py`、`scripts/analyze_and_forecast.py` | 缺少面向 LLM 研判的结构化证据包与价格白名单 | ✅ 生成 `*_judgment_pack.json`（含 `E1…En` 精确 `json_path` 溯源、冲突信号 `C1…Cm`、L2 历史频率与归一化基准概率、合法价格白名单）；`test_o3_30_to_o3_34_judgment_layer_and_scoring` |
| O3-31 | P1 | `references/llm-judgment-protocol.md`、`SKILL.md` | 缺少受约束的 LLM 研判协议 | ✅ 新增研判协议文档并在 `SKILL.md` 接入（要求先正反列证据、调整幅度受样本量约束、标注「主观研判」并给出证伪条件）；`test_docs_contract.py` |
| O3-32 | P1 | `scripts/validate_judgment.py` | 缺少对 LLM 研判 JSON 的硬校验器与日志记录 | ✅ 校验 JSON 结构、互斥情景概率之和=100%、调整幅度上限（`n >= 30` 最多 `±15%`，`n < 30` 最多 `±10%`）、证据编号存在性、价格白名单与「主观研判」标注，通过后追加至 `judgment_log.jsonl`；`test_o3_30_to_o3_34_judgment_layer_and_scoring` |
| O3-33 | P2 | `scripts/render_report_md.py` | 报告缺少受约束主观研判独立章节 | ✅ 新增「LLM 情景研判（主观判断，已记录待检验）」章节，与确定性指标章节严格隔离，展示调整前后概率、证据编号与证伪条件；`test_o3_30_to_o3_34_judgment_layer_and_scoring` |
| O3-34 | P1 | `scripts/score_judgments.py` | 缺少对已到期主观研判的后验校准打分 | ✅ 用真实行情回放计算 Brier 分数、对数损失、5 档可靠性分桶与相对 L2 基准频率的技能分 `BSS`（`n >= 20` 且 `BSS <= 0` 时建议收紧调整上限）；`test_o3_30_to_o3_34_judgment_layer_and_scoring` |
| O3-35 | P2 | `scripts/lint_response.py` | 缺少对 Agent 最终自然语言回复的契约校验器 | ✅ 校验 `UNAVAILABLE` 零价格、`PARTIAL` 告警置顶、风控否决置顶、禁用术语（禁止将规则分称为胜率/上涨概率）、价格白名单与免责声明；`test_lint_response_catches_contract_violations_and_passes_clean_report` |
| O3-36 | P1 | `scripts/risk.py`、`scripts/evaluation.py` | 三重屏障 `tp1 = entry_mid + 1.5 * unit_risk` 恒等构造使 `RR_INSUFFICIENT` 永远不触发，且 `WAIT_CONFIRMATION` 入场区横跨整个 120 日通道导致 `TP1` 远达 5~6 ATR；默认 `decision_window=260` 导致 15 日期限下独立样本恒为 17 (<20) | ✅ 收紧入场区、锚定结构阻力与 15 日有效摆动空间、`decision_window` 升至 300；17 个真实标的回测总胜率 `38.1% -> 45.0%`，平均累计收益 `-2.34% -> +3.10%`；`test_triple_barrier_entry_zone_anchored_and_rr_insufficient_veto` |
| O3-37 | P2 | `install.sh`、`install.ps1`、`scripts/doctor.py`、`SKILL.md` | 缺少 Hermes Agent（`~/.hermes/skills`）注册与跨目录 `--directory` 调用说明 | ✅ 新增 `hermes` 自动探测与软链/复制安装支持，`SKILL.md` 明确跨工作目录 `--directory` 调用规范；`test_install_sh_syntax_and_help` |

- 未验证：`install.ps1` 未在 Windows 实测；安装后 Claude Code / Codex 新会话能否识别技能未实测。
- 阶段 2 实测结果：在茅台（`600519.SH`）录制行情 2025-08 ~ 2026-09 回放（步长 5 日，共 151 次情景事件）中，历史条件频率样本外 Brier 分数 = 0.2340，优于全样本基准率（climatology = 38.4%）的 Brier 分数 0.2366（技能分 `BSS = +0.0108 > 0`，历史频率具有区分度）。

