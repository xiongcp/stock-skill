# 数学形态、指标、SMC 与分位区间公式字典 (v5.1)

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
  - `veto_reason_counts`：按稳定编码统计风控否决原因：`BENCHMARK_MISSING`、`BENCHMARK_WEAK`、`RR_INSUFFICIENT`、`BELOW_LOWER_RAIL`、`BREAKOUT_LOW_VOLUME`、`PLAN_GEOMETRY_INVALID`。

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
