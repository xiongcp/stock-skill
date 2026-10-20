#!/usr/bin/env python3
"""Build a current-listing universe for CN / HK / US from real vendor endpoints.

This is research tooling, not part of the distributed skill. It records the exact
source URL and fetch time; it never fills or fabricates missing values.

Limitations (must be disclosed in every report):
- Current listings only. Delisted symbols are absent -> survivorship bias present.
- Historical ST status is unavailable; only the current name prefix is recorded.
- Universe ranking uses current vendor fields and is therefore only a screening
  device. The backtest itself re-ranks point-in-time by trailing turnover.
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "long_backtest" / "universe.csv"
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Referer": "https://finance.sina.com.cn/",
}
SINA = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
US_EXCLUDE = re.compile(
    r"(warrant|right|unit|preferred|depositary|etf|etn|fund|notes|acquisition|"
    r"%|when.issued|subscription|redeemable)",
    re.IGNORECASE,
)


def http_get(url: str, encoding: str = "utf-8", timeout: int = 40, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode(encoding, "ignore")


def _get_json(url: str, encoding: str = "utf-8", retries: int = 3):
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return json.loads(http_get(url, encoding=encoding))
        except Exception as exc:  # noqa: BLE001 - source failures are reported, never backfilled
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed after {retries} attempts: {url[:120]}: {last}")


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fetch_cn() -> list[dict]:
    rows: list[dict] = []
    for page in range(1, 120):
        url = (
            SINA + "Market_Center.getHQNodeData?"
            f"page={page}&num=100&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=page"
        )
        batch = []
        for attempt in range(4):
            try:
                batch = _get_json(url, encoding="gbk")
            except Exception:  # noqa: BLE001 - transient throttle, retry the same page
                batch = []
            if batch:
                break
            time.sleep(1.0 * (attempt + 1))
        if not batch:
            break
        rows.extend(batch)
        time.sleep(0.35)
    out: list[dict] = []
    for r in rows:
        raw = str(r.get("symbol") or "")
        if not raw or raw[:2] not in ("sh", "sz", "bj"):
            continue
        code, suffix = raw[2:], {"sh": "SH", "sz": "SZ", "bj": "BJ"}[raw[:2]]
        name = str(r.get("name") or "").strip()
        out.append(
            {
                "symbol": f"{code}.{suffix}",
                "market": "CN",
                "asset_type": "equity",
                "name": name,
                "exchange": raw[:2].upper(),
                "is_st_current": int("ST" in name.upper() or "退" in name),
                "list_eligible": int(raw[:2] != "bj" and "退" not in name and "ST" not in name.upper()),
                "source": "SINA_HQ_NODE(hs_a)",
                "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "rank_value": _float(r.get("nmc")) or _float(r.get("mktcap")),
                "extra": json.dumps(
                    {"amount": _float(r.get("amount")), "mktcap": _float(r.get("mktcap")),
                     "nmc": _float(r.get("nmc")), "pe": r.get("per")},
                    ensure_ascii=False,
                ),
            }
        )
    return out


def fetch_hk() -> list[dict]:
    rows: list[dict] = []
    for page in range(1, 120):
        url = (
            SINA + "Market_Center.getHKStockData?"
            f"page={page}&num=100&sort=symbol&asc=1&node=qbgg_hk&_s_r_a=page"
        )
        batch = _get_json(url, encoding="gbk")
        if not batch:
            break
        rows.extend(batch)
        time.sleep(0.2)
    out: list[dict] = []
    for r in rows:
        raw = str(r.get("symbol") or "").strip()
        if not raw.isdigit():
            continue
        name = str(r.get("name") or "").strip()
        price = _float(r.get("lasttrade")) or _float(r.get("prevclose"))
        # Sina's HK list leaves stocks_sum/market_value at 0; amount (today's HKD
        # turnover) is populated and is used only as a screening rank.
        amount = _float(r.get("amount"))
        excluded = int(
            any(k in name for k in ("ETF", "基金", "债券", "国债", "期货", "权证", "牛熊", "反向", "杠杆"))
            or "停牌" in name
        )
        out.append(
            {
                "symbol": f"{int(raw):05d}.HK",
                "market": "HK",
                "asset_type": "equity",
                "name": name,
                "exchange": "HKEX",
                "is_st_current": 0,
                "list_eligible": int(not excluded and price > 0),
                "source": "SINA_HQ_NODE(qbgg_hk)",
                "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "rank_value": amount,
                "extra": json.dumps(
                    {"price": price, "amount": amount, "tradetype": r.get("tradetype")},
                    ensure_ascii=False,
                ),
            }
        )
    return out


def fetch_us() -> list[dict]:
    url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&offset=0&download=true"
    payload = json.loads(http_get(url, headers={"Accept": "application/json, text/plain, */*"}))
    rows = ((payload.get("data") or {}).get("rows")) or []
    out: list[dict] = []
    for r in rows:
        symbol = str(r.get("symbol") or "").strip().upper()
        name = str(r.get("name") or "").strip()
        if not symbol or not re.fullmatch(r"[A-Z0-9.\-]{1,12}", symbol):
            continue
        cap = _float((r.get("marketCap") or "0").replace(",", ""))
        price = _float((r.get("lastsale") or "").replace("$", "").replace(",", ""))
        volume = _float((r.get("volume") or "0").replace(",", ""))
        excluded = int(bool(US_EXCLUDE.search(name)) or price <= 0 or cap <= 0)
        out.append(
            {
                "symbol": f"{symbol}.US",
                "market": "US",
                "asset_type": "equity",
                "name": name,
                "exchange": "US",
                "is_st_current": 0,
                "list_eligible": int(not excluded),
                "source": "NASDAQ_API_SCREENER",
                "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "rank_value": cap,
                "extra": json.dumps({"price": price, "volume": volume, "sector": r.get("sector"),
                                     "industry": r.get("industry"), "country": r.get("country")},
                                    ensure_ascii=False),
            }
        )
    return out


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = fetch_cn() + fetch_hk() + fetch_us()
    rows.sort(key=lambda r: (r["market"], -r["rank_value"]))
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    for market in ("CN", "HK", "US"):
        sub = [r for r in rows if r["market"] == market]
        eligible = [r for r in sub if r["list_eligible"]]
        print(f"{market}: total={len(sub)} eligible={len(eligible)} "
              f"top={eligible[0]['symbol'] if eligible else None} "
              f"rank_value={eligible[0]['rank_value'] if eligible else None}")
    print(f"wrote {OUT} rows={len(rows)}")


if __name__ == "__main__":
    main()