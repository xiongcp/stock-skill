#!/usr/bin/env python3
"""多市场 (A股/港股/美股 · 大盘+个股) K线数学结构、三重屏障风控与15日保形预测同源双出入口。

一次运行同时产出：
1. result.json        — 通过 assets/analysis.schema.json 顶层契约校验的指标、形态公式证据、三重屏障与15步分位区间
2. preview.png        — 供 AI 聊天窗口直接内嵌展示的 1920x1160 高清结构与预测图
3. dashboard.html     — 零外部依赖、支持图层开关、悬停查看 OHLC/均线/RSI/分位、相对强弱切换与时间推演回放的单文件交互终端
4. backtest.json      — (当传入 --backtest 时) 因果回放审计、线上计划逐日回放回测与分位区间覆盖率审计
5. report.md          — 按 SKILL.md 回复契约组织的中文分析报告，数字全部取自 result.json（与 backtest.json）
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from analysis import analyze_bundle
from contracts import AnalysisRequest, sanitize_filename, validate_report
from data_gateway import load_bundle
from evaluation import run_causal_replay_audit, run_plan_backtest
from market_rules import price_decimals, round_to_tick
from render_html import render_html
from render_report_md import render_report_markdown
from render_structure_chart import render as render_png_chart
from structure import compute_structure_layer

MAX_RISK_PCT_LIMIT = 0.05  # 单笔风险上限 5%：更大的值几乎都是把百分数当比例传入


def run_pipeline(
    request: AnalysisRequest,
    output_dir: Path,
    input_path: Path | None = None,
    allow_network: bool = True,
    run_backtest: bool = False,
    backtest_step: int = 1,
) -> dict[str, Any]:
    disp_days = int(request.get("display_bars") or 120)
    horizon_bars = int(request.get("horizon_bars") or 15)
    if not 60 <= disp_days <= 500:
        raise ValueError(f"--days must be between 60 and 500, got {disp_days}")
    if not 1 <= horizon_bars <= 60:
        raise ValueError(f"--horizon-bars must be between 1 and 60, got {horizon_bars}")
    # 资金参数是比例，最常见的误用是把 1% 写成 1（单笔风险变成 100% 净值），超出范围直接拒绝而不是静默截断
    max_risk_pct = request.get("max_risk_pct")
    if max_risk_pct is not None and not (math.isfinite(float(max_risk_pct)) and 0 < float(max_risk_pct) <= MAX_RISK_PCT_LIMIT):
        raise ValueError(
            f"--max-risk-pct 是单笔风险占净值的比例，取值 (0, {MAX_RISK_PCT_LIMIT}]，例如 1% 写 0.01，got {max_risk_pct}"
        )
    account_equity = request.get("account_equity")
    if account_equity is not None and not (math.isfinite(float(account_equity)) and float(account_equity) > 0):
        raise ValueError(f"--account-equity 必须是大于 0 的有限数值，got {account_equity}")
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle = load_bundle(
        request,
        input_path=input_path,
        allow_network=allow_network,
    )
    # 结构层只算一次：同一份结果写入 JSON 并交给 PNG 渲染器（算图分离）；价格文本按价位表精度，与 report.md 一致
    inst = bundle["target"]["instrument"]
    bench_bars_raw = (bundle.get("benchmark") or {}).get("bars")
    layer = compute_structure_layer(
        bundle["target"]["bars"],
        bundle["target"].get("intraday"),
        int(request.get("display_bars") or 120),
        price_decimals=lambda price, day: price_decimals(price, inst["market"], inst["asset_type"], day),
        horizon_bars=horizon_bars,
        benchmark_bars=bench_bars_raw,
        round_price=lambda price, day: round_to_tick(price, inst["market"], inst["asset_type"], day),
    )
    report = analyze_bundle(bundle, request, include_replay=True, structure=layer)

    meta = report["meta"]
    quality = report["quality"]
    safe_sym = sanitize_filename(meta["symbol"])
    png_path = output_dir / f"{safe_sym}_preview.png"
    html_path = output_dir / f"{safe_sym}_dashboard.html"
    json_path = output_dir / f"{safe_sym}_result.json"
    jpack_path = output_dir / f"{safe_sym}_judgment_pack.json"

    title = (
        f"{meta['name']} ({meta['symbol']}) · 近{request.get('display_bars', 120)}日数学结构与未来{request.get('horizon_bars', 15)}日推演"
    )
    legacy_summary = render_png_chart(
        bundle["target"]["bars"],
        bundle["target"].get("intraday"),
        title,
        png_path,
        show=int(request.get("display_bars") or 120),
        data_source=str(meta["data_source"]),
        data_warning="；".join(quality["warnings"]) if quality["status"] != "OK" else None,
        structure=layer,
        color_scheme=meta["color_scheme"],
        quality_status=quality["status"],
        risk_veto=report["debate_and_veto"],
        conformal_forecast=report["future_projection_15d"],
    )
    report["legacy_summary"] = legacy_summary
    report["rendered_paths"] = {
        "result_json": str(json_path.resolve()),
        "judgment_pack_json": str(jpack_path.resolve()),
        "preview_png": str(png_path.resolve()),
        "dashboard_html": str(html_path.resolve()),
    }

    bt_payload: dict[str, Any] | None = None
    if run_backtest:
        bt_res = run_plan_backtest(
            bundle,
            request,
            horizon_bars=int(request.get("horizon_bars") or 15),
            step=backtest_step,
        )
        causal_audit = run_causal_replay_audit(bundle["target"]["bars"])
        bt_path = output_dir / f"{safe_sym}_backtest.json"
        bt_payload = {
            "symbol": meta["symbol"],
            "market": meta["market"],
            "causal_replay_audit": causal_audit,
            "backtest": bt_res,
        }
        bt_path.write_text(json.dumps(bt_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        report["rendered_paths"]["backtest_json"] = str(bt_path.resolve())

    md_path = output_dir / f"{safe_sym}_report.md"
    report["rendered_paths"]["report_md"] = str(md_path.resolve())

    validate_report(report)
    render_html(report, html_path)
    jpack_path.write_text(json.dumps(report["judgment_pack"], ensure_ascii=False, indent=2), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # 面向回答的中文 Markdown 报告：数字全部取自上面的 JSON，供 agent 直接转述
    md_path.write_text(render_report_markdown(report, bt_payload), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-market K-line algebraic analyzer & conformal forecaster")
    parser.add_argument("--market", choices=["CN", "HK", "US"], default=None, help="Market code: CN, HK, US")
    parser.add_argument("--symbol", required=True, help="Instrument symbol, e.g. 000001.SH, 600519.SH, 00700.HK, NVDA.US")
    parser.add_argument("--name", default=None, help="Optional instrument display name (auto-detects ST/*ST when containing ST)")
    parser.add_argument("--asset-type", choices=["index", "equity", "etf"], default=None)
    parser.add_argument("--st", action="store_true", help="Mark CN A-share stock as ST/*ST risk-warning stock")
    parser.add_argument("--ipo-no-limit", action="store_true", help="Mark CN stock as within first 5 IPO trading days without price limit")
    parser.add_argument("--benchmark", default=None, help="Optional benchmark symbol override")
    parser.add_argument("--days", type=int, default=120, help="Display bars (60~500)")
    parser.add_argument("--horizon-bars", type=int, default=15, help="Future forecast horizon bars (default 15)")
    parser.add_argument("--as-of", default=None, help="Optional cutoff date YYYY-MM-DD")
    parser.add_argument(
        "--adjustment",
        choices=["raw", "split", "total_return"],
        default=None,
        help="Price adjustment (default: total_return for equity/ETF = 前复权含分红, raw for index)",
    )
    parser.add_argument(
        "--color-scheme",
        choices=["auto", "cn_red_up", "us_green_up"],
        default="auto",
        help="Candlestick color scheme (default: auto = cn_red_up for CN/HK, us_green_up for US)",
    )
    parser.add_argument("--account-equity", type=float, default=None, help="Optional account equity for lot-size position sizing")
    parser.add_argument(
        "--max-risk-pct", type=float, default=0.01, help="Max risk fraction per trade, 0 < x <= 0.05 (default 0.01 = 1%%; write 1%% as 0.01)"
    )
    parser.add_argument("--input", default=None, help="Optional local real JSON bundle or bar array file")
    parser.add_argument("--output-dir", required=True, help="Output directory for JSON + PNG + HTML artifacts")
    parser.add_argument("--offline", action="store_true", help="Disable live network requests (requires --input local file)")
    parser.add_argument("--backtest", action="store_true", help="Run plan-replay backtest & causal replay audit")
    parser.add_argument("--backtest-step", type=int, default=1, help="Decision-day step for plan replay backtest (default 1 = every bar)")
    args = parser.parse_args()

    req: AnalysisRequest = {
        "symbol": args.symbol,
        "display_bars": args.days,
        "structure_bars": 120,
        "horizon_bars": args.horizon_bars,
        "account_equity": args.account_equity,
        "max_risk_pct": args.max_risk_pct,
        "color_scheme": args.color_scheme,  # type: ignore[typeddict-item]
        "timeframes": ["1D"],
    }
    if args.name:
        req["name"] = args.name
    if args.st:
        req["is_st"] = True
    if args.ipo_no_limit:
        req["is_ipo_no_limit"] = True
    if args.market:
        req["market"] = args.market  # type: ignore[typeddict-item]
    if args.asset_type:
        req["asset_type"] = args.asset_type  # type: ignore[typeddict-item]
    if args.benchmark:
        req["benchmark_symbol"] = args.benchmark
    if args.adjustment:
        req["adjustment"] = args.adjustment  # type: ignore[typeddict-item]
    if args.as_of:
        req["as_of"] = args.as_of

    try:
        report = run_pipeline(
            req,
            output_dir=Path(args.output_dir),
            input_path=Path(args.input) if args.input else None,
            allow_network=not args.offline,
            run_backtest=args.backtest,
            backtest_step=max(1, args.backtest_step),
        )
    except RuntimeError as exc:
        # 真实行情取不到：数据源失败、限流、数据损坏或历史不足
        raise SystemExit(f"[DATA_UNAVAILABLE] {exc}") from exc
    except ValueError as exc:
        # 参数或输入不合法：代码无法识别、参数越界、本地文件格式错误
        raise SystemExit(f"[INVALID_ARGUMENT] {exc}") from exc
    summary_out = {
        "meta": report["meta"],
        "quality": {k: report["quality"][k] for k in ("status", "data_source_class", "benchmark_source", "warnings", "notes")},
        "market_benchmark": {
            "index_symbol": report["market_benchmark"]["index_symbol"],
            "regime": report["market_benchmark"]["regime"],
            "regime_score": report["market_benchmark"]["regime_score"],
            "relative_strength_vs_index": report["market_benchmark"]["relative_strength_vs_index"],
        },
        "multi_timeframe_score": report["multi_timeframe_score"],
        "meta_labeling_decision": report["meta_labeling_decision"],
        "triple_barrier_plan": report["triple_barrier_plan"],
        "future_projection_15d": {
            "horizon_dates": report["future_projection_15d"]["horizon_dates"],
            "q25": report["future_projection_15d"]["q25"],
            "base_median_q50": report["future_projection_15d"]["base_median_q50"],
            "optimistic_q75": report["future_projection_15d"]["optimistic_q75"],
            "calibration_audit": report["future_projection_15d"]["calibration_audit"],
        },
        "debate_and_veto": report["debate_and_veto"],
        "rendered_paths": report["rendered_paths"],
    }
    print(json.dumps(summary_out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
