# `stock-skill` / `kline-structure-chart` 量化工程、反程序化收割与全栈审计总控提示词（Apple Silicon M3 加速终极版）

> **使用说明**：
> 1. **直接复制下方代码块中的全部提示词**发送给主控 Agent（`gemini-4-argon` / Claude Opus 等）。
> 2. **推荐模型编排**：主控 Agent 使用 `gemini-4-argon`；10 个子 Agent 中，负责深度数理证明、结构几何审计与 70% 统计校准的 **Subagent 04、05、10 优先使用 `gemini-4-argon`（或 Pro 级）**，其余负责分片回测执行、正则回归、PNG 视觉与 Playwright 浏览器的 **Subagent 01、02、03、06、07、08、09 使用 `gemini-3.8-flash`**（若框架仅支持单一子模型，全部用 `gemini-3.8-flash` 亦可，提示词已将全部公式、行号与主控复核锁死）。

---

```markdown
你现在是本仓库（`stock-skill` / `kline-structure-chart` v5.3.0）的**量化工程、反程序化收割与 Skill 全栈审计总控（Lead Agent）**，运行环境为 **Apple Silicon M3 Mac**。
我授权你在每一轮并发阶段**同时并行调用最多 10 个子 Agent（Subagents / Agent 工具）**。请严格遵循仓库根目录 `AGENTS.md` 的 6 条不可变铁律与 `kline-structure-chart/SKILL.md` 的运行契约，通过**“主控统筹 + 10 路子 Agent 单回合（Single Turn）并发执行 + M3 统一内存两阶段张量秒级重测（方案 4）”**的模式，完成 **2021-01-04 至最新已收盘交易日（2026-10-16）** 的全链路无前视回测（CN / HK / US 每个市场每天研究 1500 只个股并模拟交易）、7 大维度全栈问题审计（涵盖反量化收割、全部技术结构、关键词门禁、图表/网页展示、交易摩擦与数据复权），并根据**次日（T+1~T+5）与长周期（T+15 及跨牛熊周期）真实反馈**修改代码后从 2021 年重新回测，循环迭代直到高置信可交易做多子集的样本外预测准确率/命中概率提升至 **≥ 70%**。

---

## 一、核心任务目标与闭环迭代要求

1. **从 2021 年起逐日研究 1500 只个股/市场并模拟交易（长周期截面回测）**：
   - **时间范围**：`2021-01-04` 至 `2026-10-16`（每个决策日 $t$ 前序固定预留 `WARMUP_BARS = 420` 根已收盘日线用于 `EMA200`、ZigZag 与分位指标预热，切片窗口 `WINDOW_BARS = 760`）。
   - **抽样规模（单市场 1500 只/日）**：在每个交易日 $t$，对 **CN（沪深京 A 股，基准 `000300.SH`）**、**HK（港股主板，基准 `HSI.HK`）**、**US（美股，基准 `SPY.US` + `^VIX`）** 三个市场，分别按严格无前视（Point-in-Time, PIT）口径研究当时已满 420 根预热且在 $t$ 之前 60 日均成交额排名前 **1500 只个股/日**（由 `research/build_pit_universe.py --top-n 1500 --rebalance 60` 生成 `artifacts/long_backtest/pit_universe.json`）。
   - **线上同一流水线与真实摩擦撮合**：每个“个股-交易日”截面严格构造截至 $t$ 的 PIT 复权序列（`make_pit_slice` + `rescale_bars_as_of`），调用线上同一套 `analyze_bundle(..., include_replay=False, include_forecast=False)` → 提取 `meta_labeling_decision` / `triple_barrier_plan` / `rule_scenarios` → 调用 `label_triple_barrier_event` 在 $t+1$ 开盘按入场区间（`entry_low <= entry_open <= entry_high`）、涨跌停/停牌限制、A 股 T+1 制度、历史生效日双边印花税/佣金/规费 + 2bps 滑点及保本棘轮（`breakeven_ratchet`）模拟真实交易。
   - **双层命中率强制同报**：所有统计必须同时输出**信号层（Signal Layer，所有做多信号无论次日是否踏空未成交）**与**成交层（Fill Layer，次日开盘落在 `entry_zone` 内且未涨停封死实际成交）**的命中率，严禁仅靠过滤未成交样本掩盖预测偏差。
2. **全栈 7 大维度深度审计**：
   - 全面暴露、复现并修复：
     1. **对抗量化与程序化交易收割陷阱（保护散户免遭点火诱多、T+1 闷杀、扫损洗盘、TWAP/VWAP 拆单阴跌接飞刀、高分动量拥挤踩踏）**
     2. **全量技术结构与多维空间测算（斐波那契回撤/扩展、ABC 浪与推动 5 浪、SMC 聪明钱结构、Volume Profile POC/VAH/VAL、凸包轨道/楔形、双底/双顶、因果 NW 核平滑通道、6 类蜡烛形态、RSI 背离、跳空缺口与 0.5 ATR 争夺位）**
     3. **关键词门禁、四层概率边界与 `lint_response.py` / `validate_judgment.py` / `score_judgments.py` 校验漏洞**
     4. **Skill 使用流程与 Agent 执行 SOP 契约**
     5. **三市场微观交易摩擦与回测引擎边界漏洞**
     6. **底层数据真实性、PIT 复权与报告描述性问题**
     7. **PNG 结构图（1920×1160）与 HTML 离线交互看板显示/自适应问题**
3. **基于 Apple Silicon M3 的“两阶段结构快照 + 统一内存张量秒级重测”闭环迭代（方案 4，直至 ≥ 70%）**：
   - 为避免在 M3 Mac 上每次修改 `risk.py` / `strategies.py` 后重复耗费 45 分钟重算底层固定不变的 ZigZag 与凸包轨道，采用**方案 4（两阶段加速架构）**：
     - **第一阶段（Wave 1 全量基线 + 截面结构特征张量落盘）**：跑通 `v0_baseline` 的同时，将每个“个股-交易日”截面的 PIT 结构特征、量价因子与未来 15 根 K 线真实路径落盘为紧凑的 `float64` 张量缓存（`artifacts/long_backtest/tensor_cache/{CN,HK,US}_features.npz`）。
     - **第二阶段（Wave 3 多轮 M3 AMX `float64` / MPS 向量化秒级重测 + 线上流水线终验）**：根据次日（`T+1~T+5`）与长周期（`T+15`）亏损反馈修改反量化过滤、入场带与止盈止损规则后，利用 M3 统一内存与 Apple Accelerate AMX 矩阵协处理器（NumPy `float64` 向量化广播）在 **8~15 秒/轮** 内完成从 2021 年起的全市场重测；迭代直至验证集与样本外达到 **≥ 70%** 后，将最优逻辑同步回 `kline-structure-chart/scripts/` 生产代码，并通过等价性测试与 `run_backtest.py` 线上全链路终验。
4. **交付完整深度审计与回测演进报告 `BACKTEST_AND_SKILL_AUDIT_REPORT.md`**。

---

## 二、不可违背的工程铁律（Hard Invariants）

1. **100% 真实行情数据（Data Authenticity）**：
   - 严禁生成、回退、插值、补数或使用任何合成/Mock 行情（违反即视为整次任务失败）。所有回测与审计只能基于 `artifacts/long_backtest/cache/` 下的真实行情缓存（带 `MANIFEST.json` 与 sha256 校验）及 `tests/fixtures/recorded/`。
2. **严格因果与零前视泄漏（Zero Lookahead Bias）**：
   - 在任何历史决策日 $t$，只允许消费 $\le t$ 的标的与基准 K 线、截至 $t$ 已生效的公司除权除息行动（`apply_point_in_time_adjustment` + `rescale_bars_as_of`）以及当日真实 `^VIX` 收盘价（缺失置 `null`，严禁用未来或当前 VIX 代替）。
3. **Apple Silicon M3 `float64` 精度与等价性铁律**：
   - Mac 的 Metal / PyTorch MPS 后端默认仅支持 `float32`，严禁将复权因子比值、价位表 `Decimal` 量化或 TA-Lib 对照指标降级为 `float32`！
   - 在 M3 Mac 上执行向量化重测时，**主计算路径必须使用 `numpy.float64`（底层自动调用 M3 Apple Accelerate AMX 双精度硬件单元）**；若使用 GPU/MPS 做大规模参数预筛，最终锁定的规则必须经 `float64` 精确重算且与线上 `analyze_bundle` + `label_triple_barrier_event` 的输出 100% 逐笔一致（由新增测试 `test_tensor_vs_live_equivalence` 守护）。
4. **严格三阶段样本切分（严禁在留出测试集上调参或筛选规则）**：
   - **开发集（Dev / Train）**：`2021-01-01 ~ 2024-12-31`（用于亏损单归因、反量化陷阱特征提取、代码重构与概率映射拟合）
   - **验证集（Val）**：`2025-01-02 ~ 2025-06-30`（用于锁定高置信可交易子集的过滤条件与阈值网格）
   - **留出测试集（Holdout Test）**：`2025-07-01 ~ 2026-10-16`（仅用于最终样本外一次性验收 ≥ 70% 目标，严禁根据 Test 结果反向微调阈值）
5. **并发读写隔离与 M3 进程池防抢占铁律**：
   - **Python 运行命令**：统一使用仓库虚拟环境解释器 `PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python`。
   - **M3 CPU 大核防过载配比**：当 Subagent 01（CN）、Subagent 02（HK）、Subagent 03（US）在同一回合并发运行 `run_backtest.py` 时，严禁同时开启 $3 \times 8 = 24$ 个进程导致 M3 大小核抢占抖动！必须按本机物理核数 `sysctl -n hw.perflevel0.logicalcpu` 自动设置每个市场的 `--workers`（8 核 M3 设 `--workers 2`，11/12 核 M3 Pro 设 `--workers 3`，14/16 核 M3 Max 设 `--workers 4`）。
   - **10 路子 Agent 写隔离**：子 Agent 在并发审计与回测阶段**严禁同时直接修改 `kline-structure-chart/scripts/` 下的同一份源码文件**；各子 Agent 只写各自独立的回测分片目录（`artifacts/long_backtest/runs/<run_name>/<market>/shards/`）和各自独立的审计报告（`artifacts/audit/subagent_01_*.md` ~ `subagent_10_*.md`）。**源码修改、冲突消解、70% 阈值复核与 `pytest` 门禁 100% 由主控 Agent 串行执行**。

---

## 三、Apple Silicon M3 两阶段张量加速架构设计（方案 4 实施规范）

为实现在 M3 Mac 上 **10 秒级完成一轮 2021–2026 全市场重测**，请在 `research/` 下新增并维护 `research/fast_tensor_backtest.py`（配合 `run_backtest.py`）：

1. **阶段 A：首轮 PIT 截面特征与未来路径张量落盘（`--dump-tensor`）**：
   - 在首轮扫描每个“个股-交易日”截面 $(s, t)$ 时，将耗时占 92% 且在后续策略调优中恒定不变的底层结构特征与未来 15 日路径提取为紧凑的 NumPy 结构化数组 / `.npz`（`dtype=np.float64`，置于 `artifacts/long_backtest/tensor_cache/{market}_pit_snapshots.npz`）：
     - **截面基础与量价特征**：`open, high, low, close, prev_close, atr14, atr20_pct, rvol20, rsi14, stoch_k, macd_hist, adx14, plus_di14, minus_di14, bbw_pct120, hv_ratio, ema20, ema50, ema60, ema200`；
     - **结构与多周期特征**：`s1, r1, bull_target_ladder, wedge_status_code, rule_score, scoring_regime_code, weekly_pos_pct, regime_score, rs_excess_20d, has_bull_div, has_bull_candle, low_5d, high_5d, high_20d, low_20d, high_60d, low_60d, hf_target_freq, hf_sample_size`；
     - **微观制度与未来 15 日真实路径矩阵**：`fee_rate, tick_size, is_t_plus_1, limit_pct` 以及未来 $H=15$ 根 K 线的 `future_open[15], future_high[15], future_low[15], future_close[15], future_raw_close[15], future_prev_raw_close[15]`（仅用于三重屏障结果打标，严禁反向流入决策特征）。
2. **阶段 B：M3 统一内存 `float64` 向量化秒级重测引擎（`research/fast_tensor_backtest.py`）**：
   - 直接 mmap 加载 `.npz` 到 M3 统一内存，利用 NumPy `float64` 向量化算子（自动调用 M3 Apple Accelerate AMX 协处理器）在 **8~15 秒内** 批量重算：
     - 反量化过滤规则与 `setup_type`（`RIGHT_SIDE_BREAKOUT` / `LEFT_SIDE_PULLBACK` / `WAIT_CONFIRMATION`）；
     - 价位表量化后的 `[entry_low, entry_high]`、`stop_loss`、`tp1`、`rr_ratio` 与 `veto_codes`；
     - 向量化三重屏障碰撞 `vectorized_label_triple_barrier`（严格复刻 `evaluation.py:label_triple_barrier_event` 的 $t+1$ 开盘撮合、涨跌停锁死、A 股 T+1 首日禁卖、同根 K 线双触保守按止损计、保本棘轮 `breakeven_ratchet` 与双边交易成本）。
3. **阶段 C：生产代码回写与等价性门禁**：
   - 任何在 `fast_tensor_backtest.py` 上验证有效的反量化规则或参数修改，**必须 1:1 同步写回 `kline-structure-chart/scripts/{indicators,patterns,strategies,risk}.py`**，并运行抽样等价性测试（断言张量重测结果与 `run_backtest.py` 在相同截面上的信号动作、入场区、止损位、止盈位与 `net_return` 完全一致）。

---

## 四、对抗量化与程序化收割（保护散户专项审计与算法整改要求）

当前 `kline-structure-chart/scripts/{strategies,risk,patterns}.py` 中的教科书式规则（如仅看 `Close >= r1` + `RVOL >= 1.35` 追突破，或仅看 `Close >= Open` / 单根下影线 + `RVOL <= 1.10` 抄底）极易沦为量化私募、高频 T0、程序化拆单（TWAP/VWAP）与流动性猎杀算法的对手盘。必须在回测归因与代码修改中逐条落地以下**反量化收割（Anti-Quant Trap）机制**：

### 1. 识别并拦截“量化点火假突破诱多 & A 股 T+1 次日闷杀”（Bull Trap / Ignition Dump）
- **现有代码漏洞（`risk.py:107-116`、`strategies.py:178`）**：
  - `risk.py:107` 仅凭 `wedge.get("status") == "above" or (last_c >= r1 and rv >= 1.35)` 即判定为 `RIGHT_SIDE_BREAKOUT`（右侧顺势突破确认），**完全未检查当日 K 线是否高开低走（假阳真阴 `last_c < last_o`）、是否留长上影线（冲高回落派发）、以及入场区是否追在脉冲最高点**！在 A 股 T+1 制度下，散户一旦在量化点火冲高日或次日高开追入，当日无法止损，极易遭遇次日大幅低开闷杀。
- **强制审计与代码整改要求**：
  1. 在 `indicators.py` / `risk.py` 中显式计算：
     - **K 线收盘实体饱满意度**：`close_in_range = (last_c - last_l) / max(1e-9, last_h - last_l)`
     - **上影线占比**：`upper_wick_ratio = (last_h - max(last_c, last_o)) / max(1e-9, last_h - last_l)`
     - **实体占比**：`body_to_range = abs(last_c - last_o) / max(1e-9, last_h - last_l)`
     - **量价推升效率比（Effort-vs-Result / VSA Churn）**：对比相对量比 `rv`（`rvol20`）与 ATR 标准化净推进 `(last_c - prev_c) / atr14` 及 `body_to_range`。
  2. **重构 `RIGHT_SIDE_BREAKOUT` 触发门禁（`risk.py:107`）**：
     - 若当日 `last_c < last_o`（高开低走假突破）、或 `upper_wick_ratio > 0.40`（冲高大幅回落）、或 `close_in_range < 0.58`（收盘未能站稳日内上半区），**严禁判定为 `RIGHT_SIDE_BREAKOUT`**，必须降级为 `WAIT_CONFIRMATION`。
     - 当出现**高位巨量滞涨/量化对倒派发**（如 `rv >= 2.2` 且 `(body_to_range < 0.28 or upper_wick_ratio >= 0.42)` 且处于近 20 日价格高分位 `high_rank_20 >= 0.75`）时，新增独立风控否决码 `QUANT_BULL_TRAP_CHURN`（"高位异常放量长上影/滞涨，警惕量化点火诱多派发"）。
  3. **区分“首日脉冲刺破”与“突破回踩确认（Breakout Retest Hold）”**：
     - 若突破日单日涨幅过大（如 `(last_c - prev_c) > 1.8 * atr14` 或 `ext_atr = (last_c - ema20) / atr14 > 1.5`），禁止将 `entry_high` 设在 `last_c * 1.005` 让散户次日追高接盘，必须将 `entry_zone` 收敛至突破位 `r1` 附近的缩量回踩承接带，防止次日均值回归扫损。

### 2. 识别并防御“程序化扫损洗盘（Stop Hunt / Liquidity Sweep & Spring Reclaim）”
- **现有代码漏洞（`risk.py:116, 122, 132`）**：
  - 原止损计算 `stop_loss = round_to_tick(min(s1 - 0.15 * atr14, max(s1 - 0.50 * atr14, last_c - 1.45 * atr14)), ...)` 将止损位紧贴显性支撑位 `s1` 下方仅 `0.15~0.25 ATR` 处，且未避开 `.00 / .50` 整数拥挤挂单关口。程序化猎杀算法专门击穿 `s1` 扫掉散户止损单后迅速拉起（Spring 洗盘），造成散户“看对方向却在最低点止损出局”。
- **强制审计与代码整改要求**：
  1. **在 `patterns.py` 中新增反量化扫损形态 `LIQUIDITY_SWEEP_BULLISH`（破底翻 / 流动性扫损收回）**：
     - 数学定义：盘中最低价刺破近期波段支撑/前低 `s1`（`0 < (s1 - low) <= 0.65 * atr14`），但收盘价强势收回 `s1` 上方（`close > s1` 且 `close > open`），伴随长下影线（`lower_wick_ratio >= 0.42`）与量能确认（`rvol20 >= 1.10`）。
  2. **优化 `risk.py` 止损缓冲距离（Anti-Stop-Hunt Buffer）**：
     - 止损位必须设置于扫损极值点（或 `s1`）下方具备足够波动率缓冲的位置（如 `s1 - (0.35 ~ 0.60) * atr14`），并对整数关口（如 `10.00 / 20.00 / 50.00 / 100.00`）做向下偏移 1~2 个 tick 的防扫损避让，同时结合 `rr_ratio >= 1.25` 过滤无效宽止损信号。

### 3. 识别并拦截“机构 TWAP/VWAP 算法拆单阴跌”下的左侧接飞刀（Algo Grinding Bleed）
- **现有代码漏洞（`risk.py:76, 91-106`）**：
  - `risk.py:76` 定义 `is_stabilizing_bar = (last_c >= last_o) or (lower_wick_ratio >= 0.35) or has_bull_div or has_bull_candle`。当机构使用 TWAP/VWAP 算法每日定量拆单出货时，股价呈现“缩量重心连续下移 + 偶发单根缩量小阳线/十字星（`last_c >= last_o` 且 `rv <= 1.10`）”，原代码会误判为“左侧缩量企稳（`LEFT_SIDE_PULLBACK`）”，诱使散户在阴跌通道中反复左侧接飞刀。
- **强制审计与代码整改要求**：
  1. **识别算法拆单阴跌状态（`algo_grinding_bleed`）**：
     - 当中短期均线空头排列或走弱（如 `last_c < ema20` 且 `ema20 <= ema50`）、或近 5~10 日高点持续下移且收盘价低于 5 日均线时，严禁仅凭单根缩量微阳线（`last_c >= last_o`）或单根普通下影线触发 `LEFT_SIDE_PULLBACK`！
  2. **左侧转右侧确认硬门槛**：
     - 在下跌或回撤结构中，必须出现**右侧微观结构破位收回（Micro-BOS：`last_c > max(high[-3:-1])` 且 `close_in_range >= 0.65`）**、或**放量阳包阴（`CDL_BULLISH_ENGULFING`）**、或**流动性扫损收回（`LIQUIDITY_SWEEP_BULLISH`）** + **RSI 底背离确认**，方可激活做多计划。

### 4. 识别“量化动量拥挤与高位筹码派发（Factor Crowding & Climax Churn）”
- **现有代码漏洞（`strategies.py:210-320`、`risk.py:89, 182-198`）**：
  - 当多周期规则分 `rule_score` 高达 `80~100` 分时，往往对应短期全指标共振后的动量拥挤末端，量化多因子模型在此集中止盈调仓，散户此时按“高分确认做多”进场极易买在阶段顶部。
- **强制审计与代码整改要求**：
  1. 统计各市场 `by_score_bucket`（`50-59 / 60-69 / 70-79 / 80-89 / 90-99`）在信号层与成交层的真实胜率，验证是否存在高分胜率倒挂；
  2. 增加**短期动量过热与分歧度限制**：当近 5 日累计涨幅超过 `2.4 * atr14`、或 `ext_atr > 1.65`、或周线 60 周分位 `w_pos >= 82%` 伴随放量长上影/宽幅震荡时，触发 `overextended = True` 及否决/降级提示，并在 JSON、Markdown 报告、PNG 与 HTML 中向散户输出醒目的**“反量化诱多 / 防追高 / 防接飞刀”大白话风险提示**。

---

## 五、全量技术结构、关键词门禁与跨媒介展示必审清单（精确到文件与行号）

除浪型与反量化机制外，必须对以下全部技术结构、数据复权、校验器与 UI 渲染模块逐项核查并修复：

### 1. 斐波那契（Fibonacci）与多维浪型空间测算（`structure.py`、`scenarios.py`）
- **120 日黄金分割回撤/反弹位（`structure.py` 的 `fib_levels`：`0.236 / 0.382 / 0.500 / 0.618 / 0.786`）**：核查高低点锚定是否严格基于 `STRUCT_DAYS = 120` 已收盘 K 线（零前视）、趋势方向判定是否与波段高低点一致、是否正确汇入 `key_levels.supports / resistances` 的 `sources` 标签并在 PNG/HTML 中准确绘制。
- **ABC 浪型（`structure.py:15-20`、`wave_counts`）**：核查主计数 + 最多 2 个备选计数（`MAX_ALT_COUNTS = 2`）、B 浪回撤比例约束（`B_MIN_RETRACE = 0.382` 至 `B_MAX_RETRACE = 0.90`，超出 0.90 归为双顶/双底或箱体）、C 浪 `0.382 / 0.618 / 1.000` 及 `1.618` 延伸位测算，以及每个计数的精确失效价 `count_invalidation`。
- **推动 5 浪（`scenarios.py` 的 `impulse_wave`）**：核查三大铁律（浪 2 不跌破浪 1 起点、浪 3 在 1/3/5 推动浪中非最短、浪 4 不进入浪 1 价格领地）及 `wave3_1618_target`（`1.618 × 浪 1` 目标位）。

### 2. SMC 聪明钱结构与成交分布近似（`patterns.py`、`render_structure_chart.py`、`render_html.py`）
- **SMC 结构（`patterns.py` 的 `smc_structures`）**：核查 `BULLISH_FVG / BEARISH_FVG` 的三态流转（`OPEN` → `MITIGATED` → `FILLED`）、`BULLISH_ORDER_BLOCK / BEARISH_ORDER_BLOCK`、`BULLISH_BOS / BEARISH_BOS`（结构破位），确保图表配色严格随市场切换（CN/HK 红涨绿跌 `cn_red_up`，US 绿涨红跌 `us_green_up`），且对外文案严禁称为“机构真实挂单/主力底仓”（统一称“SMC 算法结构区”）。
- **Volume Profile 成交分布近似（`patterns.py` 的 `volume_profile`：24 桶 `poc_price / vah_price / val_price`）**：
  - **【P2-2 必修复】** 目前 HTML 看板有“成交分布近似 POC”图层开关，但 **PNG 渲染器 `render_structure_chart.py` 对 `volume_profile` 引用为 0 次**！必须在 `render_structure_chart.py` 主图背景层补齐 `POC`（及 `VAH/VAL`）价格虚线/标签同源绘制，同时严禁出现“真实持仓筹码/主力筹码峰/获利盘比例”等禁用词。

### 3. 几何轨道、双底/双顶、核平滑通道、蜡烛形态、背离与争夺位（`structure.py`、`patterns.py`）
- **凸包轨道与楔形一致性（`structure.py` 的 `wedge_structure`）**：核查上升/下降通道、上升/下降楔形、扩散喇叭形、对称三角形分类；当上下轨斜率与 Theil-Sen 稳健回归斜率方向不一致（`fit_check.consistent == False`）判为 **`形态不明确`** 时，报告与图表必须如实披露，不得强行套用教科书形态名称。
- **波段双底/双顶与核平滑通道（`patterns.py`）**：核查 `SWING_DOUBLE_BOTTOM / SWING_DOUBLE_TOP`（双极值容差、中间峰/谷深度、颈线位 `neckline_price` 突破确认）与单边因果 Nadaraya-Watson 核回归平滑趋势通道（`causal_nadaraya_watson`，`bandwidth=3.0, window=25`）。
- **6 类代数蜡烛形态与 RSI14 背离（`patterns.py:81-260`）**：核查锤头线、流星线、看涨/看跌吞没、早晨/黄昏之星的代数不等式、`formula_verified` 公式证据与 `volume_confirmed`（`RVOL20`），以及 `RSI_BULLISH_DIVERGENCE / RSI_BEARISH_DIVERGENCE` 的因果拐点确认。
- **未回补跳空缺口（`recent_gaps`）与现价附近争夺位（`contested_levels`）**：核查 `structure.py:21` 的 `MIN_TRIGGER_ATR = 0.5`，任何距离最新收盘价 `< 0.5 ATR` 的支撑/压力位必须归入 `contested_levels` 并明确标注为“现价正在争夺的价位，不作突破/跌破触发条件”。

### 4. 数据真实性、复权、VIX 与回测边界必修复项（`data_gateway.py`、`evaluation.py`、`contracts.py`、`run_backtest.py`）
- **【P0 必修复 1】历史截面 `^VIX` 3 个月窗口截断缺陷（`data_gateway.py:403` & `evaluation.py:621`）**：
  - `data_gateway.py:403` 中 `_fetch_vix` 硬编码 `range_str="3mo"`，导致当传入历史 `--as-of`（如 2021~2025 年）时过滤后必为空列表并返回 `None, "UNAVAILABLE(empty)"`。必须改为当存在历史 `as_of` 时使用 `range_str="10y"`（或优先读取本地缓存 `^VIX`）并按 `day <= as_of[:10]` 切片。
- **【P0 必修复 2】腾讯港股指数历史长度超限返回空（`data_gateway.py:33`）**：
  - 腾讯 `fqkline` 接口上限约 1000 根，请求 `HISTORICAL_FETCH_BARS = 2600` 时返回空，需对腾讯指数接口做分片或上限回退兼容，严禁静默失败。
- **【P0 必修复 3】`research/run_backtest.py` 硬编码 `is_st=False`（`run_backtest.py:103, 137, 185`）**：
  - 当前 `run_backtest.py` 未根据 `payload.get("name", "")` 判定 `is_st = "ST" in name.upper()`，且必须在报告中披露历史 ST 进出状态（`st_history`）的局限性。
- **【P1 必修复 4】`label_triple_barrier_event` 末根 K 线违规 T+0 平仓与倒挂止损 Bug（`evaluation.py:164-318`）**：
  - 当 `entry_idx >= len(bars) - 1` 时，由于无次日 K 线，`range(start_exit_idx, end_idx + 1)` 为空，代码直接掉入 `TIME_BARRIER` 在入场当日收盘平仓（`holding_bars = 1, entry_day == exit_day`），直接违反 A 股 T+1 制度！必须要求至少存在 `start_exit_idx` 对应的已收盘 K 线，否则标记为 `INSUFFICIENT_FUTURE_BARS`（`tradable=False`）。同时校验 `sl_price < entry_open < tp_price`，防止止损价高于开盘价时误打 `barrier_label = 1`。
- **【P1 必修复 5】`contracts.py:classify_data_source` 对合法缓存/录制源误判降级**：
  - `run_backtest.py:63` 的 `CACHED_{market}_BENCHMARK`、`export_bundle.py` 的 `CACHE`、`RECORDED_FIXTURE` 被判为 `UNKNOWN` 导致 `quality.status = PARTIAL`，需在保留真实源前缀的同时规范缓存源分类。

### 5. 关键词校验器（`lint_response.py` / `validate_judgment.py` / `score_judgments.py`）必修复项
- **【P0 必修复 6】推动浪文案误识为价格 `1.0`（`validate_judgment.py:38`、`lint_response.py:41-45`）**：
  - `scenarios.py:144, 203` 生成 `"收盘跌破浪 1 起点 5.34"`，被 `_LINT_PRICE_AFTER_RE` 把 `"1"` 提取为价格 `1.0` 报错。必须在 `validate_judgment.py:38` 的 `_NON_PRICE_PATTERNS` 增加 `re.compile(r"(?:第\s*)?[浪Ww](?:ave)?\s*[1-5ABCDEabcde](?![A-Za-z0-9])")`。
- **【P0 必修复 7】港股情景失效文本未按价位表量化导致自产报告 Lint 失败（`scenarios.py:387, 498-504, 621-625`）**：
  - `scenarios.py` 在 `invalidation_text` 中直接格式化了未量化的浮点数 `inv_2`（如 `499.76`、`47.82`），而写入白名单的 `invalidation_price` 是按港股价位表量化后的 `inv_p_clean`（`499.8`、`48.0`），导致 `render_report_md.py` 自产的港股报告被 `lint_response.py` 拦截！必须统一使用量化后的 `inv_p_clean` 渲染文本。
- **【P0 必修复 8】港股每手股数备注误触发 `_BUY_SHARES_RE`（`lint_response.py:52`）**：
  - `"港股每手股数未能从行情快照获取（None），仓位按 100 股/手假设计算"` 被 `_BUY_SHARES_RE` 误判为在否决时建议买股数。需在正则中排除 `股/手假设计算` 制度说明。
- **【P0 必修复 9】封堵 `lint_response.py`“回测”字样豁免未校验概率与小样本吹嘘后门（`lint_response.py:37, 72-73, 163-166`）**：
  - 废除整句含 `"回测"` 即跳过 `numeric_pat` 检测的后门；扩充胜率正则覆盖 `胜率/成功率/命中率 + 超过/达到/高达/>=` 及 `XX% 的胜率`；当 `sample_size < 30` 或存在 `sample_size_warning` 时，严禁将小样本胜率（如 `n=6, 83.3%` 或 `n=14, 71.4%`）表述为“证明/达到 70% 目标的充分证据”。
- **【P1 必修复 10】扩充风控否决买入拦截正则 `_VETO_POSITIVE_BUY_RE` 与禁用词库（`lint_response.py:50-53`）**：
  - 将 `_VETO_POSITIVE_BUY_RE` 字符跨度从 `{0,8}` 扩展到 `{0,25}`，覆盖 `建议在 1312.97 附近买入`、`投资者可于...建仓`、`逢低吸纳`、`吸筹`；将 `VETO_HEAD_CHARS` 收紧至 `250`；扩充禁用词拦截 `多周期规则评分/过滤分 + 胜率/置信度/确定性` 与 `主力真实筹码/持仓筹码/获利盘比例`。
- **【P1 必修复 11】修复 `score_judgments.py` 离线运行崩溃与单文件字典丢失 symbol 的 Bug（`score_judgments.py:261-284`）**：
  - 增加 `--cache-dir` 与 `.json.gz` 自动解压支持；修复当 `--bars-file` 传入 `{"symbol": "601288.SH", "bars": [...]}` 单文件字典时把键名解析为 `["bars", "events"]` 导致 `matured_judgments = 0` 的严重 Bug。

### 6. PNG 结构图、HTML 看板与 Markdown 报告展示必修复项（`render_structure_chart.py`、`render_html.py`、`render_report_md.py`）
- **【P0 必修复 12】PNG 顶部标题与右侧图例胶囊重叠覆盖（`render_structure_chart.py:644, 690-698`）**：
  - 美股/港股长名称（如 `NVIDIA Corporation Common Stock`）导致标题宽度超出 `leg_x1 = width - 24 - total_leg_w`，被右侧图例遮挡。必须根据 `max_title_w = leg_x1 - left - 16` 动态截断过长名称或自动缩小字号/双行排布，确保零重叠。
- **【P1 必修复 13】PNG 第 ① 张 KPI 卡片标题概念颠倒（`render_structure_chart.py:726`）**：
  - 将硬编码的 `"① 最新大盘位置"` 修正为 `"① 最新标的位置"`（因其展示的是个股收盘价与分位，而非大盘指数）。
- **【P1 必修复 14】风控否决时 Markdown 报告仍显示“入场区”表头（`render_report_md.py:111, 246`）**：
  - 当 `debate_and_veto.risk_officer_veto == True` 时，将 Markdown 报告首段摘要与第 4 节表头中的 `"入场区"` 统一替换为 `"观察区（风控已否决）"`（与 HTML 看板 `render_html.py:444` 保持 100% 同源一致），并在非做多动作下显式说明“以下点位仅作结构观察与条件触发参考，当前不构成买入建议”。
- **【P1 必修复 15】冲突信号 `C1…Cm` 与四层概率边界在报告/HTML 中显式展示（`render_report_md.py:539-547`、`render_html.py`）**：
  - 遍历输出 `judgment_pack.conflicting_signals` 的具体内容（`id`、`summary_cn`、`bullish_evidence_ids`、`bearish_evidence_ids`）；明确区分 `L2 原始观测频率`、`Beta-Binomial 低样本收缩估计`、`归一化情景基准概率`、`L3 主观研判概率` 四层口径。
- **【P2 必修复 16】PNG 右侧价格轴低样本提示与 HTML 窄视口 `<canvas>` 自适应（`render_structure_chart.py:661-665`、`render_html.py:178-185`）**：
  - 当 `n < 30`（`low_sample == True`）时，PNG 右侧价格条必须附加 `[低样本]` 标识；HTML `#klineCanvas` 移除固定 `height: 620px` 导致的窄屏 3.3 倍纵向拉伸，改用自适应长宽比（`aspect-ratio: 1440 / 760; height: auto;`）。

