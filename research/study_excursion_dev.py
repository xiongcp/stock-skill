#!/usr/bin/env python3
"""Study true price excursion (MFE/MAE) and feature-conditioned barrier hit rates
on the DEVELOPMENT split (2021-01-01 .. 2024-12-09), then verify on VAL (2025H1).
Never touches TEST (2025-07-01+).
"""
from __future__ import annotations

import json
import math
import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from aggregate import SPLITS, _non_overlapping_episodes, wilson
from common import ART, BENCHMARKS, adjusted_bars, load_bars, load_cache
from diagnose_dev import load_completed_signals
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from evaluation import label_triple_barrier_event  # noqa: E402
from indicators import atr_series, ema, rsi, sma  # noqa: E402


def compute_fast_features(bars: list[dict]) -> dict[str, list]:
    closes = [float(b["close"]) for b in bars]
    vols = [float(b.get("volume") or 0.0) for b in bars]
    return {
        "ema20": ema(closes, 20),
        "ema60": ema(closes, 60),
        "ema200": ema(closes, 200),
        "sma250": sma(closes, 250),
        "rsi14": rsi(closes, 14),
        "atr14": atr_series(bars, 14),
        "vol_ma20": sma(vols, 20),
    }


def enrich_dev_val_features(rows: list[dict], market: str, max_symbols: int = 400) -> list[dict]:
    dev_lo, _ = SPLITS["dev"]
    _, val_hi = SPLITS["val"]
    sub = [r for r in rows if dev_lo <= r["day"] <= val_hi]
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in sub:
        by_sym[r["symbol"]].append(r)

    out: list[dict] = []
    for sym in sorted(by_sym)[:max_symbols]:
        payload = load_cache(market, sym)
        _, bars = adjusted_bars(payload, mode="total_return")
        idx = {b["day"]: i for i, b in enumerate(bars)}
        feats = compute_fast_features(bars)
        n = len(bars)
        for r in by_sym[sym]:
            t = idx.get(r["day"])
            if t is None or t < 250 or t + 16 >= n:
                continue
            c = float(bars[t]["close"])
            o = float(bars[t]["open"])
            h = float(bars[t]["high"])
            l = float(bars[t]["low"])
            a14 = float(feats["atr14"][t] or (c * 0.02))
            if a14 <= 0:
                continue
            e20 = float(feats["ema20"][t] or c)
            e60 = float(feats["ema60"][t] or c)
            e200 = float(feats["ema200"][t] or c)
            r14 = float(feats["rsi14"][t] or 50.0)
            v20 = float(feats["vol_ma20"][t] or 1.0)
            rv = float(bars[t].get("volume") or 0.0) / v20 if v20 > 0 else 1.0

            # Causal 3-day / 5-day return before signal
            ret3 = c / float(bars[t - 3]["close"]) - 1.0
            ret5 = c / float(bars[t - 5]["close"]) - 1.0
            ret20 = c / float(bars[t - 20]["close"]) - 1.0
            ext_atr = (c - e20) / a14
            bull_candle = c > o and (c - o) >= 0.45 * (h - l + 1e-9)
            lower_wick = (min(o, c) - l) / (h - l + 1e-9)

            # Next-bar open & excursion over 15 bars (in PIT-rescaled units)
            pit, cut = make_pit_slice(bars, t, 15)
            entry_open = float(pit[cut + 1]["open"])
            a14_pit = a14 * (float(pit[cut]["close"]) / c) if c > 0 else a14

            # Evaluate multiple (tp_atr, sl_atr) barrier geometries using exact label_triple_barrier_event
            geoms = {}
            for tp_m, sl_m in ((0.6, 1.8), (0.7, 2.0), (0.8, 2.0), (0.8, 2.4), (1.0, 2.2), (1.2, 1.5)):
                res_g = label_triple_barrier_event(
                    pit,
                    entry_idx=cut + 1,
                    tp_price=entry_open + tp_m * a14_pit,
                    sl_price=max(0.01, entry_open - sl_m * a14_pit),
                    horizon_bars=15,
                    market=market,
                    asset_type="equity",
                    symbol=sym,
                    trailing_stop=False,
                )
                geoms[f"{tp_m}_{sl_m}"] = res_g

            out.append({
                **r,
                "ext_atr": round(ext_atr, 3),
                "rsi14_calc": round(r14, 2),
                "rvol_calc": round(rv, 2),
                "above_e200": c >= e200,
                "e60_above_e200": e60 >= e200,
                "e20_above_e60": e20 >= e60,
                "ret3": ret3,
                "ret5": ret5,
                "ret20": ret20,
                "bull_candle": bull_candle,
                "lower_wick": lower_wick,
                "geoms": geoms,
            })
    return out


