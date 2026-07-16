import React, { useState, useEffect } from 'react';
import { computeCppiStrategy } from '../api';
import {
  ShieldCheck, AlertTriangle, Info, DollarSign,
  TrendingUp, TrendingDown, RefreshCw, ShieldAlert,
  Sliders, Calendar, BarChart2, Percent, CheckCircle2,
  Lock, Unlock
} from 'lucide-react';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, BarElement, Title, Tooltip, Legend, Filler
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(
  CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title, Tooltip, Legend, Filler
);

interface PerformanceSummary {
  cumulativeReturn: number;
  cagr: number;
  volatility: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  maxDrawdown: number;
  finalValue: number;
}

interface RiskAnalysis {
  cashLocked: boolean;
  cashLockDate: string | null;
  totalTrades: number;
  totalFeesPaid: number;
  maxLeverageReached: number;
  floorBreached: boolean;
  worstFloorBreachPct: number;
  gapBreachThresholdPct: number;
  gapBreachProbabilityPct: number;
  currentAssetVol: number;
  averageAssetVol: number;
  latestMultiplier: number;
}

interface CppiCharts {
  dates: string[];
  cppiValue: number[];
  buyAndHoldValue: number[];
  floorValue: number[];
  riskyExposure: number[];
  cashExposure: number[];
  multiplier: number[];
}

interface TradeLog {
  date: string;
  action: string;
  price: number;
  shares: number;
  value: number;
  fee: number;
  portfolio_value: number;
  cash_balance: number;
}

interface CppiResult {
  success: boolean;
  ticker: string;
  currentPrice: number;
  investmentAmount: number;
  floorPct: number;
  riskMultiplier: number;
  durationYears: number;
  rebalanceFreq: string;
  rebalanceThresholdPct: number;
  riskFreeRate: number;
  transactionFeePct: number;
  allowLeverage: boolean;
  dynamicMultiplier: boolean;
  performance: {
    cppi: PerformanceSummary;
    buyAndHold: PerformanceSummary;
  };
  riskAnalysis: RiskAnalysis;
  charts: CppiCharts;
  trades: TradeLog[];
}

// ── Small reusable components ──────────────────────────────────────────────

function ParamLabel({ children }: { children: React.ReactNode }) {
  return <div className="text-xs font-semibold text-base-content/50 mb-1.5 uppercase tracking-wide">{children}</div>;
}

function SectionDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 pt-1">
      <div className="h-px flex-1 bg-white/[0.06]" />
      <span className="text-[10px] font-bold uppercase tracking-widest opacity-30">{label}</span>
      <div className="h-px flex-1 bg-white/[0.06]" />
    </div>
  );
}

function MetricRow({
  label, cppi, bh, cppiClass = 'text-secondary', isBetter
}: {
  label: string;
  cppi: string;
  bh: string;
  cppiClass?: string;
  isBetter?: boolean;
}) {
  return (
    <tr className="border-t border-white/[0.04] hover:bg-white/[0.02]">
      <td className="py-2.5 pr-4 text-xs text-base-content/60 font-medium whitespace-nowrap">{label}</td>
      <td className={`py-2.5 text-right font-bold font-mono text-sm ${cppiClass}`}>{cppi}</td>
      <td className="py-2.5 text-right font-mono text-sm text-base-content/50">{bh}</td>
    </tr>
  );
}

// ──────────────────────────────────────────────────────────────────────────

