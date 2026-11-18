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


# ---------------------------------------------------------------------------
# 输出：取整、命名、文字摘要、盘中标注
# ---------------------------------------------------------------------------


SCALE_CN = {"MAJOR": "主级别", "MINOR": "次级别"}
SIDE_CN = {"L": "支撑", "H": "压力"}
SEQUENCE_CN = {
    ("L", "RISING"): "低点逐次抬高", ("L", "FALLING"): "低点逐次降低", ("L", "MIXED"): "低点不单调",
    ("H", "RISING"): "高点逐次抬高", ("H", "FALLING"): "高点逐次降低", ("H", "MIXED"): "高点不单调",
}
STATE_CN = {
    "L": {"BROKEN": "已破位", "FALSE_BREAK": "假跌破后收回", "NECKLINE_CROSSED": "已突破颈线", "HOLDING": "价区守住、未过颈线"},
    "H": {"BROKEN": "已突破", "FALSE_BREAK": "假突破后回落", "NECKLINE_CROSSED": "已跌破颈线", "HOLDING": "价区压住、未破颈线"},
}
HS_STATE_CN = {
    "L": {"FORMING": "形成中、未过颈线", "NECKLINE_CROSSED": "已突破颈线", "FAILED": "形态失败"},
    "H": {"FORMING": "形成中、未破颈线", "NECKLINE_CROSSED": "已跌破颈线", "FAILED": "形态失败"},
}
CHANGE_CN = {
    "ZONE_NEW": "新价区", "TOUCH_ADDED": "新增未确认触点", "TOUCH_UPDATED": "触点更新", "TOUCH_CONFIRMED": "触点确认",
    "TOUCH_LEFT_ZONE": "未确认极值越出价区", "STATE_CHANGED": "状态变化", "ZONE_DISSOLVED": "价区不再列出",
    "HS_NEW": "新头肩形态", "HS_STATE_CHANGED": "头肩状态变化", "HS_ENDED": "头肩形态不再成立",
    "PLATFORM_NEW": "新平台", "PLATFORM_CHANGED": "平台变化", "PLATFORM_ENDED": "平台不再成立",
}
DISSOLVE_CN = {
    "TOUCH_REPLACED": "未确认触点被取代后不足 2 个触点",
    "HEIGHT": "颈线高度不足 1.2 个 ATR",
    "EXPIRED": "破位后超过 3 根K线才收回，价区失效",
    "BROKEN_STALE": "破位已超过 20 根K线",
    "LEFT_WINDOW": "首触点移出近 120 日结构窗口",
    "PIVOT_GONE": "首触点不再是本级别拐点",
    "NOT_SELECTED": "不再满足入选条件（价区仍在，只是不再列出）",
    "SCALE_SKIPPED": "本级别今日跳过（次级别阈值不小于主级别）",
}


def _base_name(kind: str, n_touch: int) -> str:
    if n_touch == 2:
        return "双底" if kind == "L" else "双顶"
    if n_touch == 3:
        return "三重底" if kind == "L" else "三重顶"
    return f"{n_touch} 次测试的{SIDE_CN[kind]}区"


def _state_cn(z: dict) -> str:
    if z["state"] == "TESTING":
        return f"第 {len(z['touches'])} 次测试进行中"
    return STATE_CN[z["kind"]][z["state"]]


