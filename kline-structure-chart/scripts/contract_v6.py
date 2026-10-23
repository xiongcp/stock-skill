"""v6 输出契约：把流水线内部报告投影为稳定的 result.json 与非稳定的 chart_data.json。

内部报告（analysis.analyze_bundle 的返回值）沿用 v5 字段名，供回测、历史回放与单元测试使用；
对外只写本模块投影出的两个文件。字段定义与稳定性规则见 assets/analysis.schema.json 与 references/output-fields.md。
"""
from __future__ import annotations

from typing import Any

from market_rules import MARKET_SESSIONS
from price_text import collect_price_whitelist

SCHEMA_VERSION = "6.0.0"

_REQUEST_KEYS = (
    "symbol", "market", "asset_type", "display_bars", "structure_bars", "horizon_bars", "account_equity",
    "max_risk_pct", "color_scheme", "as_of", "adjustment", "benchmark_symbol",
)
# 只用于画路径的字段放进 chart_data，不进入稳定契约
_SCENARIO_DRAW_KEYS = ("waypoints_offset", "step_labels", "eta_bars")
_SCENARIO_DROP_KEYS = (*_SCENARIO_DRAW_KEYS, "target_within_horizon")
_QUOTABLE_SECTIONS = ("structure", "indicators", "scenarios", "plan", "volatility_range")


def artifact_files(safe_sym: str, has_backtest: bool) -> dict[str, str]:
    files = {
        "result_json": f"{safe_sym}_result.json",
        "chart_data_json": f"{safe_sym}_chart_data.json",
        "preview_png": f"{safe_sym}_preview.png",
        "dashboard_html": f"{safe_sym}_dashboard.html",
        "report_md": f"{safe_sym}_report.md",
    }
    if has_backtest:
        files["backtest_json"] = f"{safe_sym}_backtest.json"
    return files


def _strip_scenario(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in _SCENARIO_DROP_KEYS}


def _draw_only(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item[k] for k in _SCENARIO_DRAW_KEYS if k in item}


