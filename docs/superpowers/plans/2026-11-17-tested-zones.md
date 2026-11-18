# 多次测试价区（动态 N 重底 / 顶、头肩、平台）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在结构层新增实验字段 `structure.tested_zones`：在主、次两个级别上由数据动态识别 N 重底 / N 重顶、头肩底 / 头肩顶与平台，给出状态、颈线、失效价、确认价和"与上一交易日相比"的变化，并在报告第 5 节、看板与 PNG 中呈现；只做描述，不参与任何评分与决策。

**Architecture:** 新模块 `scripts/tested_zones.py` 全是纯函数：`scan_zones` 在最近 420 根已收盘K线上跑两级 ZigZag，聚类、判定状态、筛选并识别头肩与平台（内部结果，价格未取整）；`compute_tested_zones` 负责取整、命名、文字摘要、盘中标注，并把截至上一根已收盘K线的重算结果拿来比较，得到 `changes`。`structure.compute_structure_layer` 只调用它一次，结果经 `analysis.py` 进入内部报告，由 `contract_v6.py` 投影到 `result.json` 的 `structure.tested_zones`；报告与看板只读 JSON，PNG 直接接收同一份结构层结果（算图分离）。回测逐日回放、历史回放快照与基准结构摘要都传 `with_tested_zones=False`，决策路径不读它。

**Tech Stack:** Python 3.12、uv、pytest（`-n auto`，慢用例标 `slow`）、Pillow（PNG）、原生 Canvas（看板）。测试只用 `tests/fixtures/recorded/` 的真实录制行情。

**Spec:** `docs/superpowers/specs/2026-11-17-tested-zones-design.md`

## 实施前须知

- 本计划里的每一段代码与补丁，都已在仓库副本上按任务顺序逐个应用过：每个任务都是"新测试先失败、实现后通过、日常门禁通过"；八个任务做完后，全量 `pytest -n auto` 为 357 passed，`check_days_invariance.py` 为 420 次 0 失败，与逐个任务应用后的结果逐文件一致。
- 补丁是 `diff -u` 格式，路径以 `a/kline-structure-chart/…`、`b/kline-structure-chart/…` 开头。在仓库根目录把补丁存成文件后执行 `git apply <文件>` 即可（也可以照着手改）。补丁按"本任务开始前"的文件内容生成，跳过或打乱任务顺序会对不上。
- 第 14 节 D4（体积）已于 2026-11-17 确认采用推荐项 a，本计划按它编写：保留 80 KB 总预算，在裁剪链末尾加两步次级别裁剪，测试守护 `tested_zones` ≤ 32 KB。若改选 b 或 c，只有 Task 5 需要改。
- Task 1 与 Task 8 各有一步需要联网（录制固定样例、重新生成 `300308.SZ` 示例）；其余步骤全部离线。

## Global Constraints

- 全部新字段是描述性实验字段（`x-stability: experimental`），不参与规则分、评级、动作、否决条件与计划价位；回测逐日回放、历史回放快照与基准结构摘要不计算它（规格第 1.3、10.4 节）。
- 只用已收盘K线计算；未收盘K线只产生 `intraday_note`，不移动任何触点、价区或失效价（AGENTS 铁律 0）。
- 不输出概率、胜率或频率；"测试次数只是计数，不代表支撑或压力更强"。
- 常量照规格第 2.3 节写定，实现时不按结果调参：价区宽度上限 0.65 ATR、破位距离 0.5 ATR、颈线最小高度 1.2 ATR、假破位窗口 3 根、近期窗口 20 根、每级别每侧最多 3 个价区、头肩两颈线点之差 ≤ 0.5 × 形态高度、触点列表上限 8 个、变化最多 12 条。
- 次级别 ZigZag 阈值 = `min(4%, max(1.5%, ceil(ATR% ÷ 0.5%) × 0.5%))`，不小于主级别阈值时跳过并写 `skipped_reason`。
- 观察到的价格（触点、价区上下沿、颈线、头肩各点、`height_abs`、`atr_abs`）用 `structure.money`；推算的价位（`break_price`、`confirm_price`、`measured_move_price`、`neckline_today_price`）用价位表取整 `round_to_tick`。
- `result.json` 落盘（`indent=2`）≤ 80 KB；`tested_zones` 单独序列化 ≤ 32 KB（D4 推荐项 a）。
- 6.x 只新增字段，不删除、不改名、不改语义；现有 `structure.support_zone` / `resistance_zone` 与 `structure.patterns` 里的双底双顶不变。
- 测试断网，只用录制的真实行情，不生成任何合成序列；只新增一份录制 `sina_daily_cases/sh000001_2026-11-17.json`，现有录制文件一律不动。
- AGENTS.md 第 2 节目录树必须列出每个 `scripts/*.py` 与 `tests/*.py`（`test_agents_md_directory_tree_matches_code` 守护）：新文件在创建它的那个任务里登记。
- 五处版本号保持 `6.0.0`；改动记在 `CHANGELOG.md` 的 `[Unreleased]`；改 `README.md` 时同步改 `README.en.md`。
- 所有命令在仓库根目录执行，用 `uv run --directory kline-structure-chart`。
- 提交信息用中文，结尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## Review Focus

1. **盘中（未收盘）K线**：价区、失效价、确认价与 N 都只按已收盘K线计算，盘中价只体现在 `intraday_note`，而且带盘中标注的报告文字也只能出现可引用价位。→ Task 3 的 `test_intraday_bar_only_annotates_and_never_moves_zones`；Task 6 的 `test_report_section_quotes_only_whitelisted_prices_across_history`（每隔一个截面带一根盘中K线）。
2. **历史太短、结构窗口里拐点不足**：输出合法的空结构（`zones` 为空、`prev_bar_day` 可空），不抛异常。→ Task 3 的 `test_short_history_outputs_valid_structure`。
3. **港股、美股的价位表**：推算出的价位必须落在价位表上，所有 `*_price` 都能被引用，`height_abs`、`atr_abs`、`first_last_atr` 不能被当成价格引用。→ Task 4 的 `test_derived_prices_follow_the_tick_table_on_hk`；Task 3 的 `test_zone_prices_are_quotable_but_heights_and_atr_are_not`。
4. **报告文字过回复校验**：日期、"N 次"、"x 个 ATR"不能被当成价格；体积裁剪去掉次级别 `summary_cn` 后的退化写法同样只用可引用价位。→ Task 6 的 `test_report_section_quotes_only_whitelisted_prices_across_history`（上证、茅台、比亚迪、英伟达、腾讯五个标的的历史截面 × 带 / 不带摘要两种变体）。
5. **阈值逐日变化与次级别跳过**：阈值变了的变化条目要带 `threshold_changed`，不能说成结构变化；次级别跳过时报告写出原因而不是空白。→ Task 3 的 `test_threshold_change_is_flagged_not_reported_as_structure_change`、`test_minor_scale_skipped_when_not_finer_than_major`；Task 6 的 `test_report_section_lists_zones_platform_and_changes`。

---

## File Structure

| 文件 | 责任 | 任务 |
| --- | --- | --- |
| `kline-structure-chart/scripts/tested_zones.py`（新增） | 次级别阈值、聚类、几何量、状态、筛选、头肩、平台（A 段）；取整、命名、摘要、盘中标注、逐日变化与入口 `compute_tested_zones`（B 段） | 2、3 |
| `kline-structure-chart/tests/test_tested_zones.py`（新增） | 固定样例 G1~G6、性质与边界、决策隔离、因果审计、报告与看板呈现 | 1~7 |
| `kline-structure-chart/tests/record_fixtures.py`、`tests/fixtures/recorded/sina_daily_cases/`、`MANIFEST.json` | 固定截止日的录制分组 `cases` | 1 |
| `kline-structure-chart/tests/test_docs_contract.py` | "头肩"已实现，从不实宣称清单移除 | 2 |
| `kline-structure-chart/scripts/structure.py` | `compute_structure_layer(with_tested_zones=True)`，结果键 `tested_zones` | 4 |
| `kline-structure-chart/scripts/market_structure.py`、`scripts/analysis.py`、`scripts/evaluation.py` | 基准摘要与回放不计算；`analyze_bundle(include_tested_zones=True)`；因果审计新增一项 | 4 |
| `kline-structure-chart/scripts/render_structure_chart.py` | v4.1 取数下限 421 根与结果键（Task 4）；PNG 价区横带、编号与标签（Task 7） | 4、7 |
| `kline-structure-chart/tests/check_days_invariance.py` | `STRUCT_KEYS` 加 `tested_zones` | 4 |
| `kline-structure-chart/scripts/contract_v6.py`、`assets/analysis.schema.json`、`tests/test_v6_contract.py`、`tests/fixtures/contract/result_v6_keypaths_*.json` | 投影、schema、四步裁剪链、键路径快照 | 5 |
| `kline-structure-chart/scripts/render_report_md.py`、`scripts/lint_response.py`、`SKILL.md` | 报告第 5、6 节；禁用说法；回复契约第 5 条 | 6 |
| `kline-structure-chart/scripts/render_html.py` | 看板图层"多次测试价区"与主次级别切换 | 7 |
| `kline-structure-chart/references/math-patterns.md`、`output-fields.md`、`interpretation-guide.md` | 第 9 节公式；字段字典与体积预算；第 14 节解读 | 5、8 |
| `AGENTS.md`、`CHANGELOG.md`、`README.md`、`README.en.md`、`examples/` | 目录树与录制分组；版本记录；输出文件说明；示例重新生成 | 1、2、8 |

---

### Task 1: 固定截止日的真实行情录制

**Files:**
- Modify: `kline-structure-chart/tests/record_fixtures.py`（新增 `CASES` 与 `cases` 分组）
- Create: `kline-structure-chart/tests/fixtures/recorded/sina_daily_cases/sh000001_2026-11-17.json`（由录制脚本生成）
- Modify: `kline-structure-chart/tests/fixtures/recorded/MANIFEST.json`（由录制脚本追加一条）
- Create: `kline-structure-chart/tests/test_tested_zones.py`
- Modify: `AGENTS.md`（测试目录树、第 3.4 节分组列表）

**Interfaces:**
- Produces: 录制文件 `sina_daily_cases/sh000001_2026-11-17.json`（上证指数新浪不复权日线，最后一根是 2026-11-17）；测试文件里的常量 `CASE`、`SH`、`MOUTAI`、`BYD` 与辅助函数 `_bars(rel: str, upto: str = "9999-12-31") -> list[dict]`（只保留 `day/open/high/low/close`），后续任务的测试都用它们。

- [ ] **Step 1: 写测试**

新建 `kline-structure-chart/tests/test_tested_zones.py`：

```python
"""多次测试价区（tested_zones）：固定样例、性质与边界（只用录制的真实行情，测试断网）。"""
from __future__ import annotations

import hashlib
import json

import pytest

from conftest import RECORDED_DIR, load_recorded

CASE = "sina_daily_cases/sh000001_2026-11-17.json"
SH = "sina_daily/sh000001.json"
MOUTAI = "sina_daily/sh600519.json"
BYD = "sina_daily/sz002594.json"


def _bars(rel: str, upto: str = "9999-12-31") -> list[dict]:
    """录制行情截至 upto（含），只保留结构层用到的字段。"""
    return [{k: r[k] for k in ("day", "open", "high", "low", "close")} for r in load_recorded(rel) if r["day"] <= upto]


# ---------------------------------------------------------------------------
# 任务 1：固定截止日的真实行情录制
# ---------------------------------------------------------------------------


def test_case_fixture_is_recorded_real_data() -> None:
    manifest = json.loads((RECORDED_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    entry = manifest["files"][CASE]
    text = (RECORDED_DIR / CASE).read_text(encoding="utf-8")
    assert entry["source"].startswith("SINA getKLineData symbol=sh000001")
    assert entry["last_day"] == "2026-11-17" and entry["rows"] >= 500
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == entry["sha256"]
    lows = {b["day"]: b["low"] for b in json.loads(text)[-3:]}
    assert lows == {"2026-11-13": 3623.841, "2026-11-16": 3586.568, "2026-11-17": 3565.59}
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: FAIL（`KeyError: 'sina_daily_cases/sh000001_2026-11-17.json'`，MANIFEST 里还没有这一条）。

- [ ] **Step 3: 录制脚本新增 `cases` 分组**

```diff
--- a/kline-structure-chart/tests/record_fixtures.py
+++ b/kline-structure-chart/tests/record_fixtures.py
@@ -3,10 +3,10 @@
 
 用法：
   uv run python tests/record_fixtures.py                    # 全量重录，写入 tests/fixtures/recorded/
-  uv run python tests/record_fixtures.py --only quotes      # 只录指定分组（sina / yahoo / tencent / events / quotes / search，可逗号分隔）
+  uv run python tests/record_fixtures.py --only quotes      # 只录指定分组（sina / yahoo / tencent / events / quotes / search / cases，可逗号分隔）
 
 录制内容：新浪不复权日线、Yahoo 日线、腾讯指数日线、Yahoo 除权除息事件、腾讯行情快照中的证券简称与港股每手股数，
-以及腾讯证券搜索（名称转代码）的原始响应。
+以及腾讯证券搜索（名称转代码）的原始响应；cases 组录制截止到固定交易日的新浪日线，供多次测试价区的固定样例使用。
 只保存已收盘K线（按交易所时区与收盘时间判定），并在 MANIFEST.json 记录每个文件的录制时间、来源与 sha256。
 """
 from __future__ import annotations
@@ -68,7 +68,9 @@
     "brk": "brk",
     "not_found": "zzqqxxnotexist",
 }
-GROUPS = ("sina", "yahoo", "tencent", "events", "quotes", "search")
+# 固定截止日的新浪日线（多次测试价区的固定样例）：文件名 -> (代码, 市场, 截止日)。截止日之后的K线不写入（以后重录时起始日会随 datalen 后移，截止日附近的K线不变）
+CASES = {"sh000001_2026-11-17": ("sh000001", "CN", "2026-11-17")}
+GROUPS = ("sina", "yahoo", "tencent", "events", "quotes", "search", "cases")
 
 
 def _completed(bars: list[dict], market: str, now: datetime) -> list[dict]:
@@ -137,6 +139,13 @@
             payload = {"query": query, "raw": fetch_tencent_search_raw(query)}
             _write(f"tencent_search/{fname}.json", payload, manifest, f"TENCENT smartbox.gtimg.cn/s3 q={query}（原始响应）", now)
             written.append(f"tencent_search/{fname}.json")
+    if "cases" in groups:
+        for fname, (code, market, last_day) in CASES.items():
+            bars = [b for b in _completed(fetch_sina_daily_bars(code, datalen=1023), market, now) if b["day"] <= last_day]
+            if not bars or bars[-1]["day"] != last_day:
+                raise SystemExit(f"{code}: 数据源里没有 {last_day} 的已收盘K线，无法录制 {fname}")
+            _write(f"sina_daily_cases/{fname}.json", bars, manifest, f"SINA getKLineData symbol={code} scale=240 (不复权)，截至 {last_day}", now)
+            written.append(f"sina_daily_cases/{fname}.json")
     manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
     print(json.dumps({k: (manifest["files"][k]["rows"], manifest["files"][k]["last_day"]) for k in written}, ensure_ascii=False))
 
```

- [ ] **Step 4: 联网录制**

Run: `uv run --directory kline-structure-chart python tests/record_fixtures.py --only cases`
Expected: 打印 `{"sina_daily_cases/sh000001_2026-11-17.json": [行数, "2026-11-17"]}`。2026-11-18 录制时行数为 1023、sha256 为 `0feec50897f15449d3d7b4fd58eedce50ee16f41cbac27f02f98dd9fa248f97c`；以后再录，新浪只返回最近 1023 根，截止日之后的K线被去掉，行数会变少、起始日后移、sha256 不同，这是正常的，测试只要求最后一根是 2026-11-17 且不少于 500 行。若脚本以"数据源里没有 2026-11-17 的已收盘K线"退出，如实报告并停下，不得用其他来源补数。

确认 MANIFEST 只多了一条：`git diff kline-structure-chart/tests/fixtures/recorded/MANIFEST.json` 只有新增的 `sina_daily_cases/…` 条目，其他条目一个字节都没变。

- [ ] **Step 5: 更新 AGENTS.md**

`AGENTS.md`：在下面这一行之后

```text
    ├── test_market_structure.py      # 大盘结构与周期层：阶段规则、对齐关系、决策不变、契约与呈现（录制真实行情）
```

插入

```markdown
    ├── test_tested_zones.py          # 多次测试价区：固定样例、性质与边界、决策不变、报告与看板呈现（录制真实行情）
```

`AGENTS.md`：把

```text
# 只补录某一类（sina / yahoo / tencent / events / quotes / search），其余文件与 MANIFEST 条目保持不变
```

改为

```markdown
# 只补录某一类（sina / yahoo / tencent / events / quotes / search / cases），其余文件与 MANIFEST 条目保持不变
```

- [ ] **Step 6: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py tests/test_docs_contract.py -q`
Expected: PASS。

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过（参考：277 passed）。

- [ ] **Step 7: 提交**

```bash
git add kline-structure-chart/tests/record_fixtures.py \
  kline-structure-chart/tests/fixtures/recorded/MANIFEST.json \
  kline-structure-chart/tests/fixtures/recorded/sina_daily_cases/sh000001_2026-11-17.json \
  kline-structure-chart/tests/test_tested_zones.py \
  AGENTS.md
git commit -m "$(cat <<'EOF'
test(tested-zones): 录制上证指数截至 2026-11-17 的固定样例

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 聚类、几何量、状态、筛选、头肩与平台（`scan_zones`）

**Files:**
- Create: `kline-structure-chart/scripts/tested_zones.py`（A 段）
- Modify: `kline-structure-chart/tests/test_tested_zones.py`（追加任务 2 的测试）
- Modify: `kline-structure-chart/tests/test_docs_contract.py`（`OVERCLAIMS` 去掉"头肩"）
- Modify: `AGENTS.md`（脚本目录树）

**Interfaces:**
- Consumes: `structure.HISTORY_BARS`（420）、`STRUCT_DAYS`（120）、`atr_pct(hist, n=20) -> float`、`money(v) -> float`、`swing_pivots`、`zigzag(bars, thr) -> list[dict]`（每个拐点有 `idx/day/kind/price/confirmed/confirmed_at_day`）。
- Produces:
  - 常量 `W_TOL_ATR`、`BREAK_ATR`、`NECK_MIN_ATR`、`FALSE_BREAK_BARS`、`RECENT_BARS`、`MAX_ZONES_PER_SIDE`、`HS_NECK_SLOPE_MAX`、`MAX_TOUCHES`、`MAX_CHANGES`、`SIDE = {"L": "SUPPORT", "H": "RESISTANCE"}`。
  - `minor_threshold(atr_frac: float, major_thr: float) -> float | None`：次级别阈值，不小于主级别时返回 `None`。
  - `scan_zones(hist: list[dict]) -> dict`：未取整的内部结果，`{"day", "window_start_day", "atr", "scales": {"MAJOR"|"MINOR": {"thr", "skipped", "zones": {zone_id: 价区}, "rejected": {zone_id: 原因}, "selected": [(zone_id, [原因])], "hs": [...], "platform"}}}`（`atr` 为绝对值），Task 3 的输出层与变化比较都基于它。
  - `confirmed_geometry(bars: list[dict], thr: float, atr: float, window_start_day: str, as_of_day: str) -> dict[str, tuple]`：只用截至 `as_of_day` 已确认的拐点聚类，返回 `{zone_id: (触点日期, 下沿, 上沿, 颈线)}`，Task 4 的因果审计用它。

`structure.py` 已有 1000 多行，价区按"一个文件一个职责"单独成文件；它只从 `structure` 引入，`structure` 在函数内延迟引入它（Task 4），不会循环引入。A 段已经引入 `money`，B 段才用到它。

- [ ] **Step 1: 写测试**

在 `kline-structure-chart/tests/test_tested_zones.py` 末尾追加（与上一段之间空两行）：

```python
# ---------------------------------------------------------------------------
# 任务 2：聚类、几何量、状态、筛选、头肩与平台（scan_zones 的内部结果，价格未取整）
# ---------------------------------------------------------------------------


def _scan(rel: str, upto: str) -> tuple[dict, list[dict]]:
    from structure import HISTORY_BARS
    from tested_zones import scan_zones

    hist = _bars(rel, upto)[-HISTORY_BARS:]
    return scan_zones(hist), hist


def _touches(z: dict) -> list[tuple[str, float]]:
    return [(t["day"], t["price"]) for t in z["touches"]]


def test_minor_threshold_rounds_up_to_half_percent_and_skips_when_not_finer() -> None:
    from tested_zones import minor_threshold

    assert minor_threshold(0.0101, 0.03) == pytest.approx(0.015)  # 下限 1.5%
    assert minor_threshold(0.0151, 0.03) == pytest.approx(0.02)  # 1.51% 向上取整到 2.0%
    assert minor_threshold(0.0200, 0.04) == pytest.approx(0.02)  # 恰好 2.0% 不再进位
    assert minor_threshold(0.0420, 0.084) == pytest.approx(0.04)  # 上限 4%
    assert minor_threshold(0.0220, 0.02475) is None  # 2.5% 不小于主级别放宽后的 2.475%：跳过本级别


def test_g2_sh_2026_10_15_four_touch_support_broken_five_touch_resistance_crossed() -> None:
    scan, hist = _scan(SH, "2026-10-15")
    minor, major = scan["scales"]["MINOR"], scan["scales"]["MAJOR"]
    sup = minor["zones"]["MINOR-SUPPORT-2026-07-20"]
    assert _touches(sup) == [("2026-07-20", 3741.11), ("2026-07-30", 3767.5), ("2026-10-09", 3754.14), ("2026-10-13", 3741.53)]
    assert sup["state"] == "BROKEN" and hist[sup["since_idx"]]["day"] == "2026-10-15"
    res = minor["zones"]["MINOR-RESISTANCE-2026-07-15"]
    assert len(res["touches"]) == 5 and res["touches"][-1]["day"] == "2026-09-22"
    assert res["state"] == "NECKLINE_CROSSED" and hist[res["since_idx"]]["day"] == "2026-10-15"
    assert res["neck"] == pytest.approx(3741.11, abs=0.006)
    top = major["zones"]["MAJOR-RESISTANCE-2026-08-18"]
    assert _touches(top) == [("2026-08-18", 3994.18), ("2026-09-01", 3995.18), ("2026-09-22", 3967.68)]
    assert top["state"] == "NECKLINE_CROSSED" and hist[top["since_idx"]]["day"] == "2026-09-28"
    assert {"MINOR-SUPPORT-2026-07-20", "MINOR-RESISTANCE-2026-07-15"} <= {zid for zid, _ in minor["selected"]}


def test_g3_moutai_double_bottom_reclaimed_too_late_is_expired() -> None:
    scan, _ = _scan(MOUTAI, "2026-10-15")
    major = scan["scales"]["MAJOR"]
    assert major["rejected"]["MAJOR-SUPPORT-2026-07-27"] == "EXPIRED"  # 07-27 1279.58 / 08-24 1270.33
    assert "MAJOR-SUPPORT-2026-07-27" not in major["zones"]
    top = major["zones"]["MAJOR-RESISTANCE-2026-08-03"]
    assert _touches(top) == [("2026-08-03", 1363.35), ("2026-08-10", 1359.97)] and top["state"] == "HOLDING"
    assert [zid for zid, _ in major["selected"]] == ["MAJOR-RESISTANCE-2026-08-03"]


def test_g4_byd_platform_and_old_four_touch_resistances_not_selected() -> None:
    scan, hist = _scan(BYD, "2026-10-15")
    pf = scan["scales"]["MAJOR"]["platform"]
    assert pf["support"]["zone_id"] == "MAJOR-SUPPORT-2026-09-11"
    assert [t["day"] for t in pf["support"]["touches"]] == ["2026-09-11", "2026-10-08"]
    assert pf["resistance"]["zone_id"] == "MAJOR-RESISTANCE-2026-09-22"
    assert [t["day"] for t in pf["resistance"]["touches"]] == ["2026-09-22", "2026-10-12"]
    assert (pf["support"]["low"], pf["resistance"]["high"]) == (81.51, 87.2) and round(pf["gap_atr"], 2) == 1.91
    minor = scan["scales"]["MINOR"]
    selected = {zid for zid, _ in minor["selected"]}
    for zid, first, last, since in (
        ("MINOR-RESISTANCE-2026-05-21", "2026-05-21", "2026-06-02", "2026-08-04"),
        ("MINOR-RESISTANCE-2026-07-17", "2026-07-17", "2026-08-03", "2026-08-31"),
    ):
        z = minor["zones"][zid]
        assert len(z["touches"]) == 4 and (z["touches"][0]["day"], z["touches"][-1]["day"]) == (first, last)
        assert z["state"] == "NECKLINE_CROSSED" and hist[z["since_idx"]]["day"] == since
        assert zid not in selected


def test_g5_minor_head_and_shoulders_bottom() -> None:
    scan, _ = _scan(SH, "2025-06-05")
    hs = scan["scales"]["MINOR"]["hs"]
    (b,) = [h for h in hs if h["kind"] == "L"]
    assert [(p["day"], p["price"]) for p in (b["s1"], b["head"], b["s2"])] == [
        ("2025-03-24", 3340.24), ("2025-04-07", 3040.69), ("2025-05-27", 3332.49)
    ]
    assert b["s2"]["confirmed"] and b["state"] == "FORMING"
    assert (b["n1"]["day"], b["n2"]["day"]) == ("2025-03-27", "2025-05-14")
    assert (b["n1"]["price"], b["n2"]["price"]) == pytest.approx((3394.04, 3417.31), abs=0.006)
    assert not [h for h in hs if h["kind"] == "H"]
    assert scan["scales"]["MAJOR"]["hs"] == []


def test_g5_steep_neck_top_is_rejected_only_by_the_slope_constraint(monkeypatch: pytest.MonkeyPatch) -> None:
    """03-27 / 05-14 / 06-05 的"头肩顶"跨越 4 月暴跌、颈线陡斜：放开斜率约束才会出现，说明上一条测试不是空转。"""
    import tested_zones

    monkeypatch.setattr(tested_zones, "HS_NECK_SLOPE_MAX", 100.0)
    scan, _ = _scan(SH, "2025-06-05")
    tops = [h for h in scan["scales"]["MINOR"]["hs"] if h["kind"] == "H"]
    assert [(h["s1"]["day"], h["head"]["day"], h["s2"]["day"]) for h in tops] == [("2025-03-27", "2025-05-14", "2025-06-05")]


def test_g6_major_head_and_shoulders_top() -> None:
    scan, _ = _scan(SH, "2026-06-26")
    (t,) = [h for h in scan["scales"]["MAJOR"]["hs"] if h["kind"] == "H"]
    assert [(p["day"], p["price"]) for p in (t["s1"], t["head"], t["s2"])] == [
        ("2026-03-03", 4197.23), ("2026-05-14", 4258.86), ("2026-06-23", 4175.35)
    ]
    assert (t["n1"]["day"], t["n2"]["day"]) == ("2026-03-23", "2026-06-08")
    assert (t["n1"]["price"], t["n2"]["price"]) == pytest.approx((3794.68, 3927.85), abs=0.006)
    assert t["state"] == "FORMING"


def test_zone_with_shallow_neck_is_not_output() -> None:
    scan, _ = _scan(SH, "2025-04-29")
    minor = scan["scales"]["MINOR"]
    assert minor["rejected"]["MINOR-SUPPORT-2025-01-24"] == "HEIGHT"
    assert "MINOR-SUPPORT-2025-01-24" not in minor["zones"]


def test_output_zones_obey_count_height_and_unconfirmed_rules_across_history() -> None:
    from structure import HISTORY_BARS
    from tested_zones import NECK_MIN_ATR, scan_zones

    bars = _bars(SH)
    seen = 0
    for cut in range(450, len(bars) + 1, 20):
        scan = scan_zones(bars[:cut][-HISTORY_BARS:])
        for sc in scan["scales"].values():
            for z in sc["zones"].values():
                seen += 1
                assert len(z["touches"]) >= 2 and z["touches"][0]["confirmed"]
                assert z["height"] >= NECK_MIN_ATR * scan["atr"] - 1e-9
                assert [t for t in z["touches"] if not t["confirmed"]] in ([], [z["touches"][-1]])  # 未确认触点至多一个且在最后
                assert all(t["confirmed_at_day"] <= scan["day"] for t in z["touches"] if t["confirmed"])
                assert z["state"] != "EXPIRED"
    assert seen > 100


def test_confirmed_geometry_ignores_bars_after_the_cut() -> None:
    """同一阈值与 ATR 下，截至截面日已确认的触点与价区几何量在追加放大 5 倍的未来K线后不变。"""
    from structure import HISTORY_BARS, STRUCT_DAYS, atr_pct
    from tested_zones import confirmed_geometry

    bars = _bars(SH)
    nonempty = 0
    for cut in range(500, len(bars) - 5, 37):
        sub = bars[:cut][-HISTORY_BARS:]
        ext = sub + [{**b, **{k: b[k] * 5 for k in ("open", "high", "low", "close")}} for b in bars[cut : cut + 25]]
        atr = atr_pct(sub) * sub[-1]["close"]
        for thr in (0.03, 0.015):
            geo = confirmed_geometry(sub, thr, atr, sub[-STRUCT_DAYS]["day"], sub[-1]["day"])
            assert geo == confirmed_geometry(ext, thr, atr, sub[-STRUCT_DAYS]["day"], sub[-1]["day"]), sub[-1]["day"]
            nonempty += bool(geo)
    assert nonempty > 10
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: FAIL（任务 2 的 10 个用例都报 `ModuleNotFoundError: No module named 'tested_zones'`；任务 1 的用例仍通过）。

- [ ] **Step 3: 新建 `scripts/tested_zones.py`（A 段）**

```python
"""多次测试价区：动态识别 N 重底 / N 重顶、头肩底 / 头肩顶与平台，并与上一根已收盘K线比较变化。

