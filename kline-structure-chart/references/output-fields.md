# 输出字段字典：`*_result.json` 与 `*_backtest.json`

用户追问某个字段时查本文件。所有数字只能取自本次运行的 JSON，本文件只解释含义，**不要引用本文件里的示例值**。

约定：
- "价格"单位为标的交易货币（`meta.currency`），已按 `meta.adjustment` 复权，并按价位表取整。
- "可空"一栏为"是"的字段可能为 `null`，表示该项没有数据，**不得自行补数**。
- `assets/analysis.schema.json` 是顶层结构的契约，`contracts.validate_report` 以它为准并拦截 `NaN` / `Infinity`。顶层必有字段：`schema_version`、`meta`、`request`、`market_benchmark`、`mathematical_indicators`、`algebraic_patterns_detected`、`smc_structures`、`volume_profile`、`market_regime`、`strategy_signals`、`multi_timeframe_score`、`meta_labeling_decision`、`triple_barrier_plan`、`rule_scenarios`、`future_projection_15d`、`debate_and_veto`、`quality`、`reproducibility`。

## 1. 版本、标的与请求

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `schema_version` | 报告结构版本，与技能版本一致 | 如 `5.2.0` | 否 | 一般不需要提 |
| `meta.symbol` / `meta.name` | 标的代码与简称 | — | 否 | "贵州茅台（600519.SH）" |
| `meta.market` / `meta.asset_type` | 市场（`CN`/`HK`/`US`）与品种（`index`/`equity`/`etf`） | 枚举 | 否 | — |
| `meta.is_st` / `meta.is_ipo_no_limit` | A 股风险警示标识；新股无涨跌停期 | 布尔 | 否 | ST 时提醒涨跌幅与风险警示 |
| `meta.board_lot` | 港股每手股数 | 股 | 是（非港股为空） | "每手 X 股" |
| `meta.currency` | 交易货币 | `CNY`/`HKD`/`USD` | 否 | — |
| `meta.as_of` | 最后一根**已收盘** K 线的日期 | `YYYY-MM-DD` | 否 | "数据截至 X" |
| `meta.adjustment` | 复权口径：`total_return` 前复权含分红、`split` 只复权拆合股、`raw` 不复权 | 枚举 | 否 | "前复权（含分红送转）" |
| `meta.color_scheme` | 配色 `cn_red_up` 红涨绿跌 / `us_green_up` 绿涨红跌 | 枚举 | 否 | — |
| `meta.price_decimals` | 价格显示小数位（随价位表） | 整数 | 否 | — |
| `meta.data_source` | 标的行情来源，如 `SINA_LIVE_DAILY`、`YAHOO_DAILY(0700.HK)` | 字符串 | 否 | "数据来自新浪日线" |
| `request.*` | 本次运行参数：`display_bars`（`--days`）、`structure_bars`（结构窗口，固定 120）、`horizon_bars`、`account_equity`、`max_risk_pct`、`as_of` 等 | — | `account_equity` 可空 | 不需要逐项转述 |

## 2. 大盘基准 `market_benchmark`

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `index_symbol` / `index_name` | 对比基准（A 股沪深300、港股恒生指数、美股 SPY） | — | 否 | — |
| `regime` / `regime_label_cn` | 基准的市场状态，五类之一（见第 5 节） | 枚举 | 否 | 用中文标签 |
| `regime_score` | 基准的状态分，由 ADX、EMA、DI 等规则加减得出 | 5~95，低于 35 风控否决做多 | 否 | "状态分 X / 100"，**不是概率** |
| `relative_strength_vs_index` | 近 20 日标的归一化走势与基准之比 | 比值，>1 跑赢、<1 跑输 | 否 | "近 20 日跑赢/跑输基准" |
| `rs_series[]` | 显示窗口内逐日归一化走势（`target_norm`、`benchmark_norm`、`rs_line`，起点为 100） | — | 否 | 供 HTML 画图 |
| `source` / `benchmark_missing` | 基准数据来源；本地文件输入缺基准时为 `true` | — | 否 | 缺基准时说明质量为 `PARTIAL` |

