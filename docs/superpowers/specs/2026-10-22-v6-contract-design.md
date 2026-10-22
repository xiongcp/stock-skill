# v6 输出契约与去预测化：设计方案

- 日期：2026-10-22
- 状态：设计已逐节确认，待审阅 spec
- 范围：产品方向调整后的第一个子项目（共四个，见第 1 节）

---

## 1. 背景与产品方向

### 1.1 为什么调整

- 长周期回测（`2026-10-20-long-horizon-backtest-report.md`）与留出集 H1（`2026-10-20-backtest-acceptance-revision.md` 第 11 节）表明：主做多计划扣费后净正率约 50%，15 日后收盘上涨的比例约 51%，高置信反转层在 CN、US 上均未通过验收。
- 只要产品对外承诺"有优势的买点或概率"，每个数字都要经过预注册、留出集与前向跟踪，一轮数月，而且大概率不通过，开发因此被拖慢。

### 1.2 新定位

- 不对外承诺预测与胜率。定位为**诚实的技术面 + 风控 + 复盘助手**：确定性的结构识别、三市场交易制度、风控点位、仓位与大盘否决。
- 研究线（H2 前向跟踪、alpha 探索）改为异步旁路，不阻塞技能发版。验收通过的能力，以后再作为"已验证"功能加回。
- 目标用户：散户，但主要通过 agent 安装技能来使用。因此重视三件事：接口与数据格式稳定、运行稳定、图片与 HTML 好看且交互感强。

### 1.3 子项目与顺序

| 顺序 | 子项目 | 说明 |
| --- | --- | --- |
| 1 | **输出契约与去预测化（本文）** | 定 v6 契约，删除预测性内容，研究线移出 main，门禁分层 |
| 2 | 可视化升级 | 基于 v6 重做 HTML 看板与 PNG |
| 3 | 交易想法复盘 | 用户提交想法 → 结构、失效位、仓位 → 只追加日志 → 到期打分 → 复盘报告 |
| 4 | 插件化分发 | 打包为 Agent Plugins 格式，接入 LangAlpha 等平台 |

契约是另外三项的上游：图表和插件都直接读它，先定契约可以避免返工。每个子项目各自走 spec → 计划 → 实现。

---

## 2. 已确认的决定

| 编号 | 问题 | 决定 |
| --- | --- | --- |
| V1 | 预测性内容的处理力度 | 反转层、L3 研判概率、L2 历史条件频率、情景混合扇形移出契约；15 日区间改名为正常波动范围；回测胜率保留为描述性统计 |
| V2 | v5 → v6 怎么过渡 | 一次性升级到 6.0.0，旧字段直接删除，不设过渡期 |
| V3 | L2 删除后风控里的 L2 守门 | 与 L2 一起删除，评级只由规则分、结构和风控否决决定 |
| V4 | research/ 怎么与主线分开 | 冻结提交打标签，research/ 移到长期分支 `research/archive`，main 只留技能 |
| V5 | v6 输出结构 | 分文件：`result.json` 是精简的稳定契约，`chart_data.json` 放渲染数据、不承诺稳定 |

---

## 3. 契约层

### 3.1 产物与稳定级别

| 产物 | 稳定级别 | 说明 |
| --- | --- | --- |
| `*_result.json` | 稳定接口 | agent、报告与插件读取的唯一结论来源 |
| `*_backtest.json` | 稳定接口 | 删除 `backtest.scenario_frequency_brier`；顶层新增 `schema_version`（与 `result.json` 一致），其余不变 |
| 命令行标准输出与退出码 | 稳定接口 | 见 3.5 |
| `*_chart_data.json` | 非稳定接口 | K 线序列、拐点、回放快照、成交分布分箱、相对强弱序列，只供渲染器读取，可随可视化自由调整 |
| `*_report.md`、`*_preview.png`、`*_dashboard.html` | 面向人 | 数字全部取自 `result.json`（图表另读 `chart_data.json`） |
| `*_judgment_pack.json` | 删除 | — |

### 3.2 `result.json` 顶层

