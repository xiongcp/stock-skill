# 分状态归因（阶段 0）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用已有的开发区主计划记录回答两个问题：计划收益里有多少只是持有期大盘的涨跌；正常波动范围在大盘弱的时候覆盖率是否还够。

**Architecture:** 在研究分支新增一个脚本 `research/regime_attribution.py`，只调用已冻结的评估函数（`main_plan_eval`、`run_dev_region_v2`、`aggregate`），不修改它们。先按 (市场, 日) 重算基准状态表，再把第二遍撮合结果补上出场日与持有期基准收益，按四个事先写定的变量分组；另做一次约 2% 的抽样，重算正常波动范围并统计分状态覆盖率。不分组时必须逐项复现已登记的开发区主表，否则直接报错。

**Tech Stack:** Python ≥ 3.12，numpy（研究分支 dev 依赖），pytest（研究分支没有 pytest-xdist，不要加 `-n`），uv。

**Spec:** `docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md` 第 2 节

## Global Constraints

- 分支 `research/regime-attribution` 从 `research/main-long-plan-validation`（`ef1caac`）切出；**在主检出目录里做，不用 worktree**：脚本从 `artifacts/long_backtest/` 读数据，而 `artifacts/` 不在 git 里。
- 只新增 `research/regime_attribution.py` 与 `research/tests/test_regime_attribution.py`。不修改任何已有的 `research/*.py`、`research/preregistration/*` 与 `kline-structure-chart/scripts/*`（预注册 v2 / v3 冻结了其中的文件）。
- 不重新扫描开发区（D3=b）：只读 `dev_main_plan_v3_metrics.json` 登记的三个分片目录与行情缓存。不调用 `scan.run_scan`。
- 不读取留出集 H1：只用 `mode="dev"`，区间 `2021-01-04 ~ 2026-09-24`。不 import `run_h1_*`。
- 只用真实缓存行情；测试不构造行情序列。
- 结果全部是样本内描述，不构成验收；本阶段不改任何规则；分组里出现的任何"优势"只能当作新假设。
- 分组变量与分桶边界事先写定，不看结果调整：状态分 `<35、[35,45)、[45,55)、[55,70)、≥70`；格子不足 100 个 episode 或 30 个信号日标"样本不足"；覆盖率格子不足 300 个样本或 30 个日期标"样本不足"。
- 口径沿用冻结值：episode 去重 21 个自然日、5 日限价成交、`n_boot = 2000`、`seed = 20261020`、同日随机移植几何基准。
- 这个分支的引擎是 5.3.0（生成开发区记录的版本，外加 `cf51a1d` 的除零修复），不是 main 的 6.0.0。切到这个分支后 `uv run` 会按它的 `uv.lock` 重建虚拟环境，切回 main 时再同步一次，属正常现象。
- 推送分支之前先向用户确认。

## Review Focus

1. **代码或缓存与生成记录时不一致**：不分组的汇总与 `dev_main_plan_v3_metrics.json` 有任何一项不同，程序必须报错退出，不输出分组结果。→ Task 4 的 `check_reproduction` 与 `test_reproduction_check_rejects_mismatch`。
2. **出场日重算与原撮合不一致**（成交日找不到、净收益不同）：必须报错，不能悄悄用错的持有期。→ Task 2 的 `eval_shard_with_exit` 内的核对与 `test_exit_day_and_bench_hold_return`。
3. **成交日或出场日不是基准交易日**（港股、美股个别日期基准缺K线）：持有期基准收益取成交日之前最近一个基准收盘到出场日当天或之前最近一个基准收盘，不报错。→ Task 2 的 `test_bench_hold_return_on_non_benchmark_days`。
4. **某个格子为空或太小**：空格子不出现；小格子照常给数字并标 `insufficient_sample = true`，bootstrap 返回 `None` 时原样写 `null`。→ Task 3 的 `test_cells_conserve_and_match_frozen_stats`。
5. **覆盖率抽样中个别股票-日流水线报错**（约 12 万次调用）：记为错误并计数，不中断整轮，也不补数。→ Task 5 的 `coverage_shard` 与 `test_coverage_sampling_and_record`。

---

