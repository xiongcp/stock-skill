---
name: kline-structure-chart
description: "Deterministic multi-market (A-share CN / Hong Kong HK / US) candlestick (K-line) technical analysis from real OHLCV data: indicators, swing and candlestick patterns, SMC zones, volume profile, triple-barrier risk levels, a calibrated 15-bar price range and a plan-replay backtest, rendered as JSON + PNG + offline HTML. Use when the user asks to analyze an index, stock or ETF chart, e.g. 上证指数/沪深300/600519.SH/00700.HK/恒生指数/NVDA/SPY; 分析K线、走势、技术形态、支撑压力、买卖点、回测或未来区间. Not for fundamentals, news, earnings, real-time order placement, or personalized investment advice."
license: MIT
compatibility: "Requires Python 3.12+, uv, and network access to PyPI and to Sina / Yahoo / Tencent market-data endpoints. Without live data the scripts exit with [DATA_UNAVAILABLE] instead of fabricating prices."
metadata:
  author: "承鹏 (chengpeng), Qoder Quant Engine"
  version: "5.1.0"
---

# K 线数学结构分析（A股 / 港股 / 美股）

脚本从真实 OHLCV 行情直接计算全部指标、形态、风控点位与未来 15 日区间，同一份结果输出为 JSON、PNG 和离线 HTML。分析结论只能引用脚本输出，严禁心算、看图猜数，也不得用图像生成模型画 K 线。

## 何时使用 / 何时不用

- **使用**：用户要分析某个指数、个股或 ETF 的 K 线结构、技术形态、支撑压力、触发与失效位、风控计划、历史回测或未来区间。
- **不使用**：基本面、财报解读、新闻事件、资金流向等脚本不计算的内容；替用户下单或给出个性化投资建议。脚本不计算的数据在回答中一律标注"未复核"。

## 一、运行

工作目录为本 `SKILL.md` 所在目录（技能根目录），以下命令均为相对路径。首次运行时 `uv` 会按 `pyproject.toml` 联网安装依赖，取行情同样需要联网。

```bash
# A股指数 / 个股（个股会同步拉取沪深300 基准，施加涨跌停与 T+1 约束）
uv run python scripts/analyze_and_forecast.py --market CN --symbol 000001.SH --asset-type index --output-dir artifacts/cn_index --backtest
uv run python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --output-dir artifacts/cn_stock --backtest

# 港股（基准恒生指数）与美股（基准 SPY）
uv run python scripts/analyze_and_forecast.py --market HK --symbol 00700.HK --asset-type equity --output-dir artifacts/hk_stock --backtest
uv run python scripts/analyze_and_forecast.py --market US --symbol NVDA.US --asset-type equity --output-dir artifacts/us_stock --backtest
```

- 代码格式：`600519.SH` / `000002.SZ` / `830799.BJ` / `00700.HK` / `HSI.HK` / `NVDA.US`。
- `--days 60~500` 只改变主图显示根数，不影响任何计算结果。
- 默认复权为个股/ETF 前复权含分红（`total_return`），指数为原始价。
- `--account-equity` 只在用户给出账户净值时传入；未提供时报告不会给出股数。
- `--st` / `--name` 用于标记 A 股 ST/\*ST 风险警示股票；`--ipo-no-limit` 用于标记上市前 5 日无涨跌停新股；`--color-scheme` 可切换涨跌配色。
- 生产与测试一律禁止合成数据：不传 `--input` 本地真实行情文件时，断网或数据源异常会直接以 `[DATA_UNAVAILABLE]` 退出。

产物（文件名以标的代码开头）：
- `*_result.json`：全部计算结果，已通过 `assets/analysis.schema.json` 顶层契约校验。
- `*_preview.png`：结构推演图，配色随市场（A股/港股红涨绿跌，美股绿涨红跌）。
- `*_dashboard.html`：离线交互看板，提供 7 个图层开关、悬停查看 OHLC/均线/RSI/预测分位、相对强弱切换、历史回放滑块和导出 PNG。
- `*_backtest.json`（传 `--backtest` 时生成）：线上计划逐日回放的成交统计，以及区间样本外覆盖率。

数据质量不是 `OK` 时，PNG 和 HTML 都会带红色告警横幅与水印。

