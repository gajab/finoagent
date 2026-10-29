/**
 * DefendPlaybook — the leg-by-leg desk read for a trapped short-premium position (shared by the classic and Beta Defend panels).
 *
 *   PositionAnatomy  what each held leg is doing for you NOW: time value left, theta $/day, vega $/vol-pt, odds — plus the
 *                    expiry zones, the pin and early-assignment mechanics, the honest "what is left" read, and what each leg
 *                    can be done with on its own (priced). An in-the-money spread is the classic trap: the long, at the money,
 *                    decays faster than the deep short has left to earn — time works AGAINST you, and you are long vega.
 *   ActionPlan       WHAT TO DO, on one card: a few facts, then at most five actions — each with the same four numbers (cash today,
 *                    worst, best, odds it ends better than closing now), one line of why, when to do it, and its orders on a tap.
 *   AssignmentLadder expiry by expiry, how far the short strike can move for NO new money — cash-secured (the effective price
 *                    you'd own the shares at) or as a wider defined-risk spread — and what rolling the same strikes costs.
 *
 * Every number comes from the backend (defend_playbook_service); nothing here computes a price. Plain classes only.
 */
import { Fragment, useState } from 'react';
import { AlertTriangle, ArrowDownRight, CalendarClock, Clock, Gauge, Info, Layers, Thermometer, TrendingDown, TrendingUp } from 'lucide-react';
import type { DefendActionRow, DefendAnatomy, DefendExplorerRow, DefendLadder, RepairAlternative, RepairMenuResult } from '../../api';
import { describeStep, shortDate } from './MoveSteps';

