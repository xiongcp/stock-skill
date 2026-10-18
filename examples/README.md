# 示例：真实行情生成的完整产物

本目录的全部文件都由技能脚本用**真实行情**联网生成，没有任何手工修改或合成数据（只把报告与 JSON 里的本机绝对路径改成了仓库内相对路径）。示例中的数字只用于说明输出格式，不代表当前行情，也不构成投资建议。

- 生成时间：2026-10-17（北京时间）
- 数据截止日（最后一根已收盘日 K 线）：**2026-10-16**
- 技能版本：5.1.0
- 数据源：A 股新浪日线 + Yahoo 除权除息事件；港股、美股 Yahoo 日线；证券简称与每手股数来自腾讯行情快照

| 目录 | 标的 | 文件 |
| --- | --- | --- |
| [600519.SH](600519.SH) | 贵州茅台（A 股，基准沪深300） | 报告、结果 JSON、回测 JSON、结构图 PNG、交互看板 HTML、[标准回复](600519.SH/reply.md) |
| [00700.HK](00700.HK) | 腾讯控股（港股，基准恒生指数） | 报告、结构图 PNG |
| [NVDA.US](NVDA.US) | 英伟达（美股，基准 SPY） | 报告、结构图 PNG |

## 生成命令

在仓库根目录执行（需要联网），产物写入 `kline-structure-chart/artifacts/examples/`，再复制到本目录：

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --output-dir artifacts/examples --backtest
```

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market HK --symbol 00700.HK --asset-type equity --account-equity 500000 --output-dir artifacts/examples --backtest
```

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market US --symbol NVDA.US --asset-type equity --account-equity 100000 --output-dir artifacts/examples --backtest
```

重新生成后数字会随行情变化。更新示例时请同时修改本文件的截止日，以及 `kline-structure-chart/SKILL.md` "回复示例"一节引用的数字。
