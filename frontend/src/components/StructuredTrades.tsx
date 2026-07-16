import React, { useState } from 'react';
import { apiBase } from '../api';
import {
    Building, AlertCircle, Percent, Timer, Briefcase, Calculator,
    TrendingUp, TrendingDown
} from 'lucide-react';
import {
    Chart as ChartJS, CategoryScale, LinearScale, PointElement,
    LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(
    CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler
);

type StructureKind = 'ppn' | 'capped' | 'yield_enhanced';

interface OptionLeg {
    side: 'Long' | 'Short';
    type: 'Call' | 'Put';
    strike: number;
    midPrice: number;
    impliedVolatility: number;
    contracts: number;
}

interface StructuredResult {
    ticker: string;
    structure: StructureKind;
    currentPrice: number;
    investmentAmount: number;
    durationDays: number;
    actualDte: number;
    expirationDate: string;
    interestRate: number;
    rateSource: string;
    allocations: {
        bond: number;
        options: number;
    };
    optionParameters: {
        type: string;
        strike: number;
        midPrice: number;
        impliedVolatility: number;
        theoreticalContracts: number;
    };
    legs: OptionLeg[];
    participationRate: number;
    metrics: {
        structure: StructureKind;
        floorProtected: boolean;
        capPct: number | null;
        bufferPct: number | null;
        barrierPrice: number | null;
        maxGainPct: number;
        maxLossPct: number;
    };
    scenarios: {
        underlyingChangePct: number;
        simulatedPrice: number;
        totalPayout: number;
        roi: number;
    }[];
}

const STRUCTURE_META: Record<StructureKind, { label: string; tagline: string; blurb: string }> = {
    ppn: {
        label: 'Principal Protected',
        tagline: '100% floor, ~70% upside',
        blurb: 'Splits your principal into a zero-coupon bond (to return 100% at maturity) and a long call for upside participation. Your money is fully protected — you simply capture a fraction of the rally.',
    },
    capped: {
        label: 'Capped Participation',
        tagline: 'Leveraged upside, capped',
        blurb: 'Adds a short call above the market. The premium it collects buys more long calls, so you capture more than 100% of the move — but only up to the cap. Principal stays fully protected.',
    },
    yield_enhanced: {
        label: 'Yield Enhanced',
        tagline: 'Fatter payoff, soft floor',
        blurb: 'Sells a deep out-of-the-money put to collect premium, which funds a richer upside. The trade-off: protection is only firm down to the put strike — below that barrier you take losses with the underlying.',
    },
};

export function StructuredTrades() {
    const [ticker, setTicker] = useState('SPY');
    const [amount, setAmount] = useState<number>(10000);
    const [duration, setDuration] = useState<number>(365);
    const [interestRate, setInterestRate] = useState<string>('');
    const [structure, setStructure] = useState<StructureKind>('ppn');
    const [capPct, setCapPct] = useState<number>(15);
    const [bufferPct, setBufferPct] = useState<number>(20);

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<StructuredResult | null>(null);

    const calculateStructuredTrade = async () => {
        if (!ticker) {
            setError('Please enter a ticker symbol');
            return;
        }
        if (amount <= 0) {
            setError('Amount must be positive');
            return;
        }

        setLoading(true);
        setError(null);
        setResult(null);

        try {
            const payload: any = {
                ticker: ticker,
                amount: Number(amount),
                duration_days: Number(duration),
                structure: structure,
                cap_pct: Number(capPct),
                put_buffer_pct: Number(bufferPct),
            };
            if (interestRate.trim() !== '') {
                payload.interest_rate = Number(interestRate);
            }

            const res = await fetch(`${apiBase}/api/stock/${ticker}/strategies/structured-trade`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${localStorage.getItem('token')}`,
                },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                const errData = await res.json();
                let errMsg = 'Failed to calculate structured trade';
                if (errData.detail) {
                    if (Array.isArray(errData.detail) && errData.detail.length > 0) {
                        errMsg = errData.detail[0].msg || 'Sorry, Data not available';
                    } else if (typeof errData.detail === 'string') {
                        errMsg = errData.detail;
                    } else {
                        errMsg = 'Sorry, Data not available';
                    }
                }
                throw new Error(errMsg);
            }

            const data = await res.json();
            setResult(data);
        } catch (err: any) {
            setError(err.message || 'An unexpected error occurred');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="border-l-4 border-info bg-info/10 p-4 rounded-r-xl">
                <h3 className="font-bold flex items-center gap-2 text-info">
                    <Building className="w-5 h-5" />
                    {STRUCTURE_META[structure].label} Note (Structured Trade)
                </h3>
                <p className="text-sm opacity-80 mt-1">
                    {STRUCTURE_META[structure].blurb}
                </p>
            </div>

            {/* Structure flavor selector */}
            <div className="form-control">
                <label className="label">
                    <span className="label-text font-medium flex items-center gap-1">
                        <Calculator className="w-4 h-4 text-secondary" /> Structure
                    </span>
                </label>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    {(Object.keys(STRUCTURE_META) as StructureKind[]).map((kind) => (
                        <button
                            key={kind}
                            type="button"
                            onClick={() => setStructure(kind)}
                            className={`text-left p-3 rounded-xl border transition-colors ${
                                structure === kind
                                    ? 'border-primary bg-primary/10 ring-1 ring-primary'
                                    : 'border-white/10 bg-base-200/30 hover:border-primary/40'
                            }`}
                        >
                            <div className="font-semibold text-sm">{STRUCTURE_META[kind].label}</div>
                            <div className="text-xs opacity-60 mt-0.5">{STRUCTURE_META[kind].tagline}</div>
                        </button>
                    ))}
                </div>
            </div>

            {/* Conditional tunables */}
            {structure === 'capped' && (
                <div className="form-control max-w-xs">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Percent className="w-4 h-4 text-success" /> Upside Cap (% above spot)
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={capPct}
                        onChange={(e) => setCapPct(Number(e.target.value))}
                        min={1}
                        max={100}
                        step={1}
                    />
                    <span className="text-xs opacity-50 mt-1">Sells a call this far above spot to fund extra upside.</span>
                </div>
            )}
            {structure === 'yield_enhanced' && (
                <div className="form-control max-w-xs">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Percent className="w-4 h-4 text-warning" /> Downside Buffer (% below spot)
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={bufferPct}
                        onChange={(e) => setBufferPct(Number(e.target.value))}
                        min={1}
                        max={90}
                        step={1}
                    />
                    <span className="text-xs opacity-50 mt-1">Sells a put here; protected above it, exposed below.</span>
                </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Building className="w-4 h-4 text-primary" /> Reference Asset
                        </span>
                    </label>
                    <input
                        type="text"
                        className="input input-bordered w-full"
                        value={ticker}
                        onChange={(e) => setTicker(e.target.value.toUpperCase())}
                        placeholder="e.g. SPY, QQQ"
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Briefcase className="w-4 h-4 text-success" /> Investment ($)
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={amount}
                        onChange={(e) => setAmount(Number(e.target.value))}
                        min={100}
                        step={100}
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Timer className="w-4 h-4 text-warning" /> Duration (Days)
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={duration}
                        onChange={(e) => setDuration(Number(e.target.value))}
                        min={30}
                        max={1000}
                        step={30}
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Percent className="w-4 h-4 text-info" /> Risk-Free Rate (%)
                        </span>
                        <span className="label-text-alt opacity-50">Optional</span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={interestRate}
                        onChange={(e) => setInterestRate(e.target.value)}
                        placeholder="e.g. 4.0 (Default)"
                        step={0.1}
                        min={0}
                        max={20}
                    />
                </div>
            </div>

            <button
                className="btn btn-primary w-full sm:w-auto"
                onClick={calculateStructuredTrade}
                disabled={loading}
            >
                {loading ? (
                    <span className="loading loading-spinner"></span>
                ) : (
                    <Calculator className="w-5 h-5 mr-2" />
                )}
                Simulate Structured Trade
            </button>

            {error && (
                <div className="alert alert-error text-sm">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                </div>
            )}

            {result && (
                <div className="mt-8 space-y-6 animate-fade-in">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        {/* Investment Breakdown */}
                        <div className="glass-card">
                            <div className="p-5">
                                <h3 className="font-bold text-sm text-success mb-2">
                                    Capital Allocation
                                </h3>
                                <div className="flex justify-between items-center mb-1">
                                    <span className="text-sm opacity-70">Total Principal</span>
                                    <span className="font-semibold">${result.investmentAmount.toLocaleString()}</span>
                                </div>
                                <div className="flex justify-between items-center mb-1">
                                    <span className="text-sm opacity-70">Bond (Principal Protection)</span>
                                    <span className="font-semibold text-warning">${result.allocations.bond.toLocaleString()}</span>
                                </div>
                                <div className="flex justify-between items-center mb-4">
                                    <span className="text-sm opacity-70">Options Budget (Upside)</span>
                                    <span className="font-semibold text-info">${result.allocations.options.toLocaleString()}</span>
                                </div>

                                <div className="divider my-0"></div>
                                <div className="flex justify-between items-center mt-2">
                                    <span className="text-sm font-medium">Interest Rate Used</span>
                                    <span className="text-sm font-mono bg-base-200/40 px-2 py-1 rounded border border-white/[0.03]">
                                        {result.interestRate}% <span className="opacity-50 text-xs">({result.rateSource})</span>
                                    </span>
                                </div>
                                <div className="flex justify-between items-center mt-1">
                                    <span className="text-sm font-medium">Actual DTE</span>
                                    <span className="text-sm">{result.actualDte} days</span>
                                </div>
                            </div>
                        </div>

                        {/* Option Legs */}
                        <div className="glass-card">
                            <div className="p-5">
                                <h3 className="font-bold text-sm text-info mb-3">
                                    Option Package
                                </h3>
                                <div className="space-y-2">
                                    {result.legs.map((leg, idx) => {
                                        const isLong = leg.side === 'Long';
                                        return (
                                            <div key={idx} className="flex justify-between items-center">
                                                <span className="flex items-center gap-2">
                                                    <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${isLong ? 'bg-success/15 text-success' : 'bg-error/15 text-error'}`}>
                                                        {leg.side === 'Long' ? 'BUY' : 'SELL'}
                                                    </span>
                                                    <span className="font-semibold text-sm">
                                                        {result.ticker} {leg.strike}{leg.type === 'Call' ? 'C' : 'P'}
                                                    </span>
                                                </span>
                                                <span className="text-sm opacity-70 font-mono">
                                                    ${leg.midPrice.toFixed(2)} &times; {leg.contracts.toFixed(2)}
                                                </span>
                                            </div>
                                        );
                                    })}
                                </div>
                                <div className="divider my-2"></div>
                                <div className="flex justify-between items-center mb-1">
                                    <span className="text-sm opacity-70">Expiration</span>
                                    <span className="font-semibold">{result.expirationDate}</span>
                                </div>
                                <div className="flex justify-between items-center">
                                    <span className="text-sm font-medium">Current {result.ticker} Price</span>
                                    <span className="font-mono text-sm">${result.currentPrice.toFixed(2)}</span>
                                </div>
                            </div>
                        </div>

                        {/* Participation Result */}
                        <div className="glass-card bg-primary text-primary-content">
                            <div className="p-5 flex flex-col items-center justify-center text-center">
                                <h3 className="font-bold text-sm mb-0">Participation Rate</h3>
                                <p className="text-xs opacity-80 mb-3">How much of the {result.ticker} upside you capture</p>
                                <div className="text-5xl font-black mb-1 flex items-baseline">
                                    {result.participationRate.toFixed(1)}<span className="text-2xl ml-1">%</span>
                                </div>
                                {result.metrics.capPct !== null && (
                                    <p className="text-xs opacity-90 mt-1">Upside capped at +{result.metrics.capPct.toFixed(1)}% ROI</p>
                                )}
                                <div className="w-full mt-4 pt-3 border-t border-white/20 grid grid-cols-2 gap-2 text-left">
                                    <div>
                                        <div className="text-[10px] uppercase tracking-wide opacity-70">Downside Floor</div>
                                        <div className="text-sm font-semibold">
                                            {result.metrics.floorProtected
                                                ? '100% protected'
                                                : `Soft — to $${result.metrics.barrierPrice?.toFixed(0)}`}
                                        </div>
                                    </div>
                                    <div>
                                        <div className="text-[10px] uppercase tracking-wide opacity-70">Worst Case</div>
                                        <div className={`text-sm font-semibold ${result.metrics.maxLossPct < 0 ? '' : ''}`}>
                                            {result.metrics.maxLossPct > 0 ? '+' : ''}{result.metrics.maxLossPct.toFixed(1)}%
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Scenario Chart / Table */}
                    <div className="glass-card mt-6">
                        <div className="p-5">
                            <h3 className="font-bold text-sm mb-4 flex items-center gap-2">
                                <TrendingUp className="w-5 h-5 text-secondary" />
                                Payoff Scenarios at Expiration ({result.expirationDate})
                            </h3>

                            <div className="h-64 sm:h-80 w-full mb-6 relative">
                                <Line
                                    data={{
                                        labels: result.scenarios.map(s => `${s.underlyingChangePct > 0 ? '+' : ''}${s.underlyingChangePct}%`),
                                        datasets: [
                                            {
                                                label: 'Strategy ROI (%)',
                                                data: result.scenarios.map(s => s.roi),
                                                borderColor: 'rgba(54, 211, 153, 1)', // success color
                                                backgroundColor: 'rgba(54, 211, 153, 0.2)',
                                                borderWidth: 2,
                                                fill: true,
                                                tension: 0.1,
                                                pointRadius: 4,
                                                pointBackgroundColor: result.scenarios.map(s => s.roi > 0 ? '#22c55e' : s.roi < 0 ? '#ef4444' : '#9ca3af')
                                            },
                                            {
                                                label: 'Underlying Asset ROI (%)',
                                                data: result.scenarios.map(s => s.underlyingChangePct),
                                                borderColor: 'rgba(156, 163, 175, 0.5)',
                                                borderWidth: 1.5,
                                                borderDash: [5, 5],
                                                fill: false,
                                                pointRadius: 0,
                                            }
                                        ]
                                    }}
                                    options={{
                                        responsive: true,
                                        maintainAspectRatio: false,
                                        interaction: {
                                            mode: 'index',
                                            intersect: false,
                                        },
                                        plugins: {
                                            legend: {
                                                position: 'top',
                                                labels: { usePointStyle: true, boxWidth: 8 }
                                            },
                                            tooltip: {
                                                callbacks: {
                                                    label: (context) => {
                                                        const yVal = context.parsed.y;
                                                        return `${context.dataset.label}: ${yVal !== null && yVal !== undefined ? yVal.toFixed(2) : '0.00'}%`;
                                                    }
                                                }
                                            }
                                        },
                                        scales: {
                                            y: {
                                                title: { display: true, text: 'Return on Investment (%)' },
                                                grid: { color: 'rgba(156, 163, 175, 0.1)' }
                                            },
                                            x: {
                                                title: { display: true, text: 'Underlying Asset Performance' },
                                                grid: { display: false }
                                            }
                                        }
                                    }}
                                />
                            </div>

                            <div className="overflow-x-auto">
                                <table className="table table-pro w-full text-center">
                                    <thead>
                                        <tr>
                                            <th className="bg-base-200/40">Asset Return</th>
                                            <th className="bg-base-200/40">Asset Price</th>
                                            <th className="bg-base-200/40">Total Payout</th>
                                            <th className="bg-base-200/40">Your ROI</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.scenarios.map((scenario, idx) => (
                                            <tr key={idx} className={scenario.roi > 0 ? 'bg-success/5' : ''}>
                                                <td className="font-medium flex items-center justify-center gap-1">
                                                    {scenario.underlyingChangePct < 0 ? (
                                                        <TrendingDown className="w-4 h-4 text-error" />
                                                    ) : scenario.underlyingChangePct > 0 ? (
                                                        <TrendingUp className="w-4 h-4 text-success" />
                                                    ) : (
                                                        <span className="w-4 h-4 block" />
                                                    )}
                                                    <span className={scenario.underlyingChangePct < 0 ? 'text-error' : scenario.underlyingChangePct > 0 ? 'text-success' : ''}>
                                                        {scenario.underlyingChangePct > 0 ? '+' : ''}{scenario.underlyingChangePct}%
                                                    </span>
                                                </td>
                                                <td>${scenario.simulatedPrice.toFixed(2)}</td>
                                                <td className="font-semibold">${scenario.totalPayout.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                                                <td className={`font-bold ${scenario.roi > 0 ? 'text-success' : scenario.roi === 0 ? 'opacity-50' : 'text-error'}`}>
                                                    {scenario.roi > 0 ? '+' : ''}{scenario.roi}%
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <p className="text-xs text-base-content/50 mt-4 text-center">
                                {result.metrics.floorProtected
                                    ? '* Note: Principal protection applies at maturity. If sold early, the bond portion may fluctuate due to interest rate changes.'
                                    : `* Note: This note has a SOFT floor. You are protected only down to $${result.metrics.barrierPrice?.toFixed(2)} (−${result.metrics.bufferPct?.toFixed(0)}%). Below that the sold put exposes you to losses with the underlying. Principal is not guaranteed.`}
                            </p>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
