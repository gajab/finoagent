const path = require('path');
const M = require(path.join(process.env.BUILD_DIR, 'betaEvidenceModel.cjs'));
const D = require(path.join(process.env.BUILD_DIR, 'betaDesk.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('betaEvidenceModel + betaDesk');

// ── formatting ───────────────────────────────────────────────────────────────
ok('fmtBig scales T/B/M/K, keeps the sign, and shows a dash for missing', () => {
  assert.equal(M.fmtBig(1060161912832), '$1.06T');
  assert.equal(M.fmtBig(8841499648), '$8.84B');
  assert.equal(M.fmtBig(361285000), '$361.3M');
  assert.equal(M.fmtBig(361285), '$361K');
  assert.equal(M.fmtBig(-2.5e9), '−$2.50B');
  assert.equal(M.fmtBig(null), '–');
  assert.equal(M.fmtBig('abc'), '–');
  assert.equal(M.fmtCount(361285), '361K');
});
ok('fractions vs percents are not confused (yfinance margins are fractions, dividend yield is already a percent)', () => {
  assert.equal(M.fromFrac(0.5572), '55.7%');
  assert.equal(M.fromFrac(1.173, 0), '117%');                 // NOK payout ratio 1.173 → 117%
  assert.equal(M.fromFrac(-0.979, 0, true), '−98%');
  assert.equal(M.fmtPct(1.6, 2), '1.60%');                   // NOK dividend yield is 1.6 (percent), NOT 160%
  assert.equal(M.fmtPct(0.5, 1, true), '+0.5%');
  assert.equal(M.fromFrac(null), '–');
  assert.equal(M.fmtSigned(0), '0.0');
  assert.equal(M.fmtSigned(-3.456, 2), '−3.46');
  assert.equal(M.fmtSigned(undefined), '–');
});
ok('num() refuses booleans, blanks and NaN so a flag is never shown as 1', () => {
  assert.equal(M.num(true), null); assert.equal(M.num(''), null); assert.equal(M.num('x'), null); assert.equal(M.num(null), null);
  assert.equal(M.num('12.5'), 12.5); assert.equal(M.num(0), 0);
});
ok('pos() places a value in its range, clamps, and refuses a degenerate range', () => {
  assert.equal(M.pos(649.42, 188.22, 658.52), (649.42 - 188.22) / (658.52 - 188.22) * 100);
  assert.equal(M.pos(700, 0, 100), 100); assert.equal(M.pos(-5, 0, 100), 0);
  assert.equal(M.pos(5, 10, 10), null); assert.equal(M.pos(null, 0, 1), null);
});

// ── dates & links ────────────────────────────────────────────────────────────
ok('relTime: minutes / hours / days / calendar date, and raw text when unparseable', () => {
  const now = Date.parse('2026-10-06T12:00:00Z');
  assert.equal(M.relTime('Tue, 06 Oct 2026 11:30:00 GMT', now), '30m ago');
  assert.equal(M.relTime('Tue, 06 Oct 2026 09:00:00 GMT', now), '3h ago');
  assert.equal(M.relTime('Sun, 04 Oct 2026 00:42:00 GMT', now), '2d ago');
  assert.match(M.relTime('Wed, 09 Sep 2026 07:00:00 GMT', now), /^Sep \d+$/);
  assert.equal(M.relTime('not a date', now), 'not a date');
  assert.equal(M.relTime('', now), '');
  assert.match(M.relTime('Fri, 09 Oct 2026 07:00:00 GMT', now), /^Oct \d+$/);   // a future timestamp never says "-3h ago"
});
ok('daysUntil counts calendar days in the viewer\'s own date (no off-by-one across time zones)', () => {
  assert.equal(M.daysUntil('2026-11-03', new Date(2026, 9, 7, 23, 59)), 27);
  assert.equal(M.daysUntil('2026-11-03', new Date(2026, 9, 7, 0, 1)), 27);
  assert.equal(M.daysUntil('2026-10-07', new Date(2026, 9, 7, 12)), 0);
  assert.equal(M.daysUntil('2026-10-01', new Date(2026, 9, 7, 12)), -6);
  assert.equal(M.daysUntil('soon'), null); assert.equal(M.daysUntil(null), null);
});
ok('safeUrl only ever follows http(s) — RSS links can not smuggle javascript:', () => {
  assert.equal(M.safeUrl('https://news.google.com/rss/articles/abc'), 'https://news.google.com/rss/articles/abc');
  assert.equal(M.safeUrl('http://example.com/a'), 'http://example.com/a');
  assert.equal(M.safeUrl('javascript:alert(1)'), null);
  assert.equal(M.safeUrl('data:text/html,<script>1</script>'), null);
  assert.equal(M.safeUrl('ftp://x.y/z'), null);
  assert.equal(M.safeUrl('//evil.com'), null); assert.equal(M.safeUrl(undefined), null); assert.equal(M.safeUrl(42), null);
});

// ── technical reads ──────────────────────────────────────────────────────────
ok('RSI words at the textbook boundaries (the AMD sample is overbought at 72.2)', () => {
  assert.deepEqual(M.rsiRead(72.2), { word: 'Overbought', tone: 'warn' });
  assert.equal(M.rsiRead(70).word, 'Overbought'); assert.equal(M.rsiRead(69.9).word, 'Bullish momentum');
  assert.equal(M.rsiRead(55).word, 'Bullish momentum'); assert.equal(M.rsiRead(50).word, 'Neutral');
  assert.equal(M.rsiRead(45).word, 'Bearish momentum'); assert.equal(M.rsiRead(30.1).word, 'Bearish momentum');
  assert.equal(M.rsiRead(30).word, 'Oversold'); assert.equal(M.rsiRead(null).word, '–');
});
ok('stochastic / MFI use 20/80', () => {
  assert.equal(M.band2080Read(91.8).word, 'Overbought'); assert.equal(M.band2080Read(80).word, 'Overbought');
  assert.equal(M.band2080Read(20).word, 'Oversold'); assert.equal(M.band2080Read(50).word, 'Mid-range');
});
ok('ADX: needs ≥25 to call a trend, and names the side from +DI/−DI', () => {
  assert.deepEqual(M.adxRead(26.9, 40.5, 18.3), { word: 'Strong uptrend', tone: 'good' });
  assert.deepEqual(M.adxRead(45, 10, 30), { word: 'Very strong downtrend', tone: 'bad' });
  assert.equal(M.adxRead(18, 30, 10).word, 'No clear trend');
  assert.deepEqual(M.adxRead(22, 30, 10), { word: 'Trend building', tone: 'info' });
  assert.deepEqual(M.adxRead(30, null, null), { word: 'Strong trend', tone: 'info' });
});
ok('MACD: histogram sign × 3-day slope (AMD: positive but falling = "Bullish but fading")', () => {
  assert.deepEqual(M.macdRead(3.564, -1.243), { word: 'Bullish but fading', tone: 'warn' });
  assert.deepEqual(M.macdRead(2, 1), { word: 'Bullish', tone: 'good' });
  assert.deepEqual(M.macdRead(2, null), { word: 'Bullish', tone: 'good' });
  assert.deepEqual(M.macdRead(-1, -0.5), { word: 'Bearish', tone: 'bad' });
  assert.deepEqual(M.macdRead(-1, 0.3), { word: 'Bearish but easing', tone: 'warn' });
  assert.equal(M.macdRead(0, 1).word, 'Flat'); assert.equal(M.macdRead(null, 1).word, '–');
});
ok('Bollinger %B, CMF, up/down volume, percentile, Weinstein stage', () => {
  assert.equal(M.bbRead(0.82).word, 'Near the upper band'); assert.equal(M.bbRead(1.2).word, 'Above the upper band');
  assert.equal(M.bbRead(-0.1).word, 'Below the lower band'); assert.equal(M.bbRead(0.1).word, 'Near the lower band'); assert.equal(M.bbRead(0.5).word, 'Mid-band');
  assert.equal(M.cmfRead(0.327).word, 'Accumulation'); assert.equal(M.cmfRead(-0.2).word, 'Distribution'); assert.equal(M.cmfRead(0.05).word, 'Balanced');
  assert.equal(M.upDownRead(3.06).word, 'Buyers dominate'); assert.equal(M.upDownRead(0.5).word, 'Sellers dominate'); assert.equal(M.upDownRead(1).word, 'Balanced');
  assert.equal(M.percentileRead(67).word, 'Typical'); assert.equal(M.percentileRead(85).word, 'High vs the past year'); assert.equal(M.percentileRead(10).word, 'Low vs the past year');
  assert.deepEqual(M.stageRead(2), { word: 'Stage 2 · advancing', tone: 'good' }); assert.equal(M.stageRead(4).tone, 'bad'); assert.equal(M.stageRead(7).word, '–');
  assert.equal(M.trendTone('up'), 'good'); assert.equal(M.trendTone('down'), 'bad'); assert.equal(M.trendTone(undefined), 'neutral');
});
ok('scoreRead: one vocabulary, and a lens clearly above neutral is never called "Neutral" (LITE technical 64.1)', () => {
  assert.equal(M.scoreRead(65).word, 'Supportive'); assert.deepEqual(M.scoreRead(64.1), { word: 'Leaning supportive', tone: 'good' });
  assert.equal(M.scoreRead(55).word, 'Leaning supportive'); assert.equal(M.scoreRead(54.9).word, 'Neutral'); assert.equal(M.scoreRead(45).word, 'Neutral');
  assert.equal(M.scoreRead(44.9).word, 'Caution'); assert.equal(M.scoreRead(30.5).word, 'Caution'); assert.equal(M.scoreRead(29.9).word, 'Against'); assert.equal(M.scoreRead(null).word, 'Not scored');
});
ok('pctFrom is signed to the price', () => {
  assert.ok(Math.abs(M.pctFrom(649.42, 572.29) - 13.48) < 0.01);
  assert.ok(M.pctFrom(500, 572.29) < 0); assert.equal(M.pctFrom(1, 0), null); assert.equal(M.pctFrom(null, 5), null);
});
ok('divergingBar: fill starts at the centre, grows left for bearish / right for bullish, clamps to ±1', () => {
  assert.deepEqual(M.divergingBar(0.5), { left: 50, width: 25, tone: 'good' });
  assert.deepEqual(M.divergingBar(-0.5), { left: 25, width: 25, tone: 'bad' });
  assert.equal(M.divergingBar(0.1).tone, 'neutral'); assert.equal(M.divergingBar(3).width, 50); assert.equal(M.divergingBar(-9).left, 0);
  assert.equal(M.divergingBar(null).width, 0);
});
ok('consensusSplit sums to 100 and is null with no traders', () => {
  const c = M.consensusSplit({ bull: 11, neutral: 3, bear: 1 });
  assert.equal(c.n, 15); assert.ok(Math.abs(c.bull + c.neutral + c.bear - 100) < 1e-9); assert.ok(Math.abs(c.bull - 73.3333) < 1e-3);
  assert.equal(M.consensusSplit({}), null); assert.equal(M.consensusSplit(null), null);
});
ok('zoneLadder: highest first, split at the spot, distance from the engine when given else computed', () => {
  const z = [{ center: 518.39, kind: 'support', distance_pct: -20.19, n_sources: 5, sources: [{ label: 'POC', price: 505 }] },
    { center: 700, kind: 'resistance', n_sources: 2, sources: [{ label: 'Call wall', price: 700 }] }, { center: 640, kind: 'support' }];
  const l = M.zoneLadder(z, 649.42);
  assert.deepEqual(l.above.map(r => r.center), [700]); assert.deepEqual(l.below.map(r => r.center), [640, 518.39]);
  assert.equal(l.below[1].distancePct, -20.19);
  assert.ok(Math.abs(l.above[0].distancePct - 7.79) < 0.01);
  assert.equal(l.below[0].nSources, 0);
  const noSpot = M.zoneLadder(z, null); assert.equal(noSpot.above.length, 3); assert.equal(noSpot.below.length, 0);
  assert.deepEqual(M.zoneLadder(undefined, 5), { above: [], below: [] });
  assert.equal(M.zoneLadder([{ center: 'x' }], 5).above.length, 0);
});
ok('edgeSample flags small samples (the AMD signals have n = 8–14)', () => {
  assert.equal(M.edgeSample({ n: 14 }).small, true); assert.equal(M.edgeSample({ n: 36 }).small, false);
  assert.equal(M.edgeSample({ n: 90, low_confidence: true }).small, true); assert.equal(M.edgeSample(null).small, true);
  assert.equal(M.EDGE_TONE.weak, 'warn');
});

// ── fundamentals ─────────────────────────────────────────────────────────────
const AMD_PILLARS = { fundamental: { score: 0 }, valuation: { score: 57 }, sentiment_targets: { score: 0 }, catalyst_revisions: { score: 10 }, quality_capital: { score: 17 },
  ownership_flow: { score: 25 }, structural: { score: 25 }, macro_sensitivity: { score: 68 }, geopolitical: { score: 33 }, sector_rotation: { score: 0 } };
ok('pressure words use the classic cut-offs (≥45 pressure, ≥32 watch) — lower is healthier', () => {
  assert.deepEqual(M.pressureRead(57), { word: 'Pressure', tone: 'bad' }); assert.equal(M.pressureRead(45).word, 'Pressure');
  assert.equal(M.pressureRead(44.9).word, 'Watch'); assert.equal(M.pressureRead(32).word, 'Watch'); assert.equal(M.pressureRead(31.9).word, 'Healthy');
  assert.equal(M.pressureRead(0).word, 'Healthy'); assert.equal(M.pressureRead(null).word, 'Not scored');
});
ok('pillarTally on the real AMD scorecard: 7 healthy · 1 watch · 2 pressure', () => {
  assert.deepEqual(M.pillarTally(AMD_PILLARS), { healthy: 7, watch: 1, pressure: 2, scored: 10 });
  assert.deepEqual(M.pillarTally({ a: { score: null } }), { healthy: 0, watch: 0, pressure: 0, scored: 0 });
  assert.deepEqual(M.pillarTally(undefined), { healthy: 0, watch: 0, pressure: 0, scored: 0 });
});
ok('pillarFacts picks the spec facts that have data, in order, with units (valuation sample)', () => {
  const data = { forward_pe: 41.3, peg_ratio: 0.64, ev_to_ebitda: 106.9, price_to_book: 15.77, target_upside_pct: -2, price_vs_52w_pct: 98.1 };
  assert.deepEqual(M.pillarFacts('valuation', data, 4), [
    { label: 'Forward P/E', value: '41.3×' }, { label: 'PEG', value: '0.64×' }, { label: 'EV/EBITDA', value: '107×' }, { label: 'P/B', value: '15.8×' }]);
  const six = M.pillarFacts('valuation', data, 6); assert.deepEqual(six[4], { label: 'To analyst target', value: '−2.0%' });
  assert.deepEqual(M.pillarFacts('valuation', { forward_pe: null, peg_ratio: 0.5 }), [{ label: 'PEG', value: '0.50×' }]);
  assert.deepEqual(M.pillarFacts('nope', data), []); assert.deepEqual(M.pillarFacts('valuation', null), []);
  assert.deepEqual(M.pillarFacts('sentiment_targets', { above_50dma: true, above_200dma: false }).map(f => f.value), ['yes', 'no']);
  assert.deepEqual(M.pillarFacts('fundamental', { revenue_direction: 'improving', fcf_direction: 'declining_slowly' }).map(f => f.value), ['improving', 'declining slowly']);
});
ok('every pillar the backend emits has a label, a blurb and at least one fact', () => {
  for (const k of Object.keys(AMD_PILLARS)) { const p = M.PILLARS[k]; assert.ok(p && p.label && p.blurb && p.facts.length > 0, k); }
});
ok('fmtFact formats by kind and never prints "undefined"', () => {
  assert.equal(M.fmtFact(null, 'x'), '–'); assert.equal(M.fmtFact('', 'pct'), '–'); assert.equal(M.fmtFact(2.609, 'x'), '2.61×'); assert.equal(M.fmtFact(165.67, 'x'), '166×');
  assert.equal(M.fmtFact(0.0248, 'frac'), '2.5%'); assert.equal(M.fmtFact(80, 'int'), '80'); assert.equal(M.fmtFact(true, 'yn'), 'yes'); assert.equal(M.fmtFact(1e9, 'usd'), '$1.00B');
});
ok('targetUpside: mean target vs price (AMD sits ~2% ABOVE its average target)', () => {
  assert.ok(Math.abs(M.targetUpside({ current: 649.42, mean: 636.208 }) - (-2.0344)) < 0.01);
  assert.equal(M.targetUpside({ current: 0, mean: 5 }), null); assert.equal(M.targetUpside({}), null); assert.equal(M.targetUpside(null), null);
});
ok('consensus word/tone from the 1–5 rating', () => {
  assert.deepEqual(M.consensusRead(1.49, 'strong_buy'), { word: 'Strong buy', tone: 'good' });
  assert.equal(M.consensusRead(2.9, 'hold').tone, 'neutral'); assert.equal(M.consensusRead(4.2, 'underperform').tone, 'bad');
  assert.equal(M.consensusRead(null, undefined).word, '–'); assert.equal(M.consensusRead(null, 'buy').tone, 'neutral');
  assert.equal(M.PERIOD_LABEL['+1y'], 'Next year');
});

// ── news & events ────────────────────────────────────────────────────────────
ok('newsRows: newest first, undated last, links sanitised, risk/positive flags matched by exact title, peer tag kept', () => {
  const now = Date.parse('2026-10-06T12:00:00Z');
  const rows = M.newsRows([
    { title: 'Old', publisher: 'A', date: 'Mon, 28 Sep 2026 07:00:00 GMT', link: 'https://x.test/1' },
    { title: 'Undated', publisher: 'B' },
    { title: 'New risk', publisher: 'C', date: 'Tue, 06 Oct 2026 10:00:00 GMT', link: 'javascript:alert(1)' },
    { title: 'New good', publisher: 'D', date: 'Tue, 06 Oct 2026 09:00:00 GMT', link: 'https://x.test/4', peer: 'NBIS' },
    { title: '', publisher: 'skipped' }, null,
  ], { risk_headlines: ['New risk'], positive_headlines: ['New good'] }, now);
  assert.deepEqual(rows.map(r => r.title), ['New risk', 'New good', 'Old', 'Undated']);
  assert.equal(rows[0].link, null); assert.equal(rows[0].flag, 'risk'); assert.equal(rows[0].when, '2h ago');
  assert.equal(rows[1].flag, 'positive'); assert.equal(rows[1].tag, 'NBIS'); assert.equal(rows[2].flag, undefined); assert.equal(rows[3].ts, null);
  assert.deepEqual(M.newsRows(undefined), []);
});
ok('earningsPlacement: before expiry = the trade owns the gap; after = safe; shares = held through it', () => {
  const inside = M.earningsPlacement(5, 9); assert.equal(inside.when, 'inside'); assert.equal(inside.tone, 'warn'); assert.match(inside.text, /INSIDE your 9-day trade/);
  assert.equal(M.earningsPlacement(9, 9).when, 'inside');                 // the print on expiry day still hits the position
  const after = M.earningsPlacement(27, 9); assert.equal(after.when, 'after'); assert.equal(after.tone, 'good'); assert.match(after.text, /after your 9-day expiry/);
  assert.equal(M.earningsPlacement(15, null).when, 'shares'); assert.equal(M.earningsPlacement(3, null).tone, 'warn'); assert.equal(M.earningsPlacement(15, null).tone, 'neutral');
  assert.equal(M.earningsPlacement(null, 9).when, 'none'); assert.equal(M.earningsPlacement(-2, 9).when, 'none');
  assert.match(M.earningsPlacement(1, null).text, /1 day away/);          // singular
});
ok('peerTable: the stock first (flagged), then peers, then the median', () => {
  const t = M.peerTable({ self_21d_pct: 37.1, self_63d_pct: 26.5, median_21d_pct: 7.2, median_63d_pct: 13.4, rows: [{ ticker: 'NBIS', ret_21d_pct: 10.8, ret_63d_pct: 15.9 }] }, 'AMD');
  assert.deepEqual(t.rows[0], { ticker: 'AMD', r21: 37.1, r63: 26.5, self: true }); assert.equal(t.rows[1].ticker, 'NBIS'); assert.deepEqual(t.median, { r21: 7.2, r63: 13.4 });
  assert.equal(M.peerTable(null, 'X'), null); assert.equal(M.peerTable({}, 'X'), null);
});
ok('tape: every symbol the backend sends has a slot; rate/vol/dollar moves are not coloured good/bad', () => {
  for (const k of ['GLD', 'HYG', 'IWM', 'QQQ', 'SPY', 'USO', 'UUP', '^TNX', '^VIX']) assert.ok(M.TAPE_ORDER.includes(k), k);
  assert.ok(M.TAPE_DIRECTIONAL.has('SPY')); assert.ok(!M.TAPE_DIRECTIONAL.has('^VIX')); assert.ok(!M.TAPE_DIRECTIONAL.has('^TNX')); assert.ok(!M.TAPE_DIRECTIONAL.has('UUP'));
});


// ── how a lens score is built — pinned to the real LITE read (technical 64.1, position risk 44) ──────────
const LITE_SIGNALS = [{ d: 0.651, w: 4 }, { d: 0.7, w: 2 }, { d: 0.6, w: 1.5 }, { d: 0, w: 1.5 }, { d: 0.22, w: 1 }, { d: 1, w: 1 }];
const LITE_BUILD = { kind: 'alignment', dir: 0.557, aligned: 0.557, k: 1.15, gain: 22, base: 50, points: 14.1, lo: 28, hi: 72, weight_total: 11, adjust: 0 };
ok('weightedAverage reproduces the backend\'s +0.557 for LITE, and a zero vote visibly dilutes it', () => {
  assert.ok(Math.abs(M.weightedAverage(LITE_SIGNALS) - 0.5567) < 1e-3);
  assert.ok(Math.abs(M.weightedAverage(LITE_SIGNALS.filter(s => s.d !== 0)) - 0.6442) < 1e-3);          // without the "no divergence" vote
  assert.equal(M.weightedAverage([]), null); assert.equal(M.weightedAverage(undefined), null); assert.equal(M.weightedAverage([{ d: 1, w: 0 }]), null);
});
ok('signalShares: each signal\'s share of the vote adds to 100 (the no-divergence row carries 14%)', () => {
  const sh = M.signalShares(LITE_SIGNALS);
  assert.ok(Math.abs(sh.reduce((a, b) => a + b, 0) - 100) < 1e-9); assert.ok(Math.abs(sh[3] - 13.636) < 0.01); assert.deepEqual(M.signalShares(undefined), []);
});
ok('alignmentView writes the real sum: 50 + 22 × 0.64 = 64.1, 82% of the way up a 28–72 range', () => {
  const v = M.alignmentView(LITE_BUILD, 64.1);
  assert.equal(v.line, '50 + 22 × 0.64 = 64.1'); assert.ok(Math.abs(v.reachPct - 82.045) < 0.01);
  assert.equal(v.ceilingNote, "64 is 82% of the way up this lens's 28–72 range");
  assert.ok(Math.abs(v.scaled - 0.6405) < 1e-3);
  // the sum on the line reproduces the score
  assert.ok(Math.abs(LITE_BUILD.base + LITE_BUILD.gain * v.scaled - 64.1) < 0.1);
});
ok('alignmentView: bearish alignment subtracts, clamps at ±1, and carries the option adjustment', () => {
  assert.equal(M.alignmentView({ ...LITE_BUILD, aligned: -0.3, dir: -0.3, points: -7.6, lo: 28, hi: 72 }, 42.4).line, '50 − 22 × 0.35 = 42.4');
  assert.equal(M.alignmentView({ ...LITE_BUILD, aligned: 0.95, dir: 0.95, points: 22 }, 72).scaled, 1);
  assert.equal(M.alignmentView({ ...LITE_BUILD, aligned: 0.3, dir: 0.3, points: 7.6, adjust: -12 }, 45.6).line, '50 + 22 × 0.35 − 12.0 = 45.6');
  assert.equal(M.alignmentView({ ...LITE_BUILD, gain: 11, lo: 39, hi: 61 }, 55).ceilingNote, "55 is 73% of the way up this lens's 39–61 range");
});
ok('alignmentView refuses anything that is not a complete alignment build (older reads have none)', () => {
  assert.equal(M.alignmentView(undefined, 60), null); assert.equal(M.alignmentView(null, 60), null);
  assert.equal(M.alignmentView({ kind: 'position_risk' }, 60), null); assert.equal(M.alignmentView({ ...LITE_BUILD, k: null }, 60), null); assert.equal(M.alignmentView(LITE_BUILD, null), null);
});
const LITE_RISK = { kind: 'position_risk', base: 62, clamped: false, terms: [{ label: 'Stop distance', pts: -18, note: '55% chance…' }],
  stop: { cost: 1083.3, level: 975.01, pct: 10, crossed: false, z: 0.6, p_touch: 0.55, sigma21_pct: 25.2 } };
ok('riskReceipt: 62 − 18 = 44 for LITE; a clean position has no terms; wrong kind → null', () => {
  const r = M.riskReceipt(LITE_RISK, 44); assert.equal(r.sum, 44); assert.equal(r.total, 44); assert.equal(r.terms.length, 1);
  assert.equal(M.riskReceipt({ ...LITE_RISK, terms: [] }, 62).sum, 62);
  assert.equal(M.riskReceipt({ ...LITE_RISK, terms: [{ label: 'x', pts: null, note: '' }, { label: 'Beta', pts: -3, note: 'n' }] }, 59).sum, 59);
  assert.equal(M.riskReceipt({ kind: 'alignment' }, 1), null); assert.equal(M.riskReceipt(undefined, 1), null);
});
ok('stopMap: stop, cost and now on one axis with the 1σ month; marks stay inside 0–100; the stop falls inside LITE\'s normal month', () => {
  const m = M.stopMap(LITE_RISK.stop, 1133.4, 1136.9);
  assert.deepEqual(m.marks.map(x => x.key), ['stop', 'cost', 'spot']);                   // the 52-week high is within 1.5% of the price: no 4th mark
  assert.ok(m.marks.every(x => x.pct >= 0 && x.pct <= 100) && m.marks[0].pct < m.marks[1].pct && m.marks[1].pct < m.marks[2].pct);
  assert.ok(Math.abs(m.band.lo - 847.8) < 0.5 && Math.abs(m.band.hi - 1419.0) < 0.5); assert.ok(m.band.lo < 975.01);
  assert.ok(Math.abs(m.marks[0].fromSpot - (-13.97)) < 0.05); assert.equal(m.marks[2].fromSpot, 0); assert.equal(m.marks[0].label, 'Stop −10%');
  const far = M.stopMap(LITE_RISK.stop, 1133.4, 1300); assert.deepEqual(far.marks.map(x => x.key), ['stop', 'cost', 'spot', 'high']);
  assert.equal(M.stopMap({ ...LITE_RISK.stop, crossed: true }, 900).marks.find(x => x.key === 'stop').label, 'Stop (breached)');
  assert.equal(M.stopMap({ ...LITE_RISK.stop, sigma21_pct: null }, 1133.4).band, null);
  assert.equal(M.stopMap(null, 5), null); assert.equal(M.stopMap(LITE_RISK.stop, null), null);
});
ok('metricText formats each backend fmt without printing "undefined"', () => {
  assert.equal(M.metricText({ value: 87.2, fmt: 'pct' }), '87%'); assert.equal(M.metricText({ value: 0.3, fmt: 'pct' }), '0.3%'); assert.equal(M.metricText({ value: 55, fmt: 'pct' }), '55%');
  assert.equal(M.metricText({ value: 438, fmt: 'pct_signed' }), '+438%'); assert.equal(M.metricText({ value: -12, fmt: 'pct_signed' }), '−12%');
  assert.equal(M.metricText({ value: 250, fmt: 'usd' }), '$250'); assert.equal(M.metricText({ value: -1949.5, fmt: 'usd' }), '−$1,950');
  assert.equal(M.metricText({ value: 1.54, fmt: 'num' }), '1.54'); assert.equal(M.metricText({ value: 1.3, fmt: 'mult' }), '1.30×'); assert.equal(M.metricText({ value: 0.5, fmt: 'ratio' }), '0.50 : 1');
  assert.equal(M.metricText({ value: 'breached', fmt: 'text' }), 'breached'); assert.equal(M.metricText({ value: null, fmt: 'pct' }), '–'); assert.equal(M.metricText({ value: 7 }), '7.00');
});

// ── desk hand-off ────────────────────────────────────────────────────────────
ok('deskForManager passes only what the backend reads, and nothing for an unusable desk result', () => {
  const full = { matched: true, signal: 'HOLD', lifecycle_score: 53, overrides: ['x'], management_analysis: { anchor: 50, contributions: [] }, opp: { huge: 'candidate' }, qp: {}, spot: 1 };
  assert.deepEqual(D.deskForManager(full), { signal: 'HOLD', lifecycle_score: 53, overrides: ['x'], management_analysis: { anchor: 50, contributions: [] } });
  assert.deepEqual(D.deskForManager({ matched: true, signal: 'CLOSE', lifecycle_score: 0 }), { signal: 'CLOSE', lifecycle_score: 0, overrides: [], management_analysis: null });   // 0 is a real score
  assert.equal(D.deskForManager({ matched: true, signal: 'HOLD' }), undefined);
  assert.equal(D.deskForManager({ matched: true, lifecycle_score: 40 }), undefined);
  assert.equal(D.deskForManager(null), undefined); assert.equal(D.deskForManager(undefined), undefined);
});
ok('deskSettled: waiting only while the run is idle or loading', () => {
  assert.equal(D.deskSettled('idle'), false); assert.equal(D.deskSettled('loading'), false);
  for (const s of ['ready', 'error', 'na']) assert.equal(D.deskSettled(s), true);
  assert.equal(D.deskSettled(undefined), true);                       // no desk (shares / classic caller) never blocks the read
});

console.log(`  ${n} checks passed`);
