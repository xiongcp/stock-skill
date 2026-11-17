# 大盘结构与周期层（阶段 1）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对基准指数计算结构层、周线月线阶段与个股—大盘对齐关系，作为描述性实验字段写入 `result.json`、`report.md` 与 HTML 看板，不改变任何决策。

**Architecture:** 新模块 `scripts/market_structure.py` 只做纯计算，结果放在内部报告的新键 `market_layer` 下，不并入传给评分与风控的 `benchmark_info`，从结构上保证决策读不到它。`contract_v6.py` 把它投影到 `market_context.structure / cycle / alignment` 与 `quotable_prices.benchmark`；`render_report_md.py`、`render_html.py`、`lint_response.py` 只读 `result.json`。

**Tech Stack:** Python ≥ 3.12（运行期只依赖 Pillow），pytest + pytest-xdist，uv；测试里可用 numpy（dev 依赖）做独立核算。

**Spec:** `docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md` 第 3 节

## Global Constraints

- 本阶段不改变任何决策：规则分、评级、动作、否决条件与计划价位不得读取新字段。
- 测试一律断网，只用 `tests/fixtures/recorded/` 的真实录制行情；严禁生成或使用合成行情。
- 只用已收盘K线；拐点只用 `confirmed = true` 的；取不到的值写 `null` 并给 `reason`，不估算、不补数。
- 不输出概率、胜率、频率；新键名不得包含 `probability`、`frequency`、`freq`、`high_conviction`、`judgment`、`brier`、`analogue`。
- 新字段在 schema 中一律 `x-stability: experimental`；6.x 内只新增字段；版本号保持 `6.0.0`。
- `result.json` 落盘（`indent=2`）不超过 80 KB，预算不放宽。
- 新字段不得含 `NaN` / `Infinity`；页面字串一律用 `textContent`。
- 常量在规格里写定，实现时不调参：30 周均线、4 周斜率、60 周区间、10 月均线、最少 35 周与 11 个月、前后 5 根窗口、120 与 60 个共同交易日、相对强弱 ±2%、拐点配对 10 根。
- PNG 不改；历史回放快照不含大盘层。
- 所有命令在仓库根目录执行，经 `uv run --directory kline-structure-chart` 进入技能目录。
- 推送分支或开 PR 之前先向用户确认。

## Review Focus

1. **基准比标的历史短或有缺口**（新上市基准、港股半日市被剔除的占位K线）：共同交易日少于 60 个时，`beta_120` / `corr_120` 为 `null`，其余对齐字段按可得数据计算，不得抛异常。→ Task 2 的 `test_alignment_short_overlap_gives_nulls`。
2. **标的本身就是市场基准**（`SPY.US`、`000300.SH`）：`structure`、`alignment` 为 `null`，`cycle.basis = "SELF"`，周期价位进入 `quotable_prices.prices`。→ Task 3 的 `test_market_layer_self_benchmark`、Task 5 的 `test_quotable_self_basis`。
3. **缺少基准的本地文件输入**：三个字段都为 `null`，schema 校验通过，报告与看板不报错。→ Task 5 的 `test_market_context_nulls_without_benchmark`、Task 6 的报告用例。
4. **基准刚确认拐点、右侧窗口不足 5 根**：`window_complete = false`，报告与看板必须标"未稳定"。→ Task 2 的 `test_alignment_window_incomplete`、Task 6 的断言。
5. **回复里引用指数点位**：提到大盘的句子放行；只谈个股的句子里出现同一数字仍报错。→ Task 5 的 `test_lint_benchmark_price_needs_index_context`。

---

## File Structure

| 文件 | 动作 | 职责 |
| --- | --- | --- |
| `kline-structure-chart/scripts/market_structure.py` | 新建 | 月线聚合、周期阶段、基准结构摘要、对齐关系、`compute_market_layer` |
| `kline-structure-chart/scripts/analysis.py` | 修改 | `analyze_bundle` 新增 `include_market_layer`，写入 `report["market_layer"]` |
| `kline-structure-chart/scripts/contracts.py` | 修改 | `AnalysisReport` 增加可选键 `market_layer` |
| `kline-structure-chart/scripts/evaluation.py` | 修改 | 回测传 `include_market_layer=False`；新增大盘层因果审计 |
| `kline-structure-chart/scripts/analyze_and_forecast.py` | 修改 | 审计调用传入 `bundle` |
| `kline-structure-chart/scripts/contract_v6.py` | 修改 | 投影 `market_context.structure/cycle/alignment` 与 `quotable_prices.benchmark` |
| `kline-structure-chart/assets/analysis.schema.json` | 修改 | 新字段定义与 `x-stability` |
| `kline-structure-chart/scripts/lint_response.py` | 修改 | 基准价位在大盘语境下放行 |
| `kline-structure-chart/scripts/render_report_md.py` | 修改 | 第 2 节增加大盘结构、周期阶段、个股与大盘三段 |
| `kline-structure-chart/scripts/render_html.py` | 修改 | 第 1 栏增加三行（回放时不显示） |
| `kline-structure-chart/tests/test_market_structure.py` | 新建 | 本计划的全部新测试 |
| `kline-structure-chart/tests/fixtures/contract/result_v6_keypaths_*.json` | 重新生成 | 五个配置的键路径快照 |
| `SKILL.md`、`references/{math-patterns,output-fields,interpretation-guide}.md`、`AGENTS.md`、`CHANGELOG.md`、`README.md`、`README.en.md`、`examples/` | 修改 | 文档与示例同步 |

开工前：

```bash
git checkout docs/regime-attribution-market-layer-spec
git checkout -b feat/market-structure-cycle-layer
uv sync --python 3.12 --directory kline-structure-chart
uv run --directory kline-structure-chart pytest -n auto -m "not slow" -q
```

预期：全部通过（记录通过数，作为基线）。

---

### Task 1: 周期阶段

**Files:**
- Create: `kline-structure-chart/scripts/market_structure.py`
- Test: `kline-structure-chart/tests/test_market_structure.py`

**Interfaces:**
- Consumes: `strategies.aggregate_bars_to_weekly(daily_bars) -> list[dict]`（末周 `complete` 可能为 `False`）；`structure.HISTORY_BARS = 420`、`structure.TREND_FLAT_PCT = 0.5`（单位：百分比）。
- Produces:
  - `aggregate_bars_to_monthly(daily_bars: list[dict]) -> list[dict]`：每项 `{"day", "open", "high", "low", "close"}`，只含已结束的自然月。
  - `classify_cycle_phase(daily_bars: list[dict], decimals: int = 2) -> dict`：键 `phase, phase_cn, phase_weeks, phase_weeks_capped, weekly_close, ma30w_price, slope_4w_pct, pos_60w_pct, range_week_count, high_60w_price, low_60w_price, monthly_vs_ma10, ma10m_price, reason`。不含 `basis`（由 Task 3 填）。

- [ ] **Step 1: 写失败的测试**

`tests/test_market_structure.py`：

```python
"""大盘结构与周期层：周期阶段、对齐关系、编排、契约与呈现（只用录制的真实行情）。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from conftest import load_recorded  # noqa: E402

CSI300 = "sina_daily/sh000300.json"
MOUTAI = "sina_daily/sh600519.json"


def _completed_weeks(daily: list[dict]) -> list[dict]:
    from strategies import aggregate_bars_to_weekly

    return [w for w in aggregate_bars_to_weekly(daily[-420:]) if w["complete"]][1:]


def _phase_of_weeks(weeks: list[dict]) -> dict | None:
    """不调用被测模块，用 numpy 按规格第 3.3 节对一串已完成周线重算阶段。"""
    if len(weeks) < 35:
        return None
    c = np.array([w["close"] for w in weeks], dtype=float)
    ma_now, ma_prev = c[-30:].mean(), c[-34:-4].mean()
    slope = ma_now / ma_prev - 1.0
    win = weeks[-60:]
    hi, lo = max(w["high"] for w in win), min(w["low"] for w in win)
    pos = (c[-1] - lo) / (hi - lo)
    if slope > 0.005 and c[-1] > ma_now:
        phase = "ADVANCE"
    elif slope < -0.005 and c[-1] < ma_now:
        phase = "DECLINE"
    else:
        phase = "TOPPING" if pos >= 0.5 else "BASING"
    return {"phase": phase, "slope": slope, "pos": pos, "ma": ma_now, "n": len(win)}


def _independent_phase(daily: list[dict]) -> dict | None:
    return _phase_of_weeks(_completed_weeks(daily))


def test_cycle_phase_matches_independent_computation() -> None:
    from market_structure import classify_cycle_phase

    bars = load_recorded(CSI300)
    seen: set[str] = set()
    for cut in range(430, len(bars) + 1, 20):
        got = classify_cycle_phase(bars[:cut])
        exp = _independent_phase(bars[:cut])
        assert exp is not None
        assert got["phase"] == exp["phase"], bars[cut - 1]["day"]
        assert got["slope_4w_pct"] == pytest.approx(exp["slope"] * 100.0, abs=0.0051)
        assert got["pos_60w_pct"] == pytest.approx(exp["pos"] * 100.0, abs=0.051)
        assert got["ma30w_price"] == pytest.approx(exp["ma"], abs=0.006)
        assert got["range_week_count"] == exp["n"]
        assert got["phase_weeks"] >= 1 and got["reason"] is None
        seen.add(got["phase"])
    # 录制区间内没有出现的阶段在这里如实列出，不为凑覆盖而构造行情
    print("phases covered:", sorted(seen))
    assert len(seen) >= 2


def test_cycle_phase_weeks_counts_consecutive_weeks() -> None:
    from market_structure import classify_cycle_phase

    bars = load_recorded(CSI300)
    got = classify_cycle_phase(bars)
    weeks = _completed_weeks(bars)
    # 从最新一周往回数：阶段相同的连续周数；数到不足 35 周仍相同，则 capped
    run, k = 0, len(weeks)
    while k >= 35 and _phase_of_weeks(weeks[:k])["phase"] == got["phase"]:
        run, k = run + 1, k - 1
    assert got["phase_weeks"] == run
    assert got["phase_weeks_capped"] == (k < 35)


def test_cycle_phase_insufficient_history_is_null_with_reason() -> None:
    from market_structure import classify_cycle_phase

    bars = load_recorded(CSI300)
    got = classify_cycle_phase(bars[:150])  # 约 30 周，少于 35
    assert got["phase"] is None and got["ma30w_price"] is None and got["phase_weeks"] is None
    assert "35" in got["reason"]


def test_monthly_aggregation_only_finished_months() -> None:
    from market_structure import aggregate_bars_to_monthly

    bars = load_recorded(CSI300)[-300:]
    months = aggregate_bars_to_monthly(bars)
    keys = [m["day"][:7] for m in months]
    assert keys == sorted(set(keys))
    assert bars[-1]["day"][:7] not in keys  # 最后一个自然月无法确认已结束，不计入
    assert bars[0]["day"][:7] not in keys   # 第一个月可能不完整，丢弃
    last_key = keys[-1]
    in_month = [b for b in bars if b["day"][:7] == last_key]
    assert months[-1]["close"] == float(in_month[-1]["close"])
    assert months[-1]["high"] == max(float(b["high"]) for b in in_month)
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q`
Expected: FAIL，`ModuleNotFoundError: No module named 'market_structure'`