## File Structure

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `research/regime_attribution.py` | 新建 | 基准状态表、出场日与持有期基准收益、分组与回归、复现核对、覆盖率抽样、命令行入口、Markdown 摘要 |
| `research/tests/test_regime_attribution.py` | 新建 | 上述各部分的测试（真实缓存行情；缺缓存时整体跳过） |
| `artifacts/long_backtest/regime_attribution.json` | 运行产物 | 0A 与 0B 的全部格子（不入库） |
| `artifacts/long_backtest/trial_ledger.jsonl` | 追加一行 | 假设名 `REGIME_ATTRIBUTION`（不入库） |
| `docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md` | 追加一节 | "阶段 0 结果"（在规格所在分支上提交） |

开工前：

```bash
git status --short            # 必须干净；阶段 1 的改动若还没提交，先提交或另开 worktree 做阶段 1
git checkout -b research/regime-attribution research/main-long-plan-validation
ls artifacts/long_backtest/main_plan/      # 应有 dev_CN_5c70d51d5fee、dev_HK_6042057c8c66、dev_US_3e5a1c72fcc7
uv run --directory kline-structure-chart pytest ../research/tests/test_main_plan.py -q
```

预期：已有的研究测试通过（记录通过数与耗时，作为基线）。

---

### Task 1: 基准状态表

**Files:**
- Create: `research/regime_attribution.py`
- Test: `research/tests/test_regime_attribution.py`

**Interfaces:**
- Consumes: `scan.init(market, "dev")`（填充 `v2._C`：`bench`、`bench_days`、`vix`、`region`、`periods`）；`run_backtest.WINDOW_BARS = 760`；`structure.HISTORY_BARS = 420`、`STRUCT_DAYS = 120`；`indicators.compute_all_indicators(bars)`；`strategies.classify_market_regime(bars, snapshot, vix_value=)`。
- Produces:
  - `reference(market: str) -> dict`：`dev_main_plan_v3_metrics.json` 里该市场的一段（含 `scan_dir`、`summary`）。
  - `read_shard(path) -> Iterator[dict]`：分片里的非错误行。
  - `bench_state_table() -> dict[str, dict]`：`{day: {"regime": str, "score": float}}`，覆盖开发区内全部基准交易日。须先 `scan.init`。
  - `score_bucket(score: float) -> str`：`"<35" | "35-45" | "45-55" | "55-70" | ">=70"`。

- [ ] **Step 1: 写失败的测试**

`research/tests/test_regime_attribution.py`：

```python
"""分状态归因：基准状态表与线上一致、出场日与持有期基准收益、分组守恒、复现核对、覆盖率抽样（真实缓存行情）。"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import main_plan_eval as ev  # noqa: E402
import main_plan_scan as scan  # noqa: E402
import regime_attribution as ra  # noqa: E402
import run_dev_region_v2 as v2  # noqa: E402
from common import LONG_ACTIONS, PIT_UNIVERSE  # noqa: E402
from run_backtest import make_pit_slice  # noqa: E402

from analysis import analyze_bundle  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (PIT_UNIVERSE.exists() and ra.REFERENCE.exists()), reason="需要 artifacts/long_backtest 的真实行情缓存与开发区记录"
)


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_bench_state_table_matches_live_pipeline(market: str) -> None:
    """状态表必须与线上流水线在同一股票-日给出的大盘状态逐项相同（每市场约 200 个股票-日）。"""
    scan.init(market)
    table = ra.bench_state_table()
    assert min(table) >= v2.DEV_REGION[0] and max(table) <= v2.DEV_REGION[1]
    symbols = sorted(v2._C["periods"])
    checked = 0
    for sym in symbols[:: max(1, len(symbols) // 8)][:8]:
        data = v2._load_symbol(sym)
        if data is None:
            continue
        days = list(v2._eligible_days(sym, data))
        for day, t, _ in days[:: max(1, len(days) // 25)][:25]:
            pit, cut = make_pit_slice(data["bars"], t, v2.HORIZON)
            bundle, request = v2.build_bundle(sym, data, pit, cut, day)
            mb = analyze_bundle(bundle, request, include_replay=False, include_forecast=False)["market_benchmark"]
            assert (table[day]["regime"], table[day]["score"]) == (mb["regime"], float(mb["regime_score"])), (sym, day)
            checked += 1
    assert checked >= 150


def test_score_bucket_edges() -> None:
    assert [ra.score_bucket(x) for x in (0.0, 34.99, 35.0, 44.99, 45.0, 54.99, 55.0, 69.99, 70.0, 100.0)] == [
        "<35", "<35", "35-45", "35-45", "45-55", "45-55", "55-70", "55-70", ">=70", ">=70"]


def test_reference_points_at_existing_complete_scan() -> None:
    for market in ("CN", "HK", "US"):
        d = ROOT / ra.reference(market)["scan_dir"]
        assert (d / "COMPLETE").exists() and next(ra.read_shard(sorted(d.glob("shard_*.jsonl.gz"))[0]))["sym"]
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'regime_attribution'`

