import React, { useState } from 'react';
import { HelpCircle, X, Award, Target, Gauge } from 'lucide-react';

// One place that explains every number the income / desk-review pages show, so the
// Grade / Win% / Execution triad (and the metrics beneath them) stop looking like
// four competing scores. Opened by <RatingsHelpButton/>.

function Term({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(88px,120px)_1fr] gap-3 py-1.5 border-t border-white/[0.05]">
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
            <div className="sticky top-0 flex items-center justify-between gap-2 rounded-t-2xl border-b border-white/10 bg-base-100/95 px-4 py-3 backdrop-blur">
              <p className="text-sm font-bold flex items-center gap-1.5"><HelpCircle className="w-4 h-4" /> How to read the ratings</p>
              <button className="btn btn-ghost btn-xs px-1" onClick={() => setOpen(false)}><X className="w-4 h-4" /></button>
            </div>

            <div className="px-4 py-3 space-y-4 text-base-content/80">
              {/* The three numbers that matter */}
              <div>
                <p className="text-[11px] uppercase tracking-wide text-base-content/40 mb-1.5">Three numbers — three different questions</p>
                <p className="text-[12px] text-base-content/60 mb-2">
                  They look alike but measure unrelated things, so a trade can score high on one and low on another
                  without contradiction. Read them together, not as a single verdict.
                </p>
                <div className="grid gap-2 sm:grid-cols-3">
                  <HeadNum icon={<Award className="w-4 h-4 text-success" />} tone="border-success/25 bg-success/[0.06]"
                    title="Grade" q="Is this a good trade?">
                    Overall quality, A–F, from a blended <b>0–100 desk score</b> (shown as <span className="font-mono">B · 71</span>).
                    A/B = the desk would likely approve; D/F = weak or flawed.
                  </HeadNum>
                  <HeadNum icon={<Target className="w-4 h-4 text-info" />} tone="border-info/25 bg-info/[0.06]"
                    title="Win %" q="How likely to work out?">
                    The chance the option you sold <b>expires worthless</b> — you keep the full premium and are not
                    assigned. Your <i>min-probability</i> setting is the floor on this.
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
                <p className="text-[11px] uppercase tracking-wide text-base-content/40 mb-1.5">What goes into the Grade</p>
                <p className="text-[12px] text-base-content/65 leading-snug">
                  A <b>base quality score</b> is read off one probability-weighted outcome curve — a blend of
                  <span className="font-mono text-[11px]"> Edge · Win-prob · Sortino · Tail · Carry</span> — then adjusted
                  for real-world frictions: <b>VRP</b> (are you over-paid for volatility?), <b>Moneyness</b> (how close is the
                  short strike?), <b>Skew</b>, <b>Liquidity</b> (spread) and <b>Beta</b> (market sensitivity). A genuinely broken
                  trade — e.g. one that loses money in expectation (Omega&lt;0.9 &amp; negative EV) — is demoted, not padded.
                </p>
              </div>

              {/* Glossary */}
              <div>
                <p className="text-[11px] uppercase tracking-wide text-base-content/40 mb-0.5">Glossary — the terms underneath</p>
                <Term name="Omega">Probability-weighted <b>gains ÷ losses</b>. &gt;1 = a winning bet on average; &lt;1 = loses in expectation.</Term>
                <Term name="Sortino">Return per unit of <b>downside</b> risk — only bad volatility counts, not upside swings.</Term>
                <Term name="Tail / CVaR₉₅">The <b>average loss in the worst 5%</b> of outcomes — how bad the bad case is.</Term>
                <Term name="Carry">Yield earned just for <b>holding</b> the position, versus parking cash at the risk-free rate.</Term>
                <Term name="Edge">The risk/reward tilt (derived from Omega): are the probability-weighted gains bigger than the losses?</Term>
                <Term name="VRP">Volatility Risk Premium — implied vol usually runs <b>above</b> realized vol, so sellers are over-paid. The real source of income edge.</Term>
                <Term name="Skew">How much more the market charges for downside puts than calls. <b>Extreme</b> skew can mean a disaster is already priced in.</Term>
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
