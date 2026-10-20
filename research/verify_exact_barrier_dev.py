#!/usr/bin/env python3
"""Exact bar-by-bar `label_triple_barrier_event` verification on DEV (2021..2024-12-09)
and VAL (2025-01-02..2025-06-09) for candidate high-conviction setups.
Never touches TEST (2025-07-01+).
"""
from __future__ import annotations

import json
import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from aggregate import SPLITS, _non_overlapping_episodes, wilson
from common import ART, BENCHMARKS, PIT_UNIVERSE, adjusted_bars, load_bars, load_cache
from discover_alpha_dev import build_benchmark_features
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from evaluation import label_triple_barrier_event  # noqa: E402
from indicators import atr_series, bollinger_bands, bbw_percentile, ema, rsi, sma  # noqa: E402


def scan_exact(market: str, max_symbols: int = 500) -> list[dict]:
    pit = json.loads(PIT_UNIVERSE.read_text(encoding="utf-8"))["markets"][market]
    symbol_periods: dict[str, list[dict]] = defaultdict(list)
    for p in pit["periods"]:
        for s in p["symbols"]:
            symbol_periods[s].append({"start_day": p["start_day"], "end_day": p["end_day"]})

    bench_feats = build_benchmark_features(market)
    bench_days = sorted(bench_feats)
    dev_lo, _ = SPLITS["dev"]
    _, val_hi = SPLITS["val"]

    symbols = sorted(symbol_periods)[:max_symbols]
    out: list[dict] = []
    for sym in symbols:
        try:
            payload = load_cache(market, sym)
            _, bars = adjusted_bars(payload, mode="total_return")
        except Exception:
            continue
        n = len(bars)
        if n < 450:
            continue
        closes = [float(b["close"]) for b in bars]
        opens = [float(b["open"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        vols = [float(b.get("volume") or 0.0) for b in bars]
        e20 = ema(closes, 20)
        e60 = ema(closes, 60)
        e200 = ema(closes, 200)
        r14 = rsi(closes, 14)
        a14 = atr_series(bars, 14)
        v20 = sma(vols, 20)
        idx = {b["day"]: i for i, b in enumerate(bars)}

        for per in symbol_periods[sym]:
            lo_d = max(per["start_day"], dev_lo)
            hi_d = min(per["end_day"], val_hi)
            if lo_d > hi_d:
                continue
            for day in bench_days:
                if day < lo_d or day > hi_d:
                    continue
                t = idx.get(day)
                if t is None or t < 250 or t + 16 >= n:
                    continue
                b_pos = bisect_right(bench_days, day)
                if b_pos < len(bench_days) and bars[t + 1]["day"] > bench_days[b_pos]:
                    continue
                c, o, h, l = closes[t], opens[t], highs[t], lows[t]
                atr_v = float(a14[t] or 0.0)
                if atr_v <= 0 or c <= 0:
                    continue
                e20_v = float(e20[t] or c)
                e60_v = float(e60[t] or c)
                e200_v = float(e200[t] or c)
                rsi_v = float(r14[t] or 50.0)
                rsi_prev = float(r14[t - 1] or rsi_v)
                rv_v = vols[t] / float(v20[t]) if (v20[t] and float(v20[t]) > 0) else 1.0
                ext = (c - e20_v) / atr_v
                ret5 = c / closes[t - 5] - 1.0
                rng = max(1e-9, h - l)
                body_ratio = (c - o) / rng
                lower_wick = (min(o, c) - l) / rng
                bf = bench_feats[day]

                # Pre-screen: require bullish reversal candle or strong wick after pullback/oversold
                is_cand = (
                    (ext <= -0.6 and (c > o or lower_wick >= 0.40))
                    or (rsi_v <= 36 and c > o)
                    or (bf["ret5"] <= -0.02 and bf["bull_bar"] and c > o)
                )
                if not is_cand:
                    continue

                pit, cut = make_pit_slice(bars, t, 15)
                entry_open = float(pit[cut + 1]["open"])
                scale_pit = float(pit[cut]["close"]) / c if c > 0 else 1.0
                atr_pit = atr_v * scale_pit

                # Run exact bar-by-bar label_triple_barrier_event for several barrier geometries
                exact = {}
                for tp_m, sl_m in ((0.8, 2.0), (0.9, 2.2), (1.0, 2.2), (1.1, 2.2)):
                    res = label_triple_barrier_event(
                        pit,
                        entry_idx=cut + 1,
                        tp_price=entry_open + tp_m * atr_pit,
                        sl_price=max(0.01, entry_open - sl_m * atr_pit),
                        horizon_bars=15,
                        market=market,
                        asset_type="equity",
                        symbol=sym,
                        trailing_stop=False,
                    )
                    exact[f"{tp_m}_{sl_m}"] = res

                out.append({
                    "symbol": sym,
                    "day": day,
                    "ext": ext,
                    "rsi": rsi_v,
                    "rsi_turn": rsi_v > rsi_prev,
                    "rv": rv_v,
                    "above_e200": c >= e200_v,
                    "e60_gt_e200": e60_v >= e200_v,
                    "ret5": ret5,
                    "body_ratio": body_ratio,
                    "lower_wick": lower_wick,
                    "bf_ret5": bf["ret5"],
                    "bf_rsi": bf["rsi14"],
                    "bf_bull": bf["bull_bar"],
                    "bf_above_e200": bf["above_e200"],
                    "exact": exact,
                })
    return out


def report_market(rows: list[dict], market: str) -> None:
    dev_lo, dev_hi = SPLITS["dev"]
    val_lo, val_hi = SPLITS["val"]
    dev = [r for r in rows if dev_lo <= r["day"] <= dev_hi]
    val = [r for r in rows if val_lo <= r["day"] <= val_hi]
    print(f"\n================ {market} EXACT BAR-BY-BAR VERIFICATION (dev={len(dev)}, val={len(val)}) ================")

    setups = [
        ("S1_bench_dip_rebound_ext08", lambda r: r["bf_ret5"] <= -0.025 and r["bf_bull"] and r["ext"] <= -0.8 and r["body_ratio"] >= 0.30),
        ("S2_oversold_ext18_bull", lambda r: r["ext"] <= -1.8 and r["body_ratio"] >= 0.35),
        ("S3_rsi30_turn_bull", lambda r: r["rsi"] <= 30 and r["rsi_turn"] and r["body_ratio"] >= 0.25),
        ("S4_uptrend_pullback_ext08_bull", lambda r: r["above_e200"] and r["e60_gt_e200"] and r["ext"] <= -0.8 and r["body_ratio"] >= 0.30),
        ("S5_combined_high_conv", lambda r: (
            (r["bf_ret5"] <= -0.02 and r["bf_bull"] and r["ext"] <= -0.8 and r["body_ratio"] >= 0.30)
            or (r["above_e200"] and r["ext"] <= -1.4 and r["body_ratio"] >= 0.35)
            or (r["rsi"] <= 28 and r["rsi_turn"] and r["body_ratio"] >= 0.30)
        )),
    ]

    for sname, fn in setups:
        for gk in ("0.8_2.0", "0.9_2.2", "1.0_2.2", "1.1_2.2"):
            d_sub = [r for r in dev if fn(r) and r["exact"][gk].get("tradable")]
            v_sub = [r for r in val if fn(r) and r["exact"][gk].get("tradable")]
            if len(d_sub) < 50:
                continue
            d_eps = _non_overlapping_episodes(d_sub)
            v_eps = _non_overlapping_episodes(v_sub)
            d_pos = sum(1 for r in d_eps if float(r["exact"][gk]["net_return"]) > 0)
            v_pos = sum(1 for r in v_eps if float(r["exact"][gk]["net_return"]) > 0)
            d_tp = sum(1 for r in d_eps if r["exact"][gk]["outcome"] == "TP_BARRIER")
            d_sl = sum(1 for r in d_eps if str(r["exact"][gk]["outcome"]).startswith("SL_BARRIER"))
            v_tp = sum(1 for r in v_eps if r["exact"][gk]["outcome"] == "TP_BARRIER")
            v_sl = sum(1 for r in v_eps if str(r["exact"][gk]["outcome"]).startswith("SL_BARRIER"))
            d_mean = sum(float(r["exact"][gk]["net_return"]) for r in d_eps) / len(d_eps) * 100
            v_mean = sum(float(r["exact"][gk]["net_return"]) for r in v_eps) / len(v_eps) * 100 if v_eps else 0.0
            print(
                f"  {sname:30s} [{gk}] | "
                f"DEV ep={len(d_eps):4d} net>0={d_pos/len(d_eps)*100:5.1f}% CI={wilson(d_pos,len(d_eps))} TP/(TP+SL)={d_tp/max(1,d_tp+d_sl)*100:5.1f}% mean={d_mean:+5.2f}% | "
                f"VAL ep={len(v_eps):3d} net>0={v_pos/len(v_eps)*100 if v_eps else 0:5.1f}% CI={wilson(v_pos,len(v_eps))} TP/(TP+SL)={v_tp/max(1,v_tp+v_sl)*100 if v_tp+v_sl else 0:5.1f}% mean={v_mean:+5.2f}%"
            )


def main() -> None:
    for m in ("CN", "HK", "US"):
        rows = scan_exact(m, max_symbols=400)
        report_market(rows, m)


if __name__ == "__main__":
    main()
