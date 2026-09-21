/**
 * DefendPanel — the trouble-trade desk. When a short-premium trade is tested (ITM / cushion gone),
 * the income score saturates at ~0 and stops being useful. Defend switches the question to "given
 * I'm already here, what's the least-bad path?" and lays out, on ONE click, seven lenses:
 *
 *   1  Recoverability  — P(return to breakeven) WITH the auditable build-up behind it (prob drivers)
 *   2  …tilted by TA   — structure · breakeven-vs-level · MACD · RSI · volume-profile · gamma regime
 *   3  Assignment      — P(finish ITM), extrinsic → early-exercise, pin risk, $ consequence
 *   4  Synthetic       — the trade reframed as the stock position it becomes; bridge to research
 *   5  Action menu     — priced repairs (roll / spread / strangle / hedge / wheel) + Hold + Close, Δ-vs-hold
 *   6  Context         — what broke: recoverable time value · vol state · structure · sector · earnings
 *   7  War room (lazy) — a Quant→Risk→PM defense cascade fed ONLY the numbers above
 *
 * Everything but the LLM war-room comes off ONE core fetch and renders immediately (no inner button).
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Loader2, AlertTriangle, ShieldAlert, Wrench, Activity, Clock, Users,
  ArrowUpRight, TrendingDown, Layers, Check, X, Minus, Target,
} from 'lucide-react';
import { fetchDefendMenu, fetchDefendCommittee, fetchRollOptimizer } from '../../api';
import type { RepairMenuResult, RepairAlternative, DefendCommittee, DefendFactor, RollOptimizerResult, RollCandidate } from '../../api';
import { Card, money, score } from './RepairMenu';

const num = (v: number | null | undefined, d = 0) =>
  v == null || !isFinite(v) ? '—' : v.toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: d });
const pct = (v: number | null | undefined, d = 0) => (v == null || !isFinite(v) ? '—' : `${v.toFixed(d)}%`);

const SEV: Record<string, { label: string; cls: string }> = {
  healthy:  { label: 'Healthy',         cls: 'badge-success' },
  fresh:    { label: 'Fresh breach',    cls: 'badge-warning' },
  deep:     { label: 'Deep ITM',        cls: 'badge-error' },
  assigned: { label: 'Assignment-like', cls: 'badge-error' },
};
const TILT: Record<string, { cls: string }> = {
  favorable: { cls: 'badge-success' }, adverse: { cls: 'badge-error' }, balanced: { cls: 'badge-ghost' }, watch: { cls: 'badge-warning' },
};

function Section({ icon, title, subtitle, children }: { icon: ReactNode; title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-white/10 bg-base-200/20 p-3 space-y-2">
      <div className="flex items-baseline gap-2">
        <span className="text-secondary/80">{icon}</span>
        <span className="text-[11px] font-semibold uppercase tracking-wider text-base-content/70">{title}</span>
        {subtitle && <span className="text-[9px] text-base-content/40">{subtitle}</span>}
      </div>
      {children}
    </div>
  );
}

const Stat = ({ label, value, tone }: { label: string; value: string; tone?: string }) => (
  <div className="flex flex-col">
    <span className="text-[8px] uppercase tracking-wide text-base-content/40">{label}</span>
    <span className={`text-sm font-semibold ${tone || 'text-base-content/90'}`}>{value}</span>
  </div>
);

// A factor row in the recoverability build-up (favorable / adverse / neutral).
function FactorRow({ f }: { f: DefendFactor }) {
  const icon = f.favorable === true ? <Check className="w-3 h-3 text-success" />
    : f.favorable === false ? <X className="w-3 h-3 text-error" />
    : <Minus className="w-3 h-3 text-base-content/40" />;
  return (
    <div className="flex items-start gap-1.5 text-[10px]">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span className="text-base-content/80 font-medium shrink-0">{f.label}</span>
      <span className="text-base-content/50 leading-snug">— {f.detail}</span>
    </div>
  );
}

// ── Lens 1 + 2 — Recoverability WITH the build-up and the TA outlook ─────────────────
function Recoverability({ d }: { d: RepairMenuResult }) {
  const r = d.recoverability;
  if (!r) return null;
  const sev = SEV[r.severity] || SEV.fresh;
  const healthy = r.posture === 'healthy';
  const marginal = r.posture === 'marginal';
  const otm = healthy || marginal;                 // OTM (not tested) → "keep premium" / cushion framing
  const rs = r.recovery_score;
  const rsTone = rs == null ? 'text-base-content/50' : rs >= 60 ? 'text-success' : rs >= 35 ? 'text-warning' : 'text-error';
  const prob = (r.factors || []).filter(f => f.kind === 'prob');
  const ta = (r.factors || []).filter(f => f.kind === 'ta');
  const outlook = r.outlook;
  return (
    <Section icon={<Activity className="w-3.5 h-3.5" />}
      title={marginal ? 'Risk read' : healthy ? 'Trade health' : 'Recoverability'}
      subtitle={marginal ? 'not tested — but is it actually safe? (P-touch · VRP · trend)' : healthy ? 'this trade is safe — nothing to defend yet' : 'odds of getting back to breakeven — and why'}>
      {healthy && (
        <div className="flex items-start gap-1.5 rounded-md bg-success/10 border border-success/25 px-2 py-1.5 text-[10px] text-success">
          <Check className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>{r.risk_read || `Healthy — ${rs != null ? `${rs}% chance it expires OTM and you keep the premium` : 'well clear of the strike'}. Nothing to defend; this panel is for monitoring.`}</span>
        </div>
      )}
      {marginal && (
        <div className="space-y-1.5 rounded-md bg-warning/10 border border-warning/30 px-2 py-1.5">
          <div className="flex items-start gap-1.5 text-[10px] text-warning">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span><b>MARGINAL — not tested, but not safe.</b> {r.risk_read}</span>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[9px] text-base-content/60 pt-0.5 border-t border-warning/15">
            {r.p_touch != null && <span>P(touch) <b className={r.p_touch >= 50 ? 'text-error/80' : 'text-warning/80'}>{r.p_touch}%</b></span>}
            {r.vrp_pct != null && <span title="IV vs realized HV — negative = premium under-priced">VRP <b className={r.vrp_pct < 0 ? 'text-error/80' : 'text-success/80'}>{r.vrp_pct > 0 ? '+' : ''}{r.vrp_pct}%</b></span>}
            {r.iv_pct != null && r.hv_pct != null && <span className="text-base-content/40">IV {r.iv_pct}% · HV {r.hv_pct}%</span>}
            {r.trend_pct != null && <span title="annualized trend velocity (EMA slope)">trend <b className="text-base-content/70">{r.trend_pct > 0 ? '+' : ''}{r.trend_pct}%/yr</b></span>}
          </div>
        </div>
      )}
      <div className="flex items-center gap-4">
        <div className="flex flex-col items-center justify-center rounded-lg border border-white/10 px-3 py-1.5 min-w-[74px]">
          <span className={`text-2xl font-bold leading-none ${rsTone}`}>{rs == null ? '—' : `${rs}%`}</span>
          <span className="text-[8px] uppercase tracking-wide text-base-content/40 mt-0.5">{otm ? 'keep premium' : 'recovery'}</span>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 flex-1">
          <Stat label="breakeven" value={r.breakeven == null ? '—' : `$${num(r.breakeven, 2)}`} />
          {otm
            ? <Stat label="cushion" value={r.cushion_pct == null ? '—' : `${num(r.cushion_pct, 1)}%`} tone={marginal ? 'text-warning' : 'text-success'} />
            : <Stat label="move needed" value={r.needed_move_pct == null ? '—' : `${r.needed_move_pct > 0 ? '+' : ''}${num(r.needed_move_pct, 1)}%`} />}
          <Stat label="that's ≈" value={r.dist_to_be_sigma == null ? '—' : `${num(r.dist_to_be_sigma, 2)}σ`} tone="text-base-content/70" />
          <div className="flex flex-col">
            <span className="text-[8px] uppercase tracking-wide text-base-content/40">P(touch) / severity</span>
            {r.p_touch != null
              ? <span className={`text-sm font-semibold ${r.p_touch >= 50 ? 'text-error' : r.p_touch >= 30 ? 'text-warning' : 'text-success'}`}>{r.p_touch}%<span className="text-[9px] text-base-content/40"> touch</span></span>
              : <span className={`badge badge-xs ${sev.cls} badge-outline mt-0.5 w-fit`}>{sev.label} · Δ{num(r.tested_delta, 2)}</span>}
          </div>
        </div>
      </div>
      <p className="text-[9px] text-base-content/40 leading-snug">
        {otm
          ? <><b>How it's computed:</b> the big number is the risk-neutral probability the short finishes OTM (past ${num(r.breakeven, 2)}) at expiry{r.expected_move ? ` — ±$${num(r.expected_move, 2)} expected move` : ''}; the <b>cushion</b> is the move it can absorb before breakeven. But <b>P(touch)</b> is the odds the strike is breached at ANY point before then (drift-aware) — the real risk a naive expiry-probability hides.</>
          : <><b>How it's computed:</b> recovery = the risk-neutral probability the position finishes at or above your breakeven (${num(r.breakeven, 2)}) by expiry, from a lognormal model at σ≈implied vol{r.expected_move ? ` (±$${num(r.expected_move, 2)} expected move)` : ''}. The factors below drive it; the technical read tilts it.</>}
      </p>

      {/* the build-up — probability / safety drivers, then TA */}
      <div className="space-y-1 pt-0.5">
        {prob.length > 0 && (
          <div className="space-y-0.5">
            <div className="text-[8px] uppercase tracking-wider text-base-content/35">{otm ? 'Safety drivers' : 'Probability drivers'}</div>
            {prob.map((f, i) => <FactorRow key={i} f={f} />)}
          </div>
        )}
        {ta.length > 0 && (
          <div className="space-y-0.5 pt-1">
            <div className="text-[8px] uppercase tracking-wider text-base-content/35">Technical read</div>
            {ta.map((f, i) => <FactorRow key={i} f={f} />)}
          </div>
        )}
      </div>

      {outlook && (
        <div className="flex items-start gap-2 rounded-md bg-base-100/40 px-2 py-1.5 mt-0.5">
          <span className={`badge badge-xs ${TILT[outlook.tilt]?.cls || 'badge-ghost'} shrink-0`}>{otm ? 'read' : 'outlook'}: {outlook.tilt}</span>
          <span className="text-[10px] text-base-content/65 leading-snug">{outlook.note}</span>
        </div>
      )}
    </Section>
  );
}

