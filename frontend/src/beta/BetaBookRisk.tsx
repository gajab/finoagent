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
 *   Stress   → crash & melt-up tiles, where a −20% month hits, vol × spot grid, assignment lab (+ how to read each)
 *   Concentration → BetaBookConcentration: independent bets, risk vs capital vs income, what a gapping group does
 *   Hedge    → BetaBookHedge: what drives the tail → an income-sized budget → only the hedges that cut CVaR → risk→hedge map
 *
 * Covered calls: the shares' gain always offsets the short call in a rally; a checkbox adds the shares' LOSSES on the
 * downside (the backend ships both variants, so toggling is instant — no live-quote refresh).
 * Every number is copied from the payload, never recomputed.
 */
import React, { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Loader2, ShieldAlert, AlertTriangle, Layers, Compass, CheckCircle2, Sparkles, RefreshCw, ArrowRight, Grid3x3, ChevronDown,
} from 'lucide-react';
import { fetchBookTailRisk } from '../api';
import type { BookTailRiskResult } from '../api';
import { Accordion, Callout, Chip, SubHead, Tile, TONE, type Tone } from './BetaKit';
import { timeAgo } from './BetaChrome';
import { groupFixesByTrade, riskRemovedPct, type TradeFix, type FixVariant, type GroupedFixes } from './betaBookFixes';
import BetaBookConcentration from './BetaBookConcentration';
import BetaBookHedge from './BetaBookHedge';

const money = (v: number | null | undefined, d = 0) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;

/** A signed number with a real minus sign (a raw `-` reads as a hyphen next to the "−$" money figures). */
const sgn = (v: number, plus = false) => `${v < 0 ? '−' : plus && v > 0 ? '+' : ''}${Math.abs(v)}`;

const GRADE_TONE: Record<string, Tone> = { 'At risk': 'bad', Watch: 'warn', Sound: 'good' };
const LEVEL_TONE: Record<string, Tone> = { Dangerous: 'bad', Elevated: 'bad', Moderate: 'warn', Contained: 'good' };
const STATUS_TONE: Record<string, Tone> = { breach: 'bad', warn: 'warn', pass: 'good' };
const LEFT: Record<Tone, string> = { good: 'border-l-success/50', warn: 'border-l-warning/60', bad: 'border-l-error/60', info: 'border-l-info/60', accent: 'border-l-secondary/60', neutral: 'border-l-base-content/20' };

