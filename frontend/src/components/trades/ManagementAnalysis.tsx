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
import { QpBoundary } from '../DeskReview';
import type { ManagementAnalysis as MA, ManagementLens } from '../../api';

// One computed base lens — shows the FULL arithmetic: sub-score bar × weight = points
// toward the base, so the base is never a black-box number.
function LensRow({ lens }: { lens: ManagementLens }) {
  const s = Math.max(0, Math.min(100, lens.score));
  const tone = s >= 66 ? 'bg-success' : s >= 40 ? 'bg-warning' : 'bg-error';
  return (
    <div title={lens.note} className="grid grid-cols-[7.5rem_1fr_5.5rem] items-center gap-2">
      <span className="text-[10px] text-base-content/70 truncate">{lens.label}</span>
      <div className="flex items-center gap-1.5">
        <div className="h-1.5 flex-1 rounded-full bg-base-300/40 overflow-hidden">
          <div className={`h-full ${tone} rounded-full`} style={{ width: `${s}%` }} />
        </div>
        <span className="font-mono text-[10px] text-base-content/50 w-5 text-right">{s}</span>
      </div>
      <span className="font-mono text-[9px] text-base-content/45 text-right whitespace-nowrap">
        ×{lens.weight}% = <span className="text-base-content/70 font-semibold">{lens.contribution}</span>
      </span>
    </div>
  );
}

const SIGNAL: Record<string, { label: string; cls: string; tone: string }> = {
  STRONG_HOLD:    { label: 'STRONG HOLD',    cls: 'badge-success',              tone: 'success' },
  HOLD:           { label: 'HOLD',           cls: 'badge-success badge-outline', tone: 'success' },
  CLOSE:          { label: 'CLOSE',          cls: 'badge-warning',               tone: 'warning' },
  STRONG_CLOSE:   { label: 'STRONG CLOSE',   cls: 'badge-error',                 tone: 'error' },
};

const GROUP = 'text-[9px] uppercase tracking-wider text-base-content/40 mb-1';
const sgn = (n: number) => `${n > 0 ? '+' : ''}${n}`;

// A single re-signed factor — green bar to the right (favorable), red to the left (risk).
function FactorRow({ label, pts, favorable, note }: { label: string; pts: number; favorable: boolean; note: string }) {
  const color = favorable ? 'bg-success' : 'bg-error';
  const w = Math.min(100, Math.abs(pts) * 6);
  return (
    <div title={note}>
      <div className="flex items-center justify-between text-[10px]">
        <span className="text-base-content/70">{label}</span>
        <span className={`font-mono font-semibold ${favorable ? 'text-success' : 'text-error'}`}>{sgn(pts)}</span>
      </div>
      <div className="h-1 rounded-full bg-base-300/40 overflow-hidden flex">
        <div className="w-1/2 flex justify-end">{!favorable && <div className={`h-full ${color} rounded-l-full`} style={{ width: `${w}%` }} />}</div>
        <div className="w-1/2">{favorable && <div className={`h-full ${color} rounded-r-full`} style={{ width: `${w}%` }} />}</div>
      </div>
    </div>
  );
}

export default function ManagementAnalysis({ ma, qp }: { ma: MA; qp?: any }) {
  const s = SIGNAL[ma.signal] || SIGNAL.HOLD;
  const overlayNet = ma.overlay.reduce((a, o) => a + o.pts, 0);

  return (
    <div className="rounded-lg border border-secondary/20 bg-secondary/[0.03] p-3 space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider font-semibold text-secondary/70">Management analysis</span>
        <span className="text-[9px] text-base-content/35">scan factors · re-read for a holder</span>
        <span className={`badge badge-sm font-semibold ml-auto ${s.cls}`}>{s.label}</span>
        <span className="text-sm font-bold">{ma.score}<span className="text-[10px] text-base-content/40">/100</span></span>
      </div>

      {/* Structural advice — covered vs naked call (capital already committed) */}
      {ma.advisories && ma.advisories.length > 0 && ma.advisories.map((a, i) => (
        <div key={i} className="text-[11px] rounded-lg border border-warning/25 bg-warning/[0.06] px-2 py-1.5 text-warning/90 leading-snug">{a}</div>
      ))}

      {/* The implied-vs-physical boundary — does spot clear both bands vs your strike? */}
      {qp && <QpBoundary qp={qp} />}

      {/* Computed base — the 5 lenses and the EXACT weighted arithmetic that reaches it,
          so "base quality" is auditable (mirrors the scan's base-quality build-up). */}
      <div className="rounded-lg border border-white/[0.06] bg-base-300/20 p-2">
        <div className="flex items-baseline gap-2 mb-1.5">
          <span className={GROUP}>{ma.anchor_label}</span>
          <span className="text-[8px] text-base-content/35 ml-auto">score × weight = pts</span>
        </div>
        {ma.base_lenses && ma.base_lenses.length > 0 && (
          <div className="space-y-1.5">
            {ma.base_lenses.map((l, i) => <LensRow key={i} lens={l} />)}
          </div>
        )}
        <div className="flex items-baseline gap-2 mt-2 pt-1.5 border-t border-white/[0.06]">
          <span className="text-[10px] text-base-content/60">Σ weighted lenses</span>
          <span className="ml-auto text-sm font-bold text-base-content/85">{ma.anchor}<span className="text-[10px] text-base-content/40">/100 base</span></span>
        </div>
      </div>

      {/* Re-signed scan + dynamic-greek factors — the holder interpretation */}
      {ma.contributions.length > 0 && (
        <div>
          <div className={GROUP}>Factors · re-signed for your position (adjust the base)</div>
          <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1.5">
            {ma.contributions.map((c, i) => <FactorRow key={i} {...c} />)}
          </div>
          <div className="text-right text-[10px] font-semibold mt-1 text-base-content/60">
            Net factors <span className={ma.factors_net >= 0 ? 'text-success' : 'text-error'}>{sgn(ma.factors_net)}</span>
          </div>
        </div>
      )}

      {/* Slim time / gamma overlay */}
      {ma.overlay.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {ma.overlay.map((o, i) => (
            <span key={i} className={`badge badge-xs text-[9px] ${o.pts >= 0 ? 'badge-success badge-outline' : 'badge-error badge-outline'}`} title={o.note}>
              {o.name} {sgn(o.pts)}
            </span>
          ))}
        </div>
      )}

      {/* Auditable build-up: computed base + factors + overlay = score */}
      <div className="text-[10px] text-base-content/50 font-mono pt-1.5 border-t border-white/[0.06]">
        base {ma.anchor} {sgn(ma.factors_net)} factors {sgn(overlayNet)} overlay = {ma.score}
        {' '}→ <span className={`text-${s.tone} font-semibold`}>{s.label}</span>
      </div>

      {ma.overrides.length > 0 && (
        <div className="text-[10px] text-warning/80">Override: {ma.overrides.join('; ')}</div>
      )}
    </div>
  );
}
