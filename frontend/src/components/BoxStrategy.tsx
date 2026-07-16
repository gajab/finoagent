import React, { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Box, Loader2, AlertTriangle, DollarSign, Calendar, Percent,
  ArrowUpRight, ArrowDownRight, Shield, ChevronDown, ChevronUp,
  TrendingUp, Info, CheckCircle2, XCircle, AlertCircle,
  Send, Wifi, WifiOff, ExternalLink, Zap, Database, Sliders,
  Sparkles, Edit3, Save, FolderOpen, Trash2, Briefcase,
  Search, Clock, Target, Gauge,
} from 'lucide-react';
import { Line } from 'react-chartjs-2';
import {
  computeBoxSpread, scanBoxOpportunities, fetchBrokerConnection, getSmartPrice,
  fetchSavedStrategies, saveStrategy, updateSavedStrategy, deleteSavedStrategy,
  fetchOptionQuotesBatch, markStrategyAsTraded, fetchOptionExpirations
} from '../api';
import type { SavedStrategyItem } from '../api';
import { roundToTick } from '../utils/tickSize';
import type {
  BoxSpreadResponse, BoxSpreadResult, BoxSpreadLeg, SmartPriceResponse,
  BoxScanResponse, BoxOpportunity,
} from '../types';
import { OrderConfirmationModal } from './OrderConfirmationModal';

type PricingMode = 'low' | 'mid' | 'high' | 'smart' | 'custom';
type BoxMode = 'scan' | 'single';

