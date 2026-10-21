"""开发区回测 v2：预筛必须是线上触发条件的超集、留出集 H1 不被评估、日期分块 bootstrap 口径（真实缓存行情）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

import run_dev_region_v2 as v2  # noqa: E402
from aggregate import date_block_bootstrap  # noqa: E402
from common import PIT_UNIVERSE  # noqa: E402

pytestmark = pytest.mark.skipif(not PIT_UNIVERSE.exists(), reason="需要 artifacts/long_backtest 真实行情缓存")


def _near_miss_days(symbol: str, data: dict, limit: int):
    """被预筛刷掉、但离触发条件不远的个股-日（阳线且偏离或 RSI 接近阈值），用来检验预筛没有漏掉线上信号。"""
    market = v2._C["market"]
    out = []
    for day, t, _ in v2._eligible_days(symbol, data):
        c, o = data["c"][t], data["o"][t]
        atr = float(data["a14"][t] or 0.0)
        if atr <= 0 or c <= o:
            continue
        ext = (c - float(data["e20"][t] or c)) / atr
        rsi_v = float(data["r14"][t] or 50.0)
        if not (ext <= -0.6 or rsi_v <= 34.0):
            continue
        if v2.prefilter(market, c, o, data["h"][t], data["l"][t], data["raw"][t], atr,
                        float(data["e20"][t] or c), rsi_v, v2._C["bf"][day]):
            continue
        out.append((day, t))
        if len(out) >= limit:
            break
    return out


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_prefilter_never_drops_a_live_trigger(market: str) -> None:
    v2._init(market)
    symbols = sorted(v2._C["periods"])
    checked = 0
    for symbol in symbols[:: max(1, len(symbols) // 12)][:12]:
        data = v2._load_symbol(symbol)
        if data is None:
            continue
        for day, t in _near_miss_days(symbol, data, limit=15):
            live = v2.live_high_conviction(symbol, data, t, day)
            assert live["subtype"] is None and live["suppressed_subtype"] is None, (symbol, day, live["subtype"])
            checked += 1
    assert checked >= 30, f"近边界样本太少（{checked}），检验没有说服力"


def test_pipeline_signals_come_from_live_plan() -> None:
    """管线记录的子类型与价位必须与线上流水线逐项相同（它就是调用线上流水线得到的，这里防止被改回复制实现）。"""
    v2._init("US")
    symbols = sorted(v2._C["periods"])
    found = 0
    for symbol in symbols[:: max(1, len(symbols) // 40)]:
        rows, _ = v2._scan_signals(symbol)
        if not rows:
            continue
        data = v2._load_symbol(symbol)
        for r in rows[:2]:
            t = data["idx"][r["day"]]
            live = v2.live_high_conviction(symbol, data, t, r["day"])
            assert (live["subtype"] or live["suppressed_subtype"]) == r["subtype"]
            assert r["vetoed"] == (live["suppressed_subtype"] is not None)
            assert [live["entry_zone"], live["take_profit_1"], live["stop_loss"]] == [r["entry_zone"], r["tp"], r["sl"]]
            # 固定价位：成交价不超过入场区上沿，止损在成交价下方
            if r["fill_status"] == "FILL":
                assert r["fill_price"] <= r["entry_zone"][1] and r["sl"] < r["fill_price"] < r["tp"]
            found += 1
        if found >= 6:
            break
    assert found >= 3


def test_dev_region_never_reaches_h1_holdout() -> None:
    assert v2.DEV_REGION[0] > v2.H1_LAST_DAY
    assert all(lo > v2.H1_LAST_DAY for lo, _ in v2.SEGMENTS.values())
    v2._init("CN")
    symbol = sorted(v2._C["periods"])[0]
    data = v2._load_symbol(symbol)
    if data is None:
        pytest.skip("样本标的缓存缺失")
    assert all(day > v2.H1_LAST_DAY for day, _, _ in v2._eligible_days(symbol, data))


def test_date_block_bootstrap_resamples_whole_days() -> None:
    # 两个交易日：一天全赢 100 笔，一天全输 1 笔。按笔数的 Wilson 区间会很窄，按日期分块的区间必须覆盖很宽的范围
    rows = [{"day": "2024-01-02", "v": 1.0}] * 100 + [{"day": "2024-01-03", "v": 0.0}] + [{"day": "2024-01-04", "v": 1.0}]
    ci = date_block_bootstrap(rows, lambda r: r["v"])
    assert ci is not None and ci[0] < 0.97 and ci[1] == pytest.approx(1.0)
    # 差值：信号组与基准组完全相同时区间覆盖 0
    diff = date_block_bootstrap(rows, lambda r: r["v"], rows)
    assert diff == [0.0, 0.0]
    assert date_block_bootstrap(rows[:2], lambda r: r["v"]) is None


def _fake_seg(p1: bool, p3: bool, low: float | None) -> dict:
    return {"all": {
        "criteria_diagnostic": {"P1_mean_net_positive": p1, "P3_sample": p3},
        "excess_vs_same_day_random": {"mean_pct_date_cluster95": None if low is None else [low, low + 1.0]},
    }}


def test_select_config_follows_predeclared_rule() -> None:
    """选择规则：CN、US 都满足 P1 与 P3；得分取两市场 P2b 下限的较小值；与最优差距 <= 0.10pp 时保留默认。"""
    default = f"{v2.geo_key(*v2.DEFAULT_GEOMETRY)}|ALL"
    other = "tp1.25_sl2.20|ALL"
    failing = "tp1.75_sl1.50|ALL"
    grid = {
        "CN": {default: _fake_seg(True, True, 0.40), other: _fake_seg(True, True, 0.48), failing: _fake_seg(False, True, 2.0)},
        "US": {default: _fake_seg(True, True, 0.35), other: _fake_seg(True, True, 0.44), failing: _fake_seg(True, True, 2.0)},
    }
    sel = v2.select_config(grid)
    assert sel["scores_pct"][failing] is None  # CN 未通过 P1，不参与
    assert sel["best"] == other and sel["selected"] == default and sel["kept_default_by_parsimony"] is True
    grid["CN"][other] = _fake_seg(True, True, 0.60)
    grid["US"][other] = _fake_seg(True, True, 0.55)
    sel = v2.select_config(grid)
    assert sel["selected"] == other and sel["kept_default_by_parsimony"] is False


def test_live_cache_hits_only_when_key_matches(tmp_path, monkeypatch) -> None:
    """线上流水线结果缓存：同样的代码与参数第二次命中，结果相同；参数一变就失效重算。"""
    monkeypatch.setattr(v2, "LIVE_CACHE_DIR", tmp_path)
    v2._init("US")
    symbols = sorted(v2._C["periods"])[::400][:3]
    live1, n1, st1 = v2._load_or_build_live("US", symbols, workers=1, refresh=False)
    live2, n2, st2 = v2._load_or_build_live("US", symbols, workers=1, refresh=False)
    assert st1["hit"] is False and st2["hit"] is True
    assert (live1, n1) == (live2, n2)
    monkeypatch.setattr(v2, "HORIZON", v2.HORIZON + 1)
    _, _, st3 = v2._load_or_build_live("US", symbols, workers=1, refresh=False)
    assert st3["hit"] is False
