#!/usr/bin/env python3
"""A股指数K线结构图（确定性脚本）：波段拐点、ABC 测算、楔形/三角形轨道、三条规则推演路径。

输出一张 PNG，并向 stdout 打印 JSON（含 small_model_copy_blocks 直抄文本）。图与文字同源，所有数字均由
本脚本从行情数据计算。

v4.0 相对 v3.0 的算法修正：
- 楔形上下轨改为「ZigZag 已确认拐点 + 凸包切线」拟合：轨道只由已确认的波段高/低点决定，
  不再把今日K线当成轨道支点（v3 的下轨在下跌中总是穿过今日最低价，上轨可能由相隔几天的两个高点外推）。
- 形态分类带斜率容差与收敛比：楔形 / 三角形 / 通道 / 箱体 / 扩散，并给出收盘突破状态与持续天数。
- ABC 测算支持下跌与上涨两个方向；测算位状态只按已收盘K线区分「收盘已破 / 曾收破已收回 / 影线触及 / 未触及」，盘中刺破另行标注。
- 三条推演路径的触发位来自形态轨道，目标位取「支撑压力阶梯」中最近的有效位，并写明来源。
- 收盘后新浪日K未入库时用腾讯收盘快照补齐当日，不再误标为「盘中未收盘」。

v4.1 三层窗口：
- 展示层：--days N（60~500）只决定主图画多少根K线。
- 结构层：轨道、ABC、黄金分割、支撑压力阶梯与三条推演固定只用最近 STRUCT_DAYS 根，与 --days 无关，
  同一天不同 --days 的触发位/失效位/目标位完全一致（tests/check_days_invariance.py 校验）。
- 背景层：近 max(N, 250) 日的区间位置、MA120/MA250 方向与长周期黄金分割，只做描述，不参与选位。
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.request
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from structure import (  # noqa: F401  兼容旧接口：tests/check_days_invariance.py 等通过本模块访问这些名字
    BACKGROUND_MIN_DAYS,
    HISTORY_BARS,
    MARKET_CLOSE,
    MAX_DAYS,
    STRUCT_DAYS,
    _src,
    compute_structure_layer,
    fmt_price,
    money,
    q2,
    rail_at,
    sma,
    split_live_bar,
)

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",  # Arch Linux
    "/System/Library/Fonts/Hiragino Sans GB.ttc",  # macOS 冬青黑体（W3/W6 双字重，字形清晰现代）
    "/System/Library/Fonts/STHeiti Medium.ttc",    # macOS 备选黑体
    "C:/Windows/Fonts/msyh.ttc",                   # Windows 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",                 # Windows 黑体
)

# 专业深色看盘配色 (TradingView Pro Dark 风格)
BG = (11, 15, 23)
PANEL_BG = (14, 19, 29)
CARD_BG = (19, 25, 38)
CARD_BORDER = (44, 56, 78)
GRID = (26, 34, 48)
AXIS_LINE = (58, 70, 92)
TEXT = (238, 242, 248)
MUTED = (148, 160, 180)

UP = (242, 68, 68)          # 阳线红
DOWN = (16, 188, 132)       # 阴线绿
INTRA = (59, 130, 246)      # 盘中蓝
MA = {
    20: (250, 204, 21),     # MA20 亮黄
    60: (192, 132, 252),    # MA60 亮紫
    120: (148, 163, 184),   # MA120 石板灰（v3 的橙色与楔形上轨撞色）
    250: (232, 121, 249),   # MA250 品红（年线）
}
SCEN = (148, 188, 255)      # ABC 测算线冰蓝
ZIGZAG = (126, 144, 174)    # 波段骨架连线

WEDGE_UPPER = (255, 159, 67)   # 楔形上轨橙
WEDGE_LOWER = (56, 189, 248)   # 楔形下轨青
PROJ_BULL = (248, 113, 113)    # 上攻预判红
PROJ_NEUTRAL = (250, 204, 21)  # 震荡预判黄
PROJ_BEAR = (52, 211, 153)     # 下探预判绿
SSAA = 2                       # 2× 超采样抗锯齿倍率（3840×2320 渲染后 LANCZOS 下采样至 1920×1160）



def datalen_for(days: int) -> int:
    """拉取日K根数：结构层固定 HISTORY_BARS 根；展示 N 根时多拉 250 根，让主图最左侧也有 MA250。"""
    return max(HISTORY_BARS, days + 250)


def fetch(url: str, encoding: str = "utf-8") -> str:
    from data_gateway import _http_get

    return _http_get(url, encoding=encoding)


def load_daily(sina_symbol: str, datalen: int = 320) -> list[dict]:
    if not re.fullmatch(r"(?:sh|sz|bj)\d{6}|hk[A-Za-z0-9]+|gb_[a-z0-9_.]+", sina_symbol.strip()):
        raise ValueError(f"Invalid sina symbol: {sina_symbol!r}")
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={sina_symbol}&scale=240&ma=no&datalen={datalen}"
    )
    rows = json.loads(fetch(url))
    if not isinstance(rows, list):
        raise SystemExit(f"invalid daily response for {sina_symbol}")
    bars = []
    for row in rows:
        bars.append(
            {
                "day": row["day"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "complete": True,
            }
        )
    if len(bars) < 130:
        raise SystemExit(f"not enough daily bars: {len(bars)}")
    from contracts import validate_bars

    validate_bars(bars, f"sina:{sina_symbol}")
    return bars


def load_intraday(sina_symbol: str) -> dict | None:
    if not re.fullmatch(r"(?:sh|sz|bj)\d{6}|hk[A-Za-z0-9]+|gb_[a-z0-9_.]+", sina_symbol.strip()):
        return None
    try:
        raw = fetch(f"https://qt.gtimg.cn/q={sina_symbol}", "gbk")
    except Exception:
        return None
    parts = raw.split("~")
    if len(parts) < 35 or not parts[3]:
        return None
    try:
        open_p = float(parts[5] or 0)
        high_p = float(parts[33] or 0)
        low_p = float(parts[34] or 0)
        close_p = float(parts[3] or 0)
        prev_p = float(parts[4] or 0)
        volume = float(parts[6] or 0) * 100  # 腾讯成交量单位为「手」，×100 与新浪日K「股」口径一致
    except ValueError:
        return None
    if min(open_p, high_p, low_p, close_p) <= 0:
        return None
    ts = parts[30].strip()
    if len(ts) < 14:
        return None
    stamp = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
    return {
        "day": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}",
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "prev": prev_p,
        "volume": volume if volume > 0 else None,
        "stamp": stamp,
        "complete": False,
    }




class _ScaledDraw:
    """将逻辑坐标 (1920×1160) 自动按 SSAA 倍率缩放到底层高分辨率 RGBA 画布，实现全局超采样抗锯齿。"""

    def __init__(self, image: Image.Image, scale: int = 1) -> None:
        self.scale = scale
        self._draw = ImageDraw.Draw(image, "RGBA")

    def _s(self, v: float) -> int:
        return int(round(v * self.scale))

    def _pts(self, xy) -> list[tuple[int, int]] | tuple[int, ...]:
        s = self.scale
        if isinstance(xy, ( tuple, list )) and xy and isinstance(xy[0], (tuple, list)):
            return [(int(round(px * s)), int(round(py * s))) for px, py in xy]
        return tuple(int(round(v * s)) for v in xy)

    def line(self, xy, fill=None, width: float = 1, joint: str | None = None) -> None:
        pts = self._pts(xy)
        w = max(1, int(round(width * self.scale)))
        if joint and isinstance(pts, list) and len(pts) > 2:
            self._draw.line(pts, fill=fill, width=w, joint=joint)
        else:
            self._draw.line(pts, fill=fill, width=w)

    def rectangle(self, xy, fill=None, outline=None, width: float = 1) -> None:
        self._draw.rectangle(self._pts(xy), fill=fill, outline=outline, width=max(1, int(round(width * self.scale))))

    def rounded_rectangle(self, xy, radius: float = 4, fill=None, outline=None, width: float = 1) -> None:
        self._draw.rounded_rectangle(
            self._pts(xy),
            radius=int(round(radius * self.scale)),
            fill=fill,
            outline=outline,
            width=max(1, int(round(width * self.scale))),
        )

    def ellipse(self, xy, fill=None, outline=None, width: float = 1) -> None:
        self._draw.ellipse(self._pts(xy), fill=fill, outline=outline, width=max(1, int(round(width * self.scale))))

    def polygon(self, xy, fill=None, outline=None) -> None:
        self._draw.polygon(self._pts(xy), fill=fill, outline=outline)

    def text(self, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont, fill=None) -> None:
        self._draw.text((self._s(xy[0]), self._s(xy[1])), text, font=font, fill=fill)

    def textbbox(self, xy: tuple[float, float], text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int, int, int]:
        return self._draw.textbbox((self._s(xy[0]), self._s(xy[1])), text, font=font)


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False, scale: int = 1) -> ImageFont.FreeTypeFont:
    px = max(8, int(round(size * scale)))
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            idx = 0
            if path.endswith("Hiragino Sans GB.ttc"):
                idx = 2 if bold else 0
            elif path.endswith("STHeiti Medium.ttc"):
                idx = 1  # Heiti SC
            try:
                return ImageFont.truetype(path, px, index=idx)
            except Exception:
                return ImageFont.truetype(path, px, index=0)
    raise SystemExit(f"no CJK font found in {FONT_CANDIDATES}")


def text_size(draw: _ScaledDraw | ImageDraw.ImageDraw, value: str, face: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), value, font=face)
    scale = getattr(draw, "scale", 1)
    return int(round((box[2] - box[0]) / scale)), int(round((box[3] - box[1]) / scale))


def _wrap_text(
    draw: _ScaledDraw | ImageDraw.ImageDraw,
    value: str,
    face: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """按像素宽度折行；风控否决理由必须完整展示，不能静默截断。"""
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if current and text_size(draw, candidate, face)[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def y_of(price: float, pmin: float, pmax: float, top: int, bottom: int) -> int:
    return int(round(top + (pmax - price) / (pmax - pmin) * (bottom - top)))




def add_trading_days_str(base_day: str, add_days: int) -> str:
    """Estimate future trading date (skipping weekends only, not holidays) for X-axis ticks."""
    try:
        dt = datetime.strptime(base_day, "%Y-%m-%d")
    except ValueError:
        return f"+{add_days}日"
    step = 0
    while step < add_days:
        dt += timedelta(days=1)
        if dt.weekday() < 5:
            step += 1
    return dt.strftime("%m-%d")


def dashed_line(
    draw: _ScaledDraw | ImageDraw.ImageDraw,
    p1: tuple[float, float],
    p2: tuple[float, float],
    fill: tuple[int, ...],
    width: float = 2,
    dash: float = 9,
    gap: float = 6,
) -> None:
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist < 1:
        return
    dx = (x2 - x1) / dist
    dy = (y2 - y1) / dist
    pos = 0.0
    while pos < dist:
        end = min(pos + dash, dist)
        sx, sy = x1 + dx * pos, y1 + dy * pos
        ex, ey = x1 + dx * end, y1 + dy * end
        draw.line((sx, sy, ex, ey), fill=fill, width=width)
        pos += dash + gap


def draw_arrow_polyline(
    draw: _ScaledDraw | ImageDraw.ImageDraw,
    pts: list[tuple[int, int]],
    fill: tuple[int, int, int],
    width: float = 3,
    dashed: bool = False,
    alpha: int = 255,
) -> None:
    if len(pts) < 2:
        return
    a_clamped = max(60, min(255, int(alpha)))
    line_col = (*fill[:3], a_clamped) if a_clamped < 255 else fill[:3]
    glow_alpha = max(10, int(round((42 if not dashed else 28) * (a_clamped / 255.0))))
    glow_col = (*fill[:3], glow_alpha)
    for i in range(len(pts) - 1):
        if dashed:
            dashed_line(draw, pts[i], pts[i + 1], fill=glow_col, width=width + 3, dash=8, gap=5)
            dashed_line(draw, pts[i], pts[i + 1], fill=line_col, width=width, dash=8, gap=5)
        else:
            draw.line([pts[i], pts[i + 1]], fill=glow_col, width=width + 4.5, joint="curve")
    if not dashed:
        draw.line(pts, fill=line_col, width=width, joint="curve")
    # 绘出中间转折圆点（外发光 + 深色内芯）
    for px, py in pts[1:-1]:
        draw.ellipse((px - 6.5, py - 6.5, px + 6.5, py + 6.5), fill=(*fill[:3], max(15, int(round(55 * a_clamped / 255.0)))))
        draw.ellipse((px - 4.2, py - 4.2, px + 4.2, py + 4.2), fill=PANEL_BG, outline=line_col, width=2)
    # 末端流线型燕尾箭头
    x1, y1 = pts[-2]
    x2, y2 = pts[-1]
    angle = math.atan2(y2 - y1, x2 - x1)
    head_len = 13.5
    a1 = angle + math.pi * 0.83
    a2 = angle - math.pi * 0.83
    p_left = (x2 + head_len * math.cos(a1), y2 + head_len * math.sin(a1))
    p_right = (x2 + head_len * math.cos(a2), y2 + head_len * math.sin(a2))
    p_notch = (x2 - head_len * 0.68 * math.cos(angle), y2 - head_len * 0.68 * math.sin(angle))
    draw.polygon([pts[-1], p_left, p_notch, p_right], fill=line_col)


def draw_pivot_dot(draw: _ScaledDraw | ImageDraw.ImageDraw, x: float, y: float, color: tuple[int, int, int]) -> None:
    draw.ellipse((x - 7.5, y - 7.5, x + 7.5, y + 7.5), fill=(*color[:3], 55))
    draw.ellipse((x - 5.2, y - 5.2, x + 5.2, y + 5.2), fill=PANEL_BG, outline=color, width=2)
    draw.ellipse((x - 2.4, y - 2.4, x + 2.4, y + 2.4), fill=color)


def clip_to_price_range(
    x1: float, p1: float, x2: float, p2: float, lo: float, hi: float
) -> tuple[float, float, float, float] | None:
    """把一条 (x, price) 线段裁剪到价格区间 [lo, hi] 内，超出画面的部分不画（v3 是把 y 夹紧，会扭曲斜率）。"""
    t0, t1 = 0.0, 1.0
    dp = p2 - p1
    for bound, sign in ((lo, 1.0), (hi, -1.0)):
        num, den = sign * (p1 - bound), sign * dp
        if den == 0:
            if num < 0:
                return None
            continue
        t = -num / den
        if den > 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
    if t0 > t1:
        return None
    return x1 + (x2 - x1) * t0, p1 + dp * t0, x1 + (x2 - x1) * t1, p1 + dp * t1



# ---------------------------------------------------------------------------
# 绘图
# ---------------------------------------------------------------------------


def dashed_hline(draw: _ScaledDraw | ImageDraw.ImageDraw, x1: int, x2: int, y: int, fill: tuple[int, ...]) -> None:
    x = x1
    while x < x2:
        draw.line((x, y, min(x + 9, x2), y), fill=fill, width=1.6)
        x += 15


def _seg_box_dist(p1: tuple[float, float], p2: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    """估算线段 p1-p2 到矩形 box 的最近距离（用于避免微标签压在推演折线上）。"""
    bx1, by1, bx2, by2 = box
    best = float("inf")
    for k in range(9):
        t = k / 8
        px = p1[0] + (p2[0] - p1[0]) * t
        py = p1[1] + (p2[1] - p1[1]) * t
        dx = max(bx1 - px, 0.0, px - bx2)
        dy = max(by1 - py, 0.0, py - by2)
        best = min(best, math.hypot(dx, dy))
    return best


def draw_callout_badge(
    draw: _ScaledDraw | ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    face: ImageFont.FreeTypeFont,
    border_col: tuple[int, int, int],
    prefer: str,
    left_lim: int,
    right_lim: int,
    top_lim: int,
    bot_lim: int,
    occupied: list[tuple[int, int, int, int]],
    obstacles: list[tuple[int, int, int, int]] | None = None,
    peer_anchors: list[tuple[int, int, str]] | None = None,
) -> None:
    """绘制圆角引线徽标：自动避开已有徽标、相邻锚点、引线穿插与K线柱体。"""
    tw, th = text_size(draw, text, face)
    pad_x, pad_y = 8, 5
    bw, bh = tw + pad_x * 2 + 3, th + pad_y * 2

    dy_up = [-bh - 16, -bh - 34, -bh - 54, -bh - 76, -bh - 98]
    dy_dn = [16, 34, 54, 76, 98]
    dx_shifts = [
        -bw // 2,
        -int(bw * 0.82),
        -int(bw * 0.18),
        -bw - 8,
        -int(bw * 0.05),
        8,
        -int(bw * 1.15),
    ]
    dy_order = (dy_up + dy_dn) if prefer == "above" else (dy_dn + dy_up)

    best_score = float("inf")
    chosen_x, chosen_y = x - bw // 2, (y - bh - 16 if prefer == "above" else y + 16)
    obs_list = obstacles or []
    peers = peer_anchors or []

    for rank_y, dy in enumerate(dy_order):
        wrong_side = (prefer == "above" and dy > 0) or (prefer == "below" and dy < 0)
        for rank_x, dx in enumerate(dx_shifts):
            cx = min(max(left_lim + 6, x + dx), right_lim - bw - 6)
            cy = min(max(top_lim + 6, y + dy), bot_lim - bh - 6)
            box = (cx, cy, cx + bw, cy + bh)
            score = rank_y * 12.0 + rank_x * 7.0 + (1600.0 if wrong_side else 0.0)
            score += abs((cx + bw * 0.5) - x) * 0.18 + abs((cy + bh * 0.5) - y) * 0.32

            # 1) 严禁与已放置的徽标重叠，且引线不得穿过已放置的徽标
            cand_ay = cy + bh if cy + bh < y else (cy if cy > y else y)
            cand_ax = min(max(cx + 10, x), cx + bw - 10)
            clash = False
            for ox1, oy1, ox2, oy2 in occupied:
                if not (box[2] + 6 < ox1 or box[0] - 6 > ox2 or box[3] + 6 < oy1 or box[1] - 6 > oy2):
                    clash = True
                    break
                if _seg_box_dist((x, y), (cand_ax, cand_ay), (ox1 - 2, oy1 - 2, ox2 + 2, oy2 + 2)) < 2.0:
                    score += 5000.0
            if clash:
                continue

            # 2) 避免遮挡其他待标注锚点正上方/正下方的紧邻空间
            for px, py, pside in peers:
                if px == x and py == y:
                    continue
                if box[0] - 8 <= px <= box[2] + 8:
                    if (pside == "above" and cy <= py and py - cy < 110) or (pside == "below" and cy >= py and cy - py < 110):
                        score += 1200.0

            # 3) 尽量避开 K 线高低点实体与影线区域
            for kx1, ky1, kx2, ky2 in obs_list:
                if not (box[2] + 2 < kx1 or box[0] - 2 > kx2 or box[3] + 2 < ky1 or box[1] - 2 > ky2):
                    ov_h = min(box[3], ky2) - max(box[1], ky1) + 4
                    score += max(12.0, ov_h * 7.5)

            if score < best_score:
                best_score = score
                chosen_x, chosen_y = cx, cy

    chosen_x = min(max(left_lim + 6, chosen_x), right_lim - bw - 6)
    chosen_y = min(max(top_lim + 6, chosen_y), bot_lim - bh - 6)
    occupied.append((chosen_x, chosen_y, chosen_x + bw, chosen_y + bh))

    anchor_y = chosen_y + bh if chosen_y + bh < y else (chosen_y if chosen_y > y else y)
    anchor_x = min(max(chosen_x + 10, x), chosen_x + bw - 10)
    # 柔和引线发光 + 实线引线
    draw.line((x, y, anchor_x, anchor_y), fill=(*border_col, 65), width=3.5)
    draw.line((x, y, anchor_x, anchor_y), fill=(*border_col, 220), width=1.5)
    draw_pivot_dot(draw, x, y, border_col)
    # 投影阴影 + 玻璃感深色底板 + 左侧色条
    draw.rounded_rectangle(
        (chosen_x + 1, chosen_y + 2, chosen_x + bw + 1, chosen_y + bh + 2),
        radius=5,
        fill=(0, 0, 0, 115),
    )
    draw.rounded_rectangle(
        (chosen_x, chosen_y, chosen_x + bw, chosen_y + bh),
        radius=5,
        fill=(17, 23, 35, 240),
        outline=(*border_col, 215),
        width=1.2,
    )
    draw.rounded_rectangle(
        (chosen_x, chosen_y, chosen_x + 3.5, chosen_y + bh),
        radius=2,
        fill=border_col,
    )
    draw.text((chosen_x + pad_x + 2, chosen_y + pad_y - 1), text, font=face, fill=TEXT)


def _palette(color_scheme: str) -> tuple[tuple[int, int, int], ...]:
    """返回 (阳线色, 阴线色, 上攻路径色, 下探路径色)。"""
    if color_scheme == "us_green_up":
        return DOWN, UP, PROJ_BEAR, PROJ_BULL
    return UP, DOWN, PROJ_BULL, PROJ_BEAR


def render(
    bars: list[dict],
    intraday: dict | None,
    title: str,
    out_path: Path,
    show: int = 120,
    box_corner: str = "auto",
    data_source: str = "SINA_LIVE_DAILY",
    data_warning: str | None = None,
    structure: dict | None = None,
    color_scheme: str = "cn_red_up",
    quality_status: str = "OK",
    risk_veto: dict | None = None,
    conformal_forecast: dict | None = None,
    volume_profile: dict | None = None,
) -> dict:
    # 涨跌与上攻/下探配色跟随市场约定：A股/港股默认红涨绿跌，美股绿涨红跌
    UP, DOWN, PROJ_BULL, PROJ_BEAR = _palette(color_scheme)
    VP_POC = (45, 212, 191)
    VP_VA_BORDER = (129, 140, 248)
    up_word, down_word = ("绿", "红") if color_scheme == "us_green_up" else ("红", "绿")
    # 结构层只来自 structure.compute_structure_layer：v5 主流程传入已算好的结果，v4.1 CLI 才在这里现算
    layer = structure if structure is not None else compute_structure_layer(bars, intraday, show)
    vp_data = volume_profile if isinstance(volume_profile, dict) else (layer.get("volume_profile") if isinstance(layer, dict) else None)
    # 图上与摘要里的价格文本与 *_report.md 同一精度（价位表小数位由结构层按市场给出）
    dec = layer["price_decimals"]

    def fp(value: float) -> str:
        return fmt_price(value, dec)

    def fps(value: float) -> str:  # 带正负号的价差（点数）
        return f"{float(value):+.{dec}f}"

    completed, active_intra, data_note = layer["completed"], layer["active_intra"], layer["data_note"]
    ma = layer["ma"]
    view, view_start, sview, off = layer["view"], layer["view_start"], layer["sview"], layer["off"]
    rail_when = f"{view[-1]['day'][5:]}收盘" if active_intra else "今日"  # 轨道值取最新已收盘日，盘中不重算
    plot = list(view)
    if active_intra:
        plot.append(
            {
                "day": active_intra["day"],
                "open": active_intra["open"],
                "high": active_intra["high"],
                "low": active_intra["low"],
                "close": active_intra["close"],
                "volume": None,
                "complete": False,
            }
        )
    last_bar_idx = layer["last_bar_idx"]  # 「今日」所在槽位（盘中时为蓝色K线）

    # 以下为展示窗口内的标注值（主图最高/最低、当日涨跌），只描述 view，不参与结构选位
    hi_idx, hi = max(enumerate(view), key=lambda item: (money(item[1]["high"]), -item[0]))
    lo_idx, lo = min(enumerate(view), key=lambda item: (money(item[1]["low"]), item[0]))
    last_c = layer["last_c"]
    prev_c = money(view[-2]["close"]) if len(view) >= 2 else last_c
    daily_chg = q2(last_c - prev_c)
    daily_chg_pct = float(q2(daily_chg / prev_c * Decimal("100"))) if prev_c > 0 else 0.0
    ref_p = layer["ref_p"]
    hi_p, lo_p = money(hi["high"]), money(lo["low"])
    range_pct = float(q2((ref_p - lo_p) / (hi_p - lo_p) * Decimal("100"))) if hi_p > lo_p else 50.0

    future_slots, struct_slots = layer["future_slots"], layer["struct_slots"]
    pivots, zz_thr, atr = layer["pivots"], layer["zz_thr"], layer["atr"]
    wedge = dict(layer["wedge"])  # 复制后再取出私有轨道字段，避免修改共享的结构结果
    scenario, fibs, gaps = layer["scenario"], layer["fibs"], layer["gaps"]
    ma20_val, ma60_val, ma120_val = layer["ma_last"][20], layer["ma_last"][60], layer["ma_last"][120]
    window_high = {"day": hi["day"], "high": float(hi_p)}
    window_low = {"day": lo["day"], "low": float(lo_p)}
    background, ladder, projections = layer["background"], layer["ladder"], layer["projections"]
    # 以下绘图一律用主图下标：结构层的轨道与拐点平移 off
    upper_rail, lower_rail = ({**r, "x1": r["x1"] + off, "x2": r["x2"] + off} for r in (wedge.pop("_upper"), wedge.pop("_lower")))
    rail_pivots = [dict(p, idx=p["idx"] + off) for p in pivots if p["idx"] + off >= 0]
    disp_pivots = layer["display_pivots"]

    # 画布尺寸与三区零遮挡布局（真实输出启用 2× 超采样抗锯齿，/dev/null 回测走 1× 加速）
    width, height = 1920, 1160
    left, right = 72, 272  # 右侧预留 272px 专属标签槽，绝不遮挡 K 线与箭头
    top, main_bottom = 156, 836
    vol_top, vol_bottom = 900, 1088
    is_null_out = str(out_path) == "/dev/null"
    scale = 1 if is_null_out else SSAA

    image = Image.new("RGB", (width * scale, height * scale), BG)
    draw = _ScaledDraw(image, scale=scale)
    f_title = font(26, bold=True, scale=scale)
    f_sub = font(14, bold=False, scale=scale)
    f_card_title = font(13, bold=True, scale=scale)
    f_card_val = font(15, bold=True, scale=scale)
    f_card_val_sm = font(13, bold=True, scale=scale)
    f_tiny = font(13, bold=False, scale=scale)
    f_tiny_sm = font(12, bold=False, scale=scale)
    f_tiny_bold = font(13, bold=True, scale=scale)

    # 计算价格轴范围（轨道今日值离现价太远时不撑大坐标轴，超出部分直接裁剪）
    prices = [bar["high"] for bar in plot] + [bar["low"] for bar in plot]
    for n, series in ma.items():
        for idx in range(view_start, len(completed)):
            if series[idx] is not None:
                prices.append(series[idx])
    if scenario:
        prices.extend(level["price"] for level in scenario["levels"] if level["drawn"])
    for key in ("bull", "neutral", "bear"):
        prices.extend(pt[1] for pt in projections[key]["waypoints_offset"])
    for sc_item in projections.get("scenarios") or []:
        for _, p_sc in sc_item.get("waypoints_offset") or []:
            if abs(float(p_sc) / float(ref_p) - 1) <= 0.25:
                prices.append(float(p_sc))
    for rail_val in (wedge["upper_today"], wedge["lower_today"]):
        if abs(rail_val / float(ref_p) - 1) <= 0.15:
            prices.append(rail_val)
    if conformal_forecast and conformal_forecast.get("q10") and conformal_forecast.get("q90"):
        for q_val in (*conformal_forecast["q10"], *conformal_forecast["q90"]):
            if abs(float(q_val) / float(ref_p) - 1) <= 0.25:
                prices.append(float(q_val))
    if vp_data:
        for vp_k in ("poc_price", "vah_price", "val_price"):
            vp_v = vp_data.get(vp_k)
            if vp_v is not None and abs(float(vp_v) / float(ref_p) - 1) <= 0.25:
                prices.append(float(vp_v))

    pmin, pmax = min(prices), max(prices)
    pad = (pmax - pmin) * 0.08
    pmin -= pad
    pmax += pad * 1.15

    # ---------------- 顶部标题与右对齐胶囊图例 ----------------
    veto_flag = bool(risk_veto and risk_veto.get("risk_officer_veto"))
    veto_tag = "风控否决: 已触发(禁做多)" if veto_flag else "风控状态: 正常"
    subtitle = (
        f"数据区间：{view[0]['day']} → {view[-1]['day']}（已收盘 {len(view)} 根）"
        + (f"  ·  盘中实时快照：{active_intra['stamp']}" if active_intra else "  ·  当前状态：已收盘")
        + f"  ·  数据质量：{quality_status}  ·  {veto_tag}  ·  结构：近{len(sview)}日 {wedge['pattern']}"
    )
    draw.text((left, 50), subtitle, font=f_sub, fill=MUTED)

    def _branch_freq_style(branch_key: str, default_dashed: bool) -> tuple[str, float, int, bool]:
        hf = (projections.get(branch_key) or {}).get("historical_frequency") or {}
        if not hf or hf.get("target_freq") is None:
            return "", 2.8, 255, default_dashed
        freq = max(0.0, min(1.0, float(hf["target_freq"])))
        n_samp = int(hf.get("sample_size") or 0)
        low_s = bool(hf.get("low_sample", n_samp < 30))
        raw_freq = float(hf["raw_target_freq"]) if hf.get("raw_target_freq") is not None else freq
        pct_str = f"{raw_freq * 100:.1f}%" if n_samp > 0 else f"先验{freq * 100:.0f}%"
        w_val = round(1.8 + 2.2 * freq, 2)
        a_val = max(135, min(255, int(round((145 if low_s else 175) + 80 * freq))))
        tag_prefix = "[低样本] " if low_s else ""
        return f" {tag_prefix}{pct_str}(n={n_samp})", w_val, a_val, (True if low_s else default_dashed)

    bull_ftag, bull_w, bull_a, bull_dash = _branch_freq_style("bull", False)
    neut_ftag, neut_w, neut_a, neut_dash = _branch_freq_style("neutral", True)
    bear_ftag, bear_w, bear_a, bear_dash = _branch_freq_style("bear", False)

    legend = [
        ("阳线", UP, "box"),
        ("阴线", DOWN, "box"),
        ("盘中", INTRA, "box"),
        ("MA20", MA[20], "line"),
        ("MA60", MA[60], "line"),
        ("MA120", MA[120], "line"),
        ("MA250", MA[250], "line"),
        ("波段", ZIGZAG, "dash"),
        ("上轨", WEDGE_UPPER, "line"),
        ("下轨", WEDGE_LOWER, "line"),
        ("POC近似", VP_POC, "dash"),
        (f"上攻{bull_ftag}", PROJ_BULL, "dash" if bull_dash else "line"),
        (f"震荡{neut_ftag}", PROJ_NEUTRAL, "dash" if neut_dash else "line"),
        (f"下探{bear_ftag}", PROJ_BEAR, "dash" if bear_dash else "line"),
    ]
    leg_face = f_tiny
    leg_gap = 10
    leg_widths = [18 + text_size(draw, name, leg_face)[0] for name, _, _ in legend]
    total_leg_w = sum(leg_widths) + leg_gap * (len(legend) - 1) + 24
    title_w = text_size(draw, title, f_title)[0]
    if width - 24 - total_leg_w < left + title_w + 16:
        leg_face = f_tiny_sm
        leg_gap = 8
        leg_widths = [18 + text_size(draw, name, leg_face)[0] for name, _, _ in legend]
        total_leg_w = sum(leg_widths) + leg_gap * (len(legend) - 1) + 24
    leg_x1 = width - 24 - total_leg_w
    # 标题必须在图例左侧结束；长资产名（美股/港股全称）动态截断，禁止压到图例胶囊下方。
    max_title_w = max(120, leg_x1 - left - 16)
    fit_title = title
    if text_size(draw, fit_title, f_title)[0] > max_title_w:
        parts = fit_title.split(" · ", 1)
        if len(parts) == 2:
            head, tail = parts
            while head and text_size(draw, head + "… · " + tail, f_title)[0] > max_title_w:
                head = head[:-1]
            fit_title = (head + "… · " + tail) if head else tail
        if text_size(draw, fit_title, f_title)[0] > max_title_w:
            while fit_title and text_size(draw, fit_title + "…", f_title)[0] > max_title_w:
                fit_title = fit_title[:-1]
            fit_title += "…"
    draw.text((left, 16), fit_title, font=f_title, fill=TEXT)
    draw.rounded_rectangle((leg_x1, 16, width - 24, 44), radius=7, fill=CARD_BG, outline=CARD_BORDER, width=1)
    lx = leg_x1 + 12
    for (name, color, kind), item_w in zip(legend, leg_widths):
        if kind == "box":
            draw.rounded_rectangle((lx, 24, lx + 12, 36), radius=3, fill=color)
        elif kind == "dash":
            draw.line((lx, 30, lx + 5, 30), fill=color, width=2.4)
            draw.line((lx + 8, 30, lx + 13, 30), fill=color, width=2.4)
        else:
            draw.line((lx, 30, lx + 13, 30), fill=color, width=2.8)
            draw.ellipse((lx + 4, 27.5, lx + 9, 32.5), fill=color)
        draw.text((lx + 17, 22), name, font=leg_face, fill=TEXT)
        lx += item_w + leg_gap

    # ---------------- 顶部 5 卡片 KPI 仪表盘 (Y: 76 ~ 142) ----------------
    if scenario:
        scen_title = f"④ {scenario['label']}（B点 {scenario['b_end']['day'][5:]} {fp(scenario['b_end']['price'])}）"
        scen_line1 = " | ".join(f"{l['ratio']:.3f}位 {fp(l['price'])}" for l in scenario["levels"][:3])
        scen_line2 = (
            f"A {scenario['a_start']['day'][5:]}→{scenario['a_end']['day'][5:]}  |  "
            f"C/A 已走 {scenario['c_progress']['c_over_a']:.2f}"
        )
    else:
        scen_title = "④ ABC测算"
        scen_line1 = "暂无合格B浪，不划分ABC"
        scen_line2 = f"窗口高 {fp(float(hi_p))} ({hi['day'][5:]}) / 低 {fp(float(lo_p))} ({lo['day'][5:]})"

    cards = [
        (
            "① 最新标的位置",
            f"{view[-1]['day']} 收 {fp(float(last_c))} ({daily_chg_pct:+.2f}%)",
            f"盘中 {fp(active_intra['close'])} ({active_intra['stamp'][11:16]})"
            if active_intra
            else f"处 {len(view)}日区间 {range_pct:.1f}% 分位",
            UP if daily_chg >= 0 else DOWN,
        ),
        (
            "② 均线位置与偏离",
            f"MA20: {fp(ma20_val)} ({fp(float(ref_p) - ma20_val)}点)" if ma20_val else "MA20: --",
            f"MA60: {fp(ma60_val)}  |  MA120: {fp(ma120_val)}" if (ma60_val and ma120_val) else "",
            MA[20],
        ),
        (
            f"③ 结构：{wedge['pattern']}",
            f"两轨已于 {wedge['apex_day'] or '近期'} 前后交汇，形态完成"
            if wedge["expired"]
            else f"{rail_when}上轨 {fp(wedge['upper_today'])} | 下轨 {fp(wedge['lower_today'])}",
            wedge["status_short"],
            WEDGE_UPPER,
        ),
        (scen_title, scen_line1, scen_line2, SCEN),
        (
            "⑤ 三路径目标（收盘确认）",
            f"{up_word} {fp(projections['bull']['target_price'])} | 黄 {fp(projections['neutral']['target_price'])} | {down_word} {fp(projections['bear']['target_price'])}",
            f"突破位 {fp(projections['breakout_level']['price'])} / 破位 {fp(projections['breakdown_level']['price'])}",
            PROJ_BULL,
        ),
    ]
    card_gap = 10
    total_card_w = width - left - 24
    card_w = (total_card_w - card_gap * (len(cards) - 1)) // len(cards)
    for idx_c, (c_title, c_line1, c_line2, accent_col) in enumerate(cards):
        cx1 = left + idx_c * (card_w + card_gap)
        cy1, cx2, cy2 = 76, cx1 + card_w, 142
        draw.rounded_rectangle((cx1 + 1, cy1 + 2, cx2 + 1, cy2 + 2), radius=7, fill=(0, 0, 0, 90))
        draw.rounded_rectangle((cx1, cy1, cx2, cy2), radius=7, fill=CARD_BG, outline=CARD_BORDER, width=1.2)
        draw.rounded_rectangle((cx1, cy1, cx2, cy1 + 24), radius=7, fill=(*accent_col, 16))
        draw.rounded_rectangle((cx1, cy1, cx1 + 4, cy2), radius=2, fill=accent_col)
        draw.text((cx1 + 13, cy1 + 5), c_title, font=f_card_title, fill=accent_col)
        val_face = f_card_val if text_size(draw, c_line1, f_card_val)[0] <= card_w - 22 else f_card_val_sm
        # 价格按价位表精度显示后可能更长（如 5 位数指数带 2 位小数）：小字号仍放不下时从末尾逐段收起，绝不越出卡片
        while text_size(draw, c_line1, val_face)[0] > card_w - 22 and " | " in c_line1:
            c_line1 = c_line1.rsplit(" | ", 1)[0] + " …"
        while text_size(draw, c_line1, val_face)[0] > card_w - 22 and len(c_line1) > 2:
            c_line1 = c_line1[:-2] + "…"
        val_y = cy1 + 25 if val_face is f_card_val else cy1 + 27
        draw.text((cx1 + 13, val_y), c_line1, font=val_face, fill=TEXT)
        draw.text((cx1 + 13, cy1 + 45), c_line2, font=f_tiny, fill=MUTED)

    # ---------------- 主图坐标系与预判区分界背景 ----------------
    # 历史区与推演区各用各的横向比例：推演区占的宽度固定（等于默认 120 日图的比例），历史区按 --days 均分
    span = width - left - right
    fut_w = span * future_slots / (struct_slots + future_slots)
    step = (span - fut_w) / len(plot)
    fstep = fut_w / future_slots
    bar_half = max(1.2 if step < 4 else 2.2, step * 0.35)  # --days 很大时K线变窄，避免实体互相重叠

    def x_of(index: float) -> int:
        if index <= last_bar_idx:
            return int(round(left + (index + 0.5) * step))
        return int(round(left + (last_bar_idx + 0.5) * step + (index - last_bar_idx) * fstep))

    def yp(price: float) -> int:
        return y_of(price, pmin, pmax, top, main_bottom)

    def rail_segment(rail: dict, xa: float, xb: float) -> tuple[tuple[int, int], tuple[int, int]] | None:
        clipped = clip_to_price_range(xa, rail_at(rail, xa), xb, rail_at(rail, xb), pmin, pmax)
        if not clipped:
            return None
        cx1, cp1, cx2, cp2 = clipped
        return (x_of(cx1), yp(cp1)), (x_of(cx2), yp(cp2))

    div_x = x_of(last_bar_idx + 0.6)
    chart_right = width - right

    # 主图面板底色 + 右侧预判区微暗底色 + 楔形 RGBA 半透明通道填充
    draw.rectangle((left, top, chart_right, main_bottom), fill=PANEL_BG)
    draw.rectangle((div_x, top, chart_right, main_bottom), fill=(20, 28, 44, 130))
    ext_slots = min(14, int(future_slots * 0.48))
    ext_idx = last_bar_idx + ext_slots
    fill_x1 = max(upper_rail["x1"], lower_rail["x1"], 0)
    fill_x2 = ext_idx
    if wedge["apex_bars_ahead"] is not None:
        fill_x2 = min(fill_x2, last_bar_idx + wedge["apex_bars_ahead"])
    if fill_x2 > fill_x1:
        poly = []
        xs = [fill_x1] + ([last_bar_idx] if fill_x1 < last_bar_idx < fill_x2 else []) + [fill_x2]  # 两区比例不同，在「今日」处加折点
        for xv in xs:
            poly.append((x_of(xv), yp(min(max(rail_at(upper_rail, xv), pmin), pmax))))
        for xv in reversed(xs):
            poly.append((x_of(xv), yp(min(max(rail_at(lower_rail, xv), pmin), pmax))))
        draw.polygon(poly, fill=(56, 189, 248, 22))

    # 网格线与左侧价格刻度（刻度放在主图外侧，不压K线）
    date_step = max(15, len(view) // 8)
    for i in range(0, len(view), date_step):
        if len(view) - 1 - i >= 8:
            gx = x_of(i)
            draw.line((gx, top, gx, main_bottom), fill=(*GRID, 150), width=1)

    for frac in range(7):
        price = pmin + (pmax - pmin) * frac / 6
        y = yp(price)
        draw.line((left, y, chart_right, y), fill=GRID, width=1)
        draw.line((left - 4, y, left, y), fill=AXIS_LINE, width=1)
        label = fp(price)
        tw, th = text_size(draw, label, f_tiny)
        draw.text((left - 8 - tw, y - th // 2 - 1), label, font=f_tiny, fill=MUTED)
    draw.rectangle((left, top, chart_right, main_bottom), outline=AXIS_LINE, width=1.2)

    if off > 0:  # 主图比结构窗口长：标出结构拟合起点
        sx = x_of(off - 0.5)
        dashed_line(draw, (sx, top), (sx, main_bottom), fill=(*AXIS_LINE, 210), width=1.4, dash=5, gap=6)
        fit_txt = f"近{len(sview)}日结构拟合区 →"
        ftw, fth = text_size(draw, fit_txt, f_tiny)
        draw.rounded_rectangle((sx + 6, main_bottom - fth - 12, sx + ftw + 18, main_bottom - 4), radius=4, fill=(19, 26, 40, 210))
        draw.text((sx + 12, main_bottom - fth - 9), fit_txt, font=f_tiny, fill=MUTED)

    dashed_line(draw, (div_x, top), (div_x, main_bottom), fill=(96, 165, 250, 220), width=1.8, dash=8, gap=6)
    scen_cnt = len(projections.get("scenarios") or [])
    zone_txt = (
        f"规则推演区(三路径+{scen_cnt}情景) · 线宽按频率 · 虚线=低样本(n<30)"
        if scen_cnt
        else f"规则推演区 · {up_word}:上攻 / 黄:震荡 / {down_word}:下探（非预测）"
    )
    ztw, _ = text_size(draw, zone_txt, f_tiny)
    z_right = min(chart_right - 8, div_x + 34 + ztw)
    draw.rounded_rectangle(
        (div_x + 8, top + 8, z_right, top + 33),
        radius=5,
        fill=(25, 36, 56, 235),
        outline=(96, 165, 250, 120),
        width=1,
    )
    # 矢量播放三角图标（避免字体缺字显示方框）
    draw.polygon([(div_x + 17, top + 15), (div_x + 17, top + 25), (div_x + 25, top + 20)], fill=(96, 165, 250))
    draw.text((div_x + 30, top + 12), zone_txt, font=f_tiny, fill=TEXT)

    # 楔形上下轨：形态内柔光实线，预判区细虚线延伸；两轨交汇（收敛顶点）之后不再画
    rail_end = last_bar_idx + wedge["apex_bars_ahead"] if wedge["apex_bars_ahead"] is not None else float("inf")
    for rail, color in ((upper_rail, WEDGE_UPPER), (lower_rail, WEDGE_LOWER)):
        solid_start, solid_end = max(rail["x1"], 0), min(last_bar_idx, rail_end)
        seg = rail_segment(rail, solid_start, solid_end) if solid_end > solid_start else None
        if seg:
            draw.line([seg[0], seg[1]], fill=(*color, 48), width=6.5)
            draw.line([seg[0], seg[1]], fill=color, width=2.8)
        dash_end = min(ext_idx, rail_end)
        seg = rail_segment(rail, last_bar_idx, dash_end) if dash_end > last_bar_idx else None
        if seg:
            dashed_line(draw, seg[0], seg[1], fill=(*color, 210), width=2.0, dash=6, gap=4)
        for pv in rail_pivots:
            if pv["confirmed"] and pv["day"] in rail["touches"]:
                tx, ty = x_of(pv["idx"]), yp(pv["price"])
                draw.ellipse((tx - 6.5, ty - 6.5, tx + 6.5, ty + 6.5), fill=(*color, 45))
                draw.ellipse((tx - 4.2, ty - 4.2, tx + 4.2, ty + 4.2), fill=PANEL_BG, outline=color, width=2)

    # ---------------- 波段骨架连线（全部 ZigZag 拐点） ----------------
    zz_pts = [(x_of(p["idx"]), yp(p["price"])) for p in disp_pivots]
    for i in range(len(zz_pts) - 1):
        dashed_line(draw, zz_pts[i], zz_pts[i + 1], fill=(*ZIGZAG, 195), width=1.8, dash=6, gap=5)

    # ---------------- 绘制 Volume Profile (POC / VAH / VAL) 与 ABC / 黄金分割水平虚线 ----------------
    right_gutter_tags = []
    abc_ys: list[int] = []
    if vp_data and vp_data.get("poc_price") is not None:
        poc_f = float(vp_data["poc_price"])
        vah_f = float(vp_data.get("vah_price") or poc_f)
        val_f = float(vp_data.get("val_price") or poc_f)
        if pmin <= vah_f <= pmax and pmin <= val_f <= pmax and vah_f > val_f:
            y_vah, y_val = yp(vah_f), yp(val_f)
            draw.rectangle((left + 1, min(y_vah, y_val), div_x, max(y_vah, y_val)), fill=(99, 102, 241, 20))
            dashed_hline(draw, left, div_x, y_vah, (*VP_VA_BORDER, 130))
            dashed_hline(draw, left, div_x, y_val, (*VP_VA_BORDER, 130))
        if pmin <= poc_f <= pmax:
            y_poc = yp(poc_f)
            abc_ys.append(y_poc)
            dashed_hline(draw, left, chart_right, y_poc, (*VP_POC, 195))
            right_gutter_tags.append((y_poc, f"POC 成交分布近似 {fp(poc_f)}", VP_POC))
    if scenario:
        for level in scenario["levels"]:
            if not level["drawn"]:
                continue
            y = yp(level["price"])
            abc_ys.append(y)
            dashed_hline(draw, left, chart_right, y, (*SCEN, 185))
            live_tag = f"·{level['intraday']}" if level["intraday"] else ""
            right_gutter_tags.append((y, f"ABC {level['ratio']:.3f}  {fp(level['price'])} ({level['state']}{live_tag})", SCEN))
    elif fibs and fibs.get("retracements"):
        for flvl in fibs["retracements"]:
            if flvl["ratio"] not in (0.382, 0.5, 0.618):
                continue
            fp_val = float(flvl["price"])
            if pmin <= fp_val <= pmax:
                y_fib = yp(fp_val)
                abc_ys.append(y_fib)
                dashed_hline(draw, left, div_x, y_fib, (*SCEN, 140))
                right_gutter_tags.append((y_fib, f"Fib {flvl['ratio']:.3f}{fibs.get('kind', '')} {fp(fp_val)}", SCEN))

    # ---------------- 绘制真实 K 线与盘中 K 线 ----------------
    kline_obstacles: list[tuple[int, int, int, int]] = []
    for i, bar in enumerate(plot):
        x = x_of(i)
        is_up = bar["close"] >= bar["open"]
        color = INTRA if not bar["complete"] else (UP if is_up else DOWN)
        y_high, y_low = yp(bar["high"]), yp(bar["low"])
        y_open, y_close = yp(bar["open"]), yp(bar["close"])
        wick = 2.6 if not bar["complete"] else (1.5 if step >= 4.5 else 1.2)
        draw.line((x, y_high, x, y_low), fill=color, width=wick)
        body_top, body_bot = sorted((y_open, y_close))
        if body_bot - body_top < 1.5:
            body_bot = body_top + 1.5
        half = max(4.0, step * 0.42) if not bar["complete"] else bar_half
        draw.rectangle((x - half, body_top, x + half, body_bot), fill=color)
        kline_obstacles.append((int(x - half - 2), int(y_high - 4), int(x + half + 2), int(y_low + 4)))

    # ---------------- 绘制均线（在K线之上叠绘带暗晕的平滑曲线，清晰不糊） ----------------
    for n, color in MA.items():
        pts = []
        for i in range(len(view)):
            value = ma[n][view_start + i]
            if value is not None:
                pts.append((x_of(i), yp(value)))
        if len(pts) > 1:
            draw.line(pts, fill=(*PANEL_BG, 150), width=3.8, joint="curve")
            draw.line(pts, fill=color, width=2.0, joint="curve")

    # ---------------- 绘制右侧 15 日保形预测扇形（80% 外扇 + 50% 内扇）与三条预判路径 ----------------
    if conformal_forecast and conformal_forecast.get("q10") and conformal_forecast.get("q90"):
        q10_s = conformal_forecast["q10"]
        q25_s = conformal_forecast["q25"]
        q75_s = conformal_forecast["optimistic_q75"]
        q90_s = conformal_forecast["q90"]
        h_len = len(q10_s)
        if h_len > 0:
            # 第 h 步画在第 h 个未来交易日槽位上（与推演折点同一时间轴，不再按推演终点拉伸）
            start_pt = (x_of(last_bar_idx), yp(float(ref_p)))
            fan80 = [start_pt]
            fan50 = [start_pt]
            for idx_h in range(h_len):
                slot_x = x_of(last_bar_idx + idx_h + 1)
                fan80.append((slot_x, yp(min(max(float(q90_s[idx_h]), pmin), pmax))))
                fan50.append((slot_x, yp(min(max(float(q75_s[idx_h]), pmin), pmax))))
            for idx_h in range(h_len - 1, -1, -1):
                slot_x = x_of(last_bar_idx + idx_h + 1)
                fan80.append((slot_x, yp(min(max(float(q10_s[idx_h]), pmin), pmax))))
                fan50.append((slot_x, yp(min(max(float(q25_s[idx_h]), pmin), pmax))))
            draw.polygon(fan80, fill=(56, 189, 248, 28))
            draw.polygon(fan50, fill=(56, 189, 248, 48))

    veto_box: tuple[int, int, int, int] | None = None
    if veto_flag:
        reasons_txt = "；".join((risk_veto or {}).get("veto_reasons") or ["风控条件未通过"])
        v_banner = f"【风控否决】{reasons_txt}"
        max_vw = chart_right - div_x - 30
        v_lines = _wrap_text(draw, v_banner, f_tiny_sm, max_vw)
        vtw = max(text_size(draw, line, f_tiny_sm)[0] for line in v_lines)
        vx2 = min(chart_right - 8, div_x + 14 + vtw)
        veto_box = (div_x + 8, top + 37, vx2, top + 45 + 15 * len(v_lines))
        draw.rounded_rectangle(
            veto_box,
            radius=5,
            fill=(127, 29, 29, 230),
            outline=(239, 68, 68, 200),
            width=1.2,
        )
        for idx, line in enumerate(v_lines):
            draw.text((div_x + 14, top + 41 + idx * 15), line, font=f_tiny_sm, fill=(254, 226, 226))

    proj_configs = [
        ("bull", PROJ_BULL, bull_dash, bull_w, bull_a, f"上攻 {fp(projections['bull']['target_price'])}{bull_ftag}"),
        ("neutral", PROJ_NEUTRAL, neut_dash, neut_w, neut_a, f"震荡 {fp(projections['neutral']['target_price'])}{neut_ftag}"),
        ("bear", PROJ_BEAR, bear_dash, bear_w, bear_a, f"下探 {fp(projections['bear']['target_price'])}{bear_ftag}"),
    ]
    dup_tol = float(ref_p) * 0.003
    skip_labels = set()
    if abs(projections["neutral"]["range"][1] - projections["breakout_level"]["price"]) <= dup_tol:
        skip_labels.add(projections["neutral"]["step_labels"][0][1])
    if abs(projections["neutral"]["range"][0] - projections["breakdown_level"]["price"]) <= dup_tol:
        skip_labels.add(projections["neutral"]["step_labels"][1][1])

    all_proj_pts: dict[str, list[tuple[int, int]]] = {}
    all_proj_segs: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for key, col, is_dashed, w_val, a_val, tag_text in proj_configs:
        waypoints = projections[key]["waypoints_offset"]
        pts = [(x_of(last_bar_idx + dx), yp(p)) for dx, p in waypoints]
        all_proj_pts[key] = pts
        for i in range(len(pts) - 1):
            all_proj_segs.append((pts[i], pts[i + 1]))
        draw_arrow_polyline(draw, pts, fill=col, width=w_val, dashed=is_dashed, alpha=a_val)
        right_gutter_tags.append((pts[-1][1], tag_text, col))

    # 同步叠绘 L1 活跃情景中与三主路径目标位不同的独立情景路径，保证 PNG 与 HTML 同源
    main_targets = [float(projections[k]["target_price"]) for k in ("bull", "neutral", "bear")]
    for sc_item in projections.get("scenarios") or []:
        sc_tgt = float(sc_item.get("target_price") or 0.0)
        if sc_tgt <= 0 or any(abs(sc_tgt - mt) <= dup_tol for mt in main_targets):
            continue
        sc_wps = sc_item.get("waypoints_offset") or []
        if len(sc_wps) < 2:
            continue
        sc_dir = str(sc_item.get("direction") or "NEUTRAL")
        sc_col = PROJ_BULL if sc_dir == "BULLISH" else (PROJ_BEAR if sc_dir == "BEARISH" else PROJ_NEUTRAL)
        sc_hf = sc_item.get("historical_frequency") or {}
        sc_freq = max(0.0, min(1.0, float(sc_hf.get("target_freq") or 0.35)))
        sc_n = int(sc_hf.get("sample_size") or 0)
        sc_low = bool(sc_hf.get("low_sample", sc_n < 30))
        sc_raw = float(sc_hf["raw_target_freq"]) if sc_hf.get("raw_target_freq") is not None else sc_freq
        sc_pct = f"{sc_raw * 100:.1f}%" if sc_n > 0 else f"先验{sc_freq * 100:.0f}%"
        sc_pts = [(x_of(last_bar_idx + dx), yp(p)) for dx, p in sc_wps]
        draw_arrow_polyline(draw, sc_pts, fill=sc_col, width=round(1.6 + 1.8 * sc_freq, 2), dashed=True, alpha=165 if sc_low else 200)
        sc_low_tag = "[低样本] " if sc_low else ""
        right_gutter_tags.append((sc_pts[-1][1], f"{sc_item['name']} {fp(sc_tgt)} {sc_low_tag}{sc_pct}(n={sc_n})", sc_col))

    step_boxes: list[tuple[int, int, int, int]] = []
    for key, col, _, _, _, _ in proj_configs:
        waypoints = projections[key]["waypoints_offset"]
        pts = all_proj_pts[key]
        for dx, step_txt in projections[key]["step_labels"]:
            k = next((j for j, (d, _) in enumerate(waypoints) if d == dx), None)
            if k is None or k == 0 or k == len(waypoints) - 1 or step_txt in skip_labels:
                continue
            sx, sy = pts[k]
            tw, th = text_size(draw, step_txt, f_tiny_bold)
            bw_s, bh_s = tw + 12, th + 6
            is_peak = waypoints[k][1] >= max(waypoints[k - 1][1], waypoints[k + 1][1])
            dy_cands = [-bh_s - 10, -bh_s - 26, -bh_s - 42, 10, 26, 42] if is_peak else [10, 26, 42, -bh_s - 10, -bh_s - 26, -bh_s - 42]
            dx_cands = [0, -14, 14, -26, 26]
            best_box = None
            best_sc = float("inf")
            step_top_bound = (veto_box[3] + 6) if veto_box is not None else (top + 36)
            for r_y, dy_c in enumerate(dy_cands):
                for r_x, dx_c in enumerate(dx_cands):
                    bx1 = min(max(div_x + 4, sx - bw_s // 2 + dx_c), chart_right - bw_s - 4)
                    by1 = min(max(step_top_bound, sy + dy_c), main_bottom - bh_s - 6)
                    cand = (bx1, by1, bx1 + bw_s, by1 + bh_s)
                    if any(not (cand[2] + 4 < b[0] or cand[0] - 4 > b[2] or cand[3] + 4 < b[1] or cand[1] - 4 > b[3]) for b in step_boxes):
                        continue
                    sc = r_y * 12.0 + r_x * 6.0
                    for seg_a, seg_b in all_proj_segs:
                        d_seg = _seg_box_dist(seg_a, seg_b, cand)
                        if d_seg < 4:
                            sc += (5.0 - d_seg) * 65.0
                    for ay in abc_ys:
                        if cand[1] - 2 <= ay <= cand[3] + 2:
                            sc += 90.0
                    if sc < best_sc:
                        best_sc = sc
                        best_box = cand
            if best_box is None:
                ly = (sy - bh_s - 10) if is_peak else (sy + 10)
                bx1 = min(max(div_x + 4, sx - bw_s // 2), chart_right - bw_s - 4)
                best_box = (bx1, ly, bx1 + bw_s, ly + bh_s)
            step_boxes.append(best_box)
            cy_mid = (best_box[1] + best_box[3]) // 2
            cx_mid = min(max(best_box[0] + 6, sx), best_box[2] - 6)
            near_y = best_box[3] if best_box[3] < sy else (best_box[1] if best_box[1] > sy else cy_mid)
            if math.hypot(cx_mid - sx, near_y - sy) > 8:
                draw.line((sx, sy, cx_mid, near_y), fill=(*col, 140), width=1.2)
            draw.rounded_rectangle(
                best_box,
                radius=4,
                fill=(16, 22, 34, 235),
                outline=(*col, 150),
                width=1,
            )
            draw.text((best_box[0] + 6, best_box[1] + 2), step_txt, font=f_tiny_bold, fill=col)

    # 将今日上下轨与最新价也放入右侧专属标签槽（形态已越过收敛顶点时轨道不再作为今日参考，不放）
    upper_tag = "形态上轨" if wedge["upper_today"] > float(ref_p) else "上轨(已突破)"
    lower_tag = "形态下轨" if wedge["lower_today"] < float(ref_p) else "下轨(已跌破)"
    for rail_val, rail_tag, rail_col in (
        (wedge["upper_today"], upper_tag, WEDGE_UPPER),
        (wedge["lower_today"], lower_tag, WEDGE_LOWER),
    ):
        if pmin <= rail_val <= pmax and not wedge["expired"]:
            right_gutter_tags.append((yp(rail_val), f"{rail_tag}  {fp(rail_val)}", rail_col))
    right_gutter_tags.append((yp(float(ref_p)), f"当前位置  {fp(float(ref_p))}", INTRA if active_intra else TEXT))

    # ---------------- 绘制右侧专属标签槽 (Right Gutter，折线引线 + 零遮挡) ----------------
    placed_tags = []
    tag_bottom = top + 6
    for y, label, col in sorted(right_gutter_tags, key=lambda item: item[0]):
        tw, th = text_size(draw, label, f_tiny)
        bh = th + 9
        ty = max(y - bh // 2, tag_bottom)
        tag_bottom = ty + bh + 5
        placed_tags.append([y, ty, tw, bh, label, col])
    next_top = main_bottom - 4
    for item in reversed(placed_tags):
        orig_y, ty, tw, bh, label, col = item
        if ty + bh > next_top:
            ty = next_top - bh
            item[1] = ty
        next_top = ty - 5
    for orig_y, ty, tw, bh, label, col in placed_tags:
        gx1 = chart_right + 14
        gx2 = width - 8
        mid_y = ty + bh // 2
        draw.ellipse((chart_right - 2, orig_y - 2, chart_right + 2, orig_y + 2), fill=col)
        draw.line([(chart_right, orig_y), (chart_right + 5, orig_y), (gx1 - 4, mid_y), (gx1, mid_y)], fill=(*col, 195), width=1.4, joint="curve")
        draw.rounded_rectangle((gx1, ty, gx2, ty + bh), radius=4, fill=(19, 25, 38, 242), outline=(*col, 165), width=1.1)
        draw.rounded_rectangle((gx1, ty, gx1 + 4, ty + bh), radius=2, fill=col)
        tag_face = f_tiny if tw <= (gx2 - gx1 - 16) else f_tiny_sm
        draw.text((gx1 + 10, ty + (4 if tag_face is f_tiny else 5)), label, font=tag_face, fill=col)

    # ---------------- 标注关键拐点：窗口高低点、ABC 三点、轨道支点、最新K线 ----------------
    occupied_badges: list[tuple[int, int, int, int]] = [(div_x + 4, top + 6, z_right + 4, top + 35)]
    if veto_box is not None:
        occupied_badges.append((veto_box[0] - 4, veto_box[1] - 2, veto_box[2] + 4, veto_box[3] + 2))
    occupied_badges += step_boxes  # 最新K线徽标可伸入推演区 48px，须避开已画好的推演步骤标签
    marks: list[dict] = []

    def mark(idx: int, price: float, tag: str, color: tuple[int, int, int], side: str, extra: str = "") -> None:
        if idx < 0:  # 结构层的点落在主图左边界之外（--days < STRUCT_DAYS）
            return
        for m in marks:  # 同一根K线同一价位只画一个徽标，标签合并成「最低·C浪低」
            if m["idx"] == idx and abs(m["price"] - price) <= price * 0.0005:
                if tag not in m["tags"]:
                    m["tags"].append(tag)
                m["extra"] = m["extra"] or extra
                return
        marks.append({"idx": idx, "price": price, "tags": [tag], "color": color, "side": side, "extra": extra})

    mark(hi_idx, hi["high"], "最高", UP, "above")
    mark(lo_idx, lo["low"], "最低", DOWN, "below")
    if scenario:
        down = scenario["direction"] == "down"
        a_s, a_e, b_e, c_p = scenario["a_start"], scenario["a_end"], scenario["b_end"], scenario["c_progress"]
        a_pct = (a_e["price"] - a_s["price"]) / a_s["price"] * 100
        b_pct = (b_e["price"] - a_e["price"]) / a_e["price"] * 100
        mark(a_s["idx"] + off, a_s["price"], "A起点", SCEN, "above" if down else "below")
        mark(a_e["idx"] + off, a_e["price"], f"A浪{'低' if down else '高'}", SCEN, "below" if down else "above", f" ({a_pct:+.1f}%)")
        mark(b_e["idx"] + off, b_e["price"], f"B浪{'高' if down else '低'}", SCEN, "above" if down else "below", f" ({b_pct:+.1f}%)")
        if c_p["c_over_a"] >= 0.15 and c_p["idx"] + off <= last_bar_idx:
            mark(c_p["idx"] + off, c_p["extreme"], f"C浪{'低' if down else '高'}", SCEN, "below" if down else "above", f" (C/A {c_p['c_over_a']:.2f})")
    for rail, col, side, tag in ((upper_rail, WEDGE_UPPER, "above", "上轨点"), (lower_rail, WEDGE_LOWER, "below", "下轨点")):
        for x_key, p_key in (("x1", "p1"), ("x2", "p2")):
            mark(rail[x_key], rail[p_key], tag, col, side)
    recent_avg = sum(b["close"] for b in plot[-12:]) / min(12, len(plot))
    last_side = "below" if plot[-1]["close"] < recent_avg else "above"
    mark(last_bar_idx, plot[-1]["close"], "盘中" if not plot[-1]["complete"] else "最新收", INTRA, last_side)

    sorted_marks = sorted(marks, key=lambda item: (item["idx"], -item["price"] if item["side"] == "above" else item["price"]))
    peer_anchors = [(x_of(m["idx"]), yp(m["price"]), m["side"]) for m in sorted_marks]
    for m in sorted_marks:
        text = f"{plot[m['idx']]['day'][5:]} {'·'.join(m['tags'])} {fp(m['price'])}{m['extra']}"
        draw_callout_badge(
            draw,
            x_of(m["idx"]),
            yp(m["price"]),
            text,
            f_tiny,
            m["color"],
            m["side"],
            left,
            div_x + 48,
            top + 36,
            main_bottom - 6,
            occupied_badges,
            obstacles=kline_obstacles,
            peer_anchors=peer_anchors,
        )

    # ---------------- 绘制 X 轴历史日期刻度 + 预判区未来时间刻度 ----------------
    for i in range(0, len(view), date_step):
        if len(view) - 1 - i < 8:
            continue
        label = view[i]["day"] if i == 0 else view[i]["day"][5:]
        tw, _ = text_size(draw, label, f_tiny)
        lx = x_of(i)
        draw.line((lx, main_bottom, lx, main_bottom + 5), fill=AXIS_LINE, width=1.2)
        draw.text((lx - tw // 2, main_bottom + 8), label, font=f_tiny, fill=MUTED)

    now_label = plot[-1]["day"][5:] + ("(当前)" if plot[-1]["complete"] else "(盘中)")
    tw, _ = text_size(draw, now_label, f_tiny_sm)
    nx = x_of(last_bar_idx)
    draw.line((nx, main_bottom, nx, main_bottom + 6), fill=INTRA, width=2)
    draw.text((nx - tw // 2, main_bottom + 8), now_label, font=f_tiny_sm, fill=INTRA)

    # 预判区未来时间刻度（只跳过周末，未扣除节假日，故标「≈」）
    base_day = plot[-1]["day"]
    prev_right = nx + tw // 2
    # 刻度按预测期等分（约 1/3、2/3 与终点），与各路径的折点时间无关
    horizon_ticks = int(projections.get("horizon_bars") or projections["bull"]["waypoints_offset"][-1][0])
    for f_off in sorted({max(1, round(horizon_ticks * k / 3)) for k in (1, 2, 3)}):
        fx = x_of(last_bar_idx + f_off)
        f_date_str = f"≈+{f_off}日({add_trading_days_str(base_day, f_off)})"
        tw, _ = text_size(draw, f_date_str, f_tiny_sm)
        draw.line((fx, main_bottom, fx, main_bottom + 5), fill=PROJ_NEUTRAL, width=1.2)
        if fx - tw // 2 > prev_right + 4:  # 刻度文字放不下就只留刻度线
            draw.text((fx - tw // 2, main_bottom + 8), f_date_str, font=f_tiny_sm, fill=PROJ_NEUTRAL)
            prev_right = fx + tw // 2

    # ---------------- 绘制成交量副图 + MA5 / MA20 均量线 ----------------
    draw.rectangle((left, vol_top, chart_right, vol_bottom), fill=PANEL_BG, outline=AXIS_LINE, width=1.2)
    volumes = [bar["volume"] for bar in view]
    vol_ma5_series = sma(volumes, 5)
    vol_ma20_series = sma(volumes, 20)
    vmax = max(volumes) if any(volumes) else 1.0

    for frac, tag in ((0.0, "0"), (0.5, "50%"), (1.0, "100%")):
        y = vol_bottom - int(frac * (vol_bottom - vol_top - 12))
        if frac > 0:
            draw.line((left, y, chart_right, y), fill=GRID, width=1)
        draw.line((left - 4, y, left, y), fill=MUTED, width=1)
        tw, th = text_size(draw, tag, f_tiny)
        draw.text((left - 8 - tw, y - th // 2 - 1), tag, font=f_tiny, fill=MUTED)

    for i, bar in enumerate(view):
        x = x_of(i)
        h = int((bar["volume"] / vmax) * (vol_bottom - vol_top - 12))
        if h <= 0:
            continue
        color = UP if bar["close"] >= bar["open"] else DOWN
        y_top = vol_bottom - h
        draw.rectangle((x - bar_half, y_top, x + bar_half, vol_bottom - 1), fill=(*color, 140))
        draw.line((x - bar_half, y_top, x + bar_half, y_top), fill=color, width=1.5)

    for v_series, v_col in ((vol_ma5_series, (232, 238, 248)), (vol_ma20_series, MA[20])):
        v_pts = []
        for i, val in enumerate(v_series):
            if val is not None:
                vy = vol_bottom - int((val / vmax) * (vol_bottom - vol_top - 12))
                v_pts.append((x_of(i), vy))
        if len(v_pts) > 1:
            draw.line(v_pts, fill=(*PANEL_BG, 160), width=4.0, joint="curve")
            draw.line(v_pts, fill=v_col, width=2.0, joint="curve")

    vol_last = view[-1]["volume"]
    v5_last = vol_ma5_series[-1] or vol_last
    v20_last = vol_ma20_series[-1] or vol_last
    r_ma5 = round(vol_last / v5_last, 2) if v5_last else 1.0
    r_ma20 = round(vol_last / v20_last, 2) if v20_last else 1.0

    vol_header = (
        f"成交量相对高度（数据源 {data_source} 原始值，未折算金额）  ·  "
        f"白线: MA5均量（当前为 {r_ma5:.2f} 倍）  ·  黄线: MA20均量（当前为 {r_ma20:.2f} 倍）"
    )
    draw.text((left, vol_top - 24), vol_header, font=f_tiny, fill=MUTED)

    last_mas = [f"MA{n} {fp(float(money(ma[n][-1])))}" for n in MA if ma[n][-1] is not None]
    footer = "  |  ".join(last_mas)
    footer += f"  |  {'绿涨红跌' if color_scheme == 'us_green_up' else '红涨绿跌'}  |  右侧折线为规则推演（日线收盘确认，盘中刺破不算；未来日期未扣除节假日），不构成投资建议"
    draw.text((left, 1118), footer, font=f_tiny, fill=MUTED)

    # 数据不可信（合成/来源未知/缺基准）时：主图顶部红色横幅 + 主图区半透明大字水印，导出后无法被忽略
    if data_warning or quality_status != "OK":
        warn_msg = data_warning or f"数据质量状态：{quality_status}"
        banner_text = f"【数据质量警告】{warn_msg}"
        if quality_status == "UNAVAILABLE":
            draw.rectangle((0, 68, width, height), fill=BG)
        draw.rectangle((left, top, width - right, top + 40), fill=(185, 28, 28, 235))
        draw.text((left + 14, top + 10), banner_text[:110], font=f_card_val_sm, fill=(255, 255, 255))
        f_mark = font(56 if quality_status == "PARTIAL" else 72, bold=True, scale=scale)
        mark_text = (
            "非真实行情 · 禁止用于交易判断"
            if quality_status == "UNAVAILABLE"
            else "数据质量 PARTIAL · 缺少大盘基准或来源待核验"
        )
        mw, mh = text_size(draw, mark_text, f_mark)
        cx = (left + width - right) / 2
        cy = (top + main_bottom) / 2
        draw.text((cx - mw / 2, cy - mh / 2), mark_text, font=f_mark, fill=(239, 68, 68, 90))

    if not is_null_out:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if scale > 1:
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        image.save(out_path, "PNG")

    # ---------------- 组装 JSON 与小模型零失误直抄文本块 ----------------
    ma_dist = {}
    for n in (20, 60, 120):
        if ma[n][-1] is not None:
            m_val = money(ma[n][-1])
            diff = q2(ref_p - m_val)
            pct = float(q2(diff / m_val * Decimal("100")))
            ma_dist[f"vs_ma{n}"] = {
                "ma": float(m_val),
                "diff_pts": float(diff),
                "diff_pct": pct,
                "position": "站上" if diff >= 0 else "在线下承压",
            }

    completed_str = (
        f"截至最新已收盘交易日 {view[-1]['day']}，收盘价 {fp(float(last_c))}（涨跌 {fps(float(daily_chg))} 点 / {daily_chg_pct:+.2f}%）；"
        f"近 {len(view)} 个交易日内最高点为 {hi['day']} 的 {fp(float(hi_p))}，最低点为 {lo['day']} 的 {fp(float(lo_p))}，"
        f"当前处于该区间的 {range_pct:.2f}% 分位；"
        f"MA20 为 {fp(ma20_val)}（当前价距 MA20 {fps(ma_dist['vs_ma20']['diff_pts'])} 点），"
        f"MA60 为 {fp(ma60_val)}（距 MA60 {fps(ma_dist['vs_ma60']['diff_pts'])} 点），"
        f"MA120 为 {fp(ma120_val)}（距 MA120 {fps(ma_dist['vs_ma120']['diff_pts'])} 点）；"
        f"最新已收盘成交量为5日均量的 {r_ma5:.2f} 倍、20日均量的 {r_ma20:.2f} 倍。"
    )
    if active_intra:
        intra_chg = float(q2(money(active_intra["close"]) - money(active_intra["prev"])))
        intra_pct = (
            float(q2(Decimal(str(intra_chg)) / money(active_intra["prev"]) * Decimal("100")))
            if active_intra["prev"]
            else 0.0
        )
        intra_str = (
            f"盘中快照时间 {active_intra['stamp']}（未收盘），最新价 {fp(active_intra['close'])}"
            f"（较昨收 {fps(intra_chg)} 点 / {intra_pct:+.2f}%），"
            f"盘中最高 {fp(active_intra['high'])}，盘中最低 {fp(active_intra['low'])}。"
        )
    elif data_note:
        intra_str = f"今日已收盘，无盘中未收盘K线（{data_note}）。"
    else:
        intra_str = "当前为非盘中时段（已收盘），无盘中未收盘K线。"

    up_r, low_r = wedge["upper"], wedge["lower"]
    if wedge["fitted"]:
        if wedge["expired"]:
            upper_now = lower_now = ""
            apex_note = f"两轨已于 {wedge['apex_day'] or '近期'} 前后交汇（收敛顶点），原形态已完成，轨道外推值不再作为今日支撑压力；"
        else:
            upper_now = f"，{rail_when}上轨 {fp(wedge['upper_today'])}"
            lower_now = f"，{rail_when}下轨 {fp(wedge['lower_today'])}"
            apex_note = ""
        rails_str = (
            f"形态：【{wedge['pattern']}】。上轨连接 {up_r['start']['day']}({fp(up_r['start']['price'])}) 与 "
            f"{up_r['end']['day']}({fp(up_r['end']['price'])})，共 {len(up_r['touches'])} 个波段高点触及{upper_now}；"
            f"下轨连接 {low_r['start']['day']}({fp(low_r['start']['price'])}) 与 "
            f"{low_r['end']['day']}({fp(low_r['end']['price'])})，共 {len(low_r['touches'])} 个波段低点触及{lower_now}；"
            f"{apex_note}状态：{wedge['status_text']}。"
        )
    else:
        rails_str = (
            f"形态：【{wedge['pattern']}】，箱体上沿 {fp(wedge['upper_today'])}、下沿 {fp(wedge['lower_today'])}；"
            f"状态：{wedge['status_text']}。"
        )

    if scenario:
        scen_parts = []
        for lvl in scenario["levels"]:
            out_tag = "，画面外" if not lvl["drawn"] else ""
            live_tag = f"，{lvl['intraday']}（未收盘，不作确认）" if lvl["intraday"] else ""
            scen_parts.append(f"{lvl['ratio']:.3f} 倍位 {fp(lvl['price'])}【{lvl['state']}{live_tag}{out_tag}】")
        scen_str = (
            f"{scenario['label']}测算：A浪 {scenario['a_start']['day']} {fp(scenario['a_start']['price'])} → "
            f"{scenario['a_end']['day']} {fp(scenario['a_end']['price'])}（幅度 {fp(scenario['a_range'])} 点），"
            f"B浪至 {scenario['b_end']['day']} {fp(scenario['b_end']['price'])}；C浪测算位："
            + "，".join(scen_parts)
            + "。"
            + (f"盘中提示：{scenario['intraday_note']}。" if scenario["intraday_note"] else "")
        )
    else:
        scen_str = f"ABC测算：近{STRUCT_DAYS}日无满足「反向≥5%且收回≥0.382」的B浪，不做ABC测算。"

    def ladder_text(levels: list[dict]) -> str:
        return "、".join(f"{fp(lvl['price'])}（{_src(lvl)}）" for lvl in levels[:4]) or "无"

    gap_parts = [f"{g['day']} {g['type']} {fp(g['range'][0])}~{fp(g['range'][1])}" for g in gaps]
    wedge_and_levels_str = (
        rails_str
        + "\n"
        + scen_str
        + "\n"
        + f"上方压力（由近到远）：{ladder_text(ladder['resistances'])}；"
        + f"下方支撑（由近到远）：{ladder_text(ladder['supports'])}"
        + ("；近期未补缺口：" + "、".join(gap_parts) + "。" if gap_parts else "；近期无未回补日线缺口。")
    )

    proj_str = (
        f"① {projections['bull']['name']}：触发条件为{projections['bull']['trigger']}，"
        f"回踩守住 {fp(projections['bull']['retest_hold'])}，上方目标看至 {fp(projections['bull']['target_price'])}"
        f"（{projections['bull']['target_source']}；失效条件：{projections['bull']['invalidation']}）；\n"
        f"  ② {projections['neutral']['name']}：条件为{projections['neutral']['condition']}，"
        f"在 {fp(projections['neutral']['range'][0])}~{fp(projections['neutral']['range'][1])} 区间内围绕中枢 {fp(projections['neutral']['target_price'])} 震荡；\n"
        f"  ③ {projections['bear']['name']}：触发条件为{projections['bear']['trigger']}，"
        f"反抽不过 {fp(projections['bear']['rebound_cap'])}，下方目标看至 {fp(projections['bear']['target_price'])}"
        f"（{projections['bear']['target_source']}；失效条件：{projections['bear']['invalidation']}）。"
    )

    if scenario:
        c_p = scenario["c_progress"]
        deepest = c_p["deepest_level_reached"]
        reach_txt = f"，已触及 {deepest:.3f} 倍测算位" if deepest else "，尚未触及 0.382 倍测算位"
        wave_str = (
            f"浪型（{scenario['label']}）：A浪 {scenario['a_start']['day'][5:]}→{scenario['a_end']['day'][5:]}，"
            f"B浪收回A浪的 {scenario['b_retrace']:.2f} 倍至 {scenario['b_end']['day'][5:]}；"
            + (
                c_p["phase"]
                if c_p["phase"].startswith("B浪")
                else f"{c_p['phase']}，最远已到 {fp(c_p['extreme'])}（{c_p['day'][5:]}，C/A={c_p['c_over_a']:.2f}{reach_txt}）"
            )
            + "。"
        )
    else:
        wave_str = f"浪型：近{STRUCT_DAYS}日内没有合格的B浪，暂不划分ABC。"
    verdict_str = (
        f"结构：【{wedge['pattern']}】，{wedge['status_text']}。"
        + wave_str
        + "以上为规则化划分，属于主观技术分析，不是确定性预测。"
    )

    bg = background
    bg_parts = [
        f"大级别背景（近{bg['days']}日，{bg['start_day']} → {bg['end_day']}）：区间最高为 {bg['high']['day']} 的 {fp(bg['high']['price'])}，"
        f"最低为 {bg['low']['day']} 的 {fp(bg['low']['price'])}，当前价处于该区间的 {bg['percentile']:.2f}% 分位"
    ]
    if bg["ma250"]:
        m250 = bg["ma250"]
        bg_parts.append(
            f"MA250（年线）为 {fp(m250['value'])}，当前价{'站上年线' if m250['diff_pct'] >= 0 else '位于年线下方'}"
            f"（偏离 {m250['diff_pct']:+.2f}%），年线近20日{m250['direction']}（{m250['chg_20d_pct']:+.2f}%）"
        )
    if bg["ma120"]:
        bg_parts.append(f"MA120 近20日{bg['ma120']['direction']}（{bg['ma120']['chg_20d_pct']:+.2f}%）")
    bg_parts.append(
        f"长周期{bg['fib']['kind']}：" + "、".join(f"{lvl['ratio']:.3f} 位 {fp(lvl['price'])}" for lvl in bg["fib"]["retracements"])
    )
    background_str = "；".join(bg_parts) + f"。以上只描述大级别位置，不参与短线触发位和目标位；短线结构与三条推演只基于近{len(sview)}日。"

    return {
        "file": str(out_path),
        "data_source": data_source,
        "data_warning": data_warning,
        "quality_status": quality_status,
        "risk_officer_veto": veto_flag,
        "color_scheme": color_scheme,
        "completed_last": view[-1]["day"],
        "data_note": data_note,
        "window_high": window_high,
        "window_low": window_low,
        "last_close": float(last_c),
        "ma20": ma20_val,
        "ma60": ma60_val,
        "ma120": ma120_val,
        "ma250": None if ma[250][-1] is None else float(money(ma[250][-1])),
        "display_days": len(view),
        "structure_window": {
            "days": len(sview),
            "start_day": sview[0]["day"],
            "end_day": sview[-1]["day"],
            "note": "wedge/scenario/fib_levels/key_levels/recent_gaps/swing/projections 只用这段数据，其中的 idx 是这段窗口内的下标",
        },
        "background": background,
        "position": {
            "reference_price": float(ref_p),
            "reference_type": "盘中最新" if active_intra else "已收盘",
            "last_completed_change_pts": float(daily_chg),
            "last_completed_change_pct": daily_chg_pct,
            "window_percentile": range_pct,
            "window_120d_percentile": range_pct,  # 向下兼容旧版接口
            "ma_distance": ma_dist,
        },
        "volume_context": {
            "vol_vs_ma5_ratio": r_ma5,
            "vol_vs_ma20_ratio": r_ma20,
        },
        "swing": {
            "zigzag_threshold_pct": round(zz_thr * 100, 2),
            "atr20_pct": round(atr * 100, 2),
            "pivots": pivots,
        },
        "wedge": wedge,
        "fib_levels": fibs,
        "key_levels": ladder,
        "recent_gaps": gaps,
        "scenario": scenario,
        "projections": projections,
        "intraday": None
        if not active_intra
        else {
            "stamp": active_intra["stamp"],
            "open": float(money(active_intra["open"])),
            "high": float(money(active_intra["high"])),
            "low": float(money(active_intra["low"])),
            "last": float(money(active_intra["close"])),
            "prev": float(money(active_intra["prev"])),
        },
        "small_model_copy_blocks": (
            {
                k: f"【数据不可用】{data_warning or '检测到合成数据或不可信来源，已屏蔽全部价格点位与推演结论。'}"
                for k in (
                    "0_long_cycle_context",
                    "1_completed_summary",
                    "2_intraday_summary",
                    "3_wedge_and_levels",
                    "4_three_projections",
                    "5_structure_verdict",
                )
            }
            if quality_status == "UNAVAILABLE"
            else {
                "0_long_cycle_context": background_str,
                "1_completed_summary": completed_str,
                "2_intraday_summary": intra_str,
                "3_wedge_and_levels": wedge_and_levels_str,
                "4_three_projections": proj_str,
                "5_structure_verdict": verdict_str,
            }
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sina", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--days", type=int, default=120, help=f"主图显示的已收盘K线根数，60~{MAX_DAYS}；结构与推演固定用近{STRUCT_DAYS}日")
    parser.add_argument(
        "--box-corner",
        choices=["auto", "tl", "bl", "tr"],
        default="auto",
        help="[Deprecated in v3.0] Retained for backward compatibility; has no effect.",
    )
    args = parser.parse_args()
    if not 60 <= args.days <= MAX_DAYS:
        raise SystemExit(f"--days must be between 60 and {MAX_DAYS}")
    bars = load_daily(args.sina, datalen=datalen_for(args.days))
    intra = load_intraday(args.sina)
    summary = render(
        bars,
        intra,
        args.title,
        Path(args.out),
        show=args.days,
        box_corner=args.box_corner,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
