# 主做多计划有效性检验 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按设计方案在开发区诊断现行主做多计划，冻结预注册 v3，并在留出集 H1 上一次性判定 CN、US、HK 三个市场是否通过 P1、P2b、P3。

**Architecture:** 新增两遍式研究管线。第一遍 `main_plan_scan.py` 对每个"股票-日"调用线上 `analyze_bundle`，只记录信号日当时可见的评级与计划价位，按分片落盘、可续跑。第二遍 `main_plan_eval.py` 按固定价位撮合信号，抽取"移植几何"同日随机基准与"自身计划"诊断基准，按信号日分块 bootstrap 汇总。H1 由新守卫 `run_h1_main_plan.py` 在校验预注册 v3 与缓存清单后运行一次。股票池加载、开发区边界、PIT bundle 与统计函数全部复用已冻结的 v2 代码，只调用、不修改。

**Tech Stack:** Python 3.12（`uv run --directory kline-structure-chart`），NumPy（bootstrap），pytest；行情只用 `artifacts/long_backtest/cache/` 的真实缓存。

**Spec:** `docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md`

**范围：** 本计划覆盖设计第 6.1 节的步骤 1~4（管线、开发区诊断、冻结、H1 验收）。步骤 5（未通过市场的线上降级，设计第 7 节）要等 H1 结果出来才知道改哪些市场，届时另写一份计划。

## Global Constraints

- 数据真实性优先：只用真实缓存行情，取不到就报错，禁止合成、补数或估算；测试不联网，不生成合成行情序列（AGENTS.md 第 1.0、4.2 条）。
- **不得修改**以下已被预注册 v2 或 H2 冻结的文件，只能 import 调用：`research/common.py`、`research/run_backtest.py`、`research/aggregate.py`、`research/discover_alpha_dev.py`、`research/run_dev_region_v2.py`、`research/run_h1_holdout.py`、`research/write_preregistration_v2.py`、`research/h2_forward.py`。
- 线上主计划代码（`kline-structure-chart/scripts/*.py`）从 Task 4 起冻结，只允许修复违反已写明契约或铁律的缺陷，禁止改任何参数或阈值（设计第 6.2 节）。
- 信号：评级为 `LONG_CONFIRMED` 或 `WATCH_LONG_SETUP`（`common.LONG_ACTIONS`）。
- 主口径：5 日限价，以入场区上沿挂单；出场调用 `evaluation.label_triple_barrier_event`，保本上移参数取自计划的 `breakeven_ratchet`；持有 15 根；21 个日历日 episode 去重。
- 硬门槛：P1、P2b、P3，定义与预注册 v2 逐字相同；P2a、命中率只报告。三个市场各自判定，P3 不满足也算未通过。
- 同日随机基准：抽样倍数 10，种子 `20261021`；bootstrap 按信号日分块，2,000 次，种子 `20261020`（`aggregate.date_block_bootstrap` 的默认值）。
- 开发区 `2021-01-04 ~ 2026-09-24`；H1 起点 CN `2017-10-24`、HK `2018-07-09`、US `2018-06-22`，终点均为 `2020-11-30`。除 `run_h1_main_plan.py` 外，任何代码路径都不得评估 2020-11-30 及以前的结果。
- 研究测试命令：`uv run --directory kline-structure-chart pytest ../research/tests/<file> -v`（缓存缺失时跳过）。
- 改代码的任务结束前跑 AGENTS.md 第 3.3 节门禁：`uv run --directory kline-structure-chart pytest -q` 与 `uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`。
- 文档与注释用中文；提交信息以 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 结尾。

## Review Focus

1. 长时间运行中某个"股票-日"的 `analyze_bundle` 抛异常：应记为错误并计数、写入输出，其余照常；不能让十几个小时的运行崩掉，也不能悄悄少算。→ Task 1 `test_scan_resume_and_error_capture`。
2. 运行到一半崩溃或被中断：重跑时复用已完成分片，不重算、不覆盖；分片里不得含任何结局。→ Task 1 `test_scan_resume_and_error_capture`。
3. 信号几何无效、跳空到止损下方、开盘涨停买不进：这些都要计入不成交原因，各状态计数之和必须等于信号数，不能悄悄丢。→ Task 3 `test_summarize_counts_criteria_cost_and_groups`。
4. 低价或低 ATR 股票移植几何后，取整让入场区、止损、TP1 挤到同一价位：应剔除并计数，不能产生倒挂的基准交易。→ Task 2 `test_transplant_levels_roundtrip_and_collapse`。
5. 冻结后行情缓存被改动（例如 H2 增量刷新）：H1 守卫必须拒绝运行。→ Task 5 `test_cache_files_checked_against_manifest`。

---

### Task 1：第一遍扫描（`main_plan_scan.py`）

**Files:**
- Create: `research/main_plan_scan.py`
- Create: `research/tests/test_main_plan.py`
- Modify: `docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md`（同步实现细节，见 Step 7）

**Interfaces:**
- Consumes（v2，已冻结）：`v2._init(market, mode=...)`、`v2._C`、`v2._load_symbol(symbol) -> dict | None`、`v2._eligible_days(symbol, data)` 产出 `(day, t, b_pos)`、`v2.build_bundle(symbol, data, pit, cut, day) -> (bundle, request)`、`v2.DEV_REGION`、`v2.H1_FIRST_DAY`、`v2.H1_LAST_DAY`、`v2.H1_UNIVERSE`、`v2.HORIZON`；`run_backtest.make_pit_slice(bars, t, horizon) -> (pit, cut)`。
- Produces：
  - `H1_AUTHORIZED: bool`（默认 False）、`SCAN_DIR: Path`、`CHUNK: int = 15`
  - `mode_config(mode: str, market: str) -> tuple[tuple[str, str], Path]`
  - `init(market: str, mode: str = "dev") -> None`
  - `scan_stock_day(symbol, data, t, day, b_pos) -> dict`，记录字段：`sym, day, t, b, a`（评级）`, st`（入场类型）`, z`（`[入场区下沿, 上沿]`）`, sl, tp, rt`（`[enabled, trigger_r, confirm_close_r, lock_r]`）`, atr`（计划所用 ATR）`, c`（PIT 收盘价）
  - `scan_symbol(symbol) -> tuple[list[dict], list[dict], bool]`（记录、错误、是否加载成功）
  - `run_scan(market, mode="dev", workers=8, limit_symbols=None) -> Path`（分片目录）
  - `iter_rows(out_dir: Path)` 逐行产出记录（含 `__error__` 行）
  - `scan_stats(out_dir: Path) -> dict`

- [ ] **Step 1：写失败测试**

`research/tests/test_main_plan.py`：

