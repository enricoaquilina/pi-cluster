import { html } from '../lib.js';

export function Sparkline({ values, width = 60, height = 20, color }) {
  if (!values || values.length < 2) return html`<span>-</span>`;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const step = width / (values.length - 1);

  const points = values.map((v, i) =>
    `${(i * step).toFixed(1)},${(height - ((v - min) / range) * height).toFixed(1)}`
  ).join(' ');

  const strokeColor = color || (values[values.length - 1] >= values[0] ? '#22c55e' : '#ef4444');

  return html`
    <svg width=${width} height=${height} style="vertical-align: middle;">
      <polyline points=${points} fill="none" stroke=${strokeColor} stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  `;
}