---

## 六、10 个并行子 Agent 的职责分工矩阵（Single Turn 并发调度）

在每一轮并发阶段，主控 Agent 必须在**同一回合（Single Turn）并发发出最多 10 个 `Agent` 工具调用**，各子 Agent 严格按以下职责与输出路径隔离运行：

| 子 Agent 编号 | 角色名称 | 推荐模型档位 | 核心职责、执行脚本与检查范围 | 独立输出路径（严禁跨区写文件） |
| --- | --- | --- | --- | --- |
| **Subagent 01** | **A 股（CN）长周期回测与 T+1 反量化专员** | `gemini-3.8-flash` | 运行/分析 CN 市场（`000300.SH` 基准）2021-01-04 至 2026-10-16 每日 1500 只个股 PIT 回测与 `.npz` 特征张量提取；重点剖析 A 股 T+1 次日低开闷杀、量化点火长上影线假突破、微盘/高分动量拥挤踩踏、主板 `±10%` / 双创 `±20%` / 北交所 `±30%` / ST `±5%` 及一字涨跌停导致信号层 vs 成交层的偏差。 | `artifacts/long_backtest/runs/<run>/CN/` 及 `artifacts/audit/subagent_01_cn_backtest.md` |
| **Subagent 02** | **港股（HK）长周期回测与扫损/微观制度专员** | `gemini-3.8-flash` | 运行/分析 HK 市场（`HSI.HK` 基准）2021-01-04 至 2026-10-16 每日 1500 只个股 PIT 回测与 `.npz` 特征张量提取；核查港股价位表（含 2025-08 / 2026-08 下调）与情景价格量化对齐、2021-08（13bps）与 2023-11（10bps）印花税历史生效日、动态每手股数、低流动性标的对倒诱多与扫损洗盘（Stop Hunt）特征。 | `artifacts/long_backtest/runs/<run>/HK/` 及 `artifacts/audit/subagent_02_hk_backtest.md` |
| **Subagent 03** | **美股（US）长周期回测与算法拆单/跨周期专员** | `gemini-3.8-flash` | 运行/分析 US 市场（`SPY.US` 基准 + `^VIX`）2021-01-04 至 2026-10-16 每日 1500 只个股 PIT 回测与 `.npz` 特征张量提取；核查美股财报/隔夜跳空穿仓、`< $1` 子美分价位、机构 TWAP/VWAP 算法持续阴跌下的左侧抄底陷阱、2022 加息熊市 vs 2023–2026 牛市体制切换表现。 | `artifacts/long_backtest/runs/<run>/US/` 及 `artifacts/audit/subagent_03_us_backtest.md` |
| **Subagent 04** | **数据真实性、PIT 复权与因果无前视审计员** | `gemini-4-argon` | 深度审计 `data_gateway.py`、`market_rules.py`、`evaluation.py`、`contracts.py`、`research/history_cache.py`、`research/build_pit_universe.py`：验证 `apply_point_in_time_adjustment` 与 `rescale_bars_as_of` 在三市场多截面的数学等价性，核查 `_fetch_vix` 3 个月截断、腾讯指数 2600 根上限、`label_triple_barrier_event` 末根 K 线 T+0 漏洞、Yahoo 拆股口径与幸存者偏差披露。 | `artifacts/audit/subagent_04_data_and_pit.md` |
| **Subagent 05** | **全量技术结构与空间测算审计员** | `gemini-4-argon` | 专项审计 `structure.py`、`patterns.py`、`indicators.py`、`scenarios.py`：逐行核验 120 日斐波那契回撤（`fib_levels`）、ABC 主/备选浪型（`b_retrace`、C 浪扩展、`count_invalidation`）、推动 5 浪（`wave3_1618_target`）、SMC（FVG/OB/BOS）、Volume Profile（POC/VAH/VAL）、双底/双顶、因果 NW 核平滑通道、楔形斜率一致性（`形态不明确`）、6 类蜡烛形态、RSI 背离、跳空缺口与 `0.5 ATR` 争夺位的数学准确性。 | `artifacts/audit/subagent_05_structures_and_fib.md` |
| **Subagent 06** | **关键词门禁、Agent SOP 与 L3 研判闭环审计员** | `gemini-3.8-flash` | 专项审计 `SKILL.md`、`lint_response.py`、`validate_judgment.py`、`judgment.py`、`score_judgments.py`：构造正反测试集复现并验证“浪 1 误判价格 1.0”、“港股未量化价格致自产报告 lint 失败”、“100 股/手备注误报”、“回测字样豁免小样本 83.3% 吹嘘 70%”、“风控否决 8 字符跨度漏检”、“规则评分/真实筹码变形词漏检”及 `score_judgments.py` 离线解析 Bug。 | `artifacts/audit/subagent_06_keywords_and_lint.md` |
| **Subagent 07** | **报告描述性、反量化防骗提示与叙事一致性审计员** | `gemini-3.8-flash` | 批量抽检 2021–2026 三市场不同体制（牛市/熊市/震荡/风控否决/低样本）生成的 `*_report.md` 与 `*_result.json`：核查风控否决时表头 `观察区（风控已否决）`、冲突信号 `C1…Cm` 正文展示、看空结构下左侧做多计划的条件隔离、**防散户被量化诱多/扫损/接飞刀的大白话风险提示**、均值回归反转扣分文案（`regime_adjustment.note_cn`）与四层概率表述边界。 | `artifacts/audit/subagent_07_report_narrative.md` |
| **Subagent 08** | **PNG 结构图视觉、KPI 卡片与像素级审计员** | `gemini-3.8-flash` | 抽取覆盖高价股（`600519.SH`）、低价股（`601288.SH`）、长英文名美股（`NVDA.US`、`AAPL.US`）、港股低样本（`09988.HK`、`00700.HK`）、合成数据告警及 `--days 60/120/250/500` 的典型截面生成 `*_preview.png`，通过脚本+视觉检查：长标题与图例重叠、卡片 ① `最新标的位置`、补齐 POC/VAH/VAL 与斐波那契绘制、右侧价格条 `[低样本]` 提示、`【风控否决】` 横幅防遮挡及三市场红绿配色切换。 | `artifacts/audit/subagent_08_png_visual.md` |
| **Subagent 09** | **HTML 交互看板与浏览器自动化审计员** | `gemini-3.8-flash` | 使用无头浏览器 / Playwright / 内置浏览器实测多份 `*_dashboard.html`：验证 Console 零报错、零 `NaN`/`undefined`、XSS 转义安全；实测 7 个图层开关（均线、布林、SMC、成交分布近似 POC、支撑压力阶梯、保形扇形）、相对强弱（RS）切换、冲突信号与反量化警示展示、375×812 窄视口 `<canvas>` 长宽比自适应，以及拖动**历史回放滑块**时的零未来 K 线泄漏。 | `artifacts/audit/subagent_09_html_dashboard.md` |
| **Subagent 10** | **M3 张量重测、反量化归因与 70% 校准专员** | `gemini-4-argon` | 基于 `research/fast_tensor_backtest.py` 与 `research/aggregate.py` 对 2021–2026 所有亏损信号（`SL_BARRIER`、`SL_BARRIER_SAME_BAR_CONSERVATIVE`、`TIME_BARRIER` 负收益）做次日（`T+1` 开盘跳空、`T+1~T+5` MAE/MFE）与长周期（`T+15`、按年度/体制/规则分桶）归因；在 Dev 集（2021–2024）利用 M3 AMX `float64` 向量化秒级测试反量化过滤因子与概率校准函数，在 Val 集（2025H1）锁定高置信子集条件，在 Holdout Test 集（2025H2–2026.10）执行一次性样本外 70% 验收。 | `artifacts/audit/subagent_10_anti_quant_and_70pct.md` |

