import React, { useEffect, useState } from 'react';
import {
  Gauge, Loader2, AlertTriangle, Cpu, Shield, Briefcase, ChevronDown, ChevronUp,
  Trophy, Play, Terminal, Maximize2, X, MessageSquare, Activity, LineChart, Layers, Zap, Sparkles,
} from 'lucide-react';
import { runDeskReview, runDeskReviewAgents } from '../api';
import { RatingsHelpButton } from './RatingsHelp';
import DeskDebateModal from './DeskDebateModal';
import { OpportunitySummary, LegsTable } from './DerivativeIncome';
import CollapsibleSection from './trades/CollapsibleSection';
import { TraderGrid, PmGrid, RiskGrid } from './trades/DeskMetrics';
import type { DeskReviewParams, DeskEvaluateParams } from '../api';
import type { DeskReviewResult, DeskRankedTrade, DeskAgentsResult, DeskAgent } from '../types';

const money = (n: number | null | undefined, d = 0) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const pct = (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`);
// Win% for a sold option (probability it expires worthless). It is asymptotic to — never exactly —
// 100%, and "100.0%" reads as a false guarantee, so cap the DISPLAY at 99.9% (one decimal, floor 0).
const winPct = (n: number | null | undefined) =>
  n == null ? '—' : `${Math.min(99.9, Math.max(0, n)).toFixed(1)}%`;
const ratio = (n: number | null | undefined) =>
  n == null ? '—' : Math.abs(n) >= 1000 ? `${Math.round(n).toLocaleString()}` : n.toFixed(2);
const annShort = (n: number | null | undefined) =>
  n == null ? '—' : n >= 1000 ? '999%+' : `${n.toFixed(0)}%`;

const gradeTone = (g?: string) => {
  const u = (g || '').toUpperCase();
  if (u === 'A' || u === 'B') return 'text-success bg-success/10 border-success/25';
  if (u === 'C' || u === 'D') return 'text-warning bg-warning/10 border-warning/25';
  return 'text-error bg-error/10 border-error/25';   // F / auto_reject
};
// Text-only version — colors the numeric desk score by its GRADE band, so the score
// alone carries the grade's meaning (no separate letter needed).
const gradeTextTone = (g?: string) => {
  const u = (g || '').toUpperCase();
  if (u === 'A' || u === 'B') return 'text-success';
  if (u === 'C' || u === 'D') return 'text-warning';
  if (u === 'F') return 'text-error';
  return 'text-base-content/70';
};
const GREEN = new Set(['CLEAR', 'ACCEPTABLE', 'ENTER', 'GOOD ENTRY', 'HOLD', 'APPROVE_WITH_CONDITIONS', 'APPROVE', 'EXECUTE']);
const RED = new Set(['NONE', 'EXCESSIVE', 'SKIP', 'NO', 'CONCEDE', 'VETO', 'REJECT']);   // WAIT / MIXED / ADJUST / EXECUTE_MODIFIED → amber
const verdictTone = (v?: string) => {
  const u = (v || '').toUpperCase();
  if (GREEN.has(u)) return 'text-success bg-success/10 border-success/25';
  if (RED.has(u)) return 'text-error bg-error/10 border-error/25';
  return 'text-warning bg-warning/10 border-warning/25';
};

function strikeStr(t: DeskRankedTrade) {
  if (t.structure === 'iron_condor') return `${t.put_long}/${t.put_short}–${t.call_short}/${t.call_long}`;
  if (t.structure === 'jade_lizard') return `put ${t.put_short} · call ${t.call_short}/${t.call_long}`;
  if (t.long_strike) return `${t.short_strike}/${t.long_strike}`;
  return `${t.short_strike}`;
}

// Execution / data-trust chip tone (the "Exe." column).
const confTextTone = (l?: string) => {
  const u = (l || '').toLowerCase();
  if (u === 'high') return 'text-success';
  if (u === 'medium') return 'text-warning';
  if (u === 'low') return 'text-error';
  return 'text-base-content/40';
};

// A 0–100 subscore bar (base-quality terms) in a bounded cell so each metric is clearly separated.
function SubBar({ label, v, hint }: { label: string; v: number; hint?: string }) {
  const col = v >= 66 ? 'bg-success' : v >= 45 ? 'bg-warning' : 'bg-error';
  return (
    <div className="flex-1 min-w-[70px] rounded-md border border-white/[0.07] bg-base-200/40 px-2 py-1.5"
      title={hint}>
      <div className="flex justify-between items-baseline mb-1.5">
        <span className="text-[10px] text-base-content/55 cursor-help">{label}</span>
        <span className="text-[11px] font-mono font-semibold text-base-content/75">{v}</span>
      </div>
      <div className="h-2 rounded-full bg-base-300/50 overflow-hidden">
        <div className={`h-full rounded-full ${col}`} style={{ width: `${Math.max(3, Math.min(100, v))}%` }} />
      </div>
    </div>
  );
}

// A SIGNED adjustment cell centred at zero: green extends right (+), red left (−). Bounded + valued
// so magnitude and where each metric starts are both unambiguous.
function AdjBar({ label, v }: { label: string; v: number }) {
  const MAX = 12;                                   // largest single-factor magnitude
  const mag = (Math.min(Math.abs(v), MAX) / MAX) * 50;
  const pos = v >= 0;
  return (
    <div className={`flex-1 min-w-[82px] rounded-md border px-2 py-1.5 ${v === 0 ? 'border-white/[0.06] bg-base-200/30' : pos ? 'border-success/25 bg-success/[0.05]' : 'border-error/25 bg-error/[0.05]'}`}>
      <div className="flex justify-between items-baseline mb-1.5">
        <span className="text-[10px] text-base-content/55">{label}</span>
        <span className={`text-[11px] font-mono font-semibold ${v > 0 ? 'text-success' : v < 0 ? 'text-error' : 'text-base-content/40'}`}>{v > 0 ? '+' : ''}{v}</span>
      </div>
      <div className="relative h-2 rounded-full bg-base-300/50 overflow-hidden">
        <div className="absolute left-1/2 top-0 h-full w-px bg-base-content/35 z-10" />
        {v !== 0 && (
          <div className={`absolute top-0 h-full ${pos ? 'bg-success' : 'bg-error'}`}
            style={pos ? { left: '50%', width: `${mag}%` } : { right: '50%', width: `${mag}%` }} />
        )}
      </div>
    </div>
  );
}

// The inline "explorer" opened under a ranked row — four collapsible desk sections rendered
// straight from the trade's own desk_metrics (no re-fetch, no LLM). Quant Recommendation shows
// base-quality bars + the signed regime/factor adjustments that sum to the desk score.
// The Q-vs-P number-line: spot at centre, the implied (Q) band, the wider physical (P) band, and the
// short-strike marker — so you SEE whether the strike clears the physical boundary or is exposed to it.
export function QpBoundary({ qp }: { qp: NonNullable<DeskRankedTrade['qp']> }) {
  const imp = qp.implied_move_pct ?? 0;
  const phys = qp.physical_move_pct ?? 0;
  const dist = qp.short_dist_pct ?? 0;
  const span = Math.max(imp, phys, dist, 1) * 1.2;
  const half = (v: number) => (v / span) * 50;          // % of the half-width
  const exposed = !!qp.exposed_physical;
  const ratio = qp.iv_hv_ratio;
  return (
    <div className="mb-3">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className={GROUP_LABEL + ' mb-0'}>VRP — implied (Q) vs realized (P) boundary</span>
        {ratio != null && (
          <span className={`text-[10.5px] font-mono font-semibold ${ratio >= 1 ? 'text-success' : 'text-error'}`}
            title="Volatility risk premium: how far implied over/under-prices the physical move. Positive = you're paid to sell.">
            VRP {ratio >= 1 ? '+' : ''}{Math.round((ratio - 1) * 100)}%
          </span>
        )}
      </div>
      <div className="relative h-11 rounded-lg bg-base-300/25 border border-white/[0.06] overflow-hidden">
        {phys > 0 && <div className="absolute inset-y-0 bg-warning/[0.16] border-x-2 border-warning/50"
          style={{ left: `${50 - half(phys)}%`, width: `${2 * half(phys)}%` }} title={`physical (realized) ±${phys}%`} />}
        {imp > 0 && <div className="absolute inset-y-2 rounded bg-info/[0.28] border-x-2 border-info/60"
          style={{ left: `${50 - half(imp)}%`, width: `${2 * half(imp)}%` }} title={`implied (market) ±${imp}%`} />}
        <div className="absolute inset-y-0 left-1/2 w-px bg-base-content/45" />
        <span className="absolute top-1 left-1/2 -translate-x-1/2 text-[8px] uppercase tracking-wide text-base-content/50 bg-base-100/70 px-1 rounded">spot</span>
        {dist > 0 && (
          <div className="absolute inset-y-0 -translate-x-1/2 flex flex-col items-center justify-end pb-0.5"
            style={{ left: `${Math.max(2, Math.min(98, 50 - half(dist)))}%` }} title={`short strike ${dist}% from spot`}>
            <div className={`w-px flex-1 ${exposed ? 'bg-error/50' : 'bg-success/50'}`}></div>
            <span className={`text-[11px] leading-none ${exposed ? 'text-error' : 'text-success'}`}>▲</span>
          </div>
        )}
      </div>
      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-[9.5px] mt-1.5">
        <span className="inline-flex items-center gap-1.5 text-info"><i className="w-2.5 h-2.5 rounded-sm bg-info/40 border border-info/60"></i>implied Q ±{imp}%</span>
        <span className="inline-flex items-center gap-1.5 text-warning"><i className="w-2.5 h-2.5 rounded-sm bg-warning/30 border border-warning/60"></i>physical P ±{phys}%{qp.gap_aware ? ' · gap-aware' : ''}</span>
        <span className={exposed ? 'text-error font-semibold' : 'text-success'}>
          ▲ short {dist}% {exposed ? '· inside physical → exposed' : '· clears both'}
        </span>
      </div>
      {qp.implied_vol_pct != null && (
        <div className="text-[10px] text-base-content/55 mt-1">
          IV {qp.implied_vol_pct}% vs HV {qp.realized_vol_pct}%
          {ratio != null && ratio < 1 && imp > 0 ? ` · physical ${(phys / imp).toFixed(1)}× wider — premium under-priced` : ''}
        </div>
      )}
    </div>
  );
}

// A group's running total, shown UNDER its bars ("at the end").
function GroupFoot({ label, value, signed = false }: { label: string; value: number; signed?: boolean }) {
  const disp = signed ? `${value > 0 ? '+' : ''}${value}` : `${value}`;
  const col = signed ? (value > 0 ? 'text-success' : value < 0 ? 'text-error' : 'text-base-content/45') : 'text-base-content/75';
  return <div className={`text-right text-[10px] font-semibold mt-1 ${col}`}>{label} <span className="font-bold">{disp}</span></div>;
}
const GROUP_LABEL = 'text-[9px] uppercase tracking-wider text-base-content/40 mb-1';

// The QUANT ANALYSIS section on its own — base quality + option-math factor
// adjustments + TA factors + the Q-vs-P boundary → desk score. Exported so the
// placed-trade lifecycle read (My Trades → Quant algorithmic → full desk score)
// renders the IDENTICAL section the Derivative Income scan shows, fed by the
// /desk-score payload. `t` carries the grade fields; `q` is desk_metrics.quant.
export function QuantAnalysisSection({ t, q, defaultOpen = false }: {
  t: DeskRankedTrade; q: any; defaultOpen?: boolean;
}) {
  const sub = q?.subscores;
  const base = Math.round(t.base_quality ?? q?.score ?? 0);
  const adjs = t.grade_adjustments || [];
  const tas = t.ta_factors || [];
  const r1 = (n: number) => Math.round(n * 10) / 10;
  const adjNet = r1(adjs.reduce((s, a) => s + a.points, 0));
  const taNet = r1(tas.reduce((s, a) => s + a.points, 0));
  const sgn = (n: number) => `${n > 0 ? '+' : ''}${n}`;
  const rawSum = r1(base + adjNet + taNet);        // the true arithmetic sum (pre-clamp)
  const clamped = rawSum !== t.desk_score;         // desk_score is clamped to [0, 100]
  return (
    <CollapsibleSection title="Quant Analysis" accent="secondary" defaultOpen={defaultOpen}
      icon={<Cpu className="w-3 h-3" />} subtitle="base + factor + TA → desk score">
      {/* Top: the TOTAL desk score (not the base) */}
      <div className={`flex items-center gap-2 rounded-lg border p-2 mb-2.5 ${gradeTone(t.algo_grade)}`}>
        <span className="text-sm font-bold uppercase tracking-wider">{q?.verdict || 'GRADE'}</span>
        <span className="text-xs text-base-content/50">desk grade · point build-up below</span>
        <span className="ml-auto inline-flex items-baseline gap-1 text-lg font-bold">{t.algo_grade || '—'}<span className="text-[10px] text-base-content/40">grade</span></span>
      </div>

      {/* Q-vs-P boundary — the implied-vs-physical read the base score is now weighted on */}
      {t.qp && <QpBoundary qp={t.qp} />}

      {/* Drift-adjusted Win % — P-measure DRIFT overlay. The headline Win% stays the standard
          risk-neutral PoP; this shows how the trend drift μ would move it. Display only. */}
      {t.qp?.keep_drift_pct != null && t.qp?.keep_standard_pct != null && (() => {
        const dd = (t.qp!.keep_drift_pct! - t.qp!.keep_standard_pct!);
        const mu = t.qp!.drift_mu_pct;
        return (
          <div className="mb-2.5">
            <div className={GROUP_LABEL}>Drift-adjusted Win % — trend (P-measure)</div>
            <div className="flex flex-wrap items-center gap-x-2 text-[11px]">
              <span className="text-base-content/70">{winPct(t.qp!.keep_standard_pct)} <span className="opacity-50">risk-neutral</span></span>
              <span className="opacity-40">→</span>
              <span className={`font-semibold ${dd >= 0 ? 'text-success' : 'text-error'}`}>{winPct(t.qp!.keep_drift_pct)} drift-adjusted</span>
              {mu != null && <span className="text-base-content/50">· EMA drift {mu > 0 ? '+' : ''}{mu}%/yr {dd >= 0 ? 'tailwind' : dd < 0 ? 'headwind' : ''}</span>}
            </div>
            <p className="text-[9px] text-base-content/40 mt-0.5">Headline Win% stays standard PoP; this overlay folds in the trend drift (velocity), not the score.</p>
          </div>
        );
      })()}

      {/* 1) Base quality — bars, then the base score at the end */}
      {sub && (
        <div className="mb-2.5">
          <div className={GROUP_LABEL}>Base quality — payoff distribution</div>
          <div className="flex flex-wrap gap-2">
            <SubBar label="Edge" v={sub.edge} hint="Modeled pricing edge — expected value vs. the option's market price. Higher = you're paid more than fair value." />
            <SubBar label="PoP" v={sub.pop} hint="Probability of profit at expiry (risk-neutral). Higher = more likely to finish in the money." />
            <SubBar label="Sortino" v={sub.sortino} hint="Reward per unit of downside risk — penalizes losses, not upside volatility. Higher = better." />
            <SubBar label="Tail" v={sub.tail} hint="Tail-risk quality — how contained the worst-case (CVaR) loss is. Higher = smaller, safer left tail." />
            <SubBar label="Carry" v={sub.carry} hint="Premium/theta carry earned vs. the risk-free rate. Higher = better paid to hold the risk." />
          </div>
          <GroupFoot label="Base quality" value={base} />
        </div>
      )}

      {/* 2) Option-math factor adjustments — bars, then the net at the end */}
      {adjs.length > 0 && (
        <div className="mb-2.5">
          <div className={GROUP_LABEL}>Regime &amp; factor adjustments — option math (± on base)</div>
          <div className="flex flex-wrap gap-2">
            {adjs.map(a => <AdjBar key={a.label} label={a.label} v={a.points} />)}
          </div>
          <GroupFoot label="Net adjustment" value={adjNet} signed />
        </div>
      )}

      {/* 3) TA factors — the technical read (6-mo daily) that tilts the score */}
      <div className="mb-2">
        <div className={GROUP_LABEL}>TA factors — regime &amp; structure · 6-mo daily (± on base)</div>
        {tas.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {tas.map(a => <AdjBar key={a.label} label={a.label} v={a.points} />)}
          </div>
        ) : (
          <p className="text-[10px] text-base-content/40">Neutral to the current regime — no technical tilt on this structure.</p>
        )}
        <GroupFoot label="Net TA" value={taNet} signed />
      </div>

      {/* Reconciliation — how the pieces sum to the desk score */}
      <p className="text-[10px] text-base-content/45 pt-1.5 border-t border-white/[0.06]">
        {base} base {sgn(adjNet)} factors {sgn(taNet)} TA = {rawSum}
        {clamped
          ? <> → <b className={gradeTextTone(t.algo_grade)}>{t.desk_score}</b> <span className="opacity-70">({rawSum > 100 ? 'capped at 100' : 'floored at 0'})</span></>
          : <> <b className={gradeTextTone(t.algo_grade)}>desk score</b></>}
      </p>

      {/* Key drivers — EVERY non-zero factor (option-math + technical), biggest mover first, so the
          highly positive and highly negative contributions are both explicit. */}
      {(() => {
        const drivers = [...adjs, ...tas].filter(d => d.points !== 0)
          .sort((a, b) => Math.abs(b.points) - Math.abs(a.points));
        if (!drivers.length) return null;
        return (
          <div className="mt-2.5 pt-2 border-t border-white/[0.06]">
            <div className={GROUP_LABEL}>Key drivers — biggest movers first</div>
            <div className="grid sm:grid-cols-2 gap-x-5 gap-y-1">
              {drivers.map((d, i) => (
                <div key={i} className="flex items-center justify-between gap-2 text-[11.5px]">
                  <span className="text-base-content/70 truncate">{d.label}</span>
                  <span className={`font-mono font-semibold shrink-0 ${d.points > 0 ? 'text-success' : 'text-error'}`}>{d.points > 0 ? '+' : ''}{d.points}</span>
                </div>
              ))}
            </div>
          </div>
        );
      })()}

      {/* The detailed 'why' — option-math reasons, then the technical read. */}
      {((t.grade_merits && t.grade_merits.length > 0) || (t.grade_demerits && t.grade_demerits.length > 0)) && (
        <ul className="text-[11px] space-y-0.5 mt-2.5">
          {(t.grade_merits || []).map((r, i) => <li key={`m${i}`} className="flex gap-1.5 text-success/80"><span className="opacity-50">+</span>{r}</li>)}
          {(t.grade_demerits || []).map((r, i) => <li key={`d${i}`} className="flex gap-1.5 text-warning/80"><span className="opacity-50">−</span>{r}</li>)}
        </ul>
      )}
      {t.ta_note && (
        <p className="text-[11px] text-base-content/55 mt-1.5"><span className="text-base-content/40">Technical read:</span> {t.ta_note}</p>
      )}
    </CollapsibleSection>
  );
}

function TradeExplorer({ t, ticker, params, evaluate }: { t: DeskRankedTrade; ticker: string; params: DeskReviewParams; evaluate?: DeskEvaluateParams }) {
  const dm = t.desk_metrics;
  const q = dm.quant || {};
  // Per-trade "Run Institutional Desk" — the Quant→Risk→PM debate focused on THIS trade,
  // presented in the dramatized boardroom modal.
  const [debateOpen, setDebateOpen] = useState(false);
  const [agents, setAgents] = useState<DeskAgentsResult | null>(null);
  const [debateLoading, setDebateLoading] = useState(false);
  const [debateErr, setDebateErr] = useState<string | null>(null);

  const runDesk = async () => {
    setDebateOpen(true); setDebateErr(null); setAgents(null); setDebateLoading(true);
    try {
      // Evaluate tab: rebuild the user's exact bring-your-own trade (works for custom/calendar
      // trades the focus selector can't reconstruct). Scan flow: focus the selected candidate.
      const a = await runDeskReviewAgents(ticker, evaluate
        ? { ...params, evaluate }
        : {
            ...params,
            focus: { structure: t.structure, expiration: t.expiration ?? null,
                     short_strike: t.short_strike ?? t.put_short ?? t.call_short ?? null },
          });
      if (a.error) setDebateErr(a.error); else setAgents(a);
    } catch (e: any) { setDebateErr(e?.message || 'Institutional desk failed'); }
    finally { setDebateLoading(false); }
  };
  return (
    <div className="p-3 space-y-2">
      {(t.grade_blocking && t.grade_blocking.length > 0) && (
        <div className="rounded-lg border border-error/40 bg-error/[0.08] p-2 text-[11px] flex items-start gap-1.5">
          <span className="font-bold text-error shrink-0">⛔ VETOED</span>
          <span className="text-error/80">{t.grade_blocking.join(' · ')}</span>
        </div>
      )}
      {/* Hero summary — identical to the standalone opportunity card */}
      <OpportunitySummary opp={t} />

      {t.legs && t.legs.length > 0 && (
        <CollapsibleSection title="Trade Legs" accent="base-content"
          icon={<Layers className="w-3 h-3" />} subtitle={`${t.legs.length} legs`}>
          <LegsTable legs={t.legs} />
        </CollapsibleSection>
      )}

      <CollapsibleSection title="Dynamic Greeks" accent="info"
        icon={<Activity className="w-3 h-3" />} subtitle="Δ · Γ · ν · Θ · Vanna · Charm · Volga">
        <TraderGrid t={dm.trader} />
      </CollapsibleSection>

      <CollapsibleSection title="Capital Risk" accent="warning"
        icon={<Shield className="w-3 h-3" />} subtitle="VaR · CVaR · max loss · sizing">
        <RiskGrid r={dm.risk} />
      </CollapsibleSection>

      <CollapsibleSection title="Risk Adjusted Quality" accent="success"
        icon={<LineChart className="w-3 h-3" />} subtitle="Omega · Sortino · Calmar · PoP · EV · Kelly">
        <PmGrid pm={dm.pm} />
      </CollapsibleSection>

      <QuantAnalysisSection t={t} q={q} />

      {/* Run Institutional Desk — the LLM debate on THIS trade, in the boardroom modal. Right-aligned,
          directly after the Quant Analysis section. */}
      <div className="flex justify-end pt-1">
        <button className="btn btn-secondary btn-sm gap-1.5" onClick={runDesk} disabled={debateLoading}>
          {debateLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
          Run Institutional Desk
        </button>
      </div>

      <DeskDebateModal
        open={debateOpen}
        onClose={() => setDebateOpen(false)}
        loading={debateLoading}
        error={debateErr}
        agents={agents}
        tradeLabel={t.label}
        onRerun={runDesk}
      />
    </div>
  );
}

function AgentCard({ agent, icon }: { agent: DeskAgent; icon: React.ReactNode }) {
  const [dbg, setDbg] = useState(false);
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-bold flex items-center gap-1.5">{icon}{agent.title}</span>
        <span className={`text-[11px] font-bold rounded-lg border px-2 py-0.5 ${verdictTone(agent.verdict)}`}>{agent.verdict || '—'}</span>
      </div>
      <div className="text-xs text-base-content/75 whitespace-pre-wrap mt-1.5 leading-snug">{agent.content}</div>
      <button type="button" onClick={() => setDbg(v => !v)}
        className="btn btn-ghost btn-xs gap-1 mt-1 text-base-content/40">
        <Terminal className="w-3 h-3" /> Inputs &amp; prompt {dbg ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
      </button>
      {dbg && (
        <div className="mt-1 space-y-2 text-[10px] font-mono text-base-content/50 bg-base-300/30 rounded-lg p-2 max-h-72 overflow-auto">
          <div><span className="text-base-content/40 uppercase">System prompt</span><pre className="whitespace-pre-wrap">{agent.system_prompt}</pre></div>
          <div><span className="text-base-content/40 uppercase">Input context ({agent.model})</span><pre className="whitespace-pre-wrap">{agent.input_context}</pre></div>
        </div>
      )}
    </div>
  );
}

// The PM's output IS the desk decision — shown ONCE, highlighted (no separate summary card).
function FinalDecisionCard({ pm, fr, onRerun, renderExplore }: {
  pm: DeskAgent; fr: DeskAgentsResult['final_recommendation']; onRerun: () => void;
  renderExplore?: () => React.ReactNode;   // the desk's recommended trade, rendered on demand
}) {
  const [dbg, setDbg] = useState(false);
  const [showExp, setShowExp] = useState(false);
  const body = pm.content.split('\n')
    .filter(l => { const u = l.trim().toUpperCase(); return !u.startsWith('VERDICT') && !u.startsWith('FINAL_DECISION'); })
    .join('\n').trim();
  return (
    <div className={`rounded-xl border p-4 ${verdictTone(pm.verdict)}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-bold flex items-center gap-1.5"><Briefcase className="w-4 h-4" /> Desk decision — {pm.title}</span>
        <span className={`text-xs font-bold rounded-lg border px-2 py-0.5 ${verdictTone(pm.verdict)}`}>{pm.verdict || '—'}</span>
      </div>
      <div className="text-xs text-base-content/80 whitespace-pre-wrap mt-1.5 leading-snug">{body}</div>
      {(fr.sizing || fr.desk_mandate) && (
        <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
          {fr.sizing && (
            <div className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2">
              <p className="text-[10px] uppercase tracking-wide text-base-content/40">Sizing</p>
              <p className="text-xs font-semibold">{fr.sizing}</p>
            </div>
          )}
          {fr.desk_mandate && (
            <div className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2">
              <p className="text-[10px] uppercase tracking-wide text-base-content/40">Desk mandate</p>
              <p className="text-xs">{fr.desk_mandate}</p>
            </div>
          )}
        </div>
      )}
      <div className="mt-2 text-[11px] text-base-content/60 space-y-0.5 border-t border-white/[0.06] pt-2">
        {fr.consistency && <p>Consistency check: <span className="italic">{fr.consistency}</span></p>}
        {fr.winning_argument && <p>Winning argument: <span className="italic">{fr.winning_argument}</span></p>}
        <p>Algorithm's #1: <span className="font-mono text-base-content/50">{fr.algo_top_pick || '—'}</span></p>
        <p>Debate: Quant → <span className="font-mono text-base-content/70">{fr.quant_choice || '—'}</span>
          {fr.quant_agrees_with_algo && <span className="italic opacity-70"> ({fr.quant_agrees_with_algo})</span>}
          {' · '}Risk → {fr.risk_verdict || '—'}{' · '}Rebuttal → {fr.rebuttal_stance || '—'}
          {fr.final_pick && <> · Desk pick → <span className="font-mono text-base-content/70">{fr.final_pick}</span></>}
        </p>
      </div>
      <div className="flex items-center gap-2 mt-2 flex-wrap">
        {renderExplore && (
          <button type="button" className="btn btn-secondary btn-xs gap-1" onClick={() => setShowExp(v => !v)}>
            <Maximize2 className="w-3 h-3" /> {showExp ? 'Hide' : 'Explore'} recommended trade
          </button>
        )}
        <button type="button" className="btn btn-ghost btn-xs gap-1 text-base-content/40" onClick={() => setDbg(v => !v)}>
          <Terminal className="w-3 h-3" /> Inputs &amp; prompt {dbg ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
        <button type="button" className="btn btn-ghost btn-xs" onClick={onRerun}>Re-run the desk</button>
      </div>
      {showExp && renderExplore && (
        <div className="mt-2 rounded-xl border border-secondary/30 bg-base-100/50 p-3">{renderExplore()}</div>
      )}
      {dbg && (
        <div className="mt-1 space-y-2 text-[10px] font-mono text-base-content/50 bg-base-300/30 rounded-lg p-2 max-h-72 overflow-auto">
          <div><span className="text-base-content/40 uppercase">System prompt</span><pre className="whitespace-pre-wrap">{pm.system_prompt}</pre></div>
          <div><span className="text-base-content/40 uppercase">Input context ({pm.model})</span><pre className="whitespace-pre-wrap">{pm.input_context}</pre></div>
        </div>
      )}
    </div>
  );
}

