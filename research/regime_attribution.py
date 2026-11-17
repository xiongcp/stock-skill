#!/usr/bin/env python3
"""分状态归因（阶段 0）：把已有的开发区主计划记录按大盘状态与持有期大盘涨跌拆开，并审计正常波动范围的分状态覆盖率。

规格：docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md 第 2 节。
只读 dev_main_plan_v3_metrics.json 登记的分片目录与行情缓存，不重新扫描，不读取留出集 H1。
全部结果为样本内描述，不构成验收；分组里出现的任何"优势"只能当作新假设。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import main_plan_eval as ev
import main_plan_scan as scan
import run_dev_region_v2 as v2
from aggregate import date_block_bootstrap
from common import ART, LONG_ACTIONS, ROOT
from run_backtest import WINDOW_BARS, make_pit_slice

from analysis import analyze_bundle  # noqa: E402
from evaluation import label_triple_barrier_event  # noqa: E402
from indicators import compute_all_indicators  # noqa: E402
from strategies import classify_market_regime  # noqa: E402
from structure import HISTORY_BARS, STRUCT_DAYS  # noqa: E402

SPEC = "docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md"
HYPOTHESIS = "REGIME_ATTRIBUTION"
REFERENCE = ART / "dev_main_plan_v3_metrics.json"
OUT = ART / "regime_attribution.json"
LEDGER = ART / "trial_ledger.jsonl"
SCORE_EDGES = (35.0, 45.0, 55.0, 70.0)
SCORE_LABELS = ("<35", "35-45", "45-55", "55-70", ">=70")
MIN_EPISODES, MIN_DAYS = 100, 30
N_BOOT, BOOT_SEED = 2000, 20261020
COV_MOD = 50
COV_STEPS = (5, 10, 15)
COV_MIN_SAMPLES, COV_MIN_DAYS = 300, 30


def reference(market: str) -> dict:
    """已登记的开发区主表（提交 509e499 生成）：复现核对的目标，也给出要读的分片目录。"""
    return json.loads(REFERENCE.read_text(encoding="utf-8"))["markets"][market]


def read_shard(path: str | Path) -> Iterator[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if not r.get("__error__"):
                yield r


def bench_state_table() -> dict[str, dict]:
    """开发区内每个基准交易日的大盘状态；窗口截取与算法同线上 analyze_bundle 第 4 步（须先 scan.init）。"""
    bench, days = v2._C["bench"], v2._C["bench_days"]
    lo, hi = v2._C["region"]
    out: dict[str, dict] = {}
    for b_hi in range(1, len(days) + 1):
        day = days[b_hi - 1]
        if not lo <= day <= hi:
            continue
        b_bars = bench[max(0, b_hi - WINDOW_BARS):b_hi]
        ind = compute_all_indicators(b_bars[-HISTORY_BARS:])
        reg = classify_market_regime(b_bars[-STRUCT_DAYS:], ind["snapshot"], vix_value=v2._C["vix"].get(day))
        out[day] = {"regime": reg["regime"], "score": float(reg["regime_score"])}
    return out


def score_bucket(score: float) -> str:
    return SCORE_LABELS[bisect_right(SCORE_EDGES, score)]


def bench_hold_return(fill_day: str, exit_day: str) -> float:
    """持有期基准收益：成交日之前最近一个基准收盘 → 出场日当天或之前最近一个基准收盘（须先 scan.init）。"""
    days, bench = v2._C["bench_days"], v2._C["bench"]
    i = bisect_left(days, fill_day) - 1
    j = bisect_right(days, exit_day) - 1
    return float(bench[j]["close"]) / float(bench[i]["close"]) - 1.0


def eval_shard_with_exit(path_str: str) -> tuple[list[dict], dict, dict]:
    """ev.eval_shard 的结果原样返回，并给每个已成交信号补上出场日与持有期基准收益。

    冻结的撮合函数不返回出场日，这里用同一个标签函数、同一成交日与成交价重算一次，并核对净收益与原结果相同。
    """
    signals, base, counts = ev.eval_shard(path_str)
    raw = {(r["sym"], r["day"]): r for r in read_shard(path_str) if r["a"] in LONG_ACTIONS}
    market = v2._C["market"]
    loaded: dict[str, dict] = {}
    for s in signals:
        if s["fill_status"] != "FILL":
            continue
        r = raw[(s["symbol"], s["day"])]
        data = loaded.get(s["symbol"]) or loaded.setdefault(s["symbol"], v2._load_symbol(s["symbol"]))
        pit, cut = make_pit_slice(data["bars"], r["t"], v2.HORIZON)
        k = next(i for i in range(cut + 1, len(pit)) if pit[i]["day"] == s["fill_day"])
        rt = r["rt"]
        res = label_triple_barrier_event(
            pit, entry_idx=k, tp_price=r["tp"], sl_price=r["sl"], entry_price_override=s["fill_price"],
            horizon_bars=v2.HORIZON, market=market, asset_type="equity", symbol=s["symbol"], is_st=data["is_st"],
            trailing_stop=bool(rt[0]), trigger_r=float(rt[1]), confirm_close_r=float(rt[2]), lock_r=float(rt[3]),
            name=data["name"],
        )
        if not res.get("tradable") or float(res["net_return"]) != s["net"]:
            raise RuntimeError(f"出场日重算与原撮合不一致：{s['symbol']} {s['day']} {res.get('net_return')} != {s['net']}")
        s["exit_day"] = res["exit_day"]
        s["bench_hold_ret"] = bench_hold_return(s["fill_day"], s["exit_day"])
    return signals, base, counts


def cell_stats(eps: list[dict], base: dict[str, list[float]]) -> dict:
    """一个格子：冻结口径的成交层统计，加上该格信号日上的基准均值与同日随机超额。"""
    st = v2._layer_stats(eps)
    ex = ev._excess(eps, base)
    days = {r["day"] for r in eps}
    b_n = sum(base[d][1] for d in days if d in base)
    return {
        "n_episodes": st["n_episodes"],
        "n_signal_days": st.get("n_signal_days", 0),
        "max_day_share_pct": st.get("max_day_share_pct"),
        "net_positive_rate_pct": st.get("net_positive_rate_pct"),
        "mean_net_return_pct": st.get("mean_net_return_pct"),
        "mean_date_cluster95_pct": st.get("mean_date_cluster95_pct"),
        "baseline_mean_net_return_pct": round(sum(base[d][0] for d in days if d in base) / b_n * 100.0, 3) if b_n else None,
        "excess_mean_pct": ex["mean_pct"] if ex else None,
        "excess_mean_date_cluster95_pct": ex["mean_pct_date_cluster95"] if ex else None,
        "insufficient_sample": st["n_episodes"] < MIN_EPISODES or st.get("n_signal_days", 0) < MIN_DAYS,
    }


def tercile_cuts(values: list[float]) -> tuple[float, float]:
    """三分位切点：排序后第 n//3 与第 2n//3 个值。LOW: v < t1；MID: t1 <= v < t2；HIGH: v >= t2。"""
    s = sorted(values)
    return s[len(s) // 3], s[2 * len(s) // 3]


def attribution_groups(eps: list[dict], table: dict[str, dict], base: dict[str, list[float]]) -> dict:
    t1, t2 = tercile_cuts([r["bench_hold_ret"] for r in eps])
    keys = {
        "E1_regime": lambda r: table[r["day"]]["regime"],
        "E2_score": lambda r: score_bucket(table[r["day"]]["score"]),
        "X1_bench_sign": lambda r: "UP" if r["bench_hold_ret"] > 0 else "DOWN_OR_FLAT",
        "X2_bench_tercile": lambda r: "LOW" if r["bench_hold_ret"] < t1 else ("MID" if r["bench_hold_ret"] < t2 else "HIGH"),
    }
    out: dict = {}
    for name, key_fn in keys.items():
        cells: dict[str, list[dict]] = defaultdict(list)
        for r in eps:
            cells[key_fn(r)].append(r)
        out[name] = {label: cell_stats(rows, base) for label, rows in sorted(cells.items())}
    out["X2_cuts_pct"] = [round(t1 * 100.0, 3), round(t2 * 100.0, 3)]
    return out


def beta_regression(eps: list[dict]) -> dict:
    """episode 净收益对持有期基准收益的一元回归；斜率与截距的区间按信号日分块 bootstrap。

    截距是"大盘持有期收益为零时的平均净收益"，斜率是对大盘涨跌的敏感度；都只是样本内描述。
    """
    import numpy as np

    days = sorted({r["day"] for r in eps})
    pos = {d: i for i, d in enumerate(days)}
    blk = np.zeros((len(days), 5))
    for r in eps:
        x, y = r["bench_hold_ret"], r["net"]
        blk[pos[r["day"]]] += (1.0, x, y, x * x, x * y)

    def fit(s):  # s[..., 0:5] = n, Σx, Σy, Σxx, Σxy
        n, sx, sy, sxx, sxy = (s[..., i] for i in range(5))
        slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
        return slope, (sy - slope * sx) / n

    slope, icpt = fit(blk.sum(axis=0))
    idx = np.random.default_rng(BOOT_SEED).integers(0, len(days), size=(N_BOOT, len(days)))
    b_slope, b_icpt = fit(blk[idx].sum(axis=1))
    x = np.array([r["bench_hold_ret"] for r in eps])
    y = np.array([r["net"] for r in eps])
    return {
        "n": len(eps),
        "slope": round(float(slope), 4),
        "slope_date_cluster95": [round(float(v), 4) for v in np.percentile(b_slope, [2.5, 97.5])],
        "intercept_pct": round(float(icpt) * 100.0, 3),
        "intercept_pct_date_cluster95": [round(float(v) * 100.0, 3) for v in np.percentile(b_icpt, [2.5, 97.5])],
        "r2": round(float(np.corrcoef(x, y)[0, 1] ** 2), 4),
    }


def _jsonable(obj):
    """经一次 JSON 往返（元组变列表），便于和文件里读出的参考值逐项比较。"""
    return json.loads(json.dumps(obj, ensure_ascii=False))


def check_reproduction(market: str, eps: list[dict], transplant: dict, ref: dict) -> None:
    """不分组时必须逐项复现已登记的开发区主表（规格第 2.5 节）；否则代码或缓存已与当时不同，分组结果不可用。"""
    got = {
        "fill_layer": _jsonable(v2._layer_stats(eps)),
        "excess_vs_same_day_random": _jsonable(ev._excess(eps, transplant)),
    }
    for key, value in got.items():
        if value != ref["summary"][key]:
            raise RuntimeError(f"{market}: {key} 未能复现 {REFERENCE.name}：{value} != {ref['summary'][key]}")


def run_attribution(market: str, workers: int) -> dict:
    """0A：在已有分片上重做第二遍撮合（与 ev.run_market 相同，但不触发扫描），核对复现后分组。"""
    ref = reference(market)
    out_dir = ROOT / ref["scan_dir"]
    pool_by_day: Counter = Counter()
    signals_by_day: dict[str, list[dict]] = defaultdict(list)
    for r in scan.iter_rows(out_dir):
        if r.get("__error__"):
            continue
        pool_by_day[r["day"]] += 1
        if r["a"] in LONG_ACTIONS:
            signals_by_day[r["day"]].append(r)
    plans = ev.baseline_plan(signals_by_day, pool_by_day)
    shards = [str(p) for p in sorted(out_dir.glob("shard_*.jsonl.gz"))]
    init_args = (False, market, "dev", plans)
    if workers <= 1:
        ev._eval_init(*init_args)
        results = [eval_shard_with_exit(s) for s in shards]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=ev._eval_init, initargs=init_args) as ex:
            results = list(ex.map(eval_shard_with_exit, shards))
    signals = [r for sig, _, _ in results for r in sig]
    transplant = ev.merge_baseline([b["transplant"] for _, b, _ in results])
    eps = v2._fill_eps(signals)
    check_reproduction(market, eps, transplant, ref)
    scan.init(market, "dev")
    table = bench_state_table()
    return {
        "scan_dir": ref["scan_dir"],
        "region": list(v2._C["region"]),
        "reproduction": "EXACT",
        "overall": cell_stats(eps, transplant),
        "groups": attribution_groups(eps, table, transplant),
        "beta_regression": beta_regression(eps),
        "bench_state_day_counts": dict(Counter(v["regime"] for v in table.values())),
    }


