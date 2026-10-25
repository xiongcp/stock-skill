# 输出字段字典（v6）：`*_result.json` 与 `*_backtest.json`

`*_result.json` 是本技能唯一的稳定输出接口（`schema_version = 6.0.0`），由 `scripts/contract_v6.py` 从内部报告投影生成，`assets/analysis.schema.json` 全量校验（含 `NaN` / `Infinity` 拦截）。用户追问某个字段时查本文件。所有数字只能取自本次运行的 JSON，本文件只解释含义，**不要引用本文件里的示例值**。

约定：
- "价格"单位为标的交易货币（`meta.currency`），已按 `meta.adjustment` 复权，并按价位表取整。
- "可否为空"一栏为"是"的字段可能为 `null`，表示该项没有数据，**不得自行补数**。
- "稳定级别"列照抄 `assets/analysis.schema.json` 的 `x-stability`。表中未单独标注的子字段，跟随其父字段的级别。
- `quotable_prices` 之外的价格不得出现在回复里；回复必须通过 `scripts/lint_response.py`。

## 稳定性规则（从 6.0.0 起）

1. `schema_version` 遵循语义化版本：6.x 内只新增字段，不删除、不改名、不改语义；破坏性变更只出现在主版本号。
2. 枚举可能新增取值，读取方必须容忍未知取值。
3. 每个字段标注稳定级别：`stable` 按第 1 条承诺；`experimental` 可在次版本中变动，变动写进 `CHANGELOG.md` 对应版本的"契约变更"小节。
4. `*_chart_data.json` 不是稳定接口，只供本技能的 PNG / HTML 渲染使用，字段可能在任何版本变化；回答用户时不要引用。
5. 契约边界由测试守护：新增或移除契约字段时，必须同时更新 `assets/analysis.schema.json` 的 `x-stability`、`CHANGELOG.md` 的"契约变更"小节，以及 `tests/fixtures/contract/result_v6_keypaths_{600519_plain,600519_equity_backtest,00700_hk,nvda_us,000001_index}.json` 各配置的全键路径快照（`tests/test_v6_contract.py::test_v6_result_keypaths_match_snapshot` 按配置参数化：A 股基线、带账户净值与回测、港股、美股、指数）；`backtest.json` 的键结构由 `tests/fixtures/contract/backtest_v6_keypaths_600519.json` 守护（以动作代码、否决代码为键的统计表折叠为 `{*}`）。
6. `result.json` 落盘（`indent=2`）不超过 80 KB。

## 顶层

| 字段 | 内容 | 稳定级别 |
| --- | --- | --- |
| `schema_version` | 契约版本，当前 `6.0.0` | stable |
| `meta` | 标的、市场、币种、时区、截止日、复权、价格精度、数据源、引擎版本、请求参数 | stable |
| `quality` | 数据质量 `status`（OK / PARTIAL / UNAVAILABLE）、告警与备注 | stable |
| `verdict` | 一眼结论：评级、风控否决与理由、规则分、一句话结论 | stable（明细为 experimental） |
| `market_context` | 大盘基准、环境评分、相对强弱 | stable |
| `structure` | 最新收盘、本标的体制、关键价位、通道、斐波那契、浪型、形态、SMC、成交分布 | stable（部分子字段 experimental） |
| `indicators` | 趋势、动量、波动率指标快照 | stable（量价因子 experimental） |
| `scenarios` | 2~5 个活跃情景与上攻 / 震荡 / 下探三条路径：触发条件、目标位、失效条件，不含任何概率或频率 | stable |
| `plan` | `long` 做多计划、`holder_defense` 持仓防守、`short` 做空计划（港股、美股） | stable |
| `volatility_range` | 以最新收盘为中心的正常波动范围（q10~q90）与样本外覆盖率审计，只描述幅度、不含方向 | stable |
| `quotable_prices` | 回复中允许引用的全部价位与容差 | stable |
| `files` | 各产物相对 `--output-dir` 的文件名 | stable |
| `reproducibility` | 引擎版本、K线哈希、报告哈希 | stable |

