/**
 * BetaBookConcentration — the Concentration tab of the Beta Manage Book.
 *
 * A list of "AMD is 38% of gamma" is a fact, not a decision. This tab answers the questions you act on:
 *   1. How many INDEPENDENT bets do I really hold?   (names merged by measured correlation → effective bets)
 *   2. Where is the risk vs where is the money?     (per name: share of capital · share of the tail loss · share of income,
 *                                                     and how many years of its own θ the tail loss would take to earn back)
 *   3. What does one group do if IT gaps?           (each correlated cluster / sector repriced on its own: −30…+30%)
 *
 * Every number comes from the backend's `concentration_view` — full reprices and plain shares, nothing invented.
 */
import React from 'react';
import { AlertTriangle, Layers, CheckCircle2, Compass } from 'lucide-react';
import type { BookTailRiskResult } from '../api';
import { Callout, Chip, SubHead } from './BetaKit';

type CV = NonNullable<BookTailRiskResult['concentration_view']>;

const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
const sgn = (v: number, plus = false) => `${v < 0 ? '−' : plus && v > 0 ? '+' : ''}${Math.abs(v)}`;
const span = (days: number) => (days >= 365 ? `${(days / 365).toFixed(1)} yr` : `${Math.round(days)} d`);

function Bar({ pct, cls, label, title }: { pct: number; cls: string; label: string; title: string }) {
  return (
    <div className="flex items-center gap-2" title={title}>
      <span className="w-9 text-[11px] text-base-content/45 shrink-0">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-base-content/[0.08] overflow-hidden"><div className={`h-full rounded-full ${cls}`} style={{ width: `${Math.max(pct > 0 ? 2 : 0, Math.min(100, pct))}%` }} /></div>
      <span className="w-10 text-right text-[11px] font-mono tabular-nums text-base-content/70 shrink-0">{pct.toFixed(0)}%</span>
    </div>
  );
}

