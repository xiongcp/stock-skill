from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

# 各市场交易所休市日（仅工作日，2021-2027）。来源：CN 国务院办公厅放假通知/上交所休市安排；HK 政府宪报公众假期；
# US NYSE 假期日历（含 2025-01-09 卡特国葬日休市）。新一年安排公布后须补录并同步 CALENDAR_COVERAGE_END。
MARKET_HOLIDAYS: dict[str, set[str]] = {
    "CN": {
        "2021-01-01", "2021-02-11", "2021-02-12", "2021-02-15", "2021-02-16", "2021-02-17",
        "2021-04-05", "2021-05-03", "2021-05-04", "2021-05-05", "2021-06-14",
        "2021-09-20", "2021-09-21", "2021-10-01", "2021-10-04", "2021-10-05", "2021-10-06", "2021-10-07",
        "2022-01-03", "2022-01-31", "2022-02-01", "2022-02-02", "2022-02-03", "2022-02-04",
        "2022-04-04", "2022-04-05", "2022-05-02", "2022-05-03", "2022-05-04", "2022-06-03",
        "2022-09-12", "2022-10-03", "2022-10-04", "2022-10-05", "2022-10-06", "2022-10-07",
        "2023-01-02", "2023-01-23", "2023-01-24", "2023-01-25", "2023-01-26", "2023-01-27",
        "2023-04-05", "2023-05-01", "2023-05-02", "2023-05-03", "2023-06-22", "2023-06-23",
        "2023-09-29", "2023-10-02", "2023-10-03", "2023-10-04", "2023-10-05", "2023-10-06",
        "2024-01-01", "2024-02-09", "2024-02-12", "2024-02-13", "2024-02-14", "2024-02-15", "2024-02-16",
        "2024-04-04", "2024-04-05", "2024-05-01", "2024-05-02", "2024-05-03", "2024-06-10",
        "2024-09-16", "2024-09-17", "2024-10-01", "2024-10-02", "2024-10-03", "2024-10-04", "2024-10-07",
        "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04",
        "2025-04-04", "2025-05-01", "2025-05-02", "2025-05-05", "2025-06-02", "2025-10-01", "2025-10-02", "2025-10-03",
        "2025-10-06", "2025-10-07", "2025-10-08",
        "2026-01-01", "2026-01-02", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
        "2026-04-06", "2026-05-01", "2026-05-04", "2026-05-05", "2026-06-19", "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
        "2027-01-01",
    },
    "HK": {
        "2021-01-01", "2021-02-12", "2021-02-15", "2021-04-02", "2021-04-05", "2021-04-06",
        "2021-05-19", "2021-06-14", "2021-07-01", "2021-09-22", "2021-10-01", "2021-10-14", "2021-12-27",
        "2022-01-03", "2022-02-01", "2022-02-02", "2022-02-03", "2022-04-05", "2022-04-15", "2022-04-18",
        "2022-05-02", "2022-05-09", "2022-06-03", "2022-07-01", "2022-09-12", "2022-10-04", "2022-12-26", "2022-12-27",
        "2023-01-02", "2023-01-23", "2023-01-24", "2023-01-25", "2023-04-05", "2023-04-07", "2023-04-10",
        "2023-05-01", "2023-05-26", "2023-06-22", "2023-10-02", "2023-10-23", "2023-12-25", "2023-12-26",
        "2024-01-01", "2024-02-12", "2024-02-13", "2024-03-29", "2024-04-01", "2024-04-04", "2024-05-01",
        "2024-05-15", "2024-06-10", "2024-07-01", "2024-09-18", "2024-10-01", "2024-10-11", "2024-12-25", "2024-12-26",
        "2025-01-01", "2025-01-29", "2025-01-30", "2025-01-31", "2025-04-04", "2025-04-18", "2025-04-21",
        "2025-05-01", "2025-05-05", "2025-07-01", "2025-10-01", "2025-10-07", "2025-10-29", "2025-12-25", "2025-12-26",
        "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-04-03", "2026-04-06", "2026-04-07",
        "2026-05-01", "2026-05-25", "2026-06-19", "2026-07-01", "2026-09-26", "2026-10-01", "2026-10-19",
        "2026-12-25",
        "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-26", "2027-03-29", "2027-04-05", "2027-05-13",
        "2027-06-09", "2027-07-01", "2027-09-16", "2027-10-01", "2027-10-08", "2027-12-27",
    },
    "US": {
        "2021-01-01", "2021-01-18", "2021-02-15", "2021-04-02", "2021-05-31", "2021-07-05",
        "2021-09-06", "2021-11-25", "2021-12-24",
        "2022-01-17", "2022-02-21", "2022-04-15", "2022-05-30", "2022-06-20", "2022-07-04",
        "2022-09-05", "2022-11-24", "2022-12-26",
        "2023-01-02", "2023-01-16", "2023-02-20", "2023-04-07", "2023-05-29", "2023-06-19", "2023-07-04",
        "2023-09-04", "2023-11-23", "2023-12-25",
        "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04",
        "2024-09-02", "2024-11-28", "2024-12-25",
        "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
        "2025-09-01", "2025-11-27", "2025-12-25",
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
        "2026-09-07", "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
        "2027-09-06", "2027-11-25", "2027-12-24",
    },
}

# 休市表已核实覆盖的起止范围（CN 2027 年放假安排截至 2026-10 尚未发布）
CALENDAR_COVERAGE_START = "2021-01-01"
CALENDAR_COVERAGE_END: dict[str, str] = {"CN": "2026-12-31", "HK": "2027-12-31", "US": "2027-12-31"}


def calendar_covers(day_str: str, market: str) -> bool:
    """该日期是否落在已核实的休市表覆盖范围内；超出范围时只能按"周一至周五"估算交易日。"""
    d = day_str[:10]
    return CALENDAR_COVERAGE_START <= d <= CALENDAR_COVERAGE_END.get(market, "0000-00-00")


DEFAULT_BENCHMARKS: dict[str, dict[str, str]] = {
    "CN": {"symbol": "000300.SH", "sina_symbol": "sh000300", "name": "沪深300"},
    "HK": {"symbol": "HSI.HK", "sina_symbol": "hkHSI", "name": "恒生指数"},
    "US": {"symbol": "SPY.US", "sina_symbol": "gb_spy", "name": "标普500 ETF(SPY)"},
}

