/**
 * RepairMenu — institutional MULTI-LEG adjustment menu for a tested short-premium trade.
 *
 * A desk weighs a menu of DEFINED repairs — roll away-&-out, jade-lizard overlay, iron-condor cap,
 * delta-hedge, take-assignment → wheel, roll the whole structure, or close — instead of hoping or
 * bailing. Lazy-loads that menu (live-chain priced), RANKS the repairs (defined-risk / risk-free /
 * PoP first), flags the top pick, and lays each out tightly: the exact legs to trade, a payoff
 * sparkline, the key numbers (max loss / gain, net, Θ, Δ), and a one-line rationale.
 */
import { useState } from 'react';
import { Loader2, AlertTriangle, Wrench, ShieldCheck, Infinity as InfinityIcon, Star, Info, ArrowRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { fetchTradeRepairMenu } from '../../api';
import type { RepairMenuResult, RepairAlternative } from '../../api';

export const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '−' : '+'}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;

export const CAT: Record<string, { label: string; cls: string }> = {
  exit:         { label: 'Close',    cls: 'badge-ghost' },
  hold:         { label: 'Hold',     cls: 'badge-ghost' },
  roll:         { label: 'Roll',     cls: 'badge-info' },
  overlay:      { label: 'Overlay',  cls: 'badge-secondary' },
  defined_risk: { label: 'Cap risk', cls: 'badge-success' },
  calendar:     { label: 'Calendar', cls: 'badge-accent' },
  butterfly:    { label: 'Butterfly',cls: 'badge-accent' },
  ratio:        { label: 'Ratio',    cls: 'badge-accent' },
  hedge:        { label: 'Hedge',    cls: 'badge-warning' },
  assignment:   { label: 'Wheel',    cls: 'badge-secondary' },
};

