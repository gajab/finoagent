import React, { useState } from 'react';
import { apiBase } from '../api';
import { Target, Zap, DollarSign, Shield, Activity, Sigma } from 'lucide-react';

interface OptionsLeg {
    action: string;
    qty: number;
    type: string;
    strike: number;
    midPrice: number;
    iv: number;
    delta: number;
    purpose: string;
    expiration: string;
}

interface Scenario {
    underlyingChangePct: number;
    simulatedPrice: number;
    status: string;
    netProfit: number;
    stockEquivalentProfit: number;
    vsStock: number;
    betterThanStock: boolean;
    probabilityPct: number | null;
}

interface Greeks {
    netDelta: number;
    netGamma: number;
    netTheta: number;
    netVega: number;
}

interface Vol {
    atmIv: number;
    forward: number;
    expectedTerminalPrice: number | null;
    sviFitRmseVolPts: number | null;
    sviArbFree: boolean | null;
    pricingEngine: string;
    heston: Record<string, number> | null;
}

interface Risk {
    maxLoss: number;
    maxLossVsStockFullDrop: number;
    worstUnderperfVsStock: number;
    worstUnderperfVsStockUnhedged: number;
    worstUnderperfPrice: number;
    lossZoneLow: number;
    lossZoneHigh: number;
    pInLossZonePct: number | null;
    pMaxLossPct: number | null;
    pProfitPct: number | null;
}

interface Hedge {
    mode: string;
    protectiveType: string;
    protectiveBuyStrike: number;
    protectiveSellStrike: number;
    protectiveCost: number;
    netHedgeCost: number;
    fundingStrike: number | null;
    fundingType: string | null;
    fundingCredit: number | null;
    upsideCapPct: number | null;
}

interface ZebraResult {
    success: boolean;
    ticker: string;
    currentPrice: number;
    investmentAmount: number;
    strategy: string;
    variant: string;
    expiration: string;
    dte: number;
    qtyStructures: number;
    netExtrinsicValue: number;
    totalDebit: number;
    capitalEfficiencySaved: number;
    greeks: Greeks;
    vol: Vol;
    risk: Risk;
    hedge: Hedge | null;
    legs: OptionsLeg[];
    scenarios: Scenario[];
}