## `meta`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `meta.symbol` / `meta.name` | 标的代码与简称 | 字符串 | 否 | stable | "贵州茅台（600519.SH）" |
| `meta.market` | 市场 | `CN` / `HK` / `US` | 否 | stable | — |
| `meta.asset_type` | 品种 | `index` / `equity` / `etf` | 否 | stable | — |
| `meta.currency` | 交易货币 | `CNY` / `HKD` / `USD` | 否 | stable | — |
| `meta.timeframe` | K 线周期 | `1D` / `1W` / `60m` / `15m`（当前只产出 `1D`） | 否 | stable | — |
| `meta.timezone` | 交易所时区，判定"是否已收盘"用 | `Asia/Shanghai` / `Asia/Hong_Kong` / `America/New_York` | 否 | stable | 一般不需要提 |
| `meta.as_of` | 最后一根**已收盘** K 线的日期 | `YYYY-MM-DD` | 否 | stable | "数据截至 X" |
| `meta.adjustment` | 复权口径：`total_return` 前复权含分红、`split` 只复权拆合股、`raw` 不复权 | 枚举 | 否 | stable | "前复权（含分红送转）" |
| `meta.color_scheme` | 配色：`cn_red_up` 红涨绿跌 / `us_green_up` 绿涨红跌 | 枚举 | 否 | stable | — |
| `meta.price_decimals` | 价格显示小数位（随价位表） | 整数 | 否 | stable | — |
| `meta.data_source` | 标的行情来源，如 `SINA_LIVE_DAILY`、`YAHOO_DAILY(0700.HK)` | 字符串 | 否 | stable | "数据来自新浪日线" |
| `meta.engine_version` | 生成本文件的技能版本 | 如 `6.0.0` | 否 | stable | 复现时使用 |
| `meta.is_st` / `meta.is_ipo_no_limit` | A 股风险警示标识；新股无涨跌停期 | 布尔 | 否 | stable | ST 时提醒涨跌幅与风险警示 |
| `meta.board_lot` | 港股每手股数 | 股 | 是（非港股为空；港股未取得时为空） | stable | "每手 X 股"；为空的港股提醒以券商为准 |
| `meta.request` | 本次运行参数：`symbol`、`market`、`asset_type`、`display_bars`（`--days`）、`structure_bars`（结构窗口，固定 120）、`horizon_bars`，以及传入时的 `account_equity`、`max_risk_pct`、`color_scheme`、`as_of`、`adjustment`、`benchmark_symbol` | 对象 | 否（子键按是否传参出现） | stable | 不需要逐项转述 |

## `quality`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `quality.status` | `OK` / `PARTIAL`（如缺基准）/ `UNAVAILABLE`（合成或无效数据） | 枚举 | 否 | stable | 不是 `OK` 时先说告警；`UNAVAILABLE` 不输出任何价位 |
| `quality.data_source` | 标的数据源（同 `meta.data_source`） | 字符串 | 否 | stable | — |
| `quality.data_source_class` | 标的来源分类 | `LIVE` / `LOCAL_FILE` / `SYNTHETIC` / `UNKNOWN` | 否 | stable | — |
| `quality.benchmark_source` / `benchmark_source_class` | 基准数据来源及分类 | 字符串 / 同上 | 是（本地文件输入缺基准时） | stable | 缺基准时说明质量为 `PARTIAL` |
| `quality.warnings` | 告警（数据质量不是 `OK` 的原因） | 文本数组 | 否（可为空数组） | stable | `PARTIAL` 时逐条放在回答首段 |
| `quality.notes` | 备注：盘中K线已排除、数据只到某日、复权未生效、日历估算、元数据未取得等 | 文本数组 | 否（可为空数组） | stable | 逐条转述 |
| `quality.provenance_errors` | 来源判定错误 | 文本数组 | 否（可为空数组） | experimental | 非空时不解读 |
| `quality.completed_bars_total` / `display_bars` / `structure_bars` | 参与计算的已收盘根数、显示根数、结构窗口根数 | 根 | 否 | stable | — |
| `quality.forecast_promotion_status` | 正常波动范围的晋级状态（同 `volatility_range.promotion_status`） | `PROMOTED` / `RESEARCH_CALIBRATION_WARMUP` | 否 | stable | 观察期不得说"已验证可靠" |
| `quality.placeholder_bars_removed` | Yahoo 日线中被剔除的占位K线日期（成交量为 0 且开高低收四价相同，不是真实成交；只剔除、不补数）：`target[]`、`benchmark[]` | 日期数组 | 否（可为空数组） | stable | 非空时按 `notes` 转述，不必逐日列出 |