| 字段 | 内容 | v5 来源 |
| --- | --- | --- |
| `schema_version` | `"6.0.0"` | `schema_version` |
| `meta` | 标的、市场、币种、`timezone`（新增）、`as_of`、复权、价格精度、数据源、`engine_version`（新增）、`request`（原顶层 `request`） | `meta`、`request` |
| `quality` | `status`、`warnings`、`notes`、数据源分类、占位K线剔除等 | `quality` |
| `verdict` | 一眼结论：`action`、`action_cn`、`setup_type`、`risk_veto`、`veto_reasons`、`veto_codes`、`rule_score`、`rating_cn`、`headline_cn`；明细（规则分分项、均值回归体制调整、策略信号、多空要点）标为 experimental | `meta_labeling_decision`（去掉 `high_conviction_*`、`decision_probability`）、`multi_timeframe_score`、`strategy_signals`、`debate_and_veto` |
| `market_context` | 基准代码与名称、`regime`、`regime_score`、`relative_strength_vs_index` | `market_benchmark`（`rs_series` 移入 `chart_data.json`） |
| `structure` | 本标的体制、关键价位与支撑阻力阶梯、通道/楔形、斐波那契、浪型主计数与备选计数、形态、SMC、Volume Profile 的 POC/VAH/VAL、近期缺口 | `market_regime`、`mathematical_indicators.structure_levels`、`algebraic_patterns_detected`、`smc_structures`、`volume_profile`（`bins` 移出）、`legacy_summary` 的结构部分 |
| `indicators` | 趋势、动量、波动率指标快照；量价因子标为 experimental | `mathematical_indicators` |
| `scenarios` | 2~5 个活跃情景：类型、状态、触发条件、目标位、失效条件、前提是否成立；**不含任何频率或用时统计** | `rule_scenarios`（删去 `historical_frequency`、`time_window_bars`） |
| `plan` | `long`（入场区、止损、TP1/TP2、时间屏障、移动保本、左右侧约束、仓位）、`holder_defense`、`short`（港股、美股） | `triple_barrier_plan`（删去 `high_conviction_plan`） |
| `volatility_range` | 以最新收盘价为中心的分期限分位区间与样本外覆盖率审计，表述为"正常波动范围" | `future_projection_15d`（删去 `scenario_conditional_fan`） |
| `quotable_prices` | 回复中允许引用的全部价位及其来源字段，供 `lint_response.py` 与 agent 使用 | 原 `judgment_pack` 里的价格白名单 |
| `files` | 各产物相对于 `--output-dir` 的路径 | `rendered_paths` |
| `reproducibility` | `engine_version`、`bars_hash`、`report_hash` | `reproducibility` |

删除且不迁移：`judgment_pack`、`llm_judgment`、`legacy_summary.small_model_copy_blocks`（由 `report.md` 的直出摘要替代）。

嵌套字段的逐项保留、改名与删除清单，在实施计划中逐字段列出。

### 3.3 稳定性规则（从 6.0.0 起生效，写入 `references/output-fields.md`）

1. `schema_version` 遵循语义化版本。6.x 内只新增字段，不删除、不改名、不改语义；破坏性变更只出现在主版本号。
2. 枚举可能新增取值，消费方必须容忍未知取值。
3. 每个字段在 schema 中标注 `x-stability: stable | experimental`。experimental 字段可在次版本中变动，但必须写进 CHANGELOG。
4. CHANGELOG 每个版本单列"契约变更"小节。

### 3.4 schema 与校验

- `assets/analysis.schema.json` 覆盖 `result.json` 全部字段，顶层 `additionalProperties: false`。
- 运行期依赖仍只有 Pillow：`contracts.validate_report` 用标准库实现校验子集（`type`、`required`、`enum`、`properties`、`items`、`additionalProperties`），并保留现有的 NaN/Infinity 拦截与分位单调校验（改为校验 `volatility_range`）。

### 3.5 命令行接口

- 标准输出固定为一个 JSON 信封：
  - 成功：`{"status": "ok", "schema_version", "verdict", "quality", "files"}`
  - 失败：`{"status": "error", "error": {"code", "message"}}`
