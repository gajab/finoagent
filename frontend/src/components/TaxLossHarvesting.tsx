import React, { useState, useEffect, useCallback } from 'react';
import { apiBase, fetchTLHPortfolios, saveTLHPortfolio, deleteTLHPortfolio, createAgent } from '../api';
import type { TLHSavedPortfolioItem } from '../api';
import type { ScheduleType, AgentCreateInput } from '../types';
import {
    Calculator, AlertCircle, BarChart3, ArrowRightLeft,
    Percent, DollarSign, Activity, TrendingUp, Info,
    Clock, ShieldCheck, Zap, ChevronDown, ChevronUp, Hash,
    Plus, Trash2, Briefcase, Target, PieChart, ArrowDownUp,
    Layers, Award, ExternalLink, Scale, Lightbulb, Scissors,
    Blend, Filter, CheckCircle, XCircle, AlertTriangle, Gauge,
    Building2, GitCompare, Save, FolderOpen, Bot, X, Loader2, Sparkles
} from 'lucide-react';
import {
    Chart as ChartJS, CategoryScale, LinearScale, PointElement,
    LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(
    CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler
);

/* ---------- types ---------- */

interface CorrelationData { ticker: string; correlation: number; z_score: number; }

interface TradeLeg {
    action: string; type: string; ticker: string; strike: number | null;
    bid: number; ask: number; mid: number; iv: number | null;
    oi: number | null; volume: number | null; qty: number;
    expiration: string | null; purpose: string;
}

interface SwapTrade {
    action: string; ticker: string; type: string;
    qty: number; price: number; total: number;
}

interface Strategy {
    id: string; name: string; type: string; badge?: string;
    description: string; pros: string[]; cons: string[];
    trades?: SwapTrade[]; legs?: TradeLeg[];
    contracts?: number; net_debit?: number; delta_exposure?: number;
    shares_cost?: number; net_collar_cost?: number;
    protection_range?: { floor: number; cap: number };
    expiration?: string; dte?: number;
    estimated_cost: number; tracking_error_1y?: number; optionsError?: string;
}

interface TimelineStep { day: string; title: string; description: string; }

/* --- Single-holding result (legacy) --- */
interface TargetInfo { ticker: string; price: number | null; shares: number; positionValue: number; }
interface PeerInfoData { ticker: string; price: number | null; sharesToBuy: number; totalCost: number; correlation: number; }
interface CostEntry { cost: number; complexity: string; protection: string; wash_sale_safe: boolean; capital_required: number; }
interface CostComparison { direct_swap: CostEntry; synthetic_long: CostEntry; protective_collar: CostEntry; }

interface ChartData {
    dates: string[]; targetNorm: number[]; peerNorm: number[];
    spread: number[]; spreadMean: number; spreadStd: number;
}

interface TaxLossResult {
    success: boolean; ticker: string; losses: number; taxRatePct: number;
    taxSavings: number; sharesHeld: number; bestPeer: string;
    targetInfo: TargetInfo; peerInfo: PeerInfoData;
    timeline: TimelineStep[]; correlations: CorrelationData[];
    chartData: ChartData; strategies: Strategy[];
    costComparison: CostComparison; error?: string;
}

/* --- Multi-holding types (unified portfolio approach) --- */
interface HoldingInput { ticker: string; shares: number; costBasis: number; }

interface HoldingResult {
    ticker: string; shares: number; costBasis: number;
    currentPrice: number | null; currentValue: number;
    costValue: number; unrealizedPnl: number; pnlPct: number;
    harvestable: boolean; losses?: number; taxSavings?: number;
    portfolioWeight?: number;
    error?: string;
}

interface BestETF {
    ticker: string; name: string; price: number | null;
    correlation: number; sharesToBuy: number; totalCost: number;
    zScore: number;
}

interface HoldingsOverlap {
    etfName: string; totalHoldings: number | null;
    overlap: { ticker: string; etfWeight: number }[];
    overlapPct: number | null; available: boolean;
}

interface PortfolioChartData {
    dates: string[]; portfolioNorm: number[]; etfNorm: number[];
    spread: number[]; spreadMean: number; spreadStd: number;
}

interface AlternateETF { ticker: string; correlation: number; zScore: number; }

interface PortfolioCostComparison {
    unified_swap: CostEntry; unified_synthetic: CostEntry; unified_collar: CostEntry;
}

interface PortfolioReplacement {
    selectedETF: string;
    bestETF: BestETF;
    holdingsOverlap: HoldingsOverlap;
    chartData: PortfolioChartData;
    alternateETFs: AlternateETF[];
    strategies: Strategy[];
    costComparison: PortfolioCostComparison;
}

interface PortfolioSummary {
    totalHoldings: number; harvestableCount: number; nonHarvestableCount: number;
    totalPortfolioValue: number; totalCostValue: number;
    totalUnrealizedPnl: number; totalHarvestableLosses: number;
    totalHarvestableValue: number; totalTaxSavings: number;
}

/* --- Quant metrics shared by all suggestions --- */
interface QuantMetrics {
    correlation: number; correlationPct: number;
    trackingError: number; trackingErrorAnn: number;
    beta: number; rSquared: number; rSquaredPct: number;
    zScore: number; informationRatio: number;
    annualizedDrift: number; maxTrackingDrawdown: number;
    cointegrationPValue?: number;
    dtwDistance?: number;
    lowerTailDependence?: number;
}

/* --- Optimization suggestion types --- */
interface OptimalBlendSuggestion {
    type: 'optimal_blend';
    title: string; description: string;
    aiRationale?: string;
    allocation: { ticker: string; weight: number; capital: number; price: number | null; shares: number }[];
    metrics: QuantMetrics; baselineMetrics: QuantMetrics;
    teDecomposition?: TEDecomposition;
    score: number; baselineScore: number; totalCapital: number;
    tradeTrigger?: TradeTrigger;
    anchored?: boolean;
    belowBaseline?: boolean;
    alternateBlends: {
        supplements: string[]; weights: Record<string, number>;
        metrics: QuantMetrics; score: number;
    }[];
}

interface SelectiveHarvestSuggestion {
    type: 'selective_harvest';
    title: string; description: string;
    harvestTickers: string[]; keepTickers: string[];
    losses: number; taxSavings: number; taxCapturePct: number;
    metrics: QuantMetrics; baselineMetrics: QuantMetrics;
    score: number; baselineScore: number;
    tradeTrigger?: TradeTrigger;
    alternates: {
        harvestTickers: string[]; keepTickers: string[];
        losses: number; taxCapturePct: number;
        metrics: QuantMetrics; score: number;
    }[];
}

interface PartialRebalanceSuggestion {
    type: 'partial_rebalance';
    title: string; description: string;
    sellPct: number;
    sellActions: { ticker: string; action: string; currentShares: number; sellShares: number; keepShares: number; sellPct: number; proceeds: number; lossRealized: number }[];
    buyActions: { ticker: string; action: string; capital: number; price: number | null; shares: number; weight: number }[];
    totalProceeds: number; totalLossCaptured: number;
    taxSavings: number; taxCapturePct: number;
    metrics: QuantMetrics; baselineMetrics: QuantMetrics;
    score: number; baselineScore: number;
    tradeTrigger?: TradeTrigger;
    alternates: {
        sellPct: number; totalLossCaptured: number; taxCapturePct: number;
        metrics: QuantMetrics; score: number;
    }[];
}

type OptimizationSuggestion = OptimalBlendSuggestion | SelectiveHarvestSuggestion | PartialRebalanceSuggestion;

interface TEDecomposition {
    teTotal: number;
    teSystematic: number;
    teIdiosyncratic: number;
    pctSystematic: number;
}

/* --- Institutional analysis types --- */
interface FactorDrift {
    perFactor: Record<string, { original: number; replacement: number; drift: number; absDrift: number }>;
    capmBeta?: { original: number; replacement: number; drift: number; absDrift: number };
    aggregateDrift: number;
    styleDriftFlags: string[];
    hasStyleDrift: boolean;
    driftRating: string;
}
interface TradeTrigger {
    taxBenefit: number; transactionCost: number; trackingErrorCost: number;
    totalCost: number; netBenefit: number; benefitRatio: number;
    triggered: boolean; verdict: string; verdictDetail: string;
}
interface TEConstraint {
    trackingErrorBps: number; trackingErrorPct: number;
    withinInstitutionalLimit: boolean; limit: string; rating: string;
}
interface InstitutionalAnalysis {
    available: boolean;
    originalFactorProfile?: {
        factorLoadings: Record<string, number>;
        capmBeta?: number;
        individualLoadings: Record<string, Record<string, number>>;
        portfolioVol: number; avgRSquared: number;
    };
    replacementFactorProfile?: {
        ticker: string; factorLoadings?: Record<string, number>;
        capmBeta?: number;
        rSquared?: number; idiosyncraticVol?: number;
    };
    factorDrift?: FactorDrift;
    tradeTrigger?: TradeTrigger;
    sectorExposure?: {
        original: Record<string, number>; replacement: Record<string, number>;
    };
    trackingErrorConstraint?: TEConstraint;
    error?: string;
}

/* --- Tightest-proxy discovery (ETFs holding a name at highest weight) --- */
interface TopHolderProxy {
    etf: string;
    statedWeight?: number | null;
    verifiedWeight?: number | null;
    correlation: number;
    trackingError?: number | null;
    beta?: number | null;
}

interface Methodology {
    objective: string;
    riskModel: string;
    selection: string;
    proxyDiscovery: string;
    tradeTrigger: string;
    references: string[];
}

interface PortfolioTLHResult {
    success: boolean; taxRatePct: number;
    summary: PortfolioSummary;
    holdings: HoldingResult[];
    harvestable: HoldingResult[];
    nonHarvestable: HoldingResult[];
    portfolioReplacement: PortfolioReplacement | null;
    optimizationSuggestions: OptimizationSuggestion[];
    aiRecommendations?: {
        stocks: string[];
        etfs: string[];
        rationale: string;
    };
    institutionalAnalysis?: InstitutionalAnalysis;
    overridesActive?: boolean;
    anchoredETF?: string | null;
    anchoredStocks?: string[];
    topHolderETFs?: Record<string, TopHolderProxy[]>;
    methodology?: Methodology;
    timeline: TimelineStep[];
    error?: string;
}

/* ---------- component ---------- */

export function TaxLossHarvesting() {
    /* --- portfolio state --- */
    const [holdings, setHoldings] = useState<HoldingInput[]>([
        { ticker: 'AAPL', shares: 50, costBasis: 195 },
        { ticker: 'MSFT', shares: 30, costBasis: 420 },
        { ticker: 'NVDA', shares: 20, costBasis: 140 },
    ]);
    const [portfolioTaxRate, setPortfolioTaxRate] = useState<number>(15);
    const [portfolioResult, setPortfolioResult] = useState<PortfolioTLHResult | null>(null);
    const [preferredETF, setPreferredETF] = useState<string | null>(null);
    const [customETFInput, setCustomETFInput] = useState('');
    const [customStocks, setCustomStocks] = useState<string[]>([]);
    const [customStocksRaw, setCustomStocksRaw] = useState('');

    /* --- shared state --- */
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [expandedStrategy, setExpandedStrategy] = useState<string | null>(null);
    const [selectedOptStrategy, setSelectedOptStrategy] = useState<number | null>(null);
    const [activeTab, setActiveTab] = useState<'default' | number>('default');

    /* --- saved portfolios state --- */
    const [savedPortfolios, setSavedPortfolios] = useState<TLHSavedPortfolioItem[]>([]);
    const [activeSavedId, setActiveSavedId] = useState<number | null>(null);
    const [showSaveInput, setShowSaveInput] = useState(false);
    const [saveName, setSaveName] = useState('');
    const [saveLoading, setSaveLoading] = useState(false);
    const [saveMsg, setSaveMsg] = useState<string | null>(null);

    /* --- agent creation state --- */
    const [showAgentForm, setShowAgentForm] = useState(false);
    const [agentName, setAgentName] = useState('');
    const [agentInstruction, setAgentInstruction] = useState('');
    const [agentSchedule, setAgentSchedule] = useState<ScheduleType>('recurring');
    const [agentCron, setAgentCron] = useState('0 9 * * 1');
    const [agentStrategyIdx, setAgentStrategyIdx] = useState<'default' | number>('default');
    const [agentCreating, setAgentCreating] = useState(false);
    const [agentSuccess, setAgentSuccess] = useState(false);
    const [agentError, setAgentError] = useState<string | null>(null);

    /* --- holdings management --- */
    const addHolding = () => setHoldings(prev => [...prev, { ticker: '', shares: 0, costBasis: 0 }]);
    const removeHolding = (idx: number) => setHoldings(prev => prev.filter((_, i) => i !== idx));
    const updateHolding = (idx: number, field: keyof HoldingInput, value: string | number) => {
        setHoldings(prev => prev.map((h, i) => i === idx ? { ...h, [field]: field === 'ticker' ? String(value).toUpperCase() : Number(value) } : h));
    };

    /* --- saved portfolios management --- */
    const loadSavedPortfolios = useCallback(async () => {
        try {
            const list = await fetchTLHPortfolios();
            setSavedPortfolios(list);
        } catch { /* silent — user may not be logged in */ }
    }, []);

    useEffect(() => { loadSavedPortfolios(); }, [loadSavedPortfolios]);

    const handleSavePortfolio = async () => {
        const name = saveName.trim();
        if (!name) return;
        const valid = holdings.filter(h => h.ticker && h.shares > 0 && h.costBasis > 0);
        if (valid.length === 0) return;
        setSaveLoading(true);
        setSaveMsg(null);
        try {
            const result = await saveTLHPortfolio(
                name,
                Number(portfolioTaxRate),
                valid.map(h => ({ ticker: h.ticker, shares: h.shares, cost_basis: h.costBasis })),
            );
            setActiveSavedId(result.id);
            setShowSaveInput(false);
            setSaveName('');
            setSaveMsg(`Saved "${name}"`);
            setTimeout(() => setSaveMsg(null), 3000);
            await loadSavedPortfolios();
        } catch (err: any) {
            setSaveMsg(err.message || 'Failed to save');
            setTimeout(() => setSaveMsg(null), 4000);
        } finally {
            setSaveLoading(false);
        }
    };

    const handleLoadPortfolio = (p: TLHSavedPortfolioItem) => {
        if (p.holdings?.length) {
            setHoldings(p.holdings.map(h => ({ ticker: h.ticker, shares: h.shares, costBasis: h.cost_basis })));
        } else {
            setHoldings(p.tickers.map(t => ({ ticker: t, shares: 0, costBasis: 0 })));
        }
        setPortfolioTaxRate(p.tax_rate_pct ?? 15);
        setActiveSavedId(p.id);
        setPortfolioResult(null);
    };

    const handleDeletePortfolio = async (id: number, e: React.MouseEvent) => {
        e.stopPropagation();
        try {
            await deleteTLHPortfolio(id);
            if (activeSavedId === id) setActiveSavedId(null);
            await loadSavedPortfolios();
            setSaveMsg('Portfolio deleted');
            setTimeout(() => setSaveMsg(null), 2000);
        } catch { /* silent */ }
    };

    /* --- agent instruction builder --- */
    const buildAgentInstruction = useCallback((stratIdx: 'default' | number) => {
        if (!portfolioResult) return '';
        const pr = portfolioResult;
        const holdingsList = holdings.filter(h => h.ticker && h.shares > 0 && h.costBasis > 0)
            .map(h => `${h.ticker} (${h.shares} shares @ $${h.costBasis})`).join(', ');

        let strategyBlock = '';
        const optSugs = pr.optimizationSuggestions || [];

        if (typeof stratIdx === 'number' && optSugs[stratIdx]) {
            const s = optSugs[stratIdx];
            if (s.type === 'optimal_blend') {
                const ob = s as OptimalBlendSuggestion;
                const alloc = ob.allocation.map(a => `${a.ticker} ${a.weight}%`).join(' + ');
                strategyBlock = `Preferred Strategy: Optimal Replacement Blend\n  Buy: ${alloc}\n  Composite score: ${ob.score.toFixed(3)}, Correlation: ${ob.metrics.correlationPct}%, TE: ${(ob.metrics.trackingErrorAnn * 100).toFixed(1)}%, Beta: ${ob.metrics.beta.toFixed(2)}`;
            } else if (s.type === 'selective_harvest') {
                const sh = s as SelectiveHarvestSuggestion;
                strategyBlock = `Preferred Strategy: Selective Harvesting\n  Harvest: ${sh.harvestTickers.join(', ')}\n  Keep: ${sh.keepTickers.join(', ')}\n  Tax capture: ${sh.taxCapturePct.toFixed(1)}%, Score: ${sh.score.toFixed(3)}`;
            } else if (s.type === 'partial_rebalance') {
                const pb = s as PartialRebalanceSuggestion;
                strategyBlock = `Preferred Strategy: Partial Rebalance (${pb.sellPct}%)\n  Tax capture: ${pb.taxCapturePct.toFixed(1)}%, Score: ${pb.score.toFixed(3)}`;
            }
        } else {
            // Default ETF strategy
            const rep = pr.portfolioReplacement;
            if (rep) {
                strategyBlock = `Preferred Strategy: ETF-Only (${rep.bestETF.ticker})\n  Correlation: ${(rep.bestETF.correlation).toFixed(1)}%`;
            }
        }

        const holdingsJson = holdings.filter(h => h.ticker && h.shares > 0 && h.costBasis > 0)
            .map(h => `{"ticker":"${h.ticker}","shares":${h.shares},"cost_basis":${h.costBasis}}`).join(',');

        return `Monitor my portfolio for tax-loss harvesting opportunities.

Holdings: ${holdingsList}
Tax Rate: ${portfolioTaxRate}%

Baseline metrics (from last analysis):
- Harvestable losses: $${pr.summary.totalHarvestableLosses.toLocaleString()}
- Tax savings: $${pr.summary.totalTaxSavings.toLocaleString()}
- Portfolio value: $${pr.summary.totalPortfolioValue.toLocaleString()}
${strategyBlock ? `\n${strategyBlock}` : ''}

**Instructions:**
1. Call \`run_tlh_analysis\` with holdings=[${holdingsJson}] and tax_rate_pct=${portfolioTaxRate} to get fresh analysis
2. Compare results against the baseline metrics above
3. Report changes: new opportunities, increased/decreased losses, correlation shifts
4. If harvestable losses increased by >10% or new positions became harvestable, flag as ALERT
5. Include the trade trigger verdict (Execute / Marginal / Do Not Execute)
6. Check wash sale rule compliance (31-day window) using \`get_tax_lot_info\`
7. Summarize with a clear action recommendation: EXECUTE / HOLD / WAIT`;
    }, [portfolioResult, holdings, portfolioTaxRate]);

    // Auto-update agent instruction when strategy changes
    useEffect(() => {
        if (showAgentForm && portfolioResult) {
            setAgentInstruction(buildAgentInstruction(agentStrategyIdx));
        }
    }, [agentStrategyIdx, showAgentForm, portfolioResult, buildAgentInstruction]);

    const handleCreateAgent = async () => {
        if (!agentName.trim() || !agentInstruction.trim()) return;
        setAgentCreating(true);
        setAgentError(null);
        try {
            const data: AgentCreateInput = {
                name: agentName.trim(),
                instruction: agentInstruction,
                schedule_type: agentSchedule,
                ...(agentSchedule === 'recurring' && { schedule_cron: agentCron }),
            };
            await createAgent(data);
            setAgentSuccess(true);
            setShowAgentForm(false);
        } catch (err: any) {
            setAgentError(err.message || 'Failed to create agent');
        } finally {
            setAgentCreating(false);
        }
    };

    /* --- portfolio mode API --- */
    const calculatePortfolio = async (overrideETF?: string | null, fresh = false, overrideStocks?: string[]) => {
        const valid = holdings.filter(h => h.ticker && h.shares > 0 && h.costBasis > 0);
        if (valid.length === 0) { setError('Add at least one valid holding'); return; }
        setLoading(true); setError(null); setPortfolioResult(null);
        // fresh=true means the main "Analyze" button was pressed — clear all overrides so the
        // engine runs a clean auto-discovery pass without any sticky ETF / stock overrides.
        // overrideStocks is passed explicitly by the "Re-Analyze Grid Overrides" button so the
        // request never depends on async (onBlur) state having flushed yet.
        const etfToUse = fresh ? null : (overrideETF !== undefined ? overrideETF : preferredETF);
        const stocksToUse = fresh ? [] : (overrideStocks !== undefined ? overrideStocks : customStocks);
        if (fresh) {
            setCustomStocks([]);
            setCustomStocksRaw('');
            setPreferredETF(null);
            setCustomETFInput('');
        }
        try {
            const payload: Record<string, unknown> = {
                holdings: valid.map(h => ({ ticker: h.ticker, shares: h.shares, cost_basis: h.costBasis })),
                tax_rate_pct: Number(portfolioTaxRate),
                custom_stocks: stocksToUse,
            };
            if (etfToUse) {
                payload.preferred_etf = etfToUse;
            }
            const endpoint = `${apiBase}/api/stock/strategies/portfolio-tax-loss-harvesting`;
            const res = await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token')}` },
                body: JSON.stringify(payload),
            });
            if (!res.ok) {
                const errData = await res.json();
                throw new Error(errData.detail ? (Array.isArray(errData.detail) ? errData.detail[0]?.msg : errData.detail) : 'Failed to analyze portfolio');
            }
            setPortfolioResult(await res.json());
        } catch (err: any) {
            setError(err.message || 'An unexpected error occurred');
        } finally {
            setLoading(false);
        }
    };

    const toggleStrategy = (id: string) => setExpandedStrategy(prev => prev === id ? null : id);

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="border-l-4 border-accent bg-accent/10 p-4 rounded-r-xl">
                <h3 className="font-bold flex items-center gap-2 text-accent">
                    <ArrowRightLeft className="w-5 h-5" />
                    Tax Loss Harvesting Strategy
                </h3>
                <p className="text-sm opacity-80 mt-1">
                    Realize portfolio losses to offset capital gains, while deploying capital into correlated proxy assets to maintain exposure without triggering the 30-day Wash Sale rule.
                </p>
            </div>

            <>{/* Natively rendering Portfolio mode */}
                <div className="space-y-5">
                    {/* Holdings Table */}
                    <div className="glass-card">
                        <div className="p-5">
                            {/* Header with save button */}
                            <div className="flex items-center justify-between mb-3">
                                <h3 className="font-bold text-sm flex items-center gap-2">
                                    <PieChart className="w-5 h-5 text-primary" /> Your Holdings
                                </h3>
                                <div className="flex items-center gap-2">
                                    {saveMsg && (
                                        <span className={`text-xs px-2 py-1 rounded-lg animate-fade-in ${saveMsg.startsWith('Saved') || saveMsg === 'Portfolio deleted' ? 'bg-success/20 text-success' : 'bg-error/20 text-error'}`}>
                                            {saveMsg}
                                        </span>
                                    )}
                                    {showSaveInput ? (
                                        <div className="flex items-center gap-1.5 animate-fade-in">
                                            <input
                                                type="text"
                                                className="input input-bordered input-sm w-44"
                                                placeholder="Portfolio name…"
                                                value={saveName}
                                                onChange={e => setSaveName(e.target.value)}
                                                onKeyDown={e => { if (e.key === 'Enter') handleSavePortfolio(); if (e.key === 'Escape') { setShowSaveInput(false); setSaveName(''); } }}
                                                autoFocus
                                            />
                                            <button className="btn btn-accent btn-sm btn-square" onClick={handleSavePortfolio} disabled={saveLoading || !saveName.trim()}>
                                                {saveLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle className="w-3.5 h-3.5" />}
                                            </button>
                                            <button className="btn btn-ghost btn-sm btn-square" onClick={() => { setShowSaveInput(false); setSaveName(''); }}>
                                                <X className="w-3.5 h-3.5" />
                                            </button>
                                        </div>
                                    ) : (
                                        <button
                                            className="btn btn-ghost btn-sm gap-1"
                                            onClick={() => setShowSaveInput(true)}
                                            disabled={holdings.filter(h => h.ticker && h.shares > 0 && h.costBasis > 0).length === 0}
                                        >
                                            <Save className="w-3.5 h-3.5" /> Save
                                        </button>
                                    )}
                                </div>
                            </div>

                            {/* Saved portfolio chips */}
                            {savedPortfolios.length > 0 && (
                                <div className="flex flex-wrap gap-2 mb-4">
                                    {savedPortfolios.map(p => (
                                        <button
                                            key={p.id}
                                            className={`group flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs transition-all cursor-pointer hover:border-accent/50 ${activeSavedId === p.id ? 'border-accent bg-accent/10 text-accent' : 'border-white/10 bg-base-200/40 text-base-content/80'}`}
                                            onClick={() => handleLoadPortfolio(p)}
                                        >
                                            <FolderOpen className="w-3 h-3 opacity-50" />
                                            <span className="font-medium">{p.name}</span>
                                            <span className="opacity-40">·</span>
                                            <span className="opacity-50 truncate max-w-[120px]">{p.tickers.join(', ')}</span>
                                            <span className="opacity-30">{p.holding_count}h</span>
                                            <span
                                                className="opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:text-error transition-opacity ml-0.5"
                                                onClick={(e) => handleDeletePortfolio(p.id, e)}
                                                title="Delete"
                                            >
                                                <X className="w-3 h-3" />
                                            </span>
                                        </button>
                                    ))}
                                </div>
                            )}

                            <div className="overflow-x-auto">
                                <table className="table table-sm table-pro">
                                    <thead>
                                        <tr>
                                            <th className="w-8">#</th>
                                            <th>Ticker</th>
                                            <th className="text-right">Shares</th>
                                            <th className="text-right">Cost Basis ($/share)</th>
                                            <th className="text-right">Total Cost</th>
                                            <th className="w-12"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {holdings.map((h, i) => (
                                            <tr key={i}>
                                                <td className="opacity-50">{i + 1}</td>
                                                <td>
                                                    <input
                                                        type="text"
                                                        className="input input-bordered input-sm w-24"
                                                        value={h.ticker}
                                                        onChange={e => updateHolding(i, 'ticker', e.target.value)}
                                                        placeholder="AAPL"
                                                    />
                                                </td>
                                                <td className="text-right">
                                                    <input
                                                        type="number"
                                                        className="input input-bordered input-sm w-24 text-right"
                                                        value={h.shares || ''}
                                                        onChange={e => updateHolding(i, 'shares', e.target.value)}
                                                        min={1}
                                                    />
                                                </td>
                                                <td className="text-right">
                                                    <input
                                                        type="number"
                                                        className="input input-bordered input-sm w-28 text-right"
                                                        value={h.costBasis || ''}
                                                        onChange={e => updateHolding(i, 'costBasis', e.target.value)}
                                                        min={0.01}
                                                        step={0.01}
                                                    />
                                                </td>
                                                <td className="text-right font-mono tabular-nums opacity-70">
                                                    {h.shares && h.costBasis ? `$${(h.shares * h.costBasis).toLocaleString()}` : '—'}
                                                </td>
                                                <td>
                                                    <button className="btn btn-ghost btn-xs text-error" onClick={() => removeHolding(i)} disabled={holdings.length <= 1}>
                                                        <Trash2 className="w-3.5 h-3.5" />
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <div className="flex items-center justify-between mt-3">
                                <button className="btn btn-ghost btn-sm" onClick={addHolding}>
                                    <Plus className="w-4 h-4 mr-1" /> Add Holding
                                </button>
                                <div className="form-control">
                                    <label className="label py-0">
                                        <span className="label-text text-xs flex items-center gap-1">
                                            <Percent className="w-3 h-3 text-warning" /> Tax Slab
                                        </span>
                                    </label>
                                    <input
                                        type="number"
                                        className="input input-bordered input-sm w-20 text-right"
                                        value={portfolioTaxRate}
                                        onChange={e => setPortfolioTaxRate(Number(e.target.value))}
                                        min={0} max={50} step={1}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>

                    <button className="btn btn-accent w-full sm:w-auto" onClick={() => calculatePortfolio(undefined, true)} disabled={loading}>
                        {loading ? <span className="loading loading-spinner"></span> : <Calculator className="w-5 h-5 mr-2" />}
                        Analyze Portfolio for Harvesting
                    </button>

                    {error && <div className="alert alert-error text-sm"><AlertCircle className="w-4 h-4" />{error}</div>}

                    {/* ============ PORTFOLIO RESULTS ============ */}
                    {portfolioResult && (
                        <div className="mt-6 space-y-6 animate-fade-in">
                            {/* Portfolio Summary */}
                            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
                                <SummaryCard
                                    label="Portfolio Value"
                                    value={`$${portfolioResult.summary.totalPortfolioValue.toLocaleString()}`}
                                    sub={`${portfolioResult.summary.totalHoldings} holdings`}
                                    color="text-base-content"
                                />
                                <SummaryCard
                                    label="Total Unrealized P&L"
                                    value={`${portfolioResult.summary.totalUnrealizedPnl >= 0 ? '+' : ''}$${portfolioResult.summary.totalUnrealizedPnl.toLocaleString()}`}
                                    sub={`Cost: $${portfolioResult.summary.totalCostValue.toLocaleString()}`}
                                    color={portfolioResult.summary.totalUnrealizedPnl >= 0 ? 'text-success' : 'text-error'}
                                />
                                <SummaryCard
                                    label="Harvestable Losses"
                                    value={`$${portfolioResult.summary.totalHarvestableLosses.toLocaleString()}`}
                                    sub={`${portfolioResult.summary.harvestableCount} of ${portfolioResult.summary.totalHoldings} positions`}
                                    color="text-warning"
                                />
                                <SummaryCard
                                    label="Tax Savings"
                                    value={`+$${portfolioResult.summary.totalTaxSavings.toLocaleString()}`}
                                    sub={`At ${portfolioResult.taxRatePct}% bracket`}
                                    color="text-success"
                                />
                                <SummaryCard
                                    label="Harvestable Value"
                                    value={`$${portfolioResult.summary.totalHarvestableValue.toLocaleString()}`}
                                    sub="Capital to redeploy"
                                    color="text-accent"
                                />
                            </div>

                            {/* All Holdings Overview Table */}
                            <div className="glass-card">
                                <div className="p-5">
                                    <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
                                        <ArrowDownUp className="w-5 h-5 text-accent" /> Holdings Analysis
                                    </h3>
                                    <div className="overflow-x-auto">
                                        <table className="table table-sm table-pro">
                                            <thead>
                                                <tr>
                                                    <th>Ticker</th>
                                                    <th className="text-right">Shares</th>
                                                    <th className="text-right">Cost Basis</th>
                                                    <th className="text-right">Current Price</th>
                                                    <th className="text-right">Current Value</th>
                                                    <th className="text-right">Weight</th>
                                                    <th className="text-right">Unrealized P&L</th>
                                                    <th className="text-right">P&L %</th>
                                                    <th>Status</th>
                                                    <th className="text-right">Tax Savings</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {[...portfolioResult.harvestable, ...portfolioResult.nonHarvestable].map((h, i) => (
                                                    <tr key={i} className={h.harvestable ? '' : 'opacity-60'}>
                                                        <td className="font-bold">{h.ticker}</td>
                                                        <td className="text-right font-mono tabular-nums">{h.shares}</td>
                                                        <td className="text-right font-mono tabular-nums">${h.costBasis.toFixed(2)}</td>
                                                        <td className="text-right font-mono tabular-nums">{h.currentPrice ? `$${h.currentPrice.toFixed(2)}` : '—'}</td>
                                                        <td className="text-right font-mono tabular-nums">${h.currentValue.toLocaleString()}</td>
                                                        <td className="text-right font-mono tabular-nums opacity-70">{h.portfolioWeight?.toFixed(1)}%</td>
                                                        <td className={`text-right font-mono tabular-nums font-bold ${h.unrealizedPnl >= 0 ? 'text-success' : 'text-error'}`}>
                                                            {h.unrealizedPnl >= 0 ? '+' : ''}${h.unrealizedPnl.toLocaleString()}
                                                        </td>
                                                        <td className={`text-right font-mono tabular-nums ${h.pnlPct >= 0 ? 'text-success' : 'text-error'}`}>
                                                            {h.pnlPct >= 0 ? '+' : ''}{h.pnlPct.toFixed(1)}%
                                                        </td>
                                                        <td>
                                                            {h.harvestable ? (
                                                                <span className="badge badge-sm badge-error gap-1">Harvest</span>
                                                            ) : (
                                                                <span className="badge badge-sm badge-success gap-1">Gain</span>
                                                            )}
                                                        </td>
                                                        <td className="text-right font-mono tabular-nums text-success font-bold">
                                                            {h.taxSavings ? `+$${h.taxSavings.toLocaleString()}` : '—'}
                                                        </td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>

                            {/* ============ TIGHTEST PROXIES (top-weight holders) ============ */}
                            {portfolioResult.topHolderETFs && Object.keys(portfolioResult.topHolderETFs).length > 0 && (
                                <div className="glass-card">
                                    <div className="p-5">
                                        <h3 className="font-bold text-sm flex items-center gap-2 mb-1">
                                            <Target className="w-5 h-5 text-success" /> Tightest Proxies — ETFs Holding Each Loser at Highest Weight
                                        </h3>
                                        <p className="text-xs opacity-60 mb-4">
                                            For each harvested name we find the ETFs that hold it at the largest portfolio weight (LLM-sourced as of today), then <b>measure</b> correlation &amp; annualized tracking error from real price history. Lower tracking error = tighter replacement. Thin or newly-listed funds that can't be priced still appear with their weight (metrics shown as “—”).
                                        </p>
                                        <div className="space-y-4">
                                            {Object.entries(portfolioResult.topHolderETFs).map(([tk, proxies]) => (
                                                <div key={tk}>
                                                    <div className="text-xs font-bold mb-1 font-mono text-error">{tk}</div>
                                                    <div className="overflow-x-auto">
                                                        <table className="table table-sm table-pro">
                                                            <thead>
                                                                <tr><th>ETF</th><th className="text-right">Held Weight</th><th className="text-right">Correlation</th><th className="text-right">Tracking Error</th><th className="text-right">Beta</th></tr>
                                                            </thead>
                                                            <tbody>
                                                                {proxies.map((p, i) => (
                                                                    <tr key={i} className={i === 0 ? 'bg-success/5' : ''}>
                                                                        <td className="font-mono font-bold">{p.etf}{i === 0 && <span className="badge badge-success badge-xs ml-2">tightest</span>}</td>
                                                                        <td className="text-right font-mono tabular-nums">
                                                                            {p.verifiedWeight != null ? `${Number(p.verifiedWeight).toFixed(2)}%` : (p.statedWeight != null ? `~${Number(p.statedWeight).toFixed(1)}%` : '—')}
                                                                            {p.verifiedWeight == null && p.statedWeight != null && <span className="opacity-40 ml-1">(est)</span>}
                                                                        </td>
                                                                        <td className="text-right font-mono tabular-nums">{p.correlation != null ? `${p.correlation.toFixed(0)}%` : '—'}</td>
                                                                        <td className={`text-right font-mono tabular-nums ${p.trackingError != null && p.trackingError < 10 ? 'text-success' : p.trackingError != null && p.trackingError < 20 ? 'text-warning' : 'text-error'}`}>{p.trackingError != null ? `${p.trackingError.toFixed(1)}%` : '—'}</td>
                                                                        <td className="text-right font-mono tabular-nums opacity-70">{p.beta != null ? p.beta.toFixed(2) : '—'}</td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            )}

                            {/* ============ METHODOLOGY & RESEARCH ============ */}
                            {portfolioResult.methodology && (
                                <details className="glass-card group">
                                    <summary className="p-4 cursor-pointer flex items-center gap-2 font-bold text-sm list-none">
                                        <Building2 className="w-5 h-5 text-info" /> Methodology &amp; Research
                                        <ChevronDown className="w-4 h-4 ml-auto opacity-60 group-open:rotate-180 transition-transform" />
                                    </summary>
                                    <div className="px-5 pb-5 text-xs space-y-2 opacity-90">
                                        <p><b className="text-info">Objective:</b> {portfolioResult.methodology.objective}</p>
                                        <p><b className="text-info">Risk model:</b> {portfolioResult.methodology.riskModel}</p>
                                        <p><b className="text-info">Selection:</b> {portfolioResult.methodology.selection}</p>
                                        <p><b className="text-info">Proxy discovery:</b> {portfolioResult.methodology.proxyDiscovery}</p>
                                        <p><b className="text-info">Trade trigger:</b> {portfolioResult.methodology.tradeTrigger}</p>
                                        <div>
                                            <b className="text-info">References:</b>
                                            <ul className="list-disc ml-5 mt-1 space-y-0.5 opacity-80">
                                                {portfolioResult.methodology.references.map((r, i) => <li key={i}>{r}</li>)}
                                            </ul>
                                        </div>
                                    </div>
                                </details>
                            )}

                            {/* ============ UNIFIED STRATEGY PANEL ============ */}
                            {portfolioResult.portfolioReplacement && (() => {
                                const pr = portfolioResult.portfolioReplacement!;
                                const bestETF = pr.bestETF;
                                const optSugs = portfolioResult.optimizationSuggestions || [];
                                const ia = portfolioResult.institutionalAnalysis;

                                /* ---- Shared tooltip component ---- */
                                const Tip = ({ term, tip, children }: { term: string; tip: string; children?: React.ReactNode }) => (
                                    <span className="relative group/tip cursor-help border-b border-dotted border-white/30">
                                        {children || term}
                                        <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-72 rounded-xl bg-gray-950 px-4 py-3 text-xs text-white shadow-[0_8px_32px_rgba(0,0,0,0.8)] border border-white/10 opacity-0 group-hover/tip:opacity-100 transition-opacity duration-150 z-[9999] text-left font-normal normal-case leading-relaxed whitespace-normal">
                                            <span className="font-semibold text-cyan-400 block mb-1 text-[11px] uppercase tracking-wide">{term}</span>
                                            <span className="text-gray-200 leading-relaxed">{tip}</span>
                                        </span>
                                    </span>
                                );

                                /* ---- Metrics pill ---- */
                                const MetricsPill = ({ label, tip, value, unit, good }: { label: string; tip: string; value: number | string; unit?: string; good?: boolean }) => (
                                    <div className="bg-base-200/30 rounded-lg px-2 py-1.5 text-center border border-white/[0.03]">
                                        <div className="text-[10px] opacity-50 uppercase tracking-wider"><Tip term={label} tip={tip}>{label}</Tip></div>
                                        <div className={`font-bold text-xs tabular-nums ${good === true ? 'text-success' : good === false ? 'text-error' : ''}`}>{value}{unit || ''}{good === true && ' ↑'}{good === false && ' ↓'}</div>
                                    </div>
                                );

                                const MetricsBar = ({ m, baseline }: { m: QuantMetrics; baseline?: QuantMetrics }) => (
                                    <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-9 gap-1.5 mt-3">
                                        <MetricsPill label="Correlation" tip="How closely replacement tracks your original. Higher = better. 95–100% Excellent · 85–95% Good · 70–85% Fair · <70% Poor." value={m.correlationPct} unit="%" good={baseline ? m.correlationPct > baseline.correlationPct : undefined} />
                                        <MetricsPill label="Track. Error" tip="Annualized return deviation from original. Lower = better. 0–5% Excellent · 5–10% Good · 10–20% Fair · >20% High risk." value={m.trackingError} unit="%" good={baseline ? m.trackingError < baseline.trackingError : undefined} />
                                        <MetricsPill label="Beta" tip="Market sensitivity vs your portfolio. Target: 1.0 (lockstep). 0.95–1.05 Excellent · 0.85–1.15 Good · outside = drift." value={m.beta} good={baseline ? Math.abs(m.beta - 1) < Math.abs(baseline.beta - 1) : undefined} />
                                        <MetricsPill label="R²" tip="% of variance explained by replacement. Higher = better. 95%+ Excellent · 85–95% Good · 70–85% Fair · <70% Weak fit." value={m.rSquaredPct} unit="%" good={baseline ? m.rSquaredPct > baseline.rSquaredPct : undefined} />
                                        <MetricsPill label="Z-Score" tip="Standard deviations of mean drift. Target: 0. ±0.5 Excellent · ±1.0 Normal · ±2.0 Significant · >±2.0 Extreme drift." value={m.zScore} good={baseline ? Math.abs(m.zScore) < Math.abs(baseline.zScore) : undefined} />
                                        <MetricsPill label="Info Ratio" tip="Return drift per unit of tracking error. Target: 0. ±0.3 Excellent · ±0.5 Acceptable · >±1.0 Significant deviation." value={m.informationRatio} good={baseline ? Math.abs(m.informationRatio) < Math.abs(baseline.informationRatio) : undefined} />
                                        
                                        {m.cointegrationPValue !== undefined && <MetricsPill label="Coint P-Val" tip="Cointegration: probability spread isn't stationary. <0.05 is good." value={m.cointegrationPValue} good={m.cointegrationPValue < 0.05} />}
                                        {m.dtwDistance !== undefined && <MetricsPill label="DTW Dist" tip="Dynamic Time Warping distance. Lower distance means higher structure similarity." value={m.dtwDistance} />}
                                        {m.lowerTailDependence !== undefined && <MetricsPill label="Tail Dep" tip="Probability of simultaneous extreme downside events." value={m.lowerTailDependence} unit="%" />}
                                    </div>
                                );

                                /* ---- Action step ---- */
                                const ActionStep = ({ step, icon, title, children }: { step: number; icon: React.ReactNode; title: string; children: React.ReactNode }) => (
                                    <div className="flex gap-3 items-start">
                                        <div className="flex-shrink-0 w-7 h-7 rounded-full bg-accent/20 text-accent flex items-center justify-center text-xs font-bold">{step}</div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-1.5 mb-1">{icon}<span className="text-xs font-bold uppercase tracking-wider opacity-70">{title}</span></div>
                                            {children}
                                        </div>
                                    </div>
                                );

                                /* ---- Institutional analysis sub-section ---- */
                                const InstitutionalSection = ({ strategyMetrics, strategyTaxSavings, strategyLabel, backendTradeTrigger, teDecomposition }: { strategyMetrics?: QuantMetrics; strategyTaxSavings?: number; strategyLabel?: string; backendTradeTrigger?: TradeTrigger; teDecomposition?: TEDecomposition }) => {
                                    if (!ia?.available) return null;
                                    const factorLabels: Record<string, string> = { market: 'Market (β)', size: 'Size (SMB)', value: 'Value (HML)', growth: 'Growth', momentum: 'Momentum', quality: 'Quality', volatility: 'Low Vol' };
                                    const factorTips: Record<string, string> = { market: 'CAPM beta — sensitivity to broad market.', size: 'Small-minus-big. Positive = small-cap tilt.', value: 'High-minus-low book/market. Positive = value.', growth: 'Growth-stock exposure.', momentum: 'Winners-minus-losers factor.', quality: 'High-quality company exposure.', volatility: 'Low-vol factor. Higher = defensive.' };

                                    // Compute per-strategy TE constraint from strategy metrics if available
                                    // trackingErrorAnn is decimal (0.044 = 4.4%), trackingErrorPct is percent (4.4)
                                    const teAnn = strategyMetrics?.trackingErrorAnn
                                        ?? (ia.trackingErrorConstraint?.trackingErrorPct ? ia.trackingErrorConstraint.trackingErrorPct / 100 : 0);
                                    const teBps = Math.round(teAnn * 10000 * 10) / 10; // decimal → bps (0.044 → 440)
                                    const teConstraint = {
                                        trackingErrorBps: teBps,
                                        trackingErrorPct: teAnn * 100,
                                        withinInstitutionalLimit: teBps < 50,
                                        rating: teBps < 50 ? 'Institutional Grade' : teBps < 100 ? 'Acceptable' : teBps < 200 ? 'Above Threshold' : 'High Risk',
                                    };

                                    // Use backend trade trigger if available (authoritative), else compute locally
                                    const tt: TradeTrigger = backendTradeTrigger ?? (() => {
                                        const taxBenefit = strategyTaxSavings ?? ia.tradeTrigger?.taxBenefit ?? 0;
                                        const totalHarvestableVal = portfolioResult.summary.totalHarvestableValue || 1;
                                        const txnCostRate = 0.002; // 10 bps each way = 20 bps round-trip (matches backend)
                                        const transactionCost = Math.round(totalHarvestableVal * txnCostRate);
                                        const teForCost = strategyMetrics?.trackingErrorAnn ?? (ia.tradeTrigger?.trackingErrorCost ? ia.tradeTrigger.trackingErrorCost / totalHarvestableVal : 0.05);
                                        const trackingErrorCost = Math.round(totalHarvestableVal * teForCost * Math.sqrt(30 / 252));
                                        const totalCost = transactionCost + trackingErrorCost;
                                        const netBenefit = taxBenefit - totalCost;
                                        const benefitRatio = totalCost > 0 ? Math.round((taxBenefit / totalCost) * 10) / 10 : taxBenefit > 0 ? 99 : 0;
                                        const triggered = netBenefit > 0;
                                        const verdict = benefitRatio >= 3 ? 'Strong Execute' : benefitRatio >= 1.5 ? 'Execute' : benefitRatio >= 1 ? 'Marginal' : 'Do Not Execute';
                                        const verdictDetail = triggered
                                            ? `Tax benefit exceeds costs by ${benefitRatio}x — economically justified.`
                                            : `Costs exceed tax benefit — not economically justified at current levels.`;
                                        return { taxBenefit: Math.round(taxBenefit), transactionCost, trackingErrorCost, totalCost, netBenefit, benefitRatio, triggered, verdict, verdictDetail };
                                    })();

                                    const VerdictIcon = tt.triggered ? CheckCircle : tt.verdict === 'Marginal' ? AlertTriangle : XCircle;
                                    const verdictBadge: Record<string, string> = {
                                        'Strong Execute': 'badge-success',
                                        'Execute': 'badge-success badge-outline',
                                        'Marginal': 'badge-warning',
                                        'Do Not Execute': 'badge-error',
                                    };
                                    const vc: Record<string, string> = { 'Strong Execute': 'text-success', Execute: 'text-success', Marginal: 'text-warning', 'Do Not Execute': 'text-error' };

                                    // CAPM beta from factor drift (simple, intuitive — not multi-factor tilt)
                                    const capmBeta = ia.factorDrift?.capmBeta;

                                    // Separate market from multi-factor tilts for cleaner display
                                    const factorTiltEntries = ia.factorDrift?.perFactor
                                        ? Object.entries(ia.factorDrift.perFactor).filter(([f]) => f !== 'market')
                                        : [];

                                    return (
                                        <div className="mt-4 pt-4 border-t border-white/[0.05] space-y-5">
                                            <div className="text-xs font-bold uppercase tracking-wider opacity-50 flex items-center gap-2">
                                                <Building2 className="w-3.5 h-3.5" />
                                                <Tip term="Institutional Analysis" tip="Barra-style multi-factor risk model. Shows factor loadings, style drift, and trade economics — computed specifically for this strategy.">Risk Factor Analysis</Tip>
                                                {strategyLabel && <span className="badge badge-xs bg-accent/20 text-accent border-accent/30 normal-case">{strategyLabel}</span>}
                                            </div>

                                            {/* ═══ Trade Trigger ═══ */}
                                            <div className={`bg-base-200/40 rounded-xl p-3.5 border ${tt.triggered ? 'border-success/20' : 'border-error/20'}`}>
                                                <div className="flex items-center gap-2 mb-1.5">
                                                    <VerdictIcon className={`w-4 h-4 ${vc[tt.verdict] || ''}`} />
                                                    <span className={`font-bold text-xs ${vc[tt.verdict] || ''}`}>
                                                        <Tip term="Trade Trigger" tip="Should you execute? Compares tax savings against transaction costs + tracking error cost. Ratio > 1.5x = Execute.">{tt.verdict}</Tip>
                                                    </span>
                                                    <span className={`badge badge-xs ${verdictBadge[tt.verdict] || 'badge-ghost'}`}>{tt.benefitRatio}x benefit</span>
                                                </div>
                                                <p className="text-[11px] opacity-60 leading-relaxed">{tt.verdictDetail}</p>
                                                <div className="grid grid-cols-3 gap-2 mt-2.5 text-center text-[10px]">
                                                    <div className="bg-base-100/30 rounded-lg py-1.5"><Tip term="Tax Benefit" tip="Estimated tax savings from harvesting losses at your marginal tax rate."><span className="opacity-40">Tax Benefit</span></Tip><div className="font-bold text-success tabular-nums mt-0.5">${tt.taxBenefit.toLocaleString()}</div></div>
                                                    <div className="bg-base-100/30 rounded-lg py-1.5"><Tip term="Txn Cost" tip="Round-trip transaction cost (~10 bps each way, 20 bps total)."><span className="opacity-40">Txn Cost</span></Tip><div className="font-bold text-error tabular-nums mt-0.5">${tt.transactionCost.toLocaleString()}</div></div>
                                                    <div className="bg-base-100/30 rounded-lg py-1.5"><Tip term="TE Cost" tip="Expected tracking error cost over 30-day holding period = TE × √(30/252) × capital."><span className="opacity-40">TE Cost</span></Tip><div className="font-bold text-error tabular-nums mt-0.5">${tt.trackingErrorCost.toLocaleString()}</div></div>
                                                </div>
                                            </div>

                                            {/* ═══ TE Constraint + Market Beta + Style Drift ═══ */}
                                            <div className="grid grid-cols-3 gap-3">
                                                {/* TE Constraint with gauge */}
                                                <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                                                    <div className="text-[10px] font-bold opacity-50 mb-1"><Tip term="TE Constraint" tip="Institutional funds target < 50 bps (0.50%) tracking error. Scale: < 50 bps = Institutional Grade, 50–100 = Acceptable, 100–200 = Above Threshold, 200+ = High Risk.">Tracking Error</Tip></div>
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-lg font-bold tabular-nums">{teConstraint.trackingErrorBps}<span className="text-xs opacity-40"> bps</span></span>
                                                        <span className={`badge badge-xs ${teConstraint.withinInstitutionalLimit ? 'bg-success/20 text-success' : teConstraint.trackingErrorBps < 100 ? 'bg-warning/20 text-warning' : 'bg-error/20 text-error'}`}>{teConstraint.rating}</span>
                                                    </div>
                                                    <div className="mt-1.5 h-1.5 bg-base-200/60 rounded-full overflow-hidden relative">
                                                        <div className={`h-full rounded-full transition-all ${teConstraint.trackingErrorBps < 50 ? 'bg-success' : teConstraint.trackingErrorBps < 100 ? 'bg-warning' : 'bg-error'}`} style={{ width: `${Math.min(teConstraint.trackingErrorBps / 200 * 100, 100)}%` }} />
                                                    </div>
                                                    <div className="flex justify-between text-[8px] opacity-30 mt-0.5 tabular-nums px-0.5"><span>0</span><span>50</span><span>100</span><span>200 bps</span></div>
                                                    {teDecomposition && teDecomposition.teSystematic > 0 && (
                                                        <div className="mt-2 pt-1.5 border-t border-white/[0.05] space-y-1">
                                                            <div className="text-[8px] opacity-40 font-bold uppercase tracking-wider">
                                                                <Tip term="TE Decomposition" tip="Splits tracking error into two causes. Systematic TE = structural mismatch in sector, beta, or style factors between your original and replacement — this is the dangerous kind, it means the replacement will behave differently in market regimes. Idiosyncratic TE = random company-specific noise (earnings, news) — less concerning because it mean-reverts and largely washes out over the 30-day holding period. Goal: keep Systematic below 30% of total TE. If Systematic dominates, the replacement has wrong factor exposure and you should choose a better sector-matched candidate.">TE Breakdown</Tip>
                                                            </div>
                                                            <div className="flex justify-between items-center text-[9px] tabular-nums">
                                                                <Tip term="Systematic TE" tip="Caused by factor mismatches — sector tilt, beta drift, style (value/growth/momentum). This is structural drift that persists for the entire holding period. Lower is better. Above 40% means your replacement is in a different 'risk category' than the sold position.">
                                                                    <span className="opacity-60 border-b border-dotted border-white/20">Systematic</span>
                                                                </Tip>
                                                                <span className={`font-bold ${teDecomposition.pctSystematic > 60 ? 'text-error' : teDecomposition.pctSystematic > 30 ? 'text-warning' : 'text-success'}`}>
                                                                    {teDecomposition.teSystematic}% <span className="opacity-40 font-normal text-[8px]">{teDecomposition.pctSystematic}% of total</span>
                                                                </span>
                                                            </div>
                                                            <div className="flex justify-between items-center text-[9px] tabular-nums">
                                                                <Tip term="Idiosyncratic TE" tip="Random stock-specific noise — earnings surprises, news events, company-specific moves. This is not structural drift; it is unpredictable and mean-reverts over time. A high idiosyncratic share is acceptable because it reflects the irreducible difference between any two individual stocks, not a factor mismatch.">
                                                                    <span className="opacity-60 border-b border-dotted border-white/20">Idiosyncratic</span>
                                                                </Tip>
                                                                <span className="font-bold opacity-60">{teDecomposition.teIdiosyncratic}% <span className="opacity-40 font-normal text-[8px]">{100 - teDecomposition.pctSystematic}% of total</span></span>
                                                            </div>
                                                            <div className={`mt-1 text-[8px] leading-tight px-0.5 ${teDecomposition.pctSystematic > 60 ? 'text-error' : teDecomposition.pctSystematic > 30 ? 'text-warning' : 'text-success'}`}>
                                                                {teDecomposition.pctSystematic > 60
                                                                    ? 'High systematic drift — replacement has different factor exposure. Consider a better sector match.'
                                                                    : teDecomposition.pctSystematic > 30
                                                                    ? 'Moderate systematic drift — factor alignment is acceptable but not ideal.'
                                                                    : 'Low systematic drift — replacement closely mirrors original factor exposure.'}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                                {/* CAPM Beta */}
                                                {capmBeta && (
                                                    <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                                                        <div className="text-[10px] font-bold opacity-50 mb-1"><Tip term="Market Beta" tip="Simple CAPM beta — sensitivity to broad market (SPY). Target ~1.0. 0.95–1.05 Excellent · 0.85–1.15 Good · outside = drift risk.">Market Beta (β)</Tip></div>
                                                        <div className="grid grid-cols-2 gap-1 mt-1.5">
                                                            <div>
                                                                <div className="text-[9px] opacity-40">Portfolio</div>
                                                                <div className="font-bold tabular-nums text-sm">{capmBeta.original.toFixed(2)}</div>
                                                            </div>
                                                            <div>
                                                                <div className="text-[9px] opacity-40">Replacement</div>
                                                                <div className="font-bold tabular-nums text-sm">{capmBeta.replacement.toFixed(2)}</div>
                                                            </div>
                                                        </div>
                                                        <div className={`text-[9px] mt-1 font-bold tabular-nums ${capmBeta.absDrift < 0.1 ? 'text-success' : capmBeta.absDrift < 0.2 ? 'text-warning' : 'text-error'}`}>
                                                            Drift: {capmBeta.drift > 0 ? '+' : ''}{capmBeta.drift.toFixed(3)}
                                                            {capmBeta.absDrift < 0.1 ? ' ✓' : capmBeta.absDrift < 0.2 ? ' ⚠' : ' ⚠⚠'}
                                                        </div>
                                                    </div>
                                                )}
                                                {/* Style Drift */}
                                                {ia.factorDrift && (
                                                    <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
                                                        <div className="text-[10px] font-bold opacity-50 mb-1"><Tip term="Style Drift" tip="Aggregate factor tilt difference (L2 norm). < 0.1 Excellent · 0.1–0.2 Good · 0.2–0.35 Moderate · > 0.35 High.">Style Drift</Tip></div>
                                                        <div className="flex items-center gap-2 mt-1.5">
                                                            <span className="text-lg font-bold tabular-nums">{ia.factorDrift.aggregateDrift.toFixed(2)}</span>
                                                            <span className={`badge badge-xs ${ia.factorDrift.driftRating === 'Excellent' || ia.factorDrift.driftRating === 'Good' ? 'bg-success/20 text-success' : ia.factorDrift.driftRating === 'Moderate' ? 'bg-warning/20 text-warning' : 'bg-error/20 text-error'}`}>{ia.factorDrift.driftRating}</span>
                                                        </div>
                                                        {ia.factorDrift.hasStyleDrift && <div className="text-[9px] text-warning mt-1.5">⚠ Drift: {ia.factorDrift.styleDriftFlags.map(f => factorLabels[f] || f).join(', ')}</div>}
                                                    </div>
                                                )}
                                            </div>

                                            {/* ═══ Factor Profile Comparison (collapsible) ═══ */}
                                            {factorTiltEntries.length > 0 && (
                                                <details className="border-t border-white/[0.03] pt-3">
                                                    <summary className="text-xs opacity-60 cursor-pointer hover:opacity-80 flex items-center gap-1.5">
                                                        <BarChart3 className="w-3 h-3" /> Factor profile comparison
                                                        <span className="text-[9px] opacity-40 ml-1">(multi-factor tilts relative to proxies)</span>
                                                    </summary>
                                                    <div className="mt-2 overflow-x-auto">
                                                        <table className="table table-xs table-pro w-full">
                                                            <thead><tr><th>Factor Tilt</th><th className="text-right">Portfolio</th><th className="text-right">Replacement</th><th className="text-right">Drift</th></tr></thead>
                                                            <tbody>
                                                                {factorTiltEntries.map(([f, d]) => (
                                                                    <tr key={f} className={d.absDrift > 0.15 ? 'bg-warning/5' : ''}>
                                                                        <td><Tip term={factorLabels[f] || f} tip={factorTips[f] || f}><span className="font-bold text-xs">{factorLabels[f] || f}</span></Tip></td>
                                                                        <td className="text-right tabular-nums font-mono text-xs">{d.original.toFixed(3)}</td>
                                                                        <td className="text-right tabular-nums font-mono text-xs">{d.replacement.toFixed(3)}</td>
                                                                        <td className={`text-right tabular-nums font-mono text-xs font-bold ${d.drift > 0 ? 'text-success' : d.drift < 0 ? 'text-error' : ''}`}>{d.drift > 0 ? '+' : ''}{d.drift.toFixed(3)}{d.absDrift > 0.15 && ' ⚠'}</td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                        <p className="text-[9px] opacity-30 mt-1 italic">Tilts are partial regression coefficients against factor proxy ETFs — not standalone betas.</p>
                                                    </div>
                                                </details>
                                            )}

                                            {/* ═══ Sector Exposure (collapsible) ═══ */}
                                            {ia.sectorExposure && Object.keys(ia.sectorExposure.original).length > 0 && (
                                                <details className="border-t border-white/[0.03] pt-3">
                                                    <summary className="text-xs opacity-60 cursor-pointer hover:opacity-80 flex items-center gap-1.5">
                                                        <PieChart className="w-3 h-3" /> Sector exposure comparison
                                                    </summary>
                                                    <div className="mt-2 space-y-1.5">
                                                        {(() => {
                                                            const allSectors = new Set([
                                                                ...Object.keys(ia.sectorExposure!.original),
                                                                ...Object.keys(ia.sectorExposure!.replacement),
                                                            ]);
                                                            return [...allSectors].sort((a, b) =>
                                                                (ia.sectorExposure!.original[b] || 0) - (ia.sectorExposure!.original[a] || 0)
                                                            ).map((sector) => {
                                                                const origPct = ia.sectorExposure!.original[sector] || 0;
                                                                const replPct = ia.sectorExposure!.replacement[sector] || 0;
                                                                const diff = replPct - origPct;
                                                                return (
                                                                    <div key={sector} className="flex items-center gap-2 text-xs">
                                                                        <span className="w-32 truncate opacity-60">{sector}</span>
                                                                        <div className="w-10 text-right tabular-nums opacity-50">{origPct.toFixed(0)}%</div>
                                                                        <div className="flex-1 h-2 bg-base-200/40 rounded-full overflow-hidden"><div className="h-full bg-primary/50 rounded-full" style={{ width: `${Math.min(origPct, 100)}%` }} /></div>
                                                                        <div className="flex-1 h-2 bg-base-200/40 rounded-full overflow-hidden"><div className="h-full bg-accent/50 rounded-full" style={{ width: `${Math.min(replPct, 100)}%` }} /></div>
                                                                        <div className="w-10 tabular-nums opacity-50">{replPct.toFixed(0)}%</div>
                                                                        <div className={`w-12 text-right tabular-nums font-bold ${Math.abs(diff) > 10 ? 'text-warning' : 'opacity-40'}`}>{diff > 0 ? '+' : ''}{diff.toFixed(0)}%</div>
                                                                    </div>
                                                                );
                                                            });
                                                        })()}
                                                        <div className="flex gap-4 text-[9px] opacity-30 justify-center mt-1">
                                                            <span><span className="inline-block w-2 h-1.5 bg-primary/50 rounded-sm mr-1" />Portfolio</span>
                                                            <span><span className="inline-block w-2 h-1.5 bg-accent/50 rounded-sm mr-1" />ETF</span>
                                                        </div>
                                                    </div>
                                                </details>
                                            )}
                                        </div>
                                    );
                                };

                                /* ---- Helper to get metrics for a strategy ---- */
                                const getStrategyMetrics = (s: OptimizationSuggestion) => {
                                    if (s.type === 'optimal_blend') return { m: (s as OptimalBlendSuggestion).metrics, bm: (s as OptimalBlendSuggestion).baselineMetrics };
                                    if (s.type === 'selective_harvest') return { m: (s as SelectiveHarvestSuggestion).metrics, bm: (s as SelectiveHarvestSuggestion).baselineMetrics };
                                    return { m: (s as PartialRebalanceSuggestion).metrics, bm: (s as PartialRebalanceSuggestion).baselineMetrics };
                                };

                                return (
                                    <>
                                        {/* AI Recommendation Context — shown once above the tab bar since
                                            proxy injection affects all strategies equally */}
                                        {portfolioResult.aiRecommendations && portfolioResult.aiRecommendations.rationale && (
                                            <div className="glass-card bg-info/5 border-info/20">
                                                <div className="p-4">
                                                    <h4 className="font-bold text-sm text-info flex items-center gap-2 mb-2">
                                                        <Bot className="w-5 h-5" /> AI Proxy Generation Matrix
                                                    </h4>
                                                    <p className="text-xs opacity-80 mb-3 leading-relaxed">
                                                        {portfolioResult.aiRecommendations.rationale}
                                                    </p>
                                                    <div className="flex flex-wrap gap-6">
                                                        <div>
                                                            <span className="text-[10px] uppercase opacity-50 font-bold tracking-wider block mb-1">Stock Proxies Injected</span>
                                                            <div className="flex gap-1.5">
                                                                {portfolioResult.aiRecommendations.stocks.length > 0
                                                                    ? portfolioResult.aiRecommendations.stocks.map((s: string) => <span key={s} className="badge badge-sm badge-outline font-mono opacity-80">{s}</span>)
                                                                    : <span className="opacity-50 text-xs text-italic">None</span>}
                                                            </div>
                                                        </div>
                                                        <div>
                                                            <span className="text-[10px] uppercase opacity-50 font-bold tracking-wider block mb-1">ETF Proxies Injected</span>
                                                            <div className="flex gap-1.5">
                                                                {portfolioResult.aiRecommendations.etfs.length > 0
                                                                    ? portfolioResult.aiRecommendations.etfs.map((e: string) => <span key={e} className="badge badge-sm badge-outline font-mono opacity-80">{e}</span>)
                                                                    : <span className="opacity-50 text-xs text-italic">None</span>}
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        )}

                                        {/* ---- STRATEGY TAB BAR ---- */}
                                        <div className="glass-card border-accent/20">
                                            <div className="p-4">
                                                <h3 className="font-bold text-sm flex items-center gap-2 mb-3">
                                                    <Lightbulb className="w-5 h-5 text-accent" />
                                                    <Tip term="Replacement Strategies" tip="Each tab is a complete strategy for harvesting losses while maintaining exposure. Institutional risk analysis is included per strategy.">
                                                        Choose a Replacement Strategy
                                                    </Tip>
                                                </h3>
                                                <div className="flex flex-wrap gap-2">
                                                    {/* Default ETF tab */}
                                                    <button
                                                        onClick={() => setActiveTab('default')}
                                                        className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all border ${activeTab === 'default' ? 'bg-accent/20 border-accent/40 text-accent ring-1 ring-accent/20' : 'bg-base-200/30 border-white/[0.03] hover:bg-base-200/50 opacity-70'}`}
                                                    >
                                                        <Award className="w-3.5 h-3.5" />
                                                        ETF-Only ({bestETF.ticker})
                                                        <span className="badge badge-xs font-mono ml-1">{bestETF.correlation}% ρ</span>
                                                    </button>
                                                    {/* Optimization strategy tabs */}
                                                    {optSugs.map((s, i) => (
                                                        <button
                                                            key={i}
                                                            onClick={() => setActiveTab(i)}
                                                            className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium transition-all border ${activeTab === i ? 'bg-accent/20 border-accent/40 text-accent ring-1 ring-accent/20' : 'bg-base-200/30 border-white/[0.03] hover:bg-base-200/50 opacity-70'}`}
                                                        >
                                                            {s.type === 'optimal_blend' && <><Blend className="w-3.5 h-3.5" /> Optimized Blend</>}
                                                            {s.type === 'selective_harvest' && <><Filter className="w-3.5 h-3.5" /> Selective Harvest</>}
                                                            {s.type === 'partial_rebalance' && <><Scissors className="w-3.5 h-3.5" /> Partial ({(s as PartialRebalanceSuggestion).sellPct}%)</>}
                                                            <span className="badge badge-xs font-mono ml-1">{s.score.toFixed(2)}</span>
                                                            {i === 0 && <span className="badge badge-xs bg-accent/20 text-accent border-accent/30">Best</span>}
                                                        </button>
                                                    ))}
                                                </div>
                                            </div>
                                        </div>

                                        {/* ============================================================ */}
                                        {/* DEFAULT TAB: ETF-Only Strategy                              */}
                                        {/* ============================================================ */}
                                        {activeTab === 'default' && (
                                            <>
                                                {/* ETF Summary Banner */}
                                                <div className="glass-card border-accent/30 bg-gradient-to-r from-accent/5 to-transparent">
                                                    <div className="p-5">
                                                        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
                                                            <div className="flex-1">
                                                                <h3 className="font-bold text-lg flex items-center gap-2 text-accent">
                                                                    <Award className="w-6 h-6" />
                                                                    Best Unified Replacement: {bestETF.ticker}
                                                                </h3>
                                                                <p className="text-sm opacity-80 mt-1">{bestETF.name}</p>
                                                                <p className="text-xs opacity-60 mt-1">
                                                                    This single ETF has the highest correlation with your combined portfolio.
                                                                    Sell all {portfolioResult.summary.harvestableCount} loss-making positions and buy {bestETF.ticker} to maintain similar exposure.
                                                                </p>
                                                            </div>
                                                            <div className="flex flex-wrap gap-3">
                                                                <div className="bg-base-200/40 rounded-xl px-4 py-2 border border-white/[0.03] text-center min-w-[100px]">
                                                                    <div className="text-xs opacity-60">Correlation</div>
                                                                    <div className="text-xl font-bold text-accent tabular-nums">{bestETF.correlation}%</div>
                                                                </div>
                                                                <div className="bg-base-200/40 rounded-xl px-4 py-2 border border-white/[0.03] text-center min-w-[100px]">
                                                                    <div className="text-xs opacity-60">Buy Shares</div>
                                                                    <div className="text-xl font-bold text-info tabular-nums">{bestETF.sharesToBuy}</div>
                                                                </div>
                                                                <div className="bg-base-200/40 rounded-xl px-4 py-2 border border-white/[0.03] text-center min-w-[100px]">
                                                                    <div className="text-xs opacity-60">ETF Price</div>
                                                                    <div className="text-xl font-bold tabular-nums">${bestETF.price?.toFixed(2)}</div>
                                                                </div>
                                                                <div className="bg-base-200/40 rounded-xl px-4 py-2 border border-white/[0.03] text-center min-w-[100px]">
                                                                    <div className="text-xs opacity-60">Total Cost</div>
                                                                    <div className="text-xl font-bold text-success tabular-nums">${bestETF.totalCost.toLocaleString()}</div>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>

                                                {/* Action Steps for Default Strategy */}
                                                <div className="glass-card">
                                                    <div className="p-5 space-y-4">
                                                        <h3 className="font-bold text-sm flex items-center gap-2 mb-2">
                                                            <Zap className="w-5 h-5 text-warning" /> Action Plan
                                                        </h3>
                                                        <ActionStep step={1} icon={<Trash2 className="w-3.5 h-3.5 text-error" />} title="Sell all loss-making positions">
                                                            <div className="text-xs opacity-70">
                                                                Sell {portfolioResult.harvestable.map(h => <span key={h.ticker} className="font-bold text-error">{h.ticker} ({h.shares} shares)</span>).reduce((prev, curr, i) => i === 0 ? [curr] : [...prev, ', ', curr], [] as any[])}
                                                                {' '}to realize <span className="font-bold text-warning">${portfolioResult.summary.totalHarvestableLosses.toLocaleString()}</span> in losses
                                                                {' '}→ <span className="font-bold text-success">${portfolioResult.summary.totalTaxSavings.toLocaleString()}</span> tax savings.
                                                            </div>
                                                        </ActionStep>
                                                        <ActionStep step={2} icon={<Plus className="w-3.5 h-3.5 text-success" />} title={`Buy ${bestETF.sharesToBuy} shares of ${bestETF.ticker}`}>
                                                            <p className="text-xs opacity-60">
                                                                Invest ${bestETF.totalCost.toLocaleString()} into {bestETF.ticker} ({bestETF.name}) to maintain broad exposure. {bestETF.correlation}% correlated with your portfolio.
                                                            </p>
                                                        </ActionStep>
                                                        <ActionStep step={3} icon={<Clock className="w-3.5 h-3.5 text-info" />} title="Hold for 30+ days (wash sale rule)">
                                                            <p className="text-xs opacity-60">Keep {bestETF.ticker} for at least 31 days. After the wash-sale window, you may switch back to your original holdings if desired.</p>
                                                        </ActionStep>
                                                    </div>
                                                </div>

                                                {/* Portfolio Weightage vs ETF Overlap */}
                                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                                    <div className="glass-card">
                                                        <div className="p-5">
                                                            <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
                                                                <PieChart className="w-5 h-5 text-primary" /> Your Portfolio Weights
                                                            </h3>
                                                            <div className="space-y-2">
                                                                {[...portfolioResult.harvestable, ...portfolioResult.nonHarvestable].map((h, i) => (
                                                                    <div key={i} className="flex items-center gap-3">
                                                                        <span className={`font-bold text-sm w-14 ${h.harvestable ? 'text-error' : 'text-success'}`}>{h.ticker}</span>
                                                                        <div className="flex-1 bg-base-200/30 rounded-full h-4 overflow-hidden">
                                                                            <div className={`h-full rounded-full ${h.harvestable ? 'bg-error/60' : 'bg-success/40'}`} style={{ width: `${Math.min(h.portfolioWeight || 0, 100)}%` }} />
                                                                        </div>
                                                                        <span className="text-xs font-mono tabular-nums w-16 text-right opacity-70">{h.portfolioWeight?.toFixed(1)}%</span>
                                                                        <span className={`text-xs w-16 text-right ${h.harvestable ? 'badge badge-error badge-xs' : 'badge badge-success badge-xs'}`}>
                                                                            {h.harvestable ? 'Harvest' : 'Gain'}
                                                                        </span>
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <div className="glass-card">
                                                        <div className="p-5">
                                                            <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
                                                                <Layers className="w-5 h-5 text-accent" /> {bestETF.ticker} Holdings Overlap
                                                            </h3>
                                                            {pr.holdingsOverlap.available && pr.holdingsOverlap.overlap.length > 0 ? (
                                                                <>
                                                                    <p className="text-xs opacity-60 mb-3">
                                                                        Your holdings found in {bestETF.ticker}
                                                                        {pr.holdingsOverlap.totalHoldings && ` (${pr.holdingsOverlap.totalHoldings} total holdings)`}:
                                                                    </p>
                                                                    <div className="space-y-2">
                                                                        {pr.holdingsOverlap.overlap.map((o, i) => (
                                                                            <div key={i} className="flex items-center gap-3">
                                                                                <span className="font-bold text-sm w-14 text-accent">{o.ticker}</span>
                                                                                <div className="flex-1 bg-base-200/30 rounded-full h-4 overflow-hidden">
                                                                                    <div className="h-full rounded-full bg-accent/50" style={{ width: `${Math.min(o.etfWeight * 10, 100)}%` }} />
                                                                                </div>
                                                                                <span className="text-xs font-mono tabular-nums w-16 text-right">{o.etfWeight}%</span>
                                                                            </div>
                                                                        ))}
                                                                    </div>
                                                                    {pr.holdingsOverlap.overlapPct != null && (
                                                                        <div className="mt-3 pt-3 border-t border-white/[0.05] flex justify-between text-sm">
                                                                            <span className="opacity-70">Total Overlap Weight</span>
                                                                            <span className="font-bold text-accent">{pr.holdingsOverlap.overlapPct}%</span>
                                                                        </div>
                                                                    )}
                                                                </>
                                                            ) : (
                                                                <div className="bg-base-200/30 rounded-xl p-4 border border-white/[0.03]">
                                                                    <p className="text-sm opacity-70 flex items-center gap-2">
                                                                        <Info className="w-4 h-4 text-info" />
                                                                        ETF holdings data not available for {bestETF.ticker}. The {bestETF.correlation}% correlation confirms strong alignment based on 1-year price history.
                                                                    </p>
                                                                </div>
                                                            )}
                                                        </div>
                                                    </div>
                                                </div>

                                                {/* Charts: Portfolio vs ETF */}
                                                {pr.chartData && (
                                                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                                        <div className="glass-card">
                                                            <div className="p-5">
                                                                <h3 className="font-bold text-sm flex items-center gap-2">
                                                                    <TrendingUp className="w-5 h-5 text-primary" /> Portfolio vs {bestETF.ticker} (1Y Normalized)
                                                                </h3>
                                                                <p className="text-xs opacity-60 mt-1 mb-3">Value-weighted portfolio performance vs {bestETF.ticker}</p>
                                                                <div className="h-56 relative">
                                                                    <Line
                                                                        data={{
                                                                            labels: pr.chartData.dates,
                                                                            datasets: [
                                                                                { label: 'Your Portfolio', data: pr.chartData.portfolioNorm, borderColor: 'rgba(99, 102, 241, 1)', borderWidth: 2, pointRadius: 0, tension: 0.1 },
                                                                                { label: bestETF.ticker, data: pr.chartData.etfNorm, borderColor: 'rgba(16, 185, 129, 1)', borderWidth: 2, borderDash: [5, 5], pointRadius: 0, tension: 0.1 },
                                                                            ]
                                                                        }}
                                                                        options={{ responsive: true, maintainAspectRatio: false, interaction: { mode: 'index' as const, intersect: false }, scales: { x: { display: false }, y: { grid: { color: 'rgba(156, 163, 175, 0.1)' } } } }}
                                                                    />
                                                                </div>
                                                            </div>
                                                        </div>
                                                        <div className="glass-card">
                                                            <div className="p-5">
                                                                <h3 className="font-bold text-sm flex items-center gap-2">
                                                                    <BarChart3 className="w-5 h-5 text-accent" /> Spread ({bestETF.ticker} - Portfolio)
                                                                </h3>
                                                                <div className="text-xs flex gap-4 opacity-60 mt-1 mb-3">
                                                                    <span>Mean: {pr.chartData.spreadMean.toFixed(2)}</span>
                                                                    <span>Std: {pr.chartData.spreadStd.toFixed(2)}</span>
                                                                    <span className="font-bold text-accent">Z: {bestETF.zScore.toFixed(2)}</span>
                                                                </div>
                                                                <div className="h-56 relative">
                                                                    <Line
                                                                        data={{
                                                                            labels: pr.chartData.dates,
                                                                            datasets: [
                                                                                { label: 'Spread', data: pr.chartData.spread, borderColor: 'rgba(139, 92, 246, 1)', backgroundColor: 'rgba(139, 92, 246, 0.1)', borderWidth: 2, fill: true, pointRadius: 0 },
                                                                                { label: '+1σ', data: Array(pr.chartData.spread.length).fill(pr.chartData.spreadMean + pr.chartData.spreadStd), borderColor: 'rgba(156, 163, 175, 0.5)', borderDash: [2, 2], pointRadius: 0, borderWidth: 1 },
                                                                                { label: '-1σ', data: Array(pr.chartData.spread.length).fill(pr.chartData.spreadMean - pr.chartData.spreadStd), borderColor: 'rgba(156, 163, 175, 0.5)', borderDash: [2, 2], pointRadius: 0, borderWidth: 1 },
                                                                            ]
                                                                        }}
                                                                        options={{ responsive: true, maintainAspectRatio: false, interaction: { mode: 'index' as const, intersect: false }, scales: { x: { display: false }, y: { grid: { color: 'rgba(156, 163, 175, 0.1)' } } } }}
                                                                    />
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </div>
                                                )}

                                                {/* Baseline Metrics for ETF Only */}
                                                {optSugs.length > 0 && optSugs[0].baselineMetrics && (
                                                    <div className="glass-card mb-4 mt-4">
                                                        <div className="p-5">
                                                            <div className="text-[10px] font-bold uppercase tracking-wider opacity-50 mb-1 flex items-center gap-1">
                                                                <BarChart3 className="w-3 h-3" />
                                                                <Tip term="Baseline Quality Metrics" tip="Standalone metrics for the ETF-only replacement strategy relative to your original portfolio.">
                                                                    {bestETF.ticker} Standalone Metrics
                                                                </Tip>
                                                            </div>
                                                            <MetricsBar m={optSugs[0].baselineMetrics} />
                                                        </div>
                                                    </div>
                                                )}
                                                
                                                {/* Institutional Analysis for Default Strategy */}
                                                <div className="glass-card border-primary/20">
                                                    <div className="p-5">
                                                        <h3 className="font-bold text-sm flex items-center gap-2 mb-1">
                                                            <Building2 className="w-5 h-5 text-primary" />
                                                            Institutional Risk Analysis
                                                            <span className="badge badge-xs bg-primary/20 text-primary border-primary/30">for ETF-Only ({bestETF.ticker})</span>
                                                        </h3>
                                                        <p className="text-xs opacity-60 mb-2">
                                                            Multi-factor risk model, trade trigger economics, and style drift assessment for the {bestETF.ticker} replacement strategy.
                                                        </p>
                                                        <InstitutionalSection strategyLabel={`ETF-Only (${bestETF.ticker})`} />
                                                    </div>
                                                </div>
                                            </>
                                        )}

                                        {/* ============================================================ */}
                                        {/* OPTIMIZATION STRATEGY TABS                                  */}
                                        {/* ============================================================ */}
                                        {typeof activeTab === 'number' && optSugs[activeTab] && (() => {
                                            const s = optSugs[activeTab];
                                            const { m, bm } = getStrategyMetrics(s);

                                            return (
                                                <>
                                                    <div className="glass-card border-accent/20">
                                                        <div className="p-5 space-y-4">
                                                            {/* Strategy header */}
                                                            <div className="flex items-center gap-3">
                                                                <div className="w-8 h-8 rounded-lg bg-accent/20 text-accent flex items-center justify-center text-sm font-bold">
                                                                    {activeTab === 0 ? '★' : `#${activeTab + 1}`}
                                                                </div>
                                                                <div>
                                                                    <div className="font-bold text-sm flex items-center gap-2">
                                                                        {s.type === 'optimal_blend' && <><Blend className="w-4 h-4 text-accent" /> {s.title || "Optimal Replacement Blend"}</>}
                                                                        {s.type === 'selective_harvest' && <><Filter className="w-4 h-4 text-info" /> Selective Harvesting</>}
                                                                        {s.type === 'partial_rebalance' && <><Scissors className="w-4 h-4 text-warning" /> Partial Rebalance ({(s as PartialRebalanceSuggestion).sellPct}%)</>}
                                                                        <span className="badge badge-sm font-mono bg-accent/20 text-accent border-accent/30">{s.score.toFixed(3)}</span>
                                                                        {s.type === 'optimal_blend' && (s as OptimalBlendSuggestion).anchored && (
                                                                            <span className="badge badge-sm badge-info gap-1"><CheckCircle className="w-3 h-3" /> Your picks</span>
                                                                        )}
                                                                        {s.type === 'optimal_blend' && (s as OptimalBlendSuggestion).belowBaseline && (
                                                                            <span className="badge badge-sm badge-warning gap-1"><AlertTriangle className="w-3 h-3" /> Below ETF baseline</span>
                                                                        )}
                                                                    </div>
                                                                    <p className="text-xs opacity-50 mt-0.5">{s.description}</p>
                                                                    {s.type === 'optimal_blend' && (s as OptimalBlendSuggestion).aiRationale && (
                                                                        <div className="mt-2 text-xs bg-primary/10 border border-primary/20 text-primary p-2 rounded flex items-start gap-2 max-w-2xl">
                                                                            <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                                                                            <span><b>AI Insight:</b> {(s as OptimalBlendSuggestion).aiRationale}</span>
                                                                        </div>
                                                                    )}
                                                                </div>
                                                            </div>

                                                            {/* ==================== OPTIMAL BLEND ==================== */}
                                                            {s.type === 'optimal_blend' && (() => {
                                                                const ob = s as OptimalBlendSuggestion;
                                                                const bestETFTicker = pr.bestETF.ticker;
                                                                return (
                                                                    <div className="space-y-4">
                                                                        <ActionStep step={1} icon={<Trash2 className="w-3.5 h-3.5 text-error" />} title="Sell all loss-making positions">
                                                                            <div className="text-xs opacity-70">
                                                                                Sell {portfolioResult.harvestable.map(h => <span key={h.ticker} className="font-bold text-error">{h.ticker} ({h.shares} shares)</span>).reduce((prev, curr, i) => i === 0 ? [curr] : [...prev, ', ', curr], [] as any[])}
                                                                                {' '}to realize <span className="font-bold text-warning">${portfolioResult.summary.totalHarvestableLosses.toLocaleString()}</span> in losses.
                                                                            </div>
                                                                        </ActionStep>
                                                                        <ActionStep step={2} icon={<Plus className="w-3.5 h-3.5 text-success" />} title="Buy replacement blend">
                                                                            <p className="text-xs opacity-60 mb-2">Use the ${ob.totalCapital.toLocaleString()} in proceeds to buy this optimized blend:</p>
                                                                            <div className="overflow-x-auto">
                                                                                <table className="table table-xs table-pro w-full">
                                                                                    <thead><tr><th>Buy</th><th className="text-right">Weight</th><th className="text-right">Capital</th><th className="text-right">Price</th><th className="text-right">Shares</th></tr></thead>
                                                                                    <tbody>
                                                                                        {ob.allocation.map((a, i) => (
                                                                                            <tr key={i}>
                                                                                                <td><span className="font-bold text-success">{a.ticker}</span>{a.ticker === bestETFTicker && <span className="text-[9px] ml-1 opacity-40">ETF</span>}</td>
                                                                                                <td className="text-right tabular-nums">{a.weight}%</td>
                                                                                                <td className="text-right tabular-nums">${a.capital.toLocaleString()}</td>
                                                                                                <td className="text-right tabular-nums opacity-60">{a.price ? `$${a.price}` : '—'}</td>
                                                                                                <td className="text-right tabular-nums font-bold text-success">{a.shares}</td>
                                                                                            </tr>
                                                                                        ))}
                                                                                        <tr className="border-t border-white/10 opacity-60 text-[10px]">
                                                                                            <td className="font-bold uppercase tracking-wide">Total</td>
                                                                                            <td className="text-right tabular-nums font-bold">{ob.allocation.reduce((s, a) => s + a.weight, 0).toFixed(1)}%</td>
                                                                                            <td className="text-right tabular-nums font-bold">${ob.allocation.reduce((s, a) => s + a.capital, 0).toLocaleString()}</td>
                                                                                            <td />
                                                                                            <td />
                                                                                        </tr>
                                                                                    </tbody>
                                                                                </table>
                                                                            </div>
                                                                        </ActionStep>
                                                                        <ActionStep step={3} icon={<Clock className="w-3.5 h-3.5 text-info" />} title="Hold for 30+ days (wash sale rule)">
                                                                            <p className="text-xs opacity-60">Keep the blend for at least 31 days. After the wash-sale window, you may switch back to your original holdings if desired.</p>
                                                                        </ActionStep>
                                                                        {ob.alternateBlends.length > 0 && (
                                                                            <details className="mt-2">
                                                                                <summary className="text-xs opacity-60 cursor-pointer hover:opacity-80">{ob.alternateBlends.length} other blend{ob.alternateBlends.length > 1 ? 's' : ''} evaluated</summary>
                                                                                <div className="mt-2 space-y-1">
                                                                                    {ob.alternateBlends.map((alt, ai) => (
                                                                                        <div key={ai} className="bg-base-200/20 rounded-lg p-2 border border-white/[0.02] text-xs flex items-center gap-2 flex-wrap">
                                                                                            <span className="font-bold">{Object.entries(alt.weights).map(([t, w]) => `${t} ${(w * 100).toFixed(0)}%`).join(' + ')}</span>
                                                                                            <span className="opacity-40">|</span>
                                                                                            <Tip term="ρ" tip="Correlation coefficient">{alt.metrics.correlationPct}%</Tip>
                                                                                            <Tip term="TE" tip="Tracking error">{alt.metrics.trackingError}%</Tip>
                                                                                            <Tip term="β" tip="Beta">{alt.metrics.beta}</Tip>
                                                                                            <span className="opacity-40 font-mono">Score {alt.score}</span>
                                                                                        </div>
                                                                                    ))}
                                                                                </div>
                                                                            </details>
                                                                        )}
                                                                    </div>
                                                                );
                                                            })()}

                                                            {/* ==================== SELECTIVE HARVEST ==================== */}
                                                            {s.type === 'selective_harvest' && (() => {
                                                                const sh = s as SelectiveHarvestSuggestion;
                                                                return (
                                                                    <div className="space-y-4">
                                                                        <ActionStep step={1} icon={<Trash2 className="w-3.5 h-3.5 text-error" />} title="Sell these positions only">
                                                                            <div className="flex flex-wrap gap-2 mt-1">
                                                                                {sh.harvestTickers.map(t => {
                                                                                    const h = portfolioResult.harvestable.find(x => x.ticker === t);
                                                                                    return (
                                                                                        <div key={t} className="bg-error/10 border border-error/20 rounded-lg px-3 py-1.5 text-xs">
                                                                                            <span className="font-bold text-error">{t}</span>
                                                                                            {h && <span className="opacity-50 ml-1">{h.shares} shares · -${Math.abs(h.losses || 0).toLocaleString()}</span>}
                                                                                        </div>
                                                                                    );
                                                                                })}
                                                                            </div>
                                                                            <div className="text-xs opacity-50 mt-1.5">
                                                                                Tax savings: <span className="font-bold text-success">${sh.taxSavings.toLocaleString()}</span> ({sh.taxCapturePct}% of total losses)
                                                                            </div>
                                                                        </ActionStep>
                                                                        <ActionStep step={2} icon={<ShieldCheck className="w-3.5 h-3.5 text-success" />} title="Keep these positions (do not sell)">
                                                                            <div className="flex flex-wrap gap-2 mt-1">
                                                                                {sh.keepTickers.map(t => (
                                                                                    <div key={t} className="bg-success/10 border border-success/20 rounded-lg px-3 py-1.5 text-xs">
                                                                                        <span className="font-bold text-success">{t}</span>
                                                                                        <span className="opacity-50 ml-1">keep all shares</span>
                                                                                    </div>
                                                                                ))}
                                                                            </div>
                                                                            <p className="text-xs opacity-50 mt-1.5">Keeping these improves portfolio correlation vs selling everything.</p>
                                                                        </ActionStep>
                                                                        <ActionStep step={3} icon={<Plus className="w-3.5 h-3.5 text-accent" />} title={`Buy ${bestETF.ticker} with proceeds`}>
                                                                            <p className="text-xs opacity-60">
                                                                                Invest the ${sh.losses.toLocaleString()} in sale proceeds into <span className="font-bold">{bestETF.ticker}</span> ({bestETF.name}) to maintain broad exposure.
                                                                            </p>
                                                                        </ActionStep>
                                                                        {sh.alternates && sh.alternates.length > 0 && (
                                                                            <details className="mt-2">
                                                                                <summary className="text-xs opacity-60 cursor-pointer hover:opacity-80">{sh.alternates.length} other subset{sh.alternates.length > 1 ? 's' : ''} evaluated</summary>
                                                                                <div className="mt-2 space-y-1">
                                                                                    {sh.alternates.map((alt, ai) => (
                                                                                        <div key={ai} className="bg-base-200/20 rounded-lg p-2 border border-white/[0.02] text-xs">
                                                                                            <span className="text-error font-bold">Sell {alt.harvestTickers.join(', ')}</span>
                                                                                            <span className="text-success font-bold ml-1">Keep {alt.keepTickers.join(', ')}</span>
                                                                                            <span className="opacity-50 ml-2">${alt.losses.toLocaleString()} · {alt.taxCapturePct}% · ρ {alt.metrics.correlationPct}% · Score {alt.score}</span>
                                                                                        </div>
                                                                                    ))}
                                                                                </div>
                                                                            </details>
                                                                        )}
                                                                    </div>
                                                                );
                                                            })()}

                                                            {/* ==================== PARTIAL REBALANCE ==================== */}
                                                            {s.type === 'partial_rebalance' && (() => {
                                                                const prs = s as PartialRebalanceSuggestion;
                                                                return (
                                                                    <div className="space-y-4">
                                                                        <ActionStep step={1} icon={<Scissors className="w-3.5 h-3.5 text-warning" />} title={`Sell ${prs.sellPct}% of each losing position`}>
                                                                            <div className="overflow-x-auto mt-1">
                                                                                <table className="table table-xs table-pro w-full">
                                                                                    <thead><tr><th>Ticker</th><th className="text-right">Sell</th><th className="text-right">Keep</th><th className="text-right">Proceeds</th><th className="text-right">Loss Realized</th></tr></thead>
                                                                                    <tbody>
                                                                                        {prs.sellActions.map((a, i) => (
                                                                                            <tr key={i}>
                                                                                                <td className="font-bold">{a.ticker}</td>
                                                                                                <td className="text-right tabular-nums text-error">{a.sellShares} shares</td>
                                                                                                <td className="text-right tabular-nums text-success">{a.keepShares} shares</td>
                                                                                                <td className="text-right tabular-nums">${a.proceeds.toLocaleString()}</td>
                                                                                                <td className="text-right tabular-nums text-error">-${a.lossRealized.toLocaleString()}</td>
                                                                                            </tr>
                                                                                        ))}
                                                                                    </tbody>
                                                                                </table>
                                                                            </div>
                                                                            <div className="text-xs opacity-50 mt-1.5">
                                                                                You keep {100 - prs.sellPct}% of each position for direct exposure. Tax savings: <span className="font-bold text-success">${prs.taxSavings.toLocaleString()}</span>
                                                                            </div>
                                                                        </ActionStep>
                                                                        <ActionStep step={2} icon={<Plus className="w-3.5 h-3.5 text-success" />} title={`Buy replacement with $${prs.totalProceeds.toLocaleString()} proceeds`}>
                                                                            <div className="overflow-x-auto mt-1">
                                                                                <table className="table table-xs table-pro w-full">
                                                                                    <thead><tr><th>Buy</th><th className="text-right">Weight</th><th className="text-right">Price</th><th className="text-right">Shares</th><th className="text-right">Capital</th></tr></thead>
                                                                                    <tbody>
                                                                                        {prs.buyActions.map((a, i) => (
                                                                                            <tr key={i}>
                                                                                                <td className="font-bold text-success">{a.ticker}</td>
                                                                                                <td className="text-right tabular-nums">{a.weight}%</td>
                                                                                                <td className="text-right tabular-nums opacity-60">{a.price ? `$${a.price}` : '—'}</td>
                                                                                                <td className="text-right tabular-nums font-bold text-success">{a.shares}</td>
                                                                                                <td className="text-right tabular-nums">${a.capital.toLocaleString()}</td>
                                                                                            </tr>
                                                                                        ))}
                                                                                    </tbody>
                                                                                </table>
                                                                            </div>
                                                                        </ActionStep>
                                                                        <ActionStep step={3} icon={<Clock className="w-3.5 h-3.5 text-info" />} title="Hold for 30+ days (wash sale rule)">
                                                                            <p className="text-xs opacity-60">Wait 31 days before repurchasing any sold shares to avoid wash sale disqualification.</p>
                                                                        </ActionStep>
                                                                        {prs.alternates && prs.alternates.length > 0 && (
                                                                            <details className="mt-2">
                                                                                <summary className="text-xs opacity-60 cursor-pointer hover:opacity-80">{prs.alternates.length} other sell levels evaluated</summary>
                                                                                <div className="mt-2 space-y-1">
                                                                                    {prs.alternates.map((alt, ai) => (
                                                                                        <div key={ai} className="bg-base-200/20 rounded-lg p-2 border border-white/[0.02] text-xs">
                                                                                            <span className="font-bold">{alt.sellPct}% sell</span>
                                                                                            <span className="opacity-50 ml-2">${alt.totalLossCaptured.toLocaleString()} loss · {alt.taxCapturePct}% · ρ {alt.metrics.correlationPct}% · TE {alt.metrics.trackingError}% · Score {alt.score}</span>
                                                                                        </div>
                                                                                    ))}
                                                                                </div>
                                                                            </details>
                                                                        )}
                                                                    </div>
                                                                );
                                                            })()}

                                                            {/* Quant metrics comparison */}
                                                            <div className="mt-4 pt-3 border-t border-white/[0.05]">
                                                                <div className="text-[10px] font-bold uppercase tracking-wider opacity-50 mb-1 flex items-center gap-1">
                                                                    <BarChart3 className="w-3 h-3" />
                                                                    <Tip term="Quality Metrics" tip="Measures how well the replacement portfolio tracks your original. Green arrows = improvement over ETF-only replacement.">
                                                                        Strategy Quality vs ETF-Only Baseline
                                                                    </Tip>
                                                                </div>
                                                                <MetricsBar m={m} baseline={bm} />
                                                            </div>

                                                        </div>
                                                    </div>

                                                    {/* Institutional Analysis for this Strategy */}
                                                    <div className="glass-card border-primary/20">
                                                        <div className="p-5">
                                                            <h3 className="font-bold text-sm flex items-center gap-2 mb-1">
                                                                <Building2 className="w-5 h-5 text-primary" />
                                                                Institutional Risk Analysis
                                                                <span className="badge badge-xs bg-primary/20 text-primary border-primary/30">
                                                                    for {s.type === 'optimal_blend' ? 'Optimized Blend' : s.type === 'selective_harvest' ? 'Selective Harvest' : `Partial ${(s as PartialRebalanceSuggestion).sellPct}%`}
                                                                </span>
                                                            </h3>
                                                            <p className="text-xs opacity-60 mb-2">
                                                                Multi-factor risk model, trade trigger economics, and style drift assessment for this strategy.
                                                            </p>
                                                            <InstitutionalSection
                                                                strategyMetrics={m}
                                                                strategyTaxSavings={
                                                                    s.type === 'optimal_blend' ? portfolioResult.summary.totalTaxSavings :
                                                                    s.type === 'selective_harvest' ? (s as SelectiveHarvestSuggestion).taxSavings :
                                                                    (s as PartialRebalanceSuggestion).taxSavings
                                                                }
                                                                backendTradeTrigger={s.tradeTrigger}
                                                                teDecomposition={s.type === 'optimal_blend' ? (s as OptimalBlendSuggestion).teDecomposition : undefined}
                                                                strategyLabel={
                                                                    s.type === 'optimal_blend' ? 'Optimized Blend' :
                                                                    s.type === 'selective_harvest' ? 'Selective Harvest' :
                                                                    `Partial ${(s as PartialRebalanceSuggestion).sellPct}%`
                                                                }
                                                            />
                                                        </div>
                                                    </div>
                                                </>
                                            );
                                        })()}

                                        {/* ---- ALTERNATE ETF SELECTOR (always visible) ---- */}
                                        <div className="glass-card">
                                            <div className="p-5">
                                                <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
                                                    <ArrowRightLeft className="w-5 h-5 text-info" /> Switch Replacement ETF / Stock
                                                </h3>
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                                                    {/* ETF Override */}
                                                    <div className="form-control bg-base-100/50 p-3 rounded-xl border border-white/5">
                                                        <label className="label py-0 mb-1">
                                                            <span className="label-text text-xs font-medium">Custom Replacement ETF <span className="opacity-50">(Max 1)</span></span>
                                                        </label>
                                                        <div className="flex gap-2">
                                                            <input
                                                                type="text"
                                                                className="input input-bordered input-sm w-full font-mono uppercase"
                                                                value={customETFInput}
                                                                onChange={e => setCustomETFInput(e.target.value.toUpperCase())}
                                                                placeholder="e.g. IGV"
                                                            />
                                                            {preferredETF && (
                                                                <button
                                                                    className="btn btn-sm btn-ghost btn-outline px-2 text-info"
                                                                    onClick={() => {
                                                                        setPreferredETF(null);
                                                                        setCustomETFInput('');
                                                                    }}
                                                                    disabled={loading}
                                                                    title="Clear Custom ETF"
                                                                >
                                                                    ✕
                                                                </button>
                                                            )}
                                                        </div>
                                                        {preferredETF && (
                                                            <div className="mt-2 text-xs text-info flex items-center gap-1">
                                                                <CheckCircle className="w-3 h-3" /> Currently anchoring calculation to: <b>{preferredETF}</b>
                                                            </div>
                                                        )}
                                                    </div>

                                                    {/* Custom Stocks Override */}
                                                    <div className="form-control bg-base-100/50 p-3 rounded-xl border border-white/5">
                                                        <label className="label py-0 mb-1">
                                                            <span className="label-text text-xs font-medium">Custom Replacement Stocks <span className="opacity-50">(Max 3, comma separated)</span></span>
                                                        </label>
                                                        <div className="flex gap-2">
                                                            <input
                                                                type="text"
                                                                className="input input-bordered input-sm w-full font-mono uppercase"
                                                                value={customStocksRaw}
                                                                onChange={e => setCustomStocksRaw(e.target.value.toUpperCase())}
                                                                onBlur={e => {
                                                                    const parsed = e.target.value.split(',').map((s: string) => s.trim().toUpperCase()).filter((s: string) => s).slice(0, 3);
                                                                    setCustomStocks(parsed);
                                                                    setCustomStocksRaw(parsed.join(', '));
                                                                }}
                                                                placeholder="e.g. CRWD, PANW, FTNT"
                                                            />
                                                            {(customStocks.length > 0 || customStocksRaw.trim() !== '') && (
                                                                <button
                                                                    className="btn btn-sm btn-ghost btn-outline px-2 text-info"
                                                                    onClick={() => { setCustomStocks([]); setCustomStocksRaw(''); }}
                                                                    disabled={loading}
                                                                    title="Clear Custom Stocks"
                                                                >
                                                                    ✕
                                                                </button>
                                                            )}
                                                        </div>
                                                        {customStocks.length > 0 && (
                                                            <div className="mt-2 text-xs text-info flex items-center gap-1">
                                                                <CheckCircle className="w-3 h-3" /> Evaluating: <b>{customStocks.join(', ')}</b>
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>
                                                
                                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
                                                    <p className="text-[11px] opacity-50 flex items-center gap-1">
                                                        <Info className="w-3 h-3 shrink-0" /> Apply your overrides with this button. The main “Analyze Portfolio” button runs a fresh auto pass and clears overrides.
                                                    </p>
                                                    <button
                                                        className="btn btn-sm btn-info w-full sm:w-auto"
                                                        disabled={loading || (!customETFInput.trim() && !customStocksRaw.trim())}
                                                        onClick={() => {
                                                            // Parse straight from the raw inputs at click time so the
                                                            // request always reflects what's on screen (no onBlur race).
                                                            const etf = customETFInput.trim().toUpperCase();
                                                            const parsedStocks = customStocksRaw
                                                                .split(',').map(s => s.trim().toUpperCase()).filter(Boolean).slice(0, 3);
                                                            setCustomStocks(parsedStocks);
                                                            setCustomStocksRaw(parsedStocks.join(', '));
                                                            if (etf) setPreferredETF(etf);
                                                            calculatePortfolio(etf || null, false, parsedStocks);
                                                        }}
                                                    >
                                                        {loading ? <span className="loading loading-spinner loading-xs" /> : <ExternalLink className="w-3.5 h-3.5 mr-1" />}
                                                        Re-Analyze Grid Overrides
                                                    </button>
                                                </div>
                                                {pr.alternateETFs && pr.alternateETFs.length > 0 && (
                                                    <div className="overflow-x-auto">
                                                        <p className="text-xs opacity-60 mb-2">
                                                            {portfolioResult.overridesActive
                                                                ? 'Auto-discovered alternates (for comparison) — your override is anchored above. Click any to switch the anchor:'
                                                                : 'Auto-discovered alternates, ranked by tracking error. Click any ETF below to re-analyze with it as the replacement:'}
                                                        </p>
                                                        <table className="table table-sm table-pro">
                                                            <thead>
                                                                <tr><th>Rank</th><th>ETF</th><th>Correlation</th><th>Z-Score</th><th></th></tr>
                                                            </thead>
                                                            <tbody>
                                                                <tr className="bg-accent/10">
                                                                    <td className="font-bold">1</td>
                                                                    <td className="font-bold text-accent">{bestETF.ticker}</td>
                                                                    <td><progress className="progress progress-accent w-20 mr-2" value={bestETF.correlation} max="100" />{bestETF.correlation}%</td>
                                                                    <td>{bestETF.zScore.toFixed(2)}</td>
                                                                    <td><span className="badge badge-accent badge-xs">Selected</span></td>
                                                                </tr>
                                                                {pr.alternateETFs.map((e, i) => (
                                                                    <tr key={i} className="cursor-pointer hover:bg-base-200/30 transition-colors" onClick={() => { setPreferredETF(e.ticker); setCustomETFInput(e.ticker); calculatePortfolio(e.ticker); }}>
                                                                        <td className="opacity-60">{i + 2}</td>
                                                                        <td className="font-bold">{e.ticker}</td>
                                                                        <td><progress className="progress progress-success w-20 mr-2" value={e.correlation} max="100" />{e.correlation}%</td>
                                                                        <td className={Math.abs(e.zScore) > 2 ? 'text-error font-bold' : ''}>{e.zScore.toFixed(2)}</td>
                                                                        <td><button className="btn btn-ghost btn-xs text-info"><ExternalLink className="w-3 h-3" /> Use</button></td>
                                                                    </tr>
                                                                ))}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                )}
                                            </div>
                                        </div>

                                        {/* Execution Strategies — shown after the Switch ETF section so the user first
                                            picks their preferred replacement, then sees the execution options for it */}
                                        {activeTab === 'default' && pr.strategies && pr.strategies.length > 0 && (
                                            <>
                                                <h3 className="text-lg font-bold border-b border-white/[0.05] pb-2">
                                                    Execution Strategies — Sell All Losers → Buy {bestETF.ticker}
                                                </h3>
                                                <div className="space-y-4">
                                                    {pr.strategies.map(strat => (
                                                        <StrategyCard key={strat.id} strat={strat} uniqueId={strat.id} expandedStrategy={expandedStrategy} toggleStrategy={toggleStrategy} />
                                                    ))}
                                                </div>
                                            </>
                                        )}

                                        {/* Strategy Comparison Table */}
                                        {activeTab === 'default' && pr.costComparison && (
                                            <div className="glass-card">
                                                <div className="p-5">
                                                    <h3 className="font-bold text-sm flex items-center gap-2 mb-3">
                                                        <Scale className="w-5 h-5 text-info" /> Strategy Comparison
                                                    </h3>
                                                    <div className="overflow-x-auto">
                                                        <table className="table table-sm table-pro">
                                                            <thead>
                                                                <tr><th>Metric</th><th className="text-center">Unified ETF Swap</th><th className="text-center">Synthetic Long</th><th className="text-center">Protective Collar</th></tr>
                                                            </thead>
                                                            <tbody>
                                                                <tr>
                                                                    <td className="font-medium">Est. Cost</td>
                                                                    <td className="text-center font-mono tabular-nums">${pr.costComparison.unified_swap.cost.toLocaleString()}</td>
                                                                    <td className="text-center font-mono tabular-nums">${pr.costComparison.unified_synthetic.cost.toLocaleString()}</td>
                                                                    <td className="text-center font-mono tabular-nums">${pr.costComparison.unified_collar.cost.toLocaleString()}</td>
                                                                </tr>
                                                                <tr>
                                                                    <td className="font-medium">Complexity</td>
                                                                    <td className="text-center"><span className="badge badge-sm badge-success">{pr.costComparison.unified_swap.complexity}</span></td>
                                                                    <td className="text-center"><span className="badge badge-sm badge-warning">{pr.costComparison.unified_synthetic.complexity}</span></td>
                                                                    <td className="text-center"><span className="badge badge-sm badge-warning">{pr.costComparison.unified_collar.complexity}</span></td>
                                                                </tr>
                                                                <tr>
                                                                    <td className="font-medium">Protection</td>
                                                                    <td className="text-center">{pr.costComparison.unified_swap.protection}</td>
                                                                    <td className="text-center">{pr.costComparison.unified_synthetic.protection}</td>
                                                                    <td className="text-center"><span className="font-bold text-success">{pr.costComparison.unified_collar.protection}</span></td>
                                                                </tr>
                                                                <tr>
                                                                    <td className="font-medium">Wash Sale Safe</td>
                                                                    <td className="text-center">Yes</td><td className="text-center">Yes</td><td className="text-center">Yes</td>
                                                                </tr>
                                                                <tr>
                                                                    <td className="font-medium">Capital Required</td>
                                                                    <td className="text-center font-mono tabular-nums">${pr.costComparison.unified_swap.capital_required.toLocaleString()}</td>
                                                                    <td className="text-center font-mono tabular-nums">${pr.costComparison.unified_synthetic.capital_required.toLocaleString()}</td>
                                                                    <td className="text-center font-mono tabular-nums">${pr.costComparison.unified_collar.capital_required.toLocaleString()}</td>
                                                                </tr>
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </div>
                                            </div>
                                        )}

                                        {/* Timeline */}
                                        {portfolioResult.summary.harvestableCount > 0 && (
                                            <div className="glass-card">
                                                <div className="p-5">
                                                    <h3 className="font-bold text-sm flex items-center gap-2 mb-4">
                                                        <Clock className="w-5 h-5 text-warning" /> Wash Sale Timeline
                                                    </h3>
                                                    <ul className="steps steps-vertical lg:steps-horizontal w-full">
                                                        {portfolioResult.timeline.map((step, i) => (
                                                            <li key={i} className={`step ${i === 0 ? 'step-accent' : ''}`} data-content={i === 0 ? '!' : i === 2 ? '✓' : '•'}>
                                                                <div className="text-left lg:text-center py-2">
                                                                    <div className="font-bold text-sm">{step.day}: {step.title}</div>
                                                                    <div className="text-xs opacity-70 max-w-xs">{step.description}</div>
                                                                </div>
                                                            </li>
                                                        ))}
                                                    </ul>
                                                </div>
                                            </div>
                                        )}
                                    </>
                                );
                            })()}

                            {/* No harvestable positions message */}
                            {portfolioResult.summary.harvestableCount === 0 && (
                                <div className="glass-card border-success/20">
                                    <div className="p-5 text-center">
                                        <TrendingUp className="w-10 h-10 text-success mx-auto mb-3" />
                                        <h3 className="font-bold text-lg text-success">All Positions in Profit</h3>
                                        <p className="text-sm opacity-70 mt-1">No loss-making positions to harvest. Your portfolio is performing well!</p>
                                    </div>
                                </div>
                            )}

                            {/* ============================================================ */}
                            {/* AGENT CREATION CTA                                          */}
                            {/* ============================================================ */}
                            {agentSuccess ? (
                                <div className="glass-card border-success/30">
                                    <div className="p-5 flex items-center justify-between">
                                        <div className="flex items-center gap-3">
                                            <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                                                <CheckCircle className="w-5 h-5 text-success" />
                                            </div>
                                            <div>
                                                <h3 className="font-bold text-sm text-success">Agent Created!</h3>
                                                <p className="text-xs opacity-60">
                                                    {agentSchedule === 'recurring' ? `It will run on schedule (${agentCron}).` : 'Run it manually from the Agents page.'}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="flex gap-2">
                                            <a href="/agents" className="btn btn-ghost btn-sm gap-1"><ExternalLink className="w-3 h-3" /> View Agents</a>
                                            <button className="btn btn-ghost btn-sm" onClick={() => { setAgentSuccess(false); setShowAgentForm(false); }}>Create Another</button>
                                        </div>
                                    </div>
                                </div>
                            ) : !showAgentForm ? (
                                <div className="glass-card border-white/[0.05] hover:border-accent/30 transition-colors cursor-pointer" onClick={() => {
                                    setShowAgentForm(true);
                                    const tickers = holdings.filter(h => h.ticker).map(h => h.ticker).join(', ');
                                    setAgentName(`TLH Monitor — ${tickers || 'Portfolio'}`);
                                    setAgentStrategyIdx(activeTab);
                                }}>
                                    <div className="p-5 flex items-center gap-4">
                                        <div className="w-10 h-10 rounded-xl bg-accent/10 flex items-center justify-center shrink-0">
                                            <Bot className="w-5 h-5 text-accent" />
                                        </div>
                                        <div className="flex-1">
                                            <h3 className="font-bold text-sm">Automate This Analysis</h3>
                                            <p className="text-xs opacity-60 mt-0.5">Create a monitoring agent to track TLH opportunities on your schedule. It will alert you when conditions change.</p>
                                        </div>
                                        <span className="text-accent text-sm font-medium whitespace-nowrap">Set Up →</span>
                                    </div>
                                </div>
                            ) : (
                                <div className="glass-card border-accent/20 animate-fade-in">
                                    <div className="p-5 space-y-4">
                                        <div className="flex items-center justify-between">
                                            <h3 className="font-bold text-sm flex items-center gap-2">
                                                <Bot className="w-5 h-5 text-accent" /> Create TLH Monitoring Agent
                                            </h3>
                                            <button className="btn btn-ghost btn-sm btn-square" onClick={() => setShowAgentForm(false)}>
                                                <X className="w-4 h-4" />
                                            </button>
                                        </div>

                                        {/* Strategy selector */}
                                        <div>
                                            <label className="text-xs font-medium opacity-70 mb-2 block">Strategy to monitor</label>
                                            <div className="flex flex-wrap gap-2">
                                                {/* Default ETF strategy */}
                                                {portfolioResult.portfolioReplacement && (
                                                    <button
                                                        className={`px-3 py-1.5 rounded-lg text-xs border transition-all ${agentStrategyIdx === 'default' ? 'border-accent bg-accent/10 text-accent' : 'border-white/10 hover:border-accent/30'}`}
                                                        onClick={() => setAgentStrategyIdx('default')}
                                                    >
                                                        ETF-Only ({portfolioResult.portfolioReplacement.bestETF.ticker}) — {portfolioResult.portfolioReplacement.bestETF.correlation.toFixed(0)}% ρ
                                                    </button>
                                                )}
                                                {/* Optimization strategies */}
                                                {(portfolioResult.optimizationSuggestions || []).map((s, i) => (
                                                    <button
                                                        key={i}
                                                        className={`px-3 py-1.5 rounded-lg text-xs border transition-all ${agentStrategyIdx === i ? 'border-accent bg-accent/10 text-accent' : 'border-white/10 hover:border-accent/30'}`}
                                                        onClick={() => setAgentStrategyIdx(i)}
                                                    >
                                                        {s.type === 'optimal_blend' && (s.title || 'Optimized Blend')}
                                                        {s.type === 'selective_harvest' && 'Selective Harvest'}
                                                        {s.type === 'partial_rebalance' && `Partial ${(s as PartialRebalanceSuggestion).sellPct}%`}
                                                        {' '}— {s.score.toFixed(3)}
                                                    </button>
                                                ))}
                                            </div>
                                        </div>

                                        {/* Agent name */}
                                        <div>
                                            <label className="text-xs font-medium opacity-70 mb-1 block">Agent Name</label>
                                            <input
                                                type="text"
                                                className="input input-bordered input-sm w-full"
                                                value={agentName}
                                                onChange={e => setAgentName(e.target.value)}
                                            />
                                        </div>

                                        {/* Schedule */}
                                        <div className="grid grid-cols-2 gap-3">
                                            <div>
                                                <label className="text-xs font-medium opacity-70 mb-1 block">Schedule</label>
                                                <select
                                                    className="select select-bordered select-sm w-full"
                                                    value={agentSchedule === 'manual' ? 'manual' : agentCron}
                                                    onChange={e => {
                                                        const v = e.target.value;
                                                        if (v === 'manual') { setAgentSchedule('manual'); setAgentCron(''); }
                                                        else { setAgentSchedule('recurring'); setAgentCron(v); }
                                                    }}
                                                >
                                                    <option value="0 9 * * 1-5">Daily (Weekdays 9 AM)</option>
                                                    <option value="0 9 * * 1">Weekly (Monday 9 AM)</option>
                                                    <option value="0 9 1 * *">Monthly (1st at 9 AM)</option>
                                                    <option value="manual">Manual Only</option>
                                                </select>
                                            </div>
                                            <div className="flex items-end">
                                                <span className="text-xs opacity-40 pb-2">
                                                    {agentSchedule === 'manual' ? 'Run on demand only' : `Cron: ${agentCron}`}
                                                </span>
                                            </div>
                                        </div>

                                        {/* Instruction textarea */}
                                        <div>
                                            <label className="text-xs font-medium opacity-70 mb-1 block">Instructions (editable)</label>
                                            <textarea
                                                className="textarea textarea-bordered w-full text-xs font-mono leading-relaxed"
                                                rows={8}
                                                value={agentInstruction}
                                                onChange={e => setAgentInstruction(e.target.value)}
                                            />
                                        </div>

                                        {agentError && (
                                            <div className="alert alert-error text-xs py-2">
                                                <AlertCircle className="w-3.5 h-3.5" /> {agentError}
                                            </div>
                                        )}

                                        {/* Actions */}
                                        <div className="flex justify-end gap-2 pt-1">
                                            <button className="btn btn-ghost btn-sm" onClick={() => setShowAgentForm(false)}>Cancel</button>
                                            <button
                                                className="btn btn-accent btn-sm gap-1"
                                                onClick={handleCreateAgent}
                                                disabled={agentCreating || !agentName.trim() || !agentInstruction.trim()}
                                            >
                                                {agentCreating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Bot className="w-3.5 h-3.5" />}
                                                Create Agent
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </>
        </div>
    );
}

/* ========================================================================= */
/* Strategy Card (shared between single & portfolio modes)                   */
/* ========================================================================= */

function StrategyCard({
    strat, uniqueId, expandedStrategy, toggleStrategy,
}: {
    strat: Strategy; uniqueId: string;
    expandedStrategy: string | null; toggleStrategy: (id: string) => void;
}) {
    const isExpanded = expandedStrategy === uniqueId;

    return (
        <div className="glass-card hover:border-accent/30 transition-colors">
            <div className="p-4">
                <div className="flex flex-wrap items-center justify-between gap-2 cursor-pointer" onClick={() => toggleStrategy(uniqueId)}>
                    <div className="flex items-center gap-3">
                        <h4 className="font-bold">{strat.name}</h4>
                        {strat.badge && (
                            <span className={`badge badge-sm ${strat.badge === 'Simplest' ? 'badge-success' : strat.badge === 'Capital Efficient' ? 'badge-info' : 'badge-warning'}`}>
                                {strat.badge === 'Simplest' && <Zap className="w-3 h-3 mr-1" />}
                                {strat.badge === 'Best Protection' && <ShieldCheck className="w-3 h-3 mr-1" />}
                                {strat.badge}
                            </span>
                        )}
                        <span className="badge badge-outline badge-sm opacity-60">{strat.type}</span>
                    </div>
                    <div className="flex items-center gap-4">
                        <span className="text-sm font-mono">
                            Est. Cost: <span className="font-bold text-accent">${strat.estimated_cost.toLocaleString()}</span>
                        </span>
                        {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </div>
                </div>
                <p className="text-sm opacity-80 mt-1">{strat.description}</p>

                {isExpanded && (
                    <div className="mt-4 space-y-4 animate-fade-in">
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            <div>
                                <span className="text-xs font-bold text-success uppercase tracking-wider">Pros</span>
                                <ul className="text-sm mt-1 list-disc list-inside opacity-80">
                                    {strat.pros.map((p, i) => <li key={i}>{p}</li>)}
                                </ul>
                            </div>
                            <div>
                                <span className="text-xs font-bold text-error uppercase tracking-wider">Cons</span>
                                <ul className="text-sm mt-1 list-disc list-inside opacity-80">
                                    {strat.cons.map((c, i) => <li key={i}>{c}</li>)}
                                </ul>
                            </div>
                        </div>

                        {strat.optionsError && (
                            <div className="alert alert-warning text-sm">
                                <AlertCircle className="w-4 h-4" /> Options data unavailable: {strat.optionsError}
                            </div>
                        )}

                        {strat.trades && strat.trades.length > 0 && (
                            <div>
                                <span className="text-xs font-bold uppercase tracking-wider opacity-60">Trades to Execute</span>
                                <div className="overflow-x-auto mt-2">
                                    <table className="table table-sm table-pro">
                                        <thead>
                                            <tr>
                                                <th>Action</th><th>Ticker</th><th>Type</th>
                                                <th className="text-right">Qty</th><th className="text-right">Price</th><th className="text-right">Total</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {strat.trades.map((t, i) => (
                                                <tr key={i}>
                                                    <td><span className={`badge badge-sm ${t.action === 'BUY' ? 'badge-success' : 'badge-error'}`}>{t.action}</span></td>
                                                    <td className="font-bold">{t.ticker}</td>
                                                    <td>{t.type}</td>
                                                    <td className="text-right font-mono">{t.qty}</td>
                                                    <td className="text-right font-mono">${t.price.toFixed(2)}</td>
                                                    <td className="text-right font-mono">${t.total.toLocaleString()}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                {strat.tracking_error_1y !== undefined && (
                                    <p className="text-xs opacity-60 mt-1">1Y Tracking Error: {strat.tracking_error_1y}</p>
                                )}
                            </div>
                        )}

                        {strat.legs && strat.legs.length > 0 && (
                            <div>
                                <div className="flex items-center gap-4 mb-2">
                                    <span className="text-xs font-bold uppercase tracking-wider opacity-60">Option Legs</span>
                                    {strat.expiration && <span className="text-xs opacity-50">Exp: {strat.expiration} ({strat.dte}d)</span>}
                                </div>
                                <div className="overflow-x-auto">
                                    <table className="table table-sm table-pro">
                                        <thead>
                                            <tr>
                                                <th>Action</th><th>Type</th><th>Ticker</th>
                                                <th className="text-right">Strike</th><th className="text-right">Bid</th>
                                                <th className="text-right">Ask</th><th className="text-right">Mid</th>
                                                <th className="text-right">Qty</th><th>Purpose</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {strat.legs.map((leg, i) => (
                                                <tr key={i}>
                                                    <td><span className={`badge badge-sm ${leg.action === 'BUY' ? 'badge-success' : 'badge-error'}`}>{leg.action}</span></td>
                                                    <td>{leg.type}</td>
                                                    <td className="font-bold">{leg.ticker}</td>
                                                    <td className="text-right font-mono">{leg.strike != null ? `$${leg.strike.toFixed(2)}` : '—'}</td>
                                                    <td className="text-right font-mono">${leg.bid.toFixed(2)}</td>
                                                    <td className="text-right font-mono">${leg.ask.toFixed(2)}</td>
                                                    <td className="text-right font-mono font-bold">${leg.mid.toFixed(2)}</td>
                                                    <td className="text-right font-mono">{leg.qty}</td>
                                                    <td className="text-xs opacity-70 max-w-[200px]">{leg.purpose}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                                <div className="flex flex-wrap gap-4 mt-3 text-sm">
                                    {strat.contracts !== undefined && <span>Contracts: <span className="font-bold">{strat.contracts}</span></span>}
                                    {strat.net_debit !== undefined && <span>Net Debit: <span className="font-bold font-mono">${strat.net_debit.toLocaleString()}</span></span>}
                                    {strat.delta_exposure !== undefined && <span>Delta Exposure: <span className="font-bold">~{strat.delta_exposure} shares</span></span>}
                                    {strat.net_collar_cost !== undefined && <span>Collar Net Cost: <span className="font-bold font-mono">${strat.net_collar_cost.toLocaleString()}</span></span>}
                                    {strat.protection_range && <span>Protection: <span className="font-bold">${strat.protection_range.floor} — ${strat.protection_range.cap}</span></span>}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}


/* ---------- sub-components ---------- */

function SummaryCard({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
    return (
        <div className="bg-base-200/40 rounded-xl p-4 text-center border border-white/[0.03]">
            <div className="text-xs opacity-60 uppercase tracking-wider mb-1">{label}</div>
            <div className={`text-xl font-bold ${color}`}>{value}</div>
            <div className="text-xs opacity-50 mt-0.5">{sub}</div>
        </div>
    );
}
