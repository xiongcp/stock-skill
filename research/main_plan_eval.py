#!/usr/bin/env python3
"""主做多计划检验 · 第二遍：按固定价位撮合信号、抽取同日随机基准并汇总。

见 docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md 第 3.2~5 节。
第一遍分片由 main_plan_scan.py 生成。主基准"移植几何"：在信号日随机抽股，把当日某个信号的价位形状
（相对收盘价的 ATR 倍数）套到被抽股票上；诊断基准用被抽股票自己的计划价位。两者同撮合、同出场、同费用。
开发区结果均为样本内，不构成验收；H1 只能由 run_h1_main_plan.py 运行。
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import zlib
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import main_plan_scan as scan
import run_dev_region_v2 as v2
from aggregate import _non_overlapping_episodes, date_block_bootstrap
from common import ART, LONG_ACTIONS, ROOT
from run_backtest import make_pit_slice

from evaluation import label_triple_barrier_event  # noqa: E402
from market_rules import round_to_tick  # noqa: E402

SEED = 20261021
BASELINE_MULTIPLE = 10
SPEC = "docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md"
OUT = ART / "dev_main_plan_v3_metrics.json"
LEDGER = ART / "trial_ledger.jsonl"
HYPOTHESIS = "MAIN_LONG_PLAN"

_E: dict = {}


def valid_geometry(r: dict) -> bool:
    return r["sl"] < r["z"][0] <= r["z"][1] < r["tp"]


def plan_atr_multiples(sig: dict) -> list[float]:
    """信号计划价位相对信号日收盘价的 ATR 倍数：[入场区下沿, 上沿, 止损, TP1]。"""
    c, atr = sig["c"], sig["atr"]
    return [(sig["z"][0] - c) / atr, (sig["z"][1] - c) / atr, (sig["sl"] - c) / atr, (sig["tp"] - c) / atr]


def transplant_levels(mult: list[float], c: float, atr: float, market: str, day: str) -> dict | None:
    """把 ATR 倍数套到另一只股票的收盘价与 ATR 上并按价位表取整；取整后几何自相矛盾时返回 None。"""
    lo, hi, sl, tp = (round_to_tick(c + k * atr, market, "equity", day) for k in mult)
    if not (sl < lo <= hi < tp):
        return None
    return {"lo": lo, "hi": hi, "sl": sl, "tp": tp}


def execute_main_plan(symbol: str, data: dict, pit: list[dict], cut: int, b_pos: int,
                      entry_low: float, entry_high: float, tp: float, sl: float, rt: list) -> dict:
    """按固定价位撮合：次日开盘落在入场区内的报告层，与 5 日限价成交的主口径；出场含保本上移。"""
    market = v2._C["market"]
    kw = {"horizon_bars": v2.HORIZON, "market": market, "asset_type": "equity", "symbol": symbol,
          "is_st": data["is_st"], "trailing_stop": bool(rt[0]), "trigger_r": float(rt[1]),
          "confirm_close_r": float(rt[2]), "lock_r": float(rt[3]), "name": data["name"]}
    out: dict = {}
    op = float(pit[cut + 1]["open"])
    # 与技能自带回测 LIVE_PLAN_REPLAY_V6 相同：次日开盘落在入场区内且高于止损才成交（只报告）
    if entry_low <= op <= entry_high and op > sl:
        r = label_triple_barrier_event(pit, entry_idx=cut + 1, tp_price=tp, sl_price=sl, **kw)
        out["open_status"] = "FILL" if r.get("tradable") else f"NOT_TRADABLE:{r.get('reason')}"
        if r.get("tradable"):
            out.update(open_net=float(r["net_return"]), open_outcome=r["outcome"])
    else:
        out["open_status"] = "OPEN_OUTSIDE_ZONE"

    bench_days = v2._C["bench_days"]
    max_day = bench_days[min(len(bench_days) - 1, b_pos + v2.LIMIT_SESSIONS)]
    out["fill_status"] = "NO_FILL_5D"
    for k in range(cut + 1, min(len(pit) - 1, cut + v2.LIMIT_SESSIONS) + 1):
        b = pit[k]
        if b["day"] > max_day:
            break
        op_k, lo_k = float(b["open"]), float(b["low"])
        if op_k <= entry_high:
            fill_px = op_k
        elif lo_k <= entry_high:
            fill_px = entry_high
        else:
            continue
        if fill_px <= sl:
            out["fill_status"] = "GAP_BELOW_STOP"
            break
        r = label_triple_barrier_event(pit, entry_idx=k, tp_price=tp, sl_price=sl, entry_price_override=fill_px, **kw)
        if r.get("tradable"):
            out.update(fill_status="FILL", fill_day=r["entry_day"], fill_price=fill_px,
                       net=float(r["net_return"]), gross=float(r["gross_return"]), outcome=r["outcome"])
        else:
            out["fill_status"] = f"NOT_TRADABLE:{r.get('reason')}"
        break
    return out


def _u(*parts: str) -> float:
    return zlib.crc32("|".join((str(SEED),) + parts).encode()) / 0xFFFFFFFF


def baseline_plan(signals_by_day: dict[str, list[dict]], pool_by_day: dict[str, int]) -> dict[str, dict]:
    """每个信号日：抽样率 min(1, 10 × 当日信号数 / 当日股票池规模)，以及当日各信号的 ATR 倍数与保本参数（按标的排序，可复现）。"""
    out: dict[str, dict] = {}
    for day, sigs in signals_by_day.items():
        ordered = sorted(sigs, key=lambda s: s["sym"])
        out[day] = {
            "rate": min(1.0, BASELINE_MULTIPLE * len(ordered) / max(1, pool_by_day.get(day, 0))),
            "mults": [plan_atr_multiples(s) for s in ordered],
            "rts": [s["rt"] for s in ordered],
        }
    return out


def draw(market: str, row: dict, day_plan: dict | None) -> int | None:
    """该股票-日是否被抽为基准；抽中时返回移植来源信号在当日列表中的下标。"""
    if day_plan is None or _u(market, row["sym"], row["day"]) >= day_plan["rate"]:
        return None
    n = len(day_plan["mults"])
    return min(n - 1, int(_u("geo", market, row["sym"], row["day"]) * n))


def _outcome(ex: dict) -> dict:
    return {"fill_status": ex["fill_status"], "net": ex.get("net"), "gross": ex.get("gross"), "outcome": ex.get("outcome")}


def _eval_init(h1_authorized: bool, market: str, mode: str, plans: dict) -> None:
    scan.H1_AUTHORIZED = h1_authorized
    scan.init(market, mode)
    _E.clear()
    _E.update(market=market, plans=plans)


def eval_shard(path_str: str) -> tuple[list[dict], dict[str, list[dict]], dict]:
    """一个分片：撮合其中的信号，抽取并撮合两类基准；基准按标的做 21 天 episode 去重后再返回，控制内存。"""
    market, plans = _E["market"], _E["plans"]
    by_sym: dict[str, list[dict]] = defaultdict(list)
    with gzip.open(path_str, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if not r.get("__error__"):
                by_sym[r["sym"]].append(r)
    signals: list[dict] = []
    base: dict[str, list[dict]] = {"transplant": [], "own_plan": []}
    counts: Counter = Counter()
    for sym, rows in by_sym.items():
        data = v2._load_symbol(sym)
        if data is None:
            raise RuntimeError(f"第二遍无法加载 {sym}：行情缓存可能在两遍之间被改动")
        sym_base: dict[str, list[dict]] = {"transplant": [], "own_plan": []}
        for r in sorted(rows, key=lambda x: x["day"]):
            is_sig = r["a"] in LONG_ACTIONS
            j = draw(market, r, plans.get(r["day"]))
            if not is_sig and j is None:
                continue
            pit, cut = make_pit_slice(data["bars"], r["t"], v2.HORIZON)
            own = None
            if valid_geometry(r):
                own = execute_main_plan(sym, data, pit, cut, r["b"], r["z"][0], r["z"][1], r["tp"], r["sl"], r["rt"])
            if is_sig:
                row = {"symbol": sym, "day": r["day"], "action": r["a"], "setup": r["st"]}
                row.update(own if own is not None else {"fill_status": "GEOMETRY_INVALID"})
                signals.append(row)
            if j is None:
                continue
            counts["drawn"] += 1
            p = plans[r["day"]]
            lv = transplant_levels(p["mults"][j], r["c"], r["atr"], market, r["day"])
            if lv is None:
                counts["transplant_geometry_invalid"] += 1
            else:
                ex = execute_main_plan(sym, data, pit, cut, r["b"], lv["lo"], lv["hi"], lv["tp"], lv["sl"], p["rts"][j])
                sym_base["transplant"].append({"symbol": sym, "day": r["day"], **_outcome(ex)})
            if own is None:
                counts["own_plan_geometry_invalid"] += 1
            else:
                sym_base["own_plan"].append({"symbol": sym, "day": r["day"], **_outcome(own)})
        for kind, rows_k in sym_base.items():
            for x in rows_k:
                counts[f"{kind}:{x['fill_status']}"] += 1
            base[kind].extend(_non_overlapping_episodes([x for x in rows_k if x["fill_status"] == "FILL"]))
    return signals, base, dict(counts)


def _excess(eps: list[dict], base_eps: list[dict]) -> dict | None:
    """信号 episode 相对基准 episode 的净正率差与均值差，区间按信号日分块配对 bootstrap。"""
    if not eps or not base_eps:
        return None
    n, nb = len(eps), len(base_eps)
    rate = sum(r["net"] > 0 for r in eps) / n - sum(r["net"] > 0 for r in base_eps) / nb
    mean = sum(r["net"] for r in eps) / n - sum(r["net"] for r in base_eps) / nb
    return {
        "baseline_to_signal_ratio": round(nb / n, 1),
        "rate_pp": round(rate * 100.0, 2),
        "rate_pp_date_cluster95": v2._ci_pct(date_block_bootstrap(eps, lambda r: r["net"] > 0, base_eps)),
        "mean_pct": round(mean * 100.0, 3),
        "mean_pct_date_cluster95": v2._ci_pct(date_block_bootstrap(eps, lambda r: r["net"], base_eps)),
    }


def _cost_2x(eps: list[dict]) -> list[dict]:
    """费用翻倍：单笔费用 = 毛收益 − 净收益，翻倍后净收益 = 2 × 净 − 毛。"""
    return [{**r, "net": 2.0 * r["net"] - r["gross"]} for r in eps]


def _criteria(main: dict, ex: dict | None, note: str) -> dict:
    mean_ci = main.get("mean_date_cluster95_pct")
    return {
        "note": note,
        "P1_mean_net_positive": bool(mean_ci and main["mean_net_return_pct"] > 0 and mean_ci[0] > 0),
        "P2a_rate_excess": bool(ex and ex["rate_pp_date_cluster95"] and ex["rate_pp_date_cluster95"][0] > 0),
        "P2b_mean_excess": bool(ex and ex["mean_pct_date_cluster95"] and ex["mean_pct_date_cluster95"][0] > 0),
        "P3_sample": bool(main.get("n_episodes", 0) >= 100 and main.get("n_signal_days", 0) >= 30),
        "T_rate_ge_70": bool(main.get("n_episodes") and main["net_positive_rate_pct"] >= 70.0),
    }


def summarize(signals: list[dict], base: dict[str, list[dict]], note: str) -> dict:
    """主口径：5 日限价成交、episode 去重；分组结果取总 episode 的子集，只报告。"""
    eps = v2._fill_eps(signals)
    main = v2._layer_stats(eps)
    transplant, own = base["transplant"], base["own_plan"]
    opened = [{**r, "net": r["open_net"], "outcome": r["open_outcome"]} for r in signals if r.get("open_status") == "FILL"]
    years = sorted({r["day"][:4] for r in eps})
    setups = sorted({r["setup"] for r in signals})
    ex = _excess(eps, transplant)
    out: dict = {
        "n_signals": len(signals),
        "actions": dict(Counter(r["action"] for r in signals)),
        "setups": dict(Counter(r["setup"] for r in signals)),
        "fill_status": dict(Counter(r["fill_status"] for r in signals)),
        "fill_layer": main,
        "next_open_layer": {
            "n_fills": len(opened),
            "outside_zone": sum(1 for r in signals if r.get("open_status") == "OPEN_OUTSIDE_ZONE"),
            "fill_layer": v2._layer_stats(_non_overlapping_episodes(opened)),
        },
        "baselines": {"transplant": v2._layer_stats(transplant), "own_plan": v2._layer_stats(own)},
        "excess_vs_same_day_random": ex,
        "excess_vs_own_plan_diagnostic": _excess(eps, own),
        "groups": {
            "action": {a: v2._layer_stats([r for r in eps if r["action"] == a]) for a in LONG_ACTIONS},
            "setup": {s: v2._layer_stats([r for r in eps if r["setup"] == s]) for s in setups},
            "year": {
                y: {
                    "fill_layer": v2._layer_stats([r for r in eps if r["day"][:4] == y]),
                    "excess_vs_same_day_random": _excess([r for r in eps if r["day"][:4] == y],
                                                         [r for r in transplant if r["day"][:4] == y]),
                }
                for y in years
            },
        },
        "cost_2x": {
            "fill_layer": v2._layer_stats(_cost_2x(eps)),
            "excess_vs_same_day_random": _excess(_cost_2x(eps), _cost_2x(transplant)),
        },
    }
    if main.get("n_episodes") and main["max_day_share_pct"] > 10.0:
        top_day = Counter(r["day"] for r in eps).most_common(1)[0][0]
        rest = [r for r in eps if r["day"] != top_day]
        out["sensitivity_excluding_top_day"] = {
            "excluded_day": top_day,
            "fill_layer": v2._layer_stats(rest),
            "excess_vs_same_day_random": _excess(rest, [r for r in transplant if r["day"] != top_day]),
        }
    out["criteria_diagnostic"] = _criteria(main, ex, note)
    return out


def config() -> dict:
    return {
        "signal_actions": list(LONG_ACTIONS), "limit_sessions": v2.LIMIT_SESSIONS, "horizon": v2.HORIZON,
        "episode_gap_days": 21, "baseline": "same_day_random_transplanted_geometry",
        "baseline_multiple": BASELINE_MULTIPLE, "seed": SEED,
        "bootstrap": {"block": "signal_day", "n_boot": 2000, "seed": 20261020},
    }


def run_market(market: str, mode: str = "dev", workers: int = 8, limit_symbols: int | None = None) -> dict:
    out_dir = scan.run_scan(market, mode, workers, limit_symbols)
    pool_by_day: Counter = Counter()
    signals_by_day: dict[str, list[dict]] = defaultdict(list)
    for r in scan.iter_rows(out_dir):
        if r.get("__error__"):
            continue
        pool_by_day[r["day"]] += 1
        if r["a"] in LONG_ACTIONS:
            signals_by_day[r["day"]].append(r)
    plans = baseline_plan(signals_by_day, pool_by_day)
    shards = [str(p) for p in sorted(out_dir.glob("shard_*.jsonl.gz"))]
    init_args = (scan.H1_AUTHORIZED, market, mode, plans)
    if workers <= 1:
        _eval_init(*init_args)
        results = [eval_shard(s) for s in shards]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_eval_init, initargs=init_args) as ex:
            results = list(ex.map(eval_shard, shards))
    signals = [r for sig, _, _ in results for r in sig]
    base = {k: [r for _, b, _ in results for r in b[k]] for k in ("transplant", "own_plan")}
    counts: Counter = Counter()
    for _, _, c in results:
        counts.update(c)
    region, _ = scan.mode_config(mode, market)
    note = "开发区·样本内，仅用于诊断，不构成验收" if mode == "dev" else "留出集 H1，一次性评估"
    return {
        "scan": scan.scan_stats(out_dir),
        "scan_dir": str(out_dir.relative_to(ROOT)),
        "region": list(region),
        "baseline_sampling": {"n_signal_days": len(plans), "counts": dict(counts)},
        "summary": summarize(signals, base, note),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="主做多计划检验：开发区诊断（样本内）。H1 只能由 run_h1_main_plan.py 运行")
    ap.add_argument("--markets", default="CN,HK,US")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit-symbols", type=int, default=None, help="冒烟测试：每市场只取等间隔抽样的 N 只")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    started = datetime.now(timezone.utc)
    result: dict = {
        "generated_at_utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": v2._git_head(),
        "spec": SPEC,
        "hypothesis": HYPOTHESIS,
        "mode": "dev",
        "in_sample_note": "开发区结果均为样本内，不构成验收；主计划规则未调参",
        "config": {**config(), "limit_symbols": args.limit_symbols},
        "markets": {},
    }
    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        res = run_market(market, "dev", args.workers, args.limit_symbols)
        result["markets"][market] = res
        s = res["summary"]
        fl = s["fill_layer"]
        print(f"[{market}] signals={s['n_signals']} episodes={fl.get('n_episodes')} days={fl.get('n_signal_days')} "
              f"rate={fl.get('net_positive_rate_pct')}% mean={fl.get('mean_net_return_pct')}% "
              f"excess={s['excess_vs_same_day_random']} scan_errors={res['scan']['errors']}", flush=True)
    result["elapsed_seconds"] = round((datetime.now(timezone.utc) - started).total_seconds())
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": result["generated_at_utc"], "script": "research/main_plan_eval.py", "hypothesis": HYPOTHESIS,
            "code_commit": result["code_commit"], "config": result["config"],
            "out": str(args.out.relative_to(ROOT)) if args.out.is_relative_to(ROOT) else str(args.out),
            "n_configs_evaluated": 1,
            "summary": {m: {k: v["summary"]["fill_layer"].get(k) for k in
                            ("n_episodes", "net_positive_rate_pct", "mean_net_return_pct")}
                        for m, v in result["markets"].items()},
        }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
