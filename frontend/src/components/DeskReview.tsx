import React, { useEffect, useState } from 'react';
import {
  Gauge, Loader2, AlertTriangle, Cpu, Shield, Briefcase, ChevronDown, ChevronUp,
  Trophy, Play, Terminal, Maximize2, X, MessageSquare, Activity, LineChart, Layers, Zap,
} from 'lucide-react';
import { runDeskReview, runDeskReviewAgents } from '../api';
import { RatingsHelpButton } from './RatingsHelp';
import { OpportunitySummary, LegsTable } from './DerivativeIncome';
import CollapsibleSection from './trades/CollapsibleSection';
import { TraderGrid, PmGrid, RiskGrid } from './trades/DeskMetrics';
import type { DeskReviewParams } from '../api';
import type { DeskReviewResult, DeskRankedTrade, DeskAgentsResult, DeskAgent } from '../types';

const money = (n: number | null | undefined, d = 0) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const pct = (n: number | null | undefined, d = 1) => (n == null ? '—' : `${n.toFixed(d)}%`);
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

// A 0–100 subscore bar (base-quality terms: Edge/PoP/Sortino/Tail/Carry).
function SubBar({ label, v }: { label: string; v: number }) {
  const col = v >= 66 ? 'bg-success' : v >= 45 ? 'bg-warning' : 'bg-error';
  return (
    <div className="flex-1 min-w-[52px]">
      <div className="flex justify-between text-[9px] text-base-content/40 mb-0.5"><span>{label}</span><span>{v}</span></div>
      <div className="h-1 rounded bg-base-300/40 overflow-hidden"><div className={`h-full ${col}`} style={{ width: `${Math.max(2, Math.min(100, v))}%` }} /></div>
    </div>
  );
}

