# 波动范围与风控前置、回测拆出大盘贡献 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `report.md` 与回复契约先给正常波动范围和风控、后给结构，并加两句固定说明；`backtest.json` 新增 `backtest.market_attribution`，把单笔收益和同期基准收益并排给出。

**Architecture:** A 只改 `render_report_md.py` 里各节的先后与三处文字，`result.json` 不变。B 在 `evaluation.run_plan_backtest` 的汇总阶段调用一个纯函数 `_market_attribution`，只读回放已产生的成交与基准K线，不进入任何决策日的输入。

**Tech Stack:** Python 3.12、uv、pytest（`-n auto`，慢用例标 `slow`）。测试只用 `tests/fixtures/recorded/` 的真实录制行情。

**Spec:** `docs/superpowers/specs/2026-11-16-range-first-report-and-backtest-attribution-design.md`

## Global Constraints

- 不动评级标签、规则分、否决条件与任何计划价位。
- `result.json`、`chart_data.json`、`assets/analysis.schema.json`、看板（HTML）、PNG 不变。
- 产品输出里不写阶段 0 的任何数字；新增说明只用规格第 2.3、3.3 节的固定句子，逐字照抄。
- `backtest.json` 只新增字段，不删除、不改名、不改已有字段的语义。
- 测试断网，只用录制的真实行情，不生成任何合成行情。
- 五处版本号保持 `6.0.0`；改动记在 `CHANGELOG.md` 的 `[Unreleased]`。
- 改 `README.md` 时同步改 `README.en.md`。
- 所有命令在仓库根目录执行，用 `uv run --directory kline-structure-chart`。
- 提交信息用中文，结尾加 `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`。

## Review Focus

1. **基准在入场日或出场日没有K线**（港股、美股节假日不一致）：持有期基准收益取入场日之前最近一个、出场日当天或之前最近一个基准收盘，不报错。→ Task 3 的 `test_market_attribution_uses_previous_benchmark_close_on_missing_days`。
2. **标的本身就是基准，或本地文件输入没有基准**：`market_attribution.available = false` 并给出原因，回测其余部分照常输出，报告第 7 节写一行原因而不是报错。→ Task 3 的 `test_market_attribution_unavailable_cases`，Task 4 的 `test_report_md_backtest_shows_market_attribution`。
3. **回放区间内零成交**：不能除以零；`available = false`，报告照常渲染。→ Task 3 同一测试。
4. **数据不可用（`quality.status = UNAVAILABLE`）**：报告仍然不出现任何章节与价位，新加的两句固定说明也不能出现。→ Task 1 的 `test_report_md_unavailable_has_no_lead_sentences`。
5. **旧的 `backtest.json`（没有 `market_attribution`）交给新的报告渲染器**：用户重渲染旧产物时不能抛 `KeyError`，第 7 节不出现归因一行。→ Task 4 的 `test_report_md_backtest_without_market_attribution_still_renders`。

---

## File Structure

| 文件 | 责任 |
| --- | --- |
| `kline-structure-chart/scripts/render_report_md.py` | 章节顺序、直出摘要分句顺序、两句固定说明、第 7 节归因一行 |
| `kline-structure-chart/scripts/evaluation.py` | `_market_attribution` 纯函数与 `run_plan_backtest` 里的一处调用 |
| `kline-structure-chart/tests/test_report_and_metadata.py` | 上述两处的全部新测试（不新建测试文件，避免改 `AGENTS.md` 目录树） |
| `kline-structure-chart/tests/fixtures/contract/backtest_v6_keypaths_600519.json` | 键路径快照（重新生成） |
| `kline-structure-chart/SKILL.md`、`references/output-fields.md`、`references/interpretation-guide.md`、`references/failure-modes.md` | 回复契约与读法 |
| `README.md`、`README.en.md` | 对报告顺序的描述 |
| `examples/` | 四份报告、茅台的 `backtest.json` 与 `reply.md` |
| `CHANGELOG.md` | `[Unreleased]` |

---

### Task 1: 报告章节顺序、直出摘要与两句固定说明

**Files:**
- Modify: `kline-structure-chart/scripts/render_report_md.py`（`render_report_markdown`，第 150~430 行附近）
- Test: `kline-structure-chart/tests/test_report_and_metadata.py`

**Interfaces:**
- Produces: 模块常量 `RISK_LEAD_CN: str`、`STRUCTURE_LEAD_CN: str`（Task 2 的文档与 Task 4 的测试引用这两句的文字）。`render_report_markdown(result, backtest=None) -> str` 签名不变。

- [ ] **Step 1: 记录改前基线（离线重渲染与已提交的示例逐字节相同）**

把下面的脚本存为 `kline-structure-chart/artifacts/rerender_examples.py`（`artifacts/` 不进 git）：

```python
"""用 examples/ 里现有的 result.json（与 backtest.json）离线重渲染四份报告，不取数。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_report_md import render_report_markdown

root = Path(__file__).resolve().parents[2] / "examples"
for sym in ("600519.SH", "300308.SZ", "00700.HK", "NVDA.US"):
    d = root / sym
    result = json.loads((d / f"{sym}_result.json").read_text(encoding="utf-8"))
    bt_path = d / f"{sym}_backtest.json"
    bt = json.loads(bt_path.read_text(encoding="utf-8")) if bt_path.exists() else None
    (d / f"{sym}_report.md").write_text(render_report_markdown(result, bt), encoding="utf-8")
    print("rendered", sym)
```

Run: `uv run --directory kline-structure-chart python artifacts/rerender_examples.py && git status --short examples/`
Expected: 打印四行 `rendered …`，`git status` 没有任何输出（重渲染与已提交文件逐字节相同）。若有差异，先 `git diff examples/` 看清原因（例如文件末尾换行），把脚本改到无差异后再继续；不要带着差异往下做。