```python
"""主做多计划检验：第一遍记录与线上一致、撮合与线上标签一致、基准抽样、汇总口径、续跑与出错计数（真实缓存行情）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import main_plan_scan as scan  # noqa: E402
import run_dev_region_v2 as v2  # noqa: E402
from common import LONG_ACTIONS, PIT_UNIVERSE  # noqa: E402
from run_backtest import make_pit_slice  # noqa: E402

from analysis import analyze_bundle  # noqa: E402

pytestmark = pytest.mark.skipif(not PIT_UNIVERSE.exists(), reason="需要 artifacts/long_backtest 真实行情缓存")


def _scan_some(market: str, n_symbols: int = 6, days_per_symbol: int = 40) -> list[tuple[str, dict, dict]]:
    """等间隔抽几只标的、每只等间隔抽若干天，逐个调用线上流水线（整只标的扫完太慢，不适合单测）。"""
    scan.init(market)
    symbols = sorted(v2._C["periods"])
    out = []
    for sym in symbols[:: max(1, len(symbols) // n_symbols)][:n_symbols]:
        data = v2._load_symbol(sym)
        if data is None:
            continue
        days = list(v2._eligible_days(sym, data))
        for day, t, b_pos in days[:: max(1, len(days) // days_per_symbol)][:days_per_symbol]:
            out.append((sym, data, scan.scan_stock_day(sym, data, t, day, b_pos)))
    return out


@pytest.fixture(scope="module")
def us_rows() -> list[tuple[str, dict, dict]]:
    return _scan_some("US")


def test_scan_record_matches_live_report(us_rows) -> None:
    """记录的评级、入场类型与计划价位必须与线上流水线逐项相同（防止被改成复制实现）。"""
    scan.init("US")
    signals = [x for x in us_rows if x[2]["a"] in LONG_ACTIONS]
    assert signals, "抽样里没有主计划信号，检验没有说服力：加大 days_per_symbol"
    for sym, data, r in us_rows[:20] + signals:
        pit, cut = make_pit_slice(data["bars"], r["t"], v2.HORIZON)
        bundle, request = v2.build_bundle(sym, data, pit, cut, r["day"])
        rep = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)
        ml, plan = rep["meta_labeling_decision"], rep["triple_barrier_plan"]
        assert (r["a"], r["st"]) == (ml["action"], ml["setup_type"])
        assert r["z"] == [float(x) for x in plan["entry_zone"]]
        assert (r["sl"], r["tp"]) == (float(plan["stop_loss_barrier"]), float(plan["take_profit_1"]))
        rt = plan["breakeven_ratchet"]
        assert r["rt"] == [bool(rt["enabled"]), float(rt["trigger_r"]), float(rt["confirm_close_r"]), float(rt["lock_r"])]
        c = float(pit[cut]["close"])
        assert r["c"] == c
        assert r["atr"] == max(c * 0.005, float(rep["mathematical_indicators"]["volatility"]["atr14"]))
    for _, _, r in signals:
        assert r["sl"] < r["z"][0] <= r["z"][1] < r["tp"], r


def test_h1_requires_authorization_and_dev_stays_out(monkeypatch) -> None:
    monkeypatch.setattr(scan, "H1_AUTHORIZED", False)
    with pytest.raises(PermissionError):
        scan.mode_config("h1", "CN")
    with pytest.raises(PermissionError):
        scan.init("CN", mode="h1")
    region, universe = scan.mode_config("dev", "CN")
    assert region == v2.DEV_REGION and region[0] > v2.H1_LAST_DAY and universe == PIT_UNIVERSE


def test_scan_resume_and_error_capture(tmp_path, monkeypatch) -> None:
    """单个股票-日出错只记错误、不中断；中途崩溃后续跑只补未完成的分片，已完成的分片不重写。"""
    monkeypatch.setattr(scan, "SCAN_DIR", tmp_path)
    monkeypatch.setattr(scan, "CHUNK", 1)
    seen: list[str] = []

    def fake_scan_stock_day(symbol, data, t, day, b_pos):
        # 只替换耗时的线上流水线调用，记录字段与真实记录相同；第一次遇到的第 3 个股票-日模拟流水线报错
        seen.append(day)
        if len(seen) == 3:
            raise ValueError("模拟流水线异常")
        return {"sym": symbol, "day": day, "t": t, "b": b_pos, "a": "NEUTRAL_HOLD_WAIT", "st": "WAIT_CONFIRMATION",
                "z": [0.0, 0.0], "sl": 0.0, "tp": 0.0, "rt": [True, 0.9, 0.3, 0.08], "atr": 0.0, "c": 0.0}

    monkeypatch.setattr(scan, "scan_stock_day", fake_scan_stock_day)
    out_dir = scan.run_scan("US", workers=1, limit_symbols=2)
    stats1 = scan.scan_stats(out_dir)
    assert stats1["n_shards"] == 2 and stats1["errors"] == 1 and stats1["rows"] == len(seen) - 1
    assert (out_dir / "COMPLETE").exists()
    rows1 = list(scan.iter_rows(out_dir))
    assert sum(1 for r in rows1 if r.get("__error__")) == 1

    kept = out_dir / "shard_0000.jsonl.gz"
    mtime = kept.stat().st_mtime_ns
    (out_dir / "shard_0001.done").unlink()
    (out_dir / "shard_0001.jsonl.gz").unlink()
    seen.clear()
    seen.extend(["x", "y", "z"])  # 续跑时不再触发模拟异常
    out_dir2 = scan.run_scan("US", workers=1, limit_symbols=2)
    assert out_dir2 == out_dir and kept.stat().st_mtime_ns == mtime
    stats2 = scan.scan_stats(out_dir)
    assert stats2["n_shards"] == 2 and stats2["rows"] >= stats1["rows"]
```

- [ ] **Step 2：运行测试，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_main_plan.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'main_plan_scan'`

- [ ] **Step 3：实现 `research/main_plan_scan.py`**

```python
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
```

- [ ] **Step 4：运行测试，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_main_plan.py -v`
Expected: 3 passed（`us_rows` 夹具约调用 240 次线上流水线，耗时约 20~40 秒）

- [ ] **Step 5：确认冻结文件未被改动**

Run: `git status --porcelain -- research/common.py research/run_backtest.py research/aggregate.py research/discover_alpha_dev.py research/run_dev_region_v2.py research/run_h1_holdout.py research/write_preregistration_v2.py research/h2_forward.py kline-structure-chart/scripts`
Expected: 无输出

- [ ] **Step 6：跑 AGENTS.md 第 3.3 节门禁**

Run: `uv run --directory kline-structure-chart pytest -q && uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`
Expected: 全部 passed；`failing=0`

- [ ] **Step 7：同步设计文档中的实现细节**

在设计文档中做以下三处替换（用 Edit，原文逐字复制自下列代码块）。

第 3.2 节，原文：

```text
成交价不高于止损时记为不成交（`GAP_BELOW_STOP`）；下根K线日期晚于下一个基准交易日（停牌跨期）时记为陈旧信号（`STALE_SIGNAL`）。均单独计数，不悄悄剔除。
```

替换为：

```text
成交价不高于止损时记为不成交（`GAP_BELOW_STOP`），开盘涨停等不可交易情况记为 `NOT_TRADABLE:<原因>`，均单独计数，不悄悄剔除。次日停牌（下根K线日期晚于下一个基准交易日）的股票-日与 v2 相同，不进入评估（`run_dev_region_v2._eligible_days`）。
```

第 3.5 节，原文：

```text
新增测试，在 `tests/fixtures/recorded/` 的录制行情上逐日核对：
```

替换为：

```text
新增测试（`research/tests/test_main_plan.py`），在 `artifacts/long_backtest/cache/` 的真实行情缓存上抽样核对（研究管线只读这份缓存，与 v2 的同类测试一致；缓存缺失时跳过）：
```

第 5.2 节，原文：

```text
成交率与不成交原因分布（5 日未成交、跳空到止损下方、陈旧信号、几何无效）；
```

替换为：

```text
成交率与不成交原因分布（5 日未成交、跳空到止损下方、开盘涨停等不可交易、几何无效）；
```

- [ ] **Step 8：提交**