- [ ] **Step 3: 实现**

`scripts/market_structure.py`：

```python
"""大盘结构与周期层：基准的结构摘要、周线月线阶段、个股与大盘的对齐关系。

全部为描述性纯计算，只用已收盘K线与已确认拐点。结果写入内部报告的 market_layer，
不参与规则分、评级、否决与计划价位（规格 2026-11-15 第 3 节）。公式见 references/math-patterns.md。
"""
from __future__ import annotations

from typing import Any

from strategies import aggregate_bars_to_weekly
from structure import HISTORY_BARS, TREND_FLAT_PCT

MA_WEEKS = 30
SLOPE_WEEKS = 4
RANGE_WEEKS = 60
MIN_WEEKS = 35
MA_MONTHS = 10
MIN_MONTHS = 11
PHASE_CN = {"ADVANCE": "上升阶段", "DECLINE": "下降阶段", "TOPPING": "高位震荡", "BASING": "低位震荡"}


def aggregate_bars_to_monthly(daily_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """已收盘日线聚合成月线。丢弃第一个月（可能不完整）和最后一个月（无法确认已结束）。"""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for b in daily_bars:
        buckets.setdefault(str(b["day"])[:7], []).append(b)
    keys = sorted(buckets)[1:-1]
    return [
        {
            "day": buckets[k][-1]["day"],
            "open": float(buckets[k][0]["open"]),
            "high": max(float(x["high"]) for x in buckets[k]),
            "low": min(float(x["low"]) for x in buckets[k]),
            "close": float(buckets[k][-1]["close"]),
        }
        for k in keys
    ]


def _phase_at(weeks: list[dict[str, Any]], k: int) -> dict[str, Any] | None:
    """只用 weeks[: k + 1] 判定阶段（无状态）；样本不足返回 None。"""
    if k + 1 < MIN_WEEKS:
        return None
    closes = [float(w["close"]) for w in weeks[: k + 1]]
    ma_now = sum(closes[-MA_WEEKS:]) / MA_WEEKS
    ma_prev = sum(closes[-MA_WEEKS - SLOPE_WEEKS : -SLOPE_WEEKS]) / MA_WEEKS
    slope = ma_now / ma_prev - 1.0
    win = weeks[max(0, k + 1 - RANGE_WEEKS) : k + 1]
    hi = max(float(w["high"]) for w in win)
    lo = min(float(w["low"]) for w in win)
    pos = (closes[-1] - lo) / (hi - lo) if hi > lo else 0.5
    flat = TREND_FLAT_PCT / 100.0
    if slope > flat and closes[-1] > ma_now:
        phase = "ADVANCE"
    elif slope < -flat and closes[-1] < ma_now:
        phase = "DECLINE"
    else:
        phase = "TOPPING" if pos >= 0.5 else "BASING"
    return {"phase": phase, "close": closes[-1], "ma": ma_now, "slope": slope, "pos": pos, "hi": hi, "lo": lo, "n": len(win)}


def classify_cycle_phase(daily_bars: list[dict[str, Any]], decimals: int = 2) -> dict[str, Any]:
    """周线阶段（四类）、已持续周数与月线相对 10 月均线的位置。输入为已收盘日线，只取最近 HISTORY_BARS 根。"""
    hist = daily_bars[-HISTORY_BARS:]
    out: dict[str, Any] = {
        "phase": None, "phase_cn": None, "phase_weeks": None, "phase_weeks_capped": None,
        "weekly_close": None, "ma30w_price": None, "slope_4w_pct": None, "pos_60w_pct": None,
        "range_week_count": None, "high_60w_price": None, "low_60w_price": None,
        "monthly_vs_ma10": None, "ma10m_price": None, "reason": None,
    }
    weeks = [w for w in aggregate_bars_to_weekly(hist) if w.get("complete", True)][1:]
    last = len(weeks) - 1
    cur = _phase_at(weeks, last)
    if cur is None:
        out["reason"] = f"已完成周线 {len(weeks)} 个，少于 {MIN_WEEKS} 个，无法判定周线阶段"
    else:
        run, capped, j = 1, False, last - 1
        while True:
            prev = _phase_at(weeks, j)
            if prev is None:
                capped = True
                break
            if prev["phase"] != cur["phase"]:
                break
            run += 1
            j -= 1
        out.update(
            phase=cur["phase"], phase_cn=PHASE_CN[cur["phase"]], phase_weeks=run, phase_weeks_capped=capped,
            weekly_close=round(cur["close"], decimals), ma30w_price=round(cur["ma"], decimals),
            slope_4w_pct=round(cur["slope"] * 100.0, 2), pos_60w_pct=round(cur["pos"] * 100.0, 1),
            range_week_count=cur["n"], high_60w_price=round(cur["hi"], decimals), low_60w_price=round(cur["lo"], decimals),
        )
    months = aggregate_bars_to_monthly(hist)
    if len(months) >= MIN_MONTHS:
        closes = [m["close"] for m in months]
        ma10 = sum(closes[-MA_MONTHS:]) / MA_MONTHS
        out["monthly_vs_ma10"] = "ABOVE" if closes[-1] > ma10 else "BELOW"
        out["ma10m_price"] = round(ma10, decimals)
    return out
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -s`
Expected: 4 passed；输出里有一行 `phases covered: [...]`，把它记进提交说明。

- [ ] **Step 5: 提交**

```bash
git add kline-structure-chart/scripts/market_structure.py kline-structure-chart/tests/test_market_structure.py
git commit -m "feat(market-layer): 周线阶段四分类与月线位置（无状态、只用已完成周）"
```

---

### Task 2: 个股与大盘的对齐关系

**Files:**
- Modify: `kline-structure-chart/scripts/market_structure.py`
- Test: `kline-structure-chart/tests/test_market_structure.py`

**Interfaces:**
- Consumes: `structure.compute_structure_layer(bars, intraday, show, ...) -> dict`，其中 `layer["pivots"]` 每项含 `day, price, kind("H"/"L"), confirmed`；`structure.MIN_RAIL_SPAN = 10`；Task 1 的 `classify_cycle_phase`。
- Produces: `compute_alignment(target_layer: dict, bench_layer: dict, target_bars: list[dict], bench_bars: list[dict], target_decimals: int = 2, bench_decimals: int = 2) -> dict`，键：
  `common_day_count, beta_120, corr_120, rs_trend_20, rs_trend_60, rs_at_60d_high, rs_at_60d_low, low_lead_lag_bars, high_lead_lag_bars, low_compare, high_compare, target_phase, benchmark_phase`。
  `low_compare` / `high_compare` 为 `None` 或 `{"benchmark_days": [d1, d2], "benchmark_extended": bool, "target_change_pct": float, "label": str, "label_cn": str, "window_complete": bool}`。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_market_structure.py`：

```python
def _layers(cut: int | None = None):
    from structure import STRUCT_DAYS, compute_structure_layer

    t = load_recorded(MOUTAI)
    b = load_recorded(CSI300)
    if cut is not None:
        t = t[:cut]
    b = [x for x in b if x["day"] <= t[-1]["day"]]
    return t, b, compute_structure_layer(t, None, STRUCT_DAYS), compute_structure_layer(b, None, STRUCT_DAYS)


def test_alignment_beta_corr_match_numpy() -> None:
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    got = compute_alignment(tl, bl, t, b)
    bmap = {x["day"]: float(x["close"]) for x in b}
    pairs = [(float(x["close"]), bmap[x["day"]]) for x in t if x["day"] in bmap][-120:]
    rt = np.diff(np.log([p[0] for p in pairs]))
    rb = np.diff(np.log([p[1] for p in pairs]))
    assert got["common_day_count"] == 120
    assert got["beta_120"] == pytest.approx(np.cov(rt, rb, ddof=0)[0, 1] / rb.var(), abs=5e-4)
    assert got["corr_120"] == pytest.approx(np.corrcoef(rt, rb)[0, 1], abs=5e-4)
    rs = np.array([p[0] / p[1] for p in pairs])
    exp20 = "UP" if rs[-1] / rs[-21] >= 1.02 else ("DOWN" if rs[-1] / rs[-21] <= 0.98 else "FLAT")
    assert got["rs_trend_20"] == exp20
    assert got["rs_at_60d_high"] == bool(rs[-1] >= rs[-60:].max())
    assert got["target_phase"] in ("ADVANCE", "DECLINE", "TOPPING", "BASING")
    assert got["benchmark_phase"] in ("ADVANCE", "DECLINE", "TOPPING", "BASING")


