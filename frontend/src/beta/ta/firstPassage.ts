/**
 * firstPassage.ts — honest odds for a stop/target trade plan (Beta Technical).
 *
 * WHY THIS EXISTS. A trade plan "wins" when price touches T1 BEFORE it touches the stop, at any moment inside the
 * holding window. The classic setup engine scores the plan with terminal probabilities — P(S_T >= T1) and
 * P(S_T <= stop) at one date — which (a) ignore the path (a stop that is hit and then recovered is booked as a
 * non-loss), (b) leave the "neither" outcome valued at zero, and (c) feed Kelly with (1 - p_win) as the loss
 * probability while the EV uses a different loss probability. For a tight stop that overstates the win chance, the
 * EV and the Kelly size by a wide margin (see test_taodds.cjs for the reference numbers).
 *
 * THE MODEL. Log-price follows Brownian motion with the risk-neutral drift (r - sigma^2/2) and constant vol sigma
 * (the same market-implied basis the classic engine states). Entry, stop and target are absorbing/measuring levels:
 *   - exitProbs():   P(exit through the profit barrier first / the loss barrier first / still inside) by horizon,
 *                    from the eigenfunction expansion of the two-sided exit problem (closed form + decaying tail).
 *   - touchProb():   P(a one-sided level is touched by the horizon) — closed form (reflection principle).
 *   - tradeOdds():   a limit entry must FILL first; the post-fill window is whatever is left of the horizon, so the
 *                    fill-time density is integrated against the two-sided exit probability.
 *
 * WHAT THESE ODDS ARE NOT. They are market-implied (risk-neutral). Under that measure every stop/target rule is a
 * fair bet (zero expected P&L apart from carry), so the honest comparison is "odds vs the break-even win rate" —
 * they can show a plan is priced fairly, they cannot manufacture an edge. Any edge has to come from the setup's
 * structure being predictive, which is what the lenses are for.
 *
 * Validated against an independent Crank-Nicolson finite-difference solution (test_taodds.cjs).
 */

export const DEFAULT_RISK_FREE = 0.045;   // same default the classic engine uses (zebra_service.DEFAULT_RISK_FREE)

// ── normal CDF (series for the core, continued fraction for the tails; ~1e-15 absolute) ─────────────────────────
function erfSeries(x: number): number {
  let term = x, sum = x;
  for (let n = 1; n < 80; n++) {
    term *= -x * x / n;
    const add = term / (2 * n + 1);
    sum += add;
    if (Math.abs(add) < 1e-17) break;
  }
  return (2 / Math.sqrt(Math.PI)) * sum;
}
function erfcCF(x: number): number {          // x >= 2.5 : Lentz continued fraction for erfc
  let f = 0;
  for (let k = 60; k >= 1; k--) f = (k / 2) / (x + f);
  return Math.exp(-x * x) / (Math.sqrt(Math.PI) * (x + f));
}
export function normCdf(z: number): number {
  if (!Number.isFinite(z)) return z > 0 ? 1 : 0;
  const x = z / Math.SQRT2;
  const ax = Math.abs(x);
  const erfc = ax < 2.5 ? 1 - erfSeries(ax) : erfcCF(ax);   // erfc(|x|)
  return x >= 0 ? 1 - 0.5 * erfc : 0.5 * erfc;
}

// ── two-sided exit: P(profit barrier first), P(loss barrier first) by horizon ────────────────────────────────────
/** P(top barrier is the first one hit) with no time limit, for unit-variance BM with drift m, start 0, barriers -L / +U. */
function exitTopForever(L: number, U: number, m: number): number {
  const W = L + U;
  if (Math.abs(m) < 1e-9) return L / W;
  if (m > 0) return (1 - Math.exp(-2 * m * L)) / (1 - Math.exp(-2 * m * W));
  return (Math.exp(2 * m * U) * (1 - Math.exp(2 * m * L))) / (1 - Math.exp(2 * m * W));
}