## `verdict`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `verdict.action` / `action_cn` | 动作标签 | `LONG_CONFIRMED` 右侧/结构看多确认、`WATCH_LONG_SETUP` 条件观察（待放量/收盘确认）、`NEUTRAL_HOLD_WAIT` 观望等待突破信号、`DEFENSIVE_AVOID` 防守回避/破位警惕 | 否 | stable | 风控否决时只能说观察或防守，不说买入 |
| `verdict.setup_type` / `setup_label_cn` | 计划类型 | `RIGHT_SIDE_BREAKOUT` 右侧顺势突破确认、`LEFT_SIDE_PULLBACK` 左侧支撑回踩观察、`WAIT_CONFIRMATION` 等待确认（标签有"区间观察/等待右侧收盘突破""冲高回落/假阳线待右侧二次确认"两种） | 否 | stable | 原文转述中文标签 |
| `verdict.risk_veto` | 风控否决开关 | 布尔 | 否 | stable | `true` 时置顶，不给做多建议 |
| `verdict.veto_reasons` | 否决原因文本（大盘状态分 < 35、扣费后净盈亏比 < 1.20、收盘在结构下轨下方、突破缺量、计划几何不合法等） | 文本数组 | 否（可为空数组） | stable | 否决时置顶逐条转述 |
| `verdict.veto_codes` | 与原因一一对应的稳定编码：`BENCHMARK_MISSING`、`BENCHMARK_WEAK`、`RR_INSUFFICIENT`、`BELOW_LOWER_RAIL`、`BREAKOUT_LOW_VOLUME`、`PLAN_GEOMETRY_INVALID`（止损不低于入场区下沿或 TP1 不高于入场区上沿） | 枚举数组（可能新增取值） | 否（可为空数组） | stable | 编码不需要对外说 |
| `verdict.rule_score` | 四组各 25 分的确定性规则加权分；均值回归体制（`rule_detail.regime_adjustment.applied = true`）下为反转调整后的分数，不再等于四组之和 | 0~100 | 否 | stable | "规则分 X / 100，不是胜率或概率"；体制调整时同时转述 `regime_adjustment.note_cn` |
| `verdict.rating` / `rating_cn` | `BULLISH_RESONANCE` 多周期多头共振 / `BEARISH_PRESSURE` 多周期空头承压 / `NEUTRAL_WATCH` 多空分歧；均值回归体制下 `rating_cn` 改为"均值回归体制评分偏多/偏空/中性" | 枚举 | 否 | stable | 用中文标签 |
| `verdict.headline_cn` | 一句话结论 | 文本 | 否 | stable | 原文引用 |
| `verdict.overextended` | 短期是否乖离过大（追高风险） | 布尔 | 否 | experimental | 为 `true` 时提醒不要追高 |
| `verdict.rule_detail` | 规则分明细：`components`（`macro_trend_25`、`structure_pattern_25`、`strategy_trigger_25`、`volume_benchmark_25`，各 0~25）、`components_total`（调整前合计）、`regime_adjustment`（`applied`、`scoring_regime`、`components_total`、`adjusted_rule_score`、`reversion_score_adj`、`causal_momentum_ic_15d`、`note_cn`）、`timeframes_aligned`、`scoring_method`、`note` | 对象 | 否（未调整时 `applied = false`） | experimental | 追问时解释；调整时原文转述 `note_cn`，不得把调整后的低分说成"多周期空头" |
| `verdict.strategy_signals` | 五个规则策略的状态：`strategy_id`、`name_cn`、`family`、`direction`、`state`（如 `LONG_ACTIVE`）、`as_of`、`formula_verified` | 对象数组 | 否 | experimental | "某策略处于多头激活" |
| `verdict.anti_quant_traps` | 反量化陷阱检查：`warnings_cn`、`gates_active`、`ignition_bull_trap_downgraded`、`momentum_crowding` 等 | 对象 | 是 | experimental | 有 `warnings_cn` 时原文转述 |
| `verdict.bull_case` / `bear_case` | 多空两方的规则化证据句 | 文本数组 | 否（可为空数组） | experimental | 可作为证据列举 |

## `market_context`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `market_context.index_symbol` / `index_name` | 对比基准（A 股沪深300、港股恒生指数、美股 SPY） | 字符串 | 是（缺基准时） | stable | — |
| `market_context.regime` / `regime_label_cn` | 基准的市场状态，五类之一（见 `references/interpretation-guide.md` 第 6 节） | 枚举 | 是（缺基准时） | stable | 用中文标签 |
| `market_context.regime_score` | 基准的状态分，由 ADX、EMA、DI 等规则加减得出 | 5~95，低于 35 风控否决做多 | 是（缺基准时） | stable | "状态分 X / 100"，**不是概率** |
| `market_context.relative_strength_vs_index` | 近 20 日标的归一化走势与基准之比 | 比值，>1 跑赢、<1 跑输 | 是（缺基准时） | stable | "近 20 日跑赢/跑输基准" |
| `market_context.benchmark_missing` | 本地文件输入缺基准时为 `true` | 布尔 | 否 | stable | 缺基准时说明质量为 `PARTIAL` |
| `market_context.source` | 基准数据来源 | 字符串 | 是 | stable | — |
| `market_context.bench_ret1` / `bench_ret5` | 基准近 1 日、5 日涨跌幅 | 小数 | 是 | experimental | 追问时解释 |

