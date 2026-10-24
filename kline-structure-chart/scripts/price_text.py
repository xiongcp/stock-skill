"""价格文本识别与价格白名单：回复校验器用它判断回复里的数字是不是报告里出现过的价位。"""
from __future__ import annotations

import re
from typing import Any

# 剔除非价格数字的模式：日期、指标参数名、百分比、百分点、样本量、天数/根数/手数/股数/倍数/ATR/分数/基点
NON_PRICE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b(?:RSI|ATR|ADX|RVOL|BBW|HV|IC|BSS|Brier|RS|C/A)\d*\s*(?:\(\s*\d+\s*\))?\s*(?:[=:：约为达]|处于|位于)\s*[+-]?\d+(?:\.\d+)?", re.I),
    re.compile(r"(?:盈亏比|风险收益比)\s*(?:[=:：约为达]|处于)?\s*\d+(?:\.\d+)?"),
    re.compile(r"\b(?:E|C|MA|EMA|SMA|RSI|ATR|ADX|RVOL|TP|Wave|W)\d+\b", re.I),
    re.compile(r"\b(?:Supertrend|MACD)\s*\(\s*\d+(?:\s*,\s*\d+)*\s*\)", re.I),
    re.compile(r"\b(?:RSI|ATR|ADX|RVOL|MA|EMA|SMA)\s*\(\s*\d+\s*\)", re.I),
    re.compile(r"[+-]?\d+(?:\.\d+)?\s*%"),
    re.compile(r"[+-]?\d+(?:\.\d+)?\s*个?百分点"),
    re.compile(r"\bn\s*=\s*\d+\b", re.I),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*/\s*100\b"),
    # 中文单位后面通常紧跟汉字（\b 在两个汉字之间不成立），用前后断言代替 \b
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:个?交易日|日|天|根|手|股|笔|次|浪|分|bps|倍)(?![A-Za-z0-9])"),
    # 波浪编号（「浪 1」「第 3 浪」「第 3 主升浪」「浪 3/5」「Wave 4」）不是价格，必须先从价格候选中剔除
    re.compile(r"(?:第\s*\d+\s*[一-鿿]{0,4}\s*浪|(?:第\s*)?[浪Ww](?:ave)?\s*[1-5ABCDEabcde](?:\s*/\s*[1-5ABCDEabcde])?)(?![A-Za-z0-9])"),
    re.compile(r"(?<![\d.])(?:0\.236|0\.382|0\.500?|0\.618|0\.786|1\.000|1\.618)\s*(?:倍位?|回撤位?|反弹位?|延伸位?|测算位?|[×*xX])"),
    re.compile(r"\b\d+(?:\.\d+)?\s*个\s*ATR\b", re.I),
    re.compile(r"(?<![\d.])\d+(?:\.\d+)?\s*(?:[×*]|[xX]\b)"),
    re.compile(r"[+-]?\d+(?:\.\d+)?\s*R\b"),
    re.compile(r"±\s*\d+(?:\.\d+)?"),
)
CONTEXT_PRICE_RE = re.compile(
    r"(?:目标位|失效位|止损位|止损|触发位|破位价|支撑位?|压力位?|现价|收盘价?|POC|VAH|VAL|TP1|TP2|入场区)\s*[为=:：约至]?\s*(\d+(?:\.\d+)?)"
)

# 键名含这些片段的字段不是价格（百分比、样本数、根数、比值、乘数、相似度等），不进入白名单，也不深入其子字段
_NON_PRICE_KEY_TOKENS = (
    "freq", "pct", "ratio", "sample", "count", "bars", "hits", "_ci", "score", "size", "coverage",
    "multiplier", "sigma", "weight", "similarity", "idx", "distance", "atr", "return", "slope",
    "days", "horizon", "_r",
)
_PRICE_KEY_TOKENS = (
    "price", "open", "high", "low", "close", "ma20", "ma60", "ma120", "ma250", "sma", "ema", "vwap",
    "poc", "vah", "val", "upper", "lower", "mid", "barrier", "take_profit", "stop_loss", "entry_zone",
    "support", "resistance", "level", "target", "q10", "q25", "q50", "q75", "q90", "band", "cap",
    "retest", "rebound", "range", "zone",
)
_RECENT_BAR_KEYS = ("open", "high", "low", "close", "ma20", "ma60", "ma120", "ma250", "kernel_smooth")


def price_in_whitelist(value: float, prices: list[float], tolerance: float) -> bool:
    return any(abs(value - p) <= tolerance + 1e-6 for p in prices)


def collect_price_whitelist(
    sections: dict[str, Any], price_decimals: int, recent_bars: list[dict[str, Any]]
) -> dict[str, Any]:
    """从给定各段里键名像价格的字段与最近 20 根K线收集全部合法价格。"""
    prices: set[float] = set()

    def _add(v: Any) -> None:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) > 0:
            prices.add(round(float(v), price_decimals))

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, val in obj.items():
                kl = k.lower()
                if any(tok in kl for tok in _NON_PRICE_KEY_TOKENS):
                    continue
                if any(tok in kl for tok in _PRICE_KEY_TOKENS):
                    if isinstance(val, (int, float)):
                        _add(val)
                    elif isinstance(val, list):
                        for item in val:
                            if isinstance(item, list):
                                for sub in item:
                                    _add(sub)
                            elif isinstance(item, dict):
                                _walk(item)
                            else:
                                _add(item)
                    elif isinstance(val, dict):
                        _walk(val)
                else:
                    _walk(val)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    for subtree in sections.values():
        _walk(subtree)
    for bar in recent_bars[-20:]:
        for k in _RECENT_BAR_KEYS:
            _add(bar.get(k))
    sorted_prices = sorted(prices)
    return {
        "price_decimals": price_decimals,
        "tolerance": round(max(10.0 ** (-price_decimals), 1e-3), 6),
        "count": len(sorted_prices),
        "prices": sorted_prices,
    }
