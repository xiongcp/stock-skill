# 数学形态、指标、SMC 与正常波动范围公式字典

本文件定义 `scripts/indicators.py`、`scripts/patterns.py`、`scripts/strategies.py`、`scripts/risk.py` 与 `scripts/forecast.py` 中使用的 100% 确定性数学公式。禁止使用视觉图像识别（CV/VLM）推断形态或点位。

## 1. 因果核平滑与波段极值检测

- **单边因果 Nadaraya-Watson 核回归**：
  $$\hat{P}_t = \frac{\sum_{j=\max(0, t-W+1)}^{t} \exp\left(-\frac{(t-j)^2}{2h^2}\right) C_j}{\sum_{j=\max(0, t-W+1)}^{t} \exp\left(-\frac{(t-j)^2}{2h^2}\right)}, \quad h = 3.0$$
- **因果 ZigZag 波段极值**：
  仅当后续价格相对当前波段极值反向变动超过自适应阈值 $\theta = \text{clip}(2.0 \times \text{ATR}_{20}\%, 3\%, 8\%)$ 时，在反转当日 $t_{\text{confirm}}$ 确认前一个极值点 $t_{\text{pivot}}$，同时记录极值日 `day` 与确认日 `confirmed_at_day`。结构窗口内确认的高/低点不足 2 个时，阈值按 ×0.75 逐步放宽，最低到 2%。
- 核平滑序列只用于展示和线性趋势判断；双底/双顶等波段形态直接使用 ZigZag 已确认的高低点（不是对核平滑序列取极值，也不是 Lo-Mamaysky-Wang 的双边核回归方法）。

## 1.1 结构层轨道、浪型计数与三条推演（`scripts/structure.py`）

- **轨道拟合（第三轮复查 O3 起）**：上/下轨分别在最近 4 个（`RAIL_RECENT_PIVOTS`）同类已确认拐点中两两连线作为候选，两支点间隔至少 10 根（`MIN_RAIL_SPAN`，无合格候选时放宽到 1 根）。候选线须满足：自第一个支点起，后续同类拐点越线不超过 $\text{tol} = \max(0.6\%,\ 0.6 \times \text{ATR}_{20}\%)$。评分
  $$\text{score} = N_{\text{touch}} + 0.5 \cdot \frac{x_2 - x_1}{N} + 0.5 \cdot \frac{x_2}{N-1} - 2 \cdot \frac{N_{\text{breach}}}{x_2 - x_1 + 1}$$
  其中 $N_{\text{touch}}$ 为距线 tol 以内的同类拐点数，$N_{\text{breach}}$ 为两支点之间收盘越线的K线数，$N$ 为结构窗口根数；取最高分，同分取末支点更近者。
  - 旧算法固定取拐点凸包的最后一条边：上凸包末边斜率最小、下凸包末边斜率最大，几何上偏向"上轨降、下轨升"。在 16 个录制序列的 1034 个截面上，收敛类形态占 85.5%（对称三角形 42.5%）；新算法为 52.5%（对称三角形 13.2%）。
- **走平阈值**：轨道每根K线斜率绝对值小于 $0.025 \times \text{ATR}_{20}\%$（`FLAT_ATR_PER_BAR`，即 40 根K线累计不足 1 个 ATR）视为走平。
- **Theil-Sen 交叉校验**：对自轨道起点以来的同类拐点（至少 3 个）取两两斜率的中位数。若轨道方向与该稳健斜率方向相反（一升一降，按同一走平阈值），形态判为"形态不明确"，校验结果写入 `structure.channel.fit_check`。
- **浪型计数**：主计数沿用 ABC 规则（A 浪起点为窗口最高/最低点，取起点更近的方向）。备选计数的 A 浪起点取窗口极值以及窗口内全部已确认拐点：
  - 下跌 ABC 用高点，上涨 ABC 用低点；
  - A 浪终点与 B 浪终点都相同，或 1.0 倍测算位相距 0.5% 以内的，视为同一计数；
  - 按 A 浪幅度（ATR 倍数 `a_range_atr`）从大到小，最多取 2 个（`MAX_ALT_COUNTS`）。
  每个计数的否定条件 `count_invalidation` 为：日线收盘反向越过 B 浪终点。结果写入 `structure.wave_counts`。备选计数只提示主计数可能错在哪里，不代表可能性大小。
- **触发位最小距离**：上攻/下探触发位距最新收盘价不少于 $0.5 \times \text{ATR}_{20}\%$（`MIN_TRIGGER_ATR`）。更近的价位落在日常波动噪声里，只写入 `scenarios.contested_levels`，不作触发条件。
- **折点时间**：以近 60 日日对数收益 RMS 为 $\sigma$，走完相对距离 $d$ 的时间尺度取随机游走扩散估计 $t \approx (d/\sigma)^2$。
  - 触发、回踩/反抽、目标三个折点按此估计，并截断在预测期 $H$（`horizon_bars`）以内且严格递增。
  - 折点时间只决定推演路径折点的横向位置，写入 `chart_data.json` 供画图使用，不进入 `result.json`；未截断的估计与目标估计是否超过 $H$ 同样只在 `chart_data.json` 中。
  - 这只是时间量级，不是方向判断。

