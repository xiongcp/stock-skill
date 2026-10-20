# 2021-2026 多市场长周期回测与审计设计

- 日期：2026-10-20
- 状态：已按用户 2026-10-20 更新约束修订，进入实施
- 修订要点（用户确认）：
  - **1500 只/日为单个市场**（CN、HK、US 各自每日 1500 只），不是三市场合计。
  - 主评估**只做多**；做空计划仅作次要诊断，不进入 70% 验收。
  - 股票池来源由实施方选择并披露；本设计采用“当前上市 + 季度 PIT 流动性前 1500”。
  - 允许最多 10 个子代理并行。
  - Claude Bash 沙箱仍拒绝行情域名；取数与长回测通过用户 Terminal 面板执行（已验证可联网），产物写入 `artifacts/`。
- 技能：`kline-structure-chart` 5.3.0
- 目标周期：2021-01-04 ~ 2026-10-16（最近已收盘交易日）

## 1. 意图与为什么

用户要每天用本技能研究股票并交易，从 2021 年开始、每个交易日随机覆盖大量个股，通过长周期回测发现技能本身、Agent 执行、PNG/HTML 展示与数据描述的问题，并持续修正，最终把高置信信号的样本外方向命中率提升到 70%。

这件事的核心不是“跑一个回测数字”，而是建立一条**可复现、无前视、可审计**的研究流水线：

1. 真实历史行情 → 2. point-in-time 股票池 → 3. 每日决策快照 → 4. 真实成交/费用模拟 → 5. 到期结果与校准 → 6. 缺陷修复 → 7. 样本外验收。

## 2. 目标、非目标与反目标

### 目标
- 用真实行情完成 CN/HK/US 三市场 2021-2026 的长周期回测，逐日、逐信号留痕。
- 审计数据真实性、因果性、计算口径、PNG/HTML/Markdown 一致性、Agent 回复契约。
- 在开发集上修复 P0/P1 缺陷；在验证集上选择少量预注册规则；在一次性留出的测试集上验收。
- 验收指标：高置信子集、扣费后方向命中率 ≥70%，n≥100，95% Wilson 下限 >50%；同时报告 Brier/BSS、校准曲线、收益/回撤、基准超额与随机入场基线。
- 同时必须区分两层“命中”：
  1. **信号层预测命中率**：高置信信号出现后，在 H 根 K 线内 TP1 先于 SL 的比例（不依赖是否成交），用于评价“预测”本身。
  2. **成交层扣费后命中率**：实际成交（次日开盘在入场区内）并通过费用/滑点后的净收益 >0 的比例，用于评价“可交易性”。
  只报告成交层会把“买得进的好信号”当成模型能力，产生选择偏差；只报告信号层又忽略执行。两层都必须报告，主验收按用户选择用成交层，但信号层是强制诊断项。
- 交付完整分析报告、整改建议、可复现命令、测试与产物。

### 非目标
- 不承诺“全市场无条件 70%”，不承诺收益，不构成投资建议，不接入下单。
- 不生成、补数、估算任何行情；数据源失败就记录缺失并如实披露。
- 不为了达标而在测试集上调参；不隐藏样本量、失败试验或不利结果。
- 不引入重型运行时依赖（运行期仍只依赖 Pillow；分析脚本可用标准库）。

### 反目标（发生即视为失败）
- 任何未来数据进入决策、复权、基准、VIX 或股票池。
- 用合成/伪造/回填数据让回测“跑通”。
- 只报告胜率、不报告置信区间/样本量/成本/回撤。
- 把 `rule_score`、过滤分、Volume Profile 说成概率或真实筹码。
- 在测试集上反复试验后只报告最好的一次。

## 3. 约束

- **数据真实性**：新浪 / Yahoo / 腾讯实时接口、录制真实 fixture、用户提供的本地真实文件。当前沙箱拒绝行情域名；需用户放开网络，或由用户在系统终端运行缓存脚本。
- **运行环境**：Python ≥3.12；本次用 Python 3.13.11 + Pillow 12.3.0 重建 `.venv`；`uv` 在沙箱内会因系统配置崩溃，统一用 `.venv/bin/python`。
- **算力**：单机；1500 只/日 × 约 1400 个交易日 ≈ 210 万“个股-日”。现有逐日全量重算的实现无法在单会话完成，必须缓存、并行、分阶段。
- **股票池**：需要 point-in-time 股票池（上市、退市、ST、停牌）。当前技能没有全市场股票池与退市数据；缺失时只能使用代理池并明确披露幸存者偏差。
- **数据源限制**：新浪单次最多约 2600-3000 根；Yahoo 10 年日线 + 公司行动；腾讯部分指数约 1000 根。不同市场/标的的复权事件来源不同。
- **Agent 成本**：L3 不能对 210 万决策逐条调用 LLM；L3 只在抽样的高置信信号上评估，主回测使用确定性 L1/L2。
- **并行限制**：用户最多允许 4 个子代理并行；CPU 进程数另计，但需控制资源。

