#!/usr/bin/env python3
"""反量化过滤规则的 Dev/Val 归因：在 baseline_v1 已有信号上，逐信号计算决策日特征，
比较「被新规则拦截」与「保留」两组的后验表现。只读 Dev 与 Val，不触碰 Holdout Test。

注意：这里的规则只会"删除"信号（降级/否决），所以可以直接复用 baseline 的结局标签；
改变止损/入场区的规则（防扫损缓冲、突破回踩带）需要重算计划，不在本脚本范围内。
特征全部取自决策日及之前的 PIT 复权序列（make_pit_slice + rescale_bars_as_of），无前视。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

from aggregate import SPLITS, wilson
from common import ART, adjusted_bars, load_cache
from run_backtest import make_pit_slice

SKILL_SCRIPTS = Path(__file__).resolve().parent.parent / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

from indicators import atr_series, ema  # noqa: E402

TP = "TP_BARRIER"
SL = ("SL_BARRIER", "SL_BARRIER_SAME_BAR_CONSERVATIVE")
RULES = ("ignition_trap", "bull_trap_churn", "algo_bleed", "momentum_crowding", "any_anti_quant")
# 与 risk.py 对齐：点火/对倒只作用于右侧突破，拆单阴跌只作用于左侧回踩；动量拥挤作用于全部做多信号
APPLIES_TO = {
    "ignition_trap": "RIGHT_SIDE_BREAKOUT",
    "bull_trap_churn": "RIGHT_SIDE_BREAKOUT",
    "algo_bleed": "LEFT_SIDE_PULLBACK",
    "momentum_crowding": None,
    "any_anti_quant": None,
}


def features(pit: list[dict], cut: int) -> dict | None:
    """决策日 cut 的反量化特征，与 risk.py 中的公式一一对应。"""
    bars = pit[: cut + 1]
    if len(bars) < 60:
        return None
    o, h, l, c = (float(bars[-1][k]) for k in ("open", "high", "low", "close"))
    span = max(1e-9, h - l)
    closes = [float(b["close"]) for b in bars]
    atr = atr_series(bars, 14)[-1]
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    if not atr or not e20 or not e50:
        return None
    atr = max(c * 0.005, float(atr))
    vols = [float(b.get("volume") or 0.0) for b in bars[-21:-1]]
    rv = float(bars[-1].get("volume") or 0.0) / (sum(vols) / len(vols)) if vols and sum(vols) > 0 else 1.0
    win20 = bars[-20:]
    hi20, lo20 = max(float(b["high"]) for b in win20), min(float(b["low"]) for b in win20)
    high_rank = 0.5 if hi20 <= lo20 else (max(c, o) - lo20) / (hi20 - lo20)
    cir, uw, body = (c - l) / span, (h - max(c, o)) / span, abs(c - o) / span
    ret5 = (c - closes[-6]) / atr
    ext = (c - float(e20)) / atr
    f = {
        "ignition_trap": (c < o) or (uw > 0.40) or (cir < 0.58),
        "bull_trap_churn": rv >= 2.2 and (body < 0.28 or uw >= 0.42) and high_rank >= 0.75,
        "algo_bleed": c < float(e20) and float(e20) <= float(e50),
        "momentum_crowding": (ret5 > 2.4 or ext > 1.65) and (uw >= 0.35 or (rv >= 1.8 and body <= 0.35)),
    }
    f["any_anti_quant"] = any(f.values())
    return f


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    tp = sum(1 for r in rows if r["outcome"] == TP)
    sl = sum(1 for r in rows if r["outcome"] in SL)
    pos = sum(1 for r in rows if (r.get("net_return") or 0.0) > 0)
    nets = [r["net_return"] for r in rows if r.get("net_return") is not None]
    return {
        "n": n,
        "tp_rate_all_pct": round(100 * tp / n, 2),
        "tp_over_tp_sl_pct": round(100 * tp / max(1, tp + sl), 2),
        "positive_net_pct": round(100 * pos / n, 2),
        "positive_net_wilson95": [round(x, 2) for x in (wilson(pos, n) or [float("nan")] * 2)],  # aggregate.wilson 已是百分数
        "mean_net_pct": round(100 * sum(nets) / max(1, len(nets)), 3),
    }


def run(run_root: Path, market: str, layer: str) -> dict:
    by_split: dict[str, list[dict]] = {"dev": [], "val": []}
    sym_rows: dict[str, list[dict]] = defaultdict(list)
    for sp in sorted((run_root / market / "shards").glob("shard_*.jsonl")):
        if not sp.with_suffix(".done").exists():
            continue
        for line in sp.open(encoding="utf-8"):
            r = json.loads(line)
            if r.get("__error__") or r.get("outcome") is None:
                continue
            if layer == "fill" and not r.get("fill"):
                continue
            for name in ("dev", "val"):
                lo, hi = SPLITS[name]
                if lo <= r["day"] <= hi:
                    sym_rows[r["symbol"]].append((name, r))
    for sym, items in sym_rows.items():
        payload = load_cache(market, sym)
        _, bars = adjusted_bars(payload, mode="total_return")
        idx = {b["day"]: i for i, b in enumerate(bars)}
        for name, r in items:
            t = idx.get(r["day"])
            if t is None:
                continue
            pit, cut = make_pit_slice(bars, t, 15)
            feat = features(pit, cut)
            if feat is None:
                continue
            by_split[name].append({**r, "feat": feat})
    out: dict = {}
    for name, rows in by_split.items():
        block = {"all": summarize(rows)}
        for rule in RULES:
            scope = APPLIES_TO[rule]
            sub = [r for r in rows if scope is None or r.get("setup_type") == scope]
            block[rule] = {
                "scope": scope or "ALL",
                "blocked": summarize([r for r in sub if r["feat"][rule]]),
                "kept": summarize([r for r in sub if not r["feat"][rule]]),
            }
        out[name] = block
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", type=Path, default=ART / "runs" / "baseline_v1")
    ap.add_argument("--layer", choices=["signal", "fill"], default="signal")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    res = {m: run(args.run_root, m, args.layer) for m in ("CN", "HK", "US")}
    text = json.dumps(res, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
