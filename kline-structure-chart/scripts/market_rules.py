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
    tz_name, close_time = MARKET_SESSIONS[market]
    now_local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    today = now_local.date().isoformat()
    day = day_str[:10]
    if day < today:
        return True
    if day > today:
        return False
    return now_local.time() >= close_time


def latest_closed_session(market: str, now: datetime | None = None) -> str:
    """交易所本地时间下最近一个已收盘的交易日（按休市表判断，超出覆盖范围时按工作日估算）。"""
    tz_name, close_time = MARKET_SESSIONS[market]
    now_local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    day = now_local.date()
    if not (is_trading_day(day, market) and now_local.time() >= close_time):
        day -= timedelta(days=1)
        while not is_trading_day(day, market):
            day -= timedelta(days=1)
    return day.isoformat()


_ST_NAME_PREFIX = re.compile(r"^\*?S?\*?ST")


def is_st_instrument(symbol: str = "", name: str = "", is_st: bool = False) -> bool:
    """识别 A 股风险警示股票：显式 is_st，或证券简称以 ST / *ST / SST / S*ST 开头。

    风险警示制度只存在于 A 股，港股、美股一律返回 False；只认名称前缀，避免英文名中间含 "ST" 误判。
    """
    if is_st:
        return True
    if symbol and not symbol.strip().upper().endswith((".SH", ".SZ", ".BJ")):
        return False
    return bool(_ST_NAME_PREFIX.match(re.sub(r"\s+", "", name or "").upper()))


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


CHINEXT_REGISTRATION_DAY = "2020-08-24"  # 创业板注册制改革：涨跌幅 ±10% -> ±20%（含 ST）
MAIN_BOARD_ST_10PCT_DAY = "2026-07-06"  # 沪深修订交易规则：主板风险警示股票 ±5% -> ±10%（2026-04-24 发布）


def get_daily_price_limit_pct(
    market: str,
    asset_type: str,
    symbol: str,
    is_st: bool = False,
    is_ipo_no_limit: bool = False,
    day_str: str | None = None,
) -> float | None:
    """返回单日涨跌停限制比例（例如 0.10 代表 ±10%）。指数、港股、美股及新股无涨跌停期返回 None。

    ST 不再单独一刀切：主板 ST 2026-07-06 前 ±5%、之后 ±10%；创业板 ST 注册制后与普通股同为 ±20%；
    科创板 ST ±20%；北交所 ±30%。ST 状态只能由调用方通过 is_st 显式提供（股票代码里不含 ST 字样）。
    """
    if market != "CN" or asset_type == "index" or is_ipo_no_limit:
        return None
    day = (day_str or date.today().isoformat())[:10]
    code = symbol.upper().split(".")[0]
    if symbol.upper().endswith(".BJ") or code.startswith(("8", "4", "92")):
        return 0.30
    if code.startswith(("688", "689")):
        return 0.20
    if code.startswith(("300", "301")):
        if day < CHINEXT_REGISTRATION_DAY:
            return 0.05 if is_st else 0.10
        return 0.20
    if is_st:
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


def round_shares_to_lot(raw_shares: int, rules: dict[str, Any]) -> int:
    """按板块最小申报股数与递增步长向下取整（科创板 200+1，北交所 100+1，主板/港股整手）。"""
    min_order = int(rules.get("min_order_shares") or rules.get("lot_size") or 1)
    step = int(rules.get("lot_step") or rules.get("lot_size") or 1)
    if raw_shares < min_order:
        return 0
    return min_order + ((raw_shares - min_order) // step) * step


def get_market_microstructure_rules(
    market: str,
    asset_type: str,
    symbol: str,
    is_st: bool = False,
    is_ipo_no_limit: bool = False,
    day_str: str | None = None,
    board_lot: int | None = None,
) -> dict[str, Any]:
    """board_lot：港股每手股数（各股票不同，如小米 200、汇丰 400），由行情快照提供；缺省时按 100 股假设并标注来源。"""
    st_flag = is_st_instrument(symbol, is_st=is_st)
    limit_pct = get_daily_price_limit_pct(
        market, asset_type, symbol, is_st=st_flag, is_ipo_no_limit=is_ipo_no_limit, day_str=day_str
    )
    tradable = asset_type != "index"
    if market == "CN":
        code = symbol.upper().split(".")[0]
        board = "MAIN"
        if asset_type == "equity":
            if code.startswith(("300", "301")):
                board = "CHINEXT"
            elif code.startswith(("688", "689")):
                board = "STAR"
            elif symbol.upper().endswith(".BJ") or code.startswith(("8", "4", "92")):
                board = "BSE"
        lot_size, min_order_shares, lot_step = _cn_lot_rules(asset_type, board)
        return {
            "market": "CN",
            "board": board,
            "is_st": st_flag,
            "is_ipo_no_limit": is_ipo_no_limit,
            "settlement": "T+1" if tradable else "INDEX_NON_TRADABLE",
            "settlement_note": "跨境/债券/黄金/货币类 ETF 为 T+0，需按品种核对" if asset_type == "etf" else None,
            "can_intraday_roundtrip": False,
            "daily_price_limit_pct": limit_pct,
            "lot_size": lot_size,
            "min_order_shares": min_order_shares,
            "lot_step": lot_step,
            "tick_size": 0.001 if asset_type == "etf" else 0.01,
            "color_scheme": "cn_red_up",
            "session_hours_local": "09:30-11:30, 13:00-15:00 Asia/Shanghai",
            "commission_bps": 2.5,
            "stamp_duty_buy_bps": 0.0,
            "stamp_duty_sell_bps": 5.0 if asset_type == "equity" else 0.0,
        }
    if market == "HK":
        # 港股印花税自 2023-11-17 起买卖双方各 0.1%；ETF 豁免
        is_hk_etp = asset_type == "etf" or is_hk_etp_code(symbol)
        hk_stamp = 0.0 if is_hk_etp or not tradable else 10.0
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
        "lot_size": 1,
        "min_order_shares": 1,
        "lot_step": 1,
        "color_scheme": "us_green_up",
        "session_hours_local": "09:30-16:00 America/New_York",
        "commission_bps": 1.0,
        "stamp_duty_buy_bps": 0.0,
        "stamp_duty_sell_bps": 0.0,
    }


def round_trip_cost_rate(rules: dict[str, Any]) -> float:
    """一次买入加一次卖出的费率合计（佣金双边 + 买卖两侧印花税）。"""
    bps = rules["commission_bps"] * 2.0 + rules.get("stamp_duty_buy_bps", 0.0) + rules["stamp_duty_sell_bps"]
    return bps / 10000.0


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
        adj_bar["adj_factor"] = round(factor, 8)
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
    if abs(base_factor - 1.0) <= 1e-9 or base_factor <= 0:
        return [dict(b) for b in bars]
    out: list[dict[str, Any]] = []
    for b in bars:
        bf = float(b.get("adj_factor") or 1.0)
        nb = dict(b)
        if abs(bf - base_factor) <= 1e-9 and "raw_close" in b:
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
            nb["adj_factor"] = round(rel, 8)
        out.append(nb)
    return out
