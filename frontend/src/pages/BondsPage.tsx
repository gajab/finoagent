import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Activity, BarChart3, Calculator, Flag, Layers, LayoutGrid, Lightbulb, Plus, Receipt, RefreshCw, ScrollText, ShieldCheck, ShoppingCart, Table2, Waves,
} from 'lucide-react';
import ErrorBoundary from '../components/ErrorBoundary';
import { fetchBondHoldings, fetchBondLadders, fetchBondPortfolio } from '../api';
import type { BondFilters, BondHoldingInput, BondLadderParams, BondPortfolio } from '../types';
import BondOverview from '../components/bonds/BondOverview';
import BondHoldings from '../components/bonds/BondHoldings';
import BondHoldingForm from '../components/bonds/BondHoldingForm';
import BondCashFlow from '../components/bonds/BondCashFlow';
import BondLadders from '../components/bonds/BondLadders';
import TipsCenter from '../components/bonds/TipsCenter';
import BondRisk from '../components/bonds/BondRisk';
import BondTax, { ProfileEditor } from '../components/bonds/BondTax';
import BondPlanner from '../components/bonds/BondPlanner';
import BondBuyPlanner from '../components/bonds/BondBuyPlanner';
import BondMarket from '../components/bonds/BondMarket';
import BondInsights from '../components/bonds/BondInsights';
import { Card, ErrorBox, Loading, fmtDate } from '../components/bonds/bondUi';
import BondFilterBar from '../components/bonds/BondFilterBar';
import { loadFilters, saveFilters } from '../components/bonds/bondFilters';

const TABS = [
  { id: 'overview', label: 'Overview', icon: LayoutGrid },
  { id: 'holdings', label: 'Holdings', icon: Table2 },
  { id: 'cashflow', label: 'Cash Flow', icon: Waves },
  { id: 'ladders', label: 'Ladders', icon: Layers },
  { id: 'tips', label: 'TIPS', icon: ShieldCheck },
  { id: 'risk', label: 'Risk', icon: Activity },
  { id: 'tax', label: 'Tax & Profile', icon: Receipt },
  { id: 'planner', label: 'Planner', icon: Flag },
  { id: 'buy', label: 'What to buy', icon: ShoppingCart },
  { id: 'market', label: 'Market', icon: BarChart3 },
  { id: 'insights', label: 'Insights', icon: Lightbulb },
] as const;
type TabId = typeof TABS[number]['id'];
const NEEDS_BOOK: TabId[] = ['overview', 'holdings', 'cashflow', 'risk', 'tax', 'planner', 'insights'];

function Onboarding({ data, onAdd, onGoto, onChanged }: { data: BondPortfolio | null; onAdd: () => void; onGoto: (t: TabId) => void; onChanged: () => void }) {
  const steps: { icon: React.ReactNode; title: string; body: string; cta: string; go: () => void }[] = [
    { icon: <Plus className="h-5 w-5" />, title: 'Add what you own', body: 'Treasuries, TIPS, munis, corporates, CDs, bond ETFs & funds. Paste a CUSIP — we fill in the terms and price Treasuries live.', cta: 'Add a bond', go: onAdd },
    { icon: <Layers className="h-5 w-5" />, title: 'Build a ladder', body: 'Pick an amount and years — get a buy list of real Treasury CUSIPs, CDs, munis or iBonds ETFs, chosen for your after-tax yield.', cta: 'Ladder builder', go: () => onGoto('ladders') },
    { icon: <ShieldCheck className="h-5 w-5" />, title: 'Lock in real income', body: 'A TIPS ladder turns today\'s real yields into an inflation-proof paycheck, year by year.', cta: 'TIPS ladder', go: () => onGoto('tips') },
    { icon: <Calculator className="h-5 w-5" />, title: 'Explore the market', body: 'Live curves, credit spreads, the after-tax yield menu and a full bond calculator.', cta: 'Market', go: () => onGoto('market') },
  ];
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-primary/20 bg-gradient-to-br from-primary/10 via-base-100/60 to-secondary/10 p-6">
        <h2 className="text-lg font-bold">Your fixed-income command center</h2>
        <p className="mt-1 max-w-2xl text-sm text-base-content/60">Track every bond you own, see exactly what cash arrives when, what you keep after tax, how exposed you are to rates, and build ladders that fund your goals — all priced off live Treasury data.</p>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {steps.map(s => (
            <div key={s.title} className="flex flex-col rounded-xl border border-white/[0.06] bg-base-100/70 p-4">
              <div className="text-primary">{s.icon}</div>
              <div className="mt-2 text-sm font-semibold">{s.title}</div>
              <p className="mt-1 flex-1 text-xs leading-relaxed text-base-content/55">{s.body}</p>
              <button className="btn btn-sm btn-outline btn-primary mt-3" onClick={s.go}>{s.cta}</button>
            </div>
          ))}
        </div>
      </div>
      {data && (
        <Card title="First, tell us how you're taxed" subtitle="After-tax yields decide whether Treasuries, CDs or munis win for you — set this once.">
          <ProfileEditor profile={data.profile} onSaved={onChanged} compact />
        </Card>
      )}
    </div>
  );
}

