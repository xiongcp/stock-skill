"""多次测试价区：动态识别 N 重底 / N 重顶、头肩底 / 头肩顶与平台，并与上一根已收盘K线比较变化。

描述性实验字段（`structure.tested_zones`），不参与规则分、评级、动作、否决与计划价位。
只读已收盘K线；盘中K线只用于 `intraday_note` 标注（铁律 0）。
规则与常量见 `references/math-patterns.md` 第 9 节。
"""
from __future__ import annotations

import math
from typing import Any, Callable

from structure import HISTORY_BARS, STRUCT_DAYS, atr_pct, money, swing_pivots, zigzag

W_TOL_ATR = 0.65          # 价区宽度上限（沿用双底"两低点差 ≤ 0.65×ATR"）
BREAK_ATR = 0.5           # 破位距离（沿用情景库与 MIN_TRIGGER_ATR）
NECK_MIN_ATR = 1.2        # 颈线最小高度（沿用双底"颈线 − 底 ≥ 1.2×ATR"）
FALSE_BREAK_BARS = 3      # 破位后 3 根内收回才算假破位
RECENT_BARS = 20          # 近期窗口（沿用可引用价位"最近 20 根K线"）
MAX_ZONES_PER_SIDE = 3    # 每级别每侧最多输出的价区数
HS_NECK_SLOPE_MAX = 0.5   # 头肩两颈线点之差 ≤ 0.5 × 形态高度
MAX_TOUCHES = 8           # 触点列表上限：超过时列第 1 个与最后 7 个
MAX_CHANGES = 12          # 变化最多输出条数（主级别优先）
MINOR_STEP, MINOR_MIN, MINOR_MAX = 0.005, 0.015, 0.04

SIDE = {"L": "SUPPORT", "H": "RESISTANCE"}


def minor_threshold(atr_frac: float, major_thr: float) -> float | None:
    """次级别 ZigZag 阈值：ATR% 按 0.5% 向上取整，夹在 1.5%~4%；不小于主级别阈值时返回 None（跳过本级别）。"""
    thr = min(MINOR_MAX, max(MINOR_MIN, math.ceil(atr_frac / MINOR_STEP - 1e-9) * MINOR_STEP))
    return None if thr >= major_thr - 1e-12 else thr


def _recent(idx: int | None, n: int) -> bool:
    return idx is not None and idx >= n - RECENT_BARS


def _clusters(bars: list[dict], piv: list[dict], kind: str, atr: float) -> list[list[dict]]:
    """按时间顺序把同类拐点聚成价区（规格 3.1）：宽度 ≤ W_TOL，且两触点之间没有收盘越过已确认边缘 BRK 以上。"""
    sgn = 1 if kind == "L" else -1
    groups: list[list[dict]] = []
    for p in (q for q in piv if q["kind"] == kind):
        best = None
        for g in groups:
            prices = [t["price"] for t in g] + [p["price"]]
            width = max(prices) - min(prices)
            if width > W_TOL_ATR * atr:
                continue
            # g 里都是已确认触点：未确认拐点只可能是 ZigZag 的最后一个拐点
            edge = min(t["price"] for t in g) if kind == "L" else max(t["price"] for t in g)
            if any(sgn * (b["close"] - edge) < -BREAK_ATR * atr for b in bars[g[-1]["idx"] + 1 : p["idx"]]):
                continue
            key = (width, -g[-1]["idx"])
            if best is None or key < best[0]:
                best = (key, g)
        if best:
            best[1].append(p)
        else:
            groups.append([p])
    return groups


def _zone_state(bars: list[dict], z: dict) -> tuple[str, int | None]:
    """规格 3.6：从最后一个已确认触点的下一根起扫描收盘价，返回 (状态, 进入该状态的K线下标)。"""
    n, kind = len(bars), z["kind"]
    beyond = (lambda c: c < z["break"]) if kind == "L" else (lambda c: c > z["break"])
    back = (lambda c: c >= z["low"]) if kind == "L" else (lambda c: c <= z["high"])
    k, last_fb = z["last_conf_idx"] + 1, None
    while k < n:
        if beyond(bars[k]["close"]):
            r = next((j for j in range(k + 1, n) if back(bars[j]["close"])), None)
            if r is None:
                return "BROKEN", k
            if r - k > FALSE_BREAK_BARS:
                return "EXPIRED", r
            last_fb, k = r, r + 1
            continue
        k += 1
    if _recent(last_fb, n):
        return "FALSE_BREAK", last_fb
    crossed = (lambda c: c > z["neck"]) if kind == "L" else (lambda c: c < z["neck"])
    if crossed(bars[-1]["close"]):
        j = n - 1
        while j > z["last_idx"] and crossed(bars[j - 1]["close"]):
            j -= 1
        return "NECKLINE_CROSSED", j
    return ("TESTING" if z["unc"] else "HOLDING"), None