def coverage_sampled(sym: str, day: str) -> bool:
    """确定性抽样（约 2%）：sha256(标的 + 日期) 的前 8 位十六进制数对 50 取模为 0，不依赖随机种子。"""
    return int(hashlib.sha256(f"{sym}{day}".encode()).hexdigest()[:8], 16) % COV_MOD == 0


def bands_at(sym: str, data: dict, t: int, day: str) -> tuple[list[dict], int, list, list]:
    """按信号日当时可见的数据重算正常波动范围；返回的切片含之后的K线，只用于对答案。"""
    pit, cut = make_pit_slice(data["bars"], t, v2.HORIZON)
    bundle, request = v2.build_bundle(sym, data, pit, cut, day)
    fp = analyze_bundle(bundle, request, include_replay=False, include_forecast=True)["future_projection_15d"]
    return pit, cut, fp["conformal_50pct_band"], fp["conformal_80pct_band"]


def coverage_shard(path_str: str) -> list[dict]:
    """一个分片里被抽中的股票-日：第 5/10/15 步的两条区间是否盖住实际收盘价。单个股票-日出错只记错误，不中断。"""
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in read_shard(path_str):
        if coverage_sampled(r["sym"], r["day"]):
            by_sym[r["sym"]].append(r)
    out: list[dict] = []
    for sym, rows in by_sym.items():
        data = v2._load_symbol(sym)
        if data is None:
            raise RuntimeError(f"无法加载 {sym}：行情缓存可能在扫描之后被改动")
        for r in rows:
            row: dict = {"symbol": sym, "day": r["day"]}
            try:
                pit, cut, b50, b80 = bands_at(sym, data, r["t"], r["day"])
                for step in COV_STEPS:
                    real = float(pit[cut + step]["close"])
                    row[f"in50_{step}"] = bool(b50[step - 1][0] <= real <= b50[step - 1][1])
                    row[f"in80_{step}"] = bool(b80[step - 1][0] <= real <= b80[step - 1][1])
            except Exception as exc:  # noqa: BLE001 - 与第一遍扫描相同：记错误并计数，不补数
                row = {"symbol": sym, "day": r["day"], "__error__": f"{type(exc).__name__}: {exc}"}
            out.append(row)
    return out


