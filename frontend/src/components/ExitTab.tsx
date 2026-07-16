import React, { useState, useCallback } from 'react';
import {
  Crosshair, Loader2, ShieldAlert, RefreshCw,
  Database, Activity, Brain, Globe, TrendingUp, Settings, Shield, BarChart3,
  DollarSign, Calendar, ArrowRightLeft, AlertTriangle, CheckCircle2,
  ChevronRight, Info, Zap, Target, Gauge,
} from 'lucide-react';
import { fetchExitAnalysis } from '../api';
import type { ExitAnalysisData, ExitSignalSummary } from '../types';
import {
  ExitSignalGauge, TechnicalSignals, PillarCard,
  PillarFundamental, PillarMacro, PillarStructural, PillarGeopolitical,
  PillarValuation, PillarSentiment, PillarSectorRotation, OptionsProtection, RiskDashboard,
} from './exit';

/* ---- Tooltip helper ---- */
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <span className="relative group/tip inline-flex items-center">
      {children}
      <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 rounded-xl bg-neutral/95 backdrop-blur-sm text-neutral-content text-[11px] whitespace-normal opacity-0 group-hover/tip:opacity-100 transition-opacity duration-200 z-50 shadow-xl shadow-black/30 max-w-[260px] text-center leading-relaxed border border-white/[0.06]">
        {text}
      </span>
    </span>
  );
}

/* ---- Pillar Heat Bar (mini score bar for overview) ---- */
function PillarHeatBar({ name, score, icon }: { name: string; score: number; icon: React.ReactNode }) {
  const color = score >= 60 ? 'bg-error' : score >= 40 ? 'bg-warning' : score >= 25 ? 'bg-info' : 'bg-success';
  const textColor = score >= 60 ? 'text-error' : score >= 40 ? 'text-warning' : score >= 25 ? 'text-info' : 'text-success';
  return (
    <div className="flex items-center gap-3 group/bar">
      <span className="w-5 h-5 flex-shrink-0 opacity-60 group-hover/bar:opacity-100 transition-opacity">{icon}</span>
      <span className="text-xs font-medium w-28 truncate text-base-content/70 group-hover/bar:text-base-content transition-colors">{name}</span>
      <div className="flex-1 bg-base-300/50 rounded-full h-2 min-w-[60px] overflow-hidden">
        <div className={`h-2 rounded-full ${color} transition-all duration-700 ease-out`} style={{ width: `${score}%` }} />
      </div>
      <span className={`text-xs font-bold w-8 text-right tabular-nums ${textColor}`}>{score}</span>
    </div>
  );
}

