import { html, useState, useEffect } from '../lib.js';
import { API } from '../api.js';
import { formatUsd, formatPct, pnlClass, formatTime } from '../lib.js';
import { MetricCard } from '../components/MetricCard.js';
import { StatusBadge } from '../components/StatusBadge.js';
import { Skeleton } from '../components/Skeleton.js';

export function Overview() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const load = async () => {
    try { setData(await API.overview()); setError(null); }
    catch (e) { setError(e.message); }
  };

  useEffect(() => { load(); }, []);

  // Expose refresh for parent polling
  Overview.refresh = load;

  if (error) return html`<div class="empty-state"><div class="icon">!</div><div>Error: ${error}</div></div>`;
  if (!data) return html`<${Skeleton} type="cards" count=${4} />`;

  const { copybot: cb, spreadbot: sb, scalper: sc } = data;
  const pnlVariant = data.total_pnl >= 0 ? 'profit' : 'loss';

  return html`
    <div class="metric-grid">
      <${MetricCard} label="Total PnL" value=${formatUsd(data.total_pnl)} colorClass=${pnlClass(data.total_pnl)} variant=${pnlVariant} />
      <${MetricCard} label="Active Positions" value=${data.total_positions} sub=${`${cb.position_count} copy + ${(sb.state_counts.LOCKED || 0) + (sb.state_counts.PARTIAL || 0)} spread + ${sc.active_count} scalp`} />
      <${MetricCard} label="Daily Limit" value=${`$${data.daily_budget_total.toFixed(0)}/day`} sub=${`Spent today: $${data.daily_spent_total.toFixed(0)}`} />
      <${MetricCard} label="Win Rate" value=${formatPct(cb.win_rate)} sub=${`${cb.wins}W / ${cb.losses}L (copybot)`} />
    </div>

    <div class="bot-grid">
      <div class="bot-card">
        <div class="bot-card-header">
          <span class="bot-card-title">Copybot</span>
          <${StatusBadge} status=${cb.mode} pulse=${true} />
        </div>
        <div class="bot-card-stat">${cb.position_count} active positions</div>
        <div class="bot-card-stat">PnL: <strong class=${pnlClass(cb.total_pnl)}>${formatUsd(cb.total_pnl)}</strong> <span class="text-dim">(${formatUsd(cb.realized_pnl)} realized)</span></div>
        <div class="bot-card-stat">Trades: <strong>${cb.total_trades}</strong> executed</div>
        <div class="bot-card-stat">Traders: <strong>${cb.enabled_traders.join(', ')}</strong></div>
      </div>

      <div class="bot-card">
        <div class="bot-card-header">
          <span class="bot-card-title">Spreadbot</span>
          <${StatusBadge} status=${sb.mode} pulse=${true} />
        </div>
        <div class="bot-card-stat">${sb.total_pairs} total pairs</div>
        <div class="bot-card-stat">Settled PnL: <strong class=${pnlClass(sb.settled_pnl)}>${formatUsd(sb.settled_pnl)}</strong></div>
        <div class="bot-card-stat">Exposure: <strong>$${sb.active_exposure.toFixed(2)}</strong></div>
        <div class="bot-card-stat">States: ${Object.entries(sb.state_counts).map(([k,v]) => `${v} ${k.toLowerCase()}`).join(', ') || 'none'}</div>
      </div>

      <div class="bot-card">
        <div class="bot-card-header">
          <span class="bot-card-title">Scalper</span>
          ${sc.enabled
            ? html`<${StatusBadge} status="running" pulse=${true} />`
            : html`<${StatusBadge} status="stopped" />`
          }
        </div>
        <div class="bot-card-stat">${sc.active_count} active / ${sc.closed_count} closed</div>
        <div class="bot-card-stat">PnL: <strong class=${pnlClass(sc.total_pnl)}>${formatUsd(sc.total_pnl)}</strong></div>
        <div class="bot-card-stat">Win rate: <strong>${formatPct(sc.win_rate)}</strong></div>
      </div>
    </div>

    <div class="section-title">Recent Activity</div>
    <div class="activity-feed">
      ${data.recent_activity.length === 0
        ? html`<div class="empty-state">No recent activity</div>`
        : data.recent_activity.slice(0, 15).map(item => html`
          <div class="activity-item">
            <span class="activity-time">${formatTime(item.time)}</span>
            <span class="activity-icon">${item.bot === 'copybot' ? (item.action === 'COPY' ? '\u{1F4CB}' : '\u23ED') : '\u{1F4B1}'}</span>
            <span class="activity-text">
              ${item.bot === 'copybot' ? `${item.action} ${item.detail}` : `${item.action} ${item.detail}`}
              ${item.result ? html` <${StatusBadge} status=${item.result} />` : ''}
              ${item.pnl != null && item.pnl !== 0 ? html` <span class=${pnlClass(item.pnl)}>${formatUsd(item.pnl)}</span>` : ''}
            </span>
          </div>
        `)
      }
    </div>
  `;
}