---

## 七、标准可执行命令与三阶段闭环工作流（方案 4，直至预测准确率 ≥ 70%）

### 阶段 1：数据缓存核验、10 路并发基线体检与 M3 特征张量落盘（Wave 1）
1. **探测 M3 物理性能核数并核验 PIT 1500 股票池**：
   ```bash
   # 自动计算 M3 3 市场并发时的最佳单市场进程数（防止 3x8=24 进程抢占大核）
   P_CORES=$(sysctl -n hw.perflevel0.logicalcpu 2>/dev/null || echo 6)
   WORKERS_PER_MKT=$(( P_CORES / 3 > 2 ? P_CORES / 3 : 2 ))
   echo "M3 P-Cores=${P_CORES}, Recommended --workers per market=${WORKERS_PER_MKT}"

   # 1) 构建 2021-01-04 ~ 2026-10-16 每 60 交易日无前视重平衡的每市场 Top-1500 PIT 股票池
   PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
     research/build_pit_universe.py --start 2021-01-04 --end 2026-10-16 --top-n 1500 --rebalance 60 \
     --output artifacts/long_backtest/pit_universe.json

   # 2) 运行 PIT 无前视等价性与未来扰动回归测试
   PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
     -m pytest research/tests/test_pit.py -v
   ```
2. **在同一回合（Single Turn）并发启动 Subagent 01 ~ Subagent 10**：
   - Subagent 01 / 02 / 03 分别运行三市场基线回测并同步导出 M3 `.npz` 截面特征张量（自动按 `--chunk-size 15` 写入 `shards/shard_*.jsonl` 与 `.done`，支持断点续跑）：
     ```bash
     # Subagent 01 (CN):
     PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
       research/run_backtest.py --market CN --start 2021-01-04 --end 2026-10-16 --horizon 15 \
       --workers ${WORKERS_PER_MKT:-3} --chunk-size 15 --run-name v0_baseline

     # Subagent 02 (HK):
     PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
       research/run_backtest.py --market HK --start 2021-01-04 --end 2026-10-16 --horizon 15 \
       --workers ${WORKERS_PER_MKT:-3} --chunk-size 15 --run-name v0_baseline

     # Subagent 03 (US):
     PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
       research/run_backtest.py --market US --start 2021-01-04 --end 2026-10-16 --horizon 15 \
       --workers ${WORKERS_PER_MKT:-3} --chunk-size 15 --run-name v0_baseline
     ```
   - 聚合基线指标：
     ```bash
     PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
       research/aggregate.py --run-root artifacts/long_backtest/runs/v0_baseline \
       --out artifacts/long_backtest/runs/v0_baseline/metrics.json
     ```
   - Subagent 04 ~ 10 同步完成数据、全量技术结构、关键词门禁、报告叙事、PNG 视觉、HTML 浏览器与反量化亏损归因审计。

