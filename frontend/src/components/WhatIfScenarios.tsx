/**
 * WhatIfScenarios — institutional-grade scenario evaluator.
 *
 * Produces the kind of brief a hedge-fund PM would read before a risk meeting:
 * executive thesis + probability + historical analogue + direct impacts
 * (stocks / ETFs / sectors with magnitude) + second-order effects + cross-asset
 * reactions (rates, DXY, oil, gold, VIX, credit) + invalidation signals +
 * suggested hedges. When the "include my trades" toggle is on, the backend
 * also scores each of the user's active positions individually.
 *
 * Endpoint: POST /api/stock/market/thematic-impact
 */

import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BrainCircuit, Zap, Target, Layers, TrendingUp, TrendingDown, Minus,
  Activity, Clock, Briefcase, Shield, AlertTriangle, GitBranch,
  CheckCircle2, XCircle, LineChart as LineChartIcon, Sparkles,
  ChevronRight, ClipboardList,
} from 'lucide-react';
import { apiBase, fetchActiveTrades } from '../api';
import type { SavedStrategyItem } from '../api';

// ── Shapes matching backend schema ──────────────────────────────────────────

type Sentiment = 'positive' | 'negative' | 'neutral';
type Magnitude = 'strong' | 'moderate' | 'mild';

interface SectorImpact { name: string; sentiment: Sentiment; magnitude?: Magnitude; reason: string; }
interface StockImpact { ticker: string; sentiment: Sentiment; magnitude?: Magnitude; estimated_move_pct?: number; reason: string; }
interface EtfImpact   { ticker: string; sentiment: Sentiment; magnitude?: Magnitude; reason: string; }
interface SecondOrderEffect { area: string; description: string; }
interface HedgeIdea { action: string; instrument: string; rationale: string; }
interface PortfolioImpact {
  ticker: string;
  exposure: 'high' | 'moderate' | 'low' | 'neutral';
  direction: Sentiment;
  estimated_impact_pct?: number;
  reasoning: string;
}

interface WhatIfAnalysis {
  executive_thesis: string;
  scenario_probability_pct?: number;
  time_horizon_estimate?: string;
  historical_analogue?: string;
  key_assumptions?: string[];
  key_catalysts?: string[];
  invalidation_signals?: string[];
  sectors?: SectorImpact[];
  stocks?: StockImpact[];
  etfs?: EtfImpact[];
  second_order_effects?: SecondOrderEffect[];
  cross_asset_reactions?: {
    equities?: string;
    rates_10y?: string;
    usd?: string;
    oil?: string;
    gold?: string;
    vix?: string;
    credit_spreads?: string;
  };
  suggested_hedges?: HedgeIdea[];
  portfolio_impacts?: PortfolioImpact[];
}

interface WhatIfResult {
  success: true;
  analysis: WhatIfAnalysis;
  market_snapshot?: Record<string, { label: string; last: number; change_pct_5d: number }>;
  metadata?: { time_horizon: string; portfolio_included: boolean; portfolio_size: number };
}

// ── Preset scenarios — what investors actually stress-test ──────────────────

interface Preset {
  id: string;
  label: string;
  category: string;
  scenario: string;
  horizon: string;
  icon?: React.ReactNode;
}