def _summary(z: dict, f: Callable[[float], str], since: str | None) -> str:
    n_t, kind = len(z["touches"]), z["kind"]
    shown = z["touches"] if n_t <= MAX_TOUCHES else z["touches"][:1] + z["touches"][-(MAX_TOUCHES - 1) :]
    parts = [f"{t['day']} {f(t['price'])}" + ("" if t["confirmed"] else "（未确认）") for t in shown]
    touch_text = (parts[0] + "、…、" + "、".join(parts[1:])) if n_t > MAX_TOUCHES else "、".join(parts)
    head = f"{_name(z)}：{touch_text}；价区 {f(z['low'])}~{f(z['high'])}，颈线 {f(z['neck'])}。"
    brk, neck, meas = f(z["break"]), f(z["neck"]), f(z["measured"])
    st = z["state"]
    if kind == "L":
        tail = {
            "TESTING": f"第 {n_t} 个低点 {f(z['unc']['price']) if z['unc'] else ''} 尚未确认：之后最高价达到 {f(z['confirm']) if z['confirm'] else ''} 即确认；收盘跌破 {brk} 判定破位。",
            "HOLDING": f"收盘站上颈线 {neck} 为突破，测量位 {meas}；收盘跌破 {brk} 判定破位。",
            "NECKLINE_CROSSED": f"已于 {since} 收盘站上颈线 {neck}，测量位 {meas}；收盘跌破 {brk} 判定破位。",
            "BROKEN": f"已于 {since} 收盘跌破失效价 {brk}，支撑转为上方压力；收盘重新站上 {f(z['low'])} 视为收回。",
            "FALSE_BREAK": f"曾收盘跌破 {brk}，{since} 收盘收回（3 根K线内，假跌破）；再次收盘跌破 {brk} 判定破位。",
        }[st]
    else:
        tail = {
            "TESTING": f"第 {n_t} 个高点 {f(z['unc']['price']) if z['unc'] else ''} 尚未确认：之后最低价达到 {f(z['confirm']) if z['confirm'] else ''} 即确认；收盘站上 {brk} 判定价区被突破。",
            "HOLDING": f"收盘跌破颈线 {neck} 为破位，测量位 {meas}；收盘站上 {brk} 判定价区被突破。",
            "NECKLINE_CROSSED": f"已于 {since} 收盘跌破颈线 {neck}，测量位 {meas}；收盘站上 {brk} 判定价区被突破。",
            "BROKEN": f"已于 {since} 收盘站上失效价 {brk}，压力转为下方支撑；收盘重新跌回 {f(z['high'])} 及以下视为收回。",
            "FALSE_BREAK": f"曾收盘站上 {brk}，{since} 收盘回落（3 根K线内，假突破）；再次收盘站上 {brk} 判定价区被突破。",
        }[st]
    return head + tail


def _name(z: dict) -> str:
    n_t = len(z["touches"])
    return _base_name(z["kind"], n_t) + (f"（第 {n_t} 次测试进行中，未确认）" if z["unc"] else "")


def _intraday_note(z: dict, live: dict | None, f: Callable[[float], str]) -> str | None:
    if not live:
        return None
    if z["kind"] == "L" and float(live["low"]) < z["break"]:
        return f"盘中最低 {f(float(live['low']))} 已低于失效价 {f(z['break'])}（未收盘，不作确认）"
    if z["kind"] == "H" and float(live["high"]) > z["break"]:
        return f"盘中最高 {f(float(live['high']))} 已高于失效价 {f(z['break'])}（未收盘，不作确认）"
    if z["low"] <= float(live["close"]) <= z["high"]:
        return "盘中正在测试该价区（未收盘，不作确认）"
    return None


def _same_touches(a: dict, b: dict) -> bool:
    return [(t["day"], t["price"], t["confirmed"]) for t in a["touches"]] == [(t["day"], t["price"], t["confirmed"]) for t in b["touches"]]


def _stub_of(scan: dict, zid: str) -> str | None:
    """次级别价区与某个已输出的主级别价区触点完全相同、且都没有未确认触点时，返回该主级别 zone_id。

    有未确认触点时两个级别的确认价不同（阈值不同），照常输出全部字段。
    """
    if not zid.startswith("MINOR-"):
        return None
    major = scan["scales"]["MAJOR"]
    z = scan["scales"]["MINOR"]["zones"][zid]
    if z["unc"]:
        return None
    for mid, _ in major["selected"]:
        m = major["zones"][mid]
        if m["kind"] == z["kind"] and _same_touches(m, z):
            return mid
    return None