const usd = (n: number) =>
    `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

export function ZebraStrategy() {
    const [ticker, setTicker] = useState('SPY');
    const [amount, setAmount] = useState<number>(10000);
    const [isCall, setIsCall] = useState<boolean>(true);
    const [durationDays, setDurationDays] = useState<number>(45);
    const [strategyVariant, setStrategyVariant] = useState<string>('zero_extrinsic');
    const [hedgeMode, setHedgeMode] = useState<string>('protective');

    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<ZebraResult | null>(null);

    const analyzeZebra = async () => {
        if (!ticker) {
            setError('Please enter a target ticker.');
            return;
        }

        setLoading(true);
        setError(null);
        setResult(null);

        try {
            const payload = {
                amount,
                is_call: isCall,
                duration_days: durationDays,
                strategy_variant: strategyVariant,
                hedge_mode: hedgeMode,
            };

            const res = await fetch(`${apiBase}/api/stock/${ticker}/strategies/zebra`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${localStorage.getItem('token')}`,
                },
                body: JSON.stringify(payload),
            });

            if (!res.ok) {
                const errData = await res.json();
                let errMsg = 'Failed to analyze ZEBRA structure';
                if (errData.detail) {
                    errMsg = typeof errData.detail === 'string' ? errData.detail : errData.detail[0]?.msg || errMsg;
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

    const hedged = result?.hedge && result.hedge.mode !== 'none';
    const stockLabel = isCall ? 'Long 100 Shares' : 'Short 100 Shares';

    return (
        <div className="space-y-6">
            <div className="bg-gradient-to-r from-primary/10 to-transparent p-6 rounded-2xl border border-primary/20">
                <div className="flex items-start gap-4">
                    <div className="bg-primary/20 p-3 rounded-xl shrink-0">
                        <Target className="w-6 h-6 text-primary" />
                    </div>
                    <div>
                        <h2 className="text-xl font-bold">ZEBRA (Zero Extrinsic BackRatio Amplitude)</h2>
                        <p className="opacity-70 text-sm mt-1 max-w-3xl">
                            Simulate long stock exposure without paying extrinsic value. Buy 2 deep-ITM options and sell 1 ATM option in the same expiration cycle—financing your entire external risk premium with the short leg. Legs are priced off a calibrated SVI volatility smile, and scenario odds come from the risk-neutral density.
                        </p>

                        <div className="mt-3 text-xs bg-info/10 border border-info/30 text-base-content/90 p-3 rounded-lg flex items-start gap-2 max-w-3xl">
                            <Shield className="w-4 h-4 text-info mt-0.5 shrink-0" />
                            <p className="opacity-90">
                                A raw ZEBRA runs <span className="font-bold">+200 delta</span> between the long and short strikes, so it loses at roughly <span className="font-bold">2× the rate of stock</span> on a moderate drop. The <span className="font-bold">Downside Hedge</span> adds a protective leg that cancels that extra delta—so losses track (or beat) holding shares—while your upside is preserved.
                            </p>
                        </div>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="form-control w-full">
                    <label className="label">
                        <span className="label-text font-bold text-base">Target Ticker</span>
                    </label>
                    <input
                        type="text"
                        className="input input-bordered w-full uppercase"
                        placeholder="e.g. SPY"
                        value={ticker}
                        onChange={e => setTicker(e.target.value.toUpperCase())}
                    />
                </div>
                <div className="form-control w-full">
                    <label className="label">
                        <span className="label-text font-bold text-base">Investment Amount ($)</span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={amount}
                        min={1000}
                        onChange={e => setAmount(Number(e.target.value))}
                    />
                </div>
                <div className="form-control w-full">
                    <label className="label">
                        <span className="label-text font-bold text-base">Directional Conviction</span>
                    </label>
                    <select
                        className="select select-bordered w-full"
                        value={isCall ? 'bullish' : 'bearish'}
                        onChange={e => setIsCall(e.target.value === 'bullish')}
                    >
                        <option value="bullish">Bullish Proxy (Calls)</option>
                        <option value="bearish">Bearish Proxy (Puts)</option>
                    </select>
                </div>
                <div className="form-control w-full">
                    <label className="label">
                        <span className="label-text font-bold text-base">Target Duration (Days)</span>
                    </label>
                    <input
                        type="number"
                        className="input input-bordered w-full"
                        value={durationDays}
                        min={7}
                        step={1}
                        onChange={e => setDurationDays(Number(e.target.value))}
                    />
                </div>
                <div className="form-control w-full">
                    <label className="label">
                        <span className="label-text font-bold text-base">Risk / Decay Variant</span>
                    </label>
                    <select
                        className="select select-bordered w-full"
                        value={strategyVariant}
                        onChange={e => setStrategyVariant(e.target.value)}
                    >
                        <option value="zero_extrinsic">Classic ZEBRA (Immunize Decay)</option>
                        <option value="low_debit">Lower Debit (Accepts Time Decay Loss)</option>
                        <option value="theta_positive">Theta Positive (Profits on Time Decay)</option>
                    </select>
                </div>
                <div className="form-control w-full">
                    <label className="label">
                        <span className="label-text font-bold text-base">Downside Hedge</span>
                    </label>
                    <select
                        className="select select-bordered w-full"
                        value={hedgeMode}
                        onChange={e => setHedgeMode(e.target.value)}
                    >
                        <option value="protective">Protective Spread — losses track stock, keep full upside</option>
                        <option value="collar">Collar — caps upside ~1σ to fund the hedge</option>
                        <option value="none">None — raw 2× downside (classic)</option>
                    </select>
                </div>
            </div>

            <div className="flex justify-end pt-4">
                <button
                    className="btn btn-primary shadow-lg"
                    onClick={analyzeZebra}
                    disabled={loading}
                >
                    {loading ? (
                        <span className="loading loading-spinner"></span>
                    ) : (
                        <Zap className="w-5 h-5" />
                    )}
                    Generate ZEBRA Matrix
                </button>
            </div>

            {error && (
                <div className="alert alert-error mt-4 text-sm shadow-sm">
                    <span className="font-bold">Error:</span> {error}
                </div>
            )}

            {result && (
                <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500 mt-8">
                    {/* Headline stats */}
                    <div className="stats bg-base-200/40 border border-white/[0.03] rounded-xl w-full overflow-hidden">
                        <div className="stat place-items-center">
                            <div className="stat-title text-xs font-bold uppercase opacity-60">Spot Price</div>
                            <div className="stat-value text-2xl">${result.currentPrice}</div>
                            <div className="stat-desc font-mono">Exp {result.expiration} · {result.dte}d</div>
                        </div>
                        <div className="stat place-items-center">
                            <div className="stat-title text-xs font-bold uppercase opacity-60">Structure Debit</div>
                            <div className="stat-value text-primary text-2xl">{usd(result.totalDebit)}</div>
                            <div className="stat-desc font-mono text-primary font-bold">{result.qtyStructures} structures</div>
                        </div>
                        <div className="stat place-items-center">
                            <div className="stat-title text-xs font-bold uppercase opacity-60 text-secondary">Capital Saved vs 100 Shares</div>
                            <div className="stat-value text-secondary text-2xl">{usd(result.capitalEfficiencySaved)}</div>
                        </div>
                        <div className="stat place-items-center">
                            <div className="stat-title text-xs font-bold uppercase opacity-60">Net Extrinsic Value</div>
                            <div className={`stat-value text-2xl ${Math.abs(result.netExtrinsicValue) < 0.2 ? 'text-success' : 'text-warning'}`}>
                                {result.netExtrinsicValue > 0 ? '+' : ''}{result.netExtrinsicValue}
                            </div>
                            <div className="stat-desc font-mono opacity-70">time-decay per share</div>
                        </div>
                    </div>

                    {/* Downside protection callout — the vs-stock story */}
                    <div className={`rounded-xl border p-5 ${hedged ? 'border-success/30 bg-success/5' : 'border-warning/30 bg-warning/5'}`}>
                        <div className="flex items-center gap-2 mb-4">
                            <Shield className={`w-5 h-5 ${hedged ? 'text-success' : 'text-warning'}`} />
                            <h3 className="text-base font-bold">Downside vs Holding {isCall ? '100 Shares' : 'Short 100 Shares'}</h3>
                            {result.hedge && (
                                <span className={`badge badge-sm ml-auto ${hedged ? 'badge-success' : 'badge-ghost'}`}>
                                    {result.hedge.mode === 'protective' ? 'Protective spread on'
                                        : result.hedge.mode === 'collar'
                                            ? `Collar · caps ${Math.abs(result.hedge.upsideCapPct ?? 0)}% ${isCall ? 'up' : 'down'}`
                                            : 'Unhedged'}
                                </span>
                            )}
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                            <div>
                                <div className="text-xs uppercase opacity-60 font-bold">Worst case vs stock</div>
                                <div className={`text-xl font-bold font-mono ${result.risk.worstUnderperfVsStock >= -1 ? 'text-success' : 'text-error'}`}>
                                    {usd(result.risk.worstUnderperfVsStock)}
                                </div>
                                <div className="text-[11px] opacity-60">at ${result.risk.worstUnderperfPrice}</div>
                            </div>
                            <div>
                                <div className="text-xs uppercase opacity-60 font-bold">If unhedged</div>
                                <div className="text-xl font-bold font-mono text-error/80">
                                    {usd(result.risk.worstUnderperfVsStockUnhedged)}
                                </div>
                                <div className="text-[11px] opacity-60">raw 2× loss zone</div>
                            </div>
                            <div>
                                <div className="text-xs uppercase opacity-60 font-bold">Max loss (structure)</div>
                                <div className="text-xl font-bold font-mono text-error">{usd(-result.risk.maxLoss)}</div>
                                <div className="text-[11px] opacity-60">vs {usd(-result.risk.maxLossVsStockFullDrop)} full stock</div>
                            </div>
                            <div>
                                <div className="text-xs uppercase opacity-60 font-bold">Odds in 2× zone</div>
                                <div className="text-xl font-bold font-mono">
                                    {result.risk.pInLossZonePct != null ? `${result.risk.pInLossZonePct}%` : '—'}
                                </div>
                                <div className="text-[11px] opacity-60">${result.risk.lossZoneLow}–${result.risk.lossZoneHigh}</div>
                            </div>
                        </div>
                        {hedged && result.risk.worstUnderperfVsStockUnhedged < result.risk.worstUnderperfVsStock && (
                            <div className="text-xs text-success/90 mt-3 text-center">
                                Hedge cuts the worst-case gap vs stock from {usd(result.risk.worstUnderperfVsStockUnhedged)} to {usd(result.risk.worstUnderperfVsStock)}
                                {result.hedge?.netHedgeCost != null && ` · net hedge cost $${result.hedge.netHedgeCost}/share`}.
                            </div>
                        )}
                    </div>

                    {/* Quant analytics row */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        <div className="rounded-xl border border-white/[0.03] bg-base-200/30 p-4">
                            <div className="flex items-center gap-1.5 text-xs uppercase font-bold opacity-60"><Activity className="w-3.5 h-3.5" /> Net Delta</div>
                            <div className="text-lg font-bold font-mono mt-1">{result.greeks.netDelta}</div>
                            <div className="text-[11px] opacity-60">≈ shares of exposure</div>
                        </div>
                        <div className="rounded-xl border border-white/[0.03] bg-base-200/30 p-4">
                            <div className="flex items-center gap-1.5 text-xs uppercase font-bold opacity-60"><Sigma className="w-3.5 h-3.5" /> Net Theta</div>
                            <div className={`text-lg font-bold font-mono mt-1 ${result.greeks.netTheta >= 0 ? 'text-success' : 'text-error'}`}>
                                {usd(result.greeks.netTheta)}<span className="text-xs opacity-60">/day</span>
                            </div>
                            <div className="text-[11px] opacity-60">time-decay P&L</div>
                        </div>
                        <div className="rounded-xl border border-white/[0.03] bg-base-200/30 p-4">
                            <div className="text-xs uppercase font-bold opacity-60">ATM Implied Vol</div>
                            <div className="text-lg font-bold font-mono mt-1">{result.vol.atmIv}%</div>
                            <div className="text-[11px] opacity-60">
                                {result.vol.expectedTerminalPrice != null ? `E[Sₜ] $${result.vol.expectedTerminalPrice}` : `fwd $${result.vol.forward}`}
                            </div>
                        </div>
                        <div className="rounded-xl border border-white/[0.03] bg-base-200/30 p-4">
                            <div className="text-xs uppercase font-bold opacity-60">Probability</div>
                            <div className="text-lg font-bold font-mono mt-1">
                                {result.risk.pProfitPct != null ? `${result.risk.pProfitPct}%` : '—'}
                                <span className="text-xs opacity-60 font-normal"> profit</span>
                            </div>
                            <div className="text-[11px] opacity-60">
                                {result.risk.pMaxLossPct != null ? `${result.risk.pMaxLossPct}% max-loss` : ''}
                            </div>
                        </div>
                    </div>

                    <div className="text-[11px] opacity-50 -mt-2 flex flex-wrap gap-x-3 gap-y-1">
                        <span>Engine: {result.vol.pricingEngine}</span>
                        {result.vol.sviFitRmseVolPts != null && <span>· SVI fit RMSE {result.vol.sviFitRmseVolPts} vol-pts</span>}
                        {result.vol.sviArbFree === false && <span className="text-warning">· butterfly-arb flag</span>}
                        {result.vol.heston && <span>· Heston vol-of-vol {result.vol.heston.vol_of_vol}, ρ {result.vol.heston.spot_vol_corr}</span>}
                    </div>

                    {/* Legs table — stable layout (no hover reflow) */}
                    <div className="overflow-x-auto rounded-xl border border-white/[0.03] mt-2">
                        <table className="table table-sm table-pro w-full font-mono text-sm">
                            <thead>
                                <tr className="bg-base-200/50 text-base-content/70">
                                    <th className="py-3">Action</th>
                                    <th>Option</th>
                                    <th>Strike</th>
                                    <th className="text-right">Price</th>
                                    <th className="text-right">IV</th>
                                    <th className="text-right">Δ</th>
                                    <th className="w-[260px]">Strategic Function</th>
                                </tr>
                            </thead>
                            <tbody>
                                {result.legs.map((leg, idx) => (
                                    <tr key={idx} className="hover:bg-base-200/30">
                                        <td className="py-3">
                                            <span className={`badge badge-sm font-bold ${leg.action === 'Buy' ? 'badge-primary text-primary-content' : 'badge-secondary text-secondary-content'}`}>
                                                {leg.action} {leg.qty}
                                            </span>
                                        </td>
                                        <td>{leg.expiration} {leg.type}</td>
                                        <td className="font-bold">${leg.strike}</td>
                                        <td className="text-right text-base-content/70">${leg.midPrice}</td>
                                        <td className="text-right text-base-content/60">{leg.iv}%</td>
                                        <td className="text-right text-base-content/60">{leg.delta}</td>
                                        <td className="w-[260px] whitespace-normal leading-snug text-xs text-base-content/60" title={leg.purpose}>
                                            {leg.purpose}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    {/* Scenario table */}
                    <div>
                        <h3 className="text-lg font-bold flex items-center gap-2 mb-4 border-b border-white/[0.05] pb-2">
                            <DollarSign className="w-5 h-5 text-success" />
                            Maturity Expiry Scenarios
                        </h3>
                        <div className="overflow-x-auto rounded-xl border border-white/[0.03]">
                            <table className="table table-sm table-pro w-full font-mono text-sm">
                                <thead>
                                    <tr className="bg-base-200/50 text-base-content/70">
                                        <th className="py-3">Mkt Move</th>
                                        <th>Expiry Spot</th>
                                        <th className="text-right">Odds</th>
                                        <th className="text-right">ZEBRA P&L</th>
                                        <th className="text-right border-l border-white/[0.05] pl-4">{stockLabel} P&L</th>
                                        <th className="text-right">Δ vs Shares</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {result.scenarios.map((scen, idx) => {
                                        const isZebraProfitable = scen.netProfit > 0;
                                        return (
                                            <tr key={idx} className={scen.underlyingChangePct === 0 ? 'bg-base-200/20' : 'hover:bg-base-200/30'}>
                                                <td className="py-3 font-bold">
                                                    {scen.underlyingChangePct > 0 ? '+' : ''}{scen.underlyingChangePct}%
                                                </td>
                                                <td>${scen.simulatedPrice}</td>
                                                <td className="text-right text-base-content/50">
                                                    {scen.probabilityPct != null ? `${scen.probabilityPct}%` : '—'}
                                                </td>
                                                <td className={`text-right font-bold ${isZebraProfitable ? 'text-success' : 'text-error'}`}>
                                                    {usd(scen.netProfit)}
                                                </td>
                                                <td className="text-right text-base-content/60 border-l border-white/[0.05] pl-4">
                                                    {usd(scen.stockEquivalentProfit)}
                                                </td>
                                                <td className={`text-right font-semibold ${scen.betterThanStock ? 'text-success/80' : 'text-error/80'}`}>
                                                    {scen.vsStock >= 0 ? '+' : ''}{usd(scen.vsStock)}
                                                </td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                        <p className="text-[11px] opacity-50 mt-2">
                            “Δ vs Shares” is ZEBRA P&L minus the equivalent stock position. Green means the ZEBRA does at least as well as holding shares at that price; odds are the risk-neutral probability of expiring in each bucket.
                        </p>
                    </div>
                </div>
            )}
        </div>
    );
}