const PRESETS: Preset[] = [
  // Monetary
  { id: 'fed-cut-200',    category: 'Monetary',    horizon: '6-12 months', label: 'Fed cuts 200bps in emergency pivot',
    scenario: 'The Fed cuts the policy rate by 200bps over 3 meetings in response to sudden recession signals, with QT paused.' },
  { id: 'fed-hike-shock', category: 'Monetary',    horizon: '1-3 months',  label: 'Fed hikes 50bps unexpectedly',
    scenario: 'Inflation surprises to the upside and the Fed hikes 50bps at the next meeting against consensus dovish expectations.' },
  { id: 'boj-unpeg',      category: 'Monetary',    horizon: '1-3 months',  label: 'BOJ abandons YCC; yen surges',
    scenario: 'The Bank of Japan fully abandons yield curve control. JGB yields spike and USDJPY drops 15%, triggering an unwind of yen carry trades.' },

  // Geopolitical
  { id: 'taiwan',         category: 'Geopolitical', horizon: '1-3 months', label: 'Taiwan strait military crisis',
    scenario: 'A military confrontation in the Taiwan strait disrupts semiconductor supply. TSMC operations are suspended for 2+ weeks.' },
  { id: 'tariffs',        category: 'Geopolitical', horizon: '6-12 months', label: 'US imposes 50% tariffs on Chinese imports',
    scenario: 'The US imposes universal 50% tariffs on all Chinese goods, China retaliates on agricultural and rare-earth exports.' },
  { id: 'middle-east',    category: 'Geopolitical', horizon: '1-3 months',  label: 'Middle East supply shock: oil at $120',
    scenario: 'Escalation in the Middle East forces Brent crude to $120+ and 1.5M bpd of supply offline for 60+ days.' },
  { id: 'ukraine-peace',  category: 'Geopolitical', horizon: '3-6 months',  label: 'Ukraine ceasefire and reconstruction',
    scenario: 'Russia and Ukraine reach a durable ceasefire. Western reconstruction funding is announced and European gas supply normalises.' },

  // Market structure
  { id: 'credit-event',   category: 'Market',      horizon: '1-3 months',  label: 'Credit event at a major US regional bank',
    scenario: 'A top-20 US regional bank announces insolvency driven by CRE losses. Contagion fears spread through HY credit and the KRE index.' },
  { id: 'ai-bubble',      category: 'Market',      horizon: '3-6 months',  label: 'AI capex bubble unwinds',
    scenario: 'Hyperscaler AI capex guidance is cut sharply in Q4 calls. Nvidia issues a revenue warning, cascading through the AI supply chain.' },
  { id: 'dollar-collapse', category: 'Market',     horizon: '6-12 months', label: 'USD drops 15% (DXY 88)',
    scenario: 'A sustained loss of confidence drives DXY from ~104 to ~88 over 9 months. Reserve diversification accelerates.' },

  // Secular
  { id: 'china-stimulus', category: 'Secular',     horizon: '3-6 months',  label: 'China unveils large-scale stimulus',
    scenario: 'China announces a ~$1.5T fiscal stimulus focused on property completion, consumer subsidies, and infrastructure.' },
  { id: 'reshoring',      category: 'Secular',     horizon: '1-3 years',   label: 'US manufacturing reshoring accelerates',
    scenario: 'CHIPS Act and IRA execution accelerate dramatically; US semiconductor and clean-tech manufacturing capex doubles vs. plan.' },
];

const TIME_HORIZONS = ['1-2 weeks', '1-3 months', '3-6 months', '6-12 months', '1-3 years'];

// ── Small presentational helpers ────────────────────────────────────────────