KNOWN_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "000001.SH": {"market": "CN", "asset_type": "index", "name": "上证指数", "sina": "sh000001", "currency": "CNY"},
    "399001.SZ": {"market": "CN", "asset_type": "index", "name": "深证成指", "sina": "sz399001", "currency": "CNY"},
    "399006.SZ": {"market": "CN", "asset_type": "index", "name": "创业板指", "sina": "sz399006", "currency": "CNY"},
    "000688.SH": {"market": "CN", "asset_type": "index", "name": "科创50", "sina": "sh000688", "currency": "CNY"},
    "000300.SH": {"market": "CN", "asset_type": "index", "name": "沪深300", "sina": "sh000300", "currency": "CNY"},
    "000016.SH": {"market": "CN", "asset_type": "index", "name": "上证50", "sina": "sh000016", "currency": "CNY"},
    "000905.SH": {"market": "CN", "asset_type": "index", "name": "中证500", "sina": "sh000905", "currency": "CNY"},
    "000852.SH": {"market": "CN", "asset_type": "index", "name": "中证1000", "sina": "sh000852", "currency": "CNY"},
    "600519.SH": {"market": "CN", "asset_type": "equity", "name": "贵州茅台", "sina": "sh600519", "currency": "CNY", "board": "MAIN"},
    "300750.SZ": {"market": "CN", "asset_type": "equity", "name": "宁德时代", "sina": "sz300750", "currency": "CNY", "board": "CHINEXT"},
    "688981.SH": {"market": "CN", "asset_type": "equity", "name": "中芯国际", "sina": "sh688981", "currency": "CNY", "board": "STAR"},
    "000001.SZ": {"market": "CN", "asset_type": "equity", "name": "平安银行", "sina": "sz000001", "currency": "CNY", "board": "MAIN"},
    "HSI.HK": {"market": "HK", "asset_type": "index", "name": "恒生指数", "sina": "hkHSI", "currency": "HKD"},
    "HSTECH.HK": {"market": "HK", "asset_type": "index", "name": "恒生科技指数", "sina": "hkHSTECH", "currency": "HKD"},
    "00700.HK": {"market": "HK", "asset_type": "equity", "name": "腾讯控股", "sina": "hk00700", "currency": "HKD", "lot_size": 100},
    "09988.HK": {"market": "HK", "asset_type": "equity", "name": "阿里巴巴-W", "sina": "hk09988", "currency": "HKD", "lot_size": 100},
    "SPY.US": {"market": "US", "asset_type": "etf", "name": "标普500 ETF (SPY)", "sina": "gb_spy", "currency": "USD"},
    "QQQ.US": {"market": "US", "asset_type": "etf", "name": "纳斯达克100 ETF (QQQ)", "sina": "gb_qqq", "currency": "USD"},
    "NVDA.US": {"market": "US", "asset_type": "equity", "name": "英伟达 (NVDA)", "sina": "gb_nvda", "currency": "USD", "lot_size": 1},
    "AAPL.US": {"market": "US", "asset_type": "equity", "name": "苹果 (AAPL)", "sina": "gb_aapl", "currency": "USD", "lot_size": 1},
}


# Yahoo 代码与本地代码不一致的标的（指数/ETF 代理）
YAHOO_ALIASES: dict[str, str] = {
    "HSI.HK": "^HSI",
}

# Yahoo 没有历史日线的指数改用腾讯指数日线（不再用 3033.HK ETF 冒充恒生科技指数）
TENCENT_INDEX_SOURCES: dict[str, str] = {
    "HSTECH.HK": "hkHSTECH",
}

# 各市场交易所时区与日线收盘时间（港股含收市竞价）
MARKET_SESSIONS: dict[str, tuple[str, time]] = {
    "CN": ("Asia/Shanghai", time(15, 0)),
    "HK": ("Asia/Hong_Kong", time(16, 10)),
    "US": ("America/New_York", time(16, 0)),
}

# 港股半日市（除夕、圣诞前夕、新年前夕，12:08-12:10 随机收市竞价）与美股提前收市日（13:00 收市）
HK_HALF_DAY_SESSIONS: frozenset[str] = frozenset(
    {
        "2025-01-28",
        "2025-12-24",
        "2025-12-31",
        "2026-02-16",
        "2026-12-24",
        "2026-12-31",
    }
)
US_EARLY_CLOSE_SESSIONS: frozenset[str] = frozenset(
    {
        "2025-07-03",
        "2025-11-28",
        "2025-12-24",
        "2026-11-27",
        "2026-12-24",
    }
)


def session_close_time(market: str, day_str: str) -> time:
    """返回指定交易日的收盘时间（支持港股半日市 12:10 与美股提前收市日 13:00）。"""
    day = day_str[:10]
    if market == "HK" and (day in HK_HALF_DAY_SESSIONS or day[5:] in ("12-24", "12-31")):
        return time(12, 10)
    if market == "US" and day in US_EARLY_CLOSE_SESSIONS:
        return time(13, 0)
    return MARKET_SESSIONS[market][1]


def sina_symbol_for(symbol: str) -> str:
    """由交易所后缀推导新浪代码：600519.SH -> sh600519，000001.SZ -> sz000001，830799.BJ -> bj830799。"""
    code, _, suffix = symbol.strip().upper().partition(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suffix)
    if not prefix or not code.isdigit():
        raise ValueError(f"Cannot derive Sina symbol for {symbol!r}")
    return f"{prefix}{code}"


# 美股代码白名单：1~6 位字母数字，可带一个份额类别后缀（BRK.B / BF-B）。代码会拼进请求 URL，必须先校验
US_TICKER_PATTERN = re.compile(r"[A-Z0-9]{1,6}(?:[.-][A-Z0-9]{1,3})?")


def yahoo_symbol_for(symbol: str) -> str:
    """由本地代码推导 Yahoo 代码：01810.HK -> 1810.HK，00700.HK -> 0700.HK，NVDA.US -> NVDA，BRK.B.US -> BRK-B。"""
    upper = symbol.strip().upper()
    if upper in YAHOO_ALIASES:
        return YAHOO_ALIASES[upper]
    code, _, suffix = upper.rpartition(".")
    if suffix == "HK":
        if not code.isdigit():
            raise ValueError(f"Cannot derive Yahoo symbol for {symbol!r}")
        return f"{int(code):04d}.HK"
    if suffix == "US" and US_TICKER_PATTERN.fullmatch(code):
        return code.replace(".", "-")
    raise ValueError(f"Cannot derive Yahoo symbol for {symbol!r}")