## `structure`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `structure.last_close` | 最近已收盘 K 线的收盘价 | 价格 | 否 | stable | "最新收盘 X" |
| `structure.regime` | 标的自身状态：`regime`（`TRENDING_BULL` 多头趋势 / `TRENDING_BEAR` 空头趋势 / `SQUEEZE_CONSOLIDATION` 收敛蓄势 / `MEAN_REVERTING_RANGE` 区间震荡 / `HIGH_VOLATILITY_CHAOS` 高波失序）、`label_cn`、`regime_score`、`evidence`（ADX、带宽分位、波动比、与 EMA 的偏离、VIX 等）、`routed_strategy_family`、`formula_verified` | 对象 | `evidence.vix` 可空 | stable | 用中文标签，只描述当下，不代表方向；状态分不是概率 |
| `structure.key_levels` | 支撑压力阶梯：`supports[]` / `resistances[]`，每项 `price` 与来源 `sources`（前高、前低、均线、斐波那契、轨道等） | 价格 | 否 | stable | 按由近到远转述并带来源 |
| `structure.support_zone` / `resistance_zone` | 最近支撑区、压力区 `[下沿, 上沿]` | 价格数组 | 否 | stable | — |
| `structure.channel` | 轨道形态：`pattern`（如"上升通道""下降楔形"，轨道与拐点稳健回归方向相反时为"形态不明确（…）"）、`status`（`above` / `inside` / `below`）、`status_text`、上下轨今日值 `upper_today` / `lower_today`、顶点距今根数 `apex_bars_ahead`、`fit_check`（上/下轨斜率与 Theil-Sen 稳健斜率，%/根；`consistent`；走平阈值 `flat_pct_per_bar`）、拐点与轨道端点 | 价格 / 根 | 拟合失败时 `fitted = false` | experimental | 按 `status_text` 原文转述；"形态不明确"时不要套用任何教科书形态 |
| `structure.fib_levels` | 近 120 日高低点之间的回撤 / 反弹位：`kind`、`from_high`、`to_low`、`retracements[]`（`ratio`、`price`、`status`） | 价格 / 比例 | 是 | experimental | — |
| `structure.recent_gaps` | 近期未回补跳空缺口 | 价格区间数组 | 否（可为空数组） | stable | — |
| `structure.abc_scenario` | 主 ABC 浪型：方向 `direction`、A/B 端点、`b_retrace`（B 回撤 A 的比例）、`c_progress`（C 浪进度与所处阶段 `phase`）、测算位 `levels`、`count_invalidation`（收盘越过即此计数失效） | 价格 / 比例 | 是（无 ABC 时） | experimental | 只按 `phase` 原文描述，不自行数浪 |
| `structure.wave_counts` | 浪型计数列表：第一项 `role = "主计数"`（与 `abc_scenario` 相同），其后最多 2 个 `role = "备选计数"`（A 浪起点不同、测算位也不同的另一种数法）；每项另有 `a_range_atr`（A 浪幅度，ATR 倍数） | 价格 / 倍数 | 否（无 ABC 时为空数组） | experimental | 先说主计数，再说备选数法及各自的否定条件；**备选不代表可能性大小** |
| `structure.patterns` | 识别到的蜡烛、波段与背离形态：`pattern`、`name_cn`、`category`、`direction`、`observed_at`（形态日）、`confirmed_at`（确认日）、`volume_confirmed`、`formula_verified`（代入数值的判定公式） | 对象数组 | 否（可为空数组） | stable | 只引用 `name_cn` 与 `formula_verified` |
| `structure.smc` | 算法结构区：`fvgs[]`（公允价值缺口，`status` 为 `ACTIVE` / `MITIGATED` 等）、`order_blocks[]`、`structure_breaks[]`、`note` | 价格区间 | 否（子数组可为空） | experimental | "算法结构区"，不说成机构真实挂单 |
| `structure.volume_profile` | Volume Profile 近似成交分布的 `poc_price`（成交最密集价位）、`vah_price` / `val_price`（70% 价值区上下沿），另有 `value_area_coverage`、`lookback_bars`、`approximation_method`、`note`；24 个价格桶明细在 `chart_data.json` | 价格 | 数据不足时三个价位可空 | stable | "成交密集区"，**不是真实持仓筹码** |

