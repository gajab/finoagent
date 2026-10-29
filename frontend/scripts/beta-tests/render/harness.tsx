// Render tests for the Beta Manage panels (Underlying Analysis + Defend & repair), run in jsdom against realistic payloads
// with a mocked network. Run via render.sh. Payload shapes mirror what the live panels returned for a covered call.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { MemoryRouter } from 'react-router-dom';
import assert from 'assert';
import BetaUnderlyingAnalysis from '../../../src/beta/BetaUnderlyingAnalysis';
import BetaDefend from '../../../src/beta/BetaDefend';
import DefendPanel from '../../../src/components/trades/DefendPanel';
import defendFixture from '../preview/defendFixture.json';
import BetaTradeInspector from '../../../src/beta/BetaTradeInspector';
import BetaBookRisk from '../../../src/beta/BetaBookRisk';
import MyTradesBeta from '../../../src/beta/MyTradesBeta';

const g: any = globalThis;
const fx = g.__fx;
let passed = 0;
const ok = (name: string) => { passed++; console.log('  ok -', name); };

function installFetch(routes: [string, (body: any, url: string) => any][], log: string[]) {
  g.fetch = async (url: string, opts: any) => {
    log.push(`${opts?.method || 'GET'} ${url}`);
    const hit = routes.find(([k]) => url.includes(k));
    if (!hit) return { ok: false, status: 404, text: async () => 'not found' };
    const out = await hit[1](opts?.body ? JSON.parse(opts.body) : null, url);
    if (out && out.__status) return { ok: false, status: out.__status, text: async () => JSON.stringify({ detail: out.detail }) };
    return { ok: true, status: 200, text: async () => JSON.stringify(out) };
  };
}
async function mount(el: React.ReactElement) {
  const container = document.createElement('div'); document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => { root.render(<MemoryRouter>{el}</MemoryRouter>); });
  return { container, text: () => container.textContent || '', unmount: async () => { await act(async () => root.unmount()); container.remove(); } };
}
const settle = async (ms = 80) => { await act(async () => { await new Promise(r => setTimeout(r, ms)); }); };
async function click(container: HTMLElement, re: RegExp, sel = 'button, summary') {
  const el = [...container.querySelectorAll(sel)].find(b => re.test(b.textContent || '')) as HTMLElement | undefined;
  assert(el, `no element matching ${re}`);
  await act(async () => { el!.dispatchEvent(new (g.window as any).MouseEvent('click', { bubbles: true })); });
  await settle(30);
}
const openDetails = async (c: HTMLElement) => { await click(c, /^Details/); await settle(30); };
const has = (t: string, ...xs: string[]) => xs.forEach(x => assert(t.includes(x), `missing "${x}"`));
const lacks = (t: string, ...xs: string[]) => xs.forEach(x => assert(!t.includes(x), `unexpected "${x}"`));
const before = (t: string, a: string, b: string) => assert(t.indexOf(a) >= 0 && t.indexOf(b) >= 0 && t.indexOf(a) < t.indexOf(b), `"${a}" should come before "${b}"`);

const trade: any = { id: 7, ticker: 'DRAM', strategy_type: 'covered_call', legs_data: [{ action: 'sell', type: 'call', strike: 85, qty: 1 }], parameters: {} };
const pnl: any = { underlying_price: 61.67, unrealized_pnl: 232 };

