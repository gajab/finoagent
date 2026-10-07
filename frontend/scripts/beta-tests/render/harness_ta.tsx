// Render tests for the Beta Technical view in jsdom, with a MOCKED network (synthetic fixtures — never real data) and ECharts stubbed.
// The odds on screen are cross-checked against firstPassage.tradeOdds, which test_taodds.cjs validates against independent solvers.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { MemoryRouter } from 'react-router-dom';
import assert from 'assert';
import BetaTechnical from '../../../src/beta/ta/BetaTechnical';
import { tradeOdds } from '../../../src/beta/ta/firstPassage';
import * as F from '../preview/fixtures';

const g: any = globalThis;
let passed = 0;
const ok = (name: string) => { passed++; console.log('  ok -', name); };
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
// the smallest payload the classic Momentum panel adapter can render (checks + metrics), with no live trade
const MOMENTUM: any = { price: 238.9, as_of: 'x', setups: [], qualification: { is_candidate: false, grade: 'B', score: 61, passes: 4, summary: 'Stage-2 uptrend, no trigger yet.', gates: { uptrend: true }, checks: [{ key: 'trend', label: 'Stage-2 uptrend', status: 'pass', value: 'yes', ideal: 'yes', detail: 'price above a rising 200d' }] },
  metrics: { pct_from_52w_high: -4.2, rs: { available: true, slope_1m: 3.1, new_high: false }, rsi: 66, bb_squeeze: false, bb_width_pctile: 40, golden_cross: { above: true }, regime: { label: 'trending' } } };

interface Net { log: string[]; setups: (url: string) => any; traders?: (url: string) => any; gate: Record<string, Promise<void>>; delay?: (url: string, nth: number) => number; fail: Set<string>; posted: any[] }
function installFetch(net: Net) {
  g.fetch = async (url: string, opts: any) => {
    const method = opts?.method || 'GET';
    net.log.push(`${method} ${url}`);
    const gate = Object.entries(net.gate).find(([k]) => url.includes(k));
    if (gate) await gate[1];                                   // deterministic: the test decides when this response may arrive
    const wait = net.delay?.(url, net.log.filter(l => l.includes(url.split('?')[0])).length);
    if (wait) await new Promise(r => setTimeout(r, wait));
    const reply = (b: any, status = 200) => ({ ok: status < 400, status, text: async () => JSON.stringify(b) });
    if ([...net.fail].some(k => url.includes(k))) { net.fail.delete([...net.fail].find(k => url.includes(k))!); return reply({ detail: 'mock failure' }, 500); }
    if (url.includes('/api/tracked-trades') && method === 'POST') { net.posted.push(JSON.parse(opts.body)); return reply({ id: 1 }); }
    if (url.includes('/candles')) return reply(F.candles(new URL(url, 'http://x').searchParams.get('interval') || '1d'));
    if (url.includes('/trade-setups')) return reply(net.setups(url));
    if (url.includes('/trader-strategies')) return reply(net.traders ? net.traders(url) : F.traderStrategies);
    if (url.includes('/momentum-setup')) return reply({ ticker: 'NVDA', momentum_setup: MOMENTUM });
    if (url.includes('/day-trade-setups')) return reply(F.dayTradeSetups);
    if (url.includes('/dealer-positioning')) { const d: any = clone(F.dealer); if (url.includes('/AAA/')) d.dealer_positioning.gamma_levels.call_resistance.strike = 9999; return reply(d); }
    if (url.includes('/market-structure')) return reply(F.marketStructure);
    if (url.includes('/microstructure')) return reply(F.microstructure);
    if (url.includes('/regime-edge')) return reply({ detail: 'n/a' }, 404);
    if (url.includes('/regime')) return reply(F.regime);
    return reply({ detail: 'n/a' }, 404);
  };
}
const newNet = (over: Partial<Net> = {}): Net => ({ log: [], setups: () => clone(F.tradeSetups), gate: {}, fail: new Set(), posted: [], ...over });

