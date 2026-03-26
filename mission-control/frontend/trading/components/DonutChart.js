import { html, useRef, useEffect } from '../lib.js';

const COLORS = ['#22c55e', '#eab308', '#a855f7', '#38bdf8', '#f97316', '#ef4444', '#6366f1', '#8892a4'];

export function DonutChart({ title, labels, values }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!canvasRef.current || !labels || !values || values.length === 0) return;

    const loadChart = async () => {
      const { Chart, ArcElement, Tooltip, Legend, DoughnutController } = await import('chart.js');
      Chart.register(ArcElement, Tooltip, Legend, DoughnutController);

      if (chartRef.current) chartRef.current.destroy();

      chartRef.current = new Chart(canvasRef.current, {
        type: 'doughnut',
        data: {
          labels,
          datasets: [{
            data: values,
            backgroundColor: COLORS.slice(0, labels.length),
            borderColor: '#0a0e1a',
            borderWidth: 2,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          cutout: '65%',
          plugins: {
            legend: {
              position: 'right',
              labels: { color: '#8892a4', font: { size: 11 }, padding: 12 }
            },
            tooltip: {
              backgroundColor: '#1e293b',
              titleColor: '#e2e8f0',
              bodyColor: '#e2e8f0',
              borderColor: '#1e3a5f',
              borderWidth: 1,
            }
          }
        }
      });
    };
    loadChart();

    return () => { if (chartRef.current) chartRef.current.destroy(); };
  }, [labels, values]);

  return html`
    <div class="chart-container">
      ${title && html`<div class="section-title">${title}</div>`}
      <canvas ref=${canvasRef} height="200"></canvas>
    </div>
  `;
}
