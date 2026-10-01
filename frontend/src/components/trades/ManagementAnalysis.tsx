/**
 * ManagementAnalysis — the DEEP hold-vs-close read for a trade you already hold.
 *
 * "Given I'm already in, is what's LEFT worth the risk?" The base is COMPUTED from the
 * live position state — remaining reward vs remaining risk from HERE (keep-prob,
 * premium-left × keep, Omega/Sortino, CVaR tail, cushion), the holder analogue of the
 * scan's entry base — NOT a fixed 50. Then the SAME scan factors, re-signed for a
 * holder (cheap/falling vol → "Vol decay"; liquidity → cost to CLOSE; expectation →
 * remote tail), a dynamic-greek convexity term, and a slim time/gamma overlay decide
 * STRONG_HOLD / HOLD / CLOSE / STRONG_CLOSE. A booked winner with premium left and a
 * manageable tail keeps reading HOLD — no reflexive "you're green, close".
 */
import { useState } from 'react';
import { QpBoundary, SideQuantPanel, DIM_ORDER, FactorLine, DimensionHeader, BaseQualityPanel } from '../DeskReview';
import type { ManagementAnalysis as MA, ManagementContribution } from '../../api';
import type { PerSideQuant } from '../../types';

// Group the holder contributions by risk dimension (same order as the entry desk), 'Other' last.
function groupContribs(cs: ManagementContribution[]): [string, ManagementContribution[]][] {
  const g = new Map<string, ManagementContribution[]>();
  for (const c of cs) { const d = c.dimension || 'Other'; if (!g.has(d)) g.set(d, []); g.get(d)!.push(c); }
  return [...DIM_ORDER, 'Other'].filter(d => g.has(d)).map(d => [d, g.get(d)!] as [string, ManagementContribution[]]);
}

// Base quality now renders through the SHARED BaseQualityPanel (imported from DeskReview), so the
// holder base reads identically to the entry grade's base — one lens-row design, no black box.

const SIGNAL: Record<string, { label: string; cls: string; tone: string }> = {
  STRONG_HOLD:    { label: 'STRONG HOLD',    cls: 'badge-success',              tone: 'success' },
  HOLD:           { label: 'HOLD',           cls: 'badge-success badge-outline', tone: 'success' },
  CLOSE:          { label: 'CLOSE',          cls: 'badge-warning',               tone: 'warning' },
  STRONG_CLOSE:   { label: 'STRONG CLOSE',   cls: 'badge-error',                 tone: 'error' },
};

const GROUP = 'text-[9px] uppercase tracking-wider text-base-content/40 mb-1';
const sgn = (n: number) => `${n > 0 ? '+' : ''}${n}`;

// Verdict tone → the solid/glow classes for the focal badge + receipt total. Full literal strings
// (not interpolated) so Tailwind's JIT always emits them.
const TONE: Record<string, { solid: string; text: string; border: string; glow: string; ring: string }> = {
  success: { solid: 'bg-success text-success-content', text: 'text-success', border: 'border-success/35', glow: 'shadow-lg shadow-success/40', ring: 'ring-success/50' },
  warning: { solid: 'bg-warning text-warning-content', text: 'text-warning', border: 'border-warning/35', glow: 'shadow-lg shadow-warning/40', ring: 'ring-warning/50' },
  error:   { solid: 'bg-error text-error-content',     text: 'text-error',   border: 'border-error/35',   glow: 'shadow-lg shadow-error/40',   ring: 'ring-error/50' },
};

// One line of the score-build-up "receipt": label — dotted leader — right-aligned signed value.
function ReceiptRow({ label, value, signed = false, note }: { label: string; value: number; signed?: boolean; note?: string }) {
  const disp = signed ? sgn(value) : `${value}`;
  const col = signed ? (value > 0 ? 'text-success' : value < 0 ? 'text-error' : 'text-base-content/45') : 'text-base-content/70';
  return (
    <div className="flex items-baseline gap-1 text-[10.5px]" title={note}>
      <span className="text-base-content/55 shrink-0">{label}</span>
      <span className="flex-1 self-center border-b border-dotted border-white/[0.09]" />
      <span className={`font-mono font-semibold tabular-nums shrink-0 ${col}`}>{disp}</span>
    </div>
  );
}

