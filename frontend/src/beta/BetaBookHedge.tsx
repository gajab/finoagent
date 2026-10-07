/**
 * BetaBookHedge — the Hedge tab of the Beta Manage Book, organised from RISK to HEDGE.
 *
 *   1. What drives your tail   the worst-5% months split into direction / convexity / vol / θ, and whether they are
 *                              rallies or selloffs — so you know WHICH hedge family can possibly help
 *   2. Insurance budget        a share of the income that has to pay for it (default 10%) — hedges are re-sized to it
 *   3. Hedges that cut CVaR    only the ones that actually reduce the book's CVaR on the same Monte-Carlo paths
 *                              (a put against a rally-driven tail does nothing, or hurts — it is listed below, with why)
 *   4. Risk → hedge map        each Greek exposure (β-Δ, net vega, short gamma): what it costs in a standard shock, what
 *                              neutralises it, what that costs — and whether it is even worth hedging
 *   5. AI hedge plan           explicit click only
 *
 * Every number comes from the backend's `hedge_plan` (full reprices on the MC sims); nothing here is recomputed.
 */
import React, { useState } from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, Compass, Loader2, Sparkles, Umbrella } from 'lucide-react';
import { fetchBookHedgeAdvice } from '../api';
import type { BookTailRiskResult } from '../api';
import { Accordion, Callout, Chip, SubHead, Tile, type Tone } from './BetaKit';
import BetaBookEtfHedge from './BetaBookEtfHedge';

type Plan = NonNullable<BookTailRiskResult['hedge_plan']>;
type Cand = Plan['candidates'][number];