| 指标（16 个录制序列、1005 个截面、$H = 15$） | 旧版 | O3 改进后 |
| --- | --- | --- |
| 推演折点时间槽种类 | 1（固定第 8/17/28 根，超出预测期） | 629（全部 ≤ $H$） |
| 任一触发位距现价不足 0.5 个 ATR 的截面 | 590（58.7%） | 0 |
| 15 日内两侧都未被收盘越过（"震荡"成立） | 6.4% | 13.9% |
| 15 日内上攻、下探都被收盘越过 | 23.9% | 11.7% |

## 2. 经典蜡烛形态 ATR 归一化不等式

- **看涨锤头线 (`CDL_BULLISH_HAMMER`)**：
  1. $\min(O_t, C_t) - L_t \ge 2.0 \times \max(|C_t - O_t|, 0.05(H_t - L_t))$
  2. $H_t - \max(O_t, C_t) \le 0.18 \times (H_t - L_t)$
  3. $H_t - L_t \ge 0.75 \times \text{ATR}_{14}(t)$
  4. 低位条件：$\frac{\min(O_t, C_t) - \min L_{t-19..t}}{\max H_{t-19..t} - \min L_{t-19..t}} \le 0.35$，或 $L_t \le 1.005 \times \min L_{t-19..t-1}$（刺破近 20 日低点）
- **看涨吞没 (`CDL_BULLISH_ENGULFING`)**：
  $C_{t-1} < O_{t-1} \land C_t > O_t \land C_t \ge O_{t-1} \land O_t \le C_{t-1} \land |C_t - O_t| \ge 1.08 |C_{t-1} - O_{t-1}| \land |C_t - O_t| \ge 0.55 \text{ATR}_{14}(t)$。

## 3. 波段几何形态与顶底背离

- **波段双底 (`SWING_DOUBLE_BOTTOM_BREAKOUT` / `_FORMING`)**：$E_2, E_4$ 为最近两个已确认 ZigZag 低点，$E_3$ 为其间最高的已确认高点（颈线）。
  $|E_2 - E_4| \le 0.65 \text{ATR}_{14} \land (E_3 - \max(E_2, E_4)) \ge 1.2 \text{ATR}_{14} \land t(E_4) - t(E_2) \in [6, 85]$；收盘高于颈线为 `BREAKOUT`。双顶（`SWING_DOUBLE_TOP_*`）对称定义。
- **核平滑线性趋势 (`KERNEL_SMOOTHED_UPTREND` / `DOWNTREND`)**：对近 60 根的因果 NW 平滑收盘价做普通最小二乘，$R^2 \ge 0.72 \land |\text{slope}| \ge 0.04 \text{ATR}_{14}$。平滑会抬高 $R^2$，该判据只描述趋势一致性，不给出通道上下沿。
- **RSI 底背离 (`RSI_BULLISH_DIVERGENCE`)**：
  $L_{t_2} < L_{t_1} - 0.25 \text{ATR}_{14} \land \text{RSI}_{14}(t_2) > \text{RSI}_{14}(t_1) + 2.5 \land \text{RSI}_{14}(t_1) < 42$。

## 4. SMC 微观结构与 Volume Profile 近似

- **BOS 结构破位 (`BULLISH_BOS` / `BEARISH_BOS`)**：收盘价越过最近一个已确认的波段高/低点；不区分趋势延续（BOS）与反转（CHoCH）。订单块取破位前最后一根反向实体K线，收盘未反向穿越即为 `ACTIVE`。

- **看涨公允价值缺口 (`BULLISH_FVG`)**：
  $L_t - H_{t-2} > 0.20 \text{ATR}_{14}(t) \land |C_{t-1} - O_{t-1}| \ge 1.10 \text{ATR}_{14}(t)$。
- **Volume Profile (`ohlcv_range_partition_v1`)**：
  将窗口高低点划分为 24 个价格桶，按每根 K 线跨越的桶均匀分配成交量，取最大量桶中位价为 `POC`，围绕 `POC` 累积 70% 成交量的上下边界为 `VAH` 与 `VAL`。

## 5. 序列 split-conformal 区间（正常波动范围）与历史相似 Patch 检索

- **无重叠历史相似 Patch**：
  候选窗口终点 $t_c$ 必须满足 $t_c + H \le t_{\text{now}} - L_{\text{patch}}$，严禁候选未来期跨越当前截面。