def _render(scan: dict, hist: list[dict], live: dict | None, rp: Callable[[float], float], decimals: int) -> list[dict]:
    def obs(v: float) -> float:  # 观察到的价格（触点、价区边缘、颈线）：与结构层拐点同口径
        return float(money(v))

    def f(v: float) -> str:
        return f"{float(v):.{decimals}f}"

    scales = []
    for scale in ("MAJOR", "MINOR"):
        sc = scan["scales"][scale]
        item: dict[str, Any] = {
            "scale": scale,
            "scale_cn": SCALE_CN[scale],
            "zigzag_threshold_pct": round(sc["thr"] * 100, 2),
            "skipped_reason": None,
            "zones": [],
            "head_shoulders": [],
            "platform": None,
        }
        if sc["skipped"]:
            major_pct = scan["scales"]["MAJOR"]["thr"] * 100
            item["skipped_reason"] = f"次级别阈值 {sc['thr'] * 100:.2f}% 不小于主级别阈值 {major_pct:.2f}%，本级别跳过"
            scales.append(item)
            continue
        for zid, why in sc["selected"]:
            stub = _stub_of(scan, zid)
            if stub:
                item["zones"].append({"zone_id": zid, "same_as_zone_id": stub})
                continue
            z = sc["zones"][zid]
            since = hist[z["since_idx"]]["day"] if z["since_idx"] is not None else None
            shown = z["touches"] if len(z["touches"]) <= MAX_TOUCHES else z["touches"][:1] + z["touches"][-(MAX_TOUCHES - 1) :]
            zr = {**z, "break": rp(z["break"]), "measured": rp(z["measured"]), "confirm": rp(z["confirm"]) if z["confirm"] else None,
                  "low": obs(z["low"]), "high": obs(z["high"]), "neck": obs(z["neck"])}
            item["zones"].append({
                "zone_id": zid,
                "side": SIDE[z["kind"]],
                "side_cn": SIDE_CN[z["kind"]],
                "name_cn": _name(z),
                "touch_count": len(z["touches"]),
                "confirmed_touch_count": sum(1 for t in z["touches"] if t["confirmed"]),
                "touches": [
                    {"day": t["day"], "price": obs(t["price"]), "confirmed": t["confirmed"], "confirmed_at_day": t["confirmed_at_day"]}
                    for t in shown
                ],
                "touches_truncated": len(z["touches"]) > MAX_TOUCHES,
                "zone_low_price": zr["low"],
                "zone_high_price": zr["high"],
                "neckline_price": zr["neck"],
                "height_abs": obs(z["height"]),
                "height_atr": round(z["height"] / scan["atr"], 2),
                "measured_move_price": zr["measured"],
                "break_price": zr["break"],
                "confirm_price": zr["confirm"],
                "sequence": z["sequence"],
                "sequence_cn": SEQUENCE_CN[(z["kind"], z["sequence"])],
                "first_last_atr": round(z["first_last_atr"], 2),
                "state": z["state"],
                "state_cn": _state_cn(z),
                "state_since_day": since,
                "selected_because": why,
                "intraday_note": _intraday_note(zr, live, f),
                "summary_cn": _summary(zr, f, since),
            })
        for hs in sc["hs"]:
            item["head_shoulders"].append(_render_hs(hs, rp, obs, f))
        pf = sc["platform"]
        if pf:
            s, r = pf["support"], pf["resistance"]
            lower, upper = obs(s["low"]), obs(r["high"])
            since_day = min(s["touches"][0]["day"], r["touches"][0]["day"])
            item["platform"] = {
                "lower_price": lower,
                "upper_price": upper,
                "lower_zone_id": s["zone_id"],
                "upper_zone_id": r["zone_id"],
                "lower_touch_count": len(s["touches"]),
                "upper_touch_count": len(r["touches"]),
                "gap_atr": round(pf["gap_atr"], 2),
                "since_day": since_day,
                "summary_cn": (
                    f"平台：下沿 {f(lower)}（{len(s['touches'])} 次测试）、上沿 {f(upper)}（{len(r['touches'])} 次测试），"
                    f"自 {since_day} 起，两沿间距 {pf['gap_atr']:.2f} 个 ATR。平台是两个价区的组合描述，任一侧破位或越过颈线后不再成立。"
                ),
            }
        scales.append(item)
    return scales


