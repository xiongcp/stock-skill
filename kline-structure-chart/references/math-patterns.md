# 数学形态、指标、SMC 与分位区间公式字典 (v5.1)

本文件定义 `scripts/indicators.py`、`scripts/patterns.py`、`scripts/strategies.py`、`scripts/risk.py` 与 `scripts/forecast.py` 中使用的 100% 确定性数学公式。禁止使用视觉图像识别（CV/VLM）推断形态或点位。

## 1. 因果核平滑与波段极值检测

- **单边因果 Nadaraya-Watson 核回归**：
  $$\hat{P}_t = \frac{\sum_{j=\max(0, t-W+1)}^{t} \exp\left(-\frac{(t-j)^2}{2h^2}\right) C_j}{\sum_{j=\max(0, t-W+1)}^{t} \exp\left(-\frac{(t-j)^2}{2h^2}\right)}, \quad h = 3.0$$
- **因果 ZigZag 波段极值**：
  仅当后续价格相对当前波段极值反向变动超过自适应阈值 $\theta = \text{clip}(2.0 \times \text{ATR}_{20}\%, 3\%, 8\%)$ 时，在反转当日 $t_{\text{confirm}}$ 确认前一个极值点 $t_{\text{pivot}}$，同时记录极值日 `day` 与确认日 `confirmed_at_day`。结构窗口内确认的高/低点不足 2 个时，阈值按 ×0.75 逐步放宽，最低到 2%。
- 核平滑序列只用于展示和线性趋势判断；双底/双顶等波段形态直接使用 ZigZag 已确认的高低点（不是对核平滑序列取极值，也不是 Lo-Mamaysky-Wang 的双边核回归方法）。

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

## 5. 序列保形预测与历史相似 Patch 检索

- **无重叠历史相似 Patch**：
  候选窗口终点 $t_c$ 必须满足 $t_c + H \le t_{\text{now}} - L_{\text{patch}}$，严禁候选未来期跨越当前截面。
- **相似 Patch 仅作披露**：检索结果写入 `historical_analogues`（`used_in_quantiles = false`），不参与分位区间计算。
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