- **相似 Patch 仅作内部披露**：检索结果只留在内部报告（`used_in_quantiles = false`），不参与分位区间计算，也不进入 `result.json`。
- **按期限 split-conformal 区间（v6）**：
  - 中心：$\hat{q}_{50,h} = C_{\text{now}}$（鞅假设，不加规则分或类比漂移）。
  - 非一致性分数：$s_{i,h} = \frac{|C_{i+h}/C_i - 1|}{\sigma_i \sqrt{h}}$，$\sigma_i$ 为截面 $i$ 当时近 60 日对数收益 RMS（预测与校准用同一估计量）；只用 $i + h \le t_{\text{now}}$ 的成熟截面，取最近 160 个。
  - 乘数：$m_{h,\alpha}$ 取 $\{s_{i,h}\}$ 的有限样本分位（第 $\lceil (n+1)\alpha \rceil$ 个次序统计量），$\alpha \in \{0.5, 0.8\}$。
  - 区间：$C_{\text{now}}(1 \pm m_{h,\alpha}\,\sigma_{\text{now}}\sqrt{h})$，再按涨跌停逐步截断、按价位表取整。
  - 审计：审计期覆盖全部可用历史，与展示校准窗口解耦。对每个截面 $e$，乘数只用 $i \in [\max(60,\ e - H - 160),\ e - h]$ 的已成熟分数估计（与展示区间同一窗口规则，$e = t_{\text{now}}$ 时恰为展示所用的分数集合；窗口内不足 40 个分数的期限不参与检验），按同一构造施加涨跌停截断与价位表取整后直接检验 $e+h$ 的实际收盘价是否落在区间内，得到 `calibration_audit` 的滚动样本外覆盖率及相对随机游走的平均绝对误差（`q50_mae_pct` / `random_walk_mae_pct`）；相邻截面结果期重叠，有效独立样本约为样本数 / H。VIX、财报放大属于未校准叠加项，启用时列入 `uncalibrated_overlays`。
  - 晋级（`promotion_status = PROMOTED`，报告称"已达标"）须同时满足：已校准；评估样本 $N_{\text{eval}} \ge 30$；有效独立样本 $\lfloor N_{\text{eval}} / H \rfloor \ge 20$（`min_effective_independent_samples`）；50%、80% 区间实测覆盖率与名义值之差都不超过 10 个百分点；没有未校准叠加项。否则为 `RESEARCH_CALIBRATION_WARMUP`（"校准观察期"）。
    理由：相邻截面的结果期重叠 $H-1$ 根，$N_{\text{eval}}$ 个样本只相当于约 $N_{\text{eval}}/H$ 个独立观测；只有 7 个左右独立样本时，80% 区间实测覆盖率的二项标准误约为 15 个百分点，"偏差不超过 10 个百分点"不足以说明区间可靠。有 20 个独立样本时标准误约为 9 个百分点，与容差同一量级。
    样本量：评估截面 $e \in [H + 99,\ n - 1 - H]$（$n$ 为已收盘K线根数），$N_{\text{eval}} = n - 2H - 99$，有效独立样本达到 20 需 $n \ge 22H + 99$（$H = 15$ 时 $n \ge 429$）。三市场取数固定 760 根时 $H = 15$ 有 $N_{\text{eval}} = 631$（约 42 个独立样本）；若标的历史不足（如不足 500 根），有效独立样本达到 20 同样要求 $n \ge 429$。展示区间仍只用最近 160 个截面校准，扩展审计期不改变展示区间宽度。

## 6. 回测统计区间

