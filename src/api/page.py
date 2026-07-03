"""Static dashboard page: human-first information design over the read-only API.

Single self-contained file (no CDN, no framework) per the MVP dashboard
contract: browser polling against the JSON endpoints, presentation-layer
translation of codes and numbers into the operator's language.
"""

from __future__ import annotations

DASHBOARD_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>每日訊號儀表板</title>
<style>
  :root {
    --bg: #0b0f14; --card: #121820; --card-2: #0e141b; --line: #1f2937;
    --text: #e8eef4; --muted: #8b9aa8; --faint: #5c6b7a;
    --up: #34d399; --down: #f87171; --warn: #fbbf24; --accent: #5b9dff;
    --chip: #1a2330;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--text);
    font-family: "Segoe UI", "Microsoft JhengHei", system-ui, sans-serif;
    font-size: 15px; line-height: 1.55; padding: 24px;
    max-width: 1080px; margin: 0 auto;
  }
  .num { font-variant-numeric: tabular-nums; }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px; margin-bottom: 20px; }
  header h1 { font-size: 1.25rem; font-weight: 600; }
  .status-line { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: .85rem; margin-left: auto; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--faint); }
  .dot.ok { background: var(--up); box-shadow: 0 0 6px rgba(52,211,153,.6); }
  .dot.warn { background: var(--warn); }
  .dot.bad { background: var(--down); }

  .grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
  .grid.full { grid-template-columns: 1fr; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 14px;
    padding: 18px 20px;
  }
  .card h2 { font-size: .82rem; font-weight: 600; color: var(--muted); letter-spacing: .06em; margin-bottom: 12px; }
  .empty { color: var(--faint); padding: 6px 0; }

  /* action card */
  .action-item { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px dashed var(--line); }
  .action-item:last-child { border-bottom: none; }
  .action-verb { font-size: 1.05rem; font-weight: 700; padding: 4px 12px; border-radius: 8px; }
  .action-verb.buy { color: var(--up); background: rgba(52,211,153,.12); }
  .action-verb.sell { color: var(--down); background: rgba(248,113,113,.12); }
  .action-body b { font-size: 1.02rem; }
  .action-sub { color: var(--muted); font-size: .85rem; }
  .no-action { display: flex; align-items: center; gap: 10px; color: var(--muted); padding: 8px 0; }

  /* ladder cards */
  .asset-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
  .asset-name { font-size: 1.05rem; font-weight: 700; }
  .asset-frac { margin-left: auto; font-size: 1.3rem; font-weight: 700; }
  .ladder { display: flex; gap: 5px; margin: 8px 0 12px; }
  .rung { flex: 1; height: 10px; border-radius: 4px; background: var(--chip); }
  .rung.on { background: linear-gradient(90deg, #3b82f6, #5b9dff); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: .78rem; padding: 3px 9px; border-radius: 20px; background: var(--chip); color: var(--muted); }
  .chip.on { color: var(--up); background: rgba(52,211,153,.1); }
  .chip.off { color: var(--faint); }
  .asset-note { color: var(--muted); font-size: .85rem; margin-top: 10px; }

  /* scoreboard */
  .score-row { display: flex; flex-wrap: wrap; gap: 28px; align-items: flex-end; }
  .metric .label { color: var(--muted); font-size: .78rem; margin-bottom: 2px; }
  .metric .value { font-size: 1.5rem; font-weight: 700; }
  .metric .value.big { font-size: 2rem; }
  .pos { color: var(--up); } .neg { color: var(--down); }
  svg.spark { width: 100%; height: 84px; margin-top: 14px; display: block; }
  .positions { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: .88rem; }
  .positions th { text-align: left; color: var(--faint); font-weight: 500; padding: 4px 10px 4px 0; }
  .positions td { padding: 4px 10px 4px 0; border-top: 1px solid var(--line); }

  /* gate */
  .gate-days { display: flex; align-items: baseline; gap: 8px; margin-bottom: 8px; }
  .gate-days .big { font-size: 1.6rem; font-weight: 700; }
  .bar { height: 8px; border-radius: 4px; background: var(--chip); overflow: hidden; margin: 6px 0 14px; }
  .bar > div { height: 100%; background: linear-gradient(90deg, #3b82f6, #34d399); }
  .kv { display: flex; justify-content: space-between; padding: 5px 0; border-top: 1px solid var(--line); font-size: .88rem; }
  .kv .k { color: var(--muted); }
  .badge { font-size: .78rem; padding: 2px 10px; border-radius: 20px; }
  .badge.ok { color: var(--up); background: rgba(52,211,153,.1); }
  .badge.warn { color: var(--warn); background: rgba(251,191,36,.1); }

  /* risk list */
  .risk-item { display: flex; gap: 10px; padding: 7px 0; border-top: 1px solid var(--line); font-size: .88rem; }
  .risk-item:first-of-type { border-top: none; }
  .risk-when { color: var(--faint); white-space: nowrap; }

  /* details */
  details { margin-top: 14px; }
  details summary {
    cursor: pointer; color: var(--muted); font-size: .88rem; padding: 10px 4px;
    user-select: none;
  }
  details summary:hover { color: var(--text); }
  table.log { width: 100%; border-collapse: collapse; font-size: .85rem; }
  table.log th { text-align: left; color: var(--faint); font-weight: 500; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line); }
  table.log td { padding: 6px 10px 6px 0; border-bottom: 1px solid var(--card-2); }
  .side-buy { color: var(--up); } .side-sell { color: var(--down); }
  .code { color: var(--faint); font-family: Consolas, monospace; font-size: .78rem; }

  footer { margin-top: 26px; color: var(--faint); font-size: .78rem; line-height: 1.7; }
  footer a { color: var(--muted); }
  .err { color: var(--down); font-size: .85rem; }
</style>
</head>
<body>

<header>
  <h1>每日訊號儀表板</h1>
  <div class="status-line">
    <span class="dot" id="statusDot"></span>
    <span id="statusText">載入中…</span>
  </div>
</header>

<div class="grid full">
  <div class="card" id="actionCard">
    <h2>今日行動</h2>
    <div id="actionBody" class="empty">載入中…</div>
  </div>
</div>

<div class="grid" id="assetGrid" style="margin-top:14px"></div>

<div class="grid full" style="margin-top:14px">
  <div class="card">
    <h2>虛擬記分板　<span style="font-weight:400">— 如果完全照訊號做，帳戶現在的樣子</span></h2>
    <div class="score-row" id="scoreRow"><div class="empty">載入中…</div></div>
    <svg class="spark" id="spark" preserveAspectRatio="none" viewBox="0 0 600 84"></svg>
    <div id="positionsWrap"></div>
  </div>
</div>

<div class="grid" style="margin-top:14px">
  <div class="card">
    <h2>驗證閘門進度　<span style="font-weight:400">— 過完六關前，訊號僅供觀察</span></h2>
    <div id="gateBody" class="empty">載入中…</div>
  </div>
  <div class="card">
    <h2>風險與系統健康</h2>
    <div id="riskBody" class="empty">載入中…</div>
  </div>
</div>

<details>
  <summary>▸ 明細紀錄（通知 / 成交 / 拒單）</summary>
  <div class="grid full" style="margin-top:8px">
    <div class="card"><h2>通知歷史</h2><div id="notifLog" class="empty">—</div></div>
    <div class="card"><h2>記分板成交</h2><div id="fillLog" class="empty">—</div></div>
    <div class="card"><h2>被拒行動（附原因）</h2><div id="rejLog" class="empty">—</div></div>
  </div>
</details>

<footer>
  本儀表板為 read-only scoreboard：無法下單、無法變更風險限制、永不接觸私有 API。訊號為系統性規則輸出，非投資建議；
  實際下單由你手動執行。原始資料：<a href="/api/signals/current">/api/signals/current</a> ·
  <a href="/api/account">account</a> · <a href="/api/gate">gate</a> · <a href="/api/risk">risk</a>
  <span id="refreshStamp"></span>
</footer>

<script>
"use strict";

/* ── 翻譯字典：理由碼 → 白話 ─────────────────────────── */
const SMA_LABELS = { 20: "20日線", 65: "65日線", 150: "150日線", 200: "200日線" };
const CODE_TEXT = {
  LADDER_UP: "加倉一檔以上", LADDER_DOWN: "減倉一檔以上", LADDER_HOLD: "維持不變",
  DISASTER_SINGLE_DAY_DROP: "單日重挫警報（≥20%）", REEVALUATE_REQUIRED: "建議重新評估",
  STALE_DATA_HALT: "資料過期：暫停加倉", STALE_DATA: "資料過期",
  MISSED_DAYS: "缺漏決策日", WARMUP_INSUFFICIENT_HISTORY: "暖身中（歷史不足 200 日）",
  NOTIFICATION_DELIVERY_FAILED: "通知投遞失敗（將自動重試）",
  ORDER_WITHOUT_FILL_SKIPPED: "偵測到未完成訂單，已安全跳過",
  DRAWDOWN_PAUSE: "回撤保護：暫停新倉", DAILY_LOSS_PAUSE: "單日虧損保護：暫停新倉",
  MIN_NOTIONAL_NOT_MET: "金額低於最小交易門檻", EXCHANGE_MIN_NOTIONAL_NOT_MET: "低於交易所最小金額",
  ZERO_QUANTITY_AFTER_ROUNDING: "數量過小（捨入後為零）",
  ACCOUNT_STOP: "帳戶停止", TRAILING_STOP: "移動停損暫停",
  BROKER_REJECTED_INSUFFICIENT_CASH: "現金不足", SELL_EXCEEDS_HOLDINGS: "賣出超過持有量",
  RISK_APPROVED: "風險核可", RISK_REDUCING_SELL_ALLOWED_DURING_PAUSE: "暫停期間允許減倉賣出",
};
const codeText = (c) => CODE_TEXT[c] || c;

/* ── 格式化 ──────────────────────────────────────────── */
const fmtMoney = (x) => Number(x).toLocaleString("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = (x, dp = 1) => (Number(x) * 100).toFixed(dp) + "%";
const fmtQty = (x) => { const n = Number(x); return n.toLocaleString("zh-TW", { maximumFractionDigits: 6 }); };
const fmtDate = (iso) => { const d = new Date(iso); return `${d.getUTCMonth() + 1}月${d.getUTCDate()}日`; };
const fmtDateFull = (iso) => { const d = new Date(iso); return `${d.getUTCFullYear()}/${d.getUTCMonth() + 1}/${d.getUTCDate()}`; };
const signCls = (x) => (Number(x) >= 0 ? "pos" : "neg");
const esc = (s) => String(s).replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));

async function getJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} → HTTP ${response.status}`);
  return response.json();
}

/* ── 今日行動 ────────────────────────────────────────── */
function renderActions(notifications, account, budgets) {
  const el = document.getElementById("actionBody");
  if (account.status !== "OK") {
    el.innerHTML = `<div class="no-action">第一個決策循環尚未執行——排程於每日 08:05（台北）自動運行。</div>`;
    return;
  }
  const cycleDate = account.close_time.slice(0, 10);
  const todays = notifications.filter((n) => n.decision_time.slice(0, 10) === cycleDate);
  if (!todays.length) {
    el.innerHTML = `<div class="no-action"><span style="font-size:1.2rem">✓</span>
      ${fmtDate(account.close_time)}收盤：<b>維持現有配置，今天不需要任何操作。</b>
      <span class="action-sub">（長期沒有動作是趨勢系統的正常狀態）</span></div>`;
    return;
  }
  el.innerHTML = todays.map((n) => {
    const isBuy = n.action === "INCREASE_EXPOSURE";
    const budget = Number(budgets[n.symbol] || 0);
    const accountPct = Number(n.delta_fraction) * budget;
    const risk = n.risk_status && n.risk_status !== "OK"
      ? ` <span class="chip off">⚠ ${esc(n.risk_status.split(",").map(codeText).join("、"))}</span>` : "";
    return `<div class="action-item">
      <span class="action-verb ${isBuy ? "buy" : "sell"}">${isBuy ? "買入" : "賣出"}</span>
      <div class="action-body">
        <b>${esc(n.symbol)}</b>　約帳戶資金的 <b class="num">${fmtPct(accountPct)}</b>
        <div class="action-sub num">目標梯位 ${fmtPct(Number(n.target_fraction), 0)} ·
          決策價 ${fmtMoney(n.decision_price)} USDT · ${fmtDate(n.decision_time)}收盤訊號${risk}</div>
      </div></div>`;
  }).join("");
}

/* ── 各資產梯位 ──────────────────────────────────────── */
function renderAssets(signals, budgets) {
  const grid = document.getElementById("assetGrid");
  if (!signals.length) {
    grid.innerHTML = `<div class="card"><h2>目前訊號</h2><div class="empty">尚無訊號。</div></div>`;
    return;
  }
  grid.innerHTML = signals.map((s) => {
    const frac = Number(s.exposure_fraction);
    const rungs = [1, 2, 3, 4].map((i) => `<div class="rung ${frac * 4 >= i ? "on" : ""}"></div>`).join("");
    const chips = [20, 65, 150, 200].map((n) => {
      const above = s.reason_codes.includes(`ABOVE_SMA_${n}`);
      return `<span class="chip ${above ? "on" : "off"}">${above ? "✓" : "✗"} ${SMA_LABELS[n]}</span>`;
    }).join("");
    const aboveCount = [20, 65, 150, 200].filter((n) => s.reason_codes.includes(`ABOVE_SMA_${n}`)).length;
    const summary = frac === 0 ? "全數跌破，空手觀望"
      : frac === 1 ? "站上全部四條均線，滿額持有"
      : `站上 ${aboveCount} 條均線，持有預算的 ${fmtPct(frac, 0)}`;
    const budget = Number(budgets[s.symbol] || 0);
    return `<div class="card">
      <div class="asset-head">
        <span class="asset-name">${esc(s.symbol)}</span>
        <span class="chip">風險預算 ${fmtPct(budget, 0)}</span>
        <span class="asset-frac num">${fmtPct(frac, 0)}</span>
      </div>
      <div class="ladder">${rungs}</div>
      <div class="chips">${chips}</div>
      <div class="asset-note">${summary} · 佔總帳戶 ${fmtPct(frac * budget, 1)} · ${fmtDate(s.as_of)}收盤</div>
    </div>`;
  }).join("");
}

/* ── 記分板 ──────────────────────────────────────────── */
function renderScoreboard(account, equitySeries) {
  const row = document.getElementById("scoreRow");
  if (account.status !== "OK") { row.innerHTML = `<div class="empty">尚無循環資料。</div>`; return; }
  const a = account.account;
  const initial = Number(account.initial_cash);
  const equity = Number(a.equity);
  const ret = initial > 0 ? equity / initial - 1 : 0;
  row.innerHTML = `
    <div class="metric"><div class="label">帳戶權益（USDT）</div>
      <div class="value big num">${fmtMoney(equity)}</div></div>
    <div class="metric"><div class="label">累計報酬</div>
      <div class="value num ${signCls(ret)}">${ret >= 0 ? "+" : ""}${fmtPct(ret)}</div></div>
    <div class="metric"><div class="label">現金</div>
      <div class="value num">${fmtMoney(a.cash)}</div></div>
    <div class="metric"><div class="label">目前回撤</div>
      <div class="value num ${Number(a.drawdown) > 0.3 ? "neg" : ""}">−${fmtPct(a.drawdown)}</div></div>
    <div class="metric"><div class="label">已實現損益</div>
      <div class="value num ${signCls(a.realized_pnl)}">${fmtMoney(a.realized_pnl)}</div></div>`;

  const svg = document.getElementById("spark");
  const points = equitySeries.points || [];
  if (points.length >= 2) {
    const values = points.map((p) => Number(p.equity));
    const lo = Math.min(...values), hi = Math.max(...values);
    const span = hi - lo || 1;
    const xs = (i) => (i / (values.length - 1)) * 600;
    const ys = (v) => 78 - ((v - lo) / span) * 70;
    const path = values.map((v, i) => `${i ? "L" : "M"}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join("");
    const base = ys(initial >= lo && initial <= hi ? initial : lo);
    svg.innerHTML = `
      <line x1="0" y1="${base}" x2="600" y2="${base}" stroke="#2a3542" stroke-dasharray="3 4"/>
      <path d="${path}" fill="none" stroke="#5b9dff" stroke-width="1.8"/>
      <path d="${path} L600,84 L0,84 Z" fill="rgba(91,157,255,.08)" stroke="none"/>`;
  } else { svg.innerHTML = ""; }

  const wrap = document.getElementById("positionsWrap");
  const positions = a.positions || [];
  wrap.innerHTML = positions.length
    ? `<table class="positions"><tr><th>持倉</th><th>數量</th><th>平均成本</th></tr>${
        positions.map((p) => `<tr class="num"><td>${esc(p.symbol)}</td><td>${fmtQty(p.quantity)}</td><td>${fmtMoney(p.average_entry_price)}</td></tr>`).join("")
      }</table>`
    : `<div class="empty" style="margin-top:10px">目前空手（全數持有 USDT）。</div>`;
}