## `indicators`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `indicators.trend` | 趋势指标：`sma20` / `sma60` / `sma120` / `sma250`、`ema20` / `ema50` / `ema60` / `ema200`、`vwap20`（近 20 日成交量加权均价）、`supertrend_line` / `supertrend_dir`（1 多、-1 空）、`adx14` / `plus_di14` / `minus_di14`（0~100，ADX 高于 23 视为有趋势） | 价格 / 0~100 | 历史不足时部分可空 | stable | "MA20 X" |
| `indicators.momentum` | 动量指标：`rsi14`（0~100，不单独作为买卖依据）、`rsi14_prev`（前一根K线的 RSI14，普通指标）、`macd_dif` / `macd_dea` / `macd_hist`、`stoch_k` / `stoch_d`、`rvol20`（当日成交量 / 近 20 日均量，倍数） | 数值 | 历史不足时可空 | stable | "量能为 20 日均量的 X 倍" |
| `indicators.volatility` | 波动率指标：`atr14`、`atr20_pct`（占价格百分比）、`bb_upper` / `bb_mid` / `bb_lower`、`bbw`、`bbw_percentile_120`（近 120 日带宽分位，低表示收敛） | 价格 / % / 0~100 | 否 | stable | — |
| `indicators.qlib_factors` | 量价因子：实体占比、上下影线比、量价相关、ATR 归一化动量、20/60 日年化波动及其比值等 | 比值 | 是 | experimental | 只在用户追问时解释 |

## `scenarios`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `scenarios.horizon_bars` | 推演期根数 | 根 | 否 | stable | — |
| `scenarios.atr_pct` / `sigma_1d_pct` | 计算时使用的 ATR 百分比；近 60 日日对数收益 RMS | % | 是 | experimental | — |
| `scenarios.min_trigger_distance_atr` | 触发位距最新收盘价的最小距离（0.5 个 ATR） | ATR 倍数 | 是 | experimental | — |
| `scenarios.breakout_level` / `breakdown_level` | 上攻触发位与下探触发位：`price`、来源 `source` | 价格 / 文本 | 否 | stable | — |
| `scenarios.contested_levels` | 距现价不足最小距离、正在争夺的价位：`price`、`sources`、`side`（`above` / `below`） | 价格 | 否（可为空数组） | experimental | "现价附近正在争夺的价位"，不作触发条件 |
| `scenarios.paths` | 三条主路径。`bull`：`name`、`trigger`、`retest_hold`、`target_price`、`target_source`、`invalidation`；`neutral`：`name`、`range`（震荡区间）、`target_price`、`condition`；`bear`：`name`、`trigger`、`rebound_cap`、`target_price`、`target_source`、`invalidation` | 价格 / 文本 | 否 | stable | 按"触发 → 目标 → 失效"转述，不承诺涨跌 |
| `scenarios.active` | 从 8 类标准情景库（见 `references/scenario-playbook.md`）中筛出的 2~5 个情景，每项字段见下表 | 对象数组 | 否 | stable | 按状态、触发、目标、失效转述，不附加任何频率或概率 |
| `scenarios.precondition_met_count` | 实际满足前提的情景个数 | 整数 | 是 | experimental | — |
| `scenarios.impulse_wave` | 推动 5 浪识别结果（浪 2 不破浪 1 起点、浪 3 非最短、浪 4 不进入浪 1 区间时返回，含 `wave3_1618_target`） | 价格 / 文本 | 是（未形成推动浪时） | experimental | 有则说明推动浪进度与测算位 |