function SentimentBadge({ sentiment, magnitude }: { sentiment: Sentiment; magnitude?: Magnitude }) {
  const color = sentiment === 'positive' ? 'text-success bg-success/10 border-success/30'
    : sentiment === 'negative' ? 'text-error bg-error/10 border-error/30'
    : 'text-base-content/70 bg-base-200 border-base-300';
  const Icon = sentiment === 'positive' ? TrendingUp : sentiment === 'negative' ? TrendingDown : Minus;
  const magStr = magnitude === 'strong' ? '•••' : magnitude === 'moderate' ? '••' : magnitude === 'mild' ? '•' : '';
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wide border ${color}`}>
      <Icon className="w-3 h-3" />
      {sentiment}
      {magStr && <span className="opacity-60 ml-0.5">{magStr}</span>}
    </span>
  );
}

function ExposureBadge({ exposure }: { exposure: PortfolioImpact['exposure'] }) {
  const color = exposure === 'high' ? 'bg-error/15 text-error border-error/30'
    : exposure === 'moderate' ? 'bg-warning/15 text-warning border-warning/30'
    : exposure === 'low' ? 'bg-info/15 text-info border-info/30'
    : 'bg-base-200 text-base-content/60 border-base-300';
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wide border ${color}`}>
      {exposure} exposure
    </span>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export default function WhatIfScenarios() {
  const [scenarioText, setScenarioText]   = useState('');
  const [timeHorizon, setTimeHorizon]     = useState('1-3 months');
  const [includePortfolio, setIncPortfolio] = useState(true);
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);

  const [result, setResult]       = useState<WhatIfResult | null>(null);
  const [tradesMap, setTradesMap] = useState<Record<string, SavedStrategyItem[]>>({});
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);

  const applyPreset = (p: Preset) => {
    setScenarioText(p.scenario);
    setTimeHorizon(p.horizon);
    setSelectedPreset(p.id);
  };

  const analyze = async () => {
    if (!scenarioText.trim() || scenarioText.length < 5) {
      setError('Please describe a scenario to analyze (min 5 characters).');
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    setTradesMap({});

    try {
      // Fire the scenario analysis + optionally load user's active trades in parallel.
      // The trades are used to cross-reference portfolio_impacts and surface a
      // clickable "go to trade" link in the results panel.
      const [res, userTrades] = await Promise.all([
        fetch(`${apiBase}/api/stock/market/thematic-impact`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('token')}`,
          },
          body: JSON.stringify({
            scenario_text: scenarioText,
            time_horizon: timeHorizon,
            include_portfolio_impact: includePortfolio,
          }),
        }),
        includePortfolio ? fetchActiveTrades('active').catch(() => [] as SavedStrategyItem[]) : Promise.resolve([] as SavedStrategyItem[]),
      ]);

      if (!res.ok) {
        const e = await res.json();
        let msg = 'Failed to analyze scenario';
        if (e.detail) msg = typeof e.detail === 'string' ? e.detail : e.detail[0]?.msg || msg;
        throw new Error(msg);
      }
      const data: WhatIfResult = await res.json();
      setResult(data);

      // Index trades by ticker so the portfolio panel can link back.
      const byTicker: Record<string, SavedStrategyItem[]> = {};
      userTrades.forEach(t => {
        if (!t.ticker) return;
        const key = t.ticker.toUpperCase();
        (byTicker[key] ||= []).push(t);
      });
      setTradesMap(byTicker);
    } catch (err: any) {
      if (err.message?.includes('OpenAI')) {
        setError('OpenAI API Key is missing or invalid. Please configure it in Settings.');
      } else {
        setError(err.message || 'An unexpected error occurred');
      }
    } finally {
      setLoading(false);
    }
  };

  const presetCategories = Array.from(new Set(PRESETS.map(p => p.category)));

  return (
    <div className="space-y-6">
      {/* Intro */}
      <div className="bg-gradient-to-r from-secondary/10 to-transparent p-5 rounded-2xl border border-secondary/20">
        <div className="flex items-start gap-3">
          <div className="bg-secondary/20 p-2.5 rounded-xl shrink-0">
            <BrainCircuit className="w-5 h-5 text-secondary" />
          </div>
          <div>
            <h2 className="text-lg font-bold">What If Scenario Evaluator</h2>
            <p className="opacity-70 text-sm mt-1">
              Institutional-style stress test. Pick a preset or describe your own scenario —
              the AI blends a live yfinance market snapshot with its training knowledge to produce
              a thesis, direct &amp; second-order impacts, cross-asset reactions, and hedge ideas.
              Optionally scores each of your active trades.
            </p>
          </div>
        </div>
      </div>

      {/* Input panel */}
      <div className="glass-card">
        <div className="p-6 space-y-5">
          {/* Presets */}
          <div>
            <label className="text-xs font-bold uppercase tracking-wider text-base-content/60 flex items-center gap-2 mb-2">
              <Sparkles className="w-3.5 h-3.5" /> Preset Scenarios
            </label>
            <div className="space-y-3">
              {presetCategories.map(cat => (
                <div key={cat}>
                  <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1.5">{cat}</div>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                    {PRESETS.filter(p => p.category === cat).map(p => (
                      <button
                        key={p.id}
                        onClick={() => applyPreset(p)}
                        className={`text-left text-xs px-3 py-2 rounded-xl border transition-all
                          ${selectedPreset === p.id
                            ? 'bg-secondary/15 border-secondary/50 text-secondary'
                            : 'bg-base-200/50 border-white/[0.06] hover:border-secondary/30 hover:bg-secondary/5'}`}
                      >
                        <div className="font-semibold">{p.label}</div>
                        <div className="text-[10px] opacity-60 mt-0.5">{p.horizon}</div>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Scenario text */}
          <div className="form-control w-full">
            <label className="label pb-1">
              <span className="label-text font-bold text-sm flex items-center gap-2">
                <BrainCircuit className="w-4 h-4 text-secondary" /> Scenario
              </span>
              <span className="label-text-alt text-[10px] opacity-50">Free-form or edit the preset above</span>
            </label>
            <textarea
              className="textarea textarea-bordered h-24 font-serif text-base"
              placeholder='e.g., "The Fed cuts 200bps over 3 meetings while QT is paused" or describe any hypothetical…'
              value={scenarioText}
              onChange={e => { setScenarioText(e.target.value); setSelectedPreset(null); }}
            />
          </div>

          {/* Horizon + options */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="label pb-1">
                <span className="label-text font-bold text-sm flex items-center gap-2">
                  <Clock className="w-4 h-4 text-info" /> Time Horizon
                </span>
              </label>
              <div className="flex flex-wrap gap-1.5">
                {TIME_HORIZONS.map(h => (
                  <button
                    key={h}
                    onClick={() => setTimeHorizon(h)}
                    className={`text-xs px-3 py-1.5 rounded-lg border transition-colors
                      ${timeHorizon === h
                        ? 'bg-primary/15 text-primary border-primary/40'
                        : 'bg-base-200/50 text-base-content/60 border-white/[0.06] hover:bg-primary/5 hover:border-primary/20'}`}
                  >
                    {h}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="label pb-1">
                <span className="label-text font-bold text-sm flex items-center gap-2">
                  <Briefcase className="w-4 h-4 text-warning" /> Options
                </span>
              </label>
              <label className="flex items-center gap-2.5 text-sm cursor-pointer px-3 py-2 rounded-lg border border-white/[0.06] bg-base-200/50 hover:bg-warning/5 hover:border-warning/20">
                <input
                  type="checkbox"
                  className="checkbox checkbox-sm checkbox-warning"
                  checked={includePortfolio}
                  onChange={e => setIncPortfolio(e.target.checked)}
                />
                <span>Score impact on my active trades</span>
              </label>
            </div>
          </div>

          {/* Run */}
          <div className="flex justify-end">
            <button className="btn btn-secondary" onClick={analyze} disabled={loading}>
              {loading
                ? <><span className="loading loading-spinner"></span> Running institutional analysis…</>
                : <><Zap className="w-5 h-5" /> Run Analysis</>}
            </button>
          </div>

          {error && (
            <div className="alert alert-error text-sm">
              <span className="font-bold">Error:</span> {error}
            </div>
          )}
        </div>
      </div>

      {/* ============================== RESULTS ============================== */}
      {result?.analysis && (
        <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
          <ResultPanels result={result} tradesMap={tradesMap} />
        </div>
      )}
    </div>
  );
}

// ── Results rendering (split out for readability) ───────────────────────────

function ResultPanels({
  result, tradesMap,
}: { result: WhatIfResult; tradesMap: Record<string, SavedStrategyItem[]> }) {
  const a = result.analysis;
  const snap = result.market_snapshot;

  return (
    <>
      {/* Executive thesis card */}
      <div className="glass-card border-l-4 border-l-secondary">
        <div className="p-6 space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <h3 className="font-bold text-base flex items-center gap-2">
              <Activity className="w-5 h-5 text-secondary" /> Executive Thesis
            </h3>
            {a.scenario_probability_pct != null && (
              <span className="badge badge-outline badge-sm gap-1 font-bold">
                <GitBranch className="w-3 h-3" /> Probability {a.scenario_probability_pct}%
              </span>
            )}
            {a.time_horizon_estimate && (
              <span className="badge badge-outline badge-sm gap-1">
                <Clock className="w-3 h-3" /> {a.time_horizon_estimate}
              </span>
            )}
          </div>
          <p className="text-sm leading-relaxed font-serif opacity-90">{a.executive_thesis}</p>

          {a.historical_analogue && (
            <div className="bg-base-200/40 border border-white/[0.04] rounded-xl p-3">
              <div className="text-[10px] uppercase tracking-wider text-secondary/70 font-bold mb-1 flex items-center gap-1">
                <LineChartIcon className="w-3 h-3" /> Historical Analogue
              </div>
              <p className="text-xs opacity-80 leading-relaxed">{a.historical_analogue}</p>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {a.key_assumptions && a.key_assumptions.length > 0 && (
              <BulletCard title="Key Assumptions" icon={<CheckCircle2 className="w-3.5 h-3.5" />} tone="info" items={a.key_assumptions} />
            )}
            {a.key_catalysts && a.key_catalysts.length > 0 && (
              <BulletCard title="Catalysts to Watch" icon={<Sparkles className="w-3.5 h-3.5" />} tone="success" items={a.key_catalysts} />
            )}
            {a.invalidation_signals && a.invalidation_signals.length > 0 && (
              <BulletCard title="Invalidation Signals" icon={<XCircle className="w-3.5 h-3.5" />} tone="error" items={a.invalidation_signals} />
            )}
          </div>
        </div>
      </div>

      {/* Market snapshot strip (grounded context) */}
      {snap && Object.keys(snap).length > 0 && (
        <div className="rounded-2xl border border-white/[0.04] bg-base-200/30 p-3">
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-bold mb-2 flex items-center gap-1">
            <Activity className="w-3 h-3" /> Live Market Snapshot Used
          </div>
          <div className="flex flex-wrap gap-2 text-[11px]">
            {Object.entries(snap).map(([tk, info]) => (
              <div key={tk} className="px-2 py-1 rounded-lg bg-base-100/60 border border-white/[0.04] flex items-center gap-1.5">
                <span className="font-mono font-bold">{tk}</span>
                <span className="opacity-70">{info.last}</span>
                <span className={info.change_pct_5d >= 0 ? 'text-success' : 'text-error'}>
                  {info.change_pct_5d >= 0 ? '+' : ''}{info.change_pct_5d.toFixed(2)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cross-asset reactions */}
      {a.cross_asset_reactions && (
        <div>
          <h3 className="text-base font-bold flex items-center gap-2 mb-3 border-b border-base-200 pb-2">
            <GitBranch className="w-4 h-4 text-info" /> Cross-Asset Reactions
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(a.cross_asset_reactions).map(([k, v]) => v ? (
              <div key={k} className="p-3 rounded-xl border border-white/[0.06] bg-base-100/50">
                <div className="text-[10px] uppercase tracking-wider text-base-content/40 font-bold mb-1">
                  {k.replace(/_/g, ' ')}
                </div>
                <div className="text-xs opacity-90">{v}</div>
              </div>
            ) : null)}
          </div>
        </div>
      )}

      {/* Portfolio impact — only when user opted in and LLM returned data */}
      {a.portfolio_impacts && a.portfolio_impacts.length > 0 && (
        <div>
          <h3 className="text-base font-bold flex items-center gap-2 mb-3 border-b border-base-200 pb-2">
            <Briefcase className="w-4 h-4 text-warning" /> Impact on Your Active Trades
            <span className="text-[10px] text-base-content/40 font-normal">
              ({a.portfolio_impacts.length} positions scored)
            </span>
          </h3>
          <div className="space-y-2">
            {a.portfolio_impacts.map((imp, i) => {
              const matches = tradesMap[imp.ticker?.toUpperCase()] || [];
              return (
                <div key={i} className={`rounded-xl border p-3 flex flex-col sm:flex-row sm:items-start sm:gap-4 gap-2
                  ${imp.exposure === 'high' ? 'border-error/30 bg-error/5'
                    : imp.exposure === 'moderate' ? 'border-warning/30 bg-warning/5'
                    : imp.exposure === 'low' ? 'border-info/20 bg-info/5'
                    : 'border-white/[0.06] bg-base-200/30'}`}>
                  <div className="flex items-center gap-2 shrink-0 sm:w-56">
                    <Link to={`/dashboard?ticker=${imp.ticker}`} className="font-bold font-mono hover:underline">
                      {imp.ticker}
                    </Link>
                    <SentimentBadge sentiment={imp.direction} />
                    <ExposureBadge exposure={imp.exposure} />
                  </div>
                  <div className="flex-1 text-xs opacity-85 leading-relaxed">
                    {imp.estimated_impact_pct != null && (
                      <span className={`font-bold font-mono mr-2 ${imp.estimated_impact_pct >= 0 ? 'text-success' : 'text-error'}`}>
                        {imp.estimated_impact_pct >= 0 ? '+' : ''}{imp.estimated_impact_pct.toFixed(1)}%
                      </span>
                    )}
                    {imp.reasoning}
                    {matches.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {matches.map(t => (
                          <Link
                            key={t.id}
                            to="/my-trades"
                            className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-md bg-base-100/60 border border-white/[0.06] hover:border-warning/30 hover:text-warning"
                          >
                            <ClipboardList className="w-2.5 h-2.5" />
                            {t.strategy_type?.replace(/_/g, ' ')} trade #{t.id}
                            <ChevronRight className="w-2.5 h-2.5" />
                          </Link>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Sector impacts */}
      {a.sectors && a.sectors.length > 0 && (
        <div>
          <h3 className="text-base font-bold flex items-center gap-2 mb-3 border-b border-base-200 pb-2">
            <Layers className="w-4 h-4 text-secondary" /> Sector Impacts
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {a.sectors.map((s, i) => (
              <div key={i} className={`p-3 rounded-xl border
                ${s.sentiment === 'positive' ? 'border-success/30 bg-success/5'
                  : s.sentiment === 'negative' ? 'border-error/30 bg-error/5'
                  : 'border-base-200 bg-base-100/40'}`}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-semibold text-sm">{s.name}</span>
                  <SentimentBadge sentiment={s.sentiment} magnitude={s.magnitude} />
                </div>
                <p className="text-[11px] opacity-80 leading-relaxed">{s.reason}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stock impacts */}
      {a.stocks && a.stocks.length > 0 && (
        <div>
          <h3 className="text-base font-bold flex items-center gap-2 mb-3 border-b border-base-200 pb-2">
            <Target className="w-4 h-4 text-warning" /> Named Stock Exposures
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {a.stocks.map((s, i) => (
              <div key={i} className={`p-3 rounded-xl border
                ${s.sentiment === 'positive' ? 'border-success/30 bg-success/5'
                  : s.sentiment === 'negative' ? 'border-error/30 bg-error/5'
                  : 'border-base-200 bg-base-100/40'}`}>
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <Link to={`/dashboard?ticker=${s.ticker}`} className="font-bold font-mono text-sm hover:underline">
                    {s.ticker} ↗
                  </Link>
                  <div className="flex items-center gap-1.5">
                    {s.estimated_move_pct != null && (
                      <span className={`text-[11px] font-bold font-mono ${s.estimated_move_pct >= 0 ? 'text-success' : 'text-error'}`}>
                        {s.estimated_move_pct >= 0 ? '+' : ''}{s.estimated_move_pct.toFixed(1)}%
                      </span>
                    )}
                    <SentimentBadge sentiment={s.sentiment} magnitude={s.magnitude} />
                  </div>
                </div>
                <p className="text-[11px] opacity-80 leading-relaxed">{s.reason}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ETF exposures */}
      {a.etfs && a.etfs.length > 0 && (
        <div>
          <h3 className="text-base font-bold flex items-center gap-2 mb-3 border-b border-base-200 pb-2">
            <Layers className="w-4 h-4 text-info" /> ETF Exposures
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {a.etfs.map((e, i) => (
              <div key={i} className={`p-3 rounded-xl border
                ${e.sentiment === 'positive' ? 'border-success/30 bg-success/5'
                  : e.sentiment === 'negative' ? 'border-error/30 bg-error/5'
                  : 'border-base-200 bg-base-100/40'}`}>
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <Link to={`/dashboard?ticker=${e.ticker}`} className="font-bold font-mono text-sm hover:underline">
                    {e.ticker} ↗
                  </Link>
                  <SentimentBadge sentiment={e.sentiment} magnitude={e.magnitude} />
                </div>
                <p className="text-[11px] opacity-80 leading-relaxed">{e.reason}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Second-order effects */}
      {a.second_order_effects && a.second_order_effects.length > 0 && (
        <div>
          <h3 className="text-base font-bold flex items-center gap-2 mb-3 border-b border-base-200 pb-2">
            <GitBranch className="w-4 h-4 text-accent" /> Second-Order Effects
          </h3>
          <div className="space-y-2">
            {a.second_order_effects.map((s, i) => (
              <div key={i} className="p-3 rounded-xl border border-white/[0.06] bg-base-100/50">
                <div className="text-sm font-semibold mb-0.5">{s.area}</div>
                <p className="text-xs opacity-80 leading-relaxed">{s.description}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Suggested hedges */}
      {a.suggested_hedges && a.suggested_hedges.length > 0 && (
        <div>
          <h3 className="text-base font-bold flex items-center gap-2 mb-3 border-b border-base-200 pb-2">
            <Shield className="w-4 h-4 text-success" /> Suggested Hedges
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {a.suggested_hedges.map((h, i) => (
              <div key={i} className="p-3 rounded-xl border border-success/20 bg-success/5">
                <div className="flex items-center gap-2 mb-1">
                  <span className="badge badge-success badge-sm font-bold uppercase">{h.action}</span>
                  <span className="font-mono font-bold text-sm">{h.instrument}</span>
                </div>
                <p className="text-[11px] opacity-80 leading-relaxed">{h.rationale}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 text-[10px] text-base-content/30 italic border-t border-white/[0.04] pt-3">
        <AlertTriangle className="w-3 h-3" />
        Scenario analysis is for research purposes only. Not investment advice. LLM output may be incorrect.
      </div>
    </>
  );
}

function BulletCard({
  title, items, icon, tone,
}: {
  title: string;
  items: string[];
  icon: React.ReactNode;
  tone: 'info' | 'success' | 'error';
}) {
  const ring = tone === 'info' ? 'border-info/20 bg-info/5 text-info'
    : tone === 'success' ? 'border-success/20 bg-success/5 text-success'
    : 'border-error/20 bg-error/5 text-error';
  return (
    <div className={`p-3 rounded-xl border ${ring}`}>
      <div className="text-[10px] uppercase tracking-wider font-bold mb-1.5 flex items-center gap-1">
        {icon} {title}
      </div>
      <ul className="text-xs space-y-1 opacity-90 text-base-content list-disc ml-4">
        {items.slice(0, 6).map((x, i) => <li key={i}>{x}</li>)}
      </ul>
    </div>
  );
}