def eval_geom_filter(rows: list[dict], geom_key: str, pred, name: str) -> None:
    dev_lo, dev_hi = SPLITS["dev"]
    val_lo, val_hi = SPLITS["val"]
    for split_name, lo, hi in (("DEV", dev_lo, dev_hi), ("VAL", val_lo, val_hi)):
        sub = [
            r for r in rows
            if lo <= r["day"] <= hi
            and pred(r)
            and r["geoms"][geom_key].get("tradable")
        ]
        n = len(sub)
        if n == 0:
            print(f"  [{split_name}] {name:38s} ({geom_key}): n=0")
            continue
        pos = sum(1 for r in sub if float(r["geoms"][geom_key]["net_return"]) > 0)
        tp = sum(1 for r in sub if r["geoms"][geom_key]["outcome"] == "TP_BARRIER")
        sl = sum(1 for r in sub if str(r["geoms"][geom_key]["outcome"]).startswith("SL_BARRIER"))
        eps = _non_overlapping_episodes(sub)
        ep_pos = sum(1 for r in eps if float(r["geoms"][geom_key]["net_return"]) > 0)
        mean_r = sum(float(r["geoms"][geom_key]["net_return"]) for r in sub) / n * 100
        print(
            f"  [{split_name}] {name:38s} ({geom_key}): n={n:5d} (ep={len(eps):4d}) | "
            f"net>0={pos/n*100:5.2f}% CI={wilson(pos, n)} | "
            f"ep_net>0={ep_pos/len(eps)*100:5.2f}% epCI={wilson(ep_pos, len(eps))} | "
            f"TP/(TP+SL)={tp/max(1,tp+sl)*100:5.2f}% | mean={mean_r:+5.2f}%"
        )


def main() -> None:
    run_root = ART / "runs" / "baseline_v1"
    for market in ("CN", "HK", "US"):
        rows = load_completed_signals(run_root, market)
        if not rows:
            continue
        enriched = enrich_dev_val_features(rows, market, max_symbols=300)
        print(f"\n================ {market} EXCURSION & GEOMETRY STUDY (enriched={len(enriched)}) ================")
        for gk in ("1.2_1.5", "1.0_2.2", "0.8_2.0", "0.8_2.4", "0.7_2.0", "0.6_1.8"):
            eval_geom_filter(enriched, gk, lambda r: True, "all signals")

        print(f"\n--- {market} Feature filters with geometry 0.7_2.0 ---")
        gk = "0.7_2.0"
        eval_geom_filter(enriched, gk, lambda r: r["above_e200"] and r["e60_above_e200"], "uptrend (C>E200 & E60>E200)")
        eval_geom_filter(
            enriched, gk,
            lambda r: r["above_e200"] and r["e60_above_e200"] and r["ext_atr"] <= -0.3,
            "uptrend + pullback (ext_atr<=-0.3)",
        )
        eval_geom_filter(
            enriched, gk,
            lambda r: r["above_e200"] and r["e60_above_e200"] and r["rsi14_calc"] <= 45,
            "uptrend + RSI<=45",
        )
        eval_geom_filter(
            enriched, gk,
            lambda r: r["ext_atr"] <= -0.8 and r["bull_candle"],
            "deep pullback ext<=-0.8 + bull_candle",
        )
        eval_geom_filter(
            enriched, gk,
            lambda r: r["ret5"] <= -0.04 and r["above_e200"],
            "5d dip <= -4% in uptrend (C>E200)",
        )


if __name__ == "__main__":
    main()
