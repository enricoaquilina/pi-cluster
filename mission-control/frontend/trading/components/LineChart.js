import { html, useRef, useEffect } from '../lib.js';

export function LineChart({ title, labels, datasets }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!canvasRef.current || !labels || !datasets) return;

    const loadChart = async () => {
      const { Chart, LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend, LineController, Filler } = await import('chart.js');
      Chart.register(LineElement, PointElement, LinearScale, CategoryScale, Tooltip, Legend, LineController, Filler);

      if (chartRef.current) chartRef.current.destroy();

      chartRef.current = new Chart(canvasRef.current, {
        type: 'line',
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          interaction: { intersect: false, mode: 'index' },
          scales: {
            x: { ticks: { color: '#8892a4', font: { size: 10 } }, grid: { color: 'rgba(30,58,95,0.3)' } },
            y: { ticks: { color: '#8892a4', font: { size: 10 } }, grid: { color: 'rgba(30,58,95,0.3)' } },
          },
          plugins: {
            legend: { labels: { color: '#8892a4', font: { size: 11 } } },
            tooltip: { backgroundColor: '#1e293b', titleColor: '#e2e8f0', bodyColor: '#e2e8f0', borderColor: '#1e3a5f', borderWidth: 1 },
          }
        }
      });
    };
    loadChart();

    return () => { if (chartRef.current) chartRef.current.destroy(); };
  }, [labels, datasets]);

  return html`
    <div class="chart-container">
      ${title && html`<div class="section-title">${title}</div>`}
      <canvas ref=${canvasRef} height="200"></canvas>
    </div>
  `;
}