async function main() {
  console.log('BetaUnderlyingAnalysis');
  const tmBodies: any[] = [];
  const tmRoute: [string, (body: any) => any] = ['/trade-manager', async (body: any) => { tmBodies.push(body); await new Promise(r => setTimeout(r, 120)); return fx.baseTM(); }];
  {
    const log: string[] = [];
    installFetch([['/trade-manager/ai', () => ({ verdict: 'EXIT', one_line: 'AI says close it', why: ['reason one'], conviction: 70, _meta: { model: 'm', packet_chars: 5000 } })], tmRoute], log);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    let t = m.text();
    assert(t.includes('Reading structure'), 'loading state should show first'); ok('shows a loading state with context while the read runs');
    await settle(300);
    t = m.text();
    has(t, 'Strong exit', 'What to do from here', 'Exit now', 'How the score is built', 'Exit plan', 'Price ladder', 'What to watch next', 'Evidence', 'AI assist');
    ok('renders every section');
    lacks(t, 'Technical Analysis'); assert(!m.container.querySelector('[data-testid="lazy-technicals"]'), 'no embedded chart card');
    ok('the collapsed "Technical Analysis" card is gone from the top of the Manage read');
    before(t, 'Strong exit', 'How the score is built'); before(t, 'How the score is built', 'Price ladder'); before(t, 'Price ladder', 'What to watch next');
    ok('verdict first, then the score build, exit plan, watch next');
    has(t, 'Since you entered', '54 days', '+$232', '54.80 → 61.67', 'with you', 'Rolled', 'Your thesis'); ok('"since you entered" strip carries held days, P&L, stock move, rolls and thesis');
    has(t, 'Captured 100% of max profit', 'Technical (51) and Quant (4) disagree'); ok('overrides and conflicts are shown');
    // ladder: higher prices first, spot in between, stop below
    before(t, '70.00', '63.82'); before(t, '63.82', 'Now $61.67'); before(t, 'Now $61.67', '55.35'); ok('price ladder sorts targets above "Now $61.67" and the stop below it');
    has(t, 'in 25d', '+$150', 'Rules & timing'); ok('time- and P&L-based rules are listed separately');
    has(t, 'Typical daily move', 'Reward : risk', '0.34 : 1', 'Short call 85', '18% to touch'); ok('plan tiles carry ATR, reward:risk and the strike touch odds');
    has(t, 'Strong exit'); assert(t.includes('Not scored'), 'unscored lens labelled'); ok('unscored lenses read "Not scored", not a fake number');
    assert(tmBodies.length === 1 && tmBodies[0].desk == null, `no desk run → the read is requested without one (${JSON.stringify(tmBodies[0]?.desk)})`); ok('with no desk run the read is requested without a desk (the light quant lens stays the fallback)');
    // the quant tile hands off to the Quant tab for an option position
    has(t, 'Open the Quant tab →'); ok('the Quant tile says the number lives in the Quant tab');
    // evidence opens from a lens tile
    await click(m.container, /^Technical · 39%/); t = m.text();
    has(t, 'Technical lens', 'Famous-trader consensus', 'What moves the technical score'); ok('tapping the Technical tile opens its evidence');
    const whyBtns = [...m.container.querySelectorAll('button')].filter(b => /Why this level/.test(b.textContent || '')) as HTMLElement[];
    assert(whyBtns.length === 3, `expected 3 expandable ladder rows, got ${whyBtns.length}`);
    await act(async () => { whyBtns[2].dispatchEvent(new (g.window as any).MouseEvent('click', { bubbles: true })); });
    has(m.text(), 'detail', 'Hide evidence'); ok('"Why this level" reveals the stop row\'s evidence');
    await act(async () => { whyBtns[0].dispatchEvent(new (g.window as any).MouseEvent('click', { bubbles: true })); });
    has(m.text(), 'Strike with the most call positioning'); ok('level source chips carry a plain-English glossary');
    // AI only on explicit click
    assert(!log.some(l => l.includes('/trade-manager/ai')), 'AI must not run on load'); ok('the LLM call does not fire on load');
    await click(m.container, /^AI assist/); await click(m.container, /Ask AI to assist/); await settle(100);
    has(m.text(), 'AI says close it'); assert(log.some(l => l.includes('POST') && l.includes('/trade-manager/ai'))); ok('AI assist runs only on the explicit click and renders its result');
    assert(log.filter(l => l.endsWith('/trade-manager')).length === 1, 'one heavy read, not two'); ok('the heavy read runs once (no StrictMode-style double fetch)');
    await m.unmount();
  }
  {
    // The quant lens must be the Quant tab's own number: the read WAITS for the shared desk run, then sends it.
    tmBodies.length = 0;
    const log: string[] = [];
    installFetch([['/trade-manager', async (body: any) => { tmBodies.push(body); const t = fx.baseTM(); t.decision.lenses.quant = { ...t.decision.lenses.quant, score: 53, source: 'full_desk', notes: ['The full desk'] }; return t; }]], log);
    let setDesk: any; let opened = 0;
    const Host = () => { const [d, set] = React.useState<any>({ status: 'loading', desk: null, error: null, run: () => {} }); setDesk = set; return <BetaUnderlyingAnalysis trade={trade} pnl={pnl} desk={d} onOpenQuant={() => { opened++; }} />; };
    const m = await mount(<Host />);
    await settle(150);
    assert(tmBodies.length === 0, 'the read must not run before the desk finishes'); has(m.text(), 'Running the quant desk on this exact trade'); ok('while the desk score runs, the Manage read waits and says so (it never shows a second, different quant number)');
    await act(async () => { setDesk({ status: 'ready', desk: fx.deskScore(53), error: null, run: () => {} }); }); await settle(150);
    assert(tmBodies.length === 1, `one read after the desk lands, got ${tmBodies.length}`);
    assert(tmBodies[0].desk && tmBodies[0].desk.lifecycle_score === 53 && tmBodies[0].desk.signal === 'HOLD' && tmBodies[0].desk.management_analysis.score === 53, JSON.stringify(tmBodies[0].desk));
    assert(tmBodies[0].desk.opp === undefined && tmBodies[0].desk.qp === undefined && tmBodies[0].desk.desk_score === undefined, 'only what the backend reads is sent');
    ok('the read is requested WITH the Quant tab\'s desk result (signal, score, overrides, management analysis) and nothing else');
    let t = m.text(); has(t, 'The Quant tab’s desk score — the same number', 'Open the Quant tab →'); ok('the Quant tile states the number is the Quant tab\'s');
    await click(m.container, /^Quant · /); assert(opened === 1, 'tile should call onOpenQuant'); ok('tapping the Quant tile jumps to the Quant tab');
    assert(![...m.container.querySelectorAll('[role="tab"]')].some(b => /Quant/.test(b.textContent || '')), 'no Quant evidence pill');
    await click(m.container, /^Evidence/);
    assert(JSON.stringify([...m.container.querySelectorAll('[role="tab"]')].map(b => (b.textContent || '').trim())) === JSON.stringify(['Technical', 'Fundamental', 'News & events']), 'pills'); ok('Evidence for an option position has Technical · Fundamental · News & events — no Quant pill');
    has(m.text(), 'quant evidence lives in the Quant tab'); ok('Evidence points to the Quant tab instead of repeating it');
    // re-running the desk (Quant tab) re-reads with the new number
    await act(async () => { setDesk({ status: 'ready', desk: fx.deskScore(41, 'CLOSE'), error: null, run: () => {} }); }); await settle(150);
    assert(tmBodies.length === 2 && tmBodies[1].desk.lifecycle_score === 41 && tmBodies[1].desk.signal === 'CLOSE', 'second read adopts the re-run');
    ok('re-running the desk makes the Manage read adopt the new quant number');
    await m.unmount();
  }
  {
    // A desk that cannot run (unsupported structure, engine down) must not block the read — it falls back, and says so.
    tmBodies.length = 0;
    installFetch([['/trade-manager', async (body: any) => { tmBodies.push(body); return fx.baseTM(); }]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} desk={{ status: 'error', desk: null, error: 'Desk scan failed: boom', run: () => {} }} onOpenQuant={() => {}} />);
    await settle(150);
    assert(tmBodies.length === 1 && tmBodies[0].desk == null, 'falls back to the light read'); has(m.text(), 'Light read — the full desk score could not run (Desk scan failed: boom)'); ok('a failed desk run falls back to the light read and tells the user why');
    await m.unmount();
  }
  {
    installFetch([['/trade-manager', () => fx.stockTM()]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={{ ...trade, strategy_type: 'stock_long', legs_data: [] }} pnl={pnl} desk={{ status: 'na', desk: null, error: null, run: () => {} }} />);
    await settle(150); const t = m.text();
    has(t, 'Hold', 'Stock', 'Exit plan'); lacks(t, 'Price ladder', 'How often did closing beat holding', 'Open the Quant tab'); ok('stock position with no plan items, no vol and no hold-odds renders without crashing (no desk, no Quant-tab link)');
    await click(m.container, /^Evidence/);
    assert(JSON.stringify([...m.container.querySelectorAll('[role="tab"]')].map(b => (b.textContent || '').trim())) === JSON.stringify(['Position risk', 'Technical', 'Fundamental', 'News & events']), 'pills'); ok('for shares the Position-risk evidence stays (there is no Quant tab to hold it)');
    await m.unmount();
  }
  {
    installFetch([['/trade-manager', () => ({ __status: 500, detail: 'engine down' })]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    await settle(150); has(m.text(), 'engine down', 'Retry'); lacks(m.text(), 'Technical Analysis'); ok('an error shows a Retry');
    await m.unmount();
  }

  console.log('BetaEvidence — Technical · Fundamental · News & events');
  {
    installFetch([['/trade-manager', () => fx.richTM()]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    await settle(200);
    await click(m.container, /^Evidence/);
    let t = m.text();
    // Technical is the default evidence tab
    has(t, 'Technical lens', 'Regime: trending', 'Bias: bullish', 'Daily ↑', 'Stage 2 · advancing', 'RSI 72 · overbought'); ok('Technical opens on an at-a-glance strip: regime, bias, multi-timeframe trend, weekly stage, RSI');
    has(t, 'Famous-trader consensus', '11', 'bullish', '15'); ok('the trader consensus is a split bar with counts, not a sentence');
    has(t, 'What moves the technical score', 'Famous-trader rulesets', '+0.62', 'Dealer gamma flip', '−0.30'.replace('−', '-')); ok('the signals that build the technical score are listed with a signed strength each');
    has(t, 'Trend & momentum', 'Strong uptrend', 'Bullish but fading', 'Overbought', 'above 50 SMA +25.2%'); ok('trend & momentum reads are plain English (ADX → strong uptrend, MACD → bullish but fading, RSI → overbought)');
    has(t, 'Price map', 'Confluence zones', 'Now $61.67', '51.84', '70.00'); before(t, '70.00', 'Now $61.67'); before(t, 'Now $61.67', '51.84'); ok('the price map puts resistance above "Now" and support below it');
    has(t, 'Volatility & expected move', 'Volume & money flow', 'Chart patterns', 'Does the setup work in this regime?', 'The 15 trader rule-sets', 'Backtest reality-check'); ok('volatility, volume, patterns, regime edge, traders and the backtest each have a section');
    await click(m.container, /^Does the setup work in this regime\?/); t = m.text();
    has(t, '20-Day Breakout', 'firing now', 'weak edge here', 'n=14', 'Small sample', 'Mean-Reversion Dip', 'confirmed edge here'); ok('regime edge: each signal shows firing / edge / n, and small samples carry a warning');
    await click(m.container, /^The 15 trader rule-sets/); await click(m.container, /Mark Minervini/, 'summary'); t = m.text();
    has(t, 'Trade only Stage-2 leaders', 'Price > 150d & 200d SMA', 'Their exit', '55.35'); ok('a trader row expands to its rules and exit');
    assert(t.indexOf('Mark Minervini') < t.indexOf('Jesse Livermore'), 'bullish first'); ok('traders are ordered most-bullish first');

    await click(m.container, /^Fundamental$/, '[role="tab"]'); t = m.text();
    has(t, 'Fundamental lens', 'Advanced Micro Devices, Inc.', 'Technology · Semiconductors', 'Market cap $1.06T', 'Beta 2.45'); ok('Fundamental opens on the company header');
    has(t, 'Health scorecard', '7 healthy · 1 watch · 2 pressure of 10', 'exit pressure'); ok('the scorecard is tallied (7 healthy · 1 watch · 2 pressure) and explains that lower is healthier');
    const sc = t.slice(t.indexOf('Health scorecard'));
    before(sc, 'Macro sensitivity', 'Valuation'); before(sc, 'Valuation', 'Headline & regulatory risk'); before(sc, 'Headline & regulatory risk', 'Business health'); ok('pillars are ranked worst first');
    has(t, 'Forward P/E 41.3×', 'PEG 0.64×', 'To analyst target −2.0%'); ok('each pillar carries its headline facts with units');
    has(t, 'Valuation & profitability', 'Gross margin', '55.7%', 'Net margin', '15.6%', 'Revenue growth', '+50.1%', 'Free cash flow', '$8.84B', 'Net cash', '$8.84B'); ok('margins/growth are shown as percents (fractions ×100), money in $B, net cash derived');
    has(t, 'Dividend yield', 'None', 'Held by institutions', '75%'); ok('a stock that pays no dividend says "None", not "0.00%"');
    has(t, 'Debt / equity', '6.4%'); ok('debt/equity is shown as a percent (yfinance reports it as one)');
    has(t, 'The Street', 'Strong buy', 'mean rating 1.49', '−2.0% from the current price', 'already run past the average analyst target'); ok('The Street: consensus, mean rating, and an honest "above the average target" read');
    has(t, 'Citigroup', 'PT $800', 'Acme Capital', 'Downgrade', 'Buy → Hold'); ok('analyst actions list firm, change, price target and direction');
    has(t, 'Insider buys / sells', '0 / 14', '361K shares sold'); ok('insider counts are shown without editorialising');
    await click(m.container, /^Estimates & earnings surprises/); t = m.text();
    has(t, 'This quarter', '1.93', '+61%', '+6.4%', 'Last 2 quarters', '+2.5%', '−10.0%'); ok('estimates: EPS, growth, drift vs 90 days ago, and the surprise history');

    await click(m.container, /^News & events$/, '[role="tab"]'); t = m.text();
    has(t, 'Event risk', 'Clear', 'Calendar & filings', 'Next earnings', 'in 5 days', 'INSIDE your 46-day trade'); ok('News & events opens on event risk and the earnings date, placed against the trade\'s expiry');
    has(t, '8-K', 'Read on EDGAR'); lacks(t, 'xbrl junk'); ok('filings show the form and a link, never the raw XBRL excerpt');
    lacks(t, 'Last ex-dividend1995'); has(t, 'no dividend'); ok('a bogus 1995 ex-dividend date is not shown for a stock that pays no dividend');
    has(t, '17 headlines scanned · 1 risk · 1 positive', 'Regulator opens probe into chip exports'); ok('headlines carry a risk / positive pulse');
    const links = [...m.container.querySelectorAll('a')].filter(a => /news\.example\.com/.test(a.getAttribute('href') || ''));
    assert(links.length >= 2 && links.every(a => a.getAttribute('target') === '_blank' && /noreferrer/.test(a.getAttribute('rel') || '')), 'external links open safely'); ok('news links open in a new tab with rel=noreferrer');
    assert(![...m.container.querySelectorAll('a')].some(a => /^javascript:/i.test(a.getAttribute('href') || '')), 'no javascript: link'); has(t, 'A headline with a hostile link'); ok('a javascript: link in a feed is rendered as plain text, never as a link');
    const feedTitles = [...m.container.querySelectorAll('a')].map(a => a.textContent || '').filter(x => /Regulator|Memory ETF/.test(x));
    assert(feedTitles.length === 2 && feedTitles[0].startsWith('Regulator') && feedTitles[1].startsWith('Memory'), `feed order ${JSON.stringify(feedTitles)}`); ok('headlines are newest first');
    await click(m.container, /^Industry/, 'button'); has(m.text(), 'Semiconductor capex outlook'); await click(m.container, /^Macro & geopolitics/, 'button'); has(m.text(), 'AI investment keeps growth in gear'); await click(m.container, /^Peers/, 'button'); has(m.text(), '[NBIS]', 'Peer story'); ok('industry, macro and peer feeds are one tap away');
    await click(m.container, /^Peers & market backdrop/); t = m.text();
    has(t, 'Versus peers', 'AMD'.replace('AMD', 'DRAM'), '(this stock)', 'Peer median', '+37.1%', 'Market tape', 'S&P 500', 'Rates & macro', 'Fed Funds %'); ok('peers table puts this stock first with the median, then the tape and rates');
    await m.unmount();
  }
  {
    // A fund / ETF has no company fundamentals: say so instead of an empty scorecard.
    installFetch([['/trade-manager', () => fx.baseTM()]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    await settle(200); await click(m.container, /^Evidence/); await click(m.container, /^Fundamental$/, '[role="tab"]');
    has(m.text(), 'is a fund / index', 'left out of the score'); lacks(m.text(), 'Health scorecard'); ok('a fund shows a clear "fundamentals do not apply" note, not an empty scorecard');
    await m.unmount();
  }
  {
    // Sparse payloads (limited-history names, a data source down) must never crash the evidence views.
    installFetch([['/trade-manager', () => { const t = fx.richTM(); t.technical = { suite: {}, zones: [], patterns: [] }; t.trader_lenses = []; t.events = {}; t.market = {}; t.fundamental = { name: 'X Corp', is_fund: false }; t.pillars = {}; t.backtest = undefined; t.decision.lenses.technical.signals = undefined; return t; }]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    await settle(200); await click(m.container, /^Evidence/);
    has(m.text(), 'Technical lens'); await click(m.container, /^Fundamental$/, '[role="tab"]'); has(m.text(), 'X Corp'); await click(m.container, /^News & events$/, '[role="tab"]');
    has(m.text(), 'Event risk', 'No headlines returned by the data source'); ok('empty technical / fundamental / event payloads render every tab without crashing');
    await m.unmount();
  }

  console.log('BetaEvidence — how the scores are built · Position risk');
  {
    // Technical: the arithmetic behind the number is on screen, with the lens's reachable range
    installFetch([['/trade-manager', () => fx.richTM()]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    await settle(200); await click(m.container, /^Evidence/);
    const tb = m.container.querySelector('[data-testid="tech-build"]') as HTMLElement; assert(tb, 'build card');
    const t = m.text(); const bt = tb.textContent || '';
    has(bt, 'How the 51 is built', '50 + 22 × 0.50 − 10.0 = 51.0', '28 floor', '50 neutral', '72 ceiling', '±22 points from neutral', 'volatility / strike-distance terms listed above');
    ok('the technical score shows its sum (50 + 22 × 0.50 − 10.0 = 51.0), the 28–72 range it can reach, and what the −10 is');
    has(t, 'direction reading from −1 (bearish) to +1 (bullish) — not points', 'Weighted average', '+0.43', '(80% of the vote)', '(20% of the vote)'); ok('signal rows are labelled as −1…+1 readings (not points), with each one\'s share of the vote and the weighted average');
    before(t, 'Dealer gamma flip', 'Weighted average'); before(t, 'Weighted average', 'How the 51 is built'); ok('inputs → weighted average → score, in that order');
    await m.unmount();
  }
  {
    // 64 is "Leaning supportive", not "Neutral"; no build (older backend) ⇒ no build card, nothing else breaks
    installFetch([['/trade-manager', () => { const t = fx.richTM(); t.decision.lenses.technical.score = 64.1; t.decision.lenses.technical.build = undefined; return t; }]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    await settle(200);
    const tile = [...m.container.querySelectorAll('button')].find(b => /^Technical · 39%/.test(b.textContent || '')) as HTMLElement;
    has(tile.textContent || '', 'Leaning supportive', '64'); lacks(tile.textContent || '', 'Neutral'); ok('a technical lens of 64 reads "Leaning supportive" on its tile — not "Neutral"');
    await click(m.container, /^Evidence/); has(m.text(), 'Technical lens', 'Leaning supportive', '64/100'); ok('…and in the evidence header');
    assert(!m.container.querySelector('[data-testid="tech-build"]'), 'no build card without a build'); has(m.text(), 'Weighted average'); ok('an older read with no build data shows no build card but still renders the signals');
    await m.unmount();
  }
  {
    // Position risk for shares
    installFetch([['/trade-manager', () => fx.stockTM()]], []);
    const stockTrade = { ...trade, strategy_type: 'stock_long', legs_data: [] };
    const m = await mount(<BetaUnderlyingAnalysis trade={stockTrade} pnl={pnl} desk={{ status: 'na', desk: null, error: null, run: () => {} }} />);
    await settle(200); await click(m.container, /^Evidence/);
    let t = m.text();
    has(t, 'Position risk', 'Caution', '44/100', '36% of the hold score'); ok('Position risk opens on its word, score and weight');
    const sc = m.container.querySelector('[data-testid="stop-card"]') as HTMLElement; assert(sc, 'stop card'); const st = sc.textContent || '';
    has(st, 'Your −10% discipline stop', '55%', 'chance of touching it within a month', '0.6σ away', 'Your cost', '$1,083.30', 'Stop −10%', '$975.01', 'Now', '$1,133.40', '(−14.0%)', 'A normal month (1σ) is $848 – $1,419', 'the stop sits inside that range');
    ok('the stop card shows touch odds, σ-distance, cost / stop / now with % from the price, and says why the odds are high');
    assert(sc.querySelectorAll('[role="img"] > span').length === 3, `three marks on the axis, got ${sc.querySelectorAll('[role="img"] > span').length}`); ok('cost, stop and now are three marks on one axis (the 52-week high is within 1.5% of the price, so it is left off)');
    const rc = (m.container.querySelector('[data-testid="risk-receipt"]') as HTMLElement).textContent || '';
    has(rc, 'Starting point', '62', 'Stop distance', '−18', '55% chance of touching the 10% stop', 'Position-risk score', '44'); ok('the score is a receipt: 62 − 18 = 44, each term with its reason');
    has(t, 'How the 44 is built', '62 base −18 = 44'); ok('the receipt\'s one-line summary reads 62 base −18 = 44');
    has(t, 'The numbers behind it', 'Volatility (annualised)', '87%', 'P(touch −10% stop in 21d)', '55%', 'Beta vs market', '1.54', '12-1 month momentum', '+438%', 'Unrealized P&L', '$250', '≈ ±$285.30 per share'); ok('the supporting numbers are tiles with units and the plain note under each');
    before(t, 'Your −10% discipline stop', 'How the 44 is built'); before(t, 'How the 44 is built', 'The numbers behind it'); ok('order: the stop, then the sum, then the numbers');
    lacks(t, 'small weight'); has(t, 'scored alongside Technical and Fundamental'); ok('the old "carries a small weight" claim is gone (it is the largest lens for shares)');
    const own = (sc.parentElement as HTMLElement).textContent || ''; assert((own.match(/55% chance of touching the 10% stop/g) || []).length === 1, `the touch sentence appears once inside the view, got ${(own.match(/55% chance of touching the 10% stop/g) || []).length}`); ok('inside the Position-risk view the touch sentence is stated once (receipt), not repeated in the header');
    await m.unmount();
  }
  {
    // Through the stop / clean position / older read
    installFetch([['/trade-manager', () => { const t = fx.stockTM(); const q = t.decision.lenses.quant; q.score = 32; q.build = { ...q.build, terms: [{ label: 'Discipline stop', pts: -30, note: 'price is already through the 10% discipline stop (975.01)' }], stop: { ...q.build.stop, crossed: true, z: null, p_touch: 1 } }; return t; }]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={{ ...trade, strategy_type: 'stock_long', legs_data: [] }} pnl={pnl} desk={{ status: 'na', desk: null, error: null, run: () => {} }} />);
    await settle(200); await click(m.container, /^Evidence/); const t = m.text();
    has(t, 'price is already through it', 'Discipline stop', '−30', 'Stop (breached)'); lacks(t, 'chance of touching it within a month'); ok('a breached stop says so, scores the −30, and shows no touch odds');
    await m.unmount();
  }
  {
    installFetch([['/trade-manager', () => { const t = fx.stockTM(); const q = t.decision.lenses.quant; q.score = 62; q.notes = ['no elevated position-risk signal — volatility, drawdown and stop distance are all normal']; q.build = { kind: 'position_risk', base: 62, terms: [], stop: null, clamped: false }; return t; }]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={{ ...trade, strategy_type: 'stock_long', legs_data: [] }} pnl={pnl} desk={{ status: 'na', desk: null, error: null, run: () => {} }} />);
    await settle(200); await click(m.container, /^Evidence/); const t = m.text();
    has(t, 'Leaning supportive', 'Starting point', 'No elevated risk signal'); assert(!m.container.querySelector('[data-testid="stop-card"]'), 'no stop card without stop data'); ok('a clean position shows its 62 start and "nothing added or taken away" — and no stop card when there is no stop data');
    await m.unmount();
  }
  {
    // An older backend (no `build`): Position risk still renders from the notes and the metric tiles
    installFetch([['/trade-manager', () => { const t = fx.stockTM(); t.decision.lenses.quant.build = undefined; return t; }]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={{ ...trade, strategy_type: 'stock_long', legs_data: [] }} pnl={pnl} desk={{ status: 'na', desk: null, error: null, run: () => {} }} />);
    await settle(200); await click(m.container, /^Evidence/); const t = m.text();
    has(t, 'Position risk', '55% chance of touching the 10% stop', 'The numbers behind it', 'Volatility (annualised)'); lacks(t, 'How the 44 is built', 'Your −10% discipline stop'); ok('with no build data (older backend) Position risk falls back to the notes and metric tiles');
    await m.unmount();
  }

  console.log('BetaDefend');
  {
    const log: string[] = []; let release: any;
    installFetch([
      ['/defend/committee', () => ({ quant: { role: 'Quant', stance: 'close', rationale: 'because q' }, risk: { role: 'Risk', stance: 'close', rationale: 'because r' }, pm: { role: 'PM', stance: 'close', rationale: 'because p' }, verdict: { primary_action: 'Close it', why: 'locks the gain', confidence: 'high', alternates: [{ action: 'Roll', why: 'credit' }], do_not: 'do not widen' } })],
      ['/defend/refine', () => new Promise(r => { release = () => r(fx.defendMenu('done')); })],
      ['/repair-menu', async () => { await new Promise(r => setTimeout(r, 120)); return fx.defendMenu('pending'); }],
    ], log);
    const m = await mount(<BetaDefend tradeId={7} quoteSource="yfinance" ticker="DRAM" />);
    assert(m.text().includes('Repricing the position'), 'phase-1 loading'); ok('shows a loading state while the menu is priced');
    await settle(300); let t = m.text();
    has(t, 'Building the desk recommendation', 'Marginal', 'Risk read', 'Mark if closed', '+$5,964'); lacks(t, 'Next best moves', 'Desk recommendation'); ok('phase 1 paints the header and risk read; the recommendation and menu are held back (never a provisional pick)');
    assert(log.filter(l => l.includes('/defend/refine')).length === 1, 'refine called once'); ok('the live roll search is requested exactly once');
    release(); await settle(150); t = m.text();
    has(t, 'Desk recommendation', '→ Close the trade', 'Locks in a gain with certainty', 'To keep the trade alive:', 'Avoid:', 'Next best moves', 'Roll to the $86 call', 'Do nothing, or close', 'Hold as-is'); ok('after phase 2: recommendation, ranked moves and the benchmarks appear');
    // top section: P(finish ITM) lives in the header with the early-assignment warning; there is no Assignment section any more
    const hdr = m.container.querySelector('.rounded-2xl') as HTMLElement; const ht = hdr.textContent || '';
    has(ht, 'Spot', 'Cushion', 'Days left', 'P(finish ITM)', '5%', '$0.36 time value left', 'Mark if closed', 'Early-assignment risk — extrinsic below dividend');
    before(ht, 'Days left', 'P(finish ITM)'); before(ht, 'P(finish ITM)', 'Mark if closed'); ok('P(finish ITM) sits in the top section between Days left and Mark if closed, with the early-assignment warning');
    lacks(t, 'Assignment risk', 'Pin ratio', 'Capital at stake', 'Effective basis'); assert(![...m.container.querySelectorAll('button')].some(b => /^Assignment risk/.test(b.textContent || ''))); ok('the Assignment risk section is gone');
    has(t, 'Synthetic reframe'); ok('the Synthetic reframe (which uses the assignment numbers) is kept');
    // payoff graphs: every recommended option shows one without expanding
    const pay = () => m.container.querySelectorAll('[data-testid="move-payoff"]').length;
    assert(pay() === 1 + 4 + 2, `recommendation + 4 fixes + hold + close = 7 payoffs, got ${pay()}`);
    has(t, '-30% move', 'now $61.67', '+30% move'); ok('the recommendation, the top moves and both benchmarks show a payoff curve up front, with the move axis labelled');
    assert(m.container.querySelectorAll('[data-testid="move-payoff"] svg').length === pay(), 'each block draws an svg'); ok('every payoff block actually draws its curve');
    before(t, 'Desk recommendation', 'Next best moves'); before(t, 'Next best moves', 'Risk read'); ok('order is recommendation → moves → risk read');
    has(t, 'Show 2 more'); lacks(t, 'Calendarised hedge'); await click(m.container, /Show 2 more/); has(m.text(), 'Calendarised hedge', 'Show fewer'); assert(pay() === 9, `the 2 revealed moves bring their payoffs, got ${pay()}`); ok('shows the top 4 moves and reveals the rest on request — each with its payoff');
    has(m.text(), 'Close & redeploy — 1 option'); lacks(m.text(), 'defined condor'); await click(m.container, /Close & redeploy/); has(m.text(), 'defined condor'); assert(pay() === 10, `redeploy option payoff, got ${pay()}`); ok('"close & redeploy" options are kept apart from fixes and fold open — with their payoff too');
    has(m.text(), 'Desk pick'); ok('the desk pick (here the Close benchmark) is flagged');
    await click(m.container, /Roll to the \$86 call/); t = m.text();
    has(t, 'Edge / \$ risk', 'P(OTM)', '98.4%', 'clears', '2/6', 'Evaluate in the Income Desk'); ok('expanding a move shows payoff, score breakdown, roll read and the Evaluate hand-off');
    await click(m.container, /Cover it/); has(m.text(), 'Earnings:', 'earnings print before expiry'); ok('an earnings-exposed move shows its deduction');
    for (const x of ['Cost of waiting', 'What broke & the setup', 'Synthetic reframe', 'War room']) has(m.text(), x);
    await click(m.container, /^What broke/); has(m.text(), 'time value', 'double bottom', 'Structure map', 'dealers long'); ok('context accordion lists time value, pattern and the structure map');
    await click(m.container, /^Cost of waiting/); has(m.text(), 'Recovery odds', '+20d'); ok('cost of waiting renders its table');
    assert(!log.some(l => l.includes('/defend/committee')), 'war room must not run on load'); ok('the LLM war room does not fire on load');
    await click(m.container, /^War room/); await click(m.container, /Convene the defense desk/); await settle(100);
    has(m.text(), 'Defense verdict', 'Close it', 'because q', 'do not widen'); ok('war room runs only on click and renders roles + verdict');
    await m.unmount();
  }
  {
    // An option with no usable curve must not crash or draw an empty box; a missing assignment block hides the tile.
    const menu = fx.defendMenu('done'); menu.assignment = undefined; menu.roll_search = 'done';
    menu.alternatives = menu.alternatives.map((a: any, i: number) => (i === 0 ? { ...a, scenarios: [] } : i === 1 ? { ...a, scenarios: [{ move_pct: 0, spot: 61.67, pnl: NaN }, { move_pct: 10, spot: 68, pnl: 5 }] } : a));
    installFetch([['/repair-menu', () => menu]], []);
    const m = await mount(<BetaDefend tradeId={10} quoteSource="yfinance" ticker="DRAM" />);
    await settle(200); const t = m.text(); const pay = m.container.querySelectorAll('[data-testid="move-payoff"]').length;
    has(t, 'Next best moves'); lacks(t, 'P(finish ITM)', 'Early-assignment risk', 'Synthetic reframe'); ok('with no assignment block the header has no P(finish ITM) tile and no assignment section');
    assert(pay >= 4 && pay < 7, `options without a usable curve draw none, got ${pay}`); ok('an option with no / non-finite payoff points shows no payoff block (and does not crash)');
    await m.unmount();
  }
  {
    // P(finish ITM) tone: a low probability is not a warning.
    const menu = fx.defendMenu('done'); menu.assignment = { ...menu.assignment, p_itm: 72, early_assignment_risk: false, extrinsic: null };
    installFetch([['/repair-menu', () => menu]], []);
    const m = await mount(<BetaDefend tradeId={10} quoteSource="yfinance" ticker="DRAM" />);
    await settle(200);
    const tile = [...m.container.querySelectorAll('div')].find(d => /^P\(finish ITM\)/.test(d.textContent || '') && d.className.includes('rounded-lg')) as HTMLElement;
    assert(tile && /72%/.test(tile.textContent || ''), 'tile shows 72%'); assert(tile.querySelector('.text-error'), 'a 72% chance of finishing ITM is flagged as bad');
    lacks(m.text(), 'Early-assignment risk'); ok('P(finish ITM) 72% reads as bad; no early-assignment callout when it is not flagged');
    await m.unmount();
  }
  {
    installFetch([['/repair-menu', () => fx.defendHealthyMinimal()]], []);
    const m = await mount(<BetaDefend tradeId={8} quoteSource="yfinance" ticker="AAPL" />);
    await settle(150); const t = m.text();
    has(t, 'Healthy', 'Trade health', 'Keep premium', '98.7%'); lacks(t, 'Next best moves', 'Desk recommendation', 'War room'.replace('War room', 'Building')); ok('a healthy trade with no alternatives, no context and no recommendation renders cleanly');
    await m.unmount();
  }
  {
    installFetch([['/repair-menu', () => ({ error: 'No option legs to repair.' })]], []);
    const m = await mount(<BetaDefend tradeId={9} quoteSource="yfinance" ticker="NOK" />);
    await settle(150); has(m.text(), 'No option legs to repair', 'Retry'); ok('a backend error shows a Retry');
    await m.unmount();
  }


  console.log('BetaDefend — defending a credit SPREAD (payloads from the REAL backend engine: preview/defendFixture.json)');
  {
    const F: any = defendFixture;
    const log: string[] = []; let release: any;
    installFetch([
      ['/defend/refine', () => new Promise(r => { release = () => r(F.after.final); })],
      ['/repair-menu', async () => { await new Promise(r => setTimeout(r, 60)); return F.after.phase1; }],
    ], log);
    const m = await mount(<BetaDefend tradeId={1} quoteSource="yfinance" ticker="AAOI" />);
    await settle(200); let t = m.text();
    has(t, 'Building the desk recommendation', 'put credit spread', 'Mark if closed', '−$248'); lacks(t, 'What to do', 'When to act', 'Next best moves'); ok('spread: phase 1 paints the header; the recommendation, steps and timing are held back until the roll search lands');
    assert(log.filter(l => l.includes('/defend/refine')).length === 1, 'the spread triggers the live roll search exactly once'); ok('a credit spread now triggers the live roll search (it used to be skipped)');
    release(); await settle(200); await openDetails(m.container); t = m.text();
    // the pick: the user's own idea — Nov 20 75/65 at a credit — with the income-policy label, not Hold/Close
    has(t, 'Desk recommendation', 'Income policy', '→ Roll the $80/70 put spread → $75/65 · Nov 20 (36d) — +$68 credit'); lacks(t, '→ Close the trade', '→ Hold as-is'); ok('the pick is the $80/70 → $75/65 Nov 20 roll at a credit, labelled as the income-policy pick (not Close, not Hold)');
    // WHAT TO CLOSE and WHAT TO OPEN, with prices and cash, and the net
    has(t, 'What to do', '1 · Close', 'Buy to close 1× $80 put · Oct 23 @ $3.28', 'Sell to close 1× $70 put · Oct 23 @ $0.46', '2 · Open', 'Sell to open 1× $75 put · Nov 20 @ $6.05', 'Buy to open 1× $65 put · Nov 20 @ $2.55', 'Net cash today', '+$68', 'a credit — no new money');
    before(t, '1 · Close', '2 · Open'); before(t, '2 · Open', 'Net cash today'); ok('it spells out what to CLOSE (buy back the $80, sell the $70) and what to OPEN ($75/$65 Nov 20), each with price and cash, then the net — no new money');
    // the ALL-IN picture including the trade you already hold
    has(t, 'All-in — the trade you hold + this move', 'Collected at open', '+$34', 'Locked in by closing', '−$248', 'Best case', '+$102', 'Worst case', '−$898', 'If the stock is unchanged at expiry', 'Break-even', '$73.98'); ok('all-in: what the old spread collected (+$34), what closing locks in (−$248), and the best/worst case and break-even of old trade + roll together');
    has(t, 'Versus holding — worst case -966 → -898', 'best case +34 → +102', 'break-even $79.66 → $73.98', 'P(profit) 54% → 58%'); ok('"versus holding" quotes the all-in change in worst case, best case, break-even and P(profit)');
    // WHEN: the print, the implied move, the window — and it is candid about the pick
    has(t, 'When to act — Window: now → Nov 11 — earnings Nov 12 (28d)', 'earnings move priced ≈ ±15.7%', 'IV 80% vs HV 85%', 'land AFTER this expiry', 'INSIDE the move', 'price of the even-cost roll', 'A roll that clears the move exists'); lacks(t, 'Only worth it with the short outside'); ok('"when to act": the window ends the day before earnings, the market-implied move is quoted, and the text is candid that the pick sells the print from inside the move');
    before(t, 'What to do', 'When to act'); before(t, 'Desk recommendation', 'Next best moves'); ok('order is recommendation → what to do → when to act → moves');
    // the runner-up must not contradict the pick
    has(t, 'Runner-up:', 'Do nothing: keeps the position exactly as it is'); lacks(t, 'No fix clears the bar'); ok('the Hold runner-up says what it keeps and forgoes — it no longer claims "no fix clears the bar" beside a pick that is a fix');
    // the ranked rows: pick FIRST (even though Hold scores higher), each with a compact Close → Open line
    const rows = [...m.container.querySelectorAll('button[aria-expanded][title^="Tap for the score"]')] as HTMLElement[];
    assert(/Desk pick/.test(rows[0].textContent || '') && /\$75\/65/.test(rows[0].textContent || ''), `first row should be the desk pick, got: ${rows[0].textContent?.slice(0, 80)}`);
    ok('the desk pick leads "Next best moves" even though Hold has the higher raw score');
    const lines = [...m.container.querySelectorAll('[data-testid="move-steps-line"]')].map(e => e.textContent || '');
    assert(lines.some(l => l === 'Close −1 P$80 +1 P$70 Oct 23  →  Open −1 P$75 +1 P$65 Nov 20'), `compact line missing: ${JSON.stringify(lines.slice(0, 3))}`);
    assert(lines.includes('No trades — keep every leg') && lines.some(l => l === 'Close −1 P$80 +1 P$70 Oct 23'), 'hold and close have their own lines'); ok('every row reads "Close … → Open …" in one line (Hold: no trades; Close: close both legs)');
    has(t, 'Worst case (all-in)', 'Best case (all-in)', 'Net cash today'); ok('row stats are labelled all-in (they include the loss already locked into the old spread)');
    // expanding a row shows the same steps + all-in for THAT option
    const nSteps = () => m.container.querySelectorAll('[data-testid="move-steps"]').length;
    const before1 = nSteps(); await click(m.container, /\$65\/55 · Jan 15/); assert(nSteps() === before1 + 1, 'expanding a row adds its steps block'); has(m.text(), 'Sell to open 1× $65 put · Jan 15'); ok('expanding another roll shows ITS close/open steps');
    await m.unmount();
  }
  {
    const F: any = defendFixture;
    installFetch([['/repair-menu', () => F.inside.final]], []);
    const m = await mount(<BetaDefend tradeId={1} quoteSource="yfinance" ticker="AAOI" />);
    await settle(200); await openDetails(m.container); const t = m.text();
    has(t, '→ Hold as-is (do nothing)', 'Nothing to trade — you keep', 'Keep short 1× $80 put', 'When to act — Defend before the print — earnings Nov 5 (21d)', 'INSIDE this trade', 'coin-flip', 'appetite');
    lacks(t, 'Income policy'); ok('print INSIDE the trade and no even-cost roll: Hold (a coin-flip vs Close, said so), "defend before the print", and no income-policy label');
    has(t, 'Roll search: No roll of this spread at even cost or better'); ok('the search says plainly that no even-cost roll exists');
    await m.unmount();
  }
  {
    const F: any = defendFixture;
    installFetch([['/repair-menu', () => F.naked.final]], []);
    const m = await mount(<BetaDefend tradeId={1} quoteSource="yfinance" ticker="AMD" />);
    await settle(200); await openDetails(m.container); const t = m.text();
    has(t, '→ Cap the tail — buy the $95 call', 'Buy to open 1× $95 call', 'new money', 'Keep short 1× $85 call'); lacks(t, 'Income policy'); ok('a naked call: the pick is a debit wing — shown as NEW money, keeping the short as it is — and the income policy does not apply');
    await m.unmount();
  }
  {
    const F: any = defendFixture;
    installFetch([['/repair-menu', () => F.healthy.final]], []);
    const m = await mount(<BetaDefend tradeId={1} quoteSource="yfinance" ticker="AAOI" />);
    await settle(200); await openDetails(m.container); const t = m.text();
    has(t, 'Healthy', '→ Hold as-is (do nothing)', 'Nothing to time — leave it'); lacks(t, 'Income policy', 'Defend before the print', 'coin-flip'); ok('a healthy spread: Hold, nothing to time, no policy label, no print lecture');
    await m.unmount();
  }
  {
    const F: any = defendFixture;
    installFetch([['/defend/refine', () => ({ __status: 500, detail: 'chain provider down' })], ['/repair-menu', () => F.after.phase1]], []);
    const m = await mount(<BetaDefend tradeId={1} quoteSource="yfinance" ticker="AAOI" />);
    await settle(300); const t = m.text();
    has(t, 'Live roll search unavailable', 'chain provider down', 'Retry', 'What to do'); ok('if the live roll search fails the fallback recommendation STILL shows what to close/open (steps come from phase 1)');
    await m.unmount();
  }

  console.log('BetaDefend — the in-the-money spread trap: what to do · today or wait · details (REAL engine payloads)');
  {
    const F: any = defendFixture;
    let release: any;
    installFetch([
      ['/defend/refine', () => new Promise(r => { release = () => r(F.trap.final); })],
      ['/repair-menu', async () => { await new Promise(r => setTimeout(r, 60)); return F.trap.phase1; }],
    ], []);
    const m = await mount(<BetaDefend tradeId={1} quoteSource="yfinance" ticker="AAOI" />);
    await settle(200); let t = m.text();
    has(t, 'Building the desk recommendation', 'Details'); lacks(t, 'What to do', 'Today or wait?'); ok('trap: phase 1 holds the plan back (never a provisional pick)');
    release(); await settle(250); t = m.text();
    const ap = F.trap.final.action_plan;
    has(t, 'What to do', ap.verdict, 'Within your rule', 'A little more risk for better odds', 'Or take the loss', 'Your idea'); ok('the plan leads with ONE verdict line, then the moves by tier: within your rule · a little more risk · close');
    const rowsEl = [...m.container.querySelectorAll('[data-testid="action-row"]')];
    assert(rowsEl.length === ap.rows.length, `rows ${rowsEl.length} vs ${ap.rows.length}`);
    const pickEl = rowsEl.find(e => /★ Pick/.test(e.textContent || '')) as HTMLElement;
    const pickRow = ap.rows.find((r: any) => r.is_pick);
    assert(pickEl && (pickEl.textContent || '').includes(pickRow.title), 'the pick card is the plan pick'); ok('exactly the plan\'s pick is starred');
    const idea = ap.rows.find((r: any) => r.tag.startsWith('Your idea'));
    const ideaEl = rowsEl.find(e => (e.textContent || '').includes(idea.title)) as HTMLElement;
    for (const o of idea.orders) has(ideaEl.textContent || '', o);
    has(ideaEl.textContent || '', 'Cash today', 'Worst', 'Best', 'Lose less', 'E[P&L]'); ok('the user\'s own same-strikes roll is priced, with its four orders and the same five numbers as every row');
    const ex = ap.explorer || [];
    assert(ex.length >= 8, `explorer families: ${ex.length}`);
    assert(m.container.querySelectorAll('[data-testid="explorer-row"]').length === ex.length);
    has(t, 'Every strategy tested', ...ex.map((x: any) => x.family)); ok('every strategy family the search priced is listed with its best version and why (calls, call spreads, jade, butterflies, backspread, calendar, diagonal, rolls…)');
    const firstEx = m.container.querySelector('[data-testid="explorer-row"]') as HTMLElement;
    await act(async () => { firstEx.dispatchEvent(new (g.window as any).MouseEvent('click', { bubbles: true })); }); await settle(30);
    has(m.text(), ex[0].best.orders[0]); ok('tapping a strategy shows its orders');
    t = m.text();
    has(t, 'Today or wait?', F.trap.final.timing_read.headline); assert(m.container.querySelectorAll('[data-testid="timing-read"] tbody tr').length === F.trap.final.timing_read.rows.length); ok('today-or-wait: the headline and one row per move (today · last day · bounce · drop · IV −10)');
    assert(m.container.querySelectorAll('[data-testid="timing-signals"] li').length === F.trap.final.timing_read.signals.length); ok('…and the signals behind it, tagged bounce / drop / volatility / earnings / tape');
    lacks(t, 'Position anatomy'); await openDetails(m.container); t = m.text();
    has(t, 'Position anatomy', 'Time now works AGAINST you', 'Desk recommendation', 'Assignment ladder'); ok('the leg-by-leg read, the scored recommendation and the ladder sit in Details');
    before(t, 'What to do', 'Today or wait?'); before(t, 'Today or wait?', 'Position anatomy'); ok('reading order: what to do → today or wait → details');
    await m.unmount();
  }

  console.log('Classic DefendPanel — same steps / timing wording');
  {
    const F: any = defendFixture;
    installFetch([['/defend/refine', () => F.after.final], ['/repair-menu', () => F.after.phase1]], []);
    const m = await mount(<DefendPanel tradeId={1} quoteSource="yfinance" ticker="AAOI" />);
    await settle(400); const t = m.text();
    has(t, 'Roll the $80/70 put spread → $75/65 · Nov 20', 'income policy', 'What to do', 'Buy to close 1× $80 put', 'Net cash today', 'All-in', 'When to act — Window: now → Nov 11');
    before(t, 'What to do', 'When to act'); ok('the classic panel shows the same what-to-close/open, all-in and when-to-act blocks');
    await m.unmount();
  }
  {
    const F: any = defendFixture;
    installFetch([['/defend/refine', () => F.trap.final], ['/repair-menu', () => F.trap.phase1]], []);
    const m = await mount(<DefendPanel tradeId={1} quoteSource="yfinance" ticker="AAOI" />);
    await settle(400); const t = m.text();
    has(t, 'What to do', 'Within your rule', 'Today or wait?', 'Position anatomy', 'Time now works AGAINST you', 'Assignment ladder');
    before(t, 'What to do', 'Position anatomy'); ok('the classic panel leads with the same plan; the anatomy and ladder are in its Details');
    await m.unmount();
  }

  console.log('BetaTradeInspector — Manage tab');
  const hold: any = { state: 'hold', headline: 'On plan — nothing to do.', reasons: [], tested: false, breached: false, dte: null, capturedPct: null, quote: { status: 'ok', missing: 0, total: 0 }, classicSignal: null };
  const noop = () => {};
  const insp = (tr: any, pl: any, st: any = hold) => (
    <BetaTradeInspector trade={tr} pnl={pl} state={st} quoteSource="yfinance" loading={false}
      onRefresh={noop} onClose={noop} onChanged={noop} onCloseTrade={noop} onUpdatePosition={noop} />);
  const tabBtns = (c: HTMLElement) => [...c.querySelectorAll('[role="tab"]')].map(b => (b.textContent || '').trim());
  {
    // A STOCK-ONLY position: Manage must show the hold / exit plan (Underlying Analysis) and never Defend & repair.
    const stock: any = { id: 11, ticker: 'NOK', strategy_type: 'stock_long', legs_data: [], parameters: { shares: 700, avg_cost: 13 }, entry_date: '2026-08-10T00:00:00Z' };
    const spnl: any = { underlying_price: 10.21, unrealized_pnl: -1949.5, pnl_pct: -21.4, entry_cost: 9100, current_value: 7150.5, current_quotes: [], breakevens: [], analysis: {}, expiration_date: null };
    const log: string[] = [];
    installFetch([['/trade-manager', () => fx.stockTM()]], log);
    const m = await mount(insp(stock, spnl));
    await click(m.container, /^Manage$/, 'button'); await settle(300);
    let t = m.text();
    lacks(t, 'Option management tools apply'); ok('a stock position no longer shows the "option legs only" notice on Manage');
    has(t, 'Underlying Analysis', 'Exit plan'); lacks(t, 'Technical Analysis'); ok('Manage shows the Underlying Analysis (hold / exit plan) for the shares — without the old Technical Analysis card');
    assert(!log.some(l => l.includes('/desk-score')), 'no desk score for shares'); ok('shares never request a desk score (there is no option payoff to score)');
    assert(!tabBtns(m.container).some(x => /Defend/.test(x)), 'no Defend tab for a stock position'); ok('there is no Defend & repair sub-tab on a stock position');
    assert(!log.some(l => l.includes('/repair-menu')), 'Defend must not be requested for stock'); ok('the Defend / repair-menu request is never made for a stock position');
    assert(log.filter(l => l.endsWith('/trade-manager')).length === 1, 'plan read runs once'); ok('the hold / exit read is requested once');
    has(t, 'Hold / exit plan & underlying analysis'); ok('the Overview shortcut offers the hold / exit plan for a stock position');
    lacks(t, 'Defend & repair options'); ok('the Overview shortcut to Defend & repair is hidden for a stock position');
    await click(m.container, /^Risk$/, 'button'); t = m.text();
    has(t, 'apply to option legs', 'Manage → Underlying Analysis'); lacks(t, 'Press Refresh to load live Greeks'); ok('the Risk tab explains Greeks are option-only instead of waiting for data that never comes');
    await m.unmount();
  }
  {
    // An OPTION position keeps both sub-tabs, in the user-specified order and names.
    const opt: any = { id: 12, ticker: 'DRAM', strategy_type: 'covered_call', legs_data: [{ action: 'sell', type: 'call', strike: 85, qty: 1, expiration: '2026-11-20' }], parameters: { shares: 100, avg_cost: 60 }, entry_date: '2026-08-10T00:00:00Z' };
    const opnl: any = { underlying_price: 61.67, unrealized_pnl: 232, pnl_pct: 3.9, entry_cost: 6000, current_value: 6232, current_quotes: [{ leg: 0, bid: 0.3, ask: 0.4, mid: 0.35 }], breakevens: [59.35], analysis: {}, expiration_date: '2026-11-20' };
    const log: string[] = [];
    installFetch([['/trade-manager', () => fx.baseTM()], ['/desk-score', () => fx.deskScore(53)], ['/defend/refine', () => fx.defendMenu('done')], ['/repair-menu', () => fx.defendMenu('pending')]], log);
    const m = await mount(insp(opt, opnl));
    await click(m.container, /^Manage$/, 'button'); await settle(300);
    assert(JSON.stringify(tabBtns(m.container)) === JSON.stringify(['Underlying Analysis', 'Defend & repair']), `sub-tabs were ${JSON.stringify(tabBtns(m.container))}`); ok('an option position keeps both sub-tabs: Underlying Analysis, then Defend & repair');
    assert(!log.some(l => l.includes('/repair-menu')), 'Defend must not run until opened'); ok('Defend & repair does not run until its sub-tab is opened');
    await click(m.container, /^Defend & repair$/, '[role="tab"]'); await settle(300);
    assert(log.some(l => l.includes('/repair-menu')), 'repair-menu requested after opening'); ok('opening Defend & repair requests the menu');
    has(m.text(), 'Defend & repair options'); ok('the Overview shortcut to Defend & repair is present for an option position');
    await m.unmount();
  }

  console.log('BetaTradeInspector — one desk score, shared by Quant and Manage');
  {
    const optA: any = { id: 12, ticker: 'DRAM', strategy_type: 'covered_call', legs_data: [{ action: 'sell', type: 'call', strike: 85, qty: 1, expiration: '2026-11-20' }], parameters: { shares: 100, avg_cost: 60 }, entry_date: '2026-08-10T00:00:00Z' };
    const optB: any = { ...optA, id: 13, ticker: 'ORCL' };
    const opnl: any = { underlying_price: 61.67, unrealized_pnl: 232, pnl_pct: 3.9, entry_cost: 6000, current_value: 6232, current_quotes: [{ leg: 0, bid: 0.3, ask: 0.4, mid: 0.35 }], breakevens: [59.35], analysis: {}, expiration_date: '2026-11-20' };
    const idOf = (u: string) => Number((/saved-strategies\/(\d+)\//.exec(u) || [])[1]);
    const mkRoutes = (scores: Record<number, number>, bodies: any[], extra: [string, any][] = []) => ([
      ...extra,
      ['/desk-score', async (_b: any, u: string) => { await new Promise(r => setTimeout(r, 60)); const sc = scores[idOf(u)] ?? 53; return fx.deskScore(sc, sc >= 50 ? 'HOLD' : 'CLOSE'); }],
      ['/trade-manager', async (b: any, u: string) => { bodies.push({ id: idOf(u), desk: b.desk }); const t = fx.baseTM(); if (b.desk) t.decision.lenses.quant = { ...t.decision.lenses.quant, score: b.desk.lifecycle_score, source: 'full_desk' }; return t; }],
    ] as [string, (b: any, u: string) => any][]);
    const cnt = (log: string[], re: RegExp) => log.filter(l => re.test(l)).length;

    {
      // Manage first → ONE desk run, then the read with that desk; the Quant tab then reuses it (no second heavy scan).
      const log: string[] = [], bodies: any[] = [];
      installFetch(mkRoutes({ 12: 53 }, bodies), log);
      const m = await mount(insp(optA, opnl));
      await click(m.container, /^Manage$/, 'button'); await settle(400);
      assert(cnt(log, /POST .*\/12\/desk-score/) === 1, `one desk run, got ${cnt(log, /desk-score/)}`);
      const iDesk = log.findIndex(l => /desk-score/.test(l)), iTm = log.findIndex(l => /trade-manager$/.test(l));
      assert(iDesk >= 0 && iTm > iDesk, 'the desk runs BEFORE the read'); assert(bodies.length === 1 && bodies[0].desk?.lifecycle_score === 53, JSON.stringify(bodies));
      ok('opening Manage runs the desk once, then reads with it — the quant lens is the Quant tab\'s number');
      await click(m.container, /^Quant$/, 'button'); await settle(150);
      assert(cnt(log, /desk-score/) === 1, 'the Quant tab must reuse the run'); has(m.text(), 'Manage This Position', 'Management score', 'Re-run with current prices'); ok('the Quant tab renders the SAME run (no second heavy desk scan)');
      // re-run → the Manage read follows
      await click(m.container, /Re-run with current prices/, 'button'); await settle(500);
      assert(cnt(log, /desk-score/) === 2, 're-run asks again'); assert(bodies.length === 2 && bodies[1].desk.lifecycle_score === 53, 'second read');
      ok('re-running from the Quant tab refreshes the Manage read too');
      await m.unmount();
    }
    {
      // Quant first, then Manage → still one desk run; the quant tile jumps back to the Quant tab.
      const log: string[] = [], bodies: any[] = [];
      installFetch(mkRoutes({ 12: 53 }, bodies), log);
      const m = await mount(insp(optA, opnl));
      await click(m.container, /^Quant$/, 'button'); await settle(300);
      has(m.text(), 'Manage This Position'); assert(cnt(log, /desk-score/) === 1, 'one run'); ok('the Quant tab alone starts the desk run');
      await click(m.container, /^Manage$/, 'button'); await settle(400);
      assert(cnt(log, /desk-score/) === 1 && bodies.length === 1 && bodies[0].desk?.lifecycle_score === 53, 'Manage adopts the existing run'); ok('Manage then reads with the run that already exists — still one desk scan');
      const quantTab = () => [...m.container.querySelectorAll('button')].find(b => /^Quant$/.test(b.textContent || '')) as HTMLElement;
      assert(!/border-primary/.test(quantTab().className), 'Manage is the active tab');
      await click(m.container, /^Quant · /); assert(/border-primary/.test(quantTab().className), 'the Quant tile switched to the Quant tab'); ok('the Quant lens tile in Manage jumps to the Quant tab');
      await m.unmount();
    }
    {
      // The desk fails: the Quant tab says why (with Retry) and Manage still reads (light) instead of hanging.
      const log: string[] = [], bodies: any[] = [];
      installFetch(mkRoutes({}, bodies, [['/desk-score', () => ({ __status: 502, detail: 'Desk scan failed: boom' })]]), log);
      const m = await mount(insp(optA, opnl));
      await click(m.container, /^Quant$/, 'button'); await settle(200);
      has(m.text(), 'Desk scan failed: boom', 'Retry'); ok('a failed desk run shows its reason and a Retry on the Quant tab');
      await click(m.container, /^Manage$/, 'button'); await settle(400);
      assert(bodies.length === 1 && bodies[0].desk == null, 'light read without a desk'); has(m.text(), 'Light read — the full desk score could not run'); ok('Manage still reads — on the light quant lens, labelled as such');
      await m.unmount();
    }
    {
      // No live market for a leg: the verdict is withheld, so the heavy desk scan must not start either.
      const log: string[] = [], bodies: any[] = [];
      installFetch(mkRoutes({}, bodies), log);
      const noq = { ...hold, state: 'noquote', headline: 'No live quote', quote: { status: 'gap', missing: 1, total: 1 } };
      const m = await mount(insp(optA, opnl, noq));
      await click(m.container, /^Quant$/, 'button'); await click(m.container, /^Manage$/, 'button'); await settle(300);
      assert(cnt(log, /desk-score|trade-manager/) === 0, `no heavy calls on an unpriced position, got ${JSON.stringify(log)}`); has(m.text(), 'one or more legs have no quote'); ok('an unpriced position starts no desk scan and no read (the notice offers "Show it anyway")');
      await m.unmount();
    }
    {
      // Switching rows must never hand one trade's desk score to another trade.
      const log: string[] = [], bodies: any[] = [];
      installFetch(mkRoutes({ 12: 53, 13: 41 }, bodies), log);
      let setTr: any;
      const Sw = () => { const [tr, set] = React.useState<any>(optA); setTr = set; return insp(tr, opnl); };
      const m = await mount(<Sw />);
      await click(m.container, /^Manage$/, 'button'); await settle(400);
      const before13 = log.length;
      await act(async () => { setTr(optB); }); await settle(300);
      assert(!log.slice(before13).some(l => /\/13\/(desk-score|trade-manager|repair-menu|defend)/.test(l)), `picking another row must not start heavy work for it: ${JSON.stringify(log.slice(before13))}`);
      ok('picking another row starts NO desk score / hold-exit / defend request for it until a tab is opened');
      await click(m.container, /^Manage$/, 'button'); await settle(500);
      assert(bodies.map(b => `${b.id}:${b.desk?.lifecycle_score}`).join(',') === '12:53,13:41', `reads: ${JSON.stringify(bodies)}`);
      assert(cnt(log, /\/12\/desk-score/) === 1 && cnt(log, /\/13\/desk-score/) === 1, 'each trade scored once'); ok('each position gets its OWN desk score — switching rows never reuses the previous trade\'s');
      await m.unmount();
    }
  }

  console.log('MyTradesBeta — Deployed column + trade-type filter');
  {
    const day = (n: number) => new Date(Date.now() + n * 86400e3).toISOString().slice(0, 10);
    const snap = (extra: any) => ({ last_pnl: { unrealized_pnl: 100, pnl_pct: 5, expiration_date: day(30), current_quotes: [{ leg: 0, bid: 1, ask: 1.2, mid: 1.1 }, { leg: 1, bid: 1, ask: 1.2, mid: 1.1 }], ...extra }, last_pnl_at: new Date().toISOString() });
    const leg = (action: string, type: string, strike: number, qty = 1) => ({ action, type, strike, qty, expiration: day(30) });
    const mk = (id: number, ticker: string, legs: any[], parameters: any) => ({ id, ticker, name: ticker, strategy_type: 'options', legs_data: legs, parameters, entry_date: day(-10), entry_net_debit: -100, trade_status: 'active' });
    const trades = [
      mk(1, 'COVD', [leg('sell', 'call', 110)], { shares: 100, avg_cost: 90, ...snap({ margin_required: 0 }) }),
      mk(2, 'NAKD', [leg('sell', 'call', 120, 2)], { ...snap({ margin_required: 4800 }) }),
      mk(3, 'CSPX', [leg('sell', 'put', 90)], { ...snap({ margin_required: 1900 }) }),
      mk(4, 'STRD', [leg('sell', 'call', 100), leg('sell', 'put', 100)], { ...snap({ margin_required: 6200 }) }),
      mk(5, 'NOMG', [leg('sell', 'call', 130)], {}),                                              // no snapshot yet → margin unknown
      mk(6, 'HDGE', [leg('buy', 'put', 80)], { purpose: 'hedge', ...snap({ entry_cost: 640 }) }),
    ];
    installFetch([['/api/saved-strategies/trades', () => trades], ['/book-tail-risk', () => ({ stored: false, positions: 0 })]], []);
    const m = await mount(<MyTradesBeta />);
    await settle(250);
    let t = m.text();
    has(t, 'Deployed', 'COVD', 'NAKD', 'CSPX', 'STRD'); ok('the table has a Deployed column');
    const cells = [...m.container.querySelectorAll('[data-testid="deployed-cell"]')].map(c => (c.textContent || '').trim());
    assert(cells.includes('$9kstock'), `covered call cell: ${JSON.stringify(cells)}`); ok('a covered call shows the stock it holds ($9k = 100 sh × $90)');
    assert(cells.includes('$4.8kReg-T') && cells.includes('$1.9kReg-T') && cells.includes('$6.2kReg-T'), `reg-t cells: ${JSON.stringify(cells)}`); ok('naked call, cash-secured put and straddle show their Reg-T margin ($4.8k / $1.9k / $6.2k)');
    assert(cells.some(c => c.startsWith('—') && c.includes('no margin yet')), `no-margin cell: ${JSON.stringify(cells)}`); ok('a short option with no margin loaded yet shows "—" — never the premium');
    assert(cells.includes('$640debit'), `debit cell: ${JSON.stringify(cells)}`); ok('a long option shows its debit');
    has(t, 'deployed $21.9k'); ok('the Income group header totals the deployed money ($9k + $4.8k + $1.9k + $6.2k)');

    // type filter
    const sel = () => m.container.querySelector('select[aria-label="Filter by trade type"]') as HTMLSelectElement;
    assert(sel(), 'type filter present'); const opts = [...sel().options].map(o => o.textContent);
    assert(opts[0] === 'All types (6)', JSON.stringify(opts));
    for (const x of ['Covered Call (1)', 'Naked Call (2)', 'Cash-Secured Put (1)', 'Short Straddle (1)', 'Long Put (1)']) assert(opts.includes(x), `missing option ${x}: ${JSON.stringify(opts)}`);
    ok('the Type filter lists each trade type present with its count (covered call, naked call, cash-secured put, straddle …)');
    const choose = async (v: string) => { await act(async () => { Object.getOwnPropertyDescriptor((g.window as any).HTMLSelectElement.prototype, 'value')!.set!.call(sel(), v); sel().dispatchEvent(new (g.window as any).Event('change', { bubbles: true })); }); await settle(30); };
    await choose('naked_call'); t = m.text();
    has(t, 'NAKD', 'NOMG'); lacks(t, 'COVD', 'CSPX', 'STRD', 'HDGE'); ok('choosing "Naked Call" keeps only the naked calls');
    has(t, 'deployed $4.8k'); ok('and the group total follows the filter');
    await choose('short_straddle'); t = m.text(); has(t, 'STRD'); lacks(t, 'NAKD', 'COVD'); ok('"Short Straddle" finds the straddle (same-strike call + put)');
    await choose('all'); has(m.text(), 'COVD', 'NAKD', 'CSPX', 'STRD', 'HDGE'); ok('"All types" restores the list');
    await m.unmount();
  }


  console.log('BetaBookRisk — Manage Book');
  {
    const log: string[] = []; const managed: number[] = [];
    installFetch([
      ['/book-hedge-advice', async () => { await new Promise(r => setTimeout(r, 60)); return { advice: 'Trim AMD and keep the SPX spread.' }; }],
      ['/book-tail-risk', (_b) => fx.bookTailRisk()],
    ], log);
    const m = await mount(<BetaBookRisk quoteSource="yfinance" onManageTrade={(id) => managed.push(id)} />);
    await settle(150);
    let t = m.text();
    has(t, 'Income only'); ok('the collapsed bar says the book is Income-only');
    has(t, 'Book risk', 'At risk', '3 breached · 1 watch · 2 ok', 'CVaR 1-mo', '18.2% of capital', 'updated 3h ago', 'Review 4 fixes', 'Refresh');
    lacks(t, 'Guardrails', 'Stress scenarios'); ok('collapsed bar carries the grade, breach/watch/ok counts, CVaR, θ, freshness and the fix count — and nothing else');
    assert(log.length === 1 && log[0].includes('/book-tail-risk') && log[0].includes('refresh=0'), `expected one stored-snapshot read, got ${JSON.stringify(log)}`); ok('on load it reads the STORED snapshot only (no recompute)');
    assert(!log.some(l => l.includes('/book-hedge-advice')), 'LLM must not run on load'); ok('the LLM hedge plan does not fire on load');

    await click(m.container, /Book risk/); t = m.text();
    has(t, 'Overview', 'Fixes', 'Stress', 'Concentration', 'Hedge', 'Guardrails', 'CVaR 95% (1-mo)', '18.2% of capital', 'limit ≤ 15%', 'Up/down tail symmetry', 'Elevated.', 'What to do', 'Cap the AMD and SMH short calls first.');
    ok('opens on Overview: vitals, verdict, flagged guardrails with value vs limit, and the actions');
    has(t, 'Income trades only', '14 trades tagged Income', '12 analysed', 'Left out of the numbers: TOST, EQT', 'no open option legs', 'Not included: 2 Hedge, 2 Untagged, 1 Trade', 'not offsetting', 'Untagged trades are not counted — tag them Income'); ok('Overview states the scope: 14 Income trades, which other folders (2 Hedge, 2 Untagged, 1 Trade) are left out, and how to include untagged ones');
    has(t, 'Indicative prices — the market looks closed.', 'missing or inconsistent for 21 of 22 legs', 're-derived from that leg\'s price', 'press Refresh during market hours'); lacks(t, 'no usable price or IV'); ok('when the chain\'s IV had to be re-derived (market closed) the Overview says so, how many legs, and what to do');
    has(t, 'Deployed (Reg-T)', '$41,000', 'same basis as My Trades', 'Capital basis', 'cash-secured · spreads at width', 'the denominator the “% of capital” limits'); ok('the Book shows Deployed (Reg-T, the My Trades figure) next to the Capital basis (cash-secured notional + spread widths) and says how they differ');
    has(t, '2 within limits'); lacks(t, 'Tail loss at −20%'); ok('passing guardrails are folded away behind "N within limits"');
    await click(m.container, /2 within limits/); has(m.text(), 'Tail loss at −20%', '11.5%'); ok('…and open on request');
    await click(m.container, /Up\/down tail symmetry/); t = m.text();
    has(t, 'Cap the call side where the squeeze loses', 'Fix with:', 'AMD', 'SMH'); ok('a guardrail row opens to its fix and links to the trades that resolve it');

    // ── Fixes: by trade
    await click(m.container, /^Fixes/, '[role="tab"]'); t = m.text();
    has(t, '4 trades to fix, clearing 4 flagged guardrails', '8', 'each trade appears once'); ok('Fixes summarises: 4 trades clear 4 guardrails (the guardrail-first layout would show 8 cards)');
    const count = (hay: string, needle: string) => hay.split(needle).length - 1;
    assert(count(t, '770C (vs your short 700C)') === 1, `AMD fix should appear once, appeared ${count(t, '770C (vs your short 700C)')}×`); ok('AMD — flagged under three guardrails — appears exactly ONCE');
    has(t, 'Addresses 3 guardrails', 'Up/down tail symmetry', 'Correlated-cluster conc.', 'Sector concentration'); ok('the AMD card lists all three guardrails its fix addresses');
    before(t, 'AMD', 'SMH'); before(t, 'SMH', 'APP'); ok('ordered by guardrails cleared (AMD and SMH 3 each, larger risk first), then APP (1)');
    has(t, '−$49,872', '−$6,760', '−86% of the loss', '$11', '$232'); ok('risk now → after, % removed, cost and premium kept are the payload numbers');
    has(t, 'Buy 1× 770C', 'Close 700C'); ok('Evaluate (buy the wing) and Close (the short) actions are offered');
    // Evaluate hand-off: classic contract (sessionStorage) + navigation
    await click(m.container, /Buy 1× 770C/);
    const pre = JSON.parse(g.sessionStorage.getItem('evaluatePrefill') || 'null');
    assert(pre && pre.ticker === 'AMD' && pre.legs.length === 1 && pre.legs[0].strike === 770 && pre.legs[0].type === 'CALL' && pre.legs[0].action === 'BUY', `bad prefill ${JSON.stringify(pre)}`); ok('"Buy 1× 770C" stores the exact prefill for Evaluate (one entry per contract) and navigates');
    await click(m.container, /Close 700C/); assert(managed.length === 1 && managed[0] === 21, `expected onManageTrade(21), got ${JSON.stringify(managed)}`); ok('"Close 700C" jumps to that position (trade id 21)');
    has(m.text(), 'APP', '$126 left to earn', '$242 left to earn'); ok('every card shows the premium left to earn');

    // ── Fixes: the numbers are explainable and priced from the live quote
    await click(m.container, /^Fixes/, '[role="tab"]'); t = m.text();
    has(t, 'at +57% (+30% mkt × β 1.9)', 'live ask $0.11/sh'); ok('"Risk now" states the move behind it (market +30% × β 1.9 = +57%) and the cost states the live ask it was priced at');
    lacks(t, 'model $'); ok('no fix is priced from a model when a live quote existed');

    // a defined-risk spread (the user's real AAOI iron condor) is never told to buy a wing it already holds
    t = m.text();
    has(t, 'AAOI', 'Already a defined-risk spread', 'no wing to buy', 'CLOSE the 80/70P put spread', 'Close the spread', 'close the spread');
    lacks(t, 'BUY 1× 2026-10-30 70P'); ok('a defined-risk spread (an iron condor) is shown as already capped: the fix is "Close the spread", with no "buy the 70P you already hold"');
    // ── Stress
    await click(m.container, /^Stress/, '[role="tab"]'); t = m.text();
    has(t, 'Stress scenarios', '−$9,500', '−$38,000', '−5.2% of capital', 'Where a −20% month hits', 'Vol sensitivity', '+10%', '−$21,000', 'Assignment lab');
    ok('Stress shows the scenario tiles, the per-name waterfall, the vol grid and the assignment lab');
    has(t, 'Include stock losses on covered calls (downside)', 'Off: a selloff counts the option overlay only', 'the shares\' gain always offsets the short call'); ok('the covered-call checkbox is offered and says what it does; default OFF');
    has(t, 'How it\'s calculated', 'a −20% market move × its β', 'AMD and SMH are 100% of this loss', 'How to read it', 'The rally is 7.0× worse', 'the upside is your tail'); ok('each table says how it is calculated and how to use it (by-name read, vol-grid read: which side is worse)');
    lacks(t, 'If every naked short is assigned'); await click(m.container, /^Assignment lab/); t = m.text();
    has(t, 'If every naked short is assigned: $93,000', '$23,000', 'Put assignment', 'ITM contracts', 'Market (× β)', 'Covered calls and spread-protected shorts are excluded', 'P</b> = naked short puts'.replace('</b>', '')); ok('the assignment lab explains P / C (naked contracts ITM), the β-scaled market column and what is excluded');

    // checkbox → downside includes the shares' losses (instant, no refetch)
    const before1 = log.length;
    await click(m.container, /Include stock losses on covered calls/, 'label');
    t = m.text();
    has(t, '−$21,500', 'On: a selloff also counts the loss on the ~$120,000 of shares', 'Downside moves include the shares\' losses'); lacks(t, '−$9,500'); ok('ticking the box swaps in the with-stock downside (−20%: −$9,500 → −$21,500) and the note changes');
    has(t, '−$38,000'); ok('rally scenarios are identical either way (the shares\' gain always offsets the short call)');
    assert(log.length === before1, 'toggling must not hit the network'); ok('toggling is instant — no live-quote refresh');
    assert(g.localStorage.getItem('finoagent.beta.book.stockDownside') === '1'); ok('the choice is remembered');

    // ── Concentration
    await click(m.container, /^Concentration/, '[role="tab"]'); t = m.text();
    has(t, '14 names behave like ~4.1 independent bets', 'Counting each name separately gives 9.8', 'AMD alone is 38% of that loss on 12% of the capital', 'it would take 3.3 yr of its own θ');
    ok('Concentration opens with how many INDEPENDENT bets the book holds, and the biggest name\'s share of the tail vs its capital');
    has(t, 'Where the risk is vs where the money is', 'capital', 'loss', 'income', 'risk 3.17× its capital', 'payback 3.3 yr', 'KO'); ok('per name: share of capital · of the tail loss · of income, a "risk-heavy" flag, and years of its own θ to earn the loss back');
    lacks(t, 'risk 0.06×'); ok('a name whose tail share is far BELOW its capital share is not flagged');
    has(t, 'If a group gaps', 'Whole book', 'AMD · SMH', 'ρ 0.82', 'Semiconductors', '−$14,200', '−137%', '33.2 yr', 'Years of its carry', 'How to use it'); ok('each correlated cluster / sector is repriced on its own (−30 … +30%), with worst-case ÷ capital and ÷ its own carry');
    has(t, 'S&P 500 +0.74'); ok('macro loadings are kept (measured ρ)');

    // ── Hedge
    await click(m.container, /^Hedge/, '[role="tab"]'); t = m.text();
    has(t, 'What drives your tail', '84% of your worst months are rallies', 'cannot cut this tail', 'Direction (delta)', 'Convexity (gamma)', 'Volatility (vega)', 'Time decay (θ)'); ok('Hedge opens with what drives the tail: rally-driven, split into direction / convexity / vol / θ');
    has(t, 'Vol is only ~3% of the loss'); ok('and says when vol is too small a part of the tail for a vol hedge to matter');
    has(t, 'Insurance budget', '10% of income', '≤ $4,100/yr', 'your option income is $41,000/yr'); ok('the insurance budget is a share of the income that pays for it (default 10% = $4,100/yr)');
    has(t, 'Hedges that actually cut CVaR', 'Start here', 'Call spread 5%/15%', '1 × spread', '$2,800', '6.8% of income', '$28,000', '−$5,100 · −15.4%', 'Closes the gap', '17%', 'Upside tail (melt-up)', 'model (BS · index vol)');
    ok('only hedges that cut CVaR are listed, sized to the budget: cost/yr as % of income, CVaR after, share of the gap closed');
    has(t, 'Standby', 'Dynamic VIXY sleeve', 'Deploy rule: deploy when spot VIX > VIX3M (backwardation)', 'Until it fires there is nothing to buy'); ok('the VIXY sleeve is labelled Standby with its arm rule — and is not "Start here"');
    assert(t.split('Start here').length - 1 === 1, 'exactly one Start here'); before(t, 'Start here', 'Standby'); ok('exactly one hedge is "Start here" and it is the actionable one');
    lacks(t, 'pays when the market FALLS'); await click(m.container, /^Doesn't help your tail/);
    has(m.text(), 'Put spread 15%/30%', 'pays when the market FALLS, but 84% of your worst months are rallies'); ok('a put spread that would RAISE CVaR is not in the list — it is under "Doesn\'t help", with the reason');
    await click(m.container, /5% of income/, 'button'); t = m.text();
    has(t, 'One spread costs ~$2,800/yr — more than a 5% budget buys'); ok('a hedge that one contract cannot fit inside the budget says so instead of showing a fake size');
    await click(m.container, /25% of income/, 'button'); has(m.text(), '3 × spread', '$8,400', '20.5% of income', '−$13,100'); ok('a bigger budget re-sizes the hedge (3 spreads, $8,400/yr, CVaR cut $13,100)');
    t = m.text();
    has(t, 'ETF hedges around your holdings', 'grouped first', 'as a basket', 'AI data-center buildout', 'SOXX', 'Semiconductors (iShares)', 'basket ρ 0.9 · tight tracking', 'names ρ 0.37–0.89', '3 names, grouped by similarity', 'indicative prices', 'group loses $40,185 at ETF −15%', 'MU', '$22,366');
    ok('similar names are grouped first (the AI/semis group), the GROUP is matched as a basket to SOXX with the basket ρ next to the members\' own ρ range, the group\'s loss and its members');
    has(t, 'BUY 5× SOXX Dec 18 540 put @ $18.38', 'SELL 5× SOXX Dec 18 700 call @ $7.60'); ok('the default is the put funded by a sold call, shown as real legs with the live prices');
    has(t, '$5,388', '41% funded by the call', '$26,938', '37.9% of income · over the 10% budget', 'Offsets at −15%', '64%', '−$26,787', 'if SOXX +20% (the sold call)');
    ok('net cost, how much the call funds, annual cost vs income (over budget flagged), offset at −15%, and the upside the sold call gives up');
    has(t, 'covered only if you hold SOXX'); ok('the sold call is flagged as covered only if you hold the ETF — otherwise it adds upside risk');
    has(t, 'Group P&L', 'Hedge', 'If SOXX moves'); ok('every ETF scenario is shown: group P&L, hedge, net');
    await click(m.container, /^Put spread \(sell a lower put\)/); t = m.text();
    has(t, 'SELL 12× SOXX Dec 18 475 put @ $5.42', 'no call sold'); lacks(t, 'covered only if you hold SOXX'); ok('the put spread sells a lower put instead — no call, so no upside risk and no covered-call warning');
    await click(m.container, /^Put only/); has(m.text(), 'BUY 5× SOXX Dec 18 540 put @ $18.38', 'no funding leg', '64.7% of income'); ok('put-only shows the full cost');
    has(m.text(), 'Copy ticket'); ok('a ticket can be copied (nothing is placed)');
    lacks(m.text(), 'XLK skipped'); await click(m.container, /^How the groups were formed/); t = m.text();
    has(t, 'basket ρ 0.9', 'AMD · LRCX · MU', 'next best SMH 0.89 · XLK 0.85', 'Group\'s ETF', 'name ρ', 'β on ETF', '(itself)', 'ZZZ', 'covered by the index hedges above', 'XLK skipped', 'less than ONE option contract'); ok('"How the groups were formed" lists each group → ETF with its basket ρ and next-best ETFs, then each name\'s own ρ/β, who has no ETF, and why an ETF was skipped');
    has(t, 'Risk → hedge map', 'Net direction (β-Δ)', 'BUY 120 SPY', 'Net vega (short vol)', 'small · −0.88% of capital', 'Too small to insure on its own', 'below one SPY option', 'Short gamma (convexity)', 'material · −13.2% of capital', '3 trades in the Fixes tab', '~$260 to buy the wings');
    ok('each exposure maps to its hedge: β-Δ → SPY shares, net vega → tiny ("small, too small to insure"), gamma → the Fixes (3 trades, ~$260)');
    has(t, 'Menu sized to offset the −20% crash'); ok('the older sized-to-the-crash menu is kept, collapsed');
    assert(!log.some(l => l.includes('/book-hedge-advice')), 'still no LLM call'); ok('the AI hedge plan has still not run');
    await click(m.container, /Get AI hedge plan/); await settle(150);
    has(m.text(), 'AI hedging strategy · Income book', 'Trim AMD and keep the SPX spread.', 'Regenerate'); assert(log.filter(l => l.includes('/book-hedge-advice')).length === 1); ok('the AI hedge plan runs only on click, once, and renders');
    assert(log.some(l => l.includes('/book-hedge-advice') && l.includes('include_stock_downside=1')), 'AI advice must follow the checkbox'); ok('the AI plan follows the "include stock losses" choice');
    // untick → back to the overlay-only downside
    await click(m.container, /^Stress/, '[role="tab"]'); await click(m.container, /Include stock losses on covered calls/, 'label'); has(m.text(), '−$9,500'); lacks(m.text(), '−$21,500'); ok('unticking returns the overlay-only downside');

    // ── Refresh recomputes
    await click(m.container, /Refresh/); await settle(150);
    assert(log.some(l => l.includes('/book-tail-risk') && l.includes('refresh=1')), 'refresh=1 requested'); ok('Refresh asks for a live recompute (refresh=1)');
    await m.unmount();
  }
  {
    // No stored snapshot: the bar offers "Analyze book"; the analysis only runs on click.
    const log: string[] = [];
    g.fetch = async (url: string) => {
      log.push(`GET ${url}`);
      if (String(url).includes('refresh=0')) return { ok: true, status: 200, text: async () => JSON.stringify({ positions: 0, stored: false }) };
      await new Promise(r => setTimeout(r, 80));
      return { ok: true, status: 200, text: async () => JSON.stringify(fx.bookTailRisk()) };
    };
    const m = await mount(<BetaBookRisk quoteSource="yfinance" />);
    await settle(150);
    has(m.text(), 'Analyze book', 'Your Income trades · guardrails · stress tests · concentration'); lacks(m.text(), 'At risk'); ok('with no stored snapshot the bar says "Analyze book" and shows no stale numbers');
    await click(m.container, /Analyze book/);
    has(m.text(), 'Repricing the book'); ok('analysis shows a progress message while it runs');
    await settle(200); has(m.text(), 'At risk', 'Guardrails', 'Overview'); ok('the live result opens the panel on Overview');
    await m.unmount();
  }
  {
    // Everything is in the Income folder: the scope note says so instead of listing exclusions.
    const all = fx.bookTailRisk(); all.scope = { purpose: 'income', included: 5, excluded: {}, excluded_total: 0 };
    installFetch([['/book-tail-risk', () => all]], []);
    const m = await mount(<BetaBookRisk quoteSource="yfinance" />);
    await settle(150); await click(m.container, /Book risk/); const t = m.text();
    has(t, '5 trades tagged Income', 'Nothing else is open in other folders'); lacks(t, 'Not included', 'Untagged'); ok('with nothing in other folders the scope note says so');
    await m.unmount();
  }
  {
    // A healthy book: nothing to fix.
    const healthy = fx.bookTailRisk();
    healthy.risk_scorecard.grade = 'Sound'; healthy.risk_scorecard.n_breach = 0; healthy.risk_scorecard.n_warn = 0;
    healthy.risk_scorecard.checks = healthy.risk_scorecard.checks.map((c: any) => ({ ...c, status: 'pass', fix: undefined }));
    healthy.verdict = { level: 'Contained', summary: 'Tail risk is inside every limit.', actions: [] };
    installFetch([['/book-tail-risk', () => healthy]], []);
    const m = await mount(<BetaBookRisk quoteSource="yfinance" />);
    await settle(150); let t = m.text();
    has(t, 'Sound', '0 breached · 0 watch · 6 ok'); lacks(t, 'Review'); ok('a healthy book reads Sound with no "Review fixes" button');
    await click(m.container, /Book risk/); await click(m.container, /^Fixes/, '[role="tab"]');
    has(m.text(), 'Every guardrail is within its limit'); ok('and the Fixes tab says there is nothing to fix');
    await m.unmount();
  }
  {
    installFetch([['/book-tail-risk', () => ({ __status: 500, detail: 'engine down' })]], []);
    const m = await mount(<BetaBookRisk quoteSource="yfinance" />);
    await settle(150); has(m.text(), 'Analyze book'); await click(m.container, /Analyze book/); await settle(120);
    has(m.text(), 'engine down'); ok('a failed analysis shows the error and keeps the Analyze button');
    await m.unmount();
  }

  console.log(`\n${passed} passed`); process.exit(0);
}
main().catch(e => { console.error('FAILED:', e.stack || e); process.exit(1); });