export function BoxStrategy() {
  const [mode, setMode] = useState<BoxMode>('scan');
  const [ticker, setTicker] = useState('.XSP');
  const [amount, setAmount] = useState(10000);
  const [duration, setDuration] = useState(30);
  const [targetReturn, setTargetReturn] = useState(5);
  const [intent, setIntent] = useState<'lend' | 'borrow'>('lend');
  const [quoteSource, setQuoteSource] = useState<'yfinance' | 'ibkr'>('yfinance');
  const [maxContracts, setMaxContracts] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BoxSpreadResponse | null>(null);
  const [selectedSpread, setSelectedSpread] = useState<number>(0);
  const [showRisks, setShowRisks] = useState(false);

  // Opportunity scanner
  const [scanTickers, setScanTickers] = useState('');
  const [scanResult, setScanResult] = useState<BoxScanResponse | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  // Pricing controls
  const [pricingMode, setPricingMode] = useState<PricingMode>('mid');
  const [customPrice, setCustomPrice] = useState<string>('');
  const [smartPrice, setSmartPrice] = useState<SmartPriceResponse | null>(null);
  const [loadingSmartPrice, setLoadingSmartPrice] = useState(false);

  // IB broker state
  const [brokerConnected, setBrokerConnected] = useState<boolean | null>(null);
  const [showOrderModal, setShowOrderModal] = useState(false);

  // Save / Load
  const [savedStrategies, setSavedStrategies] = useState<SavedStrategyItem[]>([]);
  const [loadedStrategyId, setLoadedStrategyId] = useState<number | null>(null);
  const [saveName, setSaveName] = useState('');
  const [showSaveInput, setShowSaveInput] = useState(false);
  const [savingStrategy, setSavingStrategy] = useState(false);
  const [showSavedList, setShowSavedList] = useState(false);
  const [refreshingQuotes, setRefreshingQuotes] = useState(false);
  const [selectedExpiration, setSelectedExpiration] = useState<string | null>(null);
  const [availableExpirations, setAvailableExpirations] = useState<string[]>([]);
  const [loadingExpirations, setLoadingExpirations] = useState(false);
  const [markingAsPlaced, setMarkingAsPlaced] = useState(false);
  const [markedAsPlaced, setMarkedAsPlaced] = useState(false);

  useEffect(() => {
    fetchSavedStrategies('box_spread').then(setSavedStrategies).catch(() => {});
  }, []);

  useEffect(() => {
    fetchBrokerConnection()
      .then((conn: any) => setBrokerConnected(!!(conn?.configured !== false && conn?.has_credentials)))
      .catch(() => setBrokerConnected(false));
  }, []);

  // Fetch available expirations when ticker changes
  useEffect(() => {
    const t = ticker.trim();
    if (!t) { setAvailableExpirations([]); return; }
    const timer = setTimeout(async () => {
      setLoadingExpirations(true);
      try {
        const data = await fetchOptionExpirations(t);
        setAvailableExpirations(data.expirations || []);
        setSelectedExpiration(null);
      } catch {
        setAvailableExpirations([]);
      } finally {
        setLoadingExpirations(false);
      }
    }, 500); // debounce
    return () => clearTimeout(timer);
  }, [ticker]);

  const handleSaveBoxStrategy = async (asNew = true) => {
    if (!result || !spread) return;
    const name = saveName.trim();
    if (!name && asNew) return;

    setSavingStrategy(true);
    try {
      const params = { ticker, amount, duration, targetReturn, intent, quoteSource, pricingMode, selectedSpread };
      const legsForSave = spread.legs.map((leg: any) => ({
        action: leg.action, type: leg.type, strike: leg.strike,
        bid: leg.bid, ask: leg.ask, mid: leg.mid,
        price: adjustedLegs?.find((a: any) => a.strike === leg.strike && a.type === leg.type)?.adjustedPrice ?? leg.mid,
        iv: leg.iv, oi: leg.oi, vol: leg.vol,
      }));
      const snapshot = {
        ticker: result.ticker, current_price: result.current_price,
        intent: result.intent, target_amount: result.target_amount,
        selectedSpread, spread: { ...spread },
      };

      if (!asNew && loadedStrategyId) {
        await updateSavedStrategy(loadedStrategyId, {
          name: name || undefined, parameters: params,
          legs_data: legsForSave, result_snapshot: snapshot,
        });
      } else {
        const created = await saveStrategy({
          strategy_type: 'box_spread', name, ticker,
          parameters: params, legs_data: legsForSave, result_snapshot: snapshot,
        });
        setLoadedStrategyId(created.id);
      }

      const list = await fetchSavedStrategies('box_spread');
      setSavedStrategies(list);
      setShowSaveInput(false);
      setSaveName('');
    } catch (err: any) {
      setError(err.message || 'Failed to save strategy');
    } finally {
      setSavingStrategy(false);
    }
  };

  const handleLoadBoxStrategy = (saved: SavedStrategyItem) => {
    const p = saved.parameters;
    setTicker(p.ticker || saved.ticker);
    setAmount(p.amount ?? 10000);
    setDuration(p.duration ?? 30);
    setTargetReturn(p.targetReturn ?? 5);
    setIntent(p.intent ?? 'lend');
    setQuoteSource(p.quoteSource ?? 'yfinance');
    setPricingMode(p.pricingMode ?? 'mid');

    const snap = saved.result_snapshot;
    // Reconstruct result with saved spread containing saved leg prices
    const savedSpread = snap.spread || {};
    // Overlay saved leg prices into the spread's legs
    if (saved.legs_data?.length && savedSpread.legs) {
      savedSpread.legs = savedSpread.legs.map((leg: any, i: number) => {
        const savedLeg = saved.legs_data[i];
        return savedLeg ? { ...leg, bid: savedLeg.bid, ask: savedLeg.ask, mid: savedLeg.mid } : leg;
      });
    }

    setResult({
      ticker: snap.ticker || saved.ticker,
      current_price: snap.current_price ?? 0,
      intent: snap.intent || p.intent || 'lend',
      target_amount: snap.target_amount ?? p.amount ?? 10000,
      target_duration_days: p.duration ?? 30,
      target_annual_return: p.targetReturn ?? 5,
      quote_source: p.quoteSource,
      spreads: [savedSpread],
      risks: [],
      available_expirations: [],
    } as BoxSpreadResponse);
    setSelectedSpread(0);
    setLoadedStrategyId(saved.id);
    setShowSavedList(false);
  };

  const handleDeleteBoxStrategy = async (id: number) => {
    try {
      await deleteSavedStrategy(id);
      setSavedStrategies(prev => prev.filter(s => s.id !== id));
      if (loadedStrategyId === id) setLoadedStrategyId(null);
    } catch (err: any) {
      setError(err.message || 'Failed to delete');
    }
  };

  const spread = result?.spreads?.[selectedSpread];

  // Calculate adjusted leg prices based on pricing mode
  const adjustedLegs = useMemo(() => {
    if (!spread) return null;

    const mapped = spread.legs.map((leg: BoxSpreadLeg, i: number) => {
      let price: number;
      switch (pricingMode) {
        case 'low':
          // Conservative: buy at ask, sell at bid (natural/worst case — most likely to fill)
          price = leg.action === 'BUY' ? leg.ask : leg.bid;
          break;
        case 'high':
          // Aggressive: buy at bid, sell at ask (best price, may not fill)
          price = leg.action === 'BUY' ? leg.bid : leg.ask;
          break;
        case 'smart':
          if (smartPrice?.recommended_legs?.[i]) {
            price = smartPrice.recommended_legs[i].price;
          } else {
            price = leg.mid;
          }
          break;
        case 'custom':
          // For custom mode, scale leg prices so net per contract = user's target
          if (customPrice && !isNaN(Number(customPrice))) {
            const targetNet = Number(customPrice);
            const midNet = spread.legs.reduce((sum: number, l: BoxSpreadLeg) =>
              sum + (l.action === 'BUY' ? l.mid : -l.mid), 0);
            if (midNet !== 0) {
              const ratio = targetNet / midNet;
              price = leg.mid * ratio;
            } else {
              price = leg.mid;
            }
          } else {
            price = leg.mid;
          }
          break;
        default: // 'mid'
          price = leg.mid;
      }
      // Round to valid IBKR tick (0.05 if < $3, else 0.10)
      const rounded = roundToTick(price);
      return { ...leg, adjustedPrice: rounded };
    });

    // For custom pricing, correct rounding drift on the last leg so net matches target
    if (pricingMode === 'custom' && customPrice && !isNaN(Number(customPrice)) && mapped.length > 0) {
      const targetNet = Number(customPrice);
      const currentNet = mapped.reduce((sum: number, l) =>
        sum + (l.action === 'BUY' ? l.adjustedPrice : -l.adjustedPrice), 0);
      const drift = targetNet - currentNet;
      if (Math.abs(drift) > 0.001 && Math.abs(drift) < 0.50) {
        const lastIdx = mapped.length - 1;
        const last = mapped[lastIdx];
        const correction = last.action === 'BUY' ? drift : -drift;
        const corrected = last.adjustedPrice + correction;
        mapped[lastIdx] = {
          ...last,
          adjustedPrice: roundToTick(corrected),
        };
      }
    }

    return mapped;
  }, [spread, pricingMode, customPrice, smartPrice]);

  // Calculate net cost and annualized return at adjusted prices
  const pricingMetrics = useMemo(() => {
    if (!adjustedLegs || !spread) return null;

    const netPerContract = adjustedLegs.reduce((sum, leg) =>
      sum + (leg.action === 'BUY' ? leg.adjustedPrice : -leg.adjustedPrice), 0);

    const dte = spread.dte;
    const boxWidth = spread.box_width;
    let annualizedReturn: number | null = null;
    let totalCost: number | null = null;
    let totalProfit: number | null = null;

    if (intent === 'lend' && netPerContract > 0) {
      const profit = boxWidth - netPerContract;
      annualizedReturn = (profit / netPerContract) * (365 / dte) * 100;
      totalCost = netPerContract * 100 * spread.num_contracts;
      totalProfit = profit * 100 * spread.num_contracts;
    } else if (intent === 'borrow' && netPerContract < 0) {
      const proceeds = Math.abs(netPerContract);
      const interest = boxWidth - proceeds;
      annualizedReturn = (interest / proceeds) * (365 / dte) * 100;
      totalCost = proceeds * 100 * spread.num_contracts;
      totalProfit = interest * 100 * spread.num_contracts;
    }

    return {
      netPerContract: Math.round(netPerContract * 10000) / 10000,
      annualizedReturn: annualizedReturn !== null ? Math.round(annualizedReturn * 100) / 100 : null,
      totalCost: totalCost !== null ? Math.round(totalCost * 100) / 100 : null,
      totalProfit: totalProfit !== null ? Math.round(totalProfit * 100) / 100 : null,
    };
  }, [adjustedLegs, spread, intent]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    setSelectedSpread(0);
    setSmartPrice(null);
    setPricingMode('mid');
    try {
      const data = await computeBoxSpread({
        ticker: ticker.toUpperCase(),
        amount,
        duration_days: duration,
        target_annual_return: targetReturn,
        intent,
        quote_source: quoteSource,
        max_contracts: maxContracts,
        ...(selectedExpiration ? { target_expiration: selectedExpiration } : {}),
      });
      if (data.error && (!data.spreads || data.spreads.length === 0)) {
        setError(data.error);
      } else {
        setResult(data);
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to compute box spread');
    } finally {
      setLoading(false);
    }
  };

  const handleScan = async (e: React.FormEvent) => {
    e.preventDefault();
    setScanLoading(true);
    setScanError(null);
    setScanResult(null);
    try {
      const tickers = scanTickers
        .split(',')
        .map(t => t.trim().toUpperCase())
        .filter(Boolean);
      const data = await scanBoxOpportunities({
        intent,
        duration_days: duration,
        target_annual_return: targetReturn,
        amount,
        max_contracts: maxContracts,
        per_ticker: 2,
        quote_source: quoteSource,
        ...(tickers.length ? { tickers } : {}),
      });
      setScanResult(data);
    } catch (err: any) {
      setScanError(err?.message || 'Scan failed');
    } finally {
      setScanLoading(false);
    }
  };

  // Load a scanned opportunity into the single-ticker detail/order flow,
  // pre-priced at the smart price (the realistic, likely-to-fill price the
  // headline return was evaluated at).
  const handleLoadOpportunity = (opp: BoxOpportunity) => {
    if (!scanResult) return;
    setTicker(opp.ticker);
    setIntent(scanResult.intent);
    setResult({
      ticker: opp.ticker,
      current_price: opp.current_price,
      intent: scanResult.intent,
      target_amount: scanResult.params.amount,
      target_duration_days: scanResult.params.duration_days,
      target_annual_return: scanResult.params.target_annual_return,
      quote_source: scanResult.params.quote_source,
      spreads: [opp],
      risks: [],
      available_expirations: [],
    } as BoxSpreadResponse);
    setSelectedSpread(0);
    setSmartPrice(opp.smart_price ?? null);
    // Pre-price at the no-arb fair value — the price that actually fills.
    if (opp.recommended_limit_net != null) {
      setCustomPrice(String(opp.recommended_limit_net));
      setPricingMode('custom');
    } else {
      setPricingMode(opp.smart_price ? 'smart' : 'mid');
    }
    setLoadedStrategyId(null);
    setMarkedAsPlaced(false);
    setError(null);
    setMode('single');
  };

  const handleSmartPrice = useCallback(async () => {
    if (!spread) return;
    setLoadingSmartPrice(true);
    try {
      const res = await getSmartPrice({
        legs: spread.legs.map((l: BoxSpreadLeg) => ({
          action: l.action,
          type: l.type,
          strike: l.strike,
          bid: l.bid,
          ask: l.ask,
          last: l.mid,
          oi: l.oi,
          vol: l.vol,
        })),
        dte: spread.dte,
        box_width: spread.box_width,
        intent,
      });
      setSmartPrice(res);
      setPricingMode('smart');
    } catch (err: any) {
      setError(err?.message || 'Smart price failed');
    } finally {
      setLoadingSmartPrice(false);
    }
  }, [spread, intent]);

  const handleRefreshQuotes = async () => {
    if (!spread || !result) return;
    setRefreshingQuotes(true);
    try {
      const requests = spread.legs.map((leg: any) => ({
        ticker: result.ticker,
        expiration: spread.expiration,
        strike: leg.strike,
        right: leg.type === 'CALL' ? 'C' : 'P',
        quote_source: quoteSource,
      }));
      const results = await fetchOptionQuotesBatch(requests);

      let newLegs = [...spread.legs];
      let changed = false;
      results.forEach((q, idx) => {
        if (q && !q.error) {
          newLegs[idx] = {
            ...newLegs[idx],
            bid: Math.round(q.bid * 100) / 100,
            ask: Math.round(q.ask * 100) / 100,
            mid: Math.round(q.mid * 100) / 100,
            last: Math.round(q.mid * 100) / 100,
            iv: q.iv,
            oi: q.oi,
            vol: q.volume
          };
          changed = true;
        }
      });

      if (changed) {
        setResult(prev => {
          if (!prev) return prev;
          const newSpreads = [...prev.spreads];
          newSpreads[selectedSpread] = {
            ...newSpreads[selectedSpread],
            legs: newLegs
          };
          return { ...prev, spreads: newSpreads };
        });
        setSmartPrice(null);
        setPricingMode('mid');
      }
    } catch (err) {
      console.warn('Failed to refresh quotes', err);
    } finally {
      setRefreshingQuotes(false);
    }
  };

  const severityColor = (s: string) => {
    if (s === 'High') return 'badge-error';
    if (s === 'Medium') return 'badge-warning';
    if (s === 'Low') return 'badge-success';
    return 'badge-info';
  };

  const returnColor = (val: number | null | undefined, target: number) => {
    if (val == null) return '';
    if (intent === 'lend') {
      return val >= target ? 'text-success' : val >= target * 0.8 ? 'text-warning' : 'text-error';
    } else {
      return val <= target ? 'text-success' : val <= target * 1.2 ? 'text-warning' : 'text-error';
    }
  };

  const orderLegs = useMemo(() => {
    if (!adjustedLegs || !spread || !result) return [];
    return adjustedLegs.map((leg) => ({
      ticker: result.ticker,
      action: (leg.action === 'BUY' ? 'Buy' : 'Sell') as 'Buy' | 'Sell',
      type: (leg.type === 'CALL' ? 'Call' : 'Put') as 'Call' | 'Put',
      strike: leg.strike,
      qty: spread.num_contracts,
      expiration: spread.expiration,
      limit_price: leg.adjustedPrice,
    }));
  }, [adjustedLegs, spread, result]);

  return (
    <div className="space-y-4">
      {/* Intro */}
      <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
        <div className="flex items-start gap-3">
          <Info className="w-5 h-5 text-info mt-0.5 flex-shrink-0" />
          <div className="text-sm text-base-content/70">
            <p className="font-semibold text-base-content mb-1">What is a Box Spread?</p>
            <p>
              A box spread combines a bull call spread and a bear put spread at the same strikes and
              expiration. The payoff at expiration is always the difference between strikes (K2 - K1),
              regardless of the underlying price. This makes it a synthetic loan:
            </p>
            <ul className="list-disc ml-4 mt-1 space-y-0.5">
              <li><strong>Lending:</strong> Buy the box for less than (K2-K1) and collect the full value at expiration.</li>
              <li><strong>Borrowing:</strong> Sell the box to receive cash now, repay (K2-K1) at expiration.</li>
            </ul>
            <p className="mt-1 text-warning/80 text-xs">
              <strong>Find Opportunities</strong> scans a universe of the most liquid, active option markets
              (SPX, NDX, RUT, XSP, SPY, QQQ, IWM, DIA, SMH…) and surfaces the boxes most likely to <em>fill</em> —
              ranked at the executable price, not the optimistic mid.
            </p>
          </div>
        </div>
      </div>

      {/* Mode toggle */}
      <div className="flex gap-1 p-1 bg-base-200/40 rounded-xl border border-white/[0.03] w-fit">
        <button type="button"
          className={`btn btn-sm gap-1.5 ${mode === 'scan' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => setMode('scan')}>
          <Search className="w-3.5 h-3.5" /> Find Opportunities
        </button>
        <button type="button"
          className={`btn btn-sm gap-1.5 ${mode === 'single' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => setMode('single')}>
          <Box className="w-3.5 h-3.5" /> Single Ticker
        </button>
      </div>

      {/* Input Form */}
      <form onSubmit={mode === 'scan' ? handleScan : handleSubmit} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {mode === 'single' ? (
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Ticker Symbol</span></label>
            <input type="text" className="input input-bordered input-sm w-full" value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="XSP, SPY, SPX" required />
          </div>
        ) : (
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Tickers (optional override)</span></label>
            <input type="text" className="input input-bordered input-sm w-full" value={scanTickers}
              onChange={(e) => setScanTickers(e.target.value.toUpperCase())} placeholder="blank = scan default universe" />
          </div>
        )}

        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Investment Amount ($)</span></label>
          <div className="relative">
            <DollarSign className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
            <input type="number" className="input input-bordered input-sm w-full pl-7" value={amount}
              onChange={(e) => setAmount(Number(e.target.value))} min={100} step={100} required />
          </div>
        </div>

        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Duration (days)</span></label>
          <div className="relative">
            <Calendar className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
            <input type="number" className="input input-bordered input-sm w-full pl-7" value={duration}
              onChange={(e) => setDuration(Number(e.target.value))} min={7} max={730} required />
          </div>
        </div>

        <div className="form-control">
          <label className="label py-1">
            <span className="label-text text-xs font-medium">
              {intent === 'lend' ? 'Min. Annual Return (%)' : 'Max. Annual Rate (%)'}
            </span>
          </label>
          <div className="relative">
            <Percent className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-base-content/40" />
            <input type="number" className="input input-bordered input-sm w-full pl-7" value={targetReturn}
              onChange={(e) => setTargetReturn(Number(e.target.value))} min={0.1} max={50} step={0.1} required />
          </div>
        </div>

        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Strategy Intent</span></label>
          <div className="flex gap-2">
            <button type="button" className={`btn btn-sm flex-1 gap-1 ${intent === 'lend' ? 'btn-success' : 'btn-outline'}`}
              onClick={() => setIntent('lend')}>
              <ArrowUpRight className="w-3.5 h-3.5" /> Lend (Earn)
            </button>
            <button type="button" className={`btn btn-sm flex-1 gap-1 ${intent === 'borrow' ? 'btn-warning' : 'btn-outline'}`}
              onClick={() => setIntent('borrow')}>
              <ArrowDownRight className="w-3.5 h-3.5" /> Borrow
            </button>
          </div>
        </div>

        {/* Quote Source Selector — Radio Buttons */}
        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Quote Source</span></label>
          <div className="flex flex-col gap-1">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="quoteSource"
                className="radio radio-sm radio-info"
                checked={quoteSource === 'yfinance'}
                onChange={() => setQuoteSource('yfinance')}
              />
              <Database className="w-3.5 h-3.5 text-base-content/60" />
              <span className="text-sm">Yahoo Finance</span>
              <span className="text-[10px] text-base-content/40">(free, delayed)</span>
            </label>
            <label className={`flex items-center gap-2 ${brokerConnected ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}`}>
              <input
                type="radio"
                name="quoteSource"
                className="radio radio-sm radio-info"
                checked={quoteSource === 'ibkr'}
                onChange={() => brokerConnected && setQuoteSource('ibkr')}
                disabled={!brokerConnected}
              />
              <Zap className="w-3.5 h-3.5 text-base-content/60" />
              <span className="text-sm">IBKR</span>
              {brokerConnected
                ? <span className="text-[10px] text-base-content/40">(real-time, Greeks)</span>
                : <span className="text-[10px] text-error/70">(not connected)</span>
              }
            </label>
          </div>
        </div>

        {/* Max contracts — fewer is cheaper */}
        <div className="form-control">
          <label className="label py-1"><span className="label-text text-xs font-medium">Max Contracts</span></label>
          <select className="select select-bordered select-sm w-full" value={maxContracts}
            onChange={(e) => setMaxContracts(Number(e.target.value))}>
            <option value={1}>1 (cheapest — recommended)</option>
            <option value={2}>2</option>
            <option value={3}>3</option>
          </select>
        </div>

        {/* Expiration Date — single-ticker mode only */}
        {mode === 'single' && (
          <div className="form-control">
            <label className="label py-1"><span className="label-text text-xs font-medium">Expiration Date</span></label>
            {loadingExpirations ? (
              <div className="flex items-center gap-2 h-8 text-xs text-base-content/50">
                <Loader2 className="w-3 h-3 animate-spin" /> Loading expirations...
              </div>
            ) : availableExpirations.length > 0 ? (
              <select
                className="select select-bordered select-sm w-full"
                value={selectedExpiration || ''}
                onChange={(e) => setSelectedExpiration(e.target.value || null)}
              >
                <option value="">Auto (closest to {duration}d)</option>
                {availableExpirations.map(exp => (
                  <option key={exp} value={exp}>{exp}</option>
                ))}
              </select>
            ) : (
              <div className="text-xs text-base-content/40 h-8 flex items-center">
                {ticker.trim() ? 'No expirations found' : 'Enter a ticker to see expirations'}
              </div>
            )}
          </div>
        )}

        <div className="form-control justify-end lg:col-span-3">
          <div className="flex gap-2 items-center">
            {mode === 'scan' ? (
              <button type="submit" className="btn btn-primary btn-sm gap-2" disabled={scanLoading}>
                {scanLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                {scanLoading ? 'Scanning universe…' : 'Find Opportunities'}
              </button>
            ) : (
              <button type="submit" className="btn btn-primary btn-sm gap-2" disabled={loading || !ticker}>
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Box className="w-4 h-4" />}
                {loading ? 'Scanning Options...' : 'Find Box Spreads'}
              </button>
            )}

            {/* Saved Strategies */}
            <div className="relative">
              <button type="button" className="btn btn-outline btn-sm gap-1" onClick={() => setShowSavedList(!showSavedList)}>
                <FolderOpen className="w-4 h-4" />
                Saved ({savedStrategies.length})
              </button>
              {showSavedList && savedStrategies.length > 0 && (
                <div className="absolute top-full mt-1 right-0 z-50 w-80 max-h-72 overflow-y-auto bg-base-100 border border-base-300 rounded-xl shadow-xl p-2 space-y-1">
                  {savedStrategies.map(s => (
                    <div key={s.id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg hover:bg-base-200 cursor-pointer group"
                      onClick={() => handleLoadBoxStrategy(s)}>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium truncate">{s.name}</div>
                        <div className="text-xs opacity-50">
                          {s.ticker} &middot; {new Date(s.updated_at).toLocaleDateString()}
                        </div>
                      </div>
                      <button type="button"
                        className="btn btn-ghost btn-xs text-error opacity-0 group-hover:opacity-100"
                        onClick={(e) => { e.stopPropagation(); handleDeleteBoxStrategy(s.id); }}>
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {showSavedList && savedStrategies.length === 0 && (
                <div className="absolute top-full mt-1 right-0 z-50 w-60 bg-base-100 border border-base-300 rounded-xl shadow-xl p-4 text-center text-sm opacity-60">
                  No saved strategies yet
                </div>
              )}
            </div>
          </div>
        </div>
      </form>

      {/* ═══════ SCAN MODE: market timing + opportunity cards ═══════ */}
      {mode === 'scan' && (
        <>
          {scanError && (
            <div className="alert alert-error text-sm">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span className="flex-1 min-w-0">{scanError}</span>
            </div>
          )}

          {scanResult && (
            <div className="space-y-4">
              {/* Market timing banner */}
              {(() => {
                const mt = scanResult.market_timing;
                const tone = mt.good_to_trade
                  ? 'border-success/30 bg-success/10 text-success'
                  : mt.market_open
                    ? 'border-warning/30 bg-warning/10 text-warning'
                    : 'border-base-content/20 bg-base-200/40 text-base-content/70';
                return (
                  <div className={`rounded-xl border p-3 ${tone}`}>
                    <div className="flex items-start gap-2">
                      <Clock className="w-4 h-4 mt-0.5 shrink-0" />
                      <div className="text-sm">
                        <p className="font-semibold">
                          {mt.market_open ? (mt.good_to_trade ? 'Good time to place a box' : 'Market open — caution') : 'Market closed'}
                          <span className="font-normal opacity-70"> · {mt.now_et.replace('T', ' ')} ET</span>
                        </p>
                        <p className="opacity-90">{mt.recommendation}</p>
                      </div>
                    </div>
                  </div>
                );
              })()}

              {/* Box-market rate + target feasibility */}
              <div className={`rounded-xl border p-3 text-sm ${scanResult.target_feasible ? 'border-info/30 bg-info/10' : 'border-warning/30 bg-warning/10'}`}>
                <div className="flex items-start gap-2">
                  <Percent className="w-4 h-4 mt-0.5 shrink-0 text-base-content/60" />
                  <div>
                    <p className="font-semibold">
                      Box-market rate ≈ {scanResult.achievable_rate}%
                      <span className="font-normal text-base-content/50"> — a box {scanResult.intent === 'lend' ? 'lends' : 'borrows'} at roughly the risk-free rate{scanResult.risk_free_source ? ` (${scanResult.risk_free_source})` : ''}.</span>
                    </p>
                    {scanResult.feasibility_note && (
                      <p className={scanResult.target_feasible ? 'text-base-content/70' : 'text-warning'}>{scanResult.feasibility_note}</p>
                    )}
                  </div>
                </div>
              </div>

              {/* Summary line */}
              <div className="bg-base-200/40 rounded-xl p-3 flex flex-wrap items-center gap-3 text-sm border border-white/[0.03]">
                <span className="badge badge-outline badge-sm">{scanResult.intent === 'lend' ? 'Lending' : 'Borrowing'}</span>
                <span className="text-base-content/60">{scanResult.opportunities.length} fillable opportunities</span>
                <span className="text-base-content/60">·</span>
                <span className="text-base-content/60">{scanResult.params.duration_days}d · ≤{scanResult.params.max_contracts} contract{scanResult.params.max_contracts !== 1 ? 's' : ''}</span>
                <span className="text-base-content/60">·</span>
                <span className="text-base-content/60">scanned {scanResult.universe.length} tickers</span>
              </div>

              {/* Opportunity cards */}
              {scanResult.opportunities.length === 0 ? (
                <div className="alert alert-warning text-sm">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  <span className="flex-1 min-w-0">
                    No liquid boxes found right now. Try a different duration or a wider ticker list,
                    and scan during US market hours for live quotes.
                  </span>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {scanResult.opportunities.map((opp, i) => {
                    const fp = opp.fill_probability ?? 0;
                    const fpTone = fp >= 75 ? 'badge-success' : fp >= 50 ? 'badge-warning' : 'badge-error';
                    const theo = opp.theoretical_annual_rate ?? scanResult.achievable_rate;
                    const net = opp.net_achievable_rate ?? theo;
                    const drag = opp.cost_drag_bps;
                    const fillsAt = opp.recommended_limit_net;
                    const capital = scanResult.intent === 'lend' ? opp.total_cost : opp.total_proceeds;
                    const netTone = scanResult.intent === 'lend'
                      ? (net >= theo * 0.7 ? 'text-success' : net > 0 ? 'text-warning' : 'text-error')
                      : (net <= theo * 1.3 ? 'text-success' : 'text-warning');
                    return (
                      <div key={`${opp.ticker}-${opp.expiration}-${opp.lower_strike}-${i}`}
                        className="rounded-xl border border-white/[0.06] bg-base-200/30 p-4 space-y-3 hover:border-primary/30 transition-colors">
                        <div className="flex items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span className="font-bold text-base">{opp.ticker}</span>
                            <span className="text-xs text-base-content/50">${opp.current_price}</span>
                          </div>
                          <span className={`badge badge-sm gap-1 ${fpTone}`}>
                            <Gauge className="w-3 h-3" /> {fp}% fill
                          </span>
                        </div>

                        <div className="flex items-end justify-between gap-2">
                          <div>
                            <p className="text-[10px] uppercase tracking-wider text-base-content/50 flex items-center gap-1">
                              <Target className="w-3 h-3" /> Net {scanResult.intent === 'lend' ? 'yield' : 'cost'} after fees
                            </p>
                            <p className={`text-2xl font-bold ${netTone}`}>
                              {net != null ? net.toFixed(2) : '—'}%
                              <span className="text-xs font-normal text-base-content/40 ml-1">annualized</span>
                            </p>
                            <p className="text-[10px] text-base-content/50">
                              gross ≈ {theo != null ? theo.toFixed(2) : '—'}%{drag != null ? ` · −${drag}bps fees` : ''}
                            </p>
                            {fillsAt != null && (
                              <p className="text-[10px] text-base-content/40">fills at net ${fillsAt.toFixed(2)} · friction ≈ ${opp.friction_cost?.toFixed(0)}</p>
                            )}
                          </div>
                          <div className="text-right text-xs text-base-content/60 space-y-0.5">
                            <p>${opp.lower_strike} / ${opp.upper_strike} · w ${opp.box_width}</p>
                            <p>{opp.dte}d · {opp.expiration}</p>
                            <p>{opp.num_contracts} contract{opp.num_contracts !== 1 ? 's' : ''} · ${capital?.toLocaleString()}</p>
                          </div>
                        </div>

                        <button className="btn btn-primary btn-sm w-full gap-2" onClick={() => handleLoadOpportunity(opp)}>
                          <ArrowUpRight className="w-4 h-4" /> Load &amp; price to fill
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Skipped tickers */}
              {scanResult.skipped.length > 0 && (
                <p className="text-[11px] text-base-content/40">
                  Skipped: {scanResult.skipped.map(s => s.ticker).join(', ')} (no box met target at a fillable price)
                </p>
              )}
            </div>
          )}
        </>
      )}

      {/* Error */}
      {mode === 'single' && error && (
        <div className="alert alert-error text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span className="flex-1 min-w-0">{error}</span>
        </div>
      )}

      {/* Results */}
      {mode === 'single' && result && result.spreads && result.spreads.length > 0 && (
        <div className="space-y-4">
          {/* Summary Banner */}
          <div className="bg-base-200/40 rounded-xl p-3 flex flex-wrap items-center gap-4 text-sm border border-white/[0.03]">
            <span className="font-medium">{result.ticker}</span>
            <span className="text-base-content/60">Price: ${result.current_price}</span>
            <span className="badge badge-outline badge-sm">
              {result.intent === 'lend' ? 'Lending' : 'Borrowing'}
            </span>
            <span className="text-base-content/60">{result.spreads.length} spreads found</span>
            {spread?.fill_probability != null && (
              <span className={`badge badge-sm gap-1 ${spread.fill_probability >= 75 ? 'badge-success' : spread.fill_probability >= 50 ? 'badge-warning' : 'badge-error'}`}>
                <Gauge className="w-2.5 h-2.5" /> {spread.fill_probability}% fill prob
              </span>
            )}
            {result.quote_source && (
              <span className="badge badge-info badge-sm gap-1">
                {result.quote_source === 'ibkr' ? <Zap className="w-2.5 h-2.5" /> : <Database className="w-2.5 h-2.5" />}
                {result.quote_source}
              </span>
            )}
          </div>

          {/* Spread Selector Tabs */}
          {result.spreads.length > 1 && (
            <div className="flex gap-1 flex-wrap">
              {result.spreads.map((s, i) => (
                <button key={i}
                  className={`btn btn-xs ${selectedSpread === i ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => { setSelectedSpread(i); setSmartPrice(null); setPricingMode('mid'); }}>
                  {s.lower_strike}/{s.upper_strike} · {s.dte}d ·{' '}
                  {s.intent === 'lend' ? `${s.annualized_return_pct}%` : `${s.annualized_rate_pct}%`}
                </button>
              ))}
            </div>
          )}

          {/* Selected Spread Details */}
          {spread && (
            <div className="space-y-4">
              {/* Key Metrics */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Strikes</p>
                  <p className="text-lg font-bold">${spread.lower_strike} / ${spread.upper_strike}</p>
                  <p className="text-xs text-base-content/60">Width ${spread.box_width}</p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Expiration</p>
                  <p className="text-lg font-bold">{spread.dte}d</p>
                  <p className="text-xs text-base-content/60">{spread.expiration}</p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">
                    {spread.net_achievable_rate != null ? `Net ${spread.intent === 'lend' ? 'Yield' : 'Cost'}` : 'Mid-Price Rate'}
                  </p>
                  <p className={`text-lg font-bold ${spread.intent === 'lend' ? 'text-success' : 'text-warning'}`}>
                    {spread.net_achievable_rate != null ? `${spread.net_achievable_rate}%` : `${spread.implied_annual_rate}%`}
                  </p>
                  <p className="text-xs text-base-content/60">
                    {spread.theoretical_annual_rate != null
                      ? <>gross ≈ {spread.theoretical_annual_rate}%{spread.cost_drag_bps != null ? ` · −${spread.cost_drag_bps}bps` : ''}</>
                      : <>Target: {result.target_annual_return}%</>}
                  </p>
                </div>
                <div className="bg-base-200/40 rounded-xl p-3 text-center border border-white/[0.03]">
                  <p className="text-[10px] text-base-content/50 uppercase tracking-wider">Contracts</p>
                  <p className="text-lg font-bold">{spread.num_contracts}</p>
                  <p className="text-xs text-base-content/60">
                    {spread.intent === 'lend' ? `Cost: $${spread.total_cost?.toLocaleString()}` : `Proceeds: $${spread.total_proceeds?.toLocaleString()}`}
                  </p>
                </div>
              </div>

              {/* ═══════ PRICING CONTROLS ═══════ */}
              <div className="rounded-xl border border-primary/20 bg-primary/5 p-4 space-y-3">
                <h4 className="text-sm font-semibold flex items-center gap-2">
                  <Sliders className="w-4 h-4 text-primary" />
                  Order Pricing
                </h4>

                {/* Preset buttons */}
                <div className="flex flex-wrap gap-2">
                  {spread.recommended_limit_net != null && (
                    <button className="btn btn-xs gap-1 btn-primary"
                      title={`No-arbitrage fair value — the price that fills. Transacts at ≈ ${spread.theoretical_annual_rate}%`}
                      onClick={() => { setCustomPrice(String(spread.recommended_limit_net)); setPricingMode('custom'); }}>
                      <Target className="w-3 h-3" /> Fair Value (fills) ≈ {spread.theoretical_annual_rate}%
                    </button>
                  )}
                  <button className={`btn btn-xs gap-1 ${pricingMode === 'low' ? 'btn-success' : 'btn-outline'}`}
                    onClick={() => setPricingMode('low')}>
                    Low (Conservative)
                  </button>
                  <button className={`btn btn-xs gap-1 ${pricingMode === 'mid' ? 'btn-info' : 'btn-outline'}`}
                    onClick={() => setPricingMode('mid')}>
                    Mid (Default)
                  </button>
                  <button className={`btn btn-xs gap-1 ${pricingMode === 'high' ? 'btn-warning' : 'btn-outline'}`}
                    onClick={() => setPricingMode('high')}>
                    High (Aggressive)
                  </button>
                  <button className={`btn btn-xs gap-1 ${pricingMode === 'smart' ? 'btn-secondary' : 'btn-outline'}`}
                    onClick={handleSmartPrice} disabled={loadingSmartPrice}>
                    {loadingSmartPrice ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
                    Smart Price
                  </button>
                  <button className={`btn btn-xs gap-1 ${pricingMode === 'custom' ? 'btn-accent' : 'btn-outline'}`}
                    onClick={() => setPricingMode('custom')}>
                    <Edit3 className="w-3 h-3" /> Custom
                  </button>
                  <button className="btn btn-xs gap-1 btn-outline btn-info"
                    onClick={handleRefreshQuotes} disabled={refreshingQuotes}>
                    {refreshingQuotes ? <Loader2 className="w-3 h-3 animate-spin"/> : <Zap className="w-3 h-3" />}
                    Refresh Quotes ({quoteSource})
                  </button>
                </div>

                {/* Custom price input */}
                {pricingMode === 'custom' && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-base-content/60">
                      Net {intent === 'lend' ? 'debit' : 'credit'} per contract:
                    </span>
                    <div className="relative w-28">
                      <DollarSign className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-base-content/40" />
                      <input type="number" step="0.01" min="0.01"
                        className="input input-bordered input-xs w-full pl-6 font-mono"
                        value={customPrice}
                        onChange={(e) => setCustomPrice(e.target.value)}
                        placeholder={pricingMetrics?.netPerContract?.toFixed(2) || '0.00'} />
                    </div>
                    {customPrice && spread && (
                      <span className="text-[10px] text-base-content/40">
                        = ${(Number(customPrice) * 100 * spread.num_contracts).toLocaleString()} total
                        ({spread.num_contracts} × 100 × ${Number(customPrice).toFixed(2)})
                      </span>
                    )}
                  </div>
                )}

                {/* Smart price details */}
                {pricingMode === 'smart' && smartPrice && (
                  <div className="bg-base-200/50 rounded-lg p-2 text-xs space-y-1">
                    {smartPrice.recommended_legs.map((leg, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <span className={`badge badge-xs ${leg.action === 'BUY' ? 'badge-success' : 'badge-error'}`}>
                          {leg.action}
                        </span>
                        <span className="font-mono">${leg.strike}</span>
                        <span className="font-mono font-medium">${leg.price.toFixed(2)}</span>
                        <span className="text-base-content/50">({leg.confidence_pct}% confidence)</span>
                        <span className="text-base-content/40 truncate">{leg.reasoning}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* ═══ LIVE ANNUALIZED RETURN DISPLAY ═══ */}
                {pricingMetrics && pricingMetrics.annualizedReturn !== null && (
                  <div className={`rounded-lg p-3 border ${
                    returnColor(pricingMetrics.annualizedReturn, targetReturn).includes('success')
                      ? 'border-success/30 bg-success/10'
                      : returnColor(pricingMetrics.annualizedReturn, targetReturn).includes('warning')
                        ? 'border-warning/30 bg-warning/10'
                        : 'border-error/30 bg-error/10'
                  }`}>
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-base-content/50">
                          At {pricingMode === 'smart' ? 'Smart' : pricingMode === 'custom' ? 'Custom' : pricingMode.charAt(0).toUpperCase() + pricingMode.slice(1)} Price
                        </p>
                        <p className={`text-2xl font-bold ${returnColor(pricingMetrics.annualizedReturn, targetReturn)}`}>
                          {pricingMetrics.annualizedReturn.toFixed(2)}%
                          <span className="text-sm font-normal text-base-content/50 ml-1">annualized</span>
                        </p>
                      </div>
                      <div className="text-right text-xs text-base-content/60 space-y-0.5">
                        <p>Net per contract: <span className="font-mono font-medium">${Math.abs(pricingMetrics.netPerContract).toFixed(4)}</span></p>
                        <p>Total {intent === 'lend' ? 'cost' : 'proceeds'}: <span className="font-mono font-medium">${Math.abs(pricingMetrics.totalCost || 0).toLocaleString()}</span></p>
                        <p>Total {intent === 'lend' ? 'profit' : 'interest'}: <span className="font-mono font-medium">${Math.abs(pricingMetrics.totalProfit || 0).toLocaleString()}</span></p>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* 4 Legs Table */}
              <div>
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" />
                  Option Legs ({spread.legs.length} legs)
                  {pricingMode !== 'mid' && <span className="badge badge-xs badge-primary">{pricingMode} price</span>}
                </h4>
                <div className="overflow-x-auto">
                  <table className="table table-xs table-pro w-full">
                    <thead>
                      <tr className="text-base-content/60">
                        <th>Action</th>
                        <th>Type</th>
                        <th>Strike</th>
                        <th>Bid</th>
                        <th>Ask</th>
                        <th>Mid</th>
                        <th className="text-primary">Limit</th>
                        <th>IV</th>
                        <th>OI</th>
                        <th>Vol</th>
                        {spread.legs.some((l: BoxSpreadLeg) => l.delta != null) && (
                          <>
                            <th>Delta</th>
                            <th>Theta</th>
                          </>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {adjustedLegs?.map((leg, i: number) => (
                        <tr key={i} className={leg.action === 'BUY' ? 'bg-success/5' : 'bg-error/5'}>
                          <td>
                            <span className={`badge badge-xs ${leg.action === 'BUY' ? 'badge-success' : 'badge-error'}`}>
                              {leg.action}
                            </span>
                          </td>
                          <td className="font-medium">{leg.type}</td>
                          <td className="font-mono">${leg.strike}</td>
                          <td className="font-mono">${leg.bid.toFixed(2)}</td>
                          <td className="font-mono">${leg.ask.toFixed(2)}</td>
                          <td className="font-mono">${leg.mid.toFixed(2)}</td>
                          <td className="font-mono font-medium text-primary">${leg.adjustedPrice.toFixed(2)}</td>
                          <td>{leg.iv != null ? `${leg.iv}%` : '—'}</td>
                          <td>{leg.oi.toLocaleString()}</td>
                          <td>{leg.vol.toLocaleString()}</td>
                          {spread.legs.some((l: BoxSpreadLeg) => l.delta != null) && (
                            <>
                              <td className="font-mono">{leg.delta != null ? leg.delta.toFixed(3) : '—'}</td>
                              <td className="font-mono">{leg.theta != null ? leg.theta.toFixed(3) : '—'}</td>
                            </>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Save Strategy */}
              <div className="flex items-center gap-2 mt-2">
                {showSaveInput ? (
                  <>
                    <input type="text" className="input input-sm input-bordered w-48"
                      placeholder="Strategy name..." value={saveName}
                      onChange={e => setSaveName(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && saveName.trim() && handleSaveBoxStrategy(true)}
                      autoFocus />
                    <button className="btn btn-sm btn-success gap-1"
                      onClick={() => handleSaveBoxStrategy(true)}
                      disabled={!saveName.trim() || savingStrategy}>
                      {savingStrategy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                      Save
                    </button>
                    <button className="btn btn-sm btn-ghost" onClick={() => setShowSaveInput(false)}>Cancel</button>
                  </>
                ) : (
                  <>
                    <button className="btn btn-sm btn-outline btn-success gap-1"
                      onClick={() => { setShowSaveInput(true); setSaveName(''); }}>
                      <Save className="w-3 h-3" /> Save as New
                    </button>
                    {loadedStrategyId && (
                      <button className="btn btn-sm btn-outline btn-info gap-1"
                        onClick={() => {
                          const loaded = savedStrategies.find(s => s.id === loadedStrategyId);
                          setSaveName(loaded?.name ?? '');
                          handleSaveBoxStrategy(false);
                        }}
                        disabled={savingStrategy}>
                        {savingStrategy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                        Update
                      </button>
                    )}
                  </>
                )}
              </div>

              {/* IB Order Placement */}
              <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-4">
                <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                  <Send className="w-4 h-4 text-primary" />
                  Place Order via Interactive Brokers
                </h4>

                {brokerConnected === null && (
                  <div className="flex items-center gap-2 text-xs text-base-content/50">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Checking broker connection...
                  </div>
                )}

                {brokerConnected === false && (
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2 text-sm text-base-content/60">
                      <WifiOff className="w-4 h-4 text-error/70" />
                      No Interactive Brokers account connected.
                    </div>
                    <a href="/settings" className="btn btn-sm btn-outline gap-1.5">
                      <ExternalLink className="w-3.5 h-3.5" />
                      Connect in Settings
                    </a>
                  </div>
                )}

                {brokerConnected === true && (
                  <div className="space-y-3">
                    <div className="text-xs text-base-content/60 bg-base-300/40 rounded-lg p-3 space-y-1">
                      <p className="font-medium text-base-content/80 flex items-center gap-1.5">
                        <Wifi className="w-3.5 h-3.5 text-success" />
                        IB account connected
                      </p>
                      <p>
                        Will submit {spread.legs.length} option legs × {spread.num_contracts} contract{spread.num_contracts !== 1 ? 's' : ''} at
                        <span className="font-medium text-primary"> {pricingMode}</span> prices as limit orders.
                      </p>
                    </div>
                    <button className="btn btn-primary btn-sm gap-2"
                      onClick={() => setShowOrderModal(true)}>
                      <Send className="w-4 h-4" />
                      Preview Order ({spread.legs.length} legs)
                    </button>
                  </div>
                )}
              </div>

              {/* Mark as Placed (external order tracking) */}
              <div className="rounded-xl border border-white/[0.06] bg-base-200/30 p-4">
                <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
                  <Briefcase className="w-4 h-4 text-info" />
                  Track External Order
                </h4>
                <p className="text-xs text-base-content/60 mb-3">
                  Already placed this trade on another platform? Mark it as placed to track P&L in My Trades.
                </p>
                {!loadedStrategyId ? (
                  <p className="text-xs text-warning">Save the strategy first to enable trade tracking.</p>
                ) : markedAsPlaced ? (
                  <div className="flex items-center gap-2 text-success text-sm">
                    <CheckCircle2 className="w-4 h-4" />
                    Trade is being tracked! View it in <span className="font-bold">My Trades</span>.
                  </div>
                ) : (
                  <button
                    className="btn btn-outline btn-info btn-sm gap-2"
                    disabled={markingAsPlaced}
                    onClick={async () => {
                      if (!loadedStrategyId || !adjustedLegs) return;
                      setMarkingAsPlaced(true);
                      try {
                        const entryPrices = adjustedLegs.map((l: any) => ({
                          strike: l.strike, type: l.type, price: l.adjustedPrice,
                        }));
                        const netDebit = adjustedLegs.reduce((sum: number, l: any) =>
                          sum + (l.action === 'BUY' ? l.adjustedPrice : -l.adjustedPrice) * 100 * (spread?.num_contracts ?? 1), 0);
                        await markStrategyAsTraded(loadedStrategyId, {
                          entry_prices: entryPrices,
                          entry_net_debit: netDebit,
                          order_source: 'manual',
                        });
                        setMarkedAsPlaced(true);
                      } catch (err: any) {
                        setError(err.message || 'Failed to mark as placed');
                      } finally {
                        setMarkingAsPlaced(false);
                      }
                    }}
                  >
                    {markingAsPlaced ? <Loader2 className="w-4 h-4 animate-spin" /> : <Briefcase className="w-4 h-4" />}
                    Mark as Placed
                  </button>
                )}
              </div>

              {/* Payoff Diagram */}
              <div>
                <h4 className="text-sm font-semibold mb-2">Payoff at Expiration</h4>
                <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]" style={{ height: 200 }}>
                  <Line
                    data={{
                      labels: (() => {
                        const k1 = spread.lower_strike;
                        const k2 = spread.upper_strike;
                        const range = k2 - k1;
                        const points: number[] = [];
                        for (let p = k1 - range; p <= k2 + range; p += range / 20) {
                          points.push(Math.round(p * 100) / 100);
                        }
                        return points;
                      })(),
                      datasets: [
                        {
                          label: 'P&L per Contract ($)',
                          data: (() => {
                            const k1 = spread.lower_strike;
                            const k2 = spread.upper_strike;
                            const range = k2 - k1;
                            const points: number[] = [];
                            for (let p = k1 - range; p <= k2 + range; p += range / 20) {
                              const value = k2 - k1;
                              const pl = spread.intent === 'lend'
                                ? (value - (pricingMetrics?.netPerContract || spread.box_cost_mid || 0)) * 100
                                : (value - (Math.abs(pricingMetrics?.netPerContract || 0) || spread.box_proceeds_mid || 0)) * 100;
                              points.push(Math.round(pl * 100) / 100);
                            }
                            return points;
                          })(),
                          borderColor: spread.intent === 'lend' ? '#36d399' : '#fbbd23',
                          backgroundColor: spread.intent === 'lend' ? 'rgba(54,211,153,0.1)' : 'rgba(251,189,35,0.1)',
                          fill: true,
                          tension: 0,
                          pointRadius: 0,
                        },
                        {
                          label: 'Break-even',
                          data: (() => {
                            const k1 = spread.lower_strike;
                            const k2 = spread.upper_strike;
                            const range = k2 - k1;
                            const points: number[] = [];
                            for (let p = k1 - range; p <= k2 + range; p += range / 20) {
                              points.push(0);
                            }
                            return points;
                          })(),
                          borderColor: 'rgba(255,255,255,0.2)',
                          borderDash: [4, 4],
                          pointRadius: 0,
                          borderWidth: 1,
                        },
                      ],
                    }}
                    options={{
                      responsive: true,
                      maintainAspectRatio: false,
                      plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: (ctx) => `P&L: $${(ctx.parsed.y ?? 0).toFixed(2)}` } },
                      },
                      scales: {
                        x: {
                          title: { display: true, text: 'Underlying Price at Expiration', font: { size: 10 } },
                          ticks: { font: { size: 9 }, maxTicksLimit: 8 },
                          grid: { color: 'rgba(255,255,255,0.05)' },
                        },
                        y: {
                          title: { display: true, text: 'P&L ($)', font: { size: 10 } },
                          ticks: { font: { size: 9 } },
                          grid: { color: 'rgba(255,255,255,0.05)' },
                        },
                      },
                    }}
                  />
                </div>
                <p className="text-[10px] text-base-content/40 mt-1">
                  Box spread payoff is constant regardless of underlying price — it&apos;s a synthetic bond.
                </p>
              </div>
            </div>
          )}

          {/* Risk Analysis */}
          {result.risks && result.risks.length > 0 && (
            <div>
              <button className="btn btn-ghost btn-sm gap-2 mb-2" onClick={() => setShowRisks(!showRisks)}>
                <Shield className="w-4 h-4 text-warning" />
                Risk Analysis & Mitigation
                {showRisks ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </button>
              {showRisks && (
                <div className="space-y-2">
                  {result.risks.map((risk, i) => (
                    <div key={i} className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                      <div className="flex items-center gap-2 mb-1">
                        {risk.severity === 'High' && <XCircle className="w-4 h-4 text-error" />}
                        {risk.severity === 'Medium' && <AlertCircle className="w-4 h-4 text-warning" />}
                        {risk.severity === 'Low' && <CheckCircle2 className="w-4 h-4 text-success" />}
                        {risk.severity === 'Info' && <Info className="w-4 h-4 text-info" />}
                        <span className="font-medium text-sm">{risk.category}</span>
                        <span className={`badge badge-xs ${severityColor(risk.severity)}`}>{risk.severity}</span>
                      </div>
                      <p className="text-xs text-base-content/60 mb-1">{risk.description}</p>
                      <p className="text-xs text-base-content/80"><strong>Mitigation:</strong> {risk.mitigation}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* No results */}
      {mode === 'single' && result && (!result.spreads || result.spreads.length === 0) && !error && (
        <div className="alert alert-warning text-sm">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span className="flex-1 min-w-0">No viable box spreads found for these parameters. Try adjusting the ticker, duration, or target return.</span>
        </div>
      )}

      {/* Order Confirmation Modal */}
      {showOrderModal && result && spread && (
        <OrderConfirmationModal
          ticker={result.ticker}
          strategy="box_spread"
          legs={orderLegs}
          spread={spread}
          intent={intent}
          pricingMode={pricingMode}
          savedStrategyId={loadedStrategyId ?? undefined}
          onClose={() => setShowOrderModal(false)}
        />
      )}
    </div>
  );
}
