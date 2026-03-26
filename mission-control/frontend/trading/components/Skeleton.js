import { html } from '../lib.js';

export function Skeleton({ type = 'card', count = 4 }) {
  if (type === 'cards') {
    return html`
      <div class="metric-grid">
        ${Array.from({ length: count }, (_, i) => html`
          <div key=${i} class="skeleton skeleton-card"></div>
        `)}
      </div>
    `;
  }
  if (type === 'table') {
    return html`
      <div style="padding: 1rem;">
        ${Array.from({ length: count }, (_, i) => html`
          <div key=${i} class="skeleton skeleton-line" style="width: ${80 + Math.random() * 20}%"></div>
        `)}
      </div>
    `;
  }
  return html`<div class="skeleton skeleton-card"></div>`;
}