// A SIGNED adjustment bar centred at zero: green extends right (+), red left (−).
function AdjBar({ label, v }: { label: string; v: number }) {
  const MAX = 12;                                   // largest single-factor magnitude
  const mag = (Math.min(Math.abs(v), MAX) / MAX) * 50;
  const pos = v >= 0;
  return (
    <div className="flex-1 min-w-[64px]">
      <div className="flex justify-between text-[9px] text-base-content/40 mb-0.5">
        <span>{label}</span>
        <span className={v > 0 ? 'text-success' : v < 0 ? 'text-error' : 'text-base-content/30'}>{v > 0 ? '+' : ''}{v}</span>
      </div>
      <div className="relative h-1 rounded bg-base-300/40 overflow-hidden">
        <div className="absolute left-1/2 top-0 h-full w-px bg-base-content/25" />
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
function QpBoundary({ qp }: { qp: NonNullable<DeskRankedTrade['qp']> }) {
  const imp = qp.implied_move_pct ?? 0;
  const phys = qp.physical_move_pct ?? 0;
  const dist = qp.short_dist_pct ?? 0;
  const span = Math.max(imp, phys, dist, 1) * 1.2;
  const half = (v: number) => (v / span) * 50;          // % of the half-width
  const exposed = !!qp.exposed_physical;
  const ratio = qp.iv_hv_ratio;
  return (
    <div className="mb-2.5">
      <div className={GROUP_LABEL}>VRP — implied (Q) vs realized (P) boundary</div>
      <div className="relative h-9 rounded bg-base-300/30 overflow-hidden">
        {phys > 0 && <div className="absolute inset-y-0 bg-warning/20 border-x border-warning/40"
          style={{ left: `${50 - half(phys)}%`, width: `${2 * half(phys)}%` }} title={`physical ±${phys}%`} />}
        {imp > 0 && <div className="absolute inset-y-0 bg-info/25 border-x border-info/50"
          style={{ left: `${50 - half(imp)}%`, width: `${2 * half(imp)}%` }} title={`implied ±${imp}%`} />}
        <div className="absolute inset-y-0 left-1/2 w-px bg-base-content/50" />
        <div className="absolute top-0.5 left-1/2 -translate-x-1/2 text-[8px] text-base-content/50">spot</div>
        {dist > 0 && (
          <div className="absolute bottom-0.5 -translate-x-1/2 text-[10px] leading-none"
            style={{ left: `${Math.max(2, 50 - half(dist))}%` }} title={`short strike ${dist}% away`}>
            <span className={exposed ? 'text-error' : 'text-success'}>▲</span>
          </div>
        )}
      </div>
      <div className="flex flex-wrap justify-between gap-x-2 text-[9px] mt-1">
        <span className="text-info">implied Q ±{imp}%</span>
        <span className="text-warning">physical P ±{phys}%</span>
        <span className={exposed ? 'text-error font-semibold' : 'text-success'}>
          short {dist}% {exposed ? '· inside physical → exposed' : '· clears both'}
        </span>
      </div>
      {(qp.implied_vol_pct != null || ratio != null) && (
        <div className="text-[10px] text-base-content/55 mt-0.5">
          IV {qp.implied_vol_pct}% vs HV {qp.realized_vol_pct}%
          {ratio != null && <> · VRP <b className={ratio >= 1 ? 'text-success' : 'text-error'}>{ratio >= 1 ? '+' : ''}{Math.round((ratio - 1) * 100)}%</b>
            {ratio < 1 && imp > 0 ? ` · physical ${(phys / imp).toFixed(1)}× wider (premium under-priced)` : ''}</>}
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

function TradeExplorer({ t }: { t: DeskRankedTrade }) {
  const dm = t.desk_metrics;
  const q = dm.quant || {};
  const sub = q.subscores;
  const base = Math.round(t.base_quality ?? q.score ?? 0);
  const adjs = t.grade_adjustments || [];
  const tas = t.ta_factors || [];
  const r1 = (n: number) => Math.round(n * 10) / 10;
  const adjNet = r1(adjs.reduce((s, a) => s + a.points, 0));
  const taNet = r1(tas.reduce((s, a) => s + a.points, 0));
  const sgn = (n: number) => `${n > 0 ? '+' : ''}${n}`;
  const rawSum = r1(base + adjNet + taNet);        // the true arithmetic sum (pre-clamp)
  const clamped = rawSum !== t.desk_score;         // desk_score is clamped to [0, 100]
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

      <CollapsibleSection title="Quant Analysis" accent="secondary"
        icon={<Cpu className="w-3 h-3" />} subtitle="base + factor + TA → desk score">
        {/* Top: the TOTAL desk score (not the base) */}
        <div className={`flex items-center gap-2 rounded-lg border p-2 mb-2.5 ${gradeTone(t.algo_grade)}`}>
          <span className="text-sm font-bold uppercase tracking-wider">{q.verdict || 'SCORE'}</span>
          <span className="text-xs text-base-content/50">desk score · grade {t.algo_grade || '—'}</span>
          <span className="ml-auto text-lg font-bold">{t.desk_score}<span className="text-[10px] text-base-content/40">/100</span></span>
        </div>

        {/* Q-vs-P boundary — the implied-vs-physical read the base score is now weighted on */}
        {t.qp && <QpBoundary qp={t.qp} />}

        {/* 1) Base quality — bars, then the base score at the end */}
        {sub && (
          <div className="mb-2.5">
            <div className={GROUP_LABEL}>Base quality — payoff distribution</div>
            <div className="flex flex-wrap gap-2">
              <SubBar label="Edge" v={sub.edge} />
              <SubBar label="PoP" v={sub.pop} />
              <SubBar label="Sortino" v={sub.sortino} />
              <SubBar label="Tail" v={sub.tail} />
              <SubBar label="Carry" v={sub.carry} />
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

        {((t.grade_merits && t.grade_merits.length > 0) || (t.grade_demerits && t.grade_demerits.length > 0)) && (
          <ul className="text-[11px] space-y-0.5 mt-1.5">
            {(t.grade_merits || []).map((r, i) => <li key={`m${i}`} className="flex gap-1.5 text-success/80"><span className="opacity-50">+</span>{r}</li>)}
            {(t.grade_demerits || []).map((r, i) => <li key={`d${i}`} className="flex gap-1.5 text-warning/80"><span className="opacity-50">−</span>{r}</li>)}
          </ul>
        )}
      </CollapsibleSection>
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

export function DeskReview({ ticker, params, renderTrade, renderDebate }: {
  ticker: string;
  params: DeskReviewParams;
  renderTrade?: (trade: DeskRankedTrade) => React.ReactNode;
  // When provided, the LLM debate renders through this instead of the inline agent
  // cards (v2 routes it into a slide-over). Omitted → original inline behavior.
  renderDebate?: (args: { agents: DeskAgentsResult; rerun: () => void; renderExplore?: () => React.ReactNode }) => React.ReactNode;
}) {
  const [rev, setRev] = useState<DeskReviewResult | null>(null);
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
    // The RANKING is algorithmic (no LLM cost), so auto-load it with the scan — the table just
    // appears, no extra click. The Quant·Risk·PM debate ("Ask the Desk") stays user-triggered.
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
  }, [ticker, paramsKey]);

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
        {rev?.gex?.regime ? (
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
        ) : <span />}
        <RatingsHelpButton />
      </div>

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
                  {top.approval_odds && (
                    <span className={`text-[10px] font-bold rounded border px-1.5 py-0.5 ${gradeTone(top.algo_grade)}`}>
                      {top.approval_odds} approval odds
                    </span>
                  )}
                </span>
                <span className={`text-lg font-bold ${gradeTextTone(top.algo_grade)}`} title="0–100 desk score (color = grade band)">{top.desk_score}<span className="text-xs opacity-50">/100</span></span>
              </div>
              {top.grade_demerits && top.grade_demerits.length > 0 && (
                <p className="text-[10px] text-base-content/45 mt-1">Watch-outs: {top.grade_demerits.join(' · ')}</p>
              )}
              <p className="text-xs text-base-content/60 mt-0.5">
                {strikeStr(top)}{top.short_strike_pct != null && ` (${top.short_strike_pct >= 0 ? '+' : ''}${top.short_strike_pct}%)`} · {top.dte}d · exp {top.expiration}
                {' — '}Win {pct(top.prob_keep_pct)} · prem {money(top.premium)} · Omega {ratio(top.desk_metrics.pm.omega)} · CVaR95 {money(top.desk_metrics.risk.cvar_95)}
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
                    <th title="Overall quality 0–100; color = grade band (green A/B · amber C/D · red F)">Score</th>
                    <th>Structure</th><th>Strike</th>
                    <th title="Net premium collected per contract">Premium</th>
                    <th title="Chance the option you sold expires worthless — you keep the premium, not assigned">Win %</th>
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
                                  title={`grade ${t.algo_grade || '—'} · ${t.approval_odds || ''} approval odds${(t.grade_demerits || []).length ? ' — ' + (t.grade_demerits || []).join('; ') : ''}`}>
                                  {t.desk_score}</span>}
                          </td>
                          <td className="whitespace-nowrap">{t.label}</td>
                          <td className="font-mono text-[11px]">{strikeStr(t)}</td>
                          <td className="text-success whitespace-nowrap">{money(t.premium)}</td>
                          <td>{pct(t.prob_keep_pct)}</td>
                          <td className={confTextTone(t.confidence?.label)}>{t.confidence?.label ?? '—'}</td>
                          <td title="annualized">{annShort(t.premium_annualized_pct)}</td>
                          <td className="font-mono">{dm.trader.net_delta ?? '—'}</td>
                          <td className="font-mono">{money(dm.trader.net_theta, 0)}</td>
                        </tr>
                        {open && (
                          <tr className="bg-secondary/[0.05]">
                            <td colSpan={10} className="!p-0">
                              <div className="m-2 rounded-lg border border-secondary/30 bg-base-100/40 overflow-hidden">
                                <TradeExplorer t={t} />
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


            {/* Ask the Desk (LLM cascade) */}
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
