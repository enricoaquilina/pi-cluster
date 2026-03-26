import { html, useState, useEffect } from '../lib.js';
import { API } from '../api.js';
import { formatUsd, pnlClass, formatDate } from '../lib.js';
import { MetricCard } from '../components/MetricCard.js';
import { StatusBadge } from '../components/StatusBadge.js';
import { SortableTable } from '../components/SortableTable.js';
import { DonutChart } from '../components/DonutChart.js';
import { Skeleton } from '../components/Skeleton.js';

export function Spreadbot() {
  const [summary, setSummary] = useState(null);
  const [pairs, setPairs] = useState(null);
  const [stateFilter, setStateFilter] = useState('');
  const [offset, setOffset] = useState(0);
  const LIMIT = 50;

  const load = async () => {
    try {
      const [s, p] = await Promise.all([
        API.spreadbotSummary(),
        API.spreadbotPairs(stateFilter, LIMIT, offset),
      ]);
      setSummary(s); setPairs(p);
    } catch (e) { console.error('Spreadbot load error:', e); }
  };

  useEffect(() => { load(); }, [stateFilter, offset]);
  Spreadbot.refresh = load;

  if (!summary) return html`<${Skeleton} type="cards" count=${4} />`;

  const stateLabels = Object.keys(summary.state_counts);
  const stateValues = Object.values(summary.state_counts);

  const pairColumns = [
    { key: 'market_title', label: 'Market', class: 'market', render: v => v?.slice(0, 50) },
    { key: 'state', label: 'State', sortable: true, render: v => html`<${StatusBadge} status=${v} />` },
    { key: 'yes_bid_price', label: 'Yes Bid', class: 'num', sortable: true, render: v => `$${(v || 0).toFixed(2)}` },
    { key: 'no_bid_price', label: 'No Bid', class: 'num', sortable: true, render: v => `$${(v || 0).toFixed(2)}` },
    { key: 'edge_usd', label: 'Edge', class: 'num', sortable: true, render: v => html`<span class=${pnlClass(v)}>${formatUsd(v)}</span>` },
    { key: 'cost_usd', label: 'Cost', class: 'num', sortable: true, render: v => `$${(v || 0).toFixed(2)}` },
    { key: 'pnl', label: 'PnL', class: 'num', sortable: true, render: v => html`<span class=${pnlClass(v)}>${formatUsd(v)}</span>` },
    { key: 'tighten_count', label: 'Tightens', class: 'num', sortable: true },
    { key: 'created_at', label: 'Created', sortable: true, render: v => formatDate(v) },
  ];

  const allStates = ['', 'PENDING', 'PARTIAL', 'LOCKED', 'SETTLED', 'CANCELLED'];

  return html`
    <div class="metric-grid">
      <${MetricCard} label="Mode" value=${summary.mode.toUpperCase()} colorClass=${summary.mode === 'paper' ? 'text-yellow' : 'text-red'} />
      <${MetricCard} label="Settled PnL" value=${formatUsd(summary.settled_pnl)} colorClass=${pnlClass(summary.settled_pnl)} variant=${summary.settled_pnl >= 0 ? 'profit' : 'loss'} />
      <${MetricCard} label="Total Pairs" value=${summary.total_pairs} sub=${Object.entries(summary.state_counts).map(([k,v]) => `${v} ${k.toLowerCase()}`).join(', ')} />
      <${MetricCard} label="Active Exposure" value=${`$${summary.active_exposure.toFixed(2)}`} sub=${`of $${summary.max_exposure_usd} max`} />
      <${MetricCard} label="Daily Budget" value=${`$${summary.daily_spent_usd.toFixed(0)} / $${summary.daily_budget_usd.toFixed(0)}`} />
      <${MetricCard} label="Order Size" value=${`$${summary.order_size_usd.toFixed(0)}`} sub=${`Base spread: ${(summary.base_spread * 100).toFixed(0)}%`} />
    </div>

    ${stateLabels.length > 0 && html`
      <div class="chart-row">
        <${DonutChart} title="Pair States" labels=${stateLabels} values=${stateValues} />
        <div class="config-card">
          <div class="section-title">Configuration</div>
          <div class="config-grid">
            <div class="config-item"><div class="label">Max Pairs</div><div class="value">${summary.max_pairs}</div></div>
            <div class="config-item"><div class="label">Max Exposure</div><div class="value">$${summary.max_exposure_usd}</div></div>
            <div class="config-item"><div class="label">Base Spread</div><div class="value">${(summary.base_spread * 100).toFixed(1)}%</div></div>
            <div class="config-item"><div class="label">Fee-Free Only</div><div class="value">${summary.fee_free_only ? 'Yes' : 'No'}</div></div>
          </div>
        </div>
      </div>
    `}

    <div class="section-title">Pairs</div>
    <div class="filter-bar">
      <select value=${stateFilter} onChange=${e => { setStateFilter(e.target.value); setOffset(0); }}>
        ${allStates.map(s => html`<option value=${s}>${s || 'All States'}</option>`)}
      </select>
    </div>
    <${SortableTable} columns=${pairColumns} data=${pairs?.items} emptyText="No pairs found" />
    ${pairs && html`
      <div class="pagination">
        <button disabled=${offset === 0} onclick=${() => setOffset(Math.max(0, offset - LIMIT))}>Prev</button>
        <span>${offset + 1}–${Math.min(offset + LIMIT, pairs.total)} of ${pairs.total}</span>
        <button disabled=${offset + LIMIT >= pairs.total} onclick=${() => setOffset(offset + LIMIT)}>Next</button>
      </div>
    `}
  `;
}