const FOLDER_LABEL: Record<string, string> = { income: 'Income', hedge: 'Hedge', trade: 'Trade', managed_floor: 'Managed Floor', managed_buffer: 'Managed Buffer', dual_directional: 'Dual Directional', other: 'Other', untagged: 'Untagged' };
/** "2 Hedge, 1 Trade" — the folders Manage Book leaves out. */
const excludedText = (ex: Record<string, number>) => Object.entries(ex).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n} ${FOLDER_LABEL[k] || k}`).join(', ');

const LS_STOCK = 'finoagent.beta.book.stockDownside';

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
      {data.scope && (
        <Callout tone="info" icon={<Layers className="w-3.5 h-3.5" />}>
          <b>Income trades only</b> — {data.scope.included} trade{data.scope.included === 1 ? '' : 's'} tagged Income{data.coverage ? <>, <b>{data.coverage.analysed} analysed</b></> : ' feed every number here'}.
          {data.coverage && data.coverage.skipped.length > 0 && (
            <> Left out of the numbers: {data.coverage.skipped.map(k => <span key={`${k.trade_id}`} className="font-mono">{k.ticker}</span>).reduce<ReactNode[]>((a, x, i) => (i ? [...a, ', ', x] : [x]), [])} — {data.coverage.skipped[0].reason}.</>
          )}
          {data.scope.excluded_total > 0
            ? <> Not included: <b>{excludedText(data.scope.excluded)}</b>. A hedge or directional trade you hold in another folder is not offsetting (or adding to) this risk.</>
            : <> Nothing else is open in other folders.</>}
          {(data.scope.excluded.untagged ?? 0) > 0 && <> Untagged trades are not counted — tag them Income in My Trades to include them.</>}
        </Callout>
      )}
      {data.data_quality && (data.data_quality.solved > 0 || data.data_quality.fallback > 0 || data.data_quality.indicative) && (
        <Callout tone={data.data_quality.fallback > 0 ? 'warn' : 'info'} icon={<AlertTriangle className="w-3.5 h-3.5" />}>
          <b>{data.data_quality.indicative ? 'Indicative prices — the market looks closed.' : 'Some quotes were unusable.'}</b>{' '}
          The option chain's own implied vol was missing or inconsistent for {data.data_quality.solved} of {data.data_quality.legs} legs, so each was re-derived from that leg's price.
          {data.data_quality.fallback > 0 && <> {data.data_quality.fallback} leg{data.data_quality.fallback === 1 ? ' had' : 's had'} no usable price or IV and use a default (the trade's own IV, else 30%).</>}
          {' '}Greeks and tails are good estimates; press Refresh during market hours for live quotes.
        </Callout>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
        <Tile label="Capital" value={money(data.book_capital)} sub="short-strike notional" title={data.capital_basis} />
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
        <Tile label="Risk now" value={money(v.tailBefore)} tone="bad"
          sub={v.stress ? `at ${sgn(v.stress.name_move_pct, true)}% (${sgn(v.stress.market_move_pct, true)}% mkt × β ${v.stress.beta})` : 'at the stress move'}
          title={v.stress ? `Full Black-Scholes reprice of THIS position if the market moves ${sgn(v.stress.market_move_pct, true)}%, the name moves β (${v.stress.beta}) × that = ${sgn(v.stress.name_move_pct, true)}%, and vol shifts ${sgn(v.stress.vol_shock_pts, true)} pts. Time unchanged. P&L vs today's value; premium already collected is not netted.` : undefined} />
        <Tile label="After fix" value={money(rec.tail_after)} tone="good" sub={removed != null ? `−${removed.toFixed(0)}% of the loss` : undefined} />
        <Tile label="Cost" value={money(rec.cost)} tone={rec.pricing === 'model' ? 'bad' : 'warn'}
          sub={rec.unit_price != null ? (rec.pricing === 'model' ? `model $${rec.unit_price}/sh — no live quote` : `${rec.pricing} $${rec.unit_price}/sh`) : undefined}
          title="What the leg costs at the live ask captured when the book was last refreshed (mid if there is no ask). Only a strike with no quote in the chain falls back to a model price." />
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

/** "APP and JBL are 60% of this loss — cap, roll or hedge those first." */
function byNameRead(rows: { ticker: string; pnl: number }[]): string {
  const losers = rows.filter(r => r.pnl < 0).sort((a, b) => a.pnl - b.pnl);
  const tot = losers.reduce((s, r) => s + r.pnl, 0);
  if (losers.length === 0 || tot >= 0) return 'no name loses in a −20% month — the risk is on the other side (see the melt-up tiles).';
  const top = losers.slice(0, 2); const share = top.reduce((s, r) => s + r.pnl, 0) / tot * 100;
  return `${top.map(r => r.ticker).join(' and ')} ${top.length === 1 ? 'is' : 'are'} ${share.toFixed(0)}% of this loss — cap, roll or hedge ${top.length === 1 ? 'that one' : 'those'} first (Fixes tab). A short, even spread of small bars means no single name needs fixing.`;
}

/** A deterministic read of the vol × spot grid: which side is worse (with vol moving the way it really does), and how much vol alone costs. */
function gridRead(grid: NonNullable<BookTailRiskResult['scenario_grid']>, capital: number | undefined): string | null {
  if (!grid || grid.rows.length === 0 || grid.vol_shocks.length === 0) return null;
  const hi = grid.vol_shocks.length - 1;
  const down = grid.rows.filter(r => r.move_pct < 0).map(r => ({ move: r.move_pct, v: r.cells[hi].pnl })).sort((a, b) => a.v - b.v)[0];
  const up = grid.rows.filter(r => r.move_pct > 0).map(r => ({ move: r.move_pct, v: r.cells[0].pnl })).sort((a, b) => a.v - b.v)[0];
  if (!down || !up) return null;
  const flat = grid.rows.find(r => r.move_pct === 0); const i10 = grid.vol_shocks.indexOf(10);
  const vega10 = flat && i10 >= 0 ? flat.cells[i10].pnl : null;
  const ratio = Math.abs(up.v) / Math.max(1, Math.abs(down.v));
  const verdict = ratio >= 1.5 ? `The rally is ${ratio.toFixed(1)}× worse — the upside is your tail: cap the short calls or buy call spreads; an index put does nothing for it.`
    : ratio <= 1 / 1.5 ? `The selloff is ${(1 / ratio).toFixed(1)}× worse — the downside is your tail: put spreads / a vol hedge fit.`
    : 'Both sides are of similar size — hedge the one with the cheaper cover.';
  const vegaText = vega10 != null
    ? ` Vol on its own (+10 pts, spot flat) costs ${money(vega10)}${capital ? ` (${Math.abs(vega10 / capital * 100).toFixed(1)}% of capital)` : ''} — ${capital && Math.abs(vega10 / capital) < 0.01 ? 'small; not worth a dedicated vol hedge' : 'worth sizing a vol hedge against'}.`
    : '';
  return `Pair each move with the vol it really brings — selloffs with vol UP (${sgn(down.move, true)}% & +${grid.vol_shocks[hi]}v: ${money(down.v)}), rallies with vol DOWN (${sgn(up.move, true)}% & ${sgn(grid.vol_shocks[0], true)}v: ${money(up.v)}). ${verdict}${vegaText}`;
}