def tencent_quote_code_for(symbol: str, market: str) -> str:
    """腾讯行情快照代码：600519.SH -> sh600519，01810.HK -> hk01810，HSTECH.HK -> hkHSTECH，BRK.B.US -> usBRK.B。"""
    upper = symbol.strip().upper()
    if market == "CN":
        return sina_symbol_for(upper)
    code, _, suffix = upper.rpartition(".")
    if market == "HK" and suffix == "HK":
        if code.isdigit():
            return f"hk{int(code):05d}"
        if code in ("HSI", "HSTECH"):
            return f"hk{code}"
    if market == "US" and suffix == "US" and US_TICKER_PATTERN.fullmatch(code):
        return f"us{code}"
    raise ValueError(f"Cannot derive Tencent quote code for {symbol!r}")


def yahoo_events_symbol_for(symbol: str) -> str:
    """除权除息事件查询用的 Yahoo 代码：A股 .SH -> .SS、.SZ 不变；港美股同行情代码。北交所 Yahoo 无数据。"""
    upper = symbol.strip().upper()
    code, _, suffix = upper.partition(".")
    if suffix == "SH":
        return f"{code}.SS"
    if suffix == "SZ":
        return f"{code}.SZ"
    if suffix == "BJ":
        raise ValueError(f"Yahoo has no corporate-action data for {symbol!r}")
    return yahoo_symbol_for(upper)


def is_daily_bar_complete(day_str: str, market: str, now: datetime | None = None) -> bool:
    """按交易所时区判断某日日线是否已收盘：早于交易所本地今日为已收盘，今日需过收盘时间。"""
    tz_name, _ = MARKET_SESSIONS[market]
    now_local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    today = now_local.date().isoformat()
    day = day_str[:10]
    if day < today:
        return True
    if day > today:
        return False
    return now_local.time() >= session_close_time(market, day)


def latest_closed_session(market: str, now: datetime | None = None) -> str:
    """交易所本地时间下最近一个已收盘的交易日（按休市表判断，超出覆盖范围时按工作日估算）。"""
    tz_name, _ = MARKET_SESSIONS[market]
    now_local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    day = now_local.date()
    if not (is_trading_day(day, market) and now_local.time() >= session_close_time(market, day.isoformat())):
        day -= timedelta(days=1)
        while not is_trading_day(day, market):
            day -= timedelta(days=1)
    return day.isoformat()


_ST_NAME_PREFIX = re.compile(r"^\*?S?\*?ST")
_DELISTING_NAME_RE = re.compile(r"(?:^|\*?S?\*?ST)退市|退市$|\(退市\)|（退市）|^.{2,}退$|DELISTED", re.IGNORECASE)

STALE_SESSIONS_WARNING_THRESHOLD = 2
STALE_SESSIONS_VETO_THRESHOLD = 5


def is_st_instrument(symbol: str = "", name: str = "", is_st: bool = False) -> bool:
    """识别 A 股风险警示股票：显式 is_st，或证券简称以 ST / *ST / SST / S*ST 开头。

    风险警示制度只存在于 A 股，港股、美股一律返回 False；只认名称前缀，避免英文名中间含 "ST" 误判。
    """
    if is_st:
        return True
    if symbol and not symbol.strip().upper().endswith((".SH", ".SZ", ".BJ")):
        return False
    return bool(_ST_NAME_PREFIX.match(re.sub(r"\s+", "", name or "").upper()))


def is_delisting_instrument(symbol: str = "", name: str = "") -> bool:
    """识别处于退市整理期或已终止上市的证券（如 '退市金钰'、'雏鹰退'、'XX(退市)'、'DELISTED'）。"""
    cleaned = re.sub(r"\s+", "", name or "")
    if not cleaned:
        return False
    return bool(_DELISTING_NAME_RE.search(cleaned))


_DELISTING_MARK_RE = re.compile(r"[\(（](?:退市|DELISTED)[\)）]|DELISTED|退市|(?<=\S\S)退$", re.IGNORECASE)


def strip_delisting_marks(name: str = "") -> str:
    """去掉简称里的退市整理期标记（'退市金钰'→'金钰'、'雏鹰退'→'雏鹰'、'ACME (DELISTED)'→'ACME'）。

    退市标记只反映取数当下的状态，回测历史截面时不能回填到过去（与 ST 状态同理）。
    """
    return re.sub(r"\s+", " ", _DELISTING_MARK_RE.sub("", name or "")).strip()


def _cn_lot_rules(asset_type: str, board: str) -> tuple[int, int, int]:
    """返回 A 股 (lot_size, min_order_shares, lot_step)。

    - 主板/创业板及 ETF：最低 100 股，按 100 股整手递增。
    - 科创板（STAR，上交所科创板交易特别规定）：单笔申报不少于 200 股，超过 200 股的部分可按 1 股递增。
    - 北交所（BSE，北交所交易规则）：单笔申报不少于 100 股，超过 100 股的部分可按 1 股递增。
    """
    if asset_type == "index":
        return 1, 1, 1
    if asset_type == "equity" and board == "STAR":
        return 200, 200, 1
    if asset_type == "equity" and board == "BSE":
        return 100, 100, 1
    return 100, 100, 100


