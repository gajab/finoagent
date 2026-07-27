/**
 * QuantExitCard — the tier-2 QUANT ALGORITHMIC exit read for a placed trade.
 *
 * Light view (always, from analysis.quant_exit): the 5-lens base-quality score
 * (Edge/PoP/Sortino/Tail/Carry) + lifecycle adjustments → the 4-level signal.
 *
 * Full desk score (on demand, income trades): the SAME engine the scan uses —
 * base quality + OPTION-MATH factors (VRP/Moneyness/Skew/Liquidity/Beta) + TA
 * factors + the Q-vs-P (implied vs realized) boundary — then the lifecycle
 * overlay. Fully deterministic; no LLM.
 */
import { useState } from 'react';
import { Loader2, Cpu, AlertTriangle } from 'lucide-react';
import { runDeskScore } from '../../api';
import type { QuantExit, LivePnlResponse, SavedStrategyItem, DeskScoreResult, DeskFactor } from '../../api';

const SIGNAL: Record<string, { label: string; cls: string; tone: string }> = {
  STRONG_HOLD:    { label: 'STRONG HOLD',    cls: 'badge-success',               tone: 'success' },
  HOLD:           { label: 'HOLD',           cls: 'badge-success badge-outline',  tone: 'success' },
  CONSIDER_CLOSE: { label: 'CONSIDER CLOSE', cls: 'badge-warning',                tone: 'warning' },
  CLOSE:          { label: 'CLOSE',          cls: 'badge-error',                  tone: 'error' },
};

function Lens({ label, v }: { label: string; v: number }) {
  const color = v >= 66 ? 'bg-success' : v >= 40 ? 'bg-warning' : 'bg-error';
  return (
    <div className="flex-1 min-w-[50px]">
      <div className="flex items-center justify-between text-[9px] text-base-content/40 mb-0.5">
        <span className="uppercase tracking-wider">{label}</span><span className="font-mono">{v}</span>
      </div>
      <div className="h-1.5 rounded-full bg-base-300/40 overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(2, Math.min(100, v))}%` }} />
      </div>
    </div>
  );
}

// A group of signed factor contributions (option-math or TA) as ±chips + net.
function FactorGroup({ title, factors }: { title: string; factors: DeskFactor[] }) {
  const active = factors.filter(f => Math.abs(f.points) >= 0.5);
  const net = factors.reduce((s, f) => s + f.points, 0);
  return (
    <div>
      <div className="flex items-center justify-between text-[9px] uppercase tracking-wider text-base-content/30 mb-1">
        <span>{title}</span>
        <span className={net >= 0 ? 'text-success/70' : 'text-error/70'}>net {net >= 0 ? '+' : ''}{net.toFixed(0)}</span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {active.length === 0 && <span className="text-[9px] text-base-content/25">no material factors</span>}
        {active.map((f, i) => (
          <span key={i} className={`badge badge-xs text-[9px] ${f.points >= 0 ? 'badge-success badge-outline' : 'badge-error badge-outline'}`}>
            {f.label} {f.points >= 0 ? '+' : ''}{f.points}
          </span>
        ))}
      </div>
    </div>
  );
}

// Q-vs-P boundary — implied (risk-neutral) vs realized (physical) 1σ move bands,
// with the short strike marked. A wider physical band = the negative-VRP exposure.
function VrpBoundary({ qp }: { qp: NonNullable<DeskScoreResult['qp']> }) {
  const imp = Math.abs(qp.implied_move_pct ?? 0);
  const phys = Math.abs(qp.physical_move_pct ?? 0);
  const sd = Math.abs(qp.short_dist_pct ?? 0);
  const span = Math.max(phys, imp, sd, 5) * 1.2;
  const L = (p: number) => 50 + (p / span) * 50;           // 0 → center
  const ratio = qp.iv_hv_ratio;
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wider text-base-content/30 mb-1">VRP — implied (Q) vs realized (P) boundary</div>
      <div className="relative h-7 rounded bg-base-300/25 overflow-hidden">
        <div className="absolute inset-y-0 bg-error/15" style={{ left: `${L(-phys)}%`, right: `${100 - L(phys)}%` }} title={`physical P ±${phys}%`} />
        <div className="absolute inset-y-1.5 bg-info/30" style={{ left: `${L(-imp)}%`, right: `${100 - L(imp)}%` }} title={`implied Q ±${imp}%`} />
        <div className="absolute inset-y-0 w-px bg-base-content/50" style={{ left: '50%' }} title="spot" />
        {sd > 0 && <div className="absolute inset-y-0 w-0.5 bg-success" style={{ left: `${L(-sd)}%` }} title="short strike" />}
      </div>
      <div className="flex justify-between text-[9px] mt-0.5">
        <span className="text-info/70">implied Q ±{imp}%</span>
        <span className="text-error/70">physical P ±{phys}%</span>
        {sd > 0 && <span className="text-success/70">short {sd}% {qp.short_sigmas != null ? `(${qp.short_sigmas}σ)` : ''}</span>}
      </div>
      {qp.implied_vol_pct != null && qp.realized_vol_pct != null && (
        <div className="text-[9px] text-base-content/40 mt-0.5">
          IV {qp.implied_vol_pct}% vs HV {qp.realized_vol_pct}%
          {ratio != null && <> · VRP {ratio}× <span className={ratio >= 1 ? 'text-success/70' : 'text-error/70'}>{ratio >= 1 ? '(rich)' : '(cheap — premium under-priced)'}</span></>}
        </div>
      )}
    </div>
  );
}