## 4. 数据层设计

### 4.1 股票池契约

`universe.csv`（用户提供或脚本生成），每行至少：

```text
symbol, market, asset_type, name, list_date, delist_date, is_st, is_etf, board, source, as_of
```

- `market`：CN/HK/US；`asset_type`：equity/etf/index。
- `list_date` / `delist_date`：用于 point-in-time 过滤；缺失时标记 `UNKNOWN`，该标的只在其有行情的区间参与，并在报告中披露。
- `is_st`：按日期变化时可用 `st_history` 文件补充；缺失时只用名称前缀判定并披露。
- 数据来源：交易所/指数公司官方名单优先；其次用户提供的可信文件；不得用“当前仍上市”列表冒充历史全市场。
- 幸存者偏差处理：
  - 首选包含退市标的的 point-in-time 池。
  - 若无法取得退市数据，基线使用“当前流动池”，报告中明确 `SURVIVORSHIP_BIAS = PRESENT`，并把结论限制为“当前存续标的上的条件表现”，不得外推全市场。
  - 可加做指数成分股历史池作为第二套基线，交叉验证。

### 4.2 行情缓存

每个标的一个 JSON 文件（或 JSONL），由缓存脚本生成：

```json
{
  "schema_version": "1.0",
  "symbol": "600519.SH",
  "market": "CN",
  "asset_type": "equity",
  "source": "SINA getKLineData ...",
  "fetched_at_utc": "2026-10-20T...",
  "first_day": "2019-01-02",
  "last_day": "2026-10-16",
  "bars": [{"day": "...", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0, "raw_close": 0}],
  "corporate_actions": [{"ex_date": "...", "kind": "dividend|split", "value": 0, "source": "..."}],
  "adjustment_status": {"applied": "total_return", "events_used": 0, "source": "YAHOO_EVENTS(...)"},
  "sha256": "..."
}
```

- 取数复用 `data_gateway` 的真实接口与校验；`HISTORICAL_FETCH_BARS=2600`，覆盖 2021 + 420 根预热。
- 缓存脚本必须支持 `--resume`、逐标的失败记录、限流退避、MANIFEST（录制时间、来源、行数、首末日、sha256）。
- 不缓存“已复权到最新”的价格作为回测输入；回测在每个决策日调用 `rescale_bars_as_of` 做 point-in-time 复权，避免未来除权因子泄漏。
- 基准：CN `000300.SH`、HK `HSI.HK`、US `SPY.US`；US VIX 用 `^VIX` 的当日收盘；取不到写 `null`，不得写死。
- 数据质量：OHLC 包络、日期单调、正价格、重复日、停牌缺口、未收盘 K 线、最近已收盘交易日缺失。所有异常写入 `quality.notes`，不得补数。

### 4.3 数据覆盖验收

- 抽样股票池中 ≥95% 的“个股-日”有有效 PIT 数据；每个市场每个年度都有覆盖。
- 基准在每个决策日存在；否则该日该市场不产生决策，并计入 `missing_benchmark_days`。
- 所有缓存文件有来源与 sha256；MANIFEST 与文件一致。
- 任何 `SYNTHETIC` 标记直接拒绝整次回测。

## 5. 回测引擎设计

### 5.1 新脚本与模块

```text
scripts/history_cache.py        # 用户在沙箱外运行：联网取数、校验、写缓存与 MANIFEST
scripts/universe.py             # 股票池加载、PIT 过滤、分层抽样
scripts/long_backtest.py        # 长周期截面回测编排、成交模拟、指标聚合
scripts/backtest_report.py      # 生成 Markdown/JSON 报告（数字全部取自结果 JSON）
```

现有 `analyze_and_forecast.py` / `evaluation.run_plan_backtest` 保留为单标的基线与回归参照，不直接改造成全市场引擎。

### 5.2 决策日流程

对每个交易日 `t`：

