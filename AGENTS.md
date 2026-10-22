# AGENTS.md — `stock-skill` 工程与智能体协作规范

本仓库承载多市场（A股 `CN`、港股 `HK`、美股 `US`）K线数学结构识别、三重屏障风控、分位区间预测、无前视回测与同源双出（`JSON + PNG + 离线交互式 HTML`）技能包 `kline-structure-chart`。

---

## 0. 文档分工（单一信息源）

| 文档 | 读者 | 负责内容 |
| --- | --- | --- |
| `AGENTS.md`（本文件） | 修改本仓库代码的开发者与编码 agent | 工程铁律、目录职责、开发/门禁命令、审计准则 |
| `kline-structure-chart/SKILL.md` | 使用该技能回答用户的 agent | 运行命令、运行后必检、回复契约、术语与免责要求 |
| `kline-structure-chart/references/*.md` | 两者按需查阅 | 公式定义（`math-patterns.md`）与三市场制度规则（带生效日期与来源） |
| `SKILL_REVIEW_CHECKLIST.md` | 审查者 | 问题清单、修复进度与可复用勾选清单 |
| `README.md` | 想安装、使用这个技能的人 | 安装、注册到各 Agent、命令行用法、输出解读与故障排查 |
| `INSTALL_FOR_AGENTS.md` | 替用户安装本技能的 AI 助手 | 对话安装步骤：选技能目录、检查前置、一键脚本或 venv + pip、自检与安全边界 |
| `CHANGELOG.md` | 所有人 | 版本记录（与四处版本号同步，未发布内容写在 `[Unreleased]`） |
| `examples/` | 所有人 | 真实行情生成的完整示例（报告、结果 JSON、PNG 与标准回复），注明截止日 |

- `AGENTS.md` 不随 skill 分发。运行期规则必须在 `SKILL.md` 与 `references/` 中自洽，本文件只引用、不重复。
- 公式与制度规则只在 `references/` 定义一次。代码、`SKILL.md`、本文件出现的数值必须与之一致，修改时同步更新对应测试。

---

## 1. 核心架构与不可变铁律（Hard Invariants）

0. **数据真实性（Data Authenticity）——优先于其余所有铁律**
   - 面向用户的产物与全部自动化测试只能基于真实行情（新浪 / Yahoo / 腾讯实时取数，`tests/fixtures/recorded/` 录制的真实行情，或用户提供的本地真实文件）。取不到时 `data_gateway.load_bundle` 抛出 `RuntimeError`，CLI 以 `[DATA_UNAVAILABLE]` 非零退出，**严禁回退、估算、补数或生成任何合成行情**。
   - `quality.status` 只能由 `contracts.assess_data_quality` 按来源枚举判定，标的与基准分别判定。若任何外部输入被标记为合成或离线假样本（`SYNTHETIC`），`quality.status = UNAVAILABLE`，PNG 与 HTML 必须带红色告警横幅和水印。
   - 外部数据（VIX、除权除息、财报日）取不到时写 `null` 并注明来源，禁止写死默认值。
   - 未收盘的K线（按交易所时区与收盘时间判定）不得参与指标、形态、风控与预测计算，必须拆出为盘中K线并在 `quality.notes` 中披露。数据源缺少最近已收盘交易日（`market_rules.latest_closed_session`）时同样只在 `quality.notes` 披露，不得用其他来源补数。

1. **算图分离（Compute-Render Separation）**
   - 所有指标、形态、SMC、Volume Profile、规则分、三重屏障点位与分位区间，必须 100% 由 Python 脚本从 OHLCV 序列计算并写入 JSON。**严禁调用视觉模型（CV/VLM）看图识别形态或猜价格，严禁调用任何图像生成模型绘制 K 线图。**
   - 结构层只在 `structure.compute_structure_layer` 计算一次。主流程把同一份结果交给 `analysis.py` 与 `render_structure_chart.py`；`render_html.py` 只读取 JSON。渲染层不得重算点位或篡改结论（`test_g02_*` 守护）。

2. **先大盘、后个股（Market-First Hierarchy）**
   - 分析个股或 ETF 前必须加载对应市场基准（A股 `000300.SH`，港股 `HSI.HK`，美股 `SPY.US`），计算 `market_benchmark.regime_score` 与 `relative_strength_vs_index`。基准取不到时整次分析失败；本地文件输入缺少基准时，`quality.status = PARTIAL`。
   - 大盘极弱（`regime_score < 35`）时，风控必须否决（`risk_officer_veto = true`），禁止输出激进做多评级。