export function CppiStrategy() {
  const [ticker, setTicker] = useState('NVDA');
  const [amount, setAmount] = useState<number>(100000);
  const [floorPct, setFloorPct] = useState<number>(90);
  const [multiplier, setMultiplier] = useState<number>(4);
  const [durationYears, setDurationYears] = useState<number>(3);
  const [rebalanceFreq, setRebalanceFreq] = useState<string>('weekly');
  const [rebalanceThresholdPct, setRebalanceThresholdPct] = useState<number>(2.0);
  const [riskFreeRate, setRiskFreeRate] = useState<number>(4.5);
  const [transactionFeePct, setTransactionFeePct] = useState<number>(0.1);
  const [allowLeverage, setAllowLeverage] = useState<boolean>(false);
  const [dynamicMultiplier, setDynamicMultiplier] = useState<boolean>(false);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CppiResult | null>(null);
  const [activeTab, setActiveTab] = useState<'chart' | 'allocation' | 'trades'>('chart');
  const [simulatedDrop, setSimulatedDrop] = useState<number>(-15);

  const runSimulation = async () => {
    if (!ticker) { setError('Please enter a stock ticker.'); return; }
    if (amount < 1000) { setError('Initial capital must be at least $1,000.'); return; }
    setLoading(true); setError(null); setResult(null);
    try {
      const data = await computeCppiStrategy({
        ticker, amount, floor_pct: floorPct, multiplier,
        duration_years: durationYears, rebalance_freq: rebalanceFreq,
        rebalance_threshold_pct: rebalanceThresholdPct, risk_free_rate: riskFreeRate,
        transaction_fee_pct: transactionFeePct, allow_leverage: allowLeverage,
        dynamic_multiplier: dynamicMultiplier
      });
      if (data.error) throw new Error(data.error);
      setResult(data);
    } catch (err: any) {
      setError(err.message || 'An error occurred during CPPI backtest simulation.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { runSimulation(); }, []);

  const getStressTestOutput = () => {
    if (!result) return { breach: false, finalCppiValue: 0, shortfallPct: 0 };
    const weight = result.charts.riskyExposure[result.charts.riskyExposure.length - 1]
      / result.charts.cppiValue[result.charts.cppiValue.length - 1];
    const simReturnPct = weight * simulatedDrop;
    const finalCppiValue = 100.0 * (1.0 + simReturnPct / 100.0);
    const breach = finalCppiValue < result.floorPct;
    return { breach, finalCppiValue, shortfallPct: breach ? ((result.floorPct - finalCppiValue) / result.floorPct) * 100.0 : 0.0 };
  };

  const stressResult = getStressTestOutput();

  const chartBaseOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index' as const, intersect: false },
    plugins: { legend: { display: false } },
    scales: {
      y: {
        grid: { color: 'rgba(156,163,175,0.05)' },
        ticks: { font: { family: 'monospace', size: 11 }, callback: (v: any) => `$${Math.round(Number(v)).toLocaleString()}` }
      },
      x: {
        grid: { display: false },
        ticks: { maxTicksLimit: 6, font: { family: 'monospace', size: 10 } }
      }
    }
  };

  return (
    <div className="space-y-5">

      {/* ── PAGE HEADER ─────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-secondary/15 rounded-xl shrink-0">
            <ShieldCheck className="w-5 h-5 text-secondary" />
          </div>
          <div>
            <h2 className="font-bold text-base flex items-center gap-2">
              CPPI Strategy Backtester
              <span className="badge badge-sm badge-secondary font-semibold">Dynamic Quant Hedge</span>
            </h2>
            <p className="text-xs text-base-content/50 mt-0.5">
              Allocates via&nbsp;
              <code className="font-mono font-bold text-secondary bg-secondary/10 px-1 rounded">E&nbsp;=&nbsp;m&nbsp;×&nbsp;(V&nbsp;−&nbsp;Floor)</code>
              &nbsp;— protects capital while capturing stock upside
            </p>
          </div>
        </div>
      </div>

      {/* ── MAIN LAYOUT ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">

        {/* ── LEFT: PARAMETERS PANEL ──────────────────────────────── */}
        <div className="glass-card p-5 flex flex-col gap-4">

          <div className="flex items-center gap-2">
            <Sliders className="w-3.5 h-3.5 text-secondary" />
            <span className="text-xs font-bold uppercase tracking-widest text-base-content/40">Parameters</span>
          </div>

          {/* Target */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <ParamLabel>Ticker</ParamLabel>
              <input
                type="text"
                className="input input-sm input-bordered w-full font-mono font-bold uppercase tracking-widest"
                placeholder="AAPL"
                value={ticker}
                onChange={e => setTicker(e.target.value.toUpperCase())}
              />
            </div>
            <div>
              <ParamLabel>Capital (USD)</ParamLabel>
              <div className="relative">
                <span className="absolute left-2.5 top-[7px] text-xs text-base-content/40 font-mono">$</span>
                <input
                  type="number"
                  className="input input-sm input-bordered pl-5 w-full font-mono"
                  value={amount}
                  min={1000}
                  step={5000}
                  onChange={e => setAmount(Number(e.target.value))}
                />
              </div>
            </div>
          </div>

          <SectionDivider label="Protection" />

          {/* Floor */}
          <div>
            <div className="flex justify-between items-center mb-2">
              <ParamLabel>Capital Floor</ParamLabel>
              <span className="font-mono font-bold text-secondary text-sm">{floorPct}%</span>
            </div>
            <input
              type="range" className="range range-xs range-secondary w-full"
              min="50" max="98" value={floorPct}
              onChange={e => setFloorPct(Number(e.target.value))}
            />
            <div className="flex justify-between text-[10px] text-base-content/30 mt-1.5">
              <span>50% — Aggressive</span><span>98% — Ultra-Safe</span>
            </div>
          </div>

          {/* Multiplier */}
          <div>
            <div className="flex justify-between items-center mb-2">
              <ParamLabel>Risk Multiplier (m)</ParamLabel>
              <span className="font-mono font-bold text-secondary text-sm">
                {dynamicMultiplier ? 'Auto ↕' : `${multiplier}×`}
              </span>
            </div>
            <input
              type="range" className="range range-xs range-secondary w-full"
              min="1" max="8" step="0.5"
              disabled={dynamicMultiplier}
              value={multiplier}
              onChange={e => setMultiplier(Number(e.target.value))}
            />
            <div className="flex justify-between text-[10px] text-base-content/30 mt-1.5">
              <span>1× Conservative</span><span>8× Aggressive</span>
            </div>
          </div>

          <SectionDivider label="Backtest" />

          {/* Horizon + Rebalance */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <ParamLabel>Horizon</ParamLabel>
              <select className="select select-sm select-bordered w-full"
                value={durationYears} onChange={e => setDurationYears(Number(e.target.value))}>
                <option value={1}>1 Year</option>
                <option value={2}>2 Years</option>
                <option value={3}>3 Years</option>
                <option value={5}>5 Years</option>
                <option value={10}>10 Years</option>
              </select>
            </div>
            <div>
              <ParamLabel>Rebalance</ParamLabel>
              <select className="select select-sm select-bordered w-full"
                value={rebalanceFreq} onChange={e => setRebalanceFreq(e.target.value)}>
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
                <option value="monthly">Monthly</option>
                <option value="threshold">Threshold</option>
              </select>
            </div>
          </div>

          {rebalanceFreq === 'threshold' && (
            <div>
              <ParamLabel>Deviation Threshold (%)</ParamLabel>
              <input type="number" className="input input-sm input-bordered w-full font-mono"
                value={rebalanceThresholdPct} min={0.1} max={20} step={0.5}
                onChange={e => setRebalanceThresholdPct(Number(e.target.value))} />
            </div>
          )}

          <SectionDivider label="Rates & Costs" />

          {/* Risk-free + Fee */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <ParamLabel>Risk-Free Rate</ParamLabel>
              <div className="relative">
                <input type="number" className="input input-sm input-bordered w-full font-mono pr-6"
                  value={riskFreeRate} min={0} max={20} step={0.1}
                  onChange={e => setRiskFreeRate(Number(e.target.value))} />
                <span className="absolute right-2.5 top-[7px] text-xs text-base-content/35">%</span>
              </div>
            </div>
            <div>
              <ParamLabel>Trade Fee</ParamLabel>
              <div className="relative">
                <input type="number" className="input input-sm input-bordered w-full font-mono pr-6"
                  value={transactionFeePct} min={0} max={2} step={0.01}
                  onChange={e => setTransactionFeePct(Number(e.target.value))} />
                <span className="absolute right-2.5 top-[7px] text-xs text-base-content/35">%</span>
              </div>
            </div>
          </div>

          <SectionDivider label="Advanced" />

          {/* Toggles */}
          <div className="space-y-3">
            <label className="flex items-start gap-3 cursor-pointer">
              <input type="checkbox" className="checkbox checkbox-sm checkbox-secondary mt-0.5"
                checked={allowLeverage} onChange={e => setAllowLeverage(e.target.checked)} />
              <div>
                <div className="text-xs font-semibold">Margin Leverage</div>
                <div className="text-[11px] text-base-content/40">Allow up to 2× risky exposure</div>
              </div>
            </label>
            <label className="flex items-start gap-3 cursor-pointer">
              <input type="checkbox" className="checkbox checkbox-sm checkbox-secondary mt-0.5"
                checked={dynamicMultiplier} onChange={e => setDynamicMultiplier(e.target.checked)} />
              <div>
                <div className="text-xs font-semibold text-secondary">Dynamic Multiplier (D-CPPI)</div>
                <div className="text-[11px] text-base-content/40">Scale m with 20-day trailing vol</div>
              </div>
            </label>
          </div>

          {/* Run button */}
          <button
            className="btn btn-secondary w-full mt-auto gap-2"
            onClick={runSimulation}
            disabled={loading}
          >
            {loading
              ? <><span className="loading loading-spinner loading-sm" /> Running…</>
              : <><RefreshCw className="w-4 h-4" /> Run Simulation</>
            }
          </button>
        </div>

        {/* ── RIGHT: RESULTS AREA ──────────────────────────────────── */}
        <div className="xl:col-span-2 flex flex-col gap-5 min-w-0">

          {/* Error */}
          {error && (
            <div className="alert alert-error shadow-sm">
              <ShieldAlert className="w-4 h-4 shrink-0" />
              <div>
                <span className="font-bold text-sm">Backtest Error</span>
                <div className="text-xs mt-0.5 opacity-80">{error}</div>
              </div>
            </div>
          )}

          {/* Loading placeholder */}
          {loading && !result && (
            <div className="glass-card flex flex-col items-center justify-center gap-3 py-20 text-center">
              <span className="loading loading-ring loading-lg text-secondary" />
              <p className="text-sm text-base-content/40">Running CPPI backtest on <strong>{ticker}</strong>…</p>
            </div>
          )}

          {result && (
            <>
              {/* ── STAT CARDS ─────────────────────────────────────── */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                {/* Spot Price */}
                <div className="glass-card p-4">
                  <div className="text-[11px] font-semibold text-base-content/40 uppercase tracking-wide">Spot Price</div>
                  <div className="text-xl font-black font-mono mt-1.5 truncate">${result.currentPrice.toLocaleString()}</div>
                  <div className="text-[11px] text-base-content/30 mt-1 font-mono">{result.ticker}</div>
                </div>

                {/* Final Value */}
                <div className="glass-card p-4 border-l-2 border-l-secondary">
                  <div className="text-[11px] font-semibold text-secondary/70 uppercase tracking-wide">Final Value</div>
                  <div className="text-xl font-black font-mono mt-1.5 text-secondary truncate">
                    ${result.performance.cppi.finalValue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  </div>
                  <div className="text-[11px] text-base-content/30 mt-1 font-mono">
                    from ${result.investmentAmount.toLocaleString()}
                  </div>
                </div>

                {/* CPPI Return */}
                <div className="glass-card p-4">
                  <div className="text-[11px] font-semibold text-base-content/40 uppercase tracking-wide">Total Return</div>
                  <div className={`text-xl font-black font-mono mt-1.5 truncate ${result.performance.cppi.cumulativeReturn >= 0 ? 'text-success' : 'text-error'}`}>
                    {result.performance.cppi.cumulativeReturn >= 0 ? '+' : ''}{result.performance.cppi.cumulativeReturn.toFixed(1)}%
                  </div>
                  <div className="text-[11px] text-base-content/30 mt-1 font-mono">
                    B&amp;H {result.performance.buyAndHold.cumulativeReturn >= 0 ? '+' : ''}{result.performance.buyAndHold.cumulativeReturn.toFixed(1)}%
                  </div>
                </div>

                {/* Max Drawdown */}
                <div className="glass-card p-4">
                  <div className="text-[11px] font-semibold text-base-content/40 uppercase tracking-wide">Max Drawdown</div>
                  <div className="text-xl font-black font-mono mt-1.5 text-warning truncate">
                    {result.performance.cppi.maxDrawdown.toFixed(1)}%
                  </div>
                  <div className="text-[11px] text-error/60 mt-1 font-mono">
                    B&amp;H {result.performance.buyAndHold.maxDrawdown.toFixed(1)}%
                  </div>
                </div>
              </div>

              {/* ── MAIN CHART ──────────────────────────────────────── */}
              <div className="glass-card p-5">
                {/* Tab bar + legend */}
                <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
                  <div className="flex gap-1.5">
                    {([
                      { id: 'chart', icon: <Calendar className="w-3.5 h-3.5" />, label: 'Portfolio Path' },
                      { id: 'allocation', icon: <BarChart2 className="w-3.5 h-3.5" />, label: 'Exposure Mix' },
                      { id: 'trades', icon: <RefreshCw className="w-3.5 h-3.5" />, label: `Trades (${result.riskAnalysis.totalTrades})` },
                    ] as const).map(tab => (
                      <button
                        key={tab.id}
                        className={`btn btn-xs gap-1.5 ${activeTab === tab.id ? 'btn-secondary' : 'btn-ghost opacity-50 hover:opacity-100'}`}
                        onClick={() => setActiveTab(tab.id)}
                      >
                        {tab.icon}{tab.label}
                      </button>
                    ))}
                  </div>
                  {activeTab !== 'trades' && (
                    <div className="flex items-center gap-4 text-[11px] text-base-content/50">
                      <span className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-secondary inline-block" />CPPI
                      </span>
                      <span className="flex items-center gap-1.5">
                        <span className="w-3 h-px border-t border-dashed border-base-content/40 inline-block" />B&amp;H
                      </span>
                      <span className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-error inline-block" />Floor
                      </span>
                    </div>
                  )}
                </div>

                {/* Charts */}
                {activeTab === 'chart' && (
                  <div className="h-80 w-full">
                    <Line
                      data={{
                        labels: result.charts.dates,
                        datasets: [
                          {
                            label: 'CPPI Portfolio ($)',
                            data: result.charts.cppiValue,
                            borderColor: 'rgba(236,72,153,1)',
                            backgroundColor: 'rgba(236,72,153,0.05)',
                            borderWidth: 2, fill: true, tension: 0.03, pointRadius: 0, pointHoverRadius: 4,
                          },
                          {
                            label: 'Buy & Hold ($)',
                            data: result.charts.buyAndHoldValue,
                            borderColor: 'rgba(156,163,175,0.5)',
                            borderWidth: 1.5, borderDash: [5, 4], fill: false, pointRadius: 0,
                          },
                          {
                            label: 'Floor ($)',
                            data: result.charts.floorValue,
                            borderColor: 'rgba(248,113,113,0.8)',
                            borderWidth: 1.5, fill: false, pointRadius: 0,
                          },
                        ],
                      }}
                      options={{
                        ...chartBaseOptions,
                        plugins: {
                          legend: { display: false },
                          tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: $${Math.round(ctx.parsed.y ?? 0).toLocaleString()}` } },
                        },
                      }}
                    />
                  </div>
                )}

                {activeTab === 'allocation' && (
                  <div className="h-80 w-full">
                    <Line
                      data={{
                        labels: result.charts.dates,
                        datasets: [
                          {
                            label: 'Stock Exposure ($)',
                            data: result.charts.riskyExposure,
                            borderColor: 'rgba(52,211,153,1)',
                            backgroundColor: 'rgba(52,211,153,0.1)',
                            borderWidth: 1.5, fill: 'origin', pointRadius: 0, tension: 0.03,
                          },
                          {
                            label: 'Cash Reserve ($)',
                            data: result.charts.cashExposure,
                            borderColor: 'rgba(56,189,248,1)',
                            backgroundColor: 'rgba(56,189,248,0.1)',
                            borderWidth: 1.5, fill: '-1', pointRadius: 0, tension: 0.03,
                          },
                        ],
                      }}
                      options={{
                        ...chartBaseOptions,
                        plugins: {
                          legend: { display: true, position: 'top', labels: { usePointStyle: true, boxWidth: 6, font: { size: 11 } } },
                          tooltip: { callbacks: { label: ctx => ` ${ctx.dataset.label}: $${Math.round(ctx.parsed.y ?? 0).toLocaleString()}` } },
                        },
                        scales: { ...chartBaseOptions.scales, y: { ...chartBaseOptions.scales.y, stacked: true } },
                      }}
                    />
                  </div>
                )}

                {activeTab === 'trades' && (
                  <div className="overflow-auto max-h-80 rounded-lg border border-white/[0.04]">
                    {result.trades.length === 0 ? (
                      <div className="py-14 text-center text-sm text-base-content/30">
                        No rebalance trades triggered
                      </div>
                    ) : (
                      <table className="table table-xs w-full font-mono min-w-[640px]">
                        <thead className="sticky top-0 bg-base-200/80 backdrop-blur-sm">
                          <tr className="text-base-content/50">
                            <th className="py-2.5 text-left">Date</th>
                            <th>Action</th>
                            <th className="text-right">Price</th>
                            <th className="text-right">Shares</th>
                            <th className="text-right">Value</th>
                            <th className="text-right">Fee</th>
                            <th className="text-right">Cash</th>
                            <th className="text-right">Portfolio</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.trades.map((t, idx) => (
                            <tr key={idx} className="hover:bg-white/[0.02] border-t border-white/[0.03]">
                              <td className="py-1.5 text-base-content/60">{t.date}</td>
                              <td>
                                <span className={`badge badge-xs font-semibold ${
                                  t.action.startsWith('BUY') ? 'badge-success' :
                                  t.action.startsWith('SELL') ? 'badge-warning' : 'badge-error'
                                }`}>{t.action.split(' ')[0]}</span>
                              </td>
                              <td className="text-right">${t.price.toFixed(2)}</td>
                              <td className="text-right">{t.shares.toFixed(2)}</td>
                              <td className="text-right">${t.value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                              <td className="text-right text-error/70">${t.fee.toFixed(2)}</td>
                              <td className={`text-right ${t.cash_balance < 0 ? 'text-error' : 'text-info/80'}`}>
                                ${t.cash_balance.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                              </td>
                              <td className="text-right font-semibold">
                                ${t.portfolio_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>

              {/* ── METRICS + RISK ────────────────────────────────── */}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">

                {/* Performance metrics table */}
                <div className="glass-card p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <BarChart2 className="w-4 h-4 text-secondary shrink-0" />
                    <span className="font-bold text-sm">Performance Metrics</span>
                    <span className="text-[10px] text-base-content/30 ml-auto font-mono uppercase">CPPI vs B&amp;H</span>
                  </div>
                  <table className="w-full">
                    <thead>
                      <tr className="text-[10px] text-base-content/30 uppercase tracking-wide border-b border-white/[0.06]">
                        <th className="text-left pb-2 font-medium">Metric</th>
                        <th className="text-right pb-2 font-medium text-secondary">CPPI</th>
                        <th className="text-right pb-2 font-medium">B&amp;H</th>
                      </tr>
                    </thead>
                    <tbody>
                      <MetricRow label="Cumulative Return"
                        cppi={`${result.performance.cppi.cumulativeReturn >= 0 ? '+' : ''}${result.performance.cppi.cumulativeReturn.toFixed(1)}%`}
                        bh={`${result.performance.buyAndHold.cumulativeReturn >= 0 ? '+' : ''}${result.performance.buyAndHold.cumulativeReturn.toFixed(1)}%`}
                        cppiClass={result.performance.cppi.cumulativeReturn >= 0 ? 'text-secondary' : 'text-error'}
                      />
                      <MetricRow label="CAGR"
                        cppi={`${result.performance.cppi.cagr >= 0 ? '+' : ''}${result.performance.cppi.cagr.toFixed(1)}%`}
                        bh={`${result.performance.buyAndHold.cagr >= 0 ? '+' : ''}${result.performance.buyAndHold.cagr.toFixed(1)}%`}
                        cppiClass="text-secondary"
                      />
                      <MetricRow label="Annual Volatility"
                        cppi={`${result.performance.cppi.volatility.toFixed(1)}%`}
                        bh={`${result.performance.buyAndHold.volatility.toFixed(1)}%`}
                        cppiClass="text-base-content/70"
                      />
                      <MetricRow label="Sharpe Ratio"
                        cppi={result.performance.cppi.sharpe.toFixed(2)}
                        bh={result.performance.buyAndHold.sharpe.toFixed(2)}
                        cppiClass={result.performance.cppi.sharpe >= result.performance.buyAndHold.sharpe ? 'text-success' : 'text-base-content/70'}
                      />
                      <MetricRow label="Sortino Ratio"
                        cppi={result.performance.cppi.sortino.toFixed(2)}
                        bh={result.performance.buyAndHold.sortino.toFixed(2)}
                        cppiClass={result.performance.cppi.sortino >= result.performance.buyAndHold.sortino ? 'text-success' : 'text-base-content/70'}
                      />
                      <MetricRow label="Calmar Ratio"
                        cppi={result.performance.cppi.calmar.toFixed(2)}
                        bh={result.performance.buyAndHold.calmar.toFixed(2)}
                        cppiClass={result.performance.cppi.calmar >= result.performance.buyAndHold.calmar ? 'text-success' : 'text-base-content/70'}
                      />
                      <MetricRow label="Max Drawdown"
                        cppi={`${result.performance.cppi.maxDrawdown.toFixed(1)}%`}
                        bh={`${result.performance.buyAndHold.maxDrawdown.toFixed(1)}%`}
                        cppiClass="text-success"
                      />
                    </tbody>
                  </table>
                </div>

                {/* Risk analysis */}
                <div className="glass-card p-5 flex flex-col gap-4">
                  <div className="flex items-center gap-2">
                    <ShieldAlert className="w-4 h-4 text-secondary shrink-0" />
                    <span className="font-bold text-sm">Risk &amp; Protection</span>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    {/* Cash-lock */}
                    <div className={`rounded-xl p-3.5 border ${result.riskAnalysis.cashLocked ? 'border-error/25 bg-error/5' : 'border-success/20 bg-success/5'}`}>
                      <div className="flex items-center gap-1.5 mb-1">
                        {result.riskAnalysis.cashLocked
                          ? <Lock className="w-3 h-3 text-error" />
                          : <Unlock className="w-3 h-3 text-success" />}
                        <span className="text-[10px] font-bold uppercase tracking-wide text-base-content/40">Cash-Lock</span>
                      </div>
                      <div className={`text-sm font-bold ${result.riskAnalysis.cashLocked ? 'text-error' : 'text-success'}`}>
                        {result.riskAnalysis.cashLocked ? 'Locked' : 'Active'}
                      </div>
                      <div className="text-[10px] text-base-content/35 mt-0.5">
                        {result.riskAnalysis.cashLocked ? result.riskAnalysis.cashLockDate : 'Cushion stayed positive'}
                      </div>
                    </div>

                    {/* Floor breach */}
                    <div className={`rounded-xl p-3.5 border ${result.riskAnalysis.floorBreached ? 'border-error/25 bg-error/5' : 'border-success/20 bg-success/5'}`}>
                      <div className="text-[10px] font-bold uppercase tracking-wide text-base-content/40 mb-1">Floor Breach</div>
                      <div className={`text-sm font-bold ${result.riskAnalysis.floorBreached ? 'text-error' : 'text-success'}`}>
                        {result.riskAnalysis.floorBreached ? `−${result.riskAnalysis.worstFloorBreachPct}%` : 'Protected'}
                      </div>
                      <div className="text-[10px] text-base-content/35 mt-0.5">
                        {result.riskAnalysis.floorBreached ? 'Gap-down breached floor' : 'No floor violation'}
                      </div>
                    </div>

                    {/* Volatility */}
                    <div className="rounded-xl p-3.5 border border-white/[0.05] bg-base-200/30">
                      <div className="text-[10px] font-bold uppercase tracking-wide text-base-content/40 mb-1">Asset Vol (ann.)</div>
                      <div className="text-sm font-bold font-mono">{result.riskAnalysis.currentAssetVol}%</div>
                      <div className="text-[10px] text-base-content/35 mt-0.5">{result.riskAnalysis.averageAssetVol}% period avg</div>
                    </div>

                    {/* Fee drag */}
                    <div className="rounded-xl p-3.5 border border-white/[0.05] bg-base-200/30">
                      <div className="text-[10px] font-bold uppercase tracking-wide text-base-content/40 mb-1">Fee Drag</div>
                      <div className="text-sm font-bold font-mono text-error">
                        −${result.riskAnalysis.totalFeesPaid.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                      </div>
                      <div className="text-[10px] text-base-content/35 mt-0.5">{result.riskAnalysis.totalTrades} rebalance trades</div>
                    </div>
                  </div>

                  {/* Multiplier info */}
                  <div className="rounded-xl p-3.5 bg-secondary/5 border border-secondary/10">
                    <div className="flex justify-between items-center text-xs mb-1.5">
                      <span className="text-base-content/50 font-medium">Latest Multiplier</span>
                      <span className="font-mono font-bold text-secondary">{result.riskAnalysis.latestMultiplier}×</span>
                    </div>
                    <div className="flex justify-between items-center text-xs">
                      <span className="text-base-content/50 font-medium">Peak Leverage</span>
                      <span className="font-mono font-bold text-secondary">{result.riskAnalysis.maxLeverageReached}×</span>
                    </div>
                    <div className="text-[10px] text-base-content/30 mt-2">
                      {result.dynamicMultiplier ? 'D-CPPI: multiplier scales with 20-day vol' : 'Fixed multiplier — manual selection'}
                    </div>
                  </div>
                </div>
              </div>

              {/* ── GAP RISK / STRESS TEST ───────────────────────── */}
              <div className="glass-card p-5 border border-warning/10">
                <div className="flex items-center gap-2 mb-4">
                  <AlertTriangle className="w-4 h-4 text-warning shrink-0" />
                  <span className="font-bold text-sm">Gap-Risk Stress Test</span>
                  <div className="ml-auto flex items-center gap-3 text-[11px] text-base-content/40">
                    <span>Critical drop threshold:</span>
                    <span className="font-mono font-bold text-warning">−{result.riskAnalysis.gapBreachThresholdPct.toFixed(1)}%</span>
                    <span className="opacity-30">|</span>
                    <span>Daily breach prob:</span>
                    <span className={`font-mono font-bold ${result.riskAnalysis.gapBreachProbabilityPct > 1 ? 'text-error animate-pulse' : 'text-success'}`}>
                      {result.riskAnalysis.gapBreachProbabilityPct.toFixed(4)}%
                    </span>
                  </div>
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                  <div className="text-xs text-base-content/50 leading-relaxed">
                    A gap-down larger than <code className="font-mono text-warning bg-warning/10 px-1 rounded">1&nbsp;/&nbsp;m</code> breaches the floor before
                    the engine can rebalance. The slider below estimates the immediate portfolio
                    impact based on your current stock exposure weight.
                  </div>

                  <div className="space-y-4">
                    <div className="flex justify-between items-center text-xs">
                      <span className="font-semibold text-base-content/60">Simulate asset shock</span>
                      <span className="font-mono font-bold text-error text-base">{simulatedDrop}%</span>
                    </div>
                    <input
                      type="range" className="range range-xs range-error w-full"
                      min="-50" max="-1" step="1"
                      value={simulatedDrop}
                      onChange={e => setSimulatedDrop(Number(e.target.value))}
                    />
                    <div className="flex items-center justify-between pt-2 border-t border-white/[0.05]">
                      <div>
                        <div className="text-[10px] text-base-content/40 uppercase tracking-wide">Simulated Value</div>
                        <div className={`font-mono font-bold text-base mt-0.5 ${stressResult.breach ? 'text-error' : 'text-success'}`}>
                          {stressResult.finalCppiValue.toFixed(1)}% of capital
                        </div>
                      </div>
                      <span className={`badge font-bold text-xs px-3 py-2 ${stressResult.breach ? 'badge-error' : 'badge-success'}`}>
                        {stressResult.breach ? `BREACH −${stressResult.shortfallPct.toFixed(1)}%` : 'FLOOR HELD'}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* ── HOW TO REPLICATE ─────────────────────────────── */}
              <div className="glass-card p-5 border border-success/10">
                <div className="flex items-center gap-2 mb-4">
                  <CheckCircle2 className="w-4 h-4 text-success shrink-0" />
                  <span className="font-bold text-sm text-success">How to Replicate Manually</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
                  {[
                    {
                      n: '1', title: 'Set Parameters',
                      body: `Choose your capital, floor, and multiplier. With $100K and a 90% floor, your initial cushion is $10K.`
                    },
                    {
                      n: '2', title: 'Size the Stock Position',
                      body: `Stock target = Cushion × m. E.g. $10K × 4 = $40K in stock, remainder in cash or money-market.`
                    },
                    {
                      n: '3', title: 'Rebalance on Trigger',
                      body: `Weekly or when allocation drifts >2% from target. Sell when markets fall, buy more when they rise.`
                    },
                  ].map(step => (
                    <div key={step.n} className="flex gap-3">
                      <span className="w-5 h-5 rounded-full bg-success/20 text-success text-[11px] font-black flex items-center justify-center shrink-0 mt-0.5">
                        {step.n}
                      </span>
                      <div>
                        <div className="font-semibold text-xs text-success mb-1">{step.title}</div>
                        <p className="text-xs text-base-content/40 leading-relaxed">{step.body}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
