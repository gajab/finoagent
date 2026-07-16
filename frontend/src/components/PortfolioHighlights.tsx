import React, { useState, useEffect, useRef } from 'react';
import {
  X, Check, AlertTriangle, Sparkles, RefreshCw,
  TrendingUp, TrendingDown, Layers, Calendar, ChevronRight,
  Shield, DollarSign, Brain, BarChart2, BookOpen, Clock, Bot,
  ExternalLink, ArrowLeft, RotateCcw
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  fetchPortfolioHighlights,
  dismissPortfolioHighlight,
  resetPortfolioHighlights,
  trackCompany,
  HighlightHolding
} from '../api';

interface PortfolioHighlightsProps {
  onClose: () => void;
}

export function PortfolioHighlights({ onClose }: PortfolioHighlightsProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [highlights, setHighlights] = useState<HighlightHolding[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [totalHoldings, setTotalHoldings] = useState(0);
  const [locallyDismissed, setLocallyDismissed] = useState<string[]>([]);

  // Gesture state
  const [dragStart, setDragStart] = useState<{ x: number; y: number } | null>(null);
  const [xOffset, setXOffset] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [swipeDirection, setSwipeDirection] = useState<'left' | 'right' | null>(null);
  const [cardActionStatus, setCardActionStatus] = useState<'none' | 'swiped_left' | 'swiped_right'>('none');

  const cardRef = useRef<HTMLDivElement>(null);

  const loadHighlights = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchPortfolioHighlights();
      // Filter out locally dismissed tickers so they don't show up in next batches
      const filtered = res.highlights.filter(h => !locallyDismissed.includes(h.ticker.toUpperCase().trim()));
      setHighlights(filtered);
      setTotalHoldings(res.total_portfolio_holdings);
      setCurrentIndex(0);
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch portfolio highlights');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHighlights();
  }, []);

  // Keyboard accessibility
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (highlights.length === 0 || currentIndex >= highlights.length || loading) return;
      if (e.key === 'ArrowLeft') {
        handleSwipeLeft();
      } else if (e.key === 'ArrowRight') {
        handleSwipeRight();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [highlights, currentIndex, loading]);

  const handleSwipeLeft = async () => {
    if (currentIndex >= highlights.length) return;
    const ticker = highlights[currentIndex].ticker;
    // Instantly mark as locally dismissed to prevent duplicate rendering on fast clicks
    setLocallyDismissed(prev => [...prev, ticker.toUpperCase().trim()]);
    setCardActionStatus('swiped_left');
    setSwipeDirection('left');
    setXOffset(-1000); // animate away

    setTimeout(async () => {
      try {
        await dismissPortfolioHighlight(ticker);
      } catch (err) {
        console.error('Failed to dismiss highlight:', err);
      }
      setCurrentIndex(prev => prev + 1);
      setXOffset(0);
      setSwipeDirection(null);
      setCardActionStatus('none');
    }, 300);
  };

  const handleSwipeRight = async () => {
    if (currentIndex >= highlights.length) return;
    const holding = highlights[currentIndex];
    // Instantly mark as locally dismissed to prevent duplicate rendering on fast clicks
    setLocallyDismissed(prev => [...prev, holding.ticker.toUpperCase().trim()]);
    setCardActionStatus('swiped_right');
    setSwipeDirection('right');
    setXOffset(1000); // animate away

    setTimeout(async () => {
      try {
        // Save holding to watchlist under "Take Action" theme per user approval
        await trackCompany({
          ticker: holding.ticker,
          name: holding.company_name || holding.ticker,
          theme_raw: "Take Action",
          theme_slug: "take-action",
          theme_summary: "Portfolio positions flagged for review and tactical action.",
          sector: holding.fundamentals.sector || "Unknown",
          llm_data: { thesis: holding.hypothesis },
          financial_data: { price: holding.current_price, price_chg_1d: holding.day_change_pct },
          user_notes: `Flagged from Portfolio Highlights. AI Hypothesis:\n\n${holding.hypothesis}`,
          status: 'active'
        });
        await dismissPortfolioHighlight(holding.ticker); // dismiss so it doesn't reappear in highlights queue
      } catch (err) {
        console.error('Failed to watch highlight:', err);
      }
      setCurrentIndex(prev => prev + 1);
      setXOffset(0);
      setSwipeDirection(null);
      setCardActionStatus('none');
    }, 300);
  };

  // Mouse / Touch Gesture handlers
  const handleDragStart = (clientX: number, clientY: number) => {
    if (currentIndex >= highlights.length) return;
    setDragStart({ x: clientX, y: clientY });
    setIsDragging(true);
  };

  const handleDragMove = (clientX: number) => {
    if (!dragStart || !isDragging) return;
    const diffX = clientX - dragStart.x;
    setXOffset(diffX);

    if (diffX > 50) {
      setSwipeDirection('right');
    } else if (diffX < -50) {
      setSwipeDirection('left');
    } else {
      setSwipeDirection(null);
    }
  };

  const handleDragEnd = () => {
    if (!isDragging) return;
    setIsDragging(false);
    setDragStart(null);

    if (xOffset > 120) {
      handleSwipeRight();
    } else if (xOffset < -120) {
      handleSwipeLeft();
    } else {
      // Spring back
      setXOffset(0);
      setSwipeDirection(null);
    }
  };

  // Touch event listeners
  const onTouchStart = (e: React.TouchEvent) => {
    const t = e.touches[0];
    handleDragStart(t.clientX, t.clientY);
  };

  const onTouchMove = (e: React.TouchEvent) => {
    if (!isDragging) return;
    const t = e.touches[0];
    handleDragMove(t.clientX);
  };

  // Mouse event listeners
  const onMouseDown = (e: React.MouseEvent) => {
    handleDragStart(e.clientX, e.clientY);
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    handleDragMove(e.clientX);
  };

  const handleReset = async () => {
    setLoading(true);
    try {
      await resetPortfolioHighlights();
      setLocallyDismissed([]);
      const res = await fetchPortfolioHighlights();
      setHighlights(res.highlights);
      setTotalHoldings(res.total_portfolio_holdings);
      setCurrentIndex(0);
    } catch (err: any) {
      setError(err?.message || 'Failed to reset highlights');
    } finally {
      setLoading(false);
    }
  };

  // Render SVG Sparkline
  const renderSparkline = (points: { date: string; price: number }[]) => {
    if (!points || points.length < 2) return null;
    const prices = points.map(p => p.price);
    const max = Math.max(...prices);
    const min = Math.min(...prices);
    const range = max - min === 0 ? 1 : max - min;

    const width = 180;
    const height = 44;
    const padding = 3;

    const coords = points.map((p, i) => {
      const x = padding + (i * (width - padding * 2)) / (points.length - 1);
      const y = height - padding - ((p.price - min) * (height - padding * 2)) / range;
      return { x, y };
    });

    const pathD = coords.reduce((acc, c, i) => {
      return i === 0 ? `M ${c.x} ${c.y}` : `${acc} L ${c.x} ${c.y}`;
    }, '');

    // Area path to create fill under the line
    const areaD = `${pathD} L ${coords[coords.length - 1].x} ${height} L ${coords[0].x} ${height} Z`;

    const isUp = prices[prices.length - 1] >= prices[0];
    const strokeColor = isUp ? '#10b981' : '#f43f5e'; // emerald vs rose
    const fillGradId = `sparkline-grad-${prices[0]}-${prices[prices.length - 1]}`;

    return (
      <div className="flex flex-col items-center">
        <svg width={width} height={height} className="overflow-visible">
          <defs>
            <linearGradient id={fillGradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={strokeColor} stopOpacity="0.18" />
              <stop offset="100%" stopColor={strokeColor} stopOpacity="0.0" />
            </linearGradient>
          </defs>
          {/* Fill Area */}
          <path d={areaD} fill={`url(#${fillGradId})`} />
          {/* Line Path */}
          <path d={pathD} fill="none" stroke={strokeColor} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          {/* Endpoint Dot */}
          <circle cx={coords[coords.length - 1].x} cy={coords[coords.length - 1].y} r="3" fill={strokeColor} />
        </svg>
        <div className="flex justify-between w-full text-[9px] text-base-content/30 mt-1 px-1">
          <span>{points[0].date.split('-').slice(1).join('/')}</span>
          <span>5D Trend</span>
          <span>{points[points.length - 1].date.split('-').slice(1).join('/')}</span>
        </div>
      </div>
    );
  };

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md">
        <div className="glass-card p-8 text-center max-w-sm flex flex-col items-center">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary/20 to-secondary/10 flex items-center justify-center animate-spin mb-4">
            <RefreshCw className="w-6 h-6 text-primary" />
          </div>
          <h3 className="text-md font-bold">Scanning Portfolio</h3>
          <p className="text-xs text-base-content/50 mt-1">Calculating exposure, today's movements, and drawing AI hypotheses...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-md">
        <div className="glass-card p-6 text-center max-w-sm border-error/20">
          <AlertTriangle className="w-10 h-10 text-error mx-auto mb-3" />
          <h3 className="text-md font-bold text-error">Scan Failed</h3>
          <p className="text-xs text-base-content/60 mt-1 mb-4">{error}</p>
          <div className="flex gap-2 justify-center">
            <button onClick={loadHighlights} className="btn btn-sm btn-primary rounded-xl">Retry</button>
            <button onClick={onClose} className="btn btn-sm btn-ghost rounded-xl">Close</button>
          </div>
        </div>
      </div>
    );
  }

  const activeCard = highlights[currentIndex];
  const isLastCard = currentIndex >= highlights.length;

  // Rotation and transform calculation for swiping card
  const cardStyle: React.CSSProperties = isDragging
    ? {
        transform: `translate3d(${xOffset}px, 0, 0) rotate(${xOffset * 0.08}deg)`,
        transition: 'none',
        cursor: 'grabbing'
      }
    : swipeDirection
    ? {
        transform: `translate3d(${swipeDirection === 'right' ? 1000 : -1000}px, 0, 0) rotate(${swipeDirection === 'right' ? 45 : -45}deg)`,
        transition: 'transform 0.3s ease-out',
      }
    : {
        transform: 'translate3d(0, 0, 0) rotate(0deg)',
        transition: 'transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275)',
      };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-md p-4 overflow-y-auto">
      <div className="w-full max-w-2xl my-auto">

        {/* Top bar */}
        <div className="flex items-center justify-between mb-4 text-white">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-xl bg-primary/20 border border-primary/30 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-primary" />
            </div>
            <div>
              <h2 className="font-bold text-sm leading-none">Portfolio Highlights</h2>
              <p className="text-[10px] text-white/50 mt-1">Reviewing top priority positions</p>
            </div>
          </div>
          <button onClick={onClose} className="btn btn-sm btn-circle btn-ghost text-white/60 hover:text-white hover:bg-white/10">
            <X className="w-5 h-5" />
          </button>
        </div>

        {totalHoldings === 0 ? (
          <div className="glass-card text-center p-8 border-white/10 bg-base-100/40 text-base-content max-w-md mx-auto animate-scale-up">
            <div className="w-16 h-16 rounded-full bg-warning/15 border border-warning/30 flex items-center justify-center mx-auto mb-4">
              <TrendingUp className="w-8 h-8 text-warning animate-pulse" />
            </div>
            <h3 className="text-lg font-bold">Your Portfolio is Empty</h3>
            <p className="text-xs text-base-content/50 max-w-xs mx-auto mt-1 leading-relaxed">
              Add stocks or log trade transaction history to start receiving AI-powered portfolio highlights, risk alerts, and investment hypotheses.
            </p>
            <div className="flex gap-2 justify-center mt-6">
              <a href="/my-trades" className="btn btn-sm btn-primary rounded-xl gap-1.5">
                Go to My Trades
              </a>
              <button onClick={onClose} className="btn btn-sm btn-ghost rounded-xl">
                Close
              </button>
            </div>
          </div>
        ) : highlights.length === 0 ? (
          <div className="glass-card text-center p-8 border-white/10 bg-base-100/40 text-base-content max-w-md mx-auto animate-scale-up">
            <div className="w-16 h-16 rounded-full bg-success/15 border border-success/30 flex items-center justify-center mx-auto mb-4">
              <Check className="w-8 h-8 text-success" />
            </div>
            <h3 className="text-lg font-bold">You're All Caught Up!</h3>
            <p className="text-xs text-base-content/50 max-w-xs mx-auto mt-1 leading-relaxed">
              You have reviewed all highlighted stocks in your portfolio. Tap below to reset and view them again, or close to return.
            </p>
            <div className="flex gap-2 justify-center mt-6">
              <button onClick={handleReset} className="btn btn-sm btn-secondary rounded-xl gap-1.5">
                <RotateCcw className="w-3.5 h-3.5" /> Reset Suggestions
              </button>
              <button onClick={onClose} className="btn btn-sm btn-ghost rounded-xl">
                Close
              </button>
            </div>
          </div>
        ) : currentIndex >= highlights.length ? (
          <div className="glass-card text-center p-8 border-white/10 bg-base-100/40 text-base-content max-w-md mx-auto animate-scale-up">
            <div className="w-16 h-16 rounded-full bg-primary/15 border border-primary/30 flex items-center justify-center mx-auto mb-4">
              <Sparkles className="w-8 h-8 text-primary" />
            </div>
            <h3 className="text-lg font-bold">Batch Completed!</h3>
            <p className="text-xs text-base-content/50 max-w-xs mx-auto mt-1 leading-relaxed">
              Would you like to review the next 5 suggestions in your portfolio?
            </p>
            <div className="flex gap-2 justify-center mt-6">
              <button onClick={loadHighlights} className="btn btn-sm btn-primary rounded-xl gap-1.5">
                <ChevronRight className="w-3.5 h-3.5" /> Review Next 5
              </button>
              <button onClick={onClose} className="btn btn-sm btn-ghost rounded-xl">
                Close
              </button>
            </div>
          </div>
        ) : (
          <div className="relative select-none">
            {/* Gesture feedback overlays */}
            {xOffset !== 0 && cardActionStatus === 'none' && (
              <>
                <div
                  className="absolute top-1/2 -translate-y-1/2 left-8 z-20 pointer-events-none bg-emerald-500 text-white font-bold text-xs uppercase tracking-widest px-4 py-2 rounded-xl border border-emerald-400/30 shadow-lg flex items-center gap-1.5 transition-opacity duration-150"
                  style={{ opacity: Math.min(xOffset / 120, 0.9) }}
                >
                  <Check className="w-4 h-4" /> Take Action
                </div>
                <div
                  className="absolute top-1/2 -translate-y-1/2 right-8 z-20 pointer-events-none bg-rose-500 text-white font-bold text-xs uppercase tracking-widest px-4 py-2 rounded-xl border border-rose-400/30 shadow-lg flex items-center gap-1.5 transition-opacity duration-150"
                  style={{ opacity: Math.min(-xOffset / 120, 0.9) }}
                >
                  <X className="w-4 h-4" /> Skip SUGGESTION
                </div>
              </>
            )}

            {/* Deck Shadow Card Effect (Stacked background card) */}
            {currentIndex + 1 < highlights.length && (
              <div className="absolute top-2 left-0 right-0 h-full bg-base-100/20 border border-white/5 rounded-3xl -z-10 scale-[0.97] translate-y-2 pointer-events-none opacity-50 blur-[1px]" />
            )}
            {currentIndex + 2 < highlights.length && (
              <div className="absolute top-4 left-0 right-0 h-full bg-base-100/10 border border-white/5 rounded-3xl -z-20 scale-[0.94] translate-y-4 pointer-events-none opacity-20 blur-[2px]" />
            )}

            {/* Swipeable Main Card */}
            <div
              ref={cardRef}
              style={cardStyle}
              onMouseDown={onMouseDown}
              onMouseMove={onMouseMove}
              onMouseUp={handleDragEnd}
              onMouseLeave={handleDragEnd}
              onTouchStart={onTouchStart}
              onTouchMove={onTouchMove}
              onTouchEnd={handleDragEnd}
              className="glass-card w-full rounded-3xl border border-white/[0.08] bg-base-100/90 shadow-[0_24px_64px_rgba(0,0,0,0.6)] backdrop-blur-2xl text-base-content overflow-hidden select-none active:scale-[0.99] transition-transform duration-100"
            >
              {/* Card top gradient band */}
              <div className={`h-1.5 w-full bg-gradient-to-r ${
                (activeCard.day_change_pct ?? 0) >= 0
                  ? 'from-emerald-500 to-teal-400'
                  : 'from-rose-500 to-orange-400'
              }`} />

              <div className="p-5 sm:p-6 space-y-4 max-h-[80vh] overflow-y-auto scrollbar-hide">

                {/* Section 1: Header (Ticker, company, price movement) */}
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-black text-xl tracking-tight leading-none text-base-content">{activeCard.ticker}</span>
                      <span className="text-[9px] px-2 py-0.5 rounded-md font-bold uppercase border border-white/10 bg-white/5 text-base-content/40 tracking-wider">
                        {activeCard.asset_type === 'MUTUAL_FUND' ? 'Mutual Fund' : activeCard.asset_type}
                      </span>
                    </div>
                    <h3 className="text-xs text-base-content/50 truncate mt-1">{activeCard.company_name}</h3>
                  </div>

                  <div className="text-right flex-shrink-0">
                    <div className="text-lg font-black tabular-nums">
                      {activeCard.current_price != null ? `$${activeCard.current_price.toFixed(2)}` : '—'}
                    </div>
                    {activeCard.day_change_pct != null && (
                      <div className={`flex items-center justify-end gap-0.5 text-xs font-bold ${
                        activeCard.day_change_pct >= 0 ? 'text-success' : 'text-error'
                      }`}>
                        {activeCard.day_change_pct >= 0 ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
                        {activeCard.day_change_pct >= 0 ? '+' : ''}{activeCard.day_change_pct.toFixed(2)}%
                      </div>
                    )}
                  </div>
                </div>

                {/* Section 2: Portfolio Exposure, Cost, overall P&L, 5D Sparkline */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-base-200/40 border border-white/[0.04] p-3 rounded-2xl">
                  {/* Left Column: Exposure metrics */}
                  <div className="space-y-2.5 my-auto">
                    <div>
                      <span className="text-[9px] uppercase tracking-wider text-base-content/40 font-bold block">Portfolio Weight</span>
                      <div className="flex items-baseline gap-1.5 mt-0.5">
                        <span className="text-md font-extrabold text-base-content">{activeCard.weight_pct ?? '0.00'}%</span>
                        <span className="text-[10px] text-base-content/40">exposure</span>
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2 border-t border-white/[0.03] pt-2">
                      <div>
                        <span className="text-[9px] uppercase tracking-wider text-base-content/30 block">Holdings</span>
                        <span className="text-xs font-semibold tabular-nums text-base-content/70">{activeCard.shares.toFixed(2)} shares</span>
                      </div>
                      <div>
                        <span className="text-[9px] uppercase tracking-wider text-base-content/30 block">Total Value</span>
                        <span className="text-xs font-semibold tabular-nums text-base-content/70">
                          {activeCard.current_price ? `$${(activeCard.shares * activeCard.current_price).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}
                        </span>
                      </div>
                    </div>

                    <div className="border-t border-white/[0.03] pt-2">
                      <span className="text-[9px] uppercase tracking-wider text-base-content/30 block">Overall P&L</span>
                      <div className="flex items-baseline gap-2 mt-0.5">
                        <span className={`text-xs font-bold tabular-nums ${
                          (activeCard.unrealized_gain_loss ?? 0) >= 0 ? 'text-success' : 'text-error'
                        }`}>
                          {(activeCard.unrealized_gain_loss ?? 0) >= 0 ? '+' : ''}${activeCard.unrealized_gain_loss?.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        </span>
                        <span className={`text-[10px] font-semibold tabular-nums ${
                          (activeCard.unrealized_gain_loss_pct ?? 0) >= 0 ? 'text-success/70' : 'text-error/70'
                        }`}>
                          ({(activeCard.unrealized_gain_loss_pct ?? 0) >= 0 ? '+' : ''}{activeCard.unrealized_gain_loss_pct?.toFixed(1)}%)
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Right Column: 5D sparkline */}
                  <div className="flex flex-col justify-center border-l md:border-l border-white/[0.04] pl-0 md:pl-4 min-h-[90px]">
                    {activeCard.last_5d_history && activeCard.last_5d_history.length > 0 ? (
                      renderSparkline(activeCard.last_5d_history)
                    ) : (
                      <div className="text-center text-xs text-base-content/30 italic py-4">No historical prices found</div>
                    )}
                  </div>
                </div>

                {/* Section 3: Technicals & Fundamentals columns */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {/* Technicals */}
                  <div className="space-y-2">
                    <h4 className="text-[10px] uppercase font-bold tracking-wider text-base-content/40 flex items-center gap-1">
                      <BarChart2 className="w-3.5 h-3.5 text-primary" /> Technical Indicators
                    </h4>
                    <div className="bg-base-200/20 border border-white/[0.03] p-2.5 rounded-xl space-y-2 text-xs">
                      <div className="flex justify-between items-center gap-2">
                        <span className="text-base-content/40">RSI (14)</span>
                        <span className={`font-semibold ${
                          activeCard.technicals.rsi && activeCard.technicals.rsi > 70 ? 'text-warning' :
                          activeCard.technicals.rsi && activeCard.technicals.rsi < 30 ? 'text-success' :
                          'text-base-content/70'
                        }`}>
                          {activeCard.technicals.rsi?.toFixed(1) ?? '—'}
                          {activeCard.technicals.rsi_signal && (
                            <span className="text-[9px] font-normal text-base-content/40 ml-1">
                              ({activeCard.technicals.rsi_signal.split(' ')[0]})
                            </span>
                          )}
                        </span>
                      </div>

                      <div className="flex justify-between items-center gap-2 border-t border-white/[0.03] pt-1.5">
                        <span className="text-base-content/40">Support / Resist</span>
                        <span className="font-semibold text-base-content/70 tabular-nums">
                          ${activeCard.technicals.support?.toFixed(1) ?? '—'} / ${activeCard.technicals.resistance?.toFixed(1) ?? '—'}
                        </span>
                      </div>

                      <div className="flex justify-between items-center gap-2 border-t border-white/[0.03] pt-1.5">
                        <span className="text-base-content/40">MACD Signal</span>
                        <span className={`font-semibold capitalize ${
                          activeCard.technicals.macd_signal === 'bullish' ? 'text-success' :
                          activeCard.technicals.macd_signal === 'bearish' ? 'text-error' :
                          'text-base-content/40'
                        }`}>
                          {activeCard.technicals.macd_signal ?? '—'}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Fundamentals */}
                  <div className="space-y-2">
                    <h4 className="text-[10px] uppercase font-bold tracking-wider text-base-content/40 flex items-center gap-1">
                      <BookOpen className="w-3.5 h-3.5 text-secondary" /> Key Fundamentals
                    </h4>
                    <div className="bg-base-200/20 border border-white/[0.03] p-2.5 rounded-xl space-y-2 text-xs">
                      <div className="flex justify-between items-center gap-2">
                        <span className="text-base-content/40">Market Cap</span>
                        <span className="font-semibold text-base-content/70">{activeCard.fundamentals.market_cap ?? '—'}</span>
                      </div>

                      <div className="flex justify-between items-center gap-2 border-t border-white/[0.03] pt-1.5">
                        <span className="text-base-content/40">P/E (Trailing/Fwd)</span>
                        <span className="font-semibold text-base-content/70 tabular-nums">
                          {activeCard.fundamentals.trailing_pe?.toFixed(1) ?? '—'} / {activeCard.fundamentals.forward_pe?.toFixed(1) ?? '—'}
                        </span>
                      </div>

                      <div className="flex justify-between items-center gap-2 border-t border-white/[0.03] pt-1.5">
                        <span className="text-base-content/40">PEG / Yield</span>
                        <span className="font-semibold text-base-content/70 tabular-nums">
                          {activeCard.fundamentals.peg_ratio?.toFixed(2) ?? '—'} / {activeCard.fundamentals.dividend_yield ? `${activeCard.fundamentals.dividend_yield.toFixed(2)}%` : '0.00%'}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Section 4: AI Hypothesis */}
                <div className="space-y-2 bg-gradient-to-br from-primary/10 to-secondary/5 border border-primary/10 p-4 rounded-2xl relative overflow-hidden">
                  <div className="absolute top-3 right-3 flex items-center gap-1 text-[9px] font-bold text-primary/80 uppercase tracking-widest bg-primary/10 px-2.5 py-0.5 rounded-md border border-primary/20">
                    <Brain className="w-2.5 h-2.5" /> AI Review Thesis
                  </div>
                  <h4 className="text-[10px] uppercase font-bold tracking-wider text-primary flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5" /> Investment Hypothesis
                  </h4>
                  <div className="prose prose-sm text-xs leading-relaxed text-base-content/85 mt-2 max-w-none prose-p:my-1 prose-strong:text-base-content">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeCard.hypothesis}</ReactMarkdown>
                  </div>
                </div>

                {/* Section 5: News Widget */}
                {activeCard.news && activeCard.news.length > 0 && (
                  <div className="space-y-2 pt-1">
                    <h4 className="text-[10px] uppercase font-bold tracking-wider text-base-content/40 flex items-center gap-1">
                      <Clock className="w-3.5 h-3.5" /> Recent Catalyst News
                    </h4>
                    <div className="space-y-1.5">
                      {activeCard.news.map((item, idx) => (
                        <a
                          key={idx}
                          href={item.link}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex items-center justify-between gap-3 p-2 rounded-xl bg-base-200/20 border border-white/[0.03] hover:border-primary/20 hover:bg-base-200/50 transition-all text-xs group"
                        >
                          <div className="min-w-0">
                            <span className="font-semibold text-base-content/80 group-hover:text-primary transition-colors block truncate pr-1">
                              {item.title}
                            </span>
                            <span className="text-[9px] text-base-content/30 block mt-0.5">
                              {item.publisher} · {item.published}
                            </span>
                          </div>
                          <ExternalLink className="w-3 h-3 text-base-content/20 group-hover:text-primary transition-colors flex-shrink-0" />
                        </a>
                      ))}
                    </div>
                  </div>
                )}

              </div>

              {/* Card Footer swipe guides */}
              <div className="bg-base-200/40 px-5 py-3 border-t border-white/[0.04] flex items-center justify-between text-[10px] text-base-content/40 font-semibold select-none">
                <button
                  onClick={handleSwipeLeft}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-xl border border-white/[0.06] hover:bg-rose-500/10 hover:border-rose-500/20 hover:text-rose-400 transition-colors"
                >
                  <X className="w-3 h-3 text-rose-500" /> Swipe Left (Skip)
                </button>
                <span className="text-[9px] font-bold text-base-content/25 uppercase">
                  Card {currentIndex + 1} of {highlights.length}
                </span>
                <button
                  onClick={handleSwipeRight}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-xl border border-white/[0.06] hover:bg-emerald-500/10 hover:border-emerald-500/20 hover:text-emerald-400 transition-colors"
                >
                  <Check className="w-3 h-3 text-emerald-500" /> Swipe Right (Action)
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Keyboard shortcut tips */}
        {!isLastCard && (
          <div className="flex justify-center gap-4 text-[10px] text-white/40 mt-4 select-none">
            <span className="flex items-center gap-1"><kbd className="kbd kbd-xs bg-white/10 text-white border-white/10">←</kbd> Skip</span>
            <span className="flex items-center gap-1"><kbd className="kbd kbd-xs bg-white/10 text-white border-white/10">→</kbd> Take Action</span>
          </div>
        )}

      </div>
    </div>
  );
}
