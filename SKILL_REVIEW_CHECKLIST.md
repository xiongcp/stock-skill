# `kline-structure-chart` Skill 与 `AGENTS.md` 审查清单

- 审查日期：2026-10-15（本机时区 America/Los_Angeles，此时北京/香港已是 10-16）
- 审查对象：`AGENTS.md`，以及 `kline-structure-chart/` 下的 `SKILL.md`、`references/*`、`assets/*`、`scripts/*`、`tests/*`、`artifacts/*`
- 审查方法：
  1. 通读全部源码和文档。
  2. 对照 [Agent Skills 规范](https://agentskills.io/specification)和 Anthropic 的技能编写最佳实践。
  3. 执行仓库规定的两项门禁命令。
  4. 写探针脚本复现可疑问题。
  5. 在浏览器里检查 HTML 看板。
  6. 联网核实三个市场的交易制度当前是否仍然有效。
- 本次只做审查，没有改动任何现有文件。下文代码位置如无特别说明，均相对于 `kline-structure-chart/` 目录。
- "所有 agent"按跨 Agent 产品理解，即 Claude Code、Codex、Cursor、Gemini CLI、Copilot、云端代码执行环境等能否都正确使用这个 skill。

---

## 0. 结论速览

> **修复进度（2026-10-15 更新）**：A-01 ~ A-06 六个 P0 已修复，并各自补了回归测试（`tests/test_quant_skill.py` 的 `test_a01_*` ~ `test_a06_*`）。门禁结果：pytest 21 passed；跨窗口不变性 runs=120 failing=0。另外用真实网络端到端跑通了 601318.SH、01810.HK、NVDA.US。`artifacts/` 里的旧产物是修复前生成的，标识仍是错的。
>
> **P1 制度规则修复进度**：R-01（ST 涨跌幅）、R-02（港股价位表）、R-03（港股双边印花税）、R-04（交易日历）、R-05（PDT）、R-06（`000xxx.SZ` 与 ETF 识别）已修复，测试见 `test_p1_*`。门禁结果：pytest 27 passed；跨窗口不变性 runs=120 failing=0。
> - R-04 的 CN 2027 年放假安排截至 2026-10 尚未发布，休市表只覆盖到 2026-12-31；超出部分在 `calendar_coverage` 和 `quality.notes` 中标注。
> - R-02 的 Phase 2 按 SFC 批准的规则修订生效日 2026-08-03 实施，HKEX 页面没有单独确认已上线。
> - 其余 P1 尚未处理：R-07 复权、R-08 HSTECH 映射、G 系列渲染问题、AG 系列、T 系列。
>
> **P1 计算类修复进度**：C-01 到 C-06 已修复，测试见 `test_c0*`。门禁结果：pytest 32 passed；跨窗口不变性 runs=120 failing=0。
> - C-01：指标改在固定 420 根历史上预热；EMA200 不足时显示 N/A，不再用 EMA60 冒充。
> - C-02、C-03：分位区间改为以收盘价为中心、按期限 split-conformal 校准；审计对象就是展示出来的区间；去掉了规则分漂移。
> - C-04、C-05：回测改为逐日回放线上真实计划，不再学习任何参数。
> - C-06：取数长度固定为 760 根，v5 主流程已纳入 `--days` 不变性测试。
> - 顺带解决 C-07（港股跳空重复放大）和 C-11（回放与线上不同模型）。
>
> **P1 渲染与数据源修复进度**：G-01、G-02、R-07、R-08 已修复，测试见 `test_g0*`、`test_r0*`。门禁结果：pytest 39 passed；跨窗口不变性 runs=120 failing=0；浏览器检查控制台零报错。
> - G-01：PNG 按 `color_scheme` 配色，美股为绿涨红跌；推演区说明、卡片⑤与页脚的颜色文字也随市场切换。
> - G-02：结构计算抽到 `scripts/structure.py`。主流程只计算一次，同一份结果同时写入 JSON 并交给 PNG；`analysis.py` 不再依赖渲染模块。
> - R-07：除权除息事件取自 Yahoo，按截面时点前复权。个股和 ETF 默认 `total_return`；取不到事件时按原始价，并在 `quality.notes` 披露。
> - R-08：恒生科技指数改用腾讯指数日线，不再用 3033.HK ETF 代替。
> - 至此 P1 只剩 AG 系列（文档/规范）和 T 系列（测试体系）。
>
> **P1 文档与测试体系修复进度**：AG、T 系列已处理。门禁结果：pytest 73 passed；跨窗口不变性在真实录制数据上 runs=420 failing=0，在合成序列上 runs=120 failing=0。
> - AGENTS.md 重写：
>   - 新增"0. 文档分工"与"铁律 0 数据真实性"。
>   - 命令全部改为相对路径。
>   - 运行期规则改为引用 SKILL.md，并删去未实现能力的宣称。
>   - SKILL.md 去掉绝对路径。
> - T-02：原 `sina_daily` 合成数据改名为 `synthetic_daily`。新增 `tests/record_fixtures.py` 联网录制真实行情到 `fixtures/recorded/`，带 MANIFEST，记录 sha256 与录制时间。
> - T-05：新增 TA-Lib 金标准对照（dev 依赖）。对照中发现 ATR 首值口径不同，已对齐 Wilder 口径。MACD/ADX 因播种口径差异，只比对收敛段。
> - T-06：网络与厂商数据异常 mock 覆盖 429、超时、DNS、非 JSON、空结果、OHLC 损坏、基准失败、腾讯为空、事件/VIX 失败等情形。厂商K线现在也会过 `validate_bars` 校验。
> - 新增 `test_docs_contract.py`，覆盖四项：无绝对路径、引用文件存在、目录树与代码同步、fixture 不冒充真实来源。
>
> **P2 修复进度（规范化与不实宣称）**：门禁结果：pytest 78 passed；跨窗口不变性 420/120 次全部通过；agentskills 规范校验 PASS；浏览器检查控制台零报错。
> - SKILL.md 改为规范 frontmatter（`metadata`、`compatibility`、中英双语 description，含不适用场景），正文新增"何时使用""运行后必检""回复契约""失败处理"四章；版本同步为 5.1.0。
> - C-13 清理：
>   - 形态改名：`SWING_DOUBLE_*`、`KERNEL_SMOOTHED_*TREND`、`BEARISH_BOS`。
>   - 删去头肩、MACD 背离、CHoCH、正交特征、`confidence_score` 等宣称；HTML 去掉"筹码峰"和悬停"公式证据"的说法。
>   - 同步修正 math-patterns.md、failure-modes.md、hk-market.md。
>   - `test_no_unimplemented_capability_claims` 防止这些宣称回流。
> - `defaults.json`：从未被代码读取且参数与代码冲突，已删除。`analysis.schema.json` 改为 `validate_report` 的唯一校验来源（`test_p2_schema_file_drives_report_validation`）。

**总评**：设计理念合理，包括算图分离、先大盘后个股、因果无前视、窗口不变性、术语边界和证据先行。工程上也有不少扎实的地方（见第 6 节）。问题在于两点：

- 文档里宣称的能力明显多于代码实际实现的能力，这是系统性的。
- 有 **6 个 P0 问题会直接让输出失真**：
  - 静默生成合成行情
  - 合成数据在图上没有任何警示
  - 数据质量标识在两个方向上都会判错
  - 美股 VIX 写死成一个固定值
  - 未收盘的 K 线被当作已收盘
  - 非白名单代码直接走合成数据

**P0 修复之前，不建议把这个 skill 交给任何 agent 去面向真实用户使用。**

| 问题 | 结论 |
|---|---|
| skill 与 AGENTS.md 是否合理 | 理念合理。但较大比例的能力声明与实现不符或已经过时，见 3.7 和 3.8 |
| 能否用到所有 agent 中 | 文件格式基本符合开放标准，但**现在做不到开箱即用**，原因有五个：<br>1. 没有放进任何 agent 的技能发现目录<br>2. frontmatter 里有非标准字段<br>3. 写死了本机绝对路径<br>4. 依赖 Python≥3.12、uv 和外网<br>5. 在无网环境里会**静默生成假行情**<br>详见 3.5 和 3.6 |
| 规范化 | `name`、`description` 和目录结构都合规。需要做的是：<br>1. 把 `version`、`author` 移进 `metadata`<br>2. 补上 `compatibility`<br>3. 补上"输出契约"、"失败处理"和"不适用场景"三部分 |
| 真实性 | ❌ 有 P0 问题。不在白名单里的 A 股（例如 601318.SH）**在联网状态下也不会发任何请求**，直接生成合成行情。PNG 页脚还写着"新浪原始值" |
| 可靠性与正确性 | ❌ 主要问题有四类：<br>1. 多条制度规则已过时：ST 涨跌幅、港股价位表、港股印花税、交易日历、PDT<br>2. 代码里叫 "EMA200" 的值，实际是 EMA50 或 EMA60<br>3. 保形预测的"校准审计"测量的并不是图上展示的区间<br>4. 中位数预测 q50 在 6 个标的中有 5 个误差比"价格不变"这一朴素基准还大 |
| 是否需要进一步优化 | 需要。按第 4 节从 P0 到 P3 的顺序修复。第 5 节是可以长期复用的勾选清单 |

问题统计如下。P0 指会造成失真或误导，P1 指结果错误或与文档严重不符，P2 指规范、可靠性或可移植性问题，P3 指措辞或整洁度问题。

| 级别 | 数量 |
|---|---|
| P0 | 6 |
| P1 | 30 |
| P2 | 39 |
| P3 | 11 |

---

## 1. 审查范围

| 文件 | 规模 | 说明 |
|---|---|---|
| `AGENTS.md`（仓库根目录） | 124 行 | 面向编码 agent 的工程规范 |
| `SKILL.md` | 75 行，正文 69 行 | 技能入口 |
| `references/*.md` | 5 个文件，共 104 行 | 公式字典、三个市场的制度说明、常见错误清单 |
| `assets/analysis.schema.json` | — | JSON Schema |
| `assets/defaults.json` | — | 默认参数 |
| `scripts/*.py` | 13 个文件，约 6,700 行 | 其中 `render_structure_chart.py` 有 2,021 行 |
| `tests/` | 2 个脚本 | 另有 5 个 fixture |
| `artifacts/` | 24 个产物 | 2026-10-15 20:12（PDT）生成 |

---

## 2. 验证证据（本轮实际执行）

### 2.1 仓库规定的门禁命令

```text
$ uv run --directory kline-structure-chart pytest -v
...
14 passed in 3.56s

$ uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/sina_daily --step 40
runs=120 failing=0 days=[60, 120, 200, 250, 300, 500]
```

> ⚠️ 两项门禁都通过了，但**用的全是合成数据**。测试一律设置 `allow_network=False`，`tests/fixtures/sina_daily/*.json` 本身也是合成数据（见 T-02）。所以这两项门禁发现不了下文大部分问题。

### 2.2 Agent Skills 规范校验

校验脚本是按 agentskills.io 规范写的。

```text
name='kline-structure-chart' len(description)=143 body_lines=69
frontmatter fields=['author', 'description', 'license', 'name', 'version']
硬编码绝对路径=['/Users/admin-1234/work/stock-skill/kline-structure-chart']
ISSUES:
 - 非规范顶层字段（应移入 metadata）: ['author', 'version']
 - 未声明 compatibility（本技能依赖 Python>=3.12、uv、numpy/pillow 与外网行情）
```

### 2.3 探针复现

只读调用 `scripts/` 里的函数，不修改任何东西。

```text
601318.SH -> source = DETERMINISTIC_OFFLINE_FIXTURE | 联网模式下网络请求 0 次 | 末根收盘 337.6
300059.SZ -> source = DETERMINISTIC_OFFLINE_FIXTURE | 末根收盘 150.95
[601318.SH] sina='601318.sh'        ← 正确应为 sh601318
[000002.SZ] asset_type='index'      ← 万科A 被识别成指数（lot_size=1，无涨跌停，无 T+1）
[000858.SZ] asset_type='index'      ← 五粮液 被识别成指数
[01810.HK]  Yahoo 代码 '01810.HK'   ← 正确应为 1810.HK，结果回退到合成数据
[510300.SH] asset_type='equity'     ← ETF 被识别成个股
ST 主板 → 0.05 ；ST 创业板 → 0.05
HK round_to_tick(612.30) → 612.4    ← HKEX 规定 500–1000 档最小价位为 0.50
HK round_to_tick(1234.3) → 1234.4   ← 1000–2000 档最小价位为 1.00
HK stamp_duty_sell_bps = 10         ← 只向卖方收取
is_trading_day: CN 2025-06-02 / CN 2026-02-23 / US 2025-01-09 / 2027-01-01（三个市场） → 全部返回 True
--days 120 与 --days 300 对比（同一标的、同一天、离线）：收盘 1543.44 vs 1886.6，rule_score 31.9 vs 81.0
策略证据字符串写的是 "Close(1543.44) vs EMA200(1606.05)"，而 snapshot 里 ema200=None、ema50=1606.05
```

### 2.4 抽检既有产物 `artifacts/`

这批产物的生成时间是 2026-10-15 20:12（PDT），即香港时间 10-16 11:12，港股当时在早市交易中。

| 标的 | 数据源 | quality 标识 | 最后一根 K 线 | ema200 | vix |
|---|---|---|---|---|---|
| 000001.SH | SINA_LIVE_DAILY | OK | 2026-10-15 | None | — |
| 600519.SH | SINA_LIVE_DAILY | OK | 2026-10-15，收盘 1312.97 | None | — |
| 00700.HK | YAHOO_DAILY | **PARTIAL，并警告"使用确定性离线行情样本"** | **2026-10-16，成交量 7,892,739（前一日 20,676,017）** | None | — |
| HSI.HK | YAHOO_DAILY | **PARTIAL（误标）** | **2026-10-16，成交量 0.0** | None | — |
| NVDA.US | YAHOO_DAILY | **PARTIAL（误标）** | 2026-10-15 | None | **19.5（写死）** |
| SPY.US | YAHOO_DAILY | **PARTIAL（误标）** | 2026-10-15 | None | **19.5（写死）** |

下表比较两组覆盖率：

- `calibration_audit`：报告里自称的覆盖率。
- backtest OOS：对图上实际展示的区间做的样本外实测。

| 标的 | 自称 50% / 80% 覆盖率 | 展示区间实测 50% / 80% 覆盖率（样本数） | q50 平均绝对误差 | "价格不变"基准的平均绝对误差 |
|---|---|---|---|---|
| 000001.SH | 0.583 / 0.867 | 0.40 / 0.85（20） | 2.23% | 2.22% |
| 600519.SH | **0.733** / 0.833 | **0.35** / 0.85（20） | 4.61% | 4.32% |
| 00700.HK | 0.683 / 0.967 | 0.522 / 0.957（23） | 4.91% | 4.37% |
| HSI.HK | 0.533 / 0.917 | 0.565 / 0.826（23） | 2.55% | 2.50% |
| NVDA.US | 0.667 / 0.85 | 0.458 / 0.875（24） | 6.37% | 6.16% |
| SPY.US | 0.717 / 0.85 | 0.542 / 0.833（24） | 2.16% | 2.24% |

从这两张表可以得出三点：

- 报告自称的覆盖率和图上区间的实际覆盖率差异很大，600519.SH 的 50% 区间自称 0.733，实测只有 0.35。
- q50 中位数相对"价格不变"基准没有优势：6 个标的里 5 个更差，1 个略好。
- 实测样本只有 20 到 24 个，覆盖率估计本身的误差大约在 ±0.1。

### 2.5 浏览器检查（Chrome DevTools 打开离线 HTML）

- ✅ `00700.HK_dashboard.html`：控制台 0 条消息。5 个回放截面加最新截面来回切换、7 个图层开关、RS 视图切换都没有报错。
- ❌ 用 `--days 60` 生成的看板：5 个回放截面中有 3 个，在展示窗口内能取到的 K 线为 0 根。此时前端会退回显示全部 60 根 K 线，也就是说截面日期之后的 K 线也被画了出来。
- ❌ 楔形轨道：结构层上轨实际起点 idx=100、下轨 idx=119，HTML 却一律从 `nBars-60` 处开始画，画出来的斜率是错的。
- ❌ 用合成数据生成的 PNG：
  - 标题是"贵州茅台 (600519.SH)"，图上收盘价 1543.44，而同日真实收盘价是 1312.97。
  - 页脚写着"新浪原始值"。
  - 整张图没有任何合成数据的提示。

### 2.6 联网核实制度规则

均为 2026-10 时点的口径。

| 规则 | 代码或文档现状 | 当前有效口径 | 来源 |
|---|---|---|---|
| 沪深主板 ST 涨跌幅 | ±5% | 2026-04-24 发布修订后的交易规则，**2026-07-06 起施行 ±10%**；双创板块 ST 本来就是 ±20% | [中证网](https://cs.com.cn/tj/02/01/202506/t20250627_6498570.html)、[同花顺 2026-04-10](https://news.10jqka.com.cn/20260410/c675906757.shtml)、[财联社](https://www.cls.cn/detail/2069694)（均为新闻转述，建议再用交易所规则原文复核） |
| 港股最小价位（spread table） | ≥200 一律 0.20 | 500–1000 档 0.50，1000–2000 档 1.00，2000–5000 档 2.00，5000 以上 5.00。Phase 1 于 **2025-08-04** 上线：10–20 档由 0.02 降为 0.01，20–50 档由 0.05 降为 0.02。Phase 2（0.5–10 档由 0.01 降为 0.005）排期 2026-08-03，是否已落地需看港交所公告 | [HKEX Reduction of Minimum Spreads](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads?sc_lang=en) |
| 港股印花税 | 仅卖方 0.1% | 2023-11-17 起**买方和卖方各收 0.1%** | [KPMG](https://kpmg.com/cn/en/home/insights/2023/11/tax-alert-21-hk-the-reduced-stamp-duty-rate-on-hk-stock-transfer-will-take-effect-from-17-nov-23.html)、[IRD 说明](https://www.gov.hk/en/residents/taxes/docs/IRSD123(E).pdf)、[HKEX CT/158/23](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2023/ce_SEHK_CT_158_2023.pdf) |
| A 股 2026 年春节休市 | 日历里缺 2026-02-23 | 2-15 至 2-23 休市，2-24 开市 | [上交所休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/list/)、[澎湃](https://m.thepaper.cn/newsDetail_forward_32603402) |
| A 股 2025 年端午 | 日历里缺 2025-06-02 | 5-31 至 6-2 放假 | [人民日报全文](https://paper.people.com.cn/rmrb/html/2024-11/13/nw.D110000renmrb_20241113_5-03.htm)、[新华网](https://www.news.cn/politics/20241112/46dcf0de740844dd824ff54b5be5af2e/c.html) |
| 美股 2025-01-09 | 当作交易日 | 卡特总统国葬日，NYSE 和 Nasdaq 都休市 | [ICE/NYSE](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx)、[Nasdaq](https://www.nasdaq.com/articles/stock-market-will-be-closed-january-9-2025) |
| 美股 PDT 规则 | "PDT_APPLIES"、"PDT 约束" | SEC 于 2026-04-14 批准 FINRA 4210 修订，取消 PDT 认定和 2.5 万美元门槛，2026-06-04 生效，券商最晚可过渡到 2027-10-20 | [Schwab](https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum)、[E*TRADE](https://us.etrade.com/knowledge/library/margin/pattern-day-trading-rule-change)（券商文章，建议再用 SEC Release 34-105226 原文复核） |

---

## 3. 问题清单

### 3.1 数据真实性（P0）

| ID | 级别 | 位置 | 问题 | 证据 | 修复建议 |
|---|---|---|---|---|---|
| A-01 | P0 | `scripts/market_rules.py:127`、`scripts/data_gateway.py:198-231` | 非白名单 A 股的新浪代码被拼成 `601318.sh`，不满足 `startswith(("sh","sz"))`，所以**联网时一次请求都不发**，直接生成合成行情。北交所同样没有 `bj` 分支。港股非白名单代码的 Yahoo 符号也是错的（应为 `1810.HK`，实际生成 `01810.HK`） | 2.3 | 按交易所后缀推导：`.SH`→`sh`+代码，`.SZ`→`sz`+代码，`.BJ`→`bj`+代码。港股去掉前导 0，转成 4 位 Yahoo 代码。解析失败时直接报错 |
| A-02 | P0 | `scripts/analyze_and_forecast.py:37`、`scripts/data_gateway.py:223-231,146-157` | 有四个叠加的问题：<br>1. 生产主流程写死了 `allow_synthetic_fallback=True`，CLI 没有开关可以关掉。<br>2. 网络或解析异常被 `except Exception: pass` 吞掉，随后静默回退。<br>3. 合成序列在最后 45 根 K 线里**人为注入了"双底加突破"结构**。<br>4. 合成序列的起始价格来自写死的旧价位表 | 2.3、2.5 | 生产路径默认禁止合成数据，出错时以非零码退出。只有测试才能显式使用 `--allow-synthetic`。失败原因写进 `provenance.errors` |
| A-03 | P0 | `scripts/render_structure_chart.py:1770`；`scripts/render_html.py` 全文没有渲染 `quality` | PNG 不知道数据从哪里来，页脚固定写"新浪原始值"。HTML 只在副标题里用小字显示 `data_source`，`quality.warnings` 从来不渲染 | 2.5 | 只要不是实时数据，PNG 和 HTML 都要加满幅水印和红色横幅。PNG 页脚按实际数据源输出 |
| A-04 | P0 | `scripts/analysis.py:421,427` | 数据质量标识在两个方向上都会判错：<br>1. 判定只认 `LIVE`、`LOCAL` 两个子串，所以**真实的 Yahoo 数据**被标成 PARTIAL，还附带"离线样本"警告。<br>2. 合成数据也只标 PARTIAL。<br>3. 基准指数的数据来源不参与判定 | 2.4（4 份真实数据都被误标） | 改用来源枚举：LIVE、DELAYED、LOCAL_FILE、SYNTHETIC。标的和基准分别判定，SYNTHETIC 一律判为 `UNAVAILABLE` |
| A-05 | P0 | `scripts/data_gateway.py:334-335` | 美股的 `vix` 写死为 19.5，并写进 `market_regime.evidence` 和 `volatility_scaling`，看起来像真实数据。因为 19.5 < 20，VIX 放大永远不会触发。`earnings_date` 永远是 None | 2.4 | 真实拉取 `^VIX`；拉不到就填 null 并标注"未获取"。财报日要么接入数据源，要么从文档里删掉这项能力 |
| A-06 | P0 | `scripts/data_gateway.py:50,91`、`scripts/analysis.py:155` | 未收盘的 K 线被当作已收盘：取数时一律写 `complete: True`。v5 主流程传入 `intraday=None`，使得 `split_live_bar` 实际上不起作用 | 2.4：港股早市的半根 K 线参与了全部计算，并且成为 `as_of` | 按交易所时区和收盘时间判断最后一根是否完整。不完整的就拆成 live bar，并在输出里标注 |

### 3.2 正确性：市场制度

| ID | 级别 | 位置 | 问题 | 修复建议 |
|---|---|---|---|---|
| R-01 | P1 | `scripts/market_rules.py:162-163`、`references/cn-market.md:8`、`AGENTS.md:35`、`tests/test_quant_skill.py:83` | ST 一律按 ±5% 处理，这条规则已经过时（见 2.6），并且双创板块的 ST 应为 ±20%。另外 `"ST" in symbol` 对股票代码永远不会命中，所以 ST 识别实际上从不生效 | 按板块和日期返回涨跌幅。ST 判定改用名称或外部标记字段 |
| R-02 | P1 | `scripts/market_rules.py:180-194`、`tests/test_quant_skill.py:94` | 港股价位表缺 500 以上的各档。2025-08 的减价位改革也没有体现 | 把价位表做成带生效日期的数据文件，并用档位边界值做测试 |
| R-03 | P1 | `scripts/market_rules.py:226` | 港股印花税只向卖方收取，正确做法是双边收取。ETF 没有豁免 | 双边计费，ETF 豁免 |
| R-04 | P1 | `scripts/market_rules.py:8-37` | 交易日历有三处缺漏：CN 2025-06-02、CN 2026-02-23、US 2025-01-09。日历只覆盖到 2026 年，**2027-01-01 起三个市场的所有工作日都会被当成交易日**。离现在只剩约 2.5 个月 | 改用 `exchange_calendars` 一类的库，或者按年维护数据文件。超出覆盖范围时直接报错 |
| R-05 | P1 | `scripts/market_rules.py:230`、`references/us-market.md:5`、`AGENTS.md:37` | PDT 规则已经废止，目前处于过渡期，相关字段和文案过时 | 更新字段和文案，注明过渡期 |
| R-06 | P1 | `scripts/market_rules.py:111` | `000xxx.SZ` 开头的深市主板股票（万科、五粮液、美的等）被推断为指数，于是没有涨跌停、没有 T+1，lot 被设为 1 | 按"交易所 + 代码段"推断。只有 `000xxx.SH` 和 `399xxx.SZ` 是指数。ETF（51/56/58/15 开头）单独识别 |
| R-07 | P1 | `scripts/data_gateway.py:298-303`、`scripts/market_rules.py:241-281` | 复权函数写好了，但没有公司行动的数据源，所以 `split` 模式对 A 股实际上什么都不做。新浪返回的是不复权价格，除权缺口会被误识别为 FVG、跳空或破位 | 接入除权除息数据，或改用前复权数据源。在 `meta` 里标注实际的复权状态 |
| R-08 | P1 | `scripts/data_gateway.py:207-217` | `HSTECH.HK` 被映射成 `3033.HK`（这是一只 ETF），价格量级和指数完全不同，而且没有任何披露 | 映射到正确的指数代码，或者在 `meta` 里标注"ETF 代理" |
| R-09 | P2 | `scripts/market_rules.py:210`、`scripts/risk.py:125-127` | A 股 lot 一律按 100 股。科创板单笔申报不少于 200 股，超出部分可以按 1 股递增；北交所也可以按 1 股递增（这是常识性规则，本轮没有联网复核） | 按板块分别定义最小申报数量和递增步长 |
| R-10 | P2 | `scripts/market_rules.py:195-196` | A 股 ETF 和基金的最小价位 0.001 没有处理，美股 1 美元以下的价位 0.0001 也没有处理。`money()` 统一保留两位小数 | 按品种和价格选择精度 |
| R-11 | P2 | `scripts/forecast.py:166` | 注册制新股上市前 5 个交易日不设涨跌幅，这一点没有建模。`is_ipo_no_limit` 永远是 False | 用上市日期字段来驱动 |
| R-12 | P2 | `scripts/market_rules.py:223`、`scripts/analyze_and_forecast.py` | 港股配色写死为 `cn_red_up`，文档里没有说明。CLI 也没有暴露 `color_scheme` 参数，尽管 `AnalysisRequest` 里有这个字段 | 增加 `--color-scheme` 参数，并在 `references/hk-market.md` 里写明 |

### 3.3 正确性：计算与统计

| ID | 级别 | 位置 | 问题 | 修复建议 |
|---|---|---|---|---|
| C-01 | P1 | `scripts/analysis.py:208,245`、`scripts/strategies.py:24,106,153,303` | 指标只在 120 根 K 线的结构窗口上计算，所以 EMA200、SMA250 永远是 None，被静默替换成 EMA60 或 EMA50。`formula_verified` 却仍然写 "EMA200(…)"，**证据字符串与实际不符**。作为否决依据的大盘 `regime_score` 也受影响 | 在固定的 `hist`（420 根，与 `--days` 无关）上计算指标。数据不够时明确标注，而不是悄悄替换 |
| C-02 | P1 | `scripts/forecast.py:214-255,358` | 校准与展示脱节：<br>1. 校准只用终点期 H 的 \|R\| 打分，并且以 0 为中心。<br>2. 实际展示的区间以 drift 和类比结果的混合为中心，经过 max/min 拼接、涨跌停截断和价位取整。<br>3. 因此 `calibration_audit` 审计的对象不是图上的区间。<br>4. "逐期限边际覆盖率校准"的说法不成立 | 对每个 h 分别计算以预测中心为基准的残差分位数。用滚动样本外检验审计**实际输出**的区间，并在报告里引用回测得到的实测覆盖率 |
| C-03 | P1 | `scripts/forecast.py:257-259` | q50 被 rule_score 推着漂移，而文档明确说 rule_score 不是概率。实测 q50 相对"价格不变"基准没有优势（见 2.4） | 去掉这个漂移，或者先用回测证明有效再启用。报告里强制披露 q50 相对"价格不变"基准的误差 |
| C-04 | P1 | `scripts/evaluation.py:183-239` | 回测用的入场信号和止损止盈（1.5 / 2.2 倍 ATR）与 `risk.py` 实际给用户的计划完全不同，所以不能当作这份计划的历史验证 | 回测直接回放 `compute_triple_barrier_plan` 每天的输出 |
| C-05 | P1 | `scripts/evaluation.py:242-244` | 交易笔数 ≤3 时，样本外数据就等于全部样本，训练数据混进了样本外评估 | 样本不足时返回"样本不足"，不再计算 |
| C-06 | P1 | `scripts/analysis.py`（v5 流程）、`scripts/data_gateway.py:288` | v5 主流程不满足 `--days` 不变性。`datalen=max(450, days+260)` 让历史长度随 `--days` 变化，类比检索、回测和回放都会跟着变。离线时整条合成序列都会变（见 2.3） | 让取数长度与 `--days` 解耦，并把不变性检查扩展到 `run_pipeline` |
| C-07 | P2 | `scripts/forecast.py:195-196` | 港股的"隔夜跳空修正"是在已经包含跳空的收盘到收盘波动率上再乘一个系数，跳空风险被算了两次 | 如果要建模跳空，应该拆分收益来源，而不是乘系数 |
| C-08 | P2 | `scripts/forecast.py:51-52`、`references/math-patterns.md:39` | 注释和文档写的约束是 `t_c+H ≤ t_now−L_patch`，代码实际是 `−max(5, L/2)`，允许最多 10 根重叠。这不会造成前视泄漏，但"无重叠"的说法不对 | 统一代码和文档 |
| C-09 | P2 | `scripts/evaluation.py:246-270`、`scripts/risk.py:144` | 两处名不副实：<br>1. 所谓 "Meta-Labeling" 只是手写的加权和，`meta_prob` 和 `confidence_score` 都不是概率，与 AGENTS.md 的术语边界冲突。<br>2. 只做了一次 50/50 切分，却称为 "Walk-Forward Purged/Embargo" | 改名（例如 `filter_score`），或者真正实现滚动和清洗切分 |
| C-10 | P2 | 回测输出 | 样本外只有 16 到 21 笔交易，元过滤之后只剩 4 到 15 笔，没有置信区间 | 输出样本量警告和 bootstrap 置信区间 |
| C-11 | P2 | `scripts/evaluation.py:26,36` | 因果审计有三个缺口：<br>1. 用固定的 0.04 阈值，而生产流程用自适应阈值。阈值会随新数据变化，导致历史拐点被重新定义。<br>2. 审计只用前缀窗口，覆盖不到生产中的滑动窗口。<br>3. 没有覆盖结构层、预测和 SMC | 用生产配置做滚动回放审计 |
| C-12 | P2 | `scripts/analysis.py:108`、`scripts/evaluation.py:327` | 回放和覆盖率评估用 `regime_score` 顶替 `rule_score`，跟线上预测不是同一个模型 | 回放时调用完整的流水线 |
| C-13 | P2 | `scripts/patterns.py:280-286,356-384,389-396,515-593` | 宣称的以下能力没有实现：<br>1. 核平滑极值和头肩形态。几何形态用的其实是原始高低点的 ZigZag。<br>2. WLS 通道。实际是对平滑后的序列做 OLS，r² 会被平滑抬高。<br>3. MACD 背离。`macd_hist` 参数传进来了但没有用到。<br>4. CHoCH。代码没有把它和 BOS 区分开 | 要么实现，要么删掉这些宣称 |
| C-14 | P3 | `scripts/indicators.py:371,92`、`scripts/evaluation.py:89`、`scripts/market_rules.py:208` | 四个小问题：<br>1. mom10 用的是 `closes[-10]`，实际是 9 日动量。<br>2. 价格完全不动时，第一个 RSI 值是 100。<br>3. 时间屏障实际持有 16 根，不是 15 根。<br>4. `False if … else False` 是冗余写法 | 顺手修掉 |

### 3.4 渲染层

| ID | 级别 | 位置 | 问题 | 修复建议 |
|---|---|---|---|---|
| G-01 | P1 | `scripts/render_structure_chart.py:54-55,1157,1777` | PNG 的涨跌色写死为红涨绿跌，`render()` 也没有配色参数，所以**美股 PNG 违反了 `us_green_up` 约定**。页脚对港股、美股也写着"A股红涨绿跌" | 把 `color_scheme` 传进 PNG 渲染器 |
| G-02 | P1 | `scripts/analysis.py:25-40`、`scripts/render_structure_chart.py:1204-1236` | **违反"算图分离"铁律**：<br>1. PNG 渲染器自己从 K 线重新计算结构、阶梯和推演。<br>2. 计算层反过来依赖渲染模块 | 把结构计算抽成单独的 `structure.py`。PNG 只读取 report |
| G-03 | P2 | `scripts/render_html.py:663-670` | 楔形轨道的起点固定画在 `nBars-60`，跟真实支点位置不一致（见 2.5） | 用 `upper.start.idx` 加上偏移量 |
| G-04 | P2 | `scripts/render_html.py:470-471` | `--days` 小于 120 时，回放会显示截面之后的 K 线（见 2.5） | 回放用到的 K 线随快照一起导出 |
| G-05 | P2 | `scripts/render_structure_chart.py:998` 等 | 推演标签用 `:.0f` 取整，对低价标的（例如 8 元或 3 港元的股票）会严重失真 | 按价位精度格式化 |
| G-06 | P2 | PNG 内容 | SKILL.md 要求在回复里展示 PNG，但 PNG 上没有风控否决、数据质量和保形扇形，只看 PNG 会被误导 | 在 PNG 上加否决徽章和数据质量徽章 |
| G-07 | P3 | `scripts/render_html.py:588` | 订单块固定用绿色表示多头、红色表示空头，与 `cn_red_up` 的颜色语义正好相反 | 跟随配色方案 |
| G-08 | P3 | `scripts/render_html.py:239,337`、`SKILL.md:12` | "筹码峰"这个说法和 AGENTS.md 的术语边界冲突 | 改成"成交分布 POC（近似）" |

### 3.5 Skill 规范化

对照的是 [agentskills.io/specification](https://agentskills.io/specification) 和 Anthropic 的技能编写最佳实践。

| 检查项 | 结果 | 说明 / 建议 |
|---|---|---|
| `name` 只用小写字母、数字和连字符，且不超过 64 字符 | ✅ | `kline-structure-chart` |
| `name` 与目录名一致 | ✅ | |
| `description` 在 1 到 1024 字符之间，并且写清做什么和何时用 | ✅ | 143 字符 |
| `description` 有英文关键词和不适用边界 | ⚠️ P2 | 只有中文，英文请求可能触发不了。建议补上英文和"不适用于基本面分析、新闻和下单" |
| frontmatter 只使用规范字段 | ❌ P2 | `version` 和 `author` 不是规范字段，要移进 `metadata`。预计会被严格校验器（例如 `skills-ref validate` 的字段白名单）判为非法字段（本轮没有运行该工具） |
| 写了 `compatibility` | ❌ P2 | 需要声明依赖：Python≥3.12、uv、能访问 PyPI 以及新浪和 Yahoo 的网络 |
| `license` 有对应文件 | ⚠️ P3 | frontmatter 声明 MIT，但仓库里没有 LICENSE 文件 |
| 正文少于 500 行，引用只有一层 | ✅ | 正文 69 行 |
| 使用相对路径 | ❌ P2 | `SKILL.md:23` 写死了 `/Users/admin-1234/...`。应改为"工作目录为本 SKILL.md 所在目录" |
| 写明依赖安装和首次运行 | ⚠️ P2 | 没有说明首次 `uv sync` 需要联网，也没有给出无网时的替代方案 |
| 有输出契约（回复模板、必填字段、免责声明） | ❌ P1 | 只有零散的铁律，没有统一的回复模板，也没有"不构成投资建议"的要求 |
| 有失败处理 | ❌ P1 | 没有写遇到未知代码、断网、数据不足、数据非实时时 agent 该怎么做 |
| 有使用边界（何时不用） | ❌ P2 | |
| 没有过时的时效性信息 | ❌ P1 | ST ±5% 和 PDT 写死在文档里，也没有标注生效日期和来源 |
| 术语前后一致 | ❌ P2 | 例如"筹码峰"，详见 3.8 |
| `assets/analysis.schema.json` 真正被使用 | ❌ P2 | schema 只校验顶层字段，代码从来没有加载过它。`SKILL.md:14` 却说"通过 schema 校验" |
| `assets/defaults.json` 真正被使用 | ❌ P2 | 没有任何代码读取它（`contracts.load_defaults` 从未被调用），而且参数和代码、文档都不一致，详见 3.8 的参数对照表 |
| `references/failure-modes.md` 与 v5 一致 | ❌ P2 | 它要求"始终运行 `render_structure_chart.py`"，与主入口冲突。它引用的 `small_model_copy_blocks` 实际位于 `legacy_summary` 下面 |
| 有 evals（触发测试和行为测试） | ❌ P2 | 建议加 20 条左右会触发和不该触发的查询，再加"遇到合成数据时 agent 是否拒答"的行为测试 |
| 命名风格 | ⚠️ 可选 | 最佳实践推荐动名词形式（例如 `analyzing-kline-structure`）。目前改名会影响已有引用，**不建议现在改** |

推荐的 frontmatter 和正文骨架如下：

```markdown
---
name: kline-structure-chart
description: >-
  Deterministic multi-market (CN A-share / HK / US) candlestick (K-line) technical analysis
  from OHLCV: indicators, swing & candlestick patterns, SMC zones, volume profile,
  triple-barrier levels and a 15-bar quantile forecast, rendered as JSON + PNG + offline HTML.
  Use when the user asks to analyze an index or stock chart (K线、走势、技术形态、支撑压力、
  回测、未来走势), e.g. 上证指数, 600519.SH, 00700.HK, NVDA. Not for fundamentals, news, or orders.
license: MIT
compatibility: Requires Python 3.12+, uv, and network access to PyPI and Sina/Yahoo market data; refuses to output analysis on non-live data.
metadata:
  author: chengpeng
  version: "5.1.0"
---

# K 线数学结构分析
## 何时使用 / 何时不用
## 运行（工作目录 = 本 SKILL.md 所在目录）
## 运行后必检（任一不满足 → 停止并告知用户，不输出任何价位）
1. quality.status == "OK"，meta.data_source 与 market_benchmark.source 均为实时源
2. 最后一根 K 线已收盘；盘中运行须注明"未收盘"
## 回复契约（按顺序）
1. 大盘基准 regime / regime_score 与相对强弱
2. rule_score（注明"规则分，非概率"）与 rule_scenarios（触发 → 目标 → 失效）
3. 15 日分位区间 + 回测 OOS 实测覆盖率 + 相对随机游走误差
4. 风控否决（risk_officer_veto=true 时置顶）
5. 文件：result.json / dashboard.html；客户端支持图片时再内嵌 preview.png
6. 免责声明：仅供研究，不构成投资建议
## 失败处理（未知代码 / 断网 / 数据不足）
## 参考文档（按需读取，一层引用）
```

### 3.6 跨 Agent 可移植性

| ID | 级别 | 项目 | 现状与影响 | 修复建议 |
|---|---|---|---|---|
| P-01 | P1 | 发现位置 | skill 放在仓库根目录的 `kline-structure-chart/`，不在任何 agent 的技能目录里。本会话 Claude Code 的可用技能列表里也确实没有它 | 提供一个安装脚本，用软链或复制的方式放到：<br>1. Claude Code：`.claude/skills/kline-structure-chart/` 或 `~/.claude/skills/`<br>2. Codex、Copilot CLI、Gemini CLI：`~/.agents/skills/`（据 superpowers writing-skills 文档，这是它们共同认可的路径） |
| P-02 | P1 | 运行环境 | 需要 Python≥3.12、uv、PyPI，还要能访问新浪和 Yahoo。按 Anthropic 官方的最佳实践文档，API 代码执行环境**没有网络，也不能在运行时安装包**。在这类环境里，当前代码会**静默产出合成数据**，这是最危险的组合 | 用 `compatibility` 声明依赖。无网时直接报错。同时支持 `--input` 加载本地真实数据 |
| P-03 | P2 | 绝对路径 | `SKILL.md:23` 和 `AGENTS.md:85-113` 写死了 `/Users/admin-1234/...`，换一台机器或换一个用户就会失效 | 全部改成相对路径 |
| P-04 | P2 | 客户端能力 | "在回复中展示 preview.png"并不是所有客户端都能做到，例如部分 CLI 无法内嵌图片 | 改为条件式："客户端支持显示图片时才内嵌 preview.png，否则给出文件路径" |
| P-05 | P2 | 语言 | `description` 只有中文 | 改为中英双语的触发词 |
| P-06 | P2 | 运行期约束随 skill 分发 | AGENTS.md 不会随 skill 一起分发，skill 装到 `~/.claude/skills` 之后，AGENTS.md 里的运行期约束就丢了 | 运行期规则要在 SKILL.md 和 references 里自洽。AGENTS.md 只服务于开发者和编码 agent |
| P-07 | P3 | 工具名 | `references/failure-modes.md` 提到的 `image_generate` 不是各家 agent 通用的工具名 | 改成"任何图像生成工具" |
| P-08 | P3 | 网络安全 | `scripts/render_structure_chart.py:131` 有回退到明文 `http://qt.gtimg.cn` 的逻辑 | 只用 HTTPS |
| P-09 | 可选 | `allowed-tools` | 没有声明。这个字段是可选的实验特性 | Claude Code 下可以考虑设为 `Bash(uv run:*)`，减少权限确认弹窗 |

### 3.7 AGENTS.md 合理性

**合理、建议保留的部分**：

- 六条铁律的方向都对。
- "证据先行"要求在同一轮里给出真实的命令输出。
- 规定了 NaN/Infinity 拦截和 `<script>` JSON 转义。
- 规定 UI 改动必须在浏览器里验证。
- 标准命令集中列出，便于照做。

**问题**：

| ID | 级别 | 位置 | 问题 | 修复建议 |
|---|---|---|---|---|
| AG-01 | P1 | 铁律 1 | 规定"渲染层不得重算"，但 PNG 渲染器自己重新计算结构（G-02） | 先重构代码，再保留这条铁律 |
| AG-02 | P1 | 铁律 2 | 有三个缺口：<br>1. 用 `--input` 传入 K 线数组时基准为空，代码会把标的自己当作大盘，没有任何提示。<br>2. 基准指标只基于 120 根 K 线计算。<br>3. 基准可能是合成数据，而标的是真实数据 | 缺少基准时直接报错，或者在报告里显式降级说明 |
| AG-03 | P1 | 铁律 3 | 有两处与实现不符：<br>1. 复权实际上不起作用（R-07）。<br>2. 允许未收盘的 K 线进入计算（A-06） | 先修代码，或者如实改写这条铁律 |
| AG-04 | P1 | 铁律 4 | 不变性门禁只覆盖 v4.1 的 `render()`，v5 主流程不满足（C-06） | 扩展 `check_days_invariance.py` 的覆盖范围 |
| AG-05 | P1 | 铁律 6（`AGENTS.md:35,37`） | 有两类问题：<br>1. ST ±5% 和 PDT 已过时。<br>2. 写了"支持 VIX 缩放与财报窗口"，但这两项并没有真正生效 | 规则表要带"生效日期、来源、复核周期"三项 |
| AG-06 | P1 | 缺失 | 没有"数据真实性"铁律。这是本次 P0 问题的根源 | 新增一条铁律 0：面向用户的输出禁止使用合成或缺失数据；数据非实时时必须醒目披露并降级 |
| AG-07 | P2 | `AGENTS.md:85-113` | 命令里写死了绝对路径 | 改成 `uv run --directory kline-structure-chart ...` |
| AG-08 | P2 | 第 2 节目录树 | 目录树缺 `artifacts/` 和 `tests/fixtures/`。另外两处描述不准：<br>1. 说 `data_gateway` 有"腾讯"数据源，但实际只有 v4.1 的 PNG 入口用到腾讯。<br>2. 把"确定性离线回退"当成一项特性来写 | 按实际情况更新 |
| AG-09 | P2 | 整体 | 与 SKILL.md 两处重复维护铁律，已经出现不一致（例如"筹码峰"） | AGENTS.md 只写开发契约，SKILL.md 只写运行契约，公式和规则只在 references 里定义一次 |
| AG-10 | P2 | 4.3 节 | 要求浏览器零报错，但没有可复现的脚本 | 加一个 Playwright 或 Chrome DevTools 的检查脚本 |
| AG-11 | P2 | 缺失 | 缺少以下规范：<br>1. 新增标的和代码映射的规范<br>2. 四处版本号（SKILL、pyproject、schema、engine）的同步规则<br>3. `.gitignore` 和产物管理策略<br>4. skill 的安装和分发说明<br>5. 数据源的许可与限频说明 | 逐项补齐 |
| AG-12 | P3 | `AGENTS.md:59`、`scripts/strategies.py:11` | 写"四大 Regime"，代码里实际有 5 类 | 改成"五类" |
| AG-13 | P3 | 兼容性 | 当前版本的 Claude Code 会读取 AGENTS.md，本会话已经加载了它。旧版只读 CLAUDE.md。Gemini CLI 默认读 GEMINI.md（可配置） | 如需兼容，加一个只含 `@AGENTS.md` 的 CLAUDE.md |

### 3.8 文档一致性与术语

**宣称与实际对照**：

| 宣称 | 出处 | 实际情况 |
|---|---|---|
| "通过 analysis.schema.json 校验" | `SKILL.md:14` | schema 从来没有被加载，校验是手写的局部检查 |
| "十字光标悬停查看公式证据" | `SKILL.md:16` | 悬停只显示 OHLC、MA、RSI、RVOL |
| "Qlib 量价正交特征" | `SKILL.md:12`、`AGENTS.md:10` | 没有做正交化。代码用的是 Spearman 秩相关，Alpha158 用的是 Pearson |
| "核回归平滑双底/双顶/通道" | 同上 | 形态检测用的是原始高低点的 ZigZag，核平滑只用于画图 |
| "RSI/MACD 背离" | 同上 | 只实现了 RSI 背离 |
| "SMC（BOS/CHoCH…）" | 同上 | CHoCH 没有和 BOS 区分 |
| "头肩底/顶" | `scripts/patterns.py:286` 的 docstring | 没有实现 |
| "VIX 缩放与财报窗口" | `AGENTS.md:37`、`references/us-market.md:8` | VIX 写死为 19.5，财报日永远是 None |
| "复权因子仅消费…" | `AGENTS.md:22` | 没有公司行动数据源 |
| "无重叠 Patch" | `references/math-patterns.md:39` | 代码允许重叠 10 根 |
| "逐期限边际覆盖率校准" | `scripts/forecast.py:358` | 只校准了终点期 |
| "Walk-Forward Purged/Embargo" | `scripts/evaluation.py:164` | 实际只做了一次切分 |
| "DETERMINISTIC_SYNTHETIC_FIXTURE" | `scripts/data_gateway.py:105` | 代码实际输出的标签是 `DETERMINISTIC_OFFLINE_FIXTURE` |
| 锤头线条件"PercentRank(C,20)≤0.35" | `references/math-patterns.md:18` | 代码判断的是实体底部在 20 日区间中的位置，并且用 OR 接受"创新低" |
| ZigZag 字段 `observed_at` | `references/math-patterns.md:10` | 代码字段是 `day` 和 `confirmed_at_day` |

**`assets/defaults.json` 与代码实际取值对照**。这个文件从来没被读取，下表里的值都不会生效：

| 参数 | defaults.json | 代码与 math-patterns.md |
|---|---|---|
| 锤头上影/振幅上限 | 0.15 | 0.18 |
| 锤头振幅/ATR 下限 | 0.8 | 0.75 |
| 双底低点容差 | 0.4 ATR | 0.65 ATR |
| 双底间隔 | 8–65 | 6–85 |
| 双底颈线高度 | 1.6 ATR | 1.2 ATR |
| FVG 冲击实体 | 1.2 ATR | 1.10 ATR |
| 通道 r² 门槛 | 0.75 | 0.72 |
| 类比 Patch 长度 | [5, 20, 60] | 只用 20 |
| 最少类比样本 | 15 | 3 |
| 最少校准样本 | 30 | 15 / 20 |

### 3.9 测试体系

| ID | 级别 | 问题 | 修复建议 |
|---|---|---|---|
| T-01 | P1 | 全部测试都设置 `allow_network=False`，跑的是带人工双底的合成数据。所以 `ana["available"] is True` 这类断言必然成立，说明不了问题 | 用录制的真实数据加上 mock 的网络层 |
| T-02 | P1 | `tests/fixtures/sina_daily/*.json` 名叫"新浪日K缓存"，实际是合成数据，有三点证据：<br>1. 包含休市日 2025-06-02 和 2026-02-23 的 K 线。<br>2. 最后 3 根的成交量完全相同（1654369.2）。<br>3. 2024-10-08 上证收盘为 2880.91，实际约为 3490 | 改名为 `synthetic/`，另外录制一份真实数据作为 fixture |
| T-03 | P1 | 测试固化了错误规则（ST=0.05），并且以下情况都没有覆盖：<br>1. 500 以上的港股价位<br>2. 休市日<br>3. 未知代码<br>4. `000xxx.SZ`<br>5. 未收盘的 K 线<br>6. 数据源标识 | 第 2.3 节的每个探针都可以直接改写成一个测试 |
| T-04 | P2 | 断言太弱。多市场测试只检查文件是否存在，以及 HTML 里有没有 "Time-Travel Replay" 这个字符串 | 断言关键数值和字段语义 |
| T-05 | P2 | 指标没有"金标准"对照 | 和 TA-Lib 或 pandas-ta 在固定数据上比对 RSI、ATR、ADX、MACD、Supertrend |
| T-06 | P2 | 没有网络异常场景的测试，例如限流、字段缺失、返回空数据 | 用 mock 覆盖这些分支 |
| T-07 | P2 | 没有 skill 级别的评测，即触发和不触发查询集、遇到合成数据时 agent 是否拒答 | 参照 skill-creator 的 evals 流程补上 |

### 3.10 仓库卫生

| ID | 级别 | 问题 |
|---|---|---|
| H-01 | P3 | 没有 `.gitignore`。`.venv/`、`.pytest_cache/`、`.DS_Store`、`artifacts/`（4.9 MB）都处于未跟踪状态，很容易被误提交，也容易被一起打包进 skill |
| H-02 | P3 | 没有 LICENSE、README、CHANGELOG |
| H-03 | P3 | 仓库还没有任何提交，建议修完 P0 之后再做第一次提交，避免把有问题的产物写进历史 |

---

## 4. 优化路线图

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| **阶段 0：P0，立即处理** | 1. 修复 A-01 到 A-06：代码映射、禁止合成回退、PNG/HTML 加水印、来源枚举、真实 VIX 或置空、识别未收盘 K 线<br>2. SKILL.md 增加"运行后必检"和"失败处理" | 1. 断网时运行任意标的都以非零码退出，并给出可读的报错<br>2. 601318.SH、000002.SZ、01810.HK 在联网时能取到真实数据，且标识正确<br>3. 盘中运行时，最后一根 K 线被标为未收盘 |
| **阶段 1：P1 正确性** | 1. 更新制度规则：ST、价位表、印花税、日历库、PDT，并带上生效日期<br>2. EMA200 的预热数据<br>3. 修复保形校准（C-02）和漂移（C-03）<br>4. 让回测与计划同源（C-04、C-05）<br>5. 把 v5 纳入不变性测试<br>6. 修复 PNG 配色，抽出 `structure.py`（G-01、G-02） | 1. 2.3 节的所有探针都转为单元测试并通过<br>2. 报告中的覆盖率等于样本外实测值<br>3. q50 相对"价格不变"基准的误差被披露 |
| **阶段 2：P2 规范与可移植** | 1. frontmatter（移入 `metadata`、补 `compatibility`、写双语 description）<br>2. 改为相对路径<br>3. 输出契约<br>4. 安装脚本<br>5. 让 defaults.json 和 schema 真正生效，或删除它们<br>6. 更新 failure-modes.md<br>7. 统一 AGENTS.md 和 SKILL.md 的分工<br>8. 补充测试 | 1. `skills-ref validate` 通过<br>2. 在 Claude Code 和至少一个其他 agent 上完成安装、触发和运行的冒烟测试<br>3. 有一份 evals 集 |
| **阶段 3：P3 措辞与整洁** | 1. 修正术语和命名<br>2. 补 `.gitignore`、LICENSE、README<br>3. 修小 bug（C-14） | 3.8 节的"宣称与实际对照"表清零 |

---

## 5. 可复用的 Review 勾选清单

以后每次修改 skill 或脚本之后，按这份清单逐项勾选。

**A. Skill 规范**
- [ ] frontmatter 只有 `name`、`description`、`license`、`compatibility`、`metadata`、`allowed-tools` 这几个字段，并通过 `skills-ref validate`
- [ ] `name` 与目录名一致；`description` 不超过 1024 字符，写清做什么、何时用、何时不用，并有中英文关键词
- [ ] SKILL.md 正文少于 500 行，引用只有一层，没有绝对路径
- [ ] 有输出契约（回复顺序、必填字段、免责声明）
- [ ] 有失败处理（未知代码、断网、数据不足、数据非实时）
- [ ] 四处版本号（SKILL、pyproject、schema、engine）同步，并且有 CHANGELOG

**B. 跨 Agent 可移植**
- [ ] 有安装脚本，或者已经放进 `.claude/skills/` 和 `~/.agents/skills/` 等发现目录
- [ ] 在无网、无 uv 的环境里会明确报错，不会静默降级
- [ ] 不依赖某个客户端独有的能力（例如内嵌图片）；如有依赖，已写成条件式
- [ ] 运行期规则不依赖 AGENTS.md

**C. 数据真实性**
- [ ] 生产路径不可能产出合成数据；测试用的合成数据必须显式开启
- [ ] `quality.status` 由来源枚举决定，标的和基准分别判定
- [ ] PNG 和 HTML 会醒目显示数据源；数据非实时时有水印
- [ ] 输出里没有写死的外部数据（例如 VIX）
- [ ] 最后一根 K 线是否收盘，有明确的判定和标注
- [ ] fixture 的命名与来源一致

**D. 市场制度**
- [ ] 每条规则都有生效日期、来源链接和下次复核日期
- [ ] 涨跌停按板块、ST 和新股期区分；交易日历覆盖到未来至少 1 年，超出范围会报错
- [ ] 港股价位表完整，并包含减价位改革的各个阶段；港股印花税双边收取
- [ ] 最小申报数量正确（科创板 200 股、北交所和科创板可按 1 股递增）；ETF 的价位精度正确
- [ ] 代码到资产类型和数据源代码的映射有测试（`000xxx.SZ`、北交所、港股前导 0、ETF）

**E. 计算与统计**
- [ ] 长周期指标有足够的预热数据，不会被静默替换；证据字符串与实际取值一致
- [ ] 保形和分位数区间的样本外覆盖率，就是针对实际输出的区间测出来的
- [ ] 预测披露了相对"价格不变"基准的误差
- [ ] 回测策略与给用户的计划同源；样本量和置信区间已披露；训练集和测试集严格隔离
- [ ] 所有宣称的形态都确实实现了，命名与算法一致

**F. 因果与不变性**
- [ ] 拐点、形态、类比检索、保形预测都没有前视；审计用的是生产配置
- [ ] `--days` 不变性同时覆盖 v4.1 的 `render()` 和 v5 的 `run_pipeline`

**G. 渲染**
- [ ] PNG 和 HTML 只读取 report，不重新计算
- [ ] 配色跟随 `meta.color_scheme`；页脚和图例按市场输出正确文案
- [ ] 回放时不显示截面之后的 K 线；轨道的起点和斜率与 JSON 一致
- [ ] 在浏览器里检查：控制台零报错，7 个图层、RS 视图、回放滑块都正常（要有可复现的脚本）

**H. 测试与文档**
- [ ] 门禁命令包括 pytest、v5 不变性、指标金标准对照和网络 mock
- [ ] 3.8 节的"宣称与实际对照"表没有残留项
- [ ] 有 `.gitignore`，产物不会被提交，也不会被打包进 skill

---

## 6. 做得好的地方（建议保留）

- **XSS 防护到位**：`_safe_json_for_script` 会转义 `< > & U+2028 U+2029`，前端全程用 `textContent`，并且有对应的测试。
- **契约校验**：会拦截 NaN 和 Infinity，检查分位数单调，检查 OHLC 的上下包络，`sanitize_filename` 会防止路径穿越。
- **因果 ZigZag**：同时记录 `day` 和 `confirmed_at_day`，只用已确认的拐点做形态判断。
- **回测撮合细节考虑周到**：T+1 当日不能卖；一字涨停买不进（LIMIT_UP_NO_FILL）；一字跌停卖不出；同一根 K 线同时触及止盈止损时按先止损处理；跳空时按开盘价成交。
- **v4.1 结构层**：轨道、ABC、阶梯和三条推演写得严谨，跨 `--days` 不变性实测 runs=120、failing=0。
- **不编造仓位**：没有提供账户净值时，不输出建议股数；指数直接注明不可交易。
- **术语提示写进了 JSON**：`rule_score`、`volume_profile`、SMC 都带有"非概率/近似"之类的说明字段。
- **HTML 单文件、离线可用**：没有任何 CDN 依赖，控制台零报错。
- **AGENTS.md 的"证据先行"要求**：值得长期保留。

---

## 7. 参考来源

- 规范：[Agent Skills Specification](https://agentskills.io/specification)；Anthropic 的 Skill authoring best practices（superpowers `writing-skills/anthropic-best-practices.md`）
- A 股 ST 涨跌幅：[中证网](https://cs.com.cn/tj/02/01/202506/t20250627_6498570.html)、[财联社](https://www.cls.cn/detail/2069694)、[同花顺](https://news.10jqka.com.cn/20260410/c675906757.shtml)
- A 股休市：[上交所休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/list/)、[澎湃新闻](https://m.thepaper.cn/newsDetail_forward_32603402)、[人民日报（2025 年节假日通知）](https://paper.people.com.cn/rmrb/html/2024-11/13/nw.D110000renmrb_20241113_5-03.htm)
- 港股：[HKEX Reduction of Minimum Spreads](https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads?sc_lang=en)、[KPMG 印花税](https://kpmg.com/cn/en/home/insights/2023/11/tax-alert-21-hk-the-reduced-stamp-duty-rate-on-hk-stock-transfer-will-take-effect-from-17-nov-23.html)、[IRD](https://www.gov.hk/en/residents/taxes/docs/IRSD123(E).pdf)
- 美股：[NYSE 2025-01-09 休市](https://ir.theice.com/press/news-details/2024/The-New-York-Stock-Exchange-Will-Close-Markets-on-January-9-to-Honor-the-Passing-of-Former-President-Jimmy-Carter-on-National-Day-of-Mourning/default.aspx)、[Schwab：PDT 取消](https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum)、[E*TRADE](https://us.etrade.com/knowledge/library/margin/pattern-day-trading-rule-change)
