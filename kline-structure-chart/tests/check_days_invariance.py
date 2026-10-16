#!/usr/bin/env python3
"""回放校验：同一截面、不同 --days，结构层与三条推演必须完全一致。

每个历史截面按 main() 的取数长度 datalen_for(days) 截取日K，分别以收盘态和盘中态（下一根K线当作盘中快照）
渲染，比较结构层字段（wedge / scenario / fib_levels / key_levels / recent_gaps / swing / projections，
推演折线的未来槽位也在内）和直抄块 3/4/5。只有展示层与背景层（主图根数、0/1 号直抄块、background）允许随 --days 变化。

用法：
  python3 tests/check_days_invariance.py --data DIR      # DIR 下放日K <symbol>.json（离线）：
                                                         #   tests/fixtures/recorded/sina_daily = 真实录制，tests/fixtures/synthetic_daily = 合成序列
  python3 tests/check_days_invariance.py --fetch         # 直接从新浪拉取最近 1023 根
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_structure_chart.py"
SYMBOLS = ("sh000001", "sz399001", "sz399006", "sh000688", "sh000300")
STRUCT_KEYS = ("wedge", "scenario", "fib_levels", "key_levels", "recent_gaps", "swing", "structure_window", "projections")
STRUCT_BLOCKS = ("3_wedge_and_levels", "4_three_projections", "5_structure_verdict")


def load_mod():
    # 渲染器依赖同目录的 structure.py，按文件路径加载时需要把 scripts/ 放进 sys.path
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("render_structure_chart", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_bars(mod, args, sym: str) -> list[dict]:
    if args.fetch:
        return mod.load_daily(sym, datalen=1023)
    rows = json.loads((Path(args.data) / f"{sym}.json").read_text())
    return [
        {"day": r["day"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
         "close": float(r["close"]), "volume": float(r["volume"]), "complete": True}
        for r in rows
    ]


def signature(res: dict) -> dict:
    sig = {k: res[k] for k in STRUCT_KEYS}
    sig["blocks"] = {k: res["small_model_copy_blocks"][k] for k in STRUCT_BLOCKS}
    return sig


def first_diff(a, b, path: str = "") -> str:
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() | b.keys():
            if a.get(k) != b.get(k):
                return first_diff(a.get(k), b.get(k), f"{path}.{k}")
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y:
                return first_diff(x, y, f"{path}[{i}]")
    return f"{path}: {str(a)[:120]} != {str(b)[:120]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--data", help="新浪日K缓存目录，文件名 <symbol>.json")
    src.add_argument("--fetch", action="store_true", help="从新浪拉取最近 1023 根日K")
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--days", default="60,120,200,250,300,500")
    parser.add_argument("--step", type=int, default=20, help="相邻截面间隔的交易日数")
    args = parser.parse_args()

    mod = load_mod()
    days_list = [int(d) for d in args.days.split(",")]
    need = max(mod.datalen_for(d) for d in days_list)
    runs, fails = 0, []
    for sym in args.symbols.split(","):
        bars = load_bars(mod, args, sym)
        if len(bars) < need:
            raise SystemExit(f"{sym}: only {len(bars)} bars, need >= {need}")
        for k in range(need, len(bars) + 1, args.step):
            nxt = bars[k] if k < len(bars) else None
            for mode in ("closed", "live"):
                if mode == "live" and nxt is None:
                    continue
                base = None
                for days in days_list:
                    cut = [dict(b) for b in bars[k - mod.datalen_for(days) : k]]
                    live = None
                    if mode == "live":
                        live = {**nxt, "prev": cut[-1]["close"], "stamp": nxt["day"] + " 10:30:00", "complete": False}
                    res = mod.render(cut, live, f"{sym} {days}d", Path("/dev/null"), show=days)
                    runs += 1
                    errs = []
                    if res["display_days"] != days:
                        errs.append(f"display_days={res['display_days']}")
                    if res["background"]["days"] != max(days, mod.BACKGROUND_MIN_DAYS):
                        errs.append(f"background.days={res['background']['days']}")
                    sig = signature(res)
                    if base is None:
                        base = (days, sig)
                    elif sig != base[1]:
                        errs.append(f"vs --days {base[0]}: {first_diff(base[1], sig)}")
                    if errs:
                        fails.append((sym, bars[k - 1]["day"], mode, days, errs))
    print(f"runs={runs} failing={len(fails)} days={days_list}")
    for sym, day, mode, days, errs in fails[:20]:
        print(f" FAIL {sym} {day} {mode} --days {days}: {errs[0]}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