描述性实验字段（`structure.tested_zones`），不参与规则分、评级、动作、否决与计划价位。
只读已收盘K线；盘中K线只用于 `intraday_note` 标注（铁律 0）。
规则与常量见 `references/math-patterns.md` 第 9 节。
"""
from __future__ import annotations

import math
from typing import Any, Callable

from structure import HISTORY_BARS, STRUCT_DAYS, atr_pct, money, swing_pivots, zigzag

W_TOL_ATR = 0.65          # 价区宽度上限（沿用双底"两低点差 ≤ 0.65×ATR"）
BREAK_ATR = 0.5           # 破位距离（沿用情景库与 MIN_TRIGGER_ATR）
NECK_MIN_ATR = 1.2        # 颈线最小高度（沿用双底"颈线 − 底 ≥ 1.2×ATR"）
FALSE_BREAK_BARS = 3      # 破位后 3 根内收回才算假破位
RECENT_BARS = 20          # 近期窗口（沿用可引用价位"最近 20 根K线"）
MAX_ZONES_PER_SIDE = 3    # 每级别每侧最多输出的价区数
HS_NECK_SLOPE_MAX = 0.5   # 头肩两颈线点之差 ≤ 0.5 × 形态高度
MAX_TOUCHES = 8           # 触点列表上限：超过时列第 1 个与最后 7 个
MAX_CHANGES = 12          # 变化最多输出条数（主级别优先）
MINOR_STEP, MINOR_MIN, MINOR_MAX = 0.005, 0.015, 0.04

SIDE = {"L": "SUPPORT", "H": "RESISTANCE"}


def minor_threshold(atr_frac: float, major_thr: float) -> float | None:
    """次级别 ZigZag 阈值：ATR% 按 0.5% 向上取整，夹在 1.5%~4%；不小于主级别阈值时返回 None（跳过本级别）。"""
    thr = min(MINOR_MAX, max(MINOR_MIN, math.ceil(atr_frac / MINOR_STEP - 1e-9) * MINOR_STEP))
    return None if thr >= major_thr - 1e-12 else thr


def _recent(idx: int | None, n: int) -> bool:
    return idx is not None and idx >= n - RECENT_BARS


def _clusters(bars: list[dict], piv: list[dict], kind: str, atr: float) -> list[list[dict]]:
    """按时间顺序把同类拐点聚成价区（规格 3.1）：宽度 ≤ W_TOL，且两触点之间没有收盘越过已确认边缘 BRK 以上。"""
    sgn = 1 if kind == "L" else -1
    groups: list[list[dict]] = []
    for p in (q for q in piv if q["kind"] == kind):
        best = None
        for g in groups:
            prices = [t["price"] for t in g] + [p["price"]]
            width = max(prices) - min(prices)
            if width > W_TOL_ATR * atr:
                continue
            # g 里都是已确认触点：未确认拐点只可能是 ZigZag 的最后一个拐点
            edge = min(t["price"] for t in g) if kind == "L" else max(t["price"] for t in g)
            if any(sgn * (b["close"] - edge) < -BREAK_ATR * atr for b in bars[g[-1]["idx"] + 1 : p["idx"]]):
                continue
            key = (width, -g[-1]["idx"])
            if best is None or key < best[0]:
                best = (key, g)
        if best:
            best[1].append(p)
        else:
            groups.append([p])
    return groups


def _zone_state(bars: list[dict], z: dict) -> tuple[str, int | None]:
    """规格 3.6：从最后一个已确认触点的下一根起扫描收盘价，返回 (状态, 进入该状态的K线下标)。"""
    n, kind = len(bars), z["kind"]
    beyond = (lambda c: c < z["break"]) if kind == "L" else (lambda c: c > z["break"])
    back = (lambda c: c >= z["low"]) if kind == "L" else (lambda c: c <= z["high"])
    k, last_fb = z["last_conf_idx"] + 1, None
    while k < n:
        if beyond(bars[k]["close"]):
            r = next((j for j in range(k + 1, n) if back(bars[j]["close"])), None)
            if r is None:
                return "BROKEN", k
            if r - k > FALSE_BREAK_BARS:
                return "EXPIRED", r
            last_fb, k = r, r + 1
            continue
        k += 1
    if _recent(last_fb, n):
        return "FALSE_BREAK", last_fb
    crossed = (lambda c: c > z["neck"]) if kind == "L" else (lambda c: c < z["neck"])
    if crossed(bars[-1]["close"]):
        j = n - 1
        while j > z["last_idx"] and crossed(bars[j - 1]["close"]):
            j -= 1
        return "NECKLINE_CROSSED", j
    return ("TESTING" if z["unc"] else "HOLDING"), None


def _build_zone(bars: list[dict], touches: list[dict], kind: str, atr: float, thr: float) -> tuple[dict | None, str | None]:
    """由一组触点算价区几何量与状态（规格 3.2~3.6）。不满足输出条件时返回 (None, 原因)。"""
    if len(touches) < 2:
        return None, "TOUCH_REPLACED"
    if not touches[0]["confirmed"]:
        return None, "PIVOT_GONE"
    conf = [t for t in touches if t["confirmed"]]
    unc = touches[-1] if not touches[-1]["confirmed"] else None
    low, high = min(t["price"] for t in conf), max(t["price"] for t in conf)
    mid = bars[touches[0]["idx"] + 1 : touches[-1]["idx"]]
    neck = max(b["high"] for b in mid) if kind == "L" else min(b["low"] for b in mid)
    height = neck - low if kind == "L" else high - neck
    if height < NECK_MIN_ATR * atr:
        return None, "HEIGHT"
    seq = [t["price"] for t in touches]
    z = {
        "kind": kind,
        "touches": touches,
        "unc": unc,
        "low": low,
        "high": high,
        "neck": neck,
        "height": height,
        "measured": neck + height if kind == "L" else neck - height,
        "break": low - BREAK_ATR * atr if kind == "L" else high + BREAK_ATR * atr,
        "confirm": (unc["price"] * (1 + thr) if kind == "L" else unc["price"] * (1 - thr)) if unc else None,
        "last_idx": touches[-1]["idx"],
        "last_conf_idx": conf[-1]["idx"],
        "sequence": "RISING" if all(b > a for a, b in zip(seq, seq[1:]))
        else "FALLING" if all(b < a for a, b in zip(seq, seq[1:])) else "MIXED",
        "first_last_atr": (seq[-1] - seq[0]) / atr,
    }
    z["state"], z["since_idx"] = _zone_state(bars, z)
    if z["state"] == "EXPIRED":
        return None, "EXPIRED"
    if z["state"] == "BROKEN" and not _recent(z["since_idx"], len(bars)):
        return None, "BROKEN_STALE"
    return z, None


def _select(zones: list[dict], bars: list[dict]) -> list[tuple[dict, list[str]]]:
    """规格 3.7：每侧取离收盘最近的未破位价区，加上最近 20 根内有触点或状态变化的价区，最多 3 个。"""
    n, close = len(bars), bars[-1]["close"]
    out: list[tuple[dict, list[str]]] = []
    for kind in ("L", "H"):
        side = [z for z in zones if z["kind"] == kind]
        active = [z for z in side if z["state"] != "BROKEN"]
        nearest = min(active, key=lambda z: max(0.0, z["low"] - close, close - z["high"]), default=None)
        recent = sorted((z for z in side if _recent(z["last_idx"], n) or _recent(z["since_idx"], n)), key=lambda z: -z["last_idx"])
        recent_ids = {id(z) for z in recent}
        picked: list[tuple[dict, list[str]]] = []
        for z in ([nearest] if nearest else []) + recent:
            reasons = (["NEAREST_ACTIVE"] if z is nearest else []) + (["RECENT"] if id(z) in recent_ids else [])
            if all(z is not p for p, _ in picked):
                picked.append((z, reasons))
        out += picked[:MAX_ZONES_PER_SIDE]
    return out


def _platform(selected: list[dict], atr: float) -> dict | None:
    """规格 5：同一级别已筛选的支撑价区与压力价区，时间重叠、间距 ≥ 1.2 ATR；取两者末触点中较早者最近的一对。"""
    ok = ("TESTING", "HOLDING", "FALSE_BREAK")
    best = None
    for s in (z for z in selected if z["kind"] == "L" and z["state"] in ok):
        for r in (z for z in selected if z["kind"] == "H" and z["state"] in ok):
            if max(s["touches"][0]["idx"], r["touches"][0]["idx"]) > min(s["last_idx"], r["last_idx"]):
                continue
            if r["low"] - s["high"] < NECK_MIN_ATR * atr:
                continue
            key = min(s["last_idx"], r["last_idx"])
            if best is None or key > best[0]:
                best = (key, s, r)
    return None if best is None else {"support": best[1], "resistance": best[2], "gap_atr": (best[2]["low"] - best[1]["high"]) / atr}


def _head_shoulders(bars: list[dict], piv: list[dict], kind: str, atr: float) -> dict | None:
    """规格 4：结构窗口内最后三个同类拐点 S1、H、S2。"""
    same = [p for p in piv if p["kind"] == kind]
    if len(same) < 3:
        return None
    s1, head, s2 = same[-3:]
    if kind == "L":
        head_ok = head["price"] < min(s1["price"], s2["price"]) - W_TOL_ATR * atr
    else:
        head_ok = head["price"] > max(s1["price"], s2["price"]) + W_TOL_ATR * atr
    if not head_ok or abs(s1["price"] - s2["price"]) > W_TOL_ATR * atr:
        return None
    key = "high" if kind == "L" else "low"
    pick = max if kind == "L" else min
    a = pick(range(s1["idx"] + 1, head["idx"]), key=lambda i: bars[i][key])
    b = pick(range(head["idx"] + 1, s2["idx"]), key=lambda i: bars[i][key])

    def line(x: float) -> float:
        return bars[a][key] + (bars[b][key] - bars[a][key]) * (x - a) / (b - a)

    height = abs(line(head["idx"]) - head["price"])
    if height < NECK_MIN_ATR * atr or abs(bars[b][key] - bars[a][key]) > HS_NECK_SLOPE_MAX * height:
        return None
    n, close = len(bars), bars[-1]["close"]
    neck_today = line(n - 1)
    after = bars[s2["idx"] + 1 :]
    if kind == "L":
        failed = any(x["close"] < head["price"] - BREAK_ATR * atr for x in after)
        crossed = close > neck_today
    else:
        failed = any(x["close"] > head["price"] + BREAK_ATR * atr for x in after)
        crossed = close < neck_today
    return {
        "kind": kind, "s1": s1, "head": head, "s2": s2,
        "n1": {"day": bars[a]["day"], "price": bars[a][key]}, "n2": {"day": bars[b]["day"], "price": bars[b][key]},
        "neck_today": neck_today, "height": height,
        "measured": neck_today + height if kind == "L" else neck_today - height,
        "state": "FAILED" if failed else "NECKLINE_CROSSED" if crossed else "FORMING",
    }


def scan_zones(hist: list[dict]) -> dict:
    """截至 hist 最后一根已收盘K线，算两个级别的全部价区、筛选结果、头肩与平台。

    返回未取整的内部结果（compute_tested_zones 与测试使用）：scales[级别] = {thr, skipped, zones{zone_id: 价区},
    rejected{zone_id: 不输出的原因}, selected[(zone_id, 入选原因)], hs[], platform}。
    """
    n = len(hist)
    start = n - min(n, STRUCT_DAYS)
    atr_frac = atr_pct(hist)
    atr = atr_frac * hist[-1]["close"]
    _, _, major_thr, _ = swing_pivots(hist, start)
    out = {"day": hist[-1]["day"], "window_start_day": hist[start]["day"], "atr": atr, "scales": {}}
    for scale, thr in (("MAJOR", major_thr), ("MINOR", minor_threshold(atr_frac, major_thr))):
        raw_thr = thr if thr is not None else min(MINOR_MAX, max(MINOR_MIN, math.ceil(atr_frac / MINOR_STEP - 1e-9) * MINOR_STEP))
        sc: dict[str, Any] = {"thr": raw_thr, "skipped": thr is None, "zones": {}, "rejected": {}, "selected": [], "hs": [], "platform": None}
        out["scales"][scale] = sc
        if thr is None:
            continue
        piv = [p for p in zigzag(hist, thr) if p["idx"] >= start]
        valid = []
        for kind in ("L", "H"):
            for touches in _clusters(hist, piv, kind, atr):
                zid = f"{scale}-{SIDE[kind]}-{touches[0]['day']}"
                z, why = _build_zone(hist, touches, kind, atr, thr)
                if z is None:
                    sc["rejected"][zid] = why
                    continue
                z["zone_id"] = zid
                sc["zones"][zid] = z
                valid.append(z)
        picked = _select(valid, hist)
        sc["selected"] = [(z["zone_id"], why) for z, why in picked]
        sc["platform"] = _platform([z for z, _ in picked], atr)
        for kind in ("L", "H"):
            hs = _head_shoulders(hist, piv, kind, atr)
            if hs:
                hs["hs_id"] = f"{scale}-{'HS_BOTTOM' if kind == 'L' else 'HS_TOP'}-{hs['s1']['day']}-{hs['head']['day']}-{hs['s2']['day']}"
                sc["hs"].append(hs)
    return out


def confirmed_geometry(bars: list[dict], thr: float, atr: float, window_start_day: str, as_of_day: str) -> dict[str, tuple]:
    """因果审计用：只取截至 as_of_day 已确认、落在结构窗口内的拐点做聚类，返回 {zone_id: (触点日期, 价区下沿, 上沿, 颈线)}。

    阈值与 ATR 由调用方固定；结果不得随 as_of_day 之后追加的K线改变。
    """
    piv = [p for p in zigzag(bars, thr) if p["confirmed"] and p["day"] >= window_start_day and p["confirmed_at_day"] <= as_of_day]
    out: dict[str, tuple] = {}
    for kind in ("L", "H"):
        for g in _clusters(bars, piv, kind, atr):
            if len(g) < 2:
                continue
            mid = bars[g[0]["idx"] + 1 : g[-1]["idx"]]
            neck = max(b["high"] for b in mid) if kind == "L" else min(b["low"] for b in mid)
            prices = [t["price"] for t in g]
            out[f"{SIDE[kind]}-{g[0]['day']}"] = (tuple(t["day"] for t in g), min(prices), max(prices), neck)
    return out
```

- [ ] **Step 4: "头肩"从不实宣称清单移除，登记目录树**

`test_no_unimplemented_capability_claims` 把"头肩"列为未实现的能力；本任务实现了它（G5、G6 守护），所以从清单里去掉，并注明原因：

```diff
--- a/kline-structure-chart/tests/test_docs_contract.py
+++ b/kline-structure-chart/tests/test_docs_contract.py
@@ -204,8 +204,9 @@
 
 
 # 已核实未实现或已移除的能力，任何文档、注释与界面文案都不得再宣称
+# "头肩"已于多次测试价区（scripts/tested_zones.py，规格 2026-11-17 第 4 节）实现并由 test_tested_zones.py 的 G5/G6 守护，不再列入
 OVERCLAIMS = (
-    "Lo, Mamaysky", "头肩", "MACD 背离", "MACD底背离", "RSI/MACD", "CHoCH 特征", "BEARISH_CHoCH",
+    "Lo, Mamaysky", "MACD 背离", "MACD底背离", "RSI/MACD", "CHoCH 特征", "BEARISH_CHoCH",
     "正交特征", "量价正交", "筹码峰", "公式证据。", "KERNEL_DOUBLE", "WLS_", "confidence_score",
     "Walk-Forward", "Meta-Labeling 过滤", "隔夜跳空波动率修正", "动态修正保形", "defaults.json",
 )
```

`AGENTS.md`：在下面这一行之后

```text
│   ├── market_structure.py           # 大盘结构与周期层（基准结构摘要、周线月线阶段、个股与大盘对齐；只描述，不参与评分与风控）
```

插入

```markdown
│   ├── tested_zones.py               # 多次测试价区（两级 ZigZag 聚类得 N 重底 / 顶、状态、头肩、平台与逐日变化；只描述，不参与评分与风控）
```

- [ ] **Step 5: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py tests/test_docs_contract.py -q`
Expected: PASS。

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过（参考：287 passed）。

- [ ] **Step 6: 提交**

```bash
git add kline-structure-chart/scripts/tested_zones.py \
  kline-structure-chart/tests/test_tested_zones.py \
  kline-structure-chart/tests/test_docs_contract.py \
  AGENTS.md
git commit -m "$(cat <<'EOF'
feat(tested-zones): 两级拐点聚类、状态、筛选、头肩与平台

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 取整、命名、文字摘要、盘中标注与逐日变化（`compute_tested_zones`）

**Files:**
- Modify: `kline-structure-chart/scripts/tested_zones.py`（追加 B 段）
- Modify: `kline-structure-chart/tests/test_tested_zones.py`（追加任务 3 的测试）

**Interfaces:**
- Consumes: Task 2 的 `scan_zones`、`SIDE` 与各常量；`structure.money`、`HISTORY_BARS`。
- Produces: `compute_tested_zones(completed: list[dict], live: dict | None = None, round_price: Callable[[float], float] | None = None, decimals: int = 2) -> dict[str, Any]`。返回规格第 8.1 节的形状：`{"atr_abs", "atr_basis": "STRUCTURE_ATR20", "prev_bar_day", "scales": [MAJOR, MINOR], "changes": [...]}`。每个级别 `{"scale", "scale_cn", "zigzag_threshold_pct", "skipped_reason", "zones", "head_shoulders", "platform"}`；价区字段见规格第 8.1 节；次级别与主级别相同的价区只有 `{"zone_id", "same_as_zone_id"}`。`round_price` 只接收价格（调用方已绑定日期）。Task 4 由结构层调用它，Task 5~7 读它的输出。

- [ ] **Step 1: 写测试**

在 `kline-structure-chart/tests/test_tested_zones.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 任务 3：对外输出（取整、命名、摘要、盘中标注）与逐日变化
# ---------------------------------------------------------------------------

SUP, RES = "SUPPORT-2026-10-23", "RESISTANCE-2026-10-27"


def _tz(rel: str, upto: str, live: dict | None = None) -> dict:
    from tested_zones import compute_tested_zones

    return compute_tested_zones(_bars(rel, upto), live)


def _scale(tz: dict, scale: str) -> dict:
    return next(s for s in tz["scales"] if s["scale"] == scale)


def _zone(tz: dict, scale: str, zid: str) -> dict:
    return next(z for z in _scale(tz, scale)["zones"] if z["zone_id"] == zid)


def _brief(z: dict) -> list[tuple[str, float, bool]]:
    return [(t["day"], t["price"], t["confirmed"]) for t in z["touches"]]


def _changes(tz: dict) -> dict[tuple[str, str], dict]:
    return {(c["object_id"], c["change"]): c for c in tz["changes"]}


def test_g1_2026_11_13() -> None:
    tz = _tz(CASE, "2026-11-13")
    assert (tz["prev_bar_day"], tz["atr_basis"]) == ("2026-11-12", "STRUCTURE_ATR20")
    db = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert db["name_cn"] == "双底" and _brief(db) == [("2026-10-23", 3574.27, True), ("2026-11-03", 3580.38, True)]
    assert (db["state"], db["neckline_price"], db["break_price"], db["confirm_price"]) == ("HOLDING", 3708.2, 3547.69, None)
    dt = _zone(tz, "MAJOR", "MAJOR-" + RES)
    assert _brief(dt) == [("2026-10-27", 3708.2, True), ("2026-11-05", 3701.59, False)]
    assert (dt["name_cn"], dt["state"], dt["confirm_price"]) == ("双顶（第 2 次测试进行中，未确认）", "TESTING", 3590.54)
    pf = _scale(tz, "MAJOR")["platform"]
    assert (pf["lower_price"], pf["upper_price"]) == (3574.27, 3708.2)
    md = _zone(tz, "MINOR", "MINOR-SUPPORT-2026-11-06")
    assert _brief(md) == [("2026-11-06", 3610.2, True), ("2026-11-13", 3623.84, False)] and md["state"] == "TESTING"
    tt = _zone(tz, "MINOR", "MINOR-" + RES)
    assert tt["name_cn"] == "三重顶" and [t["day"] for t in tt["touches"]] == ["2026-10-27", "2026-11-05", "2026-11-12"]
    assert (tt["sequence"], tt["state"]) == ("FALLING", "HOLDING")
    minor = _scale(tz, "MINOR")
    assert {"zone_id": "MINOR-" + SUP, "same_as_zone_id": "MAJOR-" + SUP} in minor["zones"]
    assert (minor["platform"]["lower_price"], minor["platform"]["upper_price"]) == (3610.2, 3708.2)


def test_g1_2026_11_16_and_changes_from_11_13() -> None:
    tz = _tz(CASE, "2026-11-16")
    tb = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert tb["name_cn"] == "三重底（第 3 次测试进行中，未确认）" and tb["touches"][-1]["price"] == 3586.57
    assert (tb["state"], tb["confirm_price"], tb["break_price"]) == ("TESTING", 3694.17, 3547.98)
    dt = _zone(tz, "MAJOR", "MAJOR-" + RES)
    assert dt["state"] == "HOLDING" and dt["touches"][-1]["confirmed"]
    mtb = _zone(tz, "MINOR", "MINOR-" + SUP)  # 有未确认触点：两级确认价不同，次级别照常输出全部字段
    assert _brief(mtb) == _brief(tb) and mtb["confirm_price"] == 3640.37
    minor = _scale(tz, "MINOR")
    assert all(z["zone_id"] != "MINOR-SUPPORT-2026-11-06" for z in minor["zones"])
    pf = minor["platform"]
    assert (pf["lower_price"], pf["upper_price"], pf["lower_touch_count"], pf["upper_touch_count"]) == (3574.27, 3708.2, 3, 3)
    ch = _changes(tz)
    assert ch[("MAJOR-" + SUP, "TOUCH_ADDED")]["detail_cn"] == "三重底新增第 3 个低点 2026-11-16 3586.57（未确认）"
    assert (ch[("MAJOR-" + SUP, "STATE_CHANGED")]["from"], ch[("MAJOR-" + SUP, "STATE_CHANGED")]["to"]) == ("HOLDING", "TESTING")
    assert ("MAJOR-" + RES, "TOUCH_CONFIRMED") in ch
    assert (ch[("MAJOR-" + RES, "STATE_CHANGED")]["from"], ch[("MAJOR-" + RES, "STATE_CHANGED")]["to"]) == ("TESTING", "HOLDING")
    gone = ch[("MINOR-SUPPORT-2026-11-06", "ZONE_DISSOLVED")]
    assert (gone["reason"], gone["threshold_changed"]) == ("TOUCH_REPLACED", False)
    scales = [c["scale"] for c in tz["changes"]]
    assert scales == sorted(scales) and len(scales) <= 12  # 主级别在前


def test_g1_2026_11_17_break_price_does_not_follow_the_unconfirmed_low() -> None:
    tz, prev = _tz(CASE, "2026-11-17"), _tz(CASE, "2026-11-16")
    tb = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert _brief(tb) == [("2026-10-23", 3574.27, True), ("2026-11-03", 3580.38, True), ("2026-11-17", 3565.59, False)]
    assert (tb["sequence"], tb["state"], tb["zone_low_price"], tb["zone_high_price"]) == ("MIXED", "TESTING", 3574.27, 3580.38)
    assert (tb["neckline_price"], tb["measured_move_price"], tb["break_price"], tb["confirm_price"]) == (3708.2, 3842.13, 3548.04, 3672.56)
    assert _zone(tz, "MINOR", "MINOR-" + SUP)["confirm_price"] == 3619.07
    dt = _zone(tz, "MAJOR", "MAJOR-" + RES)
    assert (dt["state"], dt["break_price"]) == ("HOLDING", 3734.43)
    tt = _zone(tz, "MINOR", "MINOR-" + RES)
    assert (tt["name_cn"], tt["state"]) == ("三重顶", "HOLDING")
    pf = _scale(tz, "MINOR")["platform"]
    assert (pf["lower_touch_count"], pf["upper_touch_count"]) == (3, 3)
    # 新低 3565.59 低于价区下沿但未确认：失效价只随 ATR 变化（0.5 × ATR 之差），不跟着新低往下移
    prev_tb = _zone(prev, "MAJOR", "MAJOR-" + SUP)
    assert tb["break_price"] - prev_tb["break_price"] == pytest.approx(-0.5 * (tz["atr_abs"] - prev["atr_abs"]), abs=0.011)
    assert [(c["object_id"], c["change"]) for c in tz["changes"] if c["scale"] == "MAJOR"] == [("MAJOR-" + SUP, "TOUCH_UPDATED")]
    assert tz["changes"][0]["detail_cn"] == "三重底的未确认低点更新为 2026-11-17 3565.59"


def test_summary_cn_is_built_from_the_zone_own_fields() -> None:
    z = _zone(_tz(CASE, "2026-11-17"), "MAJOR", "MAJOR-" + SUP)
    assert z["summary_cn"] == (
        "三重底（第 3 次测试进行中，未确认）：2026-10-23 3574.27、2026-11-03 3580.38、2026-11-17 3565.59（未确认）；"
        "价区 3574.27~3580.38，颈线 3708.20。第 3 个低点 3565.59 尚未确认：之后最高价达到 3672.56 即确认；收盘跌破 3548.04 判定破位。"
    )


def test_head_shoulders_output_fields() -> None:
    hs = _scale(_tz(SH, "2026-06-26"), "MAJOR")["head_shoulders"]
    (top,) = [h for h in hs if h["name_cn"] == "头肩顶"]
    assert (top["left_shoulder"], top["head"], top["right_shoulder"]) == (
        {"day": "2026-03-03", "price": 4197.23}, {"day": "2026-05-14", "price": 4258.86}, {"day": "2026-06-23", "price": 4175.35}
    )
    assert top["neckline_points"] == [{"day": "2026-03-23", "price": 3794.68}, {"day": "2026-06-08", "price": 3927.85}]
    assert top["right_shoulder_confirmed"] and top["state"] == "FORMING"
    assert top["measured_move_price"] == pytest.approx(top["neckline_today_price"] - top["height_abs"], abs=0.02)


def test_threshold_change_is_flagged_not_reported_as_structure_change() -> None:
    tz = _tz(MOUTAI, "2026-10-15")
    major = _scale(tz, "MAJOR")
    assert major["zigzag_threshold_pct"] == 3.11
    flagged = [c for c in tz["changes"] if c["scale"] == "MAJOR"]
    assert flagged and all(c["threshold_changed"] for c in flagged)
    assert all(c["detail_cn"].endswith("（阈值由 3.00% 变为 3.11%，按新阈值重算）") for c in flagged)
    assert [z["zone_id"] for z in major["zones"]] == ["MAJOR-RESISTANCE-2026-08-03"]


def test_minor_scale_skipped_when_not_finer_than_major(monkeypatch: pytest.MonkeyPatch) -> None:
    import tested_zones

    both = _tz(CASE, "2026-11-17")
    monkeypatch.setattr(tested_zones, "minor_threshold", lambda atr_frac, major_thr: None)
    tz = _tz(CASE, "2026-11-17")
    minor = _scale(tz, "MINOR")
    assert minor["skipped_reason"].startswith("次级别阈值") and minor["zones"] == [] and minor["platform"] is None
    assert _scale(tz, "MAJOR") == _scale(both, "MAJOR")
    assert all(c["scale"] == "MAJOR" for c in tz["changes"])


def _no_intraday(tz: dict) -> dict:
    return {**tz, "scales": [{**s, "zones": [{k: v for k, v in z.items() if k != "intraday_note"} for z in s["zones"]]} for s in tz["scales"]]}


def test_intraday_bar_only_annotates_and_never_moves_zones() -> None:
    """铁律 0：盘中K线只进 intraday_note。用录制行情里 2025-03-14 那根真实K线充当盘中K线。"""
    from tested_zones import compute_tested_zones

    bars = _bars(SH)
    i = next(k for k, b in enumerate(bars) if b["day"] == "2025-03-14")
    closed, live = compute_tested_zones(bars[:i]), compute_tested_zones(bars[:i], bars[i])
    assert _no_intraday(live) == _no_intraday(closed)
    z = _zone(live, "MINOR", "MINOR-RESISTANCE-2025-02-27")
    assert z["state"] == "HOLDING"
    assert z["intraday_note"] == "盘中最高 3421.23 已高于失效价 3410.01（未收盘，不作确认）"
    assert _zone(closed, "MINOR", "MINOR-RESISTANCE-2025-02-27")["intraday_note"] is None


def test_short_history_outputs_valid_structure() -> None:
    from tested_zones import compute_tested_zones

    for n in (30, 60):
        tz = compute_tested_zones(_bars(MOUTAI)[:n])
        assert [s["scale"] for s in tz["scales"]] == ["MAJOR", "MINOR"]
        json.dumps(tz, allow_nan=False)


def test_zone_prices_are_quotable_but_heights_and_atr_are_not() -> None:
    from price_text import collect_price_whitelist

    tz = _tz(CASE, "2026-11-17")
    prices = set(collect_price_whitelist({"structure": {"tested_zones": tz}}, 2, [])["prices"])
    z = _zone(tz, "MAJOR", "MAJOR-" + SUP)
    assert {z["break_price"], z["confirm_price"], z["neckline_price"], z["measured_move_price"], z["zone_low_price"]} <= prices
    assert {t["price"] for t in z["touches"]} <= prices
    assert z["height_abs"] not in prices and tz["atr_abs"] not in prices


def test_tested_zones_size_stays_under_hard_bound() -> None:
    """D4：数量上限（每侧 3 个价区、8 个触点、12 条变化）给出硬上界；实测最大 23.2 KB，这里守护 32 KB。"""
    from tested_zones import compute_tested_zones

    for rel in (SH, MOUTAI, BYD, CASE):
        bars = _bars(rel)
        for cut in range(450, len(bars) + 1, 25):
            tz = compute_tested_zones(bars[:cut])
            assert len(json.dumps(tz, ensure_ascii=False, indent=2).encode("utf-8")) <= 32 * 1024
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: FAIL（任务 3 的 11 个用例报 `ImportError: cannot import name 'compute_tested_zones'`；前面 11 个用例仍通过）。

- [ ] **Step 3: 在 `scripts/tested_zones.py` 末尾追加 B 段**（与 A 段之间空两行）

```python
# ---------------------------------------------------------------------------
# 输出：取整、命名、文字摘要、盘中标注
# ---------------------------------------------------------------------------


SCALE_CN = {"MAJOR": "主级别", "MINOR": "次级别"}
SIDE_CN = {"L": "支撑", "H": "压力"}
SEQUENCE_CN = {
    ("L", "RISING"): "低点逐次抬高", ("L", "FALLING"): "低点逐次降低", ("L", "MIXED"): "低点不单调",
    ("H", "RISING"): "高点逐次抬高", ("H", "FALLING"): "高点逐次降低", ("H", "MIXED"): "高点不单调",
}
STATE_CN = {
    "L": {"BROKEN": "已破位", "FALSE_BREAK": "假跌破后收回", "NECKLINE_CROSSED": "已突破颈线", "HOLDING": "价区守住、未过颈线"},
    "H": {"BROKEN": "已突破", "FALSE_BREAK": "假突破后回落", "NECKLINE_CROSSED": "已跌破颈线", "HOLDING": "价区压住、未破颈线"},
}
HS_STATE_CN = {
    "L": {"FORMING": "形成中、未过颈线", "NECKLINE_CROSSED": "已突破颈线", "FAILED": "形态失败"},
    "H": {"FORMING": "形成中、未破颈线", "NECKLINE_CROSSED": "已跌破颈线", "FAILED": "形态失败"},
}
CHANGE_CN = {
    "ZONE_NEW": "新价区", "TOUCH_ADDED": "新增未确认触点", "TOUCH_UPDATED": "触点更新", "TOUCH_CONFIRMED": "触点确认",
    "TOUCH_LEFT_ZONE": "未确认极值越出价区", "STATE_CHANGED": "状态变化", "ZONE_DISSOLVED": "价区不再列出",
    "HS_NEW": "新头肩形态", "HS_STATE_CHANGED": "头肩状态变化", "HS_ENDED": "头肩形态不再成立",
    "PLATFORM_NEW": "新平台", "PLATFORM_CHANGED": "平台变化", "PLATFORM_ENDED": "平台不再成立",
}
DISSOLVE_CN = {
    "TOUCH_REPLACED": "未确认触点被取代后不足 2 个触点",
    "HEIGHT": "颈线高度不足 1.2 个 ATR",
    "EXPIRED": "破位后超过 3 根K线才收回，价区失效",
    "BROKEN_STALE": "破位已超过 20 根K线",
    "LEFT_WINDOW": "首触点移出近 120 日结构窗口",
    "PIVOT_GONE": "首触点不再是本级别拐点",
    "NOT_SELECTED": "不再满足入选条件（价区仍在，只是不再列出）",
    "SCALE_SKIPPED": "本级别今日跳过（次级别阈值不小于主级别）",
}


def _base_name(kind: str, n_touch: int) -> str:
    if n_touch == 2:
        return "双底" if kind == "L" else "双顶"
    if n_touch == 3:
        return "三重底" if kind == "L" else "三重顶"
    return f"{n_touch} 次测试的{SIDE_CN[kind]}区"


def _state_cn(z: dict) -> str:
    if z["state"] == "TESTING":
        return f"第 {len(z['touches'])} 次测试进行中"
    return STATE_CN[z["kind"]][z["state"]]


def _summary(z: dict, f: Callable[[float], str], since: str | None) -> str:
    n_t, kind = len(z["touches"]), z["kind"]
    shown = z["touches"] if n_t <= MAX_TOUCHES else z["touches"][:1] + z["touches"][-(MAX_TOUCHES - 1) :]
    parts = [f"{t['day']} {f(t['price'])}" + ("" if t["confirmed"] else "（未确认）") for t in shown]
    touch_text = (parts[0] + "、…、" + "、".join(parts[1:])) if n_t > MAX_TOUCHES else "、".join(parts)
    head = f"{_name(z)}：{touch_text}；价区 {f(z['low'])}~{f(z['high'])}，颈线 {f(z['neck'])}。"
    brk, neck, meas = f(z["break"]), f(z["neck"]), f(z["measured"])
    st = z["state"]
    if kind == "L":
        tail = {
            "TESTING": f"第 {n_t} 个低点 {f(z['unc']['price']) if z['unc'] else ''} 尚未确认：之后最高价达到 {f(z['confirm']) if z['confirm'] else ''} 即确认；收盘跌破 {brk} 判定破位。",
            "HOLDING": f"收盘站上颈线 {neck} 为突破，测量位 {meas}；收盘跌破 {brk} 判定破位。",
            "NECKLINE_CROSSED": f"已于 {since} 收盘站上颈线 {neck}，测量位 {meas}；收盘跌破 {brk} 判定破位。",
            "BROKEN": f"已于 {since} 收盘跌破失效价 {brk}，支撑转为上方压力；收盘重新站上 {f(z['low'])} 视为收回。",
            "FALSE_BREAK": f"曾收盘跌破 {brk}，{since} 收盘收回（3 根K线内，假跌破）；再次收盘跌破 {brk} 判定破位。",
        }[st]
    else:
        tail = {
            "TESTING": f"第 {n_t} 个高点 {f(z['unc']['price']) if z['unc'] else ''} 尚未确认：之后最低价达到 {f(z['confirm']) if z['confirm'] else ''} 即确认；收盘站上 {brk} 判定价区被突破。",
            "HOLDING": f"收盘跌破颈线 {neck} 为破位，测量位 {meas}；收盘站上 {brk} 判定价区被突破。",
            "NECKLINE_CROSSED": f"已于 {since} 收盘跌破颈线 {neck}，测量位 {meas}；收盘站上 {brk} 判定价区被突破。",
            "BROKEN": f"已于 {since} 收盘站上失效价 {brk}，压力转为下方支撑；收盘重新跌回 {f(z['high'])} 及以下视为收回。",
            "FALSE_BREAK": f"曾收盘站上 {brk}，{since} 收盘回落（3 根K线内，假突破）；再次收盘站上 {brk} 判定价区被突破。",
        }[st]
    return head + tail


def _name(z: dict) -> str:
    n_t = len(z["touches"])
    return _base_name(z["kind"], n_t) + (f"（第 {n_t} 次测试进行中，未确认）" if z["unc"] else "")


def _intraday_note(z: dict, live: dict | None, f: Callable[[float], str]) -> str | None:
    if not live:
        return None
    if z["kind"] == "L" and float(live["low"]) < z["break"]:
        return f"盘中最低 {f(float(live['low']))} 已低于失效价 {f(z['break'])}（未收盘，不作确认）"
    if z["kind"] == "H" and float(live["high"]) > z["break"]:
        return f"盘中最高 {f(float(live['high']))} 已高于失效价 {f(z['break'])}（未收盘，不作确认）"
    if z["low"] <= float(live["close"]) <= z["high"]:
        return "盘中正在测试该价区（未收盘，不作确认）"
    return None


def _same_touches(a: dict, b: dict) -> bool:
    return [(t["day"], t["price"], t["confirmed"]) for t in a["touches"]] == [(t["day"], t["price"], t["confirmed"]) for t in b["touches"]]


def _stub_of(scan: dict, zid: str) -> str | None:
    """次级别价区与某个已输出的主级别价区触点完全相同、且都没有未确认触点时，返回该主级别 zone_id。

    有未确认触点时两个级别的确认价不同（阈值不同），照常输出全部字段。
    """
    if not zid.startswith("MINOR-"):
        return None
    major = scan["scales"]["MAJOR"]
    z = scan["scales"]["MINOR"]["zones"][zid]
    if z["unc"]:
        return None
    for mid, _ in major["selected"]:
        m = major["zones"][mid]
        if m["kind"] == z["kind"] and _same_touches(m, z):
            return mid
    return None


def _render(scan: dict, hist: list[dict], live: dict | None, rp: Callable[[float], float], decimals: int) -> list[dict]:
    def obs(v: float) -> float:  # 观察到的价格（触点、价区边缘、颈线）：与结构层拐点同口径
        return float(money(v))

    def f(v: float) -> str:
        return f"{float(v):.{decimals}f}"

    scales = []
    for scale in ("MAJOR", "MINOR"):
        sc = scan["scales"][scale]
        item: dict[str, Any] = {
            "scale": scale,
            "scale_cn": SCALE_CN[scale],
            "zigzag_threshold_pct": round(sc["thr"] * 100, 2),
            "skipped_reason": None,
            "zones": [],
            "head_shoulders": [],
            "platform": None,
        }
        if sc["skipped"]:
            major_pct = scan["scales"]["MAJOR"]["thr"] * 100
            item["skipped_reason"] = f"次级别阈值 {sc['thr'] * 100:.2f}% 不小于主级别阈值 {major_pct:.2f}%，本级别跳过"
            scales.append(item)
            continue
        for zid, why in sc["selected"]:
            stub = _stub_of(scan, zid)
            if stub:
                item["zones"].append({"zone_id": zid, "same_as_zone_id": stub})
                continue
            z = sc["zones"][zid]
            since = hist[z["since_idx"]]["day"] if z["since_idx"] is not None else None
            shown = z["touches"] if len(z["touches"]) <= MAX_TOUCHES else z["touches"][:1] + z["touches"][-(MAX_TOUCHES - 1) :]
            zr = {**z, "break": rp(z["break"]), "measured": rp(z["measured"]), "confirm": rp(z["confirm"]) if z["confirm"] else None,
                  "low": obs(z["low"]), "high": obs(z["high"]), "neck": obs(z["neck"])}
            item["zones"].append({
                "zone_id": zid,
                "side": SIDE[z["kind"]],
                "side_cn": SIDE_CN[z["kind"]],
                "name_cn": _name(z),
                "touch_count": len(z["touches"]),
                "confirmed_touch_count": sum(1 for t in z["touches"] if t["confirmed"]),
                "touches": [
                    {"day": t["day"], "price": obs(t["price"]), "confirmed": t["confirmed"], "confirmed_at_day": t["confirmed_at_day"]}
                    for t in shown
                ],
                "touches_truncated": len(z["touches"]) > MAX_TOUCHES,
                "zone_low_price": zr["low"],
                "zone_high_price": zr["high"],
                "neckline_price": zr["neck"],
                "height_abs": obs(z["height"]),
                "height_atr": round(z["height"] / scan["atr"], 2),
                "measured_move_price": zr["measured"],
                "break_price": zr["break"],
                "confirm_price": zr["confirm"],
                "sequence": z["sequence"],
                "sequence_cn": SEQUENCE_CN[(z["kind"], z["sequence"])],
                "first_last_atr": round(z["first_last_atr"], 2),
                "state": z["state"],
                "state_cn": _state_cn(z),
                "state_since_day": since,
                "selected_because": why,
                "intraday_note": _intraday_note(zr, live, f),
                "summary_cn": _summary(zr, f, since),
            })
        for hs in sc["hs"]:
            item["head_shoulders"].append(_render_hs(hs, rp, obs, f))
        pf = sc["platform"]
        if pf:
            s, r = pf["support"], pf["resistance"]
            lower, upper = obs(s["low"]), obs(r["high"])
            since_day = min(s["touches"][0]["day"], r["touches"][0]["day"])
            item["platform"] = {
                "lower_price": lower,
                "upper_price": upper,
                "lower_zone_id": s["zone_id"],
                "upper_zone_id": r["zone_id"],
                "lower_touch_count": len(s["touches"]),
                "upper_touch_count": len(r["touches"]),
                "gap_atr": round(pf["gap_atr"], 2),
                "since_day": since_day,
                "summary_cn": (
                    f"平台：下沿 {f(lower)}（{len(s['touches'])} 次测试）、上沿 {f(upper)}（{len(r['touches'])} 次测试），"
                    f"自 {since_day} 起，两沿间距 {pf['gap_atr']:.2f} 个 ATR。平台是两个价区的组合描述，任一侧破位或越过颈线后不再成立。"
                ),
            }
        scales.append(item)
    return scales


def _render_hs(hs: dict, rp: Callable[[float], float], obs: Callable[[float], float], f: Callable[[float], str]) -> dict:
    kind = hs["kind"]
    name = "头肩底" if kind == "L" else "头肩顶"
    s2_conf = hs["s2"]["confirmed"]
    state_cn = HS_STATE_CN[kind][hs["state"]] + ("" if s2_conf else "（右肩未确认）")
    neck_today, measured = rp(hs["neck_today"]), rp(hs["measured"])
    pt = lambda p: {"day": p["day"], "price": obs(p["price"])}  # noqa: E731
    shoulder = f"右肩 {hs['s2']['day']} {f(obs(hs['s2']['price']))}" + ("" if s2_conf else "（右肩形成中，未确认）")
    if kind == "L":
        tail = (f"收盘站上今日颈线 {f(neck_today)} 为突破，测量位 {f(measured)}；"
                f"收盘跌破头部 {f(obs(hs['head']['price']))} 下方 0.5 个 ATR 判定形态失败。")
    else:
        tail = (f"收盘跌破今日颈线 {f(neck_today)} 为破位，测量位 {f(measured)}；"
                f"收盘站上头部 {f(obs(hs['head']['price']))} 上方 0.5 个 ATR 判定形态失败。")
    return {
        "name_cn": name,
        "left_shoulder": pt(hs["s1"]),
        "head": pt(hs["head"]),
        "right_shoulder": pt(hs["s2"]),
        "right_shoulder_confirmed": s2_conf,
        "neckline_points": [pt(hs["n1"]), pt(hs["n2"])],
        "neckline_today_price": neck_today,
        "height_abs": obs(hs["height"]),
        "measured_move_price": measured,
        "state": hs["state"],
        "state_cn": state_cn,
        "summary_cn": (f"{name}：左肩 {hs['s1']['day']} {f(obs(hs['s1']['price']))}、头部 {hs['head']['day']} "
                       f"{f(obs(hs['head']['price']))}、{shoulder}；{state_cn}。{tail}"),
    }


# ---------------------------------------------------------------------------
# 与上一根已收盘K线相比的变化（规格 6）
# ---------------------------------------------------------------------------


def _zone_changes(cur: dict, prev: dict, f: Callable[[float], str]) -> list[tuple[str, str | None, str | None, str | None, str]]:
    """返回 [(change, from, to, reason, detail_cn)]。cur / prev 是该 zone_id 在两天的内部价区（都存在且都已输出或入选）。"""
    out = []
    kind = cur["kind"]
    word = "低" if kind == "L" else "高"
    name = _base_name(kind, len(cur["touches"]))
    p_unc, c_unc = prev["unc"], cur["unc"]
    cur_conf = {t["day"]: t for t in cur["touches"] if t["confirmed"]}
    explained = False
    if p_unc and p_unc["day"] in cur_conf:
        t = cur_conf[p_unc["day"]]
        out.append(("TOUCH_CONFIRMED", None, None, None, f"{name}的{word}点 {t['day']} {f(t['price'])} 已于 {t['confirmed_at_day']} 确认"))
        explained = True
    elif p_unc and c_unc and c_unc["day"] != p_unc["day"]:
        out.append(("TOUCH_UPDATED", None, None, None, f"{name}的未确认{word}点更新为 {c_unc['day']} {f(c_unc['price'])}"))
        explained = True
    elif p_unc and not c_unc:
        out.append(("TOUCH_LEFT_ZONE", None, None, None, f"{name}在 {p_unc['day']} 的未确认{word}点被越出价区的新极值取代，不再计入"))
        explained = True
    if c_unc and not p_unc:
        out.append(("TOUCH_ADDED", None, None, None, f"{name}新增第 {len(cur['touches'])} 个{word}点 {c_unc['day']} {f(c_unc['price'])}（未确认）"))
        explained = True
    prev_conf_days = [t["day"] for t in prev["touches"] if t["confirmed"]]
    expect = prev_conf_days + ([p_unc["day"]] if p_unc and p_unc["day"] in cur_conf else [])
    if not explained and [t["day"] for t in cur["touches"] if t["confirmed"]] != expect:
        text = "、".join(f"{t['day']} {f(t['price'])}" for t in cur["touches"])
        out.append(("TOUCH_UPDATED", None, None, None, f"{name}的触点按当日数据重算为 {text}"))
    if prev["state"] != cur["state"]:
        out.append(("STATE_CHANGED", prev["state"], cur["state"], None, f"{name}：{_generic_state(prev)} → {_generic_state(cur)}"))
    return out


def _generic_state(z: dict) -> str:
    return "测试进行中" if z["state"] == "TESTING" else STATE_CN[z["kind"]][z["state"]]


def _dissolve_reason(zid: str, cur_sc: dict, cur_scan: dict, prev_zone: dict) -> str:
    if cur_sc["skipped"]:
        return "SCALE_SKIPPED"
    if prev_zone["touches"][0]["day"] < cur_scan["window_start_day"]:
        return "LEFT_WINDOW"
    if zid in cur_sc["zones"]:
        return "NOT_SELECTED"
    return cur_sc["rejected"].get(zid, "PIVOT_GONE")


def _changes(cur: dict, prev: dict | None, decimals: int) -> list[dict]:
    if prev is None:
        return []

    def f(v: float) -> str:
        return f"{float(v):.{decimals}f}"

    rows: list[dict] = []
    for scale in ("MAJOR", "MINOR"):
        c_sc, p_sc = cur["scales"][scale], prev["scales"][scale]
        thr_changed = c_sc["skipped"] != p_sc["skipped"] or abs(c_sc["thr"] - p_sc["thr"]) > 1e-9
        suffix = f"（阈值由 {p_sc['thr'] * 100:.2f}% 变为 {c_sc['thr'] * 100:.2f}%，按新阈值重算）" if thr_changed else ""

        def add(obj_type: str, obj_id: str, change: str, frm: str | None, to: str | None, reason: str | None, detail: str) -> None:
            rows.append({
                "scale": scale, "object_type": obj_type, "object_id": obj_id, "change": change, "change_cn": CHANGE_CN[change],
                "from": frm, "to": to, "reason": reason, "threshold_changed": thr_changed, "detail_cn": detail + suffix,
            })

        c_sel = [zid for zid, _ in c_sc["selected"]]
        p_sel = [zid for zid, _ in p_sc["selected"]]
        for zid in c_sel + [z for z in p_sel if z not in c_sel]:
            c_stub = zid in c_sel and _stub_of(cur, zid)
            p_stub = zid in p_sel and _stub_of(prev, zid)
            if (c_stub or zid not in c_sel) and (p_stub or zid not in p_sel):
                continue  # 只以"与主级别相同"的形式出现过：视同主级别价区，不重复记变化
            if zid in c_sel and zid in p_sel:
                for change, frm, to, reason, detail in _zone_changes(c_sc["zones"][zid], p_sc["zones"][zid], f):
                    add("ZONE", zid, change, frm, to, reason, detail)
            elif zid in c_sel:
                z = c_sc["zones"][zid]
                reason = "SELECTED" if zid in p_sc["zones"] else "FORMED"
                label = _name(z) if z["state"] == "TESTING" else f"{_name(z)}（{_state_cn(z)}）"
                add("ZONE", zid, "ZONE_NEW", None, z["state"], reason, label + ("，重新满足入选条件" if reason == "SELECTED" else ""))
            else:
                pz = p_sc["zones"][zid]
                reason = _dissolve_reason(zid, c_sc, cur, pz)
                add("ZONE", zid, "ZONE_DISSOLVED", pz["state"], None, reason,
                    f"{_base_name(pz['kind'], len(pz['touches']))}（首触点 {pz['touches'][0]['day']}）不再列出：{DISSOLVE_CN[reason]}")
        c_hs = {h["hs_id"]: h for h in c_sc["hs"]}
        p_hs = {h["hs_id"]: h for h in p_sc["hs"]}
        for hid, h in c_hs.items():
            name = "头肩底" if h["kind"] == "L" else "头肩顶"
            if hid not in p_hs:
                add("HEAD_SHOULDERS", hid, "HS_NEW", None, h["state"], None, f"{name}（{HS_STATE_CN[h['kind']][h['state']]}）")
            elif p_hs[hid]["state"] != h["state"]:
                add("HEAD_SHOULDERS", hid, "HS_STATE_CHANGED", p_hs[hid]["state"], h["state"], None,
                    f"{name}：{HS_STATE_CN[h['kind']][p_hs[hid]['state']]} → {HS_STATE_CN[h['kind']][h['state']]}")
        for hid, h in p_hs.items():
            if hid not in c_hs:
                name = "头肩底" if h["kind"] == "L" else "头肩顶"
                add("HEAD_SHOULDERS", hid, "HS_ENDED", h["state"], None, None, f"{name}（右肩 {h['s2']['day']}）不再成立")
        c_pf, p_pf = c_sc["platform"], p_sc["platform"]
        c_id = f"{c_pf['support']['zone_id']}|{c_pf['resistance']['zone_id']}" if c_pf else None
        p_id = f"{p_pf['support']['zone_id']}|{p_pf['resistance']['zone_id']}" if p_pf else None

        def pf_text(pf: dict) -> str:
            return (f"{f(money(pf['support']['low']))}~{f(money(pf['resistance']['high']))}"
                    f"（下沿 {len(pf['support']['touches'])} 次、上沿 {len(pf['resistance']['touches'])} 次）")

        def pf_sig(pf: dict) -> tuple:
            return (float(money(pf["support"]["low"])), float(money(pf["resistance"]["high"])),
                    len(pf["support"]["touches"]), len(pf["resistance"]["touches"]))

        if p_id and p_id != c_id:
            add("PLATFORM", p_id, "PLATFORM_ENDED", None, None, None, "原平台不再成立（一侧价区破位、越过颈线或不再入选）")
        if c_id and c_id != p_id:
            add("PLATFORM", c_id, "PLATFORM_NEW", None, None, None, f"平台 {pf_text(c_pf)}")
        elif c_id and pf_sig(c_pf) != pf_sig(p_pf):
            add("PLATFORM", c_id, "PLATFORM_CHANGED", None, None, None, f"平台变为 {pf_text(c_pf)}")
    return rows[:MAX_CHANGES]


def compute_tested_zones(
    completed: list[dict],
    live: dict | None = None,
    round_price: Callable[[float], float] | None = None,
    decimals: int = 2,
) -> dict[str, Any]:
    """结构层入口：只用已收盘K线 completed（最近 HISTORY_BARS 根）；live 为盘中未收盘K线，只用于 intraday_note。

    round_price(价格) -> 按价位表取整后的价格，用于推算出的价位（失效价、确认价、测量位、头肩今日颈线）；
    缺省按 structure.money 取整。观察到的价格（触点、价区边缘、颈线）与结构层拐点同口径，按 money 取整。
    """
    rp = (lambda v: float(round_price(float(v)))) if round_price else (lambda v: float(money(v)))
    hist = completed[-HISTORY_BARS:]
    cur = scan_zones(hist)
    prev = scan_zones(completed[:-1][-HISTORY_BARS:]) if len(completed) > 2 else None
    return {
        "atr_abs": float(money(cur["atr"])),
        "atr_basis": "STRUCTURE_ATR20",
        "prev_bar_day": prev["day"] if prev else None,
        "scales": _render(cur, hist, live, rp, decimals),
        "changes": _changes(cur, prev, decimals),
    }
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: PASS（22 passed）。

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过（参考：298 passed）。

- [ ] **Step 5: 提交**

```bash
git add kline-structure-chart/scripts/tested_zones.py \
  kline-structure-chart/tests/test_tested_zones.py
git commit -m "$(cat <<'EOF'
feat(tested-zones): 取整、命名、文字摘要、盘中标注与逐日变化

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 接入结构层、因果审计与不变性检查（决策路径不读取）

**Files:**
- Modify: `kline-structure-chart/scripts/structure.py`（`compute_structure_layer`）
- Modify: `kline-structure-chart/scripts/market_structure.py`（基准结构层调用）
- Modify: `kline-structure-chart/scripts/analysis.py`（历史回放快照、`analyze_bundle`、`structure_levels`）
- Modify: `kline-structure-chart/scripts/evaluation.py`（因果审计 2b 项、`modules_audited`、回测逐日回放）
- Modify: `kline-structure-chart/scripts/render_structure_chart.py`（`datalen_for` 取数下限、v4.1 结果键）
- Modify: `kline-structure-chart/tests/check_days_invariance.py`（`STRUCT_KEYS`）
- Modify: `kline-structure-chart/tests/test_tested_zones.py`（追加任务 4 的测试）

**Interfaces:**
- Consumes: Task 3 的 `compute_tested_zones`；Task 2 的 `confirmed_geometry`、`minor_threshold`；`structure.atr_pct`。
- Produces:
  - `compute_structure_layer(..., with_tested_zones: bool = True)`，返回字典新增键 `"tested_zones"`（关闭时为 `None`）。推算价位用 `round_price(价格, 最后一根已收盘K线的日期)` 取整。
  - `analyze_bundle(..., include_tested_zones: bool = True)`；内部报告 `report["mathematical_indicators"]["structure_levels"]["tested_zones"]`（Task 5 从这里投影）。
  - `run_causal_replay_audit(...)["modules_audited"]` 含 `"tested_zones"`。
  - v4.1 命令行结果 JSON 新增顶层键 `"tested_zones"`；`datalen_for(days)` 返回 `max(HISTORY_BARS + 1, days + 250)`。

规格第 10.3 节：上一交易日的对照要在同样 420 根上重算，至少需要 421 根已收盘K线。主流程固定取 760 根，不受影响；v4.1 命令行在 `--days 60` 时只取 420 根，所以把取数下限改为 421 根（实测不改也能通过 420 次检查，这里改成保证）。

- [ ] **Step 1: 写测试**

在 `kline-structure-chart/tests/test_tested_zones.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 任务 4：接入结构层、回测与因果审计
# ---------------------------------------------------------------------------


def test_structure_layer_carries_tested_zones_and_can_skip_them() -> None:
    from structure import compute_structure_layer
    from tested_zones import compute_tested_zones

    bars = load_recorded(SH)
    layer = compute_structure_layer(bars, None, 120)
    assert layer["tested_zones"] == compute_tested_zones(layer["completed"], None, decimals=layer["price_decimals"])
    assert compute_structure_layer(bars, None, 120, with_tested_zones=False)["tested_zones"] is None


def test_derived_prices_follow_the_tick_table_on_hk() -> None:
    """港股价位表按价格分档（20~50 港元 0.05、200~500 港元 0.2）：推算出的价位必须落在价位表上。"""
    from market_rules import round_to_tick
    from structure import compute_structure_layer

    bars = load_recorded("yahoo_daily/0700.HK.json")
    layer = compute_structure_layer(bars, None, 120, round_price=lambda p, day: round_to_tick(p, "HK", "equity", day))
    day = layer["completed"][-1]["day"]
    derived = [
        z[k] for s in layer["tested_zones"]["scales"] for z in s["zones"]
        for k in ("break_price", "confirm_price", "measured_move_price") if z.get(k) is not None
    ]
    assert derived
    assert all(p == round_to_tick(p, "HK", "equity", day) for p in derived)


def test_decisions_do_not_depend_on_tested_zones(replay_recorded) -> None:
    """开与关多次测试价区：除 tested_zones 外，内部报告（规则分、评级、动作、否决、计划价位）逐项相同。"""
    import data_gateway
    from analysis import analyze_bundle

    req = {"symbol": "600519.SH", "market": "CN", "asset_type": "equity", "display_bars": 120, "structure_bars": 120, "horizon_bars": 15}
    full = data_gateway.load_bundle(req)
    bars, bench = full["target"]["bars"], full["benchmark"]
    for t in range(len(bars) - 200, len(bars), 25):
        cut_day = bars[t]["day"]
        bundle = {
            **full,
            "target": {**full["target"], "bars": bars[: t + 1], "intraday": None},
            "benchmark": {**bench, "bars": [b for b in bench["bars"] if b["day"] <= cut_day]},
        }
        on = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_tested_zones=True)
        off = analyze_bundle(bundle, req, include_replay=False, include_forecast=False, include_tested_zones=False)
        assert on["mathematical_indicators"]["structure_levels"].pop("tested_zones") is not None
        assert off["mathematical_indicators"]["structure_levels"].pop("tested_zones") is None
        assert on == off, cut_day


def test_causal_replay_audit_covers_tested_zones() -> None:
    from evaluation import run_causal_replay_audit

    audit = run_causal_replay_audit(load_recorded(SH), check_steps=4)
    assert "tested_zones" in audit["modules_audited"]
    assert audit["passed"], audit["mismatches"]
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: FAIL（任务 4 的 4 个用例失败：结构层没有 `tested_zones` 键、不认识 `with_tested_zones` / `include_tested_zones` 参数、审计项里没有 `tested_zones`）。

- [ ] **Step 2b: 记录改前的 v4.1 不变性基线**

Run: `uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`
Expected: `runs=420 failing=0 days=[60, 120, 200, 250, 300, 500]`。

- [ ] **Step 3: 应用补丁**

```diff
--- a/kline-structure-chart/scripts/structure.py
+++ b/kline-structure-chart/scripts/structure.py
@@ -914,6 +914,7 @@
     price_decimals: Callable[[float, str], int] | None = None,
     horizon_bars: int = 15,
     round_price: Callable[[float, str], float] | None = None,
+    with_tested_zones: bool = True,
 ) -> dict[str, Any]:
     """一次性计算结构层全部结果，供 JSON 报告与 PNG 渲染共用。
 
