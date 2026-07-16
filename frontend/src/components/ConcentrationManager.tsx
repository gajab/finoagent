import React, { useState } from 'react';
import { apiBase } from '../api';
import {
    Briefcase, AlertCircle, Percent, Timer, Calculator,
    TrendingUp, TrendingDown, Layers, Target, Coins
} from 'lucide-react';
import {
    Chart as ChartJS, CategoryScale, LinearScale, PointElement,
    LineElement, BarElement, Title, Tooltip, Legend
} from 'chart.js';
import { Chart } from 'react-chartjs-2';

ChartJS.register(
    CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title, Tooltip, Legend
);

interface OptionLeg {
    action: string;
    qty: number;
    type: string;
    strike: number;
    midPrice: number;
    purpose: string;
}

interface Scenario {
    underlyingChangePct: number;
    simulatedPrice: number;
    realizedGain: number;
    unrealizedGain: number;
    taxOwed: number;
    status: string;
    netAfterTaxValue: number;
}

interface ConcentrationResult {
    ticker: string;
    currentPrice: number;
    shares: number;
    positionType: 'long' | 'short';
    currentValue: number;
    costBasis: number;
    durationDays: number;
    expirationDate: string;
    actualDte: number;
    taxRatePct: number;
    netPremiumTotal: number;
    syntheticTaxSavings: number;
    legs: OptionLeg[];
    scenarios: Scenario[];
}

