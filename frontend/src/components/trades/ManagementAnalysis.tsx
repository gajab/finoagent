/**
 * ManagementAnalysis — the DEEP hold-vs-close read for a trade you already hold.
 *
 * It leverages the SAME quant engine the Derivative Income scan runs (VRP boundary,
 * Moneyness, Liquidity, Expectation, TA regime/value-area/gamma) but every factor is
 * RE-SIGNED and RE-WEIGHTED for a holder (cheap implied vol flips from an entry
 * demerit to "Vol decay" in your favour; liquidity becomes the cost to CLOSE;
 * expectation is downweighted to a remote tail). Starts from a NEUTRAL 50 baseline
 * (not keep-prob), then the re-signed factors + take-profit / time-gamma overlay →
 * STRONG_HOLD / HOLD / CLOSE / STRONG_CLOSE. Mirrors the scan's build-up so it's
 * fully auditable — but answers "should I stay in?", not "should I enter?".
 */
import { QpBoundary } from '../DeskReview';
import type { ManagementAnalysis as MA } from '../../api';

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

      {/* Baseline — neutral 50; the re-signed factors move it toward hold or close */}
      <div className="flex items-baseline gap-2">
        <span className={GROUP}>{ma.anchor_label}</span>
        <span className="text-sm font-bold text-base-content/80 ml-auto">{ma.anchor}</span>
      </div>

      {/* Re-signed scan factors — the holder interpretation */}
      {ma.contributions.length > 0 && (
        <div>
          <div className={GROUP}>Factors · re-signed for your position (± on keep-prob)</div>
          <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1.5">
            {ma.contributions.map((c, i) => <FactorRow key={i} {...c} />)}
          </div>
          <div className="text-right text-[10px] font-semibold mt-1 text-base-content/60">
            Net factors <span className={ma.factors_net >= 0 ? 'text-success' : 'text-error'}>{sgn(ma.factors_net)}</span>
          </div>
        </div>
      )}

      {/* Take-profit / time overlay */}
      {ma.overlay.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {ma.overlay.map((o, i) => (
            <span key={i} className={`badge badge-xs text-[9px] ${o.pts >= 0 ? 'badge-success badge-outline' : 'badge-error badge-outline'}`} title={o.note}>
              {o.name} {sgn(o.pts)}
            </span>
          ))}
        </div>
      )}

      {/* Auditable build-up */}
      <div className="text-[10px] text-base-content/50 font-mono pt-1.5 border-t border-white/[0.06]">
        {ma.anchor} {ma.anchor_label} {sgn(ma.factors_net)} factors {sgn(overlayNet)} overlay = {ma.score}
        {' '}→ <span className={`text-${s.tone} font-semibold`}>{s.label}</span>
      </div>

      {ma.overrides.length > 0 && (
        <div className="text-[10px] text-warning/80">Override: {ma.overrides.join('; ')}</div>
      )}
    </div>
  );
}
