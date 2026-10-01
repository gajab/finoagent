import React from 'react';
import { AlertTriangle, CheckCircle2, Loader2, Scale } from 'lucide-react';
import type { BondPreview } from '../../types';
import { YieldBreakdown, num, pct, pnlClass, usd } from './bondUi';

const n2 = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }));

// Live "this is how we'll value it" panel for the Add/Edit bond form — shows the arithmetic that maps
// the statement's Quantity to a value, and reconciles against the broker's Current value when given.
export default function BondValuationPreview({ preview, loading, onUseBrokerPrice }: {
  preview: BondPreview | null;
  loading: boolean;
  onUseBrokerPrice?: (price: number) => void;
}) {
  if (!preview && !loading) return null;
  const r = preview?.row;
  const rec = preview?.reconciliation;
  const isFund = r && (r.kind === 'etf' || r.kind === 'mutual_fund');
  const isTips = r?.kind === 'tips';
  const tone = rec ? (Math.abs(rec.diff_pct) <= 0.5 ? 'ok' : Math.abs(rec.diff_pct) <= 2 ? 'warn' : 'bad') : null;
  const estimated = !!r?.estimated_mark;

  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/40 p-3">
      <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold text-base-content/70">
        <Scale className="h-3.5 w-3.5 text-primary" /> How we'll value it
        {loading && <Loader2 className="h-3 w-3 animate-spin text-base-content/40" />}
      </div>
      {preview && !preview.ready && <p className="text-[11px] text-base-content/45">Keep going — {preview.missing}</p>}
      {r && preview?.ready && (
        <div className="space-y-2">
          <div className="rounded-lg bg-base-100/60 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-base-content/80">
            {isFund ? (
              <>{n2(r.quantity, 3)} shares × ${n2(r.price)} = <b className="text-base-content">{usd(r.market_value, { cents: true })}</b></>
            ) : r.no_mark_to_market ? (
              <>principal {usd(r.face)} + accrued interest = <b className="text-base-content">{usd(r.market_value, { cents: true })}</b></>
            ) : (
              <>
                {n2(r.face, 0)} face{isTips && <> × index ratio {num(r.tips?.index_ratio, 5)}</>} × price {num(r.price, 4)} ÷ 100
                {' '}= <b className="text-base-content">{usd(r.clean_value, { cents: true })}</b>
                {!!r.accrued_usd && <div className="text-base-content/50">+ accrued interest {usd(r.accrued_usd, { cents: true })} → total {usd(r.market_value, { cents: true })}</div>}
              </>
            )}
          </div>
          <div className="text-[10.5px] text-base-content/45">Price source: {r.price_source}{isTips && ' · price is the REAL (un-indexed) price per 100 of original face'}</div>

          <div className="grid grid-cols-3 gap-2 text-[11px]">
            <div title={r.yield_basis}><div className="text-base-content/45">{isTips ? `Total yield (real ${pct(r.ytw_pct)})` : 'Total yield'}</div><div className="font-semibold tabular-nums">{pct(r.total_yield_pct ?? r.ytw_pct)}</div></div>
            <div><div className="text-base-content/45">After-tax yield</div><div className="font-semibold tabular-nums text-emerald-400">{pct(r.tax?.after_tax_yield_pct)}</div></div>
            <div><div className="text-base-content/45">Income / yr</div><div className="font-semibold tabular-nums">{usd(r.annual_income)}</div></div>
            <div><div className="text-base-content/45">Cost basis</div><div className="font-semibold tabular-nums">{usd(r.cost_basis ?? null, { cents: true })}</div></div>
            <div><div className="text-base-content/45">Unrealized P&L</div><div className={`font-semibold tabular-nums ${pnlClass(r.unrealized_pnl)}`}>{usd(r.unrealized_pnl ?? null, { sign: true, cents: true })}</div></div>
            <div><div className="text-base-content/45">Duration</div><div className="font-semibold tabular-nums">{num(r.eff_duration, 2)}y</div></div>
          </div>
          <YieldBreakdown parts={r.yield_parts} />
          {isFund && r.yield_basis && <div className="text-[10.5px] leading-snug text-base-content/45">Yield: {r.yield_basis}</div>}
          {isTips && (
            <div className="text-[10.5px] leading-snug text-base-content/50">
              Index ratio today {num(r.tips?.index_ratio, 5)}{r.tips?.index_ratio_at_purchase ? ` · at purchase ${num(r.tips.index_ratio_at_purchase, 5)}` : ' · add the purchase date to get the index ratio you paid'}
              {r.purchase_price_derived && r.purchase_price != null && <> · real purchase price {num(r.purchase_price, 3)} (= cost basis ÷ (face × IR at purchase) × 100)</>}
            </div>
          )}
          {r.warnings.filter(w => !w.startsWith('Bank CD')).map((w, i) => (
            <div key={i} className="flex gap-1 text-[10.5px] text-amber-300/80"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{w}</div>
          ))}

          {rec && (
            <div className={`rounded-lg border px-3 py-2 text-[11px] ${
              tone === 'ok' ? 'border-emerald-500/30 bg-emerald-500/10' : tone === 'warn' ? 'border-amber-500/30 bg-amber-500/10' : 'border-rose-500/30 bg-rose-500/10'}`}>
              <div className="flex items-center gap-1.5 font-semibold">
                {tone === 'ok' ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" /> : <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />}
                Your statement {usd(rec.broker_value, { cents: true })} · ours {usd(rec.our_value, { cents: true })} · {usd(rec.diff, { sign: true, cents: true })} ({pct(rec.diff_pct, 2, true)})
              </div>
              <div className="mt-1 text-base-content/60">
                {tone === 'ok'
                  ? 'Matches — the mapping is right. Small gaps are just a different pricing time.'
                  : estimated
                    ? (r.kind === 'treasury' || r.kind === 'tips'
                      ? 'Today\'s TreasuryDirect price isn\'t available yet, so our value is estimated from the yield curve. Your statement\'s number is better — use it as the price:'
                      : 'This bond has no free live quote, so our value is an estimate. Your broker\'s number is better — use it as the price:')
                    : 'Check the CUSIP, that Quantity is the FACE amount (not the value), and the statement date.'}
              </div>
              {rec.implied_price != null && tone !== 'ok' && estimated && onUseBrokerPrice && (
                <button type="button" className="btn btn-xs btn-outline mt-1.5" onClick={() => onUseBrokerPrice(rec.implied_price as number)}>
                  Use {num(rec.implied_price, 4)} per 100 (from your statement) as my price
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
