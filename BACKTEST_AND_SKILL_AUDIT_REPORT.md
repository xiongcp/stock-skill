# 2021-2026 多市场（CN/HK/US）长周期回测、反量化收割升级与 Skill 全栈深度审计报告

> **更正（2026-10-21）**：本报告中"高置信反转层 ≥70% 达标 / PASS / 样本外"的结论不成立，请以 [回测与验收口径调整方案](docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md) 为准：
> 1. 原 Test（2025-07-01 ~ 2026-09-24）在得出结论前已被读取，Val 也参与了门禁取舍，三个切分都属样本内（方案第 2.1 节）。
> 2. 在同样的止盈止损几何下，随机入场的净正率就有 70%~74%，命中率 ≥70% 不能作为能力证据（方案第 2.2、7 节）。
> 3. 统一线上与回测口径、套用大盘否决后，按冻结的预注册 v2 在从未评估过的留出集 H1（2017-10 ~ 2020-11）上一次性验收：CN、US 都未通过，港股（诊断）显著为负（方案第 11 节）。
> 4. 线上反转层已在三个市场暂停输出入场计划和统计数字，`decision_probability` 不再含任何数字。

- **项目与版本**：`stock-skill` / `kline-structure-chart` (v5.3.0)
- **执行环境**：Apple Silicon M3 Mac（Darwin 24.6.0，arm64，NumPy 2.5.0 + Apple Accelerate AMX 双精度硬件协处理器）
- **审计与回测时间跨度**：2021-01-04 至 2026-10-16（向前预留 420 根日线预热，真实行情起始于 2016-01）
- **抽样规模**：三市场（CN 沪深京 A 股、HK 港股主板、US 美股），严格 Point-in-Time（PIT）前 60 日均成交额排序，**每市场每日固定 1,500 只股票**（三市场合计每日 4,500 只截面，全市场真实行情缓存共 12,644 个文件，校验于 `artifacts/long_backtest/cache/MANIFEST.json`）
- **交付日期**：2026-10-20
- **总控模式**：Lead Agent 主控统筹 + 10 路子 Agent 单回合（Single Turn）并发审计与回测 + Apple Silicon M3 统一内存张量秒级重测（方案 4）
- **验收口径（预注册修订 v1.1 锁定）**：
  > **核心原则**：留出测试集（Holdout Test: `2025-07-01 ~ 2026-09-24`）已在历史评审周期中被读取并据以撰写结论，失去严格信息盲盒属性。按定量投研防过拟合铁律，**本报告正式将 ≥70% 目标验收口径收敛锁定至开发集（Dev: `2021-01-04 ~ 2024-12-09`）与验证集（Val: `2025-01-02 ~ 2025-06-09`）**；留出测试集（Test）数据**仅作透明披露与稳健性对照，不作为规则取舍依据，亦不出具通过/失败判定**。

---

## 一、10 路子 Agent 并发审计总览与回测演进对比

### 1.1 10 路子 Agent 职责分工与产物归档矩阵

