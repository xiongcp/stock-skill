# L3 受约束的 LLM 情景研判协议（Anchored & Bounded Judgment Protocol）

本协议规定运行本技能的 Agent（Claude Code / Codex / Gemini 等）如何在确定性结构层（L1）与无前视历史条件频率层（L2）之上，完成有界、可校验、可到期打分的 L3 主观情景研判。

---

## 1. 为什么需要受约束研判

1. **锚定经验基准率**：纯规则打分或无锚点的自由发挥容易产生过度自信。L3 研判必须以 `*_judgment_pack.json` 中每个适用情景的历史条件频率（及归一化互斥基准概率 `baseline_probability_pct`）为唯一起点。
2. **证据可溯源**：支持或反对某一情景的每条理由都必须引用 `evidence_items` 中的证据编号（`E1…En`）与 `conflicting_signals` 中的冲突编号（`C1…Cm`），每条证据都有精确的 `json_path` 指向 `*_result.json`。
3. **价格零捏造**：所有关键观察价位与证伪条件中的价格数字必须严格取自 `price_whitelist`。
4. **可检验闭环**：每次通过校验的研判自动追加到 `judgment_log.jsonl`，到期后由 `scripts/score_judgments.py` 拉取真实行情计算 Brier 分数、对数损失与相对 L2 基准的技能分（`BSS`）。

---

## 2. 标准执行步骤（6 步）

1. **读取证据包**：运行 `scripts/analyze_and_forecast.py` 后，读取生成的 `<代码>_judgment_pack.json`（或 `<代码>_result.json` 中的 `judgment_pack` 字段），检查 `quality_status`、`risk_officer_veto`、`evidence_items`（`E1…En`）、`conflicting_signals`（`C1…Cm`）与 `scenario_anchors`。
2. **先报基准**：在 `scenario_judgments` 中逐个情景原样填入 L2 的 `l2_historical_frequency_pct`、`l2_display_cn` 与归一化互斥基准概率 `baseline_probability_pct`。
3. **对照多空证据与冲突**：为每个情景列出 `supporting_evidence_ids`（支持证据编号）和 `opposing_evidence_ids`（反对/约束证据编号），并审阅 `conflicting_signals` 中的矛盾信号，不得只挑顺眼信号。
4. **有界调整**：
   - 样本量 `sample_size_n >= 30` 的情景：相对 `baseline_probability_pct` 最多调整 **±15 个百分点**（`max_adjustment_pct = 15.0`）；
   - 样本量 `sample_size_n < 30` 的低样本情景：相对 `baseline_probability_pct` 最多调整 **±10 个百分点**（`max_adjustment_pct = 10.0`）；
   - 所有互斥情景的 `subjective_probability_pct` 之和必须等于 **100.0%**（允许四舍五入误差 ±0.05%）；
   - 每一处调整必须在 `rationale_cn` 中写明理由并显式引用证据编号（如 `E1`、`E6`）。
5. **明确主/备选情景与证伪条件**：
   - 指定 `primary_scenario_id`（主观研判概率最高的主情景）与 `alternate_scenario_id`（次高备选情景）；
   - 在 `key_levels_to_watch` 中列出需要观察的关键价位（只能从 `price_whitelist` 中选取）；
   - 在 `falsification_condition` 中明确写出「什么价格或收盘条件发生时说明主情景判断错了」。
6. **写入并运行校验脚本（推荐 Flash 模型加 `--auto-init` 一键预填）**：将结果保存为 `<代码>_judgment.json`（若使用 Flash 模型，可直接传入 `--auto-init` 自动生成满足全部硬约束的预填 JSON，再按需微调），执行 `scripts/validate_judgment.py` 校验、记入 `judgment_log.jsonl` 并自动更新 `<代码>_report.md` 的第 8 节：

```bash
uv run --directory kline-structure-chart python scripts/validate_judgment.py \
  --result kline-structure-chart/artifacts/<目录>/<代码>_result.json \
  --judgment kline-structure-chart/artifacts/<目录>/<代码>_judgment.json \
  --auto-init
```

