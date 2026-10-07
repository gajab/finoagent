/**
 * BetaDefend — Beta layout for Manage ▸ Defend & repair (the trouble-trade desk).
 *
 * Same flow and endpoints as the classic DefendPanel — phase 1 GET /repair-menu paints immediately, phase 2
 * POST /defend/refine (the live credit-only roll search) is merged into the SAME ranked menu and the
 * recommendation is held back until it lands (so it never changes under you), and the LLM war room runs only on
 * an explicit click. What changes is the reading order and the look:
 *
 *   header        → where the trade stands: posture, spot, cushion, days left, mark if closed
 *   recommendation→ the ONE desk pick: score ring, why, runner-up, what to avoid
 *   next best moves → every priced option as a compact ranked row (tap for payoff, breakdown and rationale)
 *   risk read     → recoverability as tiles + the drivers that build it
 *   folded        → assignment · cost of waiting · what broke · synthetic reframe · war room
 */
import React, { useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Loader2, AlertTriangle, ShieldAlert, Wrench, Activity, Clock, Users, ArrowUpRight, TrendingDown, TrendingUp, Layers, Check, X, Minus,
  Target, Star, ShieldCheck, Infinity as InfinityIcon, DollarSign, BarChart3, Thermometer, Ruler, Compass, CalendarDays,
} from 'lucide-react';
import { fetchDefendMenu, fetchDefendCommittee, fetchDefendRefine } from '../api';
import type { RepairMenuResult, RepairAlternative, DefendCommittee, DefendFactor } from '../api';
import { money, rank, CAT, PayoffCurve, legStr } from '../components/trades/RepairMenu';
import { Accordion, Callout, Chip, Meter, ScoreRing, SubHead, Tile, TONE, type Tone } from './BetaKit';

const num = (v: number | null | undefined, d = 0) => (v == null || !isFinite(v) ? '—' : v.toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (v: number | null | undefined, d = 0) => (v == null || !isFinite(v) ? '—' : `${v.toFixed(d)}%`);

const SEV: Record<string, { label: string; tone: Tone }> = {
  healthy: { label: 'Healthy', tone: 'good' }, fresh: { label: 'Fresh breach', tone: 'warn' }, deep: { label: 'Deep ITM', tone: 'bad' }, assigned: { label: 'Assignment-like', tone: 'bad' },
};
const TILT: Record<string, Tone> = { favorable: 'good', adverse: 'bad', balanced: 'neutral', watch: 'warn' };
const CAT_TONE: Record<string, Tone> = {
  exit: 'neutral', hold: 'neutral', roll: 'info', overlay: 'accent', defined_risk: 'good', calendar: 'accent', butterfly: 'accent', ratio: 'accent', cover: 'info', hedge: 'warn', assignment: 'accent',
};

function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: string }) {
  return <div className="min-w-0"><div className="text-[11px] text-base-content/45">{label}</div><div className={`text-xs font-semibold tabular-nums truncate ${tone || ''}`}>{value}</div></div>;
}

function Bar({ label, v }: { label: string; v: number }) {
  const tone: Tone = v >= 66 ? 'good' : v >= 45 ? 'warn' : 'bad';
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] text-base-content/55 w-20 shrink-0">{label}</span>
      <Meter value={v} tone={tone} className="flex-1" />
      <span className="text-[11px] tabular-nums text-base-content/60 w-6 text-right">{Math.round(v)}</span>
    </div>
  );
}