const money = (v: number | null | undefined) =>
  v == null ? '—' : `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
const sgn = (v: number, plus = false) => `${v < 0 ? '−' : plus && v > 0 ? '+' : ''}${Math.abs(v)}`;

const PARTS: { k: 'delta' | 'gamma' | 'vega' | 'theta' | 'other'; label: string; fix: string }[] = [
  { k: 'delta', label: 'Direction (delta)', fix: 'index / SPY hedge, or cap the short strikes' },
  { k: 'gamma', label: 'Convexity (gamma)', fix: 'buy wings on the offending shorts (Fixes)' },
  { k: 'vega', label: 'Volatility (vega)', fix: 'VIX call spreads / long-vol' },
  { k: 'theta', label: 'Time decay (θ)', fix: 'this is the income that pays for insurance' },
  { k: 'other', label: 'Higher-order / expiry', fix: '—' },
];

function strikeText(c: Cand): string {
  const lo = c.long_strike, sh = c.short_strike;
  if (c.instrument === 'VIXY') return 'signal-based sleeve';
  const k = lo != null ? `${lo}${sh != null ? `/${sh}` : ''}` : '';
  return `${c.instrument} ${k} ${c.kind}`.trim();
}

function ticket(c: Cand, units: number, annualCost: number): string {
  if (c.standby) return `Standby: hold ~${money(units * 1000)} cash for the VIXY sleeve; buy VIXY only when spot VIX closes above VIX3M (backwardation).`;
  const dte = c.dte_days ? ` (~${c.dte_days}d)` : '';
  return `BUY ${units}× ${strikeText(c)}${dte} — ${c.label}, ~${money(annualCost)}/yr`;
}

// ── 1. what drives the tail ───────────────────────────────────────────────────
function TailDrivers({ plan }: { plan: Plan }) {
  const t = plan.tail;
  if (!t) return null;
  const rows = PARTS.map(p => ({ ...p, v: t[p.k] })).sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const max = Math.max(1, ...rows.map(r => Math.abs(r.v)));
  const sidePct = t.side === 'up' ? t.up_pct : t.down_pct;
  const vegaShare = t.avg_tail_pnl < 0 ? Math.abs(Math.min(0, t.vega)) / Math.abs(t.avg_tail_pnl) * 100 : 0;
  return (
    <div className="space-y-2.5">
      <SubHead hint={`the average of your worst 5% of months: ${money(t.avg_tail_pnl)}`}>What drives your tail</SubHead>
      <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
        <b>{sidePct.toFixed(0)}% of your worst months are {t.side === 'up' ? 'rallies' : 'selloffs'}.</b>{' '}
        {t.side === 'up'
          ? <>Index puts and VIX calls pay when the market falls or vol spikes — they cannot cut this tail. It needs <b>upside</b> protection: capping the short calls (Fixes) or index call spreads.</>
          : <>Index put spreads and VIX / vol hedges pay in a selloff — the right family for this tail.</>}
        {vegaShare > 0 && vegaShare < 15 && <> Vol is only ~{vegaShare.toFixed(0)}% of the loss, so a vol hedge on its own would barely move it.</>}
      </Callout>
      <div className="rounded-xl border border-white/[0.07] bg-base-100/20 divide-y divide-white/[0.05]">
        {rows.map(r => (
          <div key={r.k} className="px-3 py-2 flex items-center gap-3 text-xs">
            <span className="w-36 shrink-0 text-[13px]">{r.label}</span>
            <div className="flex-1 h-2.5 rounded-full bg-base-content/[0.07] overflow-hidden">
              <div className={`h-full rounded-full ${r.v < 0 ? 'bg-error/60' : 'bg-success/55'}`} style={{ width: `${Math.max(Math.abs(r.v) > 0 ? 2 : 0, Math.abs(r.v) / max * 100)}%` }} />
            </div>
            <span className={`w-20 text-right font-mono tabular-nums shrink-0 ${r.v < 0 ? 'text-error' : 'text-success'}`}>{money(r.v)}</span>
            <span className="hidden md:block w-64 shrink-0 text-[11px] text-base-content/45 truncate" title={r.fix}>hedge: {r.fix}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-base-content/45 leading-relaxed">Taylor split (Δ·ΔS, ½Γ·ΔS², vega·Δσ, θ·Δt) of the same Monte-Carlo months that define CVaR; “higher-order” is what the full reprice adds (expiring legs, curvature in vol).</p>
    </div>
  );
}

// ── 3. one hedge at the chosen budget ────────────────────────────────────────
function PlanRow({ c, budget, plan, onStage, staged }: { c: Cand; budget: number; plan: Plan; onStage: (text: string, key: string) => void; staged: string | null }) {
  const pl = c.plans.find(p => p.budget_pct === budget);
  const affordable = !!pl && pl.units >= 1;
  const tone: Tone = c.recommended ? 'good' : 'neutral';
  const key = c.label + c.instrument;
  return (
    <div className={`rounded-xl border p-3 space-y-2.5 ${c.recommended ? 'border-success/40 bg-success/[0.05]' : 'border-white/[0.07] bg-base-100/20'}`}>
      <div className="flex items-center gap-2 flex-wrap">
        {c.recommended && <Chip tone="good">Start here</Chip>}
        {c.standby && <Chip tone="info" title="A rule that arms on a signal — not a trade you can put on today">Standby</Chip>}
        <span className="text-[13px] font-semibold">{c.label}</span>
        <span className="text-xs font-mono text-base-content/55">{strikeText(c)}</span>
        {c.pricing && <span className="text-[11px] text-base-content/40">· {c.pricing}</span>}
        <span className="ml-auto text-xs font-mono text-base-content/60">{affordable ? `${pl!.units} × ${c.unit}` : '—'}</span>
      </div>
      {affordable ? (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <Tile label="Cost / yr" value={money(pl!.annual_cost)} tone="warn" sub={`${pl!.cost_pct_income}% of income`} />
          <Tile label="CVaR after" value={money(pl!.cvar_after)} tone="good" sub={`${money(-(pl!.cvar_reduction ?? 0))}${pl!.reduction_pct != null ? ` · −${pl!.reduction_pct}%` : ''}`} />
          <Tile label="Closes the gap" value={pl!.closes_gap_pct != null ? `${pl!.closes_gap_pct}%` : '—'} tone={(pl!.closes_gap_pct ?? 0) >= 100 ? 'good' : undefined}
            sub={plan.gap > 0 ? `to the ${money(plan.limit_warn)} limit` : 'already inside the limit'} />
          <Tile label="CVaR cut per $1/yr" value={pl!.per_dollar != null ? `$${pl!.per_dollar}` : '—'} sub="value for money" />
        </div>
      ) : (
        <p className="text-xs text-base-content/50">One {c.unit} costs ~{money(c.unit_annual)}/yr — more than a {budget}% budget buys. Pick a larger budget to size this one.</p>
      )}
      <div className="flex items-center gap-2 flex-wrap">
        {c.targets.map(t => <Chip key={t} tone={tone === 'good' ? 'good' : 'neutral'}>{t}</Chip>)}
        {affordable && (
          <button className={`btn btn-xs gap-1 ml-auto normal-case ${staged === key ? 'btn-success' : 'btn-outline'}`} onClick={() => onStage(ticket(c, pl!.units, pl!.annual_cost ?? 0), key)}
            title="Copies the ticket text — nothing is placed">
            {staged === key ? <><CheckCircle2 className="w-3.5 h-3.5" /> Copied</> : <>{c.standby ? 'Copy the rule' : 'Copy ticket'} <ArrowRight className="w-3.5 h-3.5" /></>}
          </button>
        )}
      </div>
      {c.standby && c.signal && <p className="text-[11px] text-base-content/45">Deploy rule: {c.signal}. Until it fires there is nothing to buy; the cash is what you set aside.</p>}
    </div>
  );
}

// ── 4. risk → hedge map ──────────────────────────────────────────────────────
function ExposureMap({ plan, fixes }: { plan: Plan; fixes: { trades: number; cost: number } | null }) {
  const rows = plan.exposures ?? [];
  if (rows.length === 0) return null;
  return (
    <div>
      <SubHead hint="each exposure → what it costs in a standard shock → what neutralises it">Risk → hedge map</SubHead>
      <div className="rounded-xl border border-white/[0.07] bg-base-100/20 divide-y divide-white/[0.05]">
        {rows.map(r => {
          const small = Math.abs(r.worst_pct_capital) < 1;
          const impacts = [r.impact_down != null ? `down ${money(r.impact_down)}` : null, r.impact_up != null ? `${r.key === 'vega' ? 'vol up' : 'up'} ${money(r.impact_up)}` : null].filter(Boolean).join(' · ');
          return (
            <div key={r.key} className="px-3 py-3 space-y-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[13px] font-semibold">{r.label}</span>
                {r.value && <span className="text-xs font-mono text-base-content/55">{r.value}</span>}
                <Chip tone={small ? 'good' : 'warn'} title="Worst standard-shock loss as a % of the book's committed capital">{small ? 'small' : 'material'} · {sgn(r.worst_pct_capital)}% of capital</Chip>
              </div>
              <div className="text-xs text-base-content/60">If {r.shock}: <span className="font-mono">{impacts}</span></div>
              {r.key === 'gamma' && fixes && fixes.trades > 0
                ? <div className="text-xs"><b>Hedge:</b> cap the short strikes — {fixes.trades} trade{fixes.trades === 1 ? '' : 's'} in the Fixes tab, ~{money(fixes.cost)} to buy the wings. Targeted cover is cheaper and more exact than an index option.</div>
                : r.hedge
                  ? <div className="text-xs"><b>Hedge:</b> {r.hedge.action}{r.hedge.annual_cost != null && r.hedge.annual_cost > 0 ? ` · ~${money(r.hedge.annual_cost)}/yr` : ''}{r.hedge.notional ? ` · ${money(r.hedge.notional)} notional` : ''}
                    <span className="text-base-content/50"> — {r.hedge.note}</span></div>
                  : <div className="text-xs text-base-content/50">No standalone hedge needed.</div>}
              {small && <div className="text-xs text-base-content/50">Too small to insure on its own — the tail hedges above already carry what vega/gamma there is.</div>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── AI plan (click only) ─────────────────────────────────────────────────────
function AiPlan({ quoteSource, withStock }: { quoteSource: string; withStock: boolean }) {
  const [advice, setAdvice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ask = async () => {
    setLoading(true); setErr(null);
    try { const r = await fetchBookHedgeAdvice(quoteSource, withStock); if (r.error) setErr(r.error); else setAdvice(r.advice || null); }
    catch (e: any) { setErr(e?.message || 'Failed to get hedging strategy'); } finally { setLoading(false); }
  };
  return (
    <div className="rounded-2xl border border-secondary/25 bg-secondary/[0.04] p-4 space-y-3">
      <div className="flex items-center gap-2"><Sparkles className="w-4 h-4 text-secondary" /><span className="text-sm font-semibold">AI hedging strategy · Income book</span>
        <button className="btn btn-secondary btn-sm gap-1.5 ml-auto" disabled={loading} onClick={ask}>{loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}{loading ? 'Analyzing…' : advice ? 'Regenerate' : 'Get AI hedge plan'}</button>
      </div>
      {err && <Callout tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}>{err}</Callout>}
      {advice && <div className="text-[13px] whitespace-pre-wrap leading-relaxed">{advice}</div>}
      {!advice && !err && !loading && <p className="text-xs text-base-content/55 leading-relaxed">Sends only computed data — greeks, CVaR, crash P&amp;Ls, the per-name loss waterfall, and the <b>deterministic factor read</b> (GICS sectors, measured-ρ correlated clusters, macro loadings) — plus the hedge menu. The model reasons over your real concentration to say what to trim/hedge; it does no arithmetic and can't invent a correlation the data didn't measure. It runs only when you press the button.</p>}
    </div>
  );
}

// ── legacy menu (sized to offset a −20% crash) ───────────────────────────────
function LegacyMenu({ data }: { data: BookTailRiskResult }) {
  const menu = data.hedge_menu || [];
  if (menu.length === 0) return null;
  return (
    <Accordion title="Menu sized to offset the −20% crash" icon={<Umbrella className="w-4 h-4" />} summary="the older view — sized to the loss, not to a budget">
      <div className="space-y-2">
        {menu.map((c, i) => {
          const lo = c.long_strike ?? c.long_put, sh = c.short_strike ?? c.short_put; const isVixy = c.instrument === 'VIXY';
          return (
            <div key={i} className="rounded-xl border border-white/[0.07] bg-base-100/20 p-3">
              <div className="flex items-center gap-2 flex-wrap">
                {c.recommended && <CheckCircle2 className="w-3.5 h-3.5 text-success" />}
                <Chip tone={c.instrument === 'VIX' ? 'accent' : isVixy ? 'info' : 'neutral'}>{c.instrument ?? 'SPX'}</Chip>
                <span className="text-[13px] font-semibold">{c.label}</span>
                {c.standby && <Chip tone="info">Standby</Chip>}
                <span className="ml-auto font-mono text-xs text-base-content/60">{isVixy ? `${money(c.sleeve_capital)} cash` : (lo != null ? `${lo}${sh ? `/${sh}` : ''}` : '—')}{!isVixy && c.contracts != null ? ` · ×${c.contracts}` : ''}</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-2 mt-2.5">
                <div><div className="text-[11px] text-base-content/45">Cost / yr</div><div className="text-xs font-semibold tabular-nums">{money(c.annual_bleed)}{isVixy ? '*' : ''}</div></div>
                <div><div className="text-[11px] text-base-content/45">Offsets −20%</div><div className="text-xs font-semibold tabular-nums">{c.offsets_pct != null ? `${c.offsets_pct}%` : '—'}</div></div>
                <div><div className="text-[11px] text-base-content/45" title="Book CVaR-95 change (negative = it makes the tail worse)">CVaR change</div><div className={`text-xs font-semibold tabular-nums ${c.cvar_reduction > 0 ? 'text-success' : 'text-error'}`}>{c.cvar_reduction > 0 ? '−' : '+'}{money(Math.abs(c.cvar_reduction))}</div></div>
                <div><div className="text-[11px] text-base-content/45" title="Spitznagel: compound-growth lift">CAGR</div><div className={`text-xs font-semibold tabular-nums ${c.cagr_lift_pct >= 0 ? 'text-success' : 'text-error'}`}>{sgn(c.cagr_lift_pct, true)}%</div></div>
              </div>
            </div>
          );
        })}
        <p className="text-xs text-base-content/45 leading-relaxed"><b>*</b> the VIXY sleeve is near-zero cost but ties up cash and only deploys when the VIX curve inverts. A positive CVaR change means the hedge lowers your tail; <b>negative means it raises it</b> (its decay cost lands in the months that already hurt).</p>
      </div>
    </Accordion>
  );
}

export default function BetaBookHedge({ data, quoteSource, withStock, fixes }: { data: BookTailRiskResult; quoteSource: string; withStock: boolean; fixes: { trades: number; cost: number } | null }) {
  const plan = data.hedge_plan ?? null;
  const [budget, setBudget] = useState<number | null>(null);
  const [staged, setStaged] = useState<{ key: string } | null>(null);
  if (!plan) {
    return (
      <div className="space-y-4">
        <Callout tone="neutral" icon={<Umbrella className="w-4 h-4" />}>{data.hedge_note || 'No hedge plan for this snapshot — press Refresh.'}</Callout>
        <BetaBookEtfHedge data={data} />
        <LegacyMenu data={data} />
        <AiPlan quoteSource={quoteSource} withStock={withStock} />
      </div>
    );
  }
  const b = budget ?? plan.default_budget;
  const helpful = plan.candidates.filter(c => c.helps);
  const useless = plan.candidates.filter(c => !c.helps);
  const stage = async (text: string, key: string) => {
    try { await navigator.clipboard.writeText(text); setStaged({ key }); setTimeout(() => setStaged(null), 2000); } catch { /* clipboard blocked */ }
  };

  return (
    <div className="space-y-5">
      <TailDrivers plan={plan} />

      {/* 2 · budget */}
      <div className="space-y-2">
        <SubHead hint={`your option income is ${money(plan.annual_income)}/yr`}>Insurance budget</SubHead>
        {!plan.income_ok
          ? <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}>The book has no net income to pay for insurance, so no budget plan can be sized.</Callout>
          : (
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Insurance budget">
              {plan.budgets.map(x => (
                <button key={x} onClick={() => setBudget(x)} aria-pressed={x === b}
                  className={`rounded-lg border px-3 py-1.5 text-left transition-colors ${x === b ? 'border-primary/60 bg-primary/15' : 'border-white/10 hover:border-white/25'}`}>
                  <div className={`text-sm font-semibold ${x === b ? 'text-primary' : ''}`}>{x}% of income</div>
                  <div className="text-[11px] text-base-content/50 tabular-nums">≤ {money(plan.annual_income * x / 100)}/yr</div>
                </button>
              ))}
              <span className="text-xs text-base-content/50 max-w-md">Insurance should be a small slice of what the book earns. Each hedge below is sized to spend no more than this.</span>
            </div>
          )}
        <div className="flex flex-wrap gap-2">
          <Tile label="CVaR now" value={money(plan.cvar_now)} tone="warn" sub={`${plan.cvar_pct_capital}% of capital`} />
          <Tile label="Guardrail" value={`≤ ${money(plan.limit_warn)}`} sub={plan.gap > 0 ? `${money(plan.gap)} over` : 'inside the limit'} tone={plan.gap > 0 ? 'bad' : 'good'} />
        </div>
      </div>

      {/* 3 · hedges that cut CVaR */}
      <div className="space-y-2">
        <SubHead hint={`sized to ≤ ${b}% of your income, repriced on the same Monte-Carlo months`}>Hedges that actually cut CVaR</SubHead>
        {helpful.length === 0
          ? <Callout tone="warn" icon={<AlertTriangle className="w-3.5 h-3.5" />}>
              No index or vol hedge cuts your CVaR at any budget up to {plan.budgets[plan.budgets.length - 1]}% of income — the tail is concentrated in a few names, not in the market.
              Use the <b>Fixes</b> tab (cap the offending shorts) instead.
            </Callout>
          : helpful.map(c => <PlanRow key={c.label + c.instrument} c={c} budget={b} plan={plan} onStage={stage} staged={staged?.key ?? null} />)}
      </div>

      {useless.length > 0 && (
        <Accordion title="Doesn't help your tail" icon={<Compass className="w-4 h-4" />} summary={`${useless.length} hedge${useless.length === 1 ? '' : 's'} — and why`}>
          <ul className="divide-y divide-white/[0.05] rounded-lg border border-white/[0.06]">
            {useless.map(c => (
              <li key={c.label + c.instrument} className="px-3 py-2 text-xs flex items-baseline gap-2 flex-wrap">
                <span className="font-semibold text-[13px]">{c.label}</span>
                <span className="font-mono text-base-content/50">{strikeText(c)}</span>
                <span className="text-base-content/60 w-full sm:w-auto sm:ml-auto">{c.reason}</span>
              </li>
            ))}
          </ul>
        </Accordion>
      )}

      <BetaBookEtfHedge data={data} />

      <ExposureMap plan={plan} fixes={fixes} />
      {data.hedge_note && <p className="text-xs text-base-content/55">{data.hedge_note}</p>}
      <LegacyMenu data={data} />
      <AiPlan quoteSource={quoteSource} withStock={withStock} />
    </div>
  );
}