- [ ] **Step 3: 实现**

`research/regime_attribution.py`（导入区一次写全，后面几个任务不再改它）：

```python
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
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q`
Expected: 5 passed（三个市场各约 1 分钟）。

若等价测试失败，先核对三处与 `kline-structure-chart/scripts/analysis.py` 第 4 步是否一致：基准窗口 `[b_hi - 760, b_hi)`、指标用最后 420 根、状态分类用最后 120 根并传入当日 VIX。不要改测试里的相等断言。

- [ ] **Step 5: 提交**

```bash
git add research/regime_attribution.py research/tests/test_regime_attribution.py
git commit -m "feat(research): 分状态归因——按日重算基准状态表并与线上流水线核对"
```

---

### Task 2: 出场日与持有期基准收益

**Files:**
- Modify: `research/regime_attribution.py`
- Test: `research/tests/test_regime_attribution.py`

**Interfaces:**
- Consumes: `ev.eval_shard(path_str) -> (signals, {"transplant": {day: [净收益和, 笔数, 盈利笔数, 费用翻倍净收益和]}, "own_plan": {...}}, counts)`（须先 `ev._eval_init(False, market, "dev", plans)`）；信号行键 `symbol, day, action, setup, fill_status`，成交时另有 `fill_day, fill_price, net, gross, outcome`；`label_triple_barrier_event(...)` 返回 `entry_day, exit_day, net_return`。
- Produces:
  - `bench_hold_return(fill_day: str, exit_day: str) -> float`：成交日之前最近一个基准收盘到出场日当天或之前最近一个基准收盘的收益。
  - `eval_shard_with_exit(path_str: str) -> (signals, base, counts)`：与 `ev.eval_shard` 相同，已成交信号多出 `exit_day` 与 `bench_hold_ret`。

- [ ] **Step 1: 写失败的测试**

```python
@pytest.fixture(scope="module")
def cn_shard() -> tuple[list[dict], dict]:
    """CN 第一个分片的真实记录。基准抽样计划只由这一个分片构成：单测只检验口径，不代表全市场数字。"""
    path = sorted((ROOT / ra.reference("CN")["scan_dir"]).glob("shard_*.jsonl.gz"))[0]
    pool: Counter = Counter()
    sig: dict[str, list[dict]] = defaultdict(list)
    for r in ra.read_shard(path):
        pool[r["day"]] += 1
        if r["a"] in LONG_ACTIONS:
            sig[r["day"]].append(r)
    ev._eval_init(False, "CN", "dev", ev.baseline_plan(sig, pool))
    signals, base, _ = ra.eval_shard_with_exit(str(path))
    return signals, base


def test_exit_day_and_bench_hold_return(cn_shard) -> None:
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    signals, _ = cn_shard
    fills = [s for s in signals if s["fill_status"] == "FILL"]
    assert len(fills) >= 30
    days, closes = v2._C["bench_days"], [float(b["close"]) for b in v2._C["bench"]]
    for s in fills:
        assert s["day"] < s["fill_day"] <= s["exit_day"]
        i, j = days.index(s["fill_day"]), days.index(s["exit_day"])  # A 股标的的交易日都是沪深300 的交易日
        assert s["bench_hold_ret"] == pytest.approx(closes[j] / closes[i - 1] - 1.0, abs=1e-12)
    assert all("exit_day" not in s for s in signals if s["fill_status"] != "FILL")


def test_bench_hold_return_on_non_benchmark_days(cn_shard) -> None:
    """成交日或出场日落在基准没有K线的日子（用周日代表）：取前一个基准收盘，不报错。"""
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    days, closes = v2._C["bench_days"], [float(b["close"]) for b in v2._C["bench"]]
    i = next(k for k in range(500, len(days)) if days[k][:4] == "2023" and date.fromisoformat(days[k]).weekday() == 0)
    sunday = (date.fromisoformat(days[i]) - timedelta(days=1)).isoformat()
    assert sunday not in days
    # 周日"成交"：之前最近的基准收盘是 days[i-1]；周日"出场"：当天或之前最近的也是 days[i-1]
    assert ra.bench_hold_return(sunday, days[i + 3]) == pytest.approx(closes[i + 3] / closes[i - 1] - 1.0)
    assert ra.bench_hold_return(days[i - 3], sunday) == pytest.approx(closes[i - 1] / closes[i - 4] - 1.0)
```

