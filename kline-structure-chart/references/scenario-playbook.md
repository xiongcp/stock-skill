# 多情景推演手册（Scenario Playbook）

本文件定义 `kline-structure-chart` L1 多情景结构引擎与 L2 经验概率层的 8 个标准情景，与 `scripts/scenarios.py` 中的 `SCENARIO_PLAYBOOK` 严格一一对应（由自动化测试守护）。

---

## 1. 情景总表（单一信息源）

| 情景ID | 情景 | 适用前提（由 L1 状态判定） | 目标位来源 | 失效条件 |
| --- | --- | --- | --- | --- |
| `TREND_CONTINUATION` | 趋势延续 | 通道或推动浪 3/5 进行中，收盘在通道中轨之上 | 通道上轨外推 / 浪 3 = 1.618×浪 1 | 收盘跌破通道下轨 |
| `BREAKOUT_RETEST` | 突破回踩 | 刚收盘突破轨道或颈线 ≤ 5 根 | 形态高度测量 | 收盘跌回轨道内超过 0.5 个 ATR |
| `FALSE_BREAKOUT_REVERSAL` | 假突破回落 | 突破后量能不足，或收盘重新回到轨道内 | 轨道另一侧 | 再次收盘站上突破位 |
| `RANGE_OSCILLATION` | 区间震荡 | 收敛或箱体内，距两侧都超过 1 个 ATR | 区间两端 | 收盘越出任一侧 |
| `BREAKDOWN_DECLINE` | 破位下行 | 收盘跌破下轨、颈线或前低 | 形态高度 / 前低 | 收盘收回破位位 |
| `C_WAVE_REVERSAL` | C 浪末端反转 | C 浪达到 1.0×A 附近，且出现背离或反转形态 | B 浪终点 | C 浪继续创新低超过 1.618×A |
| `OVERSOLD_BOUNCE` | 超跌反弹 | RSI 低于 30、偏离 MA20 超过 2 个 ATR | MA20 / 0.382 反弹位 | 再创新低 |
| `APEX_BREAKOUT` | 收敛末端变盘 | 距收敛顶点 ≤ 10 根 | 双向，形态起点高度 | — |

---

## 2. 筛选、触发与时间窗规则

1. **按截面状态动态筛选（2~5 个情景）**：
   - 脚本只输出当前截面满足适用前提的情景，数量控制在 `2 ~ 5` 个，不再固定为上攻/震荡/下探三条模板。
   - 不同形态（如上升通道、收敛三角形、扩散喇叭形）与不同浪段（如 C 浪末端、推动第 3/5 浪、超跌段）会产出不同的情景组合。
2. **触发位最小噪声距离与状态区分**：
   - 待触发条件（`status = "PENDING_TRIGGER"`）的触发价必须与最新已收盘价至少相距 `0.5` 个 ATR。
   - 若前提已满足且触发位距最新收盘价不足 `0.5` 个 ATR，该情景归为**「已在进行中」**（`status = "ACTIVE"`），不再作为待触发条件列出。
3. **时间窗（历史到达用时分位数）**：
   - 每个情景的时间窗 `time_window_bars` 取该标的自身历史上触发同一情景后「到达目标用时」（`bars_to_target`）的中位数（`median`）与四分位数（`q25`、`q75`），并截断在预测期 `horizon_bars` 以内。
   - 当历史先到目标样本为 0 时，回退到全部成熟样本用时或随机游走扩散尺度 $t \approx (|\Delta P / P| / \sigma)^2$。

---

## 3. 历史条件频率（L2 经验概率层）与对外表述契约

1. **逐点无前视事件研究（`scripts/base_rates.py`）**：
   - 在固定历史窗口上逐日重放，只使用当日 `t` 及之前的已收盘 K 线判定某情景是否触发。
   - 仅统计结果期已完全成熟的触发日（`t + horizon_bars <= cur_idx`），在未来 `H` 根 K 线内用三重屏障标定三种互斥结果：
     - **先到目标（`HIT_TARGET`）**
     - **先到失效位（`HIT_INVALIDATION`）**（同根 K 线同时触及目标与失效时，按保守原则判先到失效）
     - **到期未触及任一屏障（`EXPIRED`）**
2. **95% Wilson 置信区间与小样本 Beta-Binomial 收缩**：
   - 所有频率均附带样本数 `sample_size`（`n`）与 95% Wilson 置信区间 `target_ci_95`。
   - 当 `n < 30` 时，`low_sample = true`，脚本使用 Beta-Binomial（等效先验强度 $M = 20$）向市场基准指数同一情景的历史频率收缩，同时输出原始频率 `raw_target_freq` 与收缩后频率 `shrunk_target_freq`。
3. **对外表述铁律**：
   - 写入 `rule_scenarios.scenarios[*].historical_frequency`（及 `rule_scenarios.{bull,neutral,bear}.historical_frequency`）。
   - 对外统一表述为**「历史条件频率 X%（n = …，95% 区间 a%~b%）」**，低样本时必须同时注明「n < 30 低样本，已做 Beta-Binomial 收缩」，**严禁称为胜率或未来上涨概率**。