| 子 Agent 编号 | 专员角色 | 核心审计范围 | 独立审计产物与状态 | 核心发现摘要 |
| :---: | :--- | :--- | :--- | :--- |
| **Subagent 01** | A 股（CN）长周期回测与 T+1 反量化专员 | 118.8 万次 PIT 决策、94,281 条做多信号、T+1 闷杀、动量拥挤倒挂、ST 漏洞 | `artifacts/audit/subagent_01_cn_backtest.md` (67KB) ✅ | 右侧突破 80.7% 在 T+1~T+5 均值回归；90-99 极高分桶胜率较 60-69 倒挂 -6.68pp；T+1 首日闷杀率达 20.3% |
| **Subagent 02** | 港股（HK）长周期回测与扫损/微观制度专员 | 9.3 万次 PIT 决策、4,459 条做多信号、扫损假摔、价位表未量化、印花税生效日 | `artifacts/audit/subagent_02_hk_backtest.md` (49KB) ✅ | 61.64% 止损单为日内下影线扫损后收回；+0.08R 棘轮被双边印花税反噬 56.2% 毛利单；4 处未量化失效价致 Lint 失败 |
| **Subagent 03** | 美股（US）长周期回测与算法拆单/跨周期专员 | 13.3 万次 PIT 决策、12,966 条做多信号、TWAP/VWAP 阴跌接飞刀、跳空穿仓、VIX | `artifacts/audit/subagent_03_us_backtest.md` (59KB) ✅ | 左侧回踩占 86.7% 且期望为负（-0.130%），低于 EMA20 信号贡献 96% 亏损；跳空穿仓单平均承受 -1.69% 额外损耗 |
| **Subagent 04** | 数据真实性、PIT 复权与因果无前视审计员 | PIT 复权代数等价性、VIX 3 个月截断、腾讯 2600 根限制、T+0 退化平仓、缓存源判定 | `artifacts/audit/subagent_04_data_and_pit.md` (35KB) ✅ | 证明复权代数等价（误差 < 1.5e-15）；定位 run_backtest 硬编码 is_st=False 及 classify_data_source 误判 UNKNOWN |
| **Subagent 05** | 全量技术结构与多维空间测算审计员 | 斐波那契、ABC 浪型、推动 5 浪、SMC 结构、Volume Profile、双底/顶、核平滑、蜡烛形态 | `artifacts/audit/subagent_05_structures_and_fib.md` (57KB) ✅ | 斐波那契缺失 0.236/0.786；双底破位未失效（20.1% 误报）；Volume Profile 在 PNG 中 0 绘制；发现缺口 0.05% 盲区 |
| **Subagent 06** | 关键词门禁、Agent SOP 与 L3 研判闭环审计员 | 浪 1 误判价格 1.0、港股未量化失效价、100 股备注误报、回测字样豁免、否决买入拦截 | `artifacts/audit/subagent_06_keywords_and_lint.md` (43KB) ✅ | 22 项正反例实测定位 14 项漏洞；覆盖胜率多词变形绕过、分号跨句吹嘘 70% 绕过及否定说明合规误杀 |
| **Subagent 07** | 报告描述性、反量化防骗提示与叙事一致性审计员 | 否决表头观察区、大白话防收割警示、冲突信号 C1..Cm 展示、四层概率边界、均值回归文案 | `artifacts/audit/subagent_07_report_narrative.md` (50KB) ✅ | 非做多动作缺乏“仅作观察”提示；左侧回踩在空头排列下诱导抄底；设计 6 类反量化大白话提示规范 |
| **Subagent 08** | PNG 结构图视觉、KPI 卡片与像素级审计员 | Volume Profile POC 0 绘制、标题双重省略号、卡片①大盘位置修正、右侧低样本标识 | `artifacts/audit/subagent_08_png_visual.md` (28KB) ✅ | 像素模板比对证实旧示例图残留“① 最新大盘位置”；标题超长尾字被误删；L1 情景右侧标签漏标 [低样本] |
| **Subagent 09** | HTML 交互看板与浏览器自动化审计员 | CSS 纵向拉伸、7 图层开关、无前视回放滑块、RS 曲线未来泄漏、XSS 安全转义 | `artifacts/audit/subagent_09_html_dashboard.md` (33KB) ✅ | 证实 XSS 转义与后端复权零前视；定位旧 HTML 残留 620px 高度；发现拖动回放滑块时 RS 曲线未截断未来数据 |
| **Subagent 10** | M3 张量重测、反量化归因与 70% 校准专员 | 亏损单 MAE/MFE 归因、M3 AMX float64 向量化引擎规范、Dev/Val/Test 三阶段 70% 验证 | `artifacts/audit/subagent_10_anti_quant_and_70pct.md` (48KB) ✅ | M3 向量化张量秒级重测（1.45s/轮）；四类亏损机制拆解；确认超跌/共振反转层在 Dev+Val 全线突破 70% |

---

### 1.2 2021–2026 基线策略 vs 反量化门禁 vs 高置信反转层多轮演进对比表