def resolve_instrument(
    symbol: str,
    market: str | None = None,
    asset_type: str | None = None,
    name: str | None = None,
    is_st: bool = False,
) -> dict[str, Any]:
    raw = symbol.strip()
    upper = raw.upper()

    # Check ambiguous bare 6-digit code like 000001 without exchange or asset_type
    if raw == "000001" and not asset_type:
        raise ValueError("Ambiguous symbol '000001': specify '000001.SH' (上证指数) or '000001.SZ' (平安银行)")

    # Direct match
    if upper in KNOWN_INSTRUMENTS:
        info = dict(KNOWN_INSTRUMENTS[upper], symbol=upper)
        if market and info["market"] != market:
            raise ValueError(f"Symbol {upper} belongs to market {info['market']}, not {market}")
        if name:
            info["name"] = name
        info["is_st"] = is_st_instrument(upper, info.get("name", ""), is_st or bool(info.get("is_st", False)))
        if info["market"] == "CN":
            lot, min_sh, step = _cn_lot_rules(info["asset_type"], info.get("board", "MAIN"))
            info.setdefault("lot_size", lot)
            info.setdefault("min_order_shares", min_sh)
            info.setdefault("lot_step", step)
        return info

    # Sina shorthand mapping (e.g. sh000001, sz399006)
    for canonical, meta in KNOWN_INSTRUMENTS.items():
        if meta.get("sina", "").lower() == raw.lower():
            info = dict(meta, symbol=canonical)
            if name:
                info["name"] = name
            info["is_st"] = is_st_instrument(canonical, info.get("name", ""), is_st or bool(info.get("is_st", False)))
            return info

    # Infer suffix
    inferred_market = market
    if upper.endswith((".SH", ".SZ", ".BJ")):
        inferred_market = "CN"
    elif upper.endswith(".HK") or upper in ("^HSI", "HSI", "HSTECH"):
        inferred_market = "HK"
        if not upper.endswith(".HK"):
            upper = f"{upper.lstrip('^')}.HK"
            if upper in KNOWN_INSTRUMENTS:
                return dict(KNOWN_INSTRUMENTS[upper], symbol=upper)
    elif upper.endswith(".US") or (inferred_market == "US"):
        inferred_market = "US"
        if not upper.endswith(".US"):
            cand = f"{upper}.US"
            if cand in KNOWN_INSTRUMENTS:
                return dict(KNOWN_INSTRUMENTS[cand], symbol=cand)
            upper = cand

    if not inferred_market:
        raise ValueError(f"Cannot resolve market for symbol {symbol!r}; provide explicit --market CN|HK|US")

    currency = {"CN": "CNY", "HK": "HKD", "US": "USD"}[inferred_market]
    resolved_type = asset_type or _infer_asset_type(upper)
    code_num = upper.split(".")[0]
    board = "MAIN"
    if inferred_market == "CN" and resolved_type == "equity":
        if code_num.startswith(("300", "301")):
            board = "CHINEXT"
        elif code_num.startswith(("688", "689")):
            board = "STAR"
        elif upper.endswith(".BJ") or code_num.startswith(("8", "4", "92")):
            board = "BSE"
    elif inferred_market == "CN" and resolved_type == "etf":
        if code_num.startswith(("588", "589")):
            board = "STAR"
        elif is_cn_20pct_etf(upper, name or ""):
            board = "CHINEXT"

    if inferred_market == "CN":
        lot_size, min_order_shares, lot_step = _cn_lot_rules(resolved_type, board)
    elif inferred_market == "HK" and resolved_type in ("equity", "etf"):
        lot_size, min_order_shares, lot_step = 100, 100, 100
    else:
        lot_size, min_order_shares, lot_step = 1, 1, 1

    inst_name = name or upper
    return {
        "symbol": upper,
        "market": inferred_market,
        "asset_type": resolved_type,
        "name": inst_name,
        "is_st": is_st_instrument(upper, inst_name, is_st),
        "sina": sina_symbol_for(upper) if inferred_market == "CN" else raw.lower(),
        "currency": currency,
        "board": board,
        "lot_size": lot_size,
        "min_order_shares": min_order_shares,
        "lot_step": lot_step,
    }


def is_hk_etp_code(code: str) -> bool:
    """按港交所《证券代号分配计划》判断 5 位港股代码是否属于交易所买卖产品（ETF / 杠杆及反向产品）：
    2800-2849、3000-3199、3400-3499（港元柜台 ETF），7200-7999（杠杆及反向产品，含 072xx/077xx 2倍做多与 073xx/075xx 反向），
    9000-9199、9400-9499、9800-9849（美元柜台 ETF）。
    """
    raw = code.strip().upper().split(".")[0]
    if not raw.isdigit():
        return False
    n = int(raw)
    return (
        2800 <= n <= 2849
        or 3000 <= n <= 3199
        or 3400 <= n <= 3499
        or 7200 <= n <= 7999
        or 9000 <= n <= 9199
        or 9400 <= n <= 9499
        or 9800 <= n <= 9849
    )


def _infer_asset_type(upper: str) -> str:
    """按"交易所 + 代码段"推断资产类型：000xxx.SH / 399xxx.SZ 为指数（000xxx.SZ 是深市主板股票），
    沪市 51/56/58、深市 15 开头及港交所 ETF/ETP 代码段为 ETF，恒指系列为指数，其余为个股。"""
    code, _, suffix = upper.partition(".")
    if (suffix == "SH" and code.startswith("000")) or (suffix == "SZ" and code.startswith("399")):
        return "index"
    if (suffix == "SH" and code.startswith(("51", "56", "58"))) or (suffix == "SZ" and code.startswith("15")):
        return "etf"
    if suffix == "HK" and is_hk_etp_code(code):
        return "etf"
    if suffix == "HK" and code.startswith(("HSI", "HSTECH", "HSCEI")):
        return "index"
    return "equity"


def is_trading_day(day_obj: date, market: str = "CN") -> bool:
    if day_obj.weekday() >= 5:
        return False
    ds = day_obj.strftime("%Y-%m-%d")
    return ds not in MARKET_HOLIDAYS.get(market, set())


def next_trading_dates(base_day_str: str, horizon_bars: int, market: str = "CN") -> list[str]:
    dt = datetime.strptime(base_day_str[:10], "%Y-%m-%d").date()
    out: list[str] = []
    while len(out) < horizon_bars:
        dt += timedelta(days=1)
        if is_trading_day(dt, market=market):
            out.append(dt.strftime("%Y-%m-%d"))
    return out


def count_missing_trading_sessions(last_bar_day: str, reference_day: str, market: str = "CN") -> int:
    """计算最后一根已收盘K线 (last_bar_day) 与参考交易日 (reference_day) 之间缺失的交易所交易日数量。"""
    if not last_bar_day or not reference_day or last_bar_day[:10] >= reference_day[:10]:
        return 0
    cur = datetime.strptime(last_bar_day[:10], "%Y-%m-%d").date()
    end = datetime.strptime(reference_day[:10], "%Y-%m-%d").date()
    missing = 0
    while cur < end:
        cur += timedelta(days=1)
        if is_trading_day(cur, market=market):
            missing += 1
    return missing


CHINEXT_REGISTRATION_DAY = "2020-08-24"  # 创业板注册制改革：涨跌幅 ±10% -> ±20%（含 ST）
MAIN_BOARD_ST_10PCT_DAY = "2026-07-06"  # 沪深修订交易规则：主板风险警示股票 ±5% -> ±10%（2026-04-24 发布）

