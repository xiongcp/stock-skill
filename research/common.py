"""Shared loaders for the research backtest harness (real cached data only)."""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = ROOT / "kline-structure-chart" / "scripts"
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))

ART = ROOT / "artifacts" / "long_backtest"
CACHE = ART / "cache"
UNIVERSE = ART / "universe.csv"
PIT_UNIVERSE = ART / "pit_universe.json"

BENCHMARKS = {
    "CN": {"symbol": "000300.SH", "name": "沪深300", "asset_type": "index"},
    "HK": {"symbol": "HSI.HK", "name": "恒生指数", "asset_type": "index"},
    "US": {"symbol": "SPY.US", "name": "标普500 ETF (SPY)", "asset_type": "etf"},
}
VIX_SYMBOL = "^VIX"
LONG_ACTIONS = ("LONG_CONFIRMED", "WATCH_LONG_SETUP")


def cache_path(market: str, symbol: str) -> Path:
    return CACHE / market / f"{symbol.replace('^', '_')}.json.gz"


def load_cache(market: str, symbol: str) -> dict:
    with gzip.open(cache_path(market, symbol), "rt", encoding="utf-8") as fh:
        return json.load(fh)


def clean_bars(payload: dict) -> list[dict]:
    """与线上 data_gateway 同口径：Yahoo 日线剔除占位K线（成交量为 0 且四价相同）；VIX 线上不过滤，这里同样保留。"""
    from data_gateway import drop_placeholder_bars

    if str(payload.get("source") or "").startswith("YAHOO_") and payload.get("symbol") != VIX_SYMBOL:
        return drop_placeholder_bars(payload["bars"])[0]
    return payload["bars"]


def load_bars(market: str, symbol: str) -> list[dict]:
    return clean_bars(load_cache(market, symbol))


def build_actions(payload: dict) -> list[dict]:
    from data_gateway import build_corporate_actions

    return build_corporate_actions(
        payload["bars"],
        payload.get("events") or [],
        include_splits=str(payload.get("source") or "").startswith("SINA_"),
    )


def adjusted_bars(payload: dict, mode: str = "total_return") -> tuple[list[dict], list[dict]]:
    """Full-history forward-adjusted series as of the cache's last day.

    Per-cut PIT rescaling is done later via market_rules.rescale_bars_as_of.
    """
    from market_rules import apply_point_in_time_adjustment

    raw = clean_bars(payload)
    actions = build_actions({**payload, "bars": raw})
    bars = apply_point_in_time_adjustment(
        raw,
        corporate_actions=actions,
        mode=mode,
        as_of_day=raw[-1]["day"],
    )
    return actions, bars


def day_index(bars: list[dict]) -> dict[str, int]:
    return {b["day"]: i for i, b in enumerate(bars)}