1. 从 PIT 股票池中按市场分层抽样（默认每市场 500 只，总计 1500 只/日；分阶段可降到 100-300）。
2. 过滤：上市不足 420 根、停牌、当日无行情、缺基准、数据质量 `UNAVAILABLE`。
3. 构造 PIT bundle：目标 bars 只到 `t`，基准只到 `t`，公司行动只到 `t`，VIX 用 `t` 收盘或 `null`。
4. 调用 `analyze_bundle(..., include_replay=False, include_forecast=False)`，提取：
   - `meta_labeling_decision.action/setup_type/veto_codes`
   - `multi_timeframe_score.rule_score` 与 regime adjustment
   - `market_benchmark.regime_score`、`relative_strength_vs_index`
   - `triple_barrier_plan`（entry_zone/stop/TP1/TP2/ratchet）
   - `rule_scenarios.scenarios[*].historical_frequency`
   - `debate_and_veto`
5. 对 `LONG_CONFIRMED` / `WATCH_LONG_SETUP`（以及 HK/US 的 short plan，作为次要评估）生成交易候选。
6. 记录决策 JSONL；不在此处读取任何 `t` 之后的数据。

### 5.3 成交与费用

- 信号在 `t` 收盘确认，`t+1` 开盘撮合；开盘价在 entry zone 内才成交。
- A 股 T+1；港股/美股按各自规则；涨停开盘不成交，跌停封死不能卖。
- 同根 K 线同时触及 TP/SL：保守按 SL 先成交。
- 时间屏障：默认 15 根；次要评估 5/20/60 根。
- 费用：`round_trip_cost_rate` + 2bps 滑点；另加可配置的买卖价差/冲击成本（按 ADV 比例，默认保守值）。
- 制度：所有涨跌停、印花税、每手股数、T+1/T+0、结算、做空限制必须按**决策日**调用 `get_market_microstructure_rules(day_str=...)`，不得用 2026 规则回测 2021-2025；逐年抽样核对生效日。
- 复权：信号/收益用 point-in-time 复权价；成交用当日 `raw_close` 还原的真实价；分红计入总收益。
- 组合层：单笔风险 1% 净值；最大同时持仓 20；单市场/单行业暴露上限；现金约束；不加杠杆。逐日 mark-to-market 生成权益曲线。
- 交易统计与组合统计分开：命中率/期望 R 用逐笔；收益/回撤/夏普用组合权益曲线。

### 5.4 指标

**结果分类（必须逐笔标注，不得只用一个 win/loss）**

```text
TP1_BEFORE_SL       目标先到（预测命中）
SL_BEFORE_TP1       失效先到（预测未命中）
TIME_BARRIER_UP     时间屏障到期且净收益 > 0
TIME_BARRIER_DOWN   时间屏障到期且净收益 <= 0
NO_FILL             信号有效但次日开盘不在入场区
LIMIT_UP_NO_FILL    涨停无法买入
LIMIT_DOWN_LOCKED   跌停封死无法卖出（顺延/持仓状态单列）
SUSPENDED/DELISTED  停牌/退市等不可交易
```

**主指标**
- **信号层**：`TP1_BEFORE_SL / (TP1_BEFORE_SL + SL_BEFORE_TP1)`，所有高置信信号都计入，附带 Wilson 95% 区间、n、有效独立样本（n × step / H），以及按日期 cluster bootstrap 的区间。
- **成交层**：实际成交中扣费后净收益 >0 的比例，附带 Wilson 95% 区间、n、fill_rate、no_fill 原因分布；同时报告“若把 NO_FILL 计为未命中”的保守口径。
- **概率校准**：为该二元事件（TP1_BEFORE_SL）建立一个新的、逐信号输出的 `decision_probability` 字段；没有这个字段，不得对外说“70% 概率”。用 Brier / BSS / ECE / 可靠性分桶检验；BSS 相对同市场同期限基准率。
- **小样本展示门槛**：报告/HTML/Agent 回复中，n<30 的胜率/命中率只能作为附注，不得出现在首段、结论或“70%”附近；点估计超过 70% 但 Wilson 下限低于 70% 时，必须同时展示下限，禁止只写点估计。示例中的 n=6、83.3% 与 n=14、71.4% 是必须拦截的反例。
- 均值/中位净 R、盈亏比、利润因子、最大回撤、夏普、年化、换手、成本拖累。
- 相对基准超额；随机入场同期限/同成本基线；买入持有基线；同日同市场信号等权组合基线。

**分层**
- 市场、年份、board、regime_score 分桶、rule_score 分桶、L2 target_freq 分桶、相对强弱分桶、波动率分桶、ST/非 ST、是否风控否决。
- 所有分层都报告 n 与置信区间；小样本不结论。