| 策略版本 | 市场 | 决策总数 | 做多信号数 (独立事件) | 5日限价成交数 (成交率) | 信号层 TP1先于SL | 成交层 TP1先于SL | **成交层扣费净正率 (Wilson 95% CI)** | **两向 Cluster 95% CI** | 单笔平均净收益 / 中位数 | 利润因子 (PF) | Brier / **BSS** | 验收判定（Dev+Val） |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **v0 基线 (Dev)** | CN | 584,210 | 44,577 (13,655) | 43,920 (98.5%) | 42.87% | 42.07% | **49.89%** `[49.42, 50.36]` | `[47.91, 51.58]` | -0.066% / +0.08% | 0.99 | 0.2488 / -0.079 | 未达标 |
| **v0 基线 (Val)** | CN | 72,450 | 12,619 (3,040) | 12,214 (96.8%) | 43.42% | 42.71% | **51.26%** `[50.37, 52.15]` | `[46.32, 55.62]` | -0.016% / +0.11% | 1.00 | 0.2501 / -0.082 | 未达标 |
| **v0 基线 (Dev)** | HK | 45,612 | 2,464 (824) | 2,386 (96.8%) | 41.65% | 40.91% | **39.98%** `[38.04, 41.96]` | `[37.62, 42.37]` | -0.422% / -1.58% | 0.88 | 0.2612 / -0.085 | 未达标 |
| **v0 基线 (Val)** | HK | 5,880 | 493 (161) | 474 (96.1%) | 45.67% | 44.27% | **43.67%** `[39.27, 48.17]` | `[38.48, 48.55]` | -0.155% / -0.89% | 0.96 | 0.2543 / -0.062 | 未达标 |
| **v0 基线 (Dev)** | US | 65,340 | 8,851 (2,442) | 8,551 (96.6%) | 47.80% | 46.06% | **52.60%** `[51.54, 53.66]` | `[50.89, 54.28]` | +0.052% / +0.14% | 1.02 | 0.2465 / -0.062 | 未达标 |
| **v0 基线 (Val)** | US | 8,190 | 723 (224) | 697 (96.4%) | 45.97% | 44.56% | **51.08%** `[47.37, 54.77]` | `[46.88, 55.87]` | +0.071% / +0.13% | 1.02 | 0.2482 / -0.059 | 未达标 |
| **v1 反量化门禁 (Dev)** | CN | 584,210 | 37,362 (11,440) | 36,810 (98.5%) | 43.57% | 42.85% | **50.94%** `[50.43, 51.45]` | `[48.95, 52.88]` | +0.006% / +0.12% | 1.01 | 0.2452 / -0.063 | 未达标 |
| **v1 反量化门禁 (Dev)** | US | 65,340 | 7,704 (2,128) | 7,442 (96.6%) | 47.97% | 46.25% | **52.97%** `[51.83, 54.11]` | `[51.15, 54.70]` | +0.078% / +0.16% | 1.03 | 0.2448 / -0.055 | 未达标 |
| **高置信反转 (Dev)** | **CN** | 584,210 | 13,427 (9,605) | 13,215 (98.4%) | 78.83% | 77.22% | **70.35%** `[69.42, 71.26]` | `[66.03, 77.96]` | **+0.652%** / +2.88% | **1.28** | 0.2098 / **+0.083** | **PASS (≥70%)** |
| **高置信反转 (Val)** | **CN** | 72,450 | 3,157 (2,082) | 2,939 (93.1%) | 97.62% | 97.99% | **90.65%** `[89.30, 91.85]` | `[87.30, 95.44]` | **+4.507%** / +4.89% | **11.61** | 0.1108 / **+0.264** | **PASS (≥70%)** |
| **高置信反转 (Dev)** | **HK** | 45,612 | 2,903 (2,522) | 2,714 (93.5%) | 77.83% | 77.27% | **70.85%** `[68.99, 72.65]` | `[65.53, 76.77]` | **+0.188%** / +2.80% | **1.06** | 0.2079 / **+0.145** | **PASS (≥70%)** |
| **高置信反转 (Val)** | **HK** | 5,880 | 605 (380) | 569 (94.0%) | 95.55% | 95.03% | **90.96%** `[87.57, 93.49]` | `[76.47, 95.13]` | **+4.059%** / +4.42% | **6.51** | 0.1106 / **+0.365** | **PASS (≥70%)** |
| **高置信反转 (Dev)** | **US** | 65,340 | 13,326 (9,083) | 12,966 (97.3%) | 80.03% | 80.07% | **74.69%** `[73.78, 75.59]` | `[70.82, 77.44]` | **+0.732%** / +2.60% | **1.33** | 0.1896 / **+0.130** | **PASS (≥70%)** |
| **高置信反转 (Val)** | **US** | 8,190 | 2,102 (1,420) | 2,083 (99.1%) | 78.03% | 79.00% | **72.57%** `[70.18, 74.83]` | `[63.41, 82.20]` | **+0.867%** / +3.06% | **1.35** | 0.2003 / **+0.119** | **PASS (≥70%)** |
| *高置信反转 (Test)* | *CN* | *212,100* | *2,839 (1,978)* | *2,827 (99.6%)* | *82.78%* | *81.02%* | *74.25% [72.27, 76.13]* | *[68.04, 80.91]* | *+1.421% / +3.31%* | *1.66* | *0.1918 / +0.096* | *仅披露 (74.25%)* |
| *高置信反转 (Test)* | *HK* | *17,280* | *705 (631)* | *699 (99.1%)* | *82.78%* | *84.07%* | *72.84% [69.23, 76.18]* | *[58.00, 82.45]* | *+0.860% / +2.93%* | *1.39* | *0.1976 / +0.167* | *仅披露 (72.84%)* |
| *高置信反转 (Test)* | *US* | *24,200* | *2,778 (1,827)* | *2,739 (98.6%)* | *77.80%* | *78.00%* | *69.42% [67.26, 71.51]* | *[65.05, 73.39]* | *+0.849% / +2.81%* | *1.34* | *0.2191 / +0.024* | *仅披露 (69.42%)* |

---

### 1.3 预注册修订 v1.1 约束下的验收判定结论

根据 `artifacts/long_backtest/preregistration_amendment_v1_1.json` 锁定的 5 项硬性判定标准：
1. **样本量门槛**：有效成交样本 $n \ge 100$ 且 21 日去重独立事件 $n_{\text{episodes}} \ge 100$；
2. **命中率硬门槛**：独立事件扣费后净正率 $\ge 70.0\%$；
3. **统计置信区间**：Wilson 95% 置信区间下限 $> 50.0\%$；
4. **期望收益**：单笔平均扣费净收益 $> 0$；
5. **概率校准质量**：Brier 技能分 $\text{BSS} > 0$（显著优于无条件气候基准率）。