/** The decaying tail of the exit-through-the-top series, summed to convergence. */
function exitTopTail(L: number, U: number, m: number, tau: number): number {
  const W = L + U;
  const nTerms = Math.min(4000, Math.max(24, Math.ceil(4 * W / Math.sqrt(tau)) + 8));
  const pref = Math.exp(m * U) * (Math.PI / (W * W));
  let sum = 0;
  for (let n = 1; n <= nTerms; n++) {
    const lam = (n * n * Math.PI * Math.PI) / (2 * W * W) + 0.5 * m * m;
    const term = ((n % 2 === 1) ? 1 : -1) * n * Math.sin((n * Math.PI * L) / W) * Math.exp(-lam * tau) / lam;
    sum += term;
    if (n > 8 && Math.abs(term) < 1e-18) break;
  }
  return pref * sum;
}

export interface ExitProbs { top: number; bottom: number; inside: number }

/**
 * Two-sided exit by scaled time `tau` = sigma^2 * T (years). Barriers are log-distances from the start:
 * `down` = L below, `up` = U above; `m` = drift / sigma^2 (unit-variance scaling).
 */
export function exitProbs(down: number, up: number, m: number, tau: number): ExitProbs {
  if (!(down > 0) || !(up > 0) || !(tau > 0) || !Number.isFinite(m)) return { top: 0, bottom: 0, inside: 1 };
  const top = Math.min(1, Math.max(0, exitTopForever(down, up, m) - exitTopTail(down, up, m, tau)));
  // bottom barrier = the top barrier of the mirrored process (drift -m, distances swapped)
  const bottom = Math.min(1, Math.max(0, exitTopForever(up, down, -m) - exitTopTail(up, down, -m, tau)));
  const inside = Math.max(0, 1 - top - bottom);
  return { top, bottom, inside };
}

// ── one-sided touch (fills) ──────────────────────────────────────────────────────────────────────────────────────
/**
 * P(a level at log-distance `a` is touched by T). `drift` is the log drift per year TOWARD the level
 * (positive = drifting toward it). Reflection principle with drift.
 */
export function touchProb(a: number, drift: number, sigma: number, years: number): number {
  if (!(a > 0) || !(sigma > 0) || !(years > 0)) return a <= 0 ? 1 : 0;
  const sd = sigma * Math.sqrt(years);
  const p = normCdf((-a + drift * years) / sd) + Math.exp((2 * drift * a) / (sigma * sigma)) * normCdf((-a - drift * years) / sd);
  return Math.min(1, Math.max(0, p));
}

/** First-passage time density of a level at distance `a` with drift `drift` toward it. */
function touchDensity(a: number, drift: number, sigma: number, s: number): number {
  if (!(s > 0)) return 0;
  const v = sigma * sigma;
  return (a / (sigma * Math.sqrt(2 * Math.PI * s * s * s))) * Math.exp(-((a - drift * s) ** 2) / (2 * v * s));
}

// ── the trade plan ───────────────────────────────────────────────────────────────────────────────────────────────
export interface OddsInput {
  direction: 'long' | 'short';
  spot: number; entry: number; stop: number; target: number;
  /** annualised implied vol as a DECIMAL (0.32), or null when unknown */
  iv: number | null;
  /** holding window in calendar days */
  days: number;
  r?: number;
}
export type OddsFail = 'bad-levels' | 'no-iv' | 'no-window';
export interface Odds {
  ok: true;
  days: number; iv: number;
  /** null = the entry is at the market (no wait for a fill) */
  fillProb: number | null;
  /** how the entry fills: wait for a pullback, or wait for a breakout through the entry */
  fillKind: 'market' | 'pullback' | 'breakout';
  /** outcome odds GIVEN the entry fills inside the window (post-fill window = what is left of it) */
  win: number; loss: number; inside: number;
  risk: number; reward: number; rr: number;
  /** win rate at which the plan breaks even (before costs): risk / (risk + reward) */
  breakEven: number;
  /** of the plans that RESOLVE inside the window, the share that reach T1 first: win / (win + loss); null if none resolve */
  resolvedWin: number | null;
  /** resolvedWin - breakEven in percentage points: ~0 = priced fairly, negative = the odds sit below what the R:R needs.
   *  No EV is reported on purpose: under market-implied odds the unresolved trades carry the offsetting expected P&L,
   *  so "win*reward - loss*risk" alone is biased and the full expectation is ~0 by construction. */
  edgePts: number | null;
}
export type OddsResult = Odds | { ok: false; reason: OddsFail };

const MARKET_TOL = 0.0015;      // an entry within 0.15% of spot is "at the market"