export default function QuantExitCard({ q, trade, pnl, deskFocus }: {
  q: QuantExit;
  trade: SavedStrategyItem;
  pnl: LivePnlResponse;
  deskFocus?: { structure: string; expiration?: string | null; short_strike?: number | null } | null;
}) {
  const [full, setFull] = useState<DeskScoreResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const runFull = async () => {
    if (!deskFocus) return;
    setLoading(true); setErr(null);
    try {
      const r = await runDeskScore(trade.id, pnl, deskFocus, pnl.quote_source || 'yfinance');
      if (!r.matched) setErr(r.error || 'This trade is not among the current desk candidates.');
      else setFull(r);
    } catch (e: any) { setErr(e?.message || 'Desk score failed'); }
    finally { setLoading(false); }
  };

  // The full desk score, when fetched, drives the signal + buildup; else the light quant_exit.
  const sig = full?.signal || q.signal;
  const s = SIGNAL[sig] || SIGNAL.HOLD;
  const ss = full?.subscores || q.subscores;
  const lifeAdj = full?.lifecycle_adjustments || q.adjustments;
  const score = full?.lifecycle_score ?? q.score;
  const baseQ = full?.base_quality ?? q.base_quality;

  return (
    <div className={`rounded-lg border p-3 bg-${s.tone}/5 border-${s.tone}/20 space-y-2`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`badge badge-sm font-semibold ${s.cls}`}>{s.label}</span>
        <span className="text-[9px] uppercase tracking-wider text-base-content/40">
          Quant algorithmic{full ? ' · full desk score' : ''}
        </span>
        {pnl.analysis?.exit_scope === 'options_overlay' && (
          <span className="badge badge-ghost badge-xs text-[8px] text-info/70" title="Manages the OPTION overlay — the underlying stock is held separately for its own purpose">
            options overlay
          </span>
        )}
        {full?.algo_grade && <span className="text-[9px] text-base-content/40">grade {full.algo_grade}</span>}
        <span className="ml-auto text-sm font-bold">{score}<span className="text-[10px] text-base-content/40">/100</span></span>
        {deskFocus && !full && (
          <button className="btn btn-outline btn-secondary btn-xs gap-1" disabled={loading} onClick={(e) => { e.stopPropagation(); runFull(); }}>
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Cpu className="w-3 h-3" />}
            {loading ? 'Scoring…' : 'Run full desk score'}
          </button>
        )}
      </div>
      {err && <div className="text-[10px] text-warning flex items-center gap-1"><AlertTriangle className="w-3 h-3" />{err}</div>}

      {/* Base quality — the 5 payoff-distribution lenses */}
      {ss && (
        <div>
          <div className="text-[9px] uppercase tracking-wider text-base-content/30 mb-1">
            Base quality · payoff distribution {baseQ != null && <span className="text-base-content/40">({baseQ})</span>}
          </div>
          <div className="flex gap-2">
            <Lens label="Edge" v={ss.edge} /><Lens label="PoP" v={ss.pop} /><Lens label="Sortino" v={ss.sortino} />
            <Lens label="Tail" v={ss.tail} /><Lens label="Carry" v={ss.carry} />
          </div>
        </div>
      )}

      {/* Full-desk-only factor groups + VRP boundary */}
      {full && (
        <>
          {full.grade_adjustments && full.grade_adjustments.length > 0 && (
            <FactorGroup title="Option math · regime & factor adjustments" factors={full.grade_adjustments} />
          )}
          {full.ta_factors && full.ta_factors.length > 0 && (
            <FactorGroup title="Technicals · regime & structure" factors={full.ta_factors} />
          )}
          {full.qp && (full.qp.physical_move_pct != null || full.qp.iv_hv_ratio != null) && <VrpBoundary qp={full.qp} />}
        </>
      )}

      {/* Lifecycle adjustments (± on the quality score) */}
      {lifeAdj.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {lifeAdj.map((a, i) => (
            <span key={i} className={`badge badge-xs text-[9px] ${a.pts >= 0 ? 'badge-success badge-outline' : 'badge-error badge-outline'}`} title={a.note}>
              {a.name} {a.pts >= 0 ? '+' : ''}{a.pts}
            </span>
          ))}
        </div>
      )}

      {/* Auditable buildup */}
      <div className="text-[10px] text-base-content/50 font-mono">
        {full ? `desk ${full.desk_score}` : `${baseQ} base`}
        {lifeAdj.map((a, i) => <span key={i}> {a.pts >= 0 ? '+' : '−'}{Math.abs(a.pts)} {a.name.split(' ')[0].toLowerCase()}</span>)}
        {' '}= {score} → <span className={`text-${s.tone} font-semibold`}>{s.label}</span>
      </div>

      {(full?.overrides || q.overrides).length > 0 && (
        <div className="text-[10px] text-warning/80">Override: {(full?.overrides || q.overrides).join('; ')}</div>
      )}
    </div>
  );
}