async function mount(ticker = 'NVDA') {
  const container = document.createElement('div'); document.body.appendChild(container);
  const root = createRoot(container);
  const el = (t: string) => <MemoryRouter><BetaTechnical ticker={t} technical={F.technical as never} onLeave={() => { (g.__left = (g.__left || 0) + 1); }} /></MemoryRouter>;
  await act(async () => { root.render(el(ticker)); });
  return { container, text: () => container.textContent || '', rerender: async (t: string) => { await act(async () => root.render(el(t))); }, unmount: async () => { await act(async () => root.unmount()); container.remove(); } };
}
const settle = async (ms = 120) => { await act(async () => { await new Promise(r => setTimeout(r, ms)); }); };
const find = (c: HTMLElement, re: RegExp, sel = 'button') => [...c.querySelectorAll(sel)].find(b => re.test(b.textContent || '')) as HTMLElement | undefined;
async function click(c: HTMLElement, re: RegExp, sel = 'button') {
  const el = find(c, re, sel); assert(el, `no element matching ${re}`);
  await act(async () => { el!.dispatchEvent(new (g.window as any).MouseEvent('click', { bubbles: true })); });
  await settle(60);
}
const has = (t: string, ...xs: string[]) => xs.forEach(x => assert(t.includes(x), `missing "${x}"\n--- text ---\n${t.slice(0, 1500)}`));
const lacks = (t: string, ...xs: string[]) => xs.forEach(x => assert(!t.includes(x), `unexpected "${x}"`));
const count = (log: string[], frag: string) => log.filter(l => l.includes(frag)).length;
const reset = () => { try { g.localStorage.clear(); } catch { /* */ } };
const pressed = (c: HTMLElement) => [...c.querySelectorAll('button[aria-pressed="true"]')].map(b => (b.textContent || '').trim());