# 深交所跟踪创业板/科创创业（双创）20% 涨跌幅指数的常见 159xxx ETF 代码集合
# （来源：《深圳证券交易所证券投资基金交易和申购赎回实施细则（2020年修订）》2020-08-24 起施行）
_CN_20PCT_SZSE_ETF_CODES: frozenset[str] = frozenset(
    {
        "159915", "159949", "159952", "159977", "159908", "159948", "159957", "159958",
        "159964", "159966", "159967", "159971", "159991", "159808", "159810", "159814",
        "159821", "159848", "159600", "159603", "159617", "159629", "159630", "159663",
        "159675", "159676", "159680", "159681", "159682", "159691", "159698", "159761",
        "159762", "159777", "159780", "159781", "159782", "159783", "159786", "159787",
        "159788", "159370", "159371", "159372", "159373", "159375", "159376", "159377",
        "159378", "159380", "159381", "159382", "159383", "159385", "159386", "159387",
        "159388",
    }
)
_CN_20PCT_ETF_NAME_RE = re.compile(r"(科创|创业|双创|创50|创成长|创蓝筹|创大盘|创中盘|创科技|创医药|创精选)")
_CN_T0_ETF_NAME_RE = re.compile(
    r"(恒生|港股|恒科|H股|中概|纳指|纳斯达克|标普|道琼斯|日经|德国|法国|美国|沙特|东南亚|QDII|"
    r"国债|政金债|地方债|可转债|转债|城投债|公司债|短融|债券|黄金|上海金|货币|添益|银华日利|华宝添益|豆粕)"
)


def is_cn_t0_etf(symbol: str, name: str = "") -> bool:
    """识别沪深交易所实行当日回转交易（T+0）的 ETF 品种：
    跨境/港股/QDII ETF（513xxx/520xxx 等）、债券 ETF（5110xx~5115xx 等）、黄金 ETF（5188xx 等）、货币 ETF（5116xx~5119xx/15900x）。
    """
    upper = symbol.strip().upper()
    code, _, suffix = upper.partition(".")
    if suffix and suffix not in ("SH", "SZ"):
        return False
    if code.startswith(("511", "513", "5188", "520", "15900")):
        return True
    if code in {"159920", "159934", "159937", "159941", "159954", "159960", "159972", "159980", "159981", "159985"}:
        return True
    if name and (code.startswith(("15", "51", "52", "56", "58")) or suffix in ("SH", "SZ")) and _CN_T0_ETF_NAME_RE.search(name):
        return True
    return False


def is_cn_20pct_etf(symbol: str, name: str = "") -> bool:
    """判断 A 股 ETF 是否适用 ±20% 涨跌幅限制：
    1. 上交所科创板/双创 ETF 专用代码段 588xxx / 589xxx（自 2020-09-28 首只科创50 ETF 上市起实行 ±20%）；
    2. 深交所 159xxx 中跟踪创业板或双创指数的 ETF（内置代码表，或证券简称含「科创/创业/双创/创50/创成长/创蓝筹」等）。
    """
    upper = symbol.strip().upper()
    code, _, suffix = upper.partition(".")
    if suffix and suffix not in ("SH", "SZ"):
        return False
    if code.startswith(("588", "589")):
        return True
    if code in _CN_20PCT_SZSE_ETF_CODES:
        return True
    if name and code.startswith(("15", "51", "56", "58")) and _CN_20PCT_ETF_NAME_RE.search(name):
        return True
    return False


def get_daily_price_limit_pct(
    market: str,
    asset_type: str,
    symbol: str,
    is_st: bool = False,
    is_ipo_no_limit: bool = False,
    day_str: str | None = None,
    name: str = "",
) -> float | None:
    """返回单日涨跌停限制比例（例如 0.10 代表 ±10%）。指数、港股、美股及新股无涨跌停期返回 None。

    ST 不再单独一刀切：主板 ST 2026-07-06 前 ±5%、之后 ±10%；创业板 ST 注册制后与普通股同为 ±20%；
    科创板 ST ±20%；北交所 ±30%。ST 状态只能由调用方通过 is_st 显式提供（股票代码里不含 ST 字样）。
    """
    if market != "CN" or asset_type == "index" or is_ipo_no_limit:
        return None
    st_flag = is_st_instrument(symbol, name=name, is_st=is_st)
    day = (day_str or date.today().isoformat())[:10]
    code = symbol.upper().split(".")[0]
    if symbol.upper().endswith(".BJ") or code.startswith(("8", "4", "92")):
        return 0.30
    if code.startswith(("688", "689", "588", "589")):
        return 0.20
    if code.startswith(("300", "301")):
        if day < CHINEXT_REGISTRATION_DAY:
            return 0.05 if st_flag else 0.10
        return 0.20
    if (asset_type == "etf" or code.startswith(("15", "51", "56", "58"))) and is_cn_20pct_etf(symbol, name):
        return 0.10 if day < CHINEXT_REGISTRATION_DAY else 0.20
    if st_flag:
        return 0.05 if day < MAIN_BOARD_ST_10PCT_DAY else 0.10
    return 0.10


# 港交所价位表（Part A，股本证券）：(价格上限(含), 最小价位)
_HK_SPREAD_BASE: tuple[tuple[float, str], ...] = (
    (0.25, "0.001"),
    (0.50, "0.005"),
    (10.0, "0.010"),
    (20.0, "0.020"),
    (50.0, "0.050"),
    (100.0, "0.050"),
    (200.0, "0.100"),
    (500.0, "0.200"),
    (1000.0, "0.500"),
    (2000.0, "1.000"),
    (5000.0, "2.000"),
    (9995.0, "5.000"),
)
# 最小价位下调（仅股票/REIT/股本权证，ETP 等除外）：(生效日, {价格上限: 新价位})
_HK_SPREAD_REDUCTIONS: tuple[tuple[str, dict[float, str]], ...] = (
    ("2025-08-04", {20.0: "0.010", 50.0: "0.020"}),  # Phase 1：10-20 档、20-50 档
    ("2026-08-03", {10.0: "0.005"}),  # Phase 2：0.5-10 档（SFC 批准的规则修订生效日）
)


def hk_tick_size(price: float, asset_type: str = "equity", day_str: str | None = None) -> Decimal:
    """按价格与日期返回港股最小价位；ETF 等交易所买卖产品不适用下调，沿用原价位表。"""
    day = (day_str or date.today().isoformat())[:10]
    overrides: dict[float, str] = {}
    if asset_type == "equity":
        for effective_day, changes in _HK_SPREAD_REDUCTIONS:
            if day >= effective_day:
                overrides.update(changes)
    for upper, tick in _HK_SPREAD_BASE:
        if price <= upper:
            return Decimal(overrides.get(upper, tick))
    return Decimal(_HK_SPREAD_BASE[-1][1])