---

## 3. `*_judgment.json` 结构规范

```json
{
  "schema_version": "5.3.0",
  "symbol": "600519.SH",
  "name": "贵州茅台",
  "market": "CN",
  "as_of": "2026-10-16",
  "horizon_bars": 15,
  "judgment_type": "主观研判",
  "summary_cn": "【风控已否决做多，以下仅作结构观察】主观研判（基于 L2 历史条件频率锚点与证据 E1, E2, E3 的有界调整）：...",
  "primary_scenario_id": "BREAKOUT_RETEST",
  "alternate_scenario_id": "FALSE_BREAKOUT_REVERSAL",
  "scenario_judgments": [
    {
      "scenario_id": "BREAKOUT_RETEST",
      "scenario_name": "突破回踩",
      "direction": "BULLISH",
      "sample_size_n": 12,
      "low_sample": true,
      "l2_historical_frequency_pct": 38.5,
      "l2_display_cn": "历史条件频率 38.5%（低样本，已向市场基准收缩；原始 33.3%，n = 12，95% 区间 16.3%~61.2%）",
      "baseline_probability_pct": 34.20,
      "subjective_probability_pct": 33.00,
      "adjustment_pct": -1.20,
      "max_adjustment_pct": 10.0,
      "supporting_evidence_ids": ["E2", "E4"],
      "opposing_evidence_ids": ["E1", "E10"],
      "rationale_cn": "主观研判：L2 先到目标有效历史频率为 38.5%（样本 n=12），归一化互斥基准概率为 34.20%；结合支持证据 E2, E4 与反向约束 E1, E10，将该情景主观研判概率调整 -1.20 个百分点至 33.00%（未超 ±10% 上限）。",
      "target_price": 1381.97,
      "invalidation_price": 1349.43
    }
  ],
  "conflicting_signals_reviewed": ["C1", "C2"],
  "key_levels_to_watch": [
    { "label": "三重屏障止损位", "price": 1291.44, "role": "STOP_LOSS" }
  ],
  "falsification_condition": "主观研判证伪条件：若日线收盘跌破三重屏障止损位 1291.44（见 E10），则主情景判断证伪。",
  "disclaimer_cn": "本节为锚定 L2 历史条件频率的受约束主观研判，已记入日志待到期按 Brier 分数与 BSS 检验，不构成投资建议。"
}
```

---

## 4. 校验规则与到期打分

| 校验项 | 规则 | 失败处理 |
| --- | --- | --- |
| 主观标注 | `judgment_type` 与 `summary_cn` 均必须包含「主观研判」 | `[INVALID_JUDGMENT]` 非零退出 |
| 概率归一 | 所有情景 `subjective_probability_pct` 之和必须等于 `100.0%`（±0.05%） | `[INVALID_JUDGMENT]` 非零退出 |
| 调整上限 | `n >= 30` 时 `|adjustment_pct| <= 15.0`；`n < 30` 时 `|adjustment_pct| <= 10.0` | `[INVALID_JUDGMENT]` 非零退出 |
| 证据溯源 | 引用的每个 `E*` / `C*` 编号必须在 `judgment_pack` 中存在，且每条 `rationale_cn` 至少引用一个 `E*` | `[INVALID_JUDGMENT]` 非零退出 |
| 价格白名单 | `key_levels_to_watch` 与正文中出现的每个价格数字都必须存在于 `result.json` 价格白名单内 | `[INVALID_JUDGMENT]` 非零退出 |

到期打分命令：

```bash
uv run --directory kline-structure-chart python scripts/score_judgments.py \
  --log kline-structure-chart/artifacts/judgment_log.jsonl
```

- 累计已到期研判样本 `matured_judgments >= 30` 后，报告会显示 `LLM 调整相对历史频率的技能分为 X（n = …）`。若 `BSS <= 0`，报告会明确提示「LLM 调整尚未显示增益」，并建议收紧调整上限。
