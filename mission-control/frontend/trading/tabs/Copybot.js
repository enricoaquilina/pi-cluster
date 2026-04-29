import { html, useState, useEffect } from '../lib.js';
import { API } from '../api.js';
import { formatUsd, formatPct, pnlClass, formatDate } from '../lib.js';
import { MetricCard } from '../components/MetricCard.js';
import { StatusBadge } from '../components/StatusBadge.js';
import { SortableTable } from '../components/SortableTable.js';
import { Skeleton } from '../components/Skeleton.js';

export function Copybot() {
  const [summary, setSummary] = useState(null);
  const [positions, setPositions] = useState(null);
  const [traders, setTraders] = useState(null);
  const [trades, setTrades] = useState(null);
  const [live, setLive] = useState(null);
  const [tradeOffset, setTradeOffset] = useState(0);
  const [filterTrader, setFilterTrader] = useState('');
  const TRADE_LIMIT = 30;

  const load = async () => {
    try {
      const [s, p, tr, td] = await Promise.all([
        API.copybotSummary(),
        API.copybotPositions(),
        API.copybotTraders(),
        API.copybotTrades(TRADE_LIMIT, tradeOffset),
      ]);
      setSummary(s); setPositions(p); setTraders(tr); setTrades(td);
      try { setLive(await API.copybotLive()); } catch (_) {}
    } catch (e) { console.error('Copybot load error:', e); }
  };

  useEffect(() => { load(); }, [tradeOffset]);
  Copybot.refresh = load;

  if (!summary) return html`<${Skeleton} type="cards" count=${4} />`;

  const filteredPositions = filterTrader
    ? (positions || []).filter(p => p.market_title?.toLowerCase().includes(filterTrader.toLowerCase()))
    : (positions || []);
  const activePositions = filteredPositions.filter(p => !p.resolved);

  const traderColumns = [
    { key: 'trader', label: 'Trader', sortable: true },
    { key: 'executed', label: 'Executed', sortable: true, class: 'num' },
    { key: 'wins', label: 'Wins', sortable: true, class: 'num', render: v => html`<span class="text-green">${v}</span>` },
    { key: 'losses', label: 'Losses', sortable: true, class: 'num', render: v => html`<span class="text-red">${v}</span>` },
    { key: 'win_rate', label: 'Win Rate', sortable: true, class: 'num', render: v => formatPct(v) },
    { key: 'skipped', label: 'Skipped', sortable: true, class: 'num' },
    { key: 'total', label: 'Total Signals', sortable: true, class: 'num' },
  ];

  const posColumns = [
    { key: 'market_title', label: 'Market', class: 'market', render: v => v?.slice(0, 60) },
    { key: 'outcome', label: 'Outcome', sortable: true },
    { key: 'side', label: 'Side', sortable: true },
    { key: 'entry_price', label: 'Entry', sortable: true, class: 'num', render: v => `$${(v || 0).toFixed(2)}` },
    { key: 'current_price', label: 'Current', sortable: true, class: 'num', render: v => `$${(v || 0).toFixed(4)}` },
    { key: 'size', label: 'Size', sortable: true, class: 'num', render: v => (v || 0).toFixed(1) },
    { key: 'computed_pnl', label: 'PnL', sortable: true, class: 'num', render: v => html`<span class=${pnlClass(v)}>${formatUsd(v)}</span>` },
    { key: 'entry_time', label: 'Entered', sortable: true, render: v => formatDate(v) },
  ];

  const tradeColumns = [
    { key: 'detected_at', label: 'Time', sortable: true, render: v => formatDate(v) },
    { key: 'trader', label: 'Trader', sortable: true },
    { key: 'market_title', label: 'Market', class: 'market', render: v => v?.slice(0, 50) },
    { key: 'outcome', label: 'Outcome' },
    { key: 'side', label: 'Side' },
    { key: 'signal_price', label: 'Signal $', class: 'num', render: v => `$${(v || 0).toFixed(2)}` },
    { key: 'exec_price', label: 'Exec $', class: 'num', render: v => v ? `$${v.toFixed(2)}` : '-' },
    { key: 'executed', label: 'Status', render: (v, row) => v
      ? html`<${StatusBadge} status=${row.paper_result || 'OPEN'} />`
      : html`<span class="text-muted" title=${row.risk_verdict || ''}>Skipped</span>`
    },
  ];

  return html`
    <div class="metric-grid">
      <${MetricCard} label="Mode" value=${summary.mode.toUpperCase()} colorClass=${summary.mode === 'paper' ? 'text-yellow' : 'text-red'} />
      <${MetricCard} label="Total PnL" value=${formatUsd(summary.total_pnl)} colorClass=${pnlClass(summary.total_pnl)} variant=${summary.total_pnl >= 0 ? 'profit' : 'loss'} sub=${`Realized: ${formatUsd(summary.realized_pnl)} | Open: ${formatUsd(summary.unrealized_pnl)}`} />
      <${MetricCard} label="Active Positions" value=${summary.position_count} sub=${`of ${summary.max_total_positions} max`} />
      <${MetricCard} label="Daily Limit" value=${`$${summary.daily_budget_usd.toFixed(0)}/day`} sub=${`Spent today: $${summary.daily_spent_usd.toFixed(0)} (${summary.daily_date})`} />
      <${MetricCard} label="Win Rate" value=${formatPct(summary.win_rate)} sub=${`${summary.wins}W / ${summary.losses}L of ${summary.resolved_trades} resolved`} />
      <${MetricCard} label="Order Size" value=${`$${summary.order_size_usd.toFixed(2)}`} sub="per trade (drawdown adjusted)" />
    </div>

    ${live?.available && html`
      <div class="metric-grid" style="margin-top: 0.5rem;">
        <${MetricCard} label="Bot Uptime" value=${live.uptime_seconds > 3600 ? Math.floor(live.uptime_seconds/3600) + 'h' : Math.floor(live.uptime_seconds/60) + 'm'} sub=${`Updated: ${live.updated_at?.slice(11,19) || '?'}`} />
        <${MetricCard} label="Chain Monitor" value=${live.chain_monitor?.active ? 'ACTIVE' : 'OFF'} colorClass=${live.chain_monitor?.active ? 'text-green' : 'text-red'} sub=${live.chain_monitor?.active ? `${live.chain_monitor.uptime_pct}% uptime · ${live.chain_monitor.signals_today} signals today` : 'Not running'} />
        <${MetricCard} label="Today" value=${`${live.executed_today}/${live.rejected_today}`} sub=${`Executed/Rejected · ${live.wins_today}W ${live.losses_today}L`} />
        <${MetricCard} label="Balance" value=${formatUsd(live.balance)} sub=${`Portfolio: ${formatUsd(live.portfolio_value)}`} />
      </div>
    `}

    ${live?.available && live.gates && html`
      <div class="section-title">Gate Rejections</div>
      <div class="gate-stats" style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1rem;">
        ${Object.entries(live.gates.rejected_by_gate || {}).sort((a,b) => b[1]-a[1]).map(([gate, count]) =>
          html`<span class="badge" style="background:var(--surface);padding:0.25rem 0.5rem;border-radius:4px;font-size:0.85rem;">${gate}: <strong>${count}</strong></span>`
        )}
        ${Object.keys(live.gates.rejected_by_gate || {}).length === 0 && html`<span class="text-muted">No rejections today</span>`}
      </div>
    `}

    ${live?.available && live.market_types && html`
      <div class="section-title">Market Types</div>
      <div style="overflow-x:auto;margin-bottom:1rem;">
        <table class="data-table">
          <thead><tr><th>Type</th><th class="num">Open</th><th class="num">Resolved</th><th class="num">W/L</th><th class="num">Win Rate</th><th class="num">Realized</th><th class="num">Unrealized</th></tr></thead>
          <tbody>
            ${Object.entries(live.market_types).map(([type, s]) => html`
              <tr>
                <td>${type}</td>
                <td class="num">${s.open}</td>
                <td class="num">${s.resolved}</td>
                <td class="num"><span class="text-green">${s.wins}</span>/<span class="text-red">${s.losses}</span></td>
                <td class="num">${s.win_rate != null ? formatPct(s.win_rate * 100) : '-'}</td>
                <td class="num"><span class=${pnlClass(s.realized_pnl)}>${formatUsd(s.realized_pnl)}</span></td>
                <td class="num"><span class=${pnlClass(s.unrealized_pnl)}>${formatUsd(s.unrealized_pnl)}</span></td>
              </tr>
            `)}
          </tbody>
        </table>
      </div>
    `}

    <div class="section-title">Trader Performance</div>
    <${SortableTable} columns=${traderColumns} data=${traders} emptyText="No trader data" />

    <div class="section-title">Active Positions (${activePositions.length})</div>
    <div class="filter-bar">
      <input type="text" placeholder="Filter markets..." value=${filterTrader} onInput=${e => setFilterTrader(e.target.value)} />
    </div>
    <${SortableTable} columns=${posColumns} data=${activePositions} emptyText="No active positions" />

    <div class="section-title">Trade Log</div>
    <${SortableTable} columns=${tradeColumns} data=${trades?.items} emptyText="No trades yet" />
    ${trades && html`
      <div class="pagination">
        <button disabled=${tradeOffset === 0} onclick=${() => setTradeOffset(Math.max(0, tradeOffset - TRADE_LIMIT))}>Prev</button>
        <span>${tradeOffset + 1}–${Math.min(tradeOffset + TRADE_LIMIT, trades.total)} of ${trades.total}</span>
        <button disabled=${tradeOffset + TRADE_LIMIT >= trades.total} onclick=${() => setTradeOffset(tradeOffset + TRADE_LIMIT)}>Next</button>
      </div>
    `}
  `;
}
