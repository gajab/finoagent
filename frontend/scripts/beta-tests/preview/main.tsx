// Visual preview of the Beta Technical view with a MOCKED network (synthetic data, no real tickers, nothing leaves the browser).
//   npm run dev  →  http://localhost:3000/scripts/beta-tests/preview/index.html
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '../../../src/index.css';
import BetaTechnical from '../../../src/beta/ta/BetaTechnical';
import * as F from './fixtures';
import momentumFragile from './momentumFragile.json';   // REAL engine on a synthetic 4/4-fragile series

const params = new URLSearchParams(location.search);
const noIv = params.has('noiv'), fail = params.get('fail');
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const real = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === 'string' ? input : (input as Request).url ?? input);
  if (!url.includes('/api/')) return real(input, init);
  await new Promise(r => setTimeout(r, 250 + Math.random() * 400));
  if (fail && url.includes(fail)) return json({ detail: 'mock failure' }, 500);
  if (url.includes('/trader-strategies')) return json(F.traderStrategies);
  if (url.includes('/candles')) return json(F.candles(new URL(url, location.origin).searchParams.get('interval') || '1d'));
  if (url.includes('/trade-setups')) {
    if (params.has('slow')) await new Promise(r => setTimeout(r, 6000));
    const b = JSON.parse(JSON.stringify(F.tradeSetups)) as typeof F.tradeSetups;
    if (noIv) b.trade_setups.context.expected_move = null as never;
    if (params.has('mixed')) { b.trade_setups.context.bias = { ...b.trade_setups.context.bias, direction: 'neutral', strength: 'weak', score: 0.1, confirmations: [
      { signal: 'market_structure', reads: 'bullish', detail: 'market structure is bullish' }, { signal: 'daily_trend', reads: 'bearish', detail: 'daily trend is down' }, { signal: 'rsi', reads: 'bullish', detail: 'RSI 58' }, { signal: 'macd', reads: 'bearish', detail: 'MACD below signal' }] }; b.trade_setups.setups = []; }
    // ?earnings — a recent print the market has since reversed (TTMI-shaped, re-priced onto this fixture's spot)
    if (params.has('earnings')) {
      const ctx = b.trade_setups.context as Record<string, unknown>;
      ctx.flip_levels = { daily: 262.4, h4: 247.1, h1: 241.8 };
      ctx.earnings_reaction = { date: '2026-10-22', sessions_since: 2, direction: 'up', move_pct: 12.4, sigma_multiple: 4.1, rvol: 3.6,
        print_high: 251.2, print_low: 241.3, prior_close: 214.9, since_pct: -1.9, retraced_pct: 13.5, state: 'reclaimed',
        avwap: 244.6, avwap_side: 'below', read: 'Earnings 2026-10-22: +12.4% on 3.6× normal volume; -1.9% since. Back below the print-day low $241.30 — buyers from the print are under water · below the earnings AVWAP $244.60 · 14% of the move retraced; the gap fills at $214.90.',
        evidence: 'Context, not a signal: across 3,409 S&P 500 earnings moves of 7%+ (2017–2026), reclaiming or extending past the print-day range in the next 1–3 sessions did not predict the following 5–40 days vs SPY, and the gap filled about as often as the distances alone imply.' };
    }
    return json(b);
  }
  if (url.includes('/momentum-setup')) return json(momentumFragile);
  if (url.includes('/day-trade-setups')) return json(F.dayTradeSetups);
  if (url.includes('/microstructure')) return json(F.microstructure);
  if (url.includes('/market-structure')) return json(F.marketStructure);
  if (url.includes('/regime-edge')) return json({ detail: 'mock: not available in the preview' }, 404);
  if (url.includes('/regime')) return json(F.regime);
  if (url.includes('/dealer-positioning')) return json(F.dealer);
  return json({ detail: 'mock: not available in the preview' }, 404);
}) as typeof window.fetch;

function App() {
  return (
    <div className="container-app py-5">
      <div className="mb-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-1.5 text-xs text-warning">Preview with MOCK data — layout and behaviour check only. Query flags: <code>?slow</code> <code>?noiv</code> <code>?mixed</code> <code>?earnings</code> <code>?fail=trade-setups</code> <code>?fail=trader-strategies</code></div>
      <BetaTechnical ticker="NVDA" technical={F.technical as never} onLeave={() => alert('Back to classic')} />
    </div>
  );
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><MemoryRouter><App /></MemoryRouter></React.StrictMode>);
