/**
 * LogTradeModal — trade journal entry.
 *
 * Two modes:
 *   • Linked (company prop provided): opened from Tracking page, ticker/name
 *     pre-filled, thesis auto-fill available.
 *   • Standalone (no company prop): opened from My Trades, user types ticker only.
 *
 * Trade types:
 *   long      → stock_long
 *   short     → stock_short
 *   options   → options_* / options_spread
 *   combo     → stock_combo  (stock position + option legs together)
 */

import React, { useState, useEffect } from 'react';
import {
  X, TrendingUp, TrendingDown, Loader2, AlertCircle,
  Sparkles, Plus, Trash2, ClipboardList, Layers, Activity,
} from 'lucide-react';
import { createManualTrade } from '../api';
import type { TrackedCompany } from '../types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type TradeKind = 'long' | 'short' | 'options' | 'combo' | 'futures';
type AnalysisBasis = 'technical' | 'fundamental' | 'both';
type OptionAction = 'buy' | 'sell';
type OptionType = 'call' | 'put';

interface OptionLeg {
  id: number;
  action: OptionAction;
  type: OptionType;
  contracts: number;
  strike: string;
  expiration: string;
  premium: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  company?: TrackedCompany;   // optional — standalone mode when absent
  onLogged: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let legIdSeq = 0;
const newLeg = (): OptionLeg => ({
  id: ++legIdSeq,
  action: 'buy', type: 'call',
  contracts: 1, strike: '', expiration: '', premium: '',
});

const fmtMoney = (n: number | null) =>
  n == null ? '—' : `$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const today = () => new Date().toISOString().slice(0, 10);

// ---------------------------------------------------------------------------
// Pill button
// ---------------------------------------------------------------------------

function Pill({ active, onClick, children, color = 'primary' }: {
  active: boolean; onClick: () => void; children: React.ReactNode; color?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all
        ${active
          ? `border-${color}/40 bg-${color}/15 text-${color}`
          : 'border-white/[0.08] bg-base-100/50 text-base-content/50 hover:border-white/15 hover:text-base-content/80'
        }`}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Option leg row
// ---------------------------------------------------------------------------