同时在测试文件顶部的 `import sys` 之后加一行 `from datetime import date, timedelta`。

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q -k "exit_day or non_benchmark"`
Expected: FAIL，`AttributeError: module 'regime_attribution' has no attribute 'eval_shard_with_exit'`

- [ ] **Step 3: 实现**

追加到 `research/regime_attribution.py`：

```python
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
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q -k "exit_day or non_benchmark"`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add research/regime_attribution.py research/tests/test_regime_attribution.py
git commit -m "feat(research): 分状态归因——已成交信号补出场日与持有期基准收益（与原撮合逐笔核对）"
```

---

### Task 3: 分组、单格统计与线性分解

**Files:**
- Modify: `research/regime_attribution.py`
- Test: `research/tests/test_regime_attribution.py`

**Interfaces:**
- Consumes: `v2._fill_eps(signals) -> list[dict]`（已成交且按 21 个自然日去重的 episode）；`v2._layer_stats(eps) -> dict`；`ev._excess(eps, base_by_day) -> dict | None`；Task 1 的状态表与 `score_bucket`；Task 2 的 `bench_hold_ret`。
- Produces:
  - `cell_stats(eps: list[dict], base: dict[str, list[float]]) -> dict`：键 `n_episodes, n_signal_days, max_day_share_pct, net_positive_rate_pct, mean_net_return_pct, mean_date_cluster95_pct, baseline_mean_net_return_pct, excess_mean_pct, excess_mean_date_cluster95_pct, insufficient_sample`。
  - `tercile_cuts(values: list[float]) -> tuple[float, float]`。
  - `attribution_groups(eps, table, base) -> dict`：`{"E1_regime": {label: cell}, "E2_score": {...}, "X1_bench_sign": {"UP" | "DOWN_OR_FLAT": cell}, "X2_bench_tercile": {"LOW" | "MID" | "HIGH": cell}, "X2_cuts_pct": [t1, t2]}`。
  - `beta_regression(eps) -> dict`：键 `n, slope, slope_date_cluster95, intercept_pct, intercept_pct_date_cluster95, r2`。

四个分组变量：E1、E2 是信号日就知道的（事前）；X1、X2 用了持有期的大盘涨跌，是事后变量，只用于拆解，不能用于选股或择时。

- [ ] **Step 1: 写失败的测试**

```python
def test_cells_conserve_and_match_frozen_stats(cn_shard) -> None:
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    signals, base = cn_shard
    eps = v2._fill_eps(signals)
    table = ra.bench_state_table()
    groups = ra.attribution_groups(eps, table, base["transplant"])
    total = v2._layer_stats(eps)["n_episodes"]
    for name in ("E1_regime", "E2_score", "X1_bench_sign", "X2_bench_tercile"):
        assert sum(c["n_episodes"] for c in groups[name].values()) == total, name
        assert all(c["n_episodes"] > 0 for c in groups[name].values())  # 空格子不出现
    # 任取一格，用冻结函数在同一子集上直接重算，数字必须相同
    label, cell = next(iter(groups["E1_regime"].items()))
    sub = [r for r in eps if table[r["day"]]["regime"] == label]
    frozen = v2._layer_stats(sub)
    assert (cell["mean_net_return_pct"], cell["net_positive_rate_pct"]) == (
        frozen["mean_net_return_pct"], frozen["net_positive_rate_pct"])
    ex = ev._excess(sub, base["transplant"])
    assert cell["excess_mean_pct"] == (ex["mean_pct"] if ex else None)
    assert cell["insufficient_sample"] == (cell["n_episodes"] < 100 or cell["n_signal_days"] < 30)
    # 三分位：切点有序，三组的持有期基准收益不交叠
    t1, t2 = (x / 100.0 for x in groups["X2_cuts_pct"])
    assert t1 <= t2 and set(groups["X2_bench_tercile"]) <= {"LOW", "MID", "HIGH"}
    assert sum(1 for r in eps if r["bench_hold_ret"] < t1) == groups["X2_bench_tercile"].get("LOW", {"n_episodes": 0})["n_episodes"]
    up = sum(1 for r in eps if r["bench_hold_ret"] > 0)
    assert groups["X1_bench_sign"].get("UP", {"n_episodes": 0})["n_episodes"] == up


def test_regression_matches_numpy_polyfit(cn_shard) -> None:
    eps = v2._fill_eps(cn_shard[0])
    got = ra.beta_regression(eps)
    x = np.array([r["bench_hold_ret"] for r in eps])
    y = np.array([r["net"] for r in eps])
    slope, icpt = np.polyfit(x, y, 1)
    assert got["n"] == len(eps)
    assert got["slope"] == pytest.approx(slope, abs=1e-4)
    assert got["intercept_pct"] == pytest.approx(icpt * 100.0, abs=1e-3)
    assert got["r2"] == pytest.approx(np.corrcoef(x, y)[0, 1] ** 2, abs=1e-4)
    assert got["slope_date_cluster95"][0] < got["slope_date_cluster95"][1]
    assert got == ra.beta_regression(eps)  # 固定种子，可复现
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q -k "cells_conserve or regression"`
Expected: FAIL，`AttributeError: ... has no attribute 'attribution_groups'`

