/**
 * BetaBookRisk — Beta layout for "Manage Book", the book-level short-vol / tail-risk desk.
 *
 * Same data and endpoints as the classic BookTailRisk (GET /book-tail-risk — the stored snapshot on load, a live
 * recompute on Refresh — and the explicit-click LLM hedge plan). Classic is one long scroll organised BY GUARDRAIL,
 * so a trade that breaches several guardrails repeats once per breach (AMD appeared under three, with the identical
 * fix each time). Beta re-organises the same facts:
 *
 *   header   → grade · breached / watch / ok · CVaR · Θ · updated, one click to open
 *   Overview → vitals, the verdict, the guardrails (value vs limit, why, the fix) and what to do
 *   Fixes    → BY TRADE: each position once, with every guardrail its fix addresses and the risk it removes
 *   Stress   → crash & melt-up tiles, where a −20% month hits, vol × spot grid, assignment lab
 *   Concentration → by underlying, correlated clusters, sectors, macro loadings
 *   Hedge    → the budget-sized "start here" hedge, the menu, the melt-up caveat, the AI hedge plan (click only)
 *
 * Nothing the classic panel shows is dropped; every number is copied from the payload, never recomputed.
 */
import React, { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Loader2, ShieldAlert, AlertTriangle, TrendingDown, Layers, Umbrella, Compass, CheckCircle2, Sparkles, RefreshCw, ArrowRight, Grid3x3, ChevronDown,
} from 'lucide-react';
import { fetchBookTailRisk, fetchBookHedgeAdvice } from '../api';
import type { BookTailRiskResult } from '../api';
import { Accordion, Callout, Chip, SubHead, Tile, TONE, type Tone } from './BetaKit';
import { timeAgo } from './BetaChrome';
import { groupFixesByTrade, riskRemovedPct, type TradeFix, type FixVariant, type GroupedFixes } from './betaBookFixes';

const money = (v: number | null | undefined, d = 0) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;

/** A signed number with a real minus sign (a raw `-` reads as a hyphen next to the "−$" money figures). */
const sgn = (v: number, plus = false) => `${v < 0 ? '−' : plus && v > 0 ? '+' : ''}${Math.abs(v)}`;

const GRADE_TONE: Record<string, Tone> = { 'At risk': 'bad', Watch: 'warn', Sound: 'good' };
const LEVEL_TONE: Record<string, Tone> = { Dangerous: 'bad', Elevated: 'bad', Moderate: 'warn', Contained: 'good' };
const STATUS_TONE: Record<string, Tone> = { breach: 'bad', warn: 'warn', pass: 'good' };
const LEFT: Record<Tone, string> = { good: 'border-l-success/50', warn: 'border-l-warning/60', bad: 'border-l-error/60', info: 'border-l-info/60', accent: 'border-l-secondary/60', neutral: 'border-l-base-content/20' };

type TabId = 'overview' | 'fixes' | 'stress' | 'concentration' | 'hedge';
type ScoreCard = NonNullable<BookTailRiskResult['risk_scorecard']>;