// tooltip for the header yield: what the number is made of
const yieldTitle = (s: BondPortfolio['summary']): string => {
  const p = s.yield_parts;
  if (!p || p.total_pct == null) return 'Market-value-weighted yield';
  return `Total yield ${p.total_pct.toFixed(2)}% if held to maturity (rates unchanged):\n`
    + `• ${(p.income_pct ?? 0).toFixed(2)}% income — coupons + fund distributions in cash\n`
    + `• ${(p.price_gain_pct ?? 0).toFixed(2)}% price gain to maturity — discount bonds paid back at 100, box/accumulating funds growing in price\n`
    + `• ${(p.inflation_pct ?? 0).toFixed(2)}% inflation — TIPS principal rising with CPI\n`
    + `Funds use their estimated yield to maturity (or your own yield).`;
};

export default function BondsPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get('tab') as TabId | null;
  const tab: TabId = TABS.some(t => t.id === tabParam) ? (tabParam as TabId) : 'overview';
  const [data, setData] = useState<BondPortfolio | null>(null);
  const [raw, setRaw] = useState<BondHoldingInput[]>([]);
  const [ladders, setLadders] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState<{ open: boolean; initial: BondHoldingInput | null }>({ open: false, initial: null });
  const [ladderPrefill, setLadderPrefill] = useState<Partial<BondLadderParams> | null>(null);
  const [filters, setFiltersState] = useState<BondFilters>(loadFilters);
  const setFilters = (f: BondFilters) => { setFiltersState(f); saveFilters(f); };
  const started = useRef(false);

  const load = useCallback(async (soft = false) => {
    if (soft) setRefreshing(true); else setLoading(true);
    setErr(null);
    try {
      const [p, h, l] = await Promise.all([fetchBondPortfolio(), fetchBondHoldings(), fetchBondLadders().catch(() => [])]);
      setData(p); setRaw(h); setLadders(l.map(x => ({ id: x.id, name: x.name })));
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not load your bond book');
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, []);
  useEffect(() => {
    if (started.current) return;   // StrictMode double-mount guard — the first load is the heavy one
    started.current = true;
    load();
  }, [load]);

  const goto = (t: TabId) => { const next = new URLSearchParams(params); next.set('tab', t); setParams(next, { replace: true }); window.scrollTo({ top: 0 }); };
  const openAdd = () => setForm({ open: true, initial: null });
  const openEdit = (id: number) => setForm({ open: true, initial: raw.find(h => h.id === id) ?? null });
  const refresh = () => load(true);
  const hasBook = (data?.holdings.length ?? 0) + (data?.watchlist.length ?? 0) > 0;
  const s = data?.summary;

  const body = () => {
    if (!data) return null;
    if (!hasBook && tab === 'overview') return <Onboarding data={data} onAdd={openAdd} onGoto={goto} onChanged={refresh} />;
    switch (tab) {
      case 'overview': return <BondOverview data={data} onGoto={t => goto(t as TabId)} />;
      case 'holdings': return <BondHoldings data={data} filters={filters} onAdd={openAdd} onEdit={openEdit} onChanged={refresh} />;
      case 'buy': return <BondBuyPlanner data={data} onChanged={refresh} onGotoPlanner={() => goto('planner')} />;
      case 'cashflow': return <BondCashFlow key={data.as_of + (s?.market_value ?? 0)} initial={data.cash_flow} filters={filters} />;
      case 'ladders': return <BondLadders onChanged={refresh} prefill={ladderPrefill} />;
      case 'tips': return <TipsCenter onChanged={refresh} />;
      case 'risk': return <BondRisk data={data} />;
      case 'tax': return <BondTax data={data} onChanged={refresh} />;
      case 'planner': return <BondPlanner profile={data.profile} filters={filters} onChanged={refresh} onFundGaps={() => goto('buy')}
        onBuildLadder={(pf, tips) => { if (tips) goto('tips'); else { setLadderPrefill(pf); goto('ladders'); } }} />;
      case 'market': return <BondMarket />;
      case 'insights': return <BondInsights data={data} onGotoHolding={() => goto('holdings')} />;
      default: return null;
    }
  };

  return (
    <div className="container-app space-y-4 py-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold"><ScrollText className="h-5 w-5 text-primary" /> Bond Desk</h1>
          <p className="text-xs text-base-content/50">
            Individual bonds, CDs & bond funds — cash flow, after-tax yield, duration, ladders and TIPS income.
            {data?.fedinvest_as_of && <> Treasury prices: FedInvest {fmtDate(data.fedinvest_as_of)}.</>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {s && hasBook && (
            <div className="hidden items-center gap-4 rounded-xl border border-white/[0.06] bg-base-100/60 px-4 py-1.5 text-xs md:flex">
              <span><span className="text-base-content/45">Value </span><b className="tabular-nums">${Math.round(s.market_value).toLocaleString()}</b></span>
              <span title={yieldTitle(s)}>
                <span className="text-base-content/45">Yield </span><b className="tabular-nums">{(s.total_yield_pct ?? s.ytw_pct) != null ? `${(s.total_yield_pct ?? s.ytw_pct)!.toFixed(2)}%` : '—'}</b>
                {s.yield_parts?.income_pct != null && s.yield_parts?.price_gain_pct != null && (
                  <span className="ml-1 text-[10.5px] text-base-content/45 tabular-nums">
                    ({s.yield_parts.income_pct.toFixed(2)} income {s.yield_parts.price_gain_pct < 0 ? '−' : '+'} {Math.abs(s.yield_parts.price_gain_pct).toFixed(2)} at maturity
                    {(s.yield_parts.inflation_pct ?? 0) >= 0.005 && <> + {s.yield_parts.inflation_pct!.toFixed(2)} inflation</>})
                  </span>
                )}
              </span>
              <span><span className="text-base-content/45">After-tax </span><b className="tabular-nums text-emerald-400">{s.after_tax_yield_pct != null ? `${s.after_tax_yield_pct.toFixed(2)}%` : '—'}</b></span>
              <span><span className="text-base-content/45">Dur </span><b className="tabular-nums">{s.eff_duration != null ? s.eff_duration.toFixed(1) : '—'}</b></span>
            </div>
          )}
          <button className="btn btn-ghost btn-sm" onClick={refresh} disabled={refreshing || loading} title="Reprice everything">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          </button>
          <button className="btn btn-primary btn-sm" onClick={openAdd}><Plus className="h-3.5 w-3.5" /> Add bond</button>
        </div>
      </header>

      <nav className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1">
        {TABS.map(t => {
          const Icon = t.icon;
          const active = tab === t.id;
          const badge = t.id === 'insights' && data?.recommendations.filter(r => r.severity === 'high').length;
          return (
            <button key={t.id} onClick={() => goto(t.id)}
              className={`flex shrink-0 items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium transition-colors ${
                active ? 'border-primary/25 bg-primary/15 text-primary' : 'border-transparent text-base-content/60 hover:bg-base-200/50 hover:text-base-content'}`}>
              <Icon className="h-3.5 w-3.5" />{t.label}
              {badge ? <span className="rounded-full bg-rose-500/80 px-1.5 text-[9px] font-bold text-white">{badge}</span> : null}
            </button>
          );
        })}
      </nav>

      {err && <ErrorBox message={err} onRetry={() => load()} />}
      {data && (tab === 'holdings' || tab === 'cashflow' || tab === 'planner') && (
        <BondFilterBar holdings={raw} value={filters} onChange={setFilters}
          scope={tab === 'holdings' ? 'holdings' : tab === 'cashflow' ? 'the cash flow' : 'the plan'} />
      )}
      {loading && NEEDS_BOOK.includes(tab)
        ? <Loading label="Pricing your book off live Treasury data…" />
        : (
          <ErrorBoundary label={`the ${TABS.find(t => t.id === tab)?.label} tab`}>
            {tab === 'market' || tab === 'tips' || tab === 'ladders' ? (
              tab === 'market' ? <BondMarket /> : tab === 'tips' ? <TipsCenter onChanged={refresh} /> : <BondLadders onChanged={refresh} prefill={ladderPrefill} />
            ) : body()}
          </ErrorBoundary>
        )}

      {form.open && (
        <BondHoldingForm initial={form.initial} ladders={ladders}
          onClose={() => setForm({ open: false, initial: null })}
          onSaved={() => { setForm({ open: false, initial: null }); refresh(); }} />
      )}
    </div>
  );
}
