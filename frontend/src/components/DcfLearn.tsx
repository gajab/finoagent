import React, { useEffect } from 'react';
import { X, BookOpen } from 'lucide-react';

/** The two-stage FCF growth glide-path (constant CAP → linear fade → terminal). */
function GrowthProfile() {
  const bc = (a: number) => `hsl(var(--bc) / ${a})`;
  return (
    <svg viewBox="0 0 640 312" className="w-full h-auto" role="img" aria-label="Two-stage growth profile">
      {/* zone backgrounds */}
      <rect x="60" y="40" width="176" height="200" fill="hsl(var(--p) / 0.08)" />
      <rect x="236" y="40" width="220" height="200" fill="hsl(var(--p) / 0.04)" />
      <rect x="456" y="40" width="104" height="200" fill={bc(0.06)} />
      {/* axes */}
      <line x1="60" y1="240" x2="576" y2="240" stroke={bc(0.4)} strokeWidth="1.5" />
      <line x1="60" y1="40" x2="60" y2="240" stroke={bc(0.4)} strokeWidth="1.5" />
      {/* y ticks */}
      <line x1="56" y1="96" x2="60" y2="96" stroke={bc(0.4)} />
      <text x="52" y="100" textAnchor="end" fontSize="11" fill={bc(0.7)}>7.6%</text>
      <text x="52" y="88" textAnchor="end" fontSize="9" fill={bc(0.4)}>stage-1 (x)</text>
      <line x1="56" y1="192" x2="60" y2="192" stroke={bc(0.4)} />
      <text x="52" y="196" textAnchor="end" fontSize="11" fill={bc(0.7)}>2.5%</text>
      <text x="52" y="184" textAnchor="end" fontSize="9" fill={bc(0.4)}>terminal (l)</text>
      <text x="20" y="150" textAnchor="middle" fontSize="11" fill={bc(0.4)} transform="rotate(-90 20 150)">FCF growth % / yr</text>
      {/* growth path: flat 7.6% yrs 0-4, fade to 2.5% by yr 9, flat after */}
      <polyline points="60,96 236,96 456,192" fill="none" stroke="hsl(var(--p))" strokeWidth="2.5" />
      <line x1="456" y1="192" x2="560" y2="192" stroke="hsl(var(--p))" strokeWidth="2.5" strokeDasharray="5 4" />
      {/* phase dividers */}
      <line x1="236" y1="40" x2="236" y2="240" stroke={bc(0.15)} strokeDasharray="2 3" />
      <line x1="456" y1="40" x2="456" y2="240" stroke={bc(0.15)} strokeDasharray="2 3" />
      {/* x labels */}
      <text x="60" y="256" textAnchor="middle" fontSize="10" fill={bc(0.4)}>yr 0</text>
      <text x="236" y="256" textAnchor="middle" fontSize="10" fill={bc(0.4)}>yr 4</text>
      <text x="456" y="256" textAnchor="middle" fontSize="10" fill={bc(0.4)}>yr 9</text>
      <text x="560" y="256" textAnchor="middle" fontSize="10" fill={bc(0.4)}>∞</text>
      {/* phase captions */}
      <text x="148" y="284" textAnchor="middle" fontSize="11" fontWeight="700" fill="hsl(var(--p))">Stage 1 · CAP = y yrs</text>
      <text x="148" y="300" textAnchor="middle" fontSize="10" fill={bc(0.4)}>constant x% (here 4 yrs)</text>
      <text x="346" y="284" textAnchor="middle" fontSize="11" fontWeight="700" fill={bc(0.7)}>Fade · N = 5 yrs</text>
      <text x="346" y="300" textAnchor="middle" fontSize="10" fill={bc(0.4)}>linear x% → terminal</text>
      <text x="512" y="284" textAnchor="middle" fontSize="11" fontWeight="700" fill={bc(0.85)}>Terminal</text>
      <text x="512" y="300" textAnchor="middle" fontSize="10" fill={bc(0.4)}>perpetuity l%</text>
    </svg>
  );
}

