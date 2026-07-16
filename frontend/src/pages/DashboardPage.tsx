import React, { useState, useEffect, useRef } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import {
  AlertTriangle, TrendingUp, BarChart3, Activity,
  Crosshair, MessageSquare, Eye, Layers, Search,
  Key, Zap, Bookmark, Bot, LineChart, Crown, Lock,
  Sparkles, Target, ArrowRight, CheckCircle2, Wifi, WifiOff,
  Settings, ChevronRight,
} from 'lucide-react';
import { fetchStockData, fetchPortfolioSummary, fetchApiKeys, fetchBrokerConnection, fetchDisplayPreferences } from '../api';
import type { StockData, Portfolio } from '../types';
import { TickerInput } from '../components/TickerInput';
import { StockHeader } from '../components/StockHeader';
import { NewsFeed } from '../components/NewsFeed';
import { AnalystRatings } from '../components/AnalystRatings';
import { FinancialCharts } from '../components/FinancialCharts';
import { EarningsSummary } from '../components/EarningsSummary';
import { TechnicalAnalysis } from '../components/TechnicalAnalysis';
import { OptionsVolatility } from '../components/OptionsVolatility';
import { TradingOpportunities } from '../components/TradingOpportunities';
import { IndustryWatch } from '../components/IndustryWatch';
import { PricePrediction } from '../components/PricePrediction';
import { DCFAnalysis } from '../components/DCFAnalysis';
import { GuruAnalysis } from '../components/GuruAnalysis';
import { StockChat } from '../components/StockChat';
import { CreateStockAgent } from '../components/CreateStockAgent';
import { FinancialHealth } from '../components/FinancialHealth';
import { QualitativeAnalysis } from '../components/QualitativeAnalysis';
import { MacroAnalysis } from '../components/MacroAnalysis';
import { AgentDebate } from '../components/AgentDebate';
import type { DcfScenarioParams } from '../api';
import { AIImpact } from '../components/AIIImpact';
import { AIFortressAnalysis } from '../components/AIFortressAnalysis';
import { AIStressTest } from '../components/AIStressTest';
import { RupeeAnalysis } from '../components/RupeeAnalysis';
import { ExitTab } from '../components/ExitTab';
import { FundFundamentals } from '../components/FundFundamentals';
import { useAuth } from '../contexts/AuthContext';
import { PortfolioHighlights } from '../components/PortfolioHighlights';

const QUICK_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA'];

const TAB_CONFIG = [
  { key: 'overview' as const, label: 'Overview', icon: Eye },
  { key: 'fundamental' as const, label: 'Fundamental', icon: BarChart3 },
  { key: 'technical' as const, label: 'Technical', icon: Activity },
  { key: 'trading' as const, label: 'Options', icon: Layers },
  { key: 'exit' as const, label: 'Analysis', icon: Crosshair },
  { key: 'guru' as const, label: 'Guru', icon: Search },
  { key: 'chat' as const, label: 'Chat', icon: MessageSquare },
];

// ---------------------------------------------------------------------------
// Feature guide data
// ---------------------------------------------------------------------------