`scenarios.active[*]` 必有的 9 个字段（枚举可能新增取值）：

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `id` | 情景 ID（8 类之一） | 枚举 | 否 | stable | — |
| `name` | 情景中文名 | 文本 | 否 | stable | 用中文名 |
| `direction` | 方向 | `BULLISH` / `BEARISH` / `NEUTRAL` 等 | 否 | stable | — |
| `status` / `status_cn` | `ACTIVE`（已在进行中）/ `PENDING_TRIGGER`（待触发）；前提未成立的条件备选情景的 `status_cn` 为"待触发（前提未成立，条件备选）" | 枚举 / 文本 | 否 | stable | 转述 `status_cn` |
| `precondition_met` | 当前截面是否满足前提；不足 2 个时补充的备选情景保留 `false` 并标 `selection_role = CONDITIONAL_FALLBACK` | 布尔 | 否 | stable | `false` 时说明是条件备选 |
| `trigger` | 触发条件（已在进行中时描述当前位置） | 文本 | 否 | stable | 原文转述 |
| `target_price` | 目标位 | 价格 | 是 | stable | 带 `target_source` 一起说 |
| `invalidation` | 失效条件 | 文本 | 否 | stable | 原文转述 |

另有 `precondition_rationale`、`precondition_template`、`selection_role`、`applicability_score`、`trigger_price`、`reference_trigger_price`、`trigger_distance_atr`、`target_source`、`target_source_template`、`invalidation_price`、`invalidation_template` 等辅助字段，按 `scenarios.active` 的级别（stable）对待，一般不需要逐项转述。

## `plan`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `plan.long` | 做多三重屏障计划，字段见下表 | 对象 | 否 | stable | 风控否决时入场区称"观察区" |
| `plan.holder_defense` | 已持仓者的防守计划：`reduce_level`（收盘跌破减仓）、`exit_level`（盘中触及硬止损屏障离场）、`rebound_cap`（跌破后反抽上限）、`upside_recheck_level`、`downside_reference`（`price`、`source`）、`rules[]`（中文规则原文）、`note`（A 股 T+1 提示）、`applies_to`；指数为 `available = false` | 价格 / 文本 | 否 | stable | 用户说"已经持有"时转述 `rules`；不是卖出建议 |
| `plan.short` | 破位做空计划，仅港股、美股的股票/ETF：`available`、`trigger`、`entry_zone`、`stop_loss_barrier`、`take_profit_1` / `take_profit_2`、`time_barrier_bars`、`risk_reward_ratio`（扣交易成本，**未计融券费**）、`invalidation`、`constraints`（卖空制度限制）、`warnings`（大盘偏强、盈亏比不足）；A 股与指数 `available = false` 并给出 `reason` | 价格 / 文本 | 否 | stable | 必须同时转述 `constraints` 与 `warnings`；不得给出报告里没有的做空点位 |

`plan.long` 的字段：

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `entry_zone` | 入场区 `[下沿, 上沿]` | 价格 | 否 | stable | 风控否决时称"观察区" |
| `stop_loss_barrier` | 硬止损屏障（盘中最低价触及离场；A 股受 T+1 约束自入场次日起生效） | 价格 | 否 | stable | — |
| `take_profit_1` / `take_profit_2` | 第一、第二止盈屏障 | 价格 | 否 | stable | — |
| `time_barrier_bars` | 时间屏障：持仓满 N 根仍未到 TP1 且偏离不足时离场（规则文字在 `time_stop_rule`） | 根 | 否 | stable | — |
| `risk_reward_ratio` | 扣除佣金、印花税后的 TP1 净盈亏比 | 比值，低于 1.20 风控否决 | 否 | stable | "扣费后盈亏比 X" |
| `breakeven_ratchet` | 移动保本规则（与回测同源）：`enabled`、`trigger_r`（0.90）、`confirm_close_r`（0.30）、`lock_r`（0.08）、`trigger_price`、`confirm_close_price`、`breakeven_stop_price`、`rule_cn` | 价格 / R 倍数 / 文本 | 否 | stable | 原文转述 `rule_cn` |
| `market_microstructure` | 交易制度：`market`、`board`、`settlement`、`can_intraday_roundtrip`、`daily_price_limit_pct`（A 股为小数，如 0.1；港美股为空）、`lot_size` / `min_order_shares` / `lot_step`、`tick_size`、`session_hours_local`、`commission_bps`、`stamp_duty_buy_bps` / `stamp_duty_sell_bps`、`stamp_duty_note`、`is_st`、`is_ipo_no_limit`、`color_scheme`，港股另有 `lot_size_source`，美股另有 `day_trading_rule` | — | 部分可空 | stable | 按市场转述，规则来源见 `references/{cn,hk,us}-market.md` |
| `position_sizing` | 仓位：`tradable`、`account_equity`、`max_risk_pct`（%）、`risk_budget_amount`、`sizing_basis`（`ENTRY_HIGH_WORST_FILL`：每股风险 = 入场区上沿 − 止损 + 上沿 × 往返费率，区内任意价位成交触及止损都不超预算）、`unit_risk_per_share`、`recommended_shares`、`estimated_notional`（按入场区上沿计）、`max_loss_at_stop`、`gap_risk_note`（跳空/跌停时亏损可能超预算）、`reason` | 股 / 货币 | 未传账户净值、指数、**风控否决或评级不是 `LONG_CONFIRMED` / `WATCH_LONG_SETUP`** 时 `recommended_shares` 等为 `null` 并给出 `reason` | stable | "按入场区上沿到止损的距离计算为 X 股"，同时转述 `gap_risk_note`；为 `null` 时转述 `reason`，不得自行换算股数；不是个性化建议 |