- [ ] **Step 2: 写失败的测试**

在 `tests/test_report_and_metadata.py` 的 `test_report_md_zero_trade_rows_show_dash` 之后加入：

```python
RANGE_FIRST_HEADERS = [
    "## 1. 结论速览", "## 2. 大盘环境", "## 3. 未来 ", "## 4. 风控计划", "## 5. 结构与条件推演",
    "## 6. 形态与信号证据", "## 7. 历史计划回放回测", "## 8. 产物文件",
]


def _lead_after(md: str, header: str) -> str:
    """标题行之后隔一个空行的那一行。"""
    lines = md.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith(header))
    assert lines[i + 1] == ""
    return lines[i + 2]


@pytest.mark.slow
def test_report_md_puts_range_and_risk_before_structure(cn_pipeline) -> None:
    _, md, _ = cn_pipeline
    headers = [ln for ln in md.splitlines() if ln.startswith("## ")]
    assert len(headers) == len(RANGE_FIRST_HEADERS)
    for got, want in zip(headers, RANGE_FIRST_HEADERS):
        assert got.startswith(want), (got, want)
    assert "正常波动范围" in headers[2]


@pytest.mark.slow
def test_report_md_lead_sentences_in_both_veto_states(cn_pipeline) -> None:
    from render_report_md import RISK_LEAD_CN, STRUCTURE_LEAD_CN

    assert RISK_LEAD_CN == "做多计划的盈亏主要取决于持有期大盘涨跌；计划的作用是把亏损限定在预算内，不代表方向判断。"
    assert STRUCTURE_LEAD_CN == "以下只描述价格结构和条件路径，不预示涨跌方向。"
    report, _, backtest = cn_pipeline
    result = load_v6(report)[0]
    flipped = copy.deepcopy(result)
    flipped["verdict"]["risk_veto"] = not result["verdict"]["risk_veto"]
    flipped["verdict"]["veto_reasons"] = [] if result["verdict"]["risk_veto"] else ["大盘环境评分偏弱"]
    for res in (result, flipped):
        md = render_report_markdown(res, backtest)
        assert _lead_after(md, "## 4. 风控计划") == RISK_LEAD_CN
        assert _lead_after(md, "## 5. 结构与条件推演") == STRUCTURE_LEAD_CN
        assert md.count(RISK_LEAD_CN) == 1 and md.count(STRUCTURE_LEAD_CN) == 1


@pytest.mark.slow
def test_report_md_digest_and_summary_order(cn_pipeline) -> None:
    report, md, _ = cn_pipeline
    digest = next(ln for ln in md.splitlines() if "直出摘要" in ln)
    order = [digest.index(k) for k in ("最新收盘", "正常波动范围", "三重屏障计划", "当前评级为")]
    assert order == sorted(order)
    if load_v6(report)[0]["verdict"]["risk_veto"]:
        assert digest.split("：", 1)[1].startswith("【风控否决")
    bullets = [md.index(k) for k in ("- 直出摘要", "- 最新收盘：", "- 结论：", "- 规则分：")]
    assert bullets == sorted(bullets)


@pytest.mark.slow
def test_report_md_unavailable_has_no_lead_sentences(cn_pipeline) -> None:
    from render_report_md import RISK_LEAD_CN, STRUCTURE_LEAD_CN

    broken = copy.deepcopy(load_v6(cn_pipeline[0])[0])
    broken["quality"]["status"] = "UNAVAILABLE"
    md = render_report_markdown(broken)
    assert RISK_LEAD_CN not in md and STRUCTURE_LEAD_CN not in md and "## " not in md
```

- [ ] **Step 3: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_report_and_metadata.py -q -k "range_and_risk or lead_sentences or digest_and_summary or unavailable_has_no_lead"`
Expected: 4 个用例失败。前三个分别因为标题顺序不符、`ImportError: cannot import name 'RISK_LEAD_CN'`、摘要顺序不符；第四个同样是 `ImportError`。

- [ ] **Step 4: 实现**

在 `scripts/render_report_md.py` 的模块常量区（`DISCLAIMER` 附近）加入：

```python
RISK_LEAD_CN = "做多计划的盈亏主要取决于持有期大盘涨跌；计划的作用是把亏损限定在预算内，不代表方向判断。"
STRUCTURE_LEAD_CN = "以下只描述价格结构和条件路径，不预示涨跌方向。"
```

`render_report_markdown` 内做五处修改：

(a) 直出摘要：把 `flash_digest` 的后四个 f-string 换成下面的顺序（前两句"数据截至…""基准…"不动）：

```python
        f"最新收盘 {price(last_close)}，"
        f"未来 {vr['horizon_bars']} 日 80% 正常波动范围为 {price(vr['q10'][-1])} ~ {price(vr['q90'][-1])}（中枢 q50 = {price(vr['q50'][-1])}，只描述波动幅度，不含方向预测）；"
        f"三重屏障计划{plan_zone_label} {price(plan['entry_zone'][0])} ~ {price(plan['entry_zone'][1])}、止损 {price(plan['stop_loss_barrier'])}、"
        f"TP1 {price(plan['take_profit_1'])}（扣费后净盈亏比 {plan['risk_reward_ratio']:.2f}）。"
        f"本标的规则分 {verdict['rule_score']:.1f}/100（{verdict['rating_cn']}，确定性规则加权分，不是胜率或上涨概率），{mr_note}"
        f"当前评级为「{verdict['action_cn']}」（{verdict['setup_label_cn']}）。"
```

(b) 结论速览的条目改为：

```python
    lines += [
        "## 1. 结论速览",
        "",
        f"- 直出摘要（供 Agent / Flash 模型首段原样引用）：{flash_digest}",
        f"- 最新收盘：{price(last_close)}；计划类型：{verdict['setup_label_cn']}。",
        f"- 结论：{verdict['headline_cn']}",
        f"- 规则分：**{verdict['rule_score']:.1f} / 100**（{verdict['rating_cn']}）。这是确定性规则加权分，不是胜率，也不是上涨概率。",
        "",
    ]