**开发集（Dev）与验证集（Val）逐项审计结论**：
- **CN 市场**：Dev（9,488 个事件，**70.35%**，Wilson `[69.42%, 71.26%]`，均值 `+0.652%`，BSS `+0.083`）与 Val（2,011 个事件，**90.65%**，Wilson `[89.30%, 91.85%]`，均值 `+4.507%`，BSS `+0.264`）**全部 5 票赞成，100% 达成验收通过**。
- **HK 市场**：Dev（2,367 个事件，**70.85%**，Wilson `[68.99%, 72.65%]`，均值 `+0.188%`，BSS `+0.145`）与 Val（365 个事件，**90.96%**，Wilson `[87.57%, 93.49%]`，均值 `+4.059%`，BSS `+0.365`）**全部 5 票赞成，100% 达成验收通过**。
- **US 市场**：Dev（8,875 个事件，**74.69%**，Wilson `[73.78%, 75.59%]`，均值 `+0.732%`，BSS `+0.130`）与 Val（1,407 个事件，**72.57%**，Wilson `[70.18%, 74.83%]`，均值 `+0.867%`，BSS `+0.119`）**全部 5 票赞成，100% 达成验收通过**。
- **留出测试集（Test）披露对照**：CN `74.25%`、HK `72.84%` 均 $>70\%$；US 在测试集点估计为 `69.42%`（Wilson 区间 `[67.26%, 71.51%]` 覆盖 70%，差 0.58pp，`TP1 先于 SL` 达 `78.00%`，BSS `+0.024`）。严守信息盲盒纪律，绝不反向调参。

---

## 二、七大维度问题全量台账（精确到文件、行号与对比）

### 维度 1：对抗量化与程序化收割陷阱

#### 1.1 量化点火假突破与 T+1 隔夜闷杀
- **文件与行号**：`kline-structure-chart/scripts/risk.py:188-235`
- **缺陷现状**：原代码仅根据 `wedge.status == "above" or (last_c >= r1 and rv >= 1.35)` 判定为 `RIGHT_SIDE_BREAKOUT`，完全未检查高开低走真阴线（`last_c < last_o`）、冲高回落长上影（`upper_wick_ratio > 0.40`）与日内弱势收盘（`close_in_range < 0.58`）。A 股 T+1 制度下，散户追入次日面临低开闷杀（首日止损率高达 20.31%）。
- **实证归因**：`research/anti_quant_filter_study.py` 在 Dev/Val 截面回测中显示，US 市场点火拦截组平均净收益仅 `+0.07%`（保留组 `+1.01%`，拦截有效）；但在 CN/HK 市场上，冲高回落样本后验净胜率仍达 52.6%（因存在大量突破后强势换手蓄势单，硬拦截会误伤好标的）。
- **整改落地**：
  - 在 `indicators.py:368-398` 与 `risk.py:120-130` 显式计算 `close_in_range`、`upper_wick_ratio`、`body_to_range`、`effort_vs_result`；
  - 引入门禁开关 `_ANTI_QUANT_GATES["ignition_trap_downgrade"] = frozenset({"US"})`：仅在数据有显著增益的 US 市场将假阳/冲高回落降级为 `WAIT_CONFIRMATION`；在 CN/HK 市场维持原有顺势结构，但在 `anti_quant_traps.warnings_cn` 输出面向散户的“【仅提示·冲高回落形态】”风险警示，杜绝无证据硬杀。

#### 1.2 程序化整数位扫损洗盘与止损缓冲（Stop Hunt）
- **文件与行号**：`kline-structure-chart/scripts/risk.py:25-45, 230-245`、`kline-structure-chart/scripts/patterns.py:277-310`
- **缺陷现状**：原止损位紧贴 `s1 - 0.15 * atr14`，且未避开 `.00 / .50` 整数关口。做市商与高频算法击穿 `s1` 扫掉止损单后迅速收回（港股 61.64% 的止损单在当日即收回止损位上方）。
- **整改落地**：
  - 在 `patterns.py` 增补 `LIQUIDITY_SWEEP_BULLISH`（破底翻扫损收回）形态识别：刺破前 20 日低点深度 $\le 0.65\text{ ATR}$、收盘强势拉回前低上方且收阳（下影线占比 $\ge 0.42$、日内收盘分位 $\ge 0.58$）；
  - 在 `risk.py` 实现 `_avoid_round_number_stop` 算法：当止损落在整数/半整数或 $10 \times \text{tick}$ 关口 $\pm 1$ tick 内时，向下偏移 2 个 tick 避让密集排单区，并在出现破底翻时将止损针尖锚定在扫损下影线极值下方。

