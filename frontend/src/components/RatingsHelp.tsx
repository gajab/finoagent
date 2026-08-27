import React, { useState } from 'react';
import { HelpCircle, X, Award, Target, Gauge, Zap, Activity, Sparkles } from 'lucide-react';

// One place that explains every number the income / desk-review pages show, so the
// Grade / Win% / Execution triad (and the chips + boundaries beneath them) stop looking
// like competing scores. Opened by <RatingsHelpButton/>.

function Term({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(88px,132px)_1fr] gap-3 py-1.5 border-t border-white/[0.05]">
      <span className="font-mono text-[11px] text-base-content/80 pt-px">{name}</span>
      <span className="text-[12px] text-base-content/65 leading-snug">{children}</span>
    </div>
  );
}

function HeadNum({ icon, tone, title, q, children }: {
  icon: React.ReactNode; tone: string; title: string; q: string; children: React.ReactNode;
}) {
  return (
    <div className={`rounded-lg border p-2.5 ${tone}`}>
      <p className="flex items-center gap-1.5 text-sm font-bold">{icon}{title}</p>
      <p className="text-[11px] italic text-base-content/50 mt-0.5">{q}</p>
      <p className="text-[12px] text-base-content/70 leading-snug mt-1">{children}</p>
    </div>
  );
}