// ── Lens 3 — Assignment ──────────────────────────────────────────────────────────
function Assignment({ d }: { d: RepairMenuResult }) {
  const a = d.assignment;
  if (!a) return null;
  return (
    <Section icon={<ShieldAlert className="w-3.5 h-3.5" />} title="Assignment risk" subtitle="the real risk on a trapped short">
      {a.early_assignment_risk && (
        <div className="flex items-start gap-1.5 rounded-md bg-error/10 border border-error/25 px-2 py-1.5 text-[10px] text-error">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>Early-assignment risk — {a.early_reason}</span>
        </div>
      )}
      <div className="grid grid-cols-3 gap-x-3 gap-y-2">
        <Stat label="P(finish ITM)" value={pct(a.p_itm)} tone={a.p_itm != null && a.p_itm >= 55 ? 'text-error' : 'text-warning'} />
        <Stat label="time value left" value={`$${num(a.extrinsic, 2)}`} tone={a.extrinsic <= 0.15 ? 'text-error' : 'text-base-content/80'} />
        <Stat label="pin ratio" value={a.pin_ratio == null ? '—' : num(a.pin_ratio, 2)} tone="text-base-content/70" />
        <Stat label="effective basis" value={`$${num(a.effective_basis, 2)}`} />
        <Stat label="capital at stake" value={`$${num(a.assignment_capital)}`} tone="text-base-content/80" />
      </div>
      <p className="text-[10px] text-base-content/60 leading-snug border-t border-white/[0.06] pt-1.5">{a.consequence}</p>
    </Section>
  );
}