## 3. 技术指标 `mathematical_indicators`

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `trend.sma20/60/120/250`、`ema20/50/60/200` | 简单 / 指数均线 | 价格 | 历史不足时可空 | "MA20 X" |
| `trend.vwap20` | 近 20 日成交量加权均价 | 价格 | 否 | — |
| `trend.supertrend_line` / `supertrend_dir` | Supertrend 线与方向（1 多、-1 空） | 价格 / ±1 | 否 | — |
| `trend.adx14`、`plus_di14`、`minus_di14` | 趋势强度与多空方向指标 | 0~100 | 否 | ADX 高于 23 视为有趋势 |
| `momentum.rsi14` | 相对强弱指数 | 0~100 | 否 | 不单独作为买卖依据 |
| `momentum.macd_dif/dea/hist` | MACD 快线、慢线、柱 | 价格差 | 否 | — |
| `momentum.stoch_k/d` | 随机指标 | 0~100 | 否 | — |
| `momentum.rvol20` | 当日成交量 / 近 20 日均量 | 倍数 | 否 | "量能为 20 日均量的 X 倍" |
| `volatility.atr14`、`atr20_pct` | 平均真实波幅；占价格百分比 | 价格 / % | 否 | — |
| `volatility.bb_upper/mid/lower`、`bbw`、`bbw_percentile_120` | 布林带、带宽与近 120 日带宽分位 | 价格 / 比值 / 0~100 | 否 | 分位低表示收敛 |
| `qlib_factors.*` | 量价因子：实体占比、上下影线比、量价相关、ATR 归一化动量、20/60 日年化波动及其比值 | 比值 | 否 | 只在用户追问时解释 |
| `structure_levels.poc_price/vah_price/val_price` | Volume Profile 的成交最密集价位与 70% 价值区上下沿 | 价格 | 否 | "成交密集区"，**不是真实持仓筹码** |
| `structure_levels.support_zone` / `resistance_zone` | 最近支撑区、压力区 `[下沿, 上沿]` | 价格 | 否 | — |
| `structure_levels.key_levels.supports[]` / `resistances[]` | 支撑压力阶梯，每项 `price` 与来源 `sources`（前高、前低、均线、斐波那契、轨道等） | 价格 | 否 | 按由近到远转述并带来源 |
| `structure_levels.fib_levels` | 近 120 日高低点之间的回撤 / 反弹位 | 价格 | 是 | — |
| `structure_levels.recent_gaps[]` | 近期未回补跳空缺口 | 价格区间 | 否（可为空数组） | — |
| `structure_levels.abc_scenario` | ABC 浪型：方向 `direction`、A/B 端点、`b_retrace`（B 回撤 A 的比例）、`c_progress`（C 浪进度与所处阶段 `phase`）、测算位 `levels` | 价格 / 比例 | 是（无 ABC 时） | 只按 `phase` 原文描述，不自行数浪 |
| `structure_levels.wave_counts[]` | 浪型计数列表：第一项 `role = "主计数"`（与 `abc_scenario` 相同），其后最多 2 个 `role = "备选计数"`（A 浪起点不同、终点或测算位也不同的另一种数法）；每项另有 `a_range_atr`（A 浪幅度，ATR 倍数）与 `count_invalidation`（`price` 为 B 浪终点，`text` 为"日线收盘越过即此计数失效"） | 价格 / 倍数 | 否（无 ABC 时为空数组） | 先说主计数，再说备选数法及各自的否定条件；**备选不代表可能性大小** |
| `structure_levels.wedge_structure` | 轨道形态：`pattern`（如"上升通道""下降楔形"，轨道与拐点稳健回归方向相反时为"形态不明确（…）"）、`status`（`above`/`inside`/`below`）、`status_text`、上下轨今日值 `upper_today`/`lower_today`、顶点距今根数 `apex_bars_ahead`、`fit_check`（上/下轨斜率与 Theil-Sen 稳健斜率，%/根；`consistent`；走平阈值 `flat_pct_per_bar`） | 价格 / 根 | 是（拟合失败时 `fitted=false`） | 按 `status_text` 原文转述；"形态不明确"时不要套用任何教科书形态 |