另有 `left_side_constraints`（左侧回踩的必要证据、失效位与离场规则）、`right_side_constraints`（右侧突破的必要证据与假突破规则）与 `time_stop_rule`，随 `plan.long` 输出，原文转述。

## `volatility_range`

以最新收盘价为中心的正常波动范围，只描述波动幅度，不含方向判断；不是看涨 / 看跌概率。

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `volatility_range.method` | 方法（按期限 split-conformal，中枢取最新收盘） | 字符串 | 是 | stable | — |
| `volatility_range.promotion_status` | `PROMOTED`（已达标）或 `RESEARCH_CALIBRATION_WARMUP`（校准观察期） | 枚举 | 否 | stable | 观察期不得说"已验证可靠" |
| `volatility_range.horizon_bars` / `horizon_dates` | 预测天数与对应交易日 | 根 / 日期数组 | 否 | stable | — |
| `volatility_range.calendar_coverage` | 未来日期是否在休市表覆盖内：`covered`、`coverage_end`、`estimated_dates`（按工作日估算的日期） | 对象 | 是 | stable | 有估算日期时转述 |
| `volatility_range.q10` / `q25` / `q50` / `q75` / `q90` | 逐日分位数 | 价格数组 | 否 | stable | 50% 范围 = q25~q75，80% 范围 = q10~q90；**不是看涨/看跌概率** |
| `volatility_range.q50_method` | `MARTINGALE_LAST_CLOSE`：中枢就是最新收盘价 | 字符串 | 是 | stable | "区间只描述波动幅度，不含方向" |
| `volatility_range.q50_note` | 中枢取法的说明 | 文本 | 是 | experimental | — |
| `volatility_range.band_50` / `band_80` | 逐日范围 `[下沿, 上沿]` | 价格 | `band_50` 可空 | stable | — |
| `volatility_range.daily_price_limit_pct` | 范围按涨跌停截断时使用的比例 | 小数 | 是 | stable | — |
| `volatility_range.volatility_scaling` | `sigma_1d`（近 60 日日波动）、`sigma_lookback_bars`、`overnight_gap_ratio`（仅披露）、`vol_scale_multiplier`、`vix` 与 `vix_source`、`earnings_date` | 比值 | `vix`、`earnings_date` 可空 | experimental | 为空时说明没有数据，不补数 |
| `volatility_range.calibration_audit` | 滚动样本外审计：`calibrated`、`evaluation_samples`、`effective_independent_samples`、`min_effective_independent_samples`（20）、`nominal_50pct_empirical_coverage` / `nominal_80pct_empirical_coverage`、`q50_mae_pct` 与 `random_walk_mae_pct`、`per_horizon_coverage[]`、`uncalibrated_overlays`、`coverage_scope_note` | 比例（0~1）/ 个数 / % | 是 | stable | 必须同时给出覆盖率与独立样本数 |

## `quotable_prices`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `quotable_prices.price_decimals` | 价格小数位 | 整数 | 否 | stable | — |
| `quotable_prices.tolerance` | 比对容差（一个最小价位） | 价格 | 否 | stable | — |
| `quotable_prices.prices` | 回复中允许出现的全部价位：收集自 `structure`、`indicators`、`scenarios`、`plan`、`volatility_range` 中的价格字段，加上最近 20 根 K 线的开高低收（K 线本身在 `chart_data.json`） | 价格数组 | 否 | stable | `lint_response.py` 用它判断有没有编造价格；回复里的价格必须在其中 |