@@ -998,7 +999,13 @@
     projections["impulse_wave"] = scen_bundle["impulse_wave"]
     # 展示用波段骨架：同一阈值在全部已收盘K线上的 ZigZag，下标换算到主图
     display_pivots = [dict(p, idx=p["idx"] - view_start) for p in zigzag(completed, zz_thr) if p["idx"] >= view_start]
+    tested_zones = None
+    if with_tested_zones:
+        from tested_zones import compute_tested_zones
+        last_day = completed[-1]["day"]
+        tested_zones = compute_tested_zones(completed, active_intra, round_price=(lambda v: round_price(v, last_day)) if round_price else None, decimals=decimals)
     return {
+        "tested_zones": tested_zones,
         "completed": completed,
         "active_intra": active_intra,
         "data_note": data_note,
```

```diff
--- a/kline-structure-chart/scripts/market_structure.py
+++ b/kline-structure-chart/scripts/market_structure.py
@@ -260,6 +260,7 @@
         price_decimals=lambda price, day: price_decimals(price, b_inst["market"], b_inst["asset_type"], day),
         horizon_bars=horizon_bars,
         round_price=lambda price, day: round_to_tick(price, b_inst["market"], b_inst["asset_type"], day),
+        with_tested_zones=False,
     )
     sview = b_layer["sview"]
     series = compute_all_indicators(b_layer["hist"])["series"]