/* ── 閘門 ────────────────────────────────────────────── */
function renderGate(gate) {
  const el = document.getElementById("gateBody");
  const paper = gate.paper_trading || {};
  const days = paper.days || 0;
  const target = 90;
  const holdout = gate.holdout;
  const holdoutBadge = !holdout ? `<span class="badge warn">未鎖定</span>`
    : holdout.spent ? `<span class="badge warn">已使用（單次）</span>`
    : `<span class="badge ok">已鎖定・未動用</span>`;
  const reached = days >= target ? ` <span class="badge ok">天數已達標</span>` : "";
  el.innerHTML = `
    <div class="gate-days"><span class="big num">${days}</span>
      <span class="num" style="color:var(--muted)">/ ${target} 天 paper 累積</span>${reached}</div>
    <div class="bar"><div style="width:${Math.min(100, (days / target) * 100).toFixed(1)}%"></div></div>
    <div class="kv"><span class="k">已登記試驗 N</span><b class="num">${gate.registered_trials_n}</b></div>
    <div class="kv"><span class="k">樣本外 Holdout（最近12個月）</span>${holdoutBadge}</div>
    <div class="kv"><span class="k">過關門檻</span>
      <span class="num" style="color:var(--muted)">PBO ≤ ${gate.thresholds.pbo_max} · DSR ≥ ${gate.thresholds.dsr_min}</span></div>
    <div class="kv"><span class="k">循環數</span><b class="num">${paper.cycles || 0}</b></div>`;
}