async function main() {
  console.log('BetaTechnical');

  // ── loading → loaded ──
  {
    reset();
    let release!: () => void; const net = newNet({ gate: { '/trade-setups': new Promise<void>(r => { release = r; }) } }); installFetch(net);
    const m = await mount();
    let t = m.text();
    has(t, 'Reading structure, regime and dealer flow', 'reading…', 'RSI 66 · MACD histogram +0.50');
    lacks(t, 'Lean long', 'No momentum signal');
    assert(m.container.querySelector('[data-testid="echart"]'), 'the chart is ready before the engine read'); ok('while the engine reads: a loading verdict, "reading…" lenses with raw numbers only, and the chart already drawn');
    release(); await settle(400); t = m.text();
    has(t, 'Lean long', 'medium conviction', 'Wrong if price trades below $229.87', 'Buy pullbacks into support');
    has(t, 'Trend', 'supports', 'Market structure is bullish');
    ok('then the verdict, conviction, "wrong if" level and lenses appear');
    has(t, 'Trend Pullback', '6.75R', '$3.80', '65 sh', 'Odds', '32 days · market-implied', 'T1 first', 'Stop first');
    ok('the plan card shows reward:risk, risk per share, size for 1% risk and the odds window');
    assert.equal(count(net.log, '/trade-setups'), 1, 'one engine read despite StrictMode-style remounts'); ok('the engine read is fetched exactly once');
    await m.unmount();
  }

  // ── the odds on screen are the tested model's output ──
  {
    reset(); installFetch(newNet()); const m = await mount(); await settle(400);
    const o: any = tradeOdds({ direction: 'long', spot: 238.9, entry: 233.67, stop: 229.87, target: 259.31, iv: 0.315, days: 32 });
    const pc = (p: number) => `${(p * 100).toFixed(p < 0.1 ? 1 : 0)}%`;
    has(m.text(), `T1 first ${pc(o.win)}`, `Stop first ${pc(o.loss)}`, `Open ${pc(o.inside)}`, `${pc(o.fillProb)} chance it fills`, `needs ${pc(o.breakEven)} to break even`);
    ok('the odds block equals tradeOdds() for the same inputs (win, loss, open, fill chance, break-even)');
    await m.unmount();
  }

  // ── lens states follow the verdict ──
  {
    reset(); installFetch(newNet()); const m = await mount(); await settle(400);
    const rows = [...m.container.querySelectorAll('ul li')].map(li => (li.textContent || ''));
    const row = (k: string) => rows.find(r => r.startsWith(k)) || '';
    assert(/Trend.*supports/.test(row('Trend'))); assert(/Momentum.*supports/.test(row('Momentum'))); assert(/Dealer flow.*supports/.test(row('Dealer flow')));
    assert(/Stretch.*context.*neutral/.test(row('Stretch'))); assert(/Volume.*context.*supports/.test(row('Volume'))); assert(/Regime.*context.*supports/.test(row('Regime')));
    ok('six lenses: three counted votes and three context lenses, each with a state');
    await m.unmount();
  }

  // ── no implied vol → no odds, with the reason ──
  {
    reset(); const net = newNet({ setups: () => { const b = clone(F.tradeSetups); (b.trade_setups.context as any).expected_move = null; return b; } }); installFetch(net);
    const m = await mount(); await settle(400); const t = m.text();
    has(t, 'No implied-volatility reading for NVDA', 'Lean long'); lacks(t, 'T1 first', 'Stop first');
    ok('without implied volatility no odds are invented — the card says why, the verdict still shows'); await m.unmount();
  }

  // ── mixed / no edge ──
  {
    reset(); const net = newNet({ setups: () => { const b: any = clone(F.tradeSetups); b.trade_setups.context.bias = { direction: 'neutral', strength: 'weak', score: 0.1, rationale: '', regime: 'trending', confirmations: [
      { signal: 'market_structure', reads: 'bullish', detail: 'market structure is bullish' }, { signal: 'daily_trend', reads: 'bearish', detail: 'daily trend is down' }] }; b.trade_setups.setups = []; return b; } }); installFetch(net);
    const m = await mount(); await settle(400); const t = m.text();
    has(t, 'Mixed — no clear edge', 'The signals pull both ways', 'What would settle it', 'resistance at $249.50', 'support at $227.10', 'No clean swing setup right now'); lacks(t, 'Lean long', 'Wrong if');
    ok('mixed signals: no side is picked, the two zones that would settle it are named, and there is no plan to invent'); await m.unmount();
  }

  // ── failure and retry ──
  {
    reset(); const net = newNet(); net.fail.add('/trade-setups'); installFetch(net);
    const m = await mount(); await settle(400); let t = m.text();
    has(t, "Couldn't read the market: mock failure", 'Retry', 'Needs the market read'); lacks(t, 'Lean long');
    assert(m.container.querySelector('[data-testid="echart"]'), 'chart survives a failed engine read');
    await click(m.container, /Retry/); await settle(300); t = m.text();
    has(t, 'Lean long'); assert.equal(count(net.log, '/trade-setups'), 2); ok('a failed read says so, leaves the chart alone, and Retry recovers'); await m.unmount();
  }

  // ── styles ──
  {
    reset(); const net = newNet(); installFetch(net); const m = await mount(); await settle(400);
    await click(m.container, /^Position$/, '[role="tab"]'); await settle(100); let t = m.text();
    has(t, 'Position Accumulate', '90 days · market-implied'); assert.equal(m.container.querySelector('[data-testid="echart"]')!.getAttribute('data-candles'), '110', 'weekly candles for a position plan');
    ok('Position: the position setup, a 90-day odds window, weekly candles');
    assert.equal(count(net.log, '/day-trade-setups'), 0, 'day setups are lazy');
    await click(m.container, /^Day$/, '[role="tab"]'); await settle(300); t = m.text();
    has(t, 'VWAP Pullback', '1 session · market-implied'); assert.equal(count(net.log, '/day-trade-setups'), 1);
    await click(m.container, /^Swing$/, '[role="tab"]'); await click(m.container, /^Day$/, '[role="tab"]'); await settle(100);
    assert.equal(count(net.log, '/day-trade-setups'), 1, 'day setups fetched once per ticker'); ok('Day: fetched only when opened, once, odds quoted over one trading session');
    await click(m.container, /^Swing$/, '[role="tab"]'); await settle(100);
    await click(m.container, /^#2$/, '[role="tab"]'); await settle(60); t = m.text();
    has(t, 'Breakout', 'Needs a break through the entry to fill'); ok('ranked setups can be stepped through; a breakout plan reads "needs a break through the entry"');
    await m.unmount();
  }

  // ── layers: lazy per method, fetched once, persisted ──
  {
    reset(); const net = newNet(); installFetch(net); const m = await mount(); await settle(400);
    assert.deepEqual(pressed(m.container).filter(x => /^(Plan|Key levels)$/.test(x)).sort(), ['Key levels', 'Plan']);
    assert.equal(count(net.log, '/dealer-positioning') + count(net.log, '/market-structure') + count(net.log, '/microstructure'), 0, 'method layers are not fetched until used');
    const base = Number(m.container.querySelector('[data-testid="echart"]')!.getAttribute('data-lines'));
    await click(m.container, /^Dealer gamma$/); await settle(300);
    assert.equal(count(net.log, '/dealer-positioning'), 1); assert(Number(m.container.querySelector('[data-testid="echart"]')!.getAttribute('data-lines')) > base, 'dealer levels are on the chart');
    await click(m.container, /^Dealer gamma$/); await click(m.container, /^Dealer gamma$/); await settle(150);
    assert.equal(count(net.log, '/dealer-positioning'), 1, 'toggling again never refetches');
    assert(JSON.parse(g.localStorage.getItem('beta:ta:layers')).includes('dealer'), 'chosen layers persist for next time');
    await click(m.container, /^Order blocks$/); await click(m.container, /^Liquidity$/); await settle(300);
    assert.equal(count(net.log, '/market-structure'), 1, 'structure, blocks and liquidity share one fetch');
    ok('layers load on first use, once, share a fetch where the data is shared, and persist');
    // a lens row switches its layer on and off
    await click(m.container, /^Dealer flow/); await settle(60); assert(!pressed(m.container).includes('Dealer gamma'), 'clicking the lens turns the layer off');
    await click(m.container, /^Dealer flow/); await settle(100); assert(pressed(m.container).includes('Dealer gamma')); ok('clicking a lens draws / hides its layer');
    await m.unmount();
  }

  // ── timeframe ──
  {
    reset(); const net = newNet(); installFetch(net); const m = await mount(); await settle(400);
    await click(m.container, /^1H$/, '[role="tab"]'); await settle(200);
    assert(count(net.log, 'interval=1h') >= 1); assert.equal(m.container.querySelector('[data-testid="echart"]')!.getAttribute('data-candles'), '420');
    ok('the chart timeframe switch loads that resolution'); await m.unmount();
  }

  // ── evidence is lazy ──
  {
    reset(); const net = newNet(); installFetch(net); const m = await mount(); await settle(400);
    assert.equal(count(net.log, '/chart-patterns'), 0);
    await click(m.container, /Chart patterns/); await settle(200);
    assert(count(net.log, '/chart-patterns') >= 1); ok('evidence panels fetch only when opened'); await m.unmount();
  }

  // ── track ──
  {
    reset(); const net = newNet(); installFetch(net); const m = await mount(); await settle(400);
    await click(m.container, /Track this trade/); await settle(150);
    assert.equal(net.posted.length, 1); const p = net.posted[0];
    assert.equal(p.ticker, 'NVDA'); assert.equal(p.direction, 'long'); assert.equal(p.instrument, 'equity'); assert.equal(p.entry_level, 233.67); assert.equal(p.stop_level, 229.87);
    assert.deepEqual(p.target_levels, [259.31, 261.98]); assert.equal(p.evaluate_now, true);
    has(m.text(), 'Tracking — open'); ok('Track this trade posts the same payload the classic card does, then offers to open the tracker'); await m.unmount();
  }

  // ── levels table ──
  {
    reset(); installFetch(newNet()); const m = await mount(); await settle(400); const t = m.text();
    has(t, 'Levels that matter', 'Target 1', '$259.31', 'Stop', '$229.87', 'your plan', 'Dealer gamma flip', '$222.31');
    const tbl = t.slice(t.indexOf('Levels that matter'));            // only the table (the verdict bar also quotes $229.87)
    const i1 = tbl.indexOf('$259.31'), i2 = tbl.indexOf('$233.67'), i3 = tbl.indexOf('$229.87');
    assert(i1 > 0 && i1 < i2 && i2 < i3, 'levels are listed high to low'); ok('the levels table lists the plan and the nearest zones, high to low'); await m.unmount();
  }

  // ── ticker change: no stale data ──
  {
    reset();
    // AAA's answer is the BEARISH one at $111.11 and arrives LAST (after the user has already moved to BBB, which answers fast)
    const net = newNet({ delay: (url, nth) => (url.includes('/trade-setups') ? (url.includes('/AAA/') ? 350 : 40) : 0) });
    net.setups = (url: string) => { const b: any = clone(F.tradeSetups); if (url.includes('/AAA/')) { b.trade_setups.price = 111.11; b.trade_setups.context.bias.direction = 'bearish'; } return b; };
    installFetch(net); const m = await mount('AAA'); await m.rerender('BBB'); await settle(200);
    has(m.text(), 'Lean long');                                      // BBB's answer is in
    await settle(500); const t = m.text();                           // ... and now AAA's late answer has arrived
    has(t, 'Lean long'); lacks(t, 'Lean short', '$111.11'); assert.equal(count(net.log, '/trade-setups'), 2);
    ok('switching ticker mid-load never shows the previous ticker\'s read, even when its answer arrives last'); await m.unmount();
  }

  // ── a method layer's slow answer for the previous ticker never lands on the new one ──
  {
    reset(); g.localStorage.setItem('beta:ta:layers', JSON.stringify(['plan', 'levels', 'dealer']));
    const net = newNet({ delay: (url) => (url.includes('/dealer-positioning') && url.includes('/AAA/') ? 400 : 0) }); installFetch(net);
    const m = await mount('AAA'); await settle(60); await m.rerender('BBB'); await settle(900);
    lacks(m.text(), '9,999', '9999'); assert(count(net.log, '/AAA/dealer-positioning') === 1 && count(net.log, '/BBB/dealer-positioning') === 1);
    has(m.text(), 'Call Resistance'); ok('a slow layer answer for the previous ticker is dropped, not drawn on the new ticker'); await m.unmount();
  }

  // ── corrupted saved layers ──
  {
    reset(); g.localStorage.setItem('beta:ta:layers', '{"oops":1}'); installFetch(newNet());
    const m = await mount(); await settle(300); has(m.text(), 'Lean long'); assert(pressed(m.container).includes('Plan'), 'falls back to the default layers');
    ok('a corrupted saved-layers value falls back to the defaults instead of crashing'); await m.unmount();
  }

  // ── strategies board ──
  {
    reset(); const net = newNet(); installFetch(net); const m = await mount(); await settle(400);
    assert.equal(count(net.log, '/trader-strategies'), 0, 'the 15 rule-sets are not run until the board is opened');
    await click(m.container, /^Strategies/); await settle(400);
    assert.equal(count(net.log, '/trader-strategies'), 1);
    const tile = (id: string) => m.container.querySelector(`[data-tile="${id}"]`) as HTMLElement;
    const ids = [...m.container.querySelectorAll('[data-testid="beta-strategies"] [data-tile]')].map(e => e.getAttribute('data-tile'));
    assert.equal(ids.length, 3 + 3 + 13, 'three engine styles, three named screens, thirteen traders'); assert(!ids.includes('qullamaggie') || ids.indexOf('qullamaggie') < 6 || false);
    for (const k of ['swing', 'position', 'day', 'momentum', 'qullamaggie', 'connors_rsi2', 'minervini', 'oneil', 'weinstein', 'turtles', 'ptj', 'druckenmiller', 'raschke', 'livermore', 'carter', 'ichimoku', 'elder', 'wyckoff', 'cta']) assert(tile(k), `tile ${k}`);
    assert(!tile('connors') , 'Connors is the named screen, not a second tile');
    const D: any = F.traderStrategies.trader_strategies;
    has(m.text(), `${D.consensus.bull} bullish`, `${D.consensus.neutral} neutral`, `${D.consensus.bear} bearish`, `of ${D.consensus.n}`, 'no edge predicting returns');
    ok('opening the board runs the 15 rule-sets once and shows 3 engine styles, 3 named screens and the 13 other traders, with the consensus');

    // a trader's detail: its rules with measured values, its exit, how that exit has behaved
    await click(m.container, /./, '[data-tile="minervini"]'); await settle(60);
    const det = m.container.querySelector('[data-testid="trader-detail"]')!.textContent || '';
    const mv = D.strategies.find((s: any) => s.key === 'minervini');
    has(det, 'Mark Minervini', mv.philosophy, `${mv.met} of ${mv.total} rules met`, 'Draw on the chart', 'Their rules');
    for (const r of mv.rules) has(det, r.rule);
    assert.equal(m.container.querySelectorAll('[data-testid="trader-detail"] [data-rule]').length, mv.rules.length);
    has(det, 'How this kind of exit has behaved', 'Minervini ⅓ at 50d', 'hold 126 days');
    ok('a trader tile opens its checklist with every rule, its philosophy, its exit and the backtested behaviour of that exit');

    // a lost line: a price-line exit reads as a level to reclaim; a non-price exit's line is only a reference
    const detailText = () => m.container.querySelector('[data-testid="trader-detail"]')!.textContent || '';
    const lostLevel = D.strategies.find((s: any) => s.available && s.stop?.breached && s.exit_is_level && !s.named_elsewhere);
    const lostRef = D.strategies.find((s: any) => s.available && s.stop?.breached && s.exit_is_level === false && !s.named_elsewhere);
    const live = D.strategies.find((s: any) => s.available && s.stop && !s.stop.breached && s.exit_is_level && !s.named_elsewhere);
    assert(lostLevel && lostRef && live, 'the fixture must contain a lost price-line exit, a lost reference line and a live exit');
    await click(m.container, /./, `[data-tile="${lostLevel.key}"]`); await settle(60);
    has(detailText(), `Price has closed below $${lostLevel.stop.level.toFixed(2)}`, 'level to reclaim, not a future exit');
    await click(m.container, /./, `[data-tile="${lostRef.key}"]`); await settle(60);
    has(detailText(), `Price is below $${lostRef.stop.level.toFixed(2)}, a reference line`, 'not a price line', 'has not triggered it'); lacks(detailText(), 'level to reclaim', 'trend condition has failed');
    await click(m.container, /./, `[data-tile="${live.key}"]`); await settle(60);
    has(detailText(), `Their exit sits at $${live.stop.level.toFixed(2)}`); lacks(detailText(), 'Price has closed below', 'reference line');
    ok('a lost price-line exit is a level to reclaim; a lost reference line never claims their exit fired; a live exit is a future exit');

    // draw on the chart: one strategy at a time, removable
    const lines = () => Number(m.container.querySelector('[data-testid="echart"]')!.getAttribute('data-lines'));
    const base = lines();
    await click(m.container, /./, '[data-tile="turtles"]'); await settle(60);
    await click(m.container, /Draw on the chart/); await settle(80);
    assert(lines() > base, 'the strategy levels are on the chart'); has(m.text(), 'Turtles'); assert(m.container.querySelector('[data-testid="strategy-chip"]'));
    assert(find(m.container, /Drawn on the chart — remove/), 'the button flips to remove');
    await click(m.container, /./, '[data-tile="cta"]'); await settle(60); await click(m.container, /Draw on the chart/); await settle(80);
    assert.equal(m.container.querySelectorAll('[data-testid="strategy-chip"]').length, 1); has(m.container.querySelector('[data-testid="strategy-chip"]')!.textContent || '', 'Trend CTAs');
    await click(m.container, /./, '[data-testid="strategy-chip"]'); await settle(60);
    assert.equal(lines(), base, 'removing from the chip clears the overlay'); assert(!m.container.querySelector('[data-testid="strategy-chip"]'));
    ok('Draw on the chart adds that strategy\'s levels to the cockpit chart, one strategy at a time, removable from the chip');

    // filters
    await click(m.container, /^Trend following/); await settle(40);
    const shown = () => [...m.container.querySelectorAll('[data-testid="beta-strategies"] [data-tile]')].map(e => e.getAttribute('data-tile')).filter(k => D.strategies.some((s: any) => s.key === k && !s.named_elsewhere));
    const trend = D.strategies.filter((s: any) => s.family === 'trend' && !s.named_elsewhere).map((s: any) => s.key).sort();
    assert.deepEqual(shown().sort(), trend);
    await click(m.container, /^Bullish/); await settle(40);
    assert.deepEqual(shown().sort(), D.strategies.filter((s: any) => s.family === 'trend' && !s.named_elsewhere && s.stance === 'bullish').map((s: any) => s.key).sort());
    assert(tile('swing') && tile('momentum'), 'engine and named tiles are never hidden by a trader filter');
    await click(m.container, /^All \d+$/); await settle(40);                       // family back to all; the stance filter (bullish) stays
    assert.equal(shown().length, D.strategies.filter((s: any) => !s.named_elsewhere && s.stance === 'bullish').length);
    ok('family and stance filters narrow only the trader tiles, and combine');
    await click(m.container, /^Bullish/); await settle(40);

    // engine setups
    await click(m.container, /./, '[data-tile="swing"]'); await settle(60);
    has(m.text(), 'Trend Pullback', '6.75R');
    await click(m.container, /./, '[data-tile="position"]'); await settle(60); has(m.text(), 'Position Accumulate');
    assert.equal(count(net.log, '/day-trade-setups'), 0);
    await click(m.container, /./, '[data-tile="day"]'); await settle(250); has(m.text(), 'VWAP Pullback'); assert.equal(count(net.log, '/day-trade-setups'), 1);
    await click(m.container, /./, '[data-tile="swing"]'); await click(m.container, /./, '[data-tile="day"]'); await settle(80);
    assert.equal(count(net.log, '/day-trade-setups'), 1); ok('engine tiles list the same ranked setups; the day trade scan runs only once, when asked');

    // named screens are lazy and show their state on the tile
    assert.equal(count(net.log, '/momentum-setup'), 0);
    await click(m.container, /./, '[data-tile="momentum"]'); await settle(250);
    assert.equal(count(net.log, '/momentum-setup'), 1); has(m.text(), 'Momentum Swing', 'Stage-2 uptrend'); has(tile('momentum').textContent || '', 'Grade B');
    await click(m.container, /./, '[data-tile="swing"]'); await click(m.container, /./, '[data-tile="momentum"]'); await settle(100);
    assert.equal(count(net.log, '/momentum-setup'), 1); ok('a named screen runs when its tile is picked, once, and its grade shows on the tile afterwards');
    await m.unmount();
  }

  // ── strategies board: failure, retry, unavailable traders, stale ticker ──
  {
    reset(); const net = newNet(); net.fail.add('/trader-strategies'); installFetch(net); const m = await mount(); await settle(300);
    await click(m.container, /^Strategies/); await settle(300);
    has(m.text(), "Couldn't run the trader rule-sets: mock failure", 'Retry');
    assert(m.container.querySelector('[data-tile="swing"]') && m.container.querySelector('[data-tile="momentum"]'), 'engine and named tiles still work'); assert(!m.container.querySelector('[data-tile="minervini"]'));
    await click(m.container, /Retry/, '[data-testid="beta-strategies"] button'); await settle(300);
    assert(m.container.querySelector('[data-tile="minervini"]')); assert.equal(count(net.log, '/trader-strategies'), 2); ok('a failed rule-set run says so, leaves the other tiles usable, and Retry recovers'); await m.unmount();
  }
  {
    reset(); const net = newNet({ traders: () => { const b: any = clone(F.traderStrategies); const w = b.trader_strategies.strategies.find((s: any) => s.key === 'weinstein');
      Object.assign(w, { available: false, reason: 'Needs about 200 daily bars (a 30-week average and a weekly stage).', rules: [], met: 0, total: 0, grade: 'no_data', stance: 'neutral', stop: null, levels: [], exit_backtest: null }); delete w.philosophy; return b; } });
    installFetch(net); const m = await mount(); await settle(300); await click(m.container, /^Strategies/); await settle(300);
    has(m.container.querySelector('[data-tile="weinstein"]')!.textContent || '', 'Not scored'); lacks(m.container.querySelector('[data-tile="weinstein"]')!.textContent || '', 'rules met');
    await click(m.container, /./, '[data-tile="weinstein"]'); await settle(60);
    has(m.container.querySelector('[data-testid="trader-detail"]')!.textContent || '', 'Not scored', '200 daily bars'); assert(!find(m.container, /Draw on the chart/));
    ok('a trader the backend could not score shows "Not scored" and why — no fake rules, nothing to draw'); await m.unmount();
  }
  {
    reset(); const net = newNet({ delay: (url) => (url.includes('/trader-strategies') && url.includes('/AAA/') ? 350 : 0) });
    net.traders = (url: string) => { const b: any = clone(F.traderStrategies); if (url.includes('/AAA/')) b.trader_strategies.consensus = { d: 1, bull: 15, bear: 0, neutral: 0, n: 15 }; return b; };
    installFetch(net); const m = await mount('AAA'); await settle(40);
    // the board mounts when opened, so open it for AAA, then move to BBB before AAA answers
    await click(m.container, /^Strategies/); await m.rerender('BBB'); await settle(900);
    const t = m.text(); lacks(t, '15 bullish'); has(t, `${(F.traderStrategies.trader_strategies as any).consensus.bull} bullish`);
    ok('switching ticker while the rule-sets run never shows the previous ticker\'s consensus'); await m.unmount();
  }

  // ── leaving ──
  {
    reset(); installFetch(newNet()); g.__left = 0; const m = await mount(); await settle(300);
    await click(m.container, /Back to classic/); assert.equal(g.__left, 1); ok('"Back to classic" calls onLeave'); await m.unmount();
  }
  console.log(`\n${passed} passed`);
}
main().then(() => process.exit(0)).catch(e => { console.error('FAIL:', e.message || e); process.exit(1); });
