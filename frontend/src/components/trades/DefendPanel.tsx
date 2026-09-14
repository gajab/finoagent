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
  ArrowUpRight, TrendingDown, Layers, Check, X, Minus,
} from 'lucide-react';
import { fetchDefendMenu, fetchDefendCommittee } from '../../api';
import type { RepairMenuResult, RepairAlternative, DefendCommittee, DefendFactor } from '../../api';
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
  favorable: { cls: 'badge-success' }, adverse: { cls: 'badge-error' }, balanced: { cls: 'badge-ghost' },
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
  const rs = r.recovery_score;
  const rsTone = rs == null ? 'text-base-content/50' : rs >= 60 ? 'text-success' : rs >= 35 ? 'text-warning' : 'text-error';
  const prob = (r.factors || []).filter(f => f.kind === 'prob');
  const ta = (r.factors || []).filter(f => f.kind === 'ta');
  const outlook = r.outlook;
  return (
    <Section icon={<Activity className="w-3.5 h-3.5" />} title="Recoverability" subtitle="odds of getting back to breakeven — and why">
      <div className="flex items-center gap-4">
        <div className="flex flex-col items-center justify-center rounded-lg border border-white/10 px-3 py-1.5 min-w-[74px]">
          <span className={`text-2xl font-bold leading-none ${rsTone}`}>{rs == null ? '—' : `${rs}%`}</span>
          <span className="text-[8px] uppercase tracking-wide text-base-content/40 mt-0.5">recovery</span>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 flex-1">
          <Stat label="breakeven" value={r.breakeven == null ? '—' : `$${num(r.breakeven, 2)}`} />
          <Stat label="move needed" value={r.needed_move_pct == null ? '—' : `${r.needed_move_pct > 0 ? '+' : ''}${num(r.needed_move_pct, 1)}%`} />
          <Stat label="that's ≈" value={r.dist_to_be_sigma == null ? '—' : `${num(r.dist_to_be_sigma, 2)}σ`} tone="text-base-content/70" />
          <div className="flex flex-col">
            <span className="text-[8px] uppercase tracking-wide text-base-content/40">severity</span>
            <span className={`badge badge-xs ${sev.cls} badge-outline mt-0.5 w-fit`}>{sev.label} · Δ{num(r.tested_delta, 2)}</span>
          </div>
        </div>
      </div>
      <p className="text-[9px] text-base-content/40 leading-snug">
        <b>How it's computed:</b> recovery = the risk-neutral probability the position finishes at or above your breakeven
        (${num(r.breakeven, 2)}) by expiry, from a lognormal model at σ≈implied vol{r.expected_move ? ` (±$${num(r.expected_move, 2)} expected move)` : ''}.
        The factors below drive it; the technical read tilts it.
      </p>

      {/* the build-up — probability drivers, then TA */}
      <div className="space-y-1 pt-0.5">
        {prob.length > 0 && (
          <div className="space-y-0.5">
            <div className="text-[8px] uppercase tracking-wider text-base-content/35">Probability drivers</div>
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
          <span className={`badge badge-xs ${TILT[outlook.tilt]?.cls || 'badge-ghost'} shrink-0`}>outlook: {outlook.tilt}</span>
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

// ── Lens 5 — Action menu (priced repairs + Hold + Close) ───────────────────────────
function ActionMenu({ d, onEvaluate }: { d: RepairMenuResult; onEvaluate: (a: RepairAlternative) => void }) {
  const [showAll, setShowAll] = useState(false);
  const TOP = 4;
  const alts = d.alternatives || [];
  const repairs = alts.filter(a => a.category !== 'exit' && a.category !== 'hold').sort((x, y) => score(y) - score(x));
  const hold = alts.find(a => a.category === 'hold');
  const close = alts.find(a => a.category === 'exit');
  const bestName = repairs[0]?.name;
  const shown = showAll ? repairs : repairs.slice(0, TOP);
  const hidden = repairs.length - shown.length;
  return (
    <Section icon={<Wrench className="w-3.5 h-3.5" />} title="Defensive actions" subtitle={`${repairs.length} priced off the live chain · best-first · Δ vs holding`}>
      <div className="grid gap-1.5 lg:grid-cols-2">
        {shown.map((a, i) => <Card key={i} a={a} best={a.name === bestName} spot={d.spot ?? 0} onEvaluate={onEvaluate} />)}
      </div>
      {hidden > 0 && (
        <button className="btn btn-ghost btn-xs w-full" onClick={() => setShowAll(true)}>Show {hidden} more (calendars · hedges · wheel…)</button>
      )}
      {showAll && repairs.length > TOP && (
        <button className="btn btn-ghost btn-xs w-full" onClick={() => setShowAll(false)}>Show fewer</button>
      )}
      <div className="grid gap-1.5 lg:grid-cols-2 pt-0.5">
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
    <Section icon={<TrendingDown className="w-3.5 h-3.5" />} title="What broke & the setup" subtitle="recoverable value · vol · structure · sector · earnings">
      {tv != null && (
        <p className="text-[10px] text-base-content/70">💵 <b>{money(tv)}</b> of the mark is still <b>time value</b> — it decays back to you if the stock simply holds; the rest is directional and needs a move.</p>
      )}
      {c.vol_note && <p className="text-[10px] text-base-content/65">📊 {c.vol_note}</p>}
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

// ── the panel ──────────────────────────────────────────────────────────────────
export default function DefendPanel({ tradeId, quoteSource }: { tradeId: number; quoteSource: string; ticker?: string }) {
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
  return (
    <div className="space-y-2.5">
      {/* header — the trade at a glance */}
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px]">
        <span className={`badge badge-xs ${sev.cls}`}>{sev.label}</span>
        {data.structure && <span className="badge badge-xs badge-ghost">{data.structure.replace(/_/g, ' ')}</span>}
        <span className="text-base-content/60">
          {data.ticker} · tested {data.short_right === 'P' ? 'put' : 'call'} ${num(data.short_strike, 0)} · spot ${num(data.spot, 2)} · {num(data.cushion_pct, 1)}% cushion · {data.dte_days}d
        </span>
        <span className="ml-auto text-base-content/60">mark <b className={(data.unrealized_pnl ?? 0) >= 0 ? 'text-success' : 'text-error'}>{money(data.unrealized_pnl)}</b> if closed</span>
      </div>

      <Recoverability d={data} />
      <Assignment d={data} />
      <Synthetic d={data} />
      <CostOfWaiting d={data} />
      <ActionMenu d={data} onEvaluate={toEvaluate} />
      <ContextLens d={data} />
      <CommitteeLens tradeId={tradeId} quoteSource={quoteSource} defend={data} />
    </div>
  );
}