def _expected_low_compare(t: list[dict], b: list[dict], bl: dict) -> dict | None:
    days = [x["day"] for x in t if x["day"] in {y["day"] for y in b}]
    pos = {d: i for i, d in enumerate(days)}
    tlow = {x["day"]: float(x["low"]) for x in t}
    lows = [p for p in bl["pivots"] if p["confirmed"] and p["kind"] == "L" and p["day"] in pos]
    if len(lows) < 2:
        return None
    l1, l2 = lows[-2], lows[-1]

    def wmin(day: str) -> float:
        i = pos[day]
        return min(tlow[d] for d in days[max(0, i - 5) : i + 6])

    m1, m2 = wmin(l1["day"]), wmin(l2["day"])
    ext = float(l2["price"]) < float(l1["price"])
    if ext:
        label = "HIGHER_LOW_WHILE_INDEX_LOWER_LOW" if m2 > m1 else "LOWER_LOW_WITH_INDEX"
    else:
        label = "LOWER_LOW_WHILE_INDEX_HIGHER_LOW" if m2 < m1 else "HIGHER_LOW_WITH_INDEX"
    return {"label": label, "days": [l1["day"], l2["day"]], "complete": pos[l2["day"]] + 5 <= len(days) - 1,
            "chg": round((m2 / m1 - 1.0) * 100.0, 2)}


def test_alignment_low_compare_matches_independent_on_many_cuts() -> None:
    from market_structure import compute_alignment

    n = len(load_recorded(MOUTAI))
    checked = incomplete = 0
    for cut in range(n - 400, n + 1, 10):
        t, b, tl, bl = _layers(cut)
        got = compute_alignment(tl, bl, t, b)["low_compare"]
        exp = _expected_low_compare(t, b, bl)
        if exp is None:
            assert got is None
            continue
        checked += 1
        assert got["label"] == exp["label"] and got["benchmark_days"] == exp["days"]
        assert got["window_complete"] == exp["complete"]
        assert got["target_change_pct"] == exp["chg"]
        incomplete += 0 if exp["complete"] else 1
    assert checked >= 20
    print("low_compare cuts:", checked, "window_incomplete:", incomplete)


def test_alignment_window_incomplete() -> None:
    """标的最近几天没有K线（例如停牌）：基准最近一个低点之后的共同交易日不足 5 个，标签必须标为未稳定。"""
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    lows = [p for p in bl["pivots"] if p["confirmed"] and p["kind"] == "L"]
    assert len(lows) >= 2
    cut_day = next(x["day"] for i, x in enumerate(b) if i >= 2 and b[i - 2]["day"] == lows[-1]["day"])  # 低点后第 2 个交易日
    got = compute_alignment(tl, bl, [x for x in t if x["day"] <= cut_day], b)["low_compare"]
    assert got["benchmark_days"][1] == lows[-1]["day"] and got["window_complete"] is False
    full = compute_alignment(tl, bl, t, b)["low_compare"]
    assert full["window_complete"] is (sum(1 for x in t if x["day"] > lows[-1]["day"]) >= 5)


def test_alignment_lead_lag_sign_and_window() -> None:
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    got = compute_alignment(tl, bl, t, b)
    for key in ("low_lead_lag_bars", "high_lead_lag_bars"):
        assert got[key] is None or (isinstance(got[key], int) and abs(got[key]) <= 10)


def test_alignment_short_overlap_gives_nulls() -> None:
    from market_structure import compute_alignment

    t, b, tl, bl = _layers()
    got = compute_alignment(tl, bl, t, b[-40:])  # 基准只有 40 根：共同交易日不足 60
    assert got["common_day_count"] == 40
    assert got["beta_120"] is None and got["corr_120"] is None
    assert got["rs_trend_60"] is None and got["rs_at_60d_high"] is None
    assert got["rs_trend_20"] in ("UP", "DOWN", "FLAT")
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -k alignment`
Expected: FAIL，`ImportError: cannot import name 'compute_alignment'`

- [ ] **Step 3: 实现**

在 `market_structure.py` 顶部导入处把 `from structure import HISTORY_BARS, TREND_FLAT_PCT` 改为 `from structure import HISTORY_BARS, MIN_RAIL_SPAN, TREND_FLAT_PCT`，并加 `import math`；常量区追加：

```python
ALIGN_DAYS = 120
ALIGN_MIN_DAYS = 60
RS_BAND = 0.02
ALIGN_WINDOW_BARS = 5
_COMPARE_CN = {
    "HIGHER_LOW_WHILE_INDEX_LOWER_LOW": "大盘新低、个股未新低",
    "LOWER_LOW_WITH_INDEX": "与大盘同步新低",
    "LOWER_LOW_WHILE_INDEX_HIGHER_LOW": "大盘未新低、个股新低",
    "HIGHER_LOW_WITH_INDEX": "与大盘同步抬高低点",
    "LOWER_HIGH_WHILE_INDEX_HIGHER_HIGH": "大盘新高、个股未新高",
    "HIGHER_HIGH_WITH_INDEX": "与大盘同步新高",
    "HIGHER_HIGH_WHILE_INDEX_LOWER_HIGH": "大盘未新高、个股新高",
    "LOWER_HIGH_WITH_INDEX": "与大盘同步压低高点",
}
```

文件末尾追加：

```python
def _trend(rs: list[float], n: int) -> str | None:
    if len(rs) <= n:
        return None
    ratio = rs[-1] / rs[-1 - n]
    return "UP" if ratio >= 1.0 + RS_BAND else ("DOWN" if ratio <= 1.0 - RS_BAND else "FLAT")


def _confirmed(layer: dict[str, Any], kind: str, pos: dict[str, int]) -> list[dict[str, Any]]:
    return [p for p in layer["pivots"] if p.get("confirmed") and p["kind"] == kind and p["day"] in pos]


def _lead_lag(t_layer: dict[str, Any], b_layer: dict[str, Any], kind: str, pos: dict[str, int]) -> int | None:
    b_piv = _confirmed(b_layer, kind, pos)
    if not b_piv:
        return None
    b_pos = pos[b_piv[-1]["day"]]
    near = [pos[p["day"]] - b_pos for p in _confirmed(t_layer, kind, pos) if abs(pos[p["day"]] - b_pos) <= MIN_RAIL_SPAN]
    return min(near, key=lambda d: (abs(d), d)) if near else None


def _extreme_compare(
    b_layer: dict[str, Any], kind: str, days: list[str], pos: dict[str, int], t_ext: dict[str, float]
) -> dict[str, Any] | None:
    """基准最近两个已确认低点（或高点）处，标的在前后 ALIGN_WINDOW_BARS 根窗口内的极值对照。"""
    piv = _confirmed(b_layer, kind, pos)
    if len(piv) < 2:
        return None
    p1, p2 = piv[-2], piv[-1]
    low = kind == "L"
    pick = min if low else max

    def window_ext(day: str) -> float:
        i = pos[day]
        return pick(t_ext[d] for d in days[max(0, i - ALIGN_WINDOW_BARS) : i + ALIGN_WINDOW_BARS + 1])

    m1, m2 = window_ext(p1["day"]), window_ext(p2["day"])
    extended = float(p2["price"]) < float(p1["price"]) if low else float(p2["price"]) > float(p1["price"])
    if low:
        label = ("HIGHER_LOW_WHILE_INDEX_LOWER_LOW" if m2 > m1 else "LOWER_LOW_WITH_INDEX") if extended else (
            "LOWER_LOW_WHILE_INDEX_HIGHER_LOW" if m2 < m1 else "HIGHER_LOW_WITH_INDEX")
    else:
        label = ("LOWER_HIGH_WHILE_INDEX_HIGHER_HIGH" if m2 < m1 else "HIGHER_HIGH_WITH_INDEX") if extended else (
            "HIGHER_HIGH_WHILE_INDEX_LOWER_HIGH" if m2 > m1 else "LOWER_HIGH_WITH_INDEX")
    return {
        "benchmark_days": [p1["day"], p2["day"]],
        "benchmark_extended": extended,
        "target_change_pct": round((m2 / m1 - 1.0) * 100.0, 2),
        "label": label,
        "label_cn": _COMPARE_CN[label],
        "window_complete": pos[p2["day"]] + ALIGN_WINDOW_BARS <= len(days) - 1,
    }


def compute_alignment(
    target_layer: dict[str, Any],
    bench_layer: dict[str, Any],
    target_bars: list[dict[str, Any]],
    bench_bars: list[dict[str, Any]],
    target_decimals: int = 2,
    bench_decimals: int = 2,
) -> dict[str, Any]:
    """个股与大盘的对齐关系：联动程度、相对强弱线、拐点先后、新低新高对照、阶段对照。"""
    b_close = {b["day"]: float(b["close"]) for b in bench_bars if float(b["close"]) > 0}
    common = [b for b in target_bars if b["day"] in b_close and float(b["close"]) > 0]
    days = [b["day"] for b in common]
    pos = {d: i for i, d in enumerate(days)}
    tail = common[-ALIGN_DAYS:]
    rs = [float(b["close"]) / b_close[b["day"]] for b in tail]

    beta = corr = None
    if len(tail) >= ALIGN_MIN_DAYS:
        rt = [math.log(float(tail[i]["close"]) / float(tail[i - 1]["close"])) for i in range(1, len(tail))]
        rb = [math.log(b_close[tail[i]["day"]] / b_close[tail[i - 1]["day"]]) for i in range(1, len(tail))]
        mt, mb = sum(rt) / len(rt), sum(rb) / len(rb)
        cov = sum((x - mt) * (y - mb) for x, y in zip(rt, rb)) / len(rt)
        vt = sum((x - mt) ** 2 for x in rt) / len(rt)
        vb = sum((y - mb) ** 2 for y in rb) / len(rb)
        if vb > 0 and vt > 0:
            beta, corr = round(cov / vb, 3), round(cov / math.sqrt(vt * vb), 3)
    has60 = len(rs) >= ALIGN_MIN_DAYS
    return {
        "common_day_count": len(tail),
        "beta_120": beta,
        "corr_120": corr,
        "rs_trend_20": _trend(rs, 20),
        "rs_trend_60": _trend(rs, 60) if has60 else None,
        "rs_at_60d_high": bool(rs[-1] >= max(rs[-60:])) if has60 else None,
        "rs_at_60d_low": bool(rs[-1] <= min(rs[-60:])) if has60 else None,
        "low_lead_lag_bars": _lead_lag(target_layer, bench_layer, "L", pos),
        "high_lead_lag_bars": _lead_lag(target_layer, bench_layer, "H", pos),
        "low_compare": _extreme_compare(bench_layer, "L", days, pos, {b["day"]: float(b["low"]) for b in common}),
        "high_compare": _extreme_compare(bench_layer, "H", days, pos, {b["day"]: float(b["high"]) for b in common}),
        "target_phase": classify_cycle_phase(target_bars, target_decimals)["phase"],
        "benchmark_phase": classify_cycle_phase(bench_bars, bench_decimals)["phase"],
    }