```

```diff
--- a/kline-structure-chart/scripts/analysis.py
+++ b/kline-structure-chart/scripts/analysis.py
@@ -124,6 +124,7 @@
             price_decimals=lambda price, day: price_decimals(price, meta["market"], meta["asset_type"], day),
             horizon_bars=horizon_bars,
             round_price=lambda price, day: round_to_tick(price, meta["market"], meta["asset_type"], day),
+            with_tested_zones=False,
         )
         sview_sub = layer_sub["sview"]
         ind_sub_win = _indicators_on_window(layer_sub["hist"], len(sview_sub))
@@ -239,6 +240,7 @@
     structure: dict[str, Any] | None = None,
     include_forecast: bool = True,
     include_market_layer: bool = True,
+    include_tested_zones: bool = True,
 ) -> AnalysisReport:
     """100% 纯数学计算核心：从 OHLCV 矩阵计算全部指标、形态证据、SMC、多周期共振分、三重屏障与正常波动范围。"""
     target = bundle["target"]
@@ -263,6 +265,7 @@
             price_decimals=lambda price, day: price_decimals(price, inst["market"], inst["asset_type"], day),
             horizon_bars=horizon_bars,
             round_price=lambda price, day: round_to_tick(price, inst["market"], inst["asset_type"], day),
+            with_tested_zones=include_tested_zones,
         )
     )
     completed, active_intra, data_note = layer["completed"], layer["active_intra"], layer["data_note"]
@@ -543,6 +546,7 @@
             "abc_scenario": scenario,
             "wave_counts": layer["wave_counts"],
             "wedge_structure": wedge_clean,
+            "tested_zones": layer.get("tested_zones"),
         },
     }
 