## 4. 形态、SMC 与成交分布

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `algebraic_patterns_detected[]` | 识别到的蜡烛、波段与背离形态：`pattern`、`name_cn`、`direction`、`observed_at`（形态日）、`confirmed_at`（确认日）、`volume_confirmed`、`formula_verified`（代入数值的判定公式） | — | 否（可为空数组） | 只引用 `name_cn` 与 `formula_verified` |
| `smc_structures.fvgs[]` / `order_blocks[]` / `structure_breaks[]` | 公允价值缺口、订单块、结构破位；`status` 为 `ACTIVE` / `MITIGATED` 等 | 价格区间 | 否（可为空数组） | "算法结构区"，不说成机构真实挂单 |
| `volume_profile.*` | 24 个价格桶的成交分布 `bins[]`、`poc_price`、`vah_price`、`val_price`、`value_area_coverage`（0.7） | 价格 / 比例 | 否 | 同上，是近似成交分布 |

## 5. 市场状态、策略与规则分

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `market_regime.regime` / `label_cn` | 标的自身状态：`TRENDING_BULL` 多头趋势、`TRENDING_BEAR` 空头趋势、`SQUEEZE_CONSOLIDATION` 收敛蓄势、`MEAN_REVERTING_RANGE` 区间震荡、`HIGH_VOLATILITY_CHAOS` 高波失序 | 枚举 | 否 | 用中文标签，只描述当下，不代表方向 |
| `market_regime.regime_score` / `evidence` / `routed_strategy_family` | 状态分、判定证据（ADX、带宽分位、波动比、VIX 等）、路由到的策略族 | 5~95 | `evidence.vix` 可空 | 状态分不是概率 |
| `strategy_signals[]` | 五个规则策略的状态：`strategy_id`、`name_cn`、`direction`、`state`（如 `LONG_ACTIVE`）、`formula_verified` | 枚举 | 否 | "某策略处于多头激活" |
| `multi_timeframe_score.rule_score` | 四组各 25 分的确定性规则加权分 | 0~100 | 否 | "规则分 X / 100，不是胜率或概率" |
| `multi_timeframe_score.rating` / `rating_cn` | `BULLISH_RESONANCE` 多周期多头共振 / `BEARISH_PRESSURE` 多周期空头承压 / `NEUTRAL_WATCH` 多空分歧 | 枚举 | 否 | 用中文标签 |
| `multi_timeframe_score.components` | 四组分项：`macro_trend_25`、`structure_pattern_25`、`strategy_trigger_25`、`volume_benchmark_25` | 0~25 | 否 | 追问时解释 |
| `meta_labeling_decision.setup_type` / `setup_label_cn` | 计划类型：右侧突破、左侧回踩、等待确认等 | 枚举 | 否 | — |
| `meta_labeling_decision.action` / `action_cn` | 动作标签：`LONG_CONFIRMED` 确认做多、`WATCH_LONG_SETUP` 条件观察、`NEUTRAL_HOLD_WAIT` 观望、`DEFENSIVE_AVOID` 防守回避 | 枚举 | 否 | 风控否决时只能说观察，不说买入 |
| `meta_labeling_decision.risk_veto_applied` / `veto_reasons` / `veto_codes` | 是否被风控否决、原因文本及稳定编码（`BENCHMARK_MISSING`、`BENCHMARK_WEAK`、`RR_INSUFFICIENT`、`BELOW_LOWER_RAIL`、`BREAKOUT_LOW_VOLUME`，与原因一一对应） | 布尔 / 文本 / 枚举 | 否 | 否决时置顶转述原因文本，编码不需要对外说 |

