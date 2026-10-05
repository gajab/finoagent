import React, { useMemo, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Eye, Pencil, Plus, Trash2, CheckCircle2, Wallet } from 'lucide-react';
import { deleteBondHolding, setBondHoldingStatus } from '../../api';
import type { BondFilters, BondPortfolio, BondRow } from '../../types';
import { filtersActive, matchesFilters } from './bondFilters';
import BondHoldingsVisuals from './BondHoldingsVisuals';
import { Card, Empty, KindBadge, Seg, YieldBreakdown, bp, fmtDate, num, pct, pnlClass, tenorLabel, usd } from './bondUi';

type SortKey = 'maturity' | 'market_value' | 'ytw_pct' | 'eff_duration' | 'after_tax' | 'label';

const Metric: React.FC<{ k: string; v: React.ReactNode; hint?: string }> = ({ k, v, hint }) => (
  <div className="flex items-center justify-between gap-2 py-0.5 text-[11px]" title={hint}>
    <span className="text-base-content/50">{k}</span><span className="font-medium tabular-nums text-base-content/85">{v}</span>
  </div>
);

function Detail({ r }: { r: BondRow }) {
  const krd = Object.entries(r.krd ?? {}).map(([t, d]) => ({ t: Number(t), d })).sort((a, b) => a.t - b.t);
  const maxK = Math.max(0.0001, ...krd.map(k => Math.abs(k.d)));
  const isFund = r.kind === 'etf' || r.kind === 'mutual_fund';
  return (
    <div className="grid gap-4 bg-base-200/30 px-4 py-3 md:grid-cols-4">
      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/40">Price & yield</div>
        {isFund ? (
          <>
            <Metric k="Price" v={usd(r.price ?? null, { cents: true })} />
            <Metric k="Total yield (used)" v={<b>{pct(r.total_yield_pct ?? r.ytw_pct, 3)}</b>} hint={r.yield_basis} />
            {r.fund?.user_yield_pct != null && <Metric k="Your yield" v={pct(r.fund.user_yield_pct, 2)} />}
            <Metric k="Est. yield to maturity" v={pct(r.fund?.est_ytm_pct, 3)} hint={r.fund?.est_basis ?? 'Not estimated for this fund type — uses its distributions'} />
            <Metric k="Distributions (TTM)" v={pct(r.fund?.distribution_yield_pct)} />
            <Metric k="Pays out" v={r.fund?.payout === 'accumulates' ? 'accumulates — when sold' : 'distributions'} hint={r.fund?.payout_source} />
            <Metric k="Expense ratio" v={pct(r.fund?.expense_ratio_pct, 2)} />
            <Metric k="Avg maturity" v={r.fund?.avg_maturity ? `${num(r.fund.avg_maturity, 1)}y` : '—'} />
            {r.defined_maturity_year && <Metric k="Matures" v={`Dec ${r.defined_maturity_year}`} />}
          </>
        ) : (
          <>
            <Metric k="Clean / dirty" v={`${num(r.price ?? null, 3)} / ${num(r.dirty_price ?? null, 3)}`} />
            <Metric k="Total yield" v={<b>{pct(r.total_yield_pct, 3)}</b>}
              hint={r.kind === 'tips' ? 'Real yield + expected inflation (nominal)' : 'Yield to worst: coupons + the gain/loss as the price pulls to par'} />
            <Metric k="YTM" v={pct(r.ytm_pct, 3)} />
            <Metric k="YTW" v={`${pct(r.ytw_pct, 3)}${r.ytw_kind === 'call' ? ` (call ${fmtDate(r.ytw_date)})` : ''}`} />
            {r.ytc_pct != null && <Metric k="Yield to call" v={pct(r.ytc_pct, 3)} />}
            <Metric k="Book yield (at cost)" v={pct(r.book_yield_pct, 3)} />
            <Metric k="Current yield" v={pct(r.current_yield_pct, 3)} />
            {r.spread_bp != null && <Metric k="Spread vs UST" v={bp(r.spread_bp)} />}
          </>
        )}
        <YieldBreakdown parts={r.yield_parts} className="mt-1.5" />
        <div className="mt-1 text-[10px] leading-snug text-base-content/40">Source: {r.price_source}</div>
      </div>
      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/40">Risk</div>
        <Metric k="Effective duration" v={`${num(r.eff_duration, 2)}y`} hint="To worst — reflects the call option" />
        {!isFund && <Metric k="Modified (to maturity)" v={`${num(r.mod_duration, 2)}y`} />}
        <Metric k="Convexity" v={num(r.convexity, 2)} />
        <Metric k="DV01" v={usd(r.dv01, { cents: true })} />
        {r.callable && <Metric k="Callable" v={`${fmtDate(r.call_date)} @ ${num(r.call_price, 2)}`} />}
        {r.no_mark_to_market && <Metric k="Value at maturity" v={usd(r.value_at_maturity)} />}
        {krd.length > 0 && (
          <div className="mt-2 space-y-0.5">
            <div className="text-[10px] text-base-content/40">Key-rate duration</div>
            {krd.map(k => (
              <div key={k.t} className="flex items-center gap-1.5 text-[10px]">
                <span className="w-7 text-base-content/45">{tenorLabel(k.t)}</span>
                <div className="h-1.5 flex-1 rounded bg-base-300/60"><div className="h-1.5 rounded bg-sky-400/70" style={{ width: `${(Math.abs(k.d) / maxK) * 100}%` }} /></div>
                <span className="w-9 text-right tabular-nums text-base-content/60">{k.d.toFixed(2)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/40">Tax ({r.account_type.toUpperCase()})</div>
        <Metric k="Federal" v={r.tax?.fed_taxable ? 'taxable' : 'exempt'} />
        <Metric k="State" v={r.tax?.state_taxable ? 'taxable' : 'exempt'} />
        <Metric k="Your rate on interest" v={pct(r.tax?.rate_pct, 1)} />
        <Metric k="After-tax yield" v={<span className="text-emerald-400">{pct(r.tax?.after_tax_yield_pct, 3)}</span>} />
        <Metric k="Tax-equivalent yield" v={pct(r.tax?.tey_pct, 3)} />
        {r.tax?.after_tax_real_pct != null && <Metric k="After-tax REAL yield" v={pct(r.tax.after_tax_real_pct, 3)} />}
        <ul className="mt-1 space-y-0.5">{(r.tax?.notes ?? []).map((n, i) => <li key={i} className="text-[10px] leading-snug text-base-content/45">• {n}</li>)}</ul>
      </div>
      <div>
        <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-base-content/40">Cash</div>
        <Metric k="Annual income" v={usd(r.annual_income)} />
        {!!r.annual_accrual && Math.abs(r.annual_accrual) >= 1 && (
          <Metric k={r.annual_accrual > 0 ? 'Builds in price / yr' : 'Price erosion / yr'} v={usd(r.annual_accrual)}
            hint="Return that isn't paid in cash — realized when you sell (accumulating funds, NAV pull-to-par)" />
        )}
        {r.next_coupon_date && <Metric k="Next coupon" v={`${fmtDate(r.next_coupon_date)} · ${usd(r.next_coupon_amount ?? null)}`} />}
        {r.tips && (
          <>
            <Metric k="Index ratio" v={`${num(r.tips.index_ratio, 5)}${r.tips.index_ratio_projected ? '*' : ''}`} hint="* CPI not yet published — projected" />
            <Metric k="Inflation-adjusted principal" v={usd(r.tips.adjusted_principal)} />
            <Metric k="Real yield" v={pct(r.tips.real_yield_pct, 3)} />
            <Metric k="Breakeven inflation" v={pct(r.tips.breakeven_pct, 2)} />
          </>
        )}
        {r.cost_basis != null && <Metric k="Cost basis" v={usd(r.cost_basis)} />}
        {r.warnings.length > 0 && (
          <ul className="mt-2 space-y-1">
            {r.warnings.map((w, i) => <li key={i} className="flex gap-1 text-[10px] leading-snug text-amber-300/80"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{w}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

// Subtotals for the filtered rows — the same definitions as the book summary: sums, market-value-weighted
// yields, and duration = DV01 ÷ (value × 1bp) so it reconciles with the DV01 shown.
function subtotal(rows: BondRow[]) {
  const live = rows.filter(r => r.status === 'held' && !r.matured);
  const mv = live.reduce((a, r) => a + (r.market_value || 0), 0);
  const w = (f: (r: BondRow) => number | null | undefined) => {
    let n = 0, d = 0;
    live.forEach(r => { const v = f(r); if (v != null && r.market_value > 0) { n += v * r.market_value; d += r.market_value; } });
    return d ? n / d : null;
  };
  const dv01 = live.reduce((a, r) => a + (r.dv01 || 0), 0);
  return {
    n: live.length, mv, dv01, income: live.reduce((a, r) => a + (r.annual_income || 0), 0),
    pnl: live.reduce((a, r) => a + (r.unrealized_pnl || 0), 0), ytw: w(r => r.total_yield_pct ?? r.ytw_pct),
    at: w(r => r.tax?.after_tax_yield_pct), dur: mv ? dv01 / (mv * 1e-4) : null,
  };
}

export default function BondHoldings({ data, filters, onAdd, onEdit, onChanged }: {
  data: BondPortfolio;
  filters?: BondFilters;
  onAdd: () => void;
  onEdit: (id: number) => void;
  onChanged: () => void;
}) {
  const [view, setView] = useState<'held' | 'watch' | 'closed'>('held');
  const [layout, setLayout] = useState<'table' | 'charts'>(() => {
    try { return localStorage.getItem('bonds.holdings.layout') === 'charts' ? 'charts' : 'table'; } catch { return 'table'; }
  });
  const pickLayout = (l: 'table' | 'charts') => { setLayout(l); try { localStorage.setItem('bonds.holdings.layout', l); } catch { /* private mode */ } };
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: 'maturity', dir: 1 });
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const rows = useMemo(() => {
    const f = filters && filtersActive(filters) ? filters : null;
    const pick = (list: BondRow[]) => (f ? list.filter(r => matchesFilters(r, f)) : list);
    const base = view === 'watch' ? pick(data.watchlist)
      : view === 'closed' ? pick(data.holdings.filter(r => r.status === 'sold' || r.status === 'matured'))
      : pick(data.holdings.filter(r => r.status === 'held'));
    const val = (r: BondRow): number | string => {
      switch (sort.k) {
        case 'maturity': return r.maturity ?? (r.defined_maturity_year ? `${r.defined_maturity_year}-12-15` : '9999');
        case 'market_value': return r.market_value;
        case 'ytw_pct': return r.total_yield_pct ?? r.ytw_pct ?? -99;
        case 'eff_duration': return r.eff_duration ?? -1;
        case 'after_tax': return r.tax?.after_tax_yield_pct ?? -99;
        default: return r.label.toLowerCase();
      }
    };
    return [...base].sort((a, b) => (val(a) > val(b) ? 1 : val(a) < val(b) ? -1 : 0) * sort.dir);
  }, [data, view, sort, filters]);
  const sub = useMemo(() => (filters && filtersActive(filters) && view === 'held' ? subtotal(rows) : null), [rows, filters, view]);

  const act = async (id: number, fn: () => Promise<unknown>) => {
    setBusy(id);
    try { await fn(); onChanged(); } finally { setBusy(null); }
  };
  const Th: React.FC<{ k: SortKey; children: React.ReactNode; right?: boolean }> = ({ k, children, right }) => (
    <th className={`cursor-pointer select-none px-2 py-2 font-medium ${right ? 'text-right' : 'text-left'}`}
      onClick={() => setSort(s => ({ k, dir: s.k === k ? (s.dir === 1 ? -1 : 1) : (k === 'maturity' || k === 'label' ? 1 : -1) }))}>
      {children}{sort.k === k ? (sort.dir === 1 ? ' ↑' : ' ↓') : ''}
    </th>
  );
  const closedCount = data.holdings.filter(r => r.status === 'sold' || r.status === 'matured').length;

  return (
    <Card title="Holdings" subtitle="Every bond, CD and bond fund — marked to market, with yield, duration and after-tax view"
      right={<>
        {view === 'held' && <Seg value={layout} onChange={v => pickLayout(v as typeof layout)} options={[{ value: 'table', label: 'Table' }, { value: 'charts', label: 'Charts' }]} />}
        <Seg value={view} onChange={v => setView(v as typeof view)} options={[
          { value: 'held', label: `Held (${data.holdings.filter(r => r.status === 'held').length})` },
          { value: 'watch', label: `Watchlist (${data.watchlist.length})` },
          ...(closedCount ? [{ value: 'closed', label: `Closed (${closedCount})` }] : []),
        ]} />
        <button className="btn btn-primary btn-sm" onClick={onAdd}><Plus className="h-3.5 w-3.5" /> Add</button>
      </>} pad={false}>
      {rows.length === 0 ? (
        filters && filtersActive(filters) ? (
          <Empty title="Nothing matches these filters" body="Clear or widen the filters above to see more holdings." />
        ) : <Empty icon={view === 'watch' ? <Eye className="h-8 w-8" /> : <Wallet className="h-8 w-8" />}
          title={view === 'watch' ? 'Your watchlist is empty' : 'No bonds yet'}
          body={view === 'watch' ? 'Add bonds you are considering with status "Watchlist" — or adopt a ladder plan as a watchlist.'
            : 'Add Treasuries, TIPS, munis, corporates, CDs or bond funds. Paste a CUSIP and we fill in the terms.'}
          action={<button className="btn btn-primary btn-sm" onClick={onAdd}><Plus className="h-3.5 w-3.5" /> Add a bond</button>} />
      ) : view === 'held' && layout === 'charts' ? (
        <BondHoldingsVisuals rows={rows} curve={data.curve?.nominal} />
      ) : (
        <div className="overflow-x-auto">
          {sub && (
            <div className="flex flex-wrap gap-x-5 gap-y-1 border-y border-primary/15 bg-primary/5 px-4 py-2 text-[11px]">
              <span className="font-semibold text-primary">Filtered: {sub.n} holding{sub.n === 1 ? '' : 's'}</span>
              <span><span className="text-base-content/45">Value </span><b className="tabular-nums">{usd(sub.mv)}</b></span>
              <span><span className="text-base-content/45">P&L </span><b className={`tabular-nums ${pnlClass(sub.pnl)}`}>{usd(sub.pnl, { sign: true })}</b></span>
              <span><span className="text-base-content/45">Yield </span><b className="tabular-nums">{pct(sub.ytw)}</b></span>
              <span><span className="text-base-content/45">After-tax </span><b className="tabular-nums text-emerald-400">{pct(sub.at)}</b></span>
              <span><span className="text-base-content/45">Duration </span><b className="tabular-nums">{num(sub.dur, 2)}y</b></span>
              <span><span className="text-base-content/45">DV01 </span><b className="tabular-nums">{usd(sub.dv01)}</b></span>
              <span><span className="text-base-content/45">Income/yr </span><b className="tabular-nums">{usd(sub.income)}</b></span>
            </div>
          )}
          <table className="w-full text-xs">
            <thead className="border-y border-white/[0.05] text-[10px] uppercase tracking-wide text-base-content/45">
              <tr>
                <th className="w-6" />
                <Th k="label">Holding</Th>
                <Th k="maturity">Maturity</Th>
                <th className="px-2 py-2 text-right font-medium" title="As on your statement: face (par) for bonds & CDs, shares for funds">Quantity</th>
                <th className="px-2 py-2 text-right font-medium" title="Per 100 of face (real price for TIPS); $/share for funds">Price</th>
                <Th k="market_value" right>Value</Th>
                <th className="px-2 py-2 text-right font-medium">P&L</th>
                <Th k="ytw_pct" right>Yield</Th>
                <Th k="after_tax" right>After-tax</Th>
                <Th k="eff_duration" right>Dur.</Th>
                <th className="px-2 py-2 text-right font-medium">Income/yr</th>
                <th className="w-24" />
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const isOpen = open === r.id;
                return (
                  <React.Fragment key={r.id}>
                    <tr className={`border-b border-white/[0.03] transition-colors hover:bg-base-200/40 ${r.matured ? 'opacity-60' : ''}`}>
                      <td className="pl-2"><button onClick={() => setOpen(isOpen ? null : r.id)} className="text-base-content/40 hover:text-base-content">
                        {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}</button></td>
                      <td className="px-2 py-2">
                        <div className="flex items-center gap-2">
                          <KindBadge kind={r.kind} />
                          <div className="min-w-0">
                            <div className="truncate font-medium text-base-content/90">{r.label}</div>
                            <div className="flex gap-2 text-[10px] text-base-content/40">
                              {r.cusip && <span>{r.cusip}</span>}{r.ticker && <span>{r.ticker}</span>}
                              {r.rating_group !== 'GOVT' && r.rating_group !== 'NR' && <span>{r.rating ?? r.rating_group}</span>}
                              <span>{r.account_type.toUpperCase()}</span>
                              {r.likely_called && <span className="text-amber-400">likely called</span>}
                              {r.estimated_mark && <span className="text-sky-400/70" title={r.price_source}>est. mark</span>}
                              {r.warnings.length > 0 && <AlertTriangle className="h-3 w-3 text-amber-400/80" />}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="whitespace-nowrap px-2 py-2 tabular-nums text-base-content/70">
                        {r.maturity ? fmtDate(r.maturity) : r.defined_maturity_year ? `Dec ${r.defined_maturity_year}` : 'open-ended'}
                        {r.years_to_maturity != null && <div className="text-[10px] text-base-content/40">{num(r.years_to_maturity, 1)}y</div>}
                      </td>
                      <td className="px-2 py-2 text-right tabular-nums">
                        {r.face != null ? r.face.toLocaleString() : r.quantity != null ? `${num(r.quantity, 2)} sh` : '—'}
                        {r.tips && <div className="text-[10px] text-base-content/40">× IR {num(r.tips.index_ratio, 4)}</div>}
                      </td>
                      <td className="px-2 py-2 text-right tabular-nums text-base-content/75">
                        {r.price != null ? (r.kind === 'etf' || r.kind === 'mutual_fund' ? `$${num(r.price, 2)}` : num(r.price, 3)) : '—'}
                        {r.kind !== 'etf' && r.kind !== 'mutual_fund' && r.price != null && <div className="text-[10px] text-base-content/35">{r.tips ? 'real /100' : '/100'}</div>}
                      </td>
                      <td className="px-2 py-2 text-right font-semibold tabular-nums" title={r.accrued_usd ? `Current value excl. accrued interest (as on statements); incl. accrued ${usd(r.market_value, { cents: true })}` : undefined}>
                        {usd(r.clean_value ?? r.market_value)}
                        {!!r.accrued_usd && <div className="text-[10px] font-normal text-base-content/40">+{usd(r.accrued_usd)} accrued</div>}
                      </td>
                      <td className={`px-2 py-2 text-right tabular-nums ${pnlClass(r.unrealized_pnl)}`}>{usd(r.unrealized_pnl ?? null, { sign: true })}</td>
                      <td className="px-2 py-2 text-right tabular-nums"
                        title={`${r.yield_basis ?? ''}${r.yield_parts ? `\n${pct(r.yield_parts.income_pct)} income + ${pct(r.yield_parts.price_gain_pct)} price gain to maturity${(r.yield_parts.inflation_pct ?? 0) > 0 ? ` + ${pct(r.yield_parts.inflation_pct)} inflation` : ''}` : ''}`}>
                        {pct(r.total_yield_pct ?? r.ytw_pct)}
                        {r.kind === 'tips' && r.ytw_pct != null && <div className="text-[9.5px] text-base-content/40">real {pct(r.ytw_pct)}</div>}
                        {r.fund?.payout === 'accumulates' && <div className="text-[9.5px] text-teal-300/70">in price</div>}
                      </td>
                      <td className="px-2 py-2 text-right tabular-nums text-emerald-400/90">{pct(r.tax?.after_tax_yield_pct)}</td>
                      <td className="px-2 py-2 text-right tabular-nums">{num(r.eff_duration, 1)}</td>
                      <td className="px-2 py-2 text-right tabular-nums text-base-content/75">{usd(r.annual_income)}</td>
                      <td className="px-2 py-2">
                        <div className="flex justify-end gap-0.5">
                          {r.matured && r.status === 'held' && (
                            <button title="Mark matured" disabled={busy === r.id} className="btn btn-ghost btn-xs text-emerald-400"
                              onClick={() => act(r.id, () => setBondHoldingStatus(r.id, 'matured'))}><CheckCircle2 className="h-3.5 w-3.5" /></button>
                          )}
                          {r.status === 'watch' && (
                            <button title="I bought it" disabled={busy === r.id} className="btn btn-ghost btn-xs text-emerald-400"
                              onClick={() => act(r.id, () => setBondHoldingStatus(r.id, 'held'))}><CheckCircle2 className="h-3.5 w-3.5" /></button>
                          )}
                          <button title="Edit" className="btn btn-ghost btn-xs" onClick={() => onEdit(r.id)}><Pencil className="h-3.5 w-3.5" /></button>
                          <button title="Delete" disabled={busy === r.id} className="btn btn-ghost btn-xs text-rose-400/80"
                            onClick={() => { if (window.confirm(`Delete ${r.label}?`)) act(r.id, () => deleteBondHolding(r.id)); }}>
                            <Trash2 className="h-3.5 w-3.5" /></button>
                        </div>
                      </td>
                    </tr>
                    {isOpen && <tr><td colSpan={12} className="p-0"><Detail r={r} /></td></tr>}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