```

```diff
--- a/kline-structure-chart/scripts/evaluation.py
+++ b/kline-structure-chart/scripts/evaluation.py
@@ -37,7 +37,8 @@
     compute_multi_timeframe_score,
     evaluate_five_core_strategies,
 )
-from structure import HISTORY_BARS, STRUCT_DAYS, compute_structure_layer
+from structure import HISTORY_BARS, STRUCT_DAYS, atr_pct, compute_structure_layer
+from tested_zones import confirmed_geometry, minor_threshold
 
 
 def audit_market_layer_causality(bundle: dict[str, Any], check_steps: int = 4) -> list[str]:
@@ -129,6 +130,18 @@
             if key not in ext_piv_map or abs(ext_piv_map[key][0] - sp["price"]) > 1e-6:
                 mismatches.append(f"Pivot mutated at cut={cut}: {sp}")
 
+        # 2b. 多次测试价区：同一阈值与 ATR 下，截至截面日已确认的触点与价区几何量不随未来K线改变
+        tz_frac = atr_pct(sub_hist)
+        tz_atr = tz_frac * float(sub_hist[-1]["close"])
+        tz_start = sub_hist[-min(len(sub_hist), STRUCT_DAYS)]["day"]
+        for tz_thr in (thr_sub, minor_threshold(tz_frac, thr_sub)):
+            if tz_thr is None:
+                continue
+            if confirmed_geometry(sub_hist, tz_thr, tz_atr, tz_start, cut_day) != confirmed_geometry(
+                ext_hist, tz_thr, tz_atr, tz_start, cut_day
+            ):
+                mismatches.append(f"Tested zones mutated at cut={cut} thr={tz_thr:.4f}")
+
         sub_ind = compute_all_indicators(sub)
         ext_ind = compute_all_indicators(ext_corrupted)
         if abs((sub_ind["series"]["atr14"][-1] or 0.0) - (ext_ind["series"]["atr14"][cut - 1] or 0.0)) > 1e-9:
@@ -279,6 +292,7 @@
             "nadaraya_watson",
             "structure_layer_pivots",
             "structure_and_scenarios",
+            "tested_zones",
             "candlestick_patterns",
             "smc_structures",
             "conformal_forecast",
@@ -824,7 +838,9 @@
             "extra_context": {"vix": None, "earnings_date": None, "is_st": st_at_t, "is_ipo_no_limit": ipo_at_t},
             "provenance": {**base_provenance, "missing_closed_session": None, "stale_sessions": 0},
         }
-        rep = analyze_bundle(sub_bundle, sub_request, include_replay=False, include_forecast=False, include_market_layer=False)
+        rep = analyze_bundle(
+            sub_bundle, sub_request, include_replay=False, include_forecast=False, include_market_layer=False, include_tested_zones=False
+        )
         decisions += 1
         action = rep["meta_labeling_decision"]["action"]
         actions_seen[action] = actions_seen.get(action, 0) + 1
```

```diff
--- a/kline-structure-chart/scripts/render_structure_chart.py
+++ b/kline-structure-chart/scripts/render_structure_chart.py
@@ -94,7 +94,7 @@
 
 def datalen_for(days: int) -> int:
     """拉取日K根数：结构层固定 HISTORY_BARS 根；展示 N 根时多拉 250 根，让主图最左侧也有 MA250。"""
-    return max(HISTORY_BARS, days + 250)
+    return max(HISTORY_BARS + 1, days + 250)
 
 
 def fetch(url: str, encoding: str = "utf-8") -> str:
@@ -1520,6 +1520,7 @@
         "recent_gaps": gaps,
         "scenario": scenario,
         "projections": projections,
+        "tested_zones": layer["tested_zones"],
         "intraday": None
         if not active_intra
         else {
```

```diff
--- a/kline-structure-chart/tests/check_days_invariance.py
+++ b/kline-structure-chart/tests/check_days_invariance.py
@@ -19,7 +19,7 @@
 
 SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "render_structure_chart.py"
 SYMBOLS = ("sh000001", "sz399001", "sz399006", "sh000688", "sh000300")
-STRUCT_KEYS = ("wedge", "scenario", "fib_levels", "key_levels", "recent_gaps", "swing", "structure_window", "projections")
+STRUCT_KEYS = ("wedge", "scenario", "fib_levels", "key_levels", "recent_gaps", "swing", "structure_window", "projections", "tested_zones")
 STRUCT_BLOCKS = ("3_wedge_and_levels", "4_three_projections", "5_structure_verdict")
 
 
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: PASS（26 passed；`test_decisions_do_not_depend_on_tested_zones` 约 1 秒，不标 `slow`）。

Run: `uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40`
Expected: `runs=420 failing=0 days=[60, 120, 200, 250, 300, 500]`（`STRUCT_KEYS` 已含 `tested_zones`）。

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过（参考：302 passed）。`result.json` 此时还没有新字段，五个键路径快照不变；`backtest.json` 的 `modules_audited` 是列表值，键路径快照也不变。

- [ ] **Step 5: 提交**

```bash
git add kline-structure-chart/scripts/structure.py \
  kline-structure-chart/scripts/market_structure.py \
  kline-structure-chart/scripts/analysis.py \
  kline-structure-chart/scripts/evaluation.py \
  kline-structure-chart/scripts/render_structure_chart.py \
  kline-structure-chart/tests/check_days_invariance.py \
  kline-structure-chart/tests/test_tested_zones.py
git commit -m "$(cat <<'EOF'
feat(tested-zones): 接入结构层、因果审计与不变性检查，决策路径不读取

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `result.json` 投影、schema 与四步裁剪链

**Files:**
- Modify: `kline-structure-chart/scripts/contract_v6.py`（投影 `structure.tested_zones`；`_trim_to_size_budget` 改为四步）
- Modify: `kline-structure-chart/assets/analysis.schema.json`（`structure.tested_zones`，`experimental`）
- Modify: `kline-structure-chart/tests/test_v6_contract.py`（裁剪测试改为四步）
- Modify: `kline-structure-chart/tests/fixtures/contract/result_v6_keypaths_{600519_plain,600519_equity_backtest,00700_hk,nvda_us,000001_index}.json`（重新生成）
- Modify: `kline-structure-chart/references/output-fields.md`（体积预算与裁剪顺序）

**Interfaces:**
- Consumes: Task 4 的 `report["mathematical_indicators"]["structure_levels"]["tested_zones"]`。
- Produces: `result["structure"]["tested_zones"]`（形状同 Task 3 的返回值，裁剪后次级别可能少了 `BROKEN` 价区或 `summary_cn`）；`_trim_to_size_budget(result: dict, target_prices: Callable[[], list[float]]) -> None`；`quality.notes` 的裁剪说明格式为 `result.json 超出体积预算，已依次裁剪：{步骤；步骤}；其余字段未裁剪`，仍超出时追加 `；裁剪后仍超出预算`。Task 6~8 读这个字段。

裁剪顺序（规格第 8.3 节，D4 推荐项 a）：① 去掉基准浪型的备选计数 → ② 基准关键价位每侧只保留离收盘最近的 5 档 → ③ 去掉次级别已破位的价区（连同指向它们的变化）→ ④ 去掉次级别价区的 `summary_cn`。每步之后检查一次；①② 之后重算 `quotable_prices.benchmark.prices`，③④ 之后用 `target_prices()` 按裁剪后的内容重算 `quotable_prices.prices`。主级别价区与其余字段不裁。实测：录制配置里只有腾讯（00700.HK，截至 2026-10-15）超出，做完 ①② 为 81912 字节。

- [ ] **Step 1: 改写裁剪测试**

`test_v6_result_trims_benchmark_summary_in_spec_order` 改名为 `test_v6_result_trims_in_spec_order`：先放开预算拿到未裁剪的结果作对照，再每次把预算设为上一步结果的字节数 − 1，恰好多做一步，逐步检查四步的内容、备注、两份可引用价位，以及不回写内部报告。

```diff
--- a/kline-structure-chart/tests/test_v6_contract.py
+++ b/kline-structure-chart/tests/test_v6_contract.py
@@ -686,17 +686,32 @@
     return {k: v for k, v in obj.items() if k not in keys}
 
 
-def _assert_only_benchmark_summary_differs(trimmed: dict, base: dict) -> None:
-    """裁剪只能动基准结构摘要的 wave_counts / key_levels、基准可引用价位与 quality.notes。"""
-    for key in V6_TOP - {"market_context", "quality", "quotable_prices"}:
+def _assert_only_trimmed_fields_differ(trimmed: dict, base: dict) -> None:
+    """裁剪只能动：基准结构摘要的 wave_counts / key_levels、次级别多次测试价区（已破位价区及指向它们的变化、
+    价区的 summary_cn）、两份可引用价位清单与 quality.notes。"""
+    for key in V6_TOP - {"market_context", "quality", "quotable_prices", "structure"}:
         assert trimmed[key] == base[key], f"{key} 被裁剪改动"
     assert _without(trimmed["quality"], "notes") == _without(base["quality"], "notes")
     assert trimmed["quality"]["notes"][: len(base["quality"]["notes"])] == base["quality"]["notes"]
-    assert _without(trimmed["quotable_prices"], "benchmark") == _without(base["quotable_prices"], "benchmark")
+    assert _without(trimmed["quotable_prices"], "benchmark", "prices") == _without(base["quotable_prices"], "benchmark", "prices")
     assert _without(trimmed["market_context"], "structure") == _without(base["market_context"], "structure")
     assert _without(trimmed["market_context"]["structure"], "wave_counts", "key_levels") == _without(
         base["market_context"]["structure"], "wave_counts", "key_levels"
     )
+    assert _without(trimmed["structure"], "tested_zones") == _without(base["structure"], "tested_zones")
+    t_tz, b_tz = trimmed["structure"]["tested_zones"], base["structure"]["tested_zones"]
+    assert _without(t_tz, "scales", "changes") == _without(b_tz, "scales", "changes")
+    assert t_tz["scales"][0] == b_tz["scales"][0]  # 主级别不裁
+    t_minor, b_minor = t_tz["scales"][1], b_tz["scales"][1]
+    assert _without(t_minor, "zones") == _without(b_minor, "zones")
+    kept = {z["zone_id"]: z for z in t_minor["zones"]}
+    for z in b_minor["zones"]:
+        if z["zone_id"] in kept:
+            assert _without(kept[z["zone_id"]], "summary_cn") == _without(z, "summary_cn")
+        else:
+            assert z["state"] == "BROKEN", z["zone_id"]
+    removed = {z["zone_id"] for z in b_minor["zones"]} - set(kept)
+    assert t_tz["changes"] == [c for c in b_tz["changes"] if c["object_id"] not in removed]
 
 
 def _assert_benchmark_prices_consistent(result: dict) -> None:
@@ -707,8 +722,17 @@
     assert bench["prices"] == wl["prices"]
 
 
-def test_v6_result_trims_benchmark_summary_in_spec_order(replay_recorded, tmp_path: Path, monkeypatch) -> None:
-    """规格 3.4：超出体积预算时先去掉基准备选计数，再截短基准关键价位；标的字段不动。只调低预算，不改行情。"""
+def _assert_target_prices_consistent(result: dict, recent_bars: list[dict]) -> None:
+    import contract_v6
+    from price_text import collect_price_whitelist
+
+    sections = {k: result[k] for k in contract_v6._QUOTABLE_SECTIONS}
+    wl = collect_price_whitelist(sections, result["quotable_prices"]["price_decimals"], recent_bars)
+    assert result["quotable_prices"]["prices"] == wl["prices"]
+
+
+def test_v6_result_trims_in_spec_order(replay_recorded, tmp_path: Path, monkeypatch) -> None:
+    """超出体积预算时依次裁剪：基准备选计数 → 基准关键价位 → 次级别已破位价区 → 次级别价区摘要。只调预算，不改行情。"""
     import contract_v6
     from contracts import validate_report
 
@@ -718,48 +742,57 @@
     on_disk = Path(rep["rendered_paths"]["result_json"]).read_bytes()
     files = json.loads(on_disk)["files"]
     build = lambda: contract_v6.build_result(rep, files)  # noqa: E731
-
-    # 未超预算：与落盘文件逐字节相同
+    bars = rep["series_export"]["bars"]
+
+    # 默认预算下与落盘文件逐字节相同；放开预算得到未裁剪的结果，作为每一步的对照
+    assert _result_bytes(build()) == on_disk
+    monkeypatch.setattr(contract_v6, "RESULT_SIZE_BUDGET_BYTES", 10**9)
     base = build()
-    assert _result_bytes(base) == on_disk
-    s0 = base["market_context"]["structure"]
+    s0, tz0 = base["market_context"]["structure"], base["structure"]["tested_zones"]
     assert [w["role"] for w in s0["wave_counts"]] == ["主计数", "备选计数"]
-    assert max(len(s0["key_levels"]["supports"]), len(s0["key_levels"]["resistances"])) > keep
-
-    # 第一步：只去掉备选计数就够，关键价位保持原样
-    monkeypatch.setattr(contract_v6, "RESULT_SIZE_BUDGET_BYTES", len(on_disk) - 1)
-    r1 = build()
-    s1 = r1["market_context"]["structure"]
-    assert s1["wave_counts"] == s0["wave_counts"][:1]
-    assert s1["key_levels"] == s0["key_levels"]
-    assert len(_result_bytes(r1)) <= len(on_disk) - 1
-    assert len(r1["quality"]["notes"]) == len(base["quality"]["notes"]) + 1
+    assert max(len(v) for v in s0["key_levels"].values()) > keep
+    broken = {z["zone_id"] for z in tz0["scales"][1]["zones"] if z.get("state") == "BROKEN"}
+    assert broken, "这份录制行情的次级别应有已破位价区，第三步才不是空转"
+
+    # 每次把预算设为上一步结果的字节数 − 1：恰好多做一步
+    results, size = [], len(_result_bytes(base))
+    for _ in range(4):
+        monkeypatch.setattr(contract_v6, "RESULT_SIZE_BUDGET_BYTES", size - 1)
+        r = build()
+        assert len(_result_bytes(r)) <= size - 1
+        size = len(_result_bytes(r))
+        results.append(r)
+    r1, r2, r3, r4 = results
+    assert r1["market_context"]["structure"]["wave_counts"] == s0["wave_counts"][:1]
+    assert r1["market_context"]["structure"]["key_levels"] == s0["key_levels"]
+    assert r1["structure"]["tested_zones"] == tz0
     assert "备选计数" in r1["quality"]["notes"][-1] and "关键价位" not in r1["quality"]["notes"][-1]
-
-    # 第二步：仍超预算时每侧只保留最近的 keep 个关键价位
-    monkeypatch.setattr(contract_v6, "RESULT_SIZE_BUDGET_BYTES", len(_result_bytes(r1)) - 1)
-    r2 = build()
-    s2 = r2["market_context"]["structure"]
-    assert s2["wave_counts"] == s0["wave_counts"][:1]
-    assert s2["key_levels"] == {side: levels[:keep] for side, levels in s0["key_levels"].items()}
-    assert len(_result_bytes(r2)) <= len(_result_bytes(r1)) - 1
-    assert "备选计数" in r2["quality"]["notes"][-1] and "关键价位" in r2["quality"]["notes"][-1]
-
-    # 两步都做完仍超预算：不再裁别的字段，如实注明
+    assert r2["market_context"]["structure"]["key_levels"] == {k: v[:keep] for k, v in s0["key_levels"].items()}
+    assert r2["structure"]["tested_zones"] == tz0
+    assert "关键价位" in r2["quality"]["notes"][-1] and "已破位" not in r2["quality"]["notes"][-1]
+    assert not broken & {z["zone_id"] for z in r3["structure"]["tested_zones"]["scales"][1]["zones"]}
+    assert all("summary_cn" in z for z in r3["structure"]["tested_zones"]["scales"][1]["zones"] if "touches" in z)
+    assert "已破位" in r3["quality"]["notes"][-1] and "文字摘要" not in r3["quality"]["notes"][-1]
+    assert not any("summary_cn" in z for z in r4["structure"]["tested_zones"]["scales"][1]["zones"])
+    assert "文字摘要" in r4["quality"]["notes"][-1]
+
+    # 四步都做完仍超预算：不再裁别的字段，如实注明
     monkeypatch.setattr(contract_v6, "RESULT_SIZE_BUDGET_BYTES", 1)
-    r3 = build()
-    assert r3["market_context"]["structure"] == s2
-    assert "仍超出" in r3["quality"]["notes"][-1]
-
-    for trimmed in (r1, r2, r3):
+    r5 = build()
+    assert _without(r5, "quality") == _without(r4, "quality") and "仍超出" in r5["quality"]["notes"][-1]
+
+    for trimmed in (r1, r2, r3, r4, r5):
         validate_report(trimmed)
-        _assert_only_benchmark_summary_differs(trimmed, base)
+        _assert_only_trimmed_fields_differ(trimmed, base)
         _assert_benchmark_prices_consistent(trimmed)
-        assert set(trimmed["quotable_prices"]["benchmark"]["prices"]) <= set(base["quotable_prices"]["benchmark"]["prices"])
-    # 截掉的关键价位不再可引用（备选计数的价位在这份行情里同时出现在关键价位中，第一步不减少价位）
+        _assert_target_prices_consistent(trimmed, bars)
+        assert set(trimmed["quotable_prices"]["prices"]) <= set(base["quotable_prices"]["prices"])
+    # 截掉的基准关键价位不再可引用
     assert len(r2["quotable_prices"]["benchmark"]["prices"]) < len(base["quotable_prices"]["benchmark"]["prices"])
 
-    # 裁剪不回写内部报告：恢复预算后与未裁剪结果逐字节相同
+    # 裁剪不回写内部报告：放开预算后与第一次未裁剪的结果相同，恢复默认预算后与落盘文件逐字节相同
+    monkeypatch.setattr(contract_v6, "RESULT_SIZE_BUDGET_BYTES", 10**9)
+    assert build() == base
     monkeypatch.undo()
     assert _result_bytes(build()) == on_disk
 
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_v6_contract.py -k trims_in_spec_order -q`
Expected: FAIL（`KeyError: 'tested_zones'`：`result.json` 的 `structure` 里还没有这个字段）。

- [ ] **Step 3: 投影、schema 与裁剪链**

```diff
--- a/kline-structure-chart/scripts/contract_v6.py
+++ b/kline-structure-chart/scripts/contract_v6.py
@@ -6,7 +6,7 @@
 from __future__ import annotations
 
 import json
-from typing import Any
+from typing import Any, Callable
 
 from market_rules import MARKET_SESSIONS
 from price_text import collect_price_whitelist
@@ -21,7 +21,7 @@
 _SCENARIO_DRAW_KEYS = ("waypoints_offset", "step_labels", "eta_bars")
 _SCENARIO_DROP_KEYS = (*_SCENARIO_DRAW_KEYS, "target_within_horizon")
 _QUOTABLE_SECTIONS = ("structure", "indicators", "scenarios", "plan", "volatility_range")
-# result.json 落盘（UTF-8、indent=2）的体积预算；超出时只裁剪基准结构摘要，见 _trim_to_size_budget
+# result.json 落盘（UTF-8、indent=2）的体积预算；超出时按顺序裁剪基准结构摘要与次级别价区，见 _trim_to_size_budget
 RESULT_SIZE_BUDGET_BYTES = 80 * 1024
 BENCH_KEY_LEVELS_KEEP = 5  # 裁剪关键价位时每侧保留离基准收盘最近的档数
 
@@ -148,6 +148,7 @@
             "patterns": [{k: v for k, v in p.items() if k != "invalidated"} for p in report["algebraic_patterns_detected"]],
             "smc": report["smc_structures"],
             "volume_profile": {k: v for k, v in report["volume_profile"].items() if k != "bins"},
+            "tested_zones": sl.get("tested_zones"),
         },
         "indicators": {k: mi[k] for k in ("trend", "momentum", "volatility", "qlib_factors")},
         "scenarios": {
@@ -205,7 +206,14 @@
         )
         bench_wl = {k: bw[k] for k in ("price_decimals", "tolerance", "prices")}
     result["quotable_prices"]["benchmark"] = bench_wl
-    _trim_to_size_budget(result)
+
+    def target_prices() -> list[float]:  # 裁剪多次测试价区后按裁剪后的内容重算标的可引用价位
+        secs = {k: result[k] for k in _QUOTABLE_SECTIONS}
+        if self_basis:
+            secs["cycle"] = ml_fields["cycle"]
+        return collect_price_whitelist(secs, int(meta.get("price_decimals") or 2), report["series_export"]["bars"])["prices"]
+
+    _trim_to_size_budget(result, target_prices)
     return result
 
 