def _render_hs(hs: dict, rp: Callable[[float], float], obs: Callable[[float], float], f: Callable[[float], str]) -> dict:
    kind = hs["kind"]
    name = "头肩底" if kind == "L" else "头肩顶"
    s2_conf = hs["s2"]["confirmed"]
    state_cn = HS_STATE_CN[kind][hs["state"]] + ("" if s2_conf else "（右肩未确认）")
    neck_today, measured = rp(hs["neck_today"]), rp(hs["measured"])
    pt = lambda p: {"day": p["day"], "price": obs(p["price"])}  # noqa: E731
    shoulder = f"右肩 {hs['s2']['day']} {f(obs(hs['s2']['price']))}" + ("" if s2_conf else "（右肩形成中，未确认）")
    if kind == "L":
        tail = (f"收盘站上今日颈线 {f(neck_today)} 为突破，测量位 {f(measured)}；"
                f"收盘跌破头部 {f(obs(hs['head']['price']))} 下方 0.5 个 ATR 判定形态失败。")
    else:
        tail = (f"收盘跌破今日颈线 {f(neck_today)} 为破位，测量位 {f(measured)}；"
                f"收盘站上头部 {f(obs(hs['head']['price']))} 上方 0.5 个 ATR 判定形态失败。")
    return {
        "name_cn": name,
        "left_shoulder": pt(hs["s1"]),
        "head": pt(hs["head"]),
        "right_shoulder": pt(hs["s2"]),
        "right_shoulder_confirmed": s2_conf,
        "neckline_points": [pt(hs["n1"]), pt(hs["n2"])],
        "neckline_today_price": neck_today,
        "height_abs": obs(hs["height"]),
        "measured_move_price": measured,
        "state": hs["state"],
        "state_cn": state_cn,
        "summary_cn": (f"{name}：左肩 {hs['s1']['day']} {f(obs(hs['s1']['price']))}、头部 {hs['head']['day']} "
                       f"{f(obs(hs['head']['price']))}、{shoulder}；{state_cn}。{tail}"),
    }


# ---------------------------------------------------------------------------
# 与上一根已收盘K线相比的变化（规格 6）
# ---------------------------------------------------------------------------


def _zone_changes(cur: dict, prev: dict, f: Callable[[float], str]) -> list[tuple[str, str | None, str | None, str | None, str]]:
    """返回 [(change, from, to, reason, detail_cn)]。cur / prev 是该 zone_id 在两天的内部价区（都存在且都已输出或入选）。"""
    out = []
    kind = cur["kind"]
    word = "低" if kind == "L" else "高"
    name = _base_name(kind, len(cur["touches"]))
    p_unc, c_unc = prev["unc"], cur["unc"]
    cur_conf = {t["day"]: t for t in cur["touches"] if t["confirmed"]}
    explained = False
    if p_unc and p_unc["day"] in cur_conf:
        t = cur_conf[p_unc["day"]]
        out.append(("TOUCH_CONFIRMED", None, None, None, f"{name}的{word}点 {t['day']} {f(t['price'])} 已于 {t['confirmed_at_day']} 确认"))
        explained = True
    elif p_unc and c_unc and c_unc["day"] != p_unc["day"]:
        out.append(("TOUCH_UPDATED", None, None, None, f"{name}的未确认{word}点更新为 {c_unc['day']} {f(c_unc['price'])}"))
        explained = True
    elif p_unc and not c_unc:
        out.append(("TOUCH_LEFT_ZONE", None, None, None, f"{name}在 {p_unc['day']} 的未确认{word}点被越出价区的新极值取代，不再计入"))
        explained = True
    if c_unc and not p_unc:
        out.append(("TOUCH_ADDED", None, None, None, f"{name}新增第 {len(cur['touches'])} 个{word}点 {c_unc['day']} {f(c_unc['price'])}（未确认）"))
        explained = True
    prev_conf_days = [t["day"] for t in prev["touches"] if t["confirmed"]]
    expect = prev_conf_days + ([p_unc["day"]] if p_unc and p_unc["day"] in cur_conf else [])
    if not explained and [t["day"] for t in cur["touches"] if t["confirmed"]] != expect:
        text = "、".join(f"{t['day']} {f(t['price'])}" for t in cur["touches"])
        out.append(("TOUCH_UPDATED", None, None, None, f"{name}的触点按当日数据重算为 {text}"))
    if prev["state"] != cur["state"]:
        out.append(("STATE_CHANGED", prev["state"], cur["state"], None, f"{name}：{_generic_state(prev)} → {_generic_state(cur)}"))
    return out


