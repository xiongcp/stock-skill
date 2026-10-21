#!/usr/bin/env python3
"""高置信反转层开发区回测 v2（回测与验收口径调整方案第 1~3 步，见
docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md）。

与 v1（run_final_oos_evaluation.py）的区别：
1. 触发、计划价位与大盘否决全部来自线上 analyze_bundle：先用宽松预筛（线上条件的超集，
   research/tests/test_dev_region_v2.py 校验）缩小候选，再对候选个股-日调用线上流水线。
2. 撮合按线上计划的固定价位：5 日内触及入场区上沿成交（低开按开盘价），TP1/SL 不随成交价移动；
   成交价已在止损下方时记为 GAP_BELOW_STOP，单独计数，不进入成交样本。
3. 大盘否决（BENCHMARK_WEAK）下的信号单独统计，主指标只用否决后的信号，同时报告否决前对照。
4. 基准：同口径随机入场（信号日随机抽股，同几何、同撮合、同费用）为主；全期随机与超跌随机为诊断。
   区间一律按信号日期分块 bootstrap。

只评估开发区 2021-01-04 ~ 2026-09-24。2020-11-30 及以前是留出集 H1，本脚本拒绝评估任何该区间的结果；
读取 2021 年前的K线只用于指标预热，与线上一致。所有结果均为样本内，不构成验收。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import inspect
import json
import subprocess
import sys
import zlib
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from aggregate import _non_overlapping_episodes, date_block_bootstrap, wilson
from common import ART, BENCHMARKS, CACHE, PIT_UNIVERSE, ROOT, VIX_SYMBOL, adjusted_bars, load_bars, load_cache
from discover_alpha_dev import build_benchmark_features
from run_backtest import WINDOW_BARS, make_pit_slice

from analysis import analyze_bundle  # noqa: E402  (common 已把技能 scripts 加入 sys.path)
from evaluation import label_triple_barrier_event  # noqa: E402
from indicators import atr_series, ema, rsi  # noqa: E402
from market_rules import is_st_instrument, resolve_instrument  # noqa: E402
from risk import HC_SL_ATR, HC_TP1_ATR, high_conviction_plan_levels  # noqa: E402

DEV_REGION = ("2021-01-04", "2026-09-24")
H1_LAST_DAY = "2020-11-30"
# 留出集 H1：各市场首个调仓日（调仓日前已有满 420 根K线，方案第 9 节）至 H1_LAST_DAY
H1_FIRST_DAY = {"CN": "2017-10-24", "HK": "2018-07-09", "US": "2018-06-22"}
H1_UNIVERSE = ART / "pit_universe_h1.json"
# 只有 run_h1_holdout.py 在校验预注册通过后才会把它设为 True；其余任何调用 mode="h1" 都会被拒绝
H1_AUTHORIZED = False
# 与 v1 的切分对齐，便于对照；"all" 为整个开发区
SEGMENTS = {
    "all": DEV_REGION,
    "seg_2021_2024": ("2021-01-04", "2024-12-09"),
    "seg_2025h1": ("2025-01-02", "2025-06-09"),
    "seg_2025h2_2026": ("2025-07-01", "2026-09-24"),
}
WARMUP_BARS = 420
HORIZON = 15
LIMIT_SESSIONS = 5
SEED = 20261020
SAME_DAY_MULTIPLE = 18  # 按"信号日股票池"折算的抽样倍数；扣除不合格日与 episode 去重后约为信号的 12 倍（要求 >= 10 倍）
ALL_DAYS_RATE = 0.02
OVERSOLD_RATE = 0.25
OUT = ART / "dev_region_v2_metrics.json"
GRID_OUT = ART / "dev_region_v2_grid.json"

# 第 4 步调参网格与选择规则（运行前写定，见方案第 8 节）。只调 TP/SL 几何与子类型，不动触发阈值。
GEOMETRIES = [(0.85, 2.20), (0.85, 1.50), (1.25, 2.20), (1.25, 1.50), (1.75, 2.20), (1.75, 1.50)]
DEFAULT_GEOMETRY = (HC_TP1_ATR, HC_SL_ATR)
SUBTYPE_FILTERS = ("ALL", "DEEP_OVERSOLD_TURN", "BENCH_SYNC_REBOUND")
SELECTION_MARKETS = ("CN", "US")  # 港股按 D6 暂停，只作诊断
PARSIMONY_PP = 0.10  # 默认配置得分与最优差距不超过 0.10 个百分点时保留默认
LEDGER = ART / "trial_ledger.jsonl"
LIVE_CACHE_DIR = ART / "v2_live_cache"

_C: dict = {}


def prefilter(market: str, c: float, o: float, h: float, lo: float, raw_c: float, atr: float, e20: float,
              rsi_v: float, bf: dict) -> bool:
    """线上触发条件的宽松超集：阈值各留余量，吸收全历史序列与线上 420 根窗口、快照舍入之间的微小差异。"""
    if atr <= 0 or c <= 0 or raw_c < 1.95 or c <= o or h <= lo:
        return False
    if atr / c < (0.020 if market == "HK" else 0.012) * 0.95:
        return False
    if (c - o) / (h - lo) < 0.31:
        return False
    if (c - e20) / atr > -0.95 and rsi_v > 29.0:
        return False
    if market == "HK":
        return bf["ret1"] >= -1e-6
    return bf["ret1"] >= -1e-6 or bool(bf["bull_bar"])


def geo_key(tp_atr: float, sl_atr: float) -> str:
    return f"tp{tp_atr:.2f}_sl{sl_atr:.2f}"


def _hash_unit(*parts: str) -> float:
    return zlib.crc32("|".join((str(SEED),) + parts).encode()) / 0xFFFFFFFF


def _mode_config(mode: str, market: str) -> tuple[tuple[str, str], Path]:
    """评估区间与股票池：开发区（默认）或留出集 H1。"""
    if mode == "dev":
        return DEV_REGION, PIT_UNIVERSE
    if mode == "h1":
        if not H1_AUTHORIZED:
            raise PermissionError("留出集 H1 只能由 run_h1_holdout.py 在校验预注册后评估")
        return (H1_FIRST_DAY[market], H1_LAST_DAY), H1_UNIVERSE
    raise ValueError(f"unknown mode: {mode}")


def _init(market: str, same_day_rate: dict | None = None, live: dict | None = None, mode: str = "dev") -> None:
    region, universe = _mode_config(mode, market)
    pit = json.loads(universe.read_text(encoding="utf-8"))["markets"][market]
    periods: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in pit["periods"]:
        for s in p["symbols"]:
            periods[s].append((p["start_day"], p["end_day"]))
    bench_payload = load_cache(market, BENCHMARKS[market]["symbol"])
    vix: dict[str, float] = {}
    if market == "US":
        try:
            vix = {b["day"]: float(b["close"]) for b in load_bars("US", VIX_SYMBOL)}
        except Exception:  # noqa: BLE001 - 缺失如实披露为 CACHE_MISS，不补数
            vix = {}
    _C.clear()
    _C.update(
        market=market,
        periods=dict(periods),
        bench=bench_payload["bars"],
        bench_days=[b["day"] for b in bench_payload["bars"]],
        bench_source=bench_payload.get("source", f"CACHED_{market}_BENCHMARK"),
        bf=build_benchmark_features(market),
        vix=vix,
        bench_inst=resolve_instrument(
            BENCHMARKS[market]["symbol"], market=market, asset_type=BENCHMARKS[market]["asset_type"],
            name=BENCHMARKS[market]["name"],
        ),
        same_day_rate=same_day_rate or {},
        live=live or {},
        mode=mode,
        region=region,
        universe=universe,
    )


def _load_symbol(symbol: str) -> dict | None:
    try:
        payload = load_cache(_C["market"], symbol)
        _, bars = adjusted_bars(payload, mode="total_return")
    except Exception:  # noqa: BLE001 - 缓存缺失或损坏的标的跳过（v1 同样处理）
        return None
    if len(bars) < WARMUP_BARS + HORIZON + LIMIT_SESSIONS + 2:
        return None
    closes = [float(b["close"]) for b in bars]
    name = str(payload.get("name") or "")
    return {
        "payload": payload,
        "bars": bars,
        "idx": {b["day"]: i for i, b in enumerate(bars)},
        "c": closes,
        "o": [float(b["open"]) for b in bars],
        "h": [float(b["high"]) for b in bars],
        "l": [float(b["low"]) for b in bars],
        "raw": [float(b.get("raw_close", b["close"])) for b in bars],
        "e20": ema(closes, 20),
        "r14": rsi(closes, 14),
        "a14": atr_series(bars, 14),
        "name": name,
        "is_st": is_st_instrument(symbol, name=name),
    }


def _eligible_days(symbol: str, data: dict):
    """开发区内、属于 PIT 股票池、有足够预热与成熟期的交易日，返回 (day, t, b_pos)。"""
    n = len(data["bars"])
    bench_days = _C["bench_days"]
    for s_d, e_d in _C["periods"].get(symbol, []):
        lo, hi = max(s_d, _C["region"][0]), min(e_d, _C["region"][1])
        if lo > hi:
            continue
        for b_pos in range(bisect_left(bench_days, lo), len(bench_days)):
            day = bench_days[b_pos]
            if day > hi:
                break
            if day not in _C["bf"]:
                continue
            if _C["mode"] == "dev":
                assert day > H1_LAST_DAY, "留出集 H1 不得在开发区管线中评估"
            else:
                assert day <= H1_LAST_DAY, "H1 评估不得读取开发区的结果"
            t = data["idx"].get(day)
            if t is None or t < WARMUP_BARS - 1 or t + LIMIT_SESSIONS + HORIZON >= n:
                continue
            if b_pos + 1 >= len(bench_days) or data["bars"][t + 1]["day"] > bench_days[b_pos + 1]:
                continue  # 次日停牌：与 v1 相同，跳过
            yield day, t, b_pos


def build_bundle(symbol: str, data: dict, pit: list[dict], cut: int, day: str) -> tuple[dict, dict]:
    """与 run_backtest 相同的 PIT bundle：标的与基准都只到 day。"""
    market = _C["market"]
    b_hi = bisect_right(_C["bench_days"], day)
    vix_value = _C["vix"].get(day)
    inst = resolve_instrument(symbol, market=market, asset_type="equity", name=data["name"], is_st=data["is_st"])
    payload = data["payload"]
    bundle = {
        "schema_version": "5.3.0",
        "market": market,
        "target": {"instrument": inst, "bars": pit[: cut + 1], "intraday": None},
        "benchmark": {
            "instrument": _C["bench_inst"],
            "bars": _C["bench"][max(0, b_hi - WINDOW_BARS):b_hi],
            "source": _C["bench_source"],
            "errors": [],
        },
        "extra_context": {
            "vix": vix_value,
            "vix_source": f"CACHE(^VIX,{day})" if vix_value is not None else "CACHE_MISS",
            "earnings_date": None,
            "earnings_date_source": "NOT_AVAILABLE",
            "is_st": data["is_st"],
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
    request = {
        "symbol": symbol, "market": market, "asset_type": "equity", "name": data["name"], "is_st": data["is_st"],
        "display_bars": 120, "horizon_bars": HORIZON, "account_equity": None, "adjustment": "total_return",
    }
    return bundle, request


def live_high_conviction(symbol: str, data: dict, t: int, day: str) -> dict:
    """对一个个股-日调用线上流水线，返回反转层结论（含被大盘否决抑制的子类型）与计划价位。"""
    pit, cut = make_pit_slice(data["bars"], t, HORIZON)
    bundle, request = build_bundle(symbol, data, pit, cut, day)
    rep = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)
    ml = rep["meta_labeling_decision"]
    plan = rep["triple_barrier_plan"]["high_conviction_plan"]
    return {
        "pit": pit,
        "cut": cut,
        "subtype": ml.get("high_conviction_subtype"),
        "suppressed_subtype": ml.get("high_conviction_suppressed_subtype"),
        # 港股按 D6 暂停：线上只记录子类型，回测照常评估作诊断
        "paused_subtype": ml.get("high_conviction_paused_subtype"),
        # 线上计划所用 ATR（快照值，下限为收盘价的 0.5%），网格中其他几何沿用同一 ATR
        "atr_live": max(float(pit[cut]["close"]) * 0.005, float(rep["mathematical_indicators"]["volatility"]["atr14"])),
        "veto_codes": ml.get("veto_codes") or [],
        "regime_score": rep["market_benchmark"].get("regime_score"),
        "entry_zone": [float(x) for x in plan["entry_zone"]],
        "take_profit_1": float(plan["take_profit_1"]),
        "stop_loss": float(plan["stop_loss_barrier"]),
    }


def execute_plan(symbol: str, data: dict, pit: list[dict], cut: int, b_pos: int,
                 entry_high: float, tp: float, sl: float, open_layer: bool = True) -> dict:
    """按固定价位撮合：次日开盘层（诊断，open_layer=False 时跳过）与 5 日限价成交层（主口径）。"""
    market = _C["market"]
    kw = {"horizon_bars": HORIZON, "market": market, "asset_type": "equity", "symbol": symbol,
          "is_st": data["is_st"], "trailing_stop": False, "name": data["name"]}
    out: dict = {}
    op = float(pit[cut + 1]["open"])
    if not open_layer:
        pass
    elif sl < op < tp:
        r = label_triple_barrier_event(pit, entry_idx=cut + 1, tp_price=tp, sl_price=sl, **kw)
        out["open_status"] = "FILL" if r.get("tradable") else f"NOT_TRADABLE:{r.get('reason')}"
        if r.get("tradable"):
            out.update(open_net=float(r["net_return"]), open_outcome=r["outcome"])
    else:
        out["open_status"] = "OPEN_OUTSIDE_PLAN"

    bench_days = _C["bench_days"]
    max_day = bench_days[min(len(bench_days) - 1, b_pos + LIMIT_SESSIONS)]
    out["fill_status"] = "NO_FILL_5D"
    for k in range(cut + 1, min(len(pit) - 1, cut + LIMIT_SESSIONS) + 1):
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


def _grid_entry(ex: dict, geometry: tuple[float, float]) -> dict:
    """默认几何保留完整撮合结果；其余几何只留汇总需要的字段，控制内存。"""
    if geometry == DEFAULT_GEOMETRY:
        return ex
    return {k: ex[k] for k in ("fill_status", "net", "outcome") if k in ex}


def _live_scan(symbol: str) -> tuple[list[dict], int]:
    """预筛候选逐个调用线上流水线（整个管线最耗时的一步），只返回触发了反转条件的记录。结果与几何无关，可缓存。"""
    data = _load_symbol(symbol)
    if data is None:
        return [], 0
    market = _C["market"]
    records: list[dict] = []
    n_candidates = 0
    for day, t, b_pos in _eligible_days(symbol, data):
        if not prefilter(market, data["c"][t], data["o"][t], data["h"][t], data["l"][t], data["raw"][t],
                         float(data["a14"][t] or 0.0), float(data["e20"][t] or data["c"][t]),
                         float(data["r14"][t] or 50.0), _C["bf"][day]):
            continue
        n_candidates += 1
        live = live_high_conviction(symbol, data, t, day)
        if (live["subtype"] or live["suppressed_subtype"] or live["paused_subtype"]) is None:
            continue
        records.append({"day": day, "t": t, "b_pos": b_pos,
                        **{k: v for k, v in live.items() if k not in ("pit", "cut")}})
    return records, n_candidates


def _execute_signals(symbol: str, data: dict, records: list[dict]) -> list[dict]:
    """按线上记录的价位撮合；网格的其他几何沿用线上 ATR，默认几何与线上计划逐笔核对。"""
    market = _C["market"]
    rows: list[dict] = []
    for rec in records:
        day = rec["day"]
        pit, cut = make_pit_slice(data["bars"], rec["t"], HORIZON)
        c_pit = float(pit[cut]["close"])
        geo: dict[str, dict] = {}
        for tp_atr, sl_atr in GEOMETRIES:
            _, entry_high, tp, sl = high_conviction_plan_levels(c_pit, rec["atr_live"], market, "equity", day, tp_atr, sl_atr)
            if (tp_atr, sl_atr) == DEFAULT_GEOMETRY and [entry_high, tp, sl] != [rec["entry_zone"][1], rec["take_profit_1"], rec["stop_loss"]]:
                raise RuntimeError(f"网格默认几何与线上计划不一致：{symbol} {day}")
            geo[geo_key(tp_atr, sl_atr)] = _grid_entry(
                execute_plan(symbol, data, pit, cut, rec["b_pos"], entry_high, tp, sl,
                             open_layer=(tp_atr, sl_atr) == DEFAULT_GEOMETRY),
                (tp_atr, sl_atr),
            )
        rows.append({
            "symbol": symbol, "day": day,
            "subtype": rec["subtype"] or rec["suppressed_subtype"] or rec["paused_subtype"],
            "vetoed": rec["suppressed_subtype"] is not None,
            "regime_score": rec["regime_score"],
            "entry_zone": rec["entry_zone"], "tp": rec["take_profit_1"], "sl": rec["stop_loss"],
            **geo[geo_key(*DEFAULT_GEOMETRY)],
            "geo": geo,
        })
    return rows


def _scan_signals(symbol: str) -> tuple[list[dict], int]:
    """单只标的：调用线上流水线并撮合（测试与冒烟用；全量运行走缓存 + _scan_symbol）。"""
    records, n_candidates = _live_scan(symbol)
    data = _load_symbol(symbol) if records else None
    return (_execute_signals(symbol, data, records) if data else []), n_candidates


def _scan_symbol(symbol: str) -> tuple[list[dict], list[dict]]:
    """第二遍：同一次加载里撮合缓存的线上信号并抽取三类随机基准。"""
    data = _load_symbol(symbol)
    if data is None:
        return [], []
    return _execute_signals(symbol, data, _C["live"].get(symbol, [])), _baselines_for(symbol, data)


def _baselines_for(symbol: str, data: dict) -> list[dict]:
    """同口径随机（信号日）、全期随机与超跌随机：用全历史序列的 ATR 按 PIT 比例换算，价位规则与线上相同。"""
    market = _C["market"]
    rows: list[dict] = []
    for day, t, b_pos in _eligible_days(symbol, data):
        u = _hash_unit(market, symbol, day)
        kinds = []
        rate = _C["same_day_rate"].get(day)
        if rate is not None and u < rate:
            kinds.append("same_day")
        if _hash_unit("all", market, symbol, day) < ALL_DAYS_RATE:
            kinds.append("all_days")
        atr = float(data["a14"][t] or 0.0)
        c = data["c"][t]
        if atr > 0 and (c - float(data["e20"][t] or c)) / atr <= -1.0 and _hash_unit("os", market, symbol, day) < OVERSOLD_RATE:
            kinds.append("oversold")
        if not kinds or atr <= 0:
            continue
        pit, cut = make_pit_slice(data["bars"], t, HORIZON)
        c_pit = float(pit[cut]["close"])
        atr_pit = max(c_pit * 0.005, atr * (c_pit / c))
        geo: dict[str, dict] = {}
        for tp_atr, sl_atr in GEOMETRIES:
            _, entry_high, tp, sl = high_conviction_plan_levels(c_pit, atr_pit, market, "equity", day, tp_atr, sl_atr)
            geo[geo_key(tp_atr, sl_atr)] = _grid_entry(
                execute_plan(symbol, data, pit, cut, b_pos, entry_high, tp, sl,
                             open_layer=(tp_atr, sl_atr) == DEFAULT_GEOMETRY),
                (tp_atr, sl_atr),
            )
        for kind in kinds:
            rows.append({"kind": kind, "symbol": symbol, "day": day, **geo[geo_key(*DEFAULT_GEOMETRY)], "geo": geo})
    return rows


def _worker_init(h1_authorized: bool, *init_args) -> None:
    """子进程会重新导入本模块，H1 授权需要由父进程显式传入。"""
    global H1_AUTHORIZED
    H1_AUTHORIZED = h1_authorized
    _init(*init_args)


def _pool_map(fn, symbols: list[str], workers: int, init_args: tuple) -> list:
    if workers <= 1:
        _init(*init_args)
        return [fn(s) for s in symbols]
    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init, initargs=(H1_AUTHORIZED, *init_args)) as ex:
        return list(ex.map(fn, symbols, chunksize=8))


def _pct(x: float | None, digits: int = 2) -> float | None:
    return None if x is None else round(x * 100.0, digits)


def _ci_pct(ci: list[float] | None) -> list[float] | None:
    return None if ci is None else [round(ci[0] * 100.0, 2), round(ci[1] * 100.0, 2)]


def _layer_stats(eps: list[dict]) -> dict:
    n = len(eps)
    if n == 0:
        return {"n_episodes": 0}
    wins = sum(1 for r in eps if r["net"] > 0)
    tp = sum(1 for r in eps if r["outcome"] == "TP_BARRIER")
    sl = sum(1 for r in eps if str(r["outcome"]).startswith("SL_BARRIER"))
    days = Counter(r["day"] for r in eps)
    return {
        "n_episodes": n,
        "n_signal_days": len(days),
        "max_day_share_pct": round(max(days.values()) / n * 100.0, 2),
        "net_positive_rate_pct": round(wins / n * 100.0, 2),
        "wilson95_pct": wilson(wins, n),
        "date_cluster95_pct": _ci_pct(date_block_bootstrap(eps, lambda r: r["net"] > 0)),
        "mean_net_return_pct": _pct(sum(r["net"] for r in eps) / n, 3),
        "mean_date_cluster95_pct": _ci_pct(date_block_bootstrap(eps, lambda r: r["net"])),
        "tp_first_rate_pct": round(tp / max(1, tp + sl) * 100.0, 2),
        "outcomes": dict(Counter(r["outcome"] for r in eps)),
    }


def _fill_eps(rows: list[dict]) -> list[dict]:
    return _non_overlapping_episodes([r for r in rows if r.get("fill_status") == "FILL"])


def summarize(signals: list[dict], baselines: list[dict], lo: str, hi: str) -> dict:
    sig = [r for r in signals if lo <= r["day"] <= hi]
    base = [r for r in baselines if lo <= r["day"] <= hi]
    kept = [r for r in sig if not r["vetoed"]]
    eps = _fill_eps(kept)
    main = _layer_stats(eps)
    status = Counter(r["fill_status"] for r in kept)
    kept_days = {r["day"] for r in kept}
    by_kind = {k: _fill_eps([r for r in base if r["kind"] == k and (k != "same_day" or r["day"] in kept_days)])
               for k in ("same_day", "all_days", "oversold")}
    same = by_kind["same_day"]
    out: dict = {
        "n_signals_pre_veto": len(sig),
        "n_vetoed_benchmark_weak": len(sig) - len(kept),
        "n_signals": len(kept),
        "subtypes": dict(Counter(r["subtype"] for r in kept)),
        "fill_status": dict(status),
        "fill_layer": main,
        "next_open_layer": {
            "n": sum(1 for r in kept if r.get("open_status") == "FILL"),
            "outside_plan": sum(1 for r in kept if r.get("open_status") == "OPEN_OUTSIDE_PLAN"),
            "net_positive_rate_pct": _pct(
                sum(1 for r in kept if r.get("open_status") == "FILL" and r["open_net"] > 0)
                / max(1, sum(1 for r in kept if r.get("open_status") == "FILL"))
            ),
        },
        "pre_veto_fill_layer": _layer_stats(_fill_eps(sig)),
        "baselines": {k: _layer_stats(v) for k, v in by_kind.items()},
    }
    if eps and same:
        rate_ci = date_block_bootstrap(eps, lambda r: r["net"] > 0, same)
        mean_ci = date_block_bootstrap(eps, lambda r: r["net"], same)
        b = out["baselines"]["same_day"]
        out["excess_vs_same_day_random"] = {
            "baseline_to_signal_ratio": round(len(same) / len(eps), 1),
            "rate_pp": round(main["net_positive_rate_pct"] - b["net_positive_rate_pct"], 2),
            "rate_pp_date_cluster95": _ci_pct(rate_ci),
            "mean_pct": round(main["mean_net_return_pct"] - b["mean_net_return_pct"], 3),
            "mean_pct_date_cluster95": _ci_pct(mean_ci),
        }
    if main.get("n_episodes") and main["max_day_share_pct"] > 10.0:
        top_day = Counter(r["day"] for r in eps).most_common(1)[0][0]
        rest = [r for r in eps if r["day"] != top_day]
        out["sensitivity_excluding_top_day"] = {"excluded_day": top_day, **_layer_stats(rest)}
    ex = out.get("excess_vs_same_day_random") or {}
    mean_ci = main.get("mean_date_cluster95_pct")
    out["criteria_diagnostic"] = {
        "note": "开发区·样本内，仅用于诊断，不构成验收",
        "P1_mean_net_positive": bool(mean_ci and main["mean_net_return_pct"] > 0 and mean_ci[0] > 0),
        "P2a_rate_excess": bool(ex.get("rate_pp_date_cluster95") and ex["rate_pp_date_cluster95"][0] > 0),
        "P2b_mean_excess": bool(ex.get("mean_pct_date_cluster95") and ex["mean_pct_date_cluster95"][0] > 0),
        "P3_sample": bool(main.get("n_episodes", 0) >= 100 and main.get("n_signal_days", 0) >= 30),
        "T_rate_ge_70": bool(main.get("n_episodes") and main["net_positive_rate_pct"] >= 70.0),
    }
    return out


def _live_cache_key(market: str, symbols: list[str]) -> str:
    """线上流水线结果的缓存键：技能全部脚本、研究侧行情加载与 PIT 切片、本管线中决定候选与调用方式的函数、
    区间参数、股票池与行情缓存清单。
    任何一项变化都会让缓存失效并重新调用线上流水线。"""
    h = hashlib.sha256()
    # 研究侧的行情加载（common.py：复权、占位K线剔除）与 PIT 切片（run_backtest.py）也决定线上流水线看到的K线
    for f in [*sorted((ROOT / "kline-structure-chart" / "scripts").glob("*.py")),
              ROOT / "research" / "common.py", ROOT / "research" / "run_backtest.py"]:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    for fn in (prefilter, _eligible_days, _load_symbol, build_bundle, live_high_conviction, _live_scan):
        h.update(inspect.getsource(fn).encode())
    h.update(json.dumps([_C["mode"], market, _C["region"], WARMUP_BARS, HORIZON, LIMIT_SESSIONS, symbols]).encode())
    h.update(_C["universe"].read_bytes())
    h.update((CACHE / "MANIFEST.json").read_bytes())
    return h.hexdigest()


def _load_or_build_live(market: str, symbols: list[str], workers: int, refresh: bool) -> tuple[dict, int, dict]:
    key = _live_cache_key(market, symbols)
    mode = _C["mode"]
    path = LIVE_CACHE_DIR / f"{mode}_{market}_{key[:12]}.json.gz"  # 键进文件名：冒烟（抽样标的）与全量互不覆盖
    if not refresh and path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("key") == key:
            return cached["records"], int(cached["n_candidates"]), {"hit": True, "key": key[:16]}
    _init(market, mode=mode)
    results = _pool_map(_live_scan, symbols, workers, (market, None, None, mode))
    live = {sym: recs for sym, (recs, _) in zip(symbols, results) if recs}
    n_candidates = sum(n for _, n in results)
    LIVE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({"key": key, "market": market, "n_candidates": n_candidates, "records": live}, fh, ensure_ascii=False)
    return live, n_candidates, {"hit": False, "key": key[:16]}


def run_market(market: str, workers: int, limit_symbols: int | None = None, grid: bool = False,
               refresh_live: bool = False, mode: str = "dev") -> dict:
    _init(market, mode=mode)
    segments = SEGMENTS if mode == "dev" else {"all": _C["region"]}
    symbols = sorted(_C["periods"])
    if limit_symbols:
        symbols = symbols[:: max(1, len(symbols) // limit_symbols)][:limit_symbols]
    live, n_candidates, cache_status = _load_or_build_live(market, symbols, workers, refresh_live)

    # 同口径随机：只在（否决后）信号日抽样，抽样率按"当日股票池规模"折算到信号数的 SAME_DAY_MULTIPLE 倍
    kept_days = Counter(rec["day"] for recs in live.values() for rec in recs if rec["suppressed_subtype"] is None)
    day_list = sorted(kept_days)
    total_pool = 0  # 信号日上的股票池"个股-日"总数
    for sym in symbols:
        for s_d, e_d in _C["periods"].get(sym, []):
            total_pool += bisect_right(day_list, e_d) - bisect_left(day_list, s_d)
    rate = min(1.0, SAME_DAY_MULTIPLE * sum(kept_days.values()) / max(1, total_pool))
    same_day_rate = {d: rate for d in kept_days}
    results = _pool_map(_scan_symbol, symbols, workers, (market, same_day_rate, live, mode))
    signals = [r for sig, _ in results for r in sig]
    baselines = [r for _, base in results for r in base]

    out = {
        "n_symbols_scanned": len(symbols),
        "n_prefilter_candidates": n_candidates,
        "live_cache": cache_status,
        "same_day_sampling_rate": round(rate, 4),
        "region": list(_C["region"]),
        "segments": {name: summarize(signals, baselines, lo, hi) for name, (lo, hi) in segments.items()},
    }
    if grid:
        out["grid"] = {}
        for tp_atr, sl_atr in GEOMETRIES:
            gk = geo_key(tp_atr, sl_atr)
            g_base = [{**r, **_flat(r, gk)} for r in baselines]
            for sub in SUBTYPE_FILTERS:
                g_sig = [{**r, **_flat(r, gk)} for r in signals if sub == "ALL" or r["subtype"] == sub]
                out["grid"][f"{gk}|{sub}"] = {
                    name: summarize(g_sig, g_base, lo, hi) for name, (lo, hi) in segments.items()
                }
    return out


def _flat(row: dict, gk: str) -> dict:
    """取某个几何的撮合结果覆盖行上的默认结果；缺失的字段（如成交失败时的 net）显式置空。"""
    g = row["geo"][gk]
    return {"fill_status": g.get("fill_status"), "net": g.get("net"), "outcome": g.get("outcome")}


def select_config(grid_results: dict) -> dict:
    """方案第 8 节写定的选择规则：在 CN、US 全开发区都满足 P1 与 P3 的配置中，取两市场 P2b 下限的较小值最大者；
    默认配置得分与最优差距不超过 PARSIMONY_PP 时保留默认。"""
    default_key = f"{geo_key(*DEFAULT_GEOMETRY)}|ALL"
    scores: dict[str, float | None] = {}
    for key in grid_results[SELECTION_MARKETS[0]]:
        lows = []
        for m in SELECTION_MARKETS:
            seg = grid_results[m][key]["all"]
            crit = seg["criteria_diagnostic"]
            ci = (seg.get("excess_vs_same_day_random") or {}).get("mean_pct_date_cluster95")
            if not (crit["P1_mean_net_positive"] and crit["P3_sample"]) or ci is None:
                lows = None
                break
            lows.append(ci[0])
        scores[key] = None if lows is None else round(min(lows), 3)
    eligible = {k: v for k, v in scores.items() if v is not None}
    if not eligible:
        return {"rule": "见方案第 8 节", "scores_pct": scores, "selected": None, "reason": "没有配置在 CN、US 同时满足 P1 与 P3"}
    best = max(eligible, key=eligible.get)
    keep_default = default_key in eligible and eligible[default_key] >= eligible[best] - PARSIMONY_PP
    return {
        "rule": "见方案第 8 节",
        "scores_pct": scores,
        "best": best,
        "selected": default_key if keep_default else best,
        "kept_default_by_parsimony": keep_default and best != default_key,
    }


def _git_head() -> str:
    """当前提交号；工作区有未提交改动时加 -dirty，台账据此判断结果能否按提交号复现。"""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "research", "kline-structure-chart/scripts"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="CN,HK,US")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit-symbols", type=int, default=None, help="冒烟测试：每市场只取等间隔抽样的 N 只")
    ap.add_argument("--grid", action="store_true", help="第 4 步：同时评估方案第 8 节写定的几何 × 子类型网格并按规则选择")
    ap.add_argument("--refresh-live-cache", action="store_true", help="忽略缓存，重新调用线上流水线")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    args.out = args.out or (GRID_OUT if args.grid else OUT)

    result: dict = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": _git_head(),
        "dev_region": DEV_REGION,
        "h1_holdout_last_day_untouched": H1_LAST_DAY,
        "in_sample_note": "开发区结果均为样本内，不构成验收",
        "config": {"limit_sessions": LIMIT_SESSIONS, "horizon": HORIZON, "seed": SEED,
                   "same_day_multiple": SAME_DAY_MULTIPLE, "all_days_rate": ALL_DAYS_RATE,
                   "oversold_rate": OVERSOLD_RATE, "limit_symbols": args.limit_symbols,
                   "grid": {"geometries": GEOMETRIES, "subtype_filters": SUBTYPE_FILTERS,
                            "selection_markets": SELECTION_MARKETS, "parsimony_pp": PARSIMONY_PP} if args.grid else None},
        "markets": {},
    }
    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        result["markets"][market] = run_market(market, args.workers, args.limit_symbols, grid=args.grid,
                                               refresh_live=args.refresh_live_cache)
        seg = result["markets"][market]["segments"]["all"]
        fl = seg["fill_layer"]
        print(f"[{market}] pre_veto={seg['n_signals_pre_veto']} vetoed={seg['n_vetoed_benchmark_weak']} "
              f"episodes={fl.get('n_episodes')} days={fl.get('n_signal_days')} rate={fl.get('net_positive_rate_pct')}% "
              f"mean={fl.get('mean_net_return_pct')}% excess={seg.get('excess_vs_same_day_random')}", flush=True)
    if args.grid and all(m in result["markets"] for m in SELECTION_MARKETS):
        result["selection"] = select_config({m: result["markets"][m]["grid"] for m in SELECTION_MARKETS})
        print(f"selection: {result['selection']['selected']} (best={result['selection'].get('best')})", flush=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": result["generated_at_utc"], "script": "research/run_dev_region_v2.py",
            "code_commit": result["code_commit"], "config": result["config"], "out": str(args.out.relative_to(ROOT)) if args.out.is_relative_to(ROOT) else str(args.out),
            "n_configs_evaluated": len(GEOMETRIES) * len(SUBTYPE_FILTERS) if args.grid else 1,
            "selection": (result.get("selection") or {}).get("selected"),
            "summary": {m: {k: v["segments"]["all"]["fill_layer"].get(k) for k in
                            ("n_episodes", "net_positive_rate_pct", "mean_net_return_pct")}
                        for m, v in result["markets"].items()},
        }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