- 退出码：`0` 成功；`2` `INVALID_ARGUMENT`；`3` `DATA_UNAVAILABLE`；`4` `INSUFFICIENT_HISTORY`（目前归在 `DATA_UNAVAILABLE` 里，v6 单独拆出）。
- stderr 上的 `[DATA_UNAVAILABLE]` 等前缀继续保留，兼容已按文字前缀判断的 agent。
- 现有命令行参数全部保留且语义不变，并在 `SKILL.md` 中列为稳定接口。

---

## 4. 去预测化：删除清单与影响

### 4.1 整文件删除

`scripts/base_rates.py`、`scripts/judgment.py`、`scripts/validate_judgment.py`、`scripts/score_judgments.py`、`references/llm-judgment-protocol.md`。

### 4.2 模块改动

| 模块 | 改动 |
| --- | --- |
| `risk.py` | 删除反转层（`detect_high_conviction_rebound`、`high_conviction_plan_levels`、`HC_*`、`decision_probability` 及 `meta_labeling_decision` 中的反转层字段）；删除 L2 守门（当前第 451~459 行） |
| `scenarios.py` | 保留 8 类情景的前提、触发、目标、失效判定与 2~5 个活跃情景筛选；删除历史频率与到达用时分位（二者来自同一套历史事件研究，时间约束改用计划的时间屏障）；不再导入 `base_rates` |
| `structure.py` | 改为调用不含频率的情景构建函数，三条推演不再挂 `historical_frequency` |
| `forecast.py` | 删除 `scenario_conditional_fan`；输出改名为 `volatility_range`，覆盖率审计保留 |
| `evaluation.py` | 删除 L2 的 `scenario_frequency_brier` 及对 `base_rates` 的依赖；保留计划逐日回放回测、无前视回放审计与规则分 IC |
| `analysis.py` | 按 3.2 组装 `result.json`，拆出 `chart_data.json`，不再生成研判证据包 |
| `analyze_and_forecast.py` | 写出两个 JSON；实现 3.5 的标准输出信封与退出码 |
| `contracts.py` | 按 3.4 实现全量 schema 校验；`AnalysisReport` 等 TypedDict 改为 v6 结构 |
| `lint_response.py` | 不再导入研判模块，价格识别正则并入本文件；价格白名单取 `quotable_prices`；概率规则简化为一条：除"回测胜率 + 95% 区间"外，任何概率、胜率、频率类数字一律拦截；删除反转层与"校准先验"的专门规则；保留"风控否决置顶"和"未给股数时不得出现买入股数"检查 |
| `render_report_md.py`、`render_html.py`、`render_structure_chart.py` | **最小适配**：改读 v6（图表另读 `chart_data.json`），删除 L2 频率、第 8 节研判、情景扇形、反转层一行。视觉重做留给子项目 2 |

### 4.3 文档

- `SKILL.md`：删除 Flash SOP 第 3 步与回复契约第 7 项；第五节"对外表述与概率规则"缩为一条（不输出任何概率，回测胜率附 95% 区间是唯一例外）；产物清单与字段路径改为 v6；列出稳定的命令行参数、标准输出信封与退出码。
- `references/`：`output-fields.md` 按 v6 重写并写入 3.3 的稳定性规则；`scenario-playbook.md` 删去频率与 Brier 口径；`interpretation-guide.md`、`failure-modes.md`、`math-patterns.md` 同步修改。
- `AGENTS.md`：铁律 5 改写为"不输出任何概率"；目录结构与模块职责同步；3.3 节按第 5 节分层。
- `README.md`：输出解读与架构图说明同步。
- 版本号四处统一为 `6.0.0`；CHANGELOG 新增 `[6.0.0]`，含"契约变更"小节。

### 4.4 对输出的影响（如实披露）

- 删除 L2 守门后，部分标的的评级会从"条件观察"回到"做多确认"。在录制数据的回放决策日上统计 v5 与 v6 评级不同的天数，写入 CHANGELOG。
- `examples/` 用录制行情 `--as-of 2026-10-15` 重新生成。

### 4.5 测试

`test_quant_skill.py` 中约 116 处引用已删除内容：针对已删功能的测试一并删除，其余迁移到 v6 字段路径。`test_docs_contract.py`、`test_report_and_metadata.py`、`test_data_sources.py`、`test_tools.py` 同步迁移。

