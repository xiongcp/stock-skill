#!/usr/bin/env python3
"""主做多计划留出集 H1 一次性验收（主做多计划有效性检验设计第 6.3 节）。

运行前校验 research/preregistration/preregistration_v3_main_plan.json：冻结文件与数据的 sha256、冻结文件与预注册
无未提交改动、预注册已提交（复用 run_h1_holdout.verify_preregistration）；并逐个核对 H1 成分股、基准与 VIX 的
缓存文件与行情缓存清单一致。任何一项不符就拒绝运行。

只能成功运行一次：结果文件已存在时拒绝运行；启动时写锁文件，锁存在而结果缺失（中途崩溃）时，需要显式加
--restart-after-crash 才能重跑。第一遍分片只含信号日当时可见的信息，续跑会复用已完成分片。
`--check` 只做校验，不评估任何 H1 数据。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import run_h1_holdout as h1
from common import ART, BENCHMARKS, CACHE, ROOT, VIX_SYMBOL

PREREG = ROOT / "research" / "preregistration" / "preregistration_v3_main_plan.json"
RESULT = ART / "h1_main_plan_result.json"
LOCK = ART / "h1_main_plan_started.json"
LEDGER = ART / "trial_ledger.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_sha256(path: Path) -> str:
    """与 history_cache 写清单时同口径：K线与除权事件的规范化 JSON 的 sha256（不信任文件内自带的 sha256 字段）。"""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    canonical = json.dumps({"bars": payload["bars"], "events": payload.get("events") or []},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_cache_files(markets: list[str], universe_path: Path, cache_dir: Path = CACHE) -> list[str]:
    """H1 成分股、基准与 VIX 的缓存文件 sha256 必须与行情缓存清单逐个一致（清单本身的 hash 已在预注册中冻结）。"""
    manifest = json.loads((cache_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    digests = {e["file"]: e.get("sha256") for e in manifest["files"]}
    universe = json.loads(Path(universe_path).read_text(encoding="utf-8"))["markets"]
    problems: list[str] = []
    for market in markets:
        symbols = {s for p in universe[market]["periods"] for s in p["symbols"]} | {BENCHMARKS[market]["symbol"]}
        if market == "US":
            symbols.add(VIX_SYMBOL)
        for sym in sorted(symbols):
            rel = f"{market}/{sym.replace('^', '_')}.json.gz"
            f = cache_dir / rel
            if rel not in digests:
                problems.append(f"行情缓存清单缺少：{rel}")
            elif not digests[rel]:
                problems.append(f"行情缓存清单条目没有 sha256：{rel}")
            elif not f.exists() or _content_sha256(f) != digests[rel]:
                problems.append(f"缓存文件与清单不符：{rel}")
    return problems


def check_scan_tolerance(stats_by_market: dict[str, dict], tolerance: dict) -> list[str]:
    """第一遍扫描的出错数与加载失败标的数必须在预注册容忍度内，否则不在缩水的子样本上做判定。"""
    problems: list[str] = []
    for market, st in stats_by_market.items():
        if st["errors"] > tolerance["max_scan_errors"]:
            problems.append(f"{market} 第一遍扫描出错 {st['errors']} 个股票-日，超过容忍度 {tolerance['max_scan_errors']}")
        if st["skipped_symbols"] > tolerance["max_skipped_symbols"]:
            problems.append(f"{market} 有 {st['skipped_symbols']} 只标的无法加载，超过容忍度 {tolerance['max_skipped_symbols']}")
    return problems


def stale_h1_scan_dirs(scan_dir: Path | None = None) -> list[Path]:
    """守卫首次运行前已存在的 H1 分片目录，说明 H1 曾在守卫之外被扫描过。"""
    import main_plan_scan as scan

    base = scan_dir or scan.SCAN_DIR
    return sorted(p for p in base.glob("h1_*") if p.is_dir()) if base.exists() else []


def verify_all(prereg_path: Path = PREREG, check_git: bool = True) -> tuple[dict, list[str]]:
    prereg, problems = h1.verify_preregistration(prereg_path, check_git=check_git)
    problems += verify_cache_files(prereg["h1"]["markets"], ROOT / prereg["h1"]["universe"])
    return prereg, problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验预注册与运行条件，不评估任何 H1 数据")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--restart-after-crash", action="store_true")
    args = ap.parse_args()

    prereg, problems = verify_all()
    if problems:
        print("预注册校验未通过，拒绝运行：\n- " + "\n- ".join(problems[:20]))
        sys.exit(2)
    if RESULT.exists():
        print(f"主做多计划 H1 已经评估过（{RESULT.name} 已存在），按预注册只能运行一次，拒绝重跑")
        sys.exit(3)
    if LOCK.exists() and not args.restart_after_crash:
        print(f"发现锁文件 {LOCK.name} 但没有结果：上次运行可能中途崩溃。确认未查看任何 H1 结果后，加 --restart-after-crash 重跑")
        sys.exit(4)
    stale = [] if LOCK.exists() else stale_h1_scan_dirs()
    if stale:
        print(f"守卫首次运行前已存在 H1 分片目录 {[p.name for p in stale]}：H1 曾在守卫之外被扫描，拒绝运行，请如实记录后处理")
        sys.exit(6)
    print("预注册 v3 校验通过" + ("（仅校验，未评估 H1）" if args.check else ""))
    if args.check:
        return

    import main_plan_eval as ev
    import main_plan_scan as scan
    import run_dev_region_v2 as v2

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    LOCK.write_text(json.dumps({"started_at_utc": started, "preregistration_sha256": _sha256(PREREG)}), encoding="utf-8")
    scan.H1_AUTHORIZED = True
    spec = prereg["acceptance"]
    result: dict = {
        "started_at_utc": started,
        "preregistration_sha256": _sha256(PREREG),
        "code_commit": v2._git_head(),
        "hypothesis": ev.HYPOTHESIS,
        "note": "主做多计划留出集 H1，一次性评估",
        "markets": {},
    }
    # 第一遍：只扫描评级与计划价位（不含结局），先检查出错容忍度与缓存，再进入第二遍
    stats = {m: scan.scan_stats(scan.run_scan(m, "h1", args.workers)) for m in prereg["h1"]["markets"]}
    problems = check_scan_tolerance(stats, prereg["run_tolerance"]) + verify_all()[1]
    if problems:
        print("第一遍扫描后检查未通过，停止运行（未评估任何结局，锁文件保留）：\n- " + "\n- ".join(problems[:20]))
        sys.exit(5)
    for market in prereg["h1"]["markets"]:
        res = ev.run_market(market, mode="h1", workers=args.workers)  # 第一遍分片已完成，这里直接复用
        result["markets"][market] = {"role": "acceptance", "verdict": h1.judge(res["summary"], spec["gates"]), **res}
        fl = res["summary"]["fill_layer"]
        # 全部市场完成前只打印样本量，不打印判定与收益，避免崩溃续跑前已看到部分结论
        print(f"[{market}] done episodes={fl.get('n_episodes')} days={fl.get('n_signal_days')}", flush=True)
    problems = verify_all()[1]
    if problems:
        print("运行期间行情缓存或冻结文件被改动，不写结果（锁文件保留）：\n- " + "\n- ".join(problems[:20]))
        sys.exit(5)
    result["finished_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["finished_at_utc"], "script": "research/run_h1_main_plan.py",
                             "hypothesis": ev.HYPOTHESIS, "code_commit": result["code_commit"],
                             "preregistration_sha256": result["preregistration_sha256"],
                             "verdicts": {m: v["verdict"] for m, v in result["markets"].items()}},
                            ensure_ascii=False) + "\n")
    for market, v in result["markets"].items():
        print(f"[{market}] verdict={v['verdict']}", flush=True)
    print(f"wrote {RESULT}")


if __name__ == "__main__":
    main()