```

说明：`_trend(rs, 60)` 需要 61 个值；共同交易日恰好 60 个时返回 `None`，与 `has60` 的判断一起保证不越界。

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -s`
Expected: 全部通过，输出里有 `low_compare cuts: N window_incomplete: M`。

- [ ] **Step 5: 提交**

```bash
git add kline-structure-chart/scripts/market_structure.py kline-structure-chart/tests/test_market_structure.py
git commit -m "feat(market-layer): 个股与大盘对齐关系（联动、相对强弱线、拐点先后、新低新高对照）"
```

---

### Task 3: 基准结构摘要、编排与接入 `analyze_bundle`

**Files:**
- Modify: `kline-structure-chart/scripts/market_structure.py`
- Modify: `kline-structure-chart/scripts/analysis.py:234-240`（签名）、`:373` 之后（调用）、`:545-562`（报告字典）
- Modify: `kline-structure-chart/scripts/contracts.py:101` 附近
- Modify: `kline-structure-chart/scripts/evaluation.py:722`
- Test: `kline-structure-chart/tests/test_market_structure.py`

**Interfaces:**
- Consumes: Task 1、2 的函数；`indicators.compute_all_indicators(bars) -> {"series": {...}, "snapshot": {...}}`；`patterns.causal_swing_pivots(sview, zz_thr, price_decimals=)`；`patterns.detect_geometric_patterns(sview, pivots, atr_series, rvol_series, price_decimals=)`；`market_rules.price_decimals(price, market, asset_type, day)`、`round_to_tick(price, market, asset_type, day)`、`DEFAULT_BENCHMARKS`。
- Produces:
  - `summarize_benchmark_structure(bench_layer: dict, bench_patterns: list[dict]) -> dict`：键 `last_close, wedge, ladder, scenario, wave_counts, patterns, projections`（内部形状，Task 5 投影）。
  - `compute_market_layer(bundle: dict, target_layer: dict, horizon_bars: int) -> dict | None`：`None` 表示缺少基准；否则 `{"structure": dict | None, "structure_reason": str | None, "cycle": dict（含 basis）, "alignment": dict | None, "benchmark_price_decimals": int | None}`。
  - `analysis.analyze_bundle(..., include_market_layer: bool = True)`；内部报告新增 `report["market_layer"]`（未计算时为 `None`）。

- [ ] **Step 1: 写失败的测试**

追加到 `tests/test_market_structure.py`：

```python
from analyze_and_forecast import run_pipeline  # noqa: E402

REQ = {"display_bars": 120, "structure_bars": 120, "horizon_bars": 15}
FIVE = [("CN", "600519.SH", "equity"), ("CN", "000001.SH", "index"), ("HK", "00700.HK", "equity"),
        ("US", "NVDA.US", "equity"), ("US", "SPY.US", "etf")]


def test_market_layer_present_for_stock(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    ml = rep["market_layer"]
    assert ml["cycle"]["basis"] == "BENCHMARK" and ml["cycle"]["phase"] in ("ADVANCE", "DECLINE", "TOPPING", "BASING")
    s = ml["structure"]
    assert set(s) == {"last_close", "wedge", "ladder", "scenario", "wave_counts", "patterns", "projections"}
    assert not any(k.startswith("_") for k in s["wedge"])
    assert all(p["category"] == "geometric" for p in s["patterns"])
    assert ml["alignment"]["common_day_count"] == 120
    assert ml["benchmark_price_decimals"] == 2


def test_market_layer_self_benchmark(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "SPY.US", "market": "US", "asset_type": "etf", **REQ}, output_dir=tmp_path)
    ml = rep["market_layer"]
    assert ml["structure"] is None and ml["alignment"] is None
    assert ml["cycle"]["basis"] == "SELF" and ml["benchmark_price_decimals"] is None


def test_market_layer_none_without_benchmark() -> None:
    from market_structure import compute_market_layer
    from structure import STRUCT_DAYS, compute_structure_layer

    bars = load_recorded(MOUTAI)
    bundle = {"market": "CN", "benchmark": None,
              "target": {"instrument": {"symbol": "600519.SH", "market": "CN", "asset_type": "equity"}, "bars": bars}}
    assert compute_market_layer(bundle, compute_structure_layer(bars, None, STRUCT_DAYS), 15) is None


@pytest.mark.slow
@pytest.mark.parametrize("market,symbol,asset_type", FIVE)
def test_decisions_do_not_depend_on_market_layer(replay_recorded, market, symbol, asset_type) -> None:
    """每 5 个交易日取一个截面：开与关大盘层，除 market_layer 外内部报告逐项相同。"""
    import data_gateway
    from analysis import analyze_bundle

    req = {"symbol": symbol, "market": market, "asset_type": asset_type, **REQ}
    full = data_gateway.load_bundle(req)
    bars, bench = full["target"]["bars"], full.get("benchmark")
    checked = 0
    # 截面的构造方式与 evaluation.run_plan_backtest 相同：标的截到第 t 根，基准截到同一天
    for t in range(len(bars) - 250, len(bars), 5):
        cut_day = bars[t]["day"]
        bundle = {
            **full,
            "target": {**full["target"], "bars": bars[: t + 1], "intraday": None},
            "benchmark": {**bench, "bars": [b for b in bench["bars"] if b["day"] <= cut_day]} if bench else None,
        }
        on = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_market_layer=True)
        off = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_market_layer=False)
        assert off["market_layer"] is None
        on.pop("market_layer"), off.pop("market_layer")
        assert on == off, f"{symbol} {cut_day}"
        checked += 1
    assert checked == 50
```

录制行情每个标的有 1000 根以上，`load_bundle` 返回最近 760 根，最早的截面仍有 510 根历史，足够 420 根预热。内部报告里没有随调用时间变化的字段，`report_hash` 只由决策字段算出，所以两次结果应当逐项相等。

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -k "market_layer or decisions"`
Expected: FAIL，`KeyError: 'market_layer'` 或 `ImportError`

- [ ] **Step 3: 实现编排**

`market_structure.py` 导入区补充三行，并把原来的 `from structure import ...` 一行换成下面最后一行：

```python
from indicators import compute_all_indicators
from market_rules import DEFAULT_BENCHMARKS, price_decimals, round_to_tick
from patterns import causal_swing_pivots, detect_geometric_patterns
from structure import HISTORY_BARS, MIN_RAIL_SPAN, STRUCT_DAYS, TREND_FLAT_PCT, compute_structure_layer
```

文件末尾追加：

```python
def summarize_benchmark_structure(bench_layer: dict[str, Any], bench_patterns: list[dict[str, Any]]) -> dict[str, Any]:
    """基准结构层中对外有意义的部分（内部形状；对外字段由 contract_v6 投影）。"""
    return {
        "last_close": float(bench_layer["last_c"]),
        "wedge": {k: v for k, v in bench_layer["wedge"].items() if not k.startswith("_")},
        "ladder": bench_layer["ladder"],
        "scenario": bench_layer["scenario"],
        "wave_counts": bench_layer["wave_counts"],
        "patterns": [p for p in bench_patterns if p.get("category") == "geometric"],
        "projections": bench_layer["projections"],
    }


def compute_market_layer(bundle: dict[str, Any], target_layer: dict[str, Any], horizon_bars: int) -> dict[str, Any] | None:
    """大盘层编排。缺少基准返回 None；标的自身即基准或指数时只给自身的周期阶段。"""
    inst = bundle["target"]["instrument"]
    t_bars = target_layer["completed"]
    t_dec = int(target_layer["price_decimals"])
    bench = bundle.get("benchmark")
    # 自己按标的最后一个已收盘日再截一次基准，不依赖上游已经对齐（Task 4 的因果审计守护）
    b_bars = [b for b in (bench or {}).get("bars") or [] if b["day"] <= t_bars[-1]["day"]]
    if not b_bars:
        is_self = (
            inst["asset_type"] == "index"
            or inst["symbol"].upper() == DEFAULT_BENCHMARKS.get(inst["market"], {}).get("symbol", "").upper()
        )
        if not is_self:
            return None
        return {"structure": None, "structure_reason": "标的自身即基准或指数", "alignment": None,
                "cycle": {"basis": "SELF", **classify_cycle_phase(t_bars, t_dec)}, "benchmark_price_decimals": None}

    b_inst = bench["instrument"]
    b_dec = price_decimals(float(b_bars[-1]["close"]), b_inst["market"], b_inst["asset_type"], b_bars[-1]["day"])
    out: dict[str, Any] = {
        "structure": None, "structure_reason": None, "alignment": None,
        "cycle": {"basis": "BENCHMARK", **classify_cycle_phase(b_bars, b_dec)},
        "benchmark_price_decimals": b_dec,
    }
    if len(b_bars) < STRUCT_DAYS:
        out["structure_reason"] = f"基准已收盘K线 {len(b_bars)} 根，少于 {STRUCT_DAYS} 根，不计算结构"
        return out
    b_layer = compute_structure_layer(
        b_bars, None, STRUCT_DAYS,
        price_decimals=lambda price, day: price_decimals(price, b_inst["market"], b_inst["asset_type"], day),
        horizon_bars=horizon_bars,
        round_price=lambda price, day: round_to_tick(price, b_inst["market"], b_inst["asset_type"], day),
    )
    sview = b_layer["sview"]
    series = compute_all_indicators(b_layer["hist"])["series"]
    piv = causal_swing_pivots(sview, b_layer["zz_thr"], price_decimals=b_dec)
    pats = detect_geometric_patterns(
        sview, piv, series["atr14"][-len(sview):], series["rvol20"][-len(sview):], price_decimals=b_dec
    )
    out["structure"] = summarize_benchmark_structure(b_layer, pats)
    out["alignment"] = compute_alignment(target_layer, b_layer, t_bars, b_bars, t_dec, b_dec)
    return out