- [ ] **Step 3: 实现**

追加到 `research/regime_attribution.py`：

```python
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
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q -k "cells_conserve or regression"`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add research/regime_attribution.py research/tests/test_regime_attribution.py
git commit -m "feat(research): 分状态归因——四个事先写定的分组、单格统计与对大盘收益的线性分解"
```

---

### Task 4: 0A 主程序、复现核对与三市场运行

**Files:**
- Modify: `research/regime_attribution.py`
- Test: `research/tests/test_regime_attribution.py`

**Interfaces:**
- Consumes: Task 1~3 的全部函数；`scan.iter_rows(out_dir)`；`ev.baseline_plan(signals_by_day, pool_by_day)`；`ev._eval_init(h1_authorized, market, mode, plans)`；`ev.merge_baseline(parts)`；`v2._git_head()`。
- Produces:
  - `check_reproduction(market: str, eps: list[dict], transplant: dict, ref: dict) -> None`（不一致时抛 `RuntimeError`）。
  - `run_attribution(market: str, workers: int) -> dict`：键 `scan_dir, region, reproduction, overall, groups, beta_regression, bench_state_day_counts`。
  - 命令行：`python research/regime_attribution.py --part attribution|coverage|all --markets CN,HK,US --workers 8 --out <path>`（`coverage` 在 Task 5 接入）。

- [ ] **Step 1: 写失败的测试**

```python
def test_reproduction_check_rejects_mismatch(cn_shard) -> None:
    signals, base = cn_shard
    eps = v2._fill_eps(signals)
    good = {"summary": {
        "fill_layer": ra._jsonable(v2._layer_stats(eps)),
        "excess_vs_same_day_random": ra._jsonable(ev._excess(eps, base["transplant"])),
    }}
    ra.check_reproduction("CN", eps, base["transplant"], good)  # 一致时不报错
    bad = {"summary": {**good["summary"], "fill_layer": {**good["summary"]["fill_layer"], "n_episodes": len(eps) + 1}}}
    with pytest.raises(RuntimeError, match="未能复现"):
        ra.check_reproduction("CN", eps, base["transplant"], bad)
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q -k reproduction_check`
Expected: FAIL，`AttributeError: ... has no attribute '_jsonable'`

- [ ] **Step 3: 实现**

追加到 `research/regime_attribution.py`：

```python
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