```bash
git add research/main_plan_scan.py research/tests/test_main_plan.py docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md
git commit -m "$(cat <<'EOF'
feat(research): 主做多计划检验第一遍扫描（逐股票-日记录评级与计划价位，可续跑）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2：撮合与同日随机基准（`main_plan_eval.py` 第一部分）

**Files:**
- Create: `research/main_plan_eval.py`
- Modify: `research/tests/test_main_plan.py`（追加测试）
- Modify: `docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md`（第 4.1 节第 2 步，见 Step 6）

**Interfaces:**
- Consumes：Task 1 的 `scan.init`、`scan.H1_AUTHORIZED`、记录字段 `sym, day, t, b, a, st, z, sl, tp, rt, atr, c`；v2 的 `v2._C["market"]`、`v2._C["bench_days"]`、`v2._load_symbol`、`v2.HORIZON`、`v2.LIMIT_SESSIONS`；`aggregate._non_overlapping_episodes`；`evaluation.label_triple_barrier_event`；`market_rules.round_to_tick`。
- Produces：
  - `SEED = 20261021`、`BASELINE_MULTIPLE = 10`
  - `valid_geometry(r: dict) -> bool`
  - `plan_atr_multiples(sig: dict) -> list[float]`（`[入场区下沿, 上沿, 止损, TP1]` 相对收盘价的 ATR 倍数）
  - `transplant_levels(mult, c, atr, market, day) -> dict | None`（键 `lo, hi, sl, tp`）
  - `execute_main_plan(symbol, data, pit, cut, b_pos, entry_low, entry_high, tp, sl, rt) -> dict`，字段：`open_status`、`open_net`、`open_outcome`、`fill_status`、`fill_day`、`fill_price`、`net`、`gross`、`outcome`
  - `baseline_plan(signals_by_day: dict[str, list[dict]], pool_by_day: dict[str, int]) -> dict[str, dict]`（每日 `rate, mults, rts`）
  - `draw(market: str, row: dict, day_plan: dict | None) -> int | None`
  - `eval_shard(path_str: str) -> tuple[list[dict], dict[str, list[dict]], dict]`（信号行、`{"transplant": [...], "own_plan": [...]}` 已去重的基准 episode、计数）

- [ ] **Step 1：追加失败测试**

在 `research/tests/test_main_plan.py` 顶部 import 区追加：

```python
import main_plan_eval as ev  # noqa: E402
from evaluation import label_triple_barrier_event  # noqa: E402
```

文件末尾追加：

```python
def test_execution_matches_online_label(us_rows) -> None:
    """固定价位撮合：成交价不超过入场区上沿、止损在成交价下方，标签与用同一成交价调用线上函数的结果相同。"""
    scan.init("US")
    filled = 0
    for sym, data, r in us_rows:
        if r["a"] not in LONG_ACTIONS or not ev.valid_geometry(r):
            continue
        pit, cut = make_pit_slice(data["bars"], r["t"], v2.HORIZON)
        ex = ev.execute_main_plan(sym, data, pit, cut, r["b"], r["z"][0], r["z"][1], r["tp"], r["sl"], r["rt"])
        if ex["open_status"] == "FILL":
            assert r["z"][0] <= float(pit[cut + 1]["open"]) <= r["z"][1]
        if ex["fill_status"] != "FILL":
            continue
        assert r["sl"] < ex["fill_price"] <= r["z"][1]
        k = next(i for i, b in enumerate(pit) if b["day"] == ex["fill_day"])
        assert cut + 1 <= k <= cut + v2.LIMIT_SESSIONS
        ref = label_triple_barrier_event(
            pit, entry_idx=k, tp_price=r["tp"], sl_price=r["sl"], horizon_bars=v2.HORIZON, market="US",
            asset_type="equity", symbol=sym, is_st=data["is_st"], trailing_stop=r["rt"][0], trigger_r=r["rt"][1],
            confirm_close_r=r["rt"][2], lock_r=r["rt"][3], name=data["name"], entry_price_override=ex["fill_price"],
        )
        assert (ref["net_return"], ref["outcome"]) == (ex["net"], ex["outcome"])
        filled += 1
    assert filled >= 1, "抽样里没有成交的信号，检验没有说服力"


def test_transplant_levels_roundtrip_and_collapse(us_rows) -> None:
    """移植到信号自身应还原原价位；取整后挤到同一价位的几何必须剔除。"""
    sig = next(r for _, _, r in us_rows if r["a"] in LONG_ACTIONS and ev.valid_geometry(r))
    other = next(r for _, _, r in us_rows if r["a"] not in LONG_ACTIONS and r["c"] > 1.0)
    mult = ev.plan_atr_multiples(sig)
    back = ev.transplant_levels(mult, sig["c"], sig["atr"], "US", sig["day"])
    assert back is not None
    assert [back["lo"], back["hi"], back["sl"], back["tp"]] == pytest.approx([sig["z"][0], sig["z"][1], sig["sl"], sig["tp"]])
    moved = ev.transplant_levels(mult, other["c"], other["atr"], "US", other["day"])
    assert moved is None or moved["sl"] < moved["lo"] <= moved["hi"] < moved["tp"]
    assert ev.transplant_levels([-1e-4, 1e-4, -2e-4, 2e-4], other["c"], other["atr"], "US", other["day"]) is None


def test_baseline_plan_rate_cap_and_reproducible_draw(us_rows) -> None:
    sigs = [r for _, _, r in us_rows if r["a"] in LONG_ACTIONS][:3]
    plans = ev.baseline_plan({"2024-01-02": sigs, "2024-01-03": sigs[:1]}, {"2024-01-02": 100, "2024-01-03": 5})
    assert plans["2024-01-02"]["rate"] == pytest.approx(ev.BASELINE_MULTIPLE * len(sigs) / 100)
    assert plans["2024-01-03"]["rate"] == 1.0  # 股票池不足 10 倍时全部抽取
    assert len(plans["2024-01-02"]["mults"]) == len(sigs)
    row = {"sym": "AAPL.US", "day": "2024-01-03"}
    j = ev.draw("US", row, plans["2024-01-03"])
    assert j == 0 and ev.draw("US", row, plans["2024-01-03"]) == j
    assert ev.draw("US", row, None) is None
```

- [ ] **Step 2：运行测试，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_main_plan.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'main_plan_eval'`

- [ ] **Step 3：实现 `research/main_plan_eval.py`（撮合与基准部分）**

```python
#!/usr/bin/env python3
"""主做多计划检验 · 第二遍：按固定价位撮合信号、抽取同日随机基准并汇总。

见 docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md 第 3.2~5 节。
第一遍分片由 main_plan_scan.py 生成。主基准"移植几何"：在信号日随机抽股，把当日某个信号的价位形状
（相对收盘价的 ATR 倍数）套到被抽股票上；诊断基准用被抽股票自己的计划价位。两者同撮合、同出场、同费用。
开发区结果均为样本内，不构成验收；H1 只能由 run_h1_main_plan.py 运行。
"""
from __future__ import annotations

import gzip
import json
import zlib
from collections import Counter, defaultdict

import main_plan_scan as scan
import run_dev_region_v2 as v2
from aggregate import _non_overlapping_episodes
from common import LONG_ACTIONS
from run_backtest import make_pit_slice

from evaluation import label_triple_barrier_event  # noqa: E402
from market_rules import round_to_tick  # noqa: E402

SEED = 20261021
BASELINE_MULTIPLE = 10

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
```

