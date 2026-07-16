import React, { useState } from 'react';
import { apiBase } from '../api';
import {
    Briefcase, AlertCircle, Percent, Timer, Calculator,
    TrendingUp, TrendingDown, Layers, Target, Clock
} from 'lucide-react';
import {
    Chart as ChartJS, CategoryScale, LinearScale, PointElement,
    LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(
    CategoryScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler
);

interface OptionLeg {
    action: string;
    qty: number;
    type: string;
    strike: number;
    midPrice: number;
    purpose: string;
    expiration: string;
}

interface Scenario {
    underlyingChangePct: number;
    simulatedPrice: number;
    status: string;
    netProfit: number;
    stockEquivalentProfit: number;
}

interface PmccResult {
    ticker: string;
    currentPrice: number;
    investmentAmount: number;
    strategy: string;
    leapsExpiration: string;
    shortExpiration: string;
    qtyContracts: number;
    totalDebit: number;
    capitalEfficiencySaved: number;
    leapsCost: number;
    shortPremiumCollected: number;
    legs: OptionLeg[];
    scenarios: Scenario[];
}

export function PoorMansCovered() {
    const [ticker, setTicker] = useState('AAPL');
    const [amount, setAmount] = useState<number>(5000);
    const [isCall, setIsCall] = useState<boolean>(true);
    const [leapsDurationDays, setLeapsDurationDays] = useState<number>(365);
    const [shortDurationDays, setShortDurationDays] = useState<number>(30);

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<PmccResult | null>(null);

    const calculateStrategy = async () => {
        if (!ticker) {
            setError('Please enter a ticker symbol');
            return;
        }
        if (amount <= 0) {
            setError('Amount must be greater than 0');
            return;
        }
        if (leapsDurationDays <= shortDurationDays) {
            setError('LEAPS duration must be strictly greater than the short duration leg.');
            return;
        }

        setLoading(true);
        setError(null);
        setResult(null);

        try {
            const payload = {
                amount: Number(amount),
                is_call: isCall,
                leaps_duration_days: Number(leapsDurationDays),
                short_duration_days: Number(shortDurationDays),
            };

            const res = await fetch(`${apiBase}/api/stock/${ticker}/strategies/pmcc`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${localStorage.getItem('token')}`,
                },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                const errData = await res.json();
                let errMsg = 'Failed to calculate strategy';
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
                    Poor Man's Covered Call / Put
                </h3>
                <p className="text-sm opacity-80 mt-1">
                    A highly capital-efficient substitute for a traditional Covered Call or Covered Put. Instead of deploying massive capital to hold or short 100 shares of the underlying stock outright, this strategy buys a Deep In-The-Money (ITM) LEAPS option as a Delta surrogate, while continuously selling Near-Term Out-of-The-Money (OTM) options against it to generate identical income for a fraction of the cost.
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
                        placeholder="e.g. MSFT"
                    />
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Briefcase className="w-4 h-4 text-success" /> Max Capital ($)
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
                            <TrendingUp className="w-4 h-4 text-secondary" /> Directional Bias
                        </span>
                    </label>
                    <div className="flex bg-base-200 rounded-lg p-1">
                        <button
                            className={`flex-1 btn btn-sm ${isCall ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => setIsCall(true)}
                        >
                            Bullish (Call)
                        </button>
                        <button
                            className={`flex-1 btn btn-sm ${!isCall ? 'btn-secondary' : 'btn-ghost'}`}
                            onClick={() => setIsCall(false)}
                        >
                            Bearish (Put)
                        </button>
                    </div>
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Clock className="w-4 h-4 text-info" /> Deep LEAPS Expiry Target
                        </span>
                    </label>
                    <select
                        className="select select-bordered w-full"
                        value={leapsDurationDays}
                        onChange={(e) => setLeapsDurationDays(Number(e.target.value))}
                    >
                        <option value={180}>6 Months (~180 Days)</option>
                        <option value={240}>8 Months (~240 Days)</option>
                        <option value={365}>1 Year (~365 Days)</option>
                        <option value={500}>1.5 Years (~500 Days)</option>
                    </select>
                </div>
                <div className="form-control">
                    <label className="label">
                        <span className="label-text font-medium flex items-center gap-1">
                            <Timer className="w-4 h-4 text-warning" /> Short Leg Expiry Target
                        </span>
                    </label>
                    <select
                        className="select select-bordered w-full"
                        value={shortDurationDays}
                        onChange={(e) => setShortDurationDays(Number(e.target.value))}
                    >
                        <option value={7}>1 Week (~7 Days)</option>
                        <option value={14}>2 Weeks (~14 Days)</option>
                        <option value={30}>1 Month (~30 Days)</option>
                        <option value={45}>45 Days</option>
                        <option value={60}>2 Months (~60 Days)</option>
                    </select>
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
                Analyze PMCC / PMCP Setup
            </button>

            {error && (
                <div className="alert alert-error text-sm">
                    <AlertCircle className="w-4 h-4" />
                    {error}
                </div>
            )}

            {result && (
                <div className="mt-8 space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
                    {/* Header */}
                    <div className="flex flex-col md:flex-row justify-between items-start md:items-end border-b border-white/[0.05] pb-4">
                        <div>
                            <h2 className="text-3xl font-extrabold flex items-center gap-3">
                                {result.ticker} <span className="text-base font-normal opacity-60">@ ${result.currentPrice.toFixed(2)}</span>
                            </h2>
                            <p className="opacity-70 mt-1 capitalize font-medium text-lg text-primary">{result.strategy} Engine</p>
                        </div>
                        <div className="text-right mt-4 md:mt-0">
                            <p className="text-sm opacity-60 uppercase tracking-wide">Investment Ceiling</p>
                            <p className="font-mono text-2xl font-bold">${result.investmentAmount.toLocaleString()}</p>
                        </div>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* Capital Efficiency Callout */}
                        <div className="glass-card bg-gradient-to-br from-success/20 to-transparent border-success/30 md:col-span-2">
                            <div className="p-6 flex flex-col sm:flex-row justify-between items-center gap-4">
                                <div className="flex-1">
                                    <h3 className="font-bold text-sm mb-1 text-success items-center gap-2">
                                        <Calculator className="w-5 h-5" />
                                        Capital Efficiency Multiplier
                                    </h3>
                                    <p className="text-sm opacity-80">
                                        Instead of spending ${(result.qtyContracts * result.currentPrice * 100).toLocaleString()} to margin {result.qtyContracts * 100} shares of stock outright, executing this options structure costs just <strong>${result.totalDebit.toLocaleString()}</strong> while replicating identical directional delta.
                                    </p>
                                </div>
                                <div className="stat place-items-center bg-base-200/40 rounded-xl max-w-sm border border-white/[0.03] shrink-0 border border-success/20 shadow-sm">
                                    <div className="stat-title font-semibold text-xs text-base-content/70">Margin Capital Saved</div>
                                    <div className="stat-value text-success text-2xl">${result.capitalEfficiencySaved.toLocaleString()}</div>
                                    <div className="stat-desc font-mono">deployable elsewhere</div>
                                </div>
                            </div>
                        </div>

                        {/* Options Legs Mapping */}
                        <div className="glass-card md:col-span-2">
                            <div className="p-5">
                                <h3 className="font-bold text-sm mb-4 flex items-center gap-2">
                                    <Layers className="w-5 h-5 text-primary" />
                                    Strategic Options Architecture
                                </h3>
                                <div className="space-y-4">
                                    {result.legs.map((leg, idx) => (
                                        <div key={idx} className={`p-4 rounded-xl border flex flex-col gap-2 ${leg.action === 'Buy' ? 'bg-primary/5 border-primary/20' : 'bg-warning/5 border-warning/20'}`}>
                                            <div className="flex justify-between items-start">
                                                <span className={`font-bold px-3 py-1 rounded-full text-sm ${leg.action === 'Buy' ? 'bg-primary text-primary-content' : 'bg-warning text-warning-content'}`}>
                                                    {leg.action} {leg.qty} {leg.type}
                                                </span>
                                                <span className="font-mono text-sm font-semibold opacity-80">{leg.expiration}</span>
                                            </div>
                                            <div className="flex justify-between items-center mt-1">
                                                <span className="text-lg font-bold">${leg.strike.toFixed(2)} Strike</span>
                                                <span className="font-mono text-sm opacity-70">${leg.midPrice.toFixed(2)} premium/contract</span>
                                            </div>
                                            <p className="text-sm opacity-70 italic mt-1 leading-relaxed border-t border-white/[0.05] pt-2">{leg.purpose}</p>
                                        </div>
                                    ))}
                                </div>
                                <div className="mt-4 p-4 bg-base-200/40 rounded-xl border border-white/[0.03] flex justify-between items-center text-sm">
                                    <span className="font-medium opacity-80">Cycle Net Debit:</span>
                                    <span className="font-mono font-bold text-lg text-primary">${result.totalDebit.toLocaleString()} Total</span>
                                </div>
                            </div>
                        </div>

                    </div>

                    {/* Scenario Table */}
                    <div className="glass-card mt-6 md:col-span-2">
                        <div className="p-5">
                            <h3 className="font-bold text-sm mb-4 flex items-center gap-2">
                                <TrendingUp className="w-5 h-5 text-secondary" />
                                Payout Table @ Short Expiry ({result.shortExpiration})
                            </h3>

                            <div className="h-64 sm:h-80 w-full mb-6 relative">
                                <Line
                                    data={{
                                        labels: result.scenarios.map(s => `${s.underlyingChangePct > 0 ? '+' : ''}${s.underlyingChangePct}%`),
                                        datasets: [
                                            {
                                                label: 'PMCC Net Profit ($)',
                                                data: result.scenarios.map(s => s.netProfit),
                                                borderColor: 'rgba(54, 211, 153, 1)',
                                                backgroundColor: 'rgba(54, 211, 153, 0.2)',
                                                borderWidth: 2,
                                                fill: true,
                                                tension: 0.1,
                                                pointRadius: 4,
                                                pointBackgroundColor: result.scenarios.map(s => s.netProfit > 0 ? '#22c55e' : s.netProfit < 0 ? '#ef4444' : '#9ca3af')
                                            },
                                            {
                                                label: 'Outright Stock Profit ($)',
                                                data: result.scenarios.map(s => s.stockEquivalentProfit),
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
                                                    label: (context: any) => {
                                                        const yVal = context.parsed.y;
                                                        return `${context.dataset.label}: $${yVal !== null && yVal !== undefined ? yVal.toFixed(2) : '0.00'}`;
                                                    }
                                                }
                                            }
                                        },
                                        scales: {
                                            y: {
                                                title: { display: true, text: 'Net Profit ($)' },
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
                                <table className="table table-pro w-full text-center text-sm">
                                    <thead>
                                        <tr className="bg-base-200/50">
                                            <th>Spot Shift</th>
                                            <th>Simulated Spot ($)</th>
                                            <th>Short Leg Status</th>
                                            <th>Holding Outright Profit ($)</th>
                                            <th>PMCC / PMCP Net Profit ($)</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {result.scenarios.map((scen, idx) => (
                                            <tr key={idx}>
                                                <td className="font-mono">
                                                    <span className={scen.underlyingChangePct > 0 ? 'text-success' : scen.underlyingChangePct < 0 ? 'text-error' : ''}>
                                                        {scen.underlyingChangePct > 0 ? '+' : ''}{scen.underlyingChangePct}%
                                                    </span>
                                                </td>
                                                <td className="font-mono">${scen.simulatedPrice.toFixed(2)}</td>
                                                <td className="font-semibold italic opacity-80 truncate max-w-[150px]">{scen.status}</td>
                                                <td className="font-mono">
                                                    <span className={scen.stockEquivalentProfit > 0 ? 'text-success' : scen.stockEquivalentProfit < 0 ? 'text-error' : ''}>
                                                        {scen.stockEquivalentProfit > 0 ? '+' : ''}${scen.stockEquivalentProfit.toLocaleString()}
                                                    </span>
                                                </td>
                                                <td className="font-mono font-bold">
                                                    <span className={scen.netProfit > 0 ? 'text-success' : scen.netProfit < 0 ? 'text-error' : ''}>
                                                        {scen.netProfit > 0 ? '+' : ''}${scen.netProfit.toLocaleString()}
                                                    </span>
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