@@ -214,39 +222,81 @@
     return len(json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"))
 
 
-def _trim_to_size_budget(result: dict[str, Any]) -> None:
-    """超出体积预算时按规格 3.4 的顺序裁剪基准结构摘要：先去掉备选浪型计数，再截短关键价位。
-
-    只动 market_context.structure 的 wave_counts / key_levels、随之重算的 quotable_prices.benchmark 与
-    quality.notes；标的自身的字段不裁。新建对象替换，不回写内部报告。
+def _refresh_benchmark_prices(result: dict[str, Any]) -> None:
+    bench = result["quotable_prices"]["benchmark"]
+    if bench is not None:
+        bw = collect_price_whitelist(
+            {"structure": result["market_context"]["structure"], "cycle": result["market_context"]["cycle"]}, bench["price_decimals"], []
+        )
+        result["quotable_prices"]["benchmark"] = {**bench, "prices": bw["prices"]}
+
+
+def _trim_bench_wave_counts(result: dict[str, Any]) -> None:
+    s = result["market_context"]["structure"]
+    result["market_context"]["structure"] = {
+        **s, "wave_counts": [w for w in s["wave_counts"] if w.get("role") == "主计数"] or s["wave_counts"][:1]
+    }
+    _refresh_benchmark_prices(result)
+
+
+def _trim_bench_key_levels(result: dict[str, Any]) -> None:
+    s = result["market_context"]["structure"]
+    result["market_context"]["structure"] = {**s, "key_levels": {k: v[:BENCH_KEY_LEVELS_KEEP] for k, v in s["key_levels"].items()}}
+    _refresh_benchmark_prices(result)
+
+
+def _map_minor_zones(result: dict[str, Any], fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> dict[str, Any]:
+    tz = result["structure"]["tested_zones"]
+    scales = [{**s, "zones": fn(s["zones"])} if s["scale"] == "MINOR" else s for s in tz["scales"]]
+    return {**tz, "scales": scales}
+
+
+def _trim_minor_broken_zones(result: dict[str, Any]) -> None:
+    tz = result["structure"]["tested_zones"]
+    gone = {z["zone_id"] for s in tz["scales"] if s["scale"] == "MINOR" for z in s["zones"] if z.get("state") == "BROKEN"}
+    new_tz = _map_minor_zones(result, lambda zones: [z for z in zones if z["zone_id"] not in gone])
+    new_tz["changes"] = [c for c in tz["changes"] if c["object_id"] not in gone]
+    result["structure"] = {**result["structure"], "tested_zones": new_tz}
+
+
+def _trim_minor_zone_summaries(result: dict[str, Any]) -> None:
+    new_tz = _map_minor_zones(result, lambda zones: [{k: v for k, v in z.items() if k != "summary_cn"} for z in zones])
+    result["structure"] = {**result["structure"], "tested_zones": new_tz}
+
+
+def _trim_to_size_budget(result: dict[str, Any], target_prices: Callable[[], list[float]]) -> None:
+    """超出体积预算时按顺序裁剪，每步之后检查一次：
+
+    1~2. v6 规格 3.4：基准结构摘要先去掉备选浪型计数，再把关键价位每侧截到离收盘最近的几档；
+    3~4. 多次测试价区规格 8.3：去掉次级别已破位的价区（连同指向它们的变化），再去掉次级别价区的 summary_cn。
+
+    每步之后重算受影响的可引用价位（target_prices() 按裁剪后的内容重算标的价位），并在 quality.notes 写明裁了什么。
+    主级别价区与其余字段不裁。新建对象替换，不回写内部报告。
     """
     budget = RESULT_SIZE_BUDGET_BYTES
-    structure = result["market_context"]["structure"]
-    if structure is None or _result_size(result) <= budget:
+    if _result_size(result) <= budget:
+        return
+    steps: list[tuple[str, Callable[[dict[str, Any]], None], bool]] = []
+    if result["market_context"]["structure"] is not None:
+        steps += [
+            ("去掉基准浪型的备选计数", _trim_bench_wave_counts, False),
+            ("基准关键价位每侧只保留离收盘最近的几档", _trim_bench_key_levels, False),
+        ]
+    if result["structure"].get("tested_zones") is not None:
+        steps += [
+            ("去掉次级别已破位的多次测试价区", _trim_minor_broken_zones, True),
+            ("去掉次级别多次测试价区的文字摘要", _trim_minor_zone_summaries, True),
+        ]
+    if not steps:
         return
     base_notes = list(result["quality"].get("notes") or [])
-    steps = (
-        (
-            "去掉基准浪型的备选计数",
-            lambda s: {**s, "wave_counts": [w for w in s["wave_counts"] if w.get("role") == "主计数"] or s["wave_counts"][:1]},
-        ),
-        (
-            "基准关键价位每侧只保留离收盘最近的几档",
-            lambda s: {**s, "key_levels": {k: v[:BENCH_KEY_LEVELS_KEEP] for k, v in s["key_levels"].items()}},
-        ),
-    )
     done: list[str] = []
-    for label, trim in steps:
-        structure = trim(structure)
+    for label, trim, touches_target in steps:
+        trim(result)
         done.append(label)
-        result["market_context"]["structure"] = structure
-        bench = result["quotable_prices"]["benchmark"]
-        if bench is not None:
-            bw = collect_price_whitelist(
-                {"structure": structure, "cycle": result["market_context"]["cycle"]}, bench["price_decimals"], []
-            )
-            result["quotable_prices"]["benchmark"] = {**bench, "prices": bw["prices"]}
-        note = f"result.json 超出体积预算，已裁剪大盘结构摘要（{'；'.join(done)}）；标的自身的字段未裁剪"
+        if touches_target:
+            result["quotable_prices"] = {**result["quotable_prices"], "prices": target_prices()}
+        note = f"result.json 超出体积预算，已依次裁剪：{'；'.join(done)}；其余字段未裁剪"
         result["quality"]["notes"] = [*base_notes, note]
         if _result_size(result) <= budget:
             return
```

```diff
--- a/kline-structure-chart/assets/analysis.schema.json
+++ b/kline-structure-chart/assets/analysis.schema.json
@@ -127,6 +127,30 @@
             "vah_price": { "type": ["number", "null"], "x-stability": "stable" },
             "val_price": { "type": ["number", "null"], "x-stability": "stable" }
           }
+        },
+        "tested_zones": {
+          "type": ["object", "null"], "x-stability": "experimental",
+          "description": "多次测试价区：两个级别上动态识别的 N 重底 / N 重顶、头肩与平台，以及与上一根已收盘K线相比的变化；只做描述，不参与评分与决策",
+          "required": ["atr_abs", "atr_basis", "prev_bar_day", "scales", "changes"],
+          "properties": {
+            "atr_abs": { "type": "number", "x-stability": "experimental" },
+            "atr_basis": { "type": "string", "enum": ["STRUCTURE_ATR20"], "x-stability": "experimental" },
+            "prev_bar_day": { "type": ["string", "null"], "x-stability": "experimental" },
+            "scales": {
+              "type": "array", "x-stability": "experimental",
+              "items": {
+                "type": "object",
+                "required": ["scale", "scale_cn", "zigzag_threshold_pct", "skipped_reason", "zones", "head_shoulders", "platform"],
+                "properties": {
+                  "scale": { "type": "string", "enum": ["MAJOR", "MINOR"] },
+                  "zones": { "type": "array", "items": { "type": "object", "required": ["zone_id"] } },
+                  "head_shoulders": { "type": "array" },
+                  "platform": { "type": ["object", "null"] }
+                }
+              }
+            },
+            "changes": { "type": "array", "x-stability": "experimental" }
+          }
         }
       }
     },
```

- [ ] **Step 4: 重新生成五个键路径快照并检查**

Run: `UPDATE_V6_KEYPATHS=1 uv run --directory kline-structure-chart pytest tests/test_v6_contract.py -k keypaths_match_snapshot -q`
Expected: 6 failed（测试改写快照后按约定以失败结束；`backtest_v6_keypaths_600519.json` 内容不变也会报失败）。

检查只新增了 `structure.tested_zones` 下的键路径、没有删除任何键路径：

```bash
for n in 600519_plain 600519_equity_backtest 00700_hk nvda_us 000001_index; do
  f=kline-structure-chart/tests/fixtures/contract/result_v6_keypaths_$n.json
  python3 - "$f" <<'EOF'
import json, subprocess, sys
f = sys.argv[1]
old = set(json.loads(subprocess.run(["git", "show", f"HEAD:{f}"], capture_output=True, text=True, check=True).stdout))
new = set(json.load(open(f)))
added, removed = new - old, old - new
assert not removed, removed
assert added and all(k.startswith("structure.tested_zones") for k in added), sorted(added)[:5]
print(f, "added", len(added))
EOF
done
git diff --stat kline-structure-chart/tests/fixtures/contract/
```

Expected: 五个文件各新增 53~81 条键路径（参考：000001_index 53、00700_hk 61、600519_equity_backtest 53、600519_plain 53、nvda_us 81），`removed` 为空；`backtest_v6_keypaths_600519.json` 没有变化。

Run: `uv run --directory kline-structure-chart pytest tests/test_v6_contract.py -q`
Expected: PASS（不带 `UPDATE_V6_KEYPATHS`）。

- [ ] **Step 5: 字段字典里的体积预算**

`kline-structure-chart/references/output-fields.md`：把

```text
6. `result.json` 落盘（`indent=2`）不超过 80 KB。
```

改为

```markdown
6. `result.json` 落盘（`indent=2`）不超过 80 KB；超出时的裁剪顺序见 `market_context` 一节的"体积预算"。
```

`kline-structure-chart/references/output-fields.md`：把

```text
体积预算：五个录制配置下三个新字段（含基准价位清单）完整输出，`result.json` 落盘为 59005~63636 字节，在 80 KB（81920 字节）预算内；联网行情的K线与价位更多，体积会更大（仓库内 `300308.SZ` 示例为 71773 字节）。运行期在投影末尾（`contract_v6.build_result`）按落盘的序列化方式计算 UTF-8 字节数，超出预算时只裁剪 `market_context.structure`，按下列顺序做到回到预算内为止：

1. `wave_counts` 只保留主计数，去掉备选计数。
2. `key_levels` 的 `supports` 与 `resistances` 各只保留离基准收盘最近的 5 个（`BENCH_KEY_LEVELS_KEEP`）。
```

改为

```markdown
体积预算：运行期在投影末尾（`contract_v6.build_result`）按落盘的序列化方式计算 UTF-8 字节数，超出 80 KB（81920 字节）时按下列顺序裁剪，每做一步重新计算一次，回到预算内为止。加入 `structure.tested_zones` 后，录制行情截至 2026-10-15 的配置里只有腾讯（00700.HK）超出预算，做完前两步为 81912 字节；按历史截面估算，约 5% 的运行会触发裁剪。

1. 基准 `market_context.structure.wave_counts` 只保留主计数，去掉备选计数。
2. 基准 `market_context.structure.key_levels` 的 `supports` 与 `resistances` 各只保留离基准收盘最近的 5 个（`BENCH_KEY_LEVELS_KEEP`）。
3. 去掉 `structure.tested_zones` 次级别已破位（`BROKEN`）的价区，连同 `changes` 中指向它们的条目。
4. 去掉 `structure.tested_zones` 次级别价区的 `summary_cn`。
```

`kline-structure-chart/references/output-fields.md`：把

```text
裁剪发生时：`quality.notes` 追加一句说明裁掉了什么；`quotable_prices.benchmark.prices` 按裁剪后的摘要重新收集，被裁掉的基准价位不再可引用；标的自身的字段、`market_context.cycle` 与 `market_context.alignment` 不裁剪，评级、否决、计划价位与回测不受影响；PNG 与 `chart_data.json` 不变。两步做完仍超出预算时不再裁剪其他字段，照常输出，并在同一条备注里写明"裁剪后仍超出预算"。
```

改为

```markdown
裁剪发生时：`quality.notes` 追加一句说明依次裁掉了什么；第 1、2 步之后按裁剪后的摘要重新收集 `quotable_prices.benchmark.prices`，第 3、4 步之后按裁剪后的内容重新收集 `quotable_prices.prices`，被裁掉的价位不再可引用；主级别价区、标的自身的其余字段、`market_context.cycle` 与 `market_context.alignment` 不裁剪，评级、否决、计划价位与回测不受影响；PNG 与 `chart_data.json` 不变。四步做完仍超出预算时不再裁剪其他字段，照常输出，并在同一条备注里写明"裁剪后仍超出预算"。
```

- [ ] **Step 6: 运行**

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过（参考：302 passed）。

- [ ] **Step 7: 提交**

```bash
git add kline-structure-chart/scripts/contract_v6.py \
  kline-structure-chart/assets/analysis.schema.json \
  kline-structure-chart/tests/test_v6_contract.py \
  kline-structure-chart/tests/fixtures/contract/ \
  kline-structure-chart/references/output-fields.md
git commit -m "$(cat <<'EOF'
feat(contract): result.json 新增 structure.tested_zones 与次级别价区裁剪

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 报告第 5 节"多次测试价区"、禁用说法与回复契约

**Files:**
- Modify: `kline-structure-chart/scripts/render_report_md.py`（新函数 `_tested_zone_lines`；第 5 节末尾与第 6 节各加一处）
- Modify: `kline-structure-chart/scripts/lint_response.py`（`_ZONE_NAME`、`_TOUCH_COUNT_PHRASES` 与检查循环）
- Modify: `kline-structure-chart/SKILL.md`（回复契约第 5 条）
- Modify: `kline-structure-chart/tests/test_tested_zones.py`（追加任务 6 的测试）

**Interfaces:**
- Consumes: `result["structure"]["tested_zones"]`（Task 5）；报告里已有的 `price(v) -> str` 格式化函数；`price_text.collect_price_whitelist`；`lint_response.lint_agent_response(text, report) -> list[str]`。
- Produces: `render_report_md._tested_zone_lines(tz: dict | None, price: Callable[[Any], str]) -> list[str]`（`tz` 为空时返回 `[]`）；`lint_response` 对"测试次数越多……""越测越……""三重底更可靠 / 必破 / 易破""几重底 + 概率 / 成功率"等说法报错，句内有否定词（"不代表""不能说"等）时放行。

报告小节的顺序：标题行"**多次测试价区**（触点数只是计数，不代表支撑或压力更强）"→ 每个级别逐条列价区 `summary_cn`（裁掉摘要时退化为"名称（状态）：价区 下沿~上沿，失效价 X。"）、盘中标注、平台、头肩 → "与上一交易日（日期）相比："逐条列 `changes[].detail_cn`，无变化时写"与上一交易日相比无变化。"。次级别跳过时写 `skipped_reason`；没有价区时写"近 120 日没有满足条件的价区"。

- [ ] **Step 1: 写测试**

在 `kline-structure-chart/tests/test_tested_zones.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 任务 6：报告第 5 节与回复校验
# ---------------------------------------------------------------------------

_LINT_STUB = {
    "quality": {"status": "OK"}, "verdict": {"risk_veto": False}, "plan": {}, "scenarios": {}, "structure": {},
    "meta": {"asset_type": "equity"}, "market_context": {},
}


def _section(tz: dict | None) -> str:
    from render_report_md import _tested_zone_lines

    return "\n".join(_tested_zone_lines(tz, lambda v: "—" if v is None else f"{float(v):.2f}"))


def _without_minor_summaries(tz: dict) -> dict:
    """与 contract_v6 裁剪第四步相同：去掉次级别价区的 summary_cn。"""
    return {**tz, "scales": [
        {**s, "zones": [{k: v for k, v in z.items() if k != "summary_cn"} for z in s["zones"]]} if s["scale"] == "MINOR" else s
        for s in tz["scales"]
    ]}


def test_report_section_lists_zones_platform_and_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    tz = _tz(CASE, "2026-11-17")
    md = _section(tz)
    assert md.startswith("**多次测试价区**（触点数只是计数，不代表支撑或压力更强）")
    assert f"- 主级别（ZigZag {_scale(tz, 'MAJOR')['zigzag_threshold_pct']:.2f}%）：{_zone(tz, 'MAJOR', 'MAJOR-' + SUP)['summary_cn']}" in md
    assert _scale(tz, "MAJOR")["platform"]["summary_cn"] in md
    assert "- 与上一交易日（2026-11-16）相比：" in md
    assert "  - 主级别：三重底的未确认低点更新为 2026-11-17 3565.59" in md
    assert "与主级别双底（首触点 2026-10-23）相同。" in _section(_tz(CASE, "2026-11-13"))
    assert _section(None) == ""
    monkeypatch.setattr(__import__("tested_zones"), "minor_threshold", lambda atr_frac, major_thr: None)
    assert "- 次级别：次级别阈值" in _section(_tz(CASE, "2026-11-17"))


def test_report_section_quotes_only_whitelisted_prices_across_history() -> None:
    """逐截面生成第 5 节的价区小节（含盘中标注、裁掉次级别摘要后的写法），每个价格都必须能在可引用价位里找到。"""
    from lint_response import lint_agent_response
    from price_text import collect_price_whitelist
    from tested_zones import compute_tested_zones

    checked = 0
    for rel in (SH, MOUTAI, BYD, "yahoo_daily/NVDA.json", "yahoo_daily/0700.HK.json"):
        bars = _bars(rel)
        for cut in range(450, len(bars) + 1, 15):
            sub = bars[:cut]
            live = bars[cut] if cut < len(bars) and cut % 2 == 0 else None
            tz = compute_tested_zones(sub, live)
            for variant in (tz, _without_minor_summaries(tz)):
                wl = collect_price_whitelist({"structure": {"tested_zones": variant}}, 2, sub)
                report = {**_LINT_STUB, "quotable_prices": {"prices": wl["prices"], "tolerance": wl["tolerance"]}}
                assert lint_agent_response(_section(variant) + "\n不构成投资建议", report) == [], (rel, sub[-1]["day"])
            checked += 1
    assert checked > 100


def test_lint_blocks_touch_count_strength_claims_but_allows_negations() -> None:
    from lint_response import lint_agent_response

    report = {**_LINT_STUB, "quotable_prices": {"prices": [], "tolerance": 0.01}}

    def flagged(text: str) -> bool:
        return any("测试次数只是计数" in e for e in lint_agent_response(text + "不构成投资建议。", report))

    for text in ("测试次数越多，支撑越强。", "这个平台越测越牢。", "三重底更可靠。", "4 次测试的支撑区必破。",
                 "三重底的成功率很高。", "多次测试后更坚固。", "双顶易破。"):
        assert flagged(text), text
    for text in ("测试次数只是计数，不代表支撑更强。", "三重底（第 3 次测试进行中，未确认）。", "不能说三重底更可靠。",
                 "多次测试价区先说主级别。", "4 次测试的支撑区（价区守住、未过颈线）。", "波段双底结构（看多，2026-10-09）。"):
        assert not flagged(text), text
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: FAIL（任务 6 的 3 个用例失败：`ImportError: cannot import name '_tested_zone_lines'`，以及禁用说法没有被拦截）。

- [ ] **Step 3: 应用补丁**

```diff
--- a/kline-structure-chart/scripts/render_report_md.py
+++ b/kline-structure-chart/scripts/render_report_md.py
@@ -7,7 +7,7 @@
 """
 from __future__ import annotations
 
-from typing import Any
+from typing import Any, Callable
 
 from market_rules import price_decimals
 
@@ -95,6 +95,46 @@
                 lines.append(f"- {c['label_cn']}{tag}：对照的大盘拐点日为 {c['benchmark_days'][0]} 与 {c['benchmark_days'][1]}。")
         lines += ["- 以上只是对已发生走势的描述，不构成交易信号，也不改变风控结论。", ""]
     return lines
+
+
+def _tested_zone_lines(tz: dict[str, Any] | None, price: Callable[[Any], str]) -> list[str]:
+    """第 5 节"多次测试价区"小节：主级别在前，再列平台、头肩与"与上一交易日相比"的变化。数字全部取自 tested_zones。"""
+    if not tz:
+        return []
+    lines = ["**多次测试价区**（触点数只是计数，不代表支撑或压力更强）", ""]
+    first_day = {z["zone_id"]: z["touches"][0]["day"] for s in tz["scales"] for z in s["zones"] if "touches" in z}
+    names = {z["zone_id"]: z["name_cn"].split("（")[0] for s in tz["scales"] for z in s["zones"] if "name_cn" in z}
+    for sc in tz["scales"]:
+        label = f"{sc['scale_cn']}（ZigZag {sc['zigzag_threshold_pct']:.2f}%）"
+        if sc["skipped_reason"]:
+            lines.append(f"- {sc['scale_cn']}：{sc['skipped_reason']}。")
+            continue
+        if not sc["zones"]:
+            lines.append(f"- {label}：近 120 日没有满足条件的价区。")
+        for z in sc["zones"]:
+            if "same_as_zone_id" in z:
+                ref = z["same_as_zone_id"]
+                lines.append(f"- {label}：与主级别{names[ref]}（首触点 {first_day[ref]}）相同。")
+                continue
+            text = z.get("summary_cn") or (
+                f"{z['name_cn']}（{z['state_cn']}）：价区 {price(z['zone_low_price'])}~{price(z['zone_high_price'])}，"
+                f"失效价 {price(z['break_price'])}。"
+            )
+            lines.append(f"- {label}：{text}")
+            if z.get("intraday_note"):
+                lines.append(f"  - {z['intraday_note']}")
+        if sc["platform"]:
+            lines.append(f"- {label}：{sc['platform']['summary_cn']}")
+        for hs in sc["head_shoulders"]:
+            lines.append(f"- {label}：{hs['summary_cn']}")
+    if tz["prev_bar_day"]:
+        scale_cn = {s["scale"]: s["scale_cn"] for s in tz["scales"]}
+        if tz["changes"]:
+            lines.append(f"- 与上一交易日（{tz['prev_bar_day']}）相比：")
+            lines += [f"  - {scale_cn[c['scale']]}：{c['detail_cn']}" for c in tz["changes"]]
+        else:
+            lines.append("- 与上一交易日相比无变化。")
+    return lines + [""]
 
 
 def render_report_markdown(
@@ -396,6 +436,7 @@
         f"- 下方支撑（由近到远）：{'；'.join(sup) if sup else '无'}",
         "",
     ]
+    lines += _tested_zone_lines(st.get("tested_zones"), price)
 
     # 6. 形态与信号证据
     lines += ["## 6. 形态与信号证据（公式可复核）", ""]
@@ -412,6 +453,8 @@
             f"- {p['name_cn']}（{DIRECTION_CN.get(p['direction'], p['direction'])}，{p.get('confirmed_at') or p.get('observed_at')}）"
         ]
         formula_lines.append(f"- {p['name_cn']}：`{p['formula_verified']}`")
+    if st.get("tested_zones") and any(p.get("category") != "candlestick" and ("双底" in p["name_cn"] or "双顶" in p["name_cn"]) for p in patterns[:6]):
+        lines += ["- 波段双底 / 双顶只比较最近两个已确认低点（高点）；同一区域的全部触点见第 5 节多次测试价区。"]
     for s in verdict.get("strategy_signals") or []:
         if s.get("direction") != "NEUTRAL":
             lines += [f"- 策略 {s['name_cn']} → {s['state']}"]
```

```diff
--- a/kline-structure-chart/scripts/lint_response.py
+++ b/kline-structure-chart/scripts/lint_response.py
@@ -43,6 +43,14 @@
         r"(?:约|大约|为|是|达|达到|高达|超|超过|高于|接近|有|在|>=?|:|：|=)?\s*\d+(?:\.\d+)?\s*%"
     ),
     re.compile(r"[一二三四五六七八九]成(?:把握|胜算|概率|机会|以上)?(?=[^\d]|$)"),
+)
+# 多次测试价区：测试次数只是计数，不得说成支撑压力更强、更可靠、更容易破，也不得给"几重底"配概率
+_ZONE_NAME = r"(?:多次测试|\d+\s*次测试|[二三四五六七八九两]次测试|[三四五多]重[底顶]|双[底顶])"
+_TOUCH_COUNT_PHRASES = (
+    re.compile(r"(?:测试|触及|触碰|回踩|探底|冲高)次数越多"),
+    re.compile(r"越测越(?:强|弱|稳|牢|坚固|可靠|容易|难)"),
+    re.compile(_ZONE_NAME + r"[^。；\n]{0,8}?(?:更可靠|更坚固|更牢固|更强|更有效|必破|易破|必守|守得住|撑得住)"),
+    re.compile(_ZONE_NAME + r"[^。；\n]{0,6}?(?:概率|成功率|胜率|准确率)"),
 )
 _NEGATION_RE = re.compile(r"不是|不叫|不代表|并非|不得|不能|严禁|禁止|不构成|不等于|不要|不应|不宜|而非|不含|没有")
 # 唯一允许的概率类数字：回测成交胜率，同一句必须出现「回测」并给出 95% 区间
@@ -188,6 +196,12 @@
             before_hit = text[sent_start : hit.start()]
             if not _NEGATION_RE.search(hit.group(0)) and not _NEGATION_RE.search(before_hit):
                 errors.append(f"检测到违规表述: {hit.group(0)!r}（禁止将规则分称为胜率/概率/置信度，禁止将成交分布称为真实持仓筹码）")