// ── Lens 4 — Synthetic reframe → research bridge ───────────────────────────────────
function Synthetic({ d }: { d: RepairMenuResult }) {
  const a = d.assignment;
  const nav = useNavigate();
  if (!a || !d.ticker) return null;
  const shares = (d.contracts ?? 1) * 100;
  const isPut = d.short_right === 'P';
  const reframe = isPut
    ? `Deep enough and this stops being an income trade — it's ${shares} shares of ${d.ticker} at an effective $${num(a.effective_basis, 2)} basis. The question is no longer "is the premium safe" but "do I want to own ${d.ticker} here?"`
    : `A tested short call is a short-stock exposure above $${num(d.short_strike, 0)}: ${shares} shares called away there. The question becomes "do I still want to be short ${d.ticker}'s upside?"`;
  const go = (tab: string) => nav(`/dashboard?ticker=${encodeURIComponent(d.ticker!)}&tab=${tab}`);
  return (
    <Section icon={<Layers className="w-3.5 h-3.5" />} title="Synthetic reframe" subtitle="you're becoming a shareholder — decide as one">
      <p className="text-[10px] text-base-content/70 leading-snug">{reframe}</p>
      <div className="flex flex-wrap gap-1.5 pt-0.5">
        <button className="btn btn-outline btn-xs gap-1" onClick={() => go('overview')}>Research <ArrowUpRight className="w-3 h-3" /></button>
        <button className="btn btn-outline btn-xs gap-1" onClick={() => go('fundamental')}>Valuation / DCF <ArrowUpRight className="w-3 h-3" /></button>
        <button className="btn btn-outline btn-xs gap-1" onClick={() => go('technical')}>Technical <ArrowUpRight className="w-3 h-3" /></button>
      </div>
    </Section>
  );
}