- [ ] **Step 4：运行测试，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_main_plan.py -v`
Expected: 6 passed

- [ ] **Step 5：跑门禁并确认冻结文件未改动**

Run: `git status --porcelain -- research/common.py research/run_backtest.py research/aggregate.py research/discover_alpha_dev.py research/run_dev_region_v2.py research/run_h1_holdout.py research/write_preregistration_v2.py research/h2_forward.py kline-structure-chart/scripts && uv run --directory kline-structure-chart pytest -q && uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`
Expected: `git status` 无输出；全部 passed；`failing=0`

- [ ] **Step 6：同步设计文档第 4.1 节第 2 步**

原文：

```text
2. 在同一信号日、同一 PIT 股票池中随机抽 10 只股票（抽样倍数 10，种子写入预注册；不排除信号股本身，与 v2 一致），把这组倍数套到各自的信号日收盘价与 ATR14 上，按交易所价位表取整。
```

替换为：

```text
2. 每个信号日按"抽样率 = min(1, 10 × 当日信号数 / 当日股票池规模)"从同一 PIT 股票池抽股（种子 20261021 写入预注册；不排除信号股本身，与 v2 一致）。每只被抽中的股票按哈希对应当日的一个信号，把该信号的倍数套到被抽股票的信号日收盘价与 ATR14 上（ATR14 取第一遍记录的线上值），按交易所价位表取整；取整后几何自相矛盾的剔除并计数。
```

- [ ] **Step 7：提交**

```bash
git add research/main_plan_eval.py research/tests/test_main_plan.py docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md
git commit -m "$(cat <<'EOF'
feat(research): 主做多计划固定价位撮合与移植几何同日随机基准

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3：汇总、判定口径与开发区 CLI（`main_plan_eval.py` 第二部分）

**Files:**
- Modify: `research/main_plan_eval.py`（追加汇总与 CLI）
- Modify: `research/tests/test_main_plan.py`（追加测试）

**Interfaces:**
- Consumes：Task 2 的 `eval_shard`、`baseline_plan`、`_eval_init`；Task 1 的 `run_scan`、`iter_rows`、`scan_stats`、`mode_config`；v2 的 `v2._layer_stats(eps) -> dict`（含 `n_episodes, n_signal_days, max_day_share_pct, net_positive_rate_pct, mean_net_return_pct, mean_date_cluster95_pct, ...`）、`v2._fill_eps(rows)`、`v2._ci_pct`、`v2._git_head()`；`aggregate.date_block_bootstrap(rows, value_fn, base_rows=None)`。
- Produces：
  - `summarize(signals: list[dict], base: dict[str, list[dict]], note: str) -> dict`，键：`n_signals, actions, setups, fill_status, fill_layer, next_open_layer, baselines, excess_vs_same_day_random, excess_vs_own_plan_diagnostic, groups, cost_2x, sensitivity_excluding_top_day`（可选）`, criteria_diagnostic`
  - `criteria_diagnostic` 的键与 v2 相同：`note, P1_mean_net_positive, P2a_rate_excess, P2b_mean_excess, P3_sample, T_rate_ge_70`（可直接交给 `run_h1_holdout.judge`）
  - `run_market(market: str, mode: str = "dev", workers: int = 8, limit_symbols: int | None = None) -> dict`，键：`scan, scan_dir, region, baseline_sampling, summary`
  - `config() -> dict`
  - CLI：`python research/main_plan_eval.py --markets CN,HK,US --workers 8 [--limit-symbols N] [--out PATH]`，写 `artifacts/long_backtest/dev_main_plan_v3_metrics.json` 并向试验台账追加一行（`hypothesis = "MAIN_LONG_PLAN"`）

- [ ] **Step 1：追加失败测试**

文件末尾追加：

```python
def _outcome_row(i: int, net: float, gross: float, **extra) -> dict:
    # 汇总口径测试只需要结局数值（不是行情序列）；每行一个独立标的、独立日期，避免 episode 去重
    return {"symbol": f"S{i:03d}", "day": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", "fill_status": "FILL",
            "net": net, "gross": gross, "outcome": "TP_BARRIER" if net > 0 else "SL_BARRIER", **extra}


def test_summarize_counts_criteria_cost_and_groups() -> None:
    signals = [
        _outcome_row(i, 0.01 if i % 3 else -0.01, (0.01 if i % 3 else -0.01) + 0.003,
                     action=LONG_ACTIONS[i % 2], setup="RIGHT_SIDE_BREAKOUT" if i % 4 else "LEFT_SIDE_PULLBACK",
                     open_status="OPEN_OUTSIDE_ZONE")
        for i in range(120)
    ]
    signals.append({"symbol": "G1", "day": "2024-06-03", "action": LONG_ACTIONS[0], "setup": "RIGHT_SIDE_BREAKOUT",
                    "fill_status": "GAP_BELOW_STOP", "open_status": "OPEN_OUTSIDE_ZONE"})
    signals.append({"symbol": "G2", "day": "2024-06-04", "action": LONG_ACTIONS[1], "setup": "LEFT_SIDE_PULLBACK",
                    "fill_status": "GEOMETRY_INVALID"})
    base_rows = [_outcome_row(i, 0.0, 0.003) for i in range(120)]
    s = ev.summarize(signals, {"transplant": base_rows, "own_plan": base_rows}, note="测试")

    assert s["n_signals"] == 122 and sum(s["fill_status"].values()) == 122
    assert s["fill_status"]["GAP_BELOW_STOP"] == 1 and s["fill_status"]["GEOMETRY_INVALID"] == 1
    assert s["fill_layer"]["n_episodes"] == 120
    mean = s["fill_layer"]["mean_net_return_pct"]
    assert mean == pytest.approx(0.333, abs=1e-3)
    assert s["cost_2x"]["fill_layer"]["mean_net_return_pct"] == pytest.approx(mean - 0.3, abs=1e-3)
    assert s["excess_vs_same_day_random"]["mean_pct"] == pytest.approx(mean, abs=1e-3)
    crit = s["criteria_diagnostic"]
    assert set(crit) == {"note", "P1_mean_net_positive", "P2a_rate_excess", "P2b_mean_excess", "P3_sample", "T_rate_ge_70"}
    assert crit["P3_sample"] is True and crit["note"] == "测试"
    assert set(s["groups"]["action"]) == set(LONG_ACTIONS)
    assert set(s["groups"]["setup"]) == {"RIGHT_SIDE_BREAKOUT", "LEFT_SIDE_PULLBACK"}
    assert set(s["groups"]["year"]) == {"2024"}


def test_summarize_flags_small_samples() -> None:
    signals = [_outcome_row(i, 0.02, 0.023, action=LONG_ACTIONS[0], setup="RIGHT_SIDE_BREAKOUT") for i in range(20)]
    s = ev.summarize(signals, {"transplant": [], "own_plan": []}, note="测试")
    assert s["criteria_diagnostic"]["P3_sample"] is False
    assert s["criteria_diagnostic"]["P2b_mean_excess"] is False  # 没有基准时不能判为通过
    assert s["excess_vs_same_day_random"] is None
```

- [ ] **Step 2：运行测试，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_main_plan.py -k summarize -v`
Expected: FAIL，`AttributeError: module 'main_plan_eval' has no attribute 'summarize'`

- [ ] **Step 3：在 `research/main_plan_eval.py` 追加汇总与 CLI**

把 import 区改成下面这样（并入 Task 2 已有的 `aggregate`、`common` 两行，不要重复写）：

```python
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
```

在常量区补充：

```python
SPEC = "docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md"
OUT = ART / "dev_main_plan_v3_metrics.json"
LEDGER = ART / "trial_ledger.jsonl"
HYPOTHESIS = "MAIN_LONG_PLAN"
```

文件末尾追加：

```python
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
```

- [ ] **Step 4：运行测试，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_main_plan.py -v`
Expected: 8 passed

- [ ] **Step 5：端到端冒烟（每市场 3 只、单进程）**

