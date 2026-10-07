// Render tests for the Beta Manage panels (Underlying Analysis + Defend & repair), run in jsdom against realistic payloads
// with a mocked network. Run via render.sh. Payload shapes mirror what the live panels returned for a covered call.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { act } from 'react-dom/test-utils';
import { MemoryRouter } from 'react-router-dom';
import assert from 'assert';
import BetaUnderlyingAnalysis from '../../../src/beta/BetaUnderlyingAnalysis';
import BetaDefend from '../../../src/beta/BetaDefend';
import BetaTradeInspector from '../../../src/beta/BetaTradeInspector';
import BetaBookRisk from '../../../src/beta/BetaBookRisk';

const g: any = globalThis;
const fx = g.__fx;
let passed = 0;
const ok = (name: string) => { passed++; console.log('  ok -', name); };

function installFetch(routes: [string, (body: any) => any][], log: string[]) {
  g.fetch = async (url: string, opts: any) => {
    log.push(`${opts?.method || 'GET'} ${url}`);
    const hit = routes.find(([k]) => url.includes(k));
    if (!hit) return { ok: false, status: 404, text: async () => 'not found' };
    const out = await hit[1](opts?.body ? JSON.parse(opts.body) : null);
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
const has = (t: string, ...xs: string[]) => xs.forEach(x => assert(t.includes(x), `missing "${x}"`));
const lacks = (t: string, ...xs: string[]) => xs.forEach(x => assert(!t.includes(x), `unexpected "${x}"`));
const before = (t: string, a: string, b: string) => assert(t.indexOf(a) >= 0 && t.indexOf(b) >= 0 && t.indexOf(a) < t.indexOf(b), `"${a}" should come before "${b}"`);

const trade: any = { id: 7, ticker: 'DRAM', strategy_type: 'covered_call', legs_data: [{ action: 'sell', type: 'call', strike: 85, qty: 1 }], parameters: {} };
const pnl: any = { underlying_price: 61.67, unrealized_pnl: 232 };

async function main() {
  console.log('BetaUnderlyingAnalysis');
  {
    const log: string[] = [];
    installFetch([['/trade-manager/ai', () => ({ verdict: 'EXIT', one_line: 'AI says close it', why: ['reason one'], conviction: 70, _meta: { model: 'm', packet_chars: 5000 } })], ['/trade-manager', async () => { await new Promise(r => setTimeout(r, 120)); return fx.baseTM(); }]], log);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    let t = m.text();
    assert(t.includes('Reading structure'), 'loading state should show first'); ok('shows a loading state with context while the read runs');
    await settle(300);
    t = m.text();
    has(t, 'Technical Analysis', 'Strong exit', 'What to do from here', 'Exit now', 'How the score is built', 'Exit plan', 'Price ladder', 'What to watch next', 'Evidence', 'AI assist');
    ok('renders every section');
    before(t, 'Technical Analysis', 'Strong exit'); before(t, 'Strong exit', 'How the score is built'); before(t, 'How the score is built', 'Price ladder'); before(t, 'Price ladder', 'What to watch next');
    ok('Technical Analysis is on top, then verdict, score build, exit plan, watch next');
    has(t, 'Since you entered', '54 days', '+$232', '54.80 → 61.67', 'with you', 'Rolled', 'Your thesis'); ok('"since you entered" strip carries held days, P&L, stock move, rolls and thesis');
    has(t, 'Captured 100% of max profit', 'Technical (51) and Quant (4) disagree'); ok('overrides and conflicts are shown');
    // ladder: higher prices first, spot in between, stop below
    before(t, '70.00', '63.82'); before(t, '63.82', 'Now $61.67'); before(t, 'Now $61.67', '55.35'); ok('price ladder sorts targets above "Now $61.67" and the stop below it');
    has(t, 'in 25d', '+$150', 'Rules & timing'); ok('time- and P&L-based rules are listed separately');
    has(t, 'Typical daily move', 'Reward : risk', '0.34 : 1', 'Short call 85', '18% to touch'); ok('plan tiles carry ATR, reward:risk and the strike touch odds');
    has(t, 'Strong exit'); assert(t.includes('Not scored'), 'unscored lens labelled'); ok('unscored lenses read "Not scored", not a fake number');
    // evidence opens from a lens tile
    await click(m.container, /Quant desk \(hold read\)/);
    t = m.text(); has(t, 'Why this score'); ok('tapping a lens tile opens its evidence');
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
    installFetch([['/trade-manager', () => fx.stockTM()]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={{ ...trade, strategy_type: 'stock_long', legs_data: [] }} pnl={pnl} />);
    await settle(150); const t = m.text();
    has(t, 'Hold', 'Stock', 'Exit plan'); lacks(t, 'Price ladder', 'How often did closing beat holding'); ok('stock position with no plan items, no vol and no hold-odds renders without crashing');
    await m.unmount();
  }
  {
    installFetch([['/trade-manager', () => ({ __status: 500, detail: 'engine down' })]], []);
    const m = await mount(<BetaUnderlyingAnalysis trade={trade} pnl={pnl} />);
    await settle(150); has(m.text(), 'engine down', 'Retry', 'Technical Analysis'); ok('an error shows a Retry and still keeps the Technical Analysis card');
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
    before(t, 'Desk recommendation', 'Next best moves'); before(t, 'Next best moves', 'Risk read'); ok('order is recommendation → moves → risk read');
    has(t, 'Show 2 more'); lacks(t, 'Calendarised hedge'); await click(m.container, /Show 2 more/); has(m.text(), 'Calendarised hedge', 'Show fewer'); ok('shows the top 4 moves and reveals the rest on request');
    has(m.text(), 'Close & redeploy — 1 option'); lacks(m.text(), 'defined condor'); await click(m.container, /Close & redeploy/); has(m.text(), 'defined condor'); ok('"close & redeploy" options are kept apart from fixes and fold open');
    has(m.text(), 'Desk pick'); ok('the desk pick (here the Close benchmark) is flagged');
    await click(m.container, /Roll to the \$86 call/); t = m.text();
    has(t, 'Edge / \$ risk', 'P(OTM)', '98.4%', 'clears', '2/6', 'Evaluate in the Income Desk'); ok('expanding a move shows payoff, score breakdown, roll read and the Evaluate hand-off');
    await click(m.container, /Cover it/); has(m.text(), 'Earnings:', 'earnings print before expiry'); ok('an earnings-exposed move shows its deduction');
    has(m.text(), 'Assignment risk', 'P(finish ITM)', 'Early-assignment risk'); ok('assignment risk is open by default when early assignment is a risk');
    for (const x of ['Cost of waiting', 'What broke & the setup', 'Synthetic reframe', 'War room']) has(m.text(), x);
    await click(m.container, /^What broke/); has(m.text(), 'time value', 'double bottom', 'Structure map', 'dealers long'); ok('context accordion lists time value, pattern and the structure map');
    await click(m.container, /^Cost of waiting/); has(m.text(), 'Recovery odds', '+20d'); ok('cost of waiting renders its table');
    assert(!log.some(l => l.includes('/defend/committee')), 'war room must not run on load'); ok('the LLM war room does not fire on load');
    await click(m.container, /^War room/); await click(m.container, /Convene the defense desk/); await settle(100);
    has(m.text(), 'Defense verdict', 'Close it', 'because q', 'do not widen'); ok('war room runs only on click and renders roles + verdict');
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
    has(t, 'Underlying Analysis', 'Technical Analysis', 'Exit plan'); ok('Manage shows the Underlying Analysis (hold / exit plan) for the shares');
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
    installFetch([['/trade-manager', () => fx.baseTM()], ['/defend/refine', () => fx.defendMenu('done')], ['/repair-menu', () => fx.defendMenu('pending')]], log);
    const m = await mount(insp(opt, opnl));
    await click(m.container, /^Manage$/, 'button'); await settle(300);
    assert(JSON.stringify(tabBtns(m.container)) === JSON.stringify(['Underlying Analysis', 'Defend & repair']), `sub-tabs were ${JSON.stringify(tabBtns(m.container))}`); ok('an option position keeps both sub-tabs: Underlying Analysis, then Defend & repair');
    assert(!log.some(l => l.includes('/repair-menu')), 'Defend must not run until opened'); ok('Defend & repair does not run until its sub-tab is opened');
    await click(m.container, /^Defend & repair$/, '[role="tab"]'); await settle(300);
    assert(log.some(l => l.includes('/repair-menu')), 'repair-menu requested after opening'); ok('opening Defend & repair requests the menu');
    has(m.text(), 'Defend & repair options'); ok('the Overview shortcut to Defend & repair is present for an option position');
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
    has(t, 'Book risk', 'At risk', '3 breached · 1 watch · 2 ok', 'CVaR 1-mo', '18.2% of capital', 'updated 3h ago', 'Review 3 fixes', 'Refresh');
    lacks(t, 'Guardrails', 'Stress scenarios'); ok('collapsed bar carries the grade, breach/watch/ok counts, CVaR, θ, freshness and the fix count — and nothing else');
    assert(log.length === 1 && log[0].includes('/book-tail-risk') && log[0].includes('refresh=0'), `expected one stored-snapshot read, got ${JSON.stringify(log)}`); ok('on load it reads the STORED snapshot only (no recompute)');
    assert(!log.some(l => l.includes('/book-hedge-advice')), 'LLM must not run on load'); ok('the LLM hedge plan does not fire on load');

    await click(m.container, /Book risk/); t = m.text();
    has(t, 'Overview', 'Fixes', 'Stress', 'Concentration', 'Hedge', 'Guardrails', 'CVaR 95% (1-mo)', '18.2% of capital', 'limit ≤ 15%', 'Up/down tail symmetry', 'Elevated.', 'What to do', 'Cap the AMD and SMH short calls first.');
    ok('opens on Overview: vitals, verdict, flagged guardrails with value vs limit, and the actions');
    has(t, '2 within limits'); lacks(t, 'Tail loss at −20%'); ok('passing guardrails are folded away behind "N within limits"');
    await click(m.container, /2 within limits/); has(m.text(), 'Tail loss at −20%', '11.5%'); ok('…and open on request');
    await click(m.container, /Up\/down tail symmetry/); t = m.text();
    has(t, 'Cap the call side where the squeeze loses', 'Fix with:', 'AMD', 'SMH'); ok('a guardrail row opens to its fix and links to the trades that resolve it');

    // ── Fixes: by trade
    await click(m.container, /^Fixes/, '[role="tab"]'); t = m.text();
    has(t, '3 trades to fix, clearing 4 flagged guardrails', '7', 'each trade appears once'); ok('Fixes summarises: 3 trades clear 4 guardrails (the guardrail-first layout would show 7 cards)');
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

    // ── Stress
    await click(m.container, /^Stress/, '[role="tab"]'); t = m.text();
    has(t, 'Stress scenarios', '−$9,500', '−$38,000', '−5.2% of capital', 'melt-up', 'Where a −20% month hits', 'Vol sensitivity', '+10%', '−$21,000', 'Assignment lab');
    ok('Stress shows the scenario tiles, the per-name waterfall, the vol grid and the assignment lab');
    lacks(t, 'If every naked short is assigned'); await click(m.container, /^Assignment lab/); has(m.text(), 'If every naked short is assigned: $93,000', '$23,000', 'Put assignment'); ok('the assignment lab opens on demand with the all-naked-assigned total');

    // ── Concentration
    await click(m.container, /^Concentration/, '[role="tab"]'); t = m.text();
    has(t, 'By underlying', 'AMD', '38% of book Γ', 'One name carries 38% of book gamma', 'AMD · SMH · MU', 'move together ρ 0.82', 'Capital by sector', 'Semiconductors', 'S&P 500 +0.74');
    ok('Concentration shows names, correlated clusters, sectors and macro loadings (all measured, none invented)');

    // ── Hedge
    await click(m.container, /^Hedge/, '[role="tab"]'); t = m.text();
    has(t, 'Start here', 'SPX 5200/5000 put spread', '2× SPX 5200/5000', '$5,400/yr', 'Stage SPX trade', 'VIX black-swan', 'VIX 30/45 call spread', 'bigger tail is the upside');
    ok('Hedge leads with the recommended hedge, then the menu, and flags that the bigger tail is the melt-up');
    assert(!log.some(l => l.includes('/book-hedge-advice')), 'still no LLM call'); ok('the AI hedge plan has still not run');
    await click(m.container, /Get AI hedge plan/); await settle(150);
    has(m.text(), 'Trim AMD and keep the SPX spread.', 'Regenerate'); assert(log.filter(l => l.includes('/book-hedge-advice')).length === 1); ok('the AI hedge plan runs only on click, once, and renders');

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
    has(m.text(), 'Analyze book', 'Guardrails · stress tests · concentration'); lacks(m.text(), 'At risk'); ok('with no stored snapshot the bar says "Analyze book" and shows no stale numbers');
    await click(m.container, /Analyze book/);
    has(m.text(), 'Repricing the book'); ok('analysis shows a progress message while it runs');
    await settle(200); has(m.text(), 'At risk', 'Guardrails', 'Overview'); ok('the live result opens the panel on Overview');
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