// ── decision clock ───────────────────────────────────────────────────────────────
function CostOfWaiting({ d }: { d: RepairMenuResult }) {
  const cw = d.cost_of_waiting || [];
  if (!cw.length) return null;
  const now = { in_trading_days: 0, recovery_pop: d.recoverability?.recovery_score ?? null, expected_pnl: d.hold?.expected_pnl ?? null };
  const pts = [now, ...cw];
  return (
    <Section icon={<Clock className="w-3.5 h-3.5" />} title="Cost of waiting" subtitle="how the odds move if you do nothing">
      <div className="overflow-x-auto">
        <table className="text-[10px] w-full min-w-[280px]">
          <thead>
            <tr className="text-base-content/40 text-[8px] uppercase tracking-wide">
              <th className="text-left font-medium py-0.5"> </th>
              {pts.map((p, i) => <th key={i} className="text-right font-medium px-1.5">{i === 0 ? 'now' : `+${p.in_trading_days}d`}</th>)}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="text-base-content/50">Recovery odds</td>
              {pts.map((p, i) => (
                <td key={i} className={`text-right px-1.5 font-semibold ${p.recovery_pop == null ? 'text-base-content/40' : p.recovery_pop >= 50 ? 'text-success' : 'text-warning'}`}>
                  {p.recovery_pop == null ? '—' : `${p.recovery_pop}%`}
                </td>
              ))}
            </tr>
            <tr>
              <td className="text-base-content/50">Expected P&amp;L</td>
              {pts.map((p, i) => (
                <td key={i} className={`text-right px-1.5 ${(p.expected_pnl ?? 0) >= 0 ? 'text-success/80' : 'text-error/80'}`}>{money(p.expected_pnl)}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="text-[9px] text-base-content/40 leading-snug">
        Each column re-prices the position you hold with that much less time left (spot unchanged). <b>Recovery odds</b> = P(finish ≥ breakeven);
        <b> Expected P&amp;L</b> = the probability-weighted average outcome. If you're slightly in the money the odds can rise as expiry nears (less time to breach); if you're through the strike they fall — that erosion is the cost of sitting.
      </p>
    </Section>
  );
}

// ── Lens 5 — Action menu: FIX the current trade (primary) vs CLOSE & REDEPLOY (secondary) ──────────
function ActionMenu({ d, onEvaluate }: { d: RepairMenuResult; onEvaluate: (a: RepairAlternative) => void }) {
  const [showAll, setShowAll] = useState(false);
  const [showRedeploy, setShowRedeploy] = useState(false);
  const TOP = 4;
  const alts = d.alternatives || [];
  const priced = alts.filter(a => a.category !== 'exit' && a.category !== 'hold');
  // A "replace" closes the current trade and opens a new one — kept apart so the focus stays on fixing.
  const fixes = priced.filter(a => a.group !== 'replace').sort((x, y) => score(y) - score(x));
  const redeploys = priced.filter(a => a.group === 'replace').sort((x, y) => score(y) - score(x));
  const hold = alts.find(a => a.category === 'hold');
  const close = alts.find(a => a.category === 'exit');
  const bestName = fixes[0]?.name;
  const shown = showAll ? fixes : fixes.slice(0, TOP);
  const hidden = fixes.length - shown.length;
  return (
    <Section icon={<Wrench className="w-3.5 h-3.5" />} title="Fix the current trade" subtitle={`${fixes.length} adjustments · keep the position · best-first · Δ vs holding`}>
      <div className="grid gap-1.5 lg:grid-cols-2">
        {shown.map((a, i) => <Card key={i} a={a} best={a.name === bestName} spot={d.spot ?? 0} onEvaluate={onEvaluate} />)}
      </div>
      {hidden > 0 && <button className="btn btn-ghost btn-xs w-full" onClick={() => setShowAll(true)}>Show {hidden} more adjustments (calendars · butterflies · hedge · wheel…)</button>}
      {showAll && fixes.length > TOP && <button className="btn btn-ghost btn-xs w-full" onClick={() => setShowAll(false)}>Show fewer</button>}

      {redeploys.length > 0 && (
        <div className="pt-1 border-t border-white/[0.06]">
          <button className="w-full flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-base-content/45 hover:text-base-content/70 py-1" onClick={() => setShowRedeploy(s => !s)}>
            {showRedeploy ? '▾' : '▸'} Close &amp; redeploy — {redeploys.length} option{redeploys.length > 1 ? 's' : ''}
            <span className="normal-case tracking-normal text-base-content/35">(closes this trade, opens a NEW position — not a fix)</span>
          </button>
          {showRedeploy && (
            <div className="grid gap-1.5 lg:grid-cols-2 pt-0.5">
              {redeploys.map((a, i) => <Card key={i} a={a} best={false} spot={d.spot ?? 0} onEvaluate={onEvaluate} />)}
            </div>
          )}
        </div>
      )}

      <div className="grid gap-1.5 lg:grid-cols-2 pt-1 border-t border-white/[0.06]">
        {hold && <Card a={hold} best={false} spot={d.spot ?? 0} />}
        {close && <Card a={close} best={false} spot={d.spot ?? 0} />}
      </div>
      <p className="text-[9px] text-base-content/35">{d.pricing}. Legs show their real expiries; PoP is to each structure's own horizon; Δrecovery / Δmax-loss are vs holding as-is. <b>Evaluate →</b> opens the structure in the Income desk; confirm executable chain prices before acting.</p>
    </Section>
  );
}

// ── Lens 6 — Context (folded into the core fetch — renders immediately) ─────────────
function ContextLens({ d }: { d: RepairMenuResult }) {
  const c = d.context;
  if (!c) return null;
  const tv = c.loss_read?.time_value_recoverable;
  return (
    <Section icon={<TrendingDown className="w-3.5 h-3.5" />} title="What broke & the setup" subtitle="pattern · range · vol · structure · sector · earnings">
      {tv != null && (
        <p className="text-[10px] text-base-content/70">💵 <b>{money(tv)}</b> of the mark is still <b>time value</b> — it decays back to you if the stock simply holds; the rest is directional and needs a move.</p>
      )}
      {c.pattern && (
        <p className="text-[10px] text-base-content/70">📈 <b className={c.pattern.direction === 'bullish' ? 'text-success/90' : 'text-error/90'}>{c.pattern.status} {c.pattern.direction} {c.pattern.type}</b>
          {c.pattern.confidence != null && <span className="text-base-content/40"> ({c.pattern.confidence}% conf{c.pattern.window ? ` · ${c.pattern.window}` : ''})</span>}
          {c.pattern.target != null && <span> · target <b>${num(c.pattern.target, 0)}</b></span>}
          {c.pattern.breakout != null && <span className="text-base-content/40"> · trigger ${num(c.pattern.breakout, 0)}</span>}</p>
      )}
      {c.range?.note && <p className="text-[10px] text-base-content/65">📊 {c.range.note}</p>}
      {c.vol_note && <p className="text-[10px] text-base-content/65">📉 {c.vol_note}</p>}
      {c.technical?.note && <p className="text-[10px] text-base-content/65">📐 {c.technical.note}</p>}
      {c.sector && (
        <p className="text-[10px] text-base-content/65">🧭 {c.sector.note}
          {c.sector.peers?.length > 0 && <span className="text-base-content/40"> · peers: {c.sector.peers.slice(0, 6).join(', ')}</span>}</p>
      )}
      {c.earnings && <p className={`text-[10px] ${c.earnings.before_expiry ? 'text-warning' : 'text-base-content/65'}`}>📅 {c.earnings.note}</p>}
    </Section>
  );
}

// ── Lens 7 — War room (lazy, LLM) ───────────────────────────────────────────────────
function RoleCard({ r }: { r?: { role: string; stance: string; rationale: string; metrics?: string[] } }) {
  if (!r) return null;
  return (
    <div className="rounded-md border border-white/10 p-2 space-y-1">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-semibold text-secondary/80">{r.role}</span>
        {r.stance && <span className="badge badge-xs badge-ghost">{r.stance}</span>}
      </div>
      <p className="text-[10px] text-base-content/70 leading-snug">{r.rationale}</p>
      {r.metrics && r.metrics.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-0.5">
          {r.metrics.map((m, i) => <span key={i} className="badge badge-xs badge-outline text-[8px] font-mono text-base-content/50">{m}</span>)}
        </div>
      )}
    </div>
  );
}

function CommitteeLens({ tradeId, quoteSource, defend }: { tradeId: number; quoteSource: string; defend: RepairMenuResult }) {
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
    <Section icon={<Users className="w-3.5 h-3.5" />} title="War room" subtitle="Quant → Risk → PM defense cascade · grounded in the numbers above">
      {!data && !loading && !err && <button className="btn btn-secondary btn-xs gap-1.5" onClick={run}><Users className="w-3 h-3" />Convene the defense desk</button>}
      {loading && <div className="text-[11px] text-base-content/50 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" />Quant, Risk & PM deliberating on the defense…</div>}
      {err && <div className="text-[11px] text-error flex items-start gap-1"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>}
      {data && (
        <div className="space-y-2">
          <div className="grid gap-1.5 lg:grid-cols-3">
            <RoleCard r={data.quant} /><RoleCard r={data.risk} /><RoleCard r={data.pm} />
          </div>
          {v && (
            <div className="rounded-md border border-secondary/30 bg-secondary/[0.05] p-2.5 space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="text-[9px] uppercase tracking-wider text-secondary/70 font-semibold">Defense verdict</span>
                {v.confidence && <span className="badge badge-xs badge-outline text-[8px]">{v.confidence} confidence</span>}
              </div>
              <div className="text-sm font-semibold text-base-content/90">→ {v.primary_action}</div>
              <p className="text-[10px] text-base-content/70 leading-snug">{v.why}</p>
              {v.alternates?.length > 0 && (
                <div className="pt-0.5">
                  <div className="text-[8px] uppercase tracking-wider text-base-content/35 mb-0.5">Also considered</div>
                  <ul className="space-y-0.5">
                    {v.alternates.map((alt, i) => (
                      <li key={i} className="text-[10px] text-base-content/60 flex gap-1.5">
                        <span className="text-base-content/30 shrink-0">•</span>
                        <span><b className="text-base-content/75">{alt.action}</b>{alt.why ? ` — ${alt.why}` : ''}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {v.do_not && (
                <p className="text-[10px] text-error/85 flex items-start gap-1 border-t border-white/[0.06] pt-1.5">
                  <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" /><span><b>Avoid:</b> {v.do_not}</span>
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

// ── Roll optimizer — deep-quant credit-only strike+expiry finder (lazy, heavy) ──────────────
function RollLevel({ label, value }: { label: string; value: number | null | undefined }) {
  if (value == null) return null;
  return <span className="text-[9px] text-base-content/50">{label} <b className="text-base-content/70">${num(value, 2)}</b></span>;
}

function ScoreBar({ label, v }: { label: string; v: number }) {
  return (
    <div className="flex items-center gap-1">
      <span className="text-[8px] text-base-content/40 w-16 shrink-0">{label}</span>
      <div className="flex-1 h-1 rounded bg-base-content/10 overflow-hidden">
        <div className="h-full bg-secondary/60" style={{ width: `${Math.max(0, Math.min(100, v))}%` }} />
      </div>
      <span className="text-[8px] text-base-content/50 w-6 text-right">{Math.round(v)}</span>
    </div>
  );
}

function RollCard({ c, best, onEvaluate }: { c: RollCandidate; best: boolean; onEvaluate: (c: RollCandidate) => void }) {
  const cleared = Object.entries(c.structure).filter(([, v]) => v === true).map(([k]) => k);
  return (
    <div className={`rounded-lg border p-2 space-y-1.5 ${best ? 'border-secondary/50 bg-secondary/[0.05]' : 'border-white/10'}`}>
      <div className="flex items-center gap-1.5">
        <span className="badge badge-xs badge-accent badge-outline">Roll</span>
        <span className="text-[11px] font-semibold">${num(c.strike, 0)} {c.right === 'P' ? 'put' : 'call'} · {c.expiry} ({c.dte}d)</span>
        {best && <span className="badge badge-xs badge-secondary">best</span>}
        {c.spans_earnings && <span className="badge badge-xs badge-warning gap-0.5" title="This roll spans the next earnings report — binary gap risk">⚠ earnings</span>}
        <span className="ml-auto text-sm font-bold text-secondary">{Math.round(c.scores.composite)}<span className="text-[9px] text-base-content/40">/100</span></span>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[9px] text-base-content/55">
        <span>credit <b className={c.roll_net_cash >= 0 ? 'text-success/80' : 'text-error/80'}>{money(c.roll_net_cash)}</b></span>
        <span>P(OTM) <b className={c.p_otm >= 70 ? 'text-success/80' : 'text-warning/80'}>{c.p_otm}%</b> <span className="text-base-content/35">{c.p_otm_source}</span></span>
        {c.scores.cushion_sigma != null && <span title="cushion in std-devs over this expiry's horizon — more days ⇒ fewer σ for the same distance">cushion <b>{c.scores.cushion_sigma.toFixed(1)}σ</b>/{c.dte}d</span>}
        <span>new B/E <b>${num(c.new_breakeven, 2)}</b></span>
        <span>capital <b>${c.new_capital.toLocaleString('en-US', { maximumFractionDigits: 0 })}</b></span>
      </div>
      {cleared.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {cleared.map((k, i) => <span key={i} className="badge badge-xs badge-success badge-outline text-[8px] gap-0.5"><Check className="w-2 h-2" />{k}</span>)}
        </div>
      )}
      <div className="space-y-0.5 pt-0.5">
        <ScoreBar label="probability" v={c.scores.probability} />
        <ScoreBar label="structure" v={c.scores.structure} />
        <ScoreBar label="credit" v={c.scores.credit} />
        <ScoreBar label="cushion" v={c.scores.cushion} />
      </div>
      <div className="flex items-center gap-2 pt-0.5">
        <p className="text-[9px] text-base-content/50 leading-snug flex-1">{c.why}</p>
        <button className="text-[9px] text-secondary hover:text-secondary/80 font-medium flex items-center gap-0.5 shrink-0" onClick={() => onEvaluate(c)}>Evaluate <ArrowUpRight className="w-2.5 h-2.5" /></button>
      </div>
    </div>
  );
}

function RollOptimizer({ tradeId, quoteSource, ticker }: { tradeId: number; quoteSource: string; ticker?: string }) {
  const [data, setData] = useState<RollOptimizerResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();
  const run = async () => {
    setLoading(true); setErr(null);
    try { const r = await fetchRollOptimizer(tradeId, quoteSource); if (r.error) setErr(r.error); else setData(r); }
    catch (e: any) { setErr(e?.message || 'Failed'); } finally { setLoading(false); }
  };
  // Send the RESULTING short (the SELL leg) to Evaluate — the roll's new position.
  const toEvaluate = (c: RollCandidate) => {
    const nl = c.legs.find(l => l.action === 'SELL');
    if (!nl) return;
    const legs = [{ action: 'SELL' as const, type: (nl.right === 'P' ? 'PUT' : 'CALL') as 'PUT' | 'CALL', strike: nl.strike, expiration: nl.expiry || '' }];
    try { sessionStorage.setItem('evaluatePrefill', JSON.stringify({ ticker: ticker || data?.ticker, legs })); } catch { /* ignore */ }
    nav('/strategies?mode=evaluate');
  };
  const s = data?.structure;
  return (
    <Section icon={<Target className="w-3.5 h-3.5" />} title="Optimize the roll" subtitle="credit-only · RND · gamma/GEX · volume · TA — where to roll">
      {!data && !loading && !err && (
        <div className="space-y-1">
          <button className="btn btn-secondary btn-xs gap-1.5" onClick={run}><Target className="w-3 h-3" />Find the best roll (deep quant)</button>
          <p className="text-[9px] text-base-content/40">Searches later expiries for a NET-CREDIT roll (no new money) and ranks each strike by the market RND probability + structure — support/resistance · gamma flip/wall · volume POC.</p>
        </div>
      )}
      {loading && <div className="text-[11px] text-base-content/50 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" />Building RNDs across expiries, mapping gamma & volume structure…</div>}
      {err && <div className="text-[11px] text-error flex items-start gap-1"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>}
      {data && (
        <div className="space-y-2">
          {s && (
            <div className="rounded-md bg-base-100/40 px-2 py-1.5 space-y-0.5">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                <span className="text-[8px] uppercase tracking-wide text-base-content/40" title={s.window || 'recent structure'}>structure · last ~15d</span>
                <RollLevel label="support" value={s.support} /><RollLevel label="resist" value={s.resistance} />
                <RollLevel label="POC" value={s.poc} /><RollLevel label="γ-flip" value={s.gamma_flip} />
                <RollLevel label="γ-wall" value={s.gamma_wall} />
                {s.gamma_regime && <span className="text-[9px] text-base-content/45">dealers {s.gamma_regime} γ</span>}
                {s.next_earnings && <span className="text-[9px] text-warning/70" title="Rolling past this date spans earnings — binary gap risk">earnings {s.next_earnings}</span>}
              </div>
              {s.recent_5d && (s.recent_5d.support != null || s.recent_5d.resistance != null) && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
                  <span className="text-[8px] uppercase tracking-wide text-base-content/35">last ~5d</span>
                  <RollLevel label="pivot lo" value={s.recent_5d.support} /><RollLevel label="pivot hi" value={s.recent_5d.resistance} />
                  <RollLevel label="POC" value={s.recent_5d.poc} />
                </div>
              )}
            </div>
          )}
          {(data.candidates || []).length === 0 && (
            <p className="text-[10px] text-warning">No net-credit roll clears the quality bar right now — rolling would cost money or leave a coin-flip strike. Consider capping (defined-risk) or closing instead.</p>
          )}
          <div className="grid gap-1.5 lg:grid-cols-2">
            {(data.candidates || []).map((c, i) => <RollCard key={i} c={c} best={i === 0} onEvaluate={toEvaluate} />)}
          </div>
          {data.note && <p className="text-[9px] text-base-content/40">{data.note} Considered {data.considered} credit rolls; weights — prob {Math.round((data.weights?.probability || 0) * 100)}% · structure {Math.round((data.weights?.structure || 0) * 100)}% · credit {Math.round((data.weights?.credit || 0) * 100)}% · cushion {Math.round((data.weights?.cushion || 0) * 100)}%.</p>}
        </div>
      )}
    </Section>
  );
}

// ── the panel ──────────────────────────────────────────────────────────────────
export default function DefendPanel({ tradeId, quoteSource, ticker }: { tradeId: number; quoteSource: string; ticker?: string }) {
  const [data, setData] = useState<RepairMenuResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const nav = useNavigate();

  const run = async () => {
    setLoading(true); setErr(null);
    try { const r = await fetchDefendMenu(tradeId, quoteSource); if (r.error) setErr(r.error); else setData(r); }
    catch (e: any) { setErr(e?.message || 'Failed'); } finally { setLoading(false); }
  };

  // #4 — clicking "Defend this trade" (which mounts this panel) runs the analysis; no inner button.
  useEffect(() => { run(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  // Hand a chosen repair to the Income desk's Evaluate tab (mirrors RepairMenu's bridge).
  const toEvaluate = (a: RepairAlternative) => {
    const opt = (a.legs || []).filter(l => l.right === 'P' || l.right === 'C');
    if (!opt.length) return;
    const unit = Math.max(1, Math.min(...opt.map(l => l.qty || 1)));
    const expFor = (dd: number | null) => (dd != null ? new Date(Date.now() + dd * 86400000).toISOString().slice(0, 10) : '');
    const legs = opt.flatMap(l => {
      const el = { action: l.action as 'BUY' | 'SELL', type: (l.right === 'P' ? 'PUT' : 'CALL') as 'PUT' | 'CALL', strike: l.strike, expiration: expFor(l.dte_days) };
      return Array(Math.max(1, Math.round((l.qty || 1) / unit))).fill(el);
    });
    try { sessionStorage.setItem('evaluatePrefill', JSON.stringify({ ticker: data?.ticker, legs })); } catch { /* ignore */ }
    nav('/strategies?mode=evaluate');
  };

  if (loading) return <div className="text-xs text-base-content/50 flex items-center gap-2 py-2"><Loader2 className="w-4 h-4 animate-spin" />Repricing the position & building the defense…</div>;
  if (err) return (
    <div className="space-y-2">
      <div className="text-xs text-error flex items-start gap-1"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>
      <button className="btn btn-ghost btn-xs" onClick={run}>Retry</button>
    </div>
  );
  if (!data) return null;

  const sev = SEV[data.recoverability?.severity || 'fresh'] || SEV.fresh;
  const posture = data.recoverability?.posture;
  const postureBadge = posture === 'marginal'
    ? { cls: 'badge-warning', label: 'MARGINAL' }
    : posture === 'healthy' ? { cls: 'badge-success', label: 'Healthy' } : sev;
  return (
    <div className="space-y-2.5">
      {/* header — the trade at a glance */}
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px]">
        <span className={`badge badge-xs ${postureBadge.cls}`} title={posture === 'marginal' ? 'Not tested, but high breach probability / under-priced premium / adverse trend — de-risk' : undefined}>{postureBadge.label}</span>
        {data.structure && <span className="badge badge-xs badge-ghost">{data.structure.replace(/_/g, ' ')}</span>}
        {data.covered && <span className="badge badge-xs badge-info badge-outline" title="Shares cover the short call — the risk is being called away (opportunity cost), not an unbounded loss">covered</span>}
        <span className="text-base-content/60">
          {data.ticker} · tested {data.short_right === 'P' ? 'put' : 'call'} ${num(data.short_strike, 0)} · spot ${num(data.spot, 2)} · {num(data.cushion_pct, 1)}% cushion · {data.dte_days}d
        </span>
        <span className="ml-auto text-base-content/60">mark <b className={(data.unrealized_pnl ?? 0) >= 0 ? 'text-success' : 'text-error'}>{money(data.unrealized_pnl)}</b> if closed</span>
      </div>

      <Recoverability d={data} />
      <Assignment d={data} />
      <Synthetic d={data} />
      <CostOfWaiting d={data} />
      <RollOptimizer tradeId={tradeId} quoteSource={quoteSource} ticker={ticker} />
      <ActionMenu d={data} onEvaluate={toEvaluate} />
      <ContextLens d={data} />
      <CommitteeLens tradeId={tradeId} quoteSource={quoteSource} defend={data} />
    </div>
  );
}