def _coverage_cell(rows: list[dict]) -> dict:
    cell: dict = {"n_samples": len(rows), "n_days": len({r["day"] for r in rows})}
    cell["insufficient_sample"] = cell["n_samples"] < COV_MIN_SAMPLES or cell["n_days"] < COV_MIN_DAYS
    for step in COV_STEPS:
        for name in ("50", "80"):
            k = f"in{name}_{step}"
            cell[f"cover{name}_step{step}_pct"] = round(sum(r[k] for r in rows) / len(rows) * 100.0, 2)
            cell[f"cover{name}_step{step}_date_cluster95_pct"] = v2._ci_pct(date_block_bootstrap(rows, lambda r, k=k: r[k]))
    return cell


def coverage_cells(rows: list[dict], table: dict[str, dict]) -> dict:
    keys = {
        "ALL": lambda r: "ALL",
        "E1_regime": lambda r: table[r["day"]]["regime"],
        "E2_score": lambda r: score_bucket(table[r["day"]]["score"]),
    }
    out: dict = {}
    for name, key_fn in keys.items():
        cells: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            cells[key_fn(r)].append(r)
        out[name] = {label: _coverage_cell(rs) for label, rs in sorted(cells.items())}
    return out


def _coverage_init(market: str) -> None:
    scan.H1_AUTHORIZED = False
    scan.init(market, "dev")