3. **严格因果性与零前视泄漏（Zero Lookahead Bias）**
   - 核平滑采用单边因果 Nadaraya-Watson；波段拐点同时记录 `day` 与 `confirmed_at_day`。
   - 分位区间的校准与审计只使用在当时已完全成熟的样本；回测在每个决策日只用该日及之前的数据调用线上同一流水线（`evaluation.run_plan_backtest`），不学习任何参数，回放中不使用当前 VIX。
   - 复权（`apply_point_in_time_adjustment`）只消费截至 `as_of` 已生效的除权除息事件，并保留 `raw_close` 供真实成交使用。

4. **窗口不变性（Cross-Window Invariance）**
   - `--days N`（`60~500`）只决定主图显示根数与背景层（`max(N, 250)`）。取数长度固定为 `data_gateway.FETCH_BARS`，指标在固定 420 根历史上预热。除展示字段外，整份 v5 报告必须与 `--days` 无关（由 `test_c06_*` 守护），v4.1 结构层还必须通过 `tests/check_days_invariance.py`。

5. **概念、术语与概率边界**
   - 对外表述规则以 `SKILL.md` 的"对外表述与概率规则"一节为准。代码中的字段名、注释与文案同样不得把规则分（`rule_score`）、过滤分称为"胜率 / 概率 / 置信度"，不得把 Volume Profile 称为真实持仓筹码。
   - 只允许两类概率，且**只有对应的 JSON 字段存在后才能输出**；字段落地之前，任何对外文字、代码文案都不得出现概率数字：
     1. **历史条件频率**：脚本在标的自身历史上逐点无前视地统计"某情景触发后先到目标 / 先到失效 / 到期"的频率，必须同时给出样本数 n 与 95% 区间，称"历史条件频率"，不叫胜率或上涨概率（已由阶段 2 写入 `rule_scenarios.scenarios[*].historical_frequency` 及 `rule_scenarios.{bull,neutral,bear}.historical_frequency`）。
     2. **主观研判概率**：运行技能的 LLM 以历史条件频率为锚点做有界调整（调整幅度有上限、引用证据编号），每次研判记入日志待到期打分，并标注"主观研判"（已由阶段 3 提供 `*_judgment_pack.json`、`validate_judgment.py` 与 `score_judgments.py` 闭环校验）。
   - 回测成交胜率（`*_backtest.json` 的 `win_rate_pct`，附 Wilson 95% 区间与样本量提示）是已发生成交的统计，可按报告原文转述，但不得说成未来上涨概率。

6. **三市场微观交易制度**
   - 涨跌停、价位表、印花税、交易日历、结算与配色等以 `references/{cn,hk,us}-market.md` 为准，实现位于 `market_rules.py`。
   - 每条规则必须带生效日期与来源。规则变化时同时修改：代码、`references/`、对应 `test_p1_*` 测试。
   - 交易日历只覆盖到 `CALENDAR_COVERAGE_END`。新一年放假安排公布后必须补录；超出覆盖范围的日期须在输出中标注为估算。

---

## 2. 目录结构与模块职责

