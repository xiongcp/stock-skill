#!/usr/bin/env python3
"""Evaluate the unified HIGH_CONVICTION_REBOUND mechanism across the FULL 1500-stock/day
PIT universe on DEV (2021-01-04..2024-12-09) and VAL (2025-01-02..2025-06-09).

Does NOT touch TEST (2025-07-01..2026-09-24).
Tests both:
1. Next-open execution
2. 5-day resting limit-order execution inside entry_zone = [c - 0.50*ATR, c + 0.25*ATR]
"""
from __future__ import annotations

import json
import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

from aggregate import SPLITS, _cluster_bootstrap, _non_overlapping_episodes, wilson
from common import ART, BENCHMARKS, PIT_UNIVERSE, adjusted_bars, load_bars, load_cache
from discover_alpha_dev import build_benchmark_features
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from evaluation import label_triple_barrier_event  # noqa: E402
from indicators import atr_series, ema, rsi  # noqa: E402


def is_high_conviction_rebound(
    market: str,
    c: float,
    o: float,
    h: float,
    l: float,
    raw_c: float,
    atr_v: float,
    e20_v: float,
    rsi_v: float,
    rsi_prev: float,
    bf: dict,
) -> tuple[bool, str]:
    """Unified, transparent K-line reversal setup across CN / HK / US."""
    if atr_v <= 0 or c <= 0 or raw_c < 2.0:
        return False, ""
    atr_pct = atr_v / c
    min_atr_pct = 0.020 if market == "HK" else 0.012
    if atr_pct < min_atr_pct:
        return False, ""

    rng = h - l
    if rng <= 0 or c <= o:
        return False, ""
    body_ratio = (c - o) / rng
    ext_atr = (c - e20_v) / atr_v

    if market == "HK":
        # HK has 28~34bps round-trip cost; require stronger benchmark rebound or deeper RSI turn
        cond_a = (
            bf["ret5"] <= -0.025
            and bf["ret1"] >= 0.005
            and ext_atr <= -1.0
            and body_ratio >= 0.32
        )
        cond_b = (
            rsi_v <= 26.0
            and rsi_v > rsi_prev
            and body_ratio >= 0.35
            and bf["ret1"] >= 0.0
        )
    else:
        cond_a = (
            bf["ret5"] <= -0.025
            and bf["ret1"] >= 0.003
            and ext_atr <= -1.0
            and body_ratio >= 0.32
        )
        cond_b = (
            ((rsi_v <= 28.0 and rsi_v > rsi_prev) or ext_atr <= -1.85)
            and body_ratio >= 0.35
            and (bf["bull_bar"] or bf["ret1"] >= 0.0)
        )

    if cond_a:
        return True, "BENCH_SYNC_REBOUND"
    if cond_b:
        return True, "DEEP_OVERSOLD_TURN"
    return False, ""