def _generic_state(z: dict) -> str:
    return "测试进行中" if z["state"] == "TESTING" else STATE_CN[z["kind"]][z["state"]]


def _dissolve_reason(zid: str, cur_sc: dict, cur_scan: dict, prev_zone: dict) -> str:
    if cur_sc["skipped"]:
        return "SCALE_SKIPPED"
    if prev_zone["touches"][0]["day"] < cur_scan["window_start_day"]:
        return "LEFT_WINDOW"
    if zid in cur_sc["zones"]:
        return "NOT_SELECTED"
    return cur_sc["rejected"].get(zid, "PIVOT_GONE")


def _changes(cur: dict, prev: dict | None, decimals: int) -> list[dict]:
    if prev is None:
        return []

    def f(v: float) -> str:
        return f"{float(v):.{decimals}f}"

    rows: list[dict] = []
    for scale in ("MAJOR", "MINOR"):
        c_sc, p_sc = cur["scales"][scale], prev["scales"][scale]
        thr_changed = c_sc["skipped"] != p_sc["skipped"] or abs(c_sc["thr"] - p_sc["thr"]) > 1e-9
        suffix = f"（阈值由 {p_sc['thr'] * 100:.2f}% 变为 {c_sc['thr'] * 100:.2f}%，按新阈值重算）" if thr_changed else ""

        def add(obj_type: str, obj_id: str, change: str, frm: str | None, to: str | None, reason: str | None, detail: str) -> None:
            rows.append({
                "scale": scale, "object_type": obj_type, "object_id": obj_id, "change": change, "change_cn": CHANGE_CN[change],
                "from": frm, "to": to, "reason": reason, "threshold_changed": thr_changed, "detail_cn": detail + suffix,
            })

        c_sel = [zid for zid, _ in c_sc["selected"]]
        p_sel = [zid for zid, _ in p_sc["selected"]]
        for zid in c_sel + [z for z in p_sel if z not in c_sel]:
            c_stub = zid in c_sel and _stub_of(cur, zid)
            p_stub = zid in p_sel and _stub_of(prev, zid)
            if (c_stub or zid not in c_sel) and (p_stub or zid not in p_sel):
                continue  # 只以"与主级别相同"的形式出现过：视同主级别价区，不重复记变化
            if zid in c_sel and zid in p_sel:
                for change, frm, to, reason, detail in _zone_changes(c_sc["zones"][zid], p_sc["zones"][zid], f):
                    add("ZONE", zid, change, frm, to, reason, detail)
            elif zid in c_sel:
                z = c_sc["zones"][zid]
                reason = "SELECTED" if zid in p_sc["zones"] else "FORMED"
                label = _name(z) if z["state"] == "TESTING" else f"{_name(z)}（{_state_cn(z)}）"
                add("ZONE", zid, "ZONE_NEW", None, z["state"], reason, label + ("，重新满足入选条件" if reason == "SELECTED" else ""))
            else:
                pz = p_sc["zones"][zid]
                reason = _dissolve_reason(zid, c_sc, cur, pz)
                add("ZONE", zid, "ZONE_DISSOLVED", pz["state"], None, reason,
                    f"{_base_name(pz['kind'], len(pz['touches']))}（首触点 {pz['touches'][0]['day']}）不再列出：{DISSOLVE_CN[reason]}")
        c_hs = {h["hs_id"]: h for h in c_sc["hs"]}
        p_hs = {h["hs_id"]: h for h in p_sc["hs"]}
        for hid, h in c_hs.items():
            name = "头肩底" if h["kind"] == "L" else "头肩顶"
            if hid not in p_hs:
                add("HEAD_SHOULDERS", hid, "HS_NEW", None, h["state"], None, f"{name}（{HS_STATE_CN[h['kind']][h['state']]}）")
            elif p_hs[hid]["state"] != h["state"]:
                add("HEAD_SHOULDERS", hid, "HS_STATE_CHANGED", p_hs[hid]["state"], h["state"], None,
                    f"{name}：{HS_STATE_CN[h['kind']][p_hs[hid]['state']]} → {HS_STATE_CN[h['kind']][h['state']]}")
        for hid, h in p_hs.items():
            if hid not in c_hs:
                name = "头肩底" if h["kind"] == "L" else "头肩顶"
                add("HEAD_SHOULDERS", hid, "HS_ENDED", h["state"], None, None, f"{name}（右肩 {h['s2']['day']}）不再成立")
        c_pf, p_pf = c_sc["platform"], p_sc["platform"]
        c_id = f"{c_pf['support']['zone_id']}|{c_pf['resistance']['zone_id']}" if c_pf else None
        p_id = f"{p_pf['support']['zone_id']}|{p_pf['resistance']['zone_id']}" if p_pf else None

        def pf_text(pf: dict) -> str:
            return (f"{f(money(pf['support']['low']))}~{f(money(pf['resistance']['high']))}"
                    f"（下沿 {len(pf['support']['touches'])} 次、上沿 {len(pf['resistance']['touches'])} 次）")

        def pf_sig(pf: dict) -> tuple:
            return (float(money(pf["support"]["low"])), float(money(pf["resistance"]["high"])),
                    len(pf["support"]["touches"]), len(pf["resistance"]["touches"]))

        if p_id and p_id != c_id:
            add("PLATFORM", p_id, "PLATFORM_ENDED", None, None, None, "原平台不再成立（一侧价区破位、越过颈线或不再入选）")
        if c_id and c_id != p_id:
            add("PLATFORM", c_id, "PLATFORM_NEW", None, None, None, f"平台 {pf_text(c_pf)}")
        elif c_id and pf_sig(c_pf) != pf_sig(p_pf):
            add("PLATFORM", c_id, "PLATFORM_CHANGED", None, None, None, f"平台变为 {pf_text(c_pf)}")
    return rows[:MAX_CHANGES]


def compute_tested_zones(
    completed: list[dict],
    live: dict | None = None,
    round_price: Callable[[float], float] | None = None,
    decimals: int = 2,
) -> dict[str, Any]:
    """结构层入口：只用已收盘K线 completed（最近 HISTORY_BARS 根）；live 为盘中未收盘K线，只用于 intraday_note。

    round_price(价格) -> 按价位表取整后的价格，用于推算出的价位（失效价、确认价、测量位、头肩今日颈线）；
    缺省按 structure.money 取整。观察到的价格（触点、价区边缘、颈线）与结构层拐点同口径，按 money 取整。
    """
    rp = (lambda v: float(round_price(float(v)))) if round_price else (lambda v: float(money(v)))
    hist = completed[-HISTORY_BARS:]
    cur = scan_zones(hist)
    prev = scan_zones(completed[:-1][-HISTORY_BARS:]) if len(completed) > 2 else None
    return {
        "atr_abs": float(money(cur["atr"])),
        "atr_basis": "STRUCTURE_ATR20",
        "prev_bar_day": prev["day"] if prev else None,
        "scales": _render(cur, hist, live, rp, decimals),
        "changes": _changes(cur, prev, decimals),
    }
