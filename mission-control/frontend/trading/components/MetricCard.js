import { html } from '../lib.js';

export function MetricCard({ label, value, sub, colorClass, variant }) {
  const cardClass = variant === 'profit' ? 'metric-card profit' :
                    variant === 'loss' ? 'metric-card loss' : 'metric-card';
  return html`
    <div class=${cardClass}>
      <div class="metric-label">${label}</div>
      <div class="metric-value ${colorClass || ''}">${value}</div>
      ${sub && html`<div class="metric-sub">${sub}</div>`}
    </div>
  `;
}