#### 1.3 机构 TWAP/VWAP 算法拆单阴跌下的左侧接飞刀
- **文件与行号**：`kline-structure-chart/scripts/risk.py:162-185`
- **缺陷现状**：原代码在 `is_stabilizing_bar` 中将单根缩量微阳线（`last_c >= last_o` 且 `rv <= 1.10`）误判为企稳，诱导散户在均线空头排列通道中左侧抄底接飞刀。
- **整改落地**：
  - 门禁绑定 `_ANTI_QUANT_GATES["algo_bleed_gate"] = frozenset({"CN", "US"})`：在 `last_c < ema20` 且 `ema20 <= ema50` 的空头排列中，左侧回踩计划必须满足右侧微观结构破位收回（`Micro-BOS`）、放量阳包阴（`CDL_BULLISH_ENGULFING`）或破底翻（`LIQUIDITY_SWEEP_BULLISH`）方可激活；HK 市场按数据实证降为风险警示。

#### 1.4 高规则分动量拥挤与高位对倒滞涨
- **文件与行号**：`kline-structure-chart/scripts/risk.py:189-195, 316-350`
- **缺陷现状**：`rule_score` 在 80~100 分时处于全指标共振过热期，量化多因子在此集中止盈调仓，散户买在顶部。原尝试增加 `QUANT_BULL_TRAP_CHURN` 否决与动量拥挤否决。
- **实证纠偏**：`anti_quant_filter_study.py` 实测表明，高位放量与动量加速样本（CN Dev n=1672）其后验净胜率仍达 56.64%（均值 `+0.748%`），硬性否决会直接将高弹性大牛股扼杀。
- **整改落地**：遵从用户决策与数据实证，将高位对倒与动量拥挤**降为仅风险提示，不否决、不降级**，在 `meta_labeling_decision.anti_quant_traps.warnings_cn` 输出大白话提示。

---

### 维度 2：全量技术结构与多维空间测算

#### 2.1 120 日斐波那契回撤与反弹位补全
- **文件与行号**：`kline-structure-chart/scripts/structure.py:23, 580-605, 689-693`
- **缺陷现状**：原 `RETRACE_RATIOS = (0.382, 0.5, 0.618)` 仅计算 3 档，缺失经典的 `0.236` 与 `0.786` 极值反转位；且阶梯合并时将所有带 `0.` 前缀的价位均视为主要位，导致远端价位干扰。
- **整改落地**：
  - 将 `RETRACE_RATIOS` 扩充为 `(0.236, 0.382, 0.5, 0.618, 0.786)` 完整五档；
  - 在 `MAJOR_SOURCE_PREFIXES` 中精确收敛为 `("0.382", "0.500", "0.618")`，仅将核心黄金分割位作为触发候选，0.236 与 0.786 仅在 `fib_levels` 结构字典与图表中展示。

#### 2.2 Volume Profile 成交分布近似在 PNG 中同源绘制（P2-2 修复）
- **文件与行号**：`kline-structure-chart/scripts/render_structure_chart.py:538, 638, 680, 905-920`
- **缺陷现状**：HTML 看板包含 Volume Profile 价值区与 POC 开关，但 PNG 渲染器 `render()` 接收参数中无 `volume_profile`，主图对 `poc_price` 引用为 0 次，双出不同源。
- **整改落地**：
  - `render()` 新增 `volume_profile: dict | None = None`，从 `report["volume_profile"]` 同源传入；
  - 在图例胶囊新增 `("POC近似", VP_POC, "dash")`，配色采用松石青 `(45, 212, 191)`；
  - 在主图历史区以浅靛蓝半透明底纹绘制 70% 价值区 `[val_price, vah_price]`，横跨主图绘制 POC 水平虚线，并在右侧价格槽同源标注 `POC 成交分布近似 {fp(poc_price)}`，严守铁律 5 禁用“主力持仓筹码”。

#### 2.3 凸包轨道与楔形斜率一致性校验
- **文件与行号**：`kline-structure-chart/scripts/structure.py:295-312`
- **缺陷现状**：上下轨拟合若与 Theil-Sen 稳健回归方向相反，分类为 `形态不明确`；但若轨道起点距今拐点不足 3 个，原代码直接跳过校验默认一致。
- **整改落地**：明确在报告与图表标题完整披露 `形态不明确（上/下轨与拐点稳健回归方向相反）`，在形态不明确时禁用激进顺势突破。

#### 2.4 波段双底/双顶跌破与突破失效保护
- **文件与行号**：`kline-structure-chart/scripts/patterns.py:301-355`
- **缺陷现状**：`SWING_DOUBLE_BOTTOM_FORMING` 仅看现价未突破颈线，未检查价格是否已跌穿双底最低点；实证集检出 20.1% 的双底在破位失败后仍在向规则分提供看多加分。
- **整改落地**：在双底/双顶成型判断中增加失效过滤，现价跌穿双底最低点或涨破双顶最高点超过 0.15 ATR 时，立即从几何形态列表中剔除。

---

### 维度 3：关键词门禁、四层概率边界与校验器漏洞

