import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Calculator, Loader2, AlertTriangle, ChevronDown, ChevronUp,
  TrendingUp, TrendingDown, DollarSign, Info, RefreshCw, Lightbulb,
  Gauge, Percent, BarChart3, Sparkles, BookOpen,
} from 'lucide-react';
import { DcfLearnModal } from './DcfLearn';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  type ChartOptions,
} from 'chart.js';
import { Bar } from 'react-chartjs-2';
import { fetchDCFAnalysis, computeDCFScenario, type DcfScenarioParams } from '../api';
import type { DCFAnalysisData, DCFValuation, PEGAnalysis } from '../types';

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, Title, Tooltip, Legend);

interface DCFAnalysisProps {
  ticker: string;
  onUseInDebate?: (params: DcfScenarioParams) => void;
}

function fmtLargeNum(val: number): string {
  const abs = Math.abs(val);
  if (abs >= 1e12) return `$${(val / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(val / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  return `$${val.toLocaleString()}`;
}

/* ──────────────────────── PEG / PEGY Section ──────────────────────── */

function pegColor(val: number | null): string {
  if (val === null) return 'text-base-content/50';
  if (val < 0) return 'text-base-content/40';
  if (val < 1) return 'text-success';
  if (val < 1.5) return 'text-warning';
  return 'text-error';
}

function pegBadge(val: number | null): { label: string; cls: string } {
  if (val === null) return { label: 'N/A', cls: 'badge-ghost' };
  if (val < 0) return { label: 'Negative', cls: 'badge-ghost' };
  if (val < 1) return { label: 'Undervalued', cls: 'badge-success' };
  if (val < 1.5) return { label: 'Fair Value', cls: 'badge-warning' };
  return { label: 'Overvalued', cls: 'badge-error' };
}

/** Visual gauge bar showing where PEG/PEGY sits on a 0→3 scale. */
function PEGGauge({ value, label }: { value: number | null; label: string }) {
  const maxScale = 3;
  const clamped = value !== null ? Math.max(0, Math.min(value, maxScale)) : 0;
  const pct = (clamped / maxScale) * 100;
  const badge = pegBadge(value);

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs font-semibold text-base-content/60">{label}</span>
        <span className={`text-lg font-bold ${pegColor(value)}`}>
          {value !== null ? value.toFixed(2) : 'N/A'}
        </span>
      </div>
      <div className="w-full h-3 bg-base-100 rounded-full overflow-hidden relative">
        {/* Zones: green (<1), yellow (1-1.5), red (>1.5) */}
        <div className="absolute inset-0 flex">
          <div className="h-full bg-success/20" style={{ width: '33.3%' }} />
          <div className="h-full bg-warning/20" style={{ width: '16.7%' }} />
          <div className="h-full bg-error/20" style={{ width: '50%' }} />
        </div>
        {/* Marker */}
        {value !== null && value >= 0 && (
          <div
            className="absolute top-0 h-full w-1 bg-base-content rounded-full transition-all duration-500"
            style={{ left: `${pct}%` }}
          />
        )}
      </div>
      <div className="flex justify-between text-[10px] text-base-content/30 mt-0.5">
        <span>0</span>
        <span>1.0</span>
        <span>2.0</span>
        <span>3.0+</span>
      </div>
      <div className="mt-1">
        <span className={`badge badge-xs ${badge.cls}`}>{badge.label}</span>
      </div>
    </div>
  );
}

function PEGSection({ peg }: { peg: PEGAnalysis }) {
  const [showDetails, setShowDetails] = useState(false);
  const hasPEG = peg.primary_peg !== null || (peg.peg_variants && peg.peg_variants.length > 0);
  const hasPEGY = peg.pegy !== null;

  if (!hasPEG && !hasPEGY) {
    return (
      <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
        <h4 className="text-sm font-semibold text-base-content/70 flex items-center gap-2">
          <Gauge className="w-4 h-4 text-info" />
          PEG & PEGY Ratios
        </h4>
        <p className="text-sm text-base-content/40 mt-2">
          Insufficient data to compute PEG/PEGY ratios — requires positive P/E and earnings growth.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-bold text-sm text-base-content flex items-center gap-2">
          <Gauge className="w-5 h-5 text-info" />
          PE &amp; PEGY Ratios
        </h2>
        {peg.yfinance_peg !== null && (
          <span className="text-xs text-base-content/30">
            Yahoo Finance PEG: {peg.yfinance_peg}
          </span>
        )}
      </div>

      {/* Caveat — naive forward-vs-trailing growth inflates a simple PEG */}
      {peg.eps_growth_caveat && (
        <div className="flex items-start gap-2 text-[11px] text-base-content/60 bg-warning/10 border border-warning/20 rounded-lg p-2.5 leading-relaxed">
          <Info className="w-3.5 h-3.5 text-warning shrink-0 mt-0.5" />
          <span>
            The headline PEG uses a long-term growth estimate (consistent with Morningstar/Yahoo). A naive
            PEG that divides P/E by the raw <span className="font-semibold">forward-vs-trailing EPS growth</span> reads
            far cheaper, but that growth is overstated — trailing GAAP EPS is depressed by one-time items vs the
            adjusted forward estimate. See the alternate methods below.
          </span>
        </div>
      )}

      {/* Main gauges */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        <div className="bg-base-200/30 rounded-xl p-4 border border-white/[0.03]">
          <PEGGauge value={peg.primary_peg} label="PEG Ratio" />
          {peg.primary_peg_source && (
            <p className="text-[10px] text-base-content/30 mt-1">{peg.primary_peg_source}</p>
          )}
          {peg.peg_variants.length > 0 && (
            <p className="text-xs text-base-content/50 mt-2">
              {peg.peg_variants[0].interpretation}
            </p>
          )}
        </div>
        <div className="bg-base-200/30 rounded-xl p-4 border border-white/[0.03]">
          <PEGGauge value={peg.pegy} label="PEGY Ratio (Dividend-Adjusted)" />
          {peg.pegy_details && (
            <>
              <p className="text-[10px] text-base-content/30 mt-1">
                P/E {peg.pegy_details.pe_used} ÷ (Growth {peg.pegy_details.growth_used}% + Yield {peg.pegy_details.dividend_yield_pct}%)
              </p>
              <p className="text-xs text-base-content/50 mt-2">
                {peg.pegy_details.interpretation}
              </p>
            </>
          )}
          {!hasPEGY && (
            <p className="text-xs text-base-content/40 mt-2">
              No dividend yield — PEGY equals PEG for non-dividend stocks.
            </p>
          )}
        </div>
      </div>

      {/* Key inputs strip */}
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
        <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
          <div className="text-[10px] text-base-content/40">Trailing P/E</div>
          <div className="text-sm font-bold">{peg.trailing_pe?.toFixed(1) ?? 'N/A'}</div>
        </div>
        <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
          <div className="text-[10px] text-base-content/40">Forward P/E</div>
          <div className="text-sm font-bold">{peg.forward_pe?.toFixed(1) ?? 'N/A'}</div>
        </div>
        <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
          <div className="text-[10px] text-base-content/40">Trailing EPS</div>
          <div className="text-sm font-bold">{peg.trailing_eps !== null ? `$${peg.trailing_eps.toFixed(2)}` : 'N/A'}</div>
        </div>
        <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
          <div className="text-[10px] text-base-content/40">Forward EPS</div>
          <div className="text-sm font-bold">{peg.forward_eps !== null ? `$${peg.forward_eps.toFixed(2)}` : 'N/A'}</div>
        </div>
        <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
          <div className="text-[10px] text-base-content/40">EPS Growth</div>
          <div className={`text-sm font-bold ${(peg.eps_growth_rate ?? 0) > 0 ? 'text-success' : (peg.eps_growth_rate ?? 0) < 0 ? 'text-error' : ''}`}>
            {peg.eps_growth_rate !== null ? `${peg.eps_growth_rate > 0 ? '+' : ''}${peg.eps_growth_rate.toFixed(1)}%` : 'N/A'}
          </div>
        </div>
        <div className="bg-base-200/30 rounded-xl p-2 text-center border border-white/[0.03]">
          <div className="text-[10px] text-base-content/40">Div Yield</div>
          <div className="text-sm font-bold">{peg.dividend_yield_pct > 0 ? `${peg.dividend_yield_pct.toFixed(2)}%` : '0%'}</div>
        </div>
      </div>

      {/* Expandable details for all PEG variants */}
      {peg.peg_variants.length > 1 && (
        <div>
          <button
            className="flex items-center gap-1.5 text-xs text-base-content/50 hover:text-base-content/70 transition-colors"
            onClick={() => setShowDetails(!showDetails)}
          >
            <BarChart3 className="w-3.5 h-3.5" />
            {showDetails ? 'Hide' : 'Show'} PEG calculation methods ({peg.peg_variants.length})
            {showDetails ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {showDetails && (
            <div className="mt-2 space-y-2">
              {peg.peg_variants.map((v, i) => (
                <div key={i} className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03] flex items-center justify-between">
                  <div>
                    <div className="text-xs font-semibold text-base-content/70">{v.method}</div>
                    <div className="text-[10px] text-base-content/40">
                      P/E {v.pe_used}x ÷ {v.growth_used}% growth ({v.growth_source})
                    </div>
                    <div className="text-xs text-base-content/50 mt-0.5">{v.interpretation}</div>
                  </div>
                  <div className={`text-lg font-bold ${pegColor(v.peg)}`}>
                    {v.peg.toFixed(2)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Quick reference */}
      <div className="flex flex-wrap gap-3 text-[10px] text-base-content/30">
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-success inline-block" /> PEG &lt; 1.0 = Undervalued
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-warning inline-block" /> PEG 1.0–1.5 = Fair
        </span>
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-error inline-block" /> PEG &gt; 1.5 = Overvalued
        </span>
        <span>|</span>
        <span>PEG = P/E ÷ Growth Rate</span>
        <span>|</span>
        <span>PEGY = P/E ÷ (Growth + Dividend Yield)</span>
      </div>
    </div>
  );
}

/* ──────────────────────── Main DCF Component ──────────────────────── */

export function DCFAnalysis({ ticker, onUseInDebate }: DCFAnalysisProps) {
  const [data, setData] = useState<DCFAnalysisData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showReasoning, setShowReasoning] = useState(false);
  const [showLearn, setShowLearn] = useState(false);

  // Editable two-stage scenario inputs (fcfGrowth = stage-1 growth, projYears = CAP years)
  const [fcfGrowth, setFcfGrowth] = useState(10);
  const [discountRate, setDiscountRate] = useState(10);
  const [terminalGrowth, setTerminalGrowth] = useState(2.5);
  const [projYears, setProjYears] = useState(5);
  const [startingFcf, setStartingFcf] = useState(0);
  const [sharesOut, setSharesOut] = useState(0);
  const [netDebt, setNetDebt] = useState(0);
  const [exitMultiple, setExitMultiple] = useState(16);

  // Scenario result
  const [scenarioVal, setScenarioVal] = useState<DCFValuation | null>(null);
  const [scenarioLoading, setScenarioLoading] = useState(false);
  const [scenarioDirty, setScenarioDirty] = useState(false);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await fetchDCFAnalysis(ticker);
      setData(result);
      // Initialize inputs from the two-stage headline the algorithm found.
      const v = result.valuation;
      setFcfGrowth(v.stage1_growth ?? result.defaults.fcf_growth_rate);
      setDiscountRate(v.wacc ?? result.defaults.discount_rate);
      setTerminalGrowth(v.terminal_growth ?? result.defaults.terminal_growth_rate);
      setProjYears(v.cap_years ?? result.defaults.projection_years);
      setStartingFcf(v.base_fcf ?? result.defaults.starting_fcf);
      setExitMultiple(v.exit_multiple ?? 16);
      setSharesOut(result.shares_outstanding);
      setNetDebt(result.defaults.net_debt);
      setScenarioVal(result.valuation);
      setScenarioDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load DCF');
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => { load(); }, [load]);

  // Mark dirty when inputs change
  const handleInputChange = (setter: (v: number) => void) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setter(Number(e.target.value));
    setScenarioDirty(true);
  };

  const buildParams = (): DcfScenarioParams => ({
    base_fcf: startingFcf,
    stage1_growth: fcfGrowth,
    cap_years: projYears,
    discount_rate: discountRate,
    terminal_growth: terminalGrowth,
    exit_multiple: exitMultiple,
    shares_outstanding: sharesOut,
    net_debt: netDebt,
    current_price: data?.current_price ?? 0,
  });

  const recalculate = async () => {
    if (!data) return;
    try {
      setScenarioLoading(true);
      const result = await computeDCFScenario(ticker, buildParams());
      setScenarioVal(result);
      setScenarioDirty(false);
    } catch {
      // silent
    } finally {
      setScenarioLoading(false);
    }
  };

  const resetDefaults = () => {
    if (!data) return;
    const v = data.valuation;
    setFcfGrowth(v.stage1_growth ?? data.defaults.fcf_growth_rate);
    setDiscountRate(v.wacc ?? data.defaults.discount_rate);
    setTerminalGrowth(v.terminal_growth ?? data.defaults.terminal_growth_rate);
    setProjYears(v.cap_years ?? data.defaults.projection_years);
    setStartingFcf(v.base_fcf ?? data.defaults.starting_fcf);
    setExitMultiple(v.exit_multiple ?? 16);
    setSharesOut(data.shares_outstanding);
    setNetDebt(data.defaults.net_debt);
    setScenarioVal(data.valuation);
    setScenarioDirty(false);
  };

  // Fair value vs current price
  const fairValue = scenarioVal?.fair_value_per_share ?? 0;
  const currentPrice = data?.current_price ?? 0;
  const upside = currentPrice > 0 ? ((fairValue - currentPrice) / currentPrice) * 100 : 0;
  const isUndervalued = fairValue > currentPrice;

  // Chart data for projected FCFs
  const chartData = useMemo(() => {
    if (!scenarioVal || !scenarioVal.projected_fcfs.length) return null;

    const labels = scenarioVal.projected_fcfs.map((_, i) => `Year ${i + 1}`);

    return {
      labels,
      datasets: [
        {
          label: 'Projected FCF',
          data: scenarioVal.projected_fcfs.map((v) => v / 1e9),
          backgroundColor: 'rgba(99, 102, 241, 0.7)',
          borderColor: 'rgb(99, 102, 241)',
          borderWidth: 1,
          borderRadius: 4,
        },
        {
          label: 'Present Value',
          data: scenarioVal.present_values.map((v) => v / 1e9),
          backgroundColor: 'rgba(34, 197, 94, 0.5)',
          borderColor: 'rgb(34, 197, 94)',
          borderWidth: 1,
          borderRadius: 4,
        },
      ],
    };
  }, [scenarioVal]);

  const chartOptions: ChartOptions<'bar'> = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top',
        labels: { color: '#999', boxWidth: 12, usePointStyle: true, padding: 15 },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => `${ctx.dataset.label}: $${(ctx.parsed.y ?? 0).toFixed(2)}B`,
        },
      },
    },
    scales: {
      x: { ticks: { color: '#999' }, grid: { display: false } },
      y: {
        ticks: { color: '#999', callback: (v) => `$${Number(v).toFixed(1)}B` },
        grid: { color: 'rgba(128,128,128,0.15)' },
      },
    },
  };

  // Historical FCF mini-chart data
  const fcfHistoryData = useMemo(() => {
    if (!data?.fcf_history.length) return null;
    const items = [...data.fcf_history].reverse();
    return {
      labels: items.map((h) => h.year),
      datasets: [
        {
          label: 'FCF',
          data: items.map((h) => (h.value ?? 0) / 1e9),
          backgroundColor: items.map((h) =>
            (h.value ?? 0) >= 0 ? 'rgba(34, 197, 94, 0.6)' : 'rgba(239, 68, 68, 0.6)'
          ),
          borderRadius: 4,
        },
      ],
    };
  }, [data]);

  return (
    <>
      {/* Standalone PE & PEGY valuation card — sits above the DCF model */}
      {data && !loading && data.peg_analysis && (
        <div className="glass-card">
          <div className="p-5">
            <PEGSection peg={data.peg_analysis} />
          </div>
        </div>
      )}

      <div className="glass-card">
      <div className="p-5">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-sm flex items-center gap-2">
            <Calculator className="w-5 h-5 text-primary" />
            DCF Fair Value Analysis
            <button className="btn btn-ghost btn-xs gap-1 text-primary font-normal" onClick={() => setShowLearn(true)}
              title="How this DCF works — CAP, terminal value, the growth glide-path, with examples">
              <BookOpen className="w-3 h-3" /> Learn
            </button>
          </h2>
          {data && (
            <button className="btn btn-ghost btn-xs gap-1" onClick={load}>
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          )}
        </div>
        <DcfLearnModal open={showLearn} onClose={() => setShowLearn(false)} />

        {/* Loading */}
        {loading && (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-8 h-8 animate-spin text-primary" />
            <span className="ml-3 text-base-content/60">Calculating DCF model...</span>
          </div>
        )}

        {/* Error */}
        {error && !loading && (
          <div className="alert alert-error">
            <AlertTriangle className="w-5 h-5" />
            <span>{error}</span>
          </div>
        )}

        {data && !loading && (
          <div className="space-y-4 mt-2">
            {/* Fair Value Result Banner */}
            <div className={`flex flex-col sm:flex-row items-center justify-between gap-4 p-4 rounded-xl ${isUndervalued ? 'bg-success/10 border border-success/30' : 'bg-error/10 border border-error/30'}`}>
              <div className="text-center sm:text-left">
                <div className="text-sm text-base-content/60 mb-1">DCF Fair Value</div>
                <div className="text-3xl font-bold">
                  ${fairValue.toFixed(2)}
                </div>
              </div>
              <div className="text-center">
                <div className="text-sm text-base-content/60 mb-1">Current Price</div>
                <div className="text-2xl font-semibold">${currentPrice.toFixed(2)}</div>
              </div>
              <div className="text-center sm:text-right">
                <div className="text-sm text-base-content/60 mb-1">
                  {isUndervalued ? 'Upside' : 'Downside'}
                </div>
                <div className={`flex items-center gap-1 text-2xl font-bold ${isUndervalued ? 'text-success' : 'text-error'}`}>
                  {isUndervalued ? <TrendingUp className="w-6 h-6" /> : <TrendingDown className="w-6 h-6" />}
                  {upside >= 0 ? '+' : ''}{upside.toFixed(1)}%
                </div>
                <div className={`badge badge-sm mt-1 ${isUndervalued ? 'badge-success' : 'badge-error'}`}>
                  {isUndervalued ? 'Potentially Undervalued' : 'Potentially Overvalued'}
                </div>
              </div>
            </div>

            {/* Live two-stage headline: range + reverse-DCF + how-it's-calculated + flags */}
            {scenarioVal && (scenarioVal.fair_value_low != null || scenarioVal.market_implied_growth != null) && (
              <div className="rounded-xl bg-base-200/40 border border-white/[0.03] p-3">
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
                  {scenarioVal.fair_value_low != null && (
                    <div>
                      <span className="text-base-content/50">Fair-value range </span>
                      <span className="font-semibold">${scenarioVal.fair_value_low.toFixed(0)}–${scenarioVal.fair_value_high?.toFixed(0)}</span>
                      <span className="text-base-content/40"> (bear/bull)</span>
                    </div>
                  )}
                  {scenarioVal.market_implied_growth != null && (
                    <div>
                      <span className="badge badge-xs badge-primary mr-1">reverse-DCF</span>
                      <span className="text-base-content/50">price implies </span>
                      <span className="font-semibold">~{scenarioVal.market_implied_growth}% FCF growth/yr for 10y</span>
                    </div>
                  )}
                  {scenarioVal.terminal_pct != null && (
                    <div className="text-base-content/50">terminal = {scenarioVal.terminal_pct}% of EV · exit {scenarioVal.exit_multiple}×</div>
                  )}
                </div>
                {/* How this fair value is calculated — with the current input values */}
                <div className="mt-2 text-[11px] text-base-content/50 leading-relaxed">
                  <span className="font-semibold text-base-content/60">How it's calculated: </span>
                  Base FCF {fmtLargeNum(startingFcf)} → grow {fcfGrowth}%/yr for {projYears}y (CAP) → fade to {terminalGrowth}% over 5y →
                  discount at {discountRate}% WACC → + terminal (Gordon &amp; {exitMultiple}× exit, blended) → − net debt {fmtLargeNum(netDebt)} → ÷ {fmtLargeNum(sharesOut)} shares.
                </div>
                {scenarioVal.flags && scenarioVal.flags.length > 0 && (
                  <div className="mt-2 space-y-0.5">
                    {scenarioVal.flags.map((f, i) => (
                      <div key={i} className="flex items-start gap-1 text-warning text-xs">
                        <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" /><span>{f}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Reasoning Toggle */}
            <div className="bg-base-200/40 rounded-xl border border-white/[0.03] overflow-hidden">
              <button
                className="flex items-center justify-between w-full p-3 text-left hover:bg-base-content/5 transition-colors"
                onClick={() => setShowReasoning(!showReasoning)}
              >
                <span className="flex items-center gap-2 text-sm font-semibold text-base-content/70">
                  <Lightbulb className="w-4 h-4 text-warning" />
                  Why these defaults? (based on {data.company_name}'s financials)
                </span>
                {showReasoning ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
              </button>
              {showReasoning && (
                <div className="px-4 pb-4 space-y-1.5">
                  {data.reasoning.map((r, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm text-base-content/60">
                      <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-info" />
                      <span>{r}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Historical FCF + Revenue */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {fcfHistoryData && (
                <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                  <h4 className="text-sm font-semibold text-base-content/70 mb-2">Historical Free Cash Flow</h4>
                  <div className="h-36">
                    <Bar data={fcfHistoryData} options={{
                      ...chartOptions,
                      plugins: { ...chartOptions.plugins, legend: { display: false } },
                    }} />
                  </div>
                </div>
              )}
              {/* Key metrics mini-cards */}
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-1">Market Cap</div>
                  <div className="text-lg font-bold">{fmtLargeNum(data.market_cap)}</div>
                </div>
                <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-1">Beta</div>
                  <div className="text-lg font-bold">{data.beta}</div>
                </div>
                <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-1">Trailing P/E</div>
                  <div className="text-lg font-bold">{data.trailing_pe?.toFixed(1) ?? 'N/A'}</div>
                </div>
                <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                  <div className="text-xs text-base-content/50 mb-1">Forward P/E</div>
                  <div className="text-lg font-bold">{data.forward_pe?.toFixed(1) ?? 'N/A'}</div>
                </div>
              </div>
            </div>

            {/* Editable Inputs — two-stage / CAP model */}
            <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
              <div className="flex items-center justify-between mb-1">
                <h4 className="text-sm font-semibold text-base-content/70">
                  Scenario Parameters — tweak the two-stage model, then Recalculate
                </h4>
                <button className="btn btn-ghost btn-xs" onClick={resetDefaults}>Reset to algorithm</button>
              </div>
              <p className="text-[11px] text-base-content/40 mb-3">
                Two-stage: FCF grows at <b>stage-1</b> for the <b>CAP</b> years, then fades linearly to <b>terminal</b> over 5 years.
                Terminal value blends Gordon-growth &amp; an exit multiple. Hover any label for help.
              </p>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
                {/* Base FCF */}
                <div>
                  <div className="tooltip tooltip-bottom tooltip-primary w-full text-left before:max-w-xs before:whitespace-normal" data-tip="Normalized starting free cash flow (average of recent positive years) — the base the projection grows from. Smooths one-off dips.">
                    <label className="label py-0 cursor-help"><span className="label-text text-xs border-b border-dashed border-base-content/40 inline-block">Base FCF</span></label>
                  </div>
                  <input type="number" className="input input-bordered input-sm w-full" value={startingFcf} onChange={handleInputChange(setStartingFcf)} step={1000000000} />
                  <div className="text-xs text-base-content/40 mt-0.5">{fmtLargeNum(startingFcf)}</div>
                </div>
                {/* Stage-1 growth */}
                <div>
                  <div className="tooltip tooltip-bottom tooltip-primary w-full text-left before:max-w-xs before:whitespace-normal" data-tip={data?.suggestions?.fcf_growth_rate || 'Constant FCF growth during the CAP years, before the fade.'}>
                    <label className="label py-0 cursor-help"><span className="label-text text-xs border-b border-dashed border-base-content/40 inline-block">Stage-1 growth %</span></label>
                  </div>
                  <input type="number" className="input input-bordered input-sm w-full" value={fcfGrowth} onChange={handleInputChange(setFcfGrowth)} step={0.5} min={-50} max={100} />
                </div>
                {/* CAP years */}
                <div>
                  <div className="tooltip tooltip-bottom tooltip-primary w-full text-left before:max-w-xs before:whitespace-normal" data-tip="Competitive-Advantage Period: years FCF grows at the stage-1 rate before fading to terminal. Larger companies mean-revert faster, so fewer years.">
                    <label className="label py-0 cursor-help"><span className="label-text text-xs border-b border-dashed border-base-content/40 inline-block">CAP (years)</span></label>
                  </div>
                  <input type="number" className="input input-bordered input-sm w-full" value={projYears} onChange={handleInputChange(setProjYears)} min={1} max={15} />
                </div>
                {/* WACC */}
                <div>
                  <div className="tooltip tooltip-bottom tooltip-primary w-full text-left before:max-w-xs before:whitespace-normal" data-tip={data?.suggestions?.discount_rate || 'Discount rate (WACC) — CAPM using live 10-Y risk-free + beta, blended with the industry average.'}>
                    <label className="label py-0 cursor-help"><span className="label-text text-xs border-b border-dashed border-base-content/40 inline-block">WACC %</span></label>
                  </div>
                  <input type="number" className="input input-bordered input-sm w-full" value={discountRate} onChange={handleInputChange(setDiscountRate)} step={0.5} min={1} max={30} />
                </div>
                {/* Terminal */}
                <div>
                  <div className="tooltip tooltip-bottom tooltip-primary w-full text-left before:max-w-xs before:whitespace-normal" data-tip={data?.suggestions?.terminal_growth_rate || 'Perpetuity growth after the fade — should mirror long-run GDP/inflation (~2–3%).'}>
                    <label className="label py-0 cursor-help"><span className="label-text text-xs border-b border-dashed border-base-content/40 inline-block">Terminal %</span></label>
                  </div>
                  <input type="number" className="input input-bordered input-sm w-full" value={terminalGrowth} onChange={handleInputChange(setTerminalGrowth)} step={0.25} min={0} max={5} />
                </div>
                {/* Exit multiple */}
                <div>
                  <div className="tooltip tooltip-bottom tooltip-primary w-full text-left before:max-w-xs before:whitespace-normal" data-tip="Terminal EV/FCF multiple. Blended 50/50 with the Gordon-growth terminal value as an independent cross-check.">
                    <label className="label py-0 cursor-help"><span className="label-text text-xs border-b border-dashed border-base-content/40 inline-block">Exit mult ×</span></label>
                  </div>
                  <input type="number" className="input input-bordered input-sm w-full" value={exitMultiple} onChange={handleInputChange(setExitMultiple)} step={0.5} min={5} max={40} />
                </div>
                {/* Net Debt */}
                <div>
                  <div className="tooltip tooltip-bottom tooltip-primary w-full text-left before:max-w-xs before:whitespace-normal" data-tip="Total debt minus cash. Subtracted from enterprise value to get equity value. Negative = net cash (added).">
                    <label className="label py-0 cursor-help"><span className="label-text text-xs border-b border-dashed border-base-content/40 inline-block">Net Debt</span></label>
                  </div>
                  <input type="number" className="input input-bordered input-sm w-full" value={netDebt} onChange={handleInputChange(setNetDebt)} step={1000000000} />
                  <div className="text-xs text-base-content/40 mt-0.5">{netDebt >= 0 ? fmtLargeNum(netDebt) : `(${fmtLargeNum(Math.abs(netDebt))} net cash)`}</div>
                </div>
                {/* Recalculate */}
                <div className="flex items-end">
                  <button className={`btn btn-sm w-full ${scenarioDirty ? 'btn-primary' : 'btn-ghost'}`} onClick={recalculate} disabled={scenarioLoading}>
                    {scenarioLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <><DollarSign className="w-4 h-4" />Recalculate</>}
                  </button>
                </div>
              </div>
              {onUseInDebate && (
                <div className="flex items-center justify-between mt-3 pt-3 border-t border-white/[0.04]">
                  <span className="text-[11px] text-base-content/40">
                    {scenarioDirty ? 'Recalculate to apply your changes, then hand this DCF to the debate.' : 'Use these assumptions as the DCF anchor when you run the debate below.'}
                  </span>
                  <button className="btn btn-secondary btn-sm gap-2" onClick={() => onUseInDebate(buildParams())} disabled={scenarioDirty}>
                    <Sparkles className="w-4 h-4" /> Use in debate
                  </button>
                </div>
              )}
            </div>

            {/* Projected FCF Chart */}
            {chartData && (
              <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                <h4 className="text-sm font-semibold text-base-content/70 mb-2">
                  Projected vs. Present Value of Free Cash Flows
                </h4>
                <div className="h-52">
                  <Bar data={chartData} options={chartOptions} />
                </div>
              </div>
            )}

            {/* Valuation Breakdown — Equity Bridge */}
            {scenarioVal && !scenarioVal.error && (
              <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
                <h4 className="text-sm font-semibold text-base-content/70 mb-3">Valuation Bridge</h4>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
                  <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03]">
                    <div className="text-xs text-base-content/50 mb-1">PV of FCFs</div>
                    <div className="text-base font-bold">{fmtLargeNum(scenarioVal.total_pv_fcf)}</div>
                  </div>
                  <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03] flex items-center">
                    <span className="text-base-content/30 mr-2">+</span>
                    <div>
                      <div className="text-xs text-base-content/50 mb-1">Terminal Value (PV)</div>
                      <div className="text-base font-bold">{fmtLargeNum(scenarioVal.pv_terminal_value)}</div>
                    </div>
                  </div>
                  <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03] flex items-center">
                    <span className="text-base-content/30 mr-2">=</span>
                    <div>
                      <div className="text-xs text-base-content/50 mb-1">Enterprise Value</div>
                      <div className="text-base font-bold">{fmtLargeNum(scenarioVal.enterprise_value)}</div>
                    </div>
                  </div>
                  <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03] flex items-center">
                    <span className="text-base-content/30 mr-2">{scenarioVal.net_debt >= 0 ? '−' : '+'}</span>
                    <div>
                      <div className="text-xs text-base-content/50 mb-1">
                        {scenarioVal.net_debt >= 0 ? 'Net Debt' : 'Net Cash'}
                      </div>
                      <div className={`text-base font-bold ${scenarioVal.net_debt > 0 ? 'text-error' : 'text-success'}`}>
                        {fmtLargeNum(Math.abs(scenarioVal.net_debt))}
                      </div>
                    </div>
                  </div>
                  <div className="bg-base-200/30 rounded-xl p-3 border border-white/[0.03] flex items-center">
                    <span className="text-base-content/30 mr-2">=</span>
                    <div>
                      <div className="text-xs text-base-content/50 mb-1">Equity Value</div>
                      <div className="text-base font-bold">{fmtLargeNum(scenarioVal.equity_value)}</div>
                    </div>
                  </div>
                  <div className="bg-primary/10 border border-primary/30 rounded-lg p-3 flex items-center">
                    <span className="text-base-content/30 mr-2">÷</span>
                    <div>
                      <div className="text-xs text-base-content/50 mb-1">Fair Value / Share</div>
                      <div className={`text-lg font-bold ${isUndervalued ? 'text-success' : 'text-error'}`}>
                        ${scenarioVal.fair_value_per_share.toFixed(2)}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {scenarioVal?.error && (
              <div className="alert alert-warning text-sm">
                <AlertTriangle className="w-4 h-4" />
                <span>{scenarioVal.error}</span>
              </div>
            )}

            {/* Disclaimer */}
            <p className="text-xs text-base-content/30">
              DCF analysis is a theoretical framework. Actual value depends on many factors not captured here.
              This is not financial advice — always do your own due diligence.
            </p>
          </div>
        )}
      </div>
      </div>
    </>
  );
}