/* ---- Dimension Score Card (NEW — for 3-dimension ratings) ---- */
function DimensionCard({ label, score, sublabel, weight, icon }: {
  label: string; score: number; sublabel: string; weight: number; icon: React.ReactNode;
}) {
  const color = score >= 70 ? 'text-error' : score >= 55 ? 'text-warning' : score >= 40 ? 'text-info' : score >= 22 ? 'text-base-content' : 'text-success';
  const bgColor = score >= 70 ? 'bg-error/10 border-error/20' : score >= 55 ? 'bg-warning/10 border-warning/20' : score >= 40 ? 'bg-info/10 border-info/20' : score >= 22 ? 'bg-base-200/60 border-white/[0.06]' : 'bg-success/10 border-success/20';
  const badgeColor = score >= 70 ? 'badge-error' : score >= 55 ? 'badge-warning' : score >= 40 ? 'badge-info' : score >= 22 ? 'badge-ghost' : 'badge-success';

  return (
    <div className={`rounded-2xl p-4 border transition-all duration-300 hover:scale-[1.02] ${bgColor}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="opacity-60">{icon}</span>
        <span className="text-xs font-semibold uppercase tracking-wider text-base-content/50">{label}</span>
        <span className="text-[10px] text-base-content/30 ml-auto font-medium">{(weight * 100).toFixed(0)}% weight</span>
      </div>
      <div className={`text-3xl font-black tracking-tighter ${color} tabular-nums`}>
        {score}
      </div>
      <span className={`badge badge-sm ${badgeColor} mt-1.5 font-semibold`}>
        {sublabel}
      </span>
    </div>
  );
}

/* ---- Signal Summary Component ---- */
function SignalSummaryCard({ summary, ticker }: { summary: ExitSignalSummary; ticker: string }) {
  return (
    <div className="glass-card p-5 sm:p-6">
      {/* Narrative heading */}
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-xl bg-primary/15 flex items-center justify-center flex-shrink-0">
          <Zap className="w-4.5 h-4.5 text-primary" />
        </div>
        <div>
          <h3 className="font-bold text-sm mb-1">Signal Intelligence</h3>
          <p className="text-sm text-base-content/70 leading-relaxed">
            {summary.overall_narrative}
          </p>
        </div>
      </div>

      {/* Detail narratives */}
      {summary.detail_narratives.length > 0 && (
        <div className="mt-4 space-y-2 pl-12">
          {summary.detail_narratives.map((n, i) => (
            <div key={i} className="flex items-start gap-2">
              <ChevronRight className="w-3.5 h-3.5 text-base-content/30 mt-0.5 flex-shrink-0" />
              <p className="text-xs text-base-content/60 leading-relaxed">{n}</p>
            </div>
          ))}
        </div>
      )}

      <div className="border-t border-white/[0.04] my-4" />

      {/* Key Drivers + Strengths */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <div>
          <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/40 mb-2.5 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 text-warning" />
            Key Concerns
          </h4>
          {summary.key_drivers.length > 0 ? (
            <div className="space-y-2">
              {summary.key_drivers.map((d, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className={`badge badge-xs ${d.severity === 'high' ? 'badge-error' : 'badge-warning'} tabular-nums`}>
                    {d.score}
                  </span>
                  <span className="text-xs text-base-content/70">{d.pillar}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-success/80 flex items-center gap-1.5">
              <CheckCircle2 className="w-3.5 h-3.5" /> No significant concerns
            </p>
          )}
        </div>

        <div>
          <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/40 mb-2.5 flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5 text-success" />
            Strengths
          </h4>
          {summary.strengths.length > 0 ? (
            <div className="space-y-2">
              {summary.strengths.map((s, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="badge badge-xs badge-success tabular-nums">{s.score}</span>
                  <span className="text-xs text-base-content/70">{s.pillar}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-base-content/40">No exceptional strengths identified</p>
          )}
        </div>
      </div>

      {/* Quantitative breakdown */}
      <div className="mt-4 bg-base-200/30 rounded-xl p-4 border border-white/[0.03]">
        <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/40 mb-3 flex items-center gap-1.5">
          <BarChart3 className="w-3.5 h-3.5" />
          Score Decomposition
        </h4>
        <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
          <Tooltip text="Average score across all 7 analysis pillars (weighted 50% in overall)">
            <div className="text-center">
              <div className="text-lg font-black tabular-nums">{summary.quantitative.pillar_avg}</div>
              <div className="text-[10px] text-base-content/40 mt-0.5">Pillar Avg</div>
            </div>
          </Tooltip>
          <Tooltip text="Average of swing + long-term technical signals (weighted 25% in overall)">
            <div className="text-center">
              <div className={`text-lg font-black tabular-nums ${summary.quantitative.tech_avg >= 55 ? 'text-error' : summary.quantitative.tech_avg >= 35 ? 'text-warning' : 'text-success'}`}>
                {summary.quantitative.tech_avg}
              </div>
              <div className="text-[10px] text-base-content/40 mt-0.5">Tech Avg</div>
            </div>
          </Tooltip>
          <Tooltip text="Short-term momentum: RSI, MACD, Bollinger, EMA, Volume">
            <div className="text-center">
              <div className={`text-lg font-black tabular-nums ${(summary.quantitative.swing_score ?? 0) >= 55 ? 'text-error' : (summary.quantitative.swing_score ?? 0) >= 35 ? 'text-warning' : 'text-success'}`}>
                {summary.quantitative.swing_score ?? '—'}
              </div>
              <div className="text-[10px] text-base-content/40 mt-0.5">Swing</div>
            </div>
          </Tooltip>
          <Tooltip text="Long-term trend: SMA cross, fundamental health, valuation, analyst consensus">
            <div className="text-center">
              <div className={`text-lg font-black tabular-nums ${(summary.quantitative.longterm_score ?? 0) >= 55 ? 'text-error' : (summary.quantitative.longterm_score ?? 0) >= 35 ? 'text-warning' : 'text-success'}`}>
                {summary.quantitative.longterm_score ?? '—'}
              </div>
              <div className="text-[10px] text-base-content/40 mt-0.5">Long-term</div>
            </div>
          </Tooltip>
          <Tooltip text="The pillar contributing most to exit signals">
            <div className="text-center">
              <div className="text-lg font-black text-warning tabular-nums">{summary.quantitative.highest_pillar.score}</div>
              <div className="text-[10px] text-base-content/40 mt-0.5 truncate">Top: {summary.quantitative.highest_pillar.name}</div>
            </div>
          </Tooltip>
          <Tooltip text="The pillar showing the strongest hold signal">
            <div className="text-center">
              <div className="text-lg font-black text-success tabular-nums">{summary.quantitative.lowest_pillar.score}</div>
              <div className="text-[10px] text-base-content/40 mt-0.5 truncate">Best: {summary.quantitative.lowest_pillar.name}</div>
            </div>
          </Tooltip>
        </div>
      </div>
    </div>
  );
}
interface ExitTabProps {
  ticker: string;
  currentPrice: number;
}

export function ExitTab({ ticker, currentPrice }: ExitTabProps) {
  const [isInitialLoad, setIsInitialLoad] = React.useState(true);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<ExitAnalysisData | null>(null);
  const [activeSection, setActiveSection] = useState<'overview' | 'pillars' | 'technical' | 'risk'>('overview');


  const loadAnalysis = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchExitAnalysis(ticker);
      if (result.error) throw new Error(result.error);
      setData(result);
      setActiveSection('overview');
    } catch (err: any) {
      setError(err.message || 'Failed to fetch exit analysis.');
    } finally {
      setLoading(false);
      setIsInitialLoad(false);
    }
  }, [ticker]);

  React.useEffect(() => {
    if (isInitialLoad && !data && !loading) {
      loadAnalysis();
    }
  }, [isInitialLoad, data, loading, loadAnalysis]);



  /* ---- Loading state ---- */
  if (loading && !data) {
    return (
      <div className="empty-state">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-error/20 to-warning/10 flex items-center justify-center animate-pulse-slow">
          <span className="loading loading-spinner loading-lg text-error" />
        </div>
        <p className="text-base-content/50 mt-6 text-sm font-medium">Computing exit analysis for {ticker}...</p>
        <p className="text-base-content/30 text-xs mt-1">Analyzing 7 pillars, technicals, options, and risk metrics</p>
      </div>
    );
  }

  if (!data) return null;

  const pillarList = [
    { key: 'fundamental', name: 'Fundamental', icon: <Database className="w-4 h-4 text-warning" />, score: data.pillars.fundamental.score },
    { key: 'macro', name: 'Macro', icon: <Activity className="w-4 h-4 text-error" />, score: data.pillars.macro.score },
    { key: 'structural', name: 'Structural', icon: <Brain className="w-4 h-4 text-primary" />, score: data.pillars.structural.score },
    { key: 'geopolitical', name: 'Geopolitical', icon: <Globe className="w-4 h-4 text-error" />, score: data.pillars.geopolitical.score },
    { key: 'valuation', name: 'Valuation', icon: <TrendingUp className="w-4 h-4 text-success" />, score: data.pillars.valuation.score },
    { key: 'sentiment', name: 'Sentiment', icon: <Settings className="w-4 h-4 text-info" />, score: data.pillars.sentiment.score },
    { key: 'sector_rotation', name: 'Sector Rotation', icon: <ArrowRightLeft className="w-4 h-4 text-secondary" />, score: data.pillars.sector_rotation.score },
  ];

  const sectionTabs = [
    { key: 'overview' as const, label: 'Overview', icon: <Target className="w-3.5 h-3.5" /> },
    { key: 'pillars' as const, label: '7 Pillars', icon: <Shield className="w-3.5 h-3.5" /> },
    { key: 'technical' as const, label: 'Technical', icon: <BarChart3 className="w-3.5 h-3.5" /> },
    { key: 'risk' as const, label: 'Risk & Options', icon: <ShieldAlert className="w-3.5 h-3.5" /> },
  ];

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex justify-end mb-2">
        <button className="btn btn-ghost btn-sm rounded-xl gap-1.5 border border-white/[0.06]" onClick={loadAnalysis} disabled={loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          Refresh
        </button>
      </div>

      {/* Score + Position Summary */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        
        <ExitSignalGauge score={data.overall_score} label={data.overall_label} />
      </div>

      {/* 3-Dimension Scores (NEW) */}
      {data.dimension_scores && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <DimensionCard
            label="7 Pillars"
            score={data.dimension_scores.pillars.score}
            sublabel={data.dimension_scores.pillars.label}
            weight={data.dimension_scores.pillars.weight}
            icon={<Shield className="w-4 h-4 text-primary" />}
          />
          <DimensionCard
            label="Technical"
            score={data.dimension_scores.technical.score}
            sublabel={data.dimension_scores.technical.label}
            weight={data.dimension_scores.technical.weight}
            icon={<BarChart3 className="w-4 h-4 text-warning" />}
          />
          <DimensionCard
            label="Risk & Options"
            score={data.dimension_scores.risk.score}
            sublabel={data.dimension_scores.risk.label}
            weight={data.dimension_scores.risk.weight}
            icon={<ShieldAlert className="w-4 h-4 text-error" />}
          />
        </div>
      )}

      {/* Section Tabs */}
      <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-hide py-1">
        {sectionTabs.map((t) => (
          <button
            key={t.key}
            className={`
              flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold
              whitespace-nowrap transition-all duration-200 border flex-shrink-0
              ${activeSection === t.key
                ? 'bg-primary/15 text-primary border-primary/20 shadow-sm shadow-primary/10'
                : 'border-transparent text-base-content/50 hover:text-base-content hover:bg-base-200/50'}
            `}
            onClick={() => setActiveSection(t.key)}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Error */}
      {error && (
        <div className="glass-card p-3 border-warning/20 flex items-center gap-2">
          <ShieldAlert className="w-4 h-4 text-warning" />
          <span className="text-sm text-warning">{error}</span>
        </div>
      )}

      {/* ====== OVERVIEW TAB ====== */}
      {activeSection === 'overview' && (
        <div className="space-y-5 animate-fade-in">
          {data.signal_summary && (
            <SignalSummaryCard summary={data.signal_summary} ticker={ticker} />
          )}

          {/* Pillar Heatmap */}
          <div className="glass-card p-5">
            <h3 className="font-bold text-sm mb-4 flex items-center gap-2">
              <Shield className="w-4 h-4 text-primary" />
              Pillar Scores
              <Tooltip text="Each pillar scores 0-100. Lower is healthier. Higher means exit pressure.">
                <Info className="w-3.5 h-3.5 text-base-content/30 cursor-help" />
              </Tooltip>
            </h3>
            <div className="space-y-2.5">
              {pillarList.map((p) => (
                <PillarHeatBar key={p.key} name={p.name} score={p.score} icon={p.icon} />
              ))}
            </div>
            <div className="flex flex-wrap gap-4 mt-4 pt-3 border-t border-white/[0.04]">
              <span className="text-[10px] flex items-center gap-1.5 text-base-content/40"><span className="w-2 h-2 rounded-full bg-success" /> 0–24 Healthy</span>
              <span className="text-[10px] flex items-center gap-1.5 text-base-content/40"><span className="w-2 h-2 rounded-full bg-info" /> 25–39 Low Risk</span>
              <span className="text-[10px] flex items-center gap-1.5 text-base-content/40"><span className="w-2 h-2 rounded-full bg-warning" /> 40–59 Moderate</span>
              <span className="text-[10px] flex items-center gap-1.5 text-base-content/40"><span className="w-2 h-2 rounded-full bg-error" /> 60+ High Risk</span>
            </div>
          </div>

          {/* Technical Snapshot */}
          <div className="glass-card p-5">
            <h3 className="font-bold text-sm mb-3 flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-warning" />
              Technical Snapshot
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              {[
                { label: 'Swing Score', value: data.technical_signals.swing.composite_score, tooltip: 'Short-term momentum: RSI, MACD, Bollinger, EMA, Volume' },
                { label: 'Long-term', value: data.technical_signals.longterm.composite_score, tooltip: 'Long-term trend: SMA cross, fundamentals, analyst consensus' },
                { label: 'RSI', value: data.technical_signals.swing.rsi_value, tooltip: 'Relative Strength Index — overbought/oversold indicator', isRaw: true },
                { label: 'SMA Signal', value: data.technical_signals.longterm.sma_cross_label, tooltip: 'Moving average trend direction', isText: true },
              ].map((item, i) => (
                <Tooltip key={i} text={item.tooltip}>
                  <div className="metric-card w-full">
                    {item.isText ? (
                      <div className="text-xs font-bold leading-tight text-base-content/80">{item.value}</div>
                    ) : (
                      <div className={`metric-value ${!item.isRaw && typeof item.value === 'number' ? (item.value >= 60 ? 'text-error' : item.value >= 40 ? 'text-warning' : 'text-success') : ''}`}>
                        {item.value}
                      </div>
                    )}
                    <div className="metric-label">{item.label}</div>
                  </div>
                </Tooltip>
              ))}
            </div>
            <button className="btn btn-ghost btn-sm rounded-xl mt-3 gap-1 text-xs" onClick={() => setActiveSection('technical')}>
              View detailed technicals <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Risk Snapshot */}
          <div className="glass-card p-5">
            <h3 className="font-bold text-sm mb-3 flex items-center gap-2">
              <Shield className="w-4 h-4 text-error" />
              Risk Snapshot
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
              {[
                { label: 'Beta', value: data.risk.beta?.toFixed(2) ?? 'N/A', tooltip: 'Market sensitivity. >1.5 = high risk' },
                { label: 'Volatility', value: data.risk.annualized_volatility != null ? `${data.risk.annualized_volatility.toFixed(1)}%` : 'N/A', tooltip: 'Annualized price volatility' },
                { label: 'Max DD', value: data.risk.max_drawdown_1y != null ? `${data.risk.max_drawdown_1y.toFixed(1)}%` : 'N/A', tooltip: 'Largest decline over past year', isError: true },
                { label: 'Sharpe', value: data.risk.sharpe_ratio_1y?.toFixed(2) ?? 'N/A', tooltip: 'Risk-adjusted return. >1.0 is good' },
                { label: 'Sortino', value: data.risk.sortino_ratio_1y?.toFixed(2) ?? 'N/A', tooltip: 'Downside risk-adjusted return' },
              ].map((item, i) => (
                <Tooltip key={i} text={item.tooltip}>
                  <div className="metric-card w-full">
                    <div className={`text-xl font-black tabular-nums ${item.isError ? 'text-error' : ''}`}>
                      {item.value}
                    </div>
                    <div className="metric-label">{item.label}</div>
                  </div>
                </Tooltip>
              ))}
            </div>
            <button className="btn btn-ghost btn-sm rounded-xl mt-3 gap-1 text-xs" onClick={() => setActiveSection('risk')}>
              View full risk analysis <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      )}

      {/* ====== 7 PILLARS TAB ====== */}
      {activeSection === 'pillars' && (
        <div className="space-y-4 animate-fade-in">
          <h3 className="text-lg font-bold flex items-center gap-2">
            <Shield className="w-5 h-5 text-primary" />
            Exit Decision Pillars
            <Tooltip text="Each pillar independently measures a specific dimension of exit risk. Combined into the overall signal.">
              <Info className="w-4 h-4 text-base-content/30 cursor-help" />
            </Tooltip>
          </h3>

          <PillarCard title="Fundamental Deterioration" icon={<Database className="w-5 h-5 text-warning" />} score={data.pillars.fundamental.score} description="Revenue, margins, earnings quality, and balance sheet health" ticker={ticker} pillarKey="Fundamental">
            <PillarFundamental data={data.pillars.fundamental.data} chartData={data.pillars.fundamental.chart_data} />
          </PillarCard>

          <PillarCard title="Macroeconomic & Policy" icon={<Activity className="w-5 h-5 text-error" />} score={data.pillars.macro.score} description="Beta, sector cyclicality, volatility regime" ticker={ticker} pillarKey="Macroeconomic">
            <PillarMacro data={data.pillars.macro.data} />
          </PillarCard>

          <PillarCard title="Structural Obsolescence" icon={<Brain className="w-5 h-5 text-primary" />} score={data.pillars.structural.score} description="R&D investment, CapEx trends, industry trajectory" ticker={ticker} pillarKey="Structural">
            <PillarStructural data={data.pillars.structural.data} chartData={data.pillars.structural.chart_data} />
          </PillarCard>

          <PillarCard title="Geopolitical & Regulatory" icon={<Globe className="w-5 h-5 text-error" />} score={data.pillars.geopolitical.score} description="News sentiment, regulatory themes, sector risk" ticker={ticker} pillarKey="Geopolitical">
            <PillarGeopolitical data={data.pillars.geopolitical.data} />
          </PillarCard>

          <PillarCard title="Valuation Extremes" icon={<TrendingUp className="w-5 h-5 text-success" />} score={data.pillars.valuation.score} description="Sector-relative PE, PEG, EV/EBITDA, analyst targets" ticker={ticker} pillarKey="Valuation">
            <PillarValuation data={data.pillars.valuation.data} currentPrice={currentPrice} />
          </PillarCard>

          <PillarCard title="Market Sentiment & Flow" icon={<Settings className="w-5 h-5 text-info" />} score={data.pillars.sentiment.score} description="Insider selling, short interest spikes, institutional flow" ticker={ticker} pillarKey="Sentiment">
            <PillarSentiment data={data.pillars.sentiment.data} />
          </PillarCard>

          <PillarCard title="Sector Rotation" icon={<ArrowRightLeft className="w-5 h-5 text-secondary" />} score={data.pillars.sector_rotation.score} description="Fund flows, relative strength vs market" ticker={ticker} pillarKey="Sector Rotation">
            <PillarSectorRotation data={data.pillars.sector_rotation.data} />
          </PillarCard>
        </div>
      )}

      {/* ====== TECHNICAL TAB ====== */}
      {activeSection === 'technical' && (
        <div className="space-y-4 animate-fade-in">
          <h3 className="text-lg font-bold flex items-center gap-2">
            <BarChart3 className="w-5 h-5 text-warning" />
            Technical Exit Signals
            <Tooltip text="Technical indicators from price action, volume, and moving averages.">
              <Info className="w-4 h-4 text-base-content/30 cursor-help" />
            </Tooltip>
          </h3>
          <TechnicalSignals
            swing={data.technical_signals.swing}
            longterm={data.technical_signals.longterm}
            priceChart={data.price_chart}
            costBasis={undefined}
          />
        </div>
      )}

      {/* ====== RISK & OPTIONS TAB ====== */}
      {activeSection === 'risk' && (
        <div className="space-y-4 animate-fade-in">
          <h3 className="text-lg font-bold flex items-center gap-2">
            <Shield className="w-5 h-5 text-error" />
            Risk Metrics & Hedging
            <Tooltip text="Comprehensive risk analysis including VaR, CVaR, tail risk, and options hedging.">
              <Info className="w-4 h-4 text-base-content/30 cursor-help" />
            </Tooltip>
          </h3>
          <RiskDashboard risk={data.risk} liquidity={data.liquidity} />

          <h4 className="text-md font-bold flex items-center gap-2 mt-4">
            <Shield className="w-4 h-4 text-info" />
            Options Protection
          </h4>
          <OptionsProtection data={data.options_protection} />
        </div>
      )}

      {/* Disclaimer */}
      <div className="text-[10px] text-base-content/25 text-center py-3 uppercase tracking-wider">
        Exit analysis is for informational purposes only and does not constitute investment advice.
      </div>
    </div>
  );
}