```text
kline-structure-chart/
├── SKILL.md                          # 运行契约（随 skill 分发）
├── pyproject.toml / uv.lock          # Python >= 3.12；运行期只依赖 Pillow，dev 组含 numpy、pytest、pytest-xdist（并行跑测试）与 TA-Lib（仅用于金标准测试）
├── requirements.txt                  # 没有 uv 时 venv + pip 用的运行期依赖，版本与 uv.lock 一致（test_docs_contract 校验）
├── assets/
│   └── analysis.schema.json          # AnalysisReport 顶层契约（contracts.validate_report 以它为唯一来源）
├── scripts/
│   ├── analyze_and_forecast.py       # 主 CLI：取数 -> 结构层 -> 分析 -> JSON+PNG+HTML (+回测)
│   ├── contracts.py                  # TypedDict 契约、数据来源枚举与质量判定、NaN/分位单调校验、安全文件名
│   ├── market_rules.py               # CN/HK/US 日历、涨跌停、价位表、印花税、收盘判定、代码映射与点位复权
│   ├── data_gateway.py               # 新浪/Yahoo/腾讯取数、厂商数据校验、除权除息事件、腾讯快照补全简称/ST/港股每手股数、未收盘K线拆分、基准配对
│   ├── structure.py                  # 结构层纯计算（拐点/轨道打分拟合/主+备选浪型计数/斐波那契/阶梯/三条推演），JSON 与 PNG 共用
│   ├── scenarios.py                  # L1 多情景引擎（8 类标准情景前提判定、推动5浪识别、2~5 个活跃情景筛选、历史用时分位时间窗）
│   ├── base_rates.py                 # L2 逐点无前视历史条件频率（三重屏障事件研究、95% Wilson 区间、小样本 Beta-Binomial 向基准收缩）
│   ├── price_text.py                 # 价格文本识别与可引用价位收集
│   ├── lint_response.py              # Agent 回复文本契约校验器（否决置顶、价格白名单、禁用术语与免责声明检查）
│   ├── indicators.py                 # SMA/EMA/MACD/RSI/Stoch/ATR/ADX/Supertrend/BB/RVOL/VWAP + 量价因子
│   ├── patterns.py                   # 因果核平滑、因果 ZigZag、蜡烛形态、双底/双顶/通道、RSI 背离、SMC、Volume Profile
│   ├── strategies.py                 # 五类 Regime 分类、五个规则策略、多周期规则评分
│   ├── risk.py                       # 左右侧计划、三重屏障点位、仓位计算、多空证伪与风控否决
│   ├── forecast.py                   # 未来 H 步分位区间（收盘价中心、按期限 split-conformal 校准与样本外审计）
│   ├── evaluation.py                 # 因果回放审计、线上计划逐日回放回测
│   ├── analysis.py                   # 纯计算编排器与历史回放快照
│   ├── resolve_symbol.py             # 中文名/拼音/代码转本技能代码（腾讯证券搜索；多结果列候选，找不到 [INVALID_ARGUMENT]）
│   ├── doctor.py                     # 环境自检：Python/uv/依赖/中文字体/数据源连通/技能软链，逐项 ✅/❌ 与修复建议
│   ├── render_html.py                # 单文件离线交互 HTML（XSS 安全转义、数据质量横幅）
│   ├── render_report_md.py           # 按 SKILL.md 回复契约生成中文 Markdown 报告（数字全部取自 JSON）
│   └── render_structure_chart.py     # 1920x1160 PNG 渲染器（按市场配色；保留 v4.1 CLI）
├── references/                       # 公式字典、字段字典、解读指南与三市场制度规则（单一信息源）
│   ├── math-patterns.md              # 指标、形态、预测区间公式
│   ├── scenario-playbook.md          # 8 类标准情景库定义、历史条件频率与 Brier 检验口径（与 scenarios.py 一一对应）
│   ├── output-fields.md              # result.json / backtest.json 字段字典
│   ├── interpretation-guide.md       # 形态、浪型、市场状态、风控与区间的解读要点和常见误读
│   ├── failure-modes.md              # 撰写回复时的常见错误
│   └── cn-market.md / hk-market.md / us-market.md  # 交易时段、涨跌停、价位表、做空、日历（带生效日期与来源）
├── artifacts/                        # 运行产物（生成物，不提交、不随 skill 分发）
└── tests/
    ├── conftest.py                   # 测试统一断网、录制数据回放夹具
    ├── test_quant_skill.py           # 单元、安全、P0/P1 回归测试
    ├── test_indicator_golden.py      # 在录制的真实行情上与 TA-Lib 逐点对照
    ├── test_data_sources.py          # 录制数据端到端回放 + 网络/厂商数据异常 mock
    ├── test_docs_contract.py         # 文档契约：无绝对路径、引用文件存在、目录树与代码同步
    ├── test_report_and_metadata.py   # 代码校验、行情快照元数据（简称/ST/每手股数）与 Markdown 报告
    ├── test_tools.py                 # 名称转代码（录制回放）、doctor 离线自检、插件市场清单与 install.sh 语法
    ├── test_v6_contract.py           # v6 输出契约：去预测化、schema、体积预算与命令行信封
    ├── check_days_invariance.py      # 跨展示窗口 (60~500日) 结构层不变性回归校验
    ├── record_fixtures.py            # 联网录制真实行情 fixture（手动执行）
    └── fixtures/
        └── recorded/                 # 真实行情录制（含 MANIFEST.json：录制时间、来源、sha256；禁止存放合成序列）
```

仓库根目录另有（均不随 skill 分发）：

```text
install.sh / install.ps1              # 一键安装、更新、卸载（macOS·Linux / Windows），默认代码目录 ~/.local/share/stock-skill
INSTALL_FOR_AGENTS.md                 # 给 AI 助手的对话安装指引（README 3.0 的提示词指向它）
.claude-plugin/marketplace.json       # Claude Code 插件市场清单（source "./"，skills 指向 kline-structure-chart）
.github/workflows/ci.yml              # CI：uv + pytest + 跨窗口不变性（只用录制数据）
examples/                             # 真实行情示例产物与标准回复
CHANGELOG.md                          # 版本记录
```