function LegRow({ leg, onChange, onRemove, canRemove }: {
  leg: OptionLeg;
  onChange: (id: number, patch: Partial<OptionLeg>) => void;
  onRemove: (id: number) => void;
  canRemove: boolean;
}) {
  const cost = leg.premium && leg.contracts
    ? parseFloat(leg.premium) * leg.contracts * 100 * (leg.action === 'buy' ? -1 : 1)
    : null;

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-100/40 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1">
          {(['buy', 'sell'] as OptionAction[]).map(a => (
            <button key={a} type="button"
              onClick={() => onChange(leg.id, { action: a })}
              className={`px-2.5 py-1 rounded-lg text-xs font-semibold border transition-all
                ${leg.action === a
                  ? a === 'buy'
                    ? 'border-success/40 bg-success/10 text-success'
                    : 'border-error/40 bg-error/10 text-error'
                  : 'border-white/[0.06] text-base-content/40 hover:border-white/15'
                }`}
            >
              {a === 'buy' ? 'Bought' : 'Sold (wrote)'}
            </button>
          ))}
        </div>
        <div className="flex gap-1">
          {(['call', 'put'] as OptionType[]).map(t => (
            <button key={t} type="button"
              onClick={() => onChange(leg.id, { type: t })}
              className={`px-2.5 py-1 rounded-lg text-xs font-semibold border transition-all capitalize
                ${leg.type === t
                  ? 'border-primary/40 bg-primary/10 text-primary'
                  : 'border-white/[0.06] text-base-content/40 hover:border-white/15'
                }`}
            >
              {t}
            </button>
          ))}
        </div>
        {canRemove && (
          <button type="button" onClick={() => onRemove(leg.id)} className="ml-auto btn btn-ghost btn-xs text-error/60 hover:text-error">
            <Trash2 className="w-3 h-3" />
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div>
          <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Contracts</label>
          <input type="number" min={1} step={1}
            className="input input-bordered input-xs w-full"
            value={leg.contracts}
            onChange={e => onChange(leg.id, { contracts: Math.max(1, parseInt(e.target.value) || 1) })}
          />
        </div>
        <div>
          <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Strike $</label>
          <input type="number" min={0} step={0.5} placeholder="900"
            className="input input-bordered input-xs w-full"
            value={leg.strike}
            onChange={e => onChange(leg.id, { strike: e.target.value })}
          />
        </div>
        <div>
          <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Expiry</label>
          <input type="date"
            className="input input-bordered input-xs w-full"
            value={leg.expiration}
            onChange={e => onChange(leg.id, { expiration: e.target.value })}
          />
        </div>
        <div>
          <label className="text-[9px] uppercase text-base-content/40 mb-0.5 block">Premium / share $</label>
          <input type="number" min={0} step={0.01} placeholder="12.50"
            className="input input-bordered input-xs w-full"
            value={leg.premium}
            onChange={e => onChange(leg.id, { premium: e.target.value })}
          />
        </div>
      </div>

      {cost !== null && (
        <p className={`text-[10px] font-medium ${cost < 0 ? 'text-error/70' : 'text-success/70'}`}>
          {cost < 0 ? 'Cost' : 'Credit'}: {fmtMoney(cost)}
          <span className="text-base-content/30 font-normal ml-1">
            ({leg.contracts} × 100 × ${leg.premium || '0'})
          </span>
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main modal
// ---------------------------------------------------------------------------

export default function LogTradeModal({ open, onClose, company, onLogged }: Props) {
  const isStandalone = !company;

  const [kind, setKind]               = useState<TradeKind>('long');
  const [ticker, setTicker]           = useState('');
  const [shares, setShares]           = useState('');
  const [price, setPrice]             = useState('');
  const [tradeDate, setTradeDate]     = useState(today());
  const [why, setWhy]                 = useState('');
  const [basis, setBasis]             = useState<AnalysisBasis>('both');
  const [legs, setLegs]               = useState<OptionLeg[]>([newLeg()]);
  const [saving, setSaving]           = useState(false);
  const [err, setErr]                 = useState<string | null>(null);

  const [futuresExpiry, setFuturesExpiry] = useState('');
  const [futuresAction, setFuturesAction] = useState<'long' | 'short'>('long');
  const [multiplier, setMultiplier]       = useState('1');
  const [marginReq, setMarginReq]         = useState('');

  // Reset on open
  useEffect(() => {
    if (open) {
      setKind('long');
      setTicker(company?.ticker || '');
      setShares(''); setPrice('');
      setTradeDate(today()); setWhy(''); setBasis('both');
      setLegs([newLeg()]); setSaving(false); setErr(null);
      setFuturesExpiry('');
      setFuturesAction('long');
      setMultiplier('1');
      setMarginReq('');
    }
  }, [open, company]);

  if (!open) return null;

  // Auto-fill thesis from research context (only in linked mode)
  const autoFill = () => {
    const llm = company?.llm_data;
    if (!llm) return;
    const parts: string[] = [];
    if (llm.thesis) parts.push(llm.thesis);
    if (llm.catalysts?.length) parts.push('Key catalysts: ' + llm.catalysts.join(' · '));
    if (llm.risk) parts.push('Key risk: ' + llm.risk);
    setWhy(parts.join('\n\n'));
  };

  const updateLeg = (id: number, patch: Partial<OptionLeg>) =>
    setLegs(prev => prev.map(l => l.id === id ? { ...l, ...patch } : l));
  const addLeg = () => setLegs(prev => [...prev, newLeg()]);
  const removeLeg = (id: number) => setLegs(prev => prev.filter(l => l.id !== id));

  const effectiveTicker = (company?.ticker || ticker).toUpperCase().trim();
  const effectiveName   = company?.name || effectiveTicker;  // use ticker as fallback name

  // Computed totals
  const stockTotal    = shares && price ? parseFloat(shares) * parseFloat(price) : null;
  const optionsTotal  = legs.reduce((sum, l) => {
    const v = l.premium && l.contracts ? parseFloat(l.premium) * l.contracts * 100 : 0;
    return sum + (l.action === 'buy' ? -v : v);
  }, 0);
  const comboTotal    = (stockTotal ?? 0) + optionsTotal;

  const hasOptions = kind === 'options' || kind === 'combo';
  const hasStock   = kind === 'long' || kind === 'short' || kind === 'combo';

  const legsValid  = legs.every(l => l.strike && l.expiration && l.premium && l.contracts > 0);
  const stockValid = !!(shares && parseFloat(shares) > 0 && price && parseFloat(price) > 0);

  const canSubmit = () => {
    if (!effectiveTicker) return false;
    if (kind === 'long' || kind === 'short' || kind === 'futures') return stockValid && !!tradeDate;
    if (kind === 'options') return legsValid && !!tradeDate;
    // combo: at least stock OR at least one valid leg
    return (stockValid || legsValid) && !!tradeDate;
  };

  const submit = async () => {
    if (!canSubmit()) return;
    setSaving(true);
    setErr(null);

    try {
      const t = effectiveTicker;
      if (kind === 'futures') {
        const contractsVal = parseFloat(shares) || 1;
        const entryPriceVal = parseFloat(price) || 0;
        const multVal = parseFloat(multiplier) || 1.0;
        const marginVal = marginReq ? parseFloat(marginReq) : undefined;
        const net = contractsVal * entryPriceVal * multVal * (futuresAction === 'short' ? 1 : -1);
        await createManualTrade({
          strategy_type: 'futures',
          name: `Futures ${futuresAction === 'long' ? 'Long' : 'Short'} ${t} — ${effectiveName}`,
          ticker: t,
          parameters: {
            ticker: t,
            name: effectiveName,
            contracts: contractsVal,
            multiplier: multVal,
            action: futuresAction,
            avg_cost: entryPriceVal,
            expiration: futuresExpiry || undefined,
            margin_req: marginVal,
            why,
            expectation_type: basis,
            ...(company ? { tracked_company_id: company.id } : {}),
          },
          legs_data: [],
          entry_prices: [{ ticker: t, price: entryPriceVal }],
          entry_net_debit: net,
          order_source: 'manual',
          notes: why || undefined,
          entry_date: tradeDate,
        });

      } else if (kind === 'long' || kind === 'short') {
        const sh = parseFloat(shares);
        const pr = parseFloat(price);
        const net = sh * pr * (kind === 'short' ? 1 : -1);
        await createManualTrade({
          strategy_type: kind === 'long' ? 'stock_long' : 'stock_short',
          name: `${kind === 'long' ? 'Long' : 'Short'} ${t} — ${effectiveName}`,
          ticker: t,
          parameters: {
            ticker: t, name: effectiveName, shares: sh,
            why, expectation_type: basis,
            ...(company ? { tracked_company_id: company.id } : {}),
          },
          legs_data: [],
          entry_prices: [{ ticker: t, price: pr }],
          entry_net_debit: net,
          order_source: 'manual',
          notes: why || undefined,
          entry_date: tradeDate,
        });

      } else if (kind === 'options') {
        const legsData = legs.map((l, idx) => ({
          action: l.action, type: l.type, qty: l.contracts,
          strike: parseFloat(l.strike), expiration: l.expiration,
          premium: parseFloat(l.premium), label: `Leg ${idx + 1}`,
        }));
        const stratType = legs.length === 1
          ? `options_${legs[0].action}_${legs[0].type}`
          : 'options_spread';
        await createManualTrade({
          strategy_type: stratType,
          name: legs.length === 1
            ? `${legs[0].action === 'buy' ? 'Long' : 'Short'} ${legs[0].type.charAt(0).toUpperCase() + legs[0].type.slice(1)} ${t}`
            : `Options Spread ${t} — ${effectiveName}`,
          ticker: t,
          parameters: {
            ticker: t, name: effectiveName, why, expectation_type: basis,
            legs_count: legs.length,
            ...(company ? { tracked_company_id: company.id } : {}),
          },
          legs_data: legsData,
          entry_prices: legs.map((l, idx) => ({ leg: idx, action: l.action, premium: parseFloat(l.premium) })),
          entry_net_debit: optionsTotal,
          order_source: 'manual',
          notes: why || undefined,
          entry_date: tradeDate,
        });

      } else {
        // combo: stock + options legs
        const sh = stockValid ? parseFloat(shares) : 0;
        const pr = stockValid ? parseFloat(price) : 0;
        const stockNet = sh * pr * -1;  // cost outlay (negative)
        const legsData = legs.map((l, idx) => ({
          action: l.action, type: l.type, qty: l.contracts,
          strike: parseFloat(l.strike), expiration: l.expiration,
          premium: parseFloat(l.premium), label: `Leg ${idx + 1}`,
        }));
        await createManualTrade({
          strategy_type: 'stock_combo',
          name: `Combo ${t} — ${effectiveName}`,
          ticker: t,
          parameters: {
            ticker: t, name: effectiveName, shares: sh, why,
            expectation_type: basis,
            options_net_debit: optionsTotal,
            ...(company ? { tracked_company_id: company.id } : {}),
          },
          legs_data: legsData,
          entry_prices: [{ ticker: t, price: pr }, ...legs.map((l, idx) => ({ leg: idx, action: l.action, premium: parseFloat(l.premium) }))],
          entry_net_debit: stockNet + optionsTotal,
          order_source: 'manual',
          notes: why || undefined,
          entry_date: tradeDate,
        });
      }

      onLogged();
      onClose();
    } catch (e: any) {
      setErr(e?.message || 'Failed to save trade');
    } finally {
      setSaving(false);
    }
  };

  const hasThesis = !!(company?.llm_data?.thesis || company?.llm_data?.catalysts?.length || company?.llm_data?.risk);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-base-200 rounded-2xl border border-white/10 w-full max-w-lg shadow-2xl flex flex-col max-h-[90vh]">

        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3.5 border-b border-white/[0.06] flex-shrink-0">
          <div>
            <div className="flex items-center gap-2">
              <ClipboardList className="w-4 h-4 text-primary" />
              <span className="font-semibold text-sm">Log Trade</span>
            </div>
            {company ? (
              <p className="text-xs text-base-content/40 mt-0.5">
                <span className="font-mono font-bold text-base-content/70">{company.ticker}</span>
                {' — '}{company.name}
              </p>
            ) : (
              <p className="text-xs text-base-content/40 mt-0.5">Record a trade in your journal</p>
            )}
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm btn-square">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body — scrollable */}
        <div className="overflow-y-auto flex-1 p-4 space-y-5">

          {/* Standalone ticker input */}
          {isStandalone && (
            <div>
              <label className="label py-1"><span className="label-text text-xs">Ticker</span></label>
              <input
                type="text" placeholder="NVDA" maxLength={10}
                className="input input-bordered input-sm w-full font-mono uppercase"
                value={ticker}
                onChange={e => setTicker(e.target.value.toUpperCase())}
              />
            </div>
          )}

          {/* Trade type */}
          <div>
            <p className="text-xs font-medium text-base-content/60 mb-2">What did you do?</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-5">
              <button type="button" onClick={() => setKind('long')}
                className={`flex items-center justify-center gap-1.5 py-2.5 rounded-xl border text-xs font-semibold transition-all
                  ${kind === 'long' ? 'border-success/40 bg-success/10 text-success' : 'border-white/[0.08] text-base-content/50 hover:border-white/15'}`}>
                <TrendingUp className="w-3.5 h-3.5" /> Long
              </button>
              <button type="button" onClick={() => setKind('short')}
                className={`flex items-center justify-center gap-1.5 py-2.5 rounded-xl border text-xs font-semibold transition-all
                  ${kind === 'short' ? 'border-error/40 bg-error/10 text-error' : 'border-white/[0.08] text-base-content/50 hover:border-white/15'}`}>
                <TrendingDown className="w-3.5 h-3.5" /> Short
              </button>
              <button type="button" onClick={() => setKind('options')}
                className={`flex items-center justify-center gap-1.5 py-2.5 rounded-xl border text-xs font-semibold transition-all
                  ${kind === 'options' ? 'border-primary/40 bg-primary/10 text-primary' : 'border-white/[0.08] text-base-content/50 hover:border-white/15'}`}>
                Options
              </button>
              <button type="button" onClick={() => setKind('combo')}
                className={`flex items-center justify-center gap-1.5 py-2.5 rounded-xl border text-xs font-semibold transition-all
                  ${kind === 'combo' ? 'border-warning/40 bg-warning/10 text-warning' : 'border-white/[0.08] text-base-content/50 hover:border-white/15'}`}>
                <Layers className="w-3.5 h-3.5" /> Combo
              </button>
              <button type="button" onClick={() => setKind('futures')}
                className={`flex items-center justify-center gap-1.5 py-2.5 rounded-xl border text-xs font-semibold transition-all
                  ${kind === 'futures' ? 'border-info/40 bg-info/10 text-info' : 'border-white/[0.08] text-base-content/50 hover:border-white/15'}`}>
                <Activity className="w-3.5 h-3.5" /> Futures
              </button>
            </div>
            {kind === 'combo' && (
              <p className="text-[10px] text-base-content/40 mt-1.5">
                Stock position + options legs — P&amp;L tracked together
              </p>
            )}
            {kind === 'futures' && (
              <p className="text-[10px] text-base-content/40 mt-1.5">
                Standalone futures contract positions
              </p>
            )}
          </div>

          {/* Stock fields */}
          {hasStock && (
            <div className="space-y-3">
              {kind === 'combo' && (
                <p className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold">Stock leg</p>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label py-1"><span className="label-text text-xs">Shares</span></label>
                  <input
                    type="number" min={1} step={1} placeholder="100"
                    className="input input-bordered input-sm w-full"
                    value={shares}
                    onChange={e => setShares(e.target.value)}
                  />
                </div>
                <div>
                  <label className="label py-1">
                    <span className="label-text text-xs">
                      {kind === 'short' ? 'Short price / share' : 'Price paid / share'}
                    </span>
                  </label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40 text-sm">$</span>
                    <input
                      type="number" min={0} step={0.01} placeholder="875.23"
                      className="input input-bordered input-sm w-full pl-6"
                      value={price}
                      onChange={e => setPrice(e.target.value)}
                    />
                  </div>
                </div>
              </div>
              {stockTotal !== null && (
                <p className="text-xs text-base-content/50">
                  {kind === 'combo' ? 'Stock cost' : (kind === 'long' ? 'Total cost' : 'Total proceeds')}:
                  <span className={`font-semibold ml-1 ${kind === 'long' || kind === 'combo' ? 'text-error/70' : 'text-success/70'}`}>
                    {fmtMoney(stockTotal)}
                  </span>
                </p>
              )}
            </div>
          )}

          {/* Futures fields */}
          {kind === 'futures' && (
            <div className="space-y-4">
              <div>
                <p className="text-xs font-medium text-base-content/60 mb-2">Direction</p>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setFuturesAction('long')}
                    className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all
                      ${futuresAction === 'long'
                        ? 'border-success/40 bg-success/10 text-success'
                        : 'border-white/[0.08] bg-base-100/50 text-base-content/50 hover:border-white/15'}`}>
                    Long
                  </button>
                  <button type="button" onClick={() => setFuturesAction('short')}
                    className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all
                      ${futuresAction === 'short'
                        ? 'border-error/40 bg-error/10 text-error'
                        : 'border-white/[0.08] bg-base-100/50 text-base-content/50 hover:border-white/15'}`}>
                    Short
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="label py-1"><span className="label-text text-xs">Contracts</span></label>
                  <input
                    type="number" min={1} step={1} placeholder="1"
                    className="input input-bordered input-sm w-full font-mono"
                    value={shares}
                    onChange={e => setShares(e.target.value)}
                  />
                </div>
                <div>
                  <label className="label py-1"><span className="label-text text-xs">Entry Price</span></label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40 text-sm">$</span>
                    <input
                      type="number" min={0} step={0.01} placeholder="5050.25"
                      className="input input-bordered input-sm w-full pl-6 font-mono"
                      value={price}
                      onChange={e => setPrice(e.target.value)}
                    />
                  </div>
                </div>
                <div>
                  <label className="label py-1">
                    <span className="label-text text-xs flex items-center gap-1">
                      Contract Multiplier
                    </span>
                  </label>
                  <input
                    type="number" min={0.001} step={0.01} placeholder="1.0"
                    className="input input-bordered input-sm w-full font-mono"
                    value={multiplier}
                    onChange={e => setMultiplier(e.target.value)}
                  />
                </div>
                <div>
                  <label className="label py-1"><span className="label-text text-xs">Expiration Date</span></label>
                  <input
                    type="date"
                    className="input input-bordered input-sm w-full font-mono text-xs"
                    value={futuresExpiry}
                    onChange={e => setFuturesExpiry(e.target.value)}
                  />
                </div>
                <div>
                  <label className="label py-1"><span className="label-text text-xs">Margin Requirement (Total)</span></label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-base-content/40 text-sm">$</span>
                    <input
                      type="number" min={0} step={1} placeholder="12000"
                      className="input input-bordered input-sm w-full pl-6 font-mono text-xs"
                      value={marginReq}
                      onChange={e => setMarginReq(e.target.value)}
                    />
                  </div>
                </div>
              </div>

              {shares && price && (
                <p className="text-xs text-base-content/50">
                  Notional value:{' '}
                  <span className="font-semibold text-base-content/75">
                    {fmtMoney(parseFloat(shares) * parseFloat(price) * (parseFloat(multiplier) || 1.0))}
                  </span>
                </p>
              )}
            </div>
          )}

          {/* Options legs */}
          {hasOptions && (
            <div className="space-y-3">
              {kind === 'combo' && (
                <p className="text-[10px] uppercase tracking-wider text-base-content/40 font-semibold">Options legs</p>
              )}
              {legs.map((leg, idx) => (
                <div key={leg.id}>
                  {legs.length > 1 && (
                    <p className="text-[10px] uppercase tracking-wider text-base-content/30 mb-1.5">Leg {idx + 1}</p>
                  )}
                  <LegRow leg={leg} onChange={updateLeg} onRemove={removeLeg} canRemove={legs.length > 1} />
                </div>
              ))}
              {legs.length < 6 && (
                <button type="button" onClick={addLeg}
                  className="btn btn-ghost btn-xs gap-1.5 text-base-content/50 hover:text-base-content">
                  <Plus className="w-3 h-3" /> Add leg
                </button>
              )}
              {optionsTotal !== 0 && (
                <p className="text-xs text-base-content/50">
                  Options net {optionsTotal < 0 ? 'cost' : 'credit'}:
                  <span className={`font-semibold ml-1 ${optionsTotal < 0 ? 'text-error/70' : 'text-success/70'}`}>
                    {fmtMoney(optionsTotal)}
                  </span>
                </p>
              )}
            </div>
          )}

          {/* Combo total */}
          {kind === 'combo' && stockTotal !== null && optionsTotal !== 0 && (
            <div className="metric-card px-3 py-2 flex items-center justify-between">
              <span className="text-xs text-base-content/50">Total net outlay</span>
              <span className={`text-xs font-bold ${comboTotal < 0 ? 'text-error/70' : 'text-success/70'}`}>
                {fmtMoney(Math.abs(comboTotal))}
              </span>
            </div>
          )}

          {/* Trade date (shown once outside stock/options blocks) */}
          {(kind === 'long' || kind === 'short') && null /* date is inside stock block */ }
          {(kind === 'options' || kind === 'combo' || kind === 'futures') && (
            <div>
              <label className="label py-1"><span className="label-text text-xs">Execution / Trade Date</span></label>
              <input
                type="date"
                className="input input-bordered input-sm w-full"
                value={tradeDate}
                onChange={e => setTradeDate(e.target.value)}
              />
            </div>
          )}
          {(kind === 'long' || kind === 'short') && (
            <div>
              <label className="label py-1"><span className="label-text text-xs">Trade date</span></label>
              <input
                type="date"
                className="input input-bordered input-sm w-full"
                value={tradeDate}
                onChange={e => setTradeDate(e.target.value)}
              />
            </div>
          )}

          {/* Separator */}
          <div className="border-t border-white/[0.04]" />

          {/* Why / thesis */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="label-text text-xs font-medium">Why this trade? (optional)</label>
              {hasThesis && (
                <button type="button" onClick={autoFill}
                  className="flex items-center gap-1 text-[10px] text-primary/70 hover:text-primary transition-colors">
                  <Sparkles className="w-3 h-3" /> Auto-fill from research
                </button>
              )}
            </div>
            <textarea
              className="textarea textarea-bordered w-full text-xs leading-relaxed resize-none"
              rows={3}
              placeholder="Your reasoning for placing this trade…"
              value={why}
              onChange={e => setWhy(e.target.value)}
            />
          </div>

          {/* Analysis basis */}
          <div>
            <p className="text-xs font-medium text-base-content/60 mb-2">Analysis basis</p>
            <div className="flex gap-2">
              {([['technical', 'Technical'], ['fundamental', 'Fundamental'], ['both', 'Both']] as [AnalysisBasis, string][]).map(([v, label]) => (
                <Pill key={v} active={basis === v} onClick={() => setBasis(v)}>{label}</Pill>
              ))}
            </div>
          </div>

          {err && (
            <p className="text-xs text-error flex items-center gap-1">
              <AlertCircle className="w-3 h-3 flex-shrink-0" />{err}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-3.5 border-t border-white/[0.06] flex items-center justify-end gap-2 flex-shrink-0">
          <button onClick={onClose} className="btn btn-ghost btn-sm">Cancel</button>
          <button
            onClick={submit}
            disabled={!canSubmit() || saving}
            className="btn btn-primary btn-sm gap-2"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ClipboardList className="w-3.5 h-3.5" />}
            {saving ? 'Saving…' : 'Save Trade →'}
          </button>
        </div>

      </div>
    </div>
  );
}