Run: `uv run --directory kline-structure-chart python ../research/main_plan_eval.py --markets US --limit-symbols 3 --workers 1 --out "$(pwd)/artifacts/long_backtest/dev_main_plan_v3_smoke.json"`
Expected: 约 5 分钟（线上流水线单次约 70ms，3 只 × 约 1,400 个股票-日）；打印 `[US] signals=... scan_errors=0` 与 `wrote .../dev_main_plan_v3_smoke.json`；用下面的命令确认各状态计数之和等于信号数、基准抽样有数据：

```bash
python3 -c "
import json; d=json.load(open('artifacts/long_backtest/dev_main_plan_v3_smoke.json'))['markets']['US']
s=d['summary']; assert sum(s['fill_status'].values())==s['n_signals'], s['fill_status']
print(s['n_signals'], s['fill_status'], d['baseline_sampling'], d['scan'])"
```

- [ ] **Step 6：跑门禁并确认冻结文件未改动**

Run: `git status --porcelain -- research/common.py research/run_backtest.py research/aggregate.py research/discover_alpha_dev.py research/run_dev_region_v2.py research/run_h1_holdout.py research/write_preregistration_v2.py research/h2_forward.py kline-structure-chart/scripts && uv run --directory kline-structure-chart pytest -q && uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`
Expected: `git status` 无输出；全部 passed；`failing=0`

- [ ] **Step 7：提交**

```bash
git add research/main_plan_eval.py research/tests/test_main_plan.py
git commit -m "$(cat <<'EOF'
feat(research): 主做多计划检验汇总口径（P1/P2b/P3 诊断、分组、费用翻倍）与开发区 CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4：开发区诊断运行（一次，样本内）

**Files:**
- Modify: `docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md`（追加第 9 节）
- 产物（不提交，`artifacts/` 已被 git 忽略）：`artifacts/long_backtest/dev_main_plan_v3_metrics.json`、`artifacts/long_backtest/main_plan/dev_*`

**Interfaces:**
- Consumes：Task 3 的 CLI。
- Produces：开发区结果 JSON（Task 5 的预注册引用其 sha256）；设计文档第 9 节。

从本任务起，线上主计划代码冻结（Global Constraints）。

- [ ] **Step 1：确认工作区干净、记录提交号**

Run: `git status --porcelain && git rev-parse HEAD`
Expected: 第一条无输出；记下提交号，写入第 9 节

- [ ] **Step 2：计时冒烟（每市场 30 只、8 进程）**

Run: `uv run --directory kline-structure-chart python ../research/main_plan_eval.py --markets CN,HK,US --limit-symbols 30 --workers 8 --out "$(pwd)/artifacts/long_backtest/dev_main_plan_v3_smoke.json"`
Expected: 约 20 分钟；三个市场都打印 `scan_errors=0`；输出里的 `elapsed_seconds` 按"全量标的数 / 30"线性外推出全量耗时（预计 12~16 小时），写进第 9 节

- [ ] **Step 3：后台运行全量开发区（防止休眠）**

用 Bash 的 `run_in_background` 运行：

```bash
caffeinate -is uv run --directory kline-structure-chart python ../research/main_plan_eval.py --markets CN,HK,US --workers 8 > artifacts/long_backtest/dev_main_plan_v3.log 2>&1
```

Expected: 进程退出码 0；日志末行为 `wrote .../dev_main_plan_v3_metrics.json`。中途中断时直接重跑同一命令：第一遍会复用已完成分片

- [ ] **Step 4：核对输出完整性**

```bash
python3 -c "
import json; d=json.load(open('artifacts/long_backtest/dev_main_plan_v3_metrics.json'))
print(d['code_commit'], d['elapsed_seconds'])
for m,v in d['markets'].items():
    s=v['summary']; assert sum(s['fill_status'].values())==s['n_signals']
    print(m, v['scan']['rows'], v['scan']['errors'], v['scan']['skipped_symbols'], s['n_signals'],
          s['fill_layer'].get('n_episodes'), s['excess_vs_same_day_random'], s['criteria_diagnostic'],
          v['baseline_sampling']['counts'])"
```

Expected: `code_commit` 不带 `-dirty`；每个市场 `errors` 为 0（不为 0 时，把 `error_examples` 写入第 9 节，并判断是否属于缺陷）；状态计数之和等于信号数

- [ ] **Step 5：在设计文档追加第 9 节"开发区诊断结果（样本内，不构成验收）"**

内容逐项取自 `dev_main_plan_v3_metrics.json`，不重新计算：
- 运行信息：提交号、耗时、各市场扫描的股票-日数、错误数、加载失败的标的数；
- 主表（每市场一行）：信号数、episode / 信号日（最大单日占比）、净正率、扣费单笔均值 [分块 95%]、移植几何基准均值、均值超额（P2b）[分块 95%]、P1 / P2b / P3 诊断；
- 只报告项：P2a、`TP1 先于 SL`、按评级 / 入场类型 / 年份分组、次日开盘口径、自身计划诊断基准、费用翻倍、成交状态分布、剔除最大单日的敏感性（若有）；
- 一句话解读，并写明"开发区结果不改变线上，按设计照常进入 H1"。

- [ ] **Step 6：提交**

```bash
git add docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md
git commit -m "$(cat <<'EOF'
docs(spec): 记录主做多计划开发区诊断结果（样本内，不调参）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5：H1 守卫与预注册 v3（冻结）

**Files:**
- Create: `research/run_h1_main_plan.py`
- Create: `research/write_preregistration_v3_main_plan.py`
- Create: `research/tests/test_h1_main_plan.py`
- Create（生成后提交）：`research/preregistration/preregistration_v3_main_plan.json`
- Modify: `docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md`（第 6.3 节数据核对方式、新增第 10 节冻结记录）

**Interfaces:**
- Consumes：`run_h1_holdout.verify_preregistration(prereg_path, check_git=True) -> (dict, list[str])`、`run_h1_holdout.judge(seg, gates) -> dict`（读 `seg["criteria_diagnostic"]`）；Task 3 的 `ev.run_market`、`ev.summarize`、`ev.config`、`ev.SEED`、`ev.HYPOTHESIS`；Task 1 的 `scan.H1_AUTHORIZED`。
- Produces：
  - `run_h1_main_plan.verify_cache_files(markets: list[str], universe_path: Path, cache_dir: Path = CACHE) -> list[str]`
  - `run_h1_main_plan.verify_all(prereg_path: Path = PREREG, check_git: bool = True) -> tuple[dict, list[str]]`
  - `run_h1_main_plan.main()`：退出码 2（校验不符）、3（结果已存在）、4（锁存在且未确认）
  - `write_preregistration_v3_main_plan.build() -> dict`

- [ ] **Step 1：写失败测试**

`research/tests/test_h1_main_plan.py`：