+                break
+    for pat in _TOUCH_COUNT_PHRASES:
+        for hit in pat.finditer(text):
+            sent_start = max((sm.end() for sm in _SENTENCE_SPLIT_RE.finditer(text, 0, hit.start())), default=0)
+            if not _NEGATION_RE.search(text[sent_start : hit.start()]):
+                errors.append(f"检测到违规表述: {hit.group(0)!r}（测试次数只是计数，不得说成支撑压力更强、更可靠或更容易破）")
                 break
     for hit in dict.fromkeys(_unvalidated_probability_hits(text)):
         errors.append(
```

- [ ] **Step 4: 回复契约**

`kline-structure-chart/SKILL.md`：在下面这一行之后

```text
   - 形态只引用 `structure.patterns` 里的 `name_cn` 和 `formula_verified`。
```

插入

```markdown
   - 多次测试价区（`structure.tested_zones`，实验字段，报告第 5 节末尾）：先说主级别，再说次级别；未确认触点必须说"未确认"；失效价引用 `break_price`；测试次数只是描述，不得说成支撑更强、更可靠或更容易破（`lint_response.py` 拦截这类说法）。
```

- [ ] **Step 5: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py tests/test_docs_contract.py tests/test_report_and_metadata.py -q`
Expected: PASS（`test_example_reply_passes_lint_and_counterexample_is_rejected` 仍通过：示例回复里没有新的禁用说法）。

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过（参考：305 passed）。

- [ ] **Step 6: 提交**

```bash
git add kline-structure-chart/scripts/render_report_md.py \
  kline-structure-chart/scripts/lint_response.py \
  kline-structure-chart/SKILL.md \
  kline-structure-chart/tests/test_tested_zones.py
git commit -m "$(cat <<'EOF'
feat(report): 报告第 5 节多次测试价区与回复校验

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 看板图层与 PNG 价区

**Files:**
- Modify: `kline-structure-chart/scripts/render_structure_chart.py`（主级别价区横带与颈线、触点编号、标签徽标）
- Modify: `kline-structure-chart/scripts/render_html.py`（图层开关、主次级别按钮、数据适配与绘制）
- Modify: `kline-structure-chart/tests/test_tested_zones.py`（追加任务 7 的测试）

**Interfaces:**
- Consumes: PNG 读结构层结果 `layer["tested_zones"]`（Task 4，与 JSON 同一份）；看板读 `result.json` 的 `structure.tested_zones`（Task 5），通过 `REPORT.tested_zones` 交给画布。PNG 用已有的 `x_of`、`yp`、`dashed_hline`、`draw_callout_badge`、`kline_obstacles`、`occupied_badges`、`peer_anchors`；看板用已有的 `clipToMainPlot`、`xOfBar`、`yOfPrice`、`UP_COL`、`DN_COL`。
- Produces: 看板图层键 `zones`（时间回放时禁用）、按钮 `#zoneScaleBtn`（文字"价区: 主级别" / "价区: 次级别"）、变量 `zoneScale`。

呈现规则（规格第 9 节，D2 推荐项 a）：PNG 只画主级别已筛选的价区——半透明横带从首个触点画到预判区分隔线，颈线虚线，触点按序编号（已确认实心、未确认空心；触点被截断时从第 2 个起接着真实序号），标签"名称 · 状态"用与拐点徽标相同的避让逻辑放置；支撑用上涨色、压力用下跌色，随市场配色切换。看板默认显示主级别，可切换次级别，另画失效价虚线，只在最新截面显示。

- [ ] **Step 1: 写测试**

在 `kline-structure-chart/tests/test_tested_zones.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 任务 7：PNG 与看板
# ---------------------------------------------------------------------------


def test_png_and_dashboard_carry_tested_zones(replay_recorded, tmp_path) -> None:
    import re
    import shutil
    import subprocess
    from pathlib import Path

    from analyze_and_forecast import run_pipeline
    from conftest import load_v6
    from render_html import render_html

    req = {"symbol": "000001.SH", "market": "CN", "asset_type": "index", "display_bars": 120, "structure_bars": 120, "horizon_bars": 15}
    rep = run_pipeline(req, output_dir=tmp_path)
    result, chart = load_v6(rep)
    tz = result["structure"]["tested_zones"]
    assert any("touches" in z for z in _scale(tz, "MAJOR")["zones"]), "主级别应有价区，PNG 才有东西可画"
    assert Path(rep["rendered_paths"]["preview_png"]).stat().st_size > 0
    html_text = render_html(result, chart, tmp_path / "dash.html").read_text(encoding="utf-8")
    assert 'data-layer="zones"' in html_text and 'id="zoneScaleBtn"' in html_text and "lk === 'zones'" in html_text
    node = shutil.which("node")
    if node is None:
        pytest.skip("本机没有 node，无法执行看板脚本")
    # 与 test_v6_html_adapter_joins_scenario_paths_by_id 同样截取看板的数据适配段，确认价区原样交给画布
    payload = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html_text, re.S).group(1)
    start = html_text.index("const DATA = JSON.parse")
    end = html_text.index("\n  };", html_text.index("rule_scenarios:", start)) + len("\n  };")
    script = (
        f"const document = {{ getElementById: () => ({{ textContent: {json.dumps(payload)} }}) }};\n"
        + html_text[start:end]
        + "\nconsole.log(JSON.stringify(REPORT.tested_zones));\n"
    )
    script_file = tmp_path / "zones_check.js"
    script_file.write_text(script, encoding="utf-8")
    out = subprocess.run([node, str(script_file)], capture_output=True, text=True, timeout=30, check=True).stdout
    assert json.loads(out) == tz
```

- [ ] **Step 2: 运行，确认失败**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -k png_and_dashboard -q`
Expected: FAIL（`assert 'data-layer="zones"' in html_text` 失败：看板还没有这个图层）。

- [ ] **Step 3: 应用补丁**

注意 `render_html.py` 的页面模板在 Python f-string 里，补丁中的花括号已经成对写（`{{`、`}}`），照抄即可。

```diff
--- a/kline-structure-chart/scripts/render_structure_chart.py
+++ b/kline-structure-chart/scripts/render_structure_chart.py
@@ -936,6 +936,19 @@
                 dashed_hline(draw, left, div_x, y_fib, (*SCEN, 140))
                 right_gutter_tags.append((y_fib, f"Fib {flvl['ratio']:.3f}{fibs.get('kind', '')} {fp(fp_val)}", SCEN))
 
+    # ---------------- 绘制多次测试价区的横带与颈线（只画主级别已筛选的价区；次级别只在 HTML 中切换查看） ----------------
+    tz_major = next((s["zones"] for s in (layer.get("tested_zones") or {}).get("scales", []) if s["scale"] == "MAJOR"), [])
+    tz_major = [z for z in tz_major if "touches" in z]  # 与主级别相同的次级别引用不会出现在主级别，这里只是防御
+    view_pos = {b["day"]: i for i, b in enumerate(view)}
+    for z in tz_major:
+        zc = UP if z["side"] == "SUPPORT" else DOWN
+        x0 = x_of(view_pos.get(z["touches"][0]["day"], 0))
+        y_hi = yp(min(max(z["zone_high_price"], pmin), pmax))
+        y_lo = yp(min(max(z["zone_low_price"], pmin), pmax))
+        draw.rectangle((x0, min(y_hi, y_lo), div_x, max(y_hi, y_lo) + 1), fill=(*zc, 34))
+        if pmin <= z["neckline_price"] <= pmax:
+            dashed_hline(draw, x0, div_x, yp(z["neckline_price"]), (*zc, 150))
+
     # ---------------- 绘制真实 K 线与盘中 K 线 ----------------
     kline_obstacles: list[tuple[int, int, int, int]] = []
     for i, bar in enumerate(plot):
@@ -952,6 +965,19 @@
         half = max(4.0, step * 0.42) if not bar["complete"] else bar_half
         draw.rectangle((x - half, body_top, x + half, body_bot), fill=color)
         kline_obstacles.append((int(x - half - 2), int(y_high - 4), int(x + half + 2), int(y_low + 4)))
+
+    # ---------------- 多次测试价区的触点编号（画在K线之上；未确认触点空心；标签在拐点徽标之后画，同样避让） ----------------
+    for z in tz_major:
+        zc = UP if z["side"] == "SUPPORT" else DOWN
+        offset = z["touch_count"] - len(z["touches"])  # 触点被截断时，第 2 个起的编号接着真实序号
+        for k, t in enumerate(z["touches"]):
+            if t["day"] not in view_pos or not pmin <= t["price"] <= pmax:
+                continue
+            tx, ty = x_of(view_pos[t["day"]]), yp(t["price"])
+            draw.ellipse((tx - 7, ty - 7, tx + 7, ty + 7), fill=(*zc, 235) if t["confirmed"] else PANEL_BG, outline=zc, width=1.6)
+            num = str(1 if k == 0 else k + 1 + offset)
+            nw, nh = text_size(draw, num, f_tiny_sm)
+            draw.text((tx - nw / 2, ty - nh / 2 - 1), num, font=f_tiny_sm, fill=PANEL_BG if t["confirmed"] else zc)
 
     # ---------------- 绘制均线（在K线之上叠绘带暗晕的平滑曲线，清晰不糊） ----------------
     for n, color in MA.items():
@@ -1201,6 +1227,28 @@
             f_tiny,
             m["color"],
             m["side"],
+            left,
+            div_x - 6,
+            top + 36,
+            main_bottom - 6,
+            occupied_badges,
+            obstacles=kline_obstacles,
+            peer_anchors=peer_anchors,
+        )
+    # 多次测试价区的标签（名称 · 状态）：锚在首个触点，与拐点徽标同样避让K线与已画徽标
+    for z in tz_major:
+        t0 = z["touches"][0]
+        if t0["day"] not in view_pos or not pmin <= t0["price"] <= pmax:
+            continue
+        support = z["side"] == "SUPPORT"
+        draw_callout_badge(
+            draw,
+            x_of(view_pos[t0["day"]]),
+            yp(t0["price"]),
+            f"{z['name_cn'].split('（')[0]} · {z['state_cn']}",
+            f_tiny,
+            UP if support else DOWN,
+            "below" if support else "above",
             left,
             div_x - 6,
             top + 36,
```

```diff
--- a/kline-structure-chart/scripts/render_html.py
+++ b/kline-structure-chart/scripts/render_html.py
@@ -306,6 +306,7 @@
     <label class="layer-pill"><input type="checkbox" data-layer="scenarios" checked /> 多情景路径(虚线=待触发)</label>
     <label class="layer-pill"><input type="checkbox" data-layer="barriers" checked /> Triple-Barrier 止盈止损框</label>
     <label class="layer-pill"><input type="checkbox" data-layer="structure" checked /> 核平滑与楔形/波段轨道</label>
+    <label class="layer-pill"><input type="checkbox" data-layer="zones" checked /> 多次测试价区</label>
     <label class="layer-pill"><input type="checkbox" data-layer="poc" checked /> 成交分布近似 POC</label>
     <label class="layer-pill"><input type="checkbox" data-layer="smc" checked /> SMC 订单块/FVG缺口</label>
     <label class="layer-pill"><input type="checkbox" data-layer="ma" checked /> 均线簇(MA20/60/120/250)</label>
@@ -313,6 +314,7 @@
   <div style="display:flex;gap:8px;align-items:center;">
     <button class="btn" id="subchartModeBtn" type="button">副图: 成交量 &amp; RVOL</button>
     <button class="btn" id="viewModeBtn" type="button">视图: 标的主图</button>
+    <button class="btn" id="zoneScaleBtn" type="button">价区: 主级别</button>
     <button class="btn" id="exportPngBtn" type="button">导出当前视图为高清 PNG</button>
   </div>
 </div>
@@ -399,6 +401,7 @@
     volume_profile: Object.assign({{}}, ST.volume_profile, {{ bins: CH.volume_profile_bins || [] }}),
     smc_structures: ST.smc,
     algebraic_patterns_detected: ST.patterns || [],
+    tested_zones: ST.tested_zones || null,
     strategy_signals: V.strategy_signals || [],
     rule_scenarios: {{ scenarios: (SC.active || []).map(function(s) {{ return Object.assign({{}}, s, SCP_BY_ID[s.id] || {{}}); }}) }},
   }};
@@ -632,9 +635,11 @@
     structure: true,
     poc: true,
     smc: true,
+    zones: true,
     ma: true
   }};
   let showRsMode = false;
+  let zoneScale = 'MAJOR';  // 多次测试价区图层显示的级别
   let activeReplayIdx = replays.length; // replays.length === live/latest view
   // 横轴密度固定：最新截面与历史回放显示同样根数（回放快照窗口长度），拖动滑块时K线宽度不变
   const VIEW_BARS = (replays.length && replays[0].bars_window && replays[0].bars_window.length >= 20)