def tick_size_for(
    price: float,
    market: str = "CN",
    asset_type: str = "equity",
    day_str: str | None = None,
) -> Decimal:
    """按市场、品种、价格档位与日期返回最小报价单位（tick size）。

    - HK 股票/ETF：按港交所价位表与两期下调规则。
    - CN ETF：上交所/深交所基金最小变动单位 0.001 元。
    - US 股票/ETF < 1 美元：SEC Reg NMS Rule 612 子美分规则 0.0001 美元。
    - 其余：0.01。
    """
    if market == "HK" and asset_type in ("equity", "etf"):
        return hk_tick_size(price, asset_type, day_str)
    if market == "CN" and asset_type == "etf":
        return Decimal("0.001")
    if market == "US" and asset_type in ("equity", "etf") and 0 < price < 1.0:
        return Decimal("0.0001")
    return Decimal("0.01")


def price_decimals(
    price: float,
    market: str = "CN",
    asset_type: str = "equity",
    day_str: str | None = None,
) -> int:
    """返回价格展示与取整应保留的小数位数（至少 2 位，ETF/低价港股 3 位，1美元以下美股 4 位）。"""
    tick = tick_size_for(max(price, 1e-4), market, asset_type, day_str)
    # 价位表以 "0.020" 这类定宽字符串存储，先 normalize 去掉尾随 0，避免 0.02 档被当成 3 位小数
    exp = max(2, -int(tick.normalize().as_tuple().exponent))
    return exp