```python
"""主做多计划 H1 守卫与预注册 v3：篡改检测、缓存与清单逐个核对、只能运行一次、判定只用登记的门槛。均不评估任何 H1 数据。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import main_plan_eval as ev  # noqa: E402
import run_h1_holdout as h1  # noqa: E402
import run_h1_main_plan as g3  # noqa: E402
from common import CACHE, LONG_ACTIONS, PIT_UNIVERSE  # noqa: E402

pytestmark = pytest.mark.skipif(not PIT_UNIVERSE.exists(), reason="需要 artifacts/long_backtest 真实行情缓存")


def test_prereg_v3_detects_tampering(tmp_path) -> None:
    import write_preregistration_v3_main_plan as wp3

    prereg = wp3.build()
    assert prereg["acceptance"]["gates"] == ["P1", "P2b", "P3"]
    assert prereg["acceptance"]["acceptance_markets"] == ["CN", "US", "HK"]
    assert "research/main_plan_scan.py" in prereg["frozen_files_sha256"]
    assert prereg["baseline"]["seed"] == ev.SEED
    path = tmp_path / "prereg.json"
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert problems == []
    prereg["frozen_files_sha256"]["research/main_plan_eval.py"] = "0" * 64
    path.write_text(json.dumps(prereg), encoding="utf-8")
    _, problems = h1.verify_preregistration(path, check_git=False)
    assert any("main_plan_eval.py" in p for p in problems)


def test_cache_files_checked_against_manifest(tmp_path) -> None:
    """复制两个真实缓存文件到临时目录：清单一致时通过；文件被改动或清单缺项时拒绝。"""
    cache = tmp_path / "cache"
    for rel in ("US/SPY.US.json.gz", "US/_VIX.json.gz"):
        (cache / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CACHE / rel, cache / rel)
    entries = [{"file": rel, "sha256": hashlib.sha256((cache / rel).read_bytes()).hexdigest()}
               for rel in ("US/SPY.US.json.gz", "US/_VIX.json.gz")]
    (cache / "MANIFEST.json").write_text(json.dumps({"files": entries}), encoding="utf-8")
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"markets": {"US": {"periods": [{"symbols": []}]}}}), encoding="utf-8")
    assert g3.verify_cache_files(["US"], universe, cache) == []
    with (cache / "US/_VIX.json.gz").open("ab") as fh:
        fh.write(b"\0")
    assert any("_VIX" in p for p in g3.verify_cache_files(["US"], universe, cache))
    (cache / "MANIFEST.json").write_text(json.dumps({"files": entries[1:]}), encoding="utf-8")
    assert any("清单缺少" in p and "SPY.US" in p for p in g3.verify_cache_files(["US"], universe, cache))


def test_runner_refuses_second_run_and_unconfirmed_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(g3, "verify_all", lambda *a, **k: ({}, []))
    result, lock = tmp_path / "h1_main_plan_result.json", tmp_path / "h1_main_plan_started.json"
    monkeypatch.setattr(g3, "RESULT", result)
    monkeypatch.setattr(g3, "LOCK", lock)
    monkeypatch.setattr(sys, "argv", ["run_h1_main_plan.py"])
    result.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        g3.main()
    assert e.value.code == 3
    result.unlink()
    lock.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        g3.main()
    assert e.value.code == 4


def test_runner_refuses_when_verification_fails(monkeypatch) -> None:
    monkeypatch.setattr(g3, "verify_all", lambda *a, **k: ({}, ["冻结文件已改动：x"]))
    monkeypatch.setattr(sys, "argv", ["run_h1_main_plan.py", "--check"])
    with pytest.raises(SystemExit) as e:
        g3.main()
    assert e.value.code == 2


def test_summary_feeds_registered_judge() -> None:
    """ev.summarize 的判定字段可直接交给 run_h1_holdout.judge，且只用登记的门槛。"""
    rows = [{"symbol": f"S{i:03d}", "day": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", "fill_status": "FILL",
             "net": 0.01, "gross": 0.013, "outcome": "TP_BARRIER", "action": LONG_ACTIONS[0],
             "setup": "RIGHT_SIDE_BREAKOUT"} for i in range(120)]
    base = [{**r, "symbol": f"B{i:03d}", "net": -0.01, "gross": -0.007, "outcome": "SL_BARRIER"} for i, r in enumerate(rows)]
    s = ev.summarize(rows, {"transplant": base, "own_plan": base}, note="测试")
    v = h1.judge(s, ["P1", "P2b", "P3"])
    assert v["pass"] is True and set(v["checks"]) == {"P1", "P2b", "P3"}
```

- [ ] **Step 2：运行测试，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_h1_main_plan.py -v`
Expected: 收集阶段报 `ModuleNotFoundError: No module named 'run_h1_main_plan'`

- [ ] **Step 3：实现 `research/run_h1_main_plan.py`**

```python
#!/usr/bin/env python3
"""主做多计划留出集 H1 一次性验收（主做多计划有效性检验设计第 6.3 节）。

运行前校验 research/preregistration/preregistration_v3_main_plan.json：冻结文件与数据的 sha256、冻结文件与预注册
无未提交改动、预注册已提交（复用 run_h1_holdout.verify_preregistration）；并逐个核对 H1 成分股、基准与 VIX 的
缓存文件与行情缓存清单一致。任何一项不符就拒绝运行。

只能成功运行一次：结果文件已存在时拒绝运行；启动时写锁文件，锁存在而结果缺失（中途崩溃）时，需要显式加
--restart-after-crash 才能重跑。第一遍分片只含信号日当时可见的信息，续跑会复用已完成分片。
`--check` 只做校验，不评估任何 H1 数据。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import run_h1_holdout as h1
from common import ART, BENCHMARKS, CACHE, ROOT, VIX_SYMBOL

PREREG = ROOT / "research" / "preregistration" / "preregistration_v3_main_plan.json"
RESULT = ART / "h1_main_plan_result.json"
LOCK = ART / "h1_main_plan_started.json"
LEDGER = ART / "trial_ledger.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_cache_files(markets: list[str], universe_path: Path, cache_dir: Path = CACHE) -> list[str]:
    """H1 成分股、基准与 VIX 的缓存文件 sha256 必须与行情缓存清单逐个一致（清单本身的 hash 已在预注册中冻结）。"""
    manifest = json.loads((cache_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    digests = {e["file"]: e["sha256"] for e in manifest["files"]}
    universe = json.loads(Path(universe_path).read_text(encoding="utf-8"))["markets"]
    problems: list[str] = []
    for market in markets:
        symbols = {s for p in universe[market]["periods"] for s in p["symbols"]} | {BENCHMARKS[market]["symbol"]}
        if market == "US":
            symbols.add(VIX_SYMBOL)
        for sym in sorted(symbols):
            rel = f"{market}/{sym.replace('^', '_')}.json.gz"
            f = cache_dir / rel
            if rel not in digests:
                problems.append(f"行情缓存清单缺少：{rel}")
            elif not f.exists() or _sha256(f) != digests[rel]:
                problems.append(f"缓存文件与清单不符：{rel}")
    return problems


def verify_all(prereg_path: Path = PREREG, check_git: bool = True) -> tuple[dict, list[str]]:
    prereg, problems = h1.verify_preregistration(prereg_path, check_git=check_git)
    problems += verify_cache_files(prereg["h1"]["markets"], ROOT / prereg["h1"]["universe"])
    return prereg, problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验预注册与运行条件，不评估任何 H1 数据")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--restart-after-crash", action="store_true")
    args = ap.parse_args()

    prereg, problems = verify_all()
    if problems:
        print("预注册校验未通过，拒绝运行：\n- " + "\n- ".join(problems[:20]))
        sys.exit(2)
    if RESULT.exists():
        print(f"主做多计划 H1 已经评估过（{RESULT.name} 已存在），按预注册只能运行一次，拒绝重跑")
        sys.exit(3)
    if LOCK.exists() and not args.restart_after_crash:
        print(f"发现锁文件 {LOCK.name} 但没有结果：上次运行可能中途崩溃。确认未查看任何 H1 结果后，加 --restart-after-crash 重跑")
        sys.exit(4)
    print("预注册 v3 校验通过" + ("（仅校验，未评估 H1）" if args.check else ""))
    if args.check:
        return

    import main_plan_eval as ev
    import main_plan_scan as scan
    import run_dev_region_v2 as v2

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    LOCK.write_text(json.dumps({"started_at_utc": started, "preregistration_sha256": _sha256(PREREG)}), encoding="utf-8")
    scan.H1_AUTHORIZED = True
    spec = prereg["acceptance"]
    result: dict = {
        "started_at_utc": started,
        "preregistration_sha256": _sha256(PREREG),
        "code_commit": v2._git_head(),
        "hypothesis": ev.HYPOTHESIS,
        "note": "主做多计划留出集 H1，一次性评估",
        "markets": {},
    }
    for market in prereg["h1"]["markets"]:
        res = ev.run_market(market, mode="h1", workers=args.workers)
        verdict = h1.judge(res["summary"], spec["gates"])
        result["markets"][market] = {"role": "acceptance", "verdict": verdict, **res}
        fl = res["summary"]["fill_layer"]
        print(f"[{market}] episodes={fl.get('n_episodes')} days={fl.get('n_signal_days')} "
              f"mean={fl.get('mean_net_return_pct')}% verdict={verdict}", flush=True)
    result["finished_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["finished_at_utc"], "script": "research/run_h1_main_plan.py",
                             "hypothesis": ev.HYPOTHESIS, "code_commit": result["code_commit"],
                             "preregistration_sha256": result["preregistration_sha256"],
                             "verdicts": {m: v["verdict"] for m, v in result["markets"].items()}},
                            ensure_ascii=False) + "\n")
    print(f"wrote {RESULT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4：实现 `research/write_preregistration_v3_main_plan.py`**

```python
#!/usr/bin/env python3
"""生成主做多计划留出集 H1 的预注册 v3（主做多计划有效性检验设计第 6.3 节）。