// Desk Review v2 — a focused Quant → Risk → Rebuttal → PM debate on ONE selected trade, triggered
// per trade-structure (user action). Reuses the same agent cards as the ranking flow.
export function SingleTradeDeskReview({ ticker, params, trade }: {
  ticker: string;
  params: DeskReviewParams & { model?: string };
  trade: { structure: string; expiration?: string | null; short_strike?: number | null; label?: string };
}) {
  const [agents, setAgents] = useState<DeskAgentsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setErr(null);
    try {
      const a = await runDeskReviewAgents(ticker, {
        ...params,
        focus: { structure: trade.structure, expiration: trade.expiration ?? null, short_strike: trade.short_strike ?? null },
      });
      if (a.error) setErr(a.error); else setAgents(a);
    } catch (e: any) { setErr(e?.message || 'Desk review failed'); }
    finally { setLoading(false); }
  };

  return (
    <div className="mt-3 border-t border-white/[0.06] pt-3">
      {!agents && (
        <button className="btn btn-secondary btn-xs gap-1.5" onClick={run} disabled={loading}>
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Gauge className="w-3.5 h-3.5" />}
          {loading ? 'Running the desk debate…' : 'Desk Review'}
        </button>
      )}
      {loading && !agents && (
        <p className="text-[11px] text-base-content/40 mt-1">Quant → Risk → Rebuttal → PM debate THIS trade (~40–60s).</p>
      )}
      {err && <div className="alert alert-error text-xs mt-1"><AlertTriangle className="w-4 h-4" /><span>{err}</span></div>}
      {agents && (
        <div className="space-y-3">
          <p className="text-[11px] text-base-content/40 flex items-center gap-1.5">
            <MessageSquare className="w-3.5 h-3.5" /> Desk debate on {trade.label || trade.structure} — Quant → Risk → Rebuttal → PM decides.
          </p>
          <AgentCard agent={agents.quant} icon={<Cpu className="w-4 h-4 text-secondary" />} />
          <AgentCard agent={agents.risk} icon={<Shield className="w-4 h-4 text-secondary" />} />
          <AgentCard agent={agents.rebuttal} icon={<MessageSquare className="w-4 h-4 text-secondary" />} />
          <FinalDecisionCard pm={agents.pm} fr={agents.final_recommendation} onRerun={() => setAgents(null)} />
        </div>
      )}
    </div>
  );
}

