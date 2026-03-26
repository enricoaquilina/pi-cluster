import { html, useState, useCallback } from '../lib.js';

export function SortableTable({ columns, data, onSort, emptyText }) {
  const [sortCol, setSortCol] = useState(null);
  const [sortDir, setSortDir] = useState('desc');

  const handleSort = useCallback((col) => {
    if (!col.sortable) return;
    const newDir = sortCol === col.key && sortDir === 'desc' ? 'asc' : 'desc';
    setSortCol(col.key);
    setSortDir(newDir);
    if (onSort) onSort(col.key, newDir);
  }, [sortCol, sortDir, onSort]);

  const sorted = [...(data || [])];
  if (sortCol) {
    sorted.sort((a, b) => {
      let va = a[sortCol], vb = b[sortCol];
      if (va == null) va = '';
      if (vb == null) vb = '';
      if (typeof va === 'number' && typeof vb === 'number') {
        return sortDir === 'asc' ? va - vb : vb - va;
      }
      const sa = String(va), sb = String(vb);
      return sortDir === 'asc' ? sa.localeCompare(sb) : sb.localeCompare(sa);
    });
  }

  if (!data || data.length === 0) {
    return html`<div class="empty-state"><div class="icon">-</div><div>${emptyText || 'No data'}</div></div>`;
  }

  return html`
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            ${columns.map(col => html`
              <th class=${sortCol === col.key ? 'sorted' : ''}
                  onclick=${() => handleSort(col)}>
                ${col.label}
                ${col.sortable && sortCol === col.key
                  ? html`<span class="sort-arrow">${sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>`
                  : ''}
              </th>
            `)}
          </tr>
        </thead>
        <tbody>
          ${sorted.map(row => html`
            <tr>
              ${columns.map(col => html`
                <td class=${col.class || ''}>
                  ${col.render ? col.render(row[col.key], row) : row[col.key]}
                </td>
              `)}
            </tr>
          `)}
        </tbody>
      </table>
    </div>
  `;
}
