import React, { useState, useEffect } from 'react';
import {
  DollarSign, TrendingUp, TrendingDown, Settings, AlertTriangle,
  Target, Calendar, Users, ArrowUpRight, ArrowDownRight, Compass, Info,
  FileText, ExternalLink, RefreshCw, Sparkles,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js';
import { Bar } from 'react-chartjs-2';
import { EarningsData, EarningsInsight } from '../types';
import { fetchEarningsInsights, generateEarningsInsights } from '../api';

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

interface EarningsSummaryProps {
  earnings: EarningsData;
  ticker: string;
}

export const EarningsSummary: React.FC<EarningsSummaryProps> = ({ earnings, ticker }) => {
  const [insight, setInsight] = useState<EarningsInsight | null>(null);
  const [loadingInsight, setLoadingInsight] = useState(false);
  const [apiKeyMissing, setApiKeyMissing] = useState(false);
  const [insightError, setInsightError] = useState<string | null>(null);

  // Load a previously generated recap (cached server-side) without spending a call.
  useEffect(() => {
    let active = true;
    setInsight(null);
    setApiKeyMissing(false);
    setInsightError(null);
    fetchEarningsInsights(ticker)
      .then((res) => { if (active && res && res.summary) setInsight(res); })
      .catch(() => { /* no cached insight yet — that's fine */ });
    return () => { active = false; };
  }, [ticker]);

  const generateInsight = async () => {
    setLoadingInsight(true);
    setApiKeyMissing(false);
    setInsightError(null);
    try {
      const res = await generateEarningsInsights(ticker);
      setInsight(res);
    } catch (err: any) {
      const msg = (err?.message || '').toLowerCase();
      if (msg.includes('api key') || msg.includes('not configured') || msg.includes('unauthorized')) {
        setApiKeyMissing(true);
      } else {
        setInsightError(err?.message || 'Unable to generate earnings insights.');
      }
    } finally {
      setLoadingInsight(false);
    }
  };

  if (!earnings.available) {
    return (
      <div className="glass-card">
        <div className="p-5">
          <h3 className="font-bold text-sm flex items-center gap-2">
            <DollarSign size={20} /> Earnings Summary
          </h3>
          <p className="text-base-content/60">No earnings data available.</p>
        </div>
      </div>
    );
  }

  const beat = earnings.epsSurprisePct !== null && earnings.epsSurprisePct > 0;

  // Chart data for quarterly EPS history
  const chartData = {
    labels: earnings.quarterlyHistory.map((q) => q.quarter),
    datasets: [
      {
        label: 'Actual EPS',
        data: earnings.quarterlyHistory.map((q) => q.epsActual),
        backgroundColor: 'rgba(34, 197, 94, 0.7)',
        borderColor: 'rgb(34, 197, 94)',
        borderWidth: 2,
        borderRadius: 4,
      },
      {
        label: 'Estimated EPS',
        data: earnings.quarterlyHistory.map((q) => q.epsEstimate),
        backgroundColor: 'rgba(56, 189, 248, 0.4)',
        borderColor: 'rgb(56, 189, 248)',
        borderWidth: 2,
        borderRadius: 4,
      },
    ],
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'top' as const,
        labels: { color: '#999', boxWidth: 12 },
      },
    },
    scales: {
      x: {
        ticks: { color: '#999' },
        grid: { display: false },
      },
      y: {
        ticks: {
          color: '#999',
          callback: (v: number | string) => `$${Number(v).toFixed(2)}`,
        },
        grid: { color: 'rgba(128,128,128,0.15)' },
      },
    },
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        <h3 className="font-bold text-sm flex items-center gap-2">
          <DollarSign size={20} /> Earnings Summary
          <span className="badge badge-sm badge-outline ml-2">{earnings.lastQuarter}</span>
        </h3>

        {/* Key metrics grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-2">
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
            <div className="text-xs text-base-content/50">Reported EPS</div>
            <div className="text-xl font-bold text-base-content tabular-nums">
              {earnings.reportedEPS !== null ? `$${earnings.reportedEPS.toFixed(2)}` : 'N/A'}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
            <div className="text-xs text-base-content/50">Estimated EPS</div>
            <div className="text-xl font-bold text-base-content tabular-nums">
              {earnings.estimatedEPS !== null ? `$${earnings.estimatedEPS.toFixed(2)}` : 'N/A'}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
            <div className="text-xs text-base-content/50">EPS Surprise</div>
            <div
              className={`text-xl font-bold flex items-center gap-1 ${beat ? 'text-success' : 'text-error'}`}
            >
              {earnings.epsSurprisePct !== null ? (
                <>
                  {beat ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
                  {beat ? '+' : ''}
                  {earnings.epsSurprisePct.toFixed(1)}%
                </>
              ) : (
                'N/A'
              )}
            </div>
          </div>
          <div className="bg-base-200/40 rounded-xl p-3 border border-white/[0.03]">
            <div className="text-xs text-base-content/50">Revenue</div>
            <div className="text-xl font-bold text-base-content tabular-nums">{earnings.revenueFormatted}</div>
          </div>
        </div>

        {/* Additional metrics */}
        <div className="flex flex-wrap gap-3 mt-2">
          {earnings.netIncomeFormatted !== 'N/A' && (
            <span className="badge badge-outline">Net Income: {earnings.netIncomeFormatted}</span>
          )}
          {earnings.trailingPE && (
            <span className="badge badge-outline">Trailing P/E: {earnings.trailingPE}</span>
          )}
          {earnings.forwardPE && (
            <span className="badge badge-outline">Forward P/E: {earnings.forwardPE}</span>
          )}
          {earnings.pegRatio && (
            <span className="badge badge-outline">PEG: {earnings.pegRatio}</span>
          )}
          {earnings.sector && <span className="badge badge-outline">{earnings.sector}</span>}
          {earnings.industry && <span className="badge badge-outline">{earnings.industry}</span>}
        </div>

        {/* EPS Beat/Miss Chart */}
        {earnings.quarterlyHistory.length > 0 && (
          <div className="mt-4">
            <h4 className="text-sm font-semibold text-base-content/70 mb-2">
              Quarterly EPS: Actual vs Estimate
            </h4>
            <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03] h-48">
              <Bar data={chartData} options={chartOptions} />
            </div>
          </div>
        )}

        {/* Forward-Looking Guidance */}
        {earnings.guidance && <ForwardGuidanceSection guidance={earnings.guidance} />}

        {/* Earnings Report Insights — grounded in the latest SEC (EDGAR) filing */}
        <div className="mt-4">
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <h4 className="text-sm font-semibold text-base-content/70 flex items-center gap-1.5">
              <FileText size={15} className="text-primary" />
              Earnings Report Insights
            </h4>
            <span className="text-[10px] text-base-content/40">from the latest SEC filing (EDGAR)</span>
            {!loadingInsight && (
              <button className="btn btn-xs btn-outline btn-primary ml-auto" onClick={generateInsight}>
                {insight
                  ? <><RefreshCw size={12} /> Regenerate</>
                  : <><Sparkles size={12} /> Generate Summary</>}
              </button>
            )}
          </div>

          {apiKeyMissing && (
            <div className="alert alert-warning py-2">
              <Settings size={14} />
              <span className="text-sm">
                An AI provider key is required. Please configure your OpenAI (or Gemini) key in{' '}
                <a href="/settings" className="link link-primary">
                  Settings
                </a>{' '}
                to use this feature.
              </span>
            </div>
          )}

          {insightError && !loadingInsight && (
            <div className="alert alert-error py-2">
              <AlertTriangle size={14} />
              <span className="text-sm">{insightError}</span>
            </div>
          )}

          {loadingInsight && (
            <div className="flex items-center gap-2 text-sm text-base-content/60">
              <span className="loading loading-spinner loading-xs" />
              Reading the latest 10-Q / earnings release from EDGAR and summarizing…
            </div>
          )}

          {!loadingInsight && !insight && !apiKeyMissing && !insightError && (
            <p className="text-xs text-base-content/40 leading-relaxed">
              Pull the most recent quarterly filing from SEC EDGAR and get an AI recap — results vs
              expectations, revenue drivers, margins, cash flow, guidance, and risks.
            </p>
          )}

          {insight && insight.summary && !loadingInsight && (
            <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
              <div className="prose prose-sm max-w-none prose-headings:text-base-content prose-headings:font-semibold prose-h3:text-sm prose-h3:mt-3 prose-h3:mb-1 prose-p:text-base-content/70 prose-strong:text-base-content prose-li:text-base-content/70">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{insight.summary}</ReactMarkdown>
              </div>
              {(insight.sources.length > 0 || insight.generated_at) && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-3 pt-3 border-t border-white/[0.05] text-[10px] text-base-content/40">
                  {insight.sources.length > 0 && (
                    <span className="font-semibold text-base-content/50">Sources:</span>
                  )}
                  {insight.sources.map((s, i) => (
                    s.url ? (
                      <a key={i} href={s.url} target="_blank" rel="noopener noreferrer" className="link link-primary inline-flex items-center gap-0.5">
                        <ExternalLink size={9} /> {s.form}{s.date ? ` · ${s.date}` : ''}
                      </a>
                    ) : (
                      <span key={i}>{s.form}</span>
                    )
                  ))}
                  {insight.generated_at && (
                    <span className="ml-auto">Generated {new Date(insight.generated_at).toLocaleDateString()}</span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};


/* ─────────── Forward-Looking Guidance Sub-component ─────────── */

function recLabel(rec: string | null): { text: string; cls: string } {
  if (!rec) return { text: 'N/A', cls: 'badge-ghost' };
  const r = rec.toLowerCase();
  if (r.includes('strong_buy') || r.includes('strong buy')) return { text: 'Strong Buy', cls: 'badge-success' };
  if (r.includes('buy')) return { text: 'Buy', cls: 'badge-success badge-outline' };
  if (r.includes('hold') || r.includes('neutral')) return { text: 'Hold', cls: 'badge-warning' };
  if (r.includes('sell') || r.includes('under')) return { text: 'Sell', cls: 'badge-error' };
  return { text: rec.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()), cls: 'badge-ghost' };
}

function ForwardGuidanceSection({ guidance }: { guidance: NonNullable<EarningsData['guidance']> }) {
  const hasTargets = guidance.targetMeanPrice !== null;
  const hasEpsForward = guidance.forwardEps !== null;
  const hasGrowth = guidance.revenueGrowthPct !== null || guidance.earningsGrowthPct !== null || guidance.epsGrowthPct !== null;
  const hasAny = hasTargets || hasEpsForward || hasGrowth || guidance.recommendation !== null;

  if (!hasAny) return null;

  const upside = guidance.targetUpsidePct;
  const isUpside = upside !== null && upside > 0;
  const rec = recLabel(guidance.recommendation);

  return (
    <div className="mt-4">
      <h4 className="text-sm font-semibold text-base-content/70 mb-3 flex items-center gap-1.5">
        <Compass size={16} className="text-info" />
        Forward-Looking Guidance
      </h4>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Analyst Price Target */}
        {hasTargets && (
          <div className="bg-base-200/40 rounded-xl p-4 border border-white/[0.03]">
            <div className="flex items-center gap-1.5 mb-3">
              <Target size={14} className="text-primary" />
              <span className="text-xs font-semibold text-base-content/60">Analyst Price Target</span>
              {guidance.numberOfAnalysts && (
                <span className="badge badge-ghost badge-xs ml-auto">
                  <Users size={10} className="mr-0.5" /> {guidance.numberOfAnalysts} analysts
                </span>
              )}
            </div>

            {/* Target range visual */}
            <div className="relative mb-3">
              <div className="flex justify-between text-[10px] text-base-content/30 mb-1">
                <span>Low</span>
                <span>Mean</span>
                <span>High</span>
              </div>
              <div className="h-2 bg-base-100 rounded-full relative overflow-hidden">
                {/* Range bar */}
                {guidance.targetLowPrice !== null && guidance.targetHighPrice !== null && guidance.targetHighPrice > 0 && (() => {
                  const low = guidance.targetLowPrice!;
                  const high = guidance.targetHighPrice!;
                  const mean = guidance.targetMeanPrice!;
                  const range = high * 1.1; // slight padding
                  const leftPct = (low / range) * 100;
                  const widthPct = ((high - low) / range) * 100;
                  const meanPct = (mean / range) * 100;
                  const curPct = guidance.currentPrice ? (guidance.currentPrice / range) * 100 : null;
                  return (
                    <>
                      <div className="absolute h-full bg-primary/30 rounded-full" style={{ left: `${leftPct}%`, width: `${widthPct}%` }} />
                      <div className="absolute h-full w-0.5 bg-primary" style={{ left: `${meanPct}%` }} />
                      {curPct !== null && (
                        <div className="absolute h-full w-1 bg-warning rounded-full" style={{ left: `${curPct}%` }} title="Current Price" />
                      )}
                    </>
                  );
                })()}
              </div>
              <div className="flex justify-between mt-1">
                <span className="text-xs font-bold text-base-content/50">${guidance.targetLowPrice?.toFixed(2)}</span>
                <span className="text-sm font-bold text-primary">${guidance.targetMeanPrice?.toFixed(2)}</span>
                <span className="text-xs font-bold text-base-content/50">${guidance.targetHighPrice?.toFixed(2)}</span>
              </div>
            </div>

            {/* Upside/downside badge */}
            {upside !== null && (
              <div className={`flex items-center gap-1.5 p-2 rounded-lg ${isUpside ? 'bg-success/10' : 'bg-error/10'}`}>
                {isUpside ? <ArrowUpRight size={14} className="text-success" /> : <ArrowDownRight size={14} className="text-error" />}
                <span className={`text-sm font-bold ${isUpside ? 'text-success' : 'text-error'}`}>
                  {isUpside ? '+' : ''}{upside.toFixed(1)}% {isUpside ? 'upside' : 'downside'} to mean target
                </span>
              </div>
            )}
          </div>
        )}

        {/* EPS & Growth Estimates */}
        <div className="bg-base-300 rounded-xl p-4">
          <div className="flex items-center gap-1.5 mb-3">
            <TrendingUp size={14} className="text-success" />
            <span className="text-xs font-semibold text-base-content/60">Earnings & Growth Outlook</span>
            {guidance.recommendation && (
              <span className={`badge badge-xs ${rec.cls} ml-auto`}>{rec.text}</span>
            )}
          </div>

          {/* Analyst EPS path — trailing TTM → current-FY est → next-FY est */}
          {hasEpsForward && <EpsOutlook guidance={guidance} />}

          <div className="grid grid-cols-2 gap-3">
            {/* Revenue Growth (most recent quarter, YoY actual) */}
            {guidance.revenueGrowthPct !== null && (
              <div className="bg-base-200/60 rounded-lg p-2.5 text-center" title="Most recent reported quarter vs the same quarter a year ago (actual, not an estimate)">
                <div className="text-[10px] text-base-content/40 flex items-center justify-center gap-1">
                  Revenue Growth <span className="text-base-content/25">· YoY qtr</span>
                </div>
                <div className={`text-lg font-bold ${guidance.revenueGrowthPct > 0 ? 'text-success' : 'text-error'}`}>
                  {guidance.revenueGrowthPct > 0 ? '+' : ''}{guidance.revenueGrowthPct.toFixed(1)}%
                </div>
              </div>
            )}

            {/* Earnings Growth (most recent quarter, YoY actual) */}
            {guidance.earningsGrowthPct !== null && (
              <div className="bg-base-200/60 rounded-lg p-2.5 text-center" title="Net income growth for the most recent reported quarter vs a year ago (actual, not an estimate)">
                <div className="text-[10px] text-base-content/40 flex items-center justify-center gap-1">
                  Earnings Growth <span className="text-base-content/25">· YoY qtr</span>
                </div>
                <div className={`text-lg font-bold ${guidance.earningsGrowthPct > 0 ? 'text-success' : 'text-error'}`}>
                  {guidance.earningsGrowthPct > 0 ? '+' : ''}{guidance.earningsGrowthPct.toFixed(1)}%
                </div>
              </div>
            )}

            {/* Profit Margins */}
            {guidance.profitMargin !== null && (
              <div className="bg-base-200/60 rounded-lg p-2.5 text-center">
                <div className="text-[10px] text-base-content/40">Profit Margin</div>
                <div className="text-sm font-bold">{guidance.profitMargin.toFixed(1)}%</div>
              </div>
            )}

            {/* Operating Margin */}
            {guidance.operatingMargin !== null && (
              <div className="bg-base-200/60 rounded-lg p-2.5 text-center">
                <div className="text-[10px] text-base-content/40">Operating Margin</div>
                <div className="text-sm font-bold">{guidance.operatingMargin.toFixed(1)}%</div>
              </div>
            )}
          </div>

          {/* Next earnings date */}
          {guidance.nextEarningsDate && (
            <div className="flex items-center flex-wrap gap-1.5 mt-3 text-xs text-base-content/40">
              <Calendar size={12} />
              Next Earnings: <span className="font-semibold text-base-content/60">{guidance.nextEarningsDate}</span>
              {guidance.nextEarningsDateIsEstimated && (
                <span
                  className="badge badge-ghost badge-xs"
                  title={`Estimated — the exact date isn't confirmed yet${guidance.lastEarningsDate ? `. Last report: ${guidance.lastEarningsDate}` : ''}. Projected ~1 quarter after the last report.`}
                >
                  est.
                </span>
              )}
            </div>
          )}

          {/* Recommendation score gauge */}
          {guidance.recommendationScore !== null && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-[10px] text-base-content/30 mb-0.5">
                <span>Strong Buy</span>
                <span>Hold</span>
                <span>Sell</span>
              </div>
              <div className="h-1.5 bg-base-100 rounded-full relative overflow-hidden">
                <div className="absolute inset-0 flex">
                  <div className="h-full bg-success/25" style={{ width: '20%' }} />
                  <div className="h-full bg-success/15" style={{ width: '20%' }} />
                  <div className="h-full bg-warning/20" style={{ width: '20%' }} />
                  <div className="h-full bg-error/15" style={{ width: '20%' }} />
                  <div className="h-full bg-error/25" style={{ width: '20%' }} />
                </div>
                {/* Marker: 1=Strong Buy (0%), 5=Sell (100%) */}
                <div
                  className="absolute top-0 h-full w-1 bg-base-content rounded-full"
                  style={{ left: `${((guidance.recommendationScore - 1) / 4) * 100}%` }}
                />
              </div>
              <div className="text-[10px] text-base-content/30 text-center mt-0.5">
                Consensus: {guidance.recommendationScore.toFixed(1)} / 5
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


/* ─────────── Analyst EPS Outlook (the "why is growth so big?" explainer) ─────────── */

function EpsStop({ label, sub, value, accent }: { label: string; sub: string; value: number | null; accent: string }) {
  return (
    <div className="flex-1 min-w-0">
      <div className="text-[9px] text-base-content/40 truncate">{label}</div>
      <div className={`text-sm font-bold ${accent}`}>{value !== null ? `$${value.toFixed(2)}` : 'N/A'}</div>
      <div className="text-[9px] text-base-content/30">{sub}</div>
    </div>
  );
}

function EpsArrow({ pct }: { pct: number | null }) {
  const cls = pct === null ? 'text-base-content/40' : pct > 0 ? 'text-success' : pct < 0 ? 'text-error' : 'text-base-content/50';
  return (
    <div className="flex flex-col items-center justify-center px-0.5 shrink-0">
      {pct !== null && (
        <span className={`text-[10px] font-semibold ${cls}`}>{pct > 0 ? '+' : ''}{pct.toFixed(1)}%</span>
      )}
      <span className="text-base-content/20 text-xs leading-none">→</span>
    </div>
  );
}

function EpsOutlook({ guidance }: { guidance: NonNullable<EarningsData['guidance']> }) {
  const [showWhy, setShowWhy] = useState(false);
  const {
    trailingEps, epsCurrentYear, forwardEps, epsGrowthPct,
    epsCurrentYearGrowthPct, epsForwardGrowthPct, numberOfAnalysts,
  } = guidance;
  const hasPath = epsCurrentYear !== null; // can show the 3-stop per-year path

  return (
    <div className="bg-base-200/60 rounded-lg p-3 mb-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] font-semibold text-base-content/60 flex items-center gap-1">
          Analyst EPS Outlook
          <button
            type="button"
            onClick={() => setShowWhy((v) => !v)}
            className="text-base-content/30 hover:text-base-content/70 transition-colors"
            title="Why is the growth number so large?"
          >
            <Info size={12} />
          </button>
        </span>
        <span className="text-[10px] text-base-content/35">
          {numberOfAnalysts ? `Consensus · ${numberOfAnalysts} analysts` : 'Analyst consensus'}
        </span>
      </div>

      {/* EPS path */}
      <div className="flex items-stretch justify-between gap-1 text-center">
        <EpsStop label="Trailing TTM" sub="GAAP actual" value={trailingEps} accent="text-base-content" />
        {hasPath ? (
          <>
            <EpsArrow pct={epsCurrentYearGrowthPct} />
            <EpsStop label="Current FY" sub="estimate" value={epsCurrentYear} accent="text-base-content" />
            <EpsArrow pct={epsForwardGrowthPct} />
            <EpsStop label="Next FY" sub="estimate" value={forwardEps} accent="text-primary" />
          </>
        ) : (
          <>
            <EpsArrow pct={epsGrowthPct} />
            <EpsStop label="Forward" sub="estimate" value={forwardEps} accent="text-primary" />
          </>
        )}
      </div>

      {/* Headline-growth caveat */}
      {epsGrowthPct !== null && hasPath && (
        <p className="text-[10px] text-base-content/40 mt-2 leading-relaxed">
          The headline forward-vs-trailing figure (
          <span className={epsGrowthPct > 0 ? 'text-success font-semibold' : 'text-error font-semibold'}>
            {epsGrowthPct > 0 ? '+' : ''}{epsGrowthPct.toFixed(1)}%
          </span>
          ) compares <span className="font-semibold">trailing GAAP EPS</span> to the{' '}
          <span className="font-semibold">next-FY adjusted estimate</span> and spans ~2 fiscal years — the per-year
          steps above are the more representative read.
        </p>
      )}

      {showWhy && (
        <div className="mt-2 text-[10px] text-base-content/55 bg-base-100/50 rounded-lg p-2.5 leading-relaxed space-y-1.5">
          <p>
            <span className="font-semibold text-base-content/70">Why the big jump?</span> Forward EPS is an{' '}
            <span className="font-semibold">adjusted (non-GAAP)</span> estimate — it strips out one-time costs,
            restructuring and intangible amortization. Trailing EPS is <span className="font-semibold">GAAP</span> and
            can be depressed by exactly those items, so the gap overstates the company's underlying growth (it's not
            from buybacks).
          </p>
          <p>
            These are <span className="font-semibold">analyst consensus estimates</span>, not official company
            guidance. A large forward jump means "analysts expect earnings to normalize," not guaranteed growth —
            sanity-check it against the quarterly revenue and earnings growth shown below.
          </p>
        </div>
      )}
    </div>
  );
}
