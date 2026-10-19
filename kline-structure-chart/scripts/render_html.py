from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from contracts import AnalysisReport, validate_report


def _safe_json_for_script(data: Any) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render_quality_banner(quality: dict[str, Any]) -> str:
    """服务端渲染数据质量横幅（全部字串经 html.escape），非 OK 状态为红色告警，盘中提示为灰色备注。"""
    status = str(quality.get("status") or "UNKNOWN")
    warnings = [str(w) for w in quality.get("warnings") or []]
    notes = [str(n) for n in quality.get("notes") or []]
    parts: list[str] = []
    if status != "OK" or warnings:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in warnings)
        parts.append(
            f'<div class="quality-banner quality-alert" role="alert" data-quality-status="{html.escape(status)}">'
            f"<strong>数据质量：{html.escape(status)}</strong>（数据源：{html.escape(str(quality.get('data_source')))}"
            f"；基准：{html.escape(str(quality.get('benchmark_source')))}）<ul>{items}</ul></div>"
        )
    if notes:
        items = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
        parts.append(f'<div class="quality-banner quality-note"><ul>{items}</ul></div>')
    return "\n".join(parts)


def render_html(report: AnalysisReport, output_path: Path) -> Path:
    """将已通过 Schema 校验的 AnalysisReport 渲染为零外部依赖、断网双击即开的单文件交互式 HTML 终端。"""
    validate_report(report)
    meta = report["meta"]
    horizon_n = int((report.get("future_projection_15d") or {}).get("horizon_bars") or 15)
    title_str = html.escape(f"{meta['name']} ({meta['symbol']}) · K线数学结构与{horizon_n}日保形预测终端")
    q_status = str((report.get("quality") or {}).get("status") or "OK")
    if q_status == "UNAVAILABLE":
        redacted_payload: dict[str, Any] = {
            "meta": meta,
            "quality": report.get("quality") or {},
            "reproducibility": report.get("reproducibility") or {},
            "series_export": {"bars": [], "pivots": []},
            "replay_snapshots": [],
        }
        safe_json = _safe_json_for_script(redacted_payload)
    else:
        safe_json = _safe_json_for_script(report)
    quality_banner = _render_quality_banner(report.get("quality") or {})

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title_str}</title>
<style>
  :root {{
    --bg: #0b0f17;
    --panel: #111827;
    --card: #161f30;
    --border: #2a364f;
    --text: #eef2f8;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --bull: #f24444;
    --bear: #10bc84;
    --warn: #facc15;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    line-height: 1.45;
    padding: 16px;
  }}
  .header-banner {{
    background: linear-gradient(135deg, #131c2e 0%, #1a263d 100%);
    border: 1px solid var(--border);
    border-left: 5px solid var(--accent);
    border-radius: 10px;
    padding: 14px 18px;
    margin-bottom: 14px;
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }}
  .header-title {{ font-size: 20px; font-weight: 700; letter-spacing: 0.3px; }}
  .header-sub {{ font-size: 13px; color: var(--muted); margin-top: 4px; }}
  .verdict-line {{
    width: 100%;
    padding-top: 8px;
    border-top: 1px solid rgba(255,255,255,0.08);
    font-size: 14px;
    color: #f8fafc;
  }}
  .badge {{
    display: inline-block;
    padding: 3px 9px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    background: rgba(56, 189, 248, 0.15);
    color: var(--accent);
    border: 1px solid rgba(56, 189, 248, 0.35);
    margin-right: 6px;
  }}
  .toolbar {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 14px;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    justify-content: space-between;
  }}
  .layer-group {{
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }}
  .layer-pill {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 12px;
    padding: 5px 10px;
    border-radius: 999px;
    background: var(--card);
    border: 1px solid var(--border);
    cursor: pointer;
    user-select: none;
  }}
  .layer-pill input {{ accent-color: var(--accent); cursor: pointer; }}
  .btn {{
    background: #1e293b;
    color: var(--text);
    border: 1px solid var(--accent);
    border-radius: 7px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }}
  .btn:hover {{ background: rgba(56, 189, 248, 0.2); }}
  .workspace {{
    display: grid;
    grid-template-columns: minmax(0, 2.35fr) minmax(320px, 1fr);
    gap: 14px;
  }}
  @media (max-width: 1100px) {{
    .workspace {{ grid-template-columns: 1fr; }}
  }}
  .chart-card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px;
    position: relative;
  }}
  canvas {{
    width: 100%;
    height: 620px;
    display: block;
    border-radius: 6px;
    background: #0b0f17;
    cursor: crosshair;
  }}
  .hover-bar {{
    min-height: 30px;
    font-size: 12px;
    color: #cbd5e1;
    padding: 6px 8px;
    background: var(--card);
    border-radius: 6px;
    margin-bottom: 8px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  .replay-box {{
    margin-top: 10px;
    padding: 10px 12px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 12px;
  }}
  .replay-box input[type=range] {{ flex: 1; accent-color: var(--accent); }}
  .side-col {{ display: flex; flex-direction: column; gap: 12px; }}
  .side-card {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 13px 15px;
  }}
  .side-card h3 {{
    font-size: 14px;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 8px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    padding-bottom: 5px;
  }}
  .kv-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 7px 10px;
    font-size: 12px;
  }}
  .kv-item span {{ color: var(--muted); display: block; font-size: 11px; }}
  .kv-item strong {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }}
  .evidence-list {{ list-style: none; font-size: 12px; display: flex; flex-direction: column; gap: 6px; }}
  .evidence-list li {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 8px;
  }}
  .formula-code {{
    display: block;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    color: #93c5fd;
    margin-top: 3px;
    word-break: break-all;
  }}
  .footer-note {{
    margin-top: 14px;
    font-size: 11px;
    color: var(--muted);
    text-align: center;
  }}
  .quality-banner {{
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 14px;
    font-size: 13px;
  }}
  .quality-banner ul {{ margin: 6px 0 0 18px; }}
  .quality-alert {{ background: #7f1d1d; border: 2px solid #ef4444; color: #fff; }}
  .quality-note {{ background: var(--card); border: 1px solid var(--border); color: var(--muted); }}
</style>
</head>
<body>
{quality_banner}

<div class="header-banner" id="headerBanner">
  <div>
    <div class="header-title" id="headerTitle"></div>
    <div class="header-sub" id="headerSub"></div>
  </div>
  <div id="headerBadges"></div>
  <div class="verdict-line" id="headerVerdict"></div>
</div>

<div class="toolbar">
  <div class="layer-group" id="layerControls">
    <label class="layer-pill"><input type="checkbox" data-layer="conformal" checked /> 未来{horizon_n}日保形预测扇形(50%/80%)</label>
    <label class="layer-pill"><input type="checkbox" data-layer="quantiles" checked /> 分位数边界路径(q75/q50/q25)</label>
    <label class="layer-pill"><input type="checkbox" data-layer="scenarios" checked /> 多情景路径(线宽/深浅按频率·虚线=n&lt;30)</label>
    <label class="layer-pill"><input type="checkbox" data-layer="scenario_fan" /> 情景混合（非校准区间）</label>
    <label class="layer-pill"><input type="checkbox" data-layer="barriers" checked /> Triple-Barrier 止盈止损框</label>
    <label class="layer-pill"><input type="checkbox" data-layer="structure" checked /> 核平滑与楔形/波段轨道</label>
    <label class="layer-pill"><input type="checkbox" data-layer="poc" checked /> 成交分布近似 POC</label>
    <label class="layer-pill"><input type="checkbox" data-layer="smc" checked /> SMC 订单块/FVG缺口</label>
    <label class="layer-pill"><input type="checkbox" data-layer="ma" checked /> 均线簇(MA20/60/120/250)</label>
  </div>
  <div style="display:flex;gap:8px;align-items:center;">
    <button class="btn" id="viewModeBtn" type="button">视图: 标的主图</button>
    <button class="btn" id="exportPngBtn" type="button">导出当前视图为高清 PNG</button>
  </div>
</div>

<div class="workspace">
  <div class="chart-card">
    <div class="hover-bar" id="hoverInspector">将鼠标移至 K 线或右侧预测区查看 OHLC、均线、RSI、RVOL 与未来分位价位。</div>
    <canvas id="klineCanvas" width="1440" height="760"></canvas>
    <div class="replay-box">
      <strong>时间推演回放 (Time-Travel Replay):</strong>
      <input type="range" id="replaySlider" min="0" max="0" value="0" />
      <span id="replayLabel">最新截面</span>
    </div>
  </div>

  <div class="side-col">
    <div class="side-card">
      <h3>1. 大盘温度计与市场状态 (Market Regime)</h3>
      <div class="kv-grid" id="regimeGrid"></div>
    </div>
    <div class="side-card">
      <h3>2. Triple-Barrier 风控与交易计划</h3>
      <div class="kv-grid" id="barrierGrid"></div>
    </div>
    <div class="side-card">
      <h3>3. 多情景推演与历史条件频率 (L1/L2)</h3>
      <ul class="evidence-list" id="scenarioList"></ul>
    </div>
    <div class="side-card">
      <h3>4. 多空双向证伪 & 独立风控否决权</h3>
      <ul class="evidence-list" id="debateList"></ul>
    </div>
    <div class="side-card">
      <h3>5. 100% 数学形态与 SMC 公式证据</h3>
      <ul class="evidence-list" id="patternList"></ul>
    </div>
  </div>
</div>

<div class="footer-note" id="footerNote"></div>

<script id="report-data" type="application/json">{safe_json}</script>
<script>
(function() {{
  const REPORT = JSON.parse(document.getElementById('report-data').textContent);
  const meta = REPORT.meta;
  if ((REPORT.quality || {{}}).status === 'UNAVAILABLE') {{
    document.getElementById('headerTitle').textContent =
      meta.name + ' (' + meta.symbol + ') · 数据不可用 (UNAVAILABLE)';
    document.getElementById('headerSub').textContent =
      '数据源: ' + meta.data_source + ' | 依据数据真实性铁律，合成或不可用行情已屏蔽全部价格点位与交易计划';
    document.getElementById('headerVerdict').textContent =
      '【数据不可用】检测到合成数据或不可信来源，禁止输出任何价格点位、形态结论或交易计划。';
    const c = document.getElementById('klineCanvas');
    const cx = c.getContext('2d');
    cx.fillStyle = '#0b0f17';
    cx.fillRect(0, 0, c.width, c.height);
    cx.save();
    cx.translate(c.width / 2, c.height / 2);
    cx.rotate(-0.25);
    cx.fillStyle = 'rgba(239, 68, 68, 0.35)';
    cx.font = 'bold 52px sans-serif';
    cx.textAlign = 'center';
    cx.fillText('数据质量 UNAVAILABLE · 非真实行情禁止用于交易判断', 0, 0);
    cx.restore();
    return;
  }}
  const bench = REPORT.market_benchmark;
  const ind = REPORT.mathematical_indicators;
  const tb = REPORT.triple_barrier_plan;
  const mtf = REPORT.multi_timeframe_score;
  const dec = REPORT.meta_labeling_decision;
  const fp = REPORT.future_projection_15d;
  const debate = REPORT.debate_and_veto;
  const series = REPORT.series_export;
  const bars = series.bars || [];
  const replays = REPORT.replay_snapshots || [];

  const isCnColor = meta.color_scheme === 'cn_red_up';
  const UP_COL = isCnColor ? '#f24444' : '#10bc84';
  const DN_COL = isCnColor ? '#10bc84' : '#f24444';
  const pDec = typeof meta.price_decimals === 'number' ? meta.price_decimals : 2;
  function fmtPrice(p) {{ return (p === null || p === undefined) ? '--' : Number(p).toFixed(pDec); }}

  // Populate Header safely via textContent
  document.getElementById('headerTitle').textContent =
    meta.name + ' (' + meta.symbol + ') · ' + meta.market + ' · ' + meta.timeframe;
  document.getElementById('headerSub').textContent =
    '数据截止: ' + meta.as_of + ' | 复权口径: ' + meta.adjustment +
    ' | 数据源: ' + meta.data_source + ' | 报告哈希: ' + REPORT.reproducibility.report_hash;

  const badgeWrap = document.getElementById('headerBadges');
  const b1 = document.createElement('span');
  b1.className = 'badge';
  b1.textContent = '大盘基准: ' + bench.index_name + ' (' + bench.regime_label_cn + ' ' + bench.regime_score + '分)';
  const b2 = document.createElement('span');
  b2.className = 'badge';
  b2.textContent = '多周期规则分: ' + mtf.rule_score + '/100 (' + dec.action_cn + ')';
  badgeWrap.appendChild(b1);
  badgeWrap.appendChild(b2);
  const vetoPrefix = debate.risk_officer_veto
    ? ('【风控否决：' + (debate.veto_reasons || []).join('；') + ' · 以下点位仅用于观察，不构成做多建议】 ')
    : '';
  document.getElementById('headerVerdict').textContent = vetoPrefix + debate.final_verdict_cn;

  function addKv(container, label, value) {{
    const div = document.createElement('div');
    div.className = 'kv-item';
    const sp = document.createElement('span');
    sp.textContent = label;
    const st = document.createElement('strong');
    st.textContent = String(value);
    div.appendChild(sp);
    div.appendChild(st);
    container.appendChild(div);
  }}

  const regGrid = document.getElementById('regimeGrid');
  const REGIME_LABELS = {{
    'TRENDING_BULL': '多头趋势扩张',
    'TRENDING_BEAR': '空头趋势承压',
    'SQUEEZE_CONSOLIDATION': '窄幅波动率压缩(Squeeze蓄势)',
    'MEAN_REVERTING_RANGE': '宽幅区间震荡',
    'HIGH_VOLATILITY_CHAOS': '高波失序/极端波动'
  }};
  function renderRegimeGrid(snap) {{
    regGrid.textContent = '';
    const regLabel = snap ? (REGIME_LABELS[snap.regime] || snap.regime) : REPORT.market_regime.label_cn;
    const rsVal = (snap && snap.benchmark_relative_strength !== undefined && snap.benchmark_relative_strength !== null)
      ? snap.benchmark_relative_strength
      : bench.relative_strength_vs_index;
    const snapLastBar = (snap && snap.bars_window && snap.bars_window.length) ? snap.bars_window[snap.bars_window.length - 1] : null;
    const rvolVal = snapLastBar && snapLastBar.rvol20 !== undefined && snapLastBar.rvol20 !== null ? snapLastBar.rvol20 : (ind.momentum.rvol20 || '--');
    const adxVal = snap ? '--(历史截面)' : (ind.trend.adx14 || '--');
    addKv(regGrid, snap ? ('截面 Regime (' + snap.as_of + ')') : '当前标的 Regime', regLabel);
    addKv(regGrid, '大盘相对强弱 (RS)', rsVal);
    addKv(regGrid, 'ADX(14) / RVOL(20)', adxVal + ' / ' + rvolVal + 'x');
    addKv(regGrid, 'BBW分位 / HV比值', (ind.volatility.bbw_percentile_120 || '--') + '% / ' + (ind.qlib_factors.hv_ratio_20_60 || '--'));
    addKv(regGrid, '成交分布 POC / VAH / VAL (近似)', fmtPrice(REPORT.volume_profile.poc_price) + ' / ' + fmtPrice(REPORT.volume_profile.vah_price) + ' / ' + fmtPrice(REPORT.volume_profile.val_price));
    const cov80 = fp.calibration_audit.nominal_80pct_empirical_coverage;
    addKv(regGrid, '保形预测80%实测覆盖', (cov80 === null || cov80 === undefined ? '--' : (cov80 * 100).toFixed(1) + '%') +
      ' (n=' + fp.calibration_audit.evaluation_samples + ', ' + fp.promotion_status + ')');
  }}
  renderRegimeGrid(null);

  const barGrid = document.getElementById('barrierGrid');
  const SETTLEMENT_CN = {{
    'T+1': 'T+1 交易（次日可卖）',
    'T+0_TRADING_T+2_CLEARING': 'T+0 回转 / T+2 交收',
    'T+1_SEC_SETTLEMENT': 'T+0 回转 / T+1 交收',
    'INDEX_NON_TRADABLE': '指数不可直接交易'
  }};
  const settleRaw = tb.market_microstructure.settlement;
  const settleLabel = SETTLEMENT_CN[settleRaw] || settleRaw;
  addKv(barGrid, '计划类型', dec.setup_label_cn);
  addKv(barGrid, debate.risk_officer_veto ? '观察区 (风控已否决)' : '建议入场区', fmtPrice(tb.entry_zone[0]) + ' ~ ' + fmtPrice(tb.entry_zone[1]));
  addKv(barGrid, '下屏障 (硬止损 SL)', fmtPrice(tb.stop_loss_barrier));
  addKv(barGrid, '上屏障 (TP1 / TP2)', fmtPrice(tb.take_profit_1) + ' / ' + fmtPrice(tb.take_profit_2));
  addKv(barGrid, '时间屏障 / 净盈亏比', tb.time_barrier_bars + ' bars / ' + tb.risk_reward_ratio + ' R');
  const sizing = tb.position_sizing;
  const sharesLabel = (sizing.recommended_shares !== null && sizing.recommended_shares !== undefined)
    ? sizing.recommended_shares + '股'
    : (sizing.account_equity ? '不给出（见报告风控）' : '需账户净值');
  addKv(barGrid, '制度 / 建议股数', settleLabel + ' / ' + sharesLabel);

  const scenList = document.getElementById('scenarioList');
  const activeScenarios = (REPORT.rule_scenarios && REPORT.rule_scenarios.scenarios) ? REPORT.rule_scenarios.scenarios : [];
  activeScenarios.forEach(function(sc) {{
    const li = document.createElement('li');
    const hf = sc.historical_frequency || {{}};
    const tw = sc.time_window_bars || {{}};
    const head = document.createElement('strong');
    head.textContent = '[' + (sc.status_cn || sc.status) + '] ' + sc.name + ' → 目标 ' + fmtPrice(sc.target_price);
    const detail = document.createElement('span');
    detail.className = 'formula-code';
    detail.textContent =
      '触发: ' + sc.trigger + ' | 失效: ' + sc.invalidation +
      ' | 预计用时中位数: ' + (tw.median || '--') + '日 | ' + (hf.display_cn || '');
    li.appendChild(head);
    li.appendChild(detail);
    scenList.appendChild(li);
  }});

  const debList = document.getElementById('debateList');
  (debate.bull_case || []).forEach(function(item) {{
    const li = document.createElement('li');
    li.textContent = '[看多依据] ' + item;
    debList.appendChild(li);
  }});
  (debate.bear_case || []).forEach(function(item) {{
    const li = document.createElement('li');
    li.textContent = '[空头证伪/风险] ' + item;
    debList.appendChild(li);
  }});

  const patList = document.getElementById('patternList');
  const allPats = REPORT.algebraic_patterns_detected || [];
  if (allPats.length === 0) {{
    const li = document.createElement('li');
    li.textContent = '当前窗口未触发极端蜡烛/背离形态；核心结构: ' + ind.structure_levels.wedge_structure.pattern;
    patList.appendChild(li);
  }} else {{
    allPats.forEach(function(p) {{
      const li = document.createElement('li');
      const title = document.createElement('strong');
      title.textContent = '[' + p.direction + '] ' + p.name_cn + ' (' + p.confirmed_at + ')';
      const code = document.createElement('span');
      code.className = 'formula-code';
      code.textContent = p.formula_verified;
      li.appendChild(title);
      li.appendChild(code);
      patList.appendChild(li);
    }});
  }}
  (REPORT.strategy_signals || []).forEach(function(s) {{
    if (s.direction !== 'NEUTRAL') {{
      const li = document.createElement('li');
      const title = document.createElement('strong');
      title.textContent = '[策略激活] ' + s.name_cn + ' -> ' + s.state;
      const code = document.createElement('span');
      code.className = 'formula-code';
      code.textContent = s.formula_verified;
      li.appendChild(title);
      li.appendChild(code);
      patList.appendChild(li);
    }}
  }});

  document.getElementById('footerNote').textContent =
    '算图分离保证：本页面所有形态、指标、三重屏障与未来15步分位数均由确定性数学公式预先计算并写入 JSON，禁止视觉模型猜数。仅供量化投研验证，不构成投资建议。';

  // Interactive Canvas Rendering
  const canvas = document.getElementById('klineCanvas');
  const ctx = canvas.getContext('2d');
  const layers = {{
    conformal: true,
    quantiles: true,
    scenarios: true,
    scenario_fan: false,
    barriers: true,
    structure: true,
    poc: true,
    smc: true,
    ma: true
  }};
  let showRsMode = false;
  let activeReplayIdx = replays.length; // replays.length === live/latest view

  const slider = document.getElementById('replaySlider');
  const replayLabel = document.getElementById('replayLabel');
  slider.max = String(replays.length);
  slider.value = String(replays.length);

  document.querySelectorAll('#layerControls input[type=checkbox]').forEach(function(cb) {{
    cb.addEventListener('change', function() {{
      layers[cb.getAttribute('data-layer')] = cb.checked;
      drawChart();
    }});
  }});

  slider.addEventListener('input', function() {{
    activeReplayIdx = parseInt(slider.value, 10);
    const isLive = activeReplayIdx === replays.length;
    document.querySelectorAll('#layerControls input[type=checkbox]').forEach(function(cb) {{
      const lk = cb.getAttribute('data-layer');
      const liveOnly = (lk === 'scenarios' || lk === 'scenario_fan' || lk === 'barriers' || lk === 'poc' || lk === 'smc');
      if (liveOnly) {{
        cb.disabled = !isLive;
        cb.parentElement.style.opacity = isLive ? '1' : '0.45';
      }}
    }});
    if (activeReplayIdx === replays.length) {{
      replayLabel.textContent = '最新截面 (' + meta.as_of + ')';
      b1.textContent = '大盘基准: ' + bench.index_name + ' (' + bench.regime_label_cn + ' ' + bench.regime_score + '分)';
      b2.textContent = '多周期规则分: ' + mtf.rule_score + '/100 (' + dec.action_cn + ')';
      renderRegimeGrid(null);
    }} else {{
      const snap = replays[activeReplayIdx];
      const hitTxt = (snap.terminal_within_80pct_band === null || snap.terminal_within_80pct_band === undefined)
        ? '待到期'
        : (snap.terminal_within_80pct_band ? '是' : '否');
      const rsTxt = snap.rule_score !== undefined ? (' · 当时规则分 ' + snap.rule_score + '/100') : '';
      replayLabel.textContent = '历史回放: ' + snap.as_of + rsTxt + ' (80%扇形命中: ' + hitTxt + ')';
      b1.textContent = '大盘基准(' + snap.as_of + '): ' + bench.index_name + ' (' + (snap.benchmark_regime_score !== undefined ? snap.benchmark_regime_score : bench.regime_score) + '分)';
      b2.textContent = '当时规则分(' + snap.as_of + '): ' + snap.rule_score + '/100 (' + (snap.rating || '') + ')';
      renderRegimeGrid(snap);
    }}
    drawChart();
  }});

  document.getElementById('viewModeBtn').addEventListener('click', function(ev) {{
    showRsMode = !showRsMode;
    ev.target.textContent = showRsMode ? '视图: 相对强弱(RS对比)' : '视图: 标的主图';
    drawChart();
  }});

  document.getElementById('exportPngBtn').addEventListener('click', function() {{
    const link = document.createElement('a');
    link.download = meta.symbol.replace(/[^A-Za-z0-9_-]/g, '_') + '_forecast_dashboard.png';
    link.href = canvas.toDataURL('image/png');
    link.click();
  }});

  let chartGeom = null;

  function drawChart(hoverX, hoverY) {{
    const W = canvas.width;
    const H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#0b0f17';
    ctx.fillRect(0, 0, W, H);

    const padL = 68, padR = 190, padT = 32, mainB = 530, volT = 575, volB = 720;
    const plotW = W - padL - padR;

    if (showRsMode && bench.rs_series && bench.rs_series.length > 5) {{
      drawRsComparison(W, H, padL, padR, padT, mainB);
      return;
    }}

    let activeBars = bars;
    let activeFp = fp;
    let realizedCloses = [];
    if (activeReplayIdx < replays.length) {{
      const snap = replays[activeReplayIdx];
      const cutDay = snap.as_of;
      const filtered = bars.filter(function(b) {{ return b.day <= cutDay; }});
      activeBars = (snap.bars_window && snap.bars_window.length >= 20) ? snap.bars_window : filtered;
      activeFp = {{
        horizon_dates: snap.horizon_dates,
        q10: snap.q10,
        q25: snap.q25,
        base_median_q50: snap.q50,
        optimistic_q75: snap.q75,
        q90: snap.q90
      }};
      realizedCloses = snap.realized_closes || [];
    }}

    const nBars = activeBars.length;
    const hBars = activeFp.horizon_dates.length;
    const histW = plotW * 0.72;
    const futW = plotW * 0.28;
    const stepH = histW / Math.max(1, nBars);
    const stepF = futW / Math.max(1, hBars + 1);

    function xOfBar(i) {{
      return padL + (i + 0.5) * stepH;
    }}
    function xOfFut(stepIdx) {{
      return padL + histW + (stepIdx + 1) * stepF;
    }}

    // Compute Y price range
    const prices = [];
    activeBars.forEach(function(b) {{
      prices.push(b.high, b.low);
      if (layers.ma) {{
        [b.ma20, b.ma60, b.ma120, b.ma250].forEach(function(m) {{ if (m !== null && m !== undefined) prices.push(m); }});
      }}
    }});
    if (layers.conformal) {{
      activeFp.q10.forEach(function(p) {{ prices.push(p); }});
      activeFp.q90.forEach(function(p) {{ prices.push(p); }});
    }}
    if (layers.scenario_fan && fp.scenario_conditional_fan && activeReplayIdx === replays.length) {{
      (fp.scenario_conditional_fan.q10 || []).forEach(function(p) {{ prices.push(p); }});
      (fp.scenario_conditional_fan.q90 || []).forEach(function(p) {{ prices.push(p); }});
    }}
    if (layers.scenarios && activeScenarios.length > 0 && activeReplayIdx === replays.length) {{
      activeScenarios.forEach(function(sc) {{
        (sc.waypoints_offset || []).forEach(function(wp) {{ prices.push(wp[1]); }});
      }});
    }}
    if (layers.barriers && activeReplayIdx === replays.length) {{
      prices.push(tb.stop_loss_barrier, tb.take_profit_1, tb.take_profit_2);
    }}
    realizedCloses.forEach(function(p) {{ prices.push(p); }});

    let pMin = Math.min.apply(null, prices);
    let pMax = Math.max.apply(null, prices);
    const pSpan = Math.max(1e-4, pMax - pMin);
    pMin -= pSpan * 0.07;
    pMax += pSpan * 0.09;

    function yOfPrice(p) {{
      return mainB - ((p - pMin) / (pMax - pMin)) * (mainB - padT);
    }}

    chartGeom = {{
      padL: padL, padR: padR, padT: padT, mainB: mainB, histW: histW, futW: futW,
      nBars: nBars, hBars: hBars, stepH: stepH, stepF: stepF,
      activeBars: activeBars, activeFp: activeFp, pMin: pMin, pMax: pMax,
      xOfBar: xOfBar, xOfFut: xOfFut, yOfPrice: yOfPrice
    }};

    // Background panels & grid
    ctx.fillStyle = '#0e1420';
    ctx.fillRect(padL, padT, histW, mainB - padT);
    ctx.fillStyle = '#131b2e';
    ctx.fillRect(padL + histW, padT, futW, mainB - padT);

    ctx.strokeStyle = '#1e293b';
    ctx.lineWidth = 1;
    for (let g = 0; g <= 5; g++) {{
      const gp = pMin + (pMax - pMin) * (g / 5);
      const gy = yOfPrice(gp);
      ctx.beginPath();
      ctx.moveTo(padL, gy);
      ctx.lineTo(W - padR, gy);
      ctx.stroke();
      ctx.fillStyle = '#94a3b8';
      ctx.font = '12px monospace';
      ctx.fillText(fmtPrice(gp), 8, gy + 4);
    }}

    // Future divider line
    const divX = padL + histW;
    ctx.strokeStyle = '#38bdf8';
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(divX, padT);
    ctx.lineTo(divX, mainB);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#38bdf8';
    ctx.font = 'bold 12px sans-serif';
    ctx.fillText('右侧: 未来 ' + hBars + ' Bar 预测演进区', divX + 10, padT + 18);

    // Layer: Volume Profile POC / Value Area
    if (layers.poc && REPORT.volume_profile && activeReplayIdx === replays.length) {{
      const vp = REPORT.volume_profile;
      const yVah = yOfPrice(vp.vah_price);
      const yVal = yOfPrice(vp.val_price);
      const yPoc = yOfPrice(vp.poc_price);
      ctx.fillStyle = 'rgba(148, 163, 184, 0.08)';
      ctx.fillRect(padL, Math.min(yVah, yVal), histW, Math.abs(yVal - yVah));
      ctx.strokeStyle = 'rgba(250, 204, 21, 0.65)';
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(padL, yPoc);
      ctx.lineTo(W - padR, yPoc);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#facc15';
      ctx.fillText('POC ' + fmtPrice(vp.poc_price), W - padR + 6, yPoc + 4);
    }}

    // Layer: SMC Order Blocks & FVG (配色跟随 meta.color_scheme)
    if (layers.smc && REPORT.smc_structures && activeReplayIdx === replays.length) {{
      const bullObFill = isCnColor ? 'rgba(242, 68, 68, 0.14)' : 'rgba(16, 188, 132, 0.14)';
      const bearObFill = isCnColor ? 'rgba(16, 188, 132, 0.14)' : 'rgba(242, 68, 68, 0.14)';
      function xStartForDay(dayStr, fallbackFrac) {{
        if (!dayStr) return padL + histW * fallbackFrac;
        for (let bi = 0; bi < activeBars.length; bi++) {{
          if (activeBars[bi].day >= dayStr) return xOfBar(bi);
        }}
        return padL + histW * fallbackFrac;
      }}
      (REPORT.smc_structures.order_blocks || []).forEach(function(ob) {{
        const y1 = yOfPrice(ob.zone[1]);
        const y2 = yOfPrice(ob.zone[0]);
        const x0 = xStartForDay(ob.day, 0.35);
        ctx.fillStyle = ob.kind.indexOf('BULL') >= 0 ? bullObFill : bearObFill;
        ctx.fillRect(x0, Math.min(y1, y2), (padL + histW) - x0, Math.max(4, Math.abs(y2 - y1)));
      }});
      (REPORT.smc_structures.fvgs || []).forEach(function(fvg) {{
        const y1 = yOfPrice(fvg.zone[1]);
        const y2 = yOfPrice(fvg.zone[0]);
        const x0 = xStartForDay(fvg.day, 0.55);
        ctx.fillStyle = 'rgba(56, 189, 248, 0.12)';
        ctx.fillRect(x0, Math.min(y1, y2), (padL + histW) - x0, Math.max(3, Math.abs(y2 - y1)));
      }});
    }}

    // Layer: Candlesticks
    const barHalf = Math.max(1.5, Math.min(6, stepH * 0.36));
    activeBars.forEach(function(b, i) {{
      const x = xOfBar(i);
      const yo = yOfPrice(b.open);
      const yc = yOfPrice(b.close);
      const yh = yOfPrice(b.high);
      const yl = yOfPrice(b.low);
      const col = b.close >= b.open ? UP_COL : DN_COL;
      ctx.strokeStyle = col;
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      ctx.moveTo(x, yh);
      ctx.lineTo(x, yl);
      ctx.stroke();
      const topY = Math.min(yo, yc);
      const bodyH = Math.max(2, Math.abs(yc - yo));
      ctx.fillStyle = col;
      ctx.fillRect(x - barHalf, topY, barHalf * 2, bodyH);
    }});

    // Layer: Moving Averages
    if (layers.ma) {{
      const maConfigs = [
        ['ma20', '#facc15', 'MA20'],
        ['ma60', '#c084fc', 'MA60'],
        ['ma120', '#94a3b8', 'MA120'],
        ['ma250', '#e879f9', 'MA250']
      ];
      maConfigs.forEach(function(cfg) {{
        const key = cfg[0], col = cfg[1];
        ctx.strokeStyle = col;
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        let started = false;
        activeBars.forEach(function(b, i) {{
          const v = b[key];
          if (v !== null && v !== undefined) {{
            const x = xOfBar(i), y = yOfPrice(v);
            if (!started) {{ ctx.moveTo(x, y); started = true; }}
            else {{ ctx.lineTo(x, y); }}
          }}
        }});
        ctx.stroke();
      }});
    }}

    // Layer: Kernel Smooth & Wedge Rails
    if (layers.structure) {{
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.75)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      activeBars.forEach(function(b, i) {{
        const x = xOfBar(i), y = yOfPrice(b.kernel_smooth || b.close);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }});
      ctx.stroke();

      const w = ind.structure_levels.wedge_structure;
      if (w && w.upper && w.lower && activeReplayIdx === replays.length) {{
        const structBars = (REPORT.quality && REPORT.quality.structure_bars) ? REPORT.quality.structure_bars : 120;
        const winOff = nBars - structBars;
        function drawRail(rail, todayPrice, slopePerDay, color) {{
          const rawStartIdx = rail.start.idx + winOff;
          const startIdx = Math.max(0, rawStartIdx);
          const startPrice = rawStartIdx >= 0
            ? rail.start.price
            : (rail.start.price + (slopePerDay || 0) * (0 - rawStartIdx));
          ctx.strokeStyle = color;
          ctx.lineWidth = 2.0;
          ctx.beginPath();
          ctx.moveTo(xOfBar(startIdx), yOfPrice(startPrice));
          ctx.lineTo(xOfBar(nBars - 1), yOfPrice(todayPrice));
          ctx.stroke();
        }}
        drawRail(w.upper, w.upper_today, w.slope_upper_per_day, '#ff9f43');
        drawRail(w.lower, w.lower_today, w.slope_lower_per_day, '#38bdf8');
      }}
    }}

    const lastBar = activeBars[nBars - 1];
    const startX = xOfBar(nBars - 1);
    const startY = yOfPrice(lastBar.close);

    // Layer: Triple-Barrier Risk Boxes
    if (layers.barriers && activeReplayIdx === replays.length) {{
      const yEntry = yOfPrice((tb.entry_zone[0] + tb.entry_zone[1]) / 2);
      const yTp1 = yOfPrice(tb.take_profit_1);
      const yTp2 = yOfPrice(tb.take_profit_2);
      const ySl = yOfPrice(tb.stop_loss_barrier);

      // Green TP zone
      ctx.fillStyle = 'rgba(16, 188, 132, 0.13)';
      ctx.fillRect(divX, Math.min(yTp2, yEntry), futW, Math.abs(yEntry - yTp2));
      ctx.strokeStyle = '#10bc84';
      ctx.strokeRect(divX, Math.min(yTp2, yEntry), futW, Math.abs(yEntry - yTp2));

      // Red SL zone
      ctx.fillStyle = 'rgba(242, 68, 68, 0.13)';
      ctx.fillRect(divX, Math.min(yEntry, ySl), futW, Math.abs(ySl - yEntry));
      ctx.strokeStyle = '#f24444';
      ctx.strokeRect(divX, Math.min(yEntry, ySl), futW, Math.abs(ySl - yEntry));

      ctx.fillStyle = '#10bc84';
      ctx.font = 'bold 11px monospace';
      ctx.fillText('TP2 ' + fmtPrice(tb.take_profit_2), W - padR + 6, yTp2 + 4);
      ctx.fillText('TP1 ' + fmtPrice(tb.take_profit_1), W - padR + 6, yTp1 + 4);
      ctx.fillStyle = '#f24444';
      ctx.fillText('SL  ' + fmtPrice(tb.stop_loss_barrier), W - padR + 6, ySl + 4);
    }}

    // Layer: Conformal Fan (80% outer & 50% inner)
    if (layers.conformal) {{
      // 80% band (q10 ~ q90)
      ctx.fillStyle = 'rgba(56, 189, 248, 0.14)';
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      for (let i = 0; i < hBars; i++) ctx.lineTo(xOfFut(i), yOfPrice(activeFp.q90[i]));
      for (let i = hBars - 1; i >= 0; i--) ctx.lineTo(xOfFut(i), yOfPrice(activeFp.q10[i]));
      ctx.closePath();
      ctx.fill();

      // 50% band (q25 ~ q75)
      ctx.fillStyle = 'rgba(56, 189, 248, 0.26)';
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      for (let i = 0; i < hBars; i++) ctx.lineTo(xOfFut(i), yOfPrice(activeFp.optimistic_q75[i]));
      for (let i = hBars - 1; i >= 0; i--) ctx.lineTo(xOfFut(i), yOfPrice(activeFp.q25[i]));
      ctx.closePath();
      ctx.fill();
    }}

    // Layer: Scenario Conditional Fan (display-only, uncalibrated)
    if (layers.scenario_fan && fp.scenario_conditional_fan && activeReplayIdx === replays.length) {{
      const scf = fp.scenario_conditional_fan;
      ctx.fillStyle = 'rgba(192, 132, 252, 0.14)';
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      for (let i = 0; i < hBars; i++) ctx.lineTo(xOfFut(i), yOfPrice(scf.q90[i]));
      for (let i = hBars - 1; i >= 0; i--) ctx.lineTo(xOfFut(i), yOfPrice(scf.q10[i]));
      ctx.closePath();
      ctx.fill();

      ctx.fillStyle = 'rgba(192, 132, 252, 0.24)';
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      for (let i = 0; i < hBars; i++) ctx.lineTo(xOfFut(i), yOfPrice(scf.q75[i]));
      for (let i = hBars - 1; i >= 0; i--) ctx.lineTo(xOfFut(i), yOfPrice(scf.q25[i]));
      ctx.closePath();
      ctx.fill();

      ctx.strokeStyle = 'rgba(192, 132, 252, 0.9)';
      ctx.lineWidth = 1.8;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      for (let i = 0; i < hBars; i++) ctx.lineTo(xOfFut(i), yOfPrice(scf.center_q50[i]));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#c084fc';
      ctx.font = 'bold 11px sans-serif';
      ctx.fillText(scf.legend_label_cn || '情景混合（非校准区间）', divX + 10, padT + 34);
    }}

    // Layer: 3 Quantile Scenario Paths (q75, q50, q25)
    if (layers.quantiles) {{
      function drawFutureCurve(arr, col, labelPrefix) {{
        ctx.strokeStyle = col;
        ctx.lineWidth = 2.2;
        ctx.beginPath();
        ctx.moveTo(startX, startY);
        for (let i = 0; i < arr.length; i++) {{
          ctx.lineTo(xOfFut(i), yOfPrice(arr[i]));
        }}
        ctx.stroke();
        const lastP = arr[arr.length - 1];
        const endY = yOfPrice(lastP);
        ctx.fillStyle = col;
        ctx.font = 'bold 11px monospace';
        ctx.fillText(labelPrefix + ' ' + fmtPrice(lastP), W - padR + 6, endY + 4);
      }}
      drawFutureCurve(activeFp.optimistic_q75, UP_COL, '上侧q75');
      drawFutureCurve(activeFp.base_median_q50, '#facc15', '基准q50');
      drawFutureCurve(activeFp.q25, DN_COL, '下侧q25');
    }}

    // Layer: Multi-Scenario Playbook Paths (width & alpha scaled by historical_frequency, dashed if n < 30)
    if (layers.scenarios && activeScenarios.length > 0 && activeReplayIdx === replays.length) {{
      activeScenarios.forEach(function(sc, idxSc) {{
        const hf = sc.historical_frequency || {{}};
        const freq = typeof hf.target_freq === 'number' ? Math.max(0, Math.min(1, hf.target_freq)) : 0.35;
        const nSamp = typeof hf.sample_size === 'number' ? hf.sample_size : 0;
        const lowSample = Boolean(hf.low_sample || nSamp < 30);
        const alpha = Math.min(1.0, (lowSample ? 0.52 : 0.68) + 0.30 * freq);
        const lw = 1.6 + 2.4 * freq;
        const rgb = sc.direction === 'BULLISH'
          ? (isCnColor ? '248, 113, 113' : '52, 211, 153')
          : (sc.direction === 'BEARISH' ? (isCnColor ? '52, 211, 153' : '248, 113, 113') : '250, 204, 21');
        ctx.save();
        ctx.strokeStyle = 'rgba(' + rgb + ', ' + alpha.toFixed(2) + ')';
        ctx.lineWidth = lw;
        if (lowSample) ctx.setLineDash([6, 4]);
        ctx.beginPath();
        const wps = sc.waypoints_offset || [];
        wps.forEach(function(wp, wi) {{
          const dx = wp[0], p = wp[1];
          const x = dx <= 0 ? startX : xOfFut(Math.min(hBars - 1, Math.max(0, Math.round(dx) - 1)));
          const y = yOfPrice(p);
          if (wi === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }});
        ctx.stroke();
        if (wps.length > 0) {{
          const lastWp = wps[wps.length - 1];
          const endX = xOfFut(Math.min(hBars - 1, Math.max(0, Math.round(lastWp[0]) - 1)));
          const endY = yOfPrice(lastWp[1]);
          ctx.fillStyle = 'rgba(' + rgb + ', ' + Math.min(1, alpha + 0.1).toFixed(2) + ')';
          ctx.font = 'bold 11px sans-serif';
          const rawFreq = typeof hf.raw_target_freq === 'number' ? hf.raw_target_freq : freq;
          const pctStr = nSamp > 0 ? (rawFreq * 100).toFixed(1) + '%' : ('先验' + Math.round(freq * 100) + '%');
          const tag = sc.name + ' ' + pctStr + '(n=' + nSamp + (lowSample ? ',低样本' : '') + ')';
          ctx.fillText(tag, Math.max(divX + 6, endX - 110), endY - 6 - (idxSc % 2) * 12);
        }}
        ctx.restore();
      }});
    }}

    // Realized path overlay when scrubbing replay slider
    if (realizedCloses.length > 0) {{
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2.6;
      ctx.beginPath();
      ctx.moveTo(startX, startY);
      realizedCloses.forEach(function(rc, i) {{
        ctx.lineTo(xOfFut(i), yOfPrice(rc));
      }});
      ctx.stroke();
      const rLast = realizedCloses[realizedCloses.length - 1];
      ctx.fillStyle = '#ffffff';
      ctx.fillText('真实收盘 ' + fmtPrice(rLast), W - padR + 6, yOfPrice(rLast) - 6);
    }}

    // Volume subplot
    ctx.fillStyle = '#0e1420';
    ctx.fillRect(padL, volT, histW, volB - volT);
    const maxVol = Math.max.apply(null, activeBars.map(function(b) {{ return b.volume || 1; }}));
    activeBars.forEach(function(b, i) {{
      const x = xOfBar(i);
      const vh = ((b.volume || 0) / maxVol) * (volB - volT - 12);
      ctx.fillStyle = b.close >= b.open ? UP_COL : DN_COL;
      ctx.fillRect(x - barHalf, volB - vh, barHalf * 2, vh);
    }});
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px sans-serif';
    const curRvol = (lastBar && lastBar.rvol20 !== undefined && lastBar.rvol20 !== null) ? lastBar.rvol20 : (ind.momentum.rvol20 || 1.0);
    ctx.fillText('成交量 Volume & RVOL20 (截面 ' + curRvol + 'x)', padL + 8, volT + 16);

    drawQualityWatermark(W, H);

    // Hover crosshair
    if (hoverX !== undefined && hoverY !== undefined) {{
      ctx.strokeStyle = 'rgba(255,255,255,0.35)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(hoverX, padT);
      ctx.lineTo(hoverX, volB);
      ctx.moveTo(padL, hoverY);
      ctx.lineTo(W - padR, hoverY);
      ctx.stroke();
    }}
  }}

  // 数据非 OK（合成/来源未知/缺基准）时在画布上叠加水印，导出 PNG 也会带上
  function drawQualityWatermark(W, H) {{
    const q = REPORT.quality || {{}};
    if (q.status === 'OK') return;
    ctx.save();
    ctx.translate(W / 2, H / 2);
    ctx.rotate(-0.25);
    ctx.fillStyle = 'rgba(239, 68, 68, 0.28)';
    ctx.font = 'bold 48px sans-serif';
    ctx.textAlign = 'center';
    const wmText = q.status === 'UNAVAILABLE'
      ? '数据质量 UNAVAILABLE · 非真实行情禁止用于交易判断'
      : ('数据质量 ' + q.status + ' · 缺少大盘基准或来源待核验');
    ctx.fillText(wmText, 0, 0);
    ctx.restore();
  }}

  function drawRsComparison(W, H, padL, padR, padT, mainB) {{
    const rs = bench.rs_series;
    const plotW = W - padL - padR;
    const vals = [];
    rs.forEach(function(r) {{ vals.push(r.target_norm, r.benchmark_norm, r.rs_line); }});
    const vMin = Math.min.apply(null, vals) * 0.98;
    const vMax = Math.max.apply(null, vals) * 1.02;
    function xOf(i) {{ return padL + (i / Math.max(1, rs.length - 1)) * plotW; }}
    function yOf(v) {{ return mainB - ((v - vMin) / (vMax - vMin)) * (mainB - padT); }}

    ctx.fillStyle = '#0e1420';
    ctx.fillRect(padL, padT, plotW, mainB - padT);
    [['target_norm', '#38bdf8', meta.name + ' (归一化=100)'],
     ['benchmark_norm', '#94a3b8', bench.index_name + ' (归一化=100)'],
     ['rs_line', '#facc15', '相对强弱 RS Line']].forEach(function(cfg) {{
      ctx.strokeStyle = cfg[1];
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      rs.forEach(function(r, i) {{
        const x = xOf(i), y = yOf(r[cfg[0]]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }});
      ctx.stroke();
    }});
    ctx.fillStyle = '#eef2f8';
    ctx.font = 'bold 14px sans-serif';
    ctx.fillText('标的 vs 基准指数 归一化相对强弱对比 (起点=100)', padL + 16, padT + 28);
    drawQualityWatermark(W, H);
  }}

  canvas.addEventListener('mousemove', function(ev) {{
    if (!chartGeom || showRsMode) return;
    const rect = canvas.getBoundingClientRect();
    const x = (ev.clientX - rect.left) * (canvas.width / rect.width);
    const y = (ev.clientY - rect.top) * (canvas.height / rect.height);
    const insp = document.getElementById('hoverInspector');

    if (x >= chartGeom.padL && x <= chartGeom.padL + chartGeom.histW) {{
      const idx = Math.max(0, Math.min(chartGeom.nBars - 1, Math.floor((x - chartGeom.padL) / chartGeom.stepH)));
      const b = chartGeom.activeBars[idx];
      insp.textContent =
        '[历史K线 ' + b.day + '] O:' + fmtPrice(b.open) + ' H:' + fmtPrice(b.high) +
        ' L:' + fmtPrice(b.low) + ' C:' + fmtPrice(b.close) +
        ' | MA20:' + (b.ma20 !== null && b.ma20 !== undefined ? fmtPrice(b.ma20) : '--') +
        ' | RSI14:' + (b.rsi14 || '--') + ' | RVOL:' + (b.rvol20 || '--') + 'x';
    }} else if (x > chartGeom.padL + chartGeom.histW && x <= canvas.width - chartGeom.padR) {{
      const fIdx = Math.max(0, Math.min(chartGeom.hBars - 1, Math.floor((x - chartGeom.padL - chartGeom.histW) / chartGeom.stepF)));
      const fd = chartGeom.activeFp.horizon_dates[fIdx];
      insp.textContent =
        '[未来预测 Step +' + (fIdx + 1) + ' (' + fd + ')] ' +
        'q10:' + fmtPrice(chartGeom.activeFp.q10[fIdx]) +
        ' | 悲观q25:' + fmtPrice(chartGeom.activeFp.q25[fIdx]) +
        ' | 基准q50:' + fmtPrice(chartGeom.activeFp.base_median_q50[fIdx]) +
        ' | 乐观q75:' + fmtPrice(chartGeom.activeFp.optimistic_q75[fIdx]) +
        ' | q90:' + fmtPrice(chartGeom.activeFp.q90[fIdx]);
    }}
    drawChart(x, y);
  }});

  drawChart();
}})();
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_doc, encoding="utf-8")
    return output_path