// ── header ───────────────────────────────────────────────────────────────────
function Header({ d }: { d: RepairMenuResult }) {
  const r = d.recoverability;
  const posture = r?.posture;
  const sev = SEV[r?.severity || 'fresh'] || SEV.fresh;
  const pb: { label: string; tone: Tone } = posture === 'marginal' ? { label: 'Marginal', tone: 'warn' } : posture === 'healthy' ? { label: 'Healthy', tone: 'good' } : sev;
  const t = TONE[pb.tone];
  return (
    <div className={`rounded-2xl border p-4 ${t.border} ${t.soft}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={pb.tone} title={posture === 'marginal' ? 'Not tested, but high breach probability / under-priced premium / adverse trend — de-risk' : undefined}>{pb.label}</Chip>
        {d.structure && <Chip>{d.structure.replace(/_/g, ' ')}</Chip>}
        {d.covered && <Chip tone="info" title="Shares cover the short call — the risk is being called away (opportunity cost), not an unbounded loss">covered</Chip>}
        <span className="text-sm text-base-content/70 ml-1">{d.ticker} · {posture === 'healthy' || posture === 'marginal' ? 'short' : 'tested'} {d.short_right === 'P' ? 'put' : 'call'} ${num(d.short_strike, 0)}</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
        <Tile label="Spot" value={`$${num(d.spot, 2)}`} />
        <Tile label="Cushion" value={d.cushion_pct == null ? '—' : `${num(d.cushion_pct, 1)}%`} sub="to the short strike" />
        <Tile label="Days left" value={d.dte_days == null ? '—' : `${d.dte_days}d`} />
        <Tile label="Mark if closed" value={money(d.unrealized_pnl)} tone={(d.unrealized_pnl ?? 0) >= 0 ? 'good' : 'bad'} />
      </div>
    </div>
  );
}

// ── recommendation ───────────────────────────────────────────────────────────
function Refining() {
  return (
    <div className="rounded-2xl border border-secondary/30 bg-secondary/[0.05] p-4 space-y-2" aria-live="polite">
      <div className="flex items-center gap-2 text-sm font-semibold"><Loader2 className="w-4 h-4 animate-spin text-secondary" /> Building the desk recommendation</div>
      <p className="text-xs text-base-content/55 leading-relaxed">
        Searching credit-only rolls across expiries — market-implied probability, support/resistance, gamma flip/wall and volume — and scoring them on the same axes as every other defense, so you get ONE ranked answer. The risk read below is ready now.
      </p>
      <div className="h-2 rounded-full bg-base-content/10 overflow-hidden"><div className="h-full w-1/3 rounded-full bg-secondary/60 animate-pulse" /></div>
    </div>
  );
}

function Recommendation({ d, onEvaluate }: { d: RepairMenuResult; onEvaluate: (a: RepairAlternative) => void }) {
  const rec = d.desk_recommendation;
  if (!rec) return null;
  const winner = (d.alternatives || []).find(a => a.name === rec.name);
  const actionable = !!winner && winner.category !== 'hold' && winner.category !== 'exit';
  const b = rec.score_breakdown;
  return (
    <div className="rounded-2xl border border-secondary/40 bg-secondary/[0.06] p-4 space-y-3">
      <div className="flex items-center gap-3">
        <ScoreRing value={rec.desk_score} tone="accent" label="Desk score" />
        <div className="min-w-0 flex-1">
          <div className="text-[11px] text-secondary/80 font-semibold flex items-center gap-1.5"><Target className="w-3.5 h-3.5" /> Desk recommendation</div>
          <div className="text-base font-semibold leading-snug mt-0.5">→ {rec.name}</div>
        </div>
      </div>
      {b && (
        <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1.5">
          <Bar label="Edge / $ risk" v={b.edge} /><Bar label="Risk shape" v={b.risk} /><Bar label="Recovery" v={b.recovery} /><Bar label="Market fit" v={b.market_fit} />
        </div>
      )}
      <ul className="space-y-1.5">
        {rec.reasons.map((r, i) => <li key={i} className="text-[13px] leading-snug flex gap-2"><Check className="w-3.5 h-3.5 text-secondary/70 mt-0.5 shrink-0" /><span>{r}</span></li>)}
      </ul>
      {rec.runner_up && (
        <div className="text-xs text-base-content/55 border-t border-white/[0.08] pt-2">
          {rec.runner_up.role === 'fix' ? 'To keep the trade alive:' : 'Runner-up:'} <b className="text-base-content/75">{rec.runner_up.name}</b> ({rec.runner_up.desk_score}/100){rec.runner_up.reasons.length > 0 ? ` — ${rec.runner_up.reasons.join('; ')}` : ''}
        </div>
      )}
      {rec.avoid.length > 0 && <div className="space-y-1.5">{rec.avoid.map((a, i) => <Callout key={i} tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}><b>Avoid:</b> {a.name} — {a.why}</Callout>)}</div>}
      <div className="flex items-center gap-2 flex-wrap">
        {actionable && winner && <button className="btn btn-secondary btn-sm gap-1.5" onClick={() => onEvaluate(winner)}>Evaluate this <ArrowUpRight className="w-3.5 h-3.5" /></button>}
      </div>
      <details className="text-xs text-base-content/45">
        <summary className="cursor-pointer select-none">How the pick is computed</summary>
        <p className="mt-1.5 leading-relaxed">Computed, not model-invented: 0.35 E[P&amp;L]-per-$-of-capital vs holding + 0.30 defined-risk size vs full assignment + 0.20 Δrecovery-odds + 0.15 fit with the market read (P(touch)/trend/VRP/pattern/IV structure — and, for a roll, how many support/resistance/POC/gamma levels its strike clears). Every option below, rolls from the live credit-only search included, is scored on these same axes. Points are then DEDUCTED for any leg that outlives the next earnings print (a binary jump the diffusion model doesn't price): −15 naked short call, −10 other naked short or a calendar's long far leg, −6 a wing-capped short.</p>
      </details>
    </div>
  );
}

// ── ranked moves ─────────────────────────────────────────────────────────────
function RollRead({ m }: { m: NonNullable<RepairAlternative['roll_meta']> }) {
  const cleared = Object.entries(m.structure || {}).filter(([k, v]) => k.startsWith('clears ') && v === true).map(([k]) => k.replace('clears ', ''));
  const total = Number(m.structure?.levels_available ?? 0);
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-base-content/60">
      {m.p_otm != null && <span title="Probability the NEW short finishes out of the money — from the market-implied distribution where the chain allows">P(OTM) <b className={m.p_otm >= 70 ? 'text-success' : 'text-warning'}>{m.p_otm}%</b> <span className="text-base-content/35">{m.p_otm_source}</span></span>}
      {m.scores?.cushion_sigma != null && <span title="Cushion in standard deviations over this expiry's horizon">cushion <b>{m.scores.cushion_sigma.toFixed(1)}σ</b> over {m.dte}d</span>}
      {total > 0 && <span title={cleared.length ? `Clears: ${cleared.join(' · ')}` : 'Clears no structural level'} className={cleared.length === total ? 'text-success' : ''}>clears <b>{cleared.length}/{total}</b> levels</span>}
    </div>
  );
}

function MoveRow({ a, no, best, spot, onEvaluate }: { a: RepairAlternative; no?: number; best: boolean; spot: number; onEvaluate?: (a: RepairAlternative) => void }) {
  const [open, setOpen] = useState(false);
  const bench = a.category === 'exit' || a.category === 'hold';
  const canEval = a.category !== 'exit' && (a.legs || []).some(l => l.right === 'P' || l.right === 'C');
  const xs = (a.scenarios || []).map(p => p.move_pct);
  return (
    <div className={`rounded-xl border ${best ? 'border-success/40 bg-success/[0.05]' : bench ? 'border-white/[0.08] border-dashed bg-base-100/10' : 'border-white/[0.08] bg-base-100/20'}`}>
      <button type="button" aria-expanded={open} onClick={() => setOpen(o => !o)} className="w-full text-left p-3 hover:bg-white/[0.02] rounded-xl transition-colors">
        <div className="flex items-start gap-3">
          <div className="h-6 w-6 shrink-0 rounded-full bg-base-content/10 flex items-center justify-center text-xs font-semibold">{no ?? '·'}</div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[13px] font-semibold">{a.name}</span>
              <Chip tone={CAT_TONE[a.category] || 'neutral'}>{CAT[a.category]?.label || a.category}</Chip>
              {best && <Chip tone="good"><Star className="w-3 h-3" /> Desk pick</Chip>}
            </div>
            {a.legs && a.legs.length > 0 && <div className="text-xs font-mono text-base-content/55 truncate mt-0.5" title={a.mechanics}>{a.legs.map(legStr).join('   ')}</div>}
          </div>
          <div className="text-right shrink-0">
            {a.desk_score != null && <div className="text-lg font-semibold tabular-nums text-secondary leading-none" title="Desk score — edge per $ of capital, risk shape, Δrecovery & market fit">{a.desk_score}</div>}
            {a.pop_pct != null && <div className="text-[11px] text-base-content/45 mt-0.5">PoP {a.pop_pct}%</div>}
          </div>
        </div>
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-x-3 gap-y-2 mt-3">
          <Stat label="Net cash" value={money(a.net_cash)} />
          <Stat label="Max loss" value={a.max_loss == null ? 'undefined' : money(a.max_loss)} tone="text-error" />
          <Stat label="Max gain" value={money(a.max_gain)} tone="text-success" />
          <Stat label="E[P&L]" value={a.ev != null ? money(a.ev) : '—'} tone={a.ev != null ? (a.ev >= 0 ? 'text-success' : 'text-error') : undefined} />
          <Stat label="Theta / day" value={money(a.theta_day)} tone={a.theta_day >= 0 ? 'text-success' : 'text-error'} />
          <Stat label="Δ recovery" value={a.d_pop != null && !bench ? `${a.d_pop >= 0 ? '+' : ''}${a.d_pop}%` : '—'} tone={a.d_pop != null && !bench ? (a.d_pop >= 0 ? 'text-success' : 'text-error') : undefined} />
        </div>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-3 space-y-3 border-t border-white/[0.06]">
          <div>
            <div className="rounded-lg border border-white/[0.06] bg-base-100/30 p-2"><PayoffCurve alt={a} spot={spot} /></div>
            {xs.length > 0 && <div className="flex justify-between text-[11px] text-base-content/40 mt-1"><span>{Math.min(...xs)}% move</span><span>now ${spot}</span><span>+{Math.max(...xs)}% move</span></div>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {a.category !== 'exit' && (a.defined_risk
              ? <Chip tone="good"><ShieldCheck className="w-3 h-3" /> defined risk</Chip>
              : <Chip tone="bad"><InfinityIcon className="w-3 h-3" /> open tail</Chip>)}
            {a.upside_risk_free && a.category !== 'exit' && <Chip tone="good">risk-free side</Chip>}
            {a.breakevens.length > 0 && <span className="text-xs text-base-content/55">Break-even {a.breakevens.map(b => `$${b}`).join(' · ')}</span>}
          </div>
          {a.score_breakdown && (
            <div className="grid sm:grid-cols-2 gap-x-6 gap-y-1.5">
              <Bar label="Edge / $ risk" v={a.score_breakdown.edge} /><Bar label="Risk shape" v={a.score_breakdown.risk} /><Bar label="Recovery" v={a.score_breakdown.recovery} /><Bar label="Market fit" v={a.score_breakdown.market_fit} />
            </div>
          )}
          <p className="text-xs text-base-content/60 leading-relaxed">{a.mechanics} — {a.rationale || a.risk_note}</p>
          {a.roll_meta && <RollRead m={a.roll_meta} />}
          {a.event_risk && <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}><b>Earnings:</b> {a.event_risk.note} <span className="opacity-70">(desk score already reduced by {a.event_risk.points} points)</span></Callout>}
          {canEval && onEvaluate && <button className="btn btn-outline btn-sm gap-1.5" onClick={() => onEvaluate(a)}>Evaluate in the Income Desk <ArrowUpRight className="w-3.5 h-3.5" /></button>}
        </div>
      )}
    </div>
  );
}

function Moves({ d, onEvaluate }: { d: RepairMenuResult; onEvaluate: (a: RepairAlternative) => void }) {
  const [showAll, setShowAll] = useState(false);
  const [showRedeploy, setShowRedeploy] = useState(false);
  const TOP = 4;
  const alts = d.alternatives || [];
  const priced = alts.filter(a => a.category !== 'exit' && a.category !== 'hold');
  const fixes = priced.filter(a => a.group !== 'replace').sort((x, y) => rank(y) - rank(x));
  const redeploys = priced.filter(a => a.group === 'replace').sort((x, y) => rank(y) - rank(x));
  const hold = alts.find(a => a.category === 'hold');
  const close = alts.find(a => a.category === 'exit');
  const bestName = d.desk_recommendation?.name || fixes[0]?.name;
  const shown = showAll ? fixes : fixes.slice(0, TOP);
  const hidden = fixes.length - shown.length;
  const rollCount = fixes.filter(a => a.roll_meta).length;
  const spot = d.spot ?? 0;
  return (
    <div className="space-y-2.5">
      <SubHead hint={`${fixes.length} options · keep the position · ranked by desk score${rollCount ? ` · ${rollCount} from the live roll search` : ''}`}>Next best moves</SubHead>
      {shown.map((a, i) => <MoveRow key={`f${i}`} a={a} no={i + 1} best={a.name === bestName} spot={spot} onEvaluate={onEvaluate} />)}
      {hidden > 0 && <button className="btn btn-ghost btn-sm w-full border border-white/10" onClick={() => setShowAll(true)}>Show {hidden} more ({rollCount > 0 ? 'credit rolls · ' : ''}calendars · butterflies · hedge · wheel…)</button>}
      {showAll && fixes.length > TOP && <button className="btn btn-ghost btn-sm w-full border border-white/10" onClick={() => setShowAll(false)}>Show fewer</button>}

      {redeploys.length > 0 && (
        <div className="pt-1">
          <button className="w-full flex items-center gap-2 text-xs text-base-content/55 hover:text-base-content/80 py-1.5" onClick={() => setShowRedeploy(s => !s)}>
            <span>{showRedeploy ? '▾' : '▸'}</span> Close &amp; redeploy — {redeploys.length} option{redeploys.length > 1 ? 's' : ''}
            <span className="text-base-content/35">(closes this trade, opens a NEW position — not a fix)</span>
          </button>
          {showRedeploy && <div className="space-y-2.5">{redeploys.map((a, i) => <MoveRow key={`r${i}`} a={a} best={false} spot={spot} onEvaluate={onEvaluate} />)}</div>}
        </div>
      )}

      {(hold || close) && (
        <div className="pt-1 space-y-2.5">
          <SubHead hint="the two benchmarks every move is compared against">Do nothing, or close</SubHead>
          {hold && <MoveRow a={hold} best={hold.name === bestName} spot={spot} />}
          {close && <MoveRow a={close} best={close.name === bestName} spot={spot} />}
        </div>
      )}
      {d.roll_note && <Callout tone={rollCount === 0 && d.roll_search === 'done' ? 'warn' : 'neutral'} icon={<Wrench className="w-3.5 h-3.5" />}>Roll search: {d.roll_note}</Callout>}
      <p className="text-[11px] text-base-content/40 leading-relaxed">{d.pricing}. Legs show their real expiries; PoP is to each structure's own horizon; Δrecovery / Δmax-loss are vs holding as-is. “Evaluate” opens the structure in the Income Desk — confirm executable chain prices before acting.</p>
    </div>
  );
}

// ── risk read ────────────────────────────────────────────────────────────────
function FactorRow({ f }: { f: DefendFactor }) {
  const icon = f.favorable === true ? <Check className="w-3.5 h-3.5 text-success" /> : f.favorable === false ? <X className="w-3.5 h-3.5 text-error" /> : <Minus className="w-3.5 h-3.5 text-base-content/40" />;
  return <div className="flex items-start gap-2 text-xs leading-snug"><span className="mt-0.5 shrink-0">{icon}</span><span><b className="font-semibold text-base-content/85">{f.label}</b> <span className="text-base-content/55">— {f.detail}</span></span></div>;
}

function RiskRead({ d }: { d: RepairMenuResult }) {
  const r = d.recoverability;
  if (!r) return null;
  const healthy = r.posture === 'healthy', marginal = r.posture === 'marginal', otm = healthy || marginal;
  const rs = r.recovery_score;
  const rsTone: Tone = rs == null ? 'neutral' : rs >= 60 ? 'good' : rs >= 35 ? 'warn' : 'bad';
  const prob = (r.factors || []).filter(f => f.kind === 'prob');
  const ta = (r.factors || []).filter(f => f.kind === 'ta');
  const sev = SEV[r.severity] || SEV.fresh;
  return (
    <div className="space-y-3">
      <SubHead hint={marginal ? 'not tested — but is it actually safe?' : healthy ? 'this trade is safe — nothing to defend yet' : 'odds of getting back to breakeven — and why'}>
        {marginal ? 'Risk read' : healthy ? 'Trade health' : 'Recoverability'}
      </SubHead>
      {healthy && <Callout tone="good" icon={<Check className="w-3.5 h-3.5" />}>{r.risk_read || `Healthy — ${rs != null ? `${rs}% chance it expires OTM and you keep the premium` : 'well clear of the strike'}. Nothing to defend; this panel is for monitoring.`}</Callout>}
      {marginal && (
        <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
          <div><b>Marginal — not tested, but not safe.</b> {r.risk_read}</div>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-1.5 pt-1.5 border-t border-warning/20 text-base-content/65">
            {r.p_touch != null && <span>P(touch) <b className={r.p_touch >= 50 ? 'text-error' : 'text-warning'}>{r.p_touch}%</b></span>}
            {r.vrp_pct != null && <span title="IV vs realized HV — negative = premium under-priced">VRP <b className={r.vrp_pct < 0 ? 'text-error' : 'text-success'}>{r.vrp_pct > 0 ? '+' : ''}{r.vrp_pct}%</b></span>}
            {r.iv_pct != null && r.hv_pct != null && <span>IV {r.iv_pct}% · HV {r.hv_pct}%</span>}
            {r.trend_pct != null && <span title="annualized trend velocity (EMA slope)">trend <b>{r.trend_pct > 0 ? '+' : ''}{r.trend_pct}%/yr</b></span>}
          </div>
        </Callout>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label={otm ? 'Keep premium' : 'Recovery odds'} value={rs == null ? '—' : `${rs}%`} tone={rsTone} />
        <Tile label="Breakeven" value={r.breakeven == null ? '—' : `$${num(r.breakeven, 2)}`} />
        {otm ? <Tile label="Cushion" value={r.cushion_pct == null ? '—' : `${num(r.cushion_pct, 1)}%`} tone={marginal ? 'warn' : 'good'} />
             : <Tile label="Move needed" value={r.needed_move_pct == null ? '—' : `${r.needed_move_pct > 0 ? '+' : ''}${num(r.needed_move_pct, 1)}%`} />}
        {r.p_touch != null
          ? <Tile label="P(touch)" value={`${r.p_touch}%`} sub={r.dist_to_be_sigma != null ? `≈ ${num(r.dist_to_be_sigma, 2)}σ away` : undefined} tone={r.p_touch >= 50 ? 'bad' : r.p_touch >= 30 ? 'warn' : 'good'} />
          : <Tile label="Severity" value={sev.label} sub={`Δ${num(r.tested_delta, 2)}`} tone={sev.tone} />}
      </div>
      {(prob.length > 0 || ta.length > 0) && (
        <div className="grid sm:grid-cols-2 gap-x-6 gap-y-3 rounded-xl border border-white/[0.07] bg-base-100/20 p-3">
          {prob.length > 0 && <div className="space-y-1.5"><div className="text-[11px] text-base-content/45">{otm ? 'Safety drivers' : 'Probability drivers'}</div>{prob.map((f, i) => <FactorRow key={i} f={f} />)}</div>}
          {ta.length > 0 && <div className="space-y-1.5"><div className="text-[11px] text-base-content/45">Technical read</div>{ta.map((f, i) => <FactorRow key={i} f={f} />)}</div>}
        </div>
      )}
      {r.outlook && <Callout tone={TILT[r.outlook.tilt] || 'neutral'}><b>{otm ? 'Read' : 'Outlook'}: {r.outlook.tilt}.</b> {r.outlook.note}</Callout>}
      <details className="text-xs text-base-content/45">
        <summary className="cursor-pointer select-none">How it's computed</summary>
        <p className="mt-1.5 leading-relaxed">
          {otm
            ? <>The big number is the risk-neutral probability the short finishes OTM (past ${num(r.breakeven, 2)}) at expiry{r.expected_move ? ` — ±$${num(r.expected_move, 2)} expected move` : ''}; the <b>cushion</b> is the move it can absorb before breakeven. <b>P(touch)</b> is the odds the strike is breached at ANY point before then (drift-aware) — the real risk a naive expiry-probability hides.</>
            : <>Recovery = the risk-neutral probability the position finishes at or above your breakeven (${num(r.breakeven, 2)}) by expiry, from a lognormal model at σ≈implied vol{r.expected_move ? ` (±$${num(r.expected_move, 2)} expected move)` : ''}. The drivers above build it; the technical read tilts it.</>}
        </p>
      </details>
    </div>
  );
}

// ── folded sections ──────────────────────────────────────────────────────────
function AssignmentBody({ d }: { d: RepairMenuResult }) {
  const a = d.assignment; if (!a) return null;
  const nakedCall = d.short_right === 'C' && !d.covered;
  return (
    <div className="space-y-3">
      {a.early_assignment_risk && <Callout tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}>Early-assignment risk — {a.early_reason}</Callout>}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        <Tile label="P(finish ITM)" value={pct(a.p_itm)} tone={a.p_itm != null && a.p_itm >= 55 ? 'bad' : 'warn'} />
        <Tile label="Time value left" value={`$${num(a.extrinsic, 2)}`} tone={a.extrinsic <= 0.15 ? 'bad' : undefined} />
        <Tile label="Pin ratio" value={a.pin_ratio == null ? '—' : num(a.pin_ratio, 2)} />
        <Tile label={nakedCall ? 'Effective sale price' : 'Effective basis'} value={`$${num(a.effective_basis, 2)}`} />
        <Tile label="Capital at stake" value={`$${num(a.assignment_capital)}`} />
      </div>
      <p className="text-xs text-base-content/65 leading-relaxed">{a.consequence}</p>
    </div>
  );
}

function CostOfWaitingBody({ d }: { d: RepairMenuResult }) {
  const cw = d.cost_of_waiting || []; if (!cw.length) return null;
  const pts = [{ in_trading_days: 0, recovery_pop: d.recoverability?.recovery_score ?? null, expected_pnl: d.hold?.expected_pnl ?? null }, ...cw];
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
        <table className="w-full text-xs min-w-[280px]">
          <thead><tr className="text-base-content/45"><th className="text-left font-normal px-3 py-1.5" />{pts.map((p, i) => <th key={i} className="text-right font-normal px-3">{i === 0 ? 'Now' : `+${p.in_trading_days}d`}</th>)}</tr></thead>
          <tbody>
            <tr className="border-t border-white/[0.05]"><td className="px-3 py-2 text-base-content/55">Recovery odds</td>
              {pts.map((p, i) => <td key={i} className={`text-right px-3 font-semibold tabular-nums ${p.recovery_pop == null ? 'text-base-content/40' : p.recovery_pop >= 50 ? 'text-success' : 'text-warning'}`}>{p.recovery_pop == null ? '—' : `${p.recovery_pop}%`}</td>)}</tr>
            <tr className="border-t border-white/[0.05]"><td className="px-3 py-2 text-base-content/55">Expected P&amp;L</td>
              {pts.map((p, i) => <td key={i} className={`text-right px-3 tabular-nums ${(p.expected_pnl ?? 0) >= 0 ? 'text-success/90' : 'text-error/90'}`}>{money(p.expected_pnl)}</td>)}</tr>
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-base-content/45 leading-relaxed">Each column re-prices the position with that much less time left (spot unchanged). Recovery odds = P(finish ≥ breakeven); expected P&amp;L = the probability-weighted average outcome. If you're slightly in the money the odds can rise as expiry nears; if you're through the strike they fall — that erosion is the cost of sitting.</p>
    </div>
  );
}

function ContextBody({ d }: { d: RepairMenuResult }) {
  const c = d.context; if (!c) return null;
  const tv = c.loss_read?.time_value_recoverable;
  const s = d.roll_structure;
  const lv = (label: string, v: number | null | undefined) => v == null ? null : <span key={label} className="rounded-md bg-base-300/30 px-1.5 py-0.5 text-xs">{label} <b>${num(v, 2)}</b></span>;
  const Row = ({ icon, children, tone }: { icon: ReactNode; children: ReactNode; tone?: string }) => <div className={`flex items-start gap-2.5 text-xs leading-relaxed ${tone || 'text-base-content/70'}`}><span className="mt-0.5 shrink-0 text-base-content/45">{icon}</span><div className="min-w-0">{children}</div></div>;
  return (
    <div className="space-y-2.5">
      {tv != null && <Row icon={<DollarSign className="w-3.5 h-3.5" />}><b>{money(tv)}</b> of the mark is still <b>time value</b> — it decays back to you if the stock simply holds; the rest is directional and needs a move.</Row>}
      {c.pattern && <Row icon={<TrendingUp className="w-3.5 h-3.5" />}><b className={c.pattern.direction === 'bullish' ? 'text-success' : 'text-error'}>{c.pattern.status} {c.pattern.direction} {c.pattern.type}</b>{c.pattern.confidence != null && <span className="text-base-content/45"> ({c.pattern.confidence}% conf{c.pattern.window ? ` · ${c.pattern.window}` : ''})</span>}{c.pattern.target != null && <span> · target <b>${num(c.pattern.target, 0)}</b></span>}{c.pattern.breakout != null && <span className="text-base-content/45"> · trigger ${num(c.pattern.breakout, 0)}</span>}</Row>}
      {c.range?.note && <Row icon={<BarChart3 className="w-3.5 h-3.5" />}>{c.range.note}</Row>}
      {d.iv_structure?.note && <Row icon={<Thermometer className="w-3.5 h-3.5" />}><b>IV structure:</b> {d.iv_structure.note}</Row>}
      {c.vol_note && <Row icon={<TrendingDown className="w-3.5 h-3.5" />}>{c.vol_note}</Row>}
      {c.technical?.note && <Row icon={<Ruler className="w-3.5 h-3.5" />}>{c.technical.note}</Row>}
      {s && (
        <Row icon={<Layers className="w-3.5 h-3.5" />}>
          <div className="flex flex-wrap items-center gap-1.5" title={s.window}>
            <b>Structure map</b><span className="text-base-content/40">(last ~15d)</span>
            {lv('support', s.support)}{lv('resist', s.resistance)}{lv('POC', s.poc)}{lv('γ-flip', s.gamma_flip)}{lv('γ-wall', s.gamma_wall)}
            {s.gamma_regime && <span className="text-base-content/45">dealers {s.gamma_regime} γ</span>}
          </div>
        </Row>
      )}
      {c.sector && <Row icon={<Compass className="w-3.5 h-3.5" />}>{c.sector.note}{c.sector.peers?.length > 0 && <span className="text-base-content/45"> · peers: {c.sector.peers.slice(0, 6).join(', ')}</span>}</Row>}
      {c.earnings && <Row icon={<CalendarDays className="w-3.5 h-3.5" />} tone={c.earnings.before_expiry ? 'text-warning' : undefined}>{c.earnings.note}</Row>}
    </div>
  );
}

function SyntheticBody({ d }: { d: RepairMenuResult }) {
  const nav = useNavigate();
  const a = d.assignment; if (!a || !d.ticker) return null;
  const shares = (d.contracts ?? 1) * 100;
  const isPut = d.short_right === 'P';
  const reframe = isPut
    ? `Deep enough and this stops being an income trade — it's ${shares} shares of ${d.ticker} at an effective $${num(a.effective_basis, 2)} basis. The question is no longer "is the premium safe" but "do I want to own ${d.ticker} here?"`
    : `A tested short call is a short-stock exposure above $${num(d.short_strike, 0)}: ${shares} shares called away there. The question becomes "do I still want to be short ${d.ticker}'s upside?"`;
  const go = (tab: string) => nav(`/dashboard?ticker=${encodeURIComponent(d.ticker!)}&tab=${tab}`);
  return (
    <div className="space-y-2.5">
      <p className="text-xs text-base-content/70 leading-relaxed">{reframe}</p>
      <div className="flex flex-wrap gap-2">
        <button className="btn btn-outline btn-sm gap-1.5" onClick={() => go('overview')}>Research <ArrowUpRight className="w-3.5 h-3.5" /></button>
        <button className="btn btn-outline btn-sm gap-1.5" onClick={() => go('fundamental')}>Valuation / DCF <ArrowUpRight className="w-3.5 h-3.5" /></button>
        <button className="btn btn-outline btn-sm gap-1.5" onClick={() => go('technical')}>Technical <ArrowUpRight className="w-3.5 h-3.5" /></button>
      </div>
    </div>
  );
}

function RoleCard({ r }: { r?: { role: string; stance: string; rationale: string; metrics?: string[] } }) {
  if (!r) return null;
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-100/20 p-3 space-y-1.5">
      <div className="flex items-center gap-2"><span className="text-sm font-semibold text-secondary/90">{r.role}</span>{r.stance && <Chip>{r.stance}</Chip>}</div>
      <p className="text-xs text-base-content/70 leading-relaxed">{r.rationale}</p>
      {r.metrics && r.metrics.length > 0 && <div className="flex flex-wrap gap-1 pt-0.5">{r.metrics.map((m, i) => <span key={i} className="rounded-md border border-white/10 px-1.5 py-0.5 text-[11px] font-mono text-base-content/55">{m}</span>)}</div>}
    </div>
  );
}

function WarRoomBody({ tradeId, quoteSource, defend }: { tradeId: number; quoteSource: string; defend: RepairMenuResult }) {
  const [data, setData] = useState<DefendCommittee | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async () => {
    setLoading(true); setErr(null);
    try { const r = await fetchDefendCommittee(tradeId, defend, quoteSource); if (r.error) setErr(r.error); else setData(r); }
    catch (e: any) { setErr(e?.message || 'Failed'); } finally { setLoading(false); }
  };
  const v = data?.verdict;
  return (
    <div className="space-y-3">
      <p className="text-xs text-base-content/55 leading-relaxed">A Quant → Risk → PM defense cascade, fed ONLY the numbers above. It calls an LLM, so it runs only when you press the button.</p>
      {!data && !loading && !err && <button className="btn btn-secondary btn-sm gap-1.5" onClick={run}><Users className="w-3.5 h-3.5" /> Convene the defense desk</button>}
      {loading && <div className="text-sm text-base-content/60 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Quant, Risk &amp; PM deliberating on the defense…</div>}
      {err && <Callout tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}>{err}</Callout>}
      {data && (
        <div className="space-y-3">
          <div className="grid gap-2.5">{<RoleCard r={data.quant} />}{<RoleCard r={data.risk} />}{<RoleCard r={data.pm} />}</div>
          {v && (
            <div className="rounded-xl border border-secondary/30 bg-secondary/[0.06] p-3 space-y-2">
              <div className="flex items-center gap-2"><span className="text-[11px] text-secondary/80 font-semibold">Defense verdict</span>{v.confidence && <Chip>{v.confidence} confidence</Chip>}</div>
              <div className="text-sm font-semibold">→ {v.primary_action}</div>
              <p className="text-xs text-base-content/70 leading-relaxed">{v.why}</p>
              {v.alternates?.length > 0 && (
                <div>
                  <div className="text-[11px] text-base-content/45 mb-1">Also considered</div>
                  <ul className="space-y-1">{v.alternates.map((alt, i) => <li key={i} className="text-xs text-base-content/65 flex gap-1.5"><span className="text-base-content/30">•</span><span><b className="text-base-content/80">{alt.action}</b>{alt.why ? ` — ${alt.why}` : ''}</span></li>)}</ul>
                </div>
              )}
              {v.do_not && <Callout tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}><b>Avoid:</b> {v.do_not}</Callout>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── panel ────────────────────────────────────────────────────────────────────
export default function BetaDefend({ tradeId, quoteSource, ticker }: { tradeId: number; quoteSource: string; ticker?: string }) {
  const [data, setData] = useState<RepairMenuResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [refineErr, setRefineErr] = useState<string | null>(null);
  const nav = useNavigate();

  const refine = async (base: RepairMenuResult) => {
    setRefineErr(null);
    try {
      const r = await fetchDefendRefine(tradeId, base, quoteSource);
      if (r.error) { setRefineErr(r.error); return; }
      setData(r);
      if (r.roll_search === 'failed') setRefineErr(r.roll_note || 'The live roll search failed.');
    } catch (e: any) { setRefineErr(e?.message || 'The live roll search failed.'); }
  };
  const run = async () => {
    setLoading(true); setErr(null); setRefineErr(null);
    try {
      const r = await fetchDefendMenu(tradeId, quoteSource);
      if (r.error) { setErr(r.error); return; }
      setData(r);
      if (r.roll_search === 'pending') void refine(r);       // paint the lenses now; the recommendation follows
    } catch (e: any) { setErr(e?.message || 'Failed'); } finally { setLoading(false); }
  };
  // Mounting this panel IS the click: run once. Guarded so StrictMode's dev double-invoke can't fire the heavy phase 2 twice.
  const startedRef = useRef(false);
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    run();
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, []);

  // Hand a chosen repair to the Income Desk's Evaluate mode — in the Beta layout.
  const toEvaluate = (a: RepairAlternative) => {
    const opt = (a.legs || []).filter(l => l.right === 'P' || l.right === 'C');
    if (!opt.length) return;
    const unit = Math.max(1, Math.min(...opt.map(l => l.qty || 1)));
    const expFor = (dd: number | null) => (dd != null ? new Date(Date.now() + dd * 86400000).toISOString().slice(0, 10) : '');
    const legs = opt.flatMap(l => {
      const el = { action: l.action as 'BUY' | 'SELL', type: (l.right === 'P' ? 'PUT' : 'CALL') as 'PUT' | 'CALL', strike: l.strike, expiration: l.expiry || expFor(l.dte_days) };
      return Array(Math.max(1, Math.round((l.qty || 1) / unit))).fill(el);
    });
    try { sessionStorage.setItem('evaluatePrefill', JSON.stringify({ ticker: data?.ticker || ticker, legs })); } catch { /* ignore */ }
    nav('/strategies?ui=beta&strategy=derivative_income&mode=evaluate');
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-white/[0.07] bg-base-200/25 p-4 space-y-2" aria-live="polite">
        <div className="flex items-center gap-2 text-sm text-base-content/70"><Loader2 className="w-4 h-4 animate-spin text-primary" /> Repricing the position and building the defense…</div>
        <div className="h-20 rounded-lg bg-base-content/[0.05] animate-pulse" /><div className="h-28 rounded-lg bg-base-content/[0.05] animate-pulse" />
      </div>
    );
  }
  if (err) return <Callout tone="bad" icon={<AlertTriangle className="w-4 h-4" />}><span>{err}</span> <button className="underline ml-1" onClick={run}>Retry</button></Callout>;
  if (!data) return null;

  const holdBack = data.roll_search === 'pending' && !refineErr;
  const cw = data.cost_of_waiting || [];
  const a = data.assignment;
  const ctx = data.context;
  return (
    <div className="space-y-3">
      <Header d={data} />
      {refineErr && (
        <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
          Live roll search unavailable ({refineErr}) — the recommendation below ranks the standard menu only.{' '}
          <button className="underline" onClick={() => void refine(data)}>Retry</button>
        </Callout>
      )}
      {holdBack ? <Refining /> : <Recommendation d={data} onEvaluate={toEvaluate} />}
      {!holdBack && (data.alternatives || []).length > 0 && <Moves d={data} onEvaluate={toEvaluate} />}
      <RiskRead d={data} />

      {a && (
        <Accordion title="Assignment risk" icon={<ShieldAlert className="w-4 h-4" />} defaultOpen={!!a.early_assignment_risk}
          summary={`P(finish ITM) ${pct(a.p_itm)} · time value left $${num(a.extrinsic, 2)}`}
          badge={a.early_assignment_risk ? <Chip tone="bad">early-assignment risk</Chip> : undefined}>
          <AssignmentBody d={data} />
        </Accordion>
      )}
      {cw.length > 0 && (
        <Accordion title="Cost of waiting" icon={<Clock className="w-4 h-4" />}
          summary={`recovery odds ${data.recoverability?.recovery_score ?? '—'}% now → ${cw[cw.length - 1]?.recovery_pop ?? '—'}% in ${cw[cw.length - 1]?.in_trading_days}d`}>
          <CostOfWaitingBody d={data} />
        </Accordion>
      )}
      {ctx && (
        <Accordion title="What broke & the setup" icon={<TrendingDown className="w-4 h-4" />} summary="pattern · range · vol · structure · sector · earnings">
          <ContextBody d={data} />
        </Accordion>
      )}
      {data.assignment && (
        <Accordion title="Synthetic reframe" icon={<Layers className="w-4 h-4" />} summary="you're becoming a shareholder — decide as one">
          <SyntheticBody d={data} />
        </Accordion>
      )}
      {!holdBack && (
        <Accordion title="War room" icon={<Users className="w-4 h-4" />} summary="Quant → Risk → PM · LLM, on demand">
          <WarRoomBody tradeId={tradeId} quoteSource={quoteSource} defend={data} />
        </Accordion>
      )}
    </div>
  );
}
