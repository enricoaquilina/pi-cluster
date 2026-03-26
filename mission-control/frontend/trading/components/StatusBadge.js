import { html } from '../lib.js';

const BADGE_MAP = {
  paper: 'badge-paper', live: 'badge-live',
  running: 'badge-on', stopped: 'badge-off',
  PENDING: 'badge-pending', OPEN: 'badge-open', LOCKED: 'badge-locked',
  PARTIAL: 'badge-partial', SETTLED: 'badge-settled', CANCELLED: 'badge-cancelled',
  CLOSED: 'badge-settled', WIN: 'badge-on', LOSS: 'badge-live',
};

export function StatusBadge({ status, pulse }) {
  const cls = BADGE_MAP[status] || 'badge-off';
  const dotColor = status === 'paper' || status === 'running' || status === 'OPEN' ? 'green' :
                   status === 'stopped' || status === 'CANCELLED' ? 'gray' : null;
  return html`
    <span class="badge ${cls}">
      ${pulse && dotColor && html`<span class="pulse-dot ${dotColor}"></span>`}
      ${status}
    </span>
  `;
}