def build_result(report: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    meta = report["meta"]
    request = report.get("request") or {}
    dec = report["meta_labeling_decision"]
    mtf = report["multi_timeframe_score"]
    deb = report["debate_and_veto"]
    mb = report["market_benchmark"]
    mi = report["mathematical_indicators"]
    sl = mi["structure_levels"]
    rs = report["rule_scenarios"]
    tb = report["triple_barrier_plan"]
    fp = report["future_projection_15d"]
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            **meta,
            "timezone": MARKET_SESSIONS[meta["market"]][0],
            "engine_version": SCHEMA_VERSION,
            "request": {k: request[k] for k in _REQUEST_KEYS if k in request},
        },
        "quality": dict(report["quality"]),
        "verdict": {
            "action": dec["action"],
            "action_cn": dec["action_cn"],
            "setup_type": dec["setup_type"],
            "setup_label_cn": dec["setup_label_cn"],
            "risk_veto": bool(deb["risk_officer_veto"]),
            "veto_reasons": list(deb.get("veto_reasons") or []),
            "veto_codes": list(deb.get("veto_codes") or []),
            "rule_score": mtf["rule_score"],
            "rating": mtf["rating"],
            "rating_cn": mtf["rating_cn"],
            "headline_cn": deb["final_verdict_cn"],
            "overextended": bool(dec.get("overextended")),
            "rule_detail": {
                "components": mtf.get("components"),
                "components_total": mtf.get("components_total"),
                "regime_adjustment": mtf.get("regime_adjustment"),
                "timeframes_aligned": mtf.get("timeframes_aligned"),
                "scoring_method": dec.get("scoring_method"),
                "note": mtf.get("note"),
            },
            "strategy_signals": list(report.get("strategy_signals") or []),
            "anti_quant_traps": dec.get("anti_quant_traps"),
            "bull_case": list(deb.get("bull_case") or []),
            "bear_case": list(deb.get("bear_case") or []),
        },
        "market_context": {
            "index_symbol": mb.get("index_symbol"),
            "index_name": mb.get("index_name"),
            "regime": mb.get("regime"),
            "regime_label_cn": mb.get("regime_label_cn"),
            "regime_score": mb.get("regime_score"),
            "relative_strength_vs_index": mb.get("relative_strength_vs_index"),
            "benchmark_missing": bool(mb.get("benchmark_missing")),
            "source": mb.get("source"),
            "bench_ret1": mb.get("bench_ret1"),
            "bench_ret5": mb.get("bench_ret5"),
        },
        "structure": {
            "last_close": float(report["series_export"]["bars"][-1]["close"]),
            "regime": report["market_regime"],
            "key_levels": sl["key_levels"],
            "support_zone": sl["support_zone"],
            "resistance_zone": sl["resistance_zone"],
            "channel": sl["wedge_structure"],
            "fib_levels": sl["fib_levels"],
            "recent_gaps": sl["recent_gaps"],
            "abc_scenario": sl["abc_scenario"],
            "wave_counts": sl["wave_counts"],
            "patterns": report["algebraic_patterns_detected"],
            "smc": report["smc_structures"],
            "volume_profile": {k: v for k, v in report["volume_profile"].items() if k != "bins"},
        },
        "indicators": {k: mi[k] for k in ("trend", "momentum", "volatility", "qlib_factors")},
        "scenarios": {
            "horizon_bars": rs["horizon_bars"],
            "atr_pct": rs.get("atr_pct"),
            "sigma_1d_pct": rs.get("sigma_1d_pct"),
            "min_trigger_distance_atr": rs.get("min_trigger_distance_atr"),
            "breakout_level": rs["breakout_level"],
            "breakdown_level": rs["breakdown_level"],
            "contested_levels": list(rs.get("contested_levels") or []),
            "paths": {k: _strip_scenario(rs[k]) for k in ("bull", "neutral", "bear")},
            "active": [_strip_scenario(s) for s in rs.get("scenarios") or []],
            "precondition_met_count": rs.get("precondition_met_count"),
            "impulse_wave": rs.get("impulse_wave"),
        },
        "plan": {
            "long": {k: v for k, v in tb.items() if k not in ("holder_defense_plan", "short_plan")},
            "holder_defense": tb["holder_defense_plan"],
            "short": tb["short_plan"],
        },
        "volatility_range": {
            "method": fp.get("method"),
            "promotion_status": fp["promotion_status"],
            "horizon_bars": fp["horizon_bars"],
            "horizon_dates": fp["horizon_dates"],
            "calendar_coverage": fp.get("calendar_coverage"),
            "q10": fp["q10"],
            "q25": fp["q25"],
            "q50": fp["base_median_q50"],
            "q75": fp["optimistic_q75"],
            "q90": fp["q90"],
            "q50_method": fp.get("q50_method"),
            "q50_note": fp.get("q50_note"),
            "band_50": fp.get("conformal_50pct_band"),
            "band_80": fp["conformal_80pct_band"],
            "daily_price_limit_pct": fp.get("daily_price_limit_pct"),
            "volatility_scaling": fp.get("volatility_scaling"),
            "calibration_audit": fp.get("calibration_audit"),
        },
        "files": dict(files),
        "reproducibility": {**report["reproducibility"], "engine_version": SCHEMA_VERSION},
    }
    # 可引用价位只从 result.json 自己的价位字段与最近 20 根K线收集：前者能在 result.json 里找到出处，
    # 最近 20 根K线的价格只存在于 chart_data.json（result.json 不含K线序列）
    wl = collect_price_whitelist(
        {k: result[k] for k in _QUOTABLE_SECTIONS},
        int(meta.get("price_decimals") or 2),
        report["series_export"]["bars"],
    )
    result["quotable_prices"] = {k: wl[k] for k in ("price_decimals", "tolerance", "prices")}
    return result


def build_chart_data(report: dict[str, Any]) -> dict[str, Any]:
    se = report["series_export"]
    rs = report["rule_scenarios"]
    return {
        "schema_version": SCHEMA_VERSION,
        "stability": "unstable",
        "note": "非稳定接口：只供本技能的 PNG/HTML 渲染器读取，字段可能在任何版本变化；对外请读 result.json",
        "bars": se["bars"],
        "pivots": se["pivots"],
        "background": se.get("background"),
        "intraday": se.get("intraday"),
        "data_note": se.get("data_note"),
        "rs_series": list(report["market_benchmark"].get("rs_series") or []),
        "volume_profile_bins": list(report["volume_profile"].get("bins") or []),
        "scenario_paths": {
            "paths": {k: _draw_only(rs[k]) for k in ("bull", "neutral", "bear")},
            "active": [{"id": s["id"], **_draw_only(s)} for s in rs.get("scenarios") or []],
        },
        "replay_snapshots": list(report.get("replay_snapshots") or []),
    }
