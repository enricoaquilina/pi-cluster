import { html, render, useState, useEffect, useCallback } from './lib.js';
import { clearCache } from './api.js';
import { Overview } from './tabs/Overview.js';
import { Copybot } from './tabs/Copybot.js';
import { Spreadbot } from './tabs/Spreadbot.js';
import { Scalper } from './tabs/Scalper.js';
import { Backtest } from './tabs/Backtest.js';

const TABS = [
  { id: 'overview', label: 'Overview', component: Overview },
  { id: 'copybot', label: 'Copybot', component: Copybot },
  { id: 'spreadbot', label: 'Spreadbot', component: Spreadbot },
  { id: 'scalper', label: 'Scalper', component: Scalper },
  { id: 'backtest', label: 'Backtest', component: Backtest },
];

const POLL_INTERVAL = 30000;
const LS_TAB_KEY = 'trading-active-tab';

function App() {
  const savedTab = localStorage.getItem(LS_TAB_KEY) || 'overview';
  const [activeTab, setActiveTab] = useState(savedTab);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [syncing, setSyncing] = useState(false);

  const switchTab = useCallback((id) => {
    setActiveTab(id);
    localStorage.setItem(LS_TAB_KEY, id);
    window.location.hash = id;
  }, []);

  // Hash-based routing
  useEffect(() => {
    const onHash = () => {
      const hash = window.location.hash.slice(1);
      if (hash && TABS.some(t => t.id === hash)) {
        setActiveTab(hash);
        localStorage.setItem(LS_TAB_KEY, hash);
      }
    };
    window.addEventListener('hashchange', onHash);
    onHash();
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // Polling
  useEffect(() => {
    const poll = async () => {
      setSyncing(true);
      clearCache();
      const tab = TABS.find(t => t.id === activeTab);
      if (tab?.component?.refresh) {
        try { await tab.component.refresh(); } catch (e) { console.error('Poll error:', e); }
      }
      setLastUpdated(new Date());
      setSyncing(false);
    };

    // Initial fetch timestamp
    setLastUpdated(new Date());

    const interval = setInterval(poll, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [activeTab]);

  const elapsed = lastUpdated ? Math.floor((Date.now() - lastUpdated) / 1000) : null;

  const ActiveComponent = TABS.find(t => t.id === activeTab)?.component || Overview;

  return html`
    <div class="header">
      <div class="header-left">
        <a href="/">← Mission Control</a>
        <h1><span>Trading</span> Dashboard</h1>
      </div>
      <div class="header-right">
        <span>${syncing ? 'Syncing...' : elapsed != null ? `Updated ${elapsed}s ago` : ''}</span>
        <a href="https://n8n.siliconsentiments.work" target="_blank" rel="noopener">n8n ↗</a>
      </div>
    </div>
    <div class="tab-bar">
      ${TABS.map(tab => html`
        <button class="tab-btn ${activeTab === tab.id ? 'active' : ''}"
                onclick=${() => switchTab(tab.id)}>
          ${tab.label}
        </button>
      `)}
    </div>
    <div class="content">
      <${ActiveComponent} />
    </div>
  `;
}

render(html`<${App} />`, document.getElementById('app'));

// Update "Updated Xs ago" every second
setInterval(() => {
  const el = document.querySelector('.header-right span');
  if (el && !el.textContent.includes('Syncing')) {
    // Force re-render by dispatching a minimal event
  }
}, 5000);