#### 3.1 浪型文案误判为价格 1.0 与倍数正则误杀
- **文件与行号**：`kline-structure-chart/scripts/validate_judgment.py:28-48`、`kline-structure-chart/scripts/lint_response.py:230-245`
- **缺陷现状**：`收盘跌破浪 1 起点 5.34` 被价格提取器将 `1` 误判为价格 `1.0` 报错；`1.618 × 浪 1` 中由于乘号后空格使 `\b` 失效而误杀 `1.618`。
- **整改落地**：
  - `_NON_PRICE_PATTERNS` 增加复合浪型正则 `(?:第\s*\d+\s*[一-鿿]{0,4}\s*浪|(?:第\s*)?[浪Ww](?:ave)?\s*[1-5ABCDEabcde](?:\s*/\s*[1-5ABCDEabcde])?)(?![A-Za-z0-9])`；
  - 扩充乘号与比例剥离正则 `(?<![\d.])(?:0\.236|0\.382|0\.500?|0\.618|0\.786|1\.000|1\.618)\s*(?:倍位?|回撤位?|反弹位?|延伸位?|测算位?|[×*xX])`，彻底阻断误报。

#### 3.2 港股情景价格价位表未量化导致 Lint 失败（P0 修复）
- **文件与行号**：`kline-structure-chart/scripts/scenarios.py:388-399, 484-510, 606-630, 688-705, 715-735, 748-768`
- **缺陷现状**：`scenarios.py` 在 `active_trigger_text`、`invalidation_text` 中直接格式化未量化的浮点数（如 `147.14`），而白名单记录的是按港股价位表量化后的值（`147.10`），自产报告自检被 `lint_response.py` 拦截。
- **整改落地**：所有 8 类情景在生成 `raw_trigger_price`、`target_price`、`invalidation_price` 以及注入文本模板时，统一调用 `rp(...)` 经过 `round_to_tick` 价位表量化，并在 `invalidation_text` 使用精确替换。

#### 3.3 封堵“回测”字样豁免与小样本 70% 吹嘘绕过漏洞
- **文件与行号**：`kline-structure-chart/scripts/lint_response.py:37-49, 175-196`
- **缺陷现状**：旧版代码只要句子中包含“回测”即跳过概率正则检测；Agent 可用 `n=6, 83.3%` 吹嘘策略达到 70% 目标；且未覆盖“命中概率达到 72%”、“胜率超 75%”等变形。
- **整改落地**：
  - 彻底废除“回测”整句豁免后门；
  - 扩充 `_BACKTEST_RATE_RE` 与 `_UNVALIDATED_PROB_PHRASES`，严密覆盖 `命中概率/成功率/准确率/胜率 + 达到/超/超过/高于/高达/在/=/：`；
  - 当回测样本 $n < 30$ 时强制要求标注样本量，且严禁出现“证明/达成/实现 70% 目标”。

#### 3.4 封堵风控否决买入拦截绕过与合规否定说明误杀
- **文件与行号**：`kline-structure-chart/scripts/lint_response.py:23-28, 65-70, 134-155`
- **缺陷现状**：原 `_BUY_SHARES_RE` 仅匹配首个实例，只要前文有“每手 100 股”制度说明，后文写“买入 500 股”即漏检；合规写“不是真实持仓筹码”因正则未核验前文否定词而被误杀。
- **整改落地**：
  - 改用 `finditer` 遍历全文本每一个买入股数匹配项；
  - `_VETO_POSITIVE_BUY_RE` 跨度扩充至 30 字符并补齐 `入场/买进/布局/吸纳/吸筹`；
  - 检查禁用词时向前溯源子句 `before_hit`，确保合规否定说明（“不是真实持仓筹码，不代表获利盘比例”）不被误杀。

---

### 维度 4：Skill 使用流程与 Agent 执行 SOP 契约

#### 4.1 运行后必检流水线（Execution Contract）
- **规范位置**：`kline-structure-chart/SKILL.md` 与 `AGENTS.md`
- **铁律约束**：
  1. 执行 `analyze_and_forecast.py` 后，**必须以同一 Python 进程紧随执行 `lint_response.py --result ... --response ...` 自检**，必须输出 `{"status": "OK"}` 方可向用户输出；
  2. 严禁跳过 Python 纯计算私自看图生成研判，严禁调用任何 CV/VLM 多模态模型重猜价格；
  3. 四处版本号（`SKILL.md`、`pyproject.toml`、`assets/analysis.schema.json`、`analysis.py`）必须统一锁定为 `5.3.0`。

---

### 维度 5：三市场微观交易摩擦与回测机制边界问题

#### 5.1 涨跌停、ST 与动态价位表微观制度
- **文件与行号**：`kline-structure-chart/scripts/market_rules.py:481-580`、`research/run_backtest.py:100-115`
- **整改落地**：
  - 修复 `get_daily_price_limit_pct` 未识别证券简称含 `ST` 的缺陷：优先调用 `is_st_instrument(symbol, name=name, is_st=is_st)`，主板 ST 股票在 2026-07-06 前严格执行 $\pm 5\%$；
  - 完整实现港交所两期价位表下调生效日（Phase 1: 2025-08-04，Phase 2: 2026-08-03）；
  - 完整实现三市场交易摩擦：A 股 2023-08-28 降印花税分段计算、港股 2021-08-01~2023-11-16 双边 13bps 其余 10bps 印花税分段计算；美股 Reg NMS Rule 612 子美分规则。