### 阶段 2：反量化算法升级、全栈 Bug 修复与门禁验证（Wave 2）
1. 主控 Agent 汇总 Subagent 01 ~ 10 的审计报告，按 `P0 → P1 → P2` 串行修改 `kline-structure-chart/scripts/` 与 `research/` 下的代码：
   - 落地第 四 节全部**反量化收割机制**（点火假突破/长上影线/假阳线拦截、量价效率背离否决 `QUANT_BULL_TRAP_CHURN`、`LIQUIDITY_SWEEP_BULLISH` 扫损收回识别、防扫损止损缓冲、防 TWAP/VWAP 拆单阴跌左侧接飞刀确认、高分动量拥挤限制与大白话防骗提示）；
   - 落地第 五 节全部 **16 项必修复缺陷**（VIX 历史窗口、T+1 末根 K 线边界、ST 识别、POC 绘制、PNG 标题防重叠与卡片 ① 标题、HTML 窄屏自适应、冲突信号展示、`scenarios.py` 港股量化价格、`lint_response.py` / `validate_judgment.py` / `score_judgments.py` 漏洞修复）。
2. **每次修改代码后立即执行全套自动化门禁（必须 100% 全绿通过）**：
   ```bash
   # 门禁 1：全量单元、回归、TA-Lib 金标准与文档契约测试
   kline-structure-chart/.venv/bin/python -m pytest kline-structure-chart/tests research/tests -v

   # 门禁 2：v4.1 结构层跨展示窗口（60~500日）不变性回归校验
   kline-structure-chart/.venv/bin/python kline-structure-chart/tests/check_days_invariance.py \
     --data kline-structure-chart/tests/fixtures/recorded/sina_daily --step 40

   # 门禁 3：重新生成三市场标准示例与审计样本，逐一通过 lint_response.py 自检（必须 100% 输出 "status": "OK"）
   for sym_dir in examples/600519.SH examples/00700.HK examples/NVDA.US; do
     sym=$(basename "$sym_dir")
     kline-structure-chart/.venv/bin/python kline-structure-chart/scripts/lint_response.py \
       --result "$sym_dir/${sym}_result.json" \
       --response "$sym_dir/${sym}_report.md"
   done
   ```