## 6. 风控计划 `triple_barrier_plan` 与否决 `debate_and_veto`

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `entry_zone` | 入场区 `[下沿, 上沿]` | 价格 | 否 | 风控否决时称"观察区" |
| `stop_loss_barrier` | 止损屏障（收盘跌破离场） | 价格 | 否 | — |
| `take_profit_1` / `take_profit_2` | 第一、第二止盈屏障 | 价格 | 否 | — |
| `time_barrier_bars` / `time_stop_rule` | 时间屏障：持仓满 N 根仍未到 TP1 且偏离不足时离场 | 根 | 否 | — |
| `risk_reward_ratio` | 扣除佣金、印花税后的 TP1 净盈亏比 | 比值，低于 1.20 风控否决 | 否 | "扣费后盈亏比 X" |
| `left_side_constraints` / `right_side_constraints` | 左侧（回踩）与右侧（突破）计划的必要证据、失效位、假突破规则 | 文本 / 价格 | 否 | 原文转述 |
| `market_microstructure.*` | 交易制度：`settlement`、`can_intraday_roundtrip`、`daily_price_limit_pct`（A 股为小数，如 0.1；港美股为空）、`lot_size`/`min_order_shares`/`lot_step`、`tick_size`、`session_hours_local`、`commission_bps`、`stamp_duty_buy_bps`/`stamp_duty_sell_bps`、港股 `lot_size_source`、美股 `day_trading_rule` | — | 部分可空 | 按市场转述，规则来源见 `references/{cn,hk,us}-market.md` |
| `holder_defense_plan` | 已持仓者的防守计划：`reduce_level`（收盘跌破减仓）、`exit_level`（硬止损离场）、`rebound_cap`（跌破后反抽上限）、`downside_reference`、`rules[]`（中文规则原文）、`note`（A 股 T+1 提示）；指数为 `available = false` | 价格 / 文本 | 否 | 用户说"已经持有"时转述 `rules`；不是卖出建议 |
| `short_plan` | 破位做空计划，仅港股、美股的股票/ETF（A 股与指数 `available = false` 并给出 `reason`）：`trigger`、`entry_zone`、`stop_loss_barrier`、`take_profit_1/2`、`time_barrier_bars`、`risk_reward_ratio`（扣交易成本，**未计融券费**）、`invalidation`、`constraints`（卖空制度限制）、`warnings`（大盘偏强、盈亏比不足） | 价格 / 文本 | 否 | 必须同时转述 `constraints` 与 `warnings`；不得给出报告里没有的做空点位 |
| `position_sizing.*` | 仓位：`tradable`、`account_equity`、`max_risk_pct`（%）、`risk_budget_amount`、`recommended_shares`、`estimated_notional` | 股 / 货币 | 未传账户净值或指数时不给股数 | "按止损距离计算为 X 股"，不是个性化建议 |
| `debate_and_veto.bull_case` / `bear_case` | 多空两方的规则化证据句 | 文本 | 否 | 可作为证据列举 |
| `debate_and_veto.risk_officer_veto` / `veto_reasons` / `veto_codes` | 风控否决开关与原因（大盘状态分 < 35、净盈亏比 < 1.20、收盘在结构下轨下方、突破缺量等） | 布尔 / 文本 | 否 | `true` 时置顶，不给做多建议 |
| `debate_and_veto.final_verdict_cn` | 一句话结论 | 文本 | 否 | 可原文引用 |

## 7. 条件推演 `rule_scenarios`

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `breakout_level` / `breakdown_level` | 上攻触发位与下探触发位，带来源 `source` | 价格 | 否 | — |
| `bull` / `neutral` / `bear` | 三条路径：`name`、`trigger`（触发条件）、`target_price` 与 `target_source`、`invalidation`（失效条件）、`retest_hold` / `rebound_cap`、`range`（震荡区间）、`waypoints_offset` 与 `step_labels`（画图折点） | 价格 / 文本 | 否 | 按"触发 → 目标 → 失效"转述，**不给任何路径的概率** |
| `atr_pct` / `sigma_1d_pct` | 计算时使用的 ATR 百分比；近 60 日日对数收益 RMS（折点时间用） | % | 否 | — |
| `min_trigger_distance_atr` | 触发位距最新收盘价的最小距离（0.5 个 ATR） | ATR 倍数 | 否 | — |
| `contested_levels[]` | 距现价不足最小距离、正在争夺的价位：`price`、`sources`、`side`（`above`/`below`） | 价格 | 否（可为空数组） | "现价附近正在争夺的价位"，不作触发条件 |
| `horizon_bars` / `timing_method` | 折点时间所在的预测期与估计方法（扩散尺度 (距离/σ)²，截断在预测期内） | 根 | 否 | — |
| `bull.eta_bars` / `bear.eta_bars` | 未截断的时间尺度估计：走到触发位、目标位约需多少根 | 根 | 否 | "按波动率估算约需 N 个交易日"，**只是时间量级，不是方向预测** |
| `bull.target_within_horizon` / `bear.target_within_horizon` | 目标位时间估计是否落在预测期内 | 布尔 | 否 | 为 `false` 时说明"目标超出 N 日预测期" |

