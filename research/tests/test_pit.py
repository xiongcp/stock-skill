"""PIT / no-lookahead regression tests for the research harness (real cached data)."""
from __future__ import annotations

import copy
import sys
from bisect import bisect_right
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))

from common import adjusted_bars, build_actions, clean_bars, load_cache  # noqa: E402
from run_backtest import make_pit_slice  # noqa: E402
from market_rules import apply_point_in_time_adjustment  # noqa: E402


def _symbols():
    return [("CN", "601288.SH"), ("HK", "00700.HK"), ("US", "AAPL.US")]


def _cache(symbol: str, market: str) -> dict:
    try:
        return load_cache(market, symbol)
    except FileNotFoundError:
        import json

        rec = ROOT / "kline-structure-chart" / "tests" / "fixtures" / "recorded"
        if market == "CN":
            bars = json.loads((rec / "sina_daily" / "sh600519.json").read_text(encoding="utf-8"))
            events = json.loads((rec / "yahoo_events" / "600519.SS.json").read_text(encoding="utf-8"))
            return {"symbol": "600519.SH", "market": "CN", "source": "SINA_LIVE_DAILY", "bars": bars, "events": events}
        if market == "HK":
            bars = json.loads((rec / "yahoo_daily" / "0700.HK.json").read_text(encoding="utf-8"))
            events = json.loads((rec / "yahoo_events" / "0700.HK.json").read_text(encoding="utf-8"))
            return {"symbol": "00700.HK", "market": "HK", "source": "YAHOO_DAILY(0700.HK)", "bars": bars, "events": events}
        bars = json.loads((rec / "yahoo_daily" / "NVDA.json").read_text(encoding="utf-8"))
        events = json.loads((rec / "yahoo_events" / "NVDA.json").read_text(encoding="utf-8"))
        return {"symbol": "NVDA.US", "market": "US", "source": "YAHOO_DAILY(NVDA)", "bars": bars, "events": events}


@pytest.mark.parametrize("market,symbol", _symbols())
@pytest.mark.parametrize("as_of", ["2023-06-30", "2024-12-31", "2025-06-30"])
def test_pit_slice_matches_direct_adjustment(market: str, symbol: str, as_of: str) -> None:
    payload = _cache(symbol, market)
    actions, adj = adjusted_bars(payload, mode="total_return")
    days = [b["day"] for b in adj]
    t = bisect_right(days, as_of) - 1
    if t < 500:
        pytest.skip(f"{symbol} lacks history before {as_of}")
    pit, cut = make_pit_slice(adj, t, 15)
    # Direct reference: apply only events with ex_date <= as_of to raw bars.
    ref = apply_point_in_time_adjustment(
        clean_bars(payload)[: t + 1], corporate_actions=actions, mode="total_return", as_of_day=days[t]
    )
    n = 60
    mine = pit[cut - n + 1: cut + 1]
    theirs = ref[-n:]
    assert [b["day"] for b in mine] == [b["day"] for b in theirs]
    for a, b in zip(mine, theirs):
        for k in ("open", "high", "low", "close"):
            assert abs(float(a[k]) - float(b[k])) < 1e-3, (symbol, as_of, a["day"], k, a[k], b[k])


@pytest.mark.parametrize("market,symbol", _symbols())
def test_pit_slice_ignores_all_future_bars(market: str, symbol: str) -> None:
    payload = _cache(symbol, market)
    _, adj = adjusted_bars(payload, mode="total_return")
    days = [b["day"] for b in adj]
    t = bisect_right(days, "2024-06-28") - 1
    if t < 500:
        pytest.skip(f"{symbol} lacks history")
    base, cut = make_pit_slice(adj, t, 15)
    base_hist = [{k: b[k] for k in ("day", "open", "high", "low", "close", "volume")} for b in base[: cut + 1]]

    tampered = copy.deepcopy(adj)
    for b in tampered[t + 1:]:
        for k in ("open", "high", "low", "close"):
            b[k] = round(float(b[k]) * 3.0 + 17.0, 4)
        b["raw_close"] = float(b["raw_close"]) * 3.0 + 17.0
        b["volume"] = float(b.get("volume") or 0.0) * 5.0
    new, cut2 = make_pit_slice(tampered, t, 15)
    assert cut2 == cut
    new_hist = [{k: b[k] for k in ("day", "open", "high", "low", "close", "volume")} for b in new[: cut2 + 1]]
    assert new_hist == base_hist


def test_actions_are_cut_at_as_of() -> None:
    payload = _cache("601288.SH", "CN")
    actions = build_actions(payload)
    assert actions
    cut = "2024-12-31"
    ref = apply_point_in_time_adjustment(payload["bars"], corporate_actions=actions, mode="total_return", as_of_day=cut)
    # Every bar at or before the cutoff must only use events <= cutoff; compare with
    # a version where all later events are removed before adjustment.
    truncated = [a for a in actions if a["ex_date"] <= cut]
    ref2 = apply_point_in_time_adjustment(payload["bars"], corporate_actions=truncated, mode="total_return", as_of_day=cut)
    for a, b in zip(ref, ref2):
        if a["day"] > cut:
            continue
        assert a["close"] == pytest.approx(b["close"], abs=1e-6)