// Visual preview of Income Desk → Screener with a MOCKED network. Payloads are real engine runs on live data
// (see gen_income_screener_fixture.py): an earnings-window screen, event-expiry vol metrics, and sell-the-print
// evaluations. Nothing leaves the browser.
//   npm run dev  →  http://localhost:3000/scripts/beta-tests/preview/incomeScreener.html
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '../../../src/index.css';
import { IncomeScreener } from '../../../src/components/IncomeScreener';
import fixture from './incomeScreenerFixture.json';

const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const volBy = Object.fromEntries((fixture.vol as any[]).map(v => [v.ticker, v]));
const real = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === 'string' ? input : (input as Request).url ?? input);
  if (!url.includes('/api/')) return real(input, init);
  await new Promise(r => setTimeout(r, 300));
  if (url.includes('/income-screener/screen')) return json(fixture.screen);
  if (url.includes('/income-screener/vol-metrics')) {
    const items = JSON.parse(String(init?.body ?? '{}')).items ?? [];
    return json(items.map((i: { ticker: string }) => volBy[i.ticker]).filter(Boolean));
  }
  const m = url.match(/\/income-screener\/evaluate\/([^/?]+)/);
  if (m) {
    const t = decodeURIComponent(m[1]);
    await new Promise(r => setTimeout(r, 600));
    return json((fixture.evals as any)[t] ?? { ticker: t, trades: [], skipped: 'mock: not evaluated in the preview fixture' });
  }
  return json({ detail: 'mock: not available in the preview' }, 404);
}) as typeof window.fetch;

function App() {
  return (
    <div className="container-app py-5">
      <div className="mb-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-1.5 text-xs text-warning">
        Preview with a MOCKED network — payloads from the real screener engine on live data (earnings window: within 2 trading days).
      </div>
      <section className="bg-gray-900/60 rounded-2xl border border-white/10 p-6 shadow-lg">
        <IncomeScreener onOpenTicker={(t, o) => alert(`Desk → ${t} ${o.expiry ?? ''}`)} />
      </section>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><MemoryRouter><App /></MemoryRouter></React.StrictMode>);
