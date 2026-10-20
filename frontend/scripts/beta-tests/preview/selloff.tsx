// Visual preview of the Market → Selloff Monitor section with a MOCKED network. The payload is a real
// engine run on live data (see gen_selloff_fixture.py). Nothing leaves the browser.
//   npm run dev  →  http://localhost:3000/scripts/beta-tests/preview/selloff.html
//   &fail  (endpoint returns 500)  ·  &nofx  (no options cone / dealer / futures — degraded payload)
//   &slowint (internals take 8 s)  ·  &failint (internals endpoint returns 503)
//   &bookstored (book snapshot already stored on load)  ·  &nobook (no open trades)  ·  &dealer (sample dealer-gamma note)
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '../../../src/index.css';
import SelloffMonitor from '../../../src/components/SelloffMonitor';
import fixture from './selloffFixture.json';
import internalsFixture from './selloffInternalsFixture.json';
import bookFixture from './selloffBookFixture.json';
import dealerNote from './selloffDealerNoteFixture.json';
import reportFixture from './selloffReportFixture.json';

const params = new URLSearchParams(location.search);
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const degraded = () => {
  const f = JSON.parse(JSON.stringify(fixture));
  f.options_cone = { available: false, reason: 'No usable SPY option prices for the cone.' };
  f.chart.options_fan = null; f.chart.spy_implied_open = null;
  f.state.dealer = null; f.chart.levels = f.chart.levels.filter((l: { kind: string }) => l.kind !== 'dealer');
  f.overnight = { available: false };
  return f;
};
const real = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === 'string' ? input : (input as Request).url ?? input);
  if (!url.includes('/api/')) return real(input, init);
  await new Promise(r => setTimeout(r, 400));
  if (url.includes('/api/market/selloff-monitor/evidence/report')) {
    await new Promise(r => setTimeout(r, 300));
    return json(reportFixture);
  }
  if (url.includes('/api/market/selloff-monitor/book')) {
    const refresh = url.includes('refresh=true');
    if (params.has('nobook')) return json({ available: false, stored: false, reason: 'No open trades in My Trades to run through the scenarios.' });
    if (!refresh) return json(params.has('bookstored') ? { ...bookFixture, stored: true } : { available: false, stored: false });
    await new Promise(r => setTimeout(r, 1500));
    return json({ ...bookFixture, stored: false });
  }
  if (url.includes('/api/market/selloff-monitor/internals')) {
    if (params.has('failint')) return json({ detail: 'S&P 500 member list unavailable right now.' }, 503);
    await new Promise(r => setTimeout(r, params.has('slowint') ? 8000 : 1200));
    return json(internalsFixture);
  }
  if (url.includes('/api/market/selloff-monitor')) {
    if (params.has('fail')) return json({ detail: 'mock failure' }, 500);
    const base: any = params.has('nofx') ? degraded() : fixture;
    return json(params.has('dealer') ? { ...base, context_notes: dealerNote } : base);
  }
  return json({ detail: 'mock: not available in the preview' }, 404);
}) as typeof window.fetch;

function App() {
  return (
    <div className="container-app py-5">
      <div className="mb-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-1.5 text-xs text-warning">
        Preview with a MOCKED network — payload from the real engine on live data. Book = a SAMPLE book (real tickers, live prices, illustrative strikes). Variants: <code>?nofx</code> · <code>?fail</code> · <code>?slowint</code> · <code>?failint</code> · <code>?bookstored</code> · <code>?nobook</code>
      </div>
      <section className="bg-gray-900/60 rounded-2xl border border-white/10 p-6 shadow-lg">
        <SelloffMonitor />
      </section>
    </div>
  );
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><MemoryRouter><App /></MemoryRouter></React.StrictMode>);