export function ConcentrationManager() {
    const [ticker, setTicker] = useState('AAPL');
    const [shares, setShares] = useState<number>(1000);
    const [costBasis, setCostBasis] = useState<number>(150);
    const [positionType, setPositionType] = useState<'long' | 'short'>('long');
    const [taxRatePct, setTaxRatePct] = useState<number>(20);
    const [durationDays, setDurationDays] = useState<number>(30);
    const [upsideCapPct, setUpsideCapPct] = useState<number>(10);
    const [downsideProtectionPct, setDownsideProtectionPct] = useState<number>(10);

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<ConcentrationResult | null>(null);

    const calculateStrategy = async () => {
        if (!ticker) {
            setError('Please enter a ticker symbol');
            return;
        }
        if (shares <= 0 || costBasis <= 0) {
            setError('Shares and Cost Basis must be positive');
            return;
        }

        setLoading(true);
        setError(null);
        setResult(null);

        try {
            const payload = {
                ticker: ticker,
                shares: Number(shares),
                cost_basis: Number(costBasis),
                position_type: positionType,
                tax_rate_pct: Number(taxRatePct),
                duration_days: Number(durationDays),
                upside_cap_pct: Number(upsideCapPct),
                downside_protection_pct: Number(downsideProtectionPct),
            };

            const res = await fetch(`${apiBase}/api/stock/${ticker}/strategies/concentration`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${localStorage.getItem('token')}`,
                },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                const errData = await res.json();
                let errMsg = 'Failed to calculate concentration strategy';
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
                    <Target className="w-5 h-5" />
                    Concentration Management
                </h3>
                <p className="text-sm opacity-80 mt-1">
                    Tax-efficiently manage concentrated stock positions using an Equity Collar. This tool simulates selling calls to generate income and synthetically offset future tax burdens while buying puts to protect against significant downside crashes.
                </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Layers className="w-4 h-4 text-primary" /> Ticker
                        </span>
                    </label>
                    <input
                        type="text"
                        className="input input-bordered w-full"
                        value={ticker}
                        onChange={(e) => setTicker(e.target.value.toUpperCase())}
                        placeholder="e.g. AAPL"
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Briefcase className="w-4 h-4 text-success" /> Number of Shares
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={shares}
                        onChange={(e) => setShares(Number(e.target.value))}
                        min={100}
                        step={100}
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Coins className="w-4 h-4 text-warning" /> Avg Cost Basis ($/sh)
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={costBasis}
                        onChange={(e) => setCostBasis(Number(e.target.value))}
                        min={1}
                        step={0.5}
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <TrendingUp className="w-4 h-4 text-secondary" /> Position
                        </span>
                    </label>
                    <div className="flex bg-base-200 rounded-lg p-1">
                        <button
                            className={`flex-1 btn btn-sm ${positionType === 'long' ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => setPositionType('long')}
                        >
                            Long
                        </button>
                        <button
                            className={`flex-1 btn btn-sm ${positionType === 'short' ? 'btn-secondary' : 'btn-ghost'}`}
                            onClick={() => setPositionType('short')}
                        >
                            Short
                        </button>
                    </div>
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Percent className="w-4 h-4 text-error" /> Tax Bracket %
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={taxRatePct}
                        onChange={(e) => setTaxRatePct(Number(e.target.value))}
                        min={0}
                        max={50}
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Timer className="w-4 h-4 text-info" /> Duration (Days)
                        </span>
                    </label>
                    <select
                        className="select select-bordered w-full"
                        value={durationDays}
                        onChange={(e) => setDurationDays(Number(e.target.value))}
                    >
                        <option value={30}>30 Days</option>
                        <option value={60}>60 Days</option>
                        <option value={90}>90 Days</option>
                        <option value={120}>120 Days</option>
                        <option value={180}>180 Days</option>
                        <option value={365}>365 Days</option>
                    </select>
                </div>

                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <TrendingUp className="w-4 h-4 text-success" /> Target Upside Cap %
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full border-success/30 focus:border-success"
                        value={upsideCapPct}
                        onChange={(e) => setUpsideCapPct(Number(e.target.value))}
                        min={1}
                        max={100}
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <TrendingDown className="w-4 h-4 text-error" /> Target Downside Floor %
                        </span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full border-error/30 focus:border-error"
                        value={downsideProtectionPct}
                        onChange={(e) => setDownsideProtectionPct(Number(e.target.value))}
                        min={1}
                        max={100}
                    />
                </div>
            </div>

            <button
                className="btn btn-info w-full sm:w-auto"
                onClick={calculateStrategy}
                disabled={loading}
            >
                {loading ? (
                    <span className="loading loading-spinner"></span>
                ) : (
                    <Calculator className="w-5 h-5 mr-2" />
                )}
                Analyze Concentration Offset
            </button>

            {error && (
                <div className="alert alert-error text-sm">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                </div>
            )}

            {result && (
                <div className="mt-8 space-y-6 animate-fade-in">
                    {/* Top Stats */}
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
                        <div className="stat bg-base-100 shadow rounded-box border border-base-200">
                            <div className="stat-title">Current Spot</div>
                            <div className="stat-value text-xl">${result.currentPrice.toFixed(2)}</div>
                            <div className="stat-desc text-info">{result.ticker} {result.positionType.toUpperCase()}</div>
                        </div>
                        <div className="stat bg-base-100 shadow rounded-box border border-base-200">
                            <div className="stat-title">Unrealized Value</div>
                            <div className="stat-value text-xl">${result.currentValue.toLocaleString()}</div>
                            <div className="stat-desc text-success">{result.shares} shares @ ${result.costBasis} src</div>
                        </div>
                        <div className="stat bg-base-100 shadow rounded-box border border-base-200">
                            <div className="stat-title">Net Collar Premium</div>
                            <div className={`stat-value text-xl ${result.netPremiumTotal > 0 ? 'text-success' : 'text-error'}`}>
                                {result.netPremiumTotal > 0 ? '+' : ''}${result.netPremiumTotal.toFixed(2)}
                            </div>
                            <div className="stat-desc">Cash generated today</div>
                        </div>
                        <div className="stat bg-base-100 shadow rounded-box border border-base-200">
                            <div className="stat-title">Synthetic Tax Savings</div>
                            <div className="stat-value text-xl text-primary">${result.syntheticTaxSavings.toFixed(2)}</div>
                            <div className="stat-desc">Premium applying to tax</div>
                        </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {/* Options Legs */}
                        <div className="card bg-base-100 shadow border border-base-200">
                            <div className="card-body p-5">
                                <h3 className="card-title text-lg font-bold text-info mb-2">
                                    Recommended Rebalancing Trades
                                </h3>
                                <p className="text-sm opacity-80 mb-4">
                                    Execute these option blocks to synthetically reduce risk and capture premium while deciding on systematic unwind. Target expiration: <span className="font-bold">{result.expirationDate}</span> ({result.actualDte} DTE).
                                </p>
                                <div className="space-y-4">
                                    {result.legs.map((leg, idx) => (
                                        <div key={idx} className="flex flex-col text-sm border-l-4 pl-4 border-info bg-info/5 p-3 rounded-r-lg">
                                            <div className="flex justify-between items-baseline mb-1">
                                                <span className="font-bold text-base">
                                                    {leg.action} <span className="text-primary">{leg.qty}</span> {leg.type}s @ ${leg.strike.toFixed(2)}
                                                </span>
                                                <span className="font-mono text-xs opacity-70">${leg.midPrice.toFixed(2)} / contract</span>
                                            </div>
                                            <span className="text-xs opacity-70 italic">{leg.purpose}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>

                        {/* Tax impact Summary */}
                        <div className="card bg-base-100 shadow border border-base-200">
                            <div className="card-body p-5">
                                <h3 className="card-title text-base font-bold mb-2">
                                    Tax Impact & Synthetic Savings
                                </h3>
                                <p className="text-sm opacity-80 leading-relaxed">
                                    Holding {result.shares} concentrated shares of {result.ticker} at a ${result.costBasis} average basis creates significant capital gains exposure if sold outright.
                                    <br /><br />
                                    By selling covered options, you generate <strong className={result.netPremiumTotal > 0 ? 'text-success' : 'text-error'}>${result.netPremiumTotal.toFixed(2)}</strong> in upfront income structure.
                                    Since your estimated tax bracket is {result.taxRatePct}%, this premium acts as a <strong>synthetic paydown</strong> of approximately <strong className="text-primary">${result.syntheticTaxSavings.toFixed(2)}</strong> towards any future tax obligations realized when diversifying this block of stock, all while capping your downside risk simultaneously.
                                </p>
                            </div>
                        </div>
                    </div>

                    {/* Scenario Table */}
                    <div className="card bg-base-100 shadow border border-base-200 mt-6">
                        <div className="card-body p-5">
                            <h3 className="card-title text-lg font-bold mb-4 flex items-center gap-2">
                                <TrendingUp className="w-5 h-5 text-secondary" />
                                Tax-Adjusted Expiry Scenarios ({result.expirationDate})
                            </h3>

                            <div className="h-64 sm:h-80 w-full mb-6 relative">
                                <Chart
                                    type="bar"
                                    data={{
                                        labels: result.scenarios.map(s => `${s.underlyingChangePct > 0 ? '+' : ''}${s.underlyingChangePct}%`),
                                        datasets: [
                                            {
                                                type: 'bar' as const,
                                                label: 'Estimated Tax Owed ($)',
                                                data: result.scenarios.map(s => -s.taxOwed),
                                                backgroundColor: 'rgba(239, 68, 68, 0.8)', // error red
                                                borderRadius: 4,
                                            },
                                            {
                                                type: 'bar' as const,
                                                label: 'Net After-Tax Outcome ($)',
                                                data: result.scenarios.map(s => s.netAfterTaxValue),
                                                backgroundColor: 'rgba(34, 197, 94, 0.8)', // success green
                                                borderRadius: 4,
                                            },
                                            {
                                                type: 'line' as const,
                                                label: 'Current Position Value ($)',
                                                data: result.scenarios.map(() => result.currentValue),
                                                borderColor: 'rgba(156, 163, 175, 0.5)',
                                                borderWidth: 2,
                                                borderDash: [5, 5],
                                                pointRadius: 0,
                                                fill: false,
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
                                                    label: (context: any) => {
                                                        const yVal = context.parsed.y;
                                                        const val = yVal !== null && yVal !== undefined ? Math.abs(yVal) : 0;
                                                        return `${context.dataset.label}: $${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
                                                    }
                                                }
                                            }
                                        },
                                        scales: {
                                            y: {
                                                stacked: true,
                                                title: { display: true, text: 'Value ($)' },
                                                grid: { color: 'rgba(156, 163, 175, 0.1)' }
                                            },
                                            x: {
                                                stacked: true,
                                                title: { display: true, text: 'Underlying Asset Performance' },
                                                grid: { display: false }
                                            }
                                        }
                                    }}
                                />
                            </div>

                            <div className="overflow-x-auto">
                                <table className="table table-zebra w-full text-center text-sm">
                                    <thead>
                                        <tr>
                                            <th className="bg-base-200">Market Return</th>
                                            <th className="bg-base-200">Asset Price</th>
                                            <th className="bg-base-200">Outcome Status</th>
                                            <th className="bg-base-200 text-error">Est. Tax Owed</th>
                                            <th className="bg-base-200 text-success">Net After-Tax Outcome</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.scenarios.map((scenario, idx) => (
                                            <tr key={idx} className={scenario.taxOwed > 0 ? 'bg-error/5' : ''}>
                                                <td className="font-medium whitespace-nowrap">
                                                    <span className={scenario.underlyingChangePct < 0 ? 'text-error' : scenario.underlyingChangePct > 0 ? 'text-success' : ''}>
                                                        {scenario.underlyingChangePct > 0 ? '+' : ''}{scenario.underlyingChangePct}%
                                                    </span>
                                                </td>
                                                <td>${scenario.simulatedPrice.toFixed(2)}</td>
                                                <td className="font-semibold text-xs whitespace-nowrap">{scenario.status}</td>
                                                <td className={`${scenario.taxOwed > 0 ? 'text-error font-mono' : 'opacity-50'}`}>
                                                    ${scenario.taxOwed.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                                </td>
                                                <td className="font-bold text-success font-mono">
                                                    ${scenario.netAfterTaxValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