---

## 5. 研究线解耦与门禁分层

### 5.1 研究线

1. 打标签：
   - `h2-frozen` → `da34cad`（`research/preregistration/preregistration_h2.json` 所在提交）
   - `main-plan-h1-v3-frozen` → `be431f2`（`research/main-long-plan-validation` 分支）
   - `v5.3.0-final` → 移出 research/ 之前 main 的最后一个提交
2. 从当时的 main 切出长期分支 `research/archive`，`research/` 原样保留。
3. main 删除 `research/`。`docs/superpowers/specs/` 下的研究设计文档留在 main 作为历史记录。
4. H2 服务器按 `H2_RUNBOOK.md` 检出冻结提交运行，不受影响。`artifacts/`（本地，不入库）不受影响。

### 5.2 合入时机

按 2026-10-21 的决定，主做多计划 H1 验收完成前，技能脚本的改动不合入 main。本子项目在功能分支上开发，H1 验收跑完后再合入。

### 5.3 门禁分层（更新 AGENTS.md 3.3 节）

| 层级 | 何时跑 | 命令 | 目标耗时 |
| --- | --- | --- | --- |
| 日常 | 每次改动后 | `pytest -n auto -m "not slow"` | < 20 秒 |
| 合并前 | 提 PR 前与 CI | 全量 `pytest -n auto` + `tests/check_days_invariance.py` | 约 1.5 分钟 |

- 标记为 `slow`：轨道拟合收敛偏差测试、三市场端到端流水线加回测、录制数据端到端回放。
- `pyproject.toml` 注册 `slow` 标记，但默认不排除：直接运行 `pytest` 仍是全量，CI 不变。

---

## 6. 验证与交付

### 6.1 交付拆分

- **PR-A：研究线移出 main + 门禁分层。** 不改技能脚本，H1 验收跑完即可合入。
- **PR-B：v6 契约与去预测化。** 在 `feat/v6-contract` 分支上分步提交（删除 → 契约重组 → 命令行信封 → 文档与示例），最后以 `6.0.0` 一次性合入，main 上不出现中间版本。

### 6.2 新增验证（只用录制的真实行情）

1. **全量 schema 校验**：CN、HK、US 录制样本生成的 `result.json` 通过全量 schema；顶层出现未声明字段即失败。
2. **体积预算**：示例 `result.json` 落盘不超过 80 KB（v5 为 340~360 KB）；超出即测试失败。
3. **命令行信封**：成功、参数越界、断网、`--as-of` 早于可用历史四种情况，分别断言标准输出的 JSON 结构与退出码 0、2、3、4。
4. **去预测守护**：`result.json` 与 `report.md` 中不得出现 `probability`、`frequency`、`high_conviction` 等键名，或"概率""胜算"等中文措辞（回测胜率附区间的句子除外）。
5. **评级变化统计**：录制数据回放决策日上 v5 与 v6 评级的差异，写入 CHANGELOG，只披露、不作门槛。
6. **原有门禁**：全量 pytest；跨窗口不变性（`test_c06_*` 改为对 v6 `result.json` 断言，展示字段除外）；`tests/check_days_invariance.py`；TA-Lib 金标准。
7. **浏览器检查**：按 AGENTS.md 第 4 节第 5 条，实际打开三个市场的 HTML，确认图层开关、相对强弱切换、时间回放滑块可用且控制台零报错；PNG 目视确认配色随市场切换。

### 6.3 完成标准

- PR-A、PR-B 合入，6.2 全部通过。
- 三个市场示例重新生成。
- `SKILL.md` 中不再出现任何概率口径。
- 记录单次分析的本机耗时，v5 与 v6 对比。

---

## 7. 不在本子项目范围

- HTML 与 PNG 的视觉重做（子项目 2）。
- 复盘日志与到期打分（子项目 3，可参考标签 `v5.3.0-final` 中 `validate_judgment.py`、`score_judgments.py` 的只追加日志与回放打分实现）。
- Agent Plugins 打包、LangAlpha 接入（子项目 4）。
- 任何新指标、新形态或新策略。