const usd = (v: number | null | undefined, signed = true) =>
  v == null || !isFinite(v) ? '—' : `${signed ? (v < 0 ? '−' : '+') : v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
const px = (v: number | null | undefined) => (v == null || !isFinite(v) ? '—' : `$${v.toFixed(2)}`);
const k$ = (v: number) => (Math.abs(v - Math.round(v)) < 1e-6 ? `$${Math.round(v)}` : `$${v.toFixed(2)}`);
const tone = (v: number | null | undefined) => (v == null ? '' : v > 0 ? 'text-success' : v < 0 ? 'text-error' : '');

const TONE_ICON: Record<string, { icon: typeof Info; cls: string }> = {
  bad: { icon: TrendingDown, cls: 'text-error' },
  good: { icon: TrendingUp, cls: 'text-success' },
  warn: { icon: AlertTriangle, cls: 'text-warning' },
  info: { icon: Info, cls: 'text-info' },
};

function Head({ icon: Icon, title, hint }: { icon: typeof Info; title: string; hint?: string }) {
  return (
    <div className="flex items-baseline gap-2 flex-wrap">
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-base-content/60"><Icon className="w-3.5 h-3.5" />{title}</span>
      {hint && <span className="text-[11px] text-base-content/40">{hint}</span>}
    </div>
  );
}

/** The near expiry's ATM vol vs daily realized, and the earnings move the straddles price. */
export function VolStrip({ d }: { d: RepairMenuResult }) {
  const v = d.vol_read, ev = d.event;
  if (!v && !ev?.implied_move_pct) return null;
  return (
    <div className="flex flex-wrap gap-1.5 text-[11px] text-base-content/60" data-testid="vol-strip">
      {v && (
        <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.1] px-2 py-0.5" title="ATM straddle vol of the trade's expiry vs close-to-close realized vol (daily bars)">
          <Thermometer className="w-3 h-3" />{shortDate(v.expiry)} ATM IV {v.atm_iv_pct}%{v.hv20_pct != null ? ` vs HV20 ${v.hv20_pct}%` : ''}{v.vrp_pct != null ? ` (${v.vrp_pct > 0 ? '+' : ''}${v.vrp_pct}%)` : ''}
        </span>
      )}
      {ev?.implied_move_pct != null && (
        <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.1] px-2 py-0.5" title={ev.source ? `Isolated from the ${ev.source}` : undefined}>
          <Gauge className="w-3 h-3" />earnings {shortDate(ev.date)} priced ≈ ±{ev.implied_move_pct}%{ev.position === 'after' ? ' · after this expiry' : ' · inside this expiry'}
        </span>
      )}
      {(ev?.term || []).filter(t => t.atm_iv_pct != null && !(v && t.expiry === v.expiry)).map(t => (
        <span key={t.expiry} className="inline-flex items-center gap-1 rounded-full border border-white/[0.1] px-2 py-0.5 text-base-content/45">
          <CalendarClock className="w-3 h-3" />{shortDate(t.expiry)} {t.atm_iv_pct}%{t.spans_earnings ? ' (with the print)' : ''}
        </span>
      ))}
    </div>
  );
}

/** What each leg is doing for you now, and what the position adds up to from here. */
export function PositionAnatomy({ d }: { d: RepairMenuResult }) {
  const an: DefendAnatomy | null | undefined = d.anatomy;
  if (!an || !an.legs?.length) return null;
  const sp = an.spread;
  const zoneTone: Record<string, string> = { max_loss: 'bg-error/70', pin: 'bg-warning/70', max_gain: 'bg-success/70' };
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-100/20 p-3 space-y-3" data-testid="position-anatomy">
      <Head icon={Layers} title="Position anatomy" hint={`what each leg is doing for you now · ${an.dte}d left`} />
      <VolStrip d={d} />

      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-[11px] text-base-content/45 text-right">
              <th className="text-left font-normal px-1 py-1">Leg</th>
              <th className="font-normal px-1 hidden sm:table-cell" title="Bid / ask now">Bid / ask</th>
              <th className="font-normal px-1 hidden sm:table-cell" title="In-the-money part of the price">Intrinsic</th>
              <th className="font-normal px-1" title="Time value left — what can still decay">Time value</th>
              <th className="font-normal px-1" title="Dollars per day from YOUR side (+ = time pays you)">θ / day</th>
              <th className="font-normal px-1" title="Dollars per vol point from YOUR side (+ = rising IV helps you)">Vega</th>
              <th className="font-normal px-1" title="Share-equivalent delta from your side">Δ (sh)</th>
              <th className="font-normal px-1" title="Risk-neutral odds this leg finishes in the money">P(ITM)</th>
            </tr>
          </thead>
          <tbody>
            {an.legs.map(l => (
              <tr key={l.label} className="text-right border-t border-white/[0.05]">
                <td className="text-left px-1 py-1 whitespace-nowrap">
                  <span className="font-semibold">{l.label}</span>
                  <span className="text-base-content/40 hidden sm:inline"> · {shortDate(l.expiry)} · {l.moneyness}</span>
                </td>
                <td className="px-1 whitespace-nowrap hidden sm:table-cell">{l.bid != null && l.ask != null ? `${l.bid.toFixed(2)} / ${l.ask.toFixed(2)}` : px(l.mid)}</td>
                <td className="px-1 hidden sm:table-cell">{px(l.intrinsic)}</td>
                <td className="px-1 font-semibold">{px(l.extrinsic)}</td>
                <td className={`px-1 ${tone(l.theta_day)}`}>{usd(l.theta_day)}</td>
                <td className={`px-1 ${tone(l.vega)}`}>{usd(l.vega)}</td>
                <td className="px-1">{l.delta_sh > 0 ? '+' : ''}{l.delta_sh.toFixed(0)}</td>
                <td className="px-1">{l.p_itm_pct.toFixed(0)}%</td>
              </tr>
            ))}
            <tr className="text-right border-t border-white/[0.12] font-semibold">
              <td className="text-left px-1 py-1">Net</td><td className="hidden sm:table-cell" /><td className="hidden sm:table-cell" /><td />
              <td className={`px-1 ${tone(an.net.theta_day)}`} data-testid="net-theta">{usd(an.net.theta_day)}</td>
              <td className={`px-1 ${tone(an.net.vega)}`}>{usd(an.net.vega)}</td>
              <td className="px-1">{an.net.delta_sh > 0 ? '+' : ''}{an.net.delta_sh.toFixed(0)}</td><td />
            </tr>
          </tbody>
        </table>
      </div>

      {sp && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-1.5 text-xs" data-testid="anatomy-spread">
          <div><div className="text-[11px] text-base-content/45">Spread now (mid / natural)</div><div className="tabular-nums font-semibold">{px(sp.mark)} / {px(sp.close_natural)}</div></div>
          <div><div className="text-[11px] text-base-content/45">P&amp;L so far</div><div className={`tabular-nums font-semibold ${tone(sp.loss_now)}`}>{usd(sp.loss_now)}</div></div>
          <div><div className="text-[11px] text-base-content/45">At most, more to lose</div><div className="tabular-nums font-semibold text-error">{usd(sp.more_to_lose)}</div></div>
          <div><div className="text-[11px] text-base-content/45">At most, back to gain</div><div className="tabular-nums font-semibold text-success">{usd(sp.more_to_gain)}</div></div>
        </div>
      )}

      {an.zones.length > 0 && (
        <div className="space-y-1" data-testid="anatomy-zones">
          <div className="text-[11px] text-base-content/45">Where it can finish at expiry</div>
          <div className="flex h-2 rounded-full overflow-hidden bg-base-content/10">
            {an.zones.map(z => <div key={z.key} className={zoneTone[z.key]} style={{ width: `${Math.max(0, z.p_pct)}%` }} title={`${z.label}: ${z.p_pct}%`} />)}
          </div>
          <div className="grid sm:grid-cols-3 gap-2 text-xs">
            {an.zones.map(z => (
              <div key={z.key} className="leading-snug">
                <div className="font-semibold">{z.label} <span className="text-base-content/50 font-normal">≈{z.p_pct}%</span></div>
                <div className="text-base-content/55">{z.pnl != null ? <b className={tone(z.pnl)}>{usd(z.pnl)}</b> : z.pnl_range ? <span>{usd(z.pnl_range[0])} … {usd(z.pnl_range[1])}</span> : null} — {z.what}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {an.insights.length > 0 && (
        <ul className="space-y-1.5" data-testid="anatomy-insights">
          {an.insights.map((i, n) => {
            const t = TONE_ICON[i.tone] || TONE_ICON.info;
            const Icon = t.icon;
            return <li key={n} className="flex gap-2 text-xs leading-snug"><Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${t.cls}`} /><span>{i.text}</span></li>;
          })}
        </ul>
      )}

      {an.leg_moves.length > 0 && (
        <div className="space-y-1.5 border-t border-white/[0.06] pt-2" data-testid="anatomy-leg-moves">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">Managing one leg on its own</div>
          {an.leg_moves.map((m, n) => (
            <div key={n} className="text-xs leading-snug">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="font-semibold">{m.move}</span>
                <span className={`tabular-nums ${tone(m.cash)}`}>{usd(m.cash)} now</span>
                {m.new_max_loss != null && <span className="text-base-content/50">worst case {usd(m.new_max_loss)}</span>}
                <span className={`rounded-full border px-1.5 text-[10px] uppercase tracking-wide ${m.verdict === 'avoid' ? 'border-error/40 text-error' : 'border-warning/40 text-warning'}`}>
                  {m.verdict === 'avoid' ? 'avoid' : 'a directional bet'}
                </span>
              </div>
              <div className="text-base-content/60">{m.text}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** One order line per leg, from the alternative's own steps: "Buy to close 1× $80 put · Oct 30 @ $12.90". */
function Orders({ a }: { a?: RepairAlternative }) {
  const st = a?.steps;
  if (!st) return null;
  const lines = [...st.close.map(x => describeStep(x, 'close')), ...st.open.map(x => describeStep(x, 'open')),
    ...(st.stock ? [describeStep(st.stock, 'open')] : [])];
  if (!lines.length) return <div className="text-xs text-base-content/50">No orders — nothing to trade.</div>;
  return (
    <div className="space-y-0.5">
      {lines.map((l, i) => <div key={i} className="font-mono text-xs text-base-content/80">{l}</div>)}
      <div className="text-[11px] text-base-content/45">Send as ONE combo order, limit near mid · net {usd(st.net_cash)}</div>
    </div>
  );
}

function Num({ label, v, cls, pct, title, none = '—' }: { label: string; v: number | null | undefined; cls?: string; pct?: boolean; title?: string; none?: string }) {
  return (
    <div className="min-w-0" title={title}>
      <div className="text-[10px] uppercase tracking-wide text-base-content/40 truncate">{label}</div>
      <div className={`text-sm font-semibold tabular-nums ${cls ?? ''}`}>{v == null ? none : pct ? `${v}%` : usd(v)}</div>
    </div>
  );
}

function ActionRowView({ r, a }: { r: DefendActionRow; a?: RepairAlternative }) {
  const [open, setOpen] = useState(false);
  const orders = r.orders && r.orders.length ? r.orders : null;
  return (
    <div className={`rounded-xl border p-3 space-y-2 ${r.is_pick ? 'border-secondary/50 bg-secondary/[0.07]' : 'border-white/[0.08] bg-base-100/20'}`} data-testid="action-row">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className={`text-[10px] font-semibold uppercase tracking-wide ${r.is_pick ? 'text-secondary' : r.tier === 'more' ? 'text-warning/80' : 'text-base-content/45'}`}>{r.is_pick ? `★ Pick · ${r.tag}` : r.tag}</span>
        <span className="text-sm font-semibold">{r.title}</span>
      </div>
      <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
        <Num label="Cash today" v={r.cash} cls={tone(r.cash)} title={r.cash_mid != null && r.cash_mid !== r.cash ? `at a realistic fill; ${usd(r.cash_mid)} at mid` : undefined} />
        <Num label="Worst" v={r.worst} cls="text-error" />
        <Num label="Best" v={r.best} cls={r.best == null ? 'text-success' : tone(r.best)} none={r.worst != null ? 'no cap' : '—'} />
        <Num label="Lose less" v={r.beat_close_pct} pct title="Odds it ends meaningfully better than closing now" />
        <Num label="E[P&L]" v={r.ev} cls={tone(r.ev)} title="Expected all-in P&L on the market's own odds" />
      </div>
      {orders && (
        <div className="space-y-0.5" data-testid="action-orders">
          {orders.map((o, i) => <div key={i} className="font-mono text-xs text-base-content/80">{o}</div>)}
        </div>
      )}
      {!orders && a && r.tag !== 'Do nothing' && <Orders a={a} />}
      {r.why && <p className="text-xs text-base-content/65 leading-snug">{r.why}</p>}
      {r.when && <p className="text-xs leading-snug flex gap-1.5 text-warning/90"><Clock className="w-3.5 h-3.5 mt-0.5 shrink-0" /><span>{r.when}</span></p>}
      {r.alt_route && (
        <div>
          <button type="button" className="text-xs text-info hover:underline" aria-expanded={open} onClick={() => setOpen(o => !o)}>{open ? '▾' : '▸'} Same thing as a collar (take the shares, sell a covered call)</button>
          {open && (
            <div className="mt-1.5 space-y-0.5 border-l border-white/[0.1] pl-2.5" data-testid="alt-route">
              {r.alt_route.steps.map((x, i) => <div key={i} className="text-xs text-base-content/75">{i + 1}. {x}</div>)}
              <div className="text-[11px] text-base-content/45 leading-snug">{r.alt_route.note}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const SIDE: Record<string, { label: string; cls: string }> = {
  bounce: { label: 'bounce case', cls: 'border-success/40 text-success' },
  drop: { label: 'further drop', cls: 'border-error/40 text-error' },
  vol: { label: 'volatility', cls: 'border-info/40 text-info' },
  event: { label: 'earnings', cls: 'border-warning/40 text-warning' },
  market: { label: 'tape', cls: 'border-white/20 text-base-content/60' },
};

/** TODAY OR WAIT — each action today vs on the last day (flat / bounce / drop) and if IV cools; the signals behind it. */
export function TimingRead({ d }: { d: RepairMenuResult }) {
  const t = d.timing_read;
  if (!t || (!t.rows.length && !t.signals.length)) return null;
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-100/20 p-3 space-y-2" data-testid="timing-read">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-sm font-semibold">Today or wait?</span>
        {t.verdict && <span className={`rounded-full px-2 text-[11px] font-semibold ${t.verdict === 'today' ? 'bg-warning/15 text-warning' : 'bg-info/15 text-info'}`}>{t.verdict === 'today' ? 'Today' : t.verdict === 'last_day' ? 'Last day' : 'Either'}</span>}
      </div>
      {t.headline && <p className="text-xs leading-snug text-base-content/80">{t.headline}</p>}
      {t.rows.length > 0 && (
        <div className="overflow-x-auto -mx-1">
          <table className="w-full text-xs tabular-nums">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-base-content/40 text-right">
                <th className="text-left font-normal px-1 py-1" title="Mid prices, so today and the last day compare like for like (the cards above add the fill cost)">Cash for the move · at mid</th>
                <th className="font-normal px-1">Today</th>
                <th className="font-normal px-1" title={`On the last day, ${t.wait_days ?? ''} days from now, at an unchanged price`}>Last day</th>
                <th className="font-normal px-1 hidden sm:table-cell" title="On the last day after a 1σ bounce">{t.up_spot ? `If ${px(t.up_spot)}` : 'Bounce'}</th>
                <th className="font-normal px-1 hidden sm:table-cell" title="On the last day after a 1σ drop">{t.down_spot ? `If ${px(t.down_spot)}` : 'Drop'}</th>
                <th className="font-normal px-1" title="Today, if implied vol drops 10 points">IV −10</th>
              </tr>
            </thead>
            <tbody>
              {t.rows.map((r, i) => (
                <tr key={i} className="text-right border-t border-white/[0.05]">
                  <td className="text-left px-1 py-1 max-w-[14rem] truncate" title={r.title}>{r.title}</td>
                  <td className={`px-1 font-semibold ${tone(r.now)}`}>{usd(r.now)}</td>
                  <td className={`px-1 ${r.flat < r.now ? 'text-error' : 'text-success'}`}>{usd(r.flat)}</td>
                  <td className={`px-1 hidden sm:table-cell ${r.up != null && r.up < r.now ? 'text-error' : 'text-success'}`}>{usd(r.up)}</td>
                  <td className={`px-1 hidden sm:table-cell ${r.down != null && r.down < r.now ? 'text-error' : 'text-success'}`}>{usd(r.down)}</td>
                  <td className={`px-1 ${r.iv_down != null && r.iv_down < r.now ? 'text-error' : 'text-success'}`}>{usd(r.iv_down)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {t.signals.length > 0 && (
        <ul className="space-y-1" data-testid="timing-signals">
          {t.signals.map((g, i) => {
            const sd = SIDE[g.side] || SIDE.market;
            return <li key={i} className="flex gap-2 text-xs leading-snug"><span className={`shrink-0 rounded-full border px-1.5 text-[10px] h-fit ${sd.cls}`}>{sd.label}</span><span className="text-base-content/75">{g.text}</span></li>;
          })}
        </ul>
      )}
    </div>
  );
}

const STATUS: Record<DefendExplorerRow['status'], { label: string; cls: string }> = {
  rule: { label: 'within your rule', cls: 'border-success/40 text-success' },
  more: { label: 'adds risk', cls: 'border-warning/40 text-warning' },
  unlimited: { label: 'unlimited risk', cls: 'border-error/40 text-error' },
};

/** EVERY STRATEGY TESTED — each family's best version (sell a call / a call spread / jade / butterflies / backspread / calendar /
 *  diagonal / rolls / the collar …) with the same numbers as the plan and, in one line, why it is or isn't the answer. */
export function StrategyExplorer({ rows, searched }: { rows: DefendExplorerRow[]; searched?: number | null }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!rows.length) return null;
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-100/20 p-3 space-y-2" data-testid="strategy-explorer">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-sm font-semibold">Every strategy tested</span>
        <span className="text-[11px] text-base-content/40">{rows.length} families{searched ? ` · ${searched.toLocaleString('en-US')} versions priced` : ''} · the best version of each · tap for its orders</span>
      </div>
      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-[10px] uppercase tracking-wide text-base-content/40 text-right">
              <th className="text-left font-normal px-1 py-1">Strategy · best version</th>
              <th className="font-normal px-1">Cash</th>
              <th className="font-normal px-1">Worst</th>
              <th className="font-normal px-1 hidden sm:table-cell">Best</th>
              <th className="font-normal px-1" title="Odds it ends meaningfully better than closing now">Lose less</th>
              <th className="font-normal px-1 hidden sm:table-cell">E[P&amp;L]</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((x, i) => {
              const st = STATUS[x.status];
              const b = x.best;
              return (
                <Fragment key={i}>
                  <tr className="text-right border-t border-white/[0.05] cursor-pointer hover:bg-white/[0.02]" onClick={() => setOpen(o => (o === i ? null : i))} data-testid="explorer-row">
                    <td className="text-left px-1 py-1.5">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-semibold">{x.family}</span>
                        <span className={`rounded-full border px-1.5 text-[10px] ${st.cls}`}>{st.label}</span>
                      </div>
                      <div className="text-base-content/55 truncate max-w-[22rem]" title={b.title}>{b.title}</div>
                      <div className="text-[11px] text-base-content/45">{x.why}</div>
                    </td>
                    <td className={`px-1 align-top pt-1.5 ${tone(b.cash_fill)}`}>{usd(b.cash_fill)}</td>
                    <td className="px-1 align-top pt-1.5 text-error">{b.worst == null ? 'unlimited' : usd(b.worst)}</td>
                    <td className={`px-1 align-top pt-1.5 hidden sm:table-cell ${tone(b.best)}`}>{b.best == null ? 'no cap' : usd(b.best)}</td>
                    <td className="px-1 align-top pt-1.5">{b.p_beat == null ? '—' : `${b.p_beat}%`}</td>
                    <td className={`px-1 align-top pt-1.5 hidden sm:table-cell ${tone(b.ev)}`}>{usd(b.ev)}</td>
                  </tr>
                  {open === i && (
                    <tr><td colSpan={6} className="px-1 pb-2">
                      <div className="border-l border-white/[0.1] pl-2.5 space-y-0.5">{b.orders.map((o, j) => <div key={j} className="font-mono text-xs text-base-content/80">{o}</div>)}</div>
                    </td></tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** WHAT TO DO — the whole desk on one card: a verdict, a few facts, then each action with the same numbers and its orders. */
export function ActionPlan({ d }: { d: RepairMenuResult }) {
  const ap = d.action_plan;
  if (!ap || !ap.rows.length) return null;
  const byName = new Map((d.alternatives || []).map(a => [a.name, a]));
  const tiers: { key: string; title: string }[] = [
    { key: 'rule', title: ap.rule_worst != null ? `Within your rule — worst case stays at or below ${usd(ap.rule_worst)}` : 'Your options' },
    { key: 'more', title: 'A little more risk for better odds' },
    { key: 'close', title: 'Or take the loss' },
  ];
  return (
    <div className="space-y-2.5" data-testid="action-plan">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-sm font-semibold">What to do</span>
        <span className="text-[11px] text-base-content/40">all-in · realistic fills · market-implied odds{ap.need ? ` · “lose less” = at least ${usd(ap.need, false)} better than closing now` : ''}</span>
      </div>
      {ap.verdict && <p className="text-[13px] font-semibold leading-snug" data-testid="action-verdict">{ap.verdict}</p>}
      {ap.facts.length > 0 && (
        <div className="flex flex-wrap gap-1.5" data-testid="action-facts">
          {ap.facts.map((f, i) => <span key={i} className="rounded-full border border-white/[0.1] bg-base-content/[0.04] px-2 py-0.5 text-[11px] text-base-content/70">{f}</span>)}
        </div>
      )}
      {tiers.map(tr => {
        const rows = ap.rows.filter(r => (r.tier || 'rule') === tr.key);
        if (!rows.length) return null;
        return (
          <div key={tr.key} className="space-y-2" data-testid={`tier-${tr.key}`}>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">{tr.title}</div>
            {rows.map((r, i) => <ActionRowView key={`${tr.key}${i}`} r={r} a={r.alt ? byName.get(r.alt) : undefined} />)}
          </div>
        );
      })}
      <TimingRead d={d} />
      {ap.explorer && ap.explorer.length > 0 && <StrategyExplorer rows={ap.explorer} searched={ap.searched} />}
    </div>
  );
}

/** Expiry by expiry: how far a no-new-money roll can move the short — cash-secured or as a wider defined-risk spread. */
export function AssignmentLadder({ d }: { d: RepairMenuResult }) {
  const lad: DefendLadder | null | undefined = d.ladder;
  if (!lad || !lad.rungs?.length) return null;
  const put = lad.right === 'P';
  const word = put ? 'put' : 'call';
  const anySpread = lad.rungs.some(r => r.spread || r.same_width);
  const anyCsp = lad.rungs.some(r => r.csp);
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-100/20 p-3 space-y-2" data-testid="assignment-ladder">
      <Head icon={ArrowDownRight} title="Assignment ladder" hint="how far each later expiry lets you move the short for no new money" />
      <p className="text-xs text-base-content/60 leading-snug">
        Closing what you hold costs <b className="text-base-content/80">{usd(lad.unwind.mid, false)}</b> at mid
        {lad.unwind.natural != null && <> ({usd(lad.unwind.natural, false)} at the natural)</>}; you collected {usd(lad.credit_collected, false)} when you opened it.
        Each row is the furthest {word} strike whose premium pays for that close — time{lad.rungs.some(r => r.spans_earnings) ? ' and the earnings premium' : ''} buy the better price.
      </p>
      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-xs tabular-nums">
          <thead>
            <tr className="text-[11px] text-base-content/45 text-right">
              <th className="text-left font-normal px-1 py-1">Expiry</th>
              {anyCsp && <th className="text-left font-normal px-1" colSpan={1} title="Close what you hold, sell ONE put further away; if assigned you own the shares at the effective price">Cash-secured {word}</th>}
              {anyCsp && <th className="font-normal px-1">Net cash</th>}
              {anyCsp && <th className="font-normal px-1" title="The effective price you'd own the shares at after every credit — vs today's stock price">{put ? 'Own at' : 'Short at'}</th>}
              {anyCsp && <th className="font-normal px-1" title="Risk-neutral odds of being assigned at that expiry">P(assign)</th>}
              {anySpread && <th className="text-left font-normal px-1 pl-3" title="A defined-risk spread (up to 1.5× today's width) that pays for the close">Defined-risk spread</th>}
              {anySpread && <th className="font-normal px-1">Net · worst</th>}
              {anySpread && <th className="font-normal px-1" title="Odds the whole position (old loss included) finishes in profit">P(profit)</th>}
              {anySpread && <th className="font-normal px-1" title="Rolling the SAME strikes to this expiry">Same strikes</th>}
            </tr>
          </thead>
          <tbody>
            {lad.rungs.map(r => (
              <tr key={r.expiry} className="text-right border-t border-white/[0.05]">
                <td className="text-left px-1 py-1 whitespace-nowrap">
                  <span className="font-semibold">{shortDate(r.expiry)}{r.expiry.slice(0, 4) !== new Date().getFullYear().toString() ? ` ’${r.expiry.slice(2, 4)}` : ''}</span>
                  <span className="text-base-content/40"> · {r.dte}d</span>
                  {r.spans_earnings && <span className="ml-1 rounded-full border border-warning/40 text-warning px-1 text-[10px]" title="This expiry carries the earnings print">print</span>}
                </td>
                {anyCsp && <td className="text-left px-1">{r.csp ? `−1 ${k$(r.csp.strike)} ${word} @ ${r.csp.premium.toFixed(2)}` : '—'}</td>}
                {anyCsp && <td className={`px-1 ${tone(r.csp?.net_cash)}`} title={r.csp?.net_cash_natural != null ? `at the natural: ${usd(r.csp.net_cash_natural)}` : undefined}>{r.csp ? usd(r.csp.net_cash) : ''}</td>}
                {anyCsp && <td className="px-1">{r.csp ? <><b>{px(r.csp.basis)}</b> <span className="text-base-content/40">({r.csp.basis_vs_spot_pct > 0 ? '+' : ''}{r.csp.basis_vs_spot_pct}%)</span></> : ''}</td>}
                {anyCsp && <td className="px-1">{r.csp ? `${r.csp.p_assigned_pct}%` : ''}</td>}
                {anySpread && <td className="text-left px-1 pl-3 whitespace-nowrap">{r.spread ? `${k$(r.spread.short)}/${k$(r.spread.long).slice(1)}${r.spread.wider ? ` (${r.spread.width} wide)` : ''}` : '—'}</td>}
                {anySpread && <td className="px-1 whitespace-nowrap">{r.spread ? <><span className={tone(r.spread.net_cash)}>{usd(r.spread.net_cash)}</span> · <span className="text-error">{usd(r.spread.max_loss)}</span></> : ''}</td>}
                {anySpread && <td className="px-1">{r.spread ? `${r.spread.p_profit_pct}%` : ''}</td>}
                {anySpread && <td className={`px-1 ${tone(r.same_width?.net_cash)}`}>{r.same_width ? usd(r.same_width.net_cash) : ''}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-base-content/40 leading-relaxed">
        Mid prices; work any roll as ONE combo order with a limit near mid. A cash-secured {word} hands the tail to the stock (no long {word} behind it) —
        it is the answer for "lowest cost if assigned", not a defined-risk defense. The rows marked on the menu above are priced in full there.
      </p>
    </div>
  );
}