def round_to_tick(price: float, market: str = "CN", asset_type: str = "equity", day_str: str | None = None) -> float:
    tick = tick_size_for(max(price, 1e-4), market, asset_type, day_str)
    if price <= 0:
        return float(tick)
    d = Decimal(str(price))
    steps = (d / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float((steps * tick).quantize(tick))


def daily_limit_prices(
    prev_close: float,
    limit_pct: float,
    market: str = "CN",
    asset_type: str = "equity",
    day_str: str | None = None,
) -> tuple[float, float]:
    """按交易所规则计算 (涨停价, 跌停价)：前收盘 × (1 ± 涨跌幅)，按最小价位四舍五入。

    用十进制计算：二进制浮点下 1.30 × 0.95 = 1.2349999…，会把交易所的 1.24 误算成 1.23。
    低价股按比例容差判断会漏判（前收 1.46 的 ST 股跌停价 1.39 只比 0.95 倍高 0.2%），必须按价位取整后比较。
    """
    tick = tick_size_for(prev_close, market, asset_type, day_str)
    prev = Decimal(str(prev_close)).quantize(tick, rounding=ROUND_HALF_UP)
    pct = Decimal(str(limit_pct))
    up = (prev * (1 + pct) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    down = (prev * (1 - pct) / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick
    return float(up), float(down)


def round_shares_to_lot(raw_shares: int, rules: dict[str, Any]) -> int:
    """按板块最小申报股数与递增步长向下取整（科创板 200+1，北交所 100+1，主板/港股整手）。"""
    min_order = int(rules.get("min_order_shares") or rules.get("lot_size") or 1)
    step = int(rules.get("lot_step") or rules.get("lot_size") or 1)
    if raw_shares < min_order:
        return 0
    return min_order + ((raw_shares - min_order) // step) * step


def _regulatory_levies_bps(market: str, asset_type: str, symbol: str, day_str: str | None) -> tuple[float, float, str]:
    """交易所/监管规费（不含佣金与印花税），返回 (买入侧 bps, 卖出侧 bps, 口径说明)。来源与生效日期见 references/{cn,hk,us}-market.md。"""
    if asset_type == "index":
        return 0.0, 0.0, "指数不可直接交易"
    day = day_str or "9999-12-31"
    if market == "CN":
        if asset_type != "equity":
            return 0.0, 0.0, "A股 ETF 不收过户费"
        # 中国结算过户费：2022-04-29 起沪深京 A 股统一按成交金额 0.001% 双向收取；此前沪深 0.002%、北交所 0.0025%
        if day >= "2022-04-29":
            side = 0.1
        else:
            side = 0.25 if (symbol.upper().endswith(".BJ") or symbol.split(".")[0].startswith(("8", "4", "92"))) else 0.2
        return side, side, "中国结算过户费：2022-04-29 起成交金额 0.001% 双向收取（此前沪深 0.002%、北交所 0.0025%）"
    if market == "HK":
        # 每边：港交所交易费（2023-01-01 起 0.00565%，此前 0.005%）+ 证监会交易征费 0.0027%（2014-11-01 起）
        # + 会计及财务汇报局交易征费 0.00015%（2022-01-01 起）+ 中央结算股份交收费（2025-06-30 起 0.0042% 且取消上下限，此前 0.002%）
        trading_fee = 0.565 if day >= "2023-01-01" else 0.5
        afrc = 0.015 if day >= "2022-01-01" else 0.0
        settlement = 0.42 if day >= "2025-06-30" else 0.20
        side = round(trading_fee + 0.27 + afrc + settlement, 4)
        return side, side, (
            "港股每边规费：交易费 0.00565% + 证监会征费 0.0027% + 会财局征费 0.00015% + 股份交收费 0.0042%（2025-06-30 起）；"
            "2025-06-30 前交收费 0.002% 设最低 2 港元/最高 100 港元，此处不计上下限"
        )
    # 美股 SEC Section 31 费只向卖方收取：2026-04-04 起每百万美元 20.60；2025-05-14~2026-04-03 为 0；此前 27.80（起始日未在本表核实）
    if day >= "2026-04-04":
        sell = 0.206
    elif day >= "2025-05-14":
        sell = 0.0
    else:
        sell = 0.278
    return 0.0, sell, "美股 SEC Section 31 费仅卖方收取（2026-04-04 起每百万美元 20.60）；FINRA TAF 按股计费，未计入"


# 单笔最低佣金是券商收费口径（各券商不同），不是交易所规则：此处取常见水平作为小额账户摩擦的保守假设
_MIN_COMMISSION_PER_LEG = {"CN": 5.0, "HK": 15.0, "US": 0.5}
_MIN_COMMISSION_NOTE = "单笔最低佣金为券商常见收费口径假设（A股 5 元、港股 15 港元、美股 0.5 美元），非交易所规则，以实际券商为准"


def _fee_fields(market: str, asset_type: str, symbol: str, day_str: str | None) -> dict[str, Any]:
    buy_bps, sell_bps, note = _regulatory_levies_bps(market, asset_type, symbol, day_str)
    return {
        "min_commission_per_leg": _MIN_COMMISSION_PER_LEG[market] if asset_type != "index" else 0.0,
        "min_commission_note": _MIN_COMMISSION_NOTE,
        "regulatory_levies_buy_bps": buy_bps,
        "regulatory_levies_sell_bps": sell_bps,
        "regulatory_levies_note": note,
    }


def get_market_microstructure_rules(
    market: str,
    asset_type: str,
    symbol: str,
    is_st: bool = False,
    is_ipo_no_limit: bool = False,
    day_str: str | None = None,
    board_lot: int | None = None,
    name: str = "",
) -> dict[str, Any]:
    """board_lot：港股每手股数（各股票不同，如小米 200、汇丰 400），由行情快照提供；缺省时按 100 股假设并标注来源。"""
    st_flag = is_st_instrument(symbol, name=name, is_st=is_st)
    limit_pct = get_daily_price_limit_pct(
        market, asset_type, symbol, is_st=st_flag, is_ipo_no_limit=is_ipo_no_limit, day_str=day_str, name=name
    )
    tradable = asset_type != "index"
    if market == "CN":
        code = symbol.upper().split(".")[0]
        # A 股证券交易印花税：2008-09-19 起卖出单边 0.1%；2023-08-28 起减半至 0.05%。
        cn_stamp_sell = 5.0 if (not day_str or day_str >= "2023-08-28") else 10.0
        board = "MAIN"
        if asset_type == "equity":
            if code.startswith(("300", "301")):
                board = "CHINEXT"
            elif code.startswith(("688", "689")):
                board = "STAR"
            elif symbol.upper().endswith(".BJ") or code.startswith(("8", "4", "92")):
                board = "BSE"
        elif asset_type == "etf":
            if code.startswith(("588", "589")):
                board = "STAR"
            elif is_cn_20pct_etf(symbol, name):
                board = "CHINEXT"
        lot_size, min_order_shares, lot_step = _cn_lot_rules(asset_type, board)
        cn_t0 = bool(tradable and asset_type == "etf" and is_cn_t0_etf(symbol, name))
        return {
            "market": "CN",
            "board": board,
            "is_st": st_flag,
            "is_ipo_no_limit": is_ipo_no_limit,
            "settlement": ("T+0_TRADING_T+1_CLEARING" if cn_t0 else "T+1") if tradable else "INDEX_NON_TRADABLE",
            "settlement_note": "跨境/债券/黄金/货币类 ETF 为 T+0，需按品种核对" if asset_type == "etf" else None,
            "can_intraday_roundtrip": cn_t0,
            "daily_price_limit_pct": limit_pct,
            "lot_size": lot_size,
            "min_order_shares": min_order_shares,
            "lot_step": lot_step,
            "tick_size": 0.001 if asset_type == "etf" else 0.01,
            "color_scheme": "cn_red_up",
            "session_hours_local": "09:30-11:30, 13:00-15:00 Asia/Shanghai",
            "commission_bps": 2.5,
            "stamp_duty_buy_bps": 0.0,
            "stamp_duty_sell_bps": cn_stamp_sell if asset_type == "equity" else 0.0,
            "stamp_duty_note": "A股卖出单边印花税：2023-08-28 起 5bps，此前 10bps",
            **_fee_fields("CN", asset_type, symbol, day_str),
        }
    if market == "HK":
        # 港股印花税：2021-08-01 起买卖双方各 0.13%，2023-11-17 起降回 0.1%（此前为 0.1%）；ETF 豁免
        is_hk_etp = asset_type == "etf" or is_hk_etp_code(symbol)
        if is_hk_etp or not tradable:
            hk_stamp = 0.0
        elif day_str and "2021-08-01" <= day_str < "2023-11-17":
            hk_stamp = 13.0
        else:
            hk_stamp = 10.0
        if not tradable:
            lot, lot_source = 1, "NOT_TRADABLE"
        elif board_lot and int(board_lot) > 0:
            lot, lot_source = int(board_lot), "PROVIDED"
        else:
            lot, lot_source = 100, "ASSUMED_DEFAULT"
        return {
            "market": "HK",
            "settlement": "T+0_TRADING_T+2_CLEARING" if tradable else "INDEX_NON_TRADABLE",
            "can_intraday_roundtrip": tradable,
            "daily_price_limit_pct": None,
            "lot_size": lot,
            "min_order_shares": lot,
            "lot_step": lot,
            "lot_size_source": lot_source,
            "color_scheme": "cn_red_up",
            "session_hours_local": "09:30-12:00, 13:00-16:00 Asia/Hong_Kong",
            "commission_bps": 3.0,
            "stamp_duty_buy_bps": hk_stamp,
            "stamp_duty_sell_bps": hk_stamp,
            "stamp_duty_note": "港股双边印花税：2021-08-01~2023-11-16 为 13bps，其余 10bps；ETF 豁免",
            **_fee_fields("HK", asset_type, symbol, day_str),
        }
    return {
        "market": "US",
        "settlement": "T+1_SEC_SETTLEMENT" if tradable else "INDEX_NON_TRADABLE",
        "day_trading_rule": (
            "FINRA 4210 修订：PDT 认定与 2.5 万美元门槛自 2026-06-04 起取消（券商最晚 2027-10-20 完成过渡），"
            "改由券商日内保证金规则约束"
        ),
        "can_intraday_roundtrip": tradable,
        "daily_price_limit_pct": None,
        "tick_size": float(tick_size_for(10.0, "US", asset_type, day_str)),
        "lot_size": 1,
        "min_order_shares": 1,
        "lot_step": 1,
        "color_scheme": "us_green_up",
        "session_hours_local": "09:30-16:00 America/New_York",
        "commission_bps": 1.0,
        "stamp_duty_buy_bps": 0.0,
        "stamp_duty_sell_bps": 0.0,
        **_fee_fields("US", asset_type, symbol, day_str),
    }


def round_trip_cost_rate(rules: dict[str, Any]) -> float:
    """一次买入加一次卖出的费率合计（佣金双边 + 买卖两侧印花税）。"""
    bps = rules["commission_bps"] * 2.0 + rules.get("stamp_duty_buy_bps", 0.0) + rules["stamp_duty_sell_bps"]
    return bps / 10000.0


def compute_round_trip_cost_detail(
    notional: float,
    rules: dict[str, Any],
    shares: int | None = None,
    exit_notional: float | None = None,
    exit_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """计算含单笔最低佣金与交易所/监管规费的往返成本明细（支持买卖两腿不同成交额与跨日规则）。

    费率只读 rules / exit_rules 里的 min_commission_per_leg、regulatory_levies_{buy,sell}_bps（由 get_market_microstructure_rules 按日期给出），
    本函数不另设常量。
    """
    prop_rate = round_trip_cost_rate(rules)
    if notional <= 0:
        return {
            "total_cost_amount": 0.0,
            "effective_cost_rate": prop_rate,
            "proportional_cost_rate": prop_rate,
            "regulatory_levies_bps": 0.0,
            "min_commission_applied": False,
            "breakeven_price_delta": 0.0,
        }
    sell_notional = float(notional if exit_notional is None else exit_notional)
    s_rules = rules if exit_rules is None else exit_rules
    buy_comm_bps = float(rules.get("commission_bps") or 0.0)
    sell_comm_bps = float(s_rules.get("commission_bps") or 0.0)
    buy_stamp_bps = float(rules.get("stamp_duty_buy_bps") or 0.0)
    sell_stamp_bps = float(s_rules.get("stamp_duty_sell_bps") or 0.0)
    buy_levies_bps = float(rules["regulatory_levies_buy_bps"])
    sell_levies_bps = float(s_rules["regulatory_levies_sell_bps"])
    buy_min_comm = float(rules["min_commission_per_leg"])
    sell_min_comm = float(s_rules["min_commission_per_leg"])

    buy_prop_comm = notional * (buy_comm_bps / 10000.0)
    sell_prop_comm = sell_notional * (sell_comm_bps / 10000.0)
    buy_actual_comm = max(buy_min_comm, buy_prop_comm)
    sell_actual_comm = max(sell_min_comm, sell_prop_comm)
    min_comm_applied = (buy_prop_comm + 1e-9 < buy_min_comm) or (sell_prop_comm + 1e-9 < sell_min_comm)

    commission_total = buy_actual_comm + sell_actual_comm
    stamp_total = notional * (buy_stamp_bps / 10000.0) + sell_notional * (sell_stamp_bps / 10000.0)
    levies_total = notional * (buy_levies_bps / 10000.0) + sell_notional * (sell_levies_bps / 10000.0)
    levies_bps = buy_levies_bps + sell_levies_bps
    total_cost = commission_total + stamp_total + levies_total
    eff_rate = total_cost / notional
    be_delta = (total_cost / int(shares)) if (shares and int(shares) > 0) else 0.0
    return {
        "total_cost_amount": round(total_cost, 4),
        "effective_cost_rate": round(eff_rate, 6),
        "proportional_cost_rate": round(prop_rate, 6),
        "regulatory_levies_bps": levies_bps,
        "min_commission_applied": min_comm_applied,
        "breakeven_price_delta": round(be_delta, 4),
    }


def apply_point_in_time_adjustment(
    bars: list[dict[str, Any]],
    corporate_actions: list[dict[str, Any]] | None = None,
    mode: str = "split",
    as_of_day: str | None = None,
) -> list[dict[str, Any]]:
    """根据截至 as_of_day 已知的拆并股/分红事件计算前复权分析序列，同时保留 raw_close 供真实成交回测使用。"""
    if not bars:
        return []
    cutoff = as_of_day or bars[-1]["day"]
    actions = [
        act
        for act in (corporate_actions or [])
        if act["ex_date"] <= cutoff
    ]
    out: list[dict[str, Any]] = []
    for bar in bars:
        b_day = bar["day"]
        factor = 1.0
        if mode in ("split", "total_return"):
            for act in actions:
                if b_day < act["ex_date"]:
                    split_ratio = float(act.get("split_ratio", 1.0))
                    if split_ratio > 0:
                        factor /= split_ratio
                    if mode == "total_return":
                        div_factor = float(act.get("dividend_factor", 1.0))
                        if div_factor > 0:
                            factor *= div_factor
        adj_bar = dict(bar)
        adj_bar["raw_open"] = float(bar["open"])
        adj_bar["raw_high"] = float(bar["high"])
        adj_bar["raw_low"] = float(bar["low"])
        adj_bar["raw_close"] = float(bar["close"])
        adj_bar["adj_factor"] = round(factor, 12)
        adj_bar["open"] = round(float(bar["open"]) * factor, 4)
        adj_bar["high"] = round(float(bar["high"]) * factor, 4)
        adj_bar["low"] = round(float(bar["low"]) * factor, 4)
        adj_bar["close"] = round(float(bar["close"]) * factor, 4)
        out.append(adj_bar)
    return out


def rescale_bars_as_of(bars: list[dict[str, Any]], cut_idx: int) -> list[dict[str, Any]]:
    """将已复权序列还原到历史截面 cut_idx 当时的 Point-in-Time 复权口径（消除 cut_idx 之后的未来分红/送转因子泄漏）。

    对于任意 bar[i]，其在 cut_idx 截面时的因果复权因子等于 factor(i) / factor(cut_idx)；
    当 adj_factor[i] == adj_factor[cut_idx]（即 [bar[i].day, bar[cut_idx].day] 之间无除权事件）时，直接取 raw_open/high/low/close。
    """
    if not bars:
        return []
    base_factor = float(bars[cut_idx].get("adj_factor") or 1.0)
    if abs(base_factor - 1.0) <= 1e-12 or base_factor <= 0:
        return [dict(b) for b in bars]
    out: list[dict[str, Any]] = []
    for b in bars:
        bf = float(b.get("adj_factor") or 1.0)
        nb = dict(b)
        if abs(bf - base_factor) <= 1e-12 and "raw_close" in b:
            nb["open"] = float(b["raw_open"])
            nb["high"] = float(b["raw_high"])
            nb["low"] = float(b["raw_low"])
            nb["close"] = float(b["raw_close"])
            nb["adj_factor"] = 1.0
        else:
            rel = bf / base_factor
            nb["open"] = round(float(b.get("raw_open", b["open"])) * rel, 4)
            nb["high"] = round(float(b.get("raw_high", b["high"])) * rel, 4)
            nb["low"] = round(float(b.get("raw_low", b["low"])) * rel, 4)
            nb["close"] = round(float(b.get("raw_close", b["close"])) * rel, 4)
            nb["adj_factor"] = round(rel, 12)
        out.append(nb)
    return out
