#!/usr/bin/env python3
"""录制真实行情 fixture（需要联网，手动执行；测试本身离线回放这些文件）。

用法：
  uv run python tests/record_fixtures.py            # 写入 tests/fixtures/recorded/

录制内容：新浪不复权日线（5 个 A 股指数 + 贵州茅台）、Yahoo 日线（NVDA/SPY/腾讯/恒指）与除权除息事件。
只保存已收盘K线（按交易所时区与收盘时间判定），并在 MANIFEST.json 记录录制时间、来源与 sha256。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from data_gateway import (  # noqa: E402
    fetch_sina_daily_bars,
    fetch_tencent_index_daily_bars,
    fetch_yahoo_corporate_actions,
    fetch_yahoo_daily_bars,
)
from market_rules import is_daily_bar_complete  # noqa: E402

OUT = Path(__file__).resolve().parent / "fixtures" / "recorded"
SINA = {
    "sh000001": "CN",
    "sz399001": "CN",
    "sz399006": "CN",
    "sh000688": "CN",
    "sh000300": "CN",
    "sh600519": "CN",
    "sh601318": "CN",
    "sz000002": "CN",
    "sz002594": "CN",
    "sh510300": "CN",
}
YAHOO = {"NVDA": "US", "SPY": "US", "^VIX": "US", "0700.HK": "HK", "1810.HK": "HK", "^HSI": "HK"}
TENCENT = {"hkHSTECH": "HK"}
EVENTS = ("600519.SS", "601318.SS", "000002.SZ", "002594.SZ", "510300.SS", "NVDA", "SPY", "0700.HK", "1810.HK")


def _completed(bars: list[dict], market: str, now: datetime) -> list[dict]:
    return [b for b in bars if is_daily_bar_complete(b["day"], market, now)]


def _write(rel: str, payload: object, manifest: dict, source: str) -> None:
    path = OUT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    rows = payload if isinstance(payload, list) else []
    manifest["files"][rel] = {
        "source": source,
        "rows": len(rows),
        "first_day": rows[0].get("day") or rows[0].get("ex_date") if rows else None,
        "last_day": rows[-1].get("day") or rows[-1].get("ex_date") if rows else None,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    manifest: dict = {"recorded_at_utc": now.isoformat(timespec="seconds"), "files": {}}
    for code, market in SINA.items():
        bars = _completed(fetch_sina_daily_bars(code, datalen=1023), market, now)
        _write(f"sina_daily/{code}.json", bars, manifest, f"SINA getKLineData symbol={code} scale=240 (不复权)")
    for sym, market in YAHOO.items():
        bars = _completed(fetch_yahoo_daily_bars(sym, range_str="5y"), market, now)
        _write(f"yahoo_daily/{sym.replace('^', '')}.json", bars, manifest, f"YAHOO chart {sym} 1d range=5y")
    for code, market in TENCENT.items():
        bars = _completed(fetch_tencent_index_daily_bars(code, datalen=1000), market, now)
        _write(f"tencent_daily/{code}.json", bars, manifest, f"TENCENT fqkline {code} day qfq")
    for sym in EVENTS:
        _write(f"yahoo_events/{sym}.json", fetch_yahoo_corporate_actions(sym), manifest, f"YAHOO chart {sym} events=div,splits")
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: (v["rows"], v["last_day"]) for k, v in manifest["files"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
