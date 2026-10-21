#!/usr/bin/env python3
"""主做多计划检验 · 第一遍：逐个"股票-日"调用线上流水线，记录评级与计划价位（不含任何结局）。

见 docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md 第 3.1 节。
结果按 CHUNK 只标的一个分片写入 artifacts/long_backtest/main_plan/<mode>_<market>_<key12>/，可续跑：
分片里只有信号日当时可见的信息，中途崩溃后续跑不会接触任何结局。
股票池、开发区边界、次日停牌跳过与 PIT bundle 全部沿用 run_dev_region_v2（该文件已被预注册 v2 冻结，只调用、不修改）。
"""
from __future__ import annotations

import gzip
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import run_dev_region_v2 as v2
from common import ART, CACHE, PIT_UNIVERSE, ROOT
from run_backtest import make_pit_slice

from analysis import analyze_bundle  # noqa: E402  (common 已把技能 scripts 加入 sys.path)

SCAN_DIR = ART / "main_plan"
CHUNK = 15
# 只有 run_h1_main_plan.py 在校验预注册 v3 通过后才会把它设为 True
H1_AUTHORIZED = False


def mode_config(mode: str, market: str) -> tuple[tuple[str, str], Path]:
    """评估区间与股票池：开发区（默认）或留出集 H1。"""
    if mode == "dev":
        return v2.DEV_REGION, PIT_UNIVERSE
    if mode == "h1":
        if not H1_AUTHORIZED:
            raise PermissionError("主做多计划的留出集 H1 只能由 run_h1_main_plan.py 在校验预注册 v3 后评估")
        return (v2.H1_FIRST_DAY[market], v2.H1_LAST_DAY), v2.H1_UNIVERSE
    raise ValueError(f"unknown mode: {mode}")


def init(market: str, mode: str = "dev") -> None:
    mode_config(mode, market)  # 未授权的 H1 在这里就被拒绝
    # v2 的 H1 守卫跟随本模块的授权：只有通过预注册 v3 校验后才会是 True
    v2.H1_AUTHORIZED = H1_AUTHORIZED
    v2._init(market, mode=mode)


def scan_stock_day(symbol: str, data: dict, t: int, day: str, b_pos: int) -> dict:
    """对一个股票-日调用线上流水线，记录主计划评级与计划价位（价位为 as-of-t 复权口径）。"""
    pit, cut = make_pit_slice(data["bars"], t, v2.HORIZON)
    bundle, request = v2.build_bundle(symbol, data, pit, cut, day)
    rep = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)
    ml = rep["meta_labeling_decision"]
    plan = rep["triple_barrier_plan"]
    rt = plan.get("breakeven_ratchet") or {}
    c = float(pit[cut]["close"])
    atr_snap = rep["mathematical_indicators"]["volatility"].get("atr14")
    # 与 risk.compute_triple_barrier_plan 相同：快照缺失时取收盘价的 1.8%，再以收盘价的 0.5% 为下限
    atr = max(c * 0.005, float(atr_snap) if atr_snap is not None else c * 0.018)
    return {
        "sym": symbol, "day": day, "t": t, "b": b_pos,
        "a": ml["action"], "st": ml["setup_type"],
        "z": [float(plan["entry_zone"][0]), float(plan["entry_zone"][1])],
        "sl": float(plan["stop_loss_barrier"]), "tp": float(plan["take_profit_1"]),
        "rt": [bool(rt.get("enabled", True)), float(rt.get("trigger_r", 0.90)),
               float(rt.get("confirm_close_r", 0.30)), float(rt.get("lock_r", 0.08))],
        "atr": atr, "c": c,
    }


def scan_symbol(symbol: str) -> tuple[list[dict], list[dict], bool]:
    """返回 (记录, 错误, 是否加载成功)。单个股票-日出错只记错误，不中断整只标的。"""
    data = v2._load_symbol(symbol)
    if data is None:
        return [], [], False
    rows: list[dict] = []
    errors: list[dict] = []
    for day, t, b_pos in v2._eligible_days(symbol, data):
        try:
            rows.append(scan_stock_day(symbol, data, t, day, b_pos))
        except Exception as exc:  # noqa: BLE001 - 如实计数并写入输出，不悄悄少算
            errors.append({"sym": symbol, "day": day, "err": f"{type(exc).__name__}: {exc}"})
    return rows, errors, True