export function tradeOdds(inp: OddsInput): OddsResult {
  const { direction, spot, entry, stop, target } = inp;
  const sign = direction === 'long' ? 1 : -1;
  const ok = [spot, entry, stop, target].every(x => Number.isFinite(x) && x > 0);
  const profitSide = sign * (target - entry) > 0, lossSide = sign * (entry - stop) > 0;
  if (!ok || !profitSide || !lossSide) return { ok: false, reason: 'bad-levels' };
  if (!(inp.iv != null && inp.iv > 0 && Number.isFinite(inp.iv))) return { ok: false, reason: 'no-iv' };
  if (!(inp.days > 0)) return { ok: false, reason: 'no-window' };

  const sigma = inp.iv, r = inp.r ?? DEFAULT_RISK_FREE;
  const T = inp.days / 365;
  const nu = (r - 0.5 * sigma * sigma) * sign;          // log drift along the PROFIT axis (flipped for shorts)
  const m = nu / (sigma * sigma);
  const up = Math.log(target / entry) * sign, down = Math.log(entry / stop) * sign;   // both > 0 in the profit frame
  const risk = Math.abs(entry - stop), reward = Math.abs(target - entry);

  // distance of the entry from spot along the profit axis: >0 means the entry sits ABOVE spot (needs a breakout),
  // <0 means it sits BELOW spot (needs a pullback)
  const gap = sign * Math.log(entry / spot);
  const tol = Math.log(1 + MARKET_TOL);
  let fillKind: Odds['fillKind'] = 'market';
  let fillProb: number | null = null;
  let win: number, loss: number, inside: number;

  if (Math.abs(gap) <= tol) {
    const e = exitProbs(down, up, m, sigma * sigma * T);
    win = e.top; loss = e.bottom; inside = e.inside;
  } else {
    fillKind = gap < 0 ? 'pullback' : 'breakout';
    const a = Math.abs(gap);
    const toward = fillKind === 'pullback' ? -nu : nu;   // drift toward the fill level
    fillProb = touchProb(a, toward, sigma, T);
    // integrate the fill-time density against the post-fill exit probabilities (composite Simpson)
    const N = 240, h = T / N;
    let w = 0, l = 0, i = 0;
    for (let k = 0; k <= N; k++) {
      const s = k * h;
      const f = k === 0 ? 0 : touchDensity(a, toward, sigma, s);
      const rem = T - s;
      const e = rem > 0 ? exitProbs(down, up, m, sigma * sigma * rem) : { top: 0, bottom: 0, inside: 1 };
      const coef = k === 0 || k === N ? 1 : k % 2 === 1 ? 4 : 2;
      w += coef * f * e.top; l += coef * f * e.bottom; i += coef * f * e.inside;
    }
    w *= h / 3; l *= h / 3; i *= h / 3;
    // renormalise by the quadrature's own fill mass so the three outcomes sum to one given a fill
    const mass = w + l + i;
    if (!(mass > 0)) { win = 0; loss = 0; inside = 1; }
    else { win = w / mass; loss = l / mass; inside = i / mass; }
  }
  const rr = reward / risk;
  const breakEven = risk / (risk + reward);
  const resolvedWin = win + loss > 1e-9 ? win / (win + loss) : null;
  return {
    ok: true, days: inp.days, iv: sigma, fillProb, fillKind, win, loss, inside,
    risk, reward, rr, breakEven, resolvedWin,
    edgePts: resolvedWin == null ? null : (resolvedWin - breakEven) * 100,
  };
}

/** The classic engine's number, reproduced for the side-by-side in the tests and the Beta's "why it differs" note. */
export function classicTerminalEdge(spot: number, entry: number, stop: number, t1: number, iv: number, days: number, r = DEFAULT_RISK_FREE) {
  const t = Math.max(days, 1) / 365;
  const pAbove = (lvl: number) => normCdf((Math.log(spot / lvl) + (r - 0.5 * iv * iv) * t) / (iv * Math.sqrt(t)));
  const pWin = pAbove(t1), pLose = 1 - pAbove(stop);
  const risk = Math.abs(entry - stop), reward = Math.abs(t1 - entry), b = reward / risk;
  return { pWin, pLose, ev: pWin * reward - pLose * risk, kelly: pWin - (1 - pWin) / b };
}
