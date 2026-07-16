import React, { useState, useEffect } from 'react';
import {
  TrendingUp, DollarSign, Percent, BarChart3, Building2,
  Star, AlertTriangle, Layers, RefreshCw, Users, ChevronDown,
  ChevronUp, Loader2, Sparkles, ArrowUpRight, ArrowDownRight,
  Minus, PieChart,
} from 'lucide-react';
import { fetchFundDetails, fetchFundManagerBrief, generateFundManagerBrief } from '../api';
import type { StockData, FundDetails, FundManagerBrief, FundBenchmark, FundReturns } from '../types';

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

function fmtAum(b: number | null | undefined): string {
  if (b === null || b === undefined) return 'N/A';
  if (b >= 1000) return `$${(b / 1000).toFixed(1)}T`;
  if (b >= 1) return `$${b.toFixed(1)}B`;
  return `$${(b * 1000).toFixed(0)}M`;
}

function ReturnCell({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-base-content/30">—</span>;
  const pos = value >= 0;
  return (
    <span className={`font-semibold tabular-nums ${pos ? 'text-success' : 'text-error'}`}>
      {pos ? '+' : ''}{value.toFixed(2)}%
    </span>
  );
}

function ReturnBadge({ value, label }: { value: number | null | undefined; label: string }) {
  return (
    <div className="flex flex-col items-center gap-0.5 p-3 rounded-xl bg-base-200/60 min-w-[80px]">
      <span className="text-[10px] text-base-content/40 font-medium uppercase tracking-wide">{label}</span>
      <ReturnCell value={value} />
    </div>
  );
}

function StarRating({ rating, max = 5 }: { rating: number | null; max?: number }) {
  if (!rating) return <span className="text-base-content/40 text-xs">N/A</span>;
  return (
    <div className="flex gap-0.5">
      {Array.from({ length: max }, (_, i) => (
        <Star key={i} className={`w-3.5 h-3.5 ${i < rating ? 'text-yellow-400 fill-yellow-400' : 'text-base-content/20'}`} />
      ))}
    </div>
  );
}

function MetricRow({ label, value, icon: Icon }: { label: string; value: React.ReactNode; icon?: React.ElementType }) {
  return (
    <div className="flex items-center justify-between py-2.5 border-b border-base-200/50 last:border-0">
      <div className="flex items-center gap-2 text-xs text-base-content/60">
        {Icon && <Icon className="w-3.5 h-3.5" />}
        {label}
      </div>
      <div className="text-xs font-semibold text-base-content tabular-nums">{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-sections
// ---------------------------------------------------------------------------

function OverviewSection({ stockData, details }: { stockData: StockData; details: FundDetails | null }) {
  const ops = details?.fund_ops;
  const ret = details?.returns;
  const ac = details?.asset_classes ?? {};

  const er = ops?.expense_ratio_pct ?? stockData.netExpenseRatioPct;
  const catEr = ops?.category_avg_expense_ratio_pct;
  const aumM = ops?.aum_m;
  const aumDisplay = aumM != null ? fmtAum(aumM / 1000) : fmtAum(stockData.aumB);

  return (
    <div className="space-y-4">
      {/* Key metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-xl bg-base-200/60 p-3 flex flex-col gap-0.5">
          <span className="text-[10px] text-base-content/40 uppercase tracking-wide">AUM</span>
          <span className="text-sm font-bold">{aumDisplay}</span>
        </div>
        <div className="rounded-xl bg-base-200/60 p-3 flex flex-col gap-0.5">
          <span className="text-[10px] text-base-content/40 uppercase tracking-wide">Expense Ratio</span>
          <span className={`text-sm font-bold ${er != null && er > 0.75 ? 'text-warning' : ''}`}>
            {er != null ? `${er.toFixed(3)}%` : 'N/A'}
          </span>
          {catEr != null && (
            <span className="text-[9px] text-base-content/40">Cat avg: {catEr.toFixed(3)}%</span>
          )}
        </div>
        <div className="rounded-xl bg-base-200/60 p-3 flex flex-col gap-0.5">
          <span className="text-[10px] text-base-content/40 uppercase tracking-wide">Distribution Yield</span>
          <span className="text-sm font-bold">
            {stockData.fundYieldPct != null ? `${stockData.fundYieldPct.toFixed(2)}%` : `${stockData.dividendYield.toFixed(2)}%`}
          </span>
        </div>
        <div className="rounded-xl bg-base-200/60 p-3 flex flex-col gap-0.5">
          <span className="text-[10px] text-base-content/40 uppercase tracking-wide">Turnover</span>
          <span className="text-sm font-bold">
            {ops?.turnover_pct != null ? `${ops.turnover_pct.toFixed(1)}%` : (stockData.fundTurnoverPct != null ? `${stockData.fundTurnoverPct.toFixed(1)}%` : 'N/A')}
          </span>
          {ops?.category_avg_turnover_pct != null && (
            <span className="text-[9px] text-base-content/40">Cat avg: {ops.category_avg_turnover_pct.toFixed(1)}%</span>
          )}
        </div>
      </div>

      {/* Multi-period returns */}
      <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
        <div className="flex items-center gap-2 mb-3">
          <TrendingUp className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-bold">Performance Returns</h3>
        </div>
        <div className="grid grid-cols-4 sm:grid-cols-8 gap-2">
          <ReturnBadge value={ret?.['7d_pct'] ?? null} label="7D" />
          <ReturnBadge value={ret?.['1m_pct'] ?? null} label="1M" />
          <ReturnBadge value={ret?.['3m_pct'] ?? null} label="3M" />
          <ReturnBadge value={ret?.ytd_pct ?? stockData.ytdReturnPct} label="YTD" />
          <ReturnBadge value={ret?.['1yr_pct'] ?? null} label="1Y" />
          <ReturnBadge value={ret?.['3yr_ann'] ?? stockData.threeYrReturnPct} label="3Y Ann" />
          <ReturnBadge value={ret?.['5yr_ann'] ?? stockData.fiveYrReturnPct} label="5Y Ann" />
          <ReturnBadge value={ret?.['10yr_ann'] ?? null} label="10Y Ann" />
        </div>
      </div>

      {/* Morningstar + details */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Star className="w-4 h-4 text-yellow-400" />
            <h3 className="text-sm font-bold">Morningstar</h3>
          </div>
          <MetricRow label="Overall Rating" value={<StarRating rating={stockData.morningstarOverallRating} />} />
          <MetricRow
            label="Risk Rating"
            value={stockData.morningstarRiskRating != null
              ? (['', 'Low', 'Below Avg', 'Average', 'Above Avg', 'High'][stockData.morningstarRiskRating] ?? String(stockData.morningstarRiskRating))
              : 'N/A'}
          />
        </div>
        <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Building2 className="w-4 h-4 text-accent" />
            <h3 className="text-sm font-bold">Fund Details</h3>
          </div>
          <MetricRow label="Fund Family" value={stockData.fundFamily || 'N/A'} />
          <MetricRow label="Category" value={stockData.fundCategory || details?.category || 'N/A'} />
          <MetricRow label="Legal Type" value={details?.legal_type || (stockData.quoteType === 'MUTUALFUND' ? 'Mutual Fund' : 'ETF')} />
          <MetricRow label="52-Week Range"
            value={stockData.fiftyTwoWeekLow != null && stockData.fiftyTwoWeekHigh != null
              ? `$${stockData.fiftyTwoWeekLow.toFixed(2)} – $${stockData.fiftyTwoWeekHigh.toFixed(2)}`
              : 'N/A'}
          />
        </div>
      </div>

      {/* Asset class breakdown */}
      {Object.keys(ac).length > 0 && (
        <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
          <div className="flex items-center gap-2 mb-3">
            <PieChart className="w-4 h-4 text-info" />
            <h3 className="text-sm font-bold">Asset Class Breakdown</h3>
          </div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(ac)
              .filter(([, v]) => v > 0)
              .sort(([, a], [, b]) => b - a)
              .map(([k, v]) => (
                <div key={k} className="flex flex-col items-center px-3 py-2 rounded-xl bg-base-200/60">
                  <span className="text-[10px] text-base-content/40 capitalize">{k.replace(/Position$/, '')}</span>
                  <span className="text-xs font-bold">{v.toFixed(1)}%</span>
                </div>
              ))}
          </div>
        </div>
      )}

      {er != null && er > 0.75 && (
        <div className="flex items-start gap-2.5 p-3 rounded-xl bg-warning/10 border border-warning/30 text-warning text-xs">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>
            Expense ratio of <strong>{er.toFixed(3)}%</strong> is above average.
            Index ETFs typically charge 0.03–0.20%. High fees compound significantly over time.
          </span>
        </div>
      )}
    </div>
  );
}

function HoldingsSection({ details }: { details: FundDetails | null }) {
  const holdings = details?.top_holdings ?? [];
  const sectors = details?.sector_weightings ?? {};

  const SECTOR_COLORS: Record<string, string> = {
    technology: 'bg-blue-500',
    healthcare: 'bg-green-500',
    financial_services: 'bg-yellow-500',
    consumer_cyclical: 'bg-orange-500',
    consumer_defensive: 'bg-teal-500',
    industrials: 'bg-purple-500',
    communication_services: 'bg-pink-500',
    energy: 'bg-red-500',
    realestate: 'bg-indigo-500',
    utilities: 'bg-cyan-500',
    basic_materials: 'bg-amber-500',
  };

  return (
    <div className="space-y-4">
      {/* Top holdings */}
      <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
        <div className="flex items-center gap-2 mb-3">
          <Layers className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-bold">Top Holdings</h3>
          <span className="text-xs text-base-content/40 ml-auto">Top {holdings.length}</span>
        </div>
        {holdings.length === 0 ? (
          <p className="text-xs text-base-content/40 text-center py-4">Holdings data not available</p>
        ) : (
          <div className="space-y-2">
            {holdings.map((h, i) => (
              <div key={h.ticker} className="flex items-center gap-3">
                <span className="text-[10px] text-base-content/30 w-4 text-right">{i + 1}</span>
                <span className="font-mono text-xs font-bold text-primary w-14 flex-shrink-0">{h.ticker}</span>
                <span className="text-xs text-base-content/70 flex-1 truncate">{h.name}</span>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <div className="w-20 bg-base-200 rounded-full h-1.5">
                    <div
                      className="bg-primary h-1.5 rounded-full"
                      style={{ width: `${Math.min(100, (h.weight_pct / (holdings[0]?.weight_pct || 1)) * 100)}%` }}
                    />
                  </div>
                  <span className="text-xs font-semibold tabular-nums w-12 text-right">{h.weight_pct.toFixed(2)}%</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Sector allocation */}
      {Object.keys(sectors).length > 0 && (
        <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
          <div className="flex items-center gap-2 mb-3">
            <BarChart3 className="w-4 h-4 text-accent" />
            <h3 className="text-sm font-bold">Sector Allocation</h3>
          </div>
          <div className="space-y-2">
            {Object.entries(sectors)
              .filter(([, v]) => v > 0)
              .sort(([, a], [, b]) => b - a)
              .map(([sector, pct]) => {
                const color = SECTOR_COLORS[sector] || 'bg-base-content/40';
                const label = sector.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
                return (
                  <div key={sector} className="flex items-center gap-3">
                    <div className={`w-2.5 h-2.5 rounded-sm flex-shrink-0 ${color}`} />
                    <span className="text-xs text-base-content/70 flex-1">{label}</span>
                    <div className="w-24 bg-base-200 rounded-full h-1.5 flex-shrink-0">
                      <div className={`${color} h-1.5 rounded-full`} style={{ width: `${Math.min(100, pct)}%` }} />
                    </div>
                    <span className="text-xs font-semibold tabular-nums w-10 text-right">{pct.toFixed(1)}%</span>
                  </div>
                );
              })}
          </div>
        </div>
      )}
    </div>
  );
}

function ManagerSection({ ticker }: { ticker: string }) {
  const [brief, setBrief] = useState<FundManagerBrief | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchFundManagerBrief(ticker)
      .then(setBrief)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [ticker]);

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    try {
      const result = await generateFundManagerBrief(ticker);
      setBrief(result);
    } catch (e: any) {
      setError(e?.message || 'Generation failed');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Users className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-bold">Fund Manager Research</h3>
        </div>
        <button
          onClick={handleGenerate}
          disabled={generating}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-xl bg-primary/10 hover:bg-primary/20 text-primary font-semibold transition-colors disabled:opacity-50"
        >
          {generating ? <Loader2 className="w-3 h-3 animate-spin" /> : <Sparkles className="w-3 h-3" />}
          {brief?.analysis ? 'Refresh' : 'Generate'}
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-base-content/40 py-4">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading…
        </div>
      )}

      {!loading && !brief?.analysis && !generating && (
        <div className="text-center py-6">
          <Users className="w-8 h-8 text-base-content/20 mx-auto mb-2" />
          <p className="text-xs text-base-content/40 mb-3">
            AI research on fund manager(s): background, investment style, public profile, track record and more.
          </p>
          <button
            onClick={handleGenerate}
            className="flex items-center gap-1.5 text-xs px-4 py-2 rounded-xl bg-primary text-primary-content font-semibold mx-auto hover:bg-primary/90 transition-colors"
          >
            <Sparkles className="w-3 h-3" />
            Generate Manager Brief
          </button>
        </div>
      )}

      {generating && (
        <div className="flex items-center gap-2 text-xs text-base-content/40 py-4">
          <Loader2 className="w-4 h-4 animate-spin" />
          Researching fund managers…
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 text-xs text-error bg-error/10 rounded-xl p-3 mt-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}

      {brief?.analysis && !generating && (
        <div className="prose prose-sm prose-invert max-w-none">
          <div
            className="text-xs text-base-content/80 leading-relaxed [&_h1]:text-sm [&_h1]:font-bold [&_h1]:text-base-content [&_h2]:text-xs [&_h2]:font-bold [&_h2]:text-base-content [&_h3]:text-xs [&_h3]:font-semibold [&_strong]:text-base-content [&_ul]:space-y-1 [&_li]:text-xs"
            dangerouslySetInnerHTML={{ __html: markdownToHtml(brief.analysis) }}
          />
          <p className="text-[10px] text-base-content/30 mt-3 italic">
            Based on publicly available information. May not reflect recent changes.
            {brief.created_at && ` Generated ${new Date(brief.created_at).toLocaleDateString()}.`}
          </p>
        </div>
      )}
    </div>
  );
}

// Minimal markdown → HTML (bold, headers, bullets)
function markdownToHtml(md: string): string {
  return md
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    .replace(/^\* (.+)$/gm, '<li>$1</li>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>(\n|$))+/g, m => `<ul>${m}</ul>`)
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br />')
    .replace(/^/, '<p>').replace(/$/, '</p>');
}

// ---------------------------------------------------------------------------
// Benchmark comparison table
// ---------------------------------------------------------------------------

const PERIOD_COLS: { key: keyof FundBenchmark; label: string }[] = [
  { key: '7d_pct', label: '7D' },
  { key: '1m_pct', label: '1M' },
  { key: '3m_pct', label: '3M' },
  { key: '1yr_pct', label: '1Y' },
  { key: '3yr_ann', label: '3Y Ann' },
  { key: '5yr_ann', label: '5Y Ann' },
  { key: '10yr_ann', label: '10Y Ann' },
];

function BenchmarkSection({ ticker, stockData, details }: { ticker: string; stockData: StockData; details: FundDetails | null }) {
  const peers = details?.benchmark_comparison ?? [];
  const ret = details?.returns;

  // Build fund row from details.returns
  const fundRow: FundBenchmark = {
    ticker,
    name: stockData.companyName,
    '7d_pct': ret?.['7d_pct'] ?? null,
    '1m_pct': ret?.['1m_pct'] ?? null,
    '3m_pct': ret?.['3m_pct'] ?? null,
    '1yr_pct': ret?.['1yr_pct'] ?? null,
    '3yr_ann': ret?.['3yr_ann'] ?? stockData.threeYrReturnPct,
    '5yr_ann': ret?.['5yr_ann'] ?? stockData.fiveYrReturnPct,
    '10yr_ann': ret?.['10yr_ann'] ?? null,
    ytd_pct: ret?.ytd_pct ?? stockData.ytdReturnPct,
    expense_ratio_pct: stockData.netExpenseRatioPct,
    aum_b: stockData.aumB,
  };

  const allRows = [fundRow, ...peers];

  return (
    <div className="rounded-2xl border border-base-300/50 bg-base-100/60 p-4">
      <div className="flex items-center gap-2 mb-4">
        <BarChart3 className="w-4 h-4 text-primary" />
        <h3 className="text-sm font-bold">Benchmark &amp; Peer Comparison</h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs min-w-[640px]">
          <thead>
            <tr className="border-b border-base-200">
              <th className="text-left py-2 pr-3 text-base-content/40 font-medium w-28">Fund</th>
              {PERIOD_COLS.map(c => (
                <th key={c.key} className="text-right py-2 px-2 text-base-content/40 font-medium">{c.label}</th>
              ))}
              <th className="text-right py-2 pl-2 text-base-content/40 font-medium">ER</th>
              <th className="text-right py-2 pl-2 text-base-content/40 font-medium">AUM</th>
            </tr>
          </thead>
          <tbody>
            {allRows.map((row, i) => (
              <tr
                key={row.ticker}
                className={`border-b border-base-200/40 last:border-0 ${i === 0 ? 'bg-primary/5' : 'hover:bg-base-200/30'} transition-colors`}
              >
                <td className="py-2.5 pr-3">
                  <div className="font-bold text-primary">{row.ticker}</div>
                  <div className="text-[10px] text-base-content/40 truncate max-w-[110px]">{row.name}</div>
                </td>
                {PERIOD_COLS.map(c => (
                  <td key={c.key} className="py-2.5 px-2 text-right">
                    <ReturnCell value={row[c.key] as number | null} />
                  </td>
                ))}
                <td className="py-2.5 pl-2 text-right text-base-content/60">
                  {row.expense_ratio_pct != null ? `${row.expense_ratio_pct.toFixed(3)}%` : '—'}
                </td>
                <td className="py-2.5 pl-2 text-right text-base-content/60">
                  {fmtAum(row.aum_b)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-base-content/30 mt-3">
        Returns are total return. Annualized figures shown for multi-year periods. Peer list based on fund category.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'holdings', label: 'Holdings' },
  { key: 'manager', label: 'Manager' },
  { key: 'benchmark', label: 'vs Benchmark' },
] as const;

type Tab = typeof TABS[number]['key'];

interface Props {
  stockData: StockData;
}

export function FundFundamentals({ stockData }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>('overview');
  const [details, setDetails] = useState<FundDetails | null>(null);
  const [loadingDetails, setLoadingDetails] = useState(false);

  const isMF = stockData.quoteType?.toUpperCase() === 'MUTUALFUND';
  const typeLabel = isMF ? 'Mutual Fund' : 'ETF';

  useEffect(() => {
    setLoadingDetails(true);
    fetchFundDetails(stockData.ticker)
      .then(setDetails)
      .catch(() => {})
      .finally(() => setLoadingDetails(false));
  }, [stockData.ticker]);

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-info/15 border border-info/30 text-info text-xs font-bold">
          <Layers className="w-3.5 h-3.5" />
          {typeLabel}
        </div>
        {(stockData.fundCategory || details?.category) && (
          <span className="text-xs text-base-content/50 font-medium">
            {stockData.fundCategory || details?.category}
          </span>
        )}
        {loadingDetails && <Loader2 className="w-3.5 h-3.5 animate-spin text-base-content/30 ml-auto" />}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 p-1 rounded-xl bg-base-200/60 w-fit">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all ${
              activeTab === t.key
                ? 'bg-base-100 text-base-content shadow-sm'
                : 'text-base-content/50 hover:text-base-content/80'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {activeTab === 'overview' && <OverviewSection stockData={stockData} details={details} />}
      {activeTab === 'holdings' && <HoldingsSection details={details} />}
      {activeTab === 'manager' && <ManagerSection ticker={stockData.ticker} />}
      {activeTab === 'benchmark' && <BenchmarkSection ticker={stockData.ticker} stockData={stockData} details={details} />}
    </div>
  );
}