```

(c) 把 `# 6. 未来区间` 整段（从这行注释到 `lines += ["- 这是逐日的边际区间，不代表整条价格路径的联合概率。", ""]`）剪切到 `# 3. 结构与条件推演` 注释之前；注释改为 `# 3. 未来区间`，标题里的 `## 6.` 改为 `## 3.`。

(d) 把原 `# 3. 结构与条件推演` 整段（从这行注释到 `# 4. 风控计划` 注释之前）剪切到 `# 5. 形态与信号证据` 注释之前；注释改为 `# 5. 结构与条件推演`，标题行改为：

```python
        "## 5. 结构与条件推演（触发 → 目标 → 失效）",
        "",
        STRUCTURE_LEAD_CN,
        "",
```

形态一节的注释与标题改为 `# 6. 形态与信号证据`、`"## 6. 形态与信号证据（公式可复核）"`。

(e) 风控计划一节的标题行改为：

```python
        "## 4. 风控计划（三重屏障）",
        "",
        RISK_LEAD_CN,
        "",
```

各段只用本段内定义的变量（`audit`/`horizon`/`steps`、`wedge`/`sp`、`rules`/`sizing`），移动后不需要改变量。

- [ ] **Step 5: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_report_and_metadata.py tests/test_v6_contract.py tests/test_quant_skill.py -q -n auto`
Expected: 三个文件全部通过（含 `test_quant_skill.py` 里检查直出摘要披露体制调整的用例、`test_v6_contract.py` 里"直出摘要加免责声明通过回复校验"的用例）。

- [ ] **Step 6: 提交**

```bash
git add kline-structure-chart/scripts/render_report_md.py kline-structure-chart/tests/test_report_and_metadata.py
git commit -m "feat(report): 报告先给正常波动范围与风控、后给结构，并加两句固定说明"
```

---

### Task 2: 回复契约、文档与示例同步（A）

**Files:**
- Modify: `kline-structure-chart/SKILL.md`（第四节"回复契约"、第七节"回复示例"）
- Modify: `kline-structure-chart/references/failure-modes.md`、`kline-structure-chart/references/interpretation-guide.md`
- Modify: `README.md`、`README.en.md`（第 303、383 行附近）
- Modify: `examples/*/…_report.md`（四份）、`examples/600519.SH/reply.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: Task 1 的 `RISK_LEAD_CN`、`STRUCTURE_LEAD_CN` 与 `artifacts/rerender_examples.py`。

- [ ] **Step 1: 重渲染四份示例报告并核对"内容不增不减"**

Run: `uv run --directory kline-structure-chart python artifacts/rerender_examples.py`

然后对每份报告比较改前改后按行排序后的差异：

```bash
for s in 600519.SH 300308.SZ 00700.HK NVDA.US; do
  echo "== $s"
  diff <(git show HEAD:examples/$s/${s}_report.md | sort) <(sort examples/$s/${s}_report.md)
done
```

Expected: 每份报告的差异只有这些行——改前的"直出摘要"一行换成改后的一行；`## 3.`、`## 5.`、`## 6.` 三个标题各一去一来；新增 `RISK_LEAD_CN`、`STRUCTURE_LEAD_CN` 两行以及随之多出的两个空行。出现其他差异说明 Task 1 移动代码时改到了节内内容，回去修。把这段输出留着，贴进 PR 描述。

- [ ] **Step 2: `SKILL.md` 回复契约**

第四节"回复契约"里，把现在的第 5 项（**未来 15 日正常波动范围**，含其下三条子项）整体移到第 2 项之后，编号改为 3；现在的第 4 项（**风控**）保持编号 4；现在的第 3 项（**结构与规则**，含其下子项）移到风控之后，编号改为 5。各项内部文字逐字保留。

在第 0 项"先答所问"后加一句：

```markdown
   - 用户问"能不能买""会不会涨"时，先给风控结论（是否否决、止损位、仓位）和正常波动范围，不给方向判断。
```

在第 4 项"风控"下加一条子项、第 5 项"结构与规则"下加一条子项：

```markdown
   - 原文转述报告第 4 节第一行："做多计划的盈亏主要取决于持有期大盘涨跌；计划的作用是把亏损限定在预算内，不代表方向判断。"
```

```markdown
   - 原文转述报告第 5 节第一行："以下只描述价格结构和条件路径，不预示涨跌方向。"
```

第七节"回复示例"里"正确的回复开头"第二段：把"结构上是扩散喇叭形…"那几句移到风控计划与正常波动范围的句子之后，数字一个都不改。

- [ ] **Step 3: `references/` 两处**

`references/failure-modes.md`：在清单末尾（现第 8 条之后）加一条，编号顺延：

```markdown
9. **是否把大盘带来的盈利说成分析准确**：做多计划的盈亏主要取决于持有期大盘涨跌。回测盈利、近期计划"都对了"时，是否说明了同期大盘的表现，而没有把它归因于形态或规则分？
```

`references/interpretation-guide.md`：全文搜索"报告第 3 节""报告第 5 节""报告第 6 节"，按新编号改（结构与条件推演 3→5、形态与信号证据 5→6、正常波动范围 6→3）；"报告第 2 节""报告第 4 节"不动。

Run: `grep -n "报告第 [356] 节" kline-structure-chart/references/*.md kline-structure-chart/SKILL.md`
Expected: 每一处命中都已按新编号核对过；没有命中也算通过。

- [ ] **Step 4: README 两版**

`README.md` 第 5 节那句改为：

```markdown
4. 以生成的 `*_report.md` 为骨架回答，顺序为：大盘 → 未来正常波动范围 → 风控计划 → 结构与条件推演 → 回测 → 文件路径 → 免责声明。报告中的数字不得改动。
```

输出文件表里 `*_report.md` 一行的顺序改为："数据说明、风控否决、结论、大盘、未来正常波动范围、风控计划、结构与条件推演、形态证据、回测、文件路径、免责声明"。

`README.en.md` 对应两处：

```markdown
4. It builds the answer on the generated `*_report.md`, in this order: market → future normal volatility range → risk plan → structure and conditional scenarios → backtest → file paths → disclaimer. Numbers from the report must not be changed.
```

表格一行的顺序改为："data notes, risk veto, conclusion, market, future normal volatility range, risk plan, structure and conditional scenarios, pattern evidence, backtest, file paths, disclaimer"。

- [ ] **Step 5: 标准回复 `examples/600519.SH/reply.md`**

合格回复部分（`---` 之后、`## 不合格的回复（反例）` 之前）按加粗小标题重排为：否决前缀段、**数据说明**、**大盘**、**未来 15 个交易日正常波动范围**、**风控计划（仅供观察）**、**结构与规则**、**浪型**、**历史回测**、**文件**、免责声明。各段文字与数字不改。在 **风控计划** 段开头加 `RISK_LEAD_CN` 一句，在 **结构与规则** 段开头加 `STRUCTURE_LEAD_CN` 一句。第一段若引用了旧的直出摘要，换成重渲染后报告第 1 节的新摘要原文。

- [ ] **Step 6: `CHANGELOG.md`**

在 `## [Unreleased]` 下最前面加：

```markdown
### 调整（报告与回复顺序）

- `report.md` 与 `SKILL.md` 回复契约改为先给正常波动范围和风控计划、后给结构与条件推演：第 3 节为正常波动范围，第 5 节为结构与条件推演，第 6 节为形态与信号证据；第 2、4、7、8 节编号不变。直出摘要的分句顺序同步调整，每个分句的文字与数字不变。
- 报告第 4 节与第 5 节的第一行各加一句固定说明（"做多计划的盈亏主要取决于持有期大盘涨跌…""以下只描述价格结构和条件路径…"）。原因：开发区回放显示做多计划的单笔盈亏跟随持有期大盘，而正常波动范围在各大盘状态下的覆盖率都接近名义值（见 `docs/superpowers/specs/2026-11-15-regime-attribution-and-market-layer-design.md` 的阶段 0 结果）。
- `result.json`、评级标签、看板与 PNG 不变。
```

- [ ] **Step 7: 验证**

Run: `uv run --directory kline-structure-chart pytest tests/test_docs_contract.py -q -n auto`
Expected: 全部通过（其中 `test_example_reply_passes_lint_and_counterexample_is_rejected` 用回复校验器检查 `reply.md` 与三份示例报告）。

Run: `git status --short examples/ | grep -v "_report.md\|reply.md"`
Expected: 没有输出（`result.json`、`backtest.json`、PNG、HTML 都没变）。

- [ ] **Step 8: 提交**

```bash
git add kline-structure-chart/SKILL.md kline-structure-chart/references README.md README.en.md examples CHANGELOG.md
git commit -m "docs: 回复契约、README 两版与示例按新的报告顺序同步"
```

---

### Task 3: `backtest.market_attribution`

**Files:**
- Modify: `kline-structure-chart/scripts/evaluation.py`（`_bootstrap_mean_ci` 之后加函数；`run_plan_backtest` 的返回字典加一项）
- Modify: `kline-structure-chart/tests/fixtures/contract/backtest_v6_keypaths_600519.json`
- Test: `kline-structure-chart/tests/test_report_and_metadata.py`

**Interfaces:**
- Produces: `evaluation._market_attribution(all_trades: list[dict], bench: dict | None, target_symbol: str, market: str) -> dict`，以及 `run_plan_backtest(...)["market_attribution"]`。返回字典的键固定为：`available`、`reason_cn`、`basis`、`benchmark_symbol`、`trades`、`mean_net_return_pct`、`mean_benchmark_hold_return_pct`、`mean_excess_vs_benchmark_pct`、`mean_excess_95ci_pct`、`benchmark_up`、`benchmark_down_or_flat`、`note_cn`；后两个分组对象的键为 `trades`、`mean_net_return_pct`。Task 4 的报告渲染读这些键。
- 成交字典用到的键（回放已有）：`entry_day: str`、`exit_day: str`、`net_return: float`（小数，不是百分比）。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_report_and_metadata.py` 末尾加入（文件顶部已有 `load_bundle`、`NOW`、`REQ_BASE`、`copy`）：

```python
# ---------------------------------------------------------------------------
# 回测拆出大盘贡献（backtest.market_attribution）
# ---------------------------------------------------------------------------
MA_KEYS = {
    "available", "reason_cn", "basis", "benchmark_symbol", "trades", "mean_net_return_pct",
    "mean_benchmark_hold_return_pct", "mean_excess_vs_benchmark_pct", "mean_excess_95ci_pct",
    "benchmark_up", "benchmark_down_or_flat", "note_cn",
}
MA_NOTE = (
    "超额是相对同一批持有期内的基准收益，不是相对随机选股；单只股票的回测没有股票池，无法判断选股本身有没有贡献。"
    "持有期大盘涨跌是事后才知道的，两组数字只用于拆解已发生的成交，不能用来择时。"
    "基准收益不含交易成本；个股用含分红的复权价，基准若是价格指数则不含分红，两者口径略有差异。"
)


def _moutai_backtest():
    from evaluation import run_plan_backtest

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE}
    bundle = load_bundle(req, now=NOW)
    return bundle, run_plan_backtest(bundle, req, horizon_bars=15, step=10)


@pytest.mark.slow
def test_market_attribution_conserves_and_matches_hand_computation(replay_recorded) -> None:
    from evaluation import _market_attribution

    bundle, bt = _moutai_backtest()
    ma = bt["market_attribution"]
    assert set(ma) == MA_KEYS and ma["note_cn"] == MA_NOTE and ma["basis"] == "ALL_LONG_SIGNALS"
    assert ma["available"] is True and ma["reason_cn"] is None and ma["benchmark_symbol"] == "000300.SH"
    assert ma["trades"] == bt["all_long_signals"]["trades"] >= 3
    assert ma["benchmark_up"]["trades"] + ma["benchmark_down_or_flat"]["trades"] == ma["trades"]
    lo, hi = ma["mean_excess_95ci_pct"]
    assert lo - 0.01 <= ma["mean_excess_vs_benchmark_pct"] <= hi + 0.01
    # 用回放给出的最近三笔真实成交，在测试里按定义独立算一遍
    trades = bt["sample_trades"]
    bars = bundle["benchmark"]["bars"]
    days = [b["day"] for b in bars]
    close = {b["day"]: float(b["close"]) for b in bars}
    nets, holds = [], []
    for t in trades:
        i = days.index(t["entry_day"])  # A 股标的的交易日都是沪深300 的交易日
        nets.append(float(t["net_return"]))
        holds.append(close[t["exit_day"]] / close[days[i - 1]] - 1.0)
    got = _market_attribution(trades, bundle["benchmark"], "600519.SH", "CN")
    n = len(trades)
    assert got["trades"] == n
    assert got["mean_net_return_pct"] == pytest.approx(sum(nets) / n * 100.0, abs=5e-4)
    assert got["mean_benchmark_hold_return_pct"] == pytest.approx(sum(holds) / n * 100.0, abs=5e-4)
    assert got["mean_excess_vs_benchmark_pct"] == pytest.approx(sum(a - b for a, b in zip(nets, holds)) / n * 100.0, abs=5e-4)
    up = [a for a, b in zip(nets, holds) if b > 0]
    assert got["benchmark_up"]["trades"] == len(up)
    assert got["benchmark_up"]["mean_net_return_pct"] == (pytest.approx(sum(up) / len(up) * 100.0, abs=5e-4) if up else None)


def test_market_attribution_uses_previous_benchmark_close_on_missing_days(replay_recorded) -> None:
    """入场日、出场日落在基准没有K线的日历日（用周六代表）：取之前最近一个基准收盘，不报错。"""
    from datetime import date, timedelta

    from evaluation import _market_attribution

    req = {"symbol": "00700.HK", "market": "HK", "asset_type": "equity", **REQ_BASE}
    bench = load_bundle(req, now=NOW)["benchmark"]
    days = [b["day"] for b in bench["bars"]]
    close = [float(b["close"]) for b in bench["bars"]]
    i = next(k for k in range(len(days) - 30, 20, -1) if date.fromisoformat(days[k]).weekday() == 4)  # 某个周五
    sat = (date.fromisoformat(days[i]) + timedelta(days=1)).isoformat()
    assert sat not in days
    # 周六"入场"：之前最近的基准收盘是周五 days[i]；周六"出场"：当天或之前最近的也是 days[i]
    t_in = {"entry_day": sat, "exit_day": days[i + 5], "net_return": 0.01}
    t_out = {"entry_day": days[i - 5], "exit_day": sat, "net_return": -0.02}
    got = _market_attribution([t_in, t_out], bench, "00700.HK", "HK")
    want = ((close[i + 5] / close[i] - 1.0) + (close[i] / close[i - 6] - 1.0)) / 2 * 100.0
    assert got["available"] is True and got["benchmark_symbol"] == "HSI.HK"
    assert got["mean_benchmark_hold_return_pct"] == pytest.approx(want, abs=5e-4)


def test_market_attribution_unavailable_cases(replay_recorded) -> None:
    from evaluation import _market_attribution

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", **REQ_BASE}
    bench = load_bundle(req, now=NOW)["benchmark"]
    trade = {"entry_day": bench["bars"][-20]["day"], "exit_day": bench["bars"][-10]["day"], "net_return": 0.01}
    early = {"entry_day": bench["bars"][0]["day"], "exit_day": bench["bars"][5]["day"], "net_return": 0.01}
    cases = [
        (_market_attribution([trade], None, "600519.SH", "CN"), "没有大盘基准行情"),
        (_market_attribution([trade], None, "000300.SH", "CN"), "标的自身即基准"),
        (_market_attribution([trade], bench, "000300.SH", "CN"), "标的自身即基准"),
        (_market_attribution([], bench, "600519.SH", "CN"), "没有成交"),
        (_market_attribution([early], bench, "600519.SH", "CN"), "之前没有基准收盘"),
    ]
    for got, reason in cases:
        assert set(got) == MA_KEYS and got["available"] is False and reason in got["reason_cn"], reason
        assert got["trades"] == 0 and got["mean_excess_95ci_pct"] is None
        assert got["mean_net_return_pct"] is None and got["mean_benchmark_hold_return_pct"] is None
        assert got["benchmark_up"] == {"trades": 0, "mean_net_return_pct": None} == got["benchmark_down_or_flat"]
    one = _market_attribution([trade], bench, "600519.SH", "CN")
    assert one["available"] is True and one["trades"] == 1 and one["mean_excess_95ci_pct"] is None  # 少于 2 笔不给区间
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_report_and_metadata.py -q -k market_attribution`
Expected: 3 个用例失败，原因是 `ImportError: cannot import name '_market_attribution'`（第一个用例在此之前先因 `KeyError: 'market_attribution'` 失败）。

- [ ] **Step 3: 实现**

`scripts/evaluation.py`：确认文件顶部已有 `import bisect`；在 `from market_rules import (…)` 的导入列表里加上 `DEFAULT_BENCHMARKS`（若尚未导入）。在 `_bootstrap_mean_ci` 之后加入：

```python
MARKET_ATTRIBUTION_NOTE_CN = (
    "超额是相对同一批持有期内的基准收益，不是相对随机选股；单只股票的回测没有股票池，无法判断选股本身有没有贡献。"
    "持有期大盘涨跌是事后才知道的，两组数字只用于拆解已发生的成交，不能用来择时。"
    "基准收益不含交易成本；个股用含分红的复权价，基准若是价格指数则不含分红，两者口径略有差异。"
)


def _market_attribution(all_trades: list[dict[str, Any]], bench: dict[str, Any] | None, target_symbol: str, market: str) -> dict[str, Any]:
    """把"全部做多信号"的成交和同一批持有期内的基准收益放在一起（规格 2026-11-16 第 3 节）。

    持有期基准收益：入场日之前最近一个基准收盘 → 出场日当天或之前最近一个基准收盘。
    只读已完成的回放结果，不进入任何决策日的输入。
    """
    def group(nets: list[float]) -> dict[str, Any]:
        return {"trades": len(nets), "mean_net_return_pct": round(sum(nets) / len(nets) * 100.0, 3) if nets else None}

    out: dict[str, Any] = {
        "available": False, "reason_cn": None, "basis": "ALL_LONG_SIGNALS", "benchmark_symbol": None, "trades": 0,
        "mean_net_return_pct": None, "mean_benchmark_hold_return_pct": None, "mean_excess_vs_benchmark_pct": None,
        "mean_excess_95ci_pct": None, "benchmark_up": group([]), "benchmark_down_or_flat": group([]),
        "note_cn": MARKET_ATTRIBUTION_NOTE_CN,
    }
    bars = (bench or {}).get("bars") or []
    b_symbol = str(bench["instrument"]["symbol"]) if bars else None
    default_symbol = DEFAULT_BENCHMARKS.get(market, {}).get("symbol", "")
    if target_symbol.upper() in {(b_symbol or "").upper(), default_symbol.upper()}:
        out["reason_cn"] = "标的自身即基准，不做拆解"
        return out
    if not bars:
        out["reason_cn"] = "没有大盘基准行情，无法拆出大盘贡献"
        return out
    out["benchmark_symbol"] = b_symbol
    if not all_trades:
        out["reason_cn"] = "回放区间内没有成交"
        return out
    days = [str(b["day"]) for b in bars]
    closes = [float(b["close"]) for b in bars]
    pairs: list[tuple[float, float]] = []
    for t in all_trades:
        i = bisect.bisect_left(days, str(t["entry_day"])) - 1
        j = bisect.bisect_right(days, str(t["exit_day"])) - 1
        if i < 0:
            out["reason_cn"] = f"入场日 {t['entry_day']} 之前没有基准收盘，无法计算持有期基准收益"
            return out
        pairs.append((float(t["net_return"]), closes[j] / closes[i] - 1.0))
    n = len(pairs)
    out.update(
        available=True,
        trades=n,
        mean_net_return_pct=round(sum(a for a, _ in pairs) / n * 100.0, 3),
        mean_benchmark_hold_return_pct=round(sum(b for _, b in pairs) / n * 100.0, 3),
        mean_excess_vs_benchmark_pct=round(sum(a - b for a, b in pairs) / n * 100.0, 3),
        mean_excess_95ci_pct=_bootstrap_mean_ci([a - b for a, b in pairs]),
        benchmark_up=group([a for a, b in pairs if b > 0]),
        benchmark_down_or_flat=group([a for a, b in pairs if b <= 0]),
    )
    return out
```

在 `run_plan_backtest` 的返回字典里，`"buy_and_hold_return_pct": round(bh_ret, 2),` 之后加一行：

```python
        "market_attribution": _market_attribution(all_trades, bench, symbol, market),
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_report_and_metadata.py -q -k market_attribution`
Expected: 3 passed。

- [ ] **Step 5: 更新键路径快照**

Run: `UPDATE_V6_KEYPATHS=1 uv run --directory kline-structure-chart pytest -q tests/test_v6_contract.py -k backtest_keypaths_match_snapshot`
Expected: 测试改写快照后以失败结束（这是设计行为）。

Run: `git diff kline-structure-chart/tests/fixtures/contract/backtest_v6_keypaths_600519.json`
Expected: 只有新增行，共 17 条，全部以 `backtest.market_attribution` 开头（对象本身 1 条、12 个键、两个分组对象各 2 个子键）。不得出现任何删除行。

Run: `uv run --directory kline-structure-chart pytest -q tests/test_v6_contract.py -k keypaths_match_snapshot`
Expected: 全部通过。

- [ ] **Step 6: 核对已有字段不变**

把下面的脚本存为 `kline-structure-chart/artifacts/compare_backtest.py`：

```python
"""同一录制配置下，新旧代码的 backtest 去掉 market_attribution 后必须逐项相同。用法见实施计划 Task 3。"""
import json
import sys

new = json.load(open(sys.argv[1], encoding="utf-8"))
old = json.load(open(sys.argv[2], encoding="utf-8"))
assert "market_attribution" in new["backtest"] and "market_attribution" not in old["backtest"]
new["backtest"].pop("market_attribution")
assert new == old, "已有字段发生变化"
print("OK: 除 market_attribution 外逐项相同")
```

对比对象是茅台示例：Task 4 第 3 步重新生成 `examples/600519.SH/600519.SH_backtest.json` 之后执行
`uv run --directory kline-structure-chart python artifacts/compare_backtest.py ../examples/600519.SH/600519.SH_backtest.json <(git show HEAD:examples/600519.SH/600519.SH_backtest.json)`。这一步在 Task 4 里做，这里只把脚本放好。

- [ ] **Step 7: 提交**

```bash
git add kline-structure-chart/scripts/evaluation.py kline-structure-chart/tests/test_report_and_metadata.py kline-structure-chart/tests/fixtures/contract/backtest_v6_keypaths_600519.json
git commit -m "feat(backtest): 新增 backtest.market_attribution，把单笔收益与同期基准收益并排给出"
```

---

### Task 4: 归因的呈现、回复契约与茅台示例

**Files:**
- Modify: `kline-structure-chart/scripts/render_report_md.py`（`# 7. 回测` 一段）
- Modify: `kline-structure-chart/SKILL.md`、`kline-structure-chart/references/output-fields.md`、`kline-structure-chart/references/interpretation-guide.md`
- Modify: `examples/600519.SH/600519.SH_backtest.json`、`examples/600519.SH/600519.SH_report.md`、`examples/600519.SH/reply.md`
- Modify: `CHANGELOG.md`
- Test: `kline-structure-chart/tests/test_report_and_metadata.py`

**Interfaces:**
- Consumes: Task 3 的 `backtest.market_attribution`（键见 Task 3 的 Interfaces）。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_report_and_metadata.py` 末尾加入：

```python
@pytest.mark.slow
def test_report_md_backtest_shows_market_attribution(cn_pipeline) -> None:
    report, md, backtest = cn_pipeline
    ma = backtest["backtest"]["market_attribution"]
    assert ma["available"] is True
    line = next(ln for ln in md.splitlines() if ln.startswith("- 大盘贡献拆解"))
    assert md.index("- 同期买入持有收益") < md.index(line)
    for key in ("mean_net_return_pct", "mean_benchmark_hold_return_pct", "mean_excess_vs_benchmark_pct"):
        assert f"{ma[key]:.3f}%" in line, key
    assert f"{ma['benchmark_up']['trades']} 笔" in line and f"{ma['benchmark_down_or_flat']['trades']} 笔" in line
    assert "不是相对随机选股" in line
    # 不可用时写一行原因，不给数字
    off = copy.deepcopy(backtest)
    off["backtest"]["market_attribution"] = {**ma, "available": False, "reason_cn": "没有大盘基准行情，无法拆出大盘贡献"}
    md_off = render_report_markdown(load_v6(report)[0], off)
    assert "- 大盘贡献拆解：未计算（没有大盘基准行情，无法拆出大盘贡献）。" in md_off


@pytest.mark.slow
def test_report_md_backtest_without_market_attribution_still_renders(cn_pipeline) -> None:
    """旧版本生成的 backtest.json 没有 market_attribution：重渲染不报错，也不出现归因一行。"""
    report, _, backtest = cn_pipeline
    old = copy.deepcopy(backtest)
    old["backtest"].pop("market_attribution")
    md = render_report_markdown(load_v6(report)[0], old)
    assert "## 7. 历史计划回放回测" in md and "大盘贡献拆解" not in md
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_report_and_metadata.py -q -k "shows_market_attribution or without_market_attribution"`
Expected: 第一个用例因 `StopIteration`（找不到"- 大盘贡献拆解"一行）失败；第二个用例通过（现在本来就不渲染这一行，它是防回归用的）。

- [ ] **Step 3: 实现报告一行并重新生成茅台示例**

`scripts/render_report_md.py` 的 `# 7. 回测` 一段，在 `f"- 同期买入持有收益：…"` 所在的 `lines += [...]` 之后加入：

```python
        ma = bt.get("market_attribution")
        if ma and ma.get("available"):
            ci = ma.get("mean_excess_95ci_pct")
            ci_text = f"（95% 区间 {ci[0]:.2f}% ~ {ci[1]:.2f}%）" if ci else ""

            def _grp(g: dict[str, Any]) -> str:
                mean = g.get("mean_net_return_pct")
                return f"{g['trades']} 笔" + ("" if mean is None else f"、单笔平均净收益 {mean:.3f}%")

            lines += [
                f"- 大盘贡献拆解（全部做多信号 {ma['trades']} 笔，基准 {ma['benchmark_symbol']}）：单笔平均净收益 {ma['mean_net_return_pct']:.3f}%，"
                f"同期基准平均收益 {ma['mean_benchmark_hold_return_pct']:.3f}%，相对基准的超额 {ma['mean_excess_vs_benchmark_pct']:.3f}%{ci_text}；"
                f"持有期大盘上涨的 {_grp(ma['benchmark_up'])}，大盘不涨的 {_grp(ma['benchmark_down_or_flat'])}。"
                "这是相对同期基准，不是相对随机选股；持有期大盘涨跌是事后变量，只用于拆解，不能用来择时。"
            ]
        elif ma:
            lines += [f"- 大盘贡献拆解：未计算（{ma.get('reason_cn')}）。"]
```

重新生成茅台示例的回测（用录制行情回放，不联网）。把下面的文件存为 `kline-structure-chart/tests/test_zz_regen_example_tmp.py`，跑完即删，不提交：

```python
"""一次性：用录制行情回放重新生成 examples/600519.SH 的 backtest.json。跑完删除本文件。"""
import shutil
from pathlib import Path

from analyze_and_forecast import run_pipeline

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "600519.SH"


def test_regen(replay_recorded, tmp_path: Path) -> None:
    rep = run_pipeline(
        {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "account_equity": 500000.0,
         "as_of": "2026-10-15", "display_bars": 120, "horizon_bars": 15},
        output_dir=tmp_path, run_backtest=True, backtest_step=1,
    )
    paths = rep["rendered_paths"]
    assert Path(paths["result_json"]).read_bytes() == (EXAMPLE / "600519.SH_result.json").read_bytes(), "result.json 必须逐字节不变"
    shutil.copyfile(paths["backtest_json"], EXAMPLE / "600519.SH_backtest.json")
```

Run: `uv run --directory kline-structure-chart pytest tests/test_zz_regen_example_tmp.py -q && rm kline-structure-chart/tests/test_zz_regen_example_tmp.py`
Expected: 1 passed。若 `result.json` 的断言失败，说明示例当初的生成参数与上面不同：对照 `examples/600519.SH/600519.SH_result.json` 的 `meta.request` 把请求参数补齐到逐字节相同为止，不要改断言。

Run: `uv run --directory kline-structure-chart python artifacts/compare_backtest.py ../examples/600519.SH/600519.SH_backtest.json <(git show HEAD:examples/600519.SH/600519.SH_backtest.json)`
Expected: `OK: 除 market_attribution 外逐项相同`。

Run: `uv run --directory kline-structure-chart python artifacts/rerender_examples.py && git status --short examples/`
Expected: 只有 `examples/600519.SH/600519.SH_backtest.json` 与 `examples/600519.SH/600519.SH_report.md` 两个文件有改动；报告的差异只是第 7 节多出"- 大盘贡献拆解"一行。

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_report_and_metadata.py -q -k "market_attribution"`
Expected: 5 passed。

- [ ] **Step 5: 回复契约与字段字典**

`SKILL.md` 第四节第 6 项"回测"末尾加一句：

```markdown
   有 `backtest.market_attribution` 且 `available = true` 时，转述报告第 7 节"大盘贡献拆解"一行（单笔平均净收益、同期基准平均收益、相对基准的超额及区间、两组的笔数与平均净收益），并说明这是相对同期基准、不是相对随机选股；不得把"持有期大盘上涨"一组的平均收益说成大盘好时的预期收益。`available = false` 时转述 `reason_cn`。
```

`references/output-fields.md` 的 `## *_backtest.json` 表里，在 `backtest.buy_and_hold_return_pct` 一行之后加：

```markdown
| `backtest.market_attribution` | "全部做多信号"成交与同期基准的对照：`available`、`reason_cn`、`basis`（固定 `ALL_LONG_SIGNALS`）、`benchmark_symbol`、`trades`、`mean_net_return_pct`、`mean_benchmark_hold_return_pct`（入场日前一个基准收盘 → 出场日基准收盘）、`mean_excess_vs_benchmark_pct` 与 `mean_excess_95ci_pct`（Bootstrap）、`benchmark_up` / `benchmark_down_or_flat`（持有期基准收益大于 0 与否两组的 `trades`、`mean_net_return_pct`）、`note_cn` | 笔 / % | `available = false` 时数值为空 | 相对同期基准，不是相对随机选股；两组数字是事后拆解，不能说成择时依据或预期收益 |
```

`references/interpretation-guide.md` 第 10 节"回测"末尾加一段：

```markdown
- `market_attribution` 回答"这批成交的盈亏里有多少是大盘带来的"：先看 `mean_benchmark_hold_return_pct`，再看 `mean_excess_vs_benchmark_pct` 的区间是否包含 0。区间包含 0 时只能说"扣掉同期大盘后看不出差别"。它回答不了选股有没有贡献（单只股票没有随机对照），`benchmark_up` / `benchmark_down_or_flat` 两组用的是事后才知道的大盘涨跌，只用于拆解。
```

- [ ] **Step 6: 标准回复与 `CHANGELOG.md`**

`examples/600519.SH/reply.md` 的 **历史回测** 段末尾，加上重渲染后报告第 7 节"大盘贡献拆解"一行的转述，数字逐字取自报告。

`CHANGELOG.md` 的 `## [Unreleased]` 下加：

```markdown
### 新增（回测拆出大盘贡献）

- **契约变更（`backtest.json`，不在 schema 约束内）**：新增 `backtest.market_attribution`，按"全部做多信号"的成交给出单笔平均净收益、同一批持有期内基准的平均收益、相对基准的超额及 95% 区间，以及持有期大盘上涨与不涨两组的笔数和平均净收益。没有基准、标的自身即基准或零成交时 `available = false` 并给出 `reason_cn`。已有字段不变。
- `report.md` 第 7 节在"同期买入持有收益"之后加"大盘贡献拆解"一行；`SKILL.md` 回复契约、字段字典与解读指南同步。
- 不做同日随机对照（单标的没有股票池）与回归（单只股票成交笔数太少）。
```

- [ ] **Step 7: 验证并提交**

Run: `uv run --directory kline-structure-chart pytest tests/test_docs_contract.py tests/test_report_and_metadata.py tests/test_v6_contract.py -q -n auto`
Expected: 全部通过（含示例回复与三份示例报告的回复校验）。若回复校验拦截了新增句子里的某个词，改句子并同步规格第 2.3、3.3 节，不要改校验器。

```bash
git add kline-structure-chart/scripts/render_report_md.py kline-structure-chart/tests/test_report_and_metadata.py kline-structure-chart/SKILL.md kline-structure-chart/references examples CHANGELOG.md
git commit -m "feat(report): 报告第 7 节与回复契约呈现回测的大盘贡献拆解，茅台示例同步"
```

---

### Task 5: 合并前门禁

**Files:** 无（只跑验证）

- [ ] **Step 1: 日常门禁**

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过，耗时 20 秒以内。

- [ ] **Step 2: 全量**

Run: `uv run --directory kline-structure-chart pytest -v -n auto`
Expected: 全部通过。任何失败（包括与本次改动无关的）都按名字记进 PR 描述。

- [ ] **Step 3: 跨窗口不变性**

Run: `uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`
Expected: 通过（本次改动不涉及结构层，这一步只确认没有碰到）。

- [ ] **Step 4: 示例未被意外改动**

Run: `git diff --stat main -- examples/ | cat`
Expected: 只有四份 `_report.md`、`examples/600519.SH/600519.SH_backtest.json` 与 `examples/600519.SH/reply.md`；没有任何 `_result.json`、`_chart_data.json`、PNG 或 HTML。
