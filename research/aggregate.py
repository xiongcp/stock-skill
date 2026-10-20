#!/usr/bin/env python3
"""Aggregate long-signal records into hit rates, non-overlapping episode stats,
calibration diagnostics, and cash-constrained portfolio metrics.

Three execution layers are reported separately:
- signal_level: every long signal, next-open TP1-vs-SL / net return
- fill_level: signals whose next-session open traded inside entry_zone
- limit_n5_level: 5-session resting limit order at entry_high inside entry_zone
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

# 15-trading-day embargo (~22 calendar days) between splits so no dev/val label
# matures inside the next split.
SPLITS = {
    "dev": ("2021-01-01", "2024-12-09"),
    "val": ("2025-01-02", "2025-06-09"),
    "test": ("2025-07-01", "2026-09-24"),
}


def wilson(wins: int, n: int, z: float = 1.96) -> list[float] | None:
    if n < 2:
        return None
    p = wins / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, center - half) * 100, 2), round(min(1.0, center + half) * 100, 2)]


def _load_shards(run_root: Path, market: str) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    agg: Counter = Counter()
    decisions = 0
    errors = 0
    shard_dir = run_root / market / "shards"
    if not shard_dir.exists():
        return rows, {"decisions": 0, "actions": {}, "errors": 0, "done_shards": 0}
    done_files = sorted(shard_dir.glob("shard_*.done"))
    for done_p in done_files:
        d = json.loads(done_p.read_text(encoding="utf-8"))
        decisions += d.get("decisions", 0)
        for k, v in (d.get("aggregate") or {}).items():
            agg[k] += v
        jsonl_p = done_p.with_suffix(".jsonl")
        if not jsonl_p.exists():
            continue
        for line in jsonl_p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("__error__"):
                errors += 1
                continue
            rows.append(r)
    return rows, {"decisions": decisions, "actions": dict(agg), "errors": errors, "done_shards": len(done_files)}


def _cluster_bootstrap(rows: list[dict], hit_fn, n_boot: int = 400, seed: int = 42) -> list[float] | None:
    day_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        s = day_stats[r["day"]]
        s[0] += int(bool(hit_fn(r)))
        s[1] += 1
    days = sorted(day_stats)
    if len(days) < 3:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(n_boot):
        hits = total = 0
        for _ in range(len(days)):
            h, t = day_stats[days[rng.randrange(len(days))]]
            hits += h
            total += t
        if total:
            rates.append(hits / total)
    if not rates:
        return None
    rates.sort()
    return [round(rates[int(0.025 * len(rates))] * 100, 2), round(rates[int(0.975 * len(rates)) - 1] * 100, 2)]


def _non_overlapping_episodes(rows: list[dict], min_gap_days: int = 21) -> list[dict]:
    """Greedy per-symbol non-overlapping episodes (at least min_gap_days apart)."""
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sym[r["symbol"]].append(r)
    episodes: list[dict] = []
    for sym, sym_rows in by_sym.items():
        sym_rows.sort(key=lambda x: x["day"])
        last_dt: date | None = None
        for r in sym_rows:
            dt = date.fromisoformat(r["day"][:10])
            if last_dt is None or (dt - last_dt).days >= min_gap_days:
                episodes.append(r)
                last_dt = dt
    return episodes


def _rate_block(
    rows: list[dict],
    field: str = "net_return",
    outcome_field: str = "outcome",
    pred=None,
) -> dict:
    sub = [r for r in rows if (pred(r) if pred else True)]
    n = len(sub)
    tp = sum(1 for r in sub if r.get(outcome_field) == "TP_BARRIER")
    sl = sum(1 for r in sub if str(r.get(outcome_field, "")).startswith("SL_BARRIER"))
    other = n - tp - sl
    pos = sum(1 for r in sub if (r.get(field) or 0) > 0)
    rets = [float(r.get(field)) for r in sub if r.get(field) is not None]
    tp_rows = [
        r for r in sub
        if r.get(outcome_field) == "TP_BARRIER" or str(r.get(outcome_field, "")).startswith("SL_BARRIER")
    ]
    episodes = _non_overlapping_episodes(sub)
    ep_pos = sum(1 for r in episodes if (r.get(field) or 0) > 0)
    ep_tp = sum(1 for r in episodes if r.get(outcome_field) == "TP_BARRIER")
    ep_sl = sum(1 for r in episodes if str(r.get(outcome_field, "")).startswith("SL_BARRIER"))
    return {
        "n": n,
        "n_days": len({r["day"] for r in sub}),
        "n_symbols": len({r["symbol"] for r in sub}),
        "n_episodes": len(episodes),
        "tp_first": tp,
        "sl_first": sl,
        "other_exit": other,
        "tp_first_rate_pct": round(tp / (tp + sl) * 100, 2) if tp + sl else None,
        "tp_first_rate_95ci": wilson(tp, tp + sl),
        "tp_first_rate_cluster_95ci": _cluster_bootstrap(tp_rows, lambda r: r.get(outcome_field) == "TP_BARRIER"),
        "episode_tp_first_rate_pct": round(ep_tp / (ep_tp + ep_sl) * 100, 2) if ep_tp + ep_sl else None,
        "episode_tp_first_95ci": wilson(ep_tp, ep_tp + ep_sl),
        "positive_net_rate_pct": round(pos / n * 100, 2) if n else None,
        "positive_net_95ci": wilson(pos, n),
        "positive_net_cluster_95ci": _cluster_bootstrap(sub, lambda r: (r.get(field) or 0) > 0),
        "episode_positive_net_rate_pct": round(ep_pos / len(episodes) * 100, 2) if episodes else None,
        "episode_positive_net_95ci": wilson(ep_pos, len(episodes)),
        "mean_net_return_pct": round(sum(rets) / len(rets) * 100, 3) if rets else None,
        "median_net_return_pct": round(sorted(rets)[len(rets) // 2] * 100, 3) if rets else None,
        "outcomes": dict(Counter(r.get(outcome_field) for r in sub)),
    }


def _brier(rows: list[dict]) -> dict:
    sub = [r for r in rows if r.get("hf_target_freq") is not None]
    if not sub:
        return {"n": 0}
    ys = [1 if r.get("outcome") == "TP_BARRIER" else 0 for r in sub]
    ps = [float(r["hf_target_freq"]) for r in sub]
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(sub)
    base = sum(ys) / len(ys)
    base_brier = sum((base - y) ** 2 for y in ys) / len(sub)
    return {
        "n": len(sub),
        "brier": round(brier, 5),
        "base_rate": round(base, 4),
        "base_brier": round(base_brier, 5),
        "bss": round(1 - brier / base_brier, 4) if base_brier > 0 else None,
    }


def _bucket(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(key))].append(r)
    return {k: _rate_block(v) for k, v in sorted(groups.items())}


def _score_bucket(r: dict) -> str:
    s = float(r.get("rule_score") or 0)
    return f"{int(s // 10) * 10}-{int(s // 10) * 10 + 9}"


def _regime_bucket(r: dict) -> str:
    s = float(r.get("regime_score") or 0)
    if s < 35:
        return "<35"
    if s < 50:
        return "35-49"
    if s < 65:
        return "50-64"
    return "65+"


def simulate_portfolio(
    trades: list[dict],
    initial: float = 1_000_000.0,
    risk_pct: float = 0.01,
    max_positions: int = 20,
    max_notional_pct: float = 0.05,
    max_new_per_day: int = 4,
    use_limit_n5: bool = True,
) -> dict:
    """Cash-constrained, non-leveraged portfolio using real exit_day and daily concentration cap."""
    fill_key = "limit_n5_fill" if use_limit_n5 else "fill"
    entry_day_key = "limit_n5_entry_day" if use_limit_n5 else "entry_day"
    entry_px_key = "limit_n5_entry_price" if use_limit_n5 else "entry_price"
    exit_day_key = "limit_n5_exit_day" if use_limit_n5 else "exit_day"
    ret_key = "limit_n5_net_return" if use_limit_n5 else "net_return"

    eligible = [
        t for t in trades
        if t.get(fill_key)
        and t.get(entry_day_key)
        and t.get(exit_day_key)
        and t.get(ret_key) is not None
        and not t.get("veto")
    ]
    by_entry = sorted(
        eligible,
        key=lambda t: (t[entry_day_key], -float(t.get("rule_score") or 0.0), t["symbol"]),
    )
    cash = initial
    open_positions: list[dict] = []
    peak = initial
    max_dd = 0.0
    taken = 0
    skipped = 0
    realized: list[float] = []
    opened_on_day: Counter = Counter()

    for t in by_entry:
        day = t[entry_day_key]
        still: list[dict] = []
        for p in open_positions:
            if p["exit_day"] <= day:
                cash += p["notional"] + p["pnl"]
                realized.append(p["net_return"])
            else:
                still.append(p)
        open_positions = still
        equity = cash + sum(p["notional"] for p in open_positions)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

        if (
            len(open_positions) >= max_positions
            or opened_on_day[day] >= max_new_per_day
            or any(p["symbol"] == t["symbol"] for p in open_positions)
        ):
            skipped += 1
            continue
        entry = float(t.get(entry_px_key) or 0)
        stop = float(t.get("stop") or 0)
        if entry <= 0 or stop <= 0 or stop >= entry:
            skipped += 1
            continue
        stop_dist = max(0.005, (entry - stop) / entry)
        notional = min(equity * risk_pct / stop_dist, equity * max_notional_pct, cash)
        if notional < equity * 0.005:
            skipped += 1
            continue
        cash -= notional
        net_r = float(t[ret_key])
        open_positions.append({
            "symbol": t["symbol"],
            "entered": day,
            "exit_day": t[exit_day_key],
            "notional": notional,
            "pnl": notional * net_r,
            "net_return": net_r,
        })
        opened_on_day[day] += 1
        taken += 1

    while open_positions:
        p = open_positions.pop(0)
        cash += p["notional"] + p["pnl"]
        realized.append(p["net_return"])
        equity = cash + sum(x["notional"] for x in open_positions)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
    wins = sum(1 for x in realized if x > 0)
    return {
        "initial_equity": initial,
        "final_equity": round(cash, 2),
        "total_return_pct": round((cash / initial - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "trades_taken": taken,
        "trades_skipped_capacity": skipped,
        "win_rate_pct": round(wins / len(realized) * 100, 2) if realized else None,
        "mean_trade_return_pct": round(sum(realized) / len(realized) * 100, 3) if realized else None,
    }


def _bucket_key(rows: list[dict], fn) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[fn(r)].append(r)
    return groups


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or (args.run_root / "metrics.json")
    report: dict = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_root": str(args.run_root),
        "splits_with_embargo": SPLITS,
        "disclosure": {
            "survivorship_bias": "PRESENT(current listings only)",
            "st_history": "UNAVAILABLE(current name only; current ST excluded from universe)",
            "vix": "US uses same-day cached ^VIX close; missing days -> null",
        },
        "markets": {},
    }
    for market in ("CN", "HK", "US"):
        rows, meta = _load_shards(args.run_root, market)
        long_rows = [r for r in rows if not r.get("__error__")]
        filled = [r for r in long_rows if r.get("fill")]
        lim_filled = [r for r in long_rows if r.get("limit_n5_fill")]
        report["markets"][market] = {
            "done_shards": meta["done_shards"],
            "decisions": meta["decisions"],
            "errors": meta["errors"],
            "actions": meta["actions"],
            "long_signals": len(long_rows),
            "long_signal_rate_pct": round(len(long_rows) / meta["decisions"] * 100, 2) if meta["decisions"] else None,
            "fills_next_open": len(filled),
            "fill_rate_next_open_pct": round(len(filled) / len(long_rows) * 100, 2) if long_rows else None,
            "fills_limit_n5": len(lim_filled),
            "fill_rate_limit_n5_pct": round(len(lim_filled) / len(long_rows) * 100, 2) if long_rows else None,
            "signal_level": _rate_block(long_rows),
            "fill_level": _rate_block(filled),
            "limit_n5_level": _rate_block(lim_filled, field="limit_n5_net_return", outcome_field="limit_n5_outcome"),
            "brier_hf_vs_tp_first": _brier(long_rows),
            "by_year": _bucket(long_rows, "year"),
            "by_action": _bucket(long_rows, "action"),
            "by_score_bucket": {k: _rate_block(v) for k, v in sorted(_bucket_key(long_rows, _score_bucket).items())},
            "by_regime_bucket": {k: _rate_block(v) for k, v in sorted(_bucket_key(long_rows, _regime_bucket).items())},
            "portfolio_sim": simulate_portfolio(long_rows, use_limit_n5=bool(lim_filled)),
        }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for m in ("CN", "HK", "US"):
        r = report["markets"][m]
        print(
            f"{m} (shards={r['done_shards']}): decisions={r['decisions']} long={r['long_signals']} "
            f"open_fill={r['fills_next_open']} lim5_fill={r['fills_limit_n5']} "
            f"signal_pos={r['signal_level']['positive_net_rate_pct']}% "
            f"open_fill_pos={r['fill_level']['positive_net_rate_pct']}% "
            f"lim5_fill_pos={r['limit_n5_level']['positive_net_rate_pct']}% "
            f"lim5_tp_first={r['limit_n5_level']['tp_first_rate_pct']}%"
        )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
