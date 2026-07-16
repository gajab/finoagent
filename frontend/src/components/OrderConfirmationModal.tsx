import React, { useState, useEffect, useMemo } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Loader2,
  ShieldCheck,
  Eye,
  Send,
  ReceiptText,
  ChevronRight,
  DollarSign,
  TrendingUp,
  Edit3,
  Info,
  ArrowRight,
  Shield,
} from 'lucide-react';
import { placeBrokerStrategyOrder, previewBrokerOrder, markStrategyAsTraded } from '../api';
import type {
  BrokerOrderLeg,
  BrokerOrderResult,
  OrderPreviewResponse,
  BoxSpreadResult,
} from '../types';
import { roundToTick } from '../utils/tickSize';

type Step = 'preview' | 'confirm' | 'receipt';

interface Props {
  ticker: string;
  strategy: string;
  legs: BrokerOrderLeg[];
  spread?: BoxSpreadResult;
  intent?: 'lend' | 'borrow';
  pricingMode?: string;
  savedStrategyId?: number;
  onClose: () => void;
  onSuccess?: () => void;
}

export function OrderConfirmationModal({
  ticker,
  strategy,
  legs: initialLegs,
  spread,
  intent,
  pricingMode,
  savedStrategyId,
  onClose,
  onSuccess,
}: Props) {
  const [step, setStep] = useState<Step>('preview');
  const [tracked, setTracked] = useState(false);

  // Editable leg prices
  const [legs, setLegs] = useState<BrokerOrderLeg[]>(initialLegs);
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');

  // Preview state
  const [previewing, setPreviewing] = useState(false);
  const [previewData, setPreviewData] = useState<OrderPreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Place order state
  const [placing, setPlacing] = useState(false);
  const [results, setResults] = useState<BrokerOrderResult[] | null>(null);
  const [overallSuccess, setOverallSuccess] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [placedAt, setPlacedAt] = useState<string | null>(null);

  const totalDebit = useMemo(() => {
    return legs.reduce((sum, leg) => {
      const mult = leg.action === 'Buy' ? -1 : 1;
      return sum + mult * leg.limit_price * leg.qty * 100;
    }, 0);
  }, [legs]);

  // Annualized return at current prices
  const annualizedReturn = useMemo(() => {
    if (!spread) return null;
    const netPerContract = legs.reduce(
      (sum, leg) =>
        sum + (leg.action === 'Buy' ? leg.limit_price : -leg.limit_price),
      0,
    );
    const dte = spread.dte;
    const boxWidth = spread.box_width;
    if (intent === 'lend' && netPerContract > 0) {
      const profit = boxWidth - netPerContract;
      return Math.round(((profit / netPerContract) * (365 / dte) * 100) * 100) / 100;
    } else if (intent === 'borrow' && netPerContract < 0) {
      const proceeds = Math.abs(netPerContract);
      const interest = boxWidth - proceeds;
      return Math.round(((interest / proceeds) * (365 / dte) * 100) * 100) / 100;
    }
    return null;
  }, [legs, spread, intent]);

  // Auto-run preview on mount
  useEffect(() => {
    runPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runPreview = async () => {
    setPreviewing(true);
    setPreviewError(null);
    setPreviewData(null);
    try {
      const data = await previewBrokerOrder({ ticker, strategy, legs });
      setPreviewData(data);
    } catch (err: any) {
      setPreviewError(err.message || 'Preview failed');
    } finally {
      setPreviewing(false);
    }
  };

  const handleEditPrice = (idx: number) => {
    setEditingIdx(idx);
    setEditValue(legs[idx].limit_price.toFixed(2));
  };

  const handleSavePrice = (idx: number) => {
    const newPrice = parseFloat(editValue);
    if (!isNaN(newPrice) && newPrice >= 0.01) {
      const updated = [...legs];
      updated[idx] = { ...updated[idx], limit_price: roundToTick(newPrice) };
      setLegs(updated);
    }
    setEditingIdx(null);
    setEditValue('');
  };

  const handleCancelEdit = () => {
    setEditingIdx(null);
    setEditValue('');
  };

  const handlePlaceOrder = async () => {
    setPlacing(true);
    setPlaceError(null);
    try {
      const result = await placeBrokerStrategyOrder(ticker, strategy, legs);
      setResults(result.legs);
      setOverallSuccess(result.overall_success);
      setPlacedAt(new Date().toLocaleString());
      setStep('receipt');
      if (result.overall_success && onSuccess) {
        onSuccess();
      }
    } catch (err: any) {
      setPlaceError(err.message || 'Order placement failed');
    } finally {
      setPlacing(false);
    }
  };

  // --- Step Indicator ---
  const steps: { key: Step; label: string; icon: React.ReactNode }[] = [
    { key: 'preview', label: 'Preview', icon: <Eye className="w-3.5 h-3.5" /> },
    { key: 'confirm', label: 'Confirm', icon: <Shield className="w-3.5 h-3.5" /> },
    { key: 'receipt', label: 'Receipt', icon: <ReceiptText className="w-3.5 h-3.5" /> },
  ];
  const currentStepIdx = steps.findIndex(s => s.key === step);

  const stepIndicator = (
    <div className="flex items-center justify-center gap-0 mb-5">
      {steps.map((s, i) => (
        <React.Fragment key={s.key}>
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-all ${
            i === currentStepIdx
              ? 'bg-primary text-primary-content shadow-sm'
              : i < currentStepIdx
                ? 'text-success'
                : 'text-base-content/30'
          }`}>
            {i < currentStepIdx ? <CheckCircle2 className="w-3.5 h-3.5" /> : s.icon}
            {s.label}
          </div>
          {i < steps.length - 1 && (
            <div className={`w-8 h-px mx-1 ${i < currentStepIdx ? 'bg-success' : 'bg-base-content/10'}`} />
          )}
        </React.Fragment>
      ))}
    </div>
  );

  // --- Legs Table ---
  const legsTable = (editable: boolean) => (
    <div className="rounded-lg border border-base-content/5 overflow-hidden">
      <table className="table table-sm w-full">
        <thead>
          <tr className="bg-base-200/40 text-[10px] uppercase tracking-wider">
            <th className="py-2">Action</th>
            <th className="py-2">Type</th>
            <th className="py-2">Strike</th>
            <th className="py-2">Qty</th>
            <th className="py-2">Limit Price</th>
            <th className="py-2 text-right">Cost</th>
            {previewData && step === 'preview' && <th className="py-2 text-right">Commission</th>}
            {results && step === 'receipt' && <th className="py-2 text-center">Status</th>}
          </tr>
        </thead>
        <tbody>
          {legs.map((leg, idx) => (
            <tr key={idx} className="border-t border-base-content/5">
              <td className="py-1.5">
                <span className={`inline-flex items-center text-[10px] font-bold px-1.5 py-0.5 rounded ${
                  leg.action === 'Buy' ? 'bg-success/10 text-success' : 'bg-error/10 text-error'
                }`}>
                  {leg.action.toUpperCase()}
                </span>
              </td>
              <td className="text-xs">{leg.type}</td>
              <td className="font-mono text-xs">${leg.strike.toFixed(2)}</td>
              <td className="font-mono text-xs">{leg.qty}</td>
              <td className="font-mono text-xs">
                {editable && editingIdx === idx ? (
                  <div className="flex items-center gap-1">
                    <span className="text-base-content/40">$</span>
                    <input
                      type="number"
                      step="0.01"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') handleSavePrice(idx);
                        if (e.key === 'Escape') handleCancelEdit();
                      }}
                      className="input input-xs input-bordered w-20 font-mono"
                      autoFocus
                    />
                    <button className="btn btn-xs btn-ghost text-success p-0.5" onClick={() => handleSavePrice(idx)}>✓</button>
                    <button className="btn btn-xs btn-ghost text-error p-0.5" onClick={handleCancelEdit}>✕</button>
                  </div>
                ) : (
                  <span className="inline-flex items-center gap-1 group">
                    ${leg.limit_price.toFixed(2)}
                    {editable && (
                      <button
                        className="opacity-0 group-hover:opacity-60 hover:!opacity-100 transition-opacity"
                        onClick={() => handleEditPrice(idx)}
                        title="Edit limit price"
                      >
                        <Edit3 className="w-3 h-3" />
                      </button>
                    )}
                  </span>
                )}
              </td>
              <td className="font-mono text-xs text-right font-semibold">
                <span className={leg.action === 'Buy' ? 'text-error' : 'text-success'}>
                  {leg.action === 'Buy' ? '-' : '+'}${(leg.limit_price * leg.qty * 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </span>
              </td>
              {previewData && step === 'preview' && (
                <td className="font-mono text-xs text-right">
                  {previewData.legs?.[idx]?.estimated_commission != null
                    ? `$${previewData.legs[idx].estimated_commission!.toFixed(2)}`
                    : <span className="opacity-30">—</span>}
                </td>
              )}
              {results && step === 'receipt' && results[idx] && (
                <td className="text-center">
                  {results[idx].success ? (
                    <span className="inline-flex items-center gap-1 text-success text-xs">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      {results[idx].order_id ? `#${results[idx].order_id}` : 'OK'}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-error text-xs max-w-[200px] truncate" title={results[idx].error || 'Failed'}>
                      <XCircle className="w-3.5 h-3.5 shrink-0" />
                      {results[idx].error || 'Failed'}
                    </span>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>

      {/* Net Total Footer */}
      <div className={`flex justify-between items-center px-4 py-2.5 border-t border-base-content/10 bg-base-200/20 ${
        results && step === 'receipt' ? '' : ''
      }`}>
        <span className="text-sm font-semibold">Net {totalDebit < 0 ? 'Debit' : 'Credit'}</span>
        <span className={`font-mono text-base font-bold ${totalDebit < 0 ? 'text-error' : 'text-success'}`}>
          {totalDebit < 0 ? '-' : '+'}${Math.abs(totalDebit).toLocaleString(undefined, { maximumFractionDigits: 0 })}
        </span>
      </div>
    </div>
  );

  return (
    <div className="modal modal-open">
      <div className={`modal-box ${initialLegs.length > 4 ? 'max-w-4xl' : 'max-w-2xl'} rounded-2xl`}>
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <h3 className="font-bold text-lg flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-primary" />
            {step === 'preview' && 'Preview Order'}
            {step === 'confirm' && 'Confirm Order'}
            {step === 'receipt' && 'Order Receipt'}
          </h3>
          <div className="flex items-center gap-2">
            <span className="badge badge-outline badge-sm font-mono">{ticker}</span>
            <span className="text-[10px] text-base-content/40">{strategy.replace(/_/g, ' ')}</span>
          </div>
        </div>

        {stepIndicator}

        {/* ====== STEP 1: PREVIEW ====== */}
        {step === 'preview' && (
          <>
            {/* Annualized return info */}
            {annualizedReturn !== null && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-base-200/40 rounded-lg">
                <TrendingUp className="w-4 h-4 text-info" />
                <span className="text-sm">
                  At current prices:{' '}
                  <span className={`font-bold ${annualizedReturn > 0 ? 'text-success' : 'text-error'}`}>
                    {annualizedReturn.toFixed(2)}% annualized
                  </span>
                </span>
                {pricingMode && (
                  <span className="badge badge-xs badge-ghost ml-auto">{pricingMode} pricing</span>
                )}
              </div>
            )}

            {legsTable(true)}

            {/* Preview states */}
            {previewing && (
              <div className="flex items-center gap-2 mt-3 text-sm text-primary animate-pulse">
                <Loader2 className="w-4 h-4 animate-spin shrink-0" />
                Fetching IBKR preview (commission, margin)...
              </div>
            )}

            {previewError && (
              <div className="mt-3 rounded-lg bg-warning/10 border border-warning/20 px-3 py-2 text-sm">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
                  <div>
                    <span className="font-semibold">Preview unavailable:</span> {previewError}
                    <div className="text-xs text-base-content/50 mt-0.5">
                      You can still proceed — orders will be placed directly.
                    </div>
                  </div>
                </div>
              </div>
            )}

            {previewData && previewData.warnings && previewData.warnings.length > 0 && (
              <div className="mt-3 rounded-lg bg-warning/10 border border-warning/20 px-3 py-2">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
                  <div className="text-sm space-y-0.5">
                    {previewData.warnings.map((w, i) => <div key={i}>{w}</div>)}
                  </div>
                </div>
              </div>
            )}

            {/* Commission & Margin summary */}
            {previewData && (
              <div className="flex items-center gap-4 mt-3 text-xs text-base-content/50">
                {previewData.total_estimated_commission != null && (
                  <span className="flex items-center gap-1">
                    <DollarSign className="w-3 h-3" />
                    Commission: ${previewData.total_estimated_commission.toFixed(2)}
                  </span>
                )}
                {previewData.margin_impact && (
                  <span className="flex items-center gap-1">
                    <DollarSign className="w-3 h-3" />
                    Margin: {previewData.margin_impact}
                  </span>
                )}
                {previewData.account_id && (
                  <span className="ml-auto font-mono">Account: {previewData.account_id}</span>
                )}
              </div>
            )}

            {/* Tip */}
            <div className="flex items-center gap-1.5 mt-3 text-[10px] text-base-content/30">
              <Info className="w-3 h-3 shrink-0" />
              Hover over any limit price to edit before placing.
            </div>

            <div className="modal-action">
              <button className="btn btn-ghost btn-sm rounded-xl" onClick={onClose} disabled={previewing}>
                Cancel
              </button>
              <button className="btn btn-ghost btn-sm rounded-xl" onClick={runPreview} disabled={previewing}>
                {previewing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Eye className="w-3.5 h-3.5" />}
                Re-preview
              </button>
              <button
                className="btn btn-primary btn-sm rounded-xl gap-1.5"
                onClick={() => setStep('confirm')}
                disabled={previewing}
              >
                Proceed
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </>
        )}

        {/* ====== STEP 2: CONFIRM ====== */}
        {step === 'confirm' && (
          <>
            {/* Warning Banner */}
            <div className="rounded-lg bg-warning/10 border border-warning/20 px-4 py-3 mb-4">
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="w-5 h-5 text-warning shrink-0 mt-0.5" />
                <div>
                  <div className="font-semibold text-sm">Final Review</div>
                  <div className="text-xs text-base-content/60 mt-0.5">
                    You are about to submit {legs.length} limit orders to IBKR as a multi-leg strategy.
                    This action cannot be undone from this interface.
                  </div>
                </div>
              </div>
            </div>

            {/* Annualized return */}
            {annualizedReturn !== null && (
              <div className="flex items-center gap-2 mb-3 px-3 py-2 bg-base-200/40 rounded-lg">
                <TrendingUp className="w-4 h-4 text-info" />
                <span className="text-sm">
                  Annualized return:{' '}
                  <span className={`font-bold ${annualizedReturn > 0 ? 'text-success' : 'text-error'}`}>
                    {annualizedReturn.toFixed(2)}%
                  </span>
                </span>
              </div>
            )}

            {legsTable(false)}

            {/* Order Meta */}
            <div className="flex items-center gap-3 mt-3 text-xs text-base-content/40 font-mono">
              <span>Exp: {legs[0]?.expiration}</span>
              <span>•</span>
              <span>{strategy.replace(/_/g, ' ')}</span>
              {previewData?.account_id && (
                <>
                  <span>•</span>
                  <span>Account: {previewData.account_id}</span>
                </>
              )}
            </div>

            {/* Place order error */}
            {placeError && (
              <div className="mt-3 rounded-lg bg-error/10 border border-error/20 px-3 py-2">
                <div className="flex items-center gap-2 text-sm text-error">
                  <XCircle className="w-4 h-4 shrink-0" />
                  {placeError}
                </div>
              </div>
            )}

            {placing && (
              <div className="flex items-center justify-center gap-2 mt-4 text-sm text-primary">
                <Loader2 className="w-5 h-5 animate-spin" />
                Submitting multi-leg order to IBKR...
              </div>
            )}

            <div className="modal-action">
              <button className="btn btn-ghost btn-sm rounded-xl" onClick={() => setStep('preview')} disabled={placing}>
                ← Back
              </button>
              <button className="btn btn-ghost btn-sm rounded-xl" onClick={onClose} disabled={placing}>
                Cancel
              </button>
              <button
                className="btn btn-error btn-sm rounded-xl gap-1.5"
                onClick={handlePlaceOrder}
                disabled={placing}
              >
                {placing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
                Place {legs.length} Orders
              </button>
            </div>
          </>
        )}

        {/* ====== STEP 3: RECEIPT ====== */}
        {step === 'receipt' && (
          <>
            {/* Overall Status */}
            <div className={`rounded-lg px-4 py-3 mb-4 ${
              overallSuccess
                ? 'bg-success/10 border border-success/20'
                : 'bg-warning/10 border border-warning/20'
            }`}>
              <div className="flex items-center gap-2.5">
                {overallSuccess ? (
                  <CheckCircle2 className="w-5 h-5 text-success" />
                ) : (
                  <AlertTriangle className="w-5 h-5 text-warning" />
                )}
                <div>
                  <div className={`font-semibold text-sm ${overallSuccess ? 'text-success' : 'text-warning'}`}>
                    {overallSuccess
                      ? `All ${legs.length} legs placed successfully`
                      : `${results?.filter(r => r.success).length ?? 0} of ${legs.length} legs placed`}
                  </div>
                  {placedAt && <div className="text-[10px] text-base-content/50 mt-0.5">{placedAt}</div>}
                </div>
              </div>
            </div>

            {legsTable(false)}

            {/* Annualized return */}
            {annualizedReturn !== null && (
              <div className="flex items-center gap-2 mt-3 px-3 py-2 bg-base-200/40 rounded-lg">
                <TrendingUp className="w-4 h-4 text-info" />
                <span className="text-sm">
                  Expected annualized return:{' '}
                  <span className="font-bold text-success">{annualizedReturn.toFixed(2)}%</span>
                </span>
              </div>
            )}

            {/* Summary Card */}
            <div className="bg-base-200/30 rounded-lg p-3 mt-3 space-y-1.5 text-sm">
              <div className="flex justify-between">
                <span className="text-base-content/50">Strategy</span>
                <span className="font-medium">{strategy.replace(/_/g, ' ')}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-base-content/50">Ticker</span>
                <span className="font-mono">{ticker}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-base-content/50">Expiration</span>
                <span className="font-mono">{legs[0]?.expiration}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-base-content/50">Net {totalDebit < 0 ? 'Debit' : 'Credit'}</span>
                <span className={`font-mono font-semibold ${totalDebit < 0 ? 'text-error' : 'text-success'}`}>
                  {totalDebit < 0 ? '-' : '+'}${Math.abs(totalDebit).toFixed(2)}
                </span>
              </div>
              {previewData?.total_estimated_commission != null && (
                <div className="flex justify-between">
                  <span className="text-base-content/50">Est. Commission</span>
                  <span className="font-mono">${previewData.total_estimated_commission.toFixed(2)}</span>
                </div>
              )}
              {results && (
                <div className="flex justify-between">
                  <span className="text-base-content/50">Order IDs</span>
                  <span className="font-mono text-xs">
                    {results.filter(r => r.order_id).map(r => `#${r.order_id}`).join(', ') || '—'}
                  </span>
                </div>
              )}
            </div>

            <div className="modal-action">
              {savedStrategyId && overallSuccess && !tracked && (
                <button
                  className="btn btn-outline btn-sm rounded-xl gap-1"
                  onClick={async () => {
                    try {
                      const entryPrices = legs.map(l => ({
                        strike: l.strike, type: l.type, price: l.limit_price,
                      }));
                      await markStrategyAsTraded(savedStrategyId, {
                        entry_prices: entryPrices,
                        entry_net_debit: totalDebit,
                        order_source: 'ibkr',
                      });
                      setTracked(true);
                    } catch {}
                  }}
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Track This Trade
                </button>
              )}
              {tracked && (
                <span className="text-xs text-success flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3" /> Tracking in My Trades
                </span>
              )}
              <button className="btn btn-primary btn-sm rounded-xl" onClick={onClose}>
                Done
              </button>
            </div>
          </>
        )}
      </div>
      <div
        className="modal-backdrop bg-black/60"
        onClick={!placing && !previewing ? onClose : undefined}
      />
    </div>
  );
}
