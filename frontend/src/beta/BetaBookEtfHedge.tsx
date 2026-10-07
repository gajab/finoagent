/**
 * BetaBookEtfHedge — "ETF hedges around your holdings" (inside the Hedge tab).
 *
 * Similar names are GROUPED first (measured correlation + shared themes); each group is matched, as a basket, to the ETF it
 * tracks best (a basket tracks an ETF far better than any single name). The hedge is a PUT on that ETF sized to offset ~60%
 * of the group's loss at a −15% ETF move, funded by selling a call (collar), a lower put (put spread) or not at all. Priced from the live ETF chain (put at the ask, sold leg at the bid) and set against income.
 * The sold call is the user's "covered call to recover cost" — it is covered only if you hold the ETF; otherwise it is a
 * short call and adds upside risk, which the scenario table makes explicit. Every number comes from `etf_hedges`.
 */
import React, { useState } from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, Compass, Layers } from 'lucide-react';
import type { BookTailRiskResult, EtfCandidate, EtfVariant } from '../api';
import { Accordion, Callout, Chip, SubHead, Tile, type Tone } from './BetaKit';

const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
const sgn = (v: number, plus = false) => `${v < 0 ? '−' : plus && v > 0 ? '+' : ''}${Math.abs(v)}`;
const STRENGTH: Record<string, { tone: Tone; text: string }> = {
  strong: { tone: 'good', text: 'tight tracking' }, good: { tone: 'good', text: 'tracks well' },
  loose: { tone: 'warn', text: 'loose tracking — expect basis risk' }, weak: { tone: 'bad', text: 'weak tracking' },
};
const expiryText = (iso: string) => { const d = new Date(`${iso}T00:00:00`); return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); };

function legsText(c: EtfCandidate, v: EtfVariant): string[] {
  const e = expiryText(c.expiry);
  const out = [`BUY ${v.qty}× ${c.etf} ${e} ${v.put.strike} put @ $${v.put.ask.toFixed(2)}`];
  if (v.short) out.push(`SELL ${v.qty}× ${c.etf} ${e} ${v.short.strike} ${v.short.right === 'C' ? 'call' : 'put'} @ $${v.short.bid.toFixed(2)}`);
  return out;
}