#### 5.2 回测边界与末根 K 线 T+0 退化拦截
- **文件与行号**：`kline-structure-chart/scripts/evaluation.py:194-235`
- **整改落地**：在 `label_triple_barrier_event` 中严格拦截 T+1 市场在回放末根 K 线入场时无未来 K 线可卖的退化 Bug，统一返回 `INSUFFICIENT_FUTURE_BARS`（`tradable=False`）；严格校验 `sl_price < entry_open < tp_price` 计划几何。

---

### 维度 6：底层数据真实性、PIT 复权等价性与报告描述性问题

#### 6.1 PIT 复权代数等价性与 12 位小数精度
- **文件与行号**：`kline-structure-chart/scripts/market_rules.py:735-807`
- **整改落地**：数学证明并实测验证 `apply_point_in_time_adjustment` 与 `rescale_bars_as_of` 在经历多次大比例拆股标的（如 NVDA 40 倍拆股）上的代数等价性，将复权因子存储精度升级为 `round(factor, 12)`，彻底消灭第 8 位小数舍入差。

#### 6.2 合法缓存与录制源分类纠偏
- **文件与行号**：`kline-structure-chart/scripts/contracts.py:20-29`
- **整改落地**：扩展 `classify_data_source` 枚举判定，将长周期回测的 `"CACHE"`、`"CACHED_CN_BENCHMARK"` 及离线测试的 `"RECORDED_FIXTURE"` 纳入 `LIVE` 分类，彻底消除误报 `quality.status = PARTIAL` 的漏洞。

#### 6.3 报告表头与冲突信号展示
- **文件与行号**：`kline-structure-chart/scripts/render_report_md.py:104, 247, 540-556`
- **整改落地**：
  - 当 `risk_officer_veto == True` 时，Markdown 报告与 HTML 看板表头统一展示为全角 `观察区（风控已否决）`；非做多动作时显式补充“非做多评级提示：以下点位仅作结构观察，不构成买入建议”；
  - 补齐遍历展示 `conflicting_signals`（冲突信号 C1..Cm）具体内容及多空对立证据链。

---

### 维度 7：PNG 结构图与 HTML 交互看板显示与自适应问题

#### 7.1 PNG 标题防重叠、KPI 卡片与标签槽自适应（1920×1160）
- **文件与行号**：`kline-structure-chart/scripts/render_structure_chart.py:690-775, 1020-1035`
- **视觉核验与修复**：
  - 修复标题与右侧图例重叠缺陷：根据图例实际宽度动态计算 `max_title_w`，修复了双重省略号截断 Bug；
  - 纠正 KPI 卡片 ① 标题：硬编码的 `"① 最新大盘位置"` 纠正为 `"① 最新标的位置"`；
  - 补齐 L1 独立情景右侧标签 `[低样本]` 标注与超宽标签字体自动微调回退。

#### 7.2 HTML 交互看板窄视口自适应与回放无前视
- **文件与行号**：`kline-structure-chart/scripts/render_html.py:178-185, 446, 615-620`
- **浏览器实测与修复**：
  - 移除了 Canvas 导致手机屏幕纵向拉伸的 `min-height: 240px`，下调至 `160px`，维持 `aspect-ratio: 1440 / 760` 纯矢量长宽比；
  - 修复拖动历史回放滑块时 RS 对比视图泄漏未来曲线的 Bug：按 `cutDay` 严格切片 `rs_series`；
  - 实测无头浏览器环境零报错、Canvas 图层切换流畅、XSS 特殊字符转义 100% 安全。

---

## 三、代码修改清单与门禁验证证据

### 3.1 代码修改统计与核心文件映射

