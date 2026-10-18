"""K 线结构层纯计算（与渲染无关）：实时K线拆分、ZigZag 拐点、凸包轨道与形态分类、ABC 测算、黄金分割、
背景层、跳空缺口、支撑压力阶梯与三条规则推演。

analysis.py 与 render_structure_chart.py 都只从这里取结构结果：v5 主流程计算一次 compute_structure_layer，
同一份结果同时写入 JSON 并交给 PNG 渲染器，渲染层不再重算任何点位。
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

from scenarios import build_scenarios_with_base_rates

REVERSAL = 0.05             # ABC：B 浪至少反向 5%
B_MIN_RETRACE = 0.382       # ABC：B 浪至少收回 A 浪的 0.382
B_MAX_RETRACE = 0.9         # ABC：B 浪收回超过 A 浪的 0.9 就是双顶/双底或区间，不再按 ABC 划分
RATIOS = (0.382, 0.618, 1.0)
EXT_RATIO = 1.618           # 1.000 倍位被触及后追加的延伸测算位
MAX_ALT_COUNTS = 2          # 主计数之外最多输出的备选浪型计数
MIN_TRIGGER_ATR = 0.5       # 推演触发位至少离最新收盘价这么多个 ATR，更近的价位只作「正在争夺」披露
SIGMA_LOOKBACK = 60         # 推演折点时间用的日对数收益 RMS 回看根数（与 forecast.py 同口径）
RETRACE_RATIOS = (0.382, 0.5, 0.618)
MIN_RAIL_SPAN = 10          # 轨道两个支点之间至少间隔的K线根数
RAIL_RECENT_PIVOTS = 4      # 轨道候选只在最近这么多个同类已确认拐点中两两组合（描述当前结构，而非窗口最早的拐点）
FLAT_ATR_PER_BAR = 0.025   # 轨道每根K线移动不足 0.025×ATR（40 根K线累计不足 1 个 ATR）视为走平
MARKET_CLOSE = "15:00:00"
STRUCT_DAYS = 120           # 结构层窗口：轨道/ABC/黄金分割/阶梯/推演只用最近这么多根已收盘K线，与 --days 无关
HISTORY_BARS = 420          # 结构层 ZigZag 与前高前低阶梯用的历史长度（约 1.7 年），与 --days 无关
BACKGROUND_MIN_DAYS = 250   # 背景层至少看一年
MAX_DAYS = 500
TREND_FLAT_PCT = 0.5        # 均线近 20 日变化在 ±0.5% 以内视为走平


def split_live_bar(bars: list[dict], intraday: dict | None) -> tuple[list[dict], dict | None, str | None]:
    """拆出「已收盘日K」与「盘中未收盘K线」。返回 (completed, live, data_note)。"""
    completed = list(bars)
    if not intraday:
        return completed, None, None
    # 数据网关已按交易所时区判定收盘状态时以其为准；腾讯快照沿用 A 股 15:00 收盘判定
    closed = intraday["session_closed"] if "session_closed" in intraday else intraday["stamp"][11:19] >= MARKET_CLOSE
    if intraday["day"] == completed[-1]["day"]:
        if closed:
            return completed, None, None
        completed.pop()  # 新浪日K里的今天是未收盘的半成品，改用腾讯实时快照
    if intraday["day"] > completed[-1]["day"]:
        if closed and intraday["volume"]:
            # 已收盘但新浪日K尚未入库：用腾讯收盘快照补齐当日K线，避免把收盘价误称「盘中」
            completed.append({**intraday, "complete": True})
            return completed, None, f"今日日K由腾讯收盘快照（{intraday['stamp']}）补齐，新浪日K尚未入库"
        return completed, intraday, None
    return completed, None, None


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= n:
            running -= values[i - n]
        if i >= n - 1:
            out[i] = running / n
    return out


def money(value: float) -> Decimal:
    v = round(float(value), 4)
    cleaned = Decimal(str(v))
    if 0 < abs(v) < 10.0 and round(v, 3) != round(v, 2):
        return cleaned.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return cleaned.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q2(value: Decimal) -> Decimal:
    if 0 < abs(float(value)) < 10.0 and value != value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
        return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_price(value: float, decimals: int = 2) -> str:
    """价格文本按价位表小数位输出，与 *_report.md 同一精度。

    decimals 由调用方按 market_rules.price_decimals（市场 + 品种 + 价位表）给出；缺省 2 位即 A 股股票口径。
    """
    return f"{float(value):.{decimals}f}"



# ---------------------------------------------------------------------------
# 结构识别：波段拐点 → 楔形/三角形轨道 → 突破状态
# ---------------------------------------------------------------------------


def atr_pct(bars: list[dict], n: int = 20) -> float:
    """近 n 日平均真实波幅（占昨收比例）。"""
    trs = []
    for prev, cur in zip(bars[-n - 1 : -1], bars[-n:]):
        tr = max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"]))
        trs.append(tr / prev["close"])
    return sum(trs) / len(trs)


def zigzag(bars: list[dict], thr: float) -> list[dict]:
    """高低点 ZigZag：反向幅度 ≥ thr 才确认上一个拐点；最后一个元素是尚未确认的当前波段极值。"""

    def pivot(i: int, kind: str, confirmed: bool = True) -> dict:
        price = bars[i]["high"] if kind == "H" else bars[i]["low"]
        return {"idx": i, "day": bars[i]["day"], "price": float(money(price)), "kind": kind, "confirmed": confirmed}

    pivots: list[dict] = []
    hi_i = lo_i = ext = 0
    trend = 0  # 1: 上涨波段进行中；-1: 下跌波段进行中；0: 尚未确定
    for i in range(1, len(bars)):
        high, low = bars[i]["high"], bars[i]["low"]
        if trend == 0:
            if high > bars[hi_i]["high"]:
                hi_i = i
            if low < bars[lo_i]["low"]:
                lo_i = i
            if bars[hi_i]["high"] >= bars[lo_i]["low"] * (1 + thr):
                if lo_i < hi_i:
                    pivots.append(pivot(lo_i, "L"))
                    trend, ext = 1, hi_i
                else:
                    pivots.append(pivot(hi_i, "H"))
                    trend, ext = -1, lo_i
        elif trend == 1:
            if high > bars[ext]["high"]:
                ext = i
            elif low <= bars[ext]["high"] * (1 - thr):
                pivots.append(pivot(ext, "H"))
                trend, ext = -1, i
        else:
            if low < bars[ext]["low"]:
                ext = i
            elif high >= bars[ext]["low"] * (1 + thr):
                pivots.append(pivot(ext, "L"))
                trend, ext = 1, i
    if trend:
        pivots.append(pivot(ext, "H" if trend == 1 else "L", confirmed=False))
    return pivots


def swing_pivots(completed: list[dict], view_start: int) -> tuple[list[dict], list[dict], float, float]:
    """在全部已收盘K线上跑 ZigZag（窗口首个拐点也是真拐点），返回 (窗口内拐点[窗口下标], 全部拐点, 阈值, ATR)。

    阈值随波动率自适应：2×ATR%，夹在 3%~8%；窗口内确认的高/低点不足 2 个时逐步放宽到 2%。
    """
    atr = atr_pct(completed)
    thr = min(0.08, max(0.03, 2.0 * atr))
    while True:
        all_pivots = zigzag(completed, thr)
        pivots = [dict(p, idx=p["idx"] - view_start) for p in all_pivots if p["idx"] >= view_start]
        n_high = sum(1 for p in pivots if p["confirmed"] and p["kind"] == "H")
        n_low = sum(1 for p in pivots if p["confirmed"] and p["kind"] == "L")
        if (n_high >= 2 and n_low >= 2) or thr <= 0.02:
            return pivots, all_pivots, thr, atr
        thr = max(0.02, thr * 0.75)


def rail_at(rail: dict, x: float) -> float:
    return rail["p1"] + rail["slope"] * (x - rail["x1"])


def _fit_rail(points: list[tuple[int, float, str]], view: list[dict], upper: bool, tol: float) -> dict | None:
    """在同类已确认拐点两两连成的候选直线中打分选出轨道。

    候选线须满足：自第一个支点起，后续同类拐点都不越过该线超过 tol（轨道不被拐点穿越）。
    评分 = 触及拐点数 + 0.5×跨度占比 + 0.5×末支点新近度 − 2×两支点之间收盘越线的比例；同分取末支点更近的。
    旧算法固定取凸包最后一条边：上凸包末边斜率最小、下凸包末边斜率最大，几何上偏向「上轨降、下轨升」的收敛形态。
    """
    side = 1 if upper else -1
    n_view = len(view)
    points = points[-RAIL_RECENT_PIVOTS:]
    for min_span in (MIN_RAIL_SPAN, 1):
        best: tuple[tuple[float, int], dict] | None = None
        for a in range(len(points)):
            x1, p1, d1 = points[a]
            later = [(x, p, d) for x, p, d in points if x >= x1]
            for x2, p2, d2 in points[a + 1 :]:
                if x2 - x1 < min_span:
                    continue
                rail = {"x1": x1, "p1": p1, "day1": d1, "x2": x2, "p2": p2, "day2": d2, "slope": (p2 - p1) / (x2 - x1)}
                if any((p - rail_at(rail, x)) * side > tol * rail_at(rail, x) for x, p, _ in later):
                    continue
                touches = [d for x, p, d in later if abs(p - rail_at(rail, x)) <= tol * rail_at(rail, x)]
                breach = sum(
                    1 for k in range(x1, x2 + 1) if (view[k]["close"] - rail_at(rail, k)) * side > tol * rail_at(rail, k)
                )
                score = len(touches) + 0.5 * (x2 - x1) / n_view + 0.5 * x2 / max(1, n_view - 1) - 2.0 * breach / (x2 - x1 + 1)
                if best is None or (score, x2) > best[0]:
                    best = ((score, x2), {**rail, "touches": touches})
        if best is not None:
            return best[1]
    return None


def _theil_sen_slope(points: list[tuple[int, float, str]]) -> float | None:
    """拐点价格对下标的 Theil-Sen 稳健斜率（两两斜率的中位数）；不足 3 个拐点时返回 None。"""
    if len(points) < 3:
        return None
    slopes = sorted((p2 - p1) / (x2 - x1) for i, (x1, p1, _) in enumerate(points) for x2, p2, _ in points[i + 1 :] if x2 != x1)
    mid = len(slopes) // 2
    return slopes[mid] if len(slopes) % 2 else (slopes[mid - 1] + slopes[mid]) / 2


def _direction(slope_pct: float, flat_pct: float) -> int:
    return 1 if slope_pct > flat_pct else (-1 if slope_pct < -flat_pct else 0)


def _classify(su: float, sl: float, width_ratio: float, flat_pct: float) -> str:
    """su/sl 为上/下轨每根K线斜率（占现价 %），width_ratio = 今日轨道宽度 / 形态起点宽度，flat_pct 为走平阈值（%/根）。"""

    def rising(s: float) -> bool:
        return s > flat_pct

    def falling(s: float) -> bool:
        return s < -flat_pct

    if width_ratio < 0.8:  # 收敛
        if falling(su) and falling(sl):
            return "下降楔形"
        if rising(su) and rising(sl):
            return "上升楔形"
        if falling(su) and rising(sl):
            return "对称三角形"
        if falling(su):
            return "下降三角形"
        if rising(sl):
            return "上升三角形"
        return "收敛整理"
    if width_ratio > 1.25:
        return "扩散喇叭形"
    mid = (su + sl) / 2
    if rising(mid):
        return "上升通道"
    if falling(mid):
        return "下降通道"
    return "横盘箱体"


def compute_structure(view: list[dict], pivots: list[dict], cur_idx: int, ref: float, atr: float, live: dict | None) -> dict:
    """用已确认拐点的凸包切线拟合上/下轨，分类形态并判断收盘突破状态。

    cur_idx 是最新已收盘K线的下标，ref 是其收盘价；live 只用于在 status_text 末尾追加盘中提示。
    """
    tol = max(0.006, 0.6 * atr)
    highs = [(p["idx"], p["price"], p["day"]) for p in pivots if p["confirmed"] and p["kind"] == "H"]
    lows = [(p["idx"], p["price"], p["day"]) for p in pivots if p["confirmed"] and p["kind"] == "L"]
    upper = _fit_rail(highs, view, upper=True, tol=tol) if len(highs) >= 2 else None
    lower = _fit_rail(lows, view, upper=False, tol=tol) if len(lows) >= 2 else None

    if upper and lower:
        x0 = max(upper["x1"], lower["x1"])
        if rail_at(upper, x0) - rail_at(lower, x0) <= 0:
            upper = lower = None
    if upper is None or lower is None:
        # 拐点不足以拟合轨道：退化为近 20 日高低点水平箱体
        n = len(view)
        hi_i = max(range(n - 20, n), key=lambda k: (view[k]["high"], -k))
        lo_i = min(range(n - 20, n), key=lambda k: (view[k]["low"], k))
        hi_p, lo_p = float(money(view[hi_i]["high"])), float(money(view[lo_i]["low"]))
        upper = {"x1": hi_i, "p1": hi_p, "day1": view[hi_i]["day"], "x2": hi_i, "p2": hi_p, "day2": view[hi_i]["day"], "slope": 0.0, "touches": [view[hi_i]["day"]]}
        lower = {"x1": lo_i, "p1": lo_p, "day1": view[lo_i]["day"], "x2": lo_i, "p2": lo_p, "day2": view[lo_i]["day"], "slope": 0.0, "touches": [view[lo_i]["day"]]}
        pattern = "近20日高低箱体（有效拐点不足）"
        fitted = False
    else:
        fitted = True

    x0 = max(upper["x1"], lower["x1"])
    width0 = rail_at(upper, x0) - rail_at(lower, x0)
    width_now = rail_at(upper, cur_idx) - rail_at(lower, cur_idx)
    width_ratio = width_now / width0 if width0 > 0 else 1.0
    su = upper["slope"] / ref * 100
    sl = lower["slope"] / ref * 100
    flat_pct = FLAT_ATR_PER_BAR * atr * 100
    # Theil-Sen 交叉校验：轨道方向与自轨道起点以来同类拐点的稳健回归方向相反时，形态判为不明确
    fit_check: dict[str, Any] = {"flat_pct_per_bar": round(flat_pct, 4)}
    conflicts = []
    for key, rail, pts, slope_pct, label in (("upper", upper, highs, su, "上"), ("lower", lower, lows, sl, "下")):
        ts = _theil_sen_slope([pt for pt in pts if pt[0] >= rail["x1"]]) if fitted else None
        ts_pct = None if ts is None else ts / ref * 100
        consistent = ts_pct is None or _direction(slope_pct, flat_pct) * _direction(ts_pct, flat_pct) >= 0
        fit_check[key] = {
            "rail_slope_pct_per_bar": round(slope_pct, 4),
            "theil_sen_slope_pct_per_bar": None if ts_pct is None else round(ts_pct, 4),
            "consistent": consistent,
        }
        if not consistent:
            conflicts.append(label)
    if fitted:
        pattern = _classify(su, sl, width_ratio, flat_pct)
        if conflicts:
            pattern = f"形态不明确（{'、'.join(conflicts)}轨与拐点稳健回归方向相反）"

    apex_ahead = None
    apex_day = None
    if abs(upper["slope"] - lower["slope"]) > 1e-9:
        apex_x = upper["x1"] + (rail_at(lower, upper["x1"]) - upper["p1"]) / (upper["slope"] - lower["slope"])
        if apex_x > x0:
            apex_ahead = round(apex_x - cur_idx, 1)
            if apex_x <= len(view) - 1:
                apex_day = view[int(round(apex_x))]["day"]
    # 两轨已交汇：形态已完成，轨道外推值不再作为今日支撑/压力（只保留形态描述与突破方向）
    expired = width_now <= 0
    if fitted and expired:
        pattern = f"{pattern}（已越过收敛顶点）"

    # ---- 收盘突破状态：只用已收盘K线判定，盘中只做提示 ----
    last_i = len(view) - 1

    def side(i: int, price: float) -> int:
        u, l = rail_at(upper, i), rail_at(lower, i)
        if price > max(u, l):
            return 1
        if price < min(u, l):
            return -1
        return 0

    state = side(last_i, view[last_i]["close"])
    run = 0
    while last_i - run >= 0 and side(last_i - run, view[last_i - run]["close"]) == state:
        run += 1
    since_day = view[last_i - run + 1]["day"]
    u_last, l_last = rail_at(upper, last_i), rail_at(lower, last_i)
    if state == 1:
        status = "above"
        status_text = f"{since_day} 起收盘站上上轨，已连续 {run} 个交易日（向上突破）"
        status_short = f"{since_day[5:]}起收盘站上上轨·{run}日"
    elif state == -1:
        status = "below"
        status_text = f"{since_day} 起收盘跌破下轨，已连续 {run} 个交易日（向下突破）"
        status_short = f"{since_day[5:]}起收盘跌破下轨·{run}日"
    else:
        status = "inside"
        notes = []
        if view[last_i]["high"] > max(u_last, l_last):
            notes.append("最新K线上影线触及上轨后回落")
        if view[last_i]["low"] < min(u_last, l_last):
            notes.append("最新K线下影线刺破下轨后收回")
        status_text = "收盘仍在上下轨之间运行" + ("（" + "，".join(notes) + "）" if notes else "")
        status_short = "收盘在轨道内运行"
    if live:
        live_side = side(cur_idx + 1, live["close"])  # 盘中K线位于最新收盘日的下一个槽位
        if live_side != state:
            hint = {1: "盘中价已在上轨上方", -1: "盘中价已在下轨下方", 0: "盘中价已回到上下轨之间"}[live_side]
            status_text += f"；{hint}（未收盘，不作确认）"

    def pack(rail: dict) -> dict:
        return {
            "start": {"idx": rail["x1"], "day": rail["day1"], "price": rail["p1"]},
            "end": {"idx": rail["x2"], "day": rail["day2"], "price": rail["p2"]},
            "touches": rail["touches"],
        }

    return {
        "pattern": pattern,
        "fitted": fitted,
        "status": status,
        "status_text": status_text,
        "status_short": status_short,
        "upper": pack(upper),
        "lower": pack(lower),
        # 兼容 v3 字段：h1/h2 为上轨两个支点，l1/l2 为下轨两个支点
        "h1": {"idx": upper["x1"], "day": upper["day1"], "price": upper["p1"]},
        "h2": {"idx": upper["x2"], "day": upper["day2"], "price": upper["p2"]},
        "l1": {"idx": lower["x1"], "day": lower["day1"], "price": lower["p1"]},
        "l2": {"idx": lower["x2"], "day": lower["day2"], "price": lower["p2"]},
        "slope_upper_per_day": float(money(upper["slope"])),
        "slope_lower_per_day": float(money(lower["slope"])),
        "upper_today": float(money(rail_at(upper, cur_idx))),
        "lower_today": float(money(rail_at(lower, cur_idx))),
        "height_at_start": float(money(width0)),
        "width_ratio": round(width_ratio, 2),
        "apex_bars_ahead": apex_ahead,
        "apex_day": apex_day,
        "expired": expired,
        "fit_check": fit_check,
        "_upper": upper,
        "_lower": lower,
    }


# ---------------------------------------------------------------------------
# ABC 测算、黄金分割、缺口、支撑压力阶梯
# ---------------------------------------------------------------------------


def _abc_one(
    window: list[dict], live: dict | None, direction: str, decimals: int = 2, start_i: int | None = None
) -> dict | None:
    """单方向 ABC。down：A 浪自起点（缺省为窗口最高点）下跌；up：A 浪自起点（缺省为窗口最低点）上涨。"""
    s = -1 if direction == "down" else 1
    a_key = "low" if direction == "down" else "high"  # A 浪终点、C 浪推进方向用的价格
    b_key = "high" if direction == "down" else "low"  # B 浪反向极值用的价格
    n = len(window)
    if start_i is None:
        if direction == "down":
            start_i = max(range(n), key=lambda k: (money(window[k]["high"]), -k))
        else:
            start_i = min(range(n), key=lambda k: (money(window[k]["low"]), k))
    start_p = money(window[start_i][b_key])

    qualified = []
    for i in range(start_i + 1, n):
        a_end = money(window[i][a_key])
        a_range = (a_end - start_p) * s
        if a_range <= 0:
            continue
        b_p = b_i = None
        for j in range(i + 1, n):
            if (money(window[j][a_key]) - a_end) * s > 0:  # 再次越过 A 浪终点，B 浪结束
                break
            cand = money(window[j][b_key])
            if b_p is None or (b_p - cand) * s > 0:
                b_p, b_i = cand, j
        if b_p is None:
            continue
        b_size = (a_end - b_p) * s
        if b_size < q2(a_end * Decimal(str(REVERSAL))) or b_size < q2(a_range * Decimal(str(B_MIN_RETRACE))):
            continue
        if b_size > a_range * Decimal(str(B_MAX_RETRACE)):
            continue
        qualified.append((a_end * -s, i, a_end, a_range, b_p, b_i, b_size))
    if not qualified:
        return None
    qualified.sort(key=lambda row: (row[0], row[1]))  # 下跌取最低的 A 浪终点，上涨取最高的；同价取更早
    _, a_i, a_end, a_range, b_p, b_i, b_size = qualified[0]

    down = direction == "down"
    post = window[b_i + 1 :]
    last_close = money(window[-1]["close"])
    # 结构失效：收盘反向越过 A 浪起点，或 C 浪之后收盘反向越过 B 浪终点（C 浪已结束并反转）。
    # 情景是否存在、C 浪进度与测算位状态只看已收盘K线；盘中价只写入 intraday 标注（铁律 0）
    if (last_close - start_p) * s < 0 or (last_close - b_p) * s < 0:
        return None
    window_low = min(money(bar["low"]) for bar in window)
    window_high = max(money(bar["high"]) for bar in window)

    def level(ratio: float) -> dict:
        price = q2(b_p + s * q2(a_range * Decimal(str(ratio))))

        def beyond(x: Decimal) -> bool:  # 越过该位：下跌=低于，上涨=高于
            return (x - price) * s > 0

        if beyond(last_close):
            state = "收盘已跌破" if down else "收盘已站上"
        elif any(beyond(money(bar["close"])) for bar in post):
            state = "曾收破已收回" if down else "曾站上已回落"
        elif any((money(bar[a_key]) - price) * s >= 0 for bar in post):
            state = "影线触及未收破" if down else "影线触及未站上"
        else:
            state = "未触及"
        # 盘中越过该位只写标注，不改变收盘口径的 state
        intraday = ("盘中刺破" if down else "盘中触及") if live and not beyond(last_close) and beyond(money(live[a_key])) else None
        drawn = q2(window_low * Decimal("0.92")) <= price <= q2(window_high * Decimal("1.08"))
        return {"ratio": ratio, "price": float(price), "state": state, "intraday": intraday, "drawn": drawn}

    levels = [level(r) for r in RATIOS]
    if levels[-1]["state"] != "未触及":
        levels.append(level(EXT_RATIO))

    # C 浪进度：B 浪之后已收盘K线朝 A 浪方向走出的最远位置（不含盘中）
    c_points = [(money(bar[a_key]), b_i + 1 + k) for k, bar in enumerate(post)]
    if c_points:
        c_ext, c_idx = (min if down else max)(c_points, key=lambda item: item[0])
        c_ratio = float((c_ext - b_p) * s / a_range)
    else:
        c_ext, c_idx, c_ratio = b_p, b_i, 0.0
    if b_i >= n - 3 and c_ratio < 0.15:
        phase = "B浪可能仍在延续，C浪尚未确认"
    elif (c_ext - a_end) * s > 0:
        phase = f"C浪已越过A浪终点（{'创出新低' if down else '创出新高'}）"
    else:
        phase = "C浪进行中"
    reached = [lvl for lvl in levels if lvl["state"] != "未触及"]
    intraday_notes = []
    if live:
        if (money(live["close"]) - start_p) * s < 0:
            intraday_notes.append(f"盘中价已越过A浪起点 {fmt_price(float(start_p), decimals)}")
        elif (money(live["close"]) - b_p) * s < 0:
            intraday_notes.append(f"盘中价已反向越过B浪终点 {fmt_price(float(b_p), decimals)}")
        if (money(live[a_key]) - c_ext) * s > 0:
            intraday_notes.append(f"盘中{'最低' if down else '最高'} {fmt_price(float(money(live[a_key])), decimals)} 超出C浪已收盘最远位置")
    return {
        "direction": direction,
        "label": "下跌ABC" if down else "上涨ABC",
        "reversal": REVERSAL,
        "a_start": {"idx": start_i, "day": window[start_i]["day"], "price": float(start_p)},
        "a_end": {"idx": a_i, "day": window[a_i]["day"], "price": float(a_end)},
        "b_end": {"idx": b_i, "day": window[b_i]["day"], "price": float(b_p)},
        "a_range": float(a_range),
        "b_retrace": round(float(b_size / a_range), 3),
        "c_progress": {
            "idx": c_idx,
            "day": window[c_idx]["day"],
            "extreme": float(c_ext),
            "c_over_a": round(c_ratio, 3),
            "phase": phase,
            "deepest_level_reached": reached[-1]["ratio"] if reached else None,
        },
        "levels": levels,
        # 盘中标注：不参与情景筛选、C 浪进度与测算位状态
        "intraday_note": "；".join(intraday_notes) + "（未收盘，不作确认）" if intraday_notes else None,
        "invalidation": "日线收盘越过该线才算，盘中刺破不算",
        # 计数的否定条件：日线收盘反向越过 B 浪终点，说明 C 浪不成立（与上面的情景存在性判定同一口径）
        "count_invalidation": {
            "price": float(b_p),
            "text": f"日线收盘{'站上' if down else '跌破'} B 浪终点 {fmt_price(float(b_p), decimals)}，此计数失效",
        },
    }


def abc_scenario(window: list[dict], live: dict | None, decimals: int = 2) -> dict | None:
    """同时尝试下跌 ABC 与上涨 ABC，取 A 浪起点更近的一个（代表当前正在演绎的结构）。"""
    found = [s for s in (_abc_one(window, live, "down", decimals=decimals), _abc_one(window, live, "up", decimals=decimals)) if s]
    if not found:
        return None
    return max(found, key=lambda s: s["a_start"]["idx"])


def wave_counts(
    window: list[dict], live: dict | None, pivots: list[dict], primary: dict | None, atr: float, decimals: int = 2
) -> list[dict]:
    """主计数 + 最多 MAX_ALT_COUNTS 个备选计数。

    备选计数的 A 浪起点取窗口极值以及窗口内全部已确认拐点（下跌 ABC 用高点、上涨 ABC 用低点），
    A 浪终点与 B 浪终点都相同（只是起点不同）或 1.0 倍测算位相距 0.5% 以内的视为同一计数；按 A 浪幅度（ATR 倍数）从大到小取。
    """
    ref = float(window[-1]["close"])
    atr_abs = max(1e-9, atr * ref)
    cands: list[dict] = []
    for direction, kind in (("down", "H"), ("up", "L")):
        anchors = [None] + sorted({p["idx"] for p in pivots if p["confirmed"] and p["kind"] == kind and 0 <= p["idx"] < len(window)})
        for start_i in anchors:
            found = _abc_one(window, live, direction, decimals=decimals, start_i=start_i)
            if found:
                cands.append(found)

    def same(a: dict, b: dict) -> bool:
        if a["direction"] != b["direction"]:
            return False
        near = lambda x, y: abs(x - y) <= 0.005 * max(abs(y), 1e-9)  # noqa: E731
        # A 浪终点与 B 浪终点都相同，只是 A 浪起点不同，C 浪的否定条件一样，视为同一数法
        return (a["a_end"]["idx"] == b["a_end"]["idx"] and a["b_end"]["idx"] == b["b_end"]["idx"]) or near(
            a["levels"][2]["price"], b["levels"][2]["price"]
        )

    chosen: list[dict] = []
    if primary:
        chosen.append({**primary, "role": "主计数", "a_range_atr": round(primary["a_range"] / atr_abs, 1)})
    for cand in sorted(cands, key=lambda c: (-c["a_range"], -c["a_start"]["idx"])):
        if len(chosen) >= (1 if primary else 0) + MAX_ALT_COUNTS:
            break
        if any(same(cand, c) for c in chosen):
            continue
        chosen.append({**cand, "role": "备选计数", "a_range_atr": round(cand["a_range"] / atr_abs, 1)})
    return chosen


def fib_levels(hi_idx: int, hi_bar: dict, lo_idx: int, lo_bar: dict, ref_price: Decimal) -> dict:
    """窗口高低点之间的黄金分割。先高后低（最近一段是下跌）→ 反弹阻力位；先低后高 → 回撤支撑位。"""
    hi_p = money(hi_bar["high"])
    lo_p = money(lo_bar["low"])
    span = hi_p - lo_p
    falling = hi_idx < lo_idx
    levels = []
    for r in RETRACE_RATIOS:
        lvl = q2(lo_p + q2(span * Decimal(str(r)))) if falling else q2(hi_p - q2(span * Decimal(str(r))))
        levels.append(
            {
                "ratio": r,
                "price": float(lvl),
                "dist_pts": float(q2(lvl - ref_price)),
                "status": "上方压力" if lvl > ref_price else "下方支撑",
            }
        )
    return {
        "kind": "反弹位" if falling else "回撤位",
        "from_high": {"day": hi_bar["day"], "high": float(hi_p)},
        "to_low": {"day": lo_bar["day"], "low": float(lo_p)},
        "retracements": levels,
    }


def _ma_trend(series: list[float | None], lookback: int = 20) -> dict | None:
    if len(series) <= lookback or series[-1] is None or series[-1 - lookback] is None:
        return None
    chg = (series[-1] / series[-1 - lookback] - 1) * 100
    direction = "向上" if chg > TREND_FLAT_PCT else ("向下" if chg < -TREND_FLAT_PCT else "走平")
    return {"value": float(money(series[-1])), "chg_20d_pct": round(chg, 2), "direction": direction}


def background_context(bg: list[dict], ma: dict[int, list[float | None]], ref_price: Decimal) -> dict:
    """背景层：大级别区间位置、MA120/MA250 方向、长周期黄金分割。只做描述，不参与触发位/目标位。"""
    hi_idx, hi = max(enumerate(bg), key=lambda item: (money(item[1]["high"]), -item[0]))
    lo_idx, lo = min(enumerate(bg), key=lambda item: (money(item[1]["low"]), item[0]))
    hi_p, lo_p = money(hi["high"]), money(lo["low"])
    pct = float(q2((ref_price - lo_p) / (hi_p - lo_p) * Decimal("100"))) if hi_p > lo_p else 50.0
    ma250 = _ma_trend(ma[250])
    if ma250:
        ma250["diff_pct"] = float(q2((ref_price / money(ma250["value"]) - 1) * Decimal("100")))
    return {
        "days": len(bg),
        "start_day": bg[0]["day"],
        "end_day": bg[-1]["day"],
        "high": {"day": hi["day"], "price": float(hi_p)},
        "low": {"day": lo["day"], "price": float(lo_p)},
        "percentile": pct,
        "ma120": _ma_trend(ma[120]),
        "ma250": ma250,
        "fib": fib_levels(hi_idx, hi, lo_idx, lo, ref_price),
        "note": f"只描述大级别位置，不参与触发位/失效位/目标位；短线结构与三条推演只基于近{STRUCT_DAYS}日",
    }


def find_unfilled_gaps(view: list[dict], lookback: int = 60) -> list[dict]:
    """只用已收盘K线判断缺口是否回补。"""
    sub = view[-lookback:] if len(view) > lookback else view
    gaps = []
    for i in range(1, len(sub)):
        prev_b, curr_b = sub[i - 1], sub[i]
        if money(curr_b["high"]) < money(prev_b["low"]):
            upper = money(prev_b["low"])
            lower = max(
                [money(curr_b["high"])]
                + [money(b["high"]) for b in sub[i + 1 :] if money(b["high"]) > money(curr_b["high"])]
            )
            if lower < upper:
                gaps.append({"day": curr_b["day"], "type": "下跳空缺口(上方压力)", "range": [float(lower), float(upper)]})
        elif money(curr_b["low"]) > money(prev_b["high"]):
            lower = money(prev_b["high"])
            upper = min(
                [money(curr_b["low"])]
                + [money(b["low"]) for b in sub[i + 1 :] if money(b["low"]) < money(curr_b["low"])]
            )
            if lower < upper:
                gaps.append({"day": curr_b["day"], "type": "上跳空缺口(下方支撑)", "range": [float(lower), float(upper)]})
    return gaps[-4:]


def build_ladder(
    ref: float,
    pivots: list[dict],
    mas: dict[int, float | None],
    fibs: dict,
    scenario: dict | None,
    gaps: list[dict],
    struct: dict,
    window_hi: dict,
    window_lo: dict,
    view_first_day: str,
) -> dict:
    """把所有客观价位汇总成「上方压力 / 下方支撑」阶梯（近者在前），相距 0.25% 以内的合并来源。

    pivots 用全部历史拐点（约 1.7 年），这样指数创窗口新高/新低时仍能找到更早的前高/前低作为目标。
    """
    items: list[tuple[float, str]] = []
    for p in pivots:
        if p["confirmed"]:
            tag = "前高" if p["kind"] == "H" else "前低"
        else:
            tag = "近期高点" if p["kind"] == "H" else "近期低点"
        day = p["day"][5:] if p["day"] >= view_first_day else p["day"]  # 窗口外的拐点写全年份
        items.append((p["price"], f"{tag} {day}"))
    items.append((window_hi["high"], f"近{STRUCT_DAYS}日最高 {window_hi['day'][5:]}"))
    items.append((window_lo["low"], f"近{STRUCT_DAYS}日最低 {window_lo['day'][5:]}"))
    for n, value in mas.items():
        if value:
            items.append((value, f"MA{n}"))
    for lvl in fibs["retracements"]:
        items.append((lvl["price"], f"{lvl['ratio']:.3f}{fibs['kind']}"))
    if scenario:
        for lvl in scenario["levels"]:
            items.append((lvl["price"], f"ABC {lvl['ratio']:.3f}倍位"))
    for g in gaps:
        items.append((g["range"][0], f"缺口下沿 {g['day'][5:]}"))
        items.append((g["range"][1], f"缺口上沿 {g['day'][5:]}"))
    if not struct["expired"]:
        u, l = struct["upper_today"], struct["lower_today"]
        items.append((u, "形态上轨" if u > ref else "已突破的上轨(回踩支撑)"))
        items.append((l, "形态下轨" if l < ref else "已跌破的下轨(反压)"))

    def merge(rows: list[tuple[float, str]]) -> list[dict]:
        out: list[dict] = []
        for price, src in rows:
            if out and abs(price - out[-1]["price"]) <= out[-1]["price"] * 0.0025:
                if src not in out[-1]["sources"] and len(out[-1]["sources"]) < 3:
                    out[-1]["sources"].append(src)
                continue
            out.append({"price": float(money(price)), "sources": [src]})
        return out

    above = merge(sorted((p, s) for p, s in items if p > ref * 1.0005))
    below = merge(sorted(((p, s) for p, s in items if p < ref * 0.9995), reverse=True))
    return {"resistances": above, "supports": below}


def _src(level: dict) -> str:
    return "/".join(level["sources"])


# 「重要位」：形态轨道、ABC 测算位、近120日极值、当前波段极值、均线、黄金分割位。普通前高前低与缺口只作目标参考。
MAJOR_SOURCE_PREFIXES = ("形态", "已跌破", "已突破", "ABC", f"近{STRUCT_DAYS}日", "近期", "MA", "0.")


def _is_major(level: dict) -> bool:
    return any(src.startswith(MAJOR_SOURCE_PREFIXES) for src in level["sources"])


def _trigger(
    levels: list[dict], rails: list[tuple[float, str]], ref: float, cap: float, min_dist: float, fallback: float, fallback_src: str
) -> tuple[float, str]:
    """触发位优先级：距现价 [min_dist, cap] 内的形态轨道 → 该范围（cap 再放宽到 2×cap）内最近的重要位 → 最近的任意位 → ATR 推算。

    距现价不足 min_dist 的价位落在日常波动噪声里，作为「正在争夺的价位」单独披露，不作触发位。
    """
    def far_enough(price: float) -> bool:
        return abs(price / ref - 1) >= min_dist

    near_rails = [r for r in rails if far_enough(r[0]) and abs(r[0] / ref - 1) <= cap]
    if near_rails:
        return near_rails[0]
    for limit in (cap, 2 * cap):
        major = next((lvl for lvl in levels if _is_major(lvl) and far_enough(lvl["price"]) and abs(lvl["price"] / ref - 1) <= limit), None)
        if major:
            return major["price"], _src(major)
    rest = next((lvl for lvl in levels if far_enough(lvl["price"])), None)
    if rest:
        return rest["price"], _src(rest)
    return fallback, fallback_src


def _diffusion_bars(price: float, ref: float, sigma: float) -> float:
    """按随机游走扩散尺度估计走完 |price/ref−1| 所需的K线根数：t ≈ (距离 / 日波动)²。"""
    return (abs(price / ref - 1) / max(sigma, 1e-6)) ** 2


def _path_slots(trigger_eta: float, target_eta: float, horizon: int) -> tuple[int, int, int]:
    """路径三个折点（触发、回踩/反抽、目标）的时间槽：取扩散尺度估计，截断在预测期 horizon 以内且严格递增。"""
    if horizon < 3:
        return horizon, horizon, horizon
    t3 = min(horizon, max(3, round(target_eta)))
    t1 = min(t3 - 2, max(1, round(trigger_eta)))
    t2 = min(t3 - 1, max(t1 + 1, round((t1 + t3) / 2)))
    return t1, t2, t3


def compute_projections(
    ref: float,
    struct: dict,
    ladder: dict,
    atr: float,
    horizon_bars: int,
    sigma: float,
    live_price: float | None = None,
    decimals: int = 2,
) -> dict:
    """三条规则推演路径。触发位 = 现价上/下方最近的形态轨道（太远则改用最近的重要位），目标位 = 阶梯里下一个有效位。

    ref 是最新已收盘价：三条路径都以日线收盘确认，所以选位以收盘价为基准；盘中时折线从盘中价出发，
    盘中价已越过触发位的，在触发条件里注明「待收盘确认」。
    触发位至少离现价 MIN_TRIGGER_ATR 个 ATR；折点时间按扩散尺度 (距离/σ)² 估计并截断在 horizon_bars 以内，
    未截断的估计值写入 eta_bars，超出预测期时 target_within_horizon = False。
    """
    above, below = ladder["resistances"], ladder["supports"]
    start = live_price if live_price is not None else ref
    u, l = struct["upper_today"], struct["lower_today"]
    live_rails = () if struct["expired"] else (u, l)
    rails_up = sorted((p, "形态上轨" if p == u else "已跌破的下轨(反压)") for p in live_rails if p > ref)
    rails_dn = sorted(((p, "形态下轨" if p == l else "已突破的上轨(回踩支撑)") for p in live_rails if p < ref), reverse=True)
    cap = max(0.04, 3 * atr)  # 触发位离现价超过 max(4%, 3×ATR) 就失去短线参考意义
    min_dist = MIN_TRIGGER_ATR * atr
    r1, r1_src = _trigger(above, rails_up, ref, cap, min_dist, ref * (1 + atr), "1倍ATR上沿")
    s1, s1_src = _trigger(below, rails_dn, ref, cap, min_dist, ref * (1 - atr), "1倍ATR下沿")
    r1, s1 = float(money(r1)), float(money(s1))
    contested = [
        {"price": lvl["price"], "sources": lvl["sources"], "side": side}
        for side, levels in (("above", above), ("below", below))
        for lvl in levels
        if abs(lvl["price"] / ref - 1) < min_dist
    ]
    contested += [
        {"price": float(money(p)), "sources": [src], "side": side}
        for side, rails in (("above", rails_up), ("below", rails_dn))
        for p, src in rails
        if abs(p / ref - 1) < min_dist and not any(abs(c["price"] - p) <= p * 0.0025 for c in contested)
    ]

    gap = max(0.01, 0.8 * atr)  # 目标位至少离触发位这么远，才算「下一个」有效位
    height = struct["height_at_start"] if struct["fitted"] else 0.0
    bull_next = next((lvl for lvl in above if lvl["price"] > r1 * (1 + gap)), None)
    if bull_next:
        bull_target, bull_src = bull_next["price"], _src(bull_next)
    else:
        bull_target = r1 + min(height, r1 * 3 * atr) if height > 0 else r1 * (1 + 3 * atr)
        bull_src = "上方无前高/均线阻力，按形态高度或3倍ATR推算"
    bear_next = next((lvl for lvl in below if lvl["price"] < s1 * (1 - gap)), None)
    if bear_next:
        bear_target, bear_src = bear_next["price"], _src(bear_next)
    else:
        bear_target = s1 - min(height, s1 * 3 * atr) if height > 0 else s1 * (1 - 3 * atr)
        bear_src = "下方无前低/测算支撑，按形态高度或3倍ATR推算"
    bull_target, bear_target = float(money(bull_target)), float(money(bear_target))

    band = 0.4 * atr  # 回踩/反抽的容忍带随波动率缩放
    bull_retest = float(money(r1 * (1 - band)))
    bear_rebound = float(money(s1 * (1 + band)))
    neutral_high = float(money(min(r1, max(ref * (1 + 0.6 * atr), (ref + r1) / 2))))
    neutral_low = float(money(max(s1, min(ref * (1 - 0.6 * atr), (ref + s1) / 2))))
    neutral_mid = float(money((neutral_high + neutral_low) / 2))

    def fp(value: float) -> str:
        return fmt_price(value, decimals)

    r1_note = "（盘中已站上，待收盘确认）" if live_price is not None and live_price > r1 else ""
    s1_note = "（盘中已跌破，待收盘确认）" if live_price is not None and live_price < s1 else ""

    horizon = max(1, int(horizon_bars))
    eta = {
        "bull": (_diffusion_bars(r1, ref, sigma), _diffusion_bars(bull_target, ref, sigma)),
        "bear": (_diffusion_bars(s1, ref, sigma), _diffusion_bars(bear_target, ref, sigma)),
    }
    b1, b2, b3 = _path_slots(*eta["bull"], horizon)
    d1, d2, d3 = _path_slots(*eta["bear"], horizon)
    if horizon < 3:
        n1 = n2 = horizon
    else:
        n1 = min(horizon - 2, max(1, round(_diffusion_bars(neutral_high, ref, sigma))))
        n2 = min(horizon - 1, max(n1 + 1, round(n1 + _diffusion_bars(neutral_low, neutral_high, sigma))))

    def eta_info(key: str) -> dict:
        trig, tgt = eta[key]
        return {"trigger": round(trig, 1), "target": round(tgt, 1)}

    return {
        "atr_pct": round(atr * 100, 2),
        "sigma_1d_pct": round(sigma * 100, 3),
        "horizon_bars": horizon,
        "min_trigger_distance_atr": MIN_TRIGGER_ATR,
        "timing_method": "按扩散尺度 t≈(距离/日波动σ)² 估计折点时间，截断在预测期内",
        "breakout_level": {"price": r1, "source": r1_src},
        "breakdown_level": {"price": s1, "source": s1_src},
        "contested_levels": sorted(contested, key=lambda c: c["price"], reverse=True),
        "bull": {
            "name": "上攻突破路径",
            "trigger": f"日线放量收盘站上 {fp(r1)}（{r1_src}）{r1_note}",
            "retest_hold": bull_retest,
            "target_price": bull_target,
            "target_source": bull_src,
            "invalidation": f"突破后收盘跌回 {fp(bull_retest)} 下方视为假突破",
            "eta_bars": eta_info("bull"),
            "target_within_horizon": round(eta["bull"][1], 1) <= horizon,  # 与 eta_bars 公布的取整值一致
            "waypoints_offset": [(0, start), (b1, r1), (b2, bull_retest), (b3, bull_target)],
            "step_labels": [(b1, f"突破 {fp(r1)}"), (b2, f"回踩 {fp(bull_retest)}")],
        },
        "neutral": {
            "name": "区间震荡路径",
            "range": [neutral_low, neutral_high],
            "target_price": neutral_mid,
            "condition": f"收盘既未站上 {fp(r1)} 也未跌破 {fp(s1)}",
            "waypoints_offset": [(0, start), (n1, neutral_high), (n2, neutral_low), (horizon, neutral_mid)],
            "step_labels": [(n1, f"受阻 {fp(neutral_high)}"), (n2, f"企稳 {fp(neutral_low)}")],
        },
        "bear": {
            "name": "下探破位路径",
            "trigger": f"日线收盘跌破 {fp(s1)}（{s1_src}）{s1_note}",
            "rebound_cap": bear_rebound,
            "target_price": bear_target,
            "target_source": bear_src,
            "invalidation": f"跌破后收盘重新站回 {fp(bear_rebound)} 上方视为假跌破",
            "eta_bars": eta_info("bear"),
            "target_within_horizon": round(eta["bear"][1], 1) <= horizon,  # 与 eta_bars 公布的取整值一致
            "waypoints_offset": [(0, start), (d1, s1), (d2, bear_rebound), (d3, bear_target)],
            "step_labels": [(d1, f"破位 {fp(s1)}"), (d2, f"反抽 {fp(bear_rebound)}")],
        },
    }


def compute_structure_layer(
    bars: list[dict],
    intraday: dict | None,
    show: int,
    price_decimals: Callable[[float, str], int] | None = None,
    horizon_bars: int = 15,
    benchmark_bars: list[dict] | None = None,
) -> dict[str, Any]:
    """一次性计算结构层全部结果，供 JSON 报告与 PNG 渲染共用。

    结构窗口固定为 HISTORY_BARS / STRUCT_DAYS，与展示根数 show 无关；show 只影响 view、背景层与展示用波段骨架。
    horizon_bars 是预测期：推演折点都落在预测期以内，PNG 推演区宽度也随它而定。
    price_decimals(最新已收盘价, 当日) -> 价格文本小数位，由知道市场与品种的调用方传入（与报告同一精度）；
    缺省时按 A 股股票口径 2 位。
    """
    completed, active_intra, data_note = split_live_bar(bars, intraday)
    closes = [float(bar["close"]) for bar in completed]
    ma = {n: sma(closes, n) for n in (20, 60, 120, 250)}
    view = completed[-show:]
    view_start = len(completed) - len(view)
    hist = completed[-HISTORY_BARS:]
    sview = hist[-STRUCT_DAYS:]
    off = len(view) - len(sview)  # 结构窗口下标 + off = 主图下标（show < STRUCT_DAYS 时为负）
    last_bar_idx = len(view) - 1 + (1 if active_intra else 0)
    last_c = money(view[-1]["close"])
    ref_p = money(active_intra["close"]) if active_intra else last_c
    decimals = price_decimals(float(last_c), view[-1]["day"]) if price_decimals else 2
    struct_slots = len(sview) + (1 if active_intra else 0)
    future_slots = max(18, int(horizon_bars) + 6)  # 推演区：预测期 + 右侧标签留白
    log_rets = [math.log(b["close"] / a["close"]) for a, b in zip(completed[-SIGMA_LOOKBACK - 1 : -1], completed[-SIGMA_LOOKBACK:])]
    sigma = math.sqrt(sum(r * r for r in log_rets) / len(log_rets)) if log_rets else 0.015

    s_hi_idx, s_hi = max(enumerate(sview), key=lambda item: (money(item[1]["high"]), -item[0]))
    s_lo_idx, s_lo = min(enumerate(sview), key=lambda item: (money(item[1]["low"]), item[0]))
    pivots, all_pivots, zz_thr, atr = swing_pivots(hist, len(hist) - len(sview))
    # 轨道、ABC、缺口、黄金分割与背景层只用已收盘K线（铁律 0）：盘中K线只进 status_text / intraday_note 标注及折线起点
    wedge = compute_structure(sview, pivots, len(sview) - 1, float(last_c), atr, active_intra)
    scenario = abc_scenario(sview, active_intra, decimals=decimals)
    counts = wave_counts(sview, active_intra, pivots, scenario, atr, decimals=decimals)
    fibs = fib_levels(s_hi_idx, s_hi, s_lo_idx, s_lo, last_c)
    gaps = find_unfilled_gaps(sview)
    ma_last = {n: None if ma[n][-1] is None else float(money(ma[n][-1])) for n in (20, 60, 120)}
    background = background_context(completed[-max(show, BACKGROUND_MIN_DAYS) :], ma, last_c)
    ladder = build_ladder(
        float(last_c),  # 支撑压力以最新收盘价为基准（路径都以日线收盘确认）
        all_pivots,
        ma_last,
        fibs,
        scenario,
        gaps,
        wedge,
        {"day": s_hi["day"], "high": float(money(s_hi["high"]))},
        {"day": s_lo["day"], "low": float(money(s_lo["low"]))},
        sview[0]["day"],
    )
    projections = compute_projections(
        float(last_c),
        wedge,
        ladder,
        atr,
        horizon_bars,
        sigma,
        live_price=float(ref_p) if active_intra else None,
        decimals=decimals,
    )
    scen_bundle = build_scenarios_with_base_rates(
        hist=hist,
        sview=sview,
        ref=float(last_c),
        live_price=float(ref_p) if active_intra else None,
        struct=wedge,
        ladder=ladder,
        pivots=pivots,
        scenario_abc=scenario,
        wave_counts=counts,
        fibs=fibs,
        ma_last=ma_last,
        atr=atr,
        sigma=sigma,
        horizon_bars=horizon_bars,
        base_projections=projections,
        decimals=decimals,
        benchmark_bars=benchmark_bars,
    )
    projections["bull"]["historical_frequency"] = scen_bundle["bull_frequency"]
    projections["neutral"]["historical_frequency"] = scen_bundle["neutral_frequency"]
    projections["bear"]["historical_frequency"] = scen_bundle["bear_frequency"]
    projections["scenarios"] = scen_bundle["scenarios"]
    projections["scenario_count"] = scen_bundle["scenario_count"]
    projections["scenario_signature"] = scen_bundle["scenario_signature"]
    projections["impulse_wave"] = scen_bundle["impulse_wave"]
    # 展示用波段骨架：同一阈值在全部已收盘K线上的 ZigZag，下标换算到主图
    display_pivots = [dict(p, idx=p["idx"] - view_start) for p in zigzag(completed, zz_thr) if p["idx"] >= view_start]
    return {
        "completed": completed,
        "active_intra": active_intra,
        "data_note": data_note,
        "ma": ma,
        "view": view,
        "view_start": view_start,
        "hist": hist,
        "sview": sview,
        "off": off,
        "last_bar_idx": last_bar_idx,
        "last_c": last_c,
        "ref_p": ref_p,
        "price_decimals": decimals,
        "future_slots": future_slots,
        "struct_slots": struct_slots,
        "s_hi": (s_hi_idx, s_hi),
        "s_lo": (s_lo_idx, s_lo),
        "pivots": pivots,
        "all_pivots": all_pivots,
        "zz_thr": zz_thr,
        "atr": atr,
        "wedge": wedge,
        "scenario": scenario,
        "wave_counts": counts,
        "fibs": fibs,
        "gaps": gaps,
        "ma_last": ma_last,
        "background": background,
        "ladder": ladder,
        "projections": projections,
        "display_pivots": display_pivots,
    }