function Card({ c, budget, onCopy, copied }: { c: EtfCandidate; budget: number; onCopy: (t: string, k: string) => void; copied: string | null }) {
  const [key, setKey] = useState<EtfVariant['key']>(c.variants.some(v => v.key === 'collar50') ? 'collar50' : c.variants[0].key);
  const v = c.variants.find(x => x.key === key) ?? c.variants[0];
  const st = STRENGTH[c.strength];
  const lines = legsText(c, v);
  const up20 = v.scenarios.find(s => s.move_pct === 20);
  const ticket = `${lines.join('; ')} — net ${v.is_credit ? 'credit' : 'debit'} ~${money(Math.abs(v.net_debit))}`;
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-base-200/25 p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold">{c.group_label}</span>
        <span className="text-xs text-base-content/45">→</span>
        <span className="font-mono text-base font-semibold">{c.etf}</span>
        <span className="text-xs text-base-content/55">{c.label}</span>
        <Chip tone={st.tone} title={`The group's basket vs ${c.etf}: ρ ${c.avg_rho}. Individual names range ρ ${c.member_rho_min}–${c.member_rho_max} — a basket tracks the ETF better than any single name.`}>basket ρ {c.avg_rho} · {st.text}</Chip>
        <span className="text-xs text-base-content/45 tabular-nums">${c.spot} · {expiryText(c.expiry)} ({c.dte}d)</span>
        {c.indicative && <Chip tone="info" title="Market closed: option prices are last-trade based — re-check at the open">indicative prices</Chip>}
        <span className="ml-auto text-xs text-base-content/55">group loses <b className="text-error tabular-nums">{money(c.loss_ref)}</b> at ETF {sgn(c.ref_move)}%</span>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-base-content/60">
        {c.n_members} names, grouped by similarity (names ρ {c.member_rho_min}–{c.member_rho_max}):
        {c.members.slice(0, 9).map(m => (
          <span key={m.ticker} title={`ρ ${m.rho} · β on ${c.etf} ${m.beta} · loses ${money(m.loss_ref)} at ETF ${sgn(c.ref_move)}%`}>
            <span className="font-mono text-base-content/80">{m.ticker}</span> <span className="text-base-content/40">ρ{m.rho}</span> {m.loss_ref > 0 ? money(m.loss_ref) : ''}</span>))}
      </div>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label={`${c.etf} funding style`}>
        {c.variants.map(x => (
          <button key={x.key} aria-pressed={x.key === key} onClick={() => setKey(x.key)}
            className={`rounded-lg border px-2.5 py-1.5 text-xs text-left transition-colors ${x.key === key ? 'border-primary/60 bg-primary/15 text-primary font-semibold' : 'border-white/10 text-base-content/70 hover:border-white/25'}`}>{x.label}</button>
        ))}
      </div>

      <div className="rounded-xl border border-white/[0.07] bg-base-100/20 px-3 py-2 font-mono text-xs leading-relaxed space-y-0.5">
        {lines.map((l, i) => <div key={i} className={i === 0 ? 'text-success' : 'text-warning'}>{l}</div>)}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label={v.is_credit ? 'Net credit' : 'Net cost now'} value={money(Math.abs(v.net_debit))} tone={v.is_credit ? 'good' : 'warn'} sub={v.short ? `${v.funded_pct}% funded by the ${v.short.right === 'C' ? 'call' : 'put'}` : 'no funding leg'} />
        <Tile label="Annual cost" value={money(v.annual_cost)} tone={v.within_budget ? 'good' : 'warn'}
          sub={v.cost_pct_income != null ? `${v.cost_pct_income}% of income${v.within_budget ? '' : ` · over the ${budget}% budget`}` : 'no income to compare'} title={`Net cost × ${(365 / c.dte).toFixed(1)} rolls a year`} />
        <Tile label={`Offsets at ${sgn(c.ref_move)}%`} value={`${v.offset_pct}%`} tone={v.offset_pct >= 50 ? 'good' : 'warn'} sub={`of the group's ${money(c.loss_ref)} loss`} />
        <Tile label="Upside given up" value={v.short?.right === 'C' ? money(up20?.hedge_pnl) : '$0'} tone={v.adds_upside_risk ? 'bad' : 'good'}
          sub={v.short?.right === 'C' ? `if ${c.etf} +20% (the sold call)` : 'no call sold'} />
      </div>

      {v.adds_upside_risk && (
        <Callout tone={v.tail_is_upside ? 'bad' : 'warn'} icon={<AlertTriangle className="w-3.5 h-3.5" />}>
          The sold {c.etf} {v.short?.strike} call is <b>covered only if you hold {c.etf}</b> (your semis shares are a partial, correlated cover). Otherwise it is a short call and adds to your upside risk
          {v.tail_is_upside && <> — and your book's tail is <b>already the upside</b>, so prefer the put spread or put-only here</>}.
        </Callout>
      )}

      <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
        <table className="w-full text-xs">
          <thead><tr className="text-base-content/45 text-left"><th className="px-3 py-1.5 font-normal">If {c.etf} moves</th>
            {v.scenarios.map(s => <th key={s.move_pct} className="px-2 font-mono font-normal text-right whitespace-nowrap">{sgn(s.move_pct, true)}%</th>)}</tr></thead>
          <tbody>
            {([['Group P&L', 'group_pnl'], ['Hedge', 'hedge_pnl'], ['Net', 'net']] as const).map(([label, k]) => (
              <tr key={k} className={`border-t border-white/[0.05] ${k === 'net' ? 'font-semibold bg-base-content/[0.04]' : ''}`}>
                <td className="px-3 py-1.5 text-base-content/65">{label}</td>
                {v.scenarios.map(s => <td key={s.move_pct} className={`px-2 text-right font-mono tabular-nums whitespace-nowrap ${s[k] < 0 ? 'text-error' : s[k] > 0 ? 'text-success' : 'text-base-content/40'}`}>{money(s[k])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-base-content/45 leading-relaxed">Each name moves its measured β-to-{c.etf} × the ETF move (full Black-Scholes reprice, vol shifting with the move); the hedge is repriced the same way and net of crossing the bid/ask. Annual cost is the net debit × rolls a year.</p>

      <div className="flex items-center gap-2">
        <button className={`btn btn-sm gap-1.5 normal-case ${copied === c.etf ? 'btn-success' : 'btn-outline'}`} onClick={() => onCopy(ticket, c.etf)} title="Copies the ticket text — nothing is placed">
          {copied === c.etf ? <><CheckCircle2 className="w-4 h-4" /> Copied</> : <>Copy ticket <ArrowRight className="w-4 h-4" /></>}
        </button>
        <span className="text-xs text-base-content/45">{v.n_within_budget != null ? `${v.n_within_budget} contract${v.n_within_budget === 1 ? '' : 's'} fit a ${budget}% income budget` : ''}</span>
      </div>
    </div>
  );
}

export default function BetaBookEtfHedge({ data }: { data: BookTailRiskResult }) {
  const e = data.etf_hedges;
  const [copied, setCopied] = useState<string | null>(null);
  if (!e) return null;
  const copy = async (text: string, key: string) => { try { await navigator.clipboard.writeText(text); setCopied(key); setTimeout(() => setCopied(null), 2000); } catch { /* clipboard blocked */ } };
  return (
    <div className="space-y-3">
      <SubHead hint="nearest ETF by measured correlation · a put on the ETF, funded by a sold call">ETF hedges around your holdings</SubHead>
      <Callout tone="info" icon={<Layers className="w-3.5 h-3.5" />}>
        Similar names are <b>grouped first</b> (measured correlation, plus shared themes), then each group is matched <b>as a basket</b> to the ETF that tracks it best — a basket follows an ETF far more closely than any single name does. The put is sized to offset ~{e.target_offset_pct}% of the group's loss; selling a call (or a lower put) recovers part of its cost.
      </Callout>
      {e.candidates.length === 0
        ? <Callout tone="neutral" icon={<Compass className="w-3.5 h-3.5" />}>No ETF hedge is needed or sizable: no matched group loses meaningfully when its ETF falls{data.has_covered_stock ? ' (tick “Include stock losses on covered calls” to include the shares behind your covered calls)' : ''}.</Callout>
        : e.candidates.map(c => <Card key={c.etf} c={c} budget={e.budget_pct} onCopy={copy} copied={copied} />)}
      {(e.skipped.length > 0 || e.mapping.length > 0) && (
        <Accordion title="How the groups were formed" icon={<Compass className="w-4 h-4" />} summary={`${e.mapping.length} names → ${(e.groups ?? []).length} groups`}>
          <div className="space-y-3">
            <div className="rounded-lg border border-white/[0.06] divide-y divide-white/[0.05]">
              {(e.groups ?? []).map(g => (
                <div key={g.etf} className="px-3 py-2 text-xs flex items-baseline gap-2 flex-wrap">
                  <span className="font-semibold text-[13px]">{g.label ?? g.etf}</span><span className="text-base-content/40">→</span>
                  <span className="font-mono font-semibold">{g.etf}</span>
                  <span className={`font-mono tabular-nums ${g.basket_rho >= 0.8 ? 'text-success' : g.basket_rho >= 0.6 ? '' : 'text-warning'}`}>basket ρ {g.basket_rho}</span>
                  <span className="font-mono text-base-content/60">{g.names.join(' · ')}</span>
                  {g.alts.length > 0 && <span className="text-base-content/40 ml-auto">next best {g.alts.map(a => `${a.etf} ${a.rho}`).join(' · ')}</span>}
                </div>
              ))}
            </div>
            <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
              <table className="w-full text-xs">
                <thead><tr className="text-base-content/45 text-left"><th className="px-3 py-1.5 font-normal">Name</th><th className="px-2 font-normal">Group's ETF</th><th className="px-2 font-normal text-right" title="This name alone vs the group's ETF">name ρ</th><th className="px-2 font-normal text-right">β on ETF</th></tr></thead>
                <tbody>
                  {e.mapping.map(m => (
                    <tr key={m.ticker} className="border-t border-white/[0.05]">
                      <td className="px-3 py-1.5 font-mono font-semibold">{m.ticker}</td>
                      <td className="px-2 font-mono">{m.etf}{m.self && <span className="text-base-content/40"> (itself)</span>}</td>
                      <td className={`px-2 text-right font-mono tabular-nums ${m.rho >= 0.75 ? 'text-success' : m.rho >= 0.55 ? '' : 'text-warning'}`}>{m.rho}</td>
                      <td className="px-2 text-right font-mono tabular-nums">{m.beta}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {e.unmapped.length > 0 && <p className="text-xs text-base-content/55">No ETF tracks these closely enough (basket ρ ≥ 0.45): <span className="font-mono">{e.unmapped.join(', ')}</span> — covered by the index hedges above instead.</p>}
            {e.skipped.map(s => <p key={s.etf} className="text-xs text-base-content/55"><span className="font-mono font-semibold">{s.etf}</span> skipped — {s.reason}.</p>)}
            <p className="text-xs text-base-content/45 leading-relaxed">ρ and β come from ~1 year of daily returns, aligned pairwise. Groups are average-linkage clusters on 1 − ρ (names sharing a registry theme count as slightly closer); a group landing on the same ETF as another is merged. A basket ρ under ~0.6 means the ETF only loosely tracks the group — the hedge can miss.</p>
          </div>
        </Accordion>
      )}
    </div>
  );
}
