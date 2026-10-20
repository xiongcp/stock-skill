#!/usr/bin/env python3
"""Discover genuine positive-drift + >=70% hit-rate K-line setups on DEV (2021..2024-12-09)
and verify stability on VAL (2025-01-02..2025-06-09). Never touches TEST.
"""
from __future__ import annotations

import json
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from pathlib import Path

from aggregate import SPLITS, _non_overlapping_episodes, wilson
from common import ART, BENCHMARKS, PIT_UNIVERSE, adjusted_bars, load_bars, load_cache
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from evaluation import label_triple_barrier_event  # noqa: E402
from indicators import atr_series, bollinger_bands, bbw_percentile, ema, rsi, sma  # noqa: E402


def build_benchmark_features(market: str) -> dict[str, dict]:
    bars = load_bars(market, BENCHMARKS[market]["symbol"])
    closes = [float(b["close"]) for b in bars]
    opens = [float(b["open"]) for b in bars]
    e20 = ema(closes, 20)
    e60 = ema(closes, 60)
    e200 = ema(closes, 200)
    r14 = rsi(closes, 14)
    a14 = atr_series(bars, 14)
    out: dict[str, dict] = {}
    for i, b in enumerate(bars):
        if i < 60:
            continue
        c = closes[i]
        ret1 = c / closes[i - 1] - 1.0
        ret3 = c / closes[i - 3] - 1.0
        ret5 = c / closes[i - 5] - 1.0
        ret10 = c / closes[i - 10] - 1.0
        out[b["day"]] = {
            "c": c,
            "bull_bar": c > opens[i],
            "above_e20": c >= float(e20[i] or c),
            "above_e60": c >= float(e60[i] or c),
            "above_e200": c >= float(e200[i] or c),
            "e20_gt_e60": float(e20[i] or c) >= float(e60[i] or c),
            "rsi14": float(r14[i] or 50.0),
            "ret1": ret1,
            "ret3": ret3,
            "ret5": ret5,
            "ret10": ret10,
            "ext_atr": (c - float(e20[i] or c)) / max(1e-6, float(a14[i] or (c * 0.01))),
        }
    return out


def scan_market(market: str, max_symbols: int = 350) -> list[dict]:
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
    records: list[dict] = []
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
        bb = bollinger_bands(closes, 20, 2.0)
        bbw_pct = bbw_percentile(bb["bbw"], 120)
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
                # Ensure next bar is not stale suspension
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
                bbw_v = float(bbw_pct[t] or 50.0)
                ext = (c - e20_v) / atr_v
                ret1 = c / closes[t - 1] - 1.0
                ret3 = c / closes[t - 3] - 1.0
                ret5 = c / closes[t - 5] - 1.0
                ret10 = c / closes[t - 10] - 1.0
                ret20 = c / closes[t - 20] - 1.0
                rng = max(1e-9, h - l)
                body_ratio = (c - o) / rng
                lower_wick = (min(o, c) - l) / rng
                hi20 = max(highs[t - 19 : t + 1])
                lo20 = min(lows[t - 19 : t + 1])
                pos20 = (c - lo20) / max(1e-9, hi20 - lo20)

                bf = bench_feats[day]

                # Only keep candidate stock-days that match at least one plausible long setup family
                c_oversold = (rsi_v <= 35 or ext <= -1.2) and (c > o or lower_wick >= 0.35)
                c_pullback = (c >= e200_v and e60_v >= e200_v and -1.2 <= ext <= 0.0 and c > o)
                c_bench_dip = (bf["ret5"] <= -0.02 and bf["bull_bar"] and c > o)
                c_breakout = (bbw_v <= 25.0 and c >= hi20 * 0.995 and rv_v >= 1.3 and c > o)
                if not (c_oversold or c_pullback or c_bench_dip or c_breakout):
                    continue

                # Compute exact forward 5d, 10d, 15d open-to-close return and barrier outcomes
                entry_open = opens[t + 1]
                if entry_open <= 0:
                    continue
                raw_hold_5 = closes[t + 5] / entry_open - 1.0
                raw_hold_10 = closes[t + 10] / entry_open - 1.0
                raw_hold_15 = closes[t + 15] / entry_open - 1.0

                # Maximum favorable / adverse excursion over 10 and 15 bars
                mfe10_atr = (max(highs[t + 1 : t + 11]) - entry_open) / atr_v
                mae10_atr = (entry_open - min(lows[t + 1 : t + 11])) / atr_v
                mfe15_atr = (max(highs[t + 1 : t + 16]) - entry_open) / atr_v
                mae15_atr = (entry_open - min(lows[t + 1 : t + 16])) / atr_v

                records.append({
                    "symbol": sym,
                    "day": day,
                    "c": c,
                    "entry_open": entry_open,
                    "atr": atr_v,
                    "atr_pct": atr_v / c,
                    "ext": ext,
                    "rsi": rsi_v,
                    "rsi_turn": rsi_v > rsi_prev,
                    "rv": rv_v,
                    "bbw": bbw_v,
                    "above_e200": c >= e200_v,
                    "e60_gt_e200": e60_v >= e200_v,
                    "e20_gt_e60": e20_v >= e60_v,
                    "ret1": ret1,
                    "ret3": ret3,
                    "ret5": ret5,
                    "ret10": ret10,
                    "ret20": ret20,
                    "body_ratio": body_ratio,
                    "lower_wick": lower_wick,
                    "pos20": pos20,
                    "bf_ret1": bf["ret1"],
                    "bf_ret3": bf["ret3"],
                    "bf_ret5": bf["ret5"],
                    "bf_rsi": bf["rsi14"],
                    "bf_bull": bf["bull_bar"],
                    "bf_above_e60": bf["above_e60"],
                    "bf_above_e200": bf["above_e200"],
                    "bf_ext": bf["ext_atr"],
                    "hold5": raw_hold_5,
                    "hold10": raw_hold_10,
                    "hold15": raw_hold_15,
                    "mfe10": mfe10_atr,
                    "mae10": mae10_atr,
                    "mfe15": mfe15_atr,
                    "mae15": mae15_atr,
                })
    return records


