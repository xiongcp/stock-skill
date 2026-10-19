# 示例：真实行情生成的完整产物

本目录的全部文件都由技能脚本用**真实行情**联网生成，没有任何手工修改或合成数据（只把报告与 JSON 里的本机绝对路径改成了仓库内相对路径）。示例中的数字只用于说明输出格式，不代表当前行情，也不构成投资建议。

- 生成时间：2026-10-18（5.3.0 + Unreleased P0/P1 审查修复后基于仓库真实录制行情 `--as-of 2026-10-15` 重新生成）
- 数据截止日（最后一根已收盘日 K 线）：**2026-10-15**
- 技能版本：5.3.0
- 数据源：A 股新浪日线 + Yahoo 除权除息事件；港股、美股 Yahoo 日线；证券简称与每手股数来自腾讯行情快照

- `600519.SH/600519.SH_judgment.json` 是 Agent 按 `references/llm-judgment-protocol.md` 写的主观研判：先用 `validate_judgment.py --auto-init` 生成预填模板，再复核证据、改写概率与理由，校验结果为 `origin = AGENT`；`reply.md` 是按报告骨架手写的标准回复（反例部分除外，已通过 `lint_response.py`）。

| 目录 | 标的 | 文件 |
| --- | --- | --- |
| [600519.SH](600519.SH) | 贵州茅台（A 股，基准沪深300） | 报告、结果 JSON、L3 研判证据包 JSON、L3 主观研判 JSON、回测 JSON、结构图 PNG、交互看板 HTML、[标准回复](600519.SH/reply.md) |
| [00700.HK](00700.HK) | 腾讯控股（港股，基准恒生指数） | 报告、结构图 PNG |
| [NVDA.US](NVDA.US) | 英伟达（美股，基准 SPY） | 报告、结构图 PNG |

## 架构与三市场实盘生成图例预览

### 1. 系统架构与三层分析流水线总览

![架构与三层分析流水线总览](architecture_overview.png)

### 2. 高清结构图 (PNG) 五大分区与交互看板 (HTML) 九大图层导读

![产物视觉导读](chart_reading_guide.png)

### 3. 贵州茅台（`600519.SH` · A股红涨绿跌）

- 完整中文报告：[600519.SH_report.md](600519.SH/600519.SH_report.md)
- Agent 标准回复示范（含正例与反例）：[reply.md](600519.SH/reply.md)

![贵州茅台 600519.SH 结构推演图](600519.SH/600519.SH_preview.png)

### 4. 腾讯控股（`00700.HK` · 港股红涨绿跌）

- 完整中文报告：[00700.HK_report.md](00700.HK/00700.HK_report.md)

![腾讯控股 00700.HK 结构推演图](00700.HK/00700.HK_preview.png)

### 5. 英伟达（`NVDA.US` · 美股绿涨红跌）

- 完整中文报告：[NVDA.US_report.md](NVDA.US/NVDA.US_report.md)

![英伟达 NVDA.US 结构推演图](NVDA.US/NVDA.US_preview.png)

## 生成命令

在仓库根目录执行（需要联网），产物写入 `kline-structure-chart/artifacts/examples/`，再复制到本目录：

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --as-of 2026-10-15 --output-dir artifacts/examples --backtest
```

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market HK --symbol 00700.HK --asset-type equity --account-equity 500000 --as-of 2026-10-15 --output-dir artifacts/examples --backtest
```

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market US --symbol NVDA.US --asset-type equity --account-equity 100000 --as-of 2026-10-15 --output-dir artifacts/examples --backtest
```

重新生成后数字会随行情变化。更新示例时请同时修改本文件的截止日，以及 `kline-structure-chart/SKILL.md` "回复示例"一节引用的数字。