def run_coverage(market: str, workers: int) -> dict:
    """0B：约 2% 的股票-日重算正常波动范围，按大盘状态统计名义 50% / 80% 区间的实际覆盖率。"""
    shards = [str(p) for p in sorted((ROOT / reference(market)["scan_dir"]).glob("shard_*.jsonl.gz"))]
    if workers <= 1:
        _coverage_init(market)
        parts = [coverage_shard(s) for s in shards]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_coverage_init, initargs=(market,)) as ex:
            parts = list(ex.map(coverage_shard, shards))
    rows = [r for part in parts for r in part]
    ok = [r for r in rows if "__error__" not in r]
    errors = [r for r in rows if "__error__" in r]
    _coverage_init(market)
    return {
        "n_sampled": len(rows),
        "n_errors": len(errors),
        "error_examples": errors[:5],
        "nominal_pct": {"cover50": 50.0, "cover80": 80.0},
        "cells": coverage_cells(ok, bench_state_table()),
    }


_GROUP_TITLES = {
    "E1_regime": "事前 · 信号日大盘状态",
    "E2_score": "事前 · 信号日大盘状态分",
    "X1_bench_sign": "事后变量 · 持有期大盘涨跌方向",
    "X2_bench_tercile": "事后变量 · 持有期大盘收益三分位",
}