- **胜率 Wilson 95% 区间**（成交 $n \ge 2$ 时提供）：$\hat p = w/n$，$z = 1.96$，
  $$\frac{\hat p + \frac{z^2}{2n} \pm z\sqrt{\frac{\hat p(1-\hat p)}{n} + \frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}$$
  小样本或全胜/全负时不会像 Bootstrap 百分位法那样退化成 $[\hat p, \hat p]$（例：3 笔全亏为 0%~56.15%）。
- **单笔均值收益 95% 区间**：固定种子的 Bootstrap 百分位法（400 次重抽样，取第 2.5%、97.5% 分位）。
- 成交少于 30 笔时输出 `sample_size_warning`，胜率与夏普等统计量仅供参考。
- **决策日诊断（第三轮复查 O3 起）**：回放按步长逐个决策日调用线上流水线；持仓期间照常记录决策，但不重复开仓。
  - 记录每个决策日的 `rule_score`、动作，以及未来 $H$ 日收益（全历史复权价 $C_{t+H}/C_t - 1$，只用于事后评估）。
  - `rule_score_ic.spearman`：规则分与未来收益的 Spearman 秩相关，并列取平均秩。
  - 有效独立样本 ≈ 决策数 × 步长 / $H$。只有有效独立样本 ≥ 20 且 $|\text{IC}| \ge 0.05$ 时，`informative = true`；否则报告注明"规则分只作描述"。
  - `rule_score_buckets`（按 [0,38)、[38,52)、[52,65)、[65,100] 分层）与 `action_forward_returns`（按动作分组）：给出平均、中位数前瞻收益和上涨占比。
  - `veto_reason_counts`：按稳定编码统计风控否决原因：`BENCHMARK_MISSING`、`BENCHMARK_WEAK`、`RR_INSUFFICIENT`、`BELOW_LOWER_RAIL`、`BREAKOUT_LOW_VOLUME`、`PLAN_GEOMETRY_INVALID`、`DELISTING_RISK`（退市整理期简称）、`STALE_OR_DELISTED`（落后基准超过 5 个交易日，仅线上最新截面判定）。
- **撮合与成本口径**（`run_plan_backtest` / `label_triple_barrier_event`）：
  - 决策日 $t$ 收盘出信号，$t+1$ 开盘落在入场区内才成交；标的 $t+1$ 那根K线晚于基准在 $t$ 之后的第一个交易日（即中间停牌）时不成交。没有基准时只在休市表覆盖范围内按交易日历判断。
  - 涨跌停幅度按每根K线当日的规则取值（跨越 2026-07-06 主板 ST 新规时自动切换）；一字跌停日不能卖出，时间屏障顺延到开板日。一直锁到数据末尾的持仓按最后一个锁定日收盘价计亏损，`outcome = LOCKED_LIMIT_DOWN_UNTIL_END`。
  - 费率：买入腿按入场日规则（佣金 + 买方印花税 + 买方规费），卖出腿按出场日规则（佣金 + 卖方印花税 + 卖方规费），另加 2 bps 滑点；回测按比例费率计，不计单笔最低佣金。
  - 历史状态不回填：当前为 ST 的标的，在 2026-07-06 之前的截面若近 60 根无除权K线内出现过单日振幅超过 5.5%，视为当时未戴帽（启发式，近期刚戴帽的标的可能被误判为非 ST）；退市整理期简称（「退市」「XX退」「DELISTED」）在历史截面一律去掉。

## 7. 持仓防守与破位做空计划（`scripts/risk.py`，第三轮复查 O3 起）

- **做多三重屏障计划**（`plan.long`）：
  - 入场区 `entry_zone` 围绕最新收盘价 $C_{\text{now}}$ 的可成交价格带构造（不把远端上方阻力 $R_1$ 当作入场区上沿）：右侧突破确认取 $[\max(S_1, C_{\text{now}}-0.35\,\text{ATR}_{14}),\ \min(R_1, C_{\text{now}}+0.30\,\text{ATR}_{14})]$（若 $C_{\text{now}} \ge R_1$ 则取 $[\min(C_{\text{now}}, 0.996 R_1),\ \max(C_{\text{now}}, 1.005 R_1)]$）；左侧回踩观察取 $[\max(0.995 S_1, C_{\text{now}}-0.45\,\text{ATR}_{14}),\ \min(R_1, C_{\text{now}}+0.20\,\text{ATR}_{14})]$；区间观察（等待右侧确认）在最新K线收阳或平盘时取 $[\max(S_1, C_{\text{now}}-0.20\,\text{ATR}_{14}),\ \min(R_1, C_{\text{now}}+0.35\,\text{ATR}_{14})]$，收阴时取 $[\max(S_1, \min(R_1, C_{\text{now}}+0.08\,\text{ATR}_{14})),\ \max(\text{下沿}, \min(1.003 R_1, C_{\text{now}}+0.45\,\text{ATR}_{14}))]$（等反弹确认再进，不在阴线收盘价追入）。任一情形下沿高于上沿时退化为 $[0.995\,C_{\text{now}},\ 1.005\,C_{\text{now}}]$。
  - 止损位 `stop_loss_barrier`：右侧突破取 $\min(S_1,\ \max(S_1-0.25\,\text{ATR}_{14},\ C_{\text{now}}-1.35\,\text{ATR}_{14}))$；左侧回踩取 $\min(S_1-0.15\,\text{ATR}_{14},\ \max(S_1-0.50\,\text{ATR}_{14},\ C_{\text{now}}-1.45\,\text{ATR}_{14}))$，区间观察取 $\min(S_1-0.15\,\text{ATR}_{14},\ \max(S_1-0.50\,\text{ATR}_{14},\ C_{\text{now}}-1.40\,\text{ATR}_{14}))$。
  - 第一目标位 `take_profit_1`：设入场中值为 $E$，单位风险 $R = \max(0.5\,\text{ATR}_{14}, E - \text{止损})$，15 日有效摆动风险 $R_{\text{eff}} = \min(R, 1.50\,\text{ATR}_{14})$，上方结构上限 $U = \max(R_1, \text{上攻目标})$（非突破态且 $R_1 \ge E + 1.15 R$ 时取 $U = R_1$），则 $\text{TP1} = \min(U,\ E + 1.5 R_{\text{eff}})$。当上方结构空间受阻或止损距离过大导致扣费后净盈亏比 $< 1.20$ 时，触发 `RR_INSUFFICIENT` 否决。
  - 计划几何不变量：止损 $\ge$ 入场区下沿或 $\text{TP1} \le$ 入场区上沿时（在区内成交即处于止损下方，或止盈即亏损），触发 `PLAN_GEOMETRY_INVALID` 否决。
  - 仓位 `position_sizing`（只在给出账户净值时计算）：每股风险按入场区上沿 $Z_{hi}$ 这一区内最差成交价计 $r = (Z_{hi} - \text{止损}) + Z_{hi} \cdot f$（$f$ 为往返费率），股数 $= \text{按板块取整}\big(\min(\lfloor \text{净值} \times \text{单笔风险} / r \rfloor,\ \lfloor \text{净值} / (Z_{hi}(1+f)) \rfloor)\big)$，因此在入场区内任意价位成交、按止损价离场的亏损都不超过风险预算。单笔风险 `--max-risk-pct` 为比例，取值 $(0, 0.05]$。风控否决或动作不是 `LONG_CONFIRMED` / `WATCH_LONG_SETUP` 时不输出股数（`recommended_shares = null` 并给出 `reason`）。该估算假设能按止损价成交，跳空、跌停封板与 T+1 当日不可卖出时实际亏损可能超出预算（`gap_risk_note`）。
  - 短期极度超买防追高过滤：当 $(C_{\text{now}} - \text{EMA}_{20}) / \text{ATR}_{14} > 2.0$ 或 $\text{RSI}_{14} \ge 70$ 时，不给出 `LONG_CONFIRMED` / `WATCH_LONG_SETUP` 追高信号，降级为 `NEUTRAL_HOLD_WAIT`（观望等待回踩）。
  - 因果动量/反转体制自适应：只用截至当日已成熟的过去 240 根历史样本无前视计算 20 日均线偏离度与未来 15 日收益的 Spearman 秩相关 $\rho_{\text{mom}}$（`causal_momentum_ic_15d`）。当宏观均线处于弱势排列（$\text{EMA}_{60}<\text{EMA}_{200}$ 或 $\text{EMA}_{60}<\text{SMA}_{250}$ 或 $\text{EMA}_{200}<\text{SMA}_{250}$）且 $\rho_{\text{mom}} \le -0.10$ 时，进入 `MEAN_REVERSION_ADAPTED` 均值回归体制（围绕中枢 50 旋转评分，禁止在高于 $\text{EMA}_{20}+0.35\,\text{ATR}_{14}$ 或 60 周高低区间 $>78\%$ 位置追高，仅在 $\le \text{EMA}_{20}+0.15\,\text{ATR}_{14}$ 且满足缩量 $\text{RVOL}_{20} \le 1.10$ 或出现反转信号时按左侧回踩公式计算 `LEFT_SIDE_PULLBACK` 入场区与止损位）。线上计划（`plan.long.breakeven_ratchet`）与回测统一采用移动保本规则：盘中最高价触及 $+0.90R$ 且当日收盘高于 $+0.30R$ 时，自次日起将保护性止损上移至 $+0.08R$ 保本位。
- **持仓防守**（`plan.holder_defense`，指数不适用）：
  - 日线收盘跌破下探触发位 $S_1$ 减仓；
  - 盘中触及硬止损屏障离场（与做多计划同一止损；止损不低于 $S_1$ 时合并为"收盘跌破 $S_1$ 或盘中触及硬止损离场"）；
  - 跌破后反抽收盘未站回 $S_1(1 + 0.4\,\text{ATR}_{20}\%)$ 不宜回补。
- **破位做空**（`plan.short`，仅港股、美股的股票/ETF；A 股与指数 `available = false`）：
  - 入场区 $[S_1 - 0.25\,\text{ATR}_{14},\ S_1]$，入场中值记为 $E$；
  - 止损 $\max(S_1 + 1.5\,\text{ATR}_{14},\ S_1(1 + 0.4\,\text{ATR}_{20}\%))$；
  - 单位风险 $R = \max(0.5\,\text{ATR}_{14},\ \text{止损} - E)$；
  - $\text{TP1} = E - 1.5R$，$\text{TP2} = \min(E - 2.5R,\ \text{下探目标})$；
  - 净盈亏比扣除往返交易成本，不含融券费用；
  - 大盘状态分 ≥ 65 或净盈亏比 < 1.20 时写入 `warnings`；
  - 卖空制度约束见 `hk-market.md` / `us-market.md`。

## 8. 大盘周期阶段与个股—大盘对齐（`scripts/market_structure.py`）

本节是描述性计算：只用已收盘K线与已确认拐点（`confirmed = true`），不参与规则分、评级、风控否决与计划价位。常量在规格中写定，未经调参，也未经有效性验证。

| 常量 | 取值 | 含义 |
| --- | --- | --- |
| `MA_WEEKS` | 30 | 周线均线周期 |
| `SLOPE_WEEKS` | 4 | 均线斜率的回看周数 |
| `RANGE_WEEKS` | 60 | 位置区间的回看周数 |
| `MIN_WEEKS` | 35 | 判定阶段所需的最少已完成周数 |
| `MA_MONTHS` | 10 | 月线均线周期 |
| `MIN_MONTHS` | 11 | 月线位置所需的最少已结束月数 |
| `ALIGN_DAYS` | 120 | 对齐窗口（共同交易日） |
| `ALIGN_MIN_DAYS` | 60 | 联动程度与相对强弱需要的最少共同交易日 |
| `RS_BAND` | 0.02 | 相对强弱线趋势的 ±2% 带 |
| `ALIGN_WINDOW_BARS` | 5 | 新低新高对照时，拐点前后的K线窗口 |

另有两个沿用自 `structure.py` 的常量：`TREND_FLAT_PCT`（0.5%，斜率走平阈值）与 `MIN_RAIL_SPAN`（10 根，拐点配对的最大间隔）。

### 8.1 周线阶段

输入取最近 `HISTORY_BARS`（420）根已收盘日线，因此结果与取数长度无关。周线来自 `strategies.aggregate_bars_to_weekly`，只保留已完成的周，并丢弃第一个可能不完整的周；未完成的当周不参与计算，阶段只在一周结束后更新。

记周线收盘为 $W$，$\text{MA30} = \text{SMA}(W, 30)$：

- $\text{slope\_4w} = \text{MA30}[-1] / \text{MA30}[-5] - 1$；$|\text{slope\_4w}| \le 0.5\%$ 视为走平。
- $\text{pos\_60w} = (W[-1] - L_{60}) / (H_{60} - L_{60})$，$H_{60}$、$L_{60}$ 为近 60 个已完成周的最高价与最低价；不足 60 周时用全部可得的周，实际周数写入 `range_week_count`；$H_{60} = L_{60}$ 时取 0.5。

| 阶段 | 条件 |
| --- | --- |
| `ADVANCE`（上升阶段） | $\text{slope\_4w} > +0.5\%$ 且 $W[-1] > \text{MA30}[-1]$ |
| `DECLINE`（下降阶段） | $\text{slope\_4w} < -0.5\%$ 且 $W[-1] < \text{MA30}[-1]$ |
| `TOPPING`（高位震荡） | 其余情况，且 $\text{pos\_60w} \ge 0.5$ |
| `BASING`（低位震荡） | 其余情况，且 $\text{pos\_60w} < 0.5$ |

规则无状态：只看当前这一周的数值。`phase_weeks` 是按同一规则逐周回看、当前阶段已连续多少周；回看到预热不足的那一周为止，此时 `phase_weeks_capped = true`（实际持续周数不少于该值）。

月线：已结束自然月（丢弃第一个月与最后一个月）的收盘相对 10 月均线的位置，`ABOVE` 或 `BELOW`，价位为 `ma10m_price`。

已完成周少于 35 个时，`phase` 与周线字段为 `null` 并给出 `reason`；已结束月少于 11 个时，月线字段为 `null`。标的自身即基准或指数时，同一函数用于标的自身，`cycle.basis = "SELF"`。

### 8.2 个股与大盘的对齐（`alignment`）

按日期对齐标的与基准的已收盘K线，取最近 120 个共同交易日（`common_day_count` 为实际个数）。

- **联动程度**：`beta_120`、`corr_120` 是日对数收益的回归斜率与相关系数；共同交易日少于 60 个，或任一方差为 0 时为 `null`。
- **相对强弱线**：$rs = $ 标的收盘 / 基准收盘。`rs_trend_20`、`rs_trend_60` 取 $rs[-1] / rs[-1-n]$：不低于 1.02 为 `UP`，不高于 0.98 为 `DOWN`，其余为 `FLAT`（`rs_trend_20` 需要多于 20 个共同交易日；`rs_trend_60` 需要多于 60 个，恰好 60 个时为 `null`）。`rs_at_60d_high`、`rs_at_60d_low` 在共同交易日不少于 60 个时给出，为 $rs[-1]$ 是否为最近 60 个值中的最大或最小值。
- **拐点先后**：取基准最近一个已确认的波段低点，在标的的已确认低点中找时间最近且相距不超过 10 根K线的一个，`low_lead_lag_bars = 标的低点序号 - 基准低点序号`（按共同交易日序号计，负数表示标的先见低点），找不到时为 `null`；高点同理，输出 `high_lead_lag_bars`。
- **新低新高对照**：取基准最近两个已确认低点 $L_1$（较早）与 $L_2$，标的在 $L_1$、$L_2$ 各自前后 5 根K线窗口内的最低价记为 $m_1$、$m_2$。

| 基准 | 标的 | 标签 |
| --- | --- | --- |
| $L_2 < L_1$（创新低） | $m_2 > m_1$ | `HIGHER_LOW_WHILE_INDEX_LOWER_LOW`（大盘新低、个股未新低） |
| $L_2 < L_1$ | $m_2 \le m_1$ | `LOWER_LOW_WITH_INDEX`（与大盘同步新低） |
| $L_2 \ge L_1$ | $m_2 < m_1$ | `LOWER_LOW_WHILE_INDEX_HIGHER_LOW`（大盘未新低、个股新低） |
| $L_2 \ge L_1$ | $m_2 \ge m_1$ | `HIGHER_LOW_WITH_INDEX`（与大盘同步抬高低点） |

  高点对称（窗口内取最高价，$H_2 > H_1$ 为创新高）：

| 基准 | 标的 | 标签 |
| --- | --- | --- |
| $H_2 > H_1$（创新高） | $m_2 < m_1$ | `LOWER_HIGH_WHILE_INDEX_HIGHER_HIGH`（大盘新高、个股未新高） |
| $H_2 > H_1$ | $m_2 \ge m_1$ | `HIGHER_HIGH_WITH_INDEX`（与大盘同步新高） |
| $H_2 \le H_1$ | $m_2 > m_1$ | `HIGHER_HIGH_WHILE_INDEX_LOWER_HIGH`（大盘未新高、个股新高） |
| $H_2 \le H_1$ | $m_2 \le m_1$ | `LOWER_HIGH_WITH_INDEX`（与大盘同步压低高点） |

  输出基准两个拐点的日期 `benchmark_days`、基准是否创新低（或新高）`benchmark_extended` 与 $m_2/m_1 - 1$ 的百分比 `target_change_pct`，不输出标的价位。$L_2$ 之后已收盘K线不足 5 根时窗口右侧截到最后一根，`window_complete = false`，标签可能在随后几根K线内改变。基准已确认低点（或高点）不足两个时，对应对照为 `null`。
- **阶段对照**：`target_phase` 与 `benchmark_phase` 各自按 8.1 计算并列给出，不合成一个标签。

因果性：大盘层自己按标的最后一个已收盘日截断基准，不依赖上游对齐；`evaluation.run_causal_replay_audit` 的 `market_layer` 项守护这一点。

## 9. 多次测试价区、头肩与平台（`scripts/tested_zones.py`）

本节是描述性计算：只用已收盘K线与 ZigZag 拐点，不参与规则分、评级、风控否决与计划价位。常量在规格中写定，未经调参，也未经有效性验证；触点数只是计数，不代表支撑或压力更强。

| 常量 | 取值 | 含义 |
| --- | --- | --- |
| `W_TOL_ATR` | 0.65 | 价区宽度上限（ATR 倍数）：全部触点的最高价与最低价之差，沿用双底"两低点差 ≤ 0.65×ATR" |
| `BREAK_ATR` | 0.5 | 破位距离：收盘越过已确认触点的边缘这么多个 ATR 才算破位，沿用情景库的 `MIN_TRIGGER_ATR` |
| `NECK_MIN_ATR` | 1.2 | 颈线最小高度（ATR 倍数），沿用双底"颈线 − 底 ≥ 1.2×ATR" |
| `FALSE_BREAK_BARS` | 3 | 破位后 3 根内收盘收回才算假破位 |
| `RECENT_BARS` | 20 | 近期窗口：K线下标 ≥ n − 20（n 为已收盘K线根数），即含最新一根在内的 20 根 |
| `MAX_ZONES_PER_SIDE` | 3 | 每级别每侧最多输出的价区数 |
| `HS_NECK_SLOPE_MAX` | 0.5 | 头肩两颈线点之差 ≤ 0.5 × 形态高度 |
| `MAX_TOUCHES` | 8 | 触点列表上限：超过时列第 1 个与最后 7 个，`touches_truncated = true` |
| `MAX_CHANGES` | 12 | 逐日变化最多输出条数，主级别优先 |

**ATR 与两个级别**：$\text{atr\_abs} = \text{atr\_pct}(hist, 20) \times$ 最新收盘价（结构层同一个 ATR，`atr_basis = "STRUCTURE_ATR20"`）。主级别 `MAJOR` 的 ZigZag 阈值就是结构层的 `zz_thr`；次级别 `MINOR` 为 $\min(4\%, \max(1.5\%, \lceil \text{ATR\%} / 0.5\% \rceil \times 0.5\%))$，不小于主级别阈值时跳过并写 `skipped_reason`。两个级别都在最近 420 根已收盘K线上跑 `structure.zigzag`，只取落在结构窗口（最近 120 根）内的拐点；ZigZag 末端尚未被反向走势确认的极值是未确认拐点。

**聚类（决定 N）**：每个级别、每一侧（支撑用低点，压力用高点）按时间顺序处理拐点。拐点 $p$ 可以并入一个已有价区，当且仅当并入后全部触点的最高价与最低价之差 ≤ 0.65 ATR，且该价区最后一个触点到 $p$ 之间没有收盘价越过已确认触点边缘 0.5 ATR 以上；有多个候选时选并入后宽度最小的，宽度相同选最后触点最近的，没有候选就新建价区。只输出触点数 ≥ 2、第一个触点已确认、颈线高度 ≥ 1.2 ATR 的价区。未确认触点每个价区至多一个，且一定是最后一个。

**几何量（支撑价区；压力镜像）**：

- `zone_low_price` / `zone_high_price`：已确认触点的最低价 / 最高价；`neckline_price`：首末触点之间的最高价；`height_abs` = 颈线 − `zone_low_price`。
- `break_price` = `zone_low_price` − 0.5 ATR（失效价，只由已确认触点决定，不随未确认的新低移动）；`measured_move_price` = 颈线 + 高度；`confirm_price` = 未确认低点 ×（1 + 本级别阈值），之后任一根最高价达到它，该触点即被确认。
- 名称：N = 2 为双底 / 双顶，N = 3 为三重底 / 三重顶，N ≥ 4 为"N 次测试的支撑区 / 压力区"；N 含未确认触点，此时名称带"（第 N 次测试进行中，未确认）"。`sequence`：`RISING` / `FALLING` / `MIXED`。
- 观察到的价格（触点、价区上下沿、颈线、头肩各点）与结构层拐点同口径保留小数；推算的价位（`break_price`、`confirm_price`、`measured_move_price`、`neckline_today_price`）按标的价位表取整。

**状态**（从最后一个已确认触点的下一根起扫描收盘价）：

| 状态 | 判定（支撑价区） |
| --- | --- |
| `BROKEN` | 收盘低于 `break_price`，此后没有收盘回到 `zone_low_price` 及以上；只在破位后 20 根内输出 |
| `FALSE_BREAK` | 收盘低于 `break_price` 后 3 根内收回，收回发生在最近 20 根内，之后没有再次破位 |
| `NECKLINE_CROSSED` | 没有破位，最新收盘高于颈线 |
| `TESTING` | 没有破位、未过颈线，有未确认触点 |
| `HOLDING` | 没有破位、未过颈线，没有未确认触点 |
| （不输出） | 破位后超过 3 根才收回 |

**筛选**：每个级别、每一侧取离最新收盘最近的未破位价区（`NEAREST_ACTIVE`），加上最后触点或状态变化落在最近 20 根内的价区（`RECENT`），合并去重后最多 3 个。次级别价区与某个已输出的主级别价区触点完全相同且没有未确认触点时，只输出 `{zone_id, same_as_zone_id}`。

**头肩底 / 头肩顶**：每个级别每侧只看结构窗口内最后三个同类拐点 $S_1, H, S_2$（$S_2$ 可未确认）。头肩底要求 $H < \min(S_1, S_2) - 0.65\,\text{ATR}$、$|S_1 - S_2| \le 0.65\,\text{ATR}$；颈线由 $S_1$–$H$ 与 $H$–$S_2$ 之间的两个最高价点连线并外推到今天；头部处的颈线高度 ≥ 1.2 ATR；两颈线点之差 ≤ 0.5 × 高度。状态：$S_2$ 之后收盘低于 $H - 0.5\,\text{ATR}$ 为 `FAILED`，否则最新收盘高于今日颈线为 `NECKLINE_CROSSED`，不高于为 `FORMING`。头肩顶镜像。

**平台**：同一级别已筛选的一个支撑价区与一个压力价区，两者状态都是 `TESTING` / `HOLDING` / `FALSE_BREAK`，首末触点的时间区间有交集，且压力价区下沿 − 支撑价区上沿 ≥ 1.2 ATR。多对满足时取两者末触点中较早者最近的一对。平台不单独判定状态，任一侧破位或越过颈线后即不再成立。

**逐日变化**：用截至上一根已收盘K线的数据按同样规则重算（阈值与 ATR 按那一天），按 `zone_id = {级别}-{SUPPORT|RESISTANCE}-{首触点日期}` 匹配价区，头肩按三个点的日期、平台按两侧 `zone_id` 匹配，输出 `changes[]`。级别阈值与上一天不同时，该级别的变化带 `threshold_changed = true`。

**盘中**：价区、头肩与平台只用已收盘K线计算；存在未收盘K线时只在价区上附 `intraday_note`（盘中越过失效价，或盘中正在测试该价区），不作确认。

因果性：`evaluation.run_causal_replay_audit` 的 `tested_zones` 项在每个截面、同一阈值与 ATR 下检查截至截面日已确认的触点与价区几何量不随未来K线改变。回测逐日回放与历史回放快照不计算价区。
