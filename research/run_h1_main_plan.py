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


def verify_cache_files(markets: list[str], universe_path: Path, cache_dir: Path = CACHE) -> list[str]:
    """H1 成分股、基准与 VIX 的缓存文件 sha256 必须与行情缓存清单逐个一致（清单本身的 hash 已在预注册中冻结）。"""
    manifest = json.loads((cache_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    digests = {e["file"]: e["sha256"] for e in manifest["files"]}
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
            elif not f.exists() or _sha256(f) != digests[rel]:
                problems.append(f"缓存文件与清单不符：{rel}")
    return problems


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
    for market in prereg["h1"]["markets"]:
        res = ev.run_market(market, mode="h1", workers=args.workers)
        verdict = h1.judge(res["summary"], spec["gates"])
        result["markets"][market] = {"role": "acceptance", "verdict": verdict, **res}
        fl = res["summary"]["fill_layer"]
        print(f"[{market}] episodes={fl.get('n_episodes')} days={fl.get('n_signal_days')} "
              f"mean={fl.get('mean_net_return_pct')}% verdict={verdict}", flush=True)
    result["finished_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["finished_at_utc"], "script": "research/run_h1_main_plan.py",
                             "hypothesis": ev.HYPOTHESIS, "code_commit": result["code_commit"],
                             "preregistration_sha256": result["preregistration_sha256"],
                             "verdicts": {m: v["verdict"] for m, v in result["markets"].items()}},
                            ensure_ascii=False) + "\n")
    print(f"wrote {RESULT}")


if __name__ == "__main__":
    main()