export default function ManagementAnalysis({ ma, qp, perSide }: { ma: MA; qp?: any; perSide?: PerSideQuant | null }) {
  const s = SIGNAL[ma.signal] || SIGNAL.HOLD;
  const b = TONE[s.tone] || TONE.success;
  // Per-side (call/put) tabs for a two-sided held position — the hold/close SCORE stays whole-trade
  // (it doesn't decompose per leg); the side tabs surface the live per-side risk (breach / cushion /
  // greeks-now / defend) so you can see WHICH side is under pressure and how to defend it.
  const hasSides = !!perSide && Array.isArray(perSide.sides) && perSide.sides.length >= 2;
  const [tab, setTab] = useState<'trade' | 'call' | 'put'>('trade');
  const activeSide = hasSides && tab !== 'trade' ? (perSide!.sides.find(x => x.key === tab) || null) : null;

  return (
    <div className="rounded-lg border border-secondary/20 bg-secondary/[0.03] p-3 space-y-3">
      <div className="flex items-center gap-2">
        <div className="min-w-0">
          <div className="text-[10px] uppercase tracking-wider font-semibold text-secondary/70 leading-tight">Manage This Position</div>
          <div className="text-[9px] text-base-content/35 leading-tight">hold vs close · your live position</div>
        </div>
        {/* Focal verdict badge — solid tone segment + big score, ringed and glowing so it's the anchor */}
        <div className={`ml-auto shrink-0 flex items-stretch rounded-lg overflow-hidden ring-1 ${b.ring} ${b.glow}`}>
          <span className={`flex items-center px-2.5 text-[11px] font-extrabold tracking-wide ${b.solid}`}>{s.label}</span>
          <span className="flex items-baseline gap-0.5 px-2.5 py-1 bg-base-100/70">
            <b className={`text-lg font-black leading-none tabular-nums ${b.text}`}>{ma.score}</b>
            <span className="text-[9px] text-base-content/40">/100</span>
          </span>
        </div>
      </div>

      {hasSides && (
        <div>
          <div className="inline-flex rounded-lg border border-white/10 bg-base-100/40 p-0.5 text-[11px]">
            {[['trade', 'Manage'] as const, ...perSide!.sides.map(x => [x.key, x.label] as const)].map(([k, lbl]) => (
              <button key={k} type="button" onClick={() => setTab(k as 'trade' | 'call' | 'put')}
                className={`px-2.5 py-1 rounded-md transition-colors ${tab === k ? 'bg-secondary/20 text-secondary font-semibold' : 'text-base-content/60 hover:text-base-content'}`}>
                {lbl}
              </button>
            ))}
          </div>
          {tab !== 'trade' && <p className="text-[9.5px] text-base-content/45 mt-1 leading-snug">Live per-side risk of your position. The hold/close score is whole-trade — it doesn't split per leg.</p>}
        </div>
      )}

      {activeSide && <SideQuantPanel side={activeSide} variant="manage" />}

      {tab === 'trade' && (<>
      {/* Structural advice — covered vs naked call (capital already committed) */}
      {ma.advisories && ma.advisories.length > 0 && ma.advisories.map((a, i) => (
        <div key={i} className="text-[11px] rounded-lg border border-warning/25 bg-warning/[0.06] px-2 py-1.5 text-warning/90 leading-snug">{a}</div>
      ))}

      {/* The implied-vs-physical boundary — does spot clear both bands vs your strike? */}
      {qp && <QpBoundary qp={qp} />}

      {/* Computed base — the 5 lenses and the EXACT weighted arithmetic that reaches it,
          so "base quality" is auditable (mirrors the scan's base-quality build-up). */}
      <BaseQualityPanel lenses={ma.base_lenses || []} base={ma.anchor}
        intro={`${ma.anchor_label} — each lens rates the remaining trade 0–100; weighted together they set the starting base, before the factors below adjust it.`} />

      {/* Re-signed scan + dynamic-greek factors — the holder interpretation */}
      {ma.contributions.length > 0 && (
        <div>
          <div className={GROUP}>Factors by dimension · re-signed for your position</div>
          {/* Grouped by risk dimension (Loss probability, Vol edge, Structural defense, …) — the SAME
              taxonomy as the entry desk. One evenly-typed row per factor: NAME · this trade's DATA
              (cushion %/σ, IV/HV, spread, earnings date) · POINTS. The note splits at the em/en-dash —
              the value before it is shown; the plain-English help after it lives on the NAME's tooltip. */}
          {groupContribs(ma.contributions).map(([dim, cs]) => {
            const net = cs.reduce((s, c) => s + c.pts, 0);
            return (
              <div key={dim} className="mb-2.5">
                {/* Shared prominent header — the dimension's ROLL-UP net is the headline (bold tinted
                    chip), never fainter than its own factors. */}
                <DimensionHeader dim={dim} net={net} />
                {/* SHARED FactorLine, ONE column so the data never truncates — same component as the
                    scan/Evaluate entry view; hover the dotted name for the plain-English help. */}
                <div>
                  {cs.map((c, i) => <FactorLine key={i} label={c.label} pts={c.pts} note={c.note} />)}
                </div>
              </div>
            );
          })}
          {/* Grand total of all factors — tops the hierarchy above each dimension's net. */}
          <div className="flex items-center gap-2 mt-2 pt-1.5 border-t border-white/[0.08]">
            <span className="text-[10px] uppercase tracking-wider font-semibold text-base-content/60">Net factors</span>
            <div className="flex-1 h-px bg-white/[0.05]" />
            <span className={`font-mono text-sm font-bold tabular-nums leading-none px-1.5 py-1 rounded ${ma.factors_net > 0 ? 'bg-success/15 text-success' : ma.factors_net < 0 ? 'bg-error/15 text-error' : 'bg-base-100/60 text-base-content/60'}`}>{sgn(ma.factors_net)}</span>
          </div>
        </div>
      )}

      {/* Score build-up — a RECEIPT: each component on its own line with a dotted leader, values in a
          strict right column, then a ruled total with the final score + verdict. The time/gamma overlay
          is just another line item (no separate floating pill). */}
      <div className={`rounded-lg border ${b.border} bg-base-300/30 p-2.5`}>
        <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1.5">Score build-up</div>
        <div className="space-y-1">
          <ReceiptRow label="Base — hold quality" value={ma.anchor} />
          <ReceiptRow label="Factors" value={ma.factors_net} signed />
          {ma.overlay.map((o, i) => <ReceiptRow key={i} label={o.name} value={o.pts} signed note={o.note} />)}
        </div>
        <div className={`mt-2 pt-2 border-t ${b.border} flex items-center gap-2`}>
          <span className="text-[11px] font-semibold text-base-content/80">Management score</span>
          <span className="ml-auto flex items-baseline gap-2">
            <b className={`text-xl font-black leading-none tabular-nums ${b.text}`}>{ma.score}</b>
            <span className={`badge badge-sm border-0 font-bold ${b.solid}`}>{s.label}</span>
          </span>
        </div>
      </div>

      {ma.overrides.length > 0 && (
        <div className="text-[10px] text-warning/80">Override: {ma.overrides.join('; ')}</div>
      )}
      </>)}
    </div>
  );
}