## 二、运行后必检（按顺序，任一不满足即停止解读）

1. **退出码**：脚本以 `[DATA_UNAVAILABLE]` 非零退出时，如实告诉用户行情不可用及原因，不输出任何价位，不得改用合成或估算数据。
2. **`quality.status`**：
   - `UNAVAILABLE` → 不输出任何价位与结论。
   - `PARTIAL` → 回答首段逐条列出 `quality.warnings`，再谨慎解读。
   - `OK` → 正常解读。
3. **`quality.notes`**：逐条转述，例如"某日 K 线未收盘，已排除出计算"、"复权未生效"、"未来日期超出休市表覆盖范围，按工作日估算"。
4. **空值**：`future_projection_15d.volatility_scaling.vix` 为 `null` 表示没有取得 VIX，不得自行补数。

## 三、回复契约（按此顺序组织回答）

1. **数据说明**：数据源（`meta.data_source`）、截止日（`meta.as_of`）、复权口径（`meta.adjustment`），以及上一步需要转述的告警或备注。
2. **大盘先行**：基准的 `market_benchmark.regime`、`regime_score`，以及个股的 `relative_strength_vs_index`。
3. **结构与规则**：
   - `multi_timeframe_score.rule_score` 是 0~100 的确定性规则分，不是胜率或概率。
   - `rule_scenarios` 按"触发条件 → 目标位 → 失效条件"转述。
   - 形态只引用 `algebraic_patterns_detected` 里的 `name_cn` 和 `formula_verified`。
4. **风控**：
   - `debate_and_veto.risk_officer_veto` 为 `true` 时，**置顶**列出 `veto_reasons`，不得给出做多建议。
   - 否则给出 `triple_barrier_plan` 的入场区、止损、TP1/TP2 与时间屏障。
5. **未来 15 日区间**：
   - `future_projection_15d` 以最新收盘价为中心（`q50_method = MARTINGALE_LAST_CLOSE`），只描述波动幅度，不包含方向判断，不得把 q75/q25 说成看涨/看跌概率。
   - 必须同时给出 `calibration_audit` 的实测覆盖率与 `effective_independent_samples`。
6. **回测**（有 `*_backtest.json` 时）：成交笔数、胜率、累计收益与同期买入持有对比；有 `sample_size_warning` 时原文转述。
7. **文件**：给出 `result.json` 与 `dashboard.html` 的路径；客户端能显示图片时再内嵌 `preview.png`，否则只给路径。
8. **免责声明**：结尾注明"以上为规则化技术分析，仅供研究参考，不构成投资建议"。

## 四、失败处理

| 情况 | 处理 |
| --- | --- |
| `[DATA_UNAVAILABLE]`（网络失败、代码无法映射、限流、数据损坏） | 转述错误原因，请用户核对代码格式或稍后重试；不估算、不改用合成数据 |
| `Cannot resolve market` / `Ambiguous symbol` | 请用户补充交易所后缀或 `--market`，例如 `000001.SH`（上证指数）与 `000001.SZ`（平安银行）的区别 |
| `历史长度不足` | 说明该标的上市时间太短或 `--as-of` 太早，无法计算 |
| 基准指数取不到 | 整次分析失败，按 `[DATA_UNAVAILABLE]` 处理（不得跳过大盘直接分析个股） |
| `quality.notes` 提示复权未生效（如北交所） | 告知价格为不复权原始价，除权缺口可能被误识别 |
| 用户要求下单、预测确定涨跌或给出个性化仓位 | 说明本技能只做规则化技术分析，不替用户交易、不承诺方向 |

## 五、参考文档（按需读取）

- `references/math-patterns.md`：用户追问某个指标、形态或预测区间如何计算时阅读。
- `references/cn-market.md`：A股涨跌停（主板 ±10%、双创 ±20%、北交所 ±30%，ST 按板块与日期区分）、T+1、代码识别、复权与交易日历。
- `references/hk-market.md`：港股价位表（含 2025-08 / 2026-08 两期下调）、双边印花税、恒生科技指数数据源。
- `references/us-market.md`：美股 T+1 结算、PDT 规则取消后的过渡期、VIX 与财报窗口说明。
- `references/failure-modes.md`：撰写文字分析前对照，避免常见的数字与表述错误。