## 8. 未来区间 `future_projection_15d`

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `method` / `horizon_bars` / `horizon_dates` | 方法（按期限 split-conformal）、预测天数与对应交易日 | — | 否 | — |
| `q10` / `q25` / `base_median_q50` / `optimistic_q75` / `q90` | 逐日分位数；`bearish_invalid_q25` 与 `q25` 相同（兼容字段） | 价格 | 否 | 50% 区间 = q25~q75，80% 区间 = q10~q90；**不是看涨/看跌概率** |
| `q50_method` / `q50_note` | `MARTINGALE_LAST_CLOSE`：中枢就是最新收盘价 | — | 否 | "区间只描述波动幅度，不含方向" |
| `conformal_50pct_band` / `conformal_80pct_band` | 逐日区间 `[下沿, 上沿]` | 价格 | 否 | — |
| `daily_price_limit_pct` | 区间按涨跌停截断时使用的比例 | 小数 | 是 | — |
| `calendar_coverage` | 未来日期是否在休市表覆盖内，`estimated_dates` 为按工作日估算的日期 | — | 否 | 有估算日期时转述 |
| `volatility_scaling.*` | `sigma_1d`（近 60 日日波动）、`overnight_gap_ratio`（仅披露）、`vol_scale_multiplier`、`vix` 与 `vix_source`、`earnings_date` | 比值 | `vix`、`earnings_date` 可空 | 为空时说明没有数据，不补数 |
| `promotion_status` | `PROMOTED`（已达标）或 `RESEARCH_CALIBRATION_WARMUP`（校准观察期） | 枚举 | 否 | 观察期不得说"已验证可靠" |
| `calibration_audit.*` | 滚动样本外审计：`evaluation_samples`、`effective_independent_samples`、`min_effective_independent_samples`（20）、`nominal_50pct_empirical_coverage` / `nominal_80pct_empirical_coverage`、`q50_mae_pct` 与 `random_walk_mae_pct`、`per_horizon_coverage[]`、`uncalibrated_overlays` | 比例（0~1）/ 个数 / % | 否 | 必须同时给出覆盖率与独立样本数 |
| `historical_analogues.*` | 历史相似片段（`matches[]` 带相似度与后续收益），`used_in_quantiles = false` | — | 否 | 只作参考，不说成预测 |

## 9. 数据质量与可复现

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `quality.status` | `OK` / `PARTIAL`（如缺基准）/ `UNAVAILABLE`（合成或无效数据） | 枚举 | 否 | 不是 `OK` 时先说告警 |
| `quality.data_source_class` / `benchmark_source_class` | 来源分类 `LIVE` / `LOCAL_FILE` / `SYNTHETIC` / `UNKNOWN` | 枚举 | 否 | — |
| `quality.warnings[]` / `notes[]` | 告警与备注（盘中K线已排除、数据只到某日、复权未生效、日历估算、元数据未取得等） | 文本 | 否（可为空数组） | `PARTIAL` 时逐条转述 warnings；notes 都要转述 |
| `quality.provenance_errors[]` | 来源判定错误 | 文本 | 否 | 非空时不解读 |
| `quality.completed_bars_total` / `display_bars` / `structure_bars` | 参与计算的已收盘根数、显示根数、结构窗口根数 | 根 | 否 | — |
| `reproducibility.engine_version` / `bars_hash` / `report_hash` | 引擎版本与输入、结果哈希 | — | 否 | 复现时使用 |