---

## 3. 标准开发、运行与验证命令

以下命令都在仓库根目录执行，通过 `uv run --directory kline-structure-chart` 进入技能目录，不依赖本机绝对路径。

### 3.1 环境同步与技能安装（跨 Agent 发现目录软链）
```bash
uv sync --python 3.12 --directory kline-structure-chart
# 用户安装走 install.sh（克隆到 ~/.local/share/stock-skill 并软链）；开发时可从本地检出演练，务必使用临时 HOME：
HOME="$(mktemp -d)" bash install.sh --source "$(pwd)" --agents claude,codex
# 按需软链到 Claude Code / Codex / Cursor / Gemini CLI 的技能发现目录：
mkdir -p ~/.claude/skills ~/.agents/skills && \
  ln -sfn "$(pwd)/kline-structure-chart" ~/.claude/skills/kline-structure-chart && \
  ln -sfn "$(pwd)/kline-structure-chart" ~/.agents/skills/kline-structure-chart
```

### 3.2 多市场诊断、15 日区间、同源双出与回测
```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py \
  --market CN --symbol 600519.SH --asset-type equity --account-equity 500000 --output-dir artifacts/cn --backtest
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py \
  --market HK --symbol 00700.HK --asset-type equity --account-equity 500000 --output-dir artifacts/hk --backtest
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py \
  --market US --symbol NVDA.US --asset-type equity --account-equity 100000 --output-dir artifacts/us --backtest
```

### 3.3 测试与回归门禁

| 层级 | 何时跑 | 目标耗时 |
| --- | --- | --- |
| 日常 | 每次改动后 | < 20 秒 |
| 合并前 | 提 PR 前与 CI | 约 1.5 分钟 |

```bash
# 日常：跳过约 5 秒以上的端到端流水线与回测用例（标记 slow），pytest-xdist 按 CPU 核数并行
uv run --directory kline-structure-chart pytest -n auto -m "not slow"

# 合并前 1：全量 pytest（单元、P0~P3 回归、TA-Lib 金标准、真实录制数据回放、网络异常、文档契约）
uv run --directory kline-structure-chart pytest -v -n auto

# 合并前 2：v4.1 结构层跨窗口不变性（真实录制行情）
uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40
```

### 3.4 重新录制真实行情 fixture（需联网，按需手动执行）
```bash
uv run --directory kline-structure-chart python tests/record_fixtures.py
# 只补录某一类（sina / yahoo / tencent / events / quotes / search），其余文件与 MANIFEST 条目保持不变
uv run --directory kline-structure-chart python tests/record_fixtures.py --only quotes
```

---

## 4. 代码修改与交付审计准则

1. **证据先行**：报告任何任务"通过""修复"或"完成"前，必须在同一轮执行对应的测试或验证命令，并附上真实的命令输出与精确数值。
2. **数据真实性自查**：
   - 新增取数路径必须同时提供失败时报错的测试（`test_data_sources.py`），并走 `data_gateway._http_get`，以便测试统一断网（例如 `resolve_symbol.fetch_tencent_search_raw`）。
   - 测试一律断网（`conftest.py`），所有行情测试统一使用 `fixtures/recorded/` 真实录制数据回放，严禁在测试中生成或使用合成行情（`generate_deterministic_series` / `synthetic_daily`）。
   - 四处版本号（`SKILL.md`、`pyproject.toml`、`assets/analysis.schema.json`、`analysis.py`）必须保持同步（当前 `5.3.0`）。
3. **契约与安全校验**：
   - 任何新增 JSON 字段不得包含 `NaN` 或 `Infinity`（`contracts.validate_report` 拦截）。
   - 写入 `<script>` 的 JSON 必须经过 `_safe_json_for_script` 转义；页面字串一律用 `textContent` 或 `html.escape`。
4. **指标口径**：修改 `indicators.py` 后必须通过 `test_indicator_golden.py`。若有意偏离 TA-Lib 口径，须在测试与 `references/math-patterns.md` 中写明原因。
5. **UI 变更必测浏览器**：修改 `render_html.py` 或 `render_structure_chart.py` 后，必须实际生成产物并在浏览器中检查图层开关、相对强弱切换、时间回放滑块及控制台零报错；PNG 需目视确认配色与文字随市场切换。
