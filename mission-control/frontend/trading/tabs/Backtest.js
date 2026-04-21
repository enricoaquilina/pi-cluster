import { html, useState, useEffect, useRef } from '../lib.js';
import { API } from '../api.js';
import { formatUsd, formatPct, pnlClass } from '../lib.js';
import { SortableTable } from '../components/SortableTable.js';
import { MetricCard } from '../components/MetricCard.js';
import { Skeleton } from '../components/Skeleton.js';

export function Backtest() {
  const [data, setData] = useState(null);
  const chartRef = useRef(null);
  const chartInstanceRef = useRef(null);

  const load = async () => {
    try { setData(await API.backtest()); }
    catch (e) { console.error('Backtest load error:', e); }
  };

  useEffect(() => { load(); }, []);
  Backtest.refresh = load;

  // ROI bar chart
  useEffect(() => {
    if (!data || !chartRef.current) return;

    const renderChart = async () => {
      const { Chart, BarElement, BarController, CategoryScale, LinearScale, Tooltip, Legend } = await import('chart.js');
      Chart.register(BarElement, BarController, CategoryScale, LinearScale, Tooltip, Legend);

      if (chartInstanceRef.current) chartInstanceRef.current.destroy();

      // Use report data, filter to realistic slippage, exclude PORTFOLIO summary row
      const realistic = (data.report || []).filter(r =>
        (r.slippage_model === 'realistic' || r.slippage_model === 'optimistic') && r.trader !== 'PORTFOLIO'
      );
      // Deduplicate: keep one entry per trader (prefer realistic)
      const byTrader = {};
      for (const r of realistic) {
        if (!byTrader[r.trader] || r.slippage_model === 'realistic') byTrader[r.trader] = r;
      }
      const sorted = Object.values(byTrader).sort((a, b) => b.roi_pct - a.roi_pct).slice(0, 20);
      const enabled = new Set(data.enabled_traders || []);

      chartInstanceRef.current = new Chart(chartRef.current, {
        type: 'bar',
        data: {
          labels: sorted.map(r => r.trader),
          datasets: [{
            label: 'ROI %',
            data: sorted.map(r => r.roi_pct),
            backgroundColor: sorted.map(r => enabled.has(r.trader) ? '#22c55e' : '#38bdf8'),
            borderColor: sorted.map(r => enabled.has(r.trader) ? '#16a34a' : '#0ea5e9'),
            borderWidth: 1,
          }]
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: '#8892a4', callback: v => `${v}%` }, grid: { color: 'rgba(30,58,95,0.3)' } },
            y: { ticks: { color: '#8892a4', font: { size: 11 } }, grid: { display: false } },
          },
          plugins: {
            legend: { display: false },
            tooltip: { backgroundColor: '#1e293b', titleColor: '#e2e8f0', bodyColor: '#e2e8f0', borderColor: '#1e3a5f', borderWidth: 1 },
          }
        }
      });
    };
    renderChart();

    return () => { if (chartInstanceRef.current) chartInstanceRef.current.destroy(); };
  }, [data]);

  if (!data) return html`<${Skeleton} type="cards" count=${4} />`;

  const enabled = new Set(data.enabled_traders || []);

  // Leaderboard table
  const lbColumns = [
    { key: 'name', label: 'Trader', sortable: true, render: (v) => html`
      <span>${enabled.has(v) ? '\u2B50 ' : ''}${v}</span>
    ` },
    { key: 'trade_count', label: 'Trades', sortable: true, class: 'num' },
    { key: 'unique_markets', label: 'Markets', sortable: true, class: 'num' },
    { key: 'win_rate', label: 'Win Rate', sortable: true, class: 'num', render: v => formatPct(v) },
    { key: 'roi_pct', label: 'ROI', sortable: true, class: 'num', render: v => html`<span class=${pnlClass(v)}>${formatPct(v)}</span>` },
    { key: 'net_pnl', label: 'Net PnL', sortable: true, class: 'num', render: v => html`<span class=${pnlClass(v)}>${formatUsd(v)}</span>` },
    { key: 'span_days', label: 'Span (days)', sortable: true, class: 'num', render: v => (v || 0).toFixed(1) },
    { key: 'trades_per_day', label: 'Trades/Day', sortable: true, class: 'num', render: v => (v || 0).toFixed(1) },
  ];

  // Backtest report table
  const reportColumns = [
    { key: 'trader', label: 'Trader', sortable: true, render: v => html`
      <span>${enabled.has(v) ? '\u2B50 ' : ''}${v}</span>
    ` },
    { key: 'slippage_model', label: 'Slippage', sortable: true },
    { key: 'num_trades', label: 'Trades', sortable: true, class: 'num' },
    { key: 'win_rate', label: 'Win Rate', sortable: true, class: 'num', render: v => formatPct(v) },
    { key: 'roi_pct', label: 'ROI', sortable: true, class: 'num', render: v => html`<span class=${pnlClass(v)}>${formatPct(v)}</span>` },
    { key: 'total_pnl', label: 'PnL', sortable: true, class: 'num', render: v => html`<span class=${pnlClass(v)}>${formatUsd(v)}</span>` },
    { key: 'sharpe', label: 'Sharpe', sortable: true, class: 'num', render: v => (v || 0).toFixed(2) },
    { key: 'max_drawdown', label: 'Max DD', sortable: true, class: 'num', render: v => `$${(v || 0).toFixed(0)}` },
  ];

  const portfolio = (data.report || []).find(r => r.trader === 'PORTFOLIO');
  const traderReport = (data.report || []).filter(r => r.trader !== 'PORTFOLIO');

  return html`
    ${portfolio && html`
      <div class="section-title">Portfolio Backtest Summary</div>
      <div class="metric-grid">
        <${MetricCard} label="Trades" value=${(portfolio.num_trades || 0).toLocaleString()} sub="with 2% slippage + 2% fees" />
        <${MetricCard} label="Win Rate" value=${formatPct(portfolio.win_rate)} />
        <${MetricCard} label="Max Drawdown" value=${formatPct(portfolio.max_drawdown)} colorClass="loss" />
        <${MetricCard} label="Bankroll" value="$100" sub="9 traders, proportional sizing" />
      </div>
    `}

    <div class="section-title">ROI Comparison (per trader)</div>
    <div class="chart-container" style="height: 400px;">
      <canvas ref=${chartRef}></canvas>
    </div>

    ${data.leaderboard.length > 0 && html`
      <div class="section-title">Leaderboard (Live Activity)</div>
      <${SortableTable} columns=${lbColumns} data=${data.leaderboard} emptyText="No leaderboard data" />
    `}

    <div class="section-title">Backtest Results</div>
    <${SortableTable} columns=${reportColumns} data=${traderReport} emptyText="No backtest data" />

    <div style="margin-top: 1rem; font-size: 0.78rem; color: var(--muted);">
      \u2B50 = Currently enabled trader. Green bars = enabled traders in chart.
    </div>
  `;
}