// Compact expiry label from an ISO date — TZ-safe (parses the string, no Date()).
const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function fmtExp(iso?: string | null): string {
  if (!iso) return '';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${_MONTHS[+m[2] - 1]}${+m[3]}` : '';
}

// Rank repairs: defined-risk + risk-free + turns-profitable + PoP + credit-over-cost win.
export function score(a: RepairAlternative): number {
  if (a.category === 'exit' || a.category === 'hold') return -Infinity;   // benchmarks sit apart, unranked
  let s = 0;
  s += a.defined_risk ? 3 : -5;
  s += a.upside_risk_free ? 2 : 0;
  s += a.turns_profitable ? 1 : 0;
  s += (a.pop_pct ?? 40) / 100;
  s += a.net_cash >= 0 ? 0.5 : 0;
  return s;
}

// A real PAYOFF DIAGRAM: P&L (y) vs underlying price (x). Green shading = profit, red = loss,
// dashed zero line, a "now" marker at current spot, and dots at the breakevens.
let _pgId = 0;
export function PayoffCurve({ alt, spot }: { alt: RepairAlternative; spot: number }) {
  const W = 260, H = 60, pad = 3;
  const s = alt.scenarios;
  const xs = s.map(p => p.move_pct);
  const ys = s.map(p => p.pnl);
  const xmin = Math.min(...xs), xmax = Math.max(...xs);
  const ymin = Math.min(0, ...ys), ymaxRaw = Math.max(0, ...ys);
  const ymax = ymaxRaw === ymin ? ymin + 1 : ymaxRaw;
  const X = (mv: number) => pad + ((mv - xmin) / (xmax - xmin)) * (W - 2 * pad);
  const Y = (p: number) => pad + ((ymax - p) / (ymax - ymin)) * (H - 2 * pad);
  const y0 = Y(0);
  const line = s.map((p, i) => `${i ? 'L' : 'M'}${X(p.move_pct).toFixed(1)},${Y(p.pnl).toFixed(1)}`).join(' ');
  const area = `${line} L${X(xmax).toFixed(1)},${y0.toFixed(1)} L${X(xmin).toFixed(1)},${y0.toFixed(1)} Z`;
  const gid = `pg${_pgId++}`;
  const zeroPct = (((y0 - pad) / (H - 2 * pad)) * 100).toFixed(2);
  const beMoves = (alt.breakevens || []).map(b => ((b / spot) - 1) * 100).filter(m => m >= xmin && m <= xmax);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 60 }} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#22c55e" stopOpacity="0.28" />
          <stop offset={`${zeroPct}%`} stopColor="#22c55e" stopOpacity="0.10" />
          <stop offset={`${zeroPct}%`} stopColor="#ef4444" stopOpacity="0.10" />
          <stop offset="100%" stopColor="#ef4444" stopOpacity="0.28" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <line x1={pad} y1={y0} x2={W - pad} y2={y0} stroke="currentColor" strokeOpacity="0.25" strokeDasharray="3 2" />
      <line x1={X(0)} y1={pad} x2={X(0)} y2={H - pad} stroke="currentColor" strokeOpacity="0.35" strokeDasharray="1 2" />
      <path d={line} fill="none" stroke="currentColor" strokeOpacity="0.75" strokeWidth="1.3" vectorEffect="non-scaling-stroke" />
      {beMoves.map((m, i) => <circle key={i} cx={X(m)} cy={y0} r="2.2" fill="#f59e0b" />)}
    </svg>
  );
}

export function legStr(l: { action: string; right: string; strike: number; qty: number; expiry?: string | null }): string {
  if (l.right === 'STK') return `${l.action} ${l.qty} sh`;
  const e = fmtExp(l.expiry);
  return `${l.action === 'SELL' ? '−' : '+'}${l.qty} ${l.right === 'P' ? 'P' : 'C'}$${l.strike}${e ? ` ${e}` : ''}`;
}

export function Card({ a, best, spot, onEvaluate }: { a: RepairAlternative; best: boolean; spot: number; onEvaluate?: (a: RepairAlternative) => void }) {
  const [open, setOpen] = useState(false);
  const undef = a.category !== 'exit' && !a.defined_risk;
  const canEval = a.category !== 'exit' && (a.legs || []).some(l => l.right === 'P' || l.right === 'C');
  return (
    <div className={`rounded-lg border p-2 space-y-1 ${best ? 'border-success/40 bg-success/[0.04]' : undef ? 'border-warning/20' : 'border-white/10'}`}>
      <div className="flex items-center gap-1.5">
        <span className={`badge badge-xs ${CAT[a.category]?.cls || 'badge-ghost'} badge-outline`}>{CAT[a.category]?.label || a.category}</span>
        <span className="text-[11px] font-semibold truncate">{a.name}</span>
        {best && <span className="badge badge-xs badge-success gap-0.5"><Star className="w-2.5 h-2.5" />best</span>}
        <span className="ml-auto text-[10px] text-base-content/50">{a.pop_pct != null ? `PoP ${a.pop_pct}%` : ''}</span>
      </div>

      {a.legs && a.legs.length > 0 && (
        <div className="text-[10px] font-mono text-base-content/60 truncate" title={a.mechanics}>{a.legs.map(legStr).join('  ')}</div>
      )}

      {/* payoff diagram — P&L (up = profit) vs the underlying price */}
      <div className="relative">
        <PayoffCurve alt={a} spot={spot} />
        <span className="absolute top-0 left-0 text-[7px] text-success/70 leading-none">+P&amp;L</span>
        <span className="absolute bottom-0 left-0 text-[7px] text-error/70 leading-none">−P&amp;L</span>
        <span className="absolute bottom-0 right-0 text-[7px] text-base-content/40 leading-none">price →</span>
        <span className="absolute top-0 text-[7px] text-base-content/45 leading-none" style={{ left: '50%', transform: 'translateX(-50%)' }}>now ${spot}</span>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[9px] text-base-content/55">
        <span>max loss <b className={a.max_loss == null ? 'text-error' : 'text-error/80'}>{a.max_loss == null ? 'undefined' : money(a.max_loss)}</b></span>
        <span>max gain <b className="text-success/80">{money(a.max_gain)}</b></span>
        <span>net <b>{money(a.net_cash)}</b></span>
        {a.ev != null && <span title="Expected P&L under the lognormal law">E[P&amp;L] <b className={a.ev >= 0 ? 'text-success/80' : 'text-error/80'}>{money(a.ev)}</b></span>}
        <span>Θ/d <b className={a.theta_day >= 0 ? 'text-success/80' : 'text-error/80'}>{money(a.theta_day)}</b></span>
        <span>Δ <b>{a.greeks.delta}</b></span>
        {a.d_pop != null && a.category !== 'exit' && a.category !== 'hold' && (
          <span title="Change in recovery odds vs holding as-is">Δrecovery <b className={a.d_pop >= 0 ? 'text-success/80' : 'text-error/80'}>{a.d_pop >= 0 ? '+' : ''}{a.d_pop}%</b></span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1 pt-0.5">
        {a.category !== 'exit' && (a.defined_risk
          ? <span className="badge badge-xs badge-success badge-outline gap-0.5"><ShieldCheck className="w-2.5 h-2.5" />defined</span>
          : <span className="badge badge-xs badge-error badge-outline gap-0.5"><InfinityIcon className="w-2.5 h-2.5" />open tail</span>)}
        {a.upside_risk_free && a.category !== 'exit' && <span className="badge badge-xs badge-success badge-outline">risk-free side</span>}
        {a.breakevens.length > 0 && <span className="text-[9px] text-warning/60">◦ B/E {a.breakevens.map(b => `$${b}`).join('·')}</span>}
        <div className="ml-auto flex items-center gap-2">
          <button className="text-[9px] text-base-content/40 hover:text-base-content/70 flex items-center gap-0.5" onClick={() => setOpen(o => !o)}>
            <Info className="w-2.5 h-2.5" />why
          </button>
          {canEval && onEvaluate && (
            <button className="text-[9px] text-secondary hover:text-secondary/80 flex items-center gap-0.5 font-medium" onClick={() => onEvaluate(a)}>
              Evaluate <ArrowRight className="w-2.5 h-2.5" />
            </button>
          )}
        </div>
      </div>
      {open && <p className="text-[9px] text-base-content/55 leading-snug border-t border-white/[0.06] pt-1">{a.mechanics} — {a.rationale || a.risk_note}</p>}
    </div>
  );
}

export default function RepairMenu({ tradeId, quoteSource }: { tradeId: number; quoteSource: string }) {
  const [data, setData] = useState<RepairMenuResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const navigate = useNavigate();

  // Hand the repaired structure to the Income desk's Evaluate tab, pre-populated, for a full-desk
  // deep-dive. EvaluateLeg has no qty, so ratio legs (e.g. a fly's 2× body) are expanded to copies.
  const toEvaluate = (a: RepairAlternative) => {
    const opt = (a.legs || []).filter(l => l.right === 'P' || l.right === 'C');
    const unit = Math.max(1, Math.min(...opt.map(l => l.qty || 1)));
    const expFor = (d: number | null) => d != null ? new Date(Date.now() + d * 86400000).toISOString().slice(0, 10) : '';
    const legs = opt.flatMap(l => {
      const el = { action: l.action as 'BUY' | 'SELL', type: (l.right === 'P' ? 'PUT' : 'CALL') as 'PUT' | 'CALL', strike: l.strike, expiration: expFor(l.dte_days) };
      return Array(Math.max(1, Math.round((l.qty || 1) / unit))).fill(el);
    });
    try { sessionStorage.setItem('evaluatePrefill', JSON.stringify({ ticker: data?.ticker, legs })); } catch { /* ignore */ }
    navigate('/strategies?mode=evaluate');
  };

  const run = async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetchTradeRepairMenu(tradeId, quoteSource);
      if (r.error) setErr(r.error); else setData(r);
    } catch (e: any) { setErr(e?.message || 'Failed'); }
    finally { setLoading(false); }
  };

  if (!data && !loading && !err)
    return <button className="btn btn-outline btn-xs gap-1.5" onClick={run}><Wrench className="w-3 h-3" /> Build repair / adjust menu</button>;
  if (loading) return <div className="text-xs text-base-content/50 flex items-center gap-2 py-2"><Loader2 className="w-4 h-4 animate-spin" />Pricing roll / jade / condor / hedge / wheel structures…</div>;
  if (err) return <div className="text-xs text-error flex items-start gap-1"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>;
  if (!data?.alternatives) return null;

  const close = data.alternatives.filter(a => a.category === 'exit');
  const repairs = data.alternatives.filter(a => a.category !== 'exit').sort((x, y) => score(y) - score(x));
  const bestName = repairs[0]?.name;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2 text-[11px]">
        <span className={`badge badge-xs ${data.tested ? 'badge-warning' : 'badge-ghost'}`}>{data.tested ? 'TESTED' : 'cushion ok'}</span>
        {data.structure && <span className="badge badge-xs badge-ghost">{data.structure.replace(/_/g, ' ')}</span>}
        <span className="text-base-content/60">{data.ticker} · tested {data.short_right === 'P' ? 'put' : 'call'} ${data.short_strike} · spot ${data.spot} · {data.cushion_pct}% cushion · {data.dte_days}d</span>
        <span className="ml-auto text-base-content/60">mark <b className={((data.unrealized_pnl ?? 0) >= 0) ? 'text-success' : 'text-error'}>{money(data.unrealized_pnl)}</b> if closed</span>
      </div>

      {/* Repairs ranked best-first; Close (the benchmark) pinned at the bottom */}
      <div className="grid gap-1.5 lg:grid-cols-2">
        {repairs.map((a, i) => <Card key={i} a={a} best={a.name === bestName} spot={data.spot ?? 0} onEvaluate={toEvaluate} />)}
      </div>
      {close.map((a, i) => <Card key={`c${i}`} a={a} best={false} spot={data.spot ?? 0} />)}

      <p className="text-[9px] text-base-content/35">{data.pricing}. Payoffs at the nearest-expiry horizon (longer legs BS-marked), from your original entry. Ranked by defined-risk, risk-free wing, and PoP. <b>Evaluate →</b> opens the structure in the Income desk for a full-desk deep-dive; confirm executable prices on the chain before acting.</p>
    </div>
  );
}