```

- [ ] **Step 4: 接入 `analysis.py`、`contracts.py`、`evaluation.py`**

`analysis.py` 导入区加 `from market_structure import compute_market_layer`。签名（234-240 行）在 `include_forecast: bool = True,` 之后加一行：

```python
    include_market_layer: bool = True,
```

在第 5 步 `mtf_score = compute_multi_timeframe_score(...)` 调用结束之后（373 行后）加：

```python
    # 4b. 大盘结构与周期层：只做描述，单独存放，不并入 benchmark_info（评分与风控读不到它）
    market_layer = compute_market_layer(bundle, layer, horizon_bars) if include_market_layer else None
```

报告字典里 `"market_benchmark": benchmark_info,` 下一行加：

```python
        "market_layer": market_layer,
```

`contracts.py` 的 `AnalysisReport` 中 `market_benchmark: dict[str, Any]` 下一行加：

```python
    market_layer: dict[str, Any] | None
```

`evaluation.py:722` 改为：

```python
        rep = analyze_bundle(sub_bundle, sub_request, include_replay=False, include_forecast=False, include_market_layer=False)
```

- [ ] **Step 5: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q`
Expected: 全部通过。

如果 `test_decisions_do_not_depend_on_market_layer` 失败，说明有决策路径读到了新结果：停下来找原因，不要改测试去放行。

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow" -q`
Expected: 通过数不少于基线；`test_c06_pipeline_is_days_invariant` 通过（它比较的键不含 `market_layer`，下一步补上）。

- [ ] **Step 6: 把 `market_layer` 纳入跨窗口不变性**

`tests/test_quant_skill.py:795` 的 `keys` 元组里，在 `"market_benchmark",` 下一行加 `"market_layer",`。

Run: `uv run --directory kline-structure-chart pytest tests/test_quant_skill.py -q -k c06_pipeline_is_days_invariant`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add kline-structure-chart/scripts kline-structure-chart/tests
git commit -m "feat(market-layer): 基准结构摘要与大盘层编排，接入 analyze_bundle（决策不读取，回测关闭）"
```

---

### Task 4: 大盘层的因果审计

**Files:**
- Modify: `kline-structure-chart/scripts/evaluation.py:37-40, 239-254`（导入、`run_causal_replay_audit` 签名与返回值；新增 `audit_market_layer_causality`）
- Modify: `kline-structure-chart/scripts/analyze_and_forecast.py:112`
- Test: `kline-structure-chart/tests/test_market_structure.py`

**Interfaces:**
- Consumes: Task 3 的 `market_structure.compute_market_layer(bundle, target_layer, horizon_bars)`（它自己按标的最后一个已收盘日截断基准）；`structure.compute_structure_layer`、`STRUCT_DAYS`。
- Produces:
  - `evaluation.audit_market_layer_causality(bundle: dict, check_steps: int = 4) -> list[str]`（不一致说明的列表，空表示通过）。
  - `evaluation.run_causal_replay_audit(bars, check_steps=8, bundle=None)`：有基准时 `modules_audited` 追加 `"market_layer"`；没有时写入新键 `modules_skipped`，不记为通过。

审计要守住的性质：大盘层只读取标的最后一个已收盘日及以前的基准K线。做法是标的截到截面日，基准保留截面日之后的K线并把价格乘以 3；结果必须与基准也截到截面日时逐项相同。

- [ ] **Step 1: 写失败的测试**

```python
def test_market_layer_ignores_benchmark_bars_after_cut(replay_recorded) -> None:
    import data_gateway
    from evaluation import audit_market_layer_causality
    from market_structure import classify_cycle_phase

    bundle = data_gateway.load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ})
    assert audit_market_layer_causality(bundle) == []
    # 对照：同一段K线被乘以 3 后，周期阶段的输出一定不同；所以上面的相等不是空转
    b = bundle["benchmark"]["bars"]
    dirty = b[:-30] + [{**x, **{k: float(x[k]) * 3.0 for k in ("open", "high", "low", "close")}} for x in b[-30:]]
    assert classify_cycle_phase(dirty)["weekly_close"] != classify_cycle_phase(b)["weekly_close"]