const H = ({ children }: { children: React.ReactNode }) => (
  <h3 className="text-base font-bold mt-6 mb-2 text-base-content">{children}</h3>
);
const P = ({ children }: { children: React.ReactNode }) => (
  <p className="text-sm text-base-content/70 leading-relaxed mb-2">{children}</p>
);
const Code = ({ children }: { children: React.ReactNode }) => (
  <pre className="bg-base-200/60 rounded-lg p-3 text-xs font-mono overflow-x-auto my-2 text-base-content/80 whitespace-pre">{children}</pre>
);

export function DcfLearnModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 sm:p-8 bg-black/50 backdrop-blur-sm animate-fade-in" onClick={onClose}>
      <div className="glass-card max-w-3xl w-full max-h-[88vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-center justify-between px-5 py-3 border-b border-white/[0.06] bg-base-100/80 backdrop-blur">
          <h2 className="font-bold flex items-center gap-2"><BookOpen className="w-5 h-5 text-primary" /> How this DCF works</h2>
          <button className="btn btn-ghost btn-sm btn-square rounded-lg" onClick={onClose}><X className="w-4 h-4" /></button>
        </div>

        <div className="p-5">
          <P>
            This isn't a generic DCF. Every assumption is <b>derived from the company's own size, growth and margins</b> —
            not hand-set. Below is exactly how the engine turns data into each number, with Apple as the running example.
            Nothing here is a magic constant except the 5-year fade window.
          </P>

          <H>The shape: a two-stage glide-path</H>
          <P>
            Free cash flow grows at a <b>stage-1 rate</b> for the <b>competitive-advantage period (CAP)</b>, then glides
            linearly down to a <b>terminal rate</b> over 5 years, then grows at that terminal rate forever:
          </P>
          <div className="rounded-xl bg-base-200/40 border border-white/[0.03] p-3 my-3"><GrowthProfile /></div>

          <H>1 · How the CAP (3 / 4 / 5 / 6 …) is chosen</H>
          <P>
            The CAP is how many years the company can out-grow the economy before mean-reverting. It starts at 5 and adjusts,
            then clamps to [3, 12]:
          </P>
          <Code>{`yrs = 5
  − size:        revenue >$500B: −3   >$100B: −2   >$20B: −1
  + persistence: rev CAGR >15% AND steady (YoY std <15%): +2   |  >8%: +1
  + margin:      FCF margin >20%: +1
CAP = clip(yrs, 3, 12)`}</Code>
          <P>
            The logic: bigger companies mean-revert faster (law of large numbers → fewer years); durable, steady high growth
            and fat margins (a moat proxy) earn more years.
          </P>
          <div className="overflow-x-auto my-2">
            <table className="table table-xs w-full">
              <thead><tr className="text-base-content/50"><th>Company</th><th>Size</th><th>Persistence</th><th>Margin</th><th>CAP</th></tr></thead>
              <tbody>
                <tr><td className="font-semibold">AAPL</td><td>$416B → −2</td><td>~2% CAGR → 0</td><td>24% → +1</td><td className="font-mono">5−2+1 = <b>4</b></td></tr>
                <tr><td className="font-semibold">MSFT</td><td>$245B → −2</td><td>~12% → +1</td><td>&gt;20% → +1</td><td className="font-mono">5−2+1+1 = <b>5</b></td></tr>
                <tr><td>$10B mid-cap, 12% CAGR</td><td>0</td><td>+1</td><td>&lt;20% → 0</td><td className="font-mono"><b>6</b></td></tr>
                <tr><td>$700B, slow</td><td>−3</td><td>0</td><td>+1</td><td className="font-mono">5−3+1 → <b>3</b></td></tr>
              </tbody>
            </table>
          </div>
          <P>So 3/4/5/6 fall out of size vs. growth-quality — not a hand-set number.</P>

          <H>2 · Why terminal growth must be below the WACC</H>
          <P>The terminal value uses the Gordon Growth formula for the perpetuity:</P>
          <Code>{`TV = FCF × (1 + g) / (WACC − g)`}</Code>
          <P>
            If <b>g = WACC</b> the denominator is 0 → infinite value; if <b>g &gt; WACC</b> it's negative → nonsense.
            Economically, a company growing faster than its cost of capital <i>forever</i> out-earns its discount rate every
            year, compounding to infinite value — impossible. In practice terminal g should be ≤ long-run GDP/inflation
            (~2.5–3%), because nothing outgrows the whole economy in perpetuity. The code guards it:
            <code className="text-xs bg-base-200/60 rounded px-1 mx-1">gordon_tv = … if wacc &gt; terminal else 0.0</code>.
          </P>

          <H>3 · How the exit multiple is calculated</H>
          <P>A terminal EV/FCF multiple — "what a mature version of this business would trade at":</P>
          <Code>{`m = 14×  (mature-market baseline)
  + 3 if FCF margin > 20%    (quality)
  + 3 if revenue CAGR > 15%  (durable growth)
m = clip(m, 10×, 24×)`}</Code>
          <P>
            Apple (24% margin, low CAGR) → 14 + 3 = <b>17×</b>. It's deliberately built from <b>fundamentals</b>, not from
            WACC − g — which is the point of the next section.
          </P>

          <H>4 · What "blended Gordon &amp; exit" terminal value means</H>
          <P>
            Terminal value is the value of everything after the explicit forecast — the single biggest, most fragile number
            in a DCF. The engine computes it two <i>independent</i> ways and averages them 50/50:
          </P>
          <Code>{`gordon_tv = FCF_last × (1 + terminal) / (WACC − terminal)   # perpetuity growth
exit_tv   = FCF_last × exit_multiple                       # market comparable
terminal_value = 0.5 × gordon_tv + 0.5 × exit_tv`}</Code>
          <P>
            Why blend: Gordon is hypersensitive to the WACC − g gap (a 0.5% change swings it wildly); the exit multiple is a
            market sanity check but ignores growth. Averaging de-risks each method's weakness — and if the two disagree by
            more than 40%, a <b>flag</b> fires so you know terminal value is shaky.
          </P>

          <H>5 · Where the growth inputs (x, y, l, N) come from</H>
          <P>Mapping the glide-path above to the actual inputs:</P>
          <ul className="text-sm text-base-content/70 space-y-1.5 list-disc pl-5 mb-2">
            <li><b>x = stage-1 growth</b> — a weighted blend, floored at terminal, capped at 40%:
              <Code>{`x = 0.45·forward-analyst growth
  + 0.40·revenue CAGR
  + 0.15·FCF CAGR      →  clip to [terminal, 40%]`}</Code>
              Forward estimates lead; noisy FCF gets the least weight; the floor stops one bad FCF year from projecting
              perpetual shrink (the old "$71 for Apple" bug).</li>
            <li><b>y = CAP years</b> — from the size/persistence/margin rule in §1.</li>
            <li><b>N = 5 years</b> — a fixed fade window.</li>
            <li><b>l = terminal growth</b> — the perpetuity rate (default 2.5%, and tweakable on the page).</li>
          </ul>
          <P>
            One correction to a common mental model: it isn't "x% for y years, then a flat rate for N years." It's
            <b> x% flat for y years, then a linear glide from x down to l over the next 5 years, then l forever</b>:
          </P>
          <Code>{`for t in 1 … (cap + 5):
    g = x                         if t ≤ cap      # constant (stage 1)
      = x + (l − x)·(t − cap)/5   otherwise       # linear fade
    fcf *= (1 + g)`}</Code>
          <P>
            <b>Concrete Apple:</b> 7.6% for years 1–4 (CAP), glide 7.6% → 2.5% over years 5–9, then 2.5% in perpetuity.
            Every one of x, y, N and l is either derived from the company's own numbers or is a slider on this page —
            so you can stress-test any of them.
          </P>

          <p className="text-[11px] text-base-content/30 mt-4 pt-3 border-t border-white/[0.04]">
            Educational — a conservative FCF-DCF will often value high-multiple quality names below the market. That's the
            model, not a bug; use the reverse-DCF (market-implied growth) and the fair-value range alongside it.
          </p>
        </div>
      </div>
    </div>
  );
}