// ── Overview ─────────────────────────────────────────────────────────────────
function Overview({ data, grouped, onOpenFixes }: { data: BookTailRiskResult; grouped: GroupedFixes; onOpenFixes: () => void }) {
  const [showPass, setShowPass] = useState(false);
  const sc = data.risk_scorecard; const v = data.verdict;
  const flagged = sc?.checks.filter(c => c.status !== 'pass') ?? [];
  const passed = sc?.checks.filter(c => c.status === 'pass') ?? [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
        <Tile label="Capital" value={money(data.book_capital)} title={data.capital_basis} />
        <Tile label="Carry" value={data.carry_yield_pct != null ? `${data.carry_yield_pct}%/yr` : '—'} sub="θ yield" tone="good" />
        <Tile label="Net θ / day" value={money(data.net_theta)} tone={(data.net_theta ?? 0) >= 0 ? 'good' : 'bad'} sub={data.theta_net_liq_pct != null ? `${data.theta_net_liq_pct}%/day` : undefined} />
        <Tile label="CVaR · 1 month" value={money(data.cvar_95)} tone="warn" sub={data.cvar_capital_pct != null ? `${data.cvar_capital_pct}% of capital` : undefined} />
        <Tile label="β-Δ" value={data.beta_delta_spy != null ? `${sgn(data.beta_delta_spy, true)} SPY` : '—'} sub={data.avg_beta != null ? `β ${data.avg_beta}` : undefined} />
        <Tile label="Net vega" value={money(data.net_vega)} tone={(data.net_vega ?? 0) < 0 ? 'bad' : 'good'} sub="per vol-pt" />
        <Tile label="Net Γ" value={data.net_gamma?.toFixed(2) ?? '—'} tone={(data.net_gamma ?? 0) < 0 ? 'bad' : 'good'} sub={data.short_vol ? 'short vol' : 'long vol'} />
      </div>
      {v && <Callout tone={LEVEL_TONE[v.level] || 'neutral'} icon={<ShieldAlert className="w-4 h-4" />}><b>{v.level}.</b> {v.summary}</Callout>}

      {sc && (
        <div className="space-y-2">
          <SubHead hint="book value vs limit — tap a row for why and the fix">Guardrails</SubHead>
          {flagged.map(c => <GuardRow key={c.key} c={c} grouped={grouped} onOpenFixes={onOpenFixes} />)}
          {passed.length > 0 && (
            <div>
              <button className="w-full flex items-center gap-2 text-xs text-base-content/55 hover:text-base-content/80 py-1.5" onClick={() => setShowPass(s => !s)}>
                <CheckCircle2 className="w-3.5 h-3.5 text-success/70" /> {passed.length} within limits
                <ChevronDown className={`w-3.5 h-3.5 ml-auto transition-transform ${showPass ? '' : '-rotate-90'}`} />
              </button>
              {showPass && <div className="space-y-2">{passed.map(c => <GuardRow key={c.key} c={c} grouped={grouped} onOpenFixes={onOpenFixes} />)}</div>}
            </div>
          )}
        </div>
      )}

      {(v?.actions?.length ?? 0) > 0 && (
        <div>
          <SubHead hint="deterministic, from the numbers above">What to do</SubHead>
          <ul className="rounded-xl border border-white/[0.07] bg-base-100/20 divide-y divide-white/[0.05]">
            {v!.actions.map((a, i) => <li key={i} className="flex items-start gap-2.5 px-3 py-2.5 text-[13px] leading-snug"><Compass className="w-3.5 h-3.5 text-secondary mt-0.5 shrink-0" /><span>{a}</span></li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function GuardRow({ c, grouped, onOpenFixes }: { c: ScoreCard['checks'][number]; grouped: GroupedFixes; onOpenFixes: () => void }) {
  const [open, setOpen] = useState(false);
  const tone = STATUS_TONE[c.status] || 'neutral';
  const expandable = !!(c.fix || c.note);
  const trades = grouped.trades.filter(t => t.checks.some(x => x.key === c.key));
  return (
    <div className={`rounded-xl border border-white/[0.07] bg-base-100/20 border-l-[3px] ${LEFT[tone]} overflow-hidden`}>
      <button type="button" aria-expanded={open} disabled={!expandable} onClick={() => setOpen(o => !o)}
        className={`w-full flex items-center gap-3 px-3 py-2.5 text-left ${expandable ? 'hover:bg-white/[0.03]' : 'cursor-default'} transition-colors`} title={c.note}>
        <span className="text-[13px] font-medium flex-1 min-w-0 truncate">{c.label}</span>
        <div className="text-right shrink-0 leading-tight">
          <div className={`text-sm font-semibold tabular-nums ${TONE[tone].text}`}>{c.value_str}</div>
          <div className="text-[11px] text-base-content/45">limit {c.limit_str}</div>
        </div>
        {expandable ? <ChevronDown className={`w-4 h-4 text-base-content/40 shrink-0 transition-transform ${open ? '' : '-rotate-90'}`} /> : <span className="w-4 shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 space-y-2 border-t border-white/[0.05]">
          {c.note && <p className="text-xs text-base-content/60 leading-relaxed">{c.note}</p>}
          {c.fix && (
            <Callout tone="info" icon={<Sparkles className="w-3.5 h-3.5" />}>
              <b>{c.fix.headline}</b>
              {c.fix.effect && <span> — {c.fix.effect}</span>}
              {c.fix.cost && c.fix.cost !== 'each target priced below' && <span className="opacity-70"> · {c.fix.cost}</span>}
              {c.fix.alt && <div className="text-xs opacity-70 mt-1">alt · {c.fix.alt}</div>}
            </Callout>
          )}
          {trades.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap text-xs text-base-content/55">
              Fix with:
              {trades.map(t => <button key={t.tradeKey} onClick={onOpenFixes} className="rounded-md border border-white/10 px-2 py-0.5 font-mono text-xs hover:border-primary/50 hover:text-primary transition-colors">{t.ticker}</button>)}
              <button className="underline hover:text-base-content/80" onClick={onOpenFixes}>see fixes</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Fixes: by trade ──────────────────────────────────────────────────────────
function FixVariantBlock({ tf, v, multi, onManage, toEvaluate }: { tf: TradeFix; v: FixVariant; multi: boolean; onManage?: (id: number) => void; toEvaluate: (pf: NonNullable<FixVariant['rec']['prefill']>) => void }) {
  const rec = v.rec;
  const recIsCap = rec.action === 'cap';
  const capOpt = [rec, v.alt].find(o => o && o.action === 'cap' && o.prefill) || null;
  const buyLeg = capOpt?.prefill?.legs.find(l => l.action === 'BUY');
  const rc = (ty: string) => (ty === 'CALL' ? 'C' : 'P');
  const removed = riskRemovedPct(v);
  return (
    <div className={multi ? 'rounded-xl border border-white/[0.07] bg-base-100/20 p-3 space-y-2.5' : 'space-y-2.5'}>
      {multi && <div className="flex items-center gap-1.5 flex-wrap text-xs text-base-content/55">This fix addresses {v.checks.map(c => <Chip key={c.key} tone={STATUS_TONE[c.status]}>{c.label}</Chip>)}</div>}
      <div className="text-xs font-mono text-base-content/60 leading-snug">{rec.legs}</div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label="Risk now" value={money(v.tailBefore)} tone="bad" sub="at the stress move" />
        <Tile label="After fix" value={money(rec.tail_after)} tone="good" sub={removed != null ? `−${removed.toFixed(0)}% of the loss` : undefined} />
        <Tile label="Cost" value={money(rec.cost)} tone="warn" />
        <Tile label="Premium kept" value={money(rec.premium_kept)} />
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        {buyLeg && capOpt && (
          <button onClick={() => toEvaluate(capOpt.prefill!)} title="Open this spread in Evaluate for exact live-chain pricing"
            className={`btn btn-sm gap-1.5 font-mono normal-case ${recIsCap ? 'btn-primary' : 'btn-outline'}`}>
            Buy {buyLeg.qty}× {buyLeg.strike}{rc(buyLeg.type)} <ArrowRight className="w-3.5 h-3.5" />
          </button>
        )}
        {tf.short && (
          <button onClick={() => (onManage && tf.tradeId != null ? onManage(tf.tradeId) : undefined)} title={tf.tradeId != null ? 'Jump to this position to close it' : undefined}
            className={`btn btn-sm font-mono normal-case ${!recIsCap ? 'btn-primary' : 'btn-ghost border border-white/10'}`}>
            Close {tf.short.strike}{tf.short.right}
          </button>
        )}
        <span className="ml-auto text-[11px] text-base-content/40">recommended: {recIsCap ? 'cap with a long wing' : 'close the short'}</span>
      </div>
    </div>
  );
}

function TradeFixCard({ tf, onManage, toEvaluate }: { tf: TradeFix; onManage?: (id: number) => void; toEvaluate: (pf: NonNullable<FixVariant['rec']['prefill']>) => void }) {
  const worst = tf.checks.some(c => c.status === 'breach') ? 'bad' : 'warn';
  return (
    <div className={`rounded-2xl border border-white/[0.08] bg-base-200/25 border-l-[3px] ${LEFT[worst]} p-4 space-y-3`}>
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="font-mono text-base font-semibold">{tf.ticker}</span>
        {tf.structure && <span className="text-xs text-base-content/50">{tf.structure.replace(/_/g, ' ')}</span>}
        <span className="ml-auto text-xs text-base-content/45 tabular-nums">{money(tf.premiumLeft)} left to earn</span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-xs text-base-content/55">Addresses {tf.checks.length === 1 ? 'a guardrail' : `${tf.checks.length} guardrails`}:</span>
        {tf.checks.map(c => <Chip key={c.key} tone={STATUS_TONE[c.status]}>{c.label}</Chip>)}
      </div>
      <div className={tf.variants.length > 1 ? 'space-y-2.5' : ''}>
        {tf.variants.map(v => <FixVariantBlock key={v.key} tf={tf} v={v} multi={tf.variants.length > 1} onManage={onManage} toEvaluate={toEvaluate} />)}
      </div>
    </div>
  );
}

function Fixes({ grouped, onManage, toEvaluate }: { grouped: GroupedFixes; onManage?: (id: number) => void; toEvaluate: (pf: NonNullable<FixVariant['rec']['prefill']>) => void }) {
  if (grouped.trades.length === 0 && grouped.portfolio.length === 0) {
    return <Callout tone="good" icon={<CheckCircle2 className="w-4 h-4" />}>Every guardrail is within its limit — there is nothing to fix.</Callout>;
  }
  return (
    <div className="space-y-3">
      <Callout tone="info">
        {grouped.trades.length} trade{grouped.trades.length === 1 ? '' : 's'} to fix, clearing {grouped.flagged} flagged guardrail{grouped.flagged === 1 ? '' : 's'}.
        {grouped.cardsToday > grouped.trades.length && <> The guardrail-first layout shows <b>{grouped.cardsToday}</b> fix cards for these; here each trade appears <b>once</b>, with every guardrail its fix addresses.</>}
        {' '}Ordered by guardrails cleared, then by risk.
      </Callout>
      {grouped.portfolio.map(p => (
        <div key={p.check.key} className="rounded-2xl border border-white/[0.08] bg-base-200/25 p-4 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap"><Chip tone="info">Book-level fix</Chip><Chip tone={STATUS_TONE[p.check.status]}>{p.check.label}</Chip></div>
          <div className="text-[13px] font-semibold">{p.headline}</div>
          {p.effect && <div className="text-xs text-base-content/65">{p.effect}</div>}
          {(p.cost || p.alt) && <div className="text-xs text-base-content/45">{p.cost}{p.cost && p.alt ? ' · ' : ''}{p.alt ? `alt · ${p.alt}` : ''}</div>}
        </div>
      ))}
      {grouped.trades.map(tf => <TradeFixCard key={tf.tradeKey} tf={tf} onManage={onManage} toEvaluate={toEvaluate} />)}
    </div>
  );
}

// ── Stress ───────────────────────────────────────────────────────────────────
function Stress({ data }: { data: BookTailRiskResult }) {
  const scen = data.crash_scenarios || [];
  const maxLoss = Math.max(1, ...scen.filter(c => c.pnl < 0).map(c => Math.abs(c.pnl)));
  const rows = (data.loss_by_name || []).filter(r => Math.abs(r.pnl) >= 1);
  const worst = Math.max(1, ...rows.map(r => Math.abs(r.pnl)));
  const grid = data.scenario_grid;
  const gmax = grid ? Math.max(1, ...grid.rows.flatMap(r => r.cells.map(c => Math.abs(c.pnl)))) : 1;
  const heat = (v: number) => { const t = Math.min(1, Math.abs(v) / gmax); return v < 0 ? `rgba(239,68,68,${0.08 + 0.55 * t})` : v > 0 ? `rgba(34,197,94,${0.08 + 0.5 * t})` : 'transparent'; };
  const na = data.naked_assignment;
  return (
    <div className="space-y-4">
      <div>
        <SubHead hint={`option overlay · downside & melt-up${data.avg_beta ? ` · book β ${data.avg_beta}` : ''}`}>Stress scenarios</SubHead>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {scen.map(c => {
            const loss = c.pnl < 0; const t = Math.min(1, Math.abs(c.pnl) / maxLoss);
            return (
              <div key={c.label} className="rounded-xl border border-white/[0.07] p-3 text-center" style={{ background: loss ? `rgba(239,68,68,${(0.06 + 0.5 * t).toFixed(3)})` : 'rgba(34,197,94,0.10)' }}
                title={c.pct_of_capital != null ? `${sgn(c.pct_of_capital)}% of capital` : undefined}>
                <div className="text-xs text-base-content/65">{c.label}</div>
                <div className={`font-mono text-base font-semibold tabular-nums mt-0.5 ${loss ? '' : 'text-success'}`}>{money(c.pnl)}</div>
                {c.pct_of_capital != null && <div className="font-mono text-xs text-base-content/55 tabular-nums">{sgn(c.pct_of_capital)}% of capital</div>}
              </div>
            );
          })}
        </div>
        <p className="text-xs text-base-content/50 mt-2 leading-relaxed">Full reprice of the <b>option overlay only</b> — the shares behind covered calls/collars are a separate core holding, not counted. So short calls <b>print</b> in a selloff (green) and the real risk is the <b>melt-up</b> (red). Stock is used only to mark a call covered and to size assignment capital.</p>
      </div>

      {rows.length > 0 && (
        <div>
          <SubHead hint="option overlay, by name">Where a −20% month hits</SubHead>
          <div className="space-y-1.5">
            {rows.slice(0, 8).map(r => {
              const loss = r.pnl < 0;
              return (
                <div key={r.ticker} className="flex items-center gap-3 text-xs">
                  <span className="w-14 font-mono font-semibold shrink-0">{r.ticker}</span>
                  <div className="flex-1 h-4 rounded bg-base-content/[0.07] overflow-hidden"><div className={`h-full ${loss ? 'bg-error/55' : 'bg-success/55'}`} style={{ width: `${Math.round((Math.abs(r.pnl) / worst) * 100)}%` }} /></div>
                  <span className={`w-20 text-right font-mono tabular-nums shrink-0 ${loss ? 'text-error' : 'text-success'}`}>{money(r.pnl)}</span>
                </div>
              );
            })}
          </div>
          <p className="text-xs text-base-content/45 mt-1.5">A red bar loses, green gains. Sums to the −20% tile above.</p>
        </div>
      )}

      {grid && grid.rows.length > 0 && (
        <div>
          <SubHead hint="option-overlay P&L by spot move × vol shock"><span className="inline-flex items-center gap-1.5"><Grid3x3 className="w-3.5 h-3.5" /> Vol sensitivity</span></SubHead>
          <div className="overflow-x-auto">
            <table className="text-xs border-separate" style={{ borderSpacing: 3 }}>
              <thead><tr><th className="text-left font-normal text-base-content/45 pr-1 whitespace-nowrap">spot ↓ · vol →</th>
                {grid.vol_shocks.map(v => <th key={v} className="font-mono font-normal text-base-content/50 px-1 text-center whitespace-nowrap">{sgn(v, true)}v</th>)}</tr></thead>
              <tbody>
                {grid.rows.map(row => (
                  <tr key={row.move_pct}>
                    <td className="pr-2 font-mono text-base-content/60 whitespace-nowrap font-medium">{sgn(row.move_pct, true)}%</td>
                    {row.cells.map((c, i) => (
                      <td key={i} className="text-center font-mono tabular-nums rounded px-1.5 py-1 whitespace-nowrap" style={{ background: heat(c.pnl), minWidth: 62 }} title={c.pct != null ? `${sgn(c.pct)}% of book capital` : undefined}>
                        <span className={c.pnl < 0 ? 'text-error' : c.pnl > 0 ? 'text-success' : 'text-base-content/40'}>{money(c.pnl)}</span>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-base-content/45 mt-1.5 leading-relaxed">Read <b>down a column</b> for gamma (a move either way), <b>across a row</b> for vega (loss as vol rises). Deepest-red cell = your worst corner.</p>
        </div>
      )}

      {(data.assignment_ladder?.length ?? 0) > 0 && (
        <Accordion title="Assignment lab" icon={<Compass className="w-4 h-4" />} summary="capital if assigned"
          badge={na && na.total > 0 ? <span className="text-xs text-base-content/55 whitespace-nowrap">all-naked-assigned <b className="text-warning">{money(na.total)}</b></span> : undefined}>
          <div className="space-y-3">
            {na && na.total > 0 && (
              <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
                <div><b>If every naked short is assigned: {money(na.total)}</b></div>
                <div className="mt-0.5 opacity-90">= {money(na.put_capital)} to buy the {na.n_naked_puts} naked short put{na.n_naked_puts !== 1 ? 's' : ''} (strike ×100){na.call_capital > 0 && <> + {money(na.call_capital)} delivery notional on {na.n_naked_calls} naked short call{na.n_naked_calls !== 1 ? 's' : ''}</>}. Covered calls &amp; spread-protected legs excluded. A naked call's true buy-to-cover can exceed its strike notional if the stock has already run.</div>
                {data.book_capital != null && <div className="text-xs opacity-75 mt-1.5 pt-1.5 border-t border-warning/20">This ≈ <b>Book capital {money(data.book_capital)}</b>; they differ only by the covered / spread-protected shorts excluded here. The “Deployed” figure on a My Trades group is different by design: that filtered group's subset, on a Reg-T margin basis.</div>}
              </Callout>
            )}
            <div className="overflow-x-auto rounded-lg border border-white/[0.06]">
              <table className="w-full text-xs">
                <thead><tr className="text-base-content/45 text-left"><th className="px-3 py-1.5 font-normal">Market</th><th className="px-3 font-normal">Book P&amp;L</th><th className="px-3 font-normal" title="Cash to take delivery on ITM short puts (strike ×100)">Put assignment</th><th className="px-3 font-normal" title="Intrinsic to deliver / buy back ITM short calls">Call cover</th><th className="px-3 font-normal">ITM</th></tr></thead>
                <tbody>
                  {data.assignment_ladder!.map((r, i) => (
                    <tr key={i} className={`border-t border-white/[0.05] ${Math.abs(r.move_pct) <= 5 ? 'bg-base-content/[0.05]' : ''}`}>
                      <td className={`px-3 py-1.5 font-mono ${r.move_pct > 0 ? 'text-warning' : 'text-error'}`}>{sgn(r.move_pct, true)}%</td>
                      <td className={`px-3 font-mono ${r.pnl >= 0 ? 'text-success' : 'text-error'}`}>{money(r.pnl)}</td>
                      <td className="px-3 font-mono text-warning/90">{r.put_assignment_capital > 0 ? money(r.put_assignment_capital) : '—'}</td>
                      <td className="px-3 font-mono text-warning/90">{r.call_cover_cost > 0 ? money(r.call_cover_cost) : '—'}</td>
                      <td className="px-3 text-base-content/55">{[r.puts_itm > 0 ? `${r.puts_itm}P` : '', r.calls_itm > 0 ? `${r.calls_itm}C` : ''].filter(Boolean).join(' ') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-base-content/45 leading-relaxed"><b>Put assignment</b> = cash to buy the shares you're put (strike ×100); <b>Call cover</b> = intrinsic to deliver/buy back at that move. Only the binding wing is ITM at a given move — a strangle never demands both at once. Shaded row ≈ spot today.</p>
          </div>
        </Accordion>
      )}
    </div>
  );
}

// ── Concentration ────────────────────────────────────────────────────────────
function Concentration({ data }: { data: BookTailRiskResult }) {
  const conc = data.concentration || [];
  const fe = data.factor_exposure;
  const clusters = (fe?.clusters || []).filter(c => c.tickers.length >= 2);
  const sectors = (fe?.sectors || []).filter(s => s.capital > 0).slice(0, 8);
  const macro = fe?.macro || [];
  if (!conc.length && !clusters.length && sectors.length <= 1 && !macro.length) return <Callout tone="good" icon={<CheckCircle2 className="w-4 h-4" />}>No concentration to report.</Callout>;
  const maxCap = Math.max(1, ...sectors.map(s => s.capital));
  return (
    <div className="space-y-4">
      {conc.length > 0 && (
        <div>
          <SubHead hint="share of book gamma">By underlying</SubHead>
          <div className="space-y-2">
            {conc.map(c => (
              <div key={c.ticker} className={`rounded-xl border p-3 ${c.flags.length ? 'border-warning/30 bg-warning/[0.05]' : 'border-white/[0.07] bg-base-100/20'}`}>
                <div className="flex items-baseline gap-2 flex-wrap text-[13px]">
                  <span className="font-mono font-semibold">{c.ticker}</span>
                  {c.beta != null && <span className={`text-xs ${c.beta >= 1.3 ? 'text-warning' : 'text-base-content/45'}`}>β {c.beta}</span>}
                  <span className="text-xs text-base-content/45">{c.trades} trade{c.trades !== 1 ? 's' : ''} · {c.short_legs} short leg{c.short_legs !== 1 ? 's' : ''}</span>
                  <span className="ml-auto font-mono text-xs text-base-content/65">{c.gamma_share_pct}% of book Γ</span>
                </div>
                {c.flags.map((f, i) => <div key={i} className="text-xs text-warning flex items-start gap-1.5 mt-1.5"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{f}</div>)}
              </div>
            ))}
          </div>
        </div>
      )}
      {(clusters.length > 0 || sectors.length > 1 || macro.length > 0) && (
        <div className="space-y-3">
          <SubHead hint="measured ρ · GICS — not opinion"><span className="inline-flex items-center gap-1.5"><Layers className="w-3.5 h-3.5" /> Factor &amp; correlation — your real concentration</span></SubHead>
          {clusters.length > 0 && (
            <div className="space-y-1.5">
              {clusters.map((c, i) => (
                <div key={i} className="flex items-center gap-2 text-[13px] rounded-lg bg-warning/[0.06] border border-warning/25 px-3 py-2">
                  <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0" /><span className="font-semibold">{c.tickers.join(' · ')}</span>
                  {c.avg_rho != null && <span className="text-warning text-xs">move together ρ {c.avg_rho}</span>}
                  <span className="ml-auto text-base-content/55 tabular-nums text-xs">{money(c.capital)} at work</span>
                </div>
              ))}
              <p className="text-xs text-base-content/50">These names are <b>one bet</b> — a shock to any of them hits the whole cluster at once. That's the concentration a per-name view hides.</p>
            </div>
          )}
          {sectors.length > 1 && (
            <div>
              <div className="text-[11px] text-base-content/45 mb-1.5">Capital by sector</div>
              <div className="space-y-2">
                {sectors.map(s => (
                  <div key={s.sector}>
                    <div className="flex items-baseline gap-2 text-xs"><span className="truncate flex-1">{s.sector}</span><span className="text-base-content/45 font-mono">{s.n}</span><span className="tabular-nums font-mono w-20 text-right">{money(s.capital)}</span></div>
                    <div className="h-1.5 rounded-full bg-base-content/10 overflow-hidden my-1"><div className="h-full rounded-full bg-secondary/60" style={{ width: `${Math.max(3, (s.capital / maxCap) * 100)}%` }} /></div>
                    {(s.tickers?.length ?? 0) > 0 && <div className="font-mono text-[11px] text-base-content/45 truncate" title={s.tickers.join(' · ')}>{s.tickers.join(' · ')}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}
          {macro.length > 0 && (
            <div>
              <div className="text-[11px] text-base-content/45 mb-1.5">Macro the book loads on (measured ρ)</div>
              <div className="flex flex-wrap gap-1.5">{macro.map(m => <Chip key={m.factor} tone={Math.abs(m.rho) >= 0.6 ? 'warn' : 'neutral'} title={`Book return vs ${m.label}: measured ρ = ${sgn(m.rho)}`}>{m.label} {sgn(m.rho, true)}</Chip>)}</div>
              <p className="text-xs text-base-content/45 mt-1.5">A move in these factors pushes the whole book one way — that's your shared driver.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Hedge ────────────────────────────────────────────────────────────────────
function HedgeHero({ data }: { data: BookTailRiskResult }) {
  const [copied, setCopied] = useState(false);
  const menu = data.hedge_menu || [];
  const rec = menu.find(c => c.recommended) || menu.find(c => c.cost_effective) || menu[0];
  if (!rec) return null;
  const before = Math.abs(data.crash_scenarios?.find(s => Math.abs(s.move_pct + 0.2) < 0.001)?.pnl ?? 0);
  const after = Math.max(0, before - (rec.crash_payoff_20 || 0));
  const carry = data.annual_income || 0;
  const budgetPct = carry > 0 ? (rec.annual_bleed / carry) * 100 : null;
  const lo = rec.long_strike ?? rec.long_put, sh = rec.short_strike ?? rec.short_put;
  const isVixy = rec.instrument === 'VIXY';
  const inst = rec.instrument ?? 'SPX';
  const strikes = lo != null ? `${lo}${sh ? `/${sh}` : ''}` : '';
  const size = isVixy ? `${money(rec.sleeve_capital)} cash` : `${rec.contracts}× ${inst} ${strikes}`.trim();
  const ticket = isVixy
    ? `Dynamic VIXY sleeve — ${money(rec.sleeve_capital)} cash, deploy on VIX backwardation (~${money(rec.annual_bleed)}/yr drag)`
    : `BUY ${rec.contracts}× ${inst} ${strikes} put spread (~${rec.dte_days}d) — tail hedge, ~${money(rec.annual_bleed)}/yr`;
  const stage = async () => { try { await navigator.clipboard.writeText(ticket); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { /* clipboard blocked */ } };
  return (
    <div className="rounded-2xl border border-success/30 bg-success/[0.06] p-4 space-y-3">
      <div className="flex items-center gap-2 flex-wrap"><Chip tone="good">Start here</Chip><span className="text-sm font-semibold">{rec.label}</span><span className="ml-auto text-xs text-base-content/50">{rec.dte_days ? `~${rec.dte_days}d` : 'signal-based'}</span></div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Tile label="Trade" value={size} />
        <Tile label="Cost" tone="warn" value={`${money(rec.annual_bleed)}/yr${isVixy ? '*' : ''}`} sub={budgetPct != null ? `${budgetPct.toFixed(0)}% of carry` : undefined} />
        <Tile label="−20% month" value={<span><span className="text-error">{money(-before)}</span> → <span className="text-success">{money(-after)}</span></span>} sub={rec.offsets_pct != null ? `${rec.offsets_pct}% capped` : undefined} />
        <Tile label="Compounding" tone={rec.cost_effective ? 'good' : undefined} value={`${sgn(rec.cagr_lift_pct, true)}% CAGR`} sub={rec.cost_effective ? 'pays for itself' : 'insurance'} />
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={stage} className={`btn btn-sm gap-1.5 normal-case ${copied ? 'btn-success' : 'btn-success btn-outline'}`}>
          {copied ? <><CheckCircle2 className="w-4 h-4" /> Ticket copied</> : <>Stage {inst} trade <ArrowRight className="w-4 h-4" /></>}
        </button>
        <span className="text-xs text-base-content/45">copies the exact ticket to paste into your broker</span>
      </div>
    </div>
  );
}

function Hedge({ data, quoteSource }: { data: BookTailRiskResult; quoteSource: string }) {
  const [advice, setAdvice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const menu = data.hedge_menu || [];
  const cs = data.crash_scenarios || [];
  const wd = Math.min(0, ...cs.filter(s => s.move_pct < 0).map(s => s.pnl));
  const wu = cs.filter(s => s.move_pct > 0).reduce<{ pnl: number; label: string } | null>((m, s) => (m == null || s.pnl < m.pnl ? { pnl: s.pnl, label: s.label } : m), null);
  const meltUp = !!wu && Math.abs(wu.pnl) > Math.abs(wd);
  const ask = async () => {
    setLoading(true); setErr(null);
    try { const r = await fetchBookHedgeAdvice(quoteSource); if (r.error) setErr(r.error); else setAdvice(r.advice || null); }
    catch (e: any) { setErr(e?.message || 'Failed to get hedging strategy'); } finally { setLoading(false); }
  };
  return (
    <div className="space-y-4">
      {menu.length > 0 && <HedgeHero data={data} />}
      {meltUp && wu && <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}>Your <b>bigger tail is the upside</b> — a {wu.label} loses {money(wu.pnl)} vs {money(wd)} for the worst downside. The hedges below protect the <b>downside</b>; for the squeeze, cap short calls / add call spreads or a small long-call (see the Fixes tab).</Callout>}
      {menu.length > 0 && (
        <div className="space-y-2">
          <SubHead hint={`~${menu[0].dte_days}d · ranked by CVaR reduced per $/yr, then Spitznagel cost-vs-drag`}>SPX puts + VIX black-swan</SubHead>
          {menu.map((c, i) => {
            const lo = c.long_strike ?? c.long_put, sh = c.short_strike ?? c.short_put; const isVixy = c.instrument === 'VIXY';
            const tone: Tone = c.instrument === 'VIX' ? 'accent' : isVixy ? 'info' : 'neutral';
            return (
              <div key={i} className={`rounded-xl border p-3 ${c.recommended ? 'border-success/40 bg-success/[0.05]' : 'border-white/[0.07] bg-base-100/20'}`} title={isVixy ? c.signal : undefined}>
                <div className="flex items-center gap-2 flex-wrap">
                  {c.recommended && <CheckCircle2 className="w-3.5 h-3.5 text-success" />}
                  <Chip tone={tone}>{c.instrument ?? 'SPX'}</Chip><span className="text-[13px] font-semibold">{c.label}</span>
                  <span className="ml-auto font-mono text-xs text-base-content/60">{isVixy ? `${money(c.sleeve_capital)} cash` : (lo != null ? `${lo}${sh ? `/${sh}` : ''}` : '—')}{!isVixy && c.contracts != null ? ` · ×${c.contracts}` : ''}</span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-2 mt-2.5">
                  <div><div className="text-[11px] text-base-content/45">Cost / yr</div><div className={`text-xs font-semibold tabular-nums ${isVixy ? 'text-success' : 'text-warning'}`}>{money(c.annual_bleed)}{isVixy ? '*' : ''}</div></div>
                  <div><div className="text-[11px] text-base-content/45">Offsets −20%</div><div className="text-xs font-semibold tabular-nums">{c.offsets_pct != null ? `${c.offsets_pct}%` : '—'}</div></div>
                  <div><div className="text-[11px] text-base-content/45" title="Book CVaR-95 reduction">CVaR cut</div><div className="text-xs font-semibold tabular-nums text-success">{money(c.cvar_reduction)}</div></div>
                  <div><div className="text-[11px] text-base-content/45" title="Spitznagel: compound-growth lift">CAGR</div><div className={`text-xs font-semibold tabular-nums ${c.cagr_lift_pct >= 0 ? 'text-success' : 'text-error'}`}>{sgn(c.cagr_lift_pct, true)}%</div></div>
                </div>
              </div>
            );
          })}
          <details className="text-xs text-base-content/45">
            <summary className="cursor-pointer select-none">How to read the menu</summary>
            <p className="mt-1.5 leading-relaxed"><b className="text-info">SPX puts</b> = linear crash protection. <b className="text-secondary">VIX call spreads</b> = cheap convexity that only pays when vol EXPLODES (a −20% month implies VIX ≈ {menu.find(c => c.instrument === 'VIX')?.vix_at_minus20 ?? '—'}). <b className="text-accent">Dynamic VIXY</b> = a cash sleeve deployed into VIXY ONLY when the VIX term structure inverts (backwardation), so it avoids permanent roll-decay — <b>*near-zero cost</b>, but it ties up cash and its payoff is haircut ~35% for signal/gap lag. A <b>positive CAGR</b> means capping the crash lifts compound growth; negative = pure insurance.</p>
          </details>
        </div>
      )}
      {data.hedge_note && <div className="text-xs text-base-content/60">{data.hedge_note}</div>}

      <div className="rounded-2xl border border-secondary/25 bg-secondary/[0.04] p-4 space-y-3">
        <div className="flex items-center gap-2"><Sparkles className="w-4 h-4 text-secondary" /><span className="text-sm font-semibold">AI hedging strategy · whole book</span>
          <button className="btn btn-secondary btn-sm gap-1.5 ml-auto" disabled={loading} onClick={ask}>{loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}{loading ? 'Analyzing…' : advice ? 'Regenerate' : 'Get AI hedge plan'}</button>
        </div>
        {err && <Callout tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}>{err}</Callout>}
        {advice && <div className="text-[13px] whitespace-pre-wrap leading-relaxed">{advice}</div>}
        {!advice && !err && !loading && <p className="text-xs text-base-content/55 leading-relaxed">Sends only computed data — greeks, CVaR, crash P&amp;Ls, the per-name loss waterfall, and the <b>deterministic factor read</b> (GICS sectors, measured-ρ correlated clusters, macro loadings) — plus the hedge menu. The model reasons over your real concentration to say what to trim/hedge; it does no arithmetic and can't invent a correlation the data didn't measure. It runs only when you press the button.</p>}
      </div>
    </div>
  );
}

// ── panel ────────────────────────────────────────────────────────────────────
export default function BetaBookRisk({ quoteSource, onManageTrade }: { quoteSource: string; onManageTrade?: (id: number) => void }) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<TabId>('overview');
  const [loading, setLoading] = useState(false);       // initial STORED-snapshot load
  const [refreshing, setRefreshing] = useState(false); // user-triggered live recompute
  const [data, setData] = useState<BookTailRiskResult | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // On mount: the STORED snapshot (no recompute), so the header shows last-known numbers immediately.
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try { const r = await fetchBookTailRisk(quoteSource, false); if (alive && r.stored && !r.error) setData(r); }
      catch { /* no stored snapshot — the user can Analyze */ }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [quoteSource]);

  // Refresh = recompute from live quotes AND overwrite the stored snapshot.
  const refresh = async () => {
    setRefreshing(true); setErr(null); setOpen(true);
    try { const r = await fetchBookTailRisk(quoteSource, true); if (r.error) setErr(r.error); else setData(r); }
    catch (e: any) { setErr(e?.message || 'Failed'); }
    finally { setRefreshing(false); }
  };

  const sc = data?.risk_scorecard;
  const grouped = useMemo(() => groupFixesByTrade(sc), [sc]);
  const passed = (sc?.n_checks ?? 0) - (sc?.n_breach ?? 0) - (sc?.n_warn ?? 0);
  const gradeTone = sc ? (GRADE_TONE[sc.grade] || 'warn') : 'neutral';
  const total = Math.max(1, sc?.checks.length || 1);

  const toEvaluate = (pf: { ticker: string; legs: { action: string; type: string; strike: number; expiration: string | null; qty: number }[] }) => {
    // EvaluateLeg carries no qty → expand to one entry per contract (mirror the classic panel).
    const legs = pf.legs.flatMap(l => Array(Math.max(1, l.qty)).fill({ action: l.action, type: l.type, strike: l.strike, expiration: l.expiration }));
    try { sessionStorage.setItem('evaluatePrefill', JSON.stringify({ ticker: pf.ticker, legs })); } catch { /* ignore */ }
    nav('/strategies?ui=beta&strategy=derivative_income&mode=evaluate');
  };
  const openTab = (t: TabId) => { setTab(t); setOpen(true); };

  const tabs: { id: TabId; label: string; badge?: ReactNode }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'fixes', label: 'Fixes', badge: grouped.trades.length + grouped.portfolio.length > 0 ? <span className="ml-1.5 rounded-full bg-error/20 text-error text-[11px] px-1.5">{grouped.trades.length + grouped.portfolio.length}</span> : undefined },
    { id: 'stress', label: 'Stress' }, { id: 'concentration', label: 'Concentration' }, { id: 'hedge', label: 'Hedge' },
  ];

  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/30 overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <button type="button" aria-expanded={open} onClick={() => (data ? setOpen(o => !o) : refresh())} className="flex items-center gap-3 flex-1 min-w-0 text-left">
          <div className="h-8 w-8 rounded-lg bg-warning/15 text-warning flex items-center justify-center shrink-0"><ShieldAlert className="w-4 h-4" /></div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold">Book risk</span>
              {sc && <Chip tone={gradeTone}>{sc.grade}</Chip>}
              {sc && <span className="text-xs text-base-content/55">{sc.n_breach > 0 ? `${sc.n_breach} breached` : '0 breached'} · {sc.n_warn} watch · {passed} ok</span>}
            </div>
            <div className="text-xs text-base-content/45 truncate">
              {data ? `CVaR 1-mo ${money(data.cvar_95)}${data.cvar_capital_pct != null ? ` (${data.cvar_capital_pct}% of capital)` : ''} · θ ${money(data.net_theta)}/day${data.computed_at ? ` · updated ${timeAgo(data.computed_at)}` : ''}`
                : 'Guardrails · stress tests · concentration · the cheapest fix per breach'}
            </div>
          </div>
        </button>
        {loading && !data ? <Loader2 className="w-4 h-4 animate-spin text-warning shrink-0" />
          : data ? (
            <div className="flex items-center gap-2 shrink-0">
              {grouped.trades.length > 0 && <button className="btn btn-sm btn-outline border-white/15 hidden sm:inline-flex" onClick={() => openTab('fixes')}>Review {grouped.trades.length} fix{grouped.trades.length === 1 ? '' : 'es'}</button>}
              <button className="btn btn-ghost btn-sm gap-1.5" onClick={refresh} disabled={refreshing} title="Recompute from live quotes and save the latest">
                {refreshing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}{refreshing ? 'Refreshing…' : 'Refresh'}
              </button>
              <button onClick={() => setOpen(o => !o)} aria-label={open ? 'Collapse' : 'Expand'}><ChevronDown className={`w-4 h-4 text-base-content/40 transition-transform ${open ? '' : '-rotate-90'}`} /></button>
            </div>
          ) : refreshing ? <Loader2 className="w-4 h-4 animate-spin text-warning shrink-0" />
          : <button className="btn btn-sm btn-primary shrink-0" onClick={refresh}>Analyze book</button>}
      </div>
      {sc && (
        <div className="flex h-[3px]" role="img" aria-label={`${sc.n_breach} breached, ${sc.n_warn} watch, ${passed} ok`}>
          <div className="bg-error" style={{ width: `${(sc.n_breach / total) * 100}%` }} /><div className="bg-warning" style={{ width: `${(sc.n_warn / total) * 100}%` }} /><div className="bg-success/50" style={{ width: `${(passed / total) * 100}%` }} />
        </div>
      )}

      {open && (refreshing || data || err) && (
        <div className="border-t border-white/[0.07]">
          {err && <div className="px-4 pt-3"><Callout tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}>{err}</Callout></div>}
          {refreshing && !data && <div className="px-4 py-4 text-sm text-base-content/55 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Repricing the book across crash scenarios and Monte-Carlo tails…</div>}
          {data && (
            <>
              <div className="flex gap-1 px-4 pt-2 border-b border-white/[0.06] overflow-x-auto overflow-y-hidden" role="tablist" aria-label="Book risk sections">
                {tabs.map(t => (
                  <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
                    className={`px-2.5 py-2 text-sm whitespace-nowrap border-b-2 -mb-px transition-colors ${tab === t.id ? 'border-primary text-base-content font-semibold' : 'border-transparent text-base-content/55 hover:text-base-content/80'}`}>{t.label}{t.badge}</button>
                ))}
              </div>
              <div className="p-4">
                {tab === 'overview' && <Overview data={data} grouped={grouped} onOpenFixes={() => setTab('fixes')} />}
                {tab === 'fixes' && <Fixes grouped={grouped} onManage={onManageTrade} toEvaluate={toEvaluate} />}
                {tab === 'stress' && <Stress data={data} />}
                {tab === 'concentration' && <Concentration data={data} />}
                {tab === 'hedge' && <Hedge data={data} quoteSource={quoteSource} />}
                <p className="text-[11px] text-base-content/35 mt-4 leading-relaxed">
                  {data.assumptions?.beta}; {data.assumptions?.tail}; market vol {data.assumptions?.mkt_vol_pct}%; VaR/CVaR over {data.horizon}; crash prob {data.assumptions?.crash_prob_annual_pct}%/yr; hedge rolled {data.assumptions?.hedge_rolls_per_year}×/yr.
                  {' '}θ/Net-Liq &amp; carry use committed capital ({money(data.book_capital)}, Σ cash-secured notional) as the net-liq base, and annualize decay at current pace. Option-layer greeks only. Model estimate — not an order.
                </p>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