@pytest.mark.slow
def test_causal_audit_reports_market_layer(replay_recorded) -> None:
    import data_gateway
    from evaluation import run_causal_replay_audit

    bundle = data_gateway.load_bundle({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ})
    res = run_causal_replay_audit(bundle["target"]["bars"], bundle=bundle)
    assert "market_layer" in res["modules_audited"] and res["modules_skipped"] == [] and res["passed"]
    res2 = run_causal_replay_audit(bundle["target"]["bars"])
    assert res2["modules_skipped"] == ["market_layer"] and "market_layer" not in res2["modules_audited"]
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -k "after_cut or audit_reports"`
Expected: FAIL，`ImportError: cannot import name 'audit_market_layer_causality'`

- [ ] **Step 3: 实现**

`evaluation.py` 导入区（`from structure import ...` 一行之前）加：

```python
from market_structure import compute_market_layer
```

在 `run_causal_replay_audit` 之前加：

```python
def audit_market_layer_causality(bundle: dict[str, Any], check_steps: int = 4) -> list[str]:
    """大盘层零前视：标的截到截面日，基准保留截面日之后的K线并把价格乘以 3。

    结果必须与基准也截到截面日时逐项相同，即大盘层不读取标的最后一个已收盘日之后的任何基准K线。
    """
    t_bars, bench = bundle["target"]["bars"], bundle["benchmark"]
    n = len(t_bars)
    mismatches: list[str] = []
    step = max(10, (n - 440) // max(1, check_steps))
    for cut in range(440, n - 5, step):
        cut_day = str(t_bars[cut - 1]["day"])
        t_layer = compute_structure_layer(t_bars[:cut], None, STRUCT_DAYS)

        def run(b_bars: list[dict[str, Any]]) -> dict[str, Any] | None:
            sub = {**bundle, "target": {**bundle["target"], "bars": t_bars[:cut]}, "benchmark": {**bench, "bars": b_bars}}
            return compute_market_layer(sub, t_layer, 15)

        clean = run([b for b in bench["bars"] if b["day"] <= cut_day])
        dirty = run(
            [b if b["day"] <= cut_day else {**b, **{k: float(b[k]) * 3.0 for k in ("open", "high", "low", "close")}}
             for b in bench["bars"]]
        )
        if clean != dirty:
            mismatches.append(f"Market layer read benchmark bars after cut_day={cut_day}")
    return mismatches
```

`run_causal_replay_audit` 的签名改为：

```python
def run_causal_replay_audit(
    bars: list[dict[str, Any]], check_steps: int = 8, bundle: dict[str, Any] | None = None
) -> dict[str, Any]:
```

在它的 `return {` 之前加：

```python
    # 大盘层：需要基准与至少 460 根标的K线；条件不满足时记为跳过，不记为通过
    market_layer_audited = bool(bundle and bundle.get("benchmark") and n >= 460)
    if market_layer_audited:
        mismatches.extend(audit_market_layer_causality(bundle))
```

返回字典改为：

```python
    return {
        "checked_cutoffs": checked,
        "modules_audited": [
            "nadaraya_watson",
            "structure_layer_pivots",
            "structure_and_scenarios",
            "candlestick_patterns",
            "smc_structures",
            "conformal_forecast",
            "pit_rescaling",
            "triple_barrier_decision",
            *(["market_layer"] if market_layer_audited else []),
        ],
        "modules_skipped": [] if market_layer_audited else ["market_layer"],
        "mismatches_count": len(mismatches),
        "mismatches": mismatches[:10],
        "passed": len(mismatches) == 0,
    }
```

`analyze_and_forecast.py:112` 改为：

```python
        causal_audit = run_causal_replay_audit(bundle["target"]["bars"], bundle=bundle)
```

- [ ] **Step 4: 运行，确认通过；更新 backtest 键路径快照**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -k "after_cut or audit_reports"`
Expected: 2 passed

`backtest.json` 多了 `modules_skipped`。Run:

```bash
UPDATE_V6_KEYPATHS=1 uv run --directory kline-structure-chart pytest -q tests/test_v6_contract.py -k backtest_keypaths
git diff kline-structure-chart/tests/fixtures/contract/backtest_v6_keypaths_600519.json
uv run --directory kline-structure-chart pytest -q tests/test_v6_contract.py -k backtest_keypaths
```

Expected: 第一次以"已改写快照"失败；diff 只新增 `modules_skipped` 一条路径；第二次 PASS。

- [ ] **Step 5: 提交**

```bash
git add kline-structure-chart
git commit -m "feat(market-layer): 因果回放审计新增大盘层项，无基准时记为跳过"
```

---

### Task 5: 契约投影、schema 与回复校验

**Files:**
- Modify: `kline-structure-chart/scripts/contract_v6.py:93-104, 161-168`
- Modify: `kline-structure-chart/assets/analysis.schema.json`（`properties.market_context.properties`、`properties.quotable_prices.properties`）
- Modify: `kline-structure-chart/scripts/lint_response.py:221-228, 262-274`
- Test: `kline-structure-chart/tests/test_market_structure.py`；重新生成 `tests/fixtures/contract/result_v6_keypaths_*.json`

**Interfaces:**
- Consumes: Task 3 的 `report["market_layer"]`；`price_text.collect_price_whitelist(sections, price_decimals, recent_bars) -> {"price_decimals", "tolerance", "count", "prices"}`；`contract_v6._strip_scenario(item)`。
- Produces:
  - `result["market_context"]["structure"]`：`None` 或 `{last_close, channel, key_levels, abc_scenario, wave_counts, patterns, scenarios: {breakout_level, breakdown_level, active, impulse_wave}}`。
  - `result["market_context"]["cycle"]`：`None` 或 Task 1 的字典加 `basis`。
  - `result["market_context"]["alignment"]`：`None` 或 Task 2 的字典。
  - `result["quotable_prices"]["benchmark"]`：`None` 或 `{price_decimals, tolerance, prices}`。

- [ ] **Step 1: 写失败的测试**

```python
import json  # 放到文件顶部导入区


def _result(rep: dict) -> dict:
    return json.loads(Path(rep["rendered_paths"]["result_json"]).read_text(encoding="utf-8"))


def test_contract_projects_market_layer(replay_recorded, tmp_path: Path) -> None:
    from contracts import validate_report

    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    validate_report(res)
    mc = res["market_context"]
    assert set(mc["structure"]) == {"last_close", "channel", "key_levels", "abc_scenario", "wave_counts",
                                    "patterns", "scenarios"}
    assert set(mc["structure"]["scenarios"]) == {"breakout_level", "breakdown_level", "active", "impulse_wave"}
    for item in mc["structure"]["scenarios"]["active"]:
        assert not {"waypoints_offset", "step_labels", "eta_bars", "target_within_horizon"} & set(item)
    assert mc["cycle"]["basis"] == "BENCHMARK" and mc["alignment"]["common_day_count"] == 120
    qb = res["quotable_prices"]["benchmark"]
    assert qb["price_decimals"] == 2 and mc["cycle"]["ma30w_price"] in qb["prices"]
    assert mc["structure"]["last_close"] in qb["prices"]
    assert mc["cycle"]["range_week_count"] not in qb["prices"]  # 周数不是价格
    size = Path(rep["rendered_paths"]["result_json"]).stat().st_size
    assert size <= 80 * 1024, size


def test_quotable_self_basis(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "SPY.US", "market": "US", "asset_type": "etf", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    mc = res["market_context"]
    assert mc["structure"] is None and mc["alignment"] is None and mc["cycle"]["basis"] == "SELF"
    assert res["quotable_prices"]["benchmark"] is None
    assert mc["cycle"]["ma30w_price"] in res["quotable_prices"]["prices"]


def test_market_context_nulls_without_benchmark() -> None:
    import contract_v6

    assert contract_v6._market_layer_fields(None) == {"structure": None, "cycle": None, "alignment": None}


def test_lint_benchmark_price_needs_index_context(replay_recorded, tmp_path: Path) -> None:
    from lint_response import lint_agent_response

    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    px = res["market_context"]["cycle"]["ma30w_price"]
    assert px not in res["quotable_prices"]["prices"]
    tail = "\n\n以上内容不构成投资建议。"
    ok = lint_agent_response(f"大盘周线均线附近的支撑位 {px}。{tail}", res)
    bad = lint_agent_response(f"该股下方支撑位 {px}。{tail}", res)
    assert not [e for e in ok if "白名单" in e]
    assert [e for e in bad if "白名单" in e]
```

两句都靠"支撑位"加数字被 `_LINT_PRICE_AFTER_RE` 识别为价格；区别只在句子里有没有"大盘"。句子里不要再写别的数字（例如"30 周"），免得干扰断言。

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -k "contract_projects or quotable or nulls_without or lint_benchmark"`
Expected: FAIL（`KeyError: 'structure'`、`AttributeError: _market_layer_fields`）

- [ ] **Step 3: 实现投影**

`contract_v6.py` 在 `_draw_only` 之后加：

```python
def _market_layer_fields(ml: dict[str, Any] | None) -> dict[str, Any]:
    """内部 market_layer → market_context 的三个实验字段。缺少基准时三者都为 None。"""
    if ml is None:
        return {"structure": None, "cycle": None, "alignment": None}
    s = ml.get("structure")
    structure = None
    if s is not None:
        pj = s["projections"]
        structure = {
            "last_close": round(s["last_close"], int(ml["benchmark_price_decimals"])),
            "channel": s["wedge"],
            "key_levels": s["ladder"],
            "abc_scenario": s["scenario"],
            "wave_counts": s["wave_counts"],
            "patterns": [{k: v for k, v in p.items() if k != "invalidated"} for p in s["patterns"]],
            "scenarios": {
                "breakout_level": pj["breakout_level"],
                "breakdown_level": pj["breakdown_level"],
                "active": [_strip_scenario(x) for x in pj.get("scenarios") or []],
                "impulse_wave": pj.get("impulse_wave"),
            },
        }
    return {"structure": structure, "cycle": ml["cycle"], "alignment": ml.get("alignment")}
```

`structure` 为 `null` 的原因不进 `result.json`（`null` 本身加 `cycle.basis` 已能区分三种情形）；内部的 `structure_reason` 只留在内部报告里供排查。

`build_result` 里 `mb = report["market_benchmark"]` 下一行加：

```python
    ml = report.get("market_layer")
    ml_fields = _market_layer_fields(ml)
```

`"market_context": {` 字典里 `"bench_ret5": mb.get("bench_ret5"),` 之后加：

```python
            **ml_fields,
```

把文件末尾收集白名单的一段（161-168 行）替换为：

```python
    # 可引用价位只从 result.json 自己的价位字段与最近 20 根K线收集：前者能在 result.json 里找到出处，
    # 最近 20 根K线的价格只存在于 chart_data.json（result.json 不含K线序列）
    sections = {k: result[k] for k in _QUOTABLE_SECTIONS}
    self_basis = bool(ml_fields["cycle"]) and ml_fields["cycle"].get("basis") == "SELF"
    if self_basis:
        sections["cycle"] = ml_fields["cycle"]  # 标的自身的周期价位属于标的
    wl = collect_price_whitelist(sections, int(meta.get("price_decimals") or 2), report["series_export"]["bars"])
    result["quotable_prices"] = {k: wl[k] for k in ("price_decimals", "tolerance", "prices")}
    bench_wl = None
    if ml is not None and not self_basis and ml.get("benchmark_price_decimals") is not None:
        bw = collect_price_whitelist(
            {"structure": ml_fields["structure"], "cycle": ml_fields["cycle"]}, int(ml["benchmark_price_decimals"]), []
        )
        bench_wl = {k: bw[k] for k in ("price_decimals", "tolerance", "prices")}
    result["quotable_prices"]["benchmark"] = bench_wl
    return result
```

`collect_price_whitelist` 按键名判断是不是价格：`weekly_close`、`ma30w_price`、`high_60w_price`、`low_60w_price`、`ma10m_price`、`last_close` 会被收集；`slope_4w_pct`、`pos_60w_pct`、`range_week_count` 命中非价格片段（`pct`、`count`）被跳过。`alignment` 不传进去，它没有价格。

- [ ] **Step 4: 更新 schema**

`assets/analysis.schema.json` 的 `properties.market_context.properties` 末尾（`bench_ret5` 之后）加：

```json
"structure": {
  "type": ["object", "null"],
  "x-stability": "experimental",
  "description": "基准指数的结构摘要：字段形状与顶层 structure / scenarios 的同名字段相同；标的自身即基准或缺少基准时为 null"
},
"cycle": {
  "type": ["object", "null"],
  "x-stability": "experimental",
  "description": "周线阶段（ADVANCE / TOPPING / DECLINE / BASING）、已持续周数与月线相对 10 月均线的位置；basis 为 BENCHMARK 或 SELF"
},
"alignment": {
  "type": ["object", "null"],
  "x-stability": "experimental",
  "description": "个股与大盘的对齐关系：联动程度、相对强弱线、拐点先后、新低新高对照、阶段对照"
}
```

`properties.quotable_prices.properties` 末尾加：

```json
"benchmark": {
  "type": ["object", "null"],
  "x-stability": "experimental",
  "description": "基准指数的可引用价位 {price_decimals, tolerance, prices}；只在提到大盘的句子里可引用"
}
```

`contracts.schema_errors` 只对写了 `additionalProperties: false` 的对象拒绝多余键，而 schema 里只有顶层写了它，所以这四个字段用不列子属性的 `object` 即可通过校验；子字段的含义写在 `references/output-fields.md`（Task 7）。

- [ ] **Step 5: 回复校验放行大盘语境下的基准价位**

`lint_response.py` 在 `wl_tol = float(whitelist.get("tolerance") or 0.01)` 之后加：

```python
    bench_wl = whitelist.get("benchmark") or {}
    bench_prices: list[float] = [float(p) for p in bench_wl.get("prices") or []]
    bench_tol = float(bench_wl.get("tolerance") or 0.01)
    mc = report.get("market_context") or {}
    idx_sym = str(mc.get("index_symbol") or "")
    bench_words = [w for w in (mc.get("index_name"), idx_sym, idx_sym.split(".")[0], "大盘", "指数", "基准") if w]
```

把循环末尾的

```python
            if not price_in_whitelist(cand, wl_prices, wl_tol) and msg not in errors:
                errors.append(msg)
```

改为：

```python
            if price_in_whitelist(cand, wl_prices, wl_tol):
                continue
            # 基准指数的价位只在提到大盘的句子里可引用；text 与 cleaned 等长与否无关，这里在 cleaned 上取句
            sent = _sentence_around(cleaned, m.start(), m.end())
            if bench_prices and price_in_whitelist(cand, bench_prices, bench_tol) and any(w in sent for w in bench_words):
                continue
            if msg not in errors:
                errors.append(msg)
```

- [ ] **Step 6: 运行，确认通过；处理体积预算**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py tests/test_v6_contract.py -q -k "not keypaths"`
Expected: 全部通过。

若任一用例报 `result.json … 超过 80 KB`，按规格第 3.4 节的顺序裁剪，在 `_market_layer_fields` 里依次启用，每启用一项重跑一次，够用即止：

```python
            "wave_counts": [w for w in s["wave_counts"] if w.get("role") == "主计数"],
```

```python
            "key_levels": {**s["ladder"], "supports": s["ladder"]["supports"][:3], "resistances": s["ladder"]["resistances"][:3]},
```

实际启用了哪一项，写进 Task 7 的 `output-fields.md`。

- [ ] **Step 7: 重新生成键路径快照并核对**

```bash
UPDATE_V6_KEYPATHS=1 uv run --directory kline-structure-chart pytest -q tests/test_v6_contract.py -k keypaths_match_snapshot
git diff --stat kline-structure-chart/tests/fixtures/contract/
git diff kline-structure-chart/tests/fixtures/contract/ | grep '^[+-]' | grep -v 'market_context\|quotable_prices.benchmark\|^+++\|^---'
uv run --directory kline-structure-chart pytest -q tests/test_v6_contract.py
```

Expected: 第一条以"已改写快照"失败；第三条没有输出（差异只在 `market_context.{structure,cycle,alignment}` 与 `quotable_prices.benchmark` 之下，且没有删除任何既有路径）；最后一条全部通过。

- [ ] **Step 8: 提交**

```bash
git add kline-structure-chart
git commit -m "feat(contract): market_context 新增 structure/cycle/alignment 实验字段与基准可引用价位，回复校验按大盘语境放行"
```

---

### Task 6: `report.md` 与 HTML 看板

**Files:**
- Modify: `kline-structure-chart/scripts/render_report_md.py:126-151`
- Modify: `kline-structure-chart/scripts/render_html.py:512-516` 附近（最新截面分支）
- Test: `kline-structure-chart/tests/test_market_structure.py`

**Interfaces:**
- Consumes: Task 5 的 `result["market_context"]`、`result["quotable_prices"]["benchmark"]`。
- Produces: `render_report_md._market_layer_lines(ctx: dict, bench_decimals: int) -> list[str]`。

- [ ] **Step 1: 写失败的测试**

```python
def test_report_md_has_market_layer_and_passes_lint(replay_recorded, tmp_path: Path) -> None:
    from lint_response import lint_agent_response

    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    res = _result(rep)
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    cyc, ali = res["market_context"]["cycle"], res["market_context"]["alignment"]
    assert "### 大盘结构" in md and "### 周期阶段" in md and "### 个股与大盘" in md
    assert cyc["phase_cn"] in md and f"{cyc['phase_weeks']}" in md
    if ali["low_compare"]:
        assert ali["low_compare"]["label_cn"] in md
        assert ("未稳定" in md) == (not ali["low_compare"]["window_complete"] or
                                    bool(ali["high_compare"] and not ali["high_compare"]["window_complete"]))
    assert "不构成交易信号，也不改变风控结论" in md
    assert lint_agent_response(md, res) == []


def test_report_md_market_layer_absent_cases(replay_recorded, tmp_path: Path) -> None:
    from render_report_md import _market_layer_lines

    assert _market_layer_lines({"structure": None, "cycle": None, "alignment": None}, 2) == []
    rep = run_pipeline({"symbol": "SPY.US", "market": "US", "asset_type": "etf", **REQ}, output_dir=tmp_path)
    md = Path(rep["rendered_paths"]["report_md"]).read_text(encoding="utf-8")
    assert "### 周期阶段" in md and "### 大盘结构" not in md and "### 个股与大盘" not in md


def test_dashboard_embeds_market_layer_rows(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline({"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ}, output_dir=tmp_path)
    html = Path(rep["rendered_paths"]["dashboard_html"]).read_text(encoding="utf-8")
    for label in ("大盘周线阶段", "大盘日线通道", "大盘低点对照"):
        assert label in html
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py -q -k "report_md or dashboard"`
Expected: FAIL（`ImportError: _market_layer_lines`、断言找不到小标题）

- [ ] **Step 3: 实现 `report.md`**

`render_report_md.py` 在 `render_report_markdown` 之前加：

```python
def _market_layer_lines(ctx: dict[str, Any], bench_decimals: int) -> list[str]:
    """第 2 节的大盘结构、周期阶段、个股与大盘三段；数字全部取自 result.json 的 market_context。"""

    def bp(value: Any) -> str:
        return "—" if value is None else f"{float(value):.{bench_decimals}f}"

    lines: list[str] = []
    s, cyc, ali = ctx.get("structure"), ctx.get("cycle"), ctx.get("alignment")
    if s:
        ch = s["channel"]
        lines += ["### 大盘结构", "", f"- 大盘日线通道：{_cell(ch.get('status_text') or ch.get('status'))}（指数收盘 {bp(s['last_close'])}）。"]
        primary = next((w for w in s.get("wave_counts") or [] if w.get("role") == "主计数"), None)
        if primary:
            lines.append(
                f"- 大盘主计数：{primary['label']}，{primary['c_progress']['phase']}；"
                f"否定条件：大盘{primary['count_invalidation']['text']}。"
            )
        sup = [bp(x["price"]) for x in (s["key_levels"].get("supports") or [])[:2]]
        res = [bp(x["price"]) for x in (s["key_levels"].get("resistances") or [])[:2]]
        if sup or res:
            lines.append(f"- 大盘最近的支撑位 {' / '.join(sup) or '—'}，大盘最近的压力位 {' / '.join(res) or '—'}。")
        lines.append("")
    if cyc and cyc.get("phase"):
        who = "大盘" if cyc.get("basis") == "BENCHMARK" else "标的自身"
        weeks = f"{cyc['phase_weeks']}{' 周以上' if cyc['phase_weeks_capped'] else ' 周'}"
        month = {"ABOVE": "之上", "BELOW": "之下"}.get(cyc.get("monthly_vs_ma10"))
        lines += [
            "### 周期阶段",
            "",
            f"- {who}周线处于{cyc['phase_cn']}，已持续 {weeks}；{who}周线收盘 {bp(cyc['weekly_close'])}，"
            f"{who} 30 周均线 {bp(cyc['ma30w_price'])}。",
        ]
        if month:
            lines.append(f"- {who}月线在 10 月均线（{bp(cyc['ma10m_price'])}）{month}。")
        lines += ["- 阶段只在一周结束后更新，它描述位置，不预示方向。", ""]
    elif cyc and cyc.get("reason"):
        lines += ["### 周期阶段", "", f"- {cyc['reason']}。", ""]
    if ali:
        trend_cn = {"UP": "走强", "DOWN": "走弱", "FLAT": "走平", None: "样本不足"}
        lines += ["### 个股与大盘", ""]
        if ali.get("beta_120") is not None:
            lines.append(f"- 近 {ali['common_day_count']} 个共同交易日：对大盘的回归斜率 {ali['beta_120']:.3f}，相关系数 {ali['corr_120']:.3f}。")
        lines.append(f"- 相对强弱线：近 20 日{trend_cn[ali.get('rs_trend_20')]}，近 60 日{trend_cn[ali.get('rs_trend_60')]}。")
        for key, name in (("low_lead_lag_bars", "低点"), ("high_lead_lag_bars", "高点")):
            v = ali.get(key)
            if v is not None:
                rel = "同一根K线" if v == 0 else (f"早于大盘 {abs(v)} 根K线" if v < 0 else f"晚于大盘 {v} 根K线")
                lines.append(f"- 最近一个已确认{name}：个股{rel}。")
        for key in ("low_compare", "high_compare"):
            c = ali.get(key)
            if c:
                tag = "" if c["window_complete"] else "（窗口未满 5 根K线，标签未稳定）"
                lines.append(f"- {c['label_cn']}{tag}：对照的大盘拐点日为 {c['benchmark_days'][0]} 与 {c['benchmark_days'][1]}。")
        lines += ["- 以上只是对已发生走势的描述，不构成交易信号，也不改变风控结论。", ""]
    return lines
```

措辞受回复校验器约束，改写时注意三点：
- "收盘""支撑位""压力位""突破""跌破""站上"等词后面 4 个字符内的数字会被当作价格，所以这些词后面只能跟真正的价位，不能跟"10 月""30 周"这类数字（月线一句因此不用"收盘"，均线价放在括号里）。
- 含指数点位的每个分句都要带"大盘""指数"或"基准"，否则基准价位不放行。
- 不出现"买入""做多"等字样：风控否决时校验器禁止它们出现在建议语境里。

在第 2 节三个分支都结束之后、`# 3. 结构与条件推演` 注释之前加：

```python
    bench_dec = int(((result.get("quotable_prices") or {}).get("benchmark") or {}).get("price_decimals") or decimals)
    lines += _market_layer_lines(ctx, bench_dec)
```

`ctx` 是函数开头的 `result.get("market_context")`，第 2 节用的 `bench` 是它的别名（`bench = ctx`）。`decimals` 是标的的价格小数位，只在没有基准价位表时作兜底。

- [ ] **Step 4: 实现 HTML**

`render_html.py` 最新截面分支里，在 `addKv(regGrid, '大盘相对强弱 (RS)', rsVal);` 之后加（注意文件是 f-string 模板，花括号要写成双花括号）：

```javascript
    const mlCycle = bench.cycle, mlStruct = bench.structure, mlAlign = bench.alignment;
    if (mlCycle && mlCycle.phase) {{
      addKv(regGrid, (mlCycle.basis === 'SELF' ? '周线阶段（标的自身）' : '大盘周线阶段'),
        mlCycle.phase_cn + ' · 已持续 ' + mlCycle.phase_weeks + (mlCycle.phase_weeks_capped ? '+' : '') + ' 周');
    }}
    if (mlStruct) {{
      addKv(regGrid, '大盘日线通道', mlStruct.channel.status_short || mlStruct.channel.status || '--');
    }}
    if (mlAlign && mlAlign.low_compare) {{
      addKv(regGrid, '大盘低点对照', mlAlign.low_compare.label_cn + (mlAlign.low_compare.window_complete ? '' : '（未稳定）'));
    }}
```

回放分支在这之前已经 `return`，所以回放时不显示这三行。三个标签文字要作为字面量出现在模板里（测试按字面量查找）。

- [ ] **Step 5: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_market_structure.py tests/test_report_and_metadata.py -q`
Expected: 全部通过。若 `lint_agent_response(md, res)` 报某个基准价位不在白名单，说明该句没有"大盘 / 指数 / 基准"字样：改报告措辞让每个含指数点位的分句都带"大盘"，不要放宽校验器。

- [ ] **Step 6: 浏览器检查（AGENTS.md 第 4.5 条）**

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py \
  --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --output-dir artifacts/cn --backtest
```

用浏览器打开 `kline-structure-chart/artifacts/cn/600519.SH_dashboard.html`，逐项确认并截图：

1. 第 1 栏出现"大盘周线阶段""大盘日线通道""大盘低点对照"三行，内容与 `600519.SH_result.json` 的 `market_context` 一致。
2. 拖动时间回放滑块到历史截面：三行消失；拖回最新截面：三行恢复。
3. 图层开关、相对强弱切换、副图切换照常工作；控制台没有报错。

- [ ] **Step 7: 提交**

```bash
git add kline-structure-chart/scripts kline-structure-chart/tests
git commit -m "feat(render): report.md 与看板呈现大盘结构、周期阶段和个股与大盘的对齐关系"
```

---

### Task 7: 文档同步、示例与全量门禁

**Files:**
- Modify: `kline-structure-chart/SKILL.md`、`kline-structure-chart/references/math-patterns.md`、`references/output-fields.md`、`references/interpretation-guide.md`
- Modify: `AGENTS.md`（第 2 节目录树）、`CHANGELOG.md`、`README.md`、`README.en.md`
- Regenerate: `examples/`

**Interfaces:**
- Consumes: Task 1~6 的全部产出。
- Produces: 无代码接口。

- [ ] **Step 1: `references/math-patterns.md` 新增一节"大盘周期阶段与个股—大盘对齐"**

把规格第 3.3 节（`MA30`、`slope_4w`、`pos_60w`、四类阶段的条件表、`phase_weeks`、月线）与第 3.5 节（`beta_120`、`corr_120`、相对强弱线的 ±2% 规则、拐点先后的 10 根配对、新低新高对照的四格表与对称的高点四格表、`window_complete`）原样写入，并列出常量名与取值：`MA_WEEKS=30`、`SLOPE_WEEKS=4`、`RANGE_WEEKS=60`、`MIN_WEEKS=35`、`MA_MONTHS=10`、`MIN_MONTHS=11`、`ALIGN_DAYS=120`、`ALIGN_MIN_DAYS=60`、`RS_BAND=0.02`、`ALIGN_WINDOW_BARS=5`，注明"常量在规格中写定，未经调参，也未经有效性验证"。

- [ ] **Step 2: `references/output-fields.md`**

在 `market_context` 的字段表里追加 `structure`、`cycle`、`alignment` 三行与各自的子字段表（键名与含义取自 Task 1、2、5 的 Interfaces），稳定级别写 `experimental`；在 `quotable_prices` 下追加 `benchmark`。写明三种 `null` 情形（规格第 3.6 节）以及 Task 5 Step 6 实际启用的裁剪项（没有启用就写"未裁剪"）。

- [ ] **Step 3: `references/interpretation-guide.md` 与 `SKILL.md`**

`interpretation-guide.md` 新增"大盘结构与周期"一节，至少包含这四条常见误读：

```markdown
- "大盘新低、个股未新低"是对已发生走势的描述，不构成交易信号；它不改变风控否决。
- 周线阶段只在一周结束后更新，描述的是位置，不预示下一阶段何时到来。
- `window_complete = false` 时，新低新高对照的标签可能在随后几根K线内改变，引用时必须说明"尚未稳定"。
- 回归斜率与相关系数描述近 120 个共同交易日的联动程度，不是未来的联动承诺。
```

`SKILL.md` 的回复契约里，大盘部分补充：先说大盘周线阶段与日线通道，再说个股；引用指数点位的句子必须出现"大盘""指数"或基准名称；`window_complete = false` 时必须说明标签未稳定；不得把这些描述说成概率或信号。

- [ ] **Step 4: `AGENTS.md`、`CHANGELOG.md`、README**

`AGENTS.md` 第 2 节目录树的 `scripts/` 下，在 `structure.py` 一行之后加：

```text
│   ├── market_structure.py           # 大盘结构与周期层（基准结构摘要、周线月线阶段、个股与大盘对齐；只描述，不参与评分与风控）
```

`tests/` 下加：

```text
    ├── test_market_structure.py      # 大盘结构与周期层：阶段规则、对齐关系、决策不变、契约与呈现
```

`CHANGELOG.md` 的 `[Unreleased]` 下加：

```markdown
### 新增（大盘结构与周期层）

- **契约变更（新增实验字段，`x-stability: experimental`）**：`market_context.structure`（基准指数的结构摘要）、`market_context.cycle`（周线阶段四分类、已持续周数、月线相对 10 月均线的位置）、`market_context.alignment`（个股与大盘的联动程度、相对强弱线、拐点先后、新低新高对照）与 `quotable_prices.benchmark`（基准可引用价位）。
- 新字段只做描述：规则分、评级、否决条件与计划价位不读取它们（`test_decisions_do_not_depend_on_market_layer` 守护）。
- `lint_response.py`：基准价位只在提到大盘、指数或基准名称的句子里可引用。
- `backtest.json` 的因果审计新增 `market_layer` 项与 `modules_skipped`。
- `report.md` 第 2 节与看板第 1 栏呈现上述内容；PNG 不变。
```

`README.md` 与 `README.en.md` 的输出解读一节各加一小段，说明这三组字段的含义与"只描述、不是信号"。

- [ ] **Step 5: 文档契约测试**

Run: `uv run --directory kline-structure-chart pytest tests/test_docs_contract.py -q`
Expected: PASS（目录树与代码同步、引用文件存在）。

- [ ] **Step 6: 重新生成示例**

`examples/README.md` 记录的口径：`600519.SH`、`00700.HK`、`NVDA.US` 用仓库内录制行情回放，`300308.SZ` 联网取新浪日线，截止日都是 `--as-of 2026-10-15`。仓库里没有现成的重生成脚本，用一个不入库的临时文件借 `replay_recorded` 夹具回放（文件名不以 `test_` 开头，平时不会被收集）。

新建 `kline-structure-chart/tests/_regen_examples.py`：

```python
"""一次性脚本：用录制行情重新生成 examples/ 的三个回放示例。跑完即删，不提交。"""
from pathlib import Path

from analyze_and_forecast import main

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_regen(replay_recorded) -> None:
    for market, symbol, extra in (("CN", "600519.SH", ["--backtest"]), ("HK", "00700.HK", []), ("US", "NVDA.US", [])):
        rc = main(["--market", market, "--symbol", symbol, "--asset-type", "equity", "--as-of", "2026-10-15",
                   "--output-dir", str(EXAMPLES / symbol), *extra])
        assert rc == 0, symbol
```

```bash
uv run --directory kline-structure-chart pytest tests/_regen_examples.py -q -p no:xdist
rm kline-structure-chart/tests/_regen_examples.py
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 300308.SZ \
  --asset-type equity --name 中际旭创 --as-of 2026-10-15 --output-dir ../examples/300308.SZ
git status --short examples/
git diff --stat examples/
uv run --directory kline-structure-chart pytest tests/test_docs_contract.py -q
```

Expected:
- 四个目录的 `*_preview.png` 没有变化（PNG 不改）。
- `*_report.md` 的差异只在第 2 节新增的三段；`*_result.json` 的差异只在 `market_context.{structure,cycle,alignment}` 与 `quotable_prices.benchmark`；`600519.SH_backtest.json` 的差异只在因果审计的 `modules_audited` 与 `modules_skipped`。
- 若出现别的差异（例如仓位行、计划价位变了），说明重生成的参数与当初不一致或有决策被改动：先 `git checkout examples/` 还原，查明原因再做，不要把无关差异带进提交。
- `300308.SZ` 需要联网；取数失败时如实记录退出码并保留旧示例，在提交说明里写明这一个示例未更新。
- 文档契约测试通过（其中包含 `examples/` 的报告与标准回复过回复校验）。`examples/README.md` 的"生成时间"一行改成当天日期并注明新增了大盘层字段。

- [ ] **Step 7: 全量门禁（AGENTS.md 第 3.3 节）**

```bash
uv run --directory kline-structure-chart pytest -v -n auto
uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40
```

Expected: pytest 全部通过；不变性检查 0 failure。把两条命令的末尾输出原样保存，交付时附上。

- [ ] **Step 8: 三市场真实取数目视检查（需联网）**

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --output-dir artifacts/cn
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market HK --symbol 00700.HK --asset-type equity --account-equity 500000 --output-dir artifacts/hk
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market US --symbol NVDA.US --asset-type equity --account-equity 100000 --output-dir artifacts/us
```

逐个打开 `*_report.md`，确认第 2 节的大盘环境读起来与基准指数的实际走势一致（可对同一基准单独跑一次看它的结构图）。取数失败时如实记录退出码，不重试到用别的来源补数。

- [ ] **Step 9: 提交**

```bash
git add -A
git commit -m "docs: 大盘结构与周期层的公式、字段字典、解读要点与示例同步"
```

推送与开 PR 之前先向用户确认。
