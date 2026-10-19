#!/usr/bin/env python3
"""环境自检：Python / uv（可选）/ 依赖 / 中文字体 / 数据源连通 / 技能注册，逐项输出 ✅ 或 ❌ 与修复建议。

用法：
  uv run python scripts/doctor.py                    # 全部检查（数据源项需要联网）
  uv run python scripts/doctor.py --offline          # 跳过数据源连通检查
  uv run python scripts/doctor.py --project <目录>   # 额外检查项目级技能目录

任一项 ❌ 时以退出码 1 结束。数据源检查直接调用线上同一套取数函数，只判断能否取到，不保存任何行情。
"""
from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_NAME = SKILL_DIR.name
MIN_PYTHON = (3, 12)
INSTALL_HINT = "在仓库根目录执行 bash install.sh（或按 README 第 3 节手动软链）"

# 与 install.sh / vercel-labs/skills 的全局目录约定一致
AGENT_SKILL_DIRS = {
    "Claude Code": ".claude/skills",
    "Codex / 通用 .agents": ".agents/skills",
    "Hermes Agent": ".hermes/skills",
    "Gemini CLI": ".gemini/skills",
    "Cursor": ".cursor/skills",
    "OpenCode": ".config/opencode/skills",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def check_python() -> Check:
    ver = ".".join(map(str, sys.version_info[:3]))
    ok = sys.version_info[:2] >= MIN_PYTHON
    return Check(
        "Python 版本",
        ok,
        f"{ver}（{sys.executable}）",
        "" if ok else "运行 uv sync --python 3.12 后用 uv run 启动；没有 uv 时安装 Python 3.12+ 后按 SKILL.md「运行环境」一节创建 .venv",
    )


def check_uv() -> Check:
    """uv 是推荐工具而非必需：当前 Python 与依赖可用时（例如 .venv/bin/python 运行），缺 uv 不算失败。"""
    uv = shutil.which("uv")
    if not uv:
        return Check("uv", True, f"未找到 uv（可选）：当前用 {sys.executable} 运行；SKILL.md 中的 uv run python 换成该解释器即可")
    try:
        out = subprocess.run([uv, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("uv", False, f"{uv} 无法运行：{exc}", "重新安装 uv")
    return Check("uv", True, out or uv)


def check_dependencies() -> Check:
    found, missing = [], []
    for module, dist in (("PIL", "pillow"),):
        try:
            mod = importlib.import_module(module)
            found.append(f"{dist} {getattr(mod, '__version__', '?')}")
        except ImportError:
            missing.append(dist)
    if missing:
        return Check(
            "Python 依赖",
            False,
            f"缺少 {', '.join(missing)}",
            "在仓库根目录执行 uv sync --frozen --python 3.12 --directory kline-structure-chart；"
            "没有 uv 时在技能目录执行 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt",
        )
    return Check("Python 依赖", True, "，".join(found))


def check_cjk_font() -> Check:
    try:
        from render_structure_chart import FONT_CANDIDATES
    except Exception as exc:  # noqa: BLE001 - 依赖缺失时由依赖项给出修复建议
        return Check("中文字体", False, f"无法加载渲染器：{type(exc).__name__}: {exc}", "先修复 Python 依赖")
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return Check("中文字体", True, path)
    return Check(
        "中文字体",
        False,
        "未找到 PNG 渲染所需的中文字体",
        "Debian/Ubuntu: sudo apt-get install -y fonts-noto-cjk；Fedora: sudo dnf install -y google-noto-sans-cjk-ttc-fonts；Arch: sudo pacman -S noto-fonts-cjk",
    )


def _probe(name: str, fn: Callable[[], str]) -> Check:
    try:
        return Check(name, True, fn())
    except Exception as exc:  # noqa: BLE001 - 任何取数失败都只报告，不中断其余检查
        return Check(name, False, f"{type(exc).__name__}: {str(exc)[:120]}", "检查网络与代理；接口限流时稍后重试。断网时分析会以 [DATA_UNAVAILABLE] 退出")


def check_data_sources() -> list[Check]:
    import data_gateway
    import resolve_symbol

    def sina() -> str:
        bars = data_gateway.fetch_sina_daily_bars("sh000300", datalen=60)
        return f"沪深300 日线 {len(bars)} 根，最新 {bars[-1]['day']}"

    def yahoo() -> str:
        bars = data_gateway.fetch_yahoo_daily_bars("SPY", range_str="1mo", datalen=None)
        if not bars:
            raise RuntimeError("Yahoo 返回 0 根K线")
        return f"SPY 日线 {len(bars)} 根，最新 {bars[-1]['day']}"

    def tencent_quote() -> str:
        meta = data_gateway.fetch_tencent_quote_meta("hk00700")
        return f"{meta['code']} {meta['name']}，每手 {meta['lot_size']} 股"

    def tencent_search() -> str:
        rows = resolve_symbol.parse_search_response(resolve_symbol.fetch_tencent_search_raw("茅台"))
        if not rows:
            raise RuntimeError("搜索'茅台'无结果")
        return f"'茅台' -> {rows[0]['exchange']}{rows[0]['code']}"

    return [
        _probe("数据源：新浪日线", sina),
        _probe("数据源：Yahoo 日线", yahoo),
        _probe("数据源：腾讯行情快照", tencent_quote),
        _probe("数据源：腾讯证券搜索", tencent_search),
    ]


def check_skill_links(home: Path, project: Path | None = None) -> list[Check]:
    locations = [(label, home / rel) for label, rel in AGENT_SKILL_DIRS.items()]
    if project is not None:
        locations += [
            ("项目 .claude/skills", project / ".claude" / "skills"),
            ("项目 .agents/skills", project / ".agents" / "skills"),
            ("项目 .hermes/skills", project / ".hermes" / "skills"),
        ]
    checks: list[Check] = []
    for label, base in locations:
        entry = base / SKILL_NAME
        if not entry.exists() and not entry.is_symlink():
            continue
        name = f"技能注册：{label}"
        if entry.is_symlink() and not entry.exists():
            checks.append(Check(name, False, f"{entry} 是失效软链（指向 {entry.readlink()}）", f"删除后重新注册：{INSTALL_HINT}"))
        elif not (entry / "SKILL.md").is_file():
            checks.append(Check(name, False, f"{entry} 下没有 SKILL.md", f"删除后重新注册：{INSTALL_HINT}"))
        else:
            target = f" -> {entry.resolve()}" if entry.is_symlink() else "（复制安装）"
            checks.append(Check(name, True, f"{entry}{target}"))
    if not checks:
        checks.append(Check("技能注册", False, f"在 {', '.join(str(b) for _, b in locations)} 中都没有找到 {SKILL_NAME}", INSTALL_HINT))
    return checks


def run_checks(offline: bool = False, home: Path | None = None, project: Path | None = None) -> list[Check]:
    checks = [check_python(), check_uv(), check_dependencies(), check_cjk_font()]
    if not offline:
        checks += check_data_sources()
    checks += check_skill_links(home or Path.home(), project)
    return checks


def main() -> None:
    parser = argparse.ArgumentParser(description="Environment self-check for kline-structure-chart")
    parser.add_argument("--offline", action="store_true", help="跳过数据源连通检查")
    parser.add_argument("--project", help="额外检查该项目目录下的 .claude/skills 与 .agents/skills")
    args = parser.parse_args()
    checks = run_checks(offline=args.offline, project=Path(args.project).resolve() if args.project else None)
    for c in checks:
        print(f"{'✅' if c.ok else '❌'} {c.name}：{c.detail}")
        if not c.ok and c.fix:
            print(f"   修复建议：{c.fix}")
    if args.offline:
        print("⏭  已跳过数据源连通检查（--offline）")
    failed = sum(not c.ok for c in checks)
    print(f"\n共 {len(checks)} 项，未通过 {failed} 项")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
