import React, { useState, useEffect, useMemo } from 'react';
import {
    Shield, ShieldAlert, AlertCircle, Percent, Timer, Briefcase, Calculator,
    TrendingUp, TrendingDown, Layers, Send, Database, Zap,
    Sliders, Edit3, Loader2, Sparkles, Save, FolderOpen, Trash2, CheckCircle2,
} from 'lucide-react';
import {
    computeDualDirectionBuffer, computeDualDirectionBufferIBKR, fetchBrokerConnection, fetchOptionQuote, fetchOptionQuotesBatch,
    fetchSavedStrategies, saveStrategy, updateSavedStrategy, deleteSavedStrategy, markStrategyAsTraded, fetchOptionExpirations,
} from '../api';
import type { SavedStrategyItem } from '../api';
import { roundToTick } from '../utils/tickSize';
import { OrderConfirmationModal } from './OrderConfirmationModal';
import PreTradeAdvisor, { type AdvisorMetric } from './PreTradeAdvisor';
import type { BrokerOrderLeg } from '../types';
import {
    Chart as ChartJS, CategoryScale, LinearScale, PointElement,
    LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(
    CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler
);

type PricingMode = 'low' | 'mid' | 'high' | 'smart' | 'custom';

interface OptionLeg {
    layer?: string;
    action: string;
    qty: number;
    type: string;
    strike: number;
    midPrice: number;
    bid?: number;
    ask?: number;
    purpose: string;
}

interface DualDirectionResult {
    success: boolean;
    ticker: string;
    currentPrice: number;
    investmentAmount: number;
    durationDays: number;
    expirationDate: string;
    actualDte: number;
    parameters: {
        requestedDownsideBuffer: number;
        requestedUpsideCap: number;
        actualDownsideBuffer: number;
        actualUpsideCap: number;
    };
    legs: OptionLeg[];
    netOptionsPremium: number;
    actualStructureCost: number;
    scenarios: any[];
    crossovers?: { breakeven_pct: number; from_roi: number; to_roi: number; direction: string }[];
    maxProfit?: { underlyingChangePct: number; roi: number; totalPayout: number; simulatedPrice: number };
    maxLoss?: { underlyingChangePct: number; roi: number; totalPayout: number; simulatedPrice: number };
    bufferCapWarnings?: string[];
    ibkr_mode?: boolean;
    quantities_rounded?: boolean;
    available_expirations?: string[];
    error?: string;
}

interface CustomOptionLeg {
    id: string;
    action: 'Buy' | 'Sell';
    qty: number;
    type: 'Call' | 'Put' | 'Equity';
    strike: number;
    price: number;
    bid: number;
    ask: number;
    mid: number;
}

/** Standard monthly options expire on the 3rd Friday of the month (day 15-21).
 * The Dual Direction Buffer only trades monthlies — they carry the full,
 * liquid strike ladder the 4-layer structure needs. */
function isMonthlyExpiration(dateStr: string): boolean {
    const d = new Date(dateStr + 'T00:00:00');
    return d.getDay() === 5 && d.getDate() >= 15 && d.getDate() <= 21;
}

export function DualDirectionBuffer() {
    const [ticker, setTicker] = useState('SPY');
    const [amount, setAmount] = useState<number>(10000);
    const [duration, setDuration] = useState<number>(365);
    const [downsideBuffer, setDownsideBuffer] = useState<number>(15);
    const [upsideCap, setUpsideCap] = useState<number>(15);

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<DualDirectionResult | null>(null);
    const [customLegs, setCustomLegs] = useState<CustomOptionLeg[]>([]);
    const [bondYield, setBondYield] = useState<number>(3.0);

    // Data source
    const [quoteSource, setQuoteSource] = useState<'yfinance' | 'ibkr'>('yfinance');
    const [brokerConnected, setBrokerConnected] = useState<boolean | null>(null);

    // Pricing mode (applies to custom legs)
    const [pricingMode, setPricingMode] = useState<PricingMode>('mid');
    const [customNetDebit, setCustomNetDebit] = useState<string>('');

    // Scenarios (button-triggered)
    const [evaluatedScenarios, setEvaluatedScenarios] = useState<any[]>([]);
    // True payoff bounds (real max gain / max loss across the whole payoff,
    // not just the value at the simulation-window edge).
    const [payoffBounds, setPayoffBounds] = useState<{
        maxGain: any; maxLoss: any;
        upsideCapped: boolean; downsideFloored: boolean;
        gainOnsetPct: number; lossOnsetPct: number;
    } | null>(null);

    // Order placement
    const [showOrderModal, setShowOrderModal] = useState(false);

    // Track whether custom legs have changed since last evaluation
    const [legsSnapshot, setLegsSnapshot] = useState<string>('');
    const [fetchingPrices, setFetchingPrices] = useState(false);
    const [selectedExpiration, setSelectedExpiration] = useState<string | null>(null);
    const [availableExpirations, setAvailableExpirations] = useState<string[]>([]);
    const [loadingExpirations, setLoadingExpirations] = useState(false);
    const [markingAsPlaced, setMarkingAsPlaced] = useState(false);
    const [markedAsPlaced, setMarkedAsPlaced] = useState(false);

    // Save / Load
    const [savedStrategies, setSavedStrategies] = useState<SavedStrategyItem[]>([]);
    const [loadedStrategyId, setLoadedStrategyId] = useState<number | null>(null);
    const [saveName, setSaveName] = useState('');
    const [showSaveInput, setShowSaveInput] = useState(false);
    const [savingStrategy, setSavingStrategy] = useState(false);
    const [showSavedList, setShowSavedList] = useState(false);

    const hasUnevaluatedChanges = useMemo(() => {
        const currentSnapshot = JSON.stringify(customLegs.map(l => ({ ...l, id: undefined }))) + `|by=${bondYield}`;
        return customLegs.length > 0 && currentSnapshot !== legsSnapshot;
    }, [customLegs, legsSnapshot, bondYield]);

    // Dual Direction Buffer only offers monthly expirations in the picker.
    const monthlyExpirations = useMemo(
        () => availableExpirations.filter(isMonthlyExpiration),
        [availableExpirations],
    );

    // Load saved strategies list on mount
    useEffect(() => {
        fetchSavedStrategies('dual_direction_buffer')
            .then(setSavedStrategies)
            .catch(() => {});
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
        }, 500);
        return () => clearTimeout(timer);
    }, [ticker]);

    // ---------------------------------------------------------------------------
    // Pricing helpers (for custom legs)
    // ---------------------------------------------------------------------------

    const roundTick = (v: number) => roundToTick(v);

    /** Compute limit price for a custom leg from its stored bid/ask/mid. */
    const getCustomLimitPrice = (leg: CustomOptionLeg, mode: PricingMode): number => {
        // Equity trades at the share price — pricing modes (bid/ask skew, net-debit
        // scaling) only apply to options. Keep the stock leg at its price.
        if (leg.type === 'Equity') return roundTick(leg.price || leg.mid || 0);
        const { bid, ask, mid } = leg;
        const spread = Math.max(0, ask - bid);
        switch (mode) {
            case 'low':
                return roundTick(leg.action === 'Buy' ? ask : bid);
            case 'high':
                return roundTick(leg.action === 'Buy' ? bid : ask);
            case 'smart':
                return roundTick(leg.action === 'Buy' ? bid + 0.4 * spread : Math.max(0.05, ask - 0.4 * spread));
            case 'custom':
                // Scale option legs to match the net debit target. Exclude equity —
                // stock is bought at market, it isn't part of the options net debit.
                if (customNetDebit && !isNaN(Number(customNetDebit))) {
                    const targetTotal = Number(customNetDebit);
                    const midTotal = customLegs.reduce((sum, l) =>
                        l.type === 'Equity' ? sum : sum + (l.action === 'Buy' ? 1 : -1) * l.mid * l.qty * 100, 0);
                    if (midTotal !== 0) return roundTick(mid * (targetTotal / midTotal));
                }
                return roundTick(mid);
            default:
                return roundTick(mid);
        }
    };

    /** Apply current pricing mode to all custom legs. */
    const applyPricingMode = (mode: PricingMode) => {
        setPricingMode(mode);
        setCustomLegs(prev => prev.map(leg => ({
            ...leg,
            price: Math.round(getCustomLimitPrice(leg, mode) * 100) / 100,
        })));
    };

    // ---------------------------------------------------------------------------
    // Custom leg helpers
    // ---------------------------------------------------------------------------

    const addCustomLeg = () => {
        setCustomLegs(prev => [...prev, {
            id: Math.random().toString(36).substr(2, 9),
            action: 'Buy',
            qty: 1,
            type: 'Call',
            strike: result?.currentPrice ? Math.floor(result.currentPrice) : 0,
            price: 0,
            bid: 0,
            ask: 0,
            mid: 0,
        }]);
    };

    const updateCustomLeg = (id: string, field: keyof CustomOptionLeg, value: any) => {
        setCustomLegs(prev => prev.map(leg => {
            if (leg.id !== id) return leg;
            const updated = { ...leg, [field]: value };
            // Switching a leg to Equity: it trades at the underlying price, not
            // whatever stale option quote it carried. Peg strike/price/bid/ask/mid
            // to the current share price so cost & pricing math stay correct.
            if (field === 'type' && value === 'Equity') {
                const px = Math.round((result?.currentPrice ?? leg.price ?? 0) * 100) / 100;
                updated.strike = px; updated.price = px;
                updated.bid = px; updated.ask = px; updated.mid = px;
            }
            return updated;
        }));
    };

    /** Batch-fetch latest bid/ask/mid for all option legs that need pricing. */
    const fetchLatestPrices = async () => {
        if (!result || customLegs.length === 0) return;
        setFetchingPrices(true);
        try {
            const optionLegs = customLegs.filter(l => l.type !== 'Equity' && l.strike > 0);
            const requests = optionLegs.map(leg => ({
                ticker,
                expiration: result.expirationDate,
                strike: leg.strike,
                right: leg.type === 'Call' ? 'C' : 'P',
                quote_source: quoteSource,
            }));
            const batchResults = await fetchOptionQuotesBatch(requests);
            
            setCustomLegs(prev => {
                const quoteMap = new Map<string, { bid: number; ask: number; mid: number }>();
                for (let i = 0; i < batchResults.length; i++) {
                    const q = batchResults[i];
                    if (q && !q.error) {
                        quoteMap.set(optionLegs[i].id, {
                            bid: Math.round(q.bid * 100) / 100,
                            ask: Math.round(q.ask * 100) / 100,
                            mid: Math.round(q.mid * 100) / 100,
                        });
                    }
                }
                return prev.map(leg => {
                    const q = quoteMap.get(leg.id);
                    if (!q) return leg;
                    const updated = { ...leg, bid: q.bid, ask: q.ask, mid: q.mid };
                    updated.price = Math.round(getCustomLimitPrice(updated, pricingMode) * 100) / 100;
                    return updated;
                });
            });
        } catch (err) {
            console.warn('Batch quote fetch failed:', err);
        } finally {
            setFetchingPrices(false);
        }
    };

    const removeCustomLeg = (id: string) => {
        setCustomLegs(prev => prev.filter(leg => leg.id !== id));
    };

    /** Fill custom execution table from recommended legs at mid price. */
    const autofillRecommended = () => {
        if (!result) return;
        const mapped: CustomOptionLeg[] = result.legs.map((leg, i) => ({
            id: `auto_${i}_${Math.random().toString(36).substr(2, 5)}`,
            action: leg.action as 'Buy' | 'Sell',
            qty: leg.qty,
            type: leg.type as 'Call' | 'Put' | 'Equity',
            strike: Math.round((leg.strike ?? 0) * 100) / 100,
            bid: Math.round((leg.bid ?? leg.midPrice) * 100) / 100,
            ask: Math.round((leg.ask ?? leg.midPrice) * 100) / 100,
            mid: Math.round((leg.midPrice ?? 0) * 100) / 100,
            price: Math.round((leg.midPrice ?? 0) * 100) / 100,
        }));
        setCustomLegs(mapped);
        setPricingMode('mid');
        setEvaluatedScenarios([]);
        setPayoffBounds(null);
    };

    // ---------------------------------------------------------------------------
    // Evaluate outcomes (button-triggered)
    // ---------------------------------------------------------------------------

    const evaluateOutcomes = () => {
        if (!result || customLegs.length === 0) return;

        // Fine-grained 1% increments across a wide window so the curve visibly
        // flattens into its plateaus on both sides (the true max gain / max loss).
        const RANGE = 50;
        const priceChanges: number[] = [];
        for (let pct = -RANGE; pct <= RANGE; pct++) priceChanges.push(pct / 100);

        const currentPrice = result.currentPrice;

        // Total capital deployed = trade debit + margin held
        const baseMargin = calcSpreadMargin(customLegs);
        const tradeCost = customLegs.reduce((sum, leg) =>
            sum + (leg.action === 'Buy' ? 1 : -1) * leg.price * leg.qty * 100, 0);
        const totalDeployed = Math.max(0, tradeCost) + baseMargin;

        // Interest earned on margin money (cash collateral earns yield)
        const durationDays = result.actualDte ?? result.durationDays ?? duration;
        const marginInterest = baseMargin * (bondYield / 100) * (durationDays / 365);

        const calcScenario = (change: number) => {
            const simPrice = currentPrice * (1 + change);

            let optionsPayout = 0;
            customLegs.forEach(leg => {
                let intrinsic = 0;
                if (leg.type === 'Call') intrinsic = Math.max(0, simPrice - leg.strike);
                else if (leg.type === 'Put') intrinsic = Math.max(0, leg.strike - simPrice);
                else if (leg.type === 'Equity') intrinsic = simPrice;
                const sign = leg.action === 'Buy' ? 1 : -1;
                optionsPayout += sign * leg.qty * intrinsic * 100;
            });

            // Estimate margin at this simulated price by shifting strikes relative to price move
            // For simplicity, compute IBKR spread margin at current strikes (margin doesn't change
            // by underlying price for defined-risk spreads, only for naked positions)
            const scenarioMargin = baseMargin;

            // P&L = options payout - trade cost + interest on margin cash
            const pnl = optionsPayout - tradeCost + marginInterest;
            // ROI based on total capital deployed (debit + margin)
            const roi = totalDeployed > 0 ? (pnl / totalDeployed) * 100 : 0;

            return {
                underlyingChangePct: Math.round(change * 100 * 100) / 100,
                simulatedPrice: Math.round(simPrice * 100) / 100,
                optionsPayout: Math.round(optionsPayout * 100) / 100,
                marginInterest: Math.round(marginInterest * 100) / 100,
                pnl: Math.round(pnl * 100) / 100,
                totalDeployed: Math.round(totalDeployed * 100) / 100,
                margin: Math.round(scenarioMargin * 100) / 100,
                roi: Math.round(roi * 100) / 100,
                isCrossover: false,
                crossoverNote: '' as string,
            };
        };

        const rawScenarios = priceChanges.map(calcScenario);

        // --- True max gain / max loss --------------------------------------
        // A piecewise-linear option payoff reaches its extrema only at a strike
        // or at the price boundaries (0 on the downside, far above the top
        // strike on the upside). Evaluate those exact points so the reported
        // max gain & max loss are the strategy's real bounds — not whatever
        // happens to sit at the edge of the simulation window.
        const optionStrikes = customLegs
            .filter(l => l.type !== 'Equity' && l.strike > 0)
            .map(l => l.strike);
        const topStrike = optionStrikes.length ? Math.max(...optionStrikes) : currentPrice * 2;
        const bottomStrike = optionStrikes.length ? Math.min(...optionStrikes) : currentPrice;
        const boundPrices = [0, ...optionStrikes, topStrike * 2];   // floor, every kink, past the top
        const boundScenarios = boundPrices.map(p => calcScenario(p / currentPrice - 1));
        const maxGain = boundScenarios.reduce((b, s) => (s.roi > b.roi ? s : b), boundScenarios[0]);
        const maxLoss = boundScenarios.reduce((w, s) => (s.roi < w.roi ? s : w), boundScenarios[0]);
        // Capped/floored = moving further past the outer strike no longer
        // changes ROI, i.e. the payoff has plateaued.
        const upsideCapped =
            Math.abs(calcScenario((topStrike * 4) / currentPrice - 1).roi
                   - calcScenario((topStrike * 2) / currentPrice - 1).roi) < 0.01;
        const downsideFloored = bottomStrike > 0 &&
            Math.abs(calcScenario(-1).roi
                   - calcScenario((bottomStrike * 0.5) / currentPrice - 1).roi) < 0.01;
        const topStrikePct = Math.round((topStrike / currentPrice - 1) * 1000) / 10;
        const bottomStrikePct = Math.round((bottomStrike / currentPrice - 1) * 1000) / 10;
        setPayoffBounds({
            maxGain, maxLoss, upsideCapped, downsideFloored,
            gainOnsetPct: upsideCapped ? topStrikePct : maxGain.underlyingChangePct,
            lossOnsetPct: downsideFloored ? bottomStrikePct : maxLoss.underlyingChangePct,
        });

        // --- Crossover detection: insert exact breakeven rows ---
        const finalScenarios: typeof rawScenarios = [];
        for (let i = 0; i < rawScenarios.length; i++) {
            if (i > 0) {
                const prev = rawScenarios[i - 1];
                const curr = rawScenarios[i];
                if ((prev.roi > 0 && curr.roi < 0) || (prev.roi < 0 && curr.roi > 0)) {
                    // Linear interpolation for exact crossover %
                    const exactPct = prev.underlyingChangePct +
                        (curr.underlyingChangePct - prev.underlyingChangePct) *
                        (-prev.roi) / (curr.roi - prev.roi);
                    const crossoverScenario = calcScenario(exactPct / 100);
                    crossoverScenario.isCrossover = true;
                    crossoverScenario.crossoverNote = prev.roi > 0
                        ? `Breakeven: ROI turns negative at ${exactPct.toFixed(2)}%`
                        : `Breakeven: ROI turns positive at ${exactPct.toFixed(2)}%`;
                    crossoverScenario.underlyingChangePct = Math.round(exactPct * 100) / 100;
                    finalScenarios.push(crossoverScenario);
                }
            }
            finalScenarios.push(rawScenarios[i]);
        }

        setEvaluatedScenarios(finalScenarios);
        setLegsSnapshot(JSON.stringify(customLegs.map(l => ({ ...l, id: undefined }))) + `|by=${bondYield}`);
    };

    // ---------------------------------------------------------------------------
    // Save / Load / Delete
    // ---------------------------------------------------------------------------

    const handleSaveStrategy = async (asNew = true) => {
        if (!result || customLegs.length === 0) return;
        const name = saveName.trim();
        if (!name && asNew) return;

        setSavingStrategy(true);
        try {
            const params = { ticker, amount, duration, downsideBuffer, upsideCap, quoteSource, bondYield, pricingMode };
            const legsForSave = customLegs.map(({ id, ...rest }) => rest);
            const snapshot = {
                currentPrice: result.currentPrice,
                expirationDate: result.expirationDate,
                actualDte: result.actualDte,
                durationDays: result.durationDays,
                parameters: result.parameters,
                ibkr_mode: result.ibkr_mode,
                actualStructureCost: result.actualStructureCost,
                legs: result.legs,
            };

            if (!asNew && loadedStrategyId) {
                await updateSavedStrategy(loadedStrategyId, {
                    name: name || undefined,
                    parameters: params,
                    legs_data: legsForSave,
                    result_snapshot: snapshot,
                });
            } else {
                const created = await saveStrategy({
                    strategy_type: 'dual_direction_buffer',
                    name,
                    ticker,
                    parameters: params,
                    legs_data: legsForSave,
                    result_snapshot: snapshot,
                });
                setLoadedStrategyId(created.id);
            }

            // Refresh list
            const list = await fetchSavedStrategies('dual_direction_buffer');
            setSavedStrategies(list);
            setShowSaveInput(false);
            setSaveName('');
        } catch (err: any) {
            setError(err.message || 'Failed to save strategy');
        } finally {
            setSavingStrategy(false);
        }
    };

    const handleLoadStrategy = (saved: SavedStrategyItem) => {
        const p = saved.parameters;
        setTicker(p.ticker || saved.ticker);
        setAmount(p.amount ?? 10000);
        setDuration(p.duration ?? 365);
        setDownsideBuffer(p.downsideBuffer ?? 15);
        setUpsideCap(p.upsideCap ?? 15);
        setQuoteSource(p.quoteSource ?? 'yfinance');
        setBondYield(p.bondYield ?? 5.0);
        setPricingMode(p.pricingMode ?? 'mid');

        // Reconstruct result from snapshot + legs
        const snap = saved.result_snapshot;
        setResult({
            success: true,
            ticker: saved.ticker,
            currentPrice: snap.currentPrice,
            investmentAmount: p.amount ?? 10000,
            durationDays: snap.durationDays ?? snap.actualDte ?? p.duration ?? 365,
            expirationDate: snap.expirationDate,
            actualDte: snap.actualDte,
            parameters: snap.parameters,
            legs: snap.legs || [],
            netOptionsPremium: 0,
            actualStructureCost: snap.actualStructureCost ?? 0,
            scenarios: [],
            ibkr_mode: snap.ibkr_mode,
        });

        // Populate custom legs with saved prices
        const mapped: CustomOptionLeg[] = saved.legs_data.map((leg: any, i: number) => ({
            id: `saved_${i}_${Math.random().toString(36).substr(2, 5)}`,
            action: leg.action,
            qty: leg.qty,
            type: leg.type,
            strike: leg.strike,
            price: leg.price,
            bid: leg.bid ?? 0,
            ask: leg.ask ?? 0,
            mid: leg.mid ?? leg.price ?? 0,
        }));
        setCustomLegs(mapped);
        setEvaluatedScenarios([]);
        setPayoffBounds(null);
        setLegsSnapshot('');
        setLoadedStrategyId(saved.id);
        setShowSavedList(false);
    };

    const handleDeleteStrategy = async (id: number) => {
        try {
            await deleteSavedStrategy(id);
            setSavedStrategies(prev => prev.filter(s => s.id !== id));
            if (loadedStrategyId === id) setLoadedStrategyId(null);
        } catch (err: any) {
            setError(err.message || 'Failed to delete');
        }
    };

    // ---------------------------------------------------------------------------
    // Strategy calculation
    // ---------------------------------------------------------------------------

    const calculateStrategy = async () => {
        if (!ticker) { setError('Please enter a ticker symbol'); return; }
        if (amount <= 0) { setError('Amount must be positive'); return; }

        setLoading(true);
        setError(null);
        setResult(null);
        setCustomLegs([]);
        setEvaluatedScenarios([]);
        setPayoffBounds(null);
        setPricingMode('mid');

        const params = {
            ticker,
            amount: Number(amount),
            duration_days: Number(duration),
            downside_buffer_pct: Number(downsideBuffer),
            upside_cap_pct: Number(upsideCap),
            ...(selectedExpiration ? { target_expiration: selectedExpiration } : {}),
        };

        try {
            const data = quoteSource === 'ibkr'
                ? await computeDualDirectionBufferIBKR(params)
                : await computeDualDirectionBuffer(params);
            if (data.error && !data.success) {
                setError(data.error);
            } else {
                setResult(data);
            }
        } catch (err: any) {
            setError(err.message || 'An unexpected error occurred');
        } finally {
            setLoading(false);
        }
    };

    // ---------------------------------------------------------------------------
    // IBKR Level 3 Spread Margin
    // ---------------------------------------------------------------------------

    const calcSpreadMargin = (legs: { action: string; type: string; strike: number; qty: number }[]): number => {
        const puts = legs.filter(l => l.type === 'Put');
        const calls = legs.filter(l => l.type === 'Call');
        // Net long equity, in contract-equivalents (1 qty = 100 shares = covers
        // one short call). Long stock collateralizes a short call (covered call
        // → no margin), so track it and consume it against short calls below.
        let longEquityContracts = legs
            .filter(l => l.type === 'Equity')
            .reduce((s, l) => s + (l.action === 'Buy' ? l.qty : -l.qty), 0);
        let margin = 0;

        const sellPuts = puts.filter(l => l.action === 'Sell').map(l => ({ ...l, remaining: l.qty }));
        const buyPuts = puts.filter(l => l.action === 'Buy').map(l => ({ ...l, remaining: l.qty }));
        sellPuts.sort((a, b) => b.strike - a.strike);
        for (const sp of sellPuts) {
            for (const bp of buyPuts) {
                if (sp.remaining <= 0) break;
                if (bp.remaining <= 0 || bp.strike <= sp.strike) continue;
                const paired = Math.min(sp.remaining, bp.remaining);
                sp.remaining -= paired; bp.remaining -= paired;
            }
            for (const bp of buyPuts) {
                if (sp.remaining <= 0) break;
                if (bp.remaining <= 0 || bp.strike >= sp.strike) continue;
                const paired = Math.min(sp.remaining, bp.remaining);
                margin += (sp.strike - bp.strike) * paired * 100;
                sp.remaining -= paired; bp.remaining -= paired;
            }
            if (sp.remaining > 0) margin += 0.20 * sp.strike * sp.remaining * 100;
        }

        const sellCalls = calls.filter(l => l.action === 'Sell').map(l => ({ ...l, remaining: l.qty }));
        const buyCalls = calls.filter(l => l.action === 'Buy').map(l => ({ ...l, remaining: l.qty }));
        sellCalls.sort((a, b) => a.strike - b.strike);
        for (const sc of sellCalls) {
            for (const bc of buyCalls) {
                if (sc.remaining <= 0) break;
                if (bc.remaining <= 0 || bc.strike > sc.strike) continue;
                const paired = Math.min(sc.remaining, bc.remaining);
                sc.remaining -= paired; bc.remaining -= paired;
            }
            // Long stock covers a short call fully (covered call → no margin),
            // just like a long lower-strike call would.
            if (sc.remaining > 0 && longEquityContracts > 0) {
                const covered = Math.min(sc.remaining, longEquityContracts);
                sc.remaining -= covered; longEquityContracts -= covered;
            }
            for (const bc of buyCalls) {
                if (sc.remaining <= 0) break;
                if (bc.remaining <= 0 || bc.strike <= sc.strike) continue;
                const paired = Math.min(sc.remaining, bc.remaining);
                margin += (bc.strike - sc.strike) * paired * 100;
                sc.remaining -= paired; bc.remaining -= paired;
            }
            if (sc.remaining > 0) margin += 0.20 * sc.strike * sc.remaining * 100;
        }

        return Math.round(margin * 100) / 100;
    };

    const ibkrMargin = useMemo(() => result ? calcSpreadMargin(result.legs) : 0, [result]);

    const customMargin = useMemo(() => calcSpreadMargin(customLegs), [customLegs]);

    const customTradeCost = useMemo(() =>
        customLegs.reduce((sum, leg) => sum + (leg.action === 'Buy' ? 1 : -1) * leg.price * leg.qty * 100, 0),
    [customLegs]);

    const customTotalDeployed = Math.round((Math.max(0, customTradeCost) + customMargin) * 100) / 100;

    // ---------------------------------------------------------------------------
    // Order legs for modal
    // ---------------------------------------------------------------------------

    const modalLegs: BrokerOrderLeg[] = customLegs
        .filter(l => l.type === 'Call' || l.type === 'Put')
        .map(leg => ({
            ticker,
            action: leg.action as 'Buy' | 'Sell',
            type: leg.type as 'Call' | 'Put',
            strike: leg.strike,
            qty: Math.max(1, Math.round(leg.qty)),  // IBKR requires integer qty >= 1
            expiration: result?.expirationDate ?? '',
            limit_price: leg.price,
        }));

    // ---------------------------------------------------------------------------
    // Render
    // ---------------------------------------------------------------------------

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="border-l-4 border-secondary bg-secondary/10 p-4 rounded-r-xl">
                <h3 className="font-bold flex items-center gap-2 text-secondary">
                    <Shield className="w-5 h-5" />
                    Dual Direction Buffer Strategy
                </h3>
                <p className="text-sm opacity-80 mt-1">
                    Constructs a 4-layer options structure to participate in upside (up to a cap) while
                    providing positive returns if the market drops within a specified buffer.
                </p>
            </div>

            {/* Input grid */}
            <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
                <div className="form-control">
                    <label className="label"><span className="label-text font-medium flex items-center gap-1">
                        <Layers className="w-4 h-4 text-primary" /> Ref Asset
                    </span></label>
                    <input type="text" className="input input-bordered w-full" value={ticker}
                        onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="e.g. SPY" />
                </div>
                <div className="form-control">
                    <label className="label"><span className="label-text font-medium flex items-center gap-1">
                        <Briefcase className="w-4 h-4 text-success" /> Invest ($)
                    </span></label>
                    <input type="number" className="input input-bordered w-full" value={amount}
                        onChange={(e) => setAmount(Number(e.target.value))} min={100} step={100} />
                </div>
                <div className="form-control">
                    <label className="label"><span className="label-text font-medium flex items-center gap-1">
                        <Timer className="w-4 h-4 text-warning" /> Days
                    </span></label>
                    <input type="number" className="input input-bordered w-full" value={duration}
                        onChange={(e) => setDuration(Number(e.target.value))} min={30} max={1000} step={30} />
                </div>
                <div className="form-control">
                    <label className="label"><span className="label-text font-medium flex items-center gap-1">
                        <TrendingDown className="w-4 h-4 text-error" /> Downside Buffer %
                    </span></label>
                    <input type="number" className="input input-bordered w-full" value={downsideBuffer}
                        onChange={(e) => setDownsideBuffer(Number(e.target.value))} min={0} max={50} />
                </div>
                <div className="form-control">
                    <label className="label"><span className="label-text font-medium flex items-center gap-1">
                        <TrendingUp className="w-4 h-4 text-success" /> Upside Cap %
                    </span></label>
                    <input type="number" className="input input-bordered w-full" value={upsideCap}
                        onChange={(e) => setUpsideCap(Number(e.target.value))} min={0} max={100} />
                </div>
            </div>

            {/* Quote source + Simulate */}
            <div className="flex flex-wrap items-end gap-6">
                <div className="form-control">
                    <label className="label py-1"><span className="label-text text-xs font-medium">Quote Source</span></label>
                    <div className="flex flex-col gap-1">
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input type="radio" name="ddb-quoteSource" className="radio radio-sm radio-info"
                                checked={quoteSource === 'yfinance'}
                                onChange={() => setQuoteSource('yfinance')} />
                            <Database className="w-3.5 h-3.5 text-base-content/60" />
                            <span className="text-sm">Yahoo Finance</span>
                            <span className="text-[10px] text-base-content/40">(free, delayed)</span>
                        </label>
                        <label className={`flex items-center gap-2 ${brokerConnected ? 'cursor-pointer' : 'opacity-50 cursor-not-allowed'}`}>
                            <input type="radio" name="ddb-quoteSource" className="radio radio-sm radio-info"
                                checked={quoteSource === 'ibkr'}
                                onChange={() => brokerConnected && setQuoteSource('ibkr')}
                                disabled={!brokerConnected} />
                            <Zap className="w-3.5 h-3.5 text-base-content/60" />
                            <span className="text-sm">IBKR</span>
                            {brokerConnected === null
                                ? <span className="text-[10px] text-base-content/40">(checking…)</span>
                                : brokerConnected
                                    ? <span className="text-[10px] text-base-content/40">(integer contracts)</span>
                                    : <span className="text-[10px] text-error/70">(not connected)</span>
                            }
                        </label>
                    </div>
                </div>

                {/* Expiration Date */}
                <div className="form-control">
                    <label className="label py-1"><span className="label-text text-xs font-medium">Expiration Date</span></label>
                    {loadingExpirations ? (
                        <div className="flex items-center gap-2 h-8 text-xs text-base-content/50">
                            <Loader2 className="w-3 h-3 animate-spin" /> Loading...
                        </div>
                    ) : availableExpirations.length > 0 ? (
                        <select
                            className="select select-bordered select-sm w-full"
                            value={selectedExpiration || ''}
                            onChange={(e) => setSelectedExpiration(e.target.value || null)}
                        >
                            <option value="">Auto (nearest monthly)</option>
                            {(monthlyExpirations.length > 0 ? monthlyExpirations : availableExpirations).map(exp => (
                                <option key={exp} value={exp}>{exp}</option>
                            ))}
                        </select>
                    ) : (
                        <div className="text-xs text-base-content/40 h-8 flex items-center">
                            {ticker.trim() ? 'No expirations' : 'Enter ticker'}
                        </div>
                    )}
                </div>

                <button className="btn btn-secondary" onClick={calculateStrategy} disabled={loading}>
                    {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Calculator className="w-5 h-5 mr-2" />}
                    Simulate Dual Direction
                </button>

                {/* Saved Strategies */}
                <div className="relative">
                    <button className="btn btn-outline btn-sm gap-1" onClick={() => setShowSavedList(!showSavedList)}>
                        <FolderOpen className="w-4 h-4" />
                        Saved ({savedStrategies.length})
                    </button>
                    {showSavedList && savedStrategies.length > 0 && (
                        <div className="absolute top-full mt-1 right-0 z-50 w-80 max-h-72 overflow-y-auto bg-base-100 border border-base-300 rounded-xl shadow-xl p-2 space-y-1">
                            {savedStrategies.map(s => (
                                <div key={s.id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg hover:bg-base-200 cursor-pointer group"
                                    onClick={() => handleLoadStrategy(s)}>
                                    <div className="flex-1 min-w-0">
                                        <div className="text-sm font-medium truncate">{s.name}</div>
                                        <div className="text-xs opacity-50">
                                            {s.ticker} &middot; {new Date(s.updated_at).toLocaleDateString()}
                                        </div>
                                    </div>
                                    <button
                                        className="btn btn-ghost btn-xs text-error opacity-0 group-hover:opacity-100"
                                        onClick={(e) => { e.stopPropagation(); handleDeleteStrategy(s.id); }}
                                    >
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

            {error && (
                <div className="alert alert-error text-sm">
                    <AlertCircle className="w-4 h-4" /> {error}
                </div>
            )}

            {/* ════════════════════════ RESULTS ════════════════════════ */}
            {result && (
                <div className="mt-4 space-y-6 animate-fade-in">

                    {result.ibkr_mode && (
                        <div className="flex items-center gap-2 text-xs text-info bg-info/10 border border-info/20 rounded-xl px-3 py-2 w-fit">
                            <Zap className="w-3.5 h-3.5" /> IBKR mode — contract quantities rounded to integers
                        </div>
                    )}

                    {/* ── Strategy Parameters + Recommended Legs (mid price only) ── */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* Strategy Parameters */}
                        <div className="glass-card">
                            <div className="p-5">
                                <h3 className="font-bold text-sm text-secondary mb-2">Strategy Parameters</h3>
                                <div className="flex justify-between items-center mb-1">
                                    <span className="text-sm opacity-70">Current {result.ticker} Price</span>
                                    <span className="font-semibold">${result.currentPrice.toFixed(2)}</span>
                                </div>
                                <div className="flex justify-between items-center mb-1">
                                    <span className="text-sm opacity-70">Expiration</span>
                                    <span className="font-semibold text-info">{result.expirationDate} ({result.actualDte} DTE)</span>
                                </div>
                                <div className="flex justify-between items-center mb-1">
                                    <span className="text-sm opacity-70">Actual Downside Buffer</span>
                                    <span className="font-semibold text-error">{result.parameters.actualDownsideBuffer.toFixed(1)}%</span>
                                </div>
                                <div className="flex justify-between items-center mb-4">
                                    <span className="text-sm opacity-70">Actual Upside Cap</span>
                                    <span className="font-semibold text-success">{result.parameters.actualUpsideCap.toFixed(1)}%</span>
                                </div>
                                <div className="divider my-0"></div>
                                <div className="mt-2 space-y-2">
                                    <div className="flex justify-between items-center text-sm">
                                        <span className="font-medium text-base-content/80">Total Trade Debit (mid)</span>
                                        <span className="font-mono font-bold text-error">
                                            ${result.actualStructureCost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                        </span>
                                    </div>
                                    {ibkrMargin > 0 && (
                                        <div className="flex justify-between items-center text-sm">
                                            <span className="font-medium text-base-content/80">Spread Margin (IBKR L3)</span>
                                            <span className="font-mono font-bold text-warning">
                                                ${ibkrMargin.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                            </span>
                                        </div>
                                    )}
                                    <div className="flex justify-between items-center text-sm border-t border-white/[0.05] pt-1">
                                        <span className="font-medium">Total Capital Required</span>
                                        <span className="font-mono font-bold text-error">
                                            ${(result.actualStructureCost + ibkrMargin).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Recommended Legs — mid price, no pricing controls */}
                        <div className="glass-card">
                            <div className="p-5">
                                <h3 className="font-bold text-sm text-info mb-2">Recommended Option Legs</h3>
                                <div className="space-y-3">
                                    {result.legs.map((leg, idx) => (
                                        <div key={idx} className="flex flex-col text-sm border-l-2 pl-3 border-white/[0.05]">
                                            {leg.layer && <span className="text-xs font-bold text-secondary mb-1">{leg.layer}</span>}
                                            <div className="flex justify-between items-baseline">
                                                <span className={`font-semibold ${leg.action === 'Buy' ? 'text-primary' : 'text-warning'}`}>
                                                    {leg.action} {leg.qty}x {leg.type} @ ${leg.strike.toFixed(2)}
                                                </span>
                                                <div className="flex items-center gap-2 text-xs">
                                                    {leg.bid !== undefined && (
                                                        <span className="opacity-50 font-mono">{leg.bid.toFixed(2)} / {leg.ask?.toFixed(2)}</span>
                                                    )}
                                                    <span className="font-mono font-bold opacity-70">${leg.midPrice.toFixed(2)}</span>
                                                </div>
                                            </div>
                                            <span className="text-xs opacity-60 mt-0.5">{leg.purpose}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* ══════════════════ CUSTOM EXECUTION ══════════════════ */}
                    <div className="glass-card">
                        <div className="p-5">
                            <div className="flex justify-between items-center mb-4">
                                <div>
                                    <h3 className="font-bold text-sm text-accent">Custom Execution</h3>
                                    <p className="text-xs opacity-70 mt-1">
                                        Edit legs, adjust pricing, then evaluate to see the payoff profile.
                                    </p>
                                </div>
                                <div className="flex gap-2 items-center text-sm flex-wrap justify-end">
                                    <button className="btn btn-sm btn-outline btn-info gap-1" onClick={autofillRecommended}>
                                        <Layers className="w-3 h-3" /> Autofill Recommended
                                    </button>
                                    <button className="btn btn-sm btn-outline btn-accent" onClick={addCustomLeg}>+ Add Leg</button>
                                    {customLegs.length > 0 && (
                                        <button
                                            className="btn btn-sm btn-outline btn-success gap-1"
                                            onClick={fetchLatestPrices}
                                            disabled={fetchingPrices || customLegs.filter(l => l.type !== 'Equity' && l.strike > 0).length === 0}
                                        >
                                            {fetchingPrices ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
                                            {fetchingPrices ? 'Fetching...' : 'Get Latest Prices'}
                                        </button>
                                    )}
                                </div>
                            </div>

                            {customLegs.length === 0 ? (
                                <div className="text-center py-4 bg-base-200/30 rounded-xl border border-dashed border-white/[0.05]">
                                    <span className="text-sm opacity-50">
                                        No custom legs. Click &apos;Autofill Recommended&apos; or &apos;+ Add Leg&apos; to start.
                                    </span>
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    {/* Column headers */}
                                    <div className="hidden md:grid grid-cols-12 gap-2 text-xs font-bold opacity-60 px-2">
                                        <div className="col-span-2">Action</div>
                                        <div className="col-span-1">Qty</div>
                                        <div className="col-span-2">Type</div>
                                        <div className="col-span-2">Strike ($)</div>
                                        <div className="col-span-2">Bid / Ask</div>
                                        <div className="col-span-2">Unit Price ($)</div>
                                        <div className="col-span-1"></div>
                                    </div>
                                    {/* Leg rows */}
                                    {customLegs.map(leg => (
                                        <div key={leg.id} className="grid grid-cols-1 md:grid-cols-12 gap-2 items-center bg-base-200/30 p-2 rounded-lg border border-base-200">
                                            <div className="col-span-2">
                                                <select className="select select-sm select-bordered w-full" value={leg.action}
                                                    onChange={e => updateCustomLeg(leg.id, 'action', e.target.value)}>
                                                    <option>Buy</option><option>Sell</option>
                                                </select>
                                            </div>
                                            <div className="col-span-1">
                                                <input type="number" className="input input-sm input-bordered w-full" value={leg.qty}
                                                    onChange={e => updateCustomLeg(leg.id, 'qty', Number(e.target.value))} min={1} />
                                            </div>
                                            <div className="col-span-2">
                                                <select className="select select-sm select-bordered w-full" value={leg.type}
                                                    onChange={e => updateCustomLeg(leg.id, 'type', e.target.value)}>
                                                    <option>Call</option><option>Put</option><option>Equity</option>
                                                </select>
                                            </div>
                                            <div className="col-span-2">
                                                <input type="number" step="0.5" className="input input-sm input-bordered w-full" value={leg.strike}
                                                    onChange={e => updateCustomLeg(leg.id, 'strike', Number(e.target.value))} />
                                            </div>
                                            <div className="col-span-2 text-xs font-mono opacity-50 flex items-center gap-1 px-1">
                                                {leg.bid > 0 ? `${leg.bid.toFixed(2)} / ${leg.ask.toFixed(2)}` : '—'}
                                            </div>
                                            <div className="col-span-2">
                                                <input type="number" step="0.01" className="input input-sm input-bordered w-full font-mono" value={leg.price}
                                                    onChange={e => updateCustomLeg(leg.id, 'price', Number(e.target.value))} />
                                            </div>
                                            <div className="col-span-1 flex justify-end">
                                                <button className="btn btn-sm btn-ghost text-error" onClick={() => removeCustomLeg(leg.id)}>✕</button>
                                            </div>
                                        </div>
                                    ))}

                                    {/* ── Pricing Mode Selector ── */}
                                    <div className="rounded-xl border border-primary/20 bg-primary/5 p-3 mt-3 space-y-2">
                                        <h4 className="text-xs font-semibold flex items-center gap-1.5">
                                            <Sliders className="w-3.5 h-3.5 text-primary" /> Order Pricing
                                        </h4>
                                        <div className="flex flex-wrap gap-1.5">
                                            <button className={`btn btn-xs gap-1 ${pricingMode === 'low' ? 'btn-success' : 'btn-outline'}`}
                                                onClick={() => applyPricingMode('low')}>Low (Conservative)</button>
                                            <button className={`btn btn-xs gap-1 ${pricingMode === 'mid' ? 'btn-info' : 'btn-outline'}`}
                                                onClick={() => applyPricingMode('mid')}>Mid (Default)</button>
                                            <button className={`btn btn-xs gap-1 ${pricingMode === 'high' ? 'btn-warning' : 'btn-outline'}`}
                                                onClick={() => applyPricingMode('high')}>High (Aggressive)</button>
                                            <button className={`btn btn-xs gap-1 ${pricingMode === 'smart' ? 'btn-secondary' : 'btn-outline'}`}
                                                onClick={() => applyPricingMode('smart')}><Sparkles className="w-3 h-3" /> Smart</button>
                                            <button className={`btn btn-xs gap-1 ${pricingMode === 'custom' ? 'btn-accent' : 'btn-outline'}`}
                                                onClick={() => setPricingMode('custom')}><Edit3 className="w-3 h-3" /> Custom</button>
                                        </div>
                                        {pricingMode === 'custom' && (
                                            <div className="flex items-center gap-2">
                                                <span className="text-xs text-base-content/60">Total net debit target ($):</span>
                                                <input type="number" step="100" className="input input-bordered input-xs w-32 font-mono"
                                                    value={customNetDebit} onChange={e => setCustomNetDebit(e.target.value)}
                                                    placeholder={String(Math.round(customTradeCost))} />
                                                <button className="btn btn-xs btn-accent" onClick={() => applyPricingMode('custom')}>Apply</button>
                                            </div>
                                        )}
                                    </div>

                                    {/* ── Totals ── */}
                                    <div className="flex flex-col items-end gap-1 mt-4 text-sm font-semibold">
                                        <div>
                                            Trade Net Cost:{' '}
                                            <span className={customTradeCost > 0 ? 'text-error' : 'text-success'}>
                                                {customTradeCost > 0
                                                    ? `Debit $${customTradeCost.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                                                    : `Credit $${Math.abs(customTradeCost).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                                            </span>
                                        </div>
                                        {customMargin > 0 && (
                                            <div>
                                                Spread Margin (IBKR L3):{' '}
                                                <span className="text-warning">${customMargin.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                                            </div>
                                        )}
                                        <div className="border-t border-white/[0.05] pt-1 mt-1">
                                            Total Capital Deployed:{' '}
                                            <span className="text-error">${customTotalDeployed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                                        </div>
                                        {customMargin > 0 && (
                                            <div className="flex items-center gap-2 mt-1 text-xs font-normal opacity-80">
                                                <span>Yield on Cash (margin):</span>
                                                <input
                                                    type="number"
                                                    className="input input-bordered input-xs w-16 font-mono text-right"
                                                    value={bondYield}
                                                    onChange={e => setBondYield(Number(e.target.value))}
                                                    min={0}
                                                    max={20}
                                                    step={0.1}
                                                />
                                                <span>%</span>
                                                <span className="text-success">
                                                    +${(customMargin * (bondYield / 100) * ((result?.actualDte ?? result?.durationDays ?? duration) / 365)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                                </span>
                                                <span className="opacity-60">
                                                    ({result?.actualDte ?? result?.durationDays ?? duration}d)
                                                </span>
                                            </div>
                                        )}
                                    </div>

                                    {/* ── Evaluate + Save Buttons ── */}
                                    <div className="mt-4 flex justify-between items-center">
                                        {/* Save controls (left) */}
                                        <div className="flex items-center gap-2">
                                            {showSaveInput ? (
                                                <>
                                                    <input
                                                        type="text" className="input input-sm input-bordered w-48"
                                                        placeholder="Strategy name..."
                                                        value={saveName}
                                                        onChange={e => setSaveName(e.target.value)}
                                                        onKeyDown={e => e.key === 'Enter' && saveName.trim() && handleSaveStrategy(true)}
                                                        autoFocus
                                                    />
                                                    <button
                                                        className="btn btn-sm btn-success gap-1"
                                                        onClick={() => handleSaveStrategy(true)}
                                                        disabled={!saveName.trim() || savingStrategy}
                                                    >
                                                        {savingStrategy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                                                        Save
                                                    </button>
                                                    <button className="btn btn-sm btn-ghost" onClick={() => setShowSaveInput(false)}>Cancel</button>
                                                </>
                                            ) : (
                                                <>
                                                    <button
                                                        className="btn btn-sm btn-outline btn-success gap-1"
                                                        onClick={() => { setShowSaveInput(true); setSaveName(''); }}
                                                    >
                                                        <Save className="w-3 h-3" /> Save as New
                                                    </button>
                                                    {loadedStrategyId && (
                                                        <button
                                                            className="btn btn-sm btn-outline btn-info gap-1"
                                                            onClick={() => {
                                                                const loaded = savedStrategies.find(s => s.id === loadedStrategyId);
                                                                setSaveName(loaded?.name ?? '');
                                                                handleSaveStrategy(false);
                                                            }}
                                                            disabled={savingStrategy}
                                                        >
                                                            {savingStrategy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                                                            Update
                                                        </button>
                                                    )}
                                                </>
                                            )}
                                        </div>

                                        {/* Evaluate (right) */}
                                        <button
                                            className={`btn gap-2 ${hasUnevaluatedChanges ? 'btn-secondary' : 'btn-outline btn-secondary'}`}
                                            onClick={evaluateOutcomes}
                                            disabled={!hasUnevaluatedChanges && evaluatedScenarios.length > 0}
                                        >
                                            <Calculator className="w-4 h-4" />
                                            {hasUnevaluatedChanges ? 'Evaluate Outcomes' : evaluatedScenarios.length > 0 ? 'Up to Date' : 'Evaluate Outcomes'}
                                        </button>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* ══════════════════ BUFFER/CAP WARNINGS ══════════════════ */}
                    {result.bufferCapWarnings && result.bufferCapWarnings.length > 0 && (
                        <div className="glass-card border-warning/30">
                            <div className="p-4 flex items-start gap-3">
                                <AlertCircle className="w-5 h-5 text-warning shrink-0 mt-0.5" />
                                <div>
                                    <h4 className="font-bold text-sm text-warning mb-1">Buffer/Cap Deviation</h4>
                                    {result.bufferCapWarnings.map((w, i) => (
                                        <p key={i} className="text-xs opacity-80">{w}</p>
                                    ))}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* ══════════════════ PAYOFF SCENARIOS ══════════════════ */}
                    {evaluatedScenarios.length > 0 && (() => {
                        // True max profit/loss = the strategy's real plateaus (computed
                        // from the leg strikes), falling back to the scanned window.
                        const maxProfitScenario = payoffBounds?.maxGain ?? evaluatedScenarios.reduce((best, s) => s.roi > best.roi ? s : best, evaluatedScenarios[0]);
                        const maxLossScenario = payoffBounds?.maxLoss ?? evaluatedScenarios.reduce((worst, s) => s.roi < worst.roi ? s : worst, evaluatedScenarios[0]);
                        const gainOnsetPct = payoffBounds?.gainOnsetPct ?? maxProfitScenario.underlyingChangePct;
                        const lossOnsetPct = payoffBounds?.lossOnsetPct ?? maxLossScenario.underlyingChangePct;
                        const upsideCapped = payoffBounds?.upsideCapped ?? false;
                        const downsideFloored = payoffBounds?.downsideFloored ?? false;
                        // Guard against nonsensical "Max Loss" labels when the payoff is flat
                        // (a conversion/box locks one number at every price) or one-sided (the
                        // worst case is still a gain, or the best case is still a loss).
                        const pnlSpan = Math.abs(maxProfitScenario.pnl - maxLossScenario.pnl);
                        const flatTol = Math.max(2, Math.abs(customTotalDeployed || maxProfitScenario.pnl) * 0.0005);
                        const isFlatPayoff = pnlSpan < flatTol;
                        const worstIsProfit = maxLossScenario.pnl >= 0;   // no scenario loses money
                        const bestIsLoss = maxProfitScenario.pnl <= 0;    // no scenario makes money
                        const money0 = (v: number) => `${v >= 0 ? '+' : '−'}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
                        const crossoverScenarios = evaluatedScenarios.filter(s => s.isCrossover);
                        // Chart: all points (smooth curve) minus crossover-inserted rows
                        const chartScenarios = evaluatedScenarios.filter(s => !s.isCrossover);
                        // Table: 5% intervals near the money, 10% out wide, + crossover breakeven rows
                        const majorPcts = new Set([-50, -40, -30, -25, -20, -15, -10, -5, 0, 5, 10, 15, 20, 25, 30, 40, 50]);
                        const tableScenarios = evaluatedScenarios.filter(s =>
                            s.isCrossover || majorPcts.has(s.underlyingChangePct)
                        );
                        const baseMarginValue = evaluatedScenarios[0]?.margin ?? 0;
                        const hasCashYield = evaluatedScenarios[0]?.marginInterest > 0;

                        return (
                        <div className="glass-card">
                            <div className="p-5">
                                <h3 className="font-bold text-sm mb-4 flex items-center gap-2">
                                    <TrendingUp className="w-5 h-5 text-secondary" />
                                    Payoff Scenarios at Expiration ({result.expirationDate})
                                </h3>

                                {/* ── Summary Cards ── */}
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
                                    {isFlatPayoff ? (
                                        <div className={`col-span-2 rounded-xl p-3 text-center border ${maxProfitScenario.pnl >= 0 ? 'bg-info/10 border-info/20' : 'bg-error/10 border-error/20'}`}>
                                            <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Locked P&amp;L — flat payoff</div>
                                            <div className={`text-lg font-bold ${maxProfitScenario.pnl >= 0 ? 'text-info' : 'text-error'}`}>{money0(maxProfitScenario.pnl)}</div>
                                            <div className="text-[10px] opacity-50">{maxProfitScenario.roi >= 0 ? '+' : ''}{maxProfitScenario.roi.toFixed(1)}% · same at every price</div>
                                            <div className="text-[10px] text-info/80 mt-0.5">No directional risk — a conversion/box. Verify the legs aren&apos;t mispriced.</div>
                                        </div>
                                    ) : (<>
                                    <div className={`rounded-xl p-3 text-center border ${bestIsLoss ? 'bg-error/10 border-error/20' : 'bg-success/10 border-success/20'}`}>
                                        <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">{bestIsLoss ? 'Best Case (still a loss)' : 'Max Profit'}</div>
                                        <div className={`text-lg font-bold ${bestIsLoss ? 'text-error' : 'text-success'}`}>{maxProfitScenario.roi >= 0 ? '+' : ''}{maxProfitScenario.roi.toFixed(1)}%</div>
                                        <div className="text-[10px] opacity-50">
                                            at {gainOnsetPct > 0 ? '+' : ''}{gainOnsetPct}%{upsideCapped ? ' & above' : ''}
                                        </div>
                                        <div className={`text-xs mt-0.5 ${bestIsLoss ? 'text-error' : 'text-success'}`}>{money0(maxProfitScenario.pnl)}</div>
                                    </div>
                                    <div className={`rounded-xl p-3 text-center border ${worstIsProfit ? 'bg-success/10 border-success/20' : 'bg-error/10 border-error/20'}`}>
                                        <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">{worstIsProfit ? 'Worst Case (still a gain)' : 'Max Loss'}</div>
                                        <div className={`text-lg font-bold ${worstIsProfit ? 'text-success' : 'text-error'}`}>{maxLossScenario.roi >= 0 ? '+' : ''}{maxLossScenario.roi.toFixed(1)}%</div>
                                        <div className="text-[10px] opacity-50">
                                            at {lossOnsetPct > 0 ? '+' : ''}{lossOnsetPct}%{downsideFloored ? ' & below' : ''}
                                        </div>
                                        <div className={`text-xs mt-0.5 ${worstIsProfit ? 'text-success' : 'text-error'}`}>{money0(maxLossScenario.pnl)}</div>
                                    </div>
                                    </>)}
                                    <div className="rounded-xl bg-base-200/30 border border-base-content/10 p-3 text-center">
                                        <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Breakeven</div>
                                        {crossoverScenarios.length > 0 ? crossoverScenarios.map((c, i) => (
                                            <div key={i} className="text-sm font-bold">
                                                {c.underlyingChangePct > 0 ? '+' : ''}{c.underlyingChangePct}%
                                                <span className="text-[10px] font-normal opacity-50 ml-1">(${c.simulatedPrice.toFixed(0)})</span>
                                            </div>
                                        )) : <div className="text-sm opacity-50">None in range</div>}
                                    </div>
                                    <div className="rounded-xl bg-base-200/30 border border-base-content/10 p-3 text-center">
                                        <div className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Margin Req.</div>
                                        <div className="text-lg font-bold">${baseMarginValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
                                        <div className="text-[10px] opacity-50">IBKR L3 Spread</div>
                                    </div>
                                </div>

                                {/* ── Max gain / loss explainer ── */}
                                <p className="text-[11px] opacity-60 -mt-2 mb-4 flex items-start gap-1.5">
                                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-px" />
                                    <span>
                                        Max profit and max loss are the strategy&apos;s true bounds — the levels where the
                                        payoff plateaus and stops changing — not just the value at the edge of the scanned range.
                                        They&apos;re drawn as dashed reference lines on the chart below.
                                    </span>
                                </p>

                                {/* ── Chart: smooth payoff curve with max gain/loss bounds ── */}
                                <div className="h-64 sm:h-80 w-full mb-6 relative">
                                    <Line
                                        data={{
                                            labels: chartScenarios.map(s => `${s.underlyingChangePct > 0 ? '+' : ''}${s.underlyingChangePct}%`),
                                            datasets: [
                                                {
                                                    label: 'Strategy ROI (%)',
                                                    data: chartScenarios.map(s => s.roi),
                                                    borderColor: 'rgba(99, 102, 241, 1)',
                                                    backgroundColor: (ctx: any) => {
                                                        const chart = ctx.chart;
                                                        const { ctx: c, chartArea } = chart;
                                                        if (!chartArea) return 'rgba(99, 102, 241, 0.2)';
                                                        const gradient = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
                                                        gradient.addColorStop(0, 'rgba(34, 197, 94, 0.12)');
                                                        gradient.addColorStop(0.5, 'rgba(99, 102, 241, 0.03)');
                                                        gradient.addColorStop(1, 'rgba(239, 68, 68, 0.12)');
                                                        return gradient;
                                                    },
                                                    borderWidth: 2.5,
                                                    fill: true,
                                                    tension: 0.3,
                                                    pointRadius: chartScenarios.map(s =>
                                                        s.underlyingChangePct % 10 === 0 ? 4 : s.underlyingChangePct % 5 === 0 ? 3 : 0
                                                    ),
                                                    pointBackgroundColor: chartScenarios.map(s =>
                                                        s.roi > 0 ? '#22c55e' : s.roi < 0 ? '#ef4444' : '#9ca3af'
                                                    ),
                                                },
                                                {
                                                    label: 'Underlying (%)',
                                                    data: chartScenarios.map(s => s.underlyingChangePct),
                                                    borderColor: 'rgba(156, 163, 175, 0.4)',
                                                    borderWidth: 1.5,
                                                    borderDash: [5, 5],
                                                    fill: false,
                                                    pointRadius: 0,
                                                },
                                                {
                                                    label: `Max Gain +${maxProfitScenario.roi.toFixed(1)}%`,
                                                    data: chartScenarios.map(() => maxProfitScenario.roi),
                                                    borderColor: 'rgba(34, 197, 94, 0.55)',
                                                    borderWidth: 1.5,
                                                    borderDash: [4, 4],
                                                    fill: false,
                                                    pointRadius: 0,
                                                },
                                                {
                                                    label: `Max Loss ${maxLossScenario.roi.toFixed(1)}%`,
                                                    data: chartScenarios.map(() => maxLossScenario.roi),
                                                    borderColor: 'rgba(239, 68, 68, 0.55)',
                                                    borderWidth: 1.5,
                                                    borderDash: [4, 4],
                                                    fill: false,
                                                    pointRadius: 0,
                                                },
                                            ],
                                        }}
                                        options={{
                                            responsive: true,
                                            maintainAspectRatio: false,
                                            interaction: { mode: 'index', intersect: false },
                                            plugins: {
                                                legend: { position: 'top', labels: { usePointStyle: true, boxWidth: 8 } },
                                                tooltip: {
                                                    callbacks: {
                                                        label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(2) ?? '0.00'}%`,
                                                    },
                                                },
                                            },
                                            scales: {
                                                y: {
                                                    title: { display: true, text: 'ROI (%)' },
                                                    grid: { color: 'rgba(156,163,175,0.1)' },
                                                },
                                                x: {
                                                    title: { display: true, text: 'Underlying Performance' },
                                                    grid: { display: false },
                                                    ticks: {
                                                        callback: function(_value: any, index: number) {
                                                            const pct = chartScenarios[index]?.underlyingChangePct;
                                                            return pct !== undefined && pct % 10 === 0 ? `${pct > 0 ? '+' : ''}${pct}%` : '';
                                                        },
                                                        maxRotation: 0,
                                                    },
                                                },
                                            },
                                        }}
                                    />
                                </div>

                                {/* ── Table: major 5% intervals + breakeven rows only ── */}
                                <div className="overflow-x-auto">
                                    <table className="table table-pro w-full text-center text-sm">
                                        <thead>
                                            <tr>
                                                <th className="bg-base-200/40">Market</th>
                                                <th className="bg-base-200/40">Price</th>
                                                <th className="bg-base-200/40">Options Payout</th>
                                                {hasCashYield && <th className="bg-base-200/40">Cash Yield</th>}
                                                <th className="bg-base-200/40">P&L</th>
                                                <th className="bg-base-200/40">ROI</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {tableScenarios.map((s, idx) => (
                                                <tr
                                                    key={idx}
                                                    className={
                                                        s.isCrossover
                                                            ? 'bg-warning/10 border-l-2 border-l-warning'
                                                            : s.roi > 0 ? 'bg-success/5' : ''
                                                    }
                                                >
                                                    <td className="font-medium">
                                                        <span className={`inline-flex items-center gap-1 ${
                                                            s.isCrossover ? 'text-warning font-semibold' :
                                                            s.underlyingChangePct < 0 ? 'text-error' :
                                                            s.underlyingChangePct > 0 ? 'text-success' : ''
                                                        }`}>
                                                            {s.isCrossover ? (
                                                                <Zap className="w-3.5 h-3.5" />
                                                            ) : s.underlyingChangePct < 0 ? (
                                                                <TrendingDown className="w-3.5 h-3.5" />
                                                            ) : s.underlyingChangePct > 0 ? (
                                                                <TrendingUp className="w-3.5 h-3.5" />
                                                            ) : null}
                                                            {s.underlyingChangePct > 0 ? '+' : ''}{s.underlyingChangePct}%
                                                            {s.isCrossover && <span className="text-[10px] opacity-70 ml-1">breakeven</span>}
                                                        </span>
                                                    </td>
                                                    <td>${s.simulatedPrice.toFixed(2)}</td>
                                                    <td className="font-semibold">
                                                        ${s.optionsPayout.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                                                    </td>
                                                    {hasCashYield && (
                                                        <td className="text-success">
                                                            +${s.marginInterest.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                                                        </td>
                                                    )}
                                                    <td className={`font-bold ${s.pnl > 0 ? 'text-success' : s.pnl < 0 ? 'text-error' : 'opacity-50'}`}>
                                                        {s.pnl > 0 ? '+' : ''}${Math.abs(s.pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                                                        {s.pnl < 0 && <span className="text-[10px] ml-0.5">loss</span>}
                                                    </td>
                                                    <td className={`font-bold ${s.roi > 0 ? 'text-success' : s.roi === 0 ? 'opacity-50' : 'text-error'}`}>
                                                        {s.roi > 0 ? '+' : ''}{s.roi.toFixed(2)}%
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                        );
                    })()}

                    {/* ══════════════════ DESK REVIEW (pre-trade) ══════════════════ */}
                    {evaluatedScenarios.length > 0 && payoffBounds && (() => {
                        const mg = payoffBounds.maxGain, ml = payoffBounds.maxLoss;
                        const money = (v: number) => `$${Math.round(v).toLocaleString()}`;
                        const pctStr = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
                        const legsForAdvisor = customLegs.map(l => ({
                            action: l.action, qty: l.qty, type: l.type, strike: l.strike,
                            expiration: result.expirationDate,
                        }));

                        // Understand the WHOLE profile — not just max gain vs the deep-tail max loss.
                        const scenarios = evaluatedScenarios
                            .filter(s => !s.isCrossover && s.underlyingChangePct % 5 === 0)
                            .map(s => ({ move_pct: s.underlyingChangePct, price: s.simulatedPrice, roi: s.roi, pnl: s.pnl }));
                        // Breakevens (where ROI crosses 0) and the nearest downside one = the real cushion.
                        const crossovers = evaluatedScenarios.filter(s => s.isCrossover);
                        const downBE = crossovers.filter(c => c.underlyingChangePct < 0)
                            .sort((a, b) => b.underlyingChangePct - a.underlyingChangePct)[0];
                        const cushionPct = downBE ? Math.abs(downBE.underlyingChangePct) : null;
                        // Loss at a plausible adverse move (-20%) — the realistic downside, vs the tail.
                        const stress = evaluatedScenarios.find(s => !s.isCrossover && Math.round(s.underlyingChangePct) === -20);

                        const quantMetrics: AdvisorMetric[] = [
                            { label: 'Max Gain', value: pctStr(mg.roi), tone: 'good', hint: 'Capped upside (payoff plateau)' },
                            { label: 'Max Loss', value: pctStr(ml.roi), tone: 'bad', hint: 'Deep-tail loss — only near a total collapse (underlying → $0)' },
                            { label: 'Breakeven', value: downBE ? pctStr(downBE.underlyingChangePct) : '—', hint: 'Underlying move where the trade turns negative' },
                            { label: 'Downside Cushion', value: cushionPct != null ? `${cushionPct.toFixed(1)}%` : '—', tone: cushionPct != null && cushionPct >= 10 ? 'good' : 'warn', hint: 'How far the underlying can fall before any loss' },
                            { label: 'Loss @ −20%', value: stress ? pctStr(stress.roi) : '—', tone: stress && stress.roi >= -10 ? 'good' : 'warn', hint: 'Realistic adverse-move loss (vs the deep tail)' },
                            { label: 'Down Buffer', value: `${result.parameters.actualDownsideBuffer.toFixed(1)}%` },
                            { label: 'Up Cap', value: `${result.parameters.actualUpsideCap.toFixed(1)}%` },
                            { label: 'Capital', value: money(customTotalDeployed) },
                        ];
                        // Verdict from the SHAPE (cushion + capped gain), not max-gain ÷ tail-loss.
                        const tone: 'good' | 'warn' | 'bad' =
                            cushionPct != null && cushionPct >= 12 && mg.roi > 0 ? 'good'
                            : cushionPct != null && cushionPct >= 6 ? 'warn' : 'bad';
                        const quantVerdict = {
                            label: tone === 'good' ? 'Wide buffer — favorable shape'
                                : tone === 'warn' ? 'Usable buffer — mind the tail' : 'Thin buffer / poor shape',
                            tone,
                            note: `Profitable on any move above ${downBE ? pctStr(downBE.underlyingChangePct) : 'breakeven'}, capped at ${pctStr(mg.roi)}. `
                                + `Max loss ${pctStr(ml.roi)} only near a collapse — a −20% move is just ${stress ? pctStr(stress.roi) : 'modest'}.`,
                        };
                        const llmMetrics = {
                            current_price: result.currentPrice,
                            max_gain_pct: mg.roi, 'max_gain_$': Math.round(mg.pnl),
                            'max_loss_pct (deep tail, underlying→0)': ml.roi, 'max_loss_$': Math.round(ml.pnl),
                            downside_breakeven_pct: downBE ? downBE.underlyingChangePct : null,
                            downside_cushion_pct: cushionPct,
                            'loss_at_-20pct_move_pct': stress ? stress.roi : null,
                            total_capital: Math.round(customTotalDeployed),
                            spread_margin: Math.round(customMargin),
                            net_debit: Math.round(customTradeCost),
                            downside_buffer_pct: result.parameters.actualDownsideBuffer,
                            upside_cap_pct: result.parameters.actualUpsideCap,
                            dte: result.actualDte,
                            expiration: result.expirationDate,
                        };
                        const stockShares = customLegs
                            .filter(l => l.type === 'Equity')
                            .reduce((s, l) => s + (l.action === 'Buy' ? 1 : -1) * l.qty * 100, 0);
                        return (
                            <PreTradeAdvisor
                                ticker={ticker}
                                strategyType="dual_direction_buffer"
                                legs={legsForAdvisor}
                                llmMetrics={llmMetrics}
                                scenarios={scenarios}
                                breakevens={crossovers.map(c => `${pctStr(c.underlyingChangePct)} (≈$${c.simulatedPrice.toFixed(0)})`)}
                                expiration={result.expirationDate}
                                spot={result.currentPrice}
                                capital={customTotalDeployed}
                                dte={result.actualDte}
                                stockShares={stockShares}
                                maxLoss={Math.round(ml.pnl)}
                                maxProfit={Math.round(mg.pnl)}
                                quantMetrics={quantMetrics}
                                quantVerdict={quantVerdict}
                                notes={`Dual Direction Buffer: stays positive on drops within a ${result.parameters.actualDownsideBuffer.toFixed(1)}% buffer, participates up to a +${result.parameters.actualUpsideCap.toFixed(1)}% cap. The max loss only occurs near a near-total collapse of the underlying, not on ordinary moves.`}
                            />
                        );
                    })()}

                    {/* ══════════════════ PLACE ORDER ══════════════════ */}
                    {customLegs.length > 0 && evaluatedScenarios.length > 0 && (
                        <div className="glass-card border border-primary/10">
                            <div className="p-5">
                                {/* Header */}
                                <div className="flex items-center justify-between mb-4">
                                    <h4 className="text-sm font-bold flex items-center gap-2">
                                        <Send className="w-4 h-4 text-primary" />
                                        Place Order via IBKR
                                    </h4>
                                    <div className="flex items-center gap-2">
                                        <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full ${
                                            brokerConnected ? 'bg-success/10 text-success' : 'bg-base-300 text-base-content/40'
                                        }`}>
                                            <span className={`w-1.5 h-1.5 rounded-full ${brokerConnected ? 'bg-success' : 'bg-base-content/30'}`} />
                                            {brokerConnected ? 'IBKR Connected' : 'Not Connected'}
                                        </span>
                                        <span className="text-[10px] text-base-content/40 font-mono">{result.expirationDate}</span>
                                    </div>
                                </div>

                                {/* Order Legs Table */}
                                <div className="rounded-lg border border-base-content/5 overflow-hidden mb-3">
                                    <table className="table table-xs w-full">
                                        <thead>
                                            <tr className="bg-base-200/40 text-[10px] uppercase tracking-wider">
                                                <th className="py-2">Action</th>
                                                <th className="py-2">Qty</th>
                                                <th className="py-2">Type</th>
                                                <th className="py-2">Strike</th>
                                                <th className="py-2">Limit Price</th>
                                                <th className="py-2 text-right">Cost</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {modalLegs.map((leg, i) => {
                                                const legCost = (leg.action === 'Buy' ? -1 : 1) * leg.limit_price * leg.qty * 100;
                                                return (
                                                    <tr key={i} className="border-t border-base-content/5">
                                                        <td className="py-1.5">
                                                            <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                                                                leg.action === 'Buy'
                                                                    ? 'bg-success/10 text-success'
                                                                    : 'bg-error/10 text-error'
                                                            }`}>
                                                                {leg.action.toUpperCase()}
                                                            </span>
                                                        </td>
                                                        <td className="font-mono text-xs">{leg.qty}</td>
                                                        <td className="text-xs">{leg.type}</td>
                                                        <td className="font-mono text-xs">${leg.strike.toFixed(2)}</td>
                                                        <td className="font-mono text-xs text-primary">${leg.limit_price.toFixed(2)}</td>
                                                        <td className={`font-mono text-xs text-right font-semibold ${legCost < 0 ? 'text-error' : 'text-success'}`}>
                                                            {legCost < 0 ? '-' : '+'}${Math.abs(legCost).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>

                                {/* Net Cost Summary — computed from modalLegs (rounded integer qty) */}
                                {(() => {
                                    const orderNetCost = modalLegs.reduce((sum, leg) =>
                                        sum + (leg.action === 'Buy' ? -1 : 1) * leg.limit_price * leg.qty * 100, 0);
                                    const isDebit = orderNetCost < 0;
                                    return (
                                <div className="bg-base-200/30 rounded-lg p-3 space-y-1.5 mb-4">
                                    <div className="flex justify-between items-center">
                                        <span className="text-sm font-semibold">Net {isDebit ? 'Debit' : 'Credit'}</span>
                                        <span className={`font-mono text-base font-bold ${isDebit ? 'text-error' : 'text-success'}`}>
                                            ${Math.abs(orderNetCost).toLocaleString(undefined, { minimumFractionDigits: 2 })}
                                        </span>
                                    </div>
                                    {customMargin > 0 && (
                                        <div className="flex justify-between items-center text-sm">
                                            <span className="opacity-60">Spread Margin</span>
                                            <span className="font-mono text-warning">${customMargin.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                                        </div>
                                    )}
                                    {customMargin > 0 && (
                                        <div className="flex justify-between items-center text-sm border-t border-base-content/5 pt-1.5">
                                            <span className="font-medium">Total Capital</span>
                                            <span className="font-mono font-bold">${(Math.abs(orderNetCost) + customMargin).toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
                                        </div>
                                    )}
                                </div>
                                    );
                                })()}

                                {/* Action Button */}
                                {brokerConnected ? (
                                    <button
                                        className="btn btn-primary w-full gap-2 rounded-xl"
                                        onClick={() => setShowOrderModal(true)}
                                        disabled={modalLegs.length === 0}
                                    >
                                        <ShieldAlert className="w-4 h-4" />
                                        Review & Place Order
                                    </button>
                                ) : (
                                    <div className="text-center space-y-2">
                                        <p className="text-xs text-base-content/50">Connect to IBKR above to place orders directly.</p>
                                        <p className="text-[10px] text-base-content/30">Or use "Track External Order" below to log a trade placed elsewhere.</p>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Mark as Placed (external order tracking) */}
                    {customLegs.length > 0 && (
                        <div className="glass-card">
                            <div className="p-5">
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
                                            if (!loadedStrategyId) return;
                                            setMarkingAsPlaced(true);
                                            try {
                                                const entryPrices = customLegs.filter(l => l.type !== 'Equity').map(l => ({
                                                    strike: l.strike, type: l.type, price: l.price,
                                                }));
                                                const netDebit = customLegs.filter(l => l.type !== 'Equity').reduce((sum, l) =>
                                                    sum + (l.action === 'Buy' ? 1 : -1) * l.price * l.qty * 100, 0);
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
                        </div>
                    )}
                </div>
            )}

            {/* Order Confirmation Modal */}
            {showOrderModal && result && (
                <OrderConfirmationModal
                    ticker={ticker}
                    strategy="dual_direction_buffer"
                    legs={modalLegs}
                    pricingMode={pricingMode}
                    savedStrategyId={loadedStrategyId ?? undefined}
                    onClose={() => setShowOrderModal(false)}
                    onSuccess={() => setShowOrderModal(false)}
                />
            )}
        </div>
    );
}