/* ── 風險 ────────────────────────────────────────────── */
function groupRuns(rows, keyOf) {
  // 相同事件的連續出現摺疊為「一列 + 次數 + 期間」，避免同類訊息洗版。
  const groups = [];
  for (const row of rows) {
    const key = keyOf(row);
    const last = groups[groups.length - 1];
    if (last && last.key === key) { last.count += 1; last.until = row.recorded_at; }
    else groups.push({ key, row, count: 1, from: row.recorded_at, until: row.recorded_at });
  }
  return groups;
}

function renderRisk(risk) {
  const el = document.getElementById("riskBody");
  const events = (risk.risk_events || []).slice(0, 5).map((e) => `
    <div class="risk-item">
      <span class="risk-when num">${e.recorded_at ? fmtDateFull(e.recorded_at) : ""}</span>
      <div><b>${esc(e.symbol)}</b> ${codeText(e.event_type)}（單日 −${fmtPct(e.observed_fraction)}）</div>
    </div>`);
  const healthGroups = groupRuns(risk.health || [], (h) => h.code).slice(0, 5).map((g) => {
    const period = g.count > 1
      ? `${fmtDateFull(g.until)} ～ ${fmtDateFull(g.from)} · ${g.count} 次`
      : fmtDateFull(g.from);
    return `<div class="risk-item"><span class="risk-when num">${period}</span>
      <div>${codeText(g.row.code)}</div></div>`;
  });
  const items = [...events, ...healthGroups];
  el.innerHTML = items.length ? items.join("") : `<div class="empty">無風險事件，一切正常。</div>`;
}