def scan_key(market: str, mode: str, symbols: list[str]) -> str:
    """分片目录的键：技能全部脚本、研究侧行情加载与 PIT 切片、v2 管线与本文件、区间、股票池与行情缓存清单。"""
    h = hashlib.sha256()
    files = [*sorted((ROOT / "kline-structure-chart" / "scripts").glob("*.py")),
             ROOT / "research" / "common.py", ROOT / "research" / "run_backtest.py",
             ROOT / "research" / "run_dev_region_v2.py", Path(__file__).resolve()]
    for f in files:
        h.update(f.name.encode())
        h.update(f.read_bytes())
    region, universe = mode_config(mode, market)
    h.update(json.dumps([mode, market, region, symbols, CHUNK]).encode())
    h.update(universe.read_bytes())
    h.update((CACHE / "MANIFEST.json").read_bytes())
    return h.hexdigest()


def _scan_chunk(out_dir_str: str, shard_id: int, symbols: list[str]) -> dict:
    out_dir = Path(out_dir_str)
    final = out_dir / f"shard_{shard_id:04d}.jsonl.gz"
    tmp = out_dir / f"shard_{shard_id:04d}.jsonl.gz.tmp"
    n_rows = n_err = skipped = 0
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for sym in symbols:
            rows, errors, loaded = scan_symbol(sym)
            skipped += 0 if loaded else 1
            for r in rows:
                fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            for e in errors:
                fh.write(json.dumps({"__error__": True, **e}, ensure_ascii=False, separators=(",", ":")) + "\n")
            n_rows += len(rows)
            n_err += len(errors)
    tmp.replace(final)
    summary = {"shard": shard_id, "symbols": len(symbols), "rows": n_rows, "errors": n_err, "skipped_symbols": skipped}
    (out_dir / f"shard_{shard_id:04d}.done").write_text(json.dumps(summary), encoding="utf-8")
    return summary


def _worker_init(h1_authorized: bool, market: str, mode: str) -> None:
    """子进程会重新导入本模块：H1 授权需要由父进程显式传入。"""
    global H1_AUTHORIZED
    H1_AUTHORIZED = h1_authorized
    init(market, mode)


def run_scan(market: str, mode: str = "dev", workers: int = 8, limit_symbols: int | None = None) -> Path:
    init(market, mode)
    symbols = sorted(v2._C["periods"])
    if limit_symbols:
        symbols = symbols[:: max(1, len(symbols) // limit_symbols)][:limit_symbols]
    key = scan_key(market, mode, symbols)
    out_dir = SCAN_DIR / f"{mode}_{market}_{key[:12]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "KEY").write_text(key, encoding="utf-8")
    chunks = [symbols[i:i + CHUNK] for i in range(0, len(symbols), CHUNK)]
    todo = [(i, c) for i, c in enumerate(chunks) if not (out_dir / f"shard_{i:04d}.done").exists()]
    print(f"[scan {mode} {market}] symbols={len(symbols)} shards={len(chunks)} pending={len(todo)} dir={out_dir.name}", flush=True)
    if todo and workers <= 1:
        for i, c in todo:
            _scan_chunk(str(out_dir), i, c)
    elif todo:
        with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init,
                                 initargs=(H1_AUTHORIZED, market, mode)) as ex:
            futures = [ex.submit(_scan_chunk, str(out_dir), i, c) for i, c in todo]
            for k, f in enumerate(as_completed(futures), 1):
                f.result()
                if k % 10 == 0 or k == len(futures):
                    print(f"[scan {mode} {market}] {k}/{len(futures)} shards", flush=True)
    (out_dir / "COMPLETE").write_text(json.dumps({"n_shards": len(chunks), "n_symbols": len(symbols)}), encoding="utf-8")
    return out_dir


def iter_rows(out_dir: Path):
    """逐行读取已完成分片（含 __error__ 行）；未完成的临时文件不读。"""
    for p in sorted(out_dir.glob("shard_*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for line in fh:
                yield json.loads(line)


def scan_stats(out_dir: Path) -> dict:
    done = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out_dir.glob("shard_*.done"))]
    errors = [r for r in iter_rows(out_dir) if r.get("__error__")]
    return {
        "n_shards": len(done),
        "symbols": sum(d["symbols"] for d in done),
        "skipped_symbols": sum(d["skipped_symbols"] for d in done),
        "rows": sum(d["rows"] for d in done),
        "errors": sum(d["errors"] for d in done),
        "error_examples": errors[:5],
    }