@@ -658,7 +663,7 @@
     const isLive = activeReplayIdx === replays.length;
     document.querySelectorAll('#layerControls input[type=checkbox]').forEach(function(cb) {{
       const lk = cb.getAttribute('data-layer');
-      const liveOnly = (lk === 'scenarios' || lk === 'barriers' || lk === 'poc' || lk === 'smc');
+      const liveOnly = (lk === 'scenarios' || lk === 'barriers' || lk === 'poc' || lk === 'smc' || lk === 'zones');
       if (liveOnly) {{
         cb.disabled = !isLive;
         cb.parentElement.style.opacity = isLive ? '1' : '0.45';
@@ -778,6 +783,12 @@
     drawChart();
   }});
 
+  document.getElementById('zoneScaleBtn').addEventListener('click', function(ev) {{
+    zoneScale = zoneScale === 'MAJOR' ? 'MINOR' : 'MAJOR';
+    ev.target.textContent = zoneScale === 'MAJOR' ? '价区: 主级别' : '价区: 次级别';
+    drawChart();
+  }});
+
   document.getElementById('exportPngBtn').addEventListener('click', function() {{
     const link = document.createElement('a');
     link.download = meta.symbol.replace(/[^A-Za-z0-9_-]/g, '_') + '_forecast_dashboard.png';
@@ -1003,6 +1014,63 @@
       ctx.fillStyle = col;
       ctx.fillRect(x - barHalf, topY, barHalf * 2, bodyH);
     }});
+
+    // Layer: 多次测试价区（只在最新截面显示；按钮切换主级别 / 次级别）
+    const TZ = REPORT.tested_zones;
+    if (layers.zones && TZ && activeReplayIdx === replays.length) {{
+      const sc = (TZ.scales || []).find(function(s) {{ return s.scale === zoneScale; }});
+      const pos = {{}};
+      activeBars.forEach(function(b, bi) {{ pos[b.day] = bi; }});
+      clipToMainPlot();
+      ((sc && sc.zones) || []).forEach(function(z) {{
+        if (!z.touches) return;  // 与主级别相同的次级别价区只有 zone_id 与 same_as_zone_id
+        const col = z.side === 'SUPPORT' ? UP_COL : DN_COL;
+        let x0 = padL;
+        for (let bi = 0; bi < activeBars.length; bi++) {{
+          if (activeBars[bi].day >= z.touches[0].day) {{ x0 = xOfBar(bi); break; }}
+        }}
+        const yHi = yOfPrice(z.zone_high_price), yLo = yOfPrice(z.zone_low_price);
+        ctx.globalAlpha = 0.16;
+        ctx.fillStyle = col;
+        ctx.fillRect(x0, Math.min(yHi, yLo), (padL + histW) - x0, Math.max(3, Math.abs(yLo - yHi)));
+        ctx.globalAlpha = 1;
+        ctx.strokeStyle = col;
+        ctx.lineWidth = 1;
+        ctx.setLineDash([5, 4]);
+        [z.neckline_price, z.break_price].forEach(function(p) {{
+          if (p === null || p === undefined) return;
+          const y = yOfPrice(p);
+          ctx.beginPath();
+          ctx.moveTo(x0, y);
+          ctx.lineTo(padL + histW, y);
+          ctx.stroke();
+        }});
+        ctx.setLineDash([]);
+        const offset = z.touch_count - z.touches.length;  // 触点被截断时，第 2 个起的编号接着真实序号
+        ctx.font = 'bold 10px sans-serif';
+        ctx.textAlign = 'center';
+        ctx.textBaseline = 'middle';
+        z.touches.forEach(function(t, ti) {{
+          if (pos[t.day] === undefined) return;
+          const x = xOfBar(pos[t.day]), y = yOfPrice(t.price);
+          ctx.beginPath();
+          ctx.arc(x, y, 7, 0, Math.PI * 2);
+          ctx.fillStyle = t.confirmed ? col : '#0e1420';
+          ctx.fill();
+          ctx.lineWidth = 1.5;
+          ctx.strokeStyle = col;
+          ctx.stroke();
+          ctx.fillStyle = t.confirmed ? '#0e1420' : col;
+          ctx.fillText(String(ti === 0 ? 1 : ti + 1 + offset), x, y);
+        }});
+        ctx.textAlign = 'left';
+        ctx.textBaseline = 'alphabetic';
+        ctx.font = '11px sans-serif';
+        ctx.fillStyle = col;
+        ctx.fillText(z.name_cn.split('（')[0] + ' · ' + z.state_cn, x0 + 4, Math.min(yHi, yLo) - 4);
+      }});
+      ctx.restore();
+    }}
 
     // Layer: Moving Averages
     if (layers.ma) {{
```

- [ ] **Step 4: 运行，确认通过**

Run: `uv run --directory kline-structure-chart pytest tests/test_tested_zones.py -q`
Expected: PASS（30 passed）。

Run: `uv run --directory kline-structure-chart pytest -n auto -m "not slow"`
Expected: 全部通过（参考：306 passed）。

- [ ] **Step 5: 生成产物，目视检查 PNG（AGENTS 第 4 节第 5 条）**

新建临时文件 `kline-structure-chart/tests/_gen_tz_art.py`（文件名不以 `test_` 开头，平时不会被收集）：

```python
"""一次性脚本：用录制行情生成价区目视检查用的产物。跑完即删，不提交。"""
from pathlib import Path

from analyze_and_forecast import main

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "tz"


def test_gen(replay_recorded) -> None:
    for market, symbol, asset_type in (("CN", "000001.SH", "index"), ("HK", "00700.HK", "equity"), ("US", "NVDA.US", "equity")):
        rc = main(["--market", market, "--symbol", symbol, "--asset-type", asset_type, "--as-of", "2026-10-15",
                   "--output-dir", str(OUT / symbol)])
        assert rc == 0, symbol
```

```bash
uv run --directory kline-structure-chart pytest tests/_gen_tz_art.py -q -p no:xdist
rm kline-structure-chart/tests/_gen_tz_art.py
ls -l kline-structure-chart/artifacts/tz/*/*_result.json kline-structure-chart/artifacts/tz/*/*_preview.png
```

Expected: 三个 `*_result.json` 都不超过 81920 字节（参考：上证指数约 76 KB；腾讯 79974 字节，`quality.notes` 带裁剪说明）。

逐个打开 `*_preview.png` 检查：
- 上证指数与腾讯（红涨绿跌）：支撑价区为红色、压力价区为绿色；英伟达（绿涨红跌）：支撑为绿色、压力为红色。
- 横带从首个触点画到预判区分隔线，颈线为同色虚线；触点圆圈编号依次递增，未确认触点为空心。
- 标签是带边框的徽标，不压在K线实体上，也不与拐点徽标、推演步骤标签重叠（腾讯 2026-10-15 有 5 个主级别价区，是最拥挤的情形）。

- [ ] **Step 6: 浏览器检查看板**

在 `.claude/launch.json`（不入库）的 `configurations` 里临时加一项静态服务：

```json
    {
      "name": "tested-zones-art",
      "runtimeExecutable": "python3",
      "runtimeArgs": ["-m", "http.server", "8799", "--bind", "127.0.0.1", "--directory", "kline-structure-chart/artifacts/tz"],
      "port": 8799
    }
```

用浏览器预览打开 `http://127.0.0.1:8799/000001.SH/` 下的 `*_dashboard.html`，再打开腾讯的看板，逐项检查：
- 控制台零报错。
- 图层区有"多次测试价区"开关，默认勾选；取消勾选后横带、编号、颈线与失效价虚线消失，重新勾选后恢复。
- 点"价区: 主级别"按钮，文字变为"价区: 次级别"，画面换成次级别价区；再点一次切回。
- 拖动时间回放滑块离开最新截面：该开关变灰不可点、价区不显示；拖回最新截面后恢复。
- 相对强弱视图切换与副图切换照常工作。

检查完删除这条临时配置与 `kline-structure-chart/artifacts/tz/`。

- [ ] **Step 7: 提交**

```bash
git add kline-structure-chart/scripts/render_structure_chart.py \
  kline-structure-chart/scripts/render_html.py \
  kline-structure-chart/tests/test_tested_zones.py
git commit -m "$(cat <<'EOF'
feat(render): 看板与 PNG 的多次测试价区图层

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 文档、示例与全量门禁

**Files:**
- Modify: `kline-structure-chart/references/math-patterns.md`（新增第 9 节）
- Modify: `kline-structure-chart/references/output-fields.md`（`structure` 表新增一行与 `structure.tested_zones` 小节）
- Modify: `kline-structure-chart/references/interpretation-guide.md`（新增第 14 节）
- Modify: `CHANGELOG.md`（`[Unreleased]`）
- Modify: `README.md`、`README.en.md`（输出文件表两行）
- Modify: `examples/`（三个回放示例与 `300308.SZ` 重新生成）、`examples/README.md`

**Interfaces:**
- Consumes: 前面各任务的全部输出。
- Produces: 无新代码接口。

- [ ] **Step 1: 文档**

`kline-structure-chart/references/math-patterns.md`：在文件末尾追加（与上一节之间空一行）

```markdown

## 9. 多次测试价区、头肩与平台（`scripts/tested_zones.py`）

本节是描述性计算：只用已收盘K线与 ZigZag 拐点，不参与规则分、评级、风控否决与计划价位。常量在规格中写定，未经调参，也未经有效性验证；触点数只是计数，不代表支撑或压力更强。

| 常量 | 取值 | 含义 |
| --- | --- | --- |
| `W_TOL_ATR` | 0.65 | 价区宽度上限（ATR 倍数）：全部触点的最高价与最低价之差，沿用双底"两低点差 ≤ 0.65×ATR" |
| `BREAK_ATR` | 0.5 | 破位距离：收盘越过已确认触点的边缘这么多个 ATR 才算破位，沿用情景库的 `MIN_TRIGGER_ATR` |
| `NECK_MIN_ATR` | 1.2 | 颈线最小高度（ATR 倍数），沿用双底"颈线 − 底 ≥ 1.2×ATR" |
| `FALSE_BREAK_BARS` | 3 | 破位后 3 根内收盘收回才算假破位 |
| `RECENT_BARS` | 20 | 近期窗口：K线下标 ≥ n − 20（n 为已收盘K线根数），即含最新一根在内的 20 根 |
| `MAX_ZONES_PER_SIDE` | 3 | 每级别每侧最多输出的价区数 |
| `HS_NECK_SLOPE_MAX` | 0.5 | 头肩两颈线点之差 ≤ 0.5 × 形态高度 |
| `MAX_TOUCHES` | 8 | 触点列表上限：超过时列第 1 个与最后 7 个，`touches_truncated = true` |
| `MAX_CHANGES` | 12 | 逐日变化最多输出条数，主级别优先 |

**ATR 与两个级别**：$\text{atr\_abs} = \text{atr\_pct}(hist, 20) \times$ 最新收盘价（结构层同一个 ATR，`atr_basis = "STRUCTURE_ATR20"`）。主级别 `MAJOR` 的 ZigZag 阈值就是结构层的 `zz_thr`；次级别 `MINOR` 为 $\min(4\%, \max(1.5\%, \lceil \text{ATR\%} / 0.5\% \rceil \times 0.5\%))$，不小于主级别阈值时跳过并写 `skipped_reason`。两个级别都在最近 420 根已收盘K线上跑 `structure.zigzag`，只取落在结构窗口（最近 120 根）内的拐点；ZigZag 末端尚未被反向走势确认的极值是未确认拐点。

**聚类（决定 N）**：每个级别、每一侧（支撑用低点，压力用高点）按时间顺序处理拐点。拐点 $p$ 可以并入一个已有价区，当且仅当并入后全部触点的最高价与最低价之差 ≤ 0.65 ATR，且该价区最后一个触点到 $p$ 之间没有收盘价越过已确认触点边缘 0.5 ATR 以上；有多个候选时选并入后宽度最小的，宽度相同选最后触点最近的，没有候选就新建价区。只输出触点数 ≥ 2、第一个触点已确认、颈线高度 ≥ 1.2 ATR 的价区。未确认触点每个价区至多一个，且一定是最后一个。

**几何量（支撑价区；压力镜像）**：

- `zone_low_price` / `zone_high_price`：已确认触点的最低价 / 最高价；`neckline_price`：首末触点之间的最高价；`height_abs` = 颈线 − `zone_low_price`。
- `break_price` = `zone_low_price` − 0.5 ATR（失效价，只由已确认触点决定，不随未确认的新低移动）；`measured_move_price` = 颈线 + 高度；`confirm_price` = 未确认低点 ×（1 + 本级别阈值），之后任一根最高价达到它，该触点即被确认。
- 名称：N = 2 为双底 / 双顶，N = 3 为三重底 / 三重顶，N ≥ 4 为"N 次测试的支撑区 / 压力区"；N 含未确认触点，此时名称带"（第 N 次测试进行中，未确认）"。`sequence`：`RISING` / `FALLING` / `MIXED`。
- 观察到的价格（触点、价区上下沿、颈线、头肩各点）与结构层拐点同口径保留小数；推算的价位（`break_price`、`confirm_price`、`measured_move_price`、`neckline_today_price`）按标的价位表取整。

**状态**（从最后一个已确认触点的下一根起扫描收盘价）：

| 状态 | 判定（支撑价区） |
| --- | --- |
| `BROKEN` | 收盘低于 `break_price`，此后没有收盘回到 `zone_low_price` 及以上；只在破位后 20 根内输出 |
| `FALSE_BREAK` | 收盘低于 `break_price` 后 3 根内收回，收回发生在最近 20 根内，之后没有再次破位 |
| `NECKLINE_CROSSED` | 没有破位，最新收盘高于颈线 |
| `TESTING` | 没有破位、未过颈线，有未确认触点 |
| `HOLDING` | 没有破位、未过颈线，没有未确认触点 |
| （不输出） | 破位后超过 3 根才收回 |

**筛选**：每个级别、每一侧取离最新收盘最近的未破位价区（`NEAREST_ACTIVE`），加上最后触点或状态变化落在最近 20 根内的价区（`RECENT`），合并去重后最多 3 个。次级别价区与某个已输出的主级别价区触点完全相同且没有未确认触点时，只输出 `{zone_id, same_as_zone_id}`。

**头肩底 / 头肩顶**：每个级别每侧只看结构窗口内最后三个同类拐点 $S_1, H, S_2$（$S_2$ 可未确认）。头肩底要求 $H < \min(S_1, S_2) - 0.65\,\text{ATR}$、$|S_1 - S_2| \le 0.65\,\text{ATR}$；颈线由 $S_1$–$H$ 与 $H$–$S_2$ 之间的两个最高价点连线并外推到今天；头部处的颈线高度 ≥ 1.2 ATR；两颈线点之差 ≤ 0.5 × 高度。状态：$S_2$ 之后收盘低于 $H - 0.5\,\text{ATR}$ 为 `FAILED`，否则最新收盘高于今日颈线为 `NECKLINE_CROSSED`，不高于为 `FORMING`。头肩顶镜像。

**平台**：同一级别已筛选的一个支撑价区与一个压力价区，两者状态都是 `TESTING` / `HOLDING` / `FALSE_BREAK`，首末触点的时间区间有交集，且压力价区下沿 − 支撑价区上沿 ≥ 1.2 ATR。多对满足时取两者末触点中较早者最近的一对。平台不单独判定状态，任一侧破位或越过颈线后即不再成立。

**逐日变化**：用截至上一根已收盘K线的数据按同样规则重算（阈值与 ATR 按那一天），按 `zone_id = {级别}-{SUPPORT|RESISTANCE}-{首触点日期}` 匹配价区，头肩按三个点的日期、平台按两侧 `zone_id` 匹配，输出 `changes[]`。级别阈值与上一天不同时，该级别的变化带 `threshold_changed = true`。

**盘中**：价区、头肩与平台只用已收盘K线计算；存在未收盘K线时只在价区上附 `intraday_note`（盘中越过失效价，或盘中正在测试该价区），不作确认。

因果性：`evaluation.run_causal_replay_audit` 的 `tested_zones` 项在每个截面、同一阈值与 ATR 下检查截至截面日已确认的触点与价区几何量不随未来K线改变。回测逐日回放与历史回放快照不计算价区。
```

`kline-structure-chart/references/output-fields.md`：在下面这一行之后

```text
| `structure.volume_profile` | Volume Profile 近似成交分布的 `poc_price`（成交最密集价位）、`vah_price` / `val_price`（70% 价值区上下沿），另有 `value_area_coverage`、`lookback_bars`、`approximation_method`、`note`；24 个价格桶明细在 `chart_data.json` | 价格 | 数据不足时三个价位可空 | stable | "成交密集区"，**不是真实持仓筹码** |
```

插入

```markdown
| `structure.tested_zones` | 多次测试价区：`atr_abs`、`atr_basis`、`prev_bar_day`；`scales[]` 依次为主级别 `MAJOR` 与次级别 `MINOR`，各含 `zigzag_threshold_pct`、`skipped_reason`、`zones[]`、`head_shoulders[]`、`platform`；`changes[]` 为与上一根已收盘K线相比的变化。明细见下表 | 对象 | 结构层不计算时为 `null`；级别跳过时 `skipped_reason` 非空、`zones` 为空 | experimental | 先说主级别再说次级别，按 `summary_cn` 原文转述；测试次数只是计数 |

### `structure.tested_zones`（多次测试价区）

公式与常量见 `references/math-patterns.md` 第 9 节。只做描述，规则分、评级、否决条件与计划价位不读取它；回测逐日回放与历史回放快照不计算它。

| 字段 | 含义 | 取值 / 单位 | 稳定级别 | 对外怎么说 |
| --- | --- | --- | --- | --- |
| `zones[].zone_id` | `{级别}-{SUPPORT\|RESISTANCE}-{首触点日期}`；次级别与主级别触点完全相同且没有未确认触点时，该项只有 `zone_id` 与 `same_as_zone_id` | 字符串 | experimental | 说"与主级别某价区相同" |
| `zones[].side` / `side_cn` / `name_cn` | 支撑或压力；名称为双底 / 三重底 / N 次测试的支撑区（压力侧镜像），有未确认触点时带"（第 N 次测试进行中，未确认）" | 枚举 / 字符串 | experimental | 原文转述名称 |
| `zones[].touch_count` / `confirmed_touch_count` | 触点数（含未确认）与已确认触点数 | 整数 | experimental | 只是计数，不代表支撑或压力更强 |
| `zones[].touches[]` / `touches_truncated` | `{day, price, confirmed, confirmed_at_day}`，最多 8 个；超过时列第 1 个与最后 7 个 | 对象数组 / 布尔 | experimental | 未确认触点必须说"未确认" |
| `zones[].zone_low_price` / `zone_high_price` / `neckline_price` | 已确认触点的最低价、最高价，首末触点之间的反向极值（颈线） | 价格 | experimental | — |
| `zones[].break_price` / `confirm_price` / `measured_move_price` | 失效价（已确认边缘外 0.5 ATR）、未确认触点的确认价（没有时为 `null`）、测量位（颈线 ± 高度） | 价格 | experimental | 失效价引用 `break_price`；测量位不是目标价 |
| `zones[].height_abs` / `height_atr` / `first_last_atr` | 形态高度及其 ATR 倍数、末触点减首触点（ATR 倍数） | 价格差 / 倍数 | experimental | 不是价位，不当价格引用 |
| `zones[].sequence` / `sequence_cn` | `RISING` 逐次抬高 / `FALLING` 逐次降低 / `MIXED` | 枚举 | experimental | — |
| `zones[].state` / `state_cn` / `state_since_day` | `TESTING` / `HOLDING` / `NECKLINE_CROSSED` / `FALSE_BREAK` / `BROKEN`（破位后 20 根内输出）；`TESTING` 与 `HOLDING` 的 `state_since_day` 为 `null` | 枚举 / 日期 | experimental | 按 `state_cn` 转述 |
| `zones[].selected_because[]` | `NEAREST_ACTIVE`（离最新收盘最近的未破位价区）/ `RECENT`（最后触点或状态变化在最近 20 根内） | 枚举数组 | experimental | — |
| `zones[].intraday_note` / `summary_cn` | 盘中标注（未收盘，不作确认；没有时为 `null`）与按状态生成的一句摘要；体积超出预算时次级别的 `summary_cn` 可能被裁掉 | 字符串 | experimental | 原文转述 |
| `head_shoulders[]` | 头肩底 / 头肩顶：`name_cn`、`left_shoulder` / `head` / `right_shoulder`（`{day, price}`）、`right_shoulder_confirmed`、`neckline_points`、`neckline_today_price`、`height_abs`、`measured_move_price`、`state`（`FORMING` / `NECKLINE_CROSSED` / `FAILED`）、`state_cn`、`summary_cn`；每级别每侧最多一个 | 对象数组 | experimental | 右肩未确认时必须说"未确认" |
| `platform` | 同级别一个支撑价区与一个压力价区组成的平台：`lower_price` / `upper_price`、`lower_zone_id` / `upper_zone_id`、`lower_touch_count` / `upper_touch_count`、`gap_atr`、`since_day`、`summary_cn`；不成立时为 `null` | 对象 | experimental | 组合描述，不单独判定状态 |
| `changes[]` | `scale`、`object_type`、`object_id`、`change`（`ZONE_NEW` / `TOUCH_ADDED` / `TOUCH_UPDATED` / `TOUCH_CONFIRMED` / `TOUCH_LEFT_ZONE` / `STATE_CHANGED` / `ZONE_DISSOLVED` / `HS_NEW` / `HS_STATE_CHANGED` / `HS_ENDED` / `PLATFORM_NEW` / `PLATFORM_CHANGED` / `PLATFORM_ENDED`）、`change_cn`、`from` / `to`、`reason`、`threshold_changed`、`detail_cn`；最多 12 条，主级别优先 | 对象数组 | experimental | 按 `detail_cn` 转述；`threshold_changed = true` 时说明是阈值变化后的重算，不说成结构变化 |
```

`kline-structure-chart/references/interpretation-guide.md`：在文件末尾追加（与上一节之间空一行）

```markdown

## 14. 多次测试价区（`structure.tested_zones`）

实验性描述字段，公式见 `references/math-patterns.md` 第 9 节：不参与规则分、评级、风控否决与计划价位，也不是买卖信号。

- **怎么读**：先说主级别，再说次级别；每个价区按 `summary_cn` 原文转述（名称、触点、价区、颈线、失效价），再说平台与头肩，最后说"与上一交易日相比"的变化（`changes[].detail_cn`）。
- **N 只是计数**：双底、三重底、"N 次测试的支撑区"只说明价格在这个区域被测试了几次，不代表支撑更强、更可靠或更容易破。
- **未确认触点**：名称带"第 N 次测试进行中，未确认"时必须说"未确认"，并给出确认价 `confirm_price`；它可能被之后更低（更高）的极值取代，价区也可能因此消失（`ZONE_DISSOLVED`）。
- **失效价**引用 `break_price`：它只由已确认触点决定，不随未确认的新低（新高）移动。
- **主次级别的 N 不同是正常的**：同一区域主级别是双底、次级别是 4 次测试，是 ZigZag 阈值不同，不是矛盾，两级并列说明即可。
- **阈值变化**：变化条目带 `threshold_changed = true` 时，按 `detail_cn` 转述"阈值由 x% 变为 y%，按新阈值重算"，不说成结构变化。

常见误读：

- 测量位 `measured_move_price` 是颈线加形态高度的几何测算位，不是目标价承诺。
- 平台是一个支撑价区与一个压力价区的组合描述，不单独判定状态；任一侧破位或越过颈线后平台即结束。
- `intraday_note` 只描述未收盘K线的位置，不作确认，价区不因盘中价改变。
- `structure.patterns` 里的"波段双底 / 双顶"只比较最近两个已确认低点（高点），与价区的 N 可以不同。
```

`CHANGELOG.md`：在下面这一行之后

```text
## [Unreleased]
```

插入

```markdown
### 新增（多次测试价区）

- **契约变更（新增实验字段，`x-stability: experimental`）**：`structure.tested_zones`。在主级别（结构层 ZigZag 阈值）与次级别（按 ATR% 向上取整到 0.5%，夹在 1.5%~4%）上把价格相近的拐点聚成价区，由数据决定是双底、三重底还是"N 次测试的支撑区"（压力侧镜像），给出状态、颈线、失效价与未确认触点的确认价；另识别头肩底 / 头肩顶与平台，并列出与上一根已收盘K线相比的变化。只做描述：规则分、评级、否决条件与计划价位不读取它（`test_decisions_do_not_depend_on_tested_zones` 守护），回测逐日回放与历史回放快照不计算它。
- **契约变更（`backtest.json`，不在 schema 约束内）**：`causal_replay_audit.modules_audited` 新增 `tested_zones` 项。
- `result.json` 体积裁剪在基准结构摘要两步之后新增两步：去掉次级别已破位的价区（连同指向它们的变化），再去掉次级别价区的 `summary_cn`；后两步之后重算 `quotable_prices.prices`。`quality.notes` 的裁剪说明改为"result.json 超出体积预算，已依次裁剪：…；其余字段未裁剪"。腾讯（00700.HK）截至 2026-10-15 的录制样例会触发前两步。
- `report.md` 第 5 节末尾新增"多次测试价区"小节，第 6 节在波段双底 / 双顶之后加一句说明；看板新增"多次测试价区"图层与主次级别切换按钮；PNG 画主级别价区的横带、触点编号与颈线。
- `lint_response.py` 拦截把测试次数说成支撑压力更强、更可靠、更容易破，或给"几重底"配概率的说法；`SKILL.md` 回复契约第 5 条同步。
- `tests/check_days_invariance.py` 检查 `tested_zones`；v4.1 命令行（`render_structure_chart.py`）取数下限由 420 根改为 421 根，上一交易日的对照也能在 420 根上重算。
- 新增录制 `tests/fixtures/recorded/sina_daily_cases/sh000001_2026-11-17.json`（`record_fixtures.py --only cases`，截止日固定）。
- `examples/` 四个示例按 `--as-of 2026-10-15` 重新生成，结果 JSON、报告、看板与 PNG 新增多次测试价区。
```

`README.md`：把

```text
数据说明、风控否决、结论、大盘、未来正常波动范围、风控计划、结构与条件推演、形态证据、回测、文件路径、免责声明。
```

改为

```markdown
数据说明、风控否决、结论、大盘、未来正常波动范围、风控计划、结构与条件推演（含多次测试价区）、形态证据、回测、文件路径、免责声明。
```

`README.md`：把

```text
| `*_dashboard.html` | 离线交互看板：8 个图层开关、
```

改为

```markdown
| `*_dashboard.html` | 离线交互看板：9 个图层开关（含多次测试价区，可切换主次级别）、
```

`README.en.md`：把

```text
risk plan, structure and conditional scenarios, pattern evidence,
```

改为

```markdown
risk plan, structure and conditional scenarios (including tested price zones), pattern evidence,
```

`README.en.md`：把

```text
| `*_dashboard.html` | Offline interactive dashboard: 8 layer toggles, 
```

改为

```markdown
| `*_dashboard.html` | Offline interactive dashboard: 9 layer toggles (including tested price zones, switchable between major and minor scale), 
```

- [ ] **Step 2: 文档契约测试**

Run: `uv run --directory kline-structure-chart pytest tests/test_docs_contract.py -q`
Expected: PASS（目录树、引用文件、无占位符、无不实宣称、字段字典覆盖顶层字段）。

- [ ] **Step 3: 重新生成三个回放示例**

参数与 `examples/README.md` 记录的命令一致（注意带 `--account-equity`，否则 `plan.long.position_sizing` 会变成"未提供账户净值"的写法，与已提交示例不同）。新建临时文件 `kline-structure-chart/tests/_regen_examples.py`：

```python
"""一次性脚本：用录制行情重新生成 examples/ 的三个回放示例（参数与 examples/README.md 记录的命令一致）。跑完即删，不提交。"""
from pathlib import Path

from analyze_and_forecast import main

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def test_regen(replay_recorded) -> None:
    for market, symbol, equity, extra in (
        ("CN", "600519.SH", "500000", ["--backtest"]),
        ("HK", "00700.HK", "500000", []),
        ("US", "NVDA.US", "100000", []),
    ):
        rc = main(["--market", market, "--symbol", symbol, "--asset-type", "equity", "--account-equity", equity,
                   "--as-of", "2026-10-15", "--output-dir", str(EXAMPLES / symbol), *extra])
        assert rc == 0, symbol
```

```bash
uv run --directory kline-structure-chart pytest tests/_regen_examples.py -q -p no:xdist
rm kline-structure-chart/tests/_regen_examples.py
```

- [ ] **Step 4: 重新生成 `300308.SZ`（需联网）**

```bash
uv run --directory kline-structure-chart python scripts/analyze_and_forecast.py --market CN --symbol 300308.SZ \
  --name 中际旭创 --asset-type equity --account-equity 500000 --as-of 2026-10-15 --output-dir ../examples/300308.SZ
```

取数失败时如实记录退出码，`git checkout examples/300308.SZ` 保留旧示例，并在提交说明里写明这一个示例未更新；不得用其他来源补数。

- [ ] **Step 5: 检查示例差异**

```bash
python3 - <<'EOF'
import json, os, subprocess
for s in ("600519.SH", "00700.HK", "NVDA.US", "300308.SZ"):
    f = f"examples/{s}/{s}_result.json"
    old = json.loads(subprocess.run(["git", "show", f"HEAD:{f}"], capture_output=True, text=True, check=True).stdout)
    new = json.load(open(f))
    assert new["verdict"] == old["verdict"], (s, "verdict")
    assert new["plan"] == old["plan"], (s, "plan")
    assert {k for k in new["structure"] if new["structure"][k] != old["structure"].get(k)} == {"tested_zones"}, s
    assert set(old["quotable_prices"]["prices"]) <= set(new["quotable_prices"]["prices"]), s
    print(s, os.path.getsize(f), "bytes;", [n for n in new["quality"]["notes"] if n not in old["quality"]["notes"]])
EOF
git diff --stat examples/
```

Expected:
- 四个示例的 `verdict` 与 `plan` 与已提交版本逐字段相同（规格第 13 节第 3 条），`structure` 只有 `tested_zones` 不同，旧的可引用价位全部保留。
- 参考体积（回放示例）：`600519.SH` 69745 字节、`00700.HK` 79974 字节、`NVDA.US` 77898 字节；只有腾讯新增一条裁剪备注（"去掉基准浪型的备选计数；基准关键价位每侧只保留离收盘最近的几档"）。
- 报告的差异只在第 5 节末尾新增的"多次测试价区"小节（腾讯另有"数据备注"里的裁剪说明）；看板与结构图 PNG 都有变化；`600519.SH_backtest.json` 只有 `causal_replay_audit.modules_audited` 多了 `tested_zones`。
- `300308.SZ` 联网取到的历史长度会随取数日期变化，样本外覆盖率审计等字段可能略有不同；只要 `verdict` 与 `plan` 不变即可接受，若它们变了，`git checkout examples/300308.SZ` 并在提交说明里写明。
- 若三个回放示例出现上述以外的差异，说明重生成参数与当初不一致或有决策被改动：先 `git checkout examples/` 还原，查明原因再做。

然后在 `examples/README.md` 的"- 生成时间：2026-11-16（……）"那一行之前插入：

```markdown
- 多次测试价区：`600519.SH`、`00700.HK`、`NVDA.US` 用录制行情、`300308.SZ` 联网，均按 `--as-of 2026-10-15` 重新生成。相对上一版只新增结果 JSON 的 `structure.tested_zones`（及其价位进入 `quotable_prices.prices`）、报告第 5 节"多次测试价区"小节、看板"多次测试价区"图层与结构图的价区横带；茅台 `*_backtest.json` 的因果审计新增 `tested_zones` 项。腾讯的 `result.json` 超出 80 KB，按裁剪顺序去掉了基准结构摘要的备选浪型计数与远端关键价位（见其 `quality.notes`）。评级、否决与计划价位不变。
```

`SKILL.md`"回复示例"一节引用的数字来自 `verdict` 与 `plan`，这两部分不变，无需改动。

- [ ] **Step 6: 全量门禁（AGENTS.md 第 3.3 节）**

```bash
uv run --directory kline-structure-chart pytest -v -n auto
uv run --directory kline-structure-chart python tests/check_days_invariance.py --data tests/fixtures/recorded/sina_daily --step 40
```

Expected: pytest 全部通过（参考：357 passed）；不变性检查 `runs=420 failing=0`。把两条命令的末尾输出原样保存，交付时附上。

- [ ] **Step 7: 提交**

```bash
git add kline-structure-chart/references/ \
  CHANGELOG.md \
  README.md \
  README.en.md \
  examples/
git commit -m "$(cat <<'EOF'
docs(tested-zones): 公式、字段字典、解读指南、版本记录与示例同步

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```


---

## 规格覆盖对照

| 规格章节 | 任务 |
| --- | --- |
| 2 通用量（ATR、两个级别、常量） | Task 2 |
| 3.1~3.7 聚类、未确认触点、几何量、命名、触点序列、状态、筛选 | Task 2（内部结果）、Task 3（输出与取整） |
| 3.8 文字摘要 | Task 3 |
| 4 头肩、5 平台 | Task 2（识别）、Task 3（输出字段与摘要） |
| 6 逐日变化 | Task 3 |
| 7 盘中标注 | Task 3 |
| 8.1 输出契约、8.2 可引用价位 | Task 5（投影与 schema）、Task 3（可引用价位测试） |
| 8.3 体积（D4 推荐项 a） | Task 3（≤ 32 KB 测试）、Task 5（四步裁剪链） |
| 9 呈现 | Task 6（报告、回复契约、禁用说法）、Task 7（看板、PNG） |
| 10 因果性、不变性与决策隔离 | Task 4 |
| 11.1 新增录制 | Task 1 |
| 11.2 固定样例 G1~G6 | Task 2（G2~G6）、Task 3（G1 与逐日变化） |
| 11.3 性质测试 | Task 2~7 |
| 12 同步清单 | Task 1、2、5、6、8 |
| 13 验收标准 | Task 4（决策隔离）、Task 7（产物、PNG 与浏览器检查）、Task 8（示例、全量门禁） |