/* ── 明細 ────────────────────────────────────────────── */
function renderLogs(notifications, fills, rejections) {
  const notif = document.getElementById("notifLog");
  notif.innerHTML = notifications.length
    ? `<table class="log"><tr><th>日期</th><th>動作</th><th>標的</th><th>梯位</th><th>決策價</th></tr>${
        notifications.slice(0, 20).map((n) => {
          const isBuy = n.action === "INCREASE_EXPOSURE";
          return `<tr class="num"><td>${fmtDateFull(n.decision_time)}</td>
            <td class="${isBuy ? "side-buy" : "side-sell"}">${isBuy ? "買入" : "賣出"}</td>
            <td>${esc(n.symbol)}</td>
            <td>${fmtPct(Number(n.previous_fraction), 0)} → ${fmtPct(Number(n.target_fraction), 0)}</td>
            <td>${fmtMoney(n.decision_price)}</td></tr>`;
        }).join("")}</table>`
    : `<div class="empty">尚無通知。</div>`;

  const fill = document.getElementById("fillLog");
  fill.innerHTML = fills.length
    ? `<table class="log"><tr><th>日期</th><th>標的</th><th>方向</th><th>數量</th><th>成交價</th><th>手續費</th></tr>${
        fills.slice(0, 20).map((f) => `<tr class="num">
          <td>${f.recorded_at ? fmtDateFull(f.recorded_at) : ""}</td><td>${esc(f.symbol)}</td>
          <td class="${f.side === "BUY" ? "side-buy" : "side-sell"}">${f.side === "BUY" ? "買入" : "賣出"}</td>
          <td>${fmtQty(f.quantity)}</td><td>${fmtMoney(f.price)}</td><td>${fmtMoney(f.fee)}</td></tr>`).join("")}</table>`
    : `<div class="empty">記分板尚無成交。</div>`;

  const rej = document.getElementById("rejLog");
  const rejGroups = groupRuns(
    rejections, (r) => `${r.symbol}|${(r.reason_codes || []).join(",")}`
  ).slice(0, 12);
  rej.innerHTML = rejGroups.length
    ? `<table class="log"><tr><th>期間</th><th>標的</th><th>原因</th></tr>${
        rejGroups.map((g) => {
          const period = g.count > 1
            ? `${fmtDateFull(g.until)} ～ ${fmtDateFull(g.from)}（${g.count} 次）`
            : fmtDateFull(g.from);
          return `<tr><td class="num" style="white-space:nowrap">${period}</td>
            <td>${esc(g.row.symbol)}</td>
            <td>${(g.row.reason_codes || []).map(codeText).map(esc).join("、")}
              <span class="code">${(g.row.reason_codes || []).map(esc).join(" ")}</span></td></tr>`;
        }).join("")}</table>`
    : `<div class="empty">無被拒行動。</div>`;
}