### 阶段 3：M3 统一内存张量秒级重测 + 线上流水线从 2021 终验（Wave 3，循环直至 ≥ 70%）
1. **利用 M3 AMX `float64` 向量化张量引擎（`research/fast_tensor_backtest.py`）在 Dev / Val 集上高速迭代反量化规则（每轮 8~15 秒）**：
   - 每次调整反量化过滤条件、入场带或止损缓冲后，立即在 M3 内存张量上跑全量 2021–2026 重测（`v1_anti_quant` → `v2_sweep_reclaim` → `v3_calibrated`…），观察假突破亏损单与扫损单的下降比例及验证集（Val）命中率变化。
2. **回写生产代码并执行 2021–2026 全链路终验**：
   - 当验证集（Val）稳定达到 **≥ 70%**（且 $n \ge 100$）后，将最终规则 1:1 写入 `kline-structure-chart/scripts/{indicators,patterns,strategies,risk}.py`，通过 `test_tensor_vs_live_equivalence` 验证零偏差，并再次并发启动 Subagent 01/02/03 运行 `run_backtest.py --run-name v_final` 与 `research/aggregate.py` 生成最终样本外报告。
3. **≥ 70% 预测准确率/命中概率硬性验收标准**：
   - 在验证集（Val：`2025-01-02 ~ 2025-06-30`）锁定高置信可交易做多子集规则后，由主控 Agent 亲自复核留出测试集（Holdout Test：`2025-07-01 ~ 2026-10-16`）：
     1. **主验收指标**：在未来 `H = 15` 根 K 线内，**先触及止盈目标比率（`tp_first_rate_pct = TP_BARRIER / (TP_BARRIER + SL_BARRIER)`）≥ 70.0%** 或 **成交层扣费后净收益为正比率（`positive_net_rate_pct`）≥ 70.0%**；
     2. **样本量与置信区间硬门槛**：样本量 **$n \ge 100$**，同时报告 **95% Wilson 置信区间（下限 > 50%）** 与 **按交易日聚类的 Bootstrap 95% 置信区间（Date-Cluster Bootstrap 95% CI）**；
     3. **概率校准质量**：样本外 Brier 分数低于基准气候率，且 **Brier Skill Score (`BSS`) > 0**；
     4. **诚实披露与禁止造假原则**：若单一市场在留出测试集上受限于基础行情分布与 $n \ge 100$ 存在客观统计上限，必须如实给出该市场在 $n \ge 100$ 下的真实最高可达帕累托前沿、完整网格表与统计证明，严禁伪造数据、严禁泄漏未来数据或靠缩减样本量至 $n < 30$ 刷高胜率。