// ── Stress ───────────────────────────────────────────────────────────────────
function Stress({ data, withStock }: { data: BookTailRiskResult; withStock: boolean }) {
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
        <p className="text-xs text-base-content/50 mt-2 leading-relaxed">
          Each name moves the scenario × its β, vol shifts as shown, and every option leg is fully repriced (Black-Scholes at the quoted IV; an instant move, no time decay).
          {data.has_covered_stock
            ? <> <b>Covered calls:</b> the shares' gain always offsets the short call in a rally, so a covered call is not shown as a melt-up loss. {withStock
              ? <>Downside moves <b>include the shares' losses</b> on covered positions.</>
              : <>Downside moves count the <b>option overlay only</b> — the shares behind covered calls are a separate core holding (tick “Include stock losses” above to add them), so a covered call looks like it profits in a selloff.</>}</>
            : <> Short calls <b>print</b> in a selloff (green); the real risk is the <b>melt-up</b> (red).</>}
        </p>
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
          <div className="text-xs text-base-content/50 mt-1.5 leading-relaxed space-y-1">
            <p><b>How it's calculated:</b> for each name, a −20% market move × its β (a β 1.4 name falls 28%) with vol +15 pts, then a full reprice of that name's option legs; names are summed from their trades and the bars add up to the −20% tile above. Red loses, green gains.</p>
            <p className="flex items-start gap-1.5"><Compass className="w-3.5 h-3.5 mt-0.5 shrink-0 text-secondary" /><span><b>How to use it:</b> {byNameRead(rows)}</span></p>
          </div>
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
          <div className="text-xs text-base-content/50 mt-1.5 leading-relaxed space-y-1">
            <p><b>How to read it:</b> each cell is the option-overlay P&amp;L if the market moves by the row (× each name's β) and every leg's IV shifts by the column, repriced instantly. <b>Down a column</b> = gamma (a bigger move costs disproportionately more); <b>across a row</b> = vega (loss as vol rises); the 0% / 0v cell is $0 by construction.</p>
            {gridRead(grid, data.book_capital) && <p className="flex items-start gap-1.5"><Compass className="w-3.5 h-3.5 mt-0.5 shrink-0 text-secondary" /><span><b>How to use it:</b> {gridRead(grid, data.book_capital)}</span></p>}
          </div>
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
                <thead><tr className="text-base-content/45 text-left"><th className="px-3 py-1.5 font-normal" title="Market move; each name moves this × its β">Market (× β)</th><th className="px-3 font-normal">Book P&amp;L</th><th className="px-3 font-normal" title="Cash to take delivery on in-the-money NAKED short puts (strike ×100 × contracts)">Put assignment</th><th className="px-3 font-normal" title="Intrinsic to deliver / buy back in-the-money NAKED short calls">Call cover</th><th className="px-3 font-normal" title="Naked short put (P) and call (C) CONTRACTS in the money at that move">ITM contracts</th></tr></thead>
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
            <p className="text-xs text-base-content/45 leading-relaxed"><b>Put assignment</b> = cash to buy the shares you're put (strike ×100 × contracts); <b>Call cover</b> = intrinsic to deliver/buy back at that move. <b>ITM contracts</b>: <b>P</b> = naked short puts, <b>C</b> = naked short calls that are in the money at that move (e.g. “12P” = 12 contracts). Covered calls and spread-protected shorts are excluded — they are covered, or capped at the spread width, so they never demand this cash. The market column is the move × each name's β. Shaded row ≈ spot today.</p>
          </div>
        </Accordion>
      )}
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
  const [raw, setRaw] = useState<BookTailRiskResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // "Include stock losses" (covered calls, downside): remembered per browser; the backend ships both variants.
  const [withStock, setWithStockState] = useState<boolean>(() => { try { return localStorage.getItem(LS_STOCK) === '1'; } catch { return false; } });
  const setWithStock = (v: boolean) => { setWithStockState(v); try { localStorage.setItem(LS_STOCK, v ? '1' : '0'); } catch { /* private mode */ } };
  const data = useMemo(() => (raw && withStock && raw.with_stock_downside ? { ...raw, ...raw.with_stock_downside } as BookTailRiskResult : raw), [raw, withStock]);

  // On mount: the STORED snapshot (no recompute), so the header shows last-known numbers immediately.
  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      try { const r = await fetchBookTailRisk(quoteSource, false); if (alive && r.stored && !r.error) setRaw(r); }
      catch { /* no stored snapshot — the user can Analyze */ }
      finally { if (alive) setLoading(false); }
    })();
    return () => { alive = false; };
  }, [quoteSource]);

  // Refresh = recompute from live quotes AND overwrite the stored snapshot.
  const refresh = async () => {
    setRefreshing(true); setErr(null); setOpen(true);
    try { const r = await fetchBookTailRisk(quoteSource, true); if (r.error) setErr(r.error); else setRaw(r); }
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
              <Chip tone="accent" title={data?.scope ? `${data.scope.included} Income trades${data.scope.excluded_total > 0 ? ` · not included: ${excludedText(data.scope.excluded)}` : ''}` : 'Only trades tagged Income are analysed'}>Income only</Chip>
              {sc && <Chip tone={gradeTone}>{sc.grade}</Chip>}
              {sc && <span className="text-xs text-base-content/55">{sc.n_breach > 0 ? `${sc.n_breach} breached` : '0 breached'} · {sc.n_warn} watch · {passed} ok</span>}
            </div>
            <div className="text-xs text-base-content/45 truncate">
              {data ? `CVaR 1-mo ${money(data.cvar_95)}${data.cvar_capital_pct != null ? ` (${data.cvar_capital_pct}% of capital)` : ''} · θ ${money(data.net_theta)}/day${data.computed_at ? ` · updated ${timeAgo(data.computed_at)}` : ''}`
                : 'Your Income trades · guardrails · stress tests · concentration · the cheapest fix per breach'}
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
                {raw?.has_covered_stock && (
                  <label className="flex items-start gap-2.5 mb-4 rounded-lg border border-white/[0.08] bg-base-100/20 px-3 py-2 cursor-pointer">
                    <input type="checkbox" className="checkbox checkbox-sm mt-0.5" checked={withStock} onChange={e => setWithStock(e.target.checked)} aria-label="Include stock losses on covered calls" />
                    <span className="text-xs leading-relaxed">
                      <b className="text-[13px]">Include stock losses on covered calls (downside)</b>
                      <span className="block text-base-content/55">
                        {withStock
                          ? `On: a selloff also counts the loss on the ~${money(raw.stock_notional)} of shares behind your covered calls — CVaR, the tiles, fixes and hedges all use it.`
                          : `Off: a selloff counts the option overlay only. In a rally the shares' gain always offsets the short call, so covered calls never show a melt-up loss either way.`}
                      </span>
                    </span>
                  </label>
                )}
                {tab === 'overview' && <Overview data={data} grouped={grouped} onOpenFixes={() => setTab('fixes')} />}
                {tab === 'fixes' && <Fixes grouped={grouped} onManage={onManageTrade} toEvaluate={toEvaluate} />}
                {tab === 'stress' && <Stress data={data} withStock={withStock} />}
                {tab === 'concentration' && <BetaBookConcentration data={data} />}
                {tab === 'hedge' && <BetaBookHedge data={data} quoteSource={quoteSource} withStock={withStock && !!raw?.with_stock_downside}
                  fixes={grouped.trades.length ? { trades: grouped.trades.length, cost: grouped.trades.reduce((s, t) => s + (t.variants[0]?.rec.cost ?? 0), 0) } : null} />}
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
