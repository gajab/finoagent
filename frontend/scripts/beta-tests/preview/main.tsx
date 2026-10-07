// Visual preview of the Beta Technical view with a MOCKED network (synthetic data, no real tickers, nothing leaves the browser).
//   npm run dev  →  http://localhost:3000/scripts/beta-tests/preview/index.html
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '../../../src/index.css';
import BetaTechnical from '../../../src/beta/ta/BetaTechnical';
import * as F from './fixtures';

const params = new URLSearchParams(location.search);
const noIv = params.has('noiv'), fail = params.get('fail');
const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
const real = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === 'string' ? input : (input as Request).url ?? input);
  if (!url.includes('/api/')) return real(input, init);
  await new Promise(r => setTimeout(r, 250 + Math.random() * 400));
  if (fail && url.includes(fail)) return json({ detail: 'mock failure' }, 500);
  if (url.includes('/candles')) return json(F.candles(new URL(url, location.origin).searchParams.get('interval') || '1d'));
  if (url.includes('/trade-setups')) {
    if (params.has('slow')) await new Promise(r => setTimeout(r, 6000));
    const b = JSON.parse(JSON.stringify(F.tradeSetups)) as typeof F.tradeSetups;
    if (noIv) b.trade_setups.context.expected_move = null as never;
    if (params.has('mixed')) { b.trade_setups.context.bias = { ...b.trade_setups.context.bias, direction: 'neutral', strength: 'weak', score: 0.1, confirmations: [
      { signal: 'market_structure', reads: 'bullish', detail: 'market structure is bullish' }, { signal: 'daily_trend', reads: 'bearish', detail: 'daily trend is down' }, { signal: 'rsi', reads: 'bullish', detail: 'RSI 58' }, { signal: 'macd', reads: 'bearish', detail: 'MACD below signal' }] }; b.trade_setups.setups = []; }
    return json(b);
  }
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
      <div className="mb-3 rounded-lg border border-warning/30 bg-warning/10 px-3 py-1.5 text-xs text-warning">Preview with MOCK data — layout and behaviour check only. Query flags: <code>?slow</code> <code>?noiv</code> <code>?mixed</code> <code>?fail=trade-setups</code></div>
      <BetaTechnical ticker="NVDA" technical={F.technical as never} onLeave={() => alert('Back to classic')} />
    </div>
  );
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><MemoryRouter><App /></MemoryRouter></React.StrictMode>);