把将要冻结的代码文件与数据清单的 sha256、主计划函数源码 hash、H1 区间、验收门槛、随机基准设置、
登记前试验次数与披露事项写入 research/preregistration/preregistration_v3_main_plan.json。
生成后需提交到 git 才算冻结；run_h1_main_plan.py 运行前会逐项校验。本脚本不读取任何 H1 结果。
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import main_plan_eval as ev
import run_dev_region_v2 as v2
from common import ART, LONG_ACTIONS, ROOT

import risk  # noqa: E402  (common 已把技能 scripts 加入 sys.path)

OUT = ROOT / "research" / "preregistration" / "preregistration_v3_main_plan.json"
RESEARCH_FILES = [
    "common.py", "run_backtest.py", "aggregate.py", "discover_alpha_dev.py", "run_dev_region_v2.py",
    "run_h1_holdout.py", "main_plan_scan.py", "main_plan_eval.py", "run_h1_main_plan.py",
    "write_preregistration_v3_main_plan.py",
]
DATA_FILES = ["artifacts/long_backtest/pit_universe_h1.json", "artifacts/long_backtest/cache/MANIFEST.json"]
DEV_RESULT = ART / "dev_main_plan_v3_metrics.json"
GATE_DEFS = {
    "P1": "扣费后单笔净收益均值 > 0，且按信号日期分块 bootstrap 95% 下限 > 0（5 日限价成交层，episode 口径）",
    "P2b": "单笔净收益均值 − 同日随机入场（移植几何）均值的分块 95% 下限 > 0",
    "P3": "episode >= 100 且不同信号日 >= 30",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _trials() -> dict:
    ledger = ART / "trial_ledger.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()] if ledger.exists() else []
    mine = [r for r in rows if r.get("hypothesis") == ev.HYPOTHESIS and r.get("script") == "research/main_plan_eval.py"]
    return {
        "main_plan_runs": len(mine),
        "main_plan_configs_evaluated": sum(int(r.get("n_configs_evaluated") or 1) for r in mine),
        "other_hypothesis_ledger_rows": len(rows) - len(mine),
        "note": "主计划开发区运行（含冒烟）只评估现行默认规则；其余台账行属于反转层（另一假设）",
    }


def build() -> dict:
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "kline-structure-chart" / "scripts").glob("*.py"))
    files += [f"research/{f}" for f in RESEARCH_FILES]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    return {
        "schema_version": "3.0",
        "kind": "PREREGISTRATION",
        "hypothesis": ev.HYPOTHESIS,
        "registered_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "base_commit": head,
        "spec": ev.SPEC,
        "frozen_files_sha256": {f: _sha(ROOT / f) for f in files},
        "frozen_data_sha256": {f: _sha(ROOT / f) for f in DATA_FILES},
        "frozen_specification": {
            "plan": "risk.compute_triple_barrier_plan",
            "plan_source_sha256": hashlib.sha256(inspect.getsource(risk.compute_triple_barrier_plan).encode()).hexdigest(),
            "signal_actions": list(LONG_ACTIONS),
            "fill": f"5 日限价（{v2.LIMIT_SESSIONS} 个交易日内以入场区上沿成交，低开按开盘价）；成交价不高于止损记 GAP_BELOW_STOP",
            "exit": "evaluation.label_triple_barrier_event：止损/TP1、同根先判止损、保本上移参数取自计划 breakeven_ratchet、T+1 与一字跌停顺延",
            "horizon_bars": v2.HORIZON,
            "episode_gap_days": 21,
            "next_open_layer": "次日开盘落在入场区内才成交（LIVE_PLAN_REPLAY_V6 规则），只报告",
        },
        "h1": {
            "markets": ["CN", "US", "HK"],
            "first_day": v2.H1_FIRST_DAY,
            "last_day": v2.H1_LAST_DAY,
            "universe": DATA_FILES[0],
            "warmup_bars": v2.WARMUP_BARS,
        },
        "acceptance": {
            "acceptance_markets": ["CN", "US", "HK"],
            "diagnostic_markets": [],
            "gates": ["P1", "P2b", "P3"],
            "gate_definitions": GATE_DEFS,
            "report_only": {
                "P2a": "episode 净正率 − 同日随机入场（移植几何）净正率的分块 95% 下限 > 0",
                "T": "episode 净正率点估计 >= 70%",
            },
            "rule": "每个市场单独判定，三项全部满足为通过；P3 不满足同样判为未通过。未通过的市场线上降级为观察位（设计 M4、第 7.1 节）；不得回到 H1 调参",
        },
        "baseline": {
            "kind": "same_day_random_transplanted_geometry",
            "definition": "信号日按 min(1, 10 × 当日信号数 / 当日股票池规模) 抽股，按哈希对应当日一个信号，把其价位的 ATR 倍数套到被抽股票的收盘价与线上 ATR14 上并取整；同撮合、同出场、同费用、同 21 天 episode 去重",
            "sampling_multiple": ev.BASELINE_MULTIPLE,
            "seed": ev.SEED,
            "diagnostic": "own_plan：被抽股票使用自身计划价位，只报告",
            "bootstrap": {"block": "signal_day", "n_boot": 2000, "seed": 20261020},
        },
        "dev_region_evidence": {
            "file": str(DEV_RESULT.relative_to(ROOT)),
            "sha256": _sha(DEV_RESULT) if DEV_RESULT.exists() else None,
        },
        "trials_before_registration": _trials(),
        "disclosures": [
            "H1 区间已被反转层验收使用过一次（h1_result.json 不含任何主计划统计）",
            "开发区主计划结果 v1 已看过，开发区结果属样本内",
            "幸存者偏差：股票池只含当前上市标的，2017–2020 年退市股不在池中",
            "ST 历史不可得，按当前名称判断",
            "港股成交稀疏：H1 港股池每期只有 51.6%~57.9% 的成分股期内 ≥95% 交易日有真实成交",
            "交易日历只覆盖 2021 年起，不在本回测路径上",
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    if not DEV_RESULT.exists():
        raise SystemExit(f"缺少开发区结果 {DEV_RESULT}：按设计须先完成开发区诊断再冻结")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5：运行测试，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_h1_main_plan.py ../research/tests/test_main_plan.py -v`
Expected: 13 passed

- [ ] **Step 6：跑门禁并确认冻结文件未改动**

Run: `git status --porcelain -- research/common.py research/run_backtest.py research/aggregate.py research/discover_alpha_dev.py research/run_dev_region_v2.py research/run_h1_holdout.py research/write_preregistration_v2.py research/h2_forward.py kline-structure-chart/scripts && uv run --directory kline-structure-chart pytest -q && uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`
Expected: `git status` 无输出；全部 passed；`failing=0`

- [ ] **Step 7：提交守卫与生成器（此时尚未冻结）**

```bash
git add research/run_h1_main_plan.py research/write_preregistration_v3_main_plan.py research/tests/test_h1_main_plan.py
git commit -m "$(cat <<'EOF'
feat(research): 主做多计划 H1 一次性验收守卫与预注册 v3 生成器（尚未冻结）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8：冻结前独立审查（H1 只有一次机会）**

用 superpowers:requesting-code-review 对本分支相对 `origin/main` 的全部改动做一次审查，重点：两遍之间无前视、基准抽样与配对、判定字段、守卫的拒绝路径。审查发现的研究管线缺陷修复后重跑 Task 4 并更新第 9 节；线上主计划只按第 6.2 节处理缺陷。

- [ ] **Step 9：同步 main 并核对技能代码差异**

Run: `git fetch origin && gh pr view 7 --json state -q .state`
- PR #7 已合并时：`git merge origin/main`，解决冲突后提交。
- 然后核对开发区运行提交（第 9 节记录）到 HEAD 之间的技能代码差异：

Run: `git diff --stat <开发区运行提交> HEAD -- kline-structure-chart/scripts`
Expected: 只出现不在 `analyze_bundle` 调用链上的文件（如 `lint_response.py`、`render_*.py`），或已记入设计第 6.5 节的缺陷修复。若出现 `risk.py`、`analysis.py`、`indicators.py` 等调用链上的其他改动，先确认属于第 6.2 节的缺陷修复并记入第 6.5 节，再按第 6.2 节核对差异：默认重跑 Task 4；只在非法输入时改变输出的修复可改用抽样等价核对（`research/check_d1_equivalence.py` 的做法），抽样中信号有变化时仍须重跑 Task 4。

（修订 2026-10-21，用户确认）D1（`structure.py`，提交 `cf51a1d`）按抽样等价核对处理，不重跑 Task 4：第 9 节使用 `509e499` 的开发区结果，预注册 `dev_region_evidence` 登记开发区运行提交与 `artifacts/long_backtest/d1_equivalence.json` 的 sha256，并写入披露。

- [ ] **Step 10：同步设计文档第 6.3 节的数据核对方式**

原文：

```text
- **数据 hash 在冻结时重新登记**，不直接引用 v2 的登记值。本机做 H2 联网实测时增量刷新过部分标的缓存，冻结前须核对各市场成分股在 H1 区间内的K线与 v2 冻结时逐根相同；不同之处如实写入预注册。
```

替换为：

```text
- **数据 hash 在冻结时重新登记**，不直接引用 v2 的登记值。守卫在运行前逐个核对 H1 成分股、基准与 VIX 的缓存文件 sha256 与行情缓存清单一致（清单本身的 hash 已冻结），比逐根核对K线更严。2026-10-21 核对：清单 hash 与 v2 冻结值相同，且没有缓存文件晚于 v2 预注册被改动。
```

- [ ] **Step 11：生成预注册并提交冻结**

```bash
uv run --directory kline-structure-chart python ../research/write_preregistration_v3_main_plan.py
git add research/preregistration/preregistration_v3_main_plan.json docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md
git commit -m "$(cat <<'EOF'
chore(research): 冻结主做多计划留出集 H1 预注册 v3（硬门槛 P1、P2b、P3，三市场各自判定）

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 12：守卫自检（不评估 H1）**

Run: `uv run --directory kline-structure-chart python ../research/run_h1_main_plan.py --check`
Expected: `预注册 v3 校验通过（仅校验，未评估 H1）`

- [ ] **Step 13：在设计文档追加第 10 节"冻结记录"并提交**

写入：预注册 sha256（`shasum -a 256 research/preregistration/preregistration_v3_main_plan.json`）、`base_commit`、冻结文件数与数据文件数、登记前主计划运行次数（取自预注册 `trials_before_registration`）。

```bash
git add docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md
git commit -m "$(cat <<'EOF'
docs(spec): 记录主做多计划预注册 v3 冻结信息

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

注意：这次提交只改设计文档，不在冻结文件列表内，不影响守卫校验；提交后再跑一次 Step 12 确认。

---

### Task 6：H1 一次性验收

**Files:**
- Modify: `docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md`（追加第 11 节）
- 产物（不提交）：`artifacts/long_backtest/h1_main_plan_result.json`、`h1_main_plan_started.json`

**Interfaces:**
- Consumes：Task 5 的守卫与已提交的预注册 v3。
- Produces：三个市场的判定，作为后续"线上降级"计划的输入。

- [ ] **Step 1：运行前最后确认**

Run: `git status --porcelain && uv run --directory kline-structure-chart python ../research/run_h1_main_plan.py --check`
Expected: 第一条无输出；第二条打印"预注册 v3 校验通过（仅校验，未评估 H1）"

- [ ] **Step 2：后台运行 H1（只此一次）**

用 Bash 的 `run_in_background` 运行：

```bash
caffeinate -is uv run --directory kline-structure-chart python ../research/run_h1_main_plan.py --workers 8 > artifacts/long_backtest/h1_main_plan.log 2>&1
```

Expected: 退出码 0，日志末行 `wrote .../h1_main_plan_result.json`。中途崩溃时：不查看任何分片或日志中的结局字段，确认后加 `--restart-after-crash` 重跑同一命令（第一遍复用已完成分片）

- [ ] **Step 3：读取判定**

```bash
python3 -c "
import json; d=json.load(open('artifacts/long_backtest/h1_main_plan_result.json'))
print(d['preregistration_sha256'], d['code_commit'])
for m,v in d['markets'].items():
    s=v['summary']; print(m, v['verdict'], s['fill_layer'].get('n_episodes'), s['fill_layer'].get('n_signal_days'),
          s['fill_layer'].get('mean_net_return_pct'), s['fill_layer'].get('mean_date_cluster95_pct'),
          s['excess_vs_same_day_random'], v['scan']['errors'])"
```

- [ ] **Step 4：在设计文档追加第 11 节"留出集 H1 验收结果（一次性）"**

格式与反转层方案第 11 节一致：运行信息（提交、预注册 sha256、起止时间）；判定表（每市场：信号数、episode / 信号日（最大单日占比）、净正率、扣费单笔均值 [区间]、移植几何基准均值、P2b [区间]、P1 / P2b / P3、判定）；只报告项；解读；按 M4 写明需要降级的市场清单。数字全部取自 `h1_main_plan_result.json`。

- [ ] **Step 5：提交**

```bash
git add docs/superpowers/specs/2026-10-20-main-long-plan-validation-design.md
git commit -m "$(cat <<'EOF'
docs(spec): 记录主做多计划留出集 H1 一次性验收结果

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6：交接线上处理**

按第 11 节的判定，为未通过的市场另写"主做多计划线上降级"实施计划（设计第 7.1 节：`MAIN_PLAN_PAUSED_MARKETS`、`main_plan_paused_action`、观察位呈现、`lint_response.py`、技能自带回测、`SKILL.md` / `references/output-fields.md` / schema / `CHANGELOG.md`）。全部通过时，只在 `CHANGELOG.md` 记录结论。
