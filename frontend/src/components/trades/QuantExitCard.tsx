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
import { useState, useEffect } from 'react';
import { Loader2, Cpu, AlertTriangle } from 'lucide-react';
import { runDeskScore } from '../../api';
import type { QuantExit, LivePnlResponse, SavedStrategyItem, DeskScoreResult } from '../../api';
import ManagementAnalysis from './ManagementAnalysis';

const SIGNAL: Record<string, { label: string; cls: string; tone: string }> = {
  STRONG_HOLD:    { label: 'STRONG HOLD',    cls: 'badge-success',               tone: 'success' },
  HOLD:           { label: 'HOLD',           cls: 'badge-success badge-outline',  tone: 'success' },
  CLOSE:          { label: 'CLOSE',          cls: 'badge-warning',                tone: 'warning' },
  STRONG_CLOSE:   { label: 'STRONG CLOSE',   cls: 'badge-error',                  tone: 'error' },
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

  // The full desk score, when fetched, refines the drift-adjusted anchor; else the
  // light quant_exit (PoP anchor). BOTH are MANAGEMENT reads — anchored on the
  // position's chance of keeping its edge, NOT the entry desk score.
  const sig = full?.signal || q.signal;
  const s = SIGNAL[sig] || SIGNAL.HOLD;
  const ss = full?.subscores || q.subscores;
  const lifeAdj = full?.lifecycle_adjustments || q.adjustments;
  const score = full?.lifecycle_score ?? q.score;
  const baseQ = full?.base_quality ?? q.base_quality;
  const holdBase = full?.hold_base ?? q.hold_base;
  const baseSrc = full?.base_source ?? q.base_source;
  const baseLabel = baseSrc === 'neutral' ? 'neutral baseline' : 'position baseline';
  const factors = q.factors ?? [];   // light narrative reads (shown until the deep read loads)

  return (
    <div className={`rounded-lg border p-3 bg-${s.tone}/5 border-${s.tone}/20 space-y-2`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`badge badge-sm font-semibold ${s.cls}`}>{s.label}</span>
        <span className="text-[9px] uppercase tracking-wider text-base-content/40">
          Quant · manage this trade{full ? ' · drift-adjusted' : ''}
        </span>
        {pnl.analysis?.exit_scope === 'options_overlay' && (
          <span className="badge badge-ghost badge-xs text-[8px] text-info/70" title="Manages the OPTION overlay — the underlying stock is held separately for its own purpose">
            options overlay
          </span>
        )}
        <span className="ml-auto text-sm font-bold" title="Hold conviction — high = let it work, low = close">{score}<span className="text-[10px] text-base-content/40">/100</span></span>
        {deskFocus && !full && (
          <button className="btn btn-outline btn-secondary btn-xs gap-1" disabled={loading} onClick={(e) => { e.stopPropagation(); runFull(); }}
            title="Run the full scan engine on THIS trade and re-read every factor for a holder (VRP/Moneyness/Liquidity/TA)">
            {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Cpu className="w-3 h-3" />}
            {loading ? 'Analyzing…' : 'Deep quant analysis'}
          </button>
        )}
      </div>
      {err && (
        <div className="text-[10px] text-base-content/50 flex items-start gap-1">
          <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0 text-base-content/40" />
          <span>{err}</span>
        </div>
      )}

      {/* ── LIGHT view — the quick read, shown until the deep analysis is loaded ── */}
      {!full && (
        <>
          {/* Holder-framed narrative reads (vol decay, trend vs YOUR strike, theta left) */}
          {factors.length > 0 && (
            <div className="space-y-1 rounded-md bg-base-100/30 p-2">
              <div className="text-[9px] uppercase tracking-wider text-base-content/30">Management read · your position</div>
              {factors.map((f, i) => (
                <div key={i} className="flex items-start gap-1.5 text-[10px] leading-snug">
                  <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${f.favorable === true ? 'bg-success' : f.favorable === false ? 'bg-error' : 'bg-base-content/30'}`} />
                  <span className="text-base-content/70"><b className="text-base-content/90">{f.label}:</b> {f.note}</span>
                </div>
              ))}
            </div>
          )}

          {ss && ss.edge != null && (
            <details className="group">
              <summary className="text-[9px] uppercase tracking-wider text-base-content/30 cursor-pointer list-none flex items-center gap-1">
                <span className="group-open:hidden">▸</span><span className="hidden group-open:inline">▾</span>
                Position risk/reward · reference {baseQ != null && <span className="text-base-content/40">({baseQ})</span>}
              </summary>
              <div className="flex gap-2 mt-1">
                <Lens label="Edge" v={ss.edge} /><Lens label="PoP" v={ss.pop} /><Lens label="Sortino" v={ss.sortino} />
                <Lens label="Tail" v={ss.tail} /><Lens label="Carry" v={ss.carry} />
              </div>
            </details>
          )}

          {lifeAdj.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {lifeAdj.map((a, i) => (
                <span key={i} className={`badge badge-xs text-[9px] ${a.pts >= 0 ? 'badge-success badge-outline' : 'badge-error badge-outline'}`} title={a.note}>
                  {a.name} {a.pts >= 0 ? '+' : ''}{a.pts}
                </span>
              ))}
            </div>
          )}

          <div className="text-[10px] text-base-content/50 font-mono">
            {holdBase != null ? `${holdBase} ${baseLabel}` : `${baseQ ?? ''} base`}
            {lifeAdj.map((a, i) => <span key={i}> {a.pts >= 0 ? '+' : '−'}{Math.abs(a.pts)} {a.name.split(' ')[0].toLowerCase()}</span>)}
            {' '}= {score} → <span className={`text-${s.tone} font-semibold`}>{s.label}</span>
          </div>

          {q.overrides.length > 0 && (
            <div className="text-[10px] text-warning/80">Override: {q.overrides.join('; ')}</div>
          )}
        </>
      )}

      {/* ── DEEP view — the scan's quant engine, every factor RE-SIGNED for a holder ── */}
      {full?.management_analysis && (
        <div className="pt-1" onClick={(e) => e.stopPropagation()}>
          <ManagementAnalysis ma={full.management_analysis} qp={full.qp} perSide={full.opp?.per_side} />
        </div>
      )}
    </div>
  );
}


/**
 * QuantAnalysisLoader — the DEEP desk-score experience (the SAME as the Derivative Income
 * scan: base quality + option-math factors + TA + Q-vs-P boundary + the holder-re-signed
 * Management Analysis) as a LAZY panel. It runs on mount, so wrapped in a CollapsibleSection
 * it only fetches when the user opens the panel — replacing the always-on "Management read".
 */
export function QuantAnalysisLoader({ trade, pnl, deskFocus }: {
  trade: SavedStrategyItem;
  pnl: LivePnlResponse;
  deskFocus?: { structure: string; expiration?: string | null; short_strike?: number | null } | null;
}) {
  const [full, setFull] = useState<DeskScoreResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      if (!deskFocus) { setErr('A fresh-chain desk score isn’t defined for this structure.'); return; }
      setLoading(true); setErr(null);
      try {
        const r = await runDeskScore(trade.id, pnl, deskFocus, pnl.quote_source || 'yfinance');
        if (!alive) return;
        if (!r.matched) setErr(r.error || 'This trade is not among the current desk candidates.');
        else setFull(r);
      } catch (e: any) { if (alive) setErr(e?.message || 'Desk score failed'); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) return (
    <div className="flex items-center gap-2 text-xs text-base-content/50 py-3">
      <Loader2 className="w-4 h-4 animate-spin" /> Running the desk on this exact trade…
    </div>
  );
  if (err) return (
    <div className="text-[11px] text-base-content/50 flex items-start gap-1.5 py-1">
      <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0 text-base-content/40" /><span>{err}</span>
    </div>
  );
  if (!full) return null;
  // My Trades is the MANAGE flow only (hold/close). The entry-desk "as a new trade" read was removed —
  // to see how a structure grades as a fresh entry, use the Evaluate tab.
  return (
    <div className="space-y-2">
      {full.management_analysis && <ManagementAnalysis ma={full.management_analysis} qp={full.qp} perSide={full.opp?.per_side} />}
    </div>
  );
}