/* ── 總覽狀態列 ──────────────────────────────────────── */
function renderStatus(account, risk) {
  const dot = document.getElementById("statusDot");
  const text = document.getElementById("statusText");
  if (account.status !== "OK") { dot.className = "dot warn"; text.textContent = "等待第一個循環"; return; }
  const healthCodes = (risk.health || []).map((h) => h.code);
  const staleness = healthCodes.includes("STALE_DATA_HALT");
  const failures = healthCodes.includes("NOTIFICATION_DELIVERY_FAILED");
  if (staleness) { dot.className = "dot warn"; text.textContent = "資料過期：已暫停加倉"; }
  else if (failures) { dot.className = "dot warn"; text.textContent = "通知投遞異常（自動重試中）"; }
  else { dot.className = "dot ok"; text.textContent = `系統正常 · 最新收盤 ${fmtDateFull(account.close_time)}`; }
}

async function refresh() {
  try {
    const [signals, account, notifications, gate, risk, fills, rejections, equity] =
      await Promise.all([
        getJson("/api/signals/current"), getJson("/api/account"),
        getJson("/api/notifications"), getJson("/api/gate"), getJson("/api/risk"),
        getJson("/api/fills"), getJson("/api/rejections"), getJson("/api/equity"),
      ]);
    renderStatus(account, risk);
    renderActions(notifications.notifications, account, signals.risk_budgets);
    renderAssets(signals.signals, signals.risk_budgets);
    renderScoreboard(account, equity);
    renderGate(gate);
    renderRisk(risk);
    renderLogs(notifications.notifications, fills.fills, rejections.rejections);
    const now = new Date();
    document.getElementById("refreshStamp").textContent =
      ` · 頁面更新於 ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}（每 60 秒自動更新）`;
  } catch (err) {
    document.getElementById("statusDot").className = "dot bad";
    document.getElementById("statusText").textContent = "無法取得資料";
    document.getElementById("actionBody").innerHTML = `<div class="err">${esc(err.message || err)}</div>`;
  }
}
refresh();
setInterval(refresh, 60000);
</script>
</body>
</html>
"""