def _ci(ci) -> str:
    return "—" if not ci else f"[{ci[0]}, {ci[1]}]"


def _row(label: str, c: dict) -> str:
    flag = "（样本不足）" if c["insufficient_sample"] else ""
    return (f"| {label}{flag} | {c['n_episodes']} | {c['n_signal_days']} | {c['net_positive_rate_pct']} | "
            f"{c['mean_net_return_pct']} {_ci(c['mean_date_cluster95_pct'])} | {c['baseline_mean_net_return_pct']} | "
            f"{c['excess_mean_pct']} {_ci(c['excess_mean_date_cluster95_pct'])} |")


def render_markdown(result: dict) -> str:
    """把结果文件排成规格里的"阶段 0 结果"一节；数字全部取自文件，不做任何再计算。"""
    lines = [
        "## 阶段 0 结果（开发区，样本内）",
        "",
        f"数据：`artifacts/long_backtest/regime_attribution.json`（生成于 {result.get('generated_at_utc')}，提交 `{result.get('code_commit')}`）。",
        "全部为样本内描述，不构成验收；带“事后变量”的分组用了持有期的大盘涨跌，只用于拆解，不能用于选股或择时。",
        "收益单位为 %；方括号内是按信号日分块 bootstrap 的 95% 区间；超额 = 信号 episode − 同日随机基准均值。",
        "",
    ]
    for market, entry in result.get("markets", {}).items():
        a = entry.get("attribution")
        if a:
            b = a["beta_regression"]
            lines += [
                f"#### {market}", "",
                f"对持有期大盘收益的一元回归（n = {b['n']}）：斜率 {b['slope']} {_ci(b['slope_date_cluster95'])}，"
                f"截距 {b['intercept_pct']}% {_ci(b['intercept_pct_date_cluster95'])}，R² = {b['r2']}。", "",
                "| 分组 | episode | 信号日 | 净收益为正占比 | 平均净收益 | 同日基准均值 | 同日随机超额 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
                _row("全部", a["overall"]),
            ]
            for g, title in _GROUP_TITLES.items():
                lines.append(f"| **{title}** | | | | | | |")
                lines += [_row(label, c) for label, c in a["groups"][g].items()]
            lines.append("")
        cov = entry.get("coverage")
        if cov:
            lines += [
                f"{market} 正常波动范围覆盖率（抽样 {cov['n_sampled']} 个股票-日，出错 {cov['n_errors']} 个；名义值 50% / 80%）：", "",
                "| 分组 | 样本 | 日期 | 50% 区间 第5/10/15步 | 80% 区间 第5/10/15步 |",
                "| --- | --- | --- | --- | --- |",
            ]
            for g in ("ALL", "E1_regime", "E2_score"):
                for label, c in cov["cells"][g].items():
                    flag = "（样本不足）" if c["insufficient_sample"] else ""
                    lines.append(
                        f"| {label}{flag} | {c['n_samples']} | {c['n_days']} | "
                        + " / ".join(str(c[f"cover50_step{s}_pct"]) for s in COV_STEPS) + " | "
                        + " / ".join(str(c[f"cover80_step{s}_pct"]) for s in COV_STEPS) + " |")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="分状态归因（0A）与分状态覆盖率（0B）：样本内描述，不构成验收")
    ap.add_argument("--part", choices=("attribution", "coverage", "all"), default="all")
    ap.add_argument("--markets", default="CN,HK,US")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--print-md", action="store_true", help="只读已有结果文件并打印摘要，不计算")
    args = ap.parse_args()
    if args.print_md:
        print(render_markdown(json.loads(args.out.read_text(encoding="utf-8"))))
        return 0
    started = datetime.now(timezone.utc)
    result: dict = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else {}
    result.update(
        generated_at_utc=started.strftime("%Y-%m-%dT%H:%M:%SZ"), code_commit=v2._git_head(), spec=SPEC,
        hypothesis=HYPOTHESIS, reference=REFERENCE.name,
        in_sample_note="开发区样本内描述，不构成验收；分组中的任何优势只能当作新假设",
        config={**ev.config(), "score_edges": list(SCORE_EDGES), "min_episodes": MIN_EPISODES, "min_days": MIN_DAYS,
                "coverage_mod": COV_MOD, "coverage_steps": list(COV_STEPS),
                "coverage_min_samples": COV_MIN_SAMPLES, "coverage_min_days": COV_MIN_DAYS},
    )
    markets = result.setdefault("markets", {})
    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        entry = markets.setdefault(market, {})
        if args.part in ("attribution", "all"):
            entry["attribution"] = run_attribution(market, args.workers)
            o = entry["attribution"]["overall"]
            print(f"[{market}] 0A 复现一致 episodes={o['n_episodes']} mean={o['mean_net_return_pct']}% "
                  f"excess={o['excess_mean_pct']}% beta={entry['attribution']['beta_regression']}", flush=True)
        if args.part in ("coverage", "all"):
            entry["coverage"] = run_coverage(market, args.workers)
            c = entry["coverage"]["cells"]["ALL"]["ALL"]
            print(f"[{market}] 0B sampled={entry['coverage']['n_sampled']} errors={entry['coverage']['n_errors']} "
                  f"cover50@15={c['cover50_step15_pct']}% cover80@15={c['cover80_step15_pct']}%", flush=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    n_cells = sum(len(c) for m in markets.values() for k, c in (m.get("attribution") or {}).get("groups", {}).items()
                  if k != "X2_cuts_pct")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": result["generated_at_utc"], "script": "research/regime_attribution.py", "hypothesis": HYPOTHESIS,
            "code_commit": result["code_commit"], "part": args.part, "config": result["config"],
            "out": str(args.out.relative_to(ROOT)) if args.out.is_relative_to(ROOT) else str(args.out),
            "grouping_variables": ["E1_regime", "E2_score", "X1_bench_sign", "X2_bench_tercile"],
            "n_cells_reported": n_cells,
            "note": "样本内描述，未调任何规则；不读取 H1",
        }, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
