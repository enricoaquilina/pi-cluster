const cache = new Map();
const CACHE_TTL = 30000; // 30s

export async function fetchApi(url) {
  const cached = cache.get(url);
  const now = Date.now();
  if (cached && now - cached.ts < CACHE_TTL) return cached.data;

  const res = await fetch(url);
  if (!res.ok) throw new Error(`API ${res.status}: ${res.statusText}`);
  const data = await res.json();
  cache.set(url, { data, ts: now });
  return data;
}

export function clearCache(url) {
  if (url) cache.delete(url);
  else cache.clear();
}

export const API = {
  overview: () => fetchApi('/api/trading/overview'),
  copybotSummary: () => fetchApi('/api/trading/copybot/summary'),
  copybotPositions: () => fetchApi('/api/trading/copybot/positions'),
  copybotTrades: (limit = 50, offset = 0) => fetchApi(`/api/trading/copybot/trades?limit=${limit}&offset=${offset}`),
  copybotTraders: () => fetchApi('/api/trading/copybot/traders'),
  spreadbotSummary: () => fetchApi('/api/trading/spreadbot/summary'),
  spreadbotPairs: (state = '', limit = 50, offset = 0) => fetchApi(`/api/trading/spreadbot/pairs?state=${state}&limit=${limit}&offset=${offset}`),
  scalperSummary: () => fetchApi('/api/trading/scalper/summary'),
  scalperPositions: () => fetchApi('/api/trading/scalper/positions'),
  backtest: () => fetchApi('/api/trading/backtest'),
};