def main() -> int:
    ap = argparse.ArgumentParser(description="分状态归因（0A）与分状态覆盖率（0B）：样本内描述，不构成验收")
    ap.add_argument("--part", choices=("attribution", "coverage", "all"), default="all")
    ap.add_argument("--markets", default="CN,HK,US")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q`
Expected: 全部通过。

- [ ] **Step 5: 先跑一个市场，确认能复现**

```bash
git add research && git commit -m "feat(research): 分状态归因——0A 主程序与开发区主表复现核对"
uv run --directory kline-structure-chart python ../research/regime_attribution.py --part attribution --markets CN --workers 8
```

（先提交再跑：结果里的 `code_commit` 不带 `-dirty`，台账才能按提交号复现。）

Expected: 打印 `[CN] 0A 复现一致 episodes=44987 mean=0.074% excess=-0.029% ...`。第二遍撮合不调用线上流水线，预计几分钟到几十分钟；超过 2 分钟的命令放后台跑。

若报"未能复现"：不要放宽比较。先用 `git diff 509e499 -- kline-structure-chart/scripts research/main_plan_eval.py research/run_dev_region_v2.py research/aggregate.py` 确认代码差异只有 `structure.py` 的除零修复，再核对 `artifacts/long_backtest/cache/` 是否在 2026-10-21 之后被刷新过（看文件修改时间）。两者都排除后把差异原样报告给用户，停在这里。

- [ ] **Step 6: 跑其余两个市场**

```bash
uv run --directory kline-structure-chart python ../research/regime_attribution.py --part attribution --markets HK,US --workers 8
```

Expected: `[HK] 0A 复现一致 episodes=33408 mean=-0.442% excess=-0.149%`，`[US] 0A 复现一致 episodes=59170 mean=0.002% excess=0.041%`；`artifacts/long_backtest/regime_attribution.json` 的 `markets` 下有三个市场的 `attribution`。

- [ ] **Step 7: 守恒核对**

```bash
python3 - <<'EOF'
import json
d = json.load(open("artifacts/long_backtest/regime_attribution.json"))
for m, v in d["markets"].items():
    a = v["attribution"]
    for g, cells in a["groups"].items():
        if g == "X2_cuts_pct":
            continue
        n = sum(c["n_episodes"] for c in cells.values())
        assert n == a["overall"]["n_episodes"], (m, g, n)
    print(m, "守恒", a["overall"]["n_episodes"], {g: len(c) for g, c in a["groups"].items() if g != "X2_cuts_pct"})
EOF
```

Expected: 三行"守恒"，没有断言失败。

---

### Task 5: 0B 正常波动范围的分状态覆盖率

**Files:**
- Modify: `research/regime_attribution.py`
- Test: `research/tests/test_regime_attribution.py`

**Interfaces:**
- Consumes: `v2.build_bundle(symbol, data, pit, cut, day) -> (bundle, request)`；`analyze_bundle(bundle, request, include_replay=False, include_forecast=True)["future_projection_15d"]`，其中 `conformal_50pct_band` 与 `conformal_80pct_band` 是长度 15 的 `[下沿, 上沿]` 列表；`aggregate.date_block_bootstrap(rows, value_fn)`；`v2._ci_pct`。
- Produces:
  - `coverage_sampled(sym: str, day: str) -> bool`。
  - `bands_at(sym: str, data: dict, t: int, day: str) -> (pit, cut, band50, band80)`。
  - `coverage_shard(path_str: str) -> list[dict]`：每行 `{"symbol", "day", "in50_5", "in80_5", "in50_10", "in80_10", "in50_15", "in80_15"}`，出错的股票-日为 `{"symbol", "day", "__error__": str}`。
  - `coverage_cells(rows, table) -> dict`：`{"ALL": {"ALL": cell}, "E1_regime": {...}, "E2_score": {...}}`，cell 键 `n_samples, n_days, insufficient_sample, cover{50|80}_step{5|10|15}_pct, cover{50|80}_step{5|10|15}_date_cluster95_pct`。
  - `run_coverage(market: str, workers: int) -> dict`：键 `n_sampled, n_errors, error_examples, cells`。

实际收盘价取信号日之后第 5 / 10 / 15 根K线的收盘价，与区间在同一份"截至信号日"的复权口径下比较（`make_pit_slice` 返回的切片把之后的K线按信号日的复权因子换算，只用于对答案）。

- [ ] **Step 1: 写失败的测试**

```python
def _first_symbol_with_days(n: int = 5) -> tuple[str, dict, list]:
    for sym in sorted(v2._C["periods"]):
        data = v2._load_symbol(sym)
        if data is not None:
            days = list(v2._eligible_days(sym, data))
            if len(days) >= 200 * n:
                return sym, data, days[::200][:n]
    raise AssertionError("没有找到历史足够长的标的")