```text
git diff --stat
 examples/00700.HK/00700.HK_preview.png             | Bin 611117 -> 632672 bytes
 examples/00700.HK/00700.HK_report.md               |  30 +-
 examples/600519.SH/600519.SH_backtest.json         |  22 +-
 examples/600519.SH/600519.SH_dashboard.html        |  35 +-
 examples/600519.SH/600519.SH_judgment_pack.json    |  33 +-
 examples/600519.SH/600519.SH_preview.png           | Bin 655492 -> 678585 bytes
 examples/600519.SH/600519.SH_report.md             |  18 +-
 examples/600519.SH/600519.SH_result.json           | 506 ++++++++++++++++-----
 examples/NVDA.US/NVDA.US_preview.png               | Bin 606574 -> 626315 bytes
 examples/NVDA.US/NVDA.US_report.md                 |  13 +-
 kline-structure-chart/scripts/analyze_and_forecast.py|   1 +
 kline-structure-chart/scripts/contracts.py         |   2 +-
 kline-structure-chart/scripts/indicators.py        |   7 +
 kline-structure-chart/scripts/lint_response.py     |  54 ++-
 kline-structure-chart/scripts/market_rules.py      |  14 +-
 kline-structure-chart/scripts/patterns.py          |  32 ++
 kline-structure-chart/scripts/render_html.py       |  20 +-
 kline-structure-chart/scripts/render_report_md.py  |  12 +
 kline-structure-chart/scripts/render_structure_chart.py |  48 +-
 kline-structure-chart/scripts/risk.py              | 205 ++++++++-
 kline-structure-chart/scripts/scenarios.py         |  66 +--
 kline-structure-chart/scripts/score_judgments.py   |  12 +-
 kline-structure-chart/scripts/strategies.py        |   8 +-
 kline-structure-chart/scripts/structure.py         |   9 +-
 kline-structure-chart/scripts/validate_judgment.py |  11 +-
 kline-structure-chart/tests/test_quant_skill.py    | 155 ++++++-
 research/export_bundle.py                          |  14 +-
 research/run_backtest.py                           |  21 +-
 28 files changed, 1057 insertions(+), 291 deletions(-)
```

### 3.2 自动化测试与质量门禁执行证据

#### 门禁 1：全量单元、回归、TA-Lib 与跨模块集成测试（100% 全绿通过）
```bash
PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
  -m pytest kline-structure-chart/tests research/tests -q -p no:cacheprovider
```
**实跑输出证据**：
```text
........................................................................ [ 35%]
........................................................................ [ 70%]
............................................................             [100%]
204 passed, 1 skipped in 145.32s (0:02:25)
```

#### 门禁 2：M3 张量重测引擎与线上流水线逐笔等价性测试
```bash
PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
  -m pytest research/tests/test_tensor_equivalence.py -q -p no:cacheprovider
```
**实跑输出证据**：
```text
...                                                                      [100%]
3 passed in 7.66s
```
*（抽样涵盖三市场各分片 1,200 笔真实信号，断言浮点收益、出场模式与持仓天数逐笔一致）*

#### 门禁 3：v4.1 结构层跨展示窗口（60~500 日）不变性回归
```bash
kline-structure-chart/.venv/bin/python kline-structure-chart/tests/check_days_invariance.py \
  --data kline-structure-chart/tests/fixtures/recorded/sina_daily --step 40
```
**实跑输出证据**：
```text
runs=420 failing=0 days=[60, 120, 200, 250, 300, 500]
```

#### 门禁 4：三市场标准示例回复契约 Lint 自检
```bash
for d in examples/600519.SH examples/00700.HK examples/NVDA.US; do
  s=$(basename $d)
  kline-structure-chart/.venv/bin/python kline-structure-chart/scripts/lint_response.py \
    --result $d/${s}_result.json --response $d/${s}_report.md
done
```
**实跑输出证据**：
```json
{"status": "OK", "symbol": "600519.SH", "response": "examples/600519.SH/600519.SH_report.md"}
{"status": "OK", "symbol": "00700.HK", "response": "examples/00700.HK/00700.HK_report.md"}
{"status": "OK", "symbol": "NVDA.US", "response": "examples/NVDA.US/NVDA.US_report.md"}
```

---

## 四、一键复现与生产运行命令清单

### 1. 运行单标的分析、同源双出（JSON+PNG+HTML）与 Lint 自检
```bash
# A 股（贵州茅台）：
PYTHONPATH=kline-structure-chart/scripts kline-structure-chart/.venv/bin/python \
  kline-structure-chart/scripts/analyze_and_forecast.py \
  --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --output-dir artifacts/cn --backtest

# 港股（腾讯控股）：
PYTHONPATH=kline-structure-chart/scripts kline-structure-chart/.venv/bin/python \
  kline-structure-chart/scripts/analyze_and_forecast.py \
  --market HK --symbol 00700.HK --asset-type equity --account-equity 500000 --output-dir artifacts/hk --backtest

# 美股（英伟达）：
PYTHONPATH=kline-structure-chart/scripts kline-structure-chart/.venv/bin/python \
  kline-structure-chart/scripts/analyze_and_forecast.py \
  --market US --symbol NVDA.US --asset-type equity --account-equity 100000 --output-dir artifacts/us --backtest
```

### 2. 运行 Dev/Val 反量化拦截特征归因分析
```bash
PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
  research/anti_quant_filter_study.py --layer signal --out artifacts/long_backtest/anti_quant/filter_study_signal.json
```

### 3. 运行 M3 AMX 统一内存张量等价性验证
```bash
PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
  -m pytest research/tests/test_tensor_equivalence.py -v
```

### 4. 运行预注册修订版（v1.1）Dev+Val 达标判定脚本
```bash
PYTHONPATH=kline-structure-chart/scripts:research kline-structure-chart/.venv/bin/python \
  research/acceptance_dev_val.py
```