**稳健性**
- 按年/按市场滚动；bootstrap；purged walk-forward。
- 多重检验：预注册主假设数量；报告所有试验；必要时用 deflated Sharpe / Bonferroni 修正。
- 费用敏感性：0/1x/2x 成本、滑点 1/2/5 bps。
- 数据敏感性：raw vs total_return、含/不含 VIX、含/不含 ST。

### 5.5 训练 / 验证 / 测试切分（预注册）

- 开发集：2021-01-04 ~ 2024-12-31（约 4 年）
- 验证集：2025-01-02 ~ 2025-06-30（约 6 个月）
- 测试集：2025-07-01 ~ 2026-10-16（约 15 个月，一次性使用）

规则：
- 代码修复、特征/阈值探索只在开发集；验证集用于选择少量预注册候选。
- 测试集只在最终验收时运行一次；运行前把候选规则、指标、阈值写入 `preregistration.json`（含代码版本/hash）。
- 若测试集未达 70%，如实报告；不得回到测试集调参再报。允许在报告中给出“若放宽/收紧阈值会怎样”的敏感性，但必须标注为事后分析。

### 5.6 高置信子集定义

候选定义（在验证集上选择，测试集固定）：

```text
action = LONG_CONFIRMED
and debate_and_veto.risk_officer_veto = false
and market_benchmark.regime_score >= 35
and triple_barrier_plan.risk_reward_ratio >= 1.5
and rule_scenarios 对应方向 historical_frequency.target_freq >= F
and rule_score >= S
```

`F` / `S` 只在验证集上从一个小网格中选择（例如 F ∈ {0.55, 0.60, 0.65, 0.70}，S ∈ {50, 55, 60}），记录所有组合；测试集只评估选中的一个。若验证集样本不足以选择，使用先验固定值并披露。

**关键缺口**：现有技能没有“该高置信信号在 H 根内 TP1 先于 SL”的逐信号概率字段。`rule_scenarios.*.historical_frequency` 是不同几何/不同事件的定义，`L3` 概率是情景概率，都不能直接当作该二元事件的概率。因此本设计新增：

```text
decision_probability:
  p_tp1_before_sl: float          # 0~1，校准后
  method: "HISTORICAL_FREQ" | "SHRUNK_BETA" | "CALIBRATED_BINNED"
  sample_size: int
  effective_sample_size: float
  ci95: [float, float]
  calibration_version: str
  as_of: str
```

只有当 `decision_probability` 在开发/验证集上通过 Brier/BSS/ECE 检验，且测试集校准不劣化时，才允许把 70% 作为概率对外表述；否则只能报告“命中率”，不能说“70% 概率”。

## 6. Agent / L3 层

- 全量回测使用确定性 L1/L2，不逐条调用 LLM。
- 在开发集/验证集上抽取高置信信号（例如每市场每日前 10 条，累计 500-1000 条），按 `references/llm-judgment-protocol.md` 生成 L3 研判：
  - `validate_judgment.py` 校验结构、概率和、调整上限、证据编号、价格白名单。
  - 到期后 `score_judgments.py` 计算 Brier、对数损失、可靠性分桶、相对 L2 的 BSS。
- L3 只有在样本外 BSS > 0 且校准不劣化时，才允许进入高置信子集；否则仅作研究展示。
- 每日 Agent 回复审计：
  - 用 `render_report_md.py` 生成报告，Agent 只转述；`lint_response.py` 检查否决置顶、价格白名单、禁用术语、免责声明、样本量与区间。
  - 抽样 30-50 条回复做人工/子代理复核，记录错误类型与频率。
- 输出 `judgment_log.jsonl`、`judgment_scores.json`、`agent_reply_audit.json`。

## 7. 渲染与输出审计

- 抽样生成 CN/HK/US 各 10-20 个日期/标的的 PNG、HTML、JSON、Markdown。
- 自动一致性：
  - 报告/HTML 中的价格必须在 `judgment_pack` 价格白名单内。
  - JSON/PNG/HTML/Markdown 的关键字段（as_of、source、adjustment、quality、regime、rule_score、plan、q25/q50/q75）一致。
  - 无 NaN/Infinity；无未来日期；无未收盘 K 线进入计算。
  - 数据质量横幅/水印、风控否决置顶、术语合规。