export default function BetaBookConcentration({ data }: { data: BookTailRiskResult }) {
  const cv: CV | null | undefined = data.concentration_view;
  if (!cv || cv.names.length === 0) {
    return <Callout tone="neutral" icon={<Layers className="w-4 h-4" />}>Press <b>Refresh</b> to compute the concentration view for your Income trades.</Callout>;
  }
  const top = cv.names[0];
  const sideWord = cv.side === 'up' ? 'squeeze' : 'crash';
  const macro = data.factor_exposure?.macro ?? [];
  const bookRow = cv.groups.find(g => g.kind === 'book');
  const groups = cv.groups;
  const gmax = Math.max(1, ...groups.flatMap(g => Object.values(g.shocks).map(v => Math.abs(v))));
  const heat = (v: number) => { const t = Math.min(1, Math.abs(v) / gmax); return v < 0 ? `rgba(239,68,68,${0.08 + 0.55 * t})` : v > 0 ? `rgba(34,197,94,${0.08 + 0.45 * t})` : 'transparent'; };
  const diversified = cv.effective_bets != null && cv.n_names > 1 && cv.effective_bets >= 0.7 * cv.n_names;

  return (
    <div className="space-y-5">
      {/* 1 · how many independent bets */}
      <Callout tone={diversified ? 'good' : 'warn'} icon={diversified ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}>
        <div><b>{cv.n_names} name{cv.n_names === 1 ? '' : 's'} behave like ~{cv.effective_bets ?? '—'} independent bet{cv.effective_bets === 1 ? '' : 's'}.</b>
          {cv.effective_names != null && cv.effective_bets != null && cv.effective_bets < cv.effective_names - 0.05 &&
            <> Counting each name separately gives {cv.effective_names}; names that move together (measured ρ) are one bet, so the real number is lower.</>}</div>
        <div className="mt-1 opacity-90">
          Your tail is a <b>{sideWord}</b> ({cv.side_label}). <span className="font-mono">{top.ticker}</span> alone is <b>{top.tail_pct.toFixed(0)}%</b> of that loss on {top.capital_pct.toFixed(0)}% of the capital
          {top.payback_days != null && <> — it would take {span(top.payback_days)} of its own θ to earn the loss back</>}.
        </div>
      </Callout>

      {/* 2 · risk vs capital vs income */}
      <div>
        <SubHead hint={`share of capital · share of the ${sideWord} loss · share of θ income`}>Where the risk is vs where the money is</SubHead>
        <div className="rounded-xl border border-white/[0.07] bg-base-100/20 divide-y divide-white/[0.05]">
          {cv.names.slice(0, 8).map(n => {
            const heavy = n.risk_ratio != null && n.risk_ratio >= 1.5 && n.tail_pct >= 10;
            return (
              <div key={n.ticker} className="px-3 py-2.5 grid grid-cols-[4.5rem_1fr] sm:grid-cols-[5rem_1fr_9.5rem] gap-x-3 gap-y-1.5 items-center">
                <div className="min-w-0">
                  <div className="font-mono text-sm font-semibold">{n.ticker}</div>
                  <div className="text-[11px] text-base-content/45">β {n.beta} · {n.trades} trade{n.trades === 1 ? '' : 's'}</div>
                </div>
                <div className="space-y-1 min-w-0">
                  <Bar pct={n.capital_pct} cls="bg-base-content/40" label="capital" title={`${money(n.capital)} committed (${n.capital_pct}% of the book's capital)`} />
                  <Bar pct={n.tail_pct} cls="bg-error/60" label="loss" title={`${money(-n.tail_loss)} in the ${cv.side_label} scenario (${n.tail_pct}% of the book's loss on that side)`} />
                  <Bar pct={n.theta_pct} cls="bg-success/55" label="income" title={`${money(n.theta)}/day of θ (${n.theta_pct}% of the book's income)`} />
                </div>
                <div className="col-span-2 sm:col-span-1 flex sm:flex-col sm:items-end items-center gap-1.5 flex-wrap">
                  {heavy && <Chip tone="warn" title="Its share of the tail loss is far above its share of the capital">risk {n.risk_ratio}× its capital</Chip>}
                  <span className="text-[11px] text-base-content/50 tabular-nums" title="Tail loss ÷ this name's daily θ: how long its own income takes to repay one bad month">
                    {n.payback_days != null ? `payback ${span(n.payback_days)}` : n.tail_loss > 0 ? 'no income' : 'no tail loss'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
        {cv.names.length > 8 && <div className="text-[11px] text-base-content/40 mt-1">+ {cv.names.length - 8} smaller names</div>}
        <p className="text-xs text-base-content/50 mt-2 leading-relaxed">
          <b>How it's calculated:</b> “loss” is each name's full Black-Scholes reprice at the book's dominant tail ({cv.side_label}: the market moves, the name moves β× that, vol shifts), as a share of the book's total loss on that side. “Payback” = that loss ÷ the name's daily θ.
          <b> How to use it:</b> a name whose <i>loss</i> bar towers over its <i>income</i> bar is badly paid for its risk — cap, roll or shrink that one first (see Fixes).
        </p>
      </div>

      {/* 3 · what a gapping group does */}
      {groups.length > 0 && (
        <div>
          <SubHead hint="every member of the group moves X% itself; the rest of the book is flat">If a group gaps</SubHead>
          <div className="overflow-x-auto rounded-xl border border-white/[0.07]">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-base-content/45 text-left">
                  <th className="px-3 py-2 font-normal">Group</th>
                  {cv.shock_moves.map(m => <th key={m} className="px-1.5 py-2 font-mono font-normal text-center whitespace-nowrap">{sgn(m, true)}%</th>)}
                  <th className="px-3 py-2 font-normal text-right whitespace-nowrap" title="Worst shock ÷ the group's committed capital">Worst / capital</th>
                  <th className="px-3 py-2 font-normal text-right whitespace-nowrap" title="Worst shock ÷ the group's own annual θ income">Years of its carry</th>
                </tr>
              </thead>
              <tbody>
                {groups.map(g => (
                  <tr key={`${g.kind}:${g.label}`} className="border-t border-white/[0.05]">
                    <td className="px-3 py-2 min-w-[11rem]">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-semibold text-[13px]">{g.kind === 'book' ? 'Whole book' : g.label}</span>
                        {g.kind === 'cluster' && <Chip tone="warn" title="Measured correlation — these names move together">{g.avg_rho != null ? `ρ ${g.avg_rho}` : 'correlated'}</Chip>}
                        {g.kind === 'sector' && <Chip tone="neutral">sector</Chip>}
                      </div>
                      <div className="text-[11px] text-base-content/45">{g.kind === 'book' ? `all ${g.tickers.length} names move together` : g.tickers.join(' · ')} · {g.capital_pct.toFixed(0)}% of capital</div>
                    </td>
                    {cv.shock_moves.map(m => {
                      const key = sgn(m, true).replace('−', '-');
                      const v = g.shocks[key];
                      return (
                        <td key={m} className="px-1 py-1.5 text-center">
                          <div className="rounded px-1.5 py-1 font-mono tabular-nums whitespace-nowrap" style={{ background: heat(v ?? 0), minWidth: 58 }}>
                            <span className={(v ?? 0) < 0 ? 'text-error' : (v ?? 0) > 0 ? 'text-success' : 'text-base-content/40'}>{money(v)}</span>
                          </div>
                        </td>
                      );
                    })}
                    <td className={`px-3 text-right font-mono tabular-nums ${g.worst_pct_capital != null && g.worst_pct_capital <= -25 ? 'text-error font-semibold' : 'text-base-content/70'}`}>
                      {g.worst_pct_capital != null ? `${sgn(g.worst_pct_capital)}%` : '—'}
                    </td>
                    <td className={`px-3 text-right font-mono tabular-nums ${g.carry_years != null && g.carry_years >= 1 ? 'text-warning' : 'text-base-content/70'}`}>
                      {g.carry_years != null ? `${g.carry_years} yr` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-xs text-base-content/50 mt-2 leading-relaxed space-y-1">
            <p><b>How it's calculated:</b> for each correlated cluster and sector, only its members are shocked by −30 … +30% (vol shifts with the move, as in the stress table) and fully repriced; “Years of its carry” = the worst case ÷ what the group earns in θ per year.</p>
            <p className="flex items-start gap-1.5"><Compass className="w-3.5 h-3.5 mt-0.5 shrink-0 text-secondary" /><span><b>How to use it:</b> a group whose worst case is several years of its own carry is not paying for itself — shrink it, cap its short strikes, or add income from a different sector instead of one more name in the same cluster.{bookRow && <> The whole-book row is what a market-wide move does if every name moves the same amount (β = 1).</>}</span></p>
          </div>
        </div>
      )}

      {/* macro loadings */}
      {macro.length > 0 && (
        <div>
          <SubHead hint="measured correlation of the book's return">What the whole book loads on</SubHead>
          <div className="flex flex-wrap gap-1.5">
            {macro.map(m => <Chip key={m.factor} tone={Math.abs(m.rho) >= 0.6 ? 'warn' : 'neutral'} title={`Book return vs ${m.label}: measured ρ = ${sgn(m.rho)}`}>{m.label} {sgn(m.rho, true)}</Chip>)}
          </div>
        </div>
      )}
    </div>
  );
}
