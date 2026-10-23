"""文档契约：AGENTS.md / SKILL.md / references 与代码保持同步，可移植且不夸大。"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SKILL_DIR.parent
AGENTS = REPO_DIR / "AGENTS.md"
README = REPO_DIR / "README.md"
SKILL = SKILL_DIR / "SKILL.md"
DOCS = [SKILL, *sorted((SKILL_DIR / "references").glob("*.md"))]


def _agents_text() -> str:
    if not AGENTS.exists():
        pytest.skip("AGENTS.md 不随 skill 分发，仅在源码仓库中检查")
    return AGENTS.read_text(encoding="utf-8")


def _readme_text() -> str:
    if not README.exists():
        pytest.skip("README.md 不随 skill 分发，仅在源码仓库中检查")
    return README.read_text(encoding="utf-8")


def test_no_machine_specific_absolute_paths() -> None:
    docs = DOCS + [p for p in (AGENTS, README) if p.exists()]
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        assert not re.search(r"(/Users/|/home/|[A-Za-z]:\\\\)", text), f"{doc.name} 含本机绝对路径"


def test_skill_md_references_existing_files() -> None:
    text = SKILL.read_text(encoding="utf-8")
    refs = set(re.findall(r"`((?:references|scripts|assets|tests)/[^`\s]+)`", text))
    refs |= set(re.findall(r"(?:python|--data) ((?:scripts|tests)/[\w./-]+)", text))
    assert refs, "SKILL.md 应引用技能内文件"
    missing = [r for r in refs if not (SKILL_DIR / r).exists()]
    assert not missing, f"SKILL.md 引用了不存在的文件: {missing}"


def test_skill_md_keeps_runtime_contract_self_contained() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for must in ("DATA_UNAVAILABLE", "INVALID_ARGUMENT", "quality.status", "rule_score", "calibration_audit", "risk_officer_veto", "禁止合成数据"):
        assert must in text, f"SKILL.md 缺少运行期规则关键字 {must}"


def test_agents_md_has_authenticity_invariant_and_doc_split() -> None:
    text = _agents_text()
    assert "## 0. 文档分工" in text
    assert "数据真实性" in text and "DATA_UNAVAILABLE" in text
    # 运行期回复规则以 SKILL.md 为准，AGENTS.md 只引用
    assert "以 `SKILL.md`" in text


def test_agents_md_directory_tree_matches_code() -> None:
    text = _agents_text()
    tree = text.split("## 2.", 1)[1].split("## 3.", 1)[0]
    listed = set(re.findall(r"([\w]+\.py)", tree))
    actual = {p.name for p in (SKILL_DIR / "scripts").glob("*.py")} | {p.name for p in (SKILL_DIR / "tests").glob("*.py")}
    assert listed == actual, f"目录树与代码不同步：多列 {listed - actual}，漏列 {actual - listed}"


def _assert_bash_blocks_use_existing_paths(text: str) -> None:
    blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
    assert blocks
    for block in blocks:
        for directory in re.findall(r"--directory (\S+)", block):
            assert (REPO_DIR / directory).is_dir(), directory
        for script in re.findall(r"python ((?:scripts|tests)/\S+\.py)", block):
            assert (SKILL_DIR / script).exists(), script
        for data_dir in re.findall(r"--data (\S+)", block):
            assert (SKILL_DIR / data_dir).is_dir(), data_dir


def test_agents_md_commands_use_relative_existing_paths() -> None:
    _assert_bash_blocks_use_existing_paths(_agents_text())


def test_readme_commands_and_links_resolve() -> None:
    text = _readme_text()
    _assert_bash_blocks_use_existing_paths(text)
    for target in re.findall(r"\]\(((?!https?://)[^)#\s]+)\)", text):
        assert (REPO_DIR / target).exists(), f"README 链接不存在: {target}"


def test_readme_cli_parameters_and_outputs_match_code() -> None:
    """README 的参数表、错误前缀与产物文件名必须与 CLI 实现一致。"""
    text = _readme_text()
    cli = (SKILL_DIR / "scripts" / "analyze_and_forecast.py").read_text(encoding="utf-8")
    table = text.split("### 5.1", 1)[1].split("### 5.2", 1)[0]
    documented = set(re.findall(r"`(--[\w-]+)`", table))
    actual = set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', cli))
    assert documented == actual, f"README 参数表与 CLI 不同步：多列 {documented - actual}，漏列 {actual - documented}"
    for label in set(re.findall(r"\[([A-Z_]{5,})\]", text)):
        # CLI 以 _fail("CODE", ...) 统一拼出 stderr 的 [CODE] 前缀，源码里只有带引号的错误码
        assert f'"{label}"' in cli, f"README 提到的错误前缀 [{label}] 在 CLI 中不存在"
    outputs = text.split("## 6.", 1)[1].split("## 7.", 1)[0]
    documented_files = set(re.findall(r"`\*(_\w+\.\w+)`", outputs))
    contract_py = (SKILL_DIR / "scripts" / "contract_v6.py").read_text(encoding="utf-8")
    actual_files = set(re.findall(r'f"\{safe_sym\}(_\w+\.\w+)"', contract_py))
    assert documented_files == actual_files, f"README 产物表与 CLI 不同步：{documented_files ^ actual_files}"


def test_fixture_naming_does_not_impersonate_real_sources() -> None:
    fixtures = SKILL_DIR / "tests" / "fixtures"
    assert not (fixtures / "sina_daily").exists(), "合成数据不得命名为 sina_daily"
    assert not (fixtures / "synthetic_daily").exists(), "禁止保留任何合成数据目录 synthetic_daily"
    assert (fixtures / "recorded" / "MANIFEST.json").exists()


def test_no_synthetic_generators_or_mock_bars_in_code_and_tests() -> None:
    """所有代码与测试必须使用真实行情，禁止存在合成序列生成器。"""
    py_files = [*(SKILL_DIR / "scripts").glob("*.py"), *(SKILL_DIR / "tests").glob("*.py")]
    forbidden = ("def generate_deterministic_series", "def _random_walk_bars", "def _synthetic_bundle", "allow_synthetic_fallback")
    hits = [
        f"{p.name}: {token}"
        for p in py_files
        if p.name != "test_docs_contract.py"
        for token in forbidden
        if token in p.read_text(encoding="utf-8")
    ]
    assert not hits, f"检测到禁止的合成数据构造器: {hits}"


# ---------------------------------------------------------------------------
# SKILL.md 规范（agentskills.io/specification）与宣称真实性
# ---------------------------------------------------------------------------
SPEC_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _frontmatter() -> tuple[dict[str, object], str]:
    text = SKILL.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, "SKILL.md 缺少 YAML frontmatter"
    fields: dict[str, object] = {}
    current: str | None = None
    for line in m.group(1).splitlines():
        if not line.strip():
            continue
        if line.startswith("  ") and current == "metadata":
            key, _, val = line.strip().partition(":")
            fields["metadata"][key.strip()] = val.strip().strip('"')  # type: ignore[index]
            continue
        key, _, val = line.partition(":")
        current = key.strip()
        fields[current] = {} if current == "metadata" and not val.strip() else val.strip().strip('"')
    return fields, m.group(2)


def test_skill_frontmatter_follows_agent_skills_spec() -> None:
    fields, body = _frontmatter()
    assert set(fields) <= SPEC_FIELDS, f"非规范字段: {set(fields) - SPEC_FIELDS}"
    name = str(fields["name"])
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert name == SKILL_DIR.name
    desc = str(fields["description"])
    assert 0 < len(desc) <= 1024
    assert "Use when" in desc and "Not for" in desc, "description 需写明何时使用与不适用场景"
    assert re.search(r"[一-鿿]", desc) and re.search(r"[A-Za-z]{20,}", desc.replace(" ", "")), "description 需中英双语"
    compat = str(fields["compatibility"])
    assert 0 < len(compat) <= 500 and "uv" in compat and "DATA_UNAVAILABLE" in compat
    meta = fields["metadata"]
    assert isinstance(meta, dict) and set(meta) >= {"author", "version"}
    assert all(isinstance(v, str) for v in meta.values())
    assert len(body.splitlines()) < 500


def test_skill_body_has_output_contract_and_failure_handling() -> None:
    _, body = _frontmatter()
    for heading in ("何时使用", "运行后必检", "回复契约", "失败处理", "参考文档"):
        assert heading in body, f"SKILL.md 缺少章节：{heading}"
    assert "不构成投资建议" in body


def test_versions_are_in_sync() -> None:
    import json

    fields, _ = _frontmatter()
    pyproject = (SKILL_DIR / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert fields["metadata"]["version"] == version  # type: ignore[index]
    schema = json.loads((SKILL_DIR / "assets" / "analysis.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["schema_version"]["enum"] == [version]
    contract_py = (SKILL_DIR / "scripts" / "contract_v6.py").read_text(encoding="utf-8")
    assert f'SCHEMA_VERSION = "{version}"' in contract_py
    marketplace = json.loads((REPO_DIR / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))

    def _versions(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "version":
                    yield v
                yield from _versions(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from _versions(v)

    found = list(_versions(marketplace))
    assert found and all(v == version for v in found), found


# 已核实未实现或已移除的能力，任何文档、注释与界面文案都不得再宣称
OVERCLAIMS = (
    "Lo, Mamaysky", "头肩", "MACD 背离", "MACD底背离", "RSI/MACD", "CHoCH 特征", "BEARISH_CHoCH",
    "正交特征", "量价正交", "筹码峰", "公式证据。", "KERNEL_DOUBLE", "WLS_", "confidence_score",
    "Walk-Forward", "Meta-Labeling 过滤", "隔夜跳空波动率修正", "动态修正保形", "defaults.json",
)


def test_no_unimplemented_capability_claims() -> None:
    files = [SKILL, *(SKILL_DIR / "references").glob("*.md"), *(SKILL_DIR / "scripts").glob("*.py")]
    if AGENTS.exists():
        files.append(AGENTS)
    hits = [
        f"{f.name}: {phrase}"
        for f in files
        for phrase in OVERCLAIMS
        if phrase in f.read_text(encoding="utf-8")
    ]
    assert not hits, f"残留不实宣称: {hits}"


# ---------------------------------------------------------------------------
# 阶段 1 文档：无占位符、字段字典覆盖顶层契约、制度规则带生效日期与来源
# ---------------------------------------------------------------------------
PLACEHOLDER = re.compile(r"<(?:仓库|repo|REPO|your|YOUR|owner|OWNER)[^>]*>|git clone <|xxx/stock-skill")


def test_docs_have_no_placeholders() -> None:
    docs = DOCS + [p for p in (AGENTS, README) if p.exists()]
    hits = [f"{d.name}: {m.group(0)}" for d in docs for m in PLACEHOLDER.finditer(d.read_text(encoding="utf-8"))]
    assert not hits, f"文档残留占位符: {hits}"


def test_output_fields_dictionary_covers_schema_top_level() -> None:
    import json

    schema = json.loads((SKILL_DIR / "assets" / "analysis.schema.json").read_text(encoding="utf-8"))
    top = set(schema.get("required", [])) | set(schema.get("properties", {}))
    text = (SKILL_DIR / "references" / "output-fields.md").read_text(encoding="utf-8")
    missing = sorted(k for k in top if f"`{k}" not in text)
    assert not missing, f"output-fields.md 缺少顶层字段: {missing}"


@pytest.mark.parametrize("market", ["cn", "hk", "us"])
def test_market_rules_have_effective_date_and_source(market: str) -> None:
    text = (SKILL_DIR / "references" / f"{market}-market.md").read_text(encoding="utf-8")
    tables = re.findall(r"^\| 规则 \| 内容 \| 生效日期 \| 来源 \|\n\| --- .*\n((?:\|.*\n?)+)", text, re.M)
    assert len(tables) >= 3, f"{market}-market.md 应以「规则 | 内容 | 生效日期 | 来源」表格列出制度"
    for table in tables:
        for row in table.strip().splitlines():
            cells = [c.strip() for c in row.strip().strip("|").split(" | ")]
            assert len(cells) == 4 and all(cells), f"{market}: 行格式不对 {row}"
            assert cells[3] != "—", f"{market}: 缺少来源 {row}"
    for topic in ("交易时段", "空"):
        assert topic in text, f"{market}-market.md 缺少 {topic} 相关章节"


def test_o3_20_scenario_playbook_doc_matches_code_verbatim() -> None:
    import sys

    scripts_dir = str(SKILL_DIR / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from scenarios import SCENARIO_PLAYBOOK

    playbook_md = (SKILL_DIR / "references" / "scenario-playbook.md").read_text(encoding="utf-8")
    m = re.search(
        r"^\| 情景ID \| 情景 \| 适用前提（由 L1 状态判定） \| 目标位来源 \| 失效条件 \|\n\| --- .*\n((?:\|.*\n?)+)",
        playbook_md,
        re.M,
    )
    assert m is not None, "scenario-playbook.md 缺少 8 类标准情景定义表"
    rows = []
    for line in m.group(1).strip().splitlines():
        cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"scenario-playbook.md 表格列数异常: {line}"
        rows.append(
            {
                "id": cells[0],
                "name": cells[1],
                "precondition": cells[2],
                "target_source": cells[3],
                "invalidation": cells[4],
            }
        )
    assert len(rows) == 8
    assert tuple(rows) == SCENARIO_PLAYBOOK


def _all_command_docs() -> list[Path]:
    extra = [REPO_DIR / "examples" / "README.md"]
    return DOCS + [p for p in (AGENTS, README, *extra) if p.exists()]


def test_documented_artifact_filenames_match_sanitize_filename() -> None:
    """文档里的产物文件名必须与脚本实际生成的一致：sanitize_filename 保留代码中的点（600519.SH_result.json）。"""
    import sys

    scripts_dir = str(SKILL_DIR / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from contracts import sanitize_filename

    assert sanitize_filename("600519.SH") == "600519.SH"
    bad = re.compile(r"\b(?:\d{5,6}|[A-Z]{1,6})_(?:SH|SZ|BJ|HK|US)_(?:result|judgment|judgment_pack|report|backtest|preview|dashboard)\b")
    for doc in _all_command_docs():
        hits = bad.findall(doc.read_text(encoding="utf-8"))
        assert not hits, f"{doc.name} 中的产物文件名与实际不符（应保留代码中的点）: {hits}"


def test_uv_directory_commands_use_paths_relative_to_skill_dir() -> None:
    """`uv run --directory kline-structure-chart` 会切换到技能目录，其后的相对路径参数不得再带 kline-structure-chart/ 前缀。"""
    for doc in _all_command_docs():
        text = doc.read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            if "--directory kline-structure-chart" not in line:
                continue
            tail = line.split("--directory kline-structure-chart", 1)[1]
            assert " kline-structure-chart/" not in tail, f"{doc.name} 命令路径会被解析成 kline-structure-chart/kline-structure-chart/…: {line.strip()}"


def test_example_reply_passes_lint_and_counterexample_is_rejected() -> None:
    """examples/ 的标准回复必须与同目录 result.json 一致并通过回复契约，反例必须被 lint 拦截。"""
    import json
    import sys

    example_dir = REPO_DIR / "examples" / "600519.SH"
    if not example_dir.exists():
        pytest.skip("examples/ 不随 skill 分发，仅在源码仓库中检查")
    scripts_dir = str(SKILL_DIR / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from lint_response import lint_agent_response

    report = json.loads((example_dir / "600519.SH_result.json").read_text(encoding="utf-8"))
    reply = (example_dir / "reply.md").read_text(encoding="utf-8")
    good, _, counter = reply.partition("## 不合格的回复（反例）")
    good_body = good.split("---", 1)[1]
    assert lint_agent_response(good_body, report) == []
    counter_quote = next(line[1:].strip() for line in counter.splitlines() if line.startswith(">"))
    assert lint_agent_response(counter_quote, report)


def test_requirements_txt_matches_locked_runtime_dependencies() -> None:
    """没有 uv 时用 requirements.txt 安装：运行期依赖与 uv.lock 锁定版本必须一致，不能比 pyproject 多或少。"""
    pyproject = (SKILL_DIR / "pyproject.toml").read_text(encoding="utf-8")
    runtime_block = re.search(r"^dependencies = \[(.*?)\]", pyproject, re.S | re.M).group(1)
    runtime = {re.split(r"[<>=!~ ]", dep.strip().strip('",'))[0].lower() for dep in runtime_block.split(",") if dep.strip().strip('"')}
    lock = (SKILL_DIR / "uv.lock").read_text(encoding="utf-8")
    locked = dict(re.findall(r'\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"', lock))
    lines = [ln.strip() for ln in (SKILL_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()]
    pins = dict(ln.split("==", 1) for ln in lines if ln and not ln.startswith("#"))
    assert set(pins) == runtime, f"requirements.txt 与 pyproject 运行期依赖不一致：{set(pins) ^ runtime}"
    for name, version in pins.items():
        assert locked.get(name) == version, f"{name}=={version} 与 uv.lock 的 {locked.get(name)} 不一致"
    # 运行期代码不得 import 未声明的第三方包（numpy 等只在测试中使用）
    for script in (SKILL_DIR / "scripts").glob("*.py"):
        for mod in re.findall(r"^\s*(?:import|from) (numpy|pandas|scipy|talib|requests)\b", script.read_text(encoding="utf-8"), re.M):
            raise AssertionError(f"{script.name} 运行期导入了未声明依赖 {mod}")