def scan_full_pit(market: str, split_hi: str) -> list[dict]:
    pit = json.loads(PIT_UNIVERSE.read_text(encoding="utf-8"))["markets"][market]
    symbol_periods: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in pit["periods"]:
        for s in p["symbols"]:
            symbol_periods[s].append((p["start_day"], p["end_day"]))

    bf_map = build_benchmark_features(market)
    bench_days = sorted(bf_map)
    dev_lo = SPLITS["dev"][0]

    signals: list[dict] = []
    for sym in sorted(symbol_periods):
        try:
            payload = load_cache(market, sym)
            _, bars = adjusted_bars(payload, mode="total_return")
        except Exception:
            continue
        n = len(bars)
        if n < 420:
            continue
        closes = [float(b["close"]) for b in bars]
        opens = [float(b["open"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        e20 = ema(closes, 20)
        r14 = rsi(closes, 14)
        a14 = atr_series(bars, 14)
        idx = {b["day"]: i for i, b in enumerate(bars)}

        for s_d, e_d in symbol_periods[sym]:
            lo_d, hi_d = max(s_d, dev_lo), min(e_d, split_hi)
            if lo_d > hi_d:
                continue
            for day in bench_days:
                if not (lo_d <= day <= hi_d):
                    continue
                t = idx.get(day)
                if t is None or t < 250 or t + 16 >= n:
                    continue
                b_pos = bisect_right(bench_days, day)
                if b_pos >= len(bench_days) or bars[t + 1]["day"] > bench_days[b_pos]:
                    continue

                c, o, h, l = closes[t], opens[t], highs[t], lows[t]
                raw_c = float(bars[t].get("raw_close", c))
                atr_v = float(a14[t] or 0.0)
                e20_v = float(e20[t] or c)
                rsi_v = float(r14[t] or 50.0)
                rsi_prev = float(r14[t - 1] or rsi_v)
                bf = bf_map[day]

                matched, subtype = is_high_conviction_rebound(
                    market, c, o, h, l, raw_c, atr_v, e20_v, rsi_v, rsi_prev, bf
                )
                if not matched:
                    continue

                pit_s, cut = make_pit_slice(bars, t, 15)
                c_pit = float(pit_s[cut]["close"])
                atr_pit = atr_v * (c_pit / c) if c > 0 else atr_v
                entry_low = c_pit - 0.50 * atr_pit
                entry_high = c_pit + 0.25 * atr_pit

                # 1. Next-open execution
                entry_open = float(pit_s[cut + 1]["open"])
                res_open = label_triple_barrier_event(
                    pit_s,
                    entry_idx=cut + 1,
                    tp_price=entry_open + 0.85 * atr_pit,
                    sl_price=max(0.01, entry_open - 2.20 * atr_pit),
                    horizon_bars=15,
                    market=market,
                    asset_type="equity",
                    symbol=sym,
                    trailing_stop=False,
                )

                # 2. 5-day resting limit-order execution at entry_high
                lim_fill = False
                lim_res: dict = {}
                max_wait = min(len(pit_s) - 1, cut + 5)
                max_b_day = bench_days[min(len(bench_days) - 1, b_pos + 4)]
                for k_idx in range(cut + 1, max_wait + 1):
                    b_k = pit_s[k_idx]
                    if b_k["day"] > max_b_day:
                        break
                    op_k, lo_k = float(b_k["open"]), float(b_k["low"])
                    if op_k <= entry_high:
                        fill_px = op_k
                    elif lo_k <= entry_high:
                        fill_px = entry_high
                    else:
                        continue
                    stop_px = max(0.01, fill_px - 2.20 * atr_pit)
                    tp_px = fill_px + 0.85 * atr_pit
                    cand = label_triple_barrier_event(
                        pit_s,
                        entry_idx=k_idx,
                        tp_price=tp_px,
                        sl_price=stop_px,
                        horizon_bars=15,
                        market=market,
                        asset_type="equity",
                        symbol=sym,
                        trailing_stop=False,
                        entry_price_override=fill_px,
                    )
                    if cand.get("tradable"):
                        lim_fill = True
                        lim_res = cand
                    break

                if not res_open.get("tradable"):
                    continue
                signals.append({
                    "symbol": sym,
                    "market": market,
                    "day": day,
                    "subtype": subtype,
                    "open_net": float(res_open["net_return"]),
                    "open_outcome": res_open["outcome"],
                    "lim_fill": lim_fill,
                    "lim_net": float(lim_res["net_return"]) if lim_fill else None,
                    "lim_outcome": lim_res.get("outcome") if lim_fill else "NO_FILL_5D",
                })
    return signals


def summarize(signals: list[dict], split_name: str, lo: str, hi: str) -> None:
    sub = [r for r in signals if lo <= r["day"] <= hi]
    eps = _non_overlapping_episodes(sub)
    fills = [r for r in sub if r["lim_fill"]]
    fill_eps = _non_overlapping_episodes(fills)
    if not eps or not fill_eps:
        print(f"  [{split_name}] empty")
        return
    open_win = sum(1 for r in eps if r["open_net"] > 0)
    lim_win = sum(1 for r in fill_eps if float(r["lim_net"]) > 0)
    lim_all_win = sum(1 for r in fills if float(r["lim_net"]) > 0)
    lim_tp = sum(1 for r in fill_eps if r["lim_outcome"] == "TP_BARRIER")
    lim_sl = sum(1 for r in fill_eps if str(r["lim_outcome"]).startswith("SL_BARRIER"))
    lim_mean = sum(float(r["lim_net"]) for r in fill_eps) / len(fill_eps) * 100
    cl_ci = _cluster_bootstrap(fills, lambda r: float(r["lim_net"]) > 0)
    print(
        f"  [{split_name}] raw_n={len(sub):5d} fills={len(fills):5d} (fill_rate={len(fills)/len(sub)*100:5.1f}%) | "
        f"all_fill_win={lim_all_win/len(fills)*100:5.2f}% clCI={cl_ci} | "
        f"INDEPENDENT EPISODES ep={len(fill_eps):4d}: "
        f"lim5_net>0={lim_win/len(fill_eps)*100:5.2f}% epCI={wilson(lim_win, len(fill_eps))} "
        f"TP/(TP+SL)={lim_tp/max(1,lim_tp+lim_sl)*100:5.2f}% mean={lim_mean:+5.2f}% "
        f"(open_ep_win={open_win/len(eps)*100:5.2f}%)"
    )


def main() -> None:
    dev_lo, dev_hi = SPLITS["dev"]
    val_lo, val_hi = SPLITS["val"]
    for m in ("CN", "HK", "US"):
        sigs = scan_full_pit(m, val_hi)
        print(f"\n================ {m} FULL 1500-STOCK/DAY PIT (DEV + VAL, total_sigs={len(sigs)}) ================")
        summarize(sigs, "DEV 2021..2024", dev_lo, dev_hi)
        summarize(sigs, "VAL 2025H1    ", val_lo, val_hi)


if __name__ == "__main__":
    main()