def test_coverage_bands_ignore_future_bars() -> None:
    """把信号日之后的K线价格乘以 3：区间必须完全不变（只有用来对答案的实际收盘价会变）。"""
    scan.init("US")
    sym, data, days = _first_symbol_with_days()
    for day, t, _ in days:
        pit, cut, b50, b80 = ra.bands_at(sym, data, t, day)
        assert pit[cut]["day"] == day and len(b50) == len(b80) == v2.HORIZON
        bad = {**data, "bars": data["bars"][: t + 1] + [
            {**b, **{k: float(b[k]) * 3.0 for k in ("open", "high", "low", "close")}} for b in data["bars"][t + 1:]]}
        pit2, cut2, c50, c80 = ra.bands_at(sym, bad, t, day)
        assert (b50, b80) == (c50, c80), (sym, day)
        assert float(pit2[cut2 + 5]["close"]) == pytest.approx(float(pit[cut + 5]["close"]) * 3.0)


def test_coverage_sampling_and_record() -> None:
    scan.init("CN")
    path = sorted((ROOT / ra.reference("CN")["scan_dir"]).glob("shard_*.jsonl.gz"))[0]
    rows = list(ra.read_shard(path))
    picked = [r for r in rows if ra.coverage_sampled(r["sym"], r["day"])]
    assert 0.01 < len(picked) / len(rows) < 0.03  # 约 2%
    assert [ra.coverage_sampled(r["sym"], r["day"]) for r in rows[:50]] == [ra.coverage_sampled(r["sym"], r["day"]) for r in rows[:50]]
    recs = ra.coverage_shard(str(path))
    assert len(recs) == len(picked)
    ok = [r for r in recs if "__error__" not in r]
    assert len(ok) >= 0.95 * len(recs)
    for r in ok[:20]:
        # 80% 区间包含 50% 区间：落在 50% 区间内的一定也落在 80% 区间内
        assert all((not r[f"in50_{s}"]) or r[f"in80_{s}"] for s in (5, 10, 15))
    cells = ra.coverage_cells(ok, ra.bench_state_table())
    assert cells["ALL"]["ALL"]["n_samples"] == len(ok)
    assert sum(c["n_samples"] for c in cells["E1_regime"].values()) == len(ok)
    assert sum(c["n_samples"] for c in cells["E2_score"].values()) == len(ok)
    c = cells["ALL"]["ALL"]
    assert c["cover50_step5_pct"] <= c["cover80_step5_pct"]
    assert c["insufficient_sample"] == (c["n_samples"] < 300 or c["n_days"] < 30)
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q -k coverage`
Expected: FAIL，`AttributeError: ... has no attribute 'bands_at'`

- [ ] **Step 3: 实现**

在 `research/regime_attribution.py` 的 `def main()` 之前插入：

```python
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
```

`main()` 里，在 `args.out.write_text(...)`（循环内那一行）之前加：

```python
        if args.part in ("coverage", "all"):
            entry["coverage"] = run_coverage(market, args.workers)
            c = entry["coverage"]["cells"]["ALL"]["ALL"]
            print(f"[{market}] 0B sampled={entry['coverage']['n_sampled']} errors={entry['coverage']['n_errors']} "
                  f"cover50@15={c['cover50_step15_pct']}% cover80@15={c['cover80_step15_pct']}%", flush=True)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q`
Expected: 全部通过。

- [ ] **Step 5: 提交，然后在后台跑三市场覆盖率**

```bash
git add research && git commit -m "feat(research): 分状态归因——0B 正常波动范围的分状态覆盖率抽样"
uv run --directory kline-structure-chart python ../research/regime_attribution.py --part coverage --markets CN,HK,US --workers 8 2>&1 | tee artifacts/long_backtest/regime_attribution_coverage.log
```

约 12 万次线上流水线调用（含正常波动范围），8 个进程预计 1.5~3 小时：放后台跑，不要轮询。

Expected: 三行 `[市场] 0B sampled=… errors=…`；`sampled` 合计在 11~13 万之间；`errors` 占比低于 0.1%（若更高，把 `error_examples` 原样报告给用户，不要改动引擎去消除）。`regime_attribution.json` 里三个市场都有 `attribution` 与 `coverage`。

---

### Task 6: 结果摘要写入规格，登记台账

**Files:**
- Modify: `research/regime_attribution.py`（新增 `render_markdown` 与 `--print-md`）
- Test: `research/tests/test_regime_attribution.py`
- Modify: `docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md`（追加"阶段 0 结果"）

**Interfaces:**
- Consumes: `artifacts/long_backtest/regime_attribution.json`（Task 4、5 的产出）。
- Produces: `render_markdown(result: dict) -> str`；命令行 `--print-md`（只读已有结果文件并打印，不计算、不写台账）。

- [ ] **Step 1: 写失败的测试**

```python
def test_render_markdown_uses_only_file_numbers(cn_shard) -> None:
    scan.init("CN")  # 其他测试可能把全局状态切到了别的市场
    signals, base = cn_shard
    eps = v2._fill_eps(signals)
    table = ra.bench_state_table()
    result = {"generated_at_utc": "T", "code_commit": "abc", "markets": {"CN": {"attribution": {
        "overall": ra.cell_stats(eps, base["transplant"]),
        "groups": ra.attribution_groups(eps, table, base["transplant"]),
        "beta_regression": ra.beta_regression(eps),
    }}}}
    md = ra.render_markdown(result)
    o = result["markets"]["CN"]["attribution"]["overall"]
    assert "#### CN" in md and f"| 全部 | {o['n_episodes']} |" in md
    assert "样本内" in md and "事后变量" in md
    small = [c for g, cells in result["markets"]["CN"]["attribution"]["groups"].items() if g != "X2_cuts_pct"
             for c in cells.values() if c["insufficient_sample"]]
    assert ("样本不足" in md) == bool(small)
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q -k render_markdown`
Expected: FAIL，`AttributeError: ... has no attribute 'render_markdown'`

- [ ] **Step 3: 实现**

在 `def main()` 之前插入：

```python
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
```

`main()` 的参数解析处加一行 `ap.add_argument("--print-md", action="store_true", help="只读已有结果文件并打印摘要，不计算")`，并在 `args = ap.parse_args()` 之后加：

```python
    if args.print_md:
        print(render_markdown(json.loads(args.out.read_text(encoding="utf-8"))))
        return 0