- 冲突信号：报告必须展示 `judgment_pack` 中的 C1…Cm，不能只列支持当前结论的单边形态/策略；非做多动作下的多头计划必须显式解释或折叠。
- 小样本门槛：n<30 的胜率/命中率不得出现在首段/结论/70% 附近；点估计与 Wilson 下限必须同时展示。
- 浏览器实测：图层开关、相对强弱切换、时间回放滑块、导出 PNG、窄屏布局、控制台零报错。
- PNG 目视：中文字体、颜色随市场、标签重叠/裁切、告警横幅、否决原因完整显示。
- 所有修复补回归测试；P0/P1 未修复项在报告中逐条列出。

## 8. 交付物

```text
artifacts/long_backtest/
  cache/                       # 真实行情缓存（不提交；带 MANIFEST）
  universe.csv                 # point-in-time 股票池
  preregistration.json         # 测试前固定的规则/指标
  decisions.jsonl              # 逐步决策
  trades.jsonl                 # 逐笔成交
  metrics.json                 # 汇总指标
  trials.jsonl                 # 所有试验记录
  report.md                    # 完整分析与整改建议
```

- 代码改动 + 回归测试 + 门禁输出。
- 明确区分：已确认缺陷、疑似缺陷、设计缺口、无法验证项。
- 对 70% 目标的结论必须是“达成 / 未达成 + 实际值 + 置信区间 + 原因”，不得模糊。

## 9. 风险与边界

| 风险 | 影响 | 缓解 |
|------|------|------|
| 幸存者偏差 | 高估收益/命中率 | 尽可能用 PIT 池；否则明确披露并限制结论 |
| 前视泄漏 | 虚假 70% | PIT 复权、基准/VIX 截断、因果审计、未来扰动测试 |
| 数据源差异 | 价格/复权不一致 | 统一来源、MANIFEST、交叉抽样校验、敏感性分析 |
| 交易成本/流动性 | 高估可交易性 | 涨跌停/停牌/ADV/滑点模型、成本敏感性 |
| 多重检验/过拟合 | 70% 不可复现 | 预注册、验证集选参、测试集一次、记录所有试验 |
| 样本不足 | 置信区间过宽 | n≥100、Wilson 下限、有效独立样本、分层报告 |
| 算力/时间 | 无法完成 1500/日 | 分阶段扩容、缓存、并行、先小样本基线 |
| 70% 本身不现实 | 目标失败 | 如实报告上限；建议改为校准/超额/风险调整收益 |

## 10. 验收标准

1. 数据覆盖与真实性：≥95% 抽样个股-日有效；基准齐全；无合成；MANIFEST 校验通过。
2. 因果性：`run_causal_replay_audit` 通过；新增“未来扰动不改变决策”的长回测审计通过。
3. 可复现：固定种子/参数重复运行，指标一致；命令与版本写入报告。
4. 缺陷：P0 数据/前视/渲染/契约问题全部修复并有回归测试；P1 修复或明确记录。
5. 样本外：高置信子集扣费后命中率 ≥70%，n≥100，Wilson 下限 >50%；同时给出 Brier/BSS/校准/收益/回撤。
6. 若未达 70%：给出实际值、区间、失败原因、可改进方向，不以调参结果冒充样本外。

## 11. 已确认事项（2026-10-20 修订）

1. **网络**：Claude Bash 沙箱仍拒绝行情域名；统一在用户 Terminal 面板运行 `research/history_cache.py` 与长回测，命令与产物路径写入报告。
2. **股票池**：采用各市场当前上市标的；季度 PIT 排名 = 截至 rebalance_day 之前 60 个交易日的平均成交额（close×volume），取前 1500；明确披露 `SURVIVORSHIP_BIAS = PRESENT`。当前 ST 与北交所（无 Yahoo 除权事件）从主池排除并披露。
3. **1500 只/日/市场**：每日对当期 PIT 前 1500 只逐一分析；若当期合格标的少于 1500，则全部纳入并披露。
4. **主评估只做多**：`LONG_CONFIRMED` 为主，`WATCH_LONG_SETUP` 作次要分层；做空/防守计划仅作诊断。
5. **L3**：全量回测用确定性 L1/L2；L3 只对抽样的高置信信号做闭环校验，不逐条调用 LLM。
6. **币种**：各市场按本币计收益，组合层不混币；统一币种换算仅作附注。
7. **数据源能力（实测）**：新浪 A 股 2600 根（2016-01 起）；Yahoo 10y 日线+公司行动；腾讯 HK 指数 2600 根返回空（仅 HSTECH 使用，需限窗口）；VIX 用 Yahoo `^VIX` 10y 缓存并按决策日切片。
8. **性能**：`analyze_bundle(include_forecast=False)` 中位 41ms/次；每日 1500×3 市场全历史约 630 万次，需 8 进程、缓存与断点续跑。