## `files`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `files.result_json` | 本文件 | 相对 `--output-dir` 的文件名 | 否 | stable | 回复末尾给出路径 |
| `files.chart_data_json` | 渲染数据，非稳定接口 | 文件名 | 否 | stable | 不要引用其内容 |
| `files.preview_png` / `dashboard_html` / `report_md` | PNG 结构推演图、离线交互看板、中文报告 | 文件名 | 否 | stable | 客户端能显示图片时内嵌 PNG，否则只给路径 |
| `files.backtest_json` | 回测结果，仅传 `--backtest` 时存在 | 文件名 | 否（无回测时不出现该键） | stable | 见本文件末节 |

## `reproducibility`

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- | --- |
| `reproducibility.engine_version` | 引擎版本 | 如 `6.0.0` | 否 | stable | 复现时使用 |
| `reproducibility.bars_hash` | 输入 K 线哈希 | 字符串 | 否 | stable | 复现时使用 |
| `reproducibility.report_hash` | 结果哈希 | 字符串 | 否 | stable | 复现时使用 |

## `*_backtest.json`

线上计划逐日回放的成交统计（`--backtest` 时生成）。顶层有 `schema_version`、`symbol`、`market`、`causal_replay_audit`、`backtest`。本文件不在 `assets/analysis.schema.json` 的约束内，本字典不为其字段标注稳定级别。

| 字段 | 含义 | 取值 / 单位 | 可否为空 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `schema_version` | 与 `result.json` 同一契约版本 | 如 `6.0.0` | 否 | 一般不需要提 |
| `causal_replay_audit.*` | 无前视审计：`checked_cutoffs`、`modules_audited`、`mismatches_count`、`passed` | — | 否 | "无前视审计通过（检查 N 个截面）"；只说明没用到未来数据，不说明策略有效 |
| `backtest.method` / `entry_rule` | `LIVE_PLAN_REPLAY_V6`：每个决策日只用当时数据调用线上同一流水线；次日开盘落在入场区才成交 | — | 否 | 原文转述规则 |
| `backtest.decision_window` | 回放区间、决策日数、步长 | — | 否 | — |
| `backtest.actions_seen` | 各动作标签出现次数 | 次 | 否 | — |
| `backtest.no_fill_outside_entry_zone` | 次日开盘不在入场区、未成交的次数 | 次 | 否 | — |
| `backtest.all_long_signals` / `long_confirmed` / `watch_long_setup` | 三个口径的成交统计：`trades`、`cumulative_return_pct`、`win_rate_pct` 与 `win_rate_95ci_pct`（Wilson）、`mean_return_95ci_pct`（Bootstrap）、`sharpe`、`max_drawdown_pct`、`profit_factor` | 笔 / % | 无成交时部分可空 | 唯一允许出现的概率类数字：胜率必须带 95% 区间与笔数；是已发生成交的统计，不是未来概率 |
| `backtest.buy_and_hold_return_pct` | 同期买入持有收益 | % | 否 | 与策略累计收益对比 |
| `backtest.rule_score_ic` | 规则分与未来 H 日收益的 Spearman 秩相关：`spearman`、`decisions`、`effective_independent_samples`（≈ 决策数 × 步长 / H）、`informative`（独立样本 ≥ 20 且 \|IC\| ≥ 0.05）、`verdict` | −1~1 / 个数 | `spearman` 可空 | 原文转述 `verdict`；`informative = false` 时说"规则分在该标的上只作描述" |
| `backtest.rule_score_buckets[]` | 按规则分 [0,38)、[38,52)、[52,65)、[65,100] 分层的决策日数、未来 H 日平均/中位收益、上涨占比 | 个 / % | 无样本的层为空 | 不对外转述（只供维护者诊断）；上涨占比是频率数字 |
| `backtest.action_forward_returns.*` | 各动作标签之后的未来 H 日平均/中位收益与上涨占比 | 个 / % | 无样本时为空 | 不对外转述（只供维护者诊断）；上涨占比是频率数字，不要说成上涨概率 |
| `backtest.veto_reason_counts` | 回放期间各否决编码出现次数 | 次 | 否（可为空对象） | — |
| `backtest.sample_trades[]` | 成交样例：入场/出场日与价、`outcome`（`TP_BARRIER` / `SL_BARRIER` / `TIME_BARRIER` 等）、`net_return` | — | 否 | — |
| `backtest.sample_size_warning` | 成交少于 30 笔时的提示 | 文本 | 是 | 有则原文转述 |
| `backtest.conformal_forecast_oos.*` | 正常波动范围的样本外覆盖：无重叠截面 `evaluated_cutoffs` 与覆盖率，以及与主报告一致的滚动全截面覆盖率 | 比例 | 否 | 与 `volatility_range.calibration_audit` 一起说明 |