const FEATURES = [
  {
    title: 'Stock Research',
    icon: TrendingUp,
    color: 'text-primary',
    bg: 'from-primary/10 to-primary/5 border-primary/20',
    route: '/dashboard',
    premium: false,
    tagline: 'Institutional-grade research. Fraction of the cost.',
    description: 'The full research desk — real-time prices, DCF valuation, options flow, earnings analysis, qualitative moats, and AI-powered forensic analysis. The same depth quants and buy-side analysts use, finally accessible to everyone.',
    bullets: ['DCF valuation, financial health & moat scoring', 'Options flow, technicals & AI stress testing', 'Guru tracker: follow what the whales are buying'],
  },
  {
    title: 'AI Research',
    icon: Sparkles,
    color: 'text-violet-400',
    bg: 'from-violet-500/10 to-violet-500/5 border-violet-500/20',
    route: '/ai-research?module=pickshv',
    premium: true,
    tagline: 'Think like a thematic hedge fund manager.',
    description: 'Run institutional-grade what-if scenarios, trace supply chains across any investment theme, and stress-test your thesis against historical analogues — the kind of research that used to require a six-figure data terminal and a quant team.',
    bullets: ['Pick & Shovels: uncover hidden supply-chain winners before the crowd', 'What-If scenarios with cross-asset contagion modelling', 'Event Impact: replay any market-moving catalyst like the pros do'],
  },
  {
    title: 'Tracking',
    icon: Bookmark,
    color: 'text-cyan-400',
    bg: 'from-cyan-500/10 to-cyan-500/5 border-cyan-500/20',
    route: '/tracking',
    premium: false,
    tagline: 'Your private research command center.',
    description: 'Build a professional watchlist organized by investment themes. Each company card holds your thesis, live prices, AI research notes, and linked AI agents — the same structured workflow institutional PMs use, without the overhead.',
    bullets: ['Theme-grouped: AI infrastructure, Energy, Defense, Biotech…', 'Live price, 52W range & key signals on every card', 'Attach AI agents to any company — one click'],
  },
  {
    title: 'AI Agents',
    icon: Bot,
    color: 'text-success',
    bg: 'from-success/10 to-success/5 border-success/20',
    route: '/agents',
    premium: true,
    tagline: 'Put your digital cortex to work 24/7.',
    description: 'Deploy a fleet of AI agents — each company gets its own multi-worker crew coordinated by an orchestrator agent that synthesizes signals, flags anomalies, and delivers a distilled briefing. Like having a quant analyst who never sleeps.',
    bullets: ['Orchestrator + specialist workers: technicals, earnings, insider flows, macro', 'Scheduled runs: daily pre-market, weekly, or on-demand', 'Wake up to a professional briefing — not raw noise'],
  },
  {
    title: 'Advanced Strategies',
    icon: Target,
    color: 'text-warning',
    bg: 'from-warning/10 to-warning/5 border-warning/20',
    route: '/strategies',
    premium: true,
    tagline: 'Quant playbooks. Retail access.',
    description: 'Box spreads, dual-direction buffers, long/short pair trades, 130/30 portfolios — the same structured strategies used by professional and systematic funds, now modelled and executable without a prime broker or a quant PhD.',
    bullets: ['Box spread & multi-leg options strategy builder', 'Pair trade & 130/30 — long/short like the big funds', 'Connect to Interactive Brokers for live execution'],
  },
  {
    title: 'Derivative Trades',
    icon: LineChart,
    color: 'text-orange-400',
    bg: 'from-orange-500/10 to-orange-500/5 border-orange-500/20',
    route: '/my-trades',
    premium: false,
    tagline: 'Research-to-trade in one workflow.',
    description: 'Log every trade — stocks, options, futures, multi-leg spreads — tied directly to the research thesis that inspired it. Live P&L, Greeks, and an AI exit advisor that knows your position context and thinks like a risk manager.',
    bullets: ['Log stocks, options, futures & spreads in seconds', 'Live P&L with Greeks and real-time price updates', 'AI exit advisor: position-aware, like having a risk desk on call'],
  },
];

// ---------------------------------------------------------------------------
// Setup Banner components
// ---------------------------------------------------------------------------

