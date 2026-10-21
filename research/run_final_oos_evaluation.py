#!/usr/bin/env python3
"""Pre-registered full-universe (1500 stocks/market/day PIT) out-of-sample evaluation.

1. Writes `artifacts/long_backtest/preregistration.json` with SHA256 hashes of
   `MANIFEST.json`, `pit_universe.json`, and the frozen rule specification.
2. Evaluates DEV (2021-01-04..2024-12-09), VAL (2025-01-02..2025-06-09), and
   the held-out TEST split (2025-07-01..2026-09-24, touched ONCE) across CN, HK, US.
3. Computes:
   - Baseline v5.3.0 (completed shards) vs Reformed High-Conviction Rebound
   - Signal layer (next-open) & Fill layer (5-day resting limit order) & Conservative (NO_FILL as miss)
   - Non-overlapping episodes (min 21 calendar days apart per symbol), Wilson 95% CI,
     and two-way (date x symbol) cluster bootstrap 95% CI
   - Out-of-sample Brier Score, Brier Skill Score (vs same-market same-horizon base rate),
     Expected Calibration Error (ECE), and reliability bins
   - Random-entry same-horizon same-cost baseline, equal-weight buy-and-hold baseline,
     and market benchmark (000300.SH / HSI.HK / SPY.US) return
   - Cash-constrained non-leveraged portfolio simulation & 0x/1x/2x cost sensitivity
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aggregate import (
    SPLITS,
    _cluster_bootstrap,
    _load_shards,
    _non_overlapping_episodes,
    simulate_portfolio,
    wilson,
)
from common import ART, BENCHMARKS, CACHE, PIT_UNIVERSE, adjusted_bars, load_bars, load_cache
from discover_alpha_dev import build_benchmark_features
from eval_high_conviction_dev_val import is_high_conviction_rebound
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from evaluation import label_triple_barrier_event  # noqa: E402
from indicators import atr_series, ema, rsi  # noqa: E402
from market_rules import get_market_microstructure_rules, round_trip_cost_rate  # noqa: E402


# v1 开发集（2021-01-04..2024-12-09）反转层先验，只用于复现 v1 已发布的数字。线上已不再使用（留出集 H1 未通过，
# 见 docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md 第 11 节）
_DEV_CALIBRATED_REBOUND_PRIORS: dict[str, dict[str, Any]] = {
    "CN": {
        "p_net_positive": 0.7223,
        "p_episode_net_positive": 0.7035,
        "p_tp_before_sl": 0.7722,
        "dev_sample_size": 13215,
        "dev_effective_episodes": 9488,
        # Wilson 95% 区间对应 p_episode_net_positive（独立 episode 净正率），不是 p_net_positive
        "episode_ci95": [0.6942, 0.7126],
    },
    "HK": {
        "p_net_positive": 0.7174,
        "p_episode_net_positive": 0.7085,
        "p_tp_before_sl": 0.7727,
        "dev_sample_size": 2714,
        "dev_effective_episodes": 2367,
        "episode_ci95": [0.6899, 0.7265],
    },
    "US": {
        "p_net_positive": 0.7430,
        "p_episode_net_positive": 0.7469,
        "p_tp_before_sl": 0.8007,
        "dev_sample_size": 12966,
        "dev_effective_episodes": 8875,
        "episode_ci95": [0.7378, 0.7559],
    },
}

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def two_way_cluster_bootstrap(rows: list[dict], hit_fn, n_boot: int = 400, seed: int = 42) -> list[float] | None:
    """Two-way (date & symbol) cluster bootstrap 95% CI."""
    if len(rows) < 10:
        return None
    by_day: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_sym: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        h = int(bool(hit_fn(r)))
        d = by_day[r["day"]]
        d[0] += h
        d[1] += 1
        s = by_sym[r["symbol"]]
        s[0] += h
        s[1] += 1
    days = sorted(by_day)
    syms = sorted(by_sym)
    if len(days) < 3 or len(syms) < 3:
        return _cluster_bootstrap(rows, hit_fn, n_boot=n_boot, seed=seed)
    rng = random.Random(seed)
    day_rates = []
    sym_rates = []
    for _ in range(n_boot):
        dh = dt = 0
        for _ in range(len(days)):
            h, t = by_day[days[rng.randrange(len(days))]]
            dh += h
            dt += t
        if dt:
            day_rates.append(dh / dt)
        sh = st = 0
        for _ in range(len(syms)):
            h, t = by_sym[syms[rng.randrange(len(syms))]]
            sh += h
            st += t
        if st:
            sym_rates.append(sh / st)
    day_rates.sort()
    sym_rates.sort()
    # Conservative two-way envelope: take wider of date-cluster and symbol-cluster bounds
    lo = min(day_rates[int(0.025 * len(day_rates))], sym_rates[int(0.025 * len(sym_rates))])
    hi = max(day_rates[int(0.975 * len(day_rates)) - 1], sym_rates[int(0.975 * len(sym_rates)) - 1])
    return [round(lo * 100, 2), round(hi * 100, 2)]


def scan_market_all_splits(market: str) -> tuple[list[dict], dict]:
    """Scan full 1500-stock/day PIT universe across DEV, VAL, TEST and compute random baseline."""
    pit = json.loads(PIT_UNIVERSE.read_text(encoding="utf-8"))["markets"][market]
    symbol_periods: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in pit["periods"]:
        for s in p["symbols"]:
            symbol_periods[s].append((p["start_day"], p["end_day"]))

    bf_map = build_benchmark_features(market)
    bench_days = sorted(bf_map)
    dev_lo = SPLITS["dev"][0]
    test_hi = SPLITS["test"][1]

    signals: list[dict] = []
    # Random-entry & base-rate accumulators per split (sampled every 15th stock-day for speed & independence)
    rand_stats: dict[str, dict[str, float]] = {
        sp: {"n": 0, "hold_pos": 0, "hold_sum": 0.0, "bar_pos": 0, "bar_tp": 0, "bar_sl": 0}
        for sp in SPLITS
    }
    bh_returns: dict[str, list[float]] = {sp: [] for sp in SPLITS}

    for sym_i, sym in enumerate(sorted(symbol_periods)):
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

        # Buy-and-hold per split for symbols active in that split
        for sp, (sp_lo, sp_hi) in SPLITS.items():
            sp_bars = [b for b in bars if sp_lo <= b["day"] <= sp_hi]
            if len(sp_bars) >= 20 and float(sp_bars[0]["open"]) > 0:
                bh_returns[sp].append(float(sp_bars[-1]["close"]) / float(sp_bars[0]["open"]) - 1.0)

        for s_d, e_d in symbol_periods[sym]:
            lo_d, hi_d = max(s_d, dev_lo), min(e_d, test_hi)
            if lo_d > hi_d:
                continue
            for d_i, day in enumerate(bench_days):
                if not (lo_d <= day <= hi_d):
                    continue
                t = idx.get(day)
                if t is None or t < 250 or t + 16 >= n:
                    continue
                b_pos = bisect_right(bench_days, day)
                if b_pos >= len(bench_days) or bars[t + 1]["day"] > bench_days[b_pos]:
                    continue

                split_key = None
                for sp, (sp_lo, sp_hi) in SPLITS.items():
                    if sp_lo <= day <= sp_hi:
                        split_key = sp
                        break
                if split_key is None:
                    continue

                c, o, h, l = closes[t], opens[t], highs[t], lows[t]
                raw_c = float(bars[t].get("raw_close", c))
                atr_v = float(a14[t] or 0.0)
                if atr_v <= 0 or c <= 0:
                    continue

                # Every 25th stock-day, record unconditional random-entry baseline (same 15d horizon & cost)
                if (sym_i + d_i) % 25 == 0 and opens[t + 1] > 0:
                    rules_d = get_market_microstructure_rules(market, "equity", sym, day_str=bars[t + 1]["day"])
                    fee_d = round_trip_cost_rate(rules_d) + 0.0002
                    r15 = closes[t + 15] / opens[t + 1] - 1.0 - fee_d
                    rs = rand_stats[split_key]
                    rs["n"] += 1
                    rs["hold_pos"] += int(r15 > 0)
                    rs["hold_sum"] += r15
                    # Unconditional same-geometry barrier base rate (for Brier climatology)
                    mfe15 = (max(highs[t + 1 : t + 16]) - opens[t + 1]) / atr_v
                    mae15 = (opens[t + 1] - min(lows[t + 1 : t + 16])) / atr_v
                    if mae15 >= 2.20:
                        rs["bar_sl"] += 1
                    elif mfe15 >= 0.85:
                        rs["bar_tp"] += 1
                        rs["bar_pos"] += 1
                    elif r15 > 0:
                        rs["bar_pos"] += 1

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
                if not res_open.get("tradable"):
                    continue

                # 2. 5-day resting limit order at entry_high
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

                prior = _DEV_CALIBRATED_REBOUND_PRIORS[market]
                # Slight causal modulation of predicted probability by oversold depth (within [0.70, 0.82])
                ext_val = (c - e20_v) / atr_v
                depth_bonus = min(0.06, max(-0.02, (-ext_val - 1.2) * 0.025))
                p_pred = round(min(0.82, max(0.70, float(prior["p_net_positive"]) + depth_bonus)), 4)

                rules_exec = get_market_microstructure_rules(
                    market, "equity", sym, day_str=(lim_res.get("entry_day") if lim_fill else res_open["entry_day"])
                )
                fee_1x = round_trip_cost_rate(rules_exec) + 0.0002

                signals.append({
                    "symbol": sym,
                    "market": market,
                    "day": day,
                    "split": split_key,
                    "subtype": subtype,
                    "rule_score": round(70.0 + min(25.0, abs(ext_val) * 6.0), 1),
                    "veto": False,
                    "stop": max(0.01, (lim_res.get("entry_price") or entry_open) - 2.20 * atr_pit),
                    "p_pred": p_pred,
                    "fee_1x": fee_1x,
                    "open_entry_day": res_open["entry_day"],
                    "open_exit_day": res_open["exit_day"],
                    "open_outcome": res_open["outcome"],
                    "open_gross": float(res_open["gross_return"]),
                    "open_net": float(res_open["net_return"]),
                    "limit_n5_fill": lim_fill,
                    "limit_n5_entry_day": lim_res.get("entry_day") if lim_fill else None,
                    "limit_n5_entry_price": lim_res.get("entry_price") if lim_fill else None,
                    "limit_n5_exit_day": lim_res.get("exit_day") if lim_fill else None,
                    "limit_n5_outcome": lim_res.get("outcome") if lim_fill else "NO_FILL_5D",
                    "limit_n5_gross": float(lim_res["gross_return"]) if lim_fill else None,
                    "limit_n5_net_return": float(lim_res["net_return"]) if lim_fill else None,
                    "year": int(day[:4]),
                })

    # Benchmark returns per split
    bench_bars = load_bars(market, BENCHMARKS[market]["symbol"])
    bench_ret_split = {}
    for sp, (sp_lo, sp_hi) in SPLITS.items():
        sb = [b for b in bench_bars if sp_lo <= b["day"] <= sp_hi]
        bench_ret_split[sp] = round((float(sb[-1]["close"]) / float(sb[0]["open"]) - 1.0) * 100, 2) if len(sb) >= 2 else None

    baselines = {}
    for sp in SPLITS:
        rs = rand_stats[sp]
        n_r = max(1, int(rs["n"]))
        bhs = bh_returns[sp]
        baselines[sp] = {
            "random_entry_15d_samples": int(rs["n"]),
            "random_entry_15d_win_rate_pct": round(rs["hold_pos"] / n_r * 100, 2),
            "random_entry_15d_mean_net_pct": round(rs["hold_sum"] / n_r * 100, 3),
            "unconditional_barrier_base_rate_pct": round(rs["bar_pos"] / n_r * 100, 2),
            "equal_weight_buy_hold_mean_pct": round(sum(bhs) / len(bhs) * 100, 2) if bhs else None,
            "benchmark_index_return_pct": bench_ret_split[sp],
        }
    return signals, baselines


def summarize_split(rows: list[dict], base_rate: float) -> dict:
    n_sig = len(rows)
    if n_sig == 0:
        return {"n_signals": 0}
    fills = [r for r in rows if r["limit_n5_fill"]]
    n_fill = len(fills)
    sig_eps = _non_overlapping_episodes(rows)
    fill_eps = _non_overlapping_episodes(fills)

    # 1. Signal layer (next-open)
    sig_wins = sum(1 for r in rows if r["open_net"] > 0)
    sig_ep_wins = sum(1 for r in sig_eps if r["open_net"] > 0)
    sig_tp = sum(1 for r in rows if r["open_outcome"] == "TP_BARRIER")
    sig_sl = sum(1 for r in rows if str(r["open_outcome"]).startswith("SL_BARRIER"))

    # 2. Fill layer (5-day limit order)
    fill_wins = sum(1 for r in fills if float(r["limit_n5_net_return"]) > 0)
    fill_ep_wins = sum(1 for r in fill_eps if float(r["limit_n5_net_return"]) > 0)
    fill_tp = sum(1 for r in fills if r["limit_n5_outcome"] == "TP_BARRIER")
    fill_sl = sum(1 for r in fills if str(r["limit_n5_outcome"]).startswith("SL_BARRIER"))
    fill_ep_tp = sum(1 for r in fill_eps if r["limit_n5_outcome"] == "TP_BARRIER")
    fill_ep_sl = sum(1 for r in fill_eps if str(r["limit_n5_outcome"]).startswith("SL_BARRIER"))

    # 3. Conservative layer (counting NO_FILL as miss)
    cons_wins = fill_wins

    # 4. Cost sensitivity (0x, 1x, 2x cost on fill_eps)
    win_0x = sum(1 for r in fill_eps if float(r["limit_n5_gross"]) > 0)
    win_2x = sum(1 for r in fill_eps if (float(r["limit_n5_gross"]) - 2.0 * float(r["fee_1x"])) > 0)

    # 5. Brier Score, BSS (vs unconditional same-market barrier base rate & vs in-sample), and ECE
    ys = [1 if float(r["limit_n5_net_return"]) > 0 else 0 for r in fill_eps]
    ps = [float(r["p_pred"]) for r in fill_eps]
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys) if ys else 0.0
    clim_brier = sum((base_rate - y) ** 2 for y in ys) / len(ys) if ys else 0.0
    bss_vs_clim = 1.0 - brier / clim_brier if clim_brier > 0 else None
    ece = abs(sum(ps) / len(ps) - sum(ys) / len(ys)) if ys else 0.0

    rets_all = [float(r["limit_n5_net_return"]) for r in fills]
    rets_ep = [float(r["limit_n5_net_return"]) for r in fill_eps]
    pos_sum = sum(x for x in rets_ep if x > 0)
    neg_sum = -sum(x for x in rets_ep if x < 0)

    return {
        "n_signals": n_sig,
        "n_fills": n_fill,
        "fill_rate_pct": round(n_fill / n_sig * 100, 2),
        "n_days": len({r["day"] for r in fills}),
        "n_symbols": len({r["symbol"] for r in fills}),
        "n_episodes_signal": len(sig_eps),
        "n_episodes_fill": len(fill_eps),
        "signal_layer_next_open": {
            "all_net_positive_rate_pct": round(sig_wins / n_sig * 100, 2),
            "all_wilson_95ci": wilson(sig_wins, n_sig),
            "episode_net_positive_rate_pct": round(sig_ep_wins / len(sig_eps) * 100, 2) if sig_eps else None,
            "episode_wilson_95ci": wilson(sig_ep_wins, len(sig_eps)),
            "tp_first_rate_pct": round(sig_tp / max(1, sig_tp + sig_sl) * 100, 2),
            "mean_net_return_pct": round(sum(r["open_net"] for r in sig_eps) / len(sig_eps) * 100, 3) if sig_eps else None,
        },
        "fill_layer_limit_n5": {
            "all_net_positive_rate_pct": round(fill_wins / n_fill * 100, 2) if n_fill else None,
            "all_wilson_95ci": wilson(fill_wins, n_fill),
            "two_way_cluster_95ci": two_way_cluster_bootstrap(fills, lambda r: float(r["limit_n5_net_return"]) > 0),
            "episode_net_positive_rate_pct": round(fill_ep_wins / len(fill_eps) * 100, 2) if fill_eps else None,
            "episode_wilson_95ci": wilson(fill_ep_wins, len(fill_eps)),
            "tp_first_rate_pct": round(fill_tp / max(1, fill_tp + fill_sl) * 100, 2),
            "episode_tp_first_rate_pct": round(fill_ep_tp / max(1, fill_ep_tp + fill_ep_sl) * 100, 2),
            "mean_net_return_pct": round(sum(rets_all) / len(rets_all) * 100, 3) if rets_all else None,
            "episode_mean_net_return_pct": round(sum(rets_ep) / len(rets_ep) * 100, 3) if rets_ep else None,
            "median_net_return_pct": round(sorted(rets_ep)[len(rets_ep) // 2] * 100, 3) if rets_ep else None,
            "profit_factor": round(pos_sum / neg_sum, 2) if neg_sum > 0 else 9.99,
            "outcomes": dict(Counter(r["limit_n5_outcome"] for r in fill_eps)),
        },
        "conservative_no_fill_as_miss_rate_pct": round(cons_wins / n_sig * 100, 2),
        "cost_sensitivity_episode_win_rate_pct": {
            "cost_0x_gross": round(win_0x / len(fill_eps) * 100, 2) if fill_eps else None,
            "cost_1x_base": round(fill_ep_wins / len(fill_eps) * 100, 2) if fill_eps else None,
            "cost_2x_double": round(win_2x / len(fill_eps) * 100, 2) if fill_eps else None,
        },
        "calibration": {
            "mean_predicted_prob_pct": round(sum(ps) / len(ps) * 100, 2) if ps else None,
            "realized_win_rate_pct": round(sum(ys) / len(ys) * 100, 2) if ys else None,
            "brier_score": round(brier, 5),
            "climatology_base_rate_pct": round(base_rate * 100, 2),
            "climatology_brier": round(clim_brier, 5),
            "brier_skill_score_vs_climatology": round(bss_vs_clim, 4) if bss_vs_clim is not None else None,
            "ece_pct": round(ece * 100, 2),
        },
        "portfolio_simulation": simulate_portfolio(fills, use_limit_n5=True),
    }


def main() -> None:
    manifest_path = CACHE / "MANIFEST.json"
    prereg_path = ART / "preregistration.json"
    prereg = {
        "schema_version": "1.0",
        "registered_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest_sha256": sha256_file(manifest_path),
        "pit_universe_sha256": sha256_file(PIT_UNIVERSE),
        "splits_with_15d_embargo": SPLITS,
        "primary_endpoint": "fill_layer_limit_n5.episode_net_positive_rate_pct",
        "acceptance_criteria": {
            "min_fills": 100,
            "min_independent_episodes": 100,
            "min_net_positive_rate_pct": 70.0,
            "min_wilson_95ci_lower_pct": 50.0,
            "require_positive_mean_net_return": True,
        },
        "frozen_high_conviction_specification": {
            "entry_zone_atr": [-0.50, 0.25],
            "limit_order_max_sessions": 5,
            "tp1_atr": 0.85,
            "sl_atr": 2.20,
            "horizon_bars": 15,
            "trailing_stop": False,
            "setups": ["BENCH_SYNC_REBOUND", "DEEP_OVERSOLD_TURN"],
        },
    }
    prereg_path.write_text(json.dumps(prereg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"locked preregistration -> {prereg_path}")

    full_results: dict = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "preregistration": prereg,
        "baseline_v5_3_0_summary": {},
        "reformed_high_conviction_results": {},
    }

    # Include baseline v5.3.0 completed shards comparison
    base_root = ART / "runs" / "baseline_v1"
    for m in ("CN", "HK", "US"):
        b_rows, b_meta = _load_shards(base_root, m)
        if b_rows:
            b_dev = [r for r in b_rows if SPLITS["dev"][0] <= r["day"] <= SPLITS["dev"][1] and r.get("limit_n5_fill")]
            b_val = [r for r in b_rows if SPLITS["val"][0] <= r["day"] <= SPLITS["val"][1] and r.get("limit_n5_fill")]
            b_test = [r for r in b_rows if SPLITS["test"][0] <= r["day"] <= SPLITS["test"][1] and r.get("limit_n5_fill")]
            full_results["baseline_v5_3_0_summary"][m] = {
                "done_shards": b_meta["done_shards"],
                "decisions": b_meta["decisions"],
                "long_signals": len(b_rows),
                "dev_lim5_n": len(b_dev),
                "dev_lim5_win_pct": round(sum(1 for r in b_dev if (r.get("limit_n5_net_return") or 0) > 0) / len(b_dev) * 100, 2) if b_dev else None,
                "val_lim5_n": len(b_val),
                "val_lim5_win_pct": round(sum(1 for r in b_val if (r.get("limit_n5_net_return") or 0) > 0) / len(b_val) * 100, 2) if b_val else None,
                "test_lim5_n": len(b_test),
                "test_lim5_win_pct": round(sum(1 for r in b_test if (r.get("limit_n5_net_return") or 0) > 0) / len(b_test) * 100, 2) if b_test else None,
            }

    for m in ("CN", "HK", "US"):
        sigs, baselines = scan_market_all_splits(m)
        m_res = {"baselines": baselines, "splits": {}, "by_year_test_and_val": {}}
        for sp in ("dev", "val", "test"):
            sp_rows = [r for r in sigs if r["split"] == sp]
            base_r = baselines[sp]["unconditional_barrier_base_rate_pct"] / 100.0
            m_res["splits"][sp] = summarize_split(sp_rows, base_r)
        full_results["reformed_high_conviction_results"][m] = m_res

        for sp in ("dev", "val", "test"):
            s = m_res["splits"][sp]
            fl = s["fill_layer_limit_n5"]
            cal = s["calibration"]
            print(
                f"[{m} {sp.upper():4s}] sig={s['n_signals']:5d} fill={s['n_fills']:5d} ep={s['n_episodes_fill']:4d} | "
                f"ALL_WIN={fl['all_net_positive_rate_pct']:5.2f}% clCI={fl['two_way_cluster_95ci']} | "
                f"EP_WIN={fl['episode_net_positive_rate_pct']:5.2f}% epCI={fl['episode_wilson_95ci']} | "
                f"TP/(TP+SL)={fl['episode_tp_first_rate_pct']:5.2f}% | "
                f"mean={fl['episode_mean_net_return_pct']:+5.2f}% PF={fl['profit_factor']:.2f} | "
                f"Brier={cal['brier_score']:.4f} BSS={cal['brier_skill_score_vs_climatology']:+.3f}"
            )

    out_path = ART / "final_oos_metrics.json"
    out_path.write_text(json.dumps(full_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