```

- [ ] **Step 4: 运行全部研究测试**

```bash
uv run --directory kline-structure-chart pytest ../research/tests/test_regime_attribution.py -q
uv run --directory kline-structure-chart pytest ../research/tests -q
```

Expected: 第一条全部通过；第二条的通过数等于开工前的基线加上本文件新增的用例数（确认没有碰坏已有研究测试）。

- [ ] **Step 5: 提交研究分支，核对台账**

```bash
git add research && git commit -m "feat(research): 分状态归因——结果摘要生成（数字全部取自结果文件）"
tail -n 3 artifacts/long_backtest/trial_ledger.jsonl | python3 -c "import sys, json; [print(json.loads(l)['hypothesis'], json.loads(l).get('part'), json.loads(l)['code_commit'], json.loads(l).get('n_cells_reported')) for l in sys.stdin]"
```

Expected: 出现 `REGIME_ATTRIBUTION` 的记录（0A 两行、0B 一行），`code_commit` 都不带 `-dirty`，0A 完成后 `n_cells_reported` 为 45 左右（某个状态在某市场一天都没出现时会更少）。

- [ ] **Step 6: 把摘要写进规格**

```bash
uv run --directory kline-structure-chart python ../research/regime_attribution.py --print-md > artifacts/long_backtest/regime_attribution_summary.md
git checkout docs/regime-attribution-market-layer-spec   # 规格所在分支；若规格已随阶段 1 合入 main，则从 main 新开一条 docs 分支
```

把 `artifacts/long_backtest/regime_attribution_summary.md` 的内容追加到规格文档末尾，再在表格之后手写一小段"读表说明"，只允许写下面四类话，每句都要能在表里找到对应的数字：

1. 回归的 R² 与斜率说明净收益在多大程度上跟随持有期大盘（例如"CN 的 R² 为 x"）。
2. 事后分组里，大盘下跌的那一格平均净收益与超额各是多少。
3. 事前分组（E1、E2）里有没有哪一格的超额区间不含 0；有的话写明"45 个格子在 5% 水平下预期约 2 个偶然显著，这只是新假设"。
4. 各状态下 50% / 80% 区间的覆盖率离名义值多远，哪些格子样本不足。

不写"某状态下策略有效"这类结论，不据此提出改规则。

```bash
git add docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md
git commit -m "docs(spec): 追加阶段 0 结果（开发区样本内：分状态归因与波动范围覆盖率）"
git checkout research/regime-attribution
```

Expected: 规格末尾有"阶段 0 结果"一节；随手抽 5 个数字与 `regime_attribution.json` 对照一致。推送任何分支之前先向用户确认。