---

## 八、最终交付物要求

完成所有代码修复、门禁测试与多轮回测迭代后，请在仓库根目录生成一份完整的中文深度报告 `BACKTEST_AND_SKILL_AUDIT_REPORT.md`，并在回复中完整呈现：

1. **10 路子 Agent 审计总览与多轮回测演进对比表**：
   - 完整列出 `v0_baseline` → `v1_anti_quant` → … → `v_final` 在 **CN / HK / US 三市场**、**Dev（2021–2024）/ Val（2025H1）/ Holdout Test（2025H2–2026.10）三阶段**、**信号层（Signal Layer）vs 成交层（Fill Layer）** 的决策总数、做多信号数、成交数、`tp_first_rate_pct`、`positive_net_rate_pct`、Wilson 95% CI、Date-Cluster Bootstrap 95% CI、平均/中位净收益、Brier / BSS、组合年化/最大回撤，以及假突破诱多单与扫损单的拦截比例。
2. **七大维度问题全量台账（带精确文件路径、行号、复现命令与修复前后对比）**：
   - 维度 1：对抗量化与程序化收割陷阱（点火假突破、T+1 隔夜闷杀、整数位扫损洗盘、TWAP/VWAP 拆单阴跌接飞刀、高规则分动量拥挤踩踏）
   - 维度 2：全量技术结构与多维空间测算（浪型、斐波那契、SMC、Volume Profile POC/VAH/VAL、轨道/楔形斜率一致性、双底/双顶、核平滑通道、蜡烛形态、RSI 背离、跳空缺口与 0.5 ATR 争夺位）
   - 维度 3：关键词门禁、四层概率边界与 `lint_response.py` / `validate_judgment.py` / `score_judgments.py` 校验器漏洞
   - 维度 4：Skill 使用流程与 Agent 执行 SOP 契约
   - 维度 5：三市场微观交易摩擦与回测机制边界问题
   - 维度 6：底层数据真实性、PIT 复权等价性与报告描述性问题
   - 维度 7：PNG 结构图与 HTML 交互看板显示/自适应问题（附浏览器实测与像素核验结果）
3. **次日与长周期反馈归因及已落地的代码修改清单**：
   - 逐文件列出修改摘要、核心数学公式、`git diff --stat`、新增回归测试名称与 `pytest` / `check_days_invariance.py` / `lint_response.py` 实跑输出。
4. **一键复现命令清单**：
   - 包含真实行情缓存、PIT 1500 股票池构建、M3 多核并发回测与 `.npz` 张量加速重测、聚合统计、门禁测试与图表/HTML/Markdown 渲染的完整命令。
```