export function DeskReview({ ticker, params, renderTrade, renderDebate, data, evaluate }: {
  ticker: string;
  params: DeskReviewParams;
  renderTrade?: (trade: DeskRankedTrade) => React.ReactNode;
  // When provided, the LLM debate renders through this instead of the inline agent
  // cards (v2 routes it into a slide-over). Omitted → original inline behavior.
  renderDebate?: (args: { agents: DeskAgentsResult; rerun: () => void; renderExplore?: () => React.ReactNode }) => React.ReactNode;
  // PRESENTATIONAL mode: when the parent already fetched the desk payload (single-ticker one-call
  // flow), pass it here and the component renders it directly — no own /desk-review fetch.
  data?: DeskReviewResult;
  // EVALUATE mode: the user's bring-your-own trade legs, so each row's LLM debate re-evaluates
  // THAT exact trade (custom/calendar trades can't be rebuilt via the focus selector).
  evaluate?: DeskEvaluateParams;
}) {
  const controlled = data !== undefined;
  const [rev, setRev] = useState<DeskReviewResult | null>(data ?? null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const [agents, setAgents] = useState<DeskAgentsResult | null>(null);
  const [agentsLoading, setAgentsLoading] = useState(false);
  const [agentsErr, setAgentsErr] = useState<string | null>(null);

  const runReview = async () => {
    setLoading(true); setErr(null);
    try {
      const r = await runDeskReview(ticker, params);
      if (r.error) setErr(r.error); else setRev(r);
    } catch (e: any) { setErr(e?.message || 'Desk review failed'); }
    finally { setLoading(false); }
  };

  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    // Controlled (single-ticker one-call flow): the parent's /desk-review payload IS the ranking —
    // adopt it, no fetch. Reset the debate/expansion when it changes.
    if (controlled) {
      setRev(data ?? null); setErr(null); setLoading(false);
      setAgents(null); setAgentsErr(null); setExpanded(null);
      return;
    }
    // Uncontrolled (v2 desk tab): the RANKING is algorithmic (no LLM cost), so auto-load it with the
    // scan — the table just appears, no extra click. The Quant·Risk·PM debate stays user-triggered.
    setRev(null); setErr(null);
    setAgents(null); setAgentsErr(null); setExpanded(null);
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        const r = await runDeskReview(ticker, params);
        if (!alive) return;
        if (r.error) setErr(r.error); else setRev(r);
      } catch (e: any) { if (alive) setErr(e?.message || 'Desk review failed'); }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, paramsKey, data]);

  const askDesk = async () => {
    setAgentsLoading(true); setAgentsErr(null);
    try {
      const a = await runDeskReviewAgents(ticker, params);
      if (a.error) setAgentsErr(a.error); else setAgents(a);
    } catch (e: any) { setAgentsErr(e?.message || 'Desk agents failed'); }
    finally { setAgentsLoading(false); }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          {rev?.gex?.regime && (
            <span
              className={`text-[11px] px-2 py-1 rounded-lg border inline-flex items-center gap-1.5 ${
                rev.gex.regime === 'long'
                  ? 'border-success/30 bg-success/[0.08] text-success'
                  : 'border-error/30 bg-error/[0.08] text-error'}`}
              title={`Dealer gamma proxy from open interest × modelled gamma (${rev.gex.n_strikes ?? '—'} strikes). LONG gamma = dealers fade moves → volatility suppressed, mean-reverting → a good backdrop for selling premium. SHORT gamma = dealers chase moves → volatility expansion, trending → dangerous, and delta-neutral structures are vetoed.`}>
              <Zap className="w-3.5 h-3.5" />
              Gamma: {rev.gex.regime === 'long' ? 'LONG · vol-suppressed' : 'SHORT · vol-expansion'}
              {rev.gex.flip_level != null && <span className="opacity-70">· flip ${rev.gex.flip_level}</span>}
              <span className="opacity-50 text-[9px]">proxy</span>
            </span>
          )}
          {rev?.ta_timeframe && (
            <span className="text-[11px] px-2 py-1 rounded-lg border border-white/[0.08] bg-base-200/50 text-base-content/60 inline-flex items-center gap-1.5"
              title="The technical read that scores every trade — a swing horizon (daily bars, ~6 months of context) matched to a multi-week option. Short/intraday would be noise; long/weekly would lag the trade.">
              <Activity className="w-3.5 h-3.5" /> TA · {rev.ta_timeframe}
            </span>
          )}
        </div>
        <RatingsHelpButton />
      </div>

      {rev?.data_source_note && (
        <div className="rounded-lg border border-warning/30 bg-warning/[0.08] px-3 py-2 text-[11px] text-warning flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" /> {rev.data_source_note}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-xs text-base-content/50 py-6 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> Scoring &amp; ranking every trade…
        </div>
      )}
      {err && (
        <div className="alert alert-warning text-xs">
          <AlertTriangle className="w-4 h-4" /><span className="flex-1">{err}</span>
          <button className="btn btn-ghost btn-xs gap-1" onClick={runReview}><Gauge className="w-3.5 h-3.5" /> Retry</button>
        </div>
      )}

      {rev && rev.ranked.length > 0 && (() => {
        const top = rev.ranked[0];
        const ts = rev.ta_summary;
        return (
          <div className="space-y-3">
            {/* Algo's best pick */}
            <div className="rounded-xl border border-success/25 bg-success/[0.06] p-3">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="flex items-center gap-1.5 text-sm font-bold text-success">
                  <Trophy className="w-4 h-4" /> Algorithmic pick: {top.label}
                </span>
                <span className={`inline-flex items-baseline gap-1 rounded-lg border px-2 py-0.5 text-lg font-bold ${gradeTone(top.algo_grade)}`}
                  title="Desk grade — overall trade quality (A best → F worst)">
                  {top.algo_grade || '—'}<span className="text-[10px] font-medium opacity-60">grade</span>
                </span>
              </div>
              {top.grade_demerits && top.grade_demerits.length > 0 && (
                <p className="text-[10px] text-base-content/45 mt-1">Watch-outs: {top.grade_demerits.join(' · ')}</p>
              )}
              <p className="text-xs text-base-content/60 mt-0.5">
                {strikeStr(top)}{top.short_strike_pct != null && ` (${top.short_strike_pct >= 0 ? '+' : ''}${top.short_strike_pct}%)`} · {top.dte}d · exp {top.expiration}
                {' — '}Win {winPct(top.prob_keep_pct)} · prem {money(top.premium)} · Omega {ratio(top.desk_metrics.pm.omega)} · CVaR95 {money(top.desk_metrics.risk.cvar_95)}
              </p>
              <p className="text-[11px] text-base-content/50 mt-1">
                Market: <b>{ts.state}</b> ({ts.bias}) · RSI {ts.rsi != null ? ts.rsi.toFixed(0) : '—'} · POC {money(ts.poc)} · trend {ts.trend}
              </p>
              <button className="btn btn-secondary btn-xs gap-1 mt-2" onClick={() => setExpanded(expanded === 0 ? null : 0)}>
                <Maximize2 className="w-3 h-3" /> {expanded === 0 ? 'Hide analysis' : 'Explore this trade'}
              </button>
            </div>

            {/* Events are already shown once at the top of the scan (collapsible) — not repeated here. */}

            {/* Full ranked table */}
            <div className="overflow-x-auto">
              <table className="table table-xs w-full">
                <thead>
                  <tr className="text-base-content/50">
                    <th>#</th>
                    <th title="Desk grade — overall trade quality. A/B = desk would likely approve · C/D = weak · F/V = flawed or vetoed">Grade</th>
                    <th>Structure</th><th>Strike</th>
                    <th title="Net premium collected per contract">Premium</th>
                    <th title="Chance the option you sold expires worthless — you keep the premium, not assigned (capped at 99.9%)">Win %</th>
                    <th title="Execution — pricing reliability &amp; how easily you can fill (liquidity / spread / model)">Exe.</th>
                    <th title="Annualized return on capital (premium yield, annualized)">Ret. %</th>
                    <th title="Net directional exposure (shares-equivalent)">net Δ</th>
                    <th title="Theta — premium decay collected per day">Θ/d</th>
                  </tr>
                </thead>
                <tbody>
                  {rev.ranked.map((t, i) => {
                    const dm = t.desk_metrics;
                    const open = expanded === i;
                    const vetoed = (t.grade_blocking || []).length > 0;
                    const vetoReason = (t.grade_blocking || []).join('; ');
                    return (
                      <React.Fragment key={i}>
                        <tr onClick={() => setExpanded(open ? null : i)}
                          title={vetoed ? `VETOED — ${vetoReason}` : (open ? 'Collapse' : 'Click to explore this trade')}
                          className={`cursor-pointer transition-colors ${open ? 'bg-secondary/[0.12]' : vetoed ? 'opacity-40 hover:opacity-70' : i === 0 ? 'bg-success/5 hover:bg-success/10' : 'hover:bg-secondary/[0.06]'}`}>
                          <td className="font-bold">
                            <span className="inline-flex items-center gap-1">
                              {open ? <ChevronUp className="w-3.5 h-3.5 text-secondary" /> : <ChevronDown className="w-3.5 h-3.5 text-secondary/60" />}
                              {i + 1}
                            </span>
                          </td>
                          <td>
                            {vetoed
                              ? <span className="text-sm font-bold text-error border border-error/50 rounded px-1" title={`VETOED — ${vetoReason}`}>V</span>
                              : <span className={`text-sm font-bold ${gradeTextTone(t.algo_grade)}`}
                                  title={`grade ${t.algo_grade || '—'}${(t.grade_demerits || []).length ? ' — ' + (t.grade_demerits || []).join('; ') : ''}`}>
                                  {t.algo_grade || '—'}</span>}
                          </td>
                          <td className="whitespace-nowrap">{t.label}</td>
                          <td className="font-mono text-[11px]">{strikeStr(t)}</td>
                          <td className="text-success whitespace-nowrap">{money(t.premium)}</td>
                          <td>{winPct(t.prob_keep_pct)}</td>
                          <td className={confTextTone(t.confidence?.label)}>{t.confidence?.label ?? '—'}</td>
                          <td title="annualized">{annShort(t.premium_annualized_pct)}</td>
                          <td className="font-mono">{dm.trader.net_delta ?? '—'}</td>
                          <td className="font-mono">{money(dm.trader.net_theta, 0)}</td>
                        </tr>
                        {open && (
                          <tr className="bg-secondary/[0.05]">
                            <td colSpan={10} className="!p-0">
                              <div className="m-2 rounded-lg border border-secondary/30 bg-base-100/40 overflow-hidden">
                                <TradeExplorer t={t} ticker={ticker} params={params} evaluate={evaluate} />
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>


            {/* Ask the Desk — GLOBAL debate across ALL trades (kept for the V2 desk tab, which supplies
                renderDebate). The main flow now runs the Institutional Desk PER TRADE, from each row's
                Explore panel, so here we just point users to it. */}
            {renderDebate ? (
              <>
                {!agents && (
                  <button className="btn btn-secondary btn-sm gap-2" onClick={askDesk} disabled={agentsLoading}>
                    {agentsLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                    {agentsLoading ? 'Running the desk debate…' : 'Ask the Desk (run the debate)'}
                  </button>
                )}
                {agentsLoading && !agents && (
                  <p className="text-[11px] text-base-content/40">Quant proposes → Risk challenges → Quant rebuts → PM decides, over all {rev.n_trades} trades (~40–60s).</p>
                )}
                {agentsErr && <div className="alert alert-error text-xs"><AlertTriangle className="w-4 h-4" /><span>{agentsErr}</span></div>}
              </>
            ) : null}

            {agents && (() => {
              const ci = agents.final_recommendation.chosen_index;
              const finalTrade = (renderTrade && ci != null && ci >= 0 && ci < rev.ranked.length) ? rev.ranked[ci] : null;
              const renderExplore = finalTrade ? () => renderTrade!(finalTrade) : undefined;
              if (renderDebate) return renderDebate({ agents, rerun: () => setAgents(null), renderExplore });
              return (
                <div className="space-y-3">
                  <p className="text-[11px] text-base-content/40 flex items-center gap-1.5">
                    <MessageSquare className="w-3.5 h-3.5" /> Desk debate — Quant proposes, Risk challenges, Quant rebuts, PM decides.
                  </p>
                  <AgentCard agent={agents.quant} icon={<Cpu className="w-4 h-4 text-secondary" />} />
                  <AgentCard agent={agents.risk} icon={<Shield className="w-4 h-4 text-secondary" />} />
                  <AgentCard agent={agents.rebuttal} icon={<MessageSquare className="w-4 h-4 text-secondary" />} />
                  <FinalDecisionCard
                    pm={agents.pm} fr={agents.final_recommendation}
                    onRerun={() => setAgents(null)}
                    renderExplore={renderExplore}
                  />
                </div>
              );
            })()}
          </div>
        );
      })()}

      {rev && rev.ranked.length === 0 && (
        <div className="alert alert-warning text-xs">
          <AlertTriangle className="w-4 h-4" />
          <span>{rev.note || 'No candidate trades to review under these filters.'}</span>
        </div>
      )}
    </div>
  );
}