## 10. 展示与兼容字段（不在顶层契约内）

| 字段 | 含义 | 对外怎么说 |
| --- | --- | --- |
| `replay_snapshots[]` | HTML 回放滑块使用的历史截面（当时的区间与之后的实际收盘） | 不需要转述 |
| `series_export` | 主图 K 线、拐点 `pivots`、大级别背景 `background`（近 250 日位置，只描述位置，不作触发位）、盘中K线 `intraday` | 盘中K线只作标注 |
| `legacy_summary` | v4.1 结构层兼容输出（PNG 渲染使用），内容与上面字段同源 | 优先使用 `*_report.md`，不要再拼接其中的旧文案块 |
| `rendered_paths` | 各产物的本机绝对路径 | 回复末尾给出路径 |

## 11. `*_backtest.json`

| 字段 | 含义 | 单位 / 取值 | 可空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `causal_replay_audit.*` | 无前视审计：`checked_cutoffs`、`modules_audited`、`mismatches_count`、`passed` | — | 否 | "无前视审计通过（检查 N 个截面）" |
| `backtest.method` / `entry_rule` | `LIVE_PLAN_REPLAY_V6`：每个决策日只用当时数据调用线上同一流水线；次日开盘落在入场区才成交 | — | 否 | 原文转述规则 |
| `backtest.decision_window` | 回放区间、决策日数、步长 | — | 否 | — |
| `backtest.actions_seen` | 各动作标签出现次数 | 次 | 否 | — |
| `backtest.no_fill_outside_entry_zone` | 次日开盘不在入场区、未成交的次数 | 次 | 否 | — |
| `backtest.all_long_signals` / `long_confirmed` / `watch_long_setup` | 三个口径的成交统计：`trades`、`cumulative_return_pct`、`win_rate_pct` 与 `win_rate_95ci_pct`（Wilson）、`mean_return_95ci_pct`（Bootstrap）、`sharpe`、`max_drawdown_pct`、`profit_factor` | 笔 / % | 无成交时部分可空 | 胜率必须带 95% 区间与笔数；是历史成交统计，不是未来概率 |
| `backtest.buy_and_hold_return_pct` | 同期买入持有收益 | % | 否 | 与策略累计收益对比 |
| `backtest.rule_score_ic` | 规则分与未来 H 日收益的 Spearman 秩相关：`spearman`、`decisions`、`effective_independent_samples`（≈ 决策数 × 步长 / H）、`informative`（独立样本 ≥ 20 且 \|IC\| ≥ 0.05）、`verdict` | −1~1 / 个数 | `spearman` 可空 | 原文转述 `verdict`；`informative = false` 时说"规则分在该标的上只作描述" |
| `backtest.rule_score_buckets[]` | 按规则分 [0,38)、[38,52)、[52,65)、[65,100] 分层的决策日数、未来 H 日平均/中位收益、上涨占比 | 个 / % | 无样本的层为空 | 是历史统计，不是未来概率 |
| `backtest.action_forward_returns.*` | 各动作标签之后的未来 H 日平均/中位收益与上涨占比 | 个 / % | 无样本时为空 | 用来检验动作标签与后续走势是否一致 |
| `backtest.veto_reason_counts` | 回放期间各否决编码出现次数 | 次 | 否（可为空对象） | — |
| `backtest.sample_trades[]` | 成交样例：入场/出场日与价、`outcome`（`TP_BARRIER` / `SL_BARRIER` / `TIME_BARRIER` 等）、`net_return` | — | 否 | — |
| `backtest.sample_size_warning` | 成交少于 30 笔时的提示 | 文本 | 是 | 有则原文转述 |
| `backtest.conformal_forecast_oos.*` | 区间样本外覆盖：无重叠截面 `evaluated_cutoffs` 与覆盖率，以及与主报告一致的滚动全截面覆盖率 | 比例 | 否 | — |
