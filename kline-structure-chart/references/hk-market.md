# 港股市场（大盘 + 个股）专项规则手册

1. **核心大盘基准**：恒生指数 (`HSI.HK`，Yahoo `^HSI`)、恒生科技指数 (`HSTECH.HK`，Yahoo 无历史日线，改用腾讯指数日线 `hkHSTECH`，不再用 3033.HK ETF 代替)。
2. **微观交易制度**：
   - 交易支持日内回转 (`T+0_TRADING_T+2_CLEARING`)，无单日涨跌停板限制。
   - 个股按每手股数 (`lot_size`) 与港交所价位表 (`round_to_tick`) 约束下单数量与最小变动单位。每手股数因股票而异（如腾讯 100、小米 200、汇丰 400、盈富基金 500），运行时取自腾讯行情快照（第 60 字段）；取不到时按 100 股假设，`market_microstructure.lot_size_source = ASSUMED_DEFAULT`，并在 `quality.notes` 中提示。
   - 价位表含 500 以上各档（0.50 / 1.00 / 2.00 / 5.00），并按生效日应用最小价位下调：Phase 1（2025-08-04）10–20 档 0.01、20–50 档 0.02；Phase 2（2026-08-03 规则修订生效）0.5–10 档 0.005。ETF 等交易所买卖产品不适用下调。
   - 印花税：2023-11-17 起买卖双方各 0.1%（`stamp_duty_buy_bps = stamp_duty_sell_bps = 10`），ETF 豁免。
3. **隔夜跳空（仅披露）**：
   - `forecast.py` 计算近 60 日隔夜跳空标准差与日波动率之比，写入 `volatility_scaling.overnight_gap_ratio` 供参考。收盘到收盘的波动已经包含跳空，因此不再额外放大区间。
4. **视觉配色**：默认红涨绿跌（`cn_red_up`），与 A 股一致。