// A "chip / boundary" explainer row: icon + what it looks like on screen + what it means.
function Chip({ icon, title, children }: { icon: React.ReactNode; title: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex gap-2.5 py-2 border-t border-white/[0.05]">
      <span className="shrink-0 mt-0.5">{icon}</span>
      <div>
        <p className="text-[12px] font-semibold text-base-content/85">{title}</p>
        <p className="text-[12px] text-base-content/65 leading-snug mt-0.5">{children}</p>
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <p className="text-[11px] uppercase tracking-wide text-base-content/40 mb-1.5">{children}</p>;
}

export function RatingsHelpButton({ className = '' }: { className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={`inline-flex items-center gap-1 text-[11px] text-base-content/50 hover:text-base-content/80 transition-colors ${className}`}
        title="How these ratings work"
      >
        <HelpCircle className="w-3.5 h-3.5" /> How these ratings work
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 sm:p-8"
          onClick={() => setOpen(false)}
        >
          <div
            className="relative w-full max-w-2xl rounded-2xl border border-white/10 bg-base-100 shadow-2xl my-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sticky top-0 z-10 flex items-center justify-between gap-2 rounded-t-2xl border-b border-white/10 bg-base-100/95 px-4 py-3 backdrop-blur">
              <p className="text-sm font-bold flex items-center gap-1.5"><HelpCircle className="w-4 h-4" /> How to read the ratings</p>
              <button className="btn btn-ghost btn-xs px-1" onClick={() => setOpen(false)}><X className="w-4 h-4" /></button>
            </div>

            <div className="px-4 py-3 space-y-4 text-base-content/80">
              {/* The three numbers that matter */}
              <div>
                <SectionLabel>Three reads — three different questions</SectionLabel>
                <p className="text-[12px] text-base-content/60 mb-2">
                  They look alike but measure unrelated things, so a trade can rate high on one and low on another
                  without contradiction. Read them together, not as a single verdict.
                </p>
                <div className="grid gap-2 sm:grid-cols-3">
                  <HeadNum icon={<Award className="w-4 h-4 text-success" />} tone="border-success/25 bg-success/[0.06]"
                    title="Grade" q="Is this a good trade?">
                    Overall quality as a letter, <b>A → F</b>. It is the desk's blended verdict on the trade;
                    A/B = the desk would likely approve, D/F = weak or flawed, <span className="font-mono">V</span> = vetoed
                    (structurally broken). The point build-up behind the letter is in <i>Explore → Quant Analysis</i>.
                  </HeadNum>
                  <HeadNum icon={<Target className="w-4 h-4 text-info" />} tone="border-info/25 bg-info/[0.06]"
                    title="Win %" q="How likely to work out?">
                    The chance the option you sold <b>expires worthless</b> — you keep the full premium and are not
                    assigned. Your <i>min-probability</i> setting is the floor. Shown to one decimal and capped at
                    <b> 99.9%</b> — it is never a certainty.
                  </HeadNum>
                  <HeadNum icon={<Gauge className="w-4 h-4 text-warning" />} tone="border-warning/25 bg-warning/[0.06]"
                    title="Execution" q="Can I trust & fill it?">
                    Pricing reliability (arbitrage-free vol model) and liquidity (bid-ask, open interest).
                    <b> High ≠ a good trade</b> — it means the numbers are trustworthy and the fill is realistic.
                  </HeadNum>
                </div>
              </div>

              {/* What builds the grade */}
              <div>
                <SectionLabel>What goes into the Grade</SectionLabel>
                <p className="text-[12px] text-base-content/65 leading-snug">
                  The grade comes from a <b>desk score</b> built in three layers (you can see each as a signed bar in
                  <i> Explore → Quant Analysis</i>):
                </p>
                <ul className="text-[12px] text-base-content/65 leading-snug mt-1.5 space-y-1">
                  <li>➊ <b>Base quality</b> — a <b>safe-income</b> read off one probability-weighted outcome curve
                    (<span className="font-mono text-[11px]">Edge · Win-prob · Sortino · Tail · Carry</span>), anchored on
                    what matters for premium selling: <b>Safety</b> (keep-probability) and <b>Income</b> (premium yield
                    vs the cash hurdle) <b>dominate</b>; Omega, risk-adjusted return and the honest deep-tail (99% CVaR)
                    are secondary checks. <span className="opacity-70">It rewards a <i>safe</i> trade that beats cash by a
                    bit of alpha — not risk-neutral profit (fairly-priced income has ~0 of that; the seller's vol edge is
                    the <b>VRP</b> factor below).</span></li>
                  <li>➋ <b>Option-math adjustments</b> (± on the base): <b>VRP</b>, <b>Moneyness</b> (how close the short
                    strike sits), <b>Skew / IV-edge</b>, <b>Liquidity</b>, <b>Beta</b>, and <b>Undefined risk</b> — an
                    explicit demerit for <b>unbounded-loss</b> structures (naked calls, short strangles): the base
                    score's tail term uses CVaR<sub>95</sub> and deliberately omits the deep worst case, so this prices
                    the <b>CVaR<sub>99</sub></b> tail, scaled by how <i>reachable</i> the strike is — a deep, low-touch
                    naked call takes a small hit, a near-money one a large one (it can push the grade to F). Exempt when
                    you own the shares (the call is then covered → bounded).</li>
                  <li>➌ <b>TA factors</b> (± on the base) — the technical read on <b>6-month daily</b> bars, the swing
                    horizon that governs a multi-week option (see the chips below).</li>
                </ul>
                <p className="text-[12px] text-base-content/55 leading-snug mt-1.5">
                  <span className="font-mono text-[11px]">base + Σ adjustments + Σ TA</span>, clamped to 0–100 → the letter.
                  Two things sit <i>outside</i> the score, so <b>quality and timing never contradict</b>:
                </p>
                <ul className="text-[12px] text-base-content/65 leading-snug mt-1.5 space-y-1">
                  <li><span className="text-error"><b>VETOED</b></span> — a <b>structurally</b> broken trade (loses in
                    expectation, crushed vol, delta-neutral into short gamma) → grade <b>F</b>, avoid. The quality score is
                    irrelevant here.</li>
                  <li><span className="text-warning"><b>WAIT · timing</b></span> — a <b>good</b> trade at the <b>wrong
                    moment</b>: momentum accelerating against a strike that's <i>actually reachable</i>. It <b>keeps its
                    quality grade</b> but the desk holds until momentum settles — <i>not</i> the same as a veto.</li>
                </ul>
              </div>

              {/* Structure — walls, buffer, sigma */}
              <div>
                <SectionLabel>Structure — the wall behind your strike</SectionLabel>
                <p className="text-[12px] text-base-content/65 leading-snug">
                  A safe-income short strike should sit <b>behind a structural wall</b> — a level price must break
                  before it can reach you. The <b>Structure</b> factor scores exactly this and is <b>always shown</b>
                  (+, 0 or −), so every trade tells you its structural situation.
                </p>
                <ul className="text-[12px] text-base-content/65 leading-snug mt-1.5 space-y-1">
                  <li><b>Walls</b> (any of): pivot <b>support / resistance</b>, the volume-profile <b>value-area</b> edges,
                    the dealer <b>gamma put-wall / call-wall / flip</b>, and the <b>high-volume node</b>.</li>
                  <li><b>Strong</b> walls = dealer gamma walls &amp; high-volume nodes — they hold, so you can sell closer.
                    <b> Standard</b> = pivots, value-area edges, gamma flip.</li>
                  <li><span className="text-success"><b>Defended</b></span> — a wall sits between the strike and spot →
                    <b> +</b> (up to <b>+4</b> strong / <b>+3</b> standard, once the strike clears the buffer past the wall;
                    ~0 if it's sitting right at the wall). A <b>far</b> wall still counts when you sell behind it — there's
                    <b> no reach cap</b>.</li>
                  <li><span className="text-error"><b>Undefended</b></span> — open air, no wall between strike and spot →
                    <b> −</b> (naked premium), <i>unless</i> the strike is <b>≥ 1.5σ from spot</b>, where probability alone
                    defends it (then <b>0</b>). The closer to spot, the bigger the penalty.</li>
                </ul>
                <p className="text-[12px] text-base-content/55 leading-snug mt-1.5">
                  <b>Buffer</b> — how far the strike sits <i>beyond</i> a wall: <b>0.20σ</b> past a strong wall,
                  <b> 0.30σ</b> past a standard one. <b>σ (1σ)</b> is the <b>event-aware expected move to expiry</b> at the
                  wall = <span className="font-mono text-[11px]">price × max(IV,HV) × √(DTE/365)</span> — it scales with
                  volatility and <b>widens before earnings</b> (IV lifts it). Premium is the go/no-go, never a reason to
                  creep closer into open air.
                </p>
                <div className="mt-2 rounded-lg border border-warning/25 bg-warning/[0.05] p-2 text-[11px] text-base-content/70 leading-snug">
                  <b className="text-warning/90">Earnings-aware ranking</b> (opt-in checkbox before you scan) — walls are a
                  <b> continuous-tape</b> defense; an earnings <b>gap jumps through them</b>. When a print falls before expiry it:
                  <b> ➊</b> discounts the <b>Structure</b> credit for walls the isolated event move can leap, and
                  <b> ➋</b> adds an <b>Earnings gap</b> penalty when the strike sits inside <b>~1.5×</b> that move (a real
                  surprise runs 2–3× the implied). The event move is <b>isolated</b> from the straddle (total move minus the
                  baseline diffusion), so it's the single-day jump the diffusion σ smears away. Every impacted metric shows
                  its value <b>with / without</b> the adjustment so you can see exactly what earnings did.
                </div>
              </div>

              {/* Breach risk — the touch probability */}
              <div>
                <SectionLabel>Breach risk — will it ever go ITM?</SectionLabel>
                <p className="text-[12px] text-base-content/65 leading-snug">
                  The worst outcome for premium income is the short going <b>ITM</b>. Note that's a <b>touch</b> event,
                  not just an expiry state: a strike can <i>finish</i> OTM yet spend days ITM. So the desk scores the
                  honest <b>P(touch)</b> — the chance the strike is breached at <b>any</b> point before expiry — which by
                  the reflection principle is <b>≈ 2× the finish-ITM odds</b> (a "90%-keep" strike ≈ 20% breach).
                </p>
                <ul className="text-[12px] text-base-content/65 leading-snug mt-1.5 space-y-1">
                  <li><b>Breach risk</b> — P(touch), computed <b>drift-aware</b> (a stock trending <i>toward</i> the strike
                    is correctly more likely to reach it). ≤ 25% is the comfort zone; above it the strike is penalized,
                    steering selection to <b>deeper, harder-to-reach</b> strikes.</li>
                  <li><b>Fortified</b> — the strike is <i>both</i> deep (low touch) <i>and</i> behind a wall: structure and
                    distance both have to fail. The lowest-breach placement.</li>
                  <li><b>Calm tape / clean window</b> — a range-bound / mean-reverting regime (probes of the strike tend to
                    revert rather than persist into assignment), and the σ itself is <b>event-aware</b>, so a window with
                    <b> no earnings/events</b> keeps the breach cone narrow while an upcoming print widens it.</li>
                  <li><b>Vol-expansion / Defensibility / Systemic beta</b> — path risks: a short-gamma tape can widen the
                    breach cone; <b>Defensibility</b> rewards a strike you can roll <i>away-and-out for a credit</i> (a put
                    down, a call up — defend for free) and docks near-expiry / debit-to-roll trades; <b>Systemic beta</b>
                    docks a high-beta short leg when the <b>market moves against it</b> — a short <b>put</b> when the SPX is
                    trending <b>down</b>, a short <b>call</b> when it's trending <b>up</b> — which the single-name touch
                    prob can't see.</li>
                </ul>
                <p className="text-[12px] text-base-content/55 leading-snug mt-1.5">
                  <b>Win %</b> is still the finish-OTM (keep-at-expiry) probability; <b>Breach risk</b> is the stricter
                  "never goes ITM" read — the one that matters most for avoiding assignment.
                </p>
              </div>

              {/* Reading the desk chips & boundaries */}
              <div>
                <SectionLabel>The chips &amp; boundaries you'll see</SectionLabel>

                <Chip icon={<Zap className="w-4 h-4 text-warning" />}
                  title={<>Gamma: <span className="text-success">LONG · vol-suppressed</span> / <span className="text-error">SHORT · vol-expansion</span> · flip $X · <span className="opacity-60">proxy</span></>}>
                  A dealer-positioning read (GEX — gamma exposure) modelled from the option chain's open interest.
                  <b> LONG</b> gamma → dealers fade moves (sell rallies / buy dips) → volatility <b>suppressed</b>, mean-reverting
                  → a <b>good backdrop for selling premium</b>. <b>SHORT</b> gamma → dealers chase moves → volatility <b>expands</b>,
                  trending → <b>dangerous</b> (delta-neutral structures like iron condors get penalised, sometimes vetoed).
                  The <b>flip</b> is the price where the regime crosses zero. Labelled <b>proxy</b> because it's built from
                  retail open-interest, not classified dealer flow.
                </Chip>

                <Chip icon={<Activity className="w-4 h-4 text-info" />}
                  title={<>implied (Q) vs realized (P) boundary — <span className="text-info">implied Q ±26%</span> · <span className="text-warning">physical P ±30.5%</span> · <span className="text-success">short 51.2% · clears both</span></>}>
                  A number-line with <b>spot at the centre</b>. The <span className="text-info">implied (Q)</span> band is
                  the option market's 1σ move to expiry (the <i>risk-neutral</i> law you're paid on). The
                  <span className="text-warning"> physical (P)</span> band is how far the stock actually tends to move
                  (realized vol). The <b>▲ marker</b> is your short strike. If the strike sits <b>outside both</b> bands it
                  <span className="text-success"> clears both</span> (cushioned); if it's inside the physical band it's
                  <span className="text-error"> exposed</span> — the stock realistically reaches it. When P is wider than Q
                  (implied crushed below realized) you're being <b>under-paid</b> for the risk — the negative-VRP trap.
                </Chip>

                <Chip icon={<Sparkles className="w-4 h-4 text-secondary" />}
                  title={<>TA factors · 6-mo daily (± on base) — e.g. <span className="text-success">Trend drift +6</span>, <span className="text-error">Gamma regime −6</span></>}>
                  Each signed bar is one technical factor's points on the base score, all computed on <b>6-month daily</b>
                  bars (the swing horizon for a multi-week option). <b>Trend drift</b> — <b>momentum as one signal</b>: the
                  annualized EMA-slope <i>velocity</i> (a tailwind + or headwind − for the short side) <i>modulated</i> by
                  MACD <i>acceleration</i> — a tailwind that's decelerating is faded, not paid in full. <b>Structure</b> — is
                  the strike behind a wall (see the section above). <b>Gamma regime</b> — the dealer-gamma chip above,
                  applied to every trade. <b>Range fit</b> — a range-bound tape suits neutral premium. <b>LVN slip</b> — a
                  strike sitting in a thin volume node has no absorption (−). When that momentum accelerates against a strike
                  that's <b>actually reachable</b> (near-money / non-trivial P(touch)) it becomes a <span className="text-warning"><b>WAIT · timing</b></span>
                  hold — but on a deep, wall-defended strike it's just the faded drift above, never a veto. (RSI &amp;
                  Bollinger are shown for context but don't move the deterministic score.)
                </Chip>
              </div>

              {/* Volatility reads */}
              <div>
                <SectionLabel>Volatility reads under the stock</SectionLabel>
                <Term name="HV30">Trailing 30-day realized vol — how much the stock <b>has</b> moved (annualized). Backward-looking.</Term>
                <Term name="Fwd RV · HAR">
                  <b>HAR-RV</b> (Corsi's Heterogeneous Auto-Regressive model): a <b>forward</b> ~1-month realized-vol
                  <b> forecast</b>. It regresses next-month variance on three horizons of past variance — <b>daily, weekly,
                  monthly</b> — capturing vol's long memory and mean-reversion. Shown next to HV30 as the forward
                  counterpart, with <span className="font-mono text-[11px]">IV ±vp</span> = how rich implied is to the
                  forecast. Used as a <b>VRP cross-check</b>: selling premium has real edge when implied sits comfortably
                  <i> above</i> the forward RV, not just above trailing HV. (The Q-vs-P boundary still uses trailing HV — the
                  robust measure; HAR is the forward sanity check.)
                </Term>
              </div>

              {/* Glossary */}
              <div>
                <SectionLabel>Glossary — the terms underneath</SectionLabel>
                <Term name="Omega">Probability-weighted <b>gains ÷ losses</b>. &gt;1 = a winning bet on average; &lt;1 = loses in expectation.</Term>
                <Term name="Sortino">Return per unit of <b>downside</b> risk — only bad volatility counts, not upside swings.</Term>
                <Term name="Tail / CVaR₉₅">The <b>average loss in the worst 5%</b> of outcomes — how bad the bad case is.</Term>
                <Term name="Carry">Yield earned just for <b>holding</b> the position, versus parking cash at the risk-free rate.</Term>
                <Term name="Edge">The risk/reward tilt (derived from Omega): are the probability-weighted gains bigger than the losses?</Term>
                <Term name="VRP">Volatility Risk Premium — implied vol usually runs <b>above</b> realized vol, so sellers are over-paid. The real source of income edge.</Term>
                <Term name="Skew">How much more the market charges for downside puts than calls. <b>Extreme</b> skew can mean a disaster is already priced in.</Term>
                <Term name="GEX / flip">Dealer gamma exposure (a positioning proxy) and the price where it flips sign — see the Gamma chip above.</Term>
                <Term name="Q vs P">Q = risk-neutral (option-implied) law you're paid on; P = physical (realized) law of how the stock moves.</Term>
                <Term name="vs SOFR (bps)">Expected return minus the risk-free cash rate. Under option math this is ~0 for a fairly-priced trade — slightly negative is <b>normal</b>, not broken.</Term>
                <Term name="Θ / day">Theta — the premium decay you <b>collect</b> each day the trade is held.</Term>
                <Term name="net Δ">Delta — net directional exposure (≈ shares-equivalent) to the underlying moving $1.</Term>
              </div>

              <p className="text-[10px] text-base-content/35 pt-1 border-t border-white/[0.05]">
                All figures are model estimates from live option prices, not guarantees. Selling options caps your gain and
                leaves the tail open — size accordingly.
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
