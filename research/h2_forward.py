#!/usr/bin/env python3
"""前向跟踪 H2（回测与验收口径调整方案第 8 步，D11~D13）。

每个交易日收盘后运行一次 `daily`：
  1. refresh：增量刷新行情（新浪最近 40 根、Yahoo 最近 3 个月，与缓存重叠日逐日核对；对不上——例如新发生拆股——
     就对该标的整段重抓）。平时只刷新当期股票池成分股、基准与 VIX；到调仓日刷新全部标的以便重新排名。
  2. rebalance：每 60 个基准交易日按开发区同一规则（调仓日前 60 日平均成交额，前 1,500 只）调仓。
  3. log：只处理每个市场最近一个已收盘交易日 D。对股票池预筛候选调用线上流水线，把触发的信号（含被否决、被暂停的子类型）
     与计划价位、以及当日的同日随机基准样本追加写入日志，记录写入时间。错过的交易日只记缺失，不补跑。
  4. score：满 5 + 15 个交易日后，按日志里的固定价位撮合并追加写入结果。
  5. status：只报告信号数、已到期 episode 数与信号日数，不报告任何收益指标（D12=a，避免反复查看）。

`evaluate` 只有在 CN、US 都达到 P3（已到期 episode >= 100 且信号日 >= 30）后才允许运行，且只能成功运行一次。
所有子命令运行前都校验 research/preregistration/preregistration_h2.json（代码被改动就拒绝运行）。
资源：默认计算进程 2 个、抓数线程 4 个，适配 2 核 4GB 的服务器。
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
import urllib.parse
from bisect import bisect_left, bisect_right
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from aggregate import _non_overlapping_episodes
from common import ART, BENCHMARKS, ROOT, VIX_SYMBOL, load_bars

import history_cache as hc  # noqa: E402
import run_dev_region_v2 as v2  # noqa: E402
from contracts import validate_bars  # noqa: E402
from risk import high_conviction_plan_levels  # noqa: E402

H2_DIR = ART / "h2"
SIGNALS = H2_DIR / "signals.jsonl"
BASELINE = H2_DIR / "baseline.jsonl"
OUTCOMES = H2_DIR / "outcomes.jsonl"
GAPS = H2_DIR / "gaps.jsonl"
RUNS = H2_DIR / "runs.jsonl"
RESULT = H2_DIR / "h2_result.json"
PREREG_H2 = ROOT / "research" / "preregistration" / "preregistration_h2.json"
MARKETS = ("CN", "US", "HK")
REBALANCE_SESSIONS = 60
TOP_N = 1500
SINA_RECENT_BARS = 40
YAHOO_RECENT_RANGE = "3mo"
P3_MIN_EPISODES, P3_MIN_DAYS = 100, 30


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 预注册校验
# ---------------------------------------------------------------------------

def check_preregistration() -> dict:
    import run_h1_holdout as h1

    if not PREREG_H2.exists():
        raise SystemExit("缺少 research/preregistration/preregistration_h2.json，H2 尚未冻结")
    prereg, problems = h1.verify_preregistration(PREREG_H2)
    if problems:
        raise SystemExit("H2 预注册校验未通过，拒绝运行：\n- " + "\n- ".join(problems))
    return prereg


# ---------------------------------------------------------------------------
# 1. 增量刷新行情
# ---------------------------------------------------------------------------

def fetch_sina_recent(symbol: str, n: int = SINA_RECENT_BARS) -> list[dict]:
    url = (hc.SINA + "CN_MarketData.getKLineData?"
           f"symbol={hc.sina_code(symbol)}&scale=240&ma=no&datalen={n}")
    rows = json.loads(hc.http_get(url, "money.finance.sina.com.cn", 0.15))
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("Sina recent daily unavailable")
    return [{"day": str(r["day"])[:10], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
             "close": float(r["close"]), "volume": float(r.get("volume") or 0.0), "complete": True} for r in rows]


def fetch_yahoo_recent(y_sym: str, market: str) -> tuple[list[dict], list[dict]]:
    encoded = urllib.parse.quote(y_sym, safe="^.-")
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}"
           f"?interval=1d&range={YAHOO_RECENT_RANGE}&events=div%2Csplits")
    return hc._parse_yahoo(json.loads(hc.http_get(url, "query1.finance.yahoo.com", 0.25)), market)


def merge_bars(old: list[dict], new: list[dict], rel_tol: float = 1e-4) -> list[dict] | None:
    """新窗口必须与缓存有重叠，且重叠日的收盘价一致；否则返回 None，调用方整段重抓（不拼接可能不同口径的数据）。"""
    if not old or not new:
        return None
    old_by_day = {b["day"]: b for b in old}
    overlap = [b for b in new if b["day"] in old_by_day]
    if not overlap or new[0]["day"] > old[-1]["day"]:
        return None
    for b in overlap:
        oc = float(old_by_day[b["day"]]["close"])
        if abs(float(b["close"]) - oc) > rel_tol * max(abs(oc), 1e-9):
            return None
    return [b for b in old if b["day"] < new[0]["day"]] + new


def _merge_events(old: list[dict], new: list[dict]) -> list[dict]:
    seen = {(e["ex_date"], e["split_ratio"], e["dividend"]) for e in old}
    return sorted(old + [e for e in new if (e["ex_date"], e["split_ratio"], e["dividend"]) not in seen],
                  key=lambda e: e["ex_date"])


def _write_cache(path: Path, old: dict, bars: list[dict], events: list[dict], dropped: str | None) -> None:
    payload = {**old, "fetched_at_utc": _now(), "first_day": bars[0]["day"], "last_day": bars[-1]["day"],
               "dropped_incomplete_day": dropped, "bars": bars, "events": events}
    canonical = json.dumps({"bars": bars, "events": events}, sort_keys=True, separators=(",", ":"))
    payload["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    tmp = path.with_suffix(".json.gz.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)


def refresh_symbol(row: dict) -> str:
    """返回 incremental / full / error:<原因>。失败只记录并跳过，绝不补数。"""
    symbol, market = row["symbol"], row["market"]
    path = hc.cache_path(market, symbol)
    try:
        if not path.exists():
            hc.cache_one(row, force=True)
            return "full"
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            old = json.load(fh)
        new_events: list[dict] = []
        if market == "CN":
            new = fetch_sina_recent(symbol)
            if row.get("asset_type", "equity") == "equity":
                try:
                    _, new_events = fetch_yahoo_recent(hc.yahoo_events_symbol_for(symbol), "CN")
                except ValueError:
                    new_events = []
        else:
            y_sym = "^VIX" if symbol == VIX_SYMBOL else hc.yahoo_symbol_for(symbol)
            new, new_events = fetch_yahoo_recent(y_sym, market)
        new, dropped = hc.drop_incomplete_tail(new, market)
        merged = merge_bars(old["bars"], new)
        old_splits = {e["ex_date"] for e in old.get("events") or [] if e["split_ratio"] != 1.0}
        new_split = any(e["split_ratio"] != 1.0 and e["ex_date"] not in old_splits for e in new_events)
        if merged is None or (market != "CN" and new_split):
            hc.cache_one(row, force=True)
            return "full"
        validate_bars(merged, f"h2:{symbol}")
        _write_cache(path, old, merged, _merge_events(old.get("events") or [], new_events), dropped)
        return "incremental"
    except Exception as exc:  # noqa: BLE001 - 失败如实记录，不补数
        return f"error:{type(exc).__name__}: {exc}"


def _universe_rows() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    with hc.UNIVERSE.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["list_eligible"] == "1":
                rows[f"{r['market']}|{r['symbol']}"] = r
    for r in hc.BENCH_ROWS:
        rows[f"{r['market']}|{r['symbol']}"] = r
    return rows


def refresh(keys: list[str], threads: int) -> Counter:
    rows = _universe_rows()
    todo = [rows[k] for k in keys if k in rows]
    with ThreadPoolExecutor(max_workers=threads) as ex:
        statuses = list(ex.map(refresh_symbol, todo))
    errors = [(r["symbol"], s) for r, s in zip(todo, statuses) if s.startswith("error")]
    for sym, s in errors[:20]:
        print(f"  refresh {sym}: {s}", flush=True)
    return Counter(s.split(":")[0] for s in statuses)


# ---------------------------------------------------------------------------
# 2. 股票池（延续开发区股票池的调仓节奏）
# ---------------------------------------------------------------------------

def load_universe() -> dict:
    if v2.H2_UNIVERSE.exists():
        return json.loads(v2.H2_UNIVERSE.read_text(encoding="utf-8"))
    dev = json.loads(v2.PIT_UNIVERSE.read_text(encoding="utf-8"))
    out = {"schema_version": "1.0", "ranking": dev["ranking"], "survivorship_bias": dev["survivorship_bias"],
           "markets": {}}
    for m, info in dev["markets"].items():
        last = info["periods"][-1]
        out["markets"][m] = {"periods": [{**{k: last[k] for k in ("rebalance_day", "start_day", "symbols")},
                                          "end_day": "9999-12-31", "source": "pit_universe.json 最后一期"}]}
    return out


def save_universe(uni: dict) -> None:
    v2.H2_UNIVERSE.parent.mkdir(parents=True, exist_ok=True)
    v2.H2_UNIVERSE.write_text(json.dumps(uni, ensure_ascii=False), encoding="utf-8")


def rebalance_due(uni: dict, market: str, bench_days: list[str], day: str) -> bool:
    last = uni["markets"][market]["periods"][-1]["rebalance_day"]
    return bisect_left(bench_days, day) - bisect_left(bench_days, last) >= REBALANCE_SESSIONS


def rebalance(uni: dict, market: str, bench_days: list[str], day: str) -> None:
    from build_pit_universe import build_market

    info = build_market(market, day, day, TOP_N, REBALANCE_SESSIONS)
    periods = uni["markets"][market]["periods"]
    prev_day = bench_days[bisect_left(bench_days, day) - 1]
    periods[-1]["end_day"] = prev_day
    p = info["periods"][0]
    periods.append({"rebalance_day": day, "start_day": day, "end_day": "9999-12-31", "symbols": p["symbols"],
                    "source": f"h2_rebalance@{_now()}"})


def pool_on(uni: dict, market: str, day: str) -> list[str]:
    for p in reversed(uni["markets"][market]["periods"]):
        if p["start_day"] <= day <= p["end_day"]:
            return p["symbols"]
    return []


# ---------------------------------------------------------------------------
# 3. 记日志（只处理最近一个已收盘交易日）
# ---------------------------------------------------------------------------

def _log_symbol(args: tuple[str, str]) -> dict | None:
    symbol, day = args
    data = v2._load_symbol(symbol)
    if data is None or data["bars"][-1]["day"] != day:
        return None
    t = len(data["bars"]) - 1
    if t < v2.WARMUP_BARS - 1:
        return None
    market = v2._C["market"]
    arrays_ok = v2.prefilter(market, data["c"][t], data["o"][t], data["h"][t], data["l"][t], data["raw"][t],
                             float(data["a14"][t] or 0.0), float(data["e20"][t] or data["c"][t]),
                             float(data["r14"][t] or 50.0), v2._C["bf"][day])
    out: dict = {"symbol": symbol, "candidate": bool(arrays_ok)}
    if arrays_ok:
        live = v2.live_high_conviction(symbol, data, t, day)
        raw = live["subtype"] or live["suppressed_subtype"] or live["paused_subtype"]
        if raw is not None:
            out["signal"] = {"subtype": raw, "vetoed": live["suppressed_subtype"] is not None,
                             "regime_score": live["regime_score"], "entry_zone": live["entry_zone"],
                             "tp": live["take_profit_1"], "sl": live["stop_loss"], "atr_live": live["atr_live"]}
    atr = float(data["a14"][t] or 0.0)
    if atr > 0:
        c = data["c"][t]
        pit, cut = v2.make_pit_slice(data["bars"], t, v2.HORIZON)
        c_pit = float(pit[cut]["close"])
        atr_pit = max(c_pit * 0.005, atr * (c_pit / c))
        _, eh, tp, sl = high_conviction_plan_levels(c_pit, atr_pit, market, "equity", day)
        out["baseline_levels"] = {"entry_high": eh, "tp": tp, "sl": sl}
    return out


def log_market(market: str, uni: dict, workers: int, prereg_sha: str, commit: str) -> dict:
    bench = load_bars(market, BENCHMARKS[market]["symbol"])
    bench_days = [b["day"] for b in bench]
    day = bench_days[-1]
    if day < v2.H2_FIRST_DAY:
        return {"market": market, "day": day, "status": "BEFORE_H2_FIRST_DAY"}
    done = {(r["market"], r["day"]) for r in _read(RUNS)}
    if (market, day) in done:
        return {"market": market, "day": day, "status": "ALREADY_LOGGED"}
    prev = max((d for m, d in done if m == market), default=None)
    if prev is not None:
        for d in bench_days[bisect_right(bench_days, prev):bisect_left(bench_days, day)]:
            _append(GAPS, {"market": market, "day": d, "reason": "MISSED_RUN（只记缺失，不补跑）", "noted_at_utc": _now()})

    pool = pool_on(uni, market, day)
    v2._init(market, mode="h2")
    results = v2._pool_map(_log_symbol, [(s, day) for s in pool], workers, (market, None, None, "h2"))
    written = _now()
    rows = [r for r in results if r]
    signals = [r for r in rows if "signal" in r]
    for r in signals:
        _append(SIGNALS, {"id": f"S|{market}|{r['symbol']}|{day}", "market": market, "symbol": r["symbol"], "day": day,
                          **r["signal"], "written_at_utc": written, "preregistration_sha256": prereg_sha,
                          "code_commit": commit})
    kept = [r for r in signals if not r["signal"]["vetoed"]]
    n_base = 0
    if kept:
        # 同日随机：按当日股票池规模折算到信号数的 SAME_DAY_MULTIPLE 倍（与开发区相同的倍数，按日折算）
        rate = min(1.0, v2.SAME_DAY_MULTIPLE * len(kept) / max(1, len(rows)))
        for r in rows:
            if "baseline_levels" in r and v2._hash_unit(market, r["symbol"], day) < rate:
                _append(BASELINE, {"id": f"B|{market}|{r['symbol']}|{day}", "market": market, "symbol": r["symbol"],
                                   "day": day, "kind": "same_day", **r["baseline_levels"], "rate": round(rate, 4),
                                   "written_at_utc": written, "preregistration_sha256": prereg_sha})
                n_base += 1
    run = {"market": market, "day": day, "pool": len(pool), "with_bar": len(rows),
           "candidates": sum(1 for r in rows if r["candidate"]), "signals": len(signals), "kept": len(kept),
           "baseline": n_base, "logged_at_utc": written, "code_commit": commit}
    _append(RUNS, run)
    return {**run, "status": "LOGGED"}


# ---------------------------------------------------------------------------
# 4. 到期打分
# ---------------------------------------------------------------------------

def _score_symbol(args: tuple[str, list[dict]]) -> list[dict]:
    symbol, entries = args
    data = v2._load_symbol(symbol)
    if data is None:
        return []
    bench_days = v2._C["bench_days"]
    out = []
    for e in entries:
        t = data["idx"].get(e["day"])
        if t is None or t + v2.LIMIT_SESSIONS + v2.HORIZON >= len(data["bars"]):
            continue  # 还没到期
        b_pos = bisect_left(bench_days, e["day"])
        if b_pos + v2.LIMIT_SESSIONS >= len(bench_days):
            continue
        pit, cut = v2.make_pit_slice(data["bars"], t, v2.HORIZON)
        eh = e["entry_zone"][1] if "entry_zone" in e else e["entry_high"]
        ex = v2.execute_plan(symbol, data, pit, cut, b_pos, eh, e["tp"], e["sl"])
        out.append({"id": e["id"], **ex, "scored_at_utc": _now()})
    return out


def score_market(market: str, workers: int) -> int:
    scored = {r["id"] for r in _read(OUTCOMES)}
    pending = [e for e in _read(SIGNALS) + _read(BASELINE) if e["market"] == market and e["id"] not in scored]
    by_sym: dict[str, list[dict]] = {}
    for e in pending:
        by_sym.setdefault(e["symbol"], []).append(e)
    if not by_sym:
        return 0
    v2._init(market, mode="h2")
    results = v2._pool_map(_score_symbol, list(by_sym.items()), workers, (market, None, None, "h2"))
    n = 0
    for rows in results:
        for r in rows:
            _append(OUTCOMES, r)
            n += 1
    return n


# ---------------------------------------------------------------------------
# 5. 状态（只报告数量）与一次性验收
# ---------------------------------------------------------------------------

def _joined(market: str) -> tuple[list[dict], list[dict]]:
    outcomes = {r["id"]: r for r in _read(OUTCOMES)}
    sig = [{**e, **outcomes[e["id"]]} for e in _read(SIGNALS) if e["market"] == market and e["id"] in outcomes]
    base = [{**e, **outcomes[e["id"]]} for e in _read(BASELINE) if e["market"] == market and e["id"] in outcomes]
    return sig, base


def status() -> dict:
    out = {}
    for m in MARKETS:
        sig, _ = _joined(m)
        kept_fills = [r for r in sig if not r["vetoed"] and r.get("fill_status") == "FILL"]
        eps = _non_overlapping_episodes(kept_fills)
        logged = [r for r in _read(SIGNALS) if r["market"] == m]
        out[m] = {
            "days_logged": sum(1 for r in _read(RUNS) if r["market"] == m),
            "days_missed": sum(1 for r in _read(GAPS) if r["market"] == m),
            "signals_logged": len(logged),
            "signals_kept": sum(1 for r in logged if not r["vetoed"]),
            "matured_episodes": len(eps),
            "matured_signal_days": len({r["day"] for r in eps}),
        }
        out[m]["p3_reached"] = out[m]["matured_episodes"] >= P3_MIN_EPISODES and out[m]["matured_signal_days"] >= P3_MIN_DAYS
    out["ready_for_evaluation"] = all(out[m]["p3_reached"] for m in ("CN", "US"))
    return out


def evaluate(prereg: dict) -> dict:
    import run_h1_holdout as h1

    if RESULT.exists():
        raise SystemExit("H2 已经验收过，按预注册只能运行一次")
    st = status()
    if not st["ready_for_evaluation"]:
        raise SystemExit(f"尚未满足验收条件（CN、US 都需达到 P3），当前：{json.dumps(st, ensure_ascii=False)}")
    result = {"evaluated_at_utc": _now(), "preregistration_sha256": hashlib.sha256(PREREG_H2.read_bytes()).hexdigest(),
              "status_at_evaluation": st, "markets": {}}
    for m in MARKETS:
        sig, base = _joined(m)
        v2._init(m, mode="h2")
        last = max((r["day"] for r in sig + base), default=v2.H2_FIRST_DAY)
        seg = v2.summarize(sig, base, v2.H2_FIRST_DAY, last)
        role = "acceptance" if m in prereg["acceptance"]["acceptance_markets"] else "diagnostic"
        result["markets"][m] = {"role": role, "verdict": h1.judge(seg, prereg["acceptance"]["gates"]), "segment": seg}
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# 0. 预注册（D13=a：原样沿用 v2 冻结的规则与门槛）
# ---------------------------------------------------------------------------

H2_FROZEN_RESEARCH = ["common.py", "run_backtest.py", "aggregate.py", "discover_alpha_dev.py", "run_dev_region_v2.py",
                      "run_h1_holdout.py", "build_pit_universe.py", "history_cache.py", "h2_forward.py"]
H2_FROZEN_DATA = ["artifacts/long_backtest/universe.csv", "artifacts/long_backtest/pit_universe.json"]


def build_h2_prereg() -> dict:
    v2_path = ROOT / "research" / "preregistration" / "preregistration_v2.json"
    v2_prereg = json.loads(v2_path.read_text(encoding="utf-8"))
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "kline-structure-chart" / "scripts").glob("*.py"))
    files += [f"research/{f}" for f in H2_FROZEN_RESEARCH]
    sha = lambda f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()  # noqa: E731
    return {
        "schema_version": "2.0",
        "kind": "PREREGISTRATION_H2_FORWARD",
        "registered_at_utc": _now(),
        "base_commit": v2._git_head(),
        "spec": "docs/superpowers/specs/2026-10-20-backtest-acceptance-revision.md 第 12 节",
        "rules_from": {"file": str(v2_path.relative_to(ROOT)), "sha256": hashlib.sha256(v2_path.read_bytes()).hexdigest(),
                       "frozen_specification": v2_prereg["frozen_specification"]},
        "frozen_files_sha256": {f: sha(f) for f in files},
        "frozen_data_sha256": {f: sha(f) for f in H2_FROZEN_DATA},
        "h2": {
            "markets": list(MARKETS),
            "first_day": v2.H2_FIRST_DAY,
            "processing": "每次运行只处理每个市场最近一个已收盘交易日；错过的交易日写入 gaps.jsonl，不补跑",
            "suggested_schedule": "每个工作日北京时间 07:00（UTC 23:00，周一至周五）：三个市场前一交易日均已收盘",
            "universe": "延续 pit_universe.json 最后一期，每 60 个基准交易日按调仓日前 60 日平均成交额取前 1,500 只",
            "refresh": f"新浪最近 {SINA_RECENT_BARS} 根、Yahoo 最近 {YAHOO_RECENT_RANGE}；重叠日收盘价不一致或出现新拆股时整段重抓",
        },
        "acceptance": {
            **v2_prereg["acceptance"],
            "evaluation_trigger": f"CN、US 已到期 episode 均 >= {P3_MIN_EPISODES} 且信号日均 >= {P3_MIN_DAYS} 时运行一次（D12=a）；此前只报告数量",
        },
        "baseline": {
            **v2_prereg["baseline"],
            "h2_rate": "按日折算：rate = min(1, sampling_multiple x 当日否决后信号数 / 当日有K线的股票池标的数)，只在有否决后信号的日子抽样",
        },
        "disclosures": v2_prereg["disclosures"] + [
            "上市标的清单固定为 2026-10-20 的 universe.csv：之后的新股不会进入股票池",
            "交易日历 CN 只覆盖到 2026-12-31，只影响未收盘K线判定，2027 年安排公布后需补录（需另行登记，不改动 H2 冻结代码）",
        ],
    }


def daily(args, prereg: dict) -> None:
    prereg_sha = hashlib.sha256(PREREG_H2.read_bytes()).hexdigest()
    commit = v2._git_head()
    bench_keys = [f"{m}|{BENCHMARKS[m]['symbol']}" for m in MARKETS] + [f"US|{VIX_SYMBOL}"]
    print(f"[{_now()}] refresh benchmarks: {dict(refresh(bench_keys, args.threads))}", flush=True)
    uni = load_universe()
    keys: list[str] = []
    for m in MARKETS:
        bench_days = [b["day"] for b in load_bars(m, BENCHMARKS[m]["symbol"])]
        day = bench_days[-1]
        if day >= v2.H2_FIRST_DAY and rebalance_due(uni, m, bench_days, day):
            keys += [k for k in _universe_rows() if k.startswith(f"{m}|")]
            uni.setdefault("_pending_rebalance", {})[m] = day
        else:
            keys += [f"{m}|{s}" for s in pool_on(uni, m, day)]
    print(f"[{_now()}] refresh {len(keys)} symbols: {dict(refresh(keys, args.threads))}", flush=True)
    for m, day in (uni.pop("_pending_rebalance", None) or {}).items():
        bench_days = [b["day"] for b in load_bars(m, BENCHMARKS[m]["symbol"])]
        rebalance(uni, m, bench_days, day)
        print(f"[{_now()}] {m} rebalanced on {day}", flush=True)
    save_universe(uni)
    for m in MARKETS:
        print(f"[{_now()}] log {m}: {log_market(m, uni, args.workers, prereg_sha, commit)}", flush=True)
    for m in MARKETS:
        print(f"[{_now()}] score {m}: {score_market(m, args.workers)} new outcomes", flush=True)
    print(f"[{_now()}] status (counts only): {json.dumps(status(), ensure_ascii=False)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["register", "daily", "status", "evaluate", "check"])
    ap.add_argument("--workers", type=int, default=2, help="计算进程数（2 核服务器取 2）")
    ap.add_argument("--threads", type=int, default=4, help="抓数线程数（受站点限速，4 已足够）")
    args = ap.parse_args()
    if args.command == "register":
        if PREREG_H2.exists():
            raise SystemExit("preregistration_h2.json 已存在，不能重新登记")
        PREREG_H2.write_text(json.dumps(build_h2_prereg(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {PREREG_H2}（提交到 git 后才算冻结）")
        return
    prereg = check_preregistration()
    if args.command == "check":
        print("H2 预注册校验通过")
    elif args.command == "daily":
        daily(args, prereg)
    elif args.command == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(evaluate(prereg)["markets"], ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    sys.exit(main())