def _build_zone(bars: list[dict], touches: list[dict], kind: str, atr: float, thr: float) -> tuple[dict | None, str | None]:
    """由一组触点算价区几何量与状态（规格 3.2~3.6）。不满足输出条件时返回 (None, 原因)。"""
    if len(touches) < 2:
        return None, "TOUCH_REPLACED"
    if not touches[0]["confirmed"]:
        return None, "PIVOT_GONE"
    conf = [t for t in touches if t["confirmed"]]
    unc = touches[-1] if not touches[-1]["confirmed"] else None
    low, high = min(t["price"] for t in conf), max(t["price"] for t in conf)
    mid = bars[touches[0]["idx"] + 1 : touches[-1]["idx"]]
    neck = max(b["high"] for b in mid) if kind == "L" else min(b["low"] for b in mid)
    height = neck - low if kind == "L" else high - neck
    if height < NECK_MIN_ATR * atr:
        return None, "HEIGHT"
    seq = [t["price"] for t in touches]
    z = {
        "kind": kind,
        "touches": touches,
        "unc": unc,
        "low": low,
        "high": high,
        "neck": neck,
        "height": height,
        "measured": neck + height if kind == "L" else neck - height,
        "break": low - BREAK_ATR * atr if kind == "L" else high + BREAK_ATR * atr,
        "confirm": (unc["price"] * (1 + thr) if kind == "L" else unc["price"] * (1 - thr)) if unc else None,
        "last_idx": touches[-1]["idx"],
        "last_conf_idx": conf[-1]["idx"],
        "sequence": "RISING" if all(b > a for a, b in zip(seq, seq[1:]))
        else "FALLING" if all(b < a for a, b in zip(seq, seq[1:])) else "MIXED",
        "first_last_atr": (seq[-1] - seq[0]) / atr,
    }
    z["state"], z["since_idx"] = _zone_state(bars, z)
    if z["state"] == "EXPIRED":
        return None, "EXPIRED"
    if z["state"] == "BROKEN" and not _recent(z["since_idx"], len(bars)):
        return None, "BROKEN_STALE"
    return z, None


def _select(zones: list[dict], bars: list[dict]) -> list[tuple[dict, list[str]]]:
    """规格 3.7：每侧取离收盘最近的未破位价区，加上最近 20 根内有触点或状态变化的价区，最多 3 个。"""
    n, close = len(bars), bars[-1]["close"]
    out: list[tuple[dict, list[str]]] = []
    for kind in ("L", "H"):
        side = [z for z in zones if z["kind"] == kind]
        active = [z for z in side if z["state"] != "BROKEN"]
        nearest = min(active, key=lambda z: max(0.0, z["low"] - close, close - z["high"]), default=None)
        recent = sorted((z for z in side if _recent(z["last_idx"], n) or _recent(z["since_idx"], n)), key=lambda z: -z["last_idx"])
        recent_ids = {id(z) for z in recent}
        picked: list[tuple[dict, list[str]]] = []
        for z in ([nearest] if nearest else []) + recent:
            reasons = (["NEAREST_ACTIVE"] if z is nearest else []) + (["RECENT"] if id(z) in recent_ids else [])
            if all(z is not p for p, _ in picked):
                picked.append((z, reasons))
        out += picked[:MAX_ZONES_PER_SIDE]
    return out


def _platform(selected: list[dict], atr: float) -> dict | None:
    """规格 5：同一级别已筛选的支撑价区与压力价区，时间重叠、间距 ≥ 1.2 ATR；取两者末触点中较早者最近的一对。"""
    ok = ("TESTING", "HOLDING", "FALSE_BREAK")
    best = None
    for s in (z for z in selected if z["kind"] == "L" and z["state"] in ok):
        for r in (z for z in selected if z["kind"] == "H" and z["state"] in ok):
            if max(s["touches"][0]["idx"], r["touches"][0]["idx"]) > min(s["last_idx"], r["last_idx"]):
                continue
            if r["low"] - s["high"] < NECK_MIN_ATR * atr:
                continue
            key = min(s["last_idx"], r["last_idx"])
            if best is None or key > best[0]:
                best = (key, s, r)
    return None if best is None else {"support": best[1], "resistance": best[2], "gap_atr": (best[2]["low"] - best[1]["high"]) / atr}