def sim_barrier(r: dict, tp_atr: float, sl_atr: float, fee: float, horizon: int = 15) -> tuple[bool, float]:
    """Fast approximate barrier check from MFE/MAE + hold return (exact check done later)."""
    mfe = r["mfe10"] if horizon == 10 else r["mfe15"]
    mae = r["mae10"] if horizon == 10 else r["mae15"]
    hold = r["hold10"] if horizon == 10 else r["hold15"]
    atr_pct = r["atr_pct"]
    # Conservative: if both MFE>=tp and MAE>=sl, count SL first
    if mae >= sl_atr:
        ret = -sl_atr * atr_pct - fee
        return False, ret
    if mfe >= tp_atr:
        ret = tp_atr * atr_pct - fee
        return True, ret
    ret = hold - fee
    return ret > 0, ret


def evaluate_candidates(records: list[dict], market: str) -> None:
    fee = {"CN": 0.0012, "HK": 0.0028, "US": 0.0004}[market]
    dev_lo, dev_hi = SPLITS["dev"]
    val_lo, val_hi = SPLITS["val"]
    dev = [r for r in records if dev_lo <= r["day"] <= dev_hi]
    val = [r for r in records if val_lo <= r["day"] <= val_hi]
    print(f"\n==================== {market} CANDIDATES (dev={len(dev)}, val={len(val)}) ====================")

    candidates = [
        ("H1_oversold_rsi28_turn", lambda r: r["rsi"] <= 28 and r["rsi_turn"] and r["body_ratio"] >= 0.3),
        ("H2_oversold_ext18_bull", lambda r: r["ext"] <= -1.8 and r["body_ratio"] >= 0.4),
        ("H3_uptrend_dip_ext06_rv08", lambda r: r["above_e200"] and r["e60_gt_e200"] and -1.2 <= r["ext"] <= -0.4 and r["rv"] <= 0.85 and r["body_ratio"] >= 0.3),
        ("H4_bench_oversold_rebound", lambda r: r["bf_rsi"] <= 36 and r["bf_bull"] and r["above_e200"] and r["body_ratio"] >= 0.3),
        ("H5_bench_dip5d_rebound", lambda r: r["bf_ret5"] <= -0.03 and r["bf_bull"] and r["ext"] <= -0.8 and r["body_ratio"] >= 0.35),
        ("H6_squeeze_breakout", lambda r: r["bbw"] <= 20 and r["pos20"] >= 0.95 and 1.3 <= r["rv"] <= 3.0 and r["bf_above_e60"] and r["above_e200"]),
        ("H7_uptrend_5d_drop_hammer", lambda r: r["above_e200"] and r["ret5"] <= -0.05 and (r["lower_wick"] >= 0.45 or r["body_ratio"] >= 0.5)),
        ("H8_deep_ext22_wick", lambda r: r["ext"] <= -2.2 and (r["lower_wick"] >= 0.40 or r["body_ratio"] >= 0.45)),
    ]

    for name, fn in candidates:
        for tp_a, sl_a in ((0.8, 2.0), (0.7, 2.2), (1.0, 2.0)):
            d_sub = [r for r in dev if fn(r)]
            v_sub = [r for r in val if fn(r)]
            if len(d_sub) < 60:
                continue
            d_eps = _non_overlapping_episodes(d_sub)
            v_eps = _non_overlapping_episodes(v_sub)
            d_res = [sim_barrier(r, tp_a, sl_a, fee, 15) for r in d_eps]
            v_res = [sim_barrier(r, tp_a, sl_a, fee, 15) for r in v_eps]
            d_win = sum(1 for w, _ in d_res if w)
            v_win = sum(1 for w, _ in v_res if w)
            d_mean = sum(x for _, x in d_res) / len(d_res) * 100 if d_res else 0
            v_mean = sum(x for _, x in v_res) / len(v_res) * 100 if v_res else 0
            d_hold15 = sum(r["hold15"] - fee for r in d_eps) / len(d_eps) * 100 if d_eps else 0
            v_hold15 = sum(r["hold15"] - fee for r in v_eps) / len(v_eps) * 100 if v_eps else 0
            print(
                f"  {name:28s} ({tp_a}/{sl_a}) | "
                f"DEV ep={len(d_eps):4d} win={d_win/len(d_eps)*100:5.1f}% mean={d_mean:+5.2f}% (raw15={d_hold15:+5.2f}%) | "
                f"VAL ep={len(v_eps):3d} win={v_win/len(v_eps)*100 if v_eps else 0:5.1f}% mean={v_mean:+5.2f}% (raw15={v_hold15:+5.2f}%)"
            )


def main() -> None:
    for m in ("CN", "HK", "US"):
        recs = scan_market(m, max_symbols=300)
        evaluate_candidates(recs, m)


if __name__ == "__main__":
    main()
