#!/usr/bin/env python3
"""Long-horizon cross-sectional backtest over cached real bars.

Per stock-day: build a point-in-time bundle (target + benchmark only up to t),
call the live `analyze_bundle` pipeline, then label the resulting long plan on
future bars. Outputs every long signal with its outcome, plus aggregate action
counts. Non-signal stock-days are aggregated, not stored.

No synthetic data, no parameter learning inside the replay.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from bisect import bisect_left, bisect_right
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from common import (
    ART,
    BENCHMARKS,
    LONG_ACTIONS,
    PIT_UNIVERSE,
    VIX_SYMBOL,
    adjusted_bars,
    clean_bars,
    load_bars,
    load_cache,
)

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from analysis import analyze_bundle  # noqa: E402
from evaluation import label_triple_barrier_event  # noqa: E402
from market_rules import is_st_instrument, resolve_instrument, rescale_bars_as_of  # noqa: E402

WARMUP_BARS = 420
WINDOW_BARS = 760
FUTURE_PAD = 10

_W: dict = {}


def _bench_days_in(bench_days: list[str], lo: str, hi: str) -> list[str]:
    a = bisect_left(bench_days, lo)
    b = bisect_right(bench_days, hi)
    return bench_days[a:b]


def make_pit_slice(bars: list[dict], t: int, horizon: int) -> tuple[list[dict], int]:
    """Return (PIT-rescaled slice, index of t inside it).

    Only bars through t may feed the decision; later bars are included solely for
    outcome labeling and are rescaled with the same as-of-t factor.
    """
    lo = max(0, t - WINDOW_BARS + 1)
    hi = min(len(bars), t + horizon + FUTURE_PAD)
    return rescale_bars_as_of(bars[lo:hi], t - lo), t - lo


def _init_worker(market: str, horizon: int, pit_path: str, out_dir: str, start: str, end: str) -> None:
    pit = json.loads(Path(pit_path).read_text(encoding="utf-8"))["markets"][market]
    symbol_periods: dict[str, list[dict]] = {}
    for p in pit["periods"]:
        for s in p["symbols"]:
            symbol_periods.setdefault(s, []).append({"start_day": p["start_day"], "end_day": p["end_day"]})
    bench_payload = load_cache(market, BENCHMARKS[market]["symbol"])
    bench = clean_bars(bench_payload)
    bench_source = bench_payload.get("source", f"CACHED_{market}_BENCHMARK")
    vix = {}
    if market == "US":
        try:
            vix = {b["day"]: float(b["close"]) for b in load_bars("US", VIX_SYMBOL)}
        except Exception:  # noqa: BLE001 - absence is disclosed, never backfilled
            vix = {}
    _W.update(
        market=market,
        horizon=horizon,
        out_dir=Path(out_dir),
        start=start,
        end=end,
        symbol_periods=symbol_periods,
        bench=bench,
        bench_source=bench_source,
        bench_days=[b["day"] for b in bench],
        vix=vix,
        bench_inst=resolve_instrument(
            BENCHMARKS[market]["symbol"], market=market, asset_type=BENCHMARKS[market]["asset_type"],
            name=BENCHMARKS[market]["name"],
        ),
    )


def _analyze_stock_day(symbol: str, payload: dict, bars: list[dict], idx: dict[str, int], days: list[str]) -> dict | None:
    market = _W["market"]
    horizon = _W["horizon"]
    n = len(bars)
    sym_name = str(payload.get("name") or "")
    sym_is_st = is_st_instrument(symbol, name=sym_name)
    inst = resolve_instrument(symbol, market=market, asset_type="equity", name=sym_name, is_st=sym_is_st)
    request = {
        "symbol": symbol,
        "market": market,
        "asset_type": "equity",
        "name": sym_name,
        "is_st": sym_is_st,
        "display_bars": 120,
        "horizon_bars": horizon,
        "account_equity": None,
        "adjustment": "total_return",
    }
    for day in days:
        t = idx[day]
        if t < WARMUP_BARS - 1 or t + horizon + 1 >= n:
            continue
        pit, cut = make_pit_slice(bars, t, horizon)
        b_hi = bisect_right(_W["bench_days"], day)
        bench_slice = _W["bench"][max(0, b_hi - WINDOW_BARS):b_hi]
        vix_value = _W["vix"].get(day)
        bundle = {
            "schema_version": "5.3.0",
            "market": market,
            "target": {"instrument": inst, "bars": pit[: cut + 1], "intraday": None},
            "benchmark": {
                "instrument": _W["bench_inst"],
                "bars": bench_slice,
                "source": _W["bench_source"],
                "errors": [],
            },
            "extra_context": {
                "vix": vix_value,
                "vix_source": f"CACHE(^VIX,{day})" if vix_value is not None else "CACHE_MISS",
                "earnings_date": None,
                "earnings_date_source": "NOT_AVAILABLE",
                "is_st": sym_is_st,
                "is_ipo_no_limit": False,
            },
            "provenance": {
                "source": payload.get("source", f"CACHED_{market}_DAILY"),
                "as_of": day,
                "adjustment": "total_return",
                "adjustment_status": {"requested": "total_return", "applied": "total_return",
                                      "events_used": len(payload.get("events") or []),
                                      "source": payload.get("events_source")},
                "errors": [],
                "intraday_excluded_day": None,
                "instrument_meta": {},
            },
        }
        rep = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)
        ml = rep["meta_labeling_decision"]
        action = ml["action"]
        agg = {"action": action, "veto": ", ".join(sorted(ml.get("veto_codes") or [])) or "-"}
        base = {
            "symbol": symbol, "market": market, "day": day, "action": action,
            "setup_type": ml.get("setup_type"), "veto": bool(ml.get("risk_veto_applied")),
            "veto_codes": ml.get("veto_codes") or [], "agg": agg,
        }
        if action not in LONG_ACTIONS:
            yield {"__aggregate__": base}
            continue
        plan = rep.get("triple_barrier_plan") or {}
        try:
            entry_low, entry_high = float(plan["entry_zone"][0]), float(plan["entry_zone"][1])
            stop = float(plan["stop_loss_barrier"])
            tp1 = float(plan["take_profit_1"])
            rr = float(plan.get("risk_reward_ratio") or 0.0)
        except (KeyError, IndexError, TypeError, ValueError):
            yield {"__aggregate__": base}
            continue
        if not (stop < entry_low <= entry_high < tp1):
            yield {"__aggregate__": base}
            continue
        ratchet = plan.get("breakeven_ratchet") or {}
        res = label_triple_barrier_event(
            pit,
            entry_idx=cut + 1,
            tp_price=tp1,
            sl_price=stop,
            horizon_bars=horizon,
            market=market,
            asset_type="equity",
            symbol=symbol,
            is_st=sym_is_st,
            is_ipo_no_limit=False,
            trailing_stop=bool(ratchet.get("enabled", True)),
            trigger_r=float(ratchet.get("trigger_r", 0.90)),
            confirm_close_r=float(ratchet.get("confirm_close_r", 0.30)),
            lock_r=float(ratchet.get("lock_r", 0.08)),
            name=sym_name,
        )
        mbs = rep.get("market_benchmark") or {}
        hf = None
        for sc in rep.get("rule_scenarios", {}).get("scenarios") or []:
            if str(sc.get("direction") or "BULLISH") == "BULLISH" and sc.get("precondition_met", True):
                hf = sc.get("historical_frequency") or {}
                break
        entry_open = float(pit[cut + 1]["open"])
        tradable = bool(res.get("tradable"))
        next_bench_day = _W["bench_days"][b_hi] if b_hi < len(_W["bench_days"]) else None
        is_stale_suspend = bool(next_bench_day and pit[cut + 1]["day"] > next_bench_day)
        filled = tradable and not is_stale_suspend and entry_low <= entry_open <= entry_high and entry_open > stop
        outcome = "STALE_SIGNAL" if is_stale_suspend else (res.get("outcome") or res.get("reason") or "UNKNOWN")

        # 5-session limit-order simulation at entry_high (resting inside entry_zone)
        lim_fill = False
        lim_reason = "NO_FILL_5D"
        lim_res: dict = {}
        max_wait = min(len(pit) - 1, cut + 5)
        max_bench_day = _W["bench_days"][min(len(_W["bench_days"]) - 1, b_hi + 4)] if b_hi < len(_W["bench_days"]) else "9999-12-31"
        for k_idx in range(cut + 1, max_wait + 1):
            b_k = pit[k_idx]
            if b_k["day"] > max_bench_day:
                break
            op_k, lo_k = float(b_k["open"]), float(b_k["low"])
            if op_k <= entry_high:
                fill_px = op_k
            elif lo_k <= entry_high:
                fill_px = entry_high
            else:
                continue
            if fill_px <= stop:
                lim_reason = "GAP_BELOW_STOP"
                break
            cand_res = label_triple_barrier_event(
                pit,
                entry_idx=k_idx,
                tp_price=tp1,
                sl_price=stop,
                horizon_bars=horizon,
                market=market,
                asset_type="equity",
                symbol=symbol,
                is_st=sym_is_st,
                is_ipo_no_limit=False,
                trailing_stop=bool(ratchet.get("enabled", True)),
                trigger_r=float(ratchet.get("trigger_r", 0.90)),
                confirm_close_r=float(ratchet.get("confirm_close_r", 0.30)),
                lock_r=float(ratchet.get("lock_r", 0.08)),
                name=sym_name,
                entry_price_override=fill_px,
            )
            if cand_res.get("tradable"):
                lim_fill = True
                lim_reason = "FILLED_LIMIT"
                lim_res = cand_res
            else:
                lim_reason = str(cand_res.get("reason") or "UNTRADABLE")
            break

        mtf = rep.get("multi_timeframe_score") or {}
        tfa = mtf.get("timeframes_aligned") or {}
        comps = mtf.get("components") or {}
        ind = rep.get("mathematical_indicators") or {}
        mom = ind.get("momentum") or {}
        trd = ind.get("trend") or {}
        vol = ind.get("volatility") or {}
        qf = ind.get("qlib_factors") or {}
        mreg = rep.get("market_regime") or {}
        rec = {
            **{k: base[k] for k in ("symbol", "market", "day", "action", "setup_type", "veto", "veto_codes")},
            "rule_score": float(mtf.get("rule_score") or 0.0),
            "components_total": float(mtf.get("components_total") or 0.0),
            "comp_macro": comps.get("macro_trend_25"),
            "comp_struct": comps.get("structure_pattern_25"),
            "comp_strat": comps.get("strategy_trigger_25"),
            "comp_vol": comps.get("volume_benchmark_25"),
            "weekly_trend": tfa.get("weekly_trend"),
            "daily_structure": tfa.get("daily_structure"),
            "scoring_regime": tfa.get("scoring_regime"),
            "active_bull_strategies": tfa.get("active_bull_strategies"),
            "active_bear_strategies": tfa.get("active_bear_strategies"),
            "weekly_60w_pos_pct": tfa.get("weekly_60w_position_pct"),
            "momentum_ic_15d": tfa.get("causal_momentum_ic_15d"),
            "target_regime": mreg.get("regime"),
            "target_regime_score": mreg.get("regime_score"),
            "rvol20": mom.get("rvol20"),
            "rsi14": mom.get("rsi14"),
            "adx14": trd.get("adx14"),
            "bbw_pct": vol.get("bbw_percentile_120"),
            "hv_ratio": qf.get("hv_ratio_20_60"),
            "regime_score": float(mbs.get("regime_score") or 0.0),
            "bench_regime": mbs.get("regime"),
            "relative_strength": mbs.get("relative_strength_vs_index"),
            "entry_zone": [entry_low, entry_high],
            "stop": stop,
            "tp1": tp1,
            "rr": rr,
            "hf_scenario_id": hf.get("scenario_id") if hf else None,
            "hf_target_freq": hf.get("target_freq") if hf else None,
            "hf_raw_freq": hf.get("raw_target_freq") if hf else None,
            "hf_sample_size": hf.get("sample_size") if hf else None,
            "hf_ci95": hf.get("target_ci_95") if hf else None,
            "quality": (rep.get("quality") or {}).get("status"),
            "entry_day": res.get("entry_day"),
            "entry_price": res.get("entry_price", entry_open),
            "exit_day": res.get("exit_day"),
            "exit_price": res.get("exit_price"),
            "fill": filled,
            "fill_reason": ("FILLED" if filled else ("STALE_SIGNAL" if is_stale_suspend else ("NO_FILL_OUTSIDE_ZONE" if tradable else outcome))),
            "outcome": outcome,
            "barrier_label": res.get("barrier_label"),
            "net_return": res.get("net_return"),
            "gross_return": res.get("gross_return"),
            "holding_bars": res.get("holding_bars"),
            "limit_n5_fill": lim_fill,
            "limit_n5_reason": lim_reason,
            "limit_n5_entry_day": lim_res.get("entry_day"),
            "limit_n5_entry_price": lim_res.get("entry_price"),
            "limit_n5_exit_day": lim_res.get("exit_day"),
            "limit_n5_outcome": lim_res.get("outcome"),
            "limit_n5_net_return": lim_res.get("net_return"),
            "limit_n5_holding_bars": lim_res.get("holding_bars"),
            "year": int(day[:4]),
        }
        yield rec


def _process_symbol(symbol: str) -> tuple[list[dict], dict]:
    market = _W["market"]
    payload = load_cache(market, symbol)
    _, bars = adjusted_bars(payload, mode="total_return")
    idx = {b["day"]: i for i, b in enumerate(bars)}
    records: list[dict] = []
    counts: dict[str, int] = {}
    for period in _W["symbol_periods"].get(symbol, []):
        if period["end_day"] < _W["start"] or period["start_day"] > _W["end"]:
            continue
        lo_day = max(period["start_day"], _W["start"])
        hi_day = min(period["end_day"], _W["end"])
        days = [d for d in _bench_days_in(_W["bench_days"], lo_day, hi_day) if d in idx]
        if not days:
            continue
        for rec in _analyze_stock_day(symbol, payload, bars, idx, days):
            if "__aggregate__" in rec:
                key = json.dumps(rec["__aggregate__"]["agg"], sort_keys=True, ensure_ascii=False)
                counts[key] = counts.get(key, 0) + 1
            else:
                records.append(rec)
    return records, counts


def _process_chunk(shard_id: int, symbols: list[str]) -> dict:
    out_dir: Path = _W["out_dir"]
    shard_dir = out_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    path = shard_dir / f"shard_{shard_id:04d}.jsonl"
    agg: dict[str, int] = {}
    n_long = 0
    n_decisions = 0
    errors = 0
    with path.open("w", encoding="utf-8") as fh:
        for symbol in symbols:
            try:
                records, counts = _process_symbol(symbol)
                for rec in records:
                    fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
                n_long += len(records)
                for k, v in counts.items():
                    agg[k] = agg.get(k, 0) + v
                n_decisions += sum(counts.values()) + len(records)
            except Exception as exc:  # noqa: BLE001 - one symbol must not kill the shard
                errors += 1
                fh.write(json.dumps({"__error__": True, "symbol": symbol,
                                     "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False) + "\n")
    summary = {"shard": shard_id, "symbols": len(symbols), "decisions": n_decisions,
               "long_signals": n_long, "errors": errors, "aggregate": agg,
               "finished_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (shard_dir / f"shard_{shard_id:04d}.done").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["CN", "HK", "US", "ALL"], default="ALL")
    ap.add_argument("--start", default="2021-01-04")
    ap.add_argument("--end", default="2026-10-16")
    ap.add_argument("--horizon", type=int, default=15)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--chunk-size", type=int, default=15)
    ap.add_argument("--run-name", default="baseline")
    ap.add_argument("--pit", type=Path, default=PIT_UNIVERSE)
    ap.add_argument("--out-root", type=Path, default=ART / "runs")
    args = ap.parse_args()

    markets = ["CN", "HK", "US"] if args.market == "ALL" else [args.market]
    pit = json.loads(args.pit.read_text(encoding="utf-8"))
    for market in markets:
        symbols = sorted({s for p in pit["markets"][market]["periods"] for s in p["symbols"]})
        if args.max_symbols:
            symbols = symbols[: args.max_symbols]
        out_dir = args.out_root / args.run_name / market
        shard_dir = out_dir / "shards"
        shard_dir.mkdir(parents=True, exist_ok=True)
        chunks = [symbols[i:i + args.chunk_size] for i in range(0, len(symbols), args.chunk_size)]
        todo = [(i, c) for i, c in enumerate(chunks) if not (shard_dir / f"shard_{i:04d}.done").exists()]
        print(f"[{market}] symbols={len(symbols)} chunks={len(chunks)} pending={len(todo)} workers={args.workers}", flush=True)
        if not todo:
            continue
        started = time.time()
        done = 0
        if args.workers <= 1:
            _init_worker(market, args.horizon, str(args.pit), str(out_dir), args.start, args.end)
            summaries = [_process_chunk(sid, chunk) for sid, chunk in todo]
        else:
            with ProcessPoolExecutor(
                max_workers=args.workers,
                initializer=_init_worker,
                initargs=(market, args.horizon, str(args.pit), str(out_dir), args.start, args.end),
            ) as pool:
                futures = {pool.submit(_process_chunk, sid, chunk): sid for sid, chunk in todo}
                summaries = [f.result() for f in as_completed(futures)]
        for s in summaries:
            done += 1
            if done % 5 == 0 or done == len(todo):
                elapsed = time.time() - started
                print(f"[{market}] {done}/{len(todo)} chunks elapsed={elapsed:.0f}s "
                      f"last_symbols={s['symbols']} last_decisions={s['decisions']} "
                      f"long={s['long_signals']} errors={s['errors']}", flush=True)
        print(f"[{market}] finished in {time.time()-started:.0f}s -> {out_dir}", flush=True)


if __name__ == "__main__":
    main()