def _head_shoulders(bars: list[dict], piv: list[dict], kind: str, atr: float) -> dict | None:
    """规格 4：结构窗口内最后三个同类拐点 S1、H、S2。"""
    same = [p for p in piv if p["kind"] == kind]
    if len(same) < 3:
        return None
    s1, head, s2 = same[-3:]
    if kind == "L":
        head_ok = head["price"] < min(s1["price"], s2["price"]) - W_TOL_ATR * atr
    else:
        head_ok = head["price"] > max(s1["price"], s2["price"]) + W_TOL_ATR * atr
    if not head_ok or abs(s1["price"] - s2["price"]) > W_TOL_ATR * atr:
        return None
    key = "high" if kind == "L" else "low"
    pick = max if kind == "L" else min
    a = pick(range(s1["idx"] + 1, head["idx"]), key=lambda i: bars[i][key])
    b = pick(range(head["idx"] + 1, s2["idx"]), key=lambda i: bars[i][key])

    def line(x: float) -> float:
        return bars[a][key] + (bars[b][key] - bars[a][key]) * (x - a) / (b - a)

    height = abs(line(head["idx"]) - head["price"])
    if height < NECK_MIN_ATR * atr or abs(bars[b][key] - bars[a][key]) > HS_NECK_SLOPE_MAX * height:
        return None
    n, close = len(bars), bars[-1]["close"]
    neck_today = line(n - 1)
    after = bars[s2["idx"] + 1 :]
    if kind == "L":
        failed = any(x["close"] < head["price"] - BREAK_ATR * atr for x in after)
        crossed = close > neck_today
    else:
        failed = any(x["close"] > head["price"] + BREAK_ATR * atr for x in after)
        crossed = close < neck_today
    return {
        "kind": kind, "s1": s1, "head": head, "s2": s2,
        "n1": {"day": bars[a]["day"], "price": bars[a][key]}, "n2": {"day": bars[b]["day"], "price": bars[b][key]},
        "neck_today": neck_today, "height": height,
        "measured": neck_today + height if kind == "L" else neck_today - height,
        "state": "FAILED" if failed else "NECKLINE_CROSSED" if crossed else "FORMING",
    }


def scan_zones(hist: list[dict]) -> dict:
    """截至 hist 最后一根已收盘K线，算两个级别的全部价区、筛选结果、头肩与平台。

    返回未取整的内部结果（compute_tested_zones 与测试使用）：scales[级别] = {thr, skipped, zones{zone_id: 价区},
    rejected{zone_id: 不输出的原因}, selected[(zone_id, 入选原因)], hs[], platform}。
    """
    n = len(hist)
    start = n - min(n, STRUCT_DAYS)
    atr_frac = atr_pct(hist)
    atr = atr_frac * hist[-1]["close"]
    _, _, major_thr, _ = swing_pivots(hist, start)
    out = {"day": hist[-1]["day"], "window_start_day": hist[start]["day"], "atr": atr, "scales": {}}
    for scale, thr in (("MAJOR", major_thr), ("MINOR", minor_threshold(atr_frac, major_thr))):
        raw_thr = thr if thr is not None else min(MINOR_MAX, max(MINOR_MIN, math.ceil(atr_frac / MINOR_STEP - 1e-9) * MINOR_STEP))
        sc: dict[str, Any] = {"thr": raw_thr, "skipped": thr is None, "zones": {}, "rejected": {}, "selected": [], "hs": [], "platform": None}
        out["scales"][scale] = sc
        if thr is None:
            continue
        piv = [p for p in zigzag(hist, thr) if p["idx"] >= start]
        valid = []
        for kind in ("L", "H"):
            for touches in _clusters(hist, piv, kind, atr):
                zid = f"{scale}-{SIDE[kind]}-{touches[0]['day']}"
                z, why = _build_zone(hist, touches, kind, atr, thr)
                if z is None:
                    sc["rejected"][zid] = why
                    continue
                z["zone_id"] = zid
                sc["zones"][zid] = z
                valid.append(z)
        picked = _select(valid, hist)
        sc["selected"] = [(z["zone_id"], why) for z, why in picked]
        sc["platform"] = _platform([z for z, _ in picked], atr)
        for kind in ("L", "H"):
            hs = _head_shoulders(hist, piv, kind, atr)
            if hs:
                hs["hs_id"] = f"{scale}-{'HS_BOTTOM' if kind == 'L' else 'HS_TOP'}-{hs['s1']['day']}-{hs['head']['day']}-{hs['s2']['day']}"
                sc["hs"].append(hs)
    return out


def confirmed_geometry(bars: list[dict], thr: float, atr: float, window_start_day: str, as_of_day: str) -> dict[str, tuple]:
    """因果审计用：只取截至 as_of_day 已确认、落在结构窗口内的拐点做聚类，返回 {zone_id: (触点日期, 价区下沿, 上沿, 颈线)}。

    阈值与 ATR 由调用方固定；结果不得随 as_of_day 之后追加的K线改变。
    """
    piv = [p for p in zigzag(bars, thr) if p["confirmed"] and p["day"] >= window_start_day and p["confirmed_at_day"] <= as_of_day]
    out: dict[str, tuple] = {}
    for kind in ("L", "H"):
        for g in _clusters(bars, piv, kind, atr):
            if len(g) < 2:
                continue
            mid = bars[g[0]["idx"] + 1 : g[-1]["idx"]]
            neck = max(b["high"] for b in mid) if kind == "L" else min(b["low"] for b in mid)
            prices = [t["price"] for t in g]
            out[f"{SIDE[kind]}-{g[0]['day']}"] = (tuple(t["day"] for t in g), min(prices), max(prices), neck)
    return out
