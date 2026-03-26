import { html, useState, useEffect } from '../lib.js';
import { API } from '../api.js';
import { formatUsd, formatPct, pnlClass, formatDate } from '../lib.js';
import { MetricCard } from '../components/MetricCard.js';
import { StatusBadge } from '../components/StatusBadge.js';
import { SortableTable } from '../components/SortableTable.js';
import { DonutChart } from '../components/DonutChart.js';
import { Skeleton } from '../components/Skeleton.js';

export function Scalper() {
  const [summary, setSummary] = useState(null);
  const [positions, setPositions] = useState(null);

  const load = async () => {
    try {
      const [s, p] = await Promise.all([
        API.scalperSummary(),
        API.scalperPositions(),
      ]);
      setSummary(s); setPositions(p);
    } catch (e) { console.error('Scalper load error:', e); }
  };

  useEffect(() => { load(); }, []);
  Scalper.refresh = load;

  if (!summary) return html`<${Skeleton} type="cards" count=${4} />`;

  const reasonLabels = Object.keys(summary.close_reasons);
  const reasonValues = Object.values(summary.close_reasons);

  const posColumns = [
    { key: 'market_title', label: 'Market', class: 'market', render: (v, row) => (v || row.market_slug || '-').slice(0, 50) },
    { key: 'outcome', label: 'Outcome' },
    { key: 'state', label: 'State', sortable: true, render: v => html`<${StatusBadge} status=${v} />` },
    { key: 'entry_price', label: 'Entry', class: 'num', sortable: true, render: v => v ? `$${v.toFixed(3)}` : '-' },
    { key: 'target_price', label: 'Target', class: 'num', render: v => v ? `$${v.toFixed(3)}` : '-' },
    { key: 'stop_loss_price', label: 'Stop', class: 'num', render: v => v ? `$${v.toFixed(3)}` : '-' },
    { key: 'close_reason', label: 'Close Reason', sortable: true, render: v => v || '-' },
    { key: 'pnl', label: 'PnL', class: 'num', sortable: true, render: v => html`<span class=${pnlClass(v)}>${formatUsd(v || 0)}</span>` },
    { key: 'created_at', label: 'Created', sortable: true, render: v => formatDate(v) },
  ];

  return html`
    <div class="metric-grid">
      <${MetricCard} label="Status" value=${summary.enabled ? 'ENABLED' : 'DISABLED'} colorClass=${summary.enabled ? 'text-green' : 'text-muted'} />
      <${MetricCard} label="Active / Closed" value=${`${summary.active_count} / ${summary.closed_count}`} />
      <${MetricCard} label="PnL" value=${formatUsd(summary.total_pnl)} colorClass=${pnlClass(summary.total_pnl)} variant=${summary.total_pnl >= 0 ? 'profit' : 'loss'} />
      <${MetricCard} label="Win Rate" value=${formatPct(summary.win_rate)} sub=${`${summary.wins}W / ${summary.losses}L`} />
      <${MetricCard} label="Daily Budget" value=${`$${summary.daily_spent_usd.toFixed(0)} / $${summary.daily_budget_usd.toFixed(0)}`} />
      <${MetricCard} label="Exposure" value=${`$${(summary.exposure || 0).toFixed(2)}`} />
    </div>

    <div class="chart-row">
      ${reasonLabels.length > 0 ? html`
        <${DonutChart} title="Close Reasons" labels=${reasonLabels} values=${reasonValues} />
      ` : html`<div></div>`}
      <div class="config-card">
        <div class="section-title">Configuration</div>
        <div class="config-grid">
          <div class="config-item"><div class="label">Target</div><div class="value">${(summary.target_cents * 100).toFixed(0)} cents</div></div>
          <div class="config-item"><div class="label">Stop-Loss</div><div class="value">${(summary.stop_loss_cents * 100).toFixed(0)} cents</div></div>
          <div class="config-item"><div class="label">Max Hold</div><div class="value">${summary.max_hold_minutes} min</div></div>
          <div class="config-item"><div class="label">Max Concurrent</div><div class="value">${summary.max_concurrent}</div></div>
          <div class="config-item"><div class="label">Order Size</div><div class="value">$${summary.order_size_usd}</div></div>
          <div class="config-item"><div class="label">Mode</div><div class="value">${summary.mode}</div></div>
        </div>
      </div>
    </div>

    <div class="section-title">Positions (${(positions || []).length})</div>
    <${SortableTable} columns=${posColumns} data=${positions} emptyText="No scalp positions" />
  `;
}
