# 示例：真实行情生成的完整产物

本目录的全部文件都由技能脚本基于仓库内录制的**真实行情**（`tests/fixtures/recorded/`）生成，没有任何手工修改或合成数据；报告与 JSON 里的文件路径本身就是相对路径，未做改写。示例中的数字只用于说明输出格式，不代表当前行情，也不构成投资建议。

- 生成时间：2026-10-22（按 6.0.0 契约基于仓库真实录制行情 `--as-of 2026-10-15` 重新生成；港股剔除 Yahoo 占位K线并在报告"数据备注"中披露）
- 数据截止日（最后一根已收盘日 K 线）：**2026-10-15**
- 技能版本：6.0.0
- 数据源：A 股新浪日线 + Yahoo 除权除息事件；港股、美股 Yahoo 日线；证券简称与每手股数来自腾讯行情快照

- `reply.md` 是按报告骨架手写的标准回复（反例部分除外，已通过 `lint_response.py`）。
- 每个市场目录都有 `*_result.json`（稳定契约）与 `*_chart_data.json`（渲染数据，非稳定接口），`files` 字段均为相对路径。

| 目录 | 标的 | 文件 |
| --- | --- | --- |
| [600519.SH](600519.SH) | 贵州茅台（A 股，基准沪深300） | 报告、结果 JSON、渲染数据 JSON、回测 JSON、结构图 PNG、交互看板 HTML、[标准回复](600519.SH/reply.md) |
| [00700.HK](00700.HK) | 腾讯控股（港股，基准恒生指数） | 报告、结果 JSON、渲染数据 JSON、结构图 PNG、交互看板 HTML（未传 `--backtest`，没有回测 JSON） |
| [NVDA.US](NVDA.US) | 英伟达（美股，基准 SPY） | 报告、结果 JSON、渲染数据 JSON、结构图 PNG、交互看板 HTML（未传 `--backtest`，没有回测 JSON） |

## 三市场实盘生成图例预览

### 1. 贵州茅台（`600519.SH` · A股红涨绿跌）

- 完整中文报告：[600519.SH_report.md](600519.SH/600519.SH_report.md)
- Agent 标准回复示范（含正例与反例）：[reply.md](600519.SH/reply.md)

![贵州茅台 600519.SH 结构推演图](600519.SH/600519.SH_preview.png)

### 2. 腾讯控股（`00700.HK` · 港股红涨绿跌）

- 完整中文报告：[00700.HK_report.md](00700.HK/00700.HK_report.md)

![腾讯控股 00700.HK 结构推演图](00700.HK/00700.HK_preview.png)

### 3. 英伟达（`NVDA.US` · 美股绿涨红跌）

- 完整中文报告：[NVDA.US_report.md](NVDA.US/NVDA.US_report.md)

![英伟达 NVDA.US 结构推演图](NVDA.US/NVDA.US_preview.png)

## 生成命令

示例本身是用 `tests/fixtures/recorded/` 的录制行情回放同一条流水线（`run_pipeline`，`as_of` 为 2026-10-15）生成的。下面的命令在仓库根目录执行，会联网取行情，产物写入 `kline-structure-chart/artifacts/examples/`；联网取到的行情可能比示例新，数字会不同：

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --as-of 2026-10-15 --output-dir artifacts/examples --backtest
```

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market HK --symbol 00700.HK --asset-type equity --account-equity 500000 --as-of 2026-10-15 --output-dir artifacts/examples
```

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market US --symbol NVDA.US --asset-type equity --account-equity 100000 --as-of 2026-10-15 --output-dir artifacts/examples
```

重新生成后数字会随行情变化。更新示例时请同时修改本文件的截止日，以及 `kline-structure-chart/SKILL.md` "回复示例"一节引用的数字。