function SetupBanner({
  icon: Icon,
  color,
  title,
  message,
  action,
  actionTo,
  onDismiss,
}: {
  icon: React.ElementType;
  color: string;
  title: string;
  message: string;
  action: string;
  actionTo: string;
  onDismiss?: () => void;
}) {
  return (
    <div className={`flex items-center gap-3 p-3.5 rounded-2xl border bg-base-100/60 backdrop-blur-sm ${color}`}>
      <div className="w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 bg-current/10">
        <Icon className="w-4.5 h-4.5" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold leading-tight">{title}</p>
        <p className="text-xs text-base-content/60 mt-0.5 leading-snug">{message}</p>
      </div>
      <Link
        to={actionTo}
        className="flex-shrink-0 flex items-center gap-1 text-xs font-semibold px-3 py-1.5 rounded-xl bg-current/10 hover:bg-current/20 transition-colors"
      >
        {action} <ChevronRight className="w-3 h-3" />
      </Link>
      {onDismiss && (
        <button onClick={onDismiss} className="text-base-content/30 hover:text-base-content/60 ml-1">
          ×
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feature card
// ---------------------------------------------------------------------------

function FeatureCard({ feature }: { feature: typeof FEATURES[0] }) {
  const Icon = feature.icon;
  return (
    <Link
      to={feature.route}
      className={`group relative flex flex-col p-4 rounded-2xl border bg-gradient-to-br transition-all duration-200
        hover:scale-[1.01] hover:shadow-lg hover:shadow-black/20 ${feature.bg}`}
    >
      {feature.premium && (
        <div className="absolute top-3 right-3 flex items-center gap-1 px-2 py-0.5 rounded-full bg-yellow-400/15 border border-yellow-400/30">
          <Crown className="w-2.5 h-2.5 text-yellow-400" />
          <span className="text-[9px] font-bold text-yellow-400 uppercase tracking-wide">Premium</span>
        </div>
      )}

      <div className="flex items-center gap-2.5 mb-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center bg-current/10 ${feature.color}`}>
          <Icon className="w-4.5 h-4.5" />
        </div>
        <div>
          <h3 className={`text-sm font-bold ${feature.color}`}>{feature.title}</h3>
          <p className="text-[10px] text-base-content/40 font-medium italic">{feature.tagline}</p>
        </div>
      </div>

      <p className="text-xs text-base-content/65 leading-relaxed mb-3">{feature.description}</p>

      <ul className="space-y-1 mb-3">
        {feature.bullets.map((b, i) => (
          <li key={i} className="flex items-start gap-1.5 text-[10px] text-base-content/55">
            <CheckCircle2 className={`w-3 h-3 flex-shrink-0 mt-0.5 ${feature.color} opacity-70`} />
            {b}
          </li>
        ))}
      </ul>

      <div className={`mt-auto flex items-center gap-1 text-[10px] font-semibold ${feature.color} opacity-0 group-hover:opacity-100 transition-opacity`}>
        Explore {feature.title} <ArrowRight className="w-3 h-3" />
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const { isPremium } = useAuth();
  const [searchParams] = useSearchParams();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stockData, setStockData] = useState<StockData | null>(null);
  // User's tweaked DCF from the DCF page, handed to the debate as its anchor.
  const [dcfOverride, setDcfOverride] = useState<DcfScenarioParams | null>(null);
  React.useEffect(() => { setDcfOverride(null); }, [stockData?.ticker]);
  const [activeTab, setActiveTab] = useState<'overview' | 'fundamental' | 'technical' | 'trading' | 'guru' | 'chat' | 'exit'>('overview');
  const [holding, setHolding] = useState<{ shares: number; cost_basis: number; purchase_date: string } | null>(null);
  const [showHighlights, setShowHighlights] = useState(false);
  // Per-user preference: whether the AI Impact / Fortress / Stress-Test blocks show
  // on the Fundamental tab. Hidden by default (opt-in via Settings).
  const [showAiSections, setShowAiSections] = useState(false);

  // Setup status
  const [hasOpenAiKey, setHasOpenAiKey] = useState<boolean | null>(null);
  const [brokerConnected, setBrokerConnected] = useState<boolean | null>(null);
  const [dismissedOpenAi, setDismissedOpenAi] = useState(false);
  const [dismissedBroker, setDismissedBroker] = useState(false);

  const queryTicker = searchParams.get('ticker')?.toUpperCase() || '';
  const queryTab = searchParams.get('tab') as typeof activeTab | null;
  const queryShares = searchParams.get('shares');
  const queryCost = searchParams.get('cost');
  const queryDate = searchParams.get('date');

  // Check setup status on mount
  useEffect(() => {
    fetchApiKeys()
      .then(keys => setHasOpenAiKey(keys.some(k => k.key_name === 'openai_api_key')))
      .catch(() => setHasOpenAiKey(null));

    fetchDisplayPreferences()
      .then(p => setShowAiSections(!!p.show_ai_sections))
      .catch(() => { /* default hidden */ });

    if (isPremium) {
      fetchBrokerConnection()
        .then(s => setBrokerConnected(s?.configured !== false && !!s?.has_credentials))
        .catch(() => setBrokerConnected(null));
    }
  }, [isPremium]);

  const handleSearch = async (ticker: string, tabOverride?: typeof activeTab) => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchStockData(ticker);
      setStockData(data);
      if (tabOverride) setActiveTab(tabOverride);
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch stock data');
      setStockData(null);
    } finally {
      setLoading(false);
    }

    if (queryShares && queryCost) {
      setHolding({
        shares: parseFloat(queryShares),
        cost_basis: parseFloat(queryCost),
        purchase_date: queryDate || new Date().toISOString().split('T')[0],
      });
      return;
    }

    try {
      const pData = await fetchPortfolioSummary();
      const h = pData.holdings.find((hdg: any) => hdg.ticker.toUpperCase() === ticker.toUpperCase());
      setHolding(h ? { shares: h.shares, cost_basis: h.cost_basis, purchase_date: h.purchase_date || '2024-01-01' } : null);
    } catch {
      // non-critical
    }
  };

  useEffect(() => {
    if (queryTicker) {
      const validTabs = ['overview', 'fundamental', 'technical', 'trading', 'guru', 'chat', 'exit'];
      const tab = queryTab && validTabs.includes(queryTab) ? queryTab : undefined;
      handleSearch(queryTicker, tab);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryTicker]);

  const showOpenAiBanner = hasOpenAiKey === false && !dismissedOpenAi;
  const showBrokerBanner = isPremium && brokerConnected === false && !dismissedBroker;

  return (
    <div className="container-app py-6 sm:py-8">

      {/* ── Setup banners (always shown, above everything) ── */}
      {(showOpenAiBanner || showBrokerBanner) && (
        <div className="space-y-2 mb-5">
          {showOpenAiBanner && (
            <SetupBanner
              icon={Key}
              color="border-warning/30 text-warning"
              title="Add your OpenAI API key to unlock AI features"
              message="AI analysis, agent monitoring, scenario research, and chat all require an OpenAI key. Takes 30 seconds to set up."
              action="Open Settings"
              actionTo="/settings"
              onDismiss={() => setDismissedOpenAi(true)}
            />
          )}
          {showBrokerBanner && (
            <SetupBanner
              icon={WifiOff}
              color="border-info/30 text-info"
              title="Connect Interactive Brokers to enable live order execution"
              message="You're Premium — unlock live trading by connecting your IBKR account via TWS or the IB Gateway."
              action="Connect IBKR"
              actionTo="/settings"
              onDismiss={() => setDismissedBroker(true)}
            />
          )}
        </div>
      )}

      {/* Ticker Input */}
      {!stockData && !loading && (
        <div className="mb-6 max-w-2xl mx-auto">
          <TickerInput onSearch={handleSearch} loading={loading} defaultValue={queryTicker} />
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="glass-card p-4 mb-6 border-error/20 flex items-center gap-3 animate-fade-in">
          <div className="w-10 h-10 rounded-xl bg-error/15 flex items-center justify-center flex-shrink-0">
            <AlertTriangle className="w-5 h-5 text-error" />
          </div>
          <div>
            <p className="text-sm font-semibold text-error">Error</p>
            <p className="text-xs text-base-content/60">{error}</p>
          </div>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="empty-state">
          <div className="relative">
            <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-secondary/10 flex items-center justify-center animate-pulse-slow">
              <span className="loading loading-spinner loading-lg text-primary" />
            </div>
          </div>
          <p className="text-base-content/50 mt-6 text-sm font-medium">Analyzing stock data...</p>
          <p className="text-base-content/30 text-xs mt-1">Gathering fundamentals, technicals, news & more</p>
        </div>
      )}

      {/* Empty state + feature guide */}
      {!loading && !stockData && !error && (
        <>
          {/* Hero */}
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-secondary/10 mb-4 animate-float">
              <TrendingUp className="w-8 h-8 text-primary" />
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold tracking-tight text-base-content mb-2">
              Stock Research
            </h2>
            <p className="text-base-content/50 max-w-md mx-auto text-sm leading-relaxed">
              Enter a ticker to get comprehensive research — fundamentals, technicals, options, and AI-powered insights.
            </p>
            <div className="flex flex-wrap justify-center gap-2 mt-5">
              {QUICK_TICKERS.map((t, i) => (
                <button
                  key={t}
                  className="btn btn-sm rounded-xl border border-primary/20 bg-primary/5 text-primary hover:bg-primary/15
                    hover:border-primary/30 transition-all duration-200 animate-fade-in-up"
                  style={{ animationDelay: `${i * 50}ms`, animationFillMode: 'both' }}
                  onClick={() => handleSearch(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* Portfolio Highlights Call to Action */}
          <div className="max-w-2xl mx-auto mb-8 animate-fade-in-up" style={{ animationDelay: '300ms', animationFillMode: 'both' }}>
            <div className="glass-card relative overflow-hidden p-6 border-primary/20 bg-gradient-to-br from-primary/10 via-base-100/50 to-secondary/5 rounded-3xl flex flex-col md:flex-row items-center justify-between gap-5 group hover:border-primary/35 transition-all duration-300 shadow-xl shadow-black/10">
              <div className="absolute -right-12 -bottom-12 w-48 h-48 bg-primary/5 rounded-full blur-3xl group-hover:bg-primary/8 transition-all duration-300 pointer-events-none" />
              <div className="absolute -left-12 -top-12 w-48 h-48 bg-secondary/5 rounded-full blur-3xl group-hover:bg-secondary/8 transition-all duration-300 pointer-events-none" />
              
              <div className="flex items-start gap-4">
                <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary/25 to-secondary/10 flex items-center justify-center flex-shrink-0 border border-primary/25 text-primary shadow-inner">
                  <Sparkles className="w-6 h-6 animate-pulse text-primary" />
                </div>
                <div className="space-y-1.5 text-left">
                  <div className="flex items-center flex-wrap gap-2">
                    <h3 className="text-md font-bold text-base-content tracking-tight">Portfolio Highlights</h3>
                    <span className="text-[9px] font-black px-2 py-0.5 rounded bg-primary/15 text-primary border border-primary/25 uppercase tracking-wider">AI Powered</span>
                  </div>
                  <p className="text-xs text-base-content/65 max-w-md leading-relaxed">
                    Scan your portfolio holdings to identify the top 5 high-priority positions requiring tactical action today. Review price performance, catalyst news, technical triggers, and AI investment hypotheses.
                  </p>
                </div>
              </div>
              
              <button
                onClick={() => setShowHighlights(true)}
                className="btn btn-sm btn-primary rounded-xl px-5 py-2.5 flex items-center gap-2 font-bold shadow-lg shadow-primary/20 hover:scale-[1.02] active:scale-[0.98] transition-all flex-shrink-0"
              >
                Scan Portfolio <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>

          {/* Feature guide */}
          <div className="mb-4">
            <div className="flex items-center gap-2 mb-4">
              <div className="h-px flex-1 bg-white/[0.06]" />
              <span className="text-[10px] uppercase tracking-widest text-base-content/30 font-semibold px-3">
                Everything the platform offers
              </span>
              <div className="h-px flex-1 bg-white/[0.06]" />
            </div>

            {!isPremium && (
              <div className="flex items-center gap-3 p-3.5 rounded-2xl border border-yellow-400/20 bg-yellow-400/5 mb-4">
                <Crown className="w-5 h-5 text-yellow-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-yellow-400">Unlock Premium features</p>
                  <p className="text-xs text-base-content/50 mt-0.5">
                    AI Research, Agents, and Advanced Strategies are available to Premium users.
                    Contact the admin to upgrade your account.
                  </p>
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {FEATURES.map(f => <FeatureCard key={f.title} feature={f} />)}
            </div>
          </div>
        </>
      )}

      {/* Stock Data */}
      {!loading && stockData && (
        <div className="space-y-5 animate-fade-in">
          <StockHeader data={stockData} />

          <div className="flex items-center gap-1.5 overflow-x-auto scrollbar-hide py-1 -mx-1 px-1">
            {TAB_CONFIG.map((tab) => {
              const Icon = tab.icon;
              const active = activeTab === tab.key;
              const isExit = tab.key === 'exit';
              return (
                <button
                  key={tab.key}
                  className={`
                    flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold
                    whitespace-nowrap transition-all duration-200 border flex-shrink-0
                    ${active
                      ? 'bg-primary/15 text-primary border-primary/20 shadow-sm shadow-primary/10'
                      : `border-transparent text-base-content/50 hover:text-base-content hover:bg-base-200/50
                         ${isExit && holding ? 'text-error/80 hover:text-error' : ''}`
                    }
                  `}
                  onClick={() => setActiveTab(tab.key)}
                >
                  <Icon className="w-3.5 h-3.5" />
                  {tab.label}
                  {isExit && holding && (
                    <span className="w-1.5 h-1.5 rounded-full bg-error animate-pulse ml-0.5" />
                  )}
                </button>
              );
            })}
          </div>

          <div className="animate-fade-in" key={activeTab}>
            {activeTab === 'overview' && (
              <div className="space-y-5">
                <div className="glass-card p-5 sm:p-6">
                  <h3 className="text-lg font-bold mb-3 tracking-tight">About {stockData.companyName}</h3>
                  <div className="flex flex-wrap gap-3 mb-4">
                    {stockData.sector && (
                      <span className="text-xs px-3 py-1 rounded-lg bg-primary/10 text-primary/80 font-medium border border-primary/10">
                        {stockData.sector}
                      </span>
                    )}
                    {stockData.industry && (
                      <span className="text-xs px-3 py-1 rounded-lg bg-secondary/10 text-secondary/80 font-medium border border-secondary/10">
                        {stockData.industry}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-base-content/60 leading-relaxed">
                    {stockData.description || 'No company description available.'}
                  </p>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                  <NewsFeed news={stockData.news} />
                  <AnalystRatings ratings={stockData.analystRatings} />
                </div>
                <IndustryWatch ticker={stockData.ticker} companyName={stockData.companyName} sector={stockData.sector} />
              </div>
            )}

            {activeTab === 'fundamental' && (() => {
              const qt = (stockData.quoteType || '').toUpperCase();
              const isFund = qt === 'ETF' || qt === 'MUTUALFUND';
              if (isFund) {
                return (
                  <div className="space-y-5">
                    <FundFundamentals stockData={stockData} />
                  </div>
                );
              }
              return (
                <div className="space-y-5">
                  <RupeeAnalysis ticker={stockData.ticker} />
                  <FinancialCharts financials={stockData.financials} ticker={stockData.ticker} />
                  <FinancialHealth ticker={stockData.ticker} />
                  <EarningsSummary earnings={stockData.earnings} ticker={stockData.ticker} />
                  <DCFAnalysis ticker={stockData.ticker} onUseInDebate={setDcfOverride} />
                  <QualitativeAnalysis ticker={stockData.ticker} />
                  <MacroAnalysis ticker={stockData.ticker} />
                  <AgentDebate ticker={stockData.ticker} dcfOverride={dcfOverride} />
                  {showAiSections && (
                    <>
                      <AIImpact ticker={stockData.ticker} />
                      <AIFortressAnalysis ticker={stockData.ticker} />
                      <AIStressTest ticker={stockData.ticker} />
                    </>
                  )}
                </div>
              );
            })()}

            {activeTab === 'technical' && (
              <div className="space-y-5">
                <TechnicalAnalysis technical={stockData.technical} ticker={stockData.ticker} />
                <PricePrediction ticker={stockData.ticker} />
              </div>
            )}

            {activeTab === 'trading' && (
              <div className="space-y-5">
                <OptionsVolatility options={stockData.options} />
                <TradingOpportunities options={stockData.options} currentPrice={stockData.price} />
              </div>
            )}

            {activeTab === 'guru' && (
              <div className="space-y-5">
                <GuruAnalysis ticker={stockData.ticker} />
              </div>
            )}

            {activeTab === 'chat' && (
              <div className="space-y-5">
                <StockChat ticker={stockData.ticker} />
                <CreateStockAgent ticker={stockData.ticker} companyName={stockData.companyName} currentPrice={stockData.price} />
              </div>
            )}

            {activeTab === 'exit' && (
              <ExitTab ticker={stockData.ticker} currentPrice={stockData.price} />
            )}
          </div>
        </div>
      )}

      {showHighlights && (
        <PortfolioHighlights onClose={() => setShowHighlights(false)} />
      )}
    </div>
  );
}
