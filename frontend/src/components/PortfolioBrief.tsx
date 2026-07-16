import React, { useMemo } from 'react';
import {
  Sparkles, RefreshCw, Loader2, TrendingUp, TrendingDown,
  ArrowUpRight, ArrowDownRight, Calendar, DollarSign, Wallet,
  History, Newspaper, Target,
} from 'lucide-react';
import type { PortfolioBrief as Brief, BriefDelta, BriefBenchmark, BriefMoverNews } from '../types';

type BriefTab = 'overview' | 'dividends' | 'fundamentals' | 'technical';

interface Props {
  brief: Brief | null;
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onFocus: (ticker: string, tab: BriefTab) => void;
}

// ── Local formatters (kept self-contained for exact control) ─────────────────
function money(n?: number | null, opts: { signed?: boolean; compact?: boolean; dp?: number } = {}): string {
  if (n == null || Number.isNaN(n)) return '—';
  const { signed, compact, dp = 0 } = opts;
  const abs = Math.abs(n);
  const sign = signed && n > 0 ? '+' : n < 0 ? '-' : '';
  if (compact && abs >= 1000) {
    const units: [number, string][] = [[1e9, 'B'], [1e6, 'M'], [1e3, 'k']];
    for (const [d, s] of units) {
      if (abs >= d) return `${sign}$${(abs / d).toFixed(abs / d >= 100 ? 0 : 1)}${s}`;
    }
  }
  return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
}
function pct(n?: number | null, signed = true): string {
  if (n == null || Number.isNaN(n)) return '—';
  const sign = signed && n > 0 ? '+' : n < 0 ? '-' : '';
  return `${sign}${Math.abs(n).toFixed(2)}%`;
}
function asOf(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function hasDelta(d?: Brief['delta']): d is NonNullable<Brief['delta']> {
  return !!d && (Math.abs(d.market_change) >= 0.005 || d.crossings.length > 0
    || d.top_contributors.length > 0 || d.positions_added.length > 0);
}

// Deterministic fallback so the hero leads with real news even without an LLM key.
function fallbackNarrative(b: Brief): string {
  const h = b.headline;
  if (!h) return '';
  const parts: string[] = [];
  const d = b.delta;

  if (hasDelta(d)) {
    const verb = d.market_change >= 0 ? 'added' : 'shed';
    let s = `Since ${d.since_label}, the market ${verb} ${money(Math.abs(d.market_change))}`;
    if (d.market_change_pct != null) s += ` (${pct(d.market_change_pct, false)})`;
    s += ' across the positions you held then';
    if (d.top_contributors[0]) s += `, led by ${d.top_contributors[0].ticker}`;
    parts.push(s + '.');
    if (d.crossings[0]) {
      const c = d.crossings[0];
      parts.push(`${c.ticker} ${c.direction === 'into_gain' ? 'crossed into a gain' : 'slipped into a loss'} versus your cost.`);
    }
  } else {
    parts.push(`Your portfolio is worth ${money(h.total_market_value)} across ${h.positions} position${h.positions === 1 ? '' : 's'}.`);
    if (h.day_change_pct != null) {
      const dir = h.day_change >= 0 ? 'up' : 'down';
      parts.push(`It's ${dir} ${money(Math.abs(h.day_change))} (${pct(h.day_change_pct, false)}) today.`);
    }
  }

  if (b.benchmark && Math.abs(b.benchmark.vs_portfolio_pp) >= 0.1) {
    const bm = b.benchmark;
    parts.push(`That's ${bm.vs_portfolio_pp >= 0 ? 'ahead of' : 'behind'} the S&P 500 by ${Math.abs(bm.vs_portfolio_pp).toFixed(2)}pp today.`);
  } else if (!hasDelta(d)) {
    if (h.annual_income > 0) parts.push(`It generates ${money(h.annual_income)} a year in dividend income.`);
  }

  return parts.join(' ').trim();
}

export function PortfolioBrief({ brief, loading, refreshing, onRefresh, onFocus }: Props) {
  const narrative = useMemo(() => {
    if (!brief || brief.empty) return '';
    return (brief.narrative && brief.narrative.trim()) || fallbackNarrative(brief);
  }, [brief]);

  if (loading && !brief) return <BriefSkeleton />;
  if (!brief || brief.empty || !brief.headline) return null;

  const h = brief.headline;
  const dayUp = (h.day_change ?? 0) >= 0;
  const allUp = (h.total_unrealized ?? 0) >= 0;
  const gainers = brief.movers?.gainers ?? [];
  const losers = brief.movers?.losers ?? [];
  const upcoming = brief.upcoming ?? [];
  const delta = brief.delta;
  const benchmark = brief.benchmark;
  const showDelta = hasDelta(delta);
  // Catalysts: the single biggest gainer / loser that actually carries a headline.
  const moverNews = [gainers[0], losers[0]]
    .filter((m): m is NonNullable<typeof m> => !!m && !!m.news)
    .map(m => ({ ticker: m.ticker, tone: (m.day_change_pct ?? 0) >= 0 ? 'pos' as const : 'neg' as const, news: m.news! }));

  return (
    <div className="relative overflow-hidden rounded-2xl border border-primary/15 bg-gradient-to-br from-primary/[0.07] via-base-200/25 to-base-200/10 p-4 sm:p-5 shadow-lg shadow-primary/5">
      {/* aurora accents */}
      <div className="pointer-events-none absolute -top-24 -right-16 h-52 w-52 rounded-full bg-primary/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-28 -left-12 h-52 w-52 rounded-full bg-success/[0.06] blur-3xl" />

      <div className="relative z-10 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-primary/20">
              <Sparkles className="h-3.5 w-3.5 text-primary" />
            </span>
            <div className="min-w-0">
              <div className="text-[13px] font-bold tracking-tight text-base-content/90 leading-none">Portfolio brief</div>
              {brief.generated_at && (
                <div className="text-[10px] text-base-content/40 mt-0.5">as of {asOf(brief.generated_at)}</div>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {benchmark && <BenchmarkBadge bm={benchmark} />}
            <button
              onClick={onRefresh}
              disabled={refreshing}
              className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-base-200/50 px-2.5 py-1.5 text-[11px] font-medium text-base-content/60 transition-colors hover:text-base-content hover:bg-base-200/80 disabled:opacity-50"
            >
              {refreshing ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
              <span className="hidden sm:inline">{refreshing ? 'Refreshing…' : 'Refresh'}</span>
            </button>
          </div>
        </div>

        {/* Narrative */}
        {narrative && (
          <p className="text-[15px] sm:text-base leading-relaxed text-base-content/85 font-medium max-w-3xl">
            {narrative}
          </p>
        )}

        {/* Since-last-visit delta — the scannable, clickable version of the lead */}
        {showDelta && (
          <DeltaPanel delta={delta!} onFocus={onFocus} />
        )}

        {/* Headline metric strip */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
          <Metric
            icon={<Wallet className="h-3.5 w-3.5" />}
            label="Portfolio value"
            value={money(h.total_market_value)}
            sub={`${h.positions} position${h.positions === 1 ? '' : 's'}`}
          />
          <Metric
            icon={dayUp ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}
            label="Today"
            value={money(h.day_change, { signed: true })}
            sub={pct(h.day_change_pct)}
            tone={dayUp ? 'pos' : 'neg'}
          />
          <Metric
            icon={allUp ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
            label="All-time"
            value={money(h.total_unrealized, { signed: true })}
            sub={pct(h.total_unrealized_pct)}
            tone={allUp ? 'pos' : 'neg'}
          />
          <Metric
            icon={<DollarSign className="h-3.5 w-3.5" />}
            label="Annual income"
            value={`${money(h.annual_income)}`}
            sub={h.portfolio_yield_pct != null ? `${h.portfolio_yield_pct.toFixed(2)}% yield` : '—'}
          />
        </div>

        {/* Movers + Upcoming */}
        {(gainers.length > 0 || losers.length > 0 || upcoming.length > 0) && (
          <div className="grid gap-3 sm:grid-cols-2 pt-0.5">
            {(gainers.length > 0 || losers.length > 0) && (
              <div className="space-y-1.5">
                <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/35">Movers today</div>
                <div className="flex flex-wrap gap-1.5">
                  {gainers.map(m => (
                    <MoverChip key={m.ticker} ticker={m.ticker} pctVal={m.day_change_pct} tone="pos" onClick={() => onFocus(m.ticker, 'overview')} />
                  ))}
                  {losers.map(m => (
                    <MoverChip key={m.ticker} ticker={m.ticker} pctVal={m.day_change_pct} tone="neg" onClick={() => onFocus(m.ticker, 'overview')} />
                  ))}
                </div>
                {moverNews.length > 0 && (
                  <div className="space-y-1 pt-0.5">
                    {moverNews.map(n => (
                      <MoverHeadline key={n.ticker} ticker={n.ticker} tone={n.tone} news={n.news} />
                    ))}
                  </div>
                )}
              </div>
            )}

            {upcoming.length > 0 && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/35">On the calendar</div>
                  {(brief.income_next_30d ?? 0) > 0 && (
                    <div className="text-[10px] text-success/70">~{money(brief.income_next_30d)} in 30d</div>
                  )}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {upcoming.slice(0, 5).map((e, i) => {
                    const isEarn = e.type === 'earnings';
                    return (
                      <button
                        key={`${e.ticker}-${i}`}
                        onClick={() => onFocus(e.ticker, isEarn ? 'technical' : 'dividends')}
                        className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-base-200/40 px-2 py-1 text-xs transition-colors hover:bg-base-200/70"
                      >
                        <Calendar className="h-3 w-3 text-base-content/35" />
                        <span className="font-bold text-base-content/80">{e.ticker}</span>
                        <span className={`text-[9px] font-semibold px-1 rounded ${isEarn ? 'bg-secondary/20 text-secondary' : 'bg-success/15 text-success'}`}>
                          {isEarn ? 'ER' : 'Ex-Div'}
                        </span>
                        <span className="text-[10px] text-base-content/45">
                          {e.days_until === 0 ? 'today' : e.days_until === 1 ? '1d' : `${e.days_until}d`}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────────────

function Metric({ icon, label, value, sub, tone }: {
  icon: React.ReactNode; label: string; value: string; sub?: string; tone?: 'pos' | 'neg';
}) {
  const toneCls = tone === 'pos' ? 'text-success' : tone === 'neg' ? 'text-error' : 'text-base-content/90';
  const iconCls = tone === 'pos' ? 'text-success/70' : tone === 'neg' ? 'text-error/70' : 'text-base-content/35';
  return (
    <div className="rounded-xl border border-white/[0.05] bg-base-200/30 px-3 py-2.5">
      <div className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-base-content/40">
        <span className={iconCls}>{icon}</span>{label}
      </div>
      <div className={`mt-1 text-lg font-bold tabular-nums leading-none ${toneCls}`}>{value}</div>
      {sub && <div className={`mt-1 text-[11px] tabular-nums ${tone ? toneCls + '/70' : 'text-base-content/40'}`}>{sub}</div>}
    </div>
  );
}

function MoverChip({ ticker, pctVal, tone, onClick }: {
  ticker: string; pctVal: number | null; tone: 'pos' | 'neg'; onClick: () => void;
}) {
  const cls = tone === 'pos'
    ? 'border-success/25 bg-success/[0.07] text-success hover:bg-success/[0.14]'
    : 'border-error/25 bg-error/[0.07] text-error hover:bg-error/[0.14]';
  return (
    <button onClick={onClick} className={`flex items-center gap-1.5 rounded-lg border px-2 py-1 text-xs font-semibold transition-colors ${cls}`}>
      {tone === 'pos' ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
      <span className="text-base-content/85">{ticker}</span>
      <span className="tabular-nums">{pct(pctVal)}</span>
    </button>
  );
}

function BenchmarkBadge({ bm }: { bm: BriefBenchmark }) {
  const diff = bm.vs_portfolio_pp;
  const flat = Math.abs(diff) < 0.05;
  const ahead = diff >= 0;
  const cls = flat
    ? 'border-white/[0.08] bg-base-200/50 text-base-content/55'
    : ahead
      ? 'border-success/25 bg-success/[0.08] text-success'
      : 'border-error/25 bg-error/[0.08] text-error';
  const label = flat
    ? `Tracking ${bm.symbol}`
    : `${ahead ? 'Beating' : 'Lagging'} ${bm.symbol} ${Math.abs(diff).toFixed(2)}pp`;
  return (
    <span
      className={`hidden sm:flex items-center gap-1 rounded-lg border px-2 py-1 text-[10px] font-semibold ${cls}`}
      title={`Portfolio vs ${bm.symbol} today — ${bm.symbol} ${pct(bm.day_change_pct)}`}
    >
      <Target className="h-3 w-3" />
      {label}
    </span>
  );
}

function DeltaPanel({ delta, onFocus }: { delta: BriefDelta; onFocus: (t: string, tab: BriefTab) => void }) {
  const up = delta.market_change >= 0;
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 px-3 py-2.5 space-y-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest text-base-content/35">
          <History className="h-3 w-3" /> Since {delta.since_label}
        </span>
        <span className={`text-sm font-bold tabular-nums ${up ? 'text-success' : 'text-error'}`}>
          {money(delta.market_change, { signed: true })}
          {delta.market_change_pct != null && (
            <span className="ml-1 text-[11px] font-semibold opacity-80">({pct(delta.market_change_pct)})</span>
          )}
        </span>
        <span className="text-[11px] text-base-content/35">on positions you held then</span>
      </div>

      {/* Crossings — the highest-signal events */}
      {delta.crossings.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {delta.crossings.map(c => (
            <button
              key={c.ticker}
              onClick={() => onFocus(c.ticker, 'overview')}
              className={`flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-semibold transition-colors ${
                c.direction === 'into_gain'
                  ? 'border-success/30 bg-success/[0.08] text-success hover:bg-success/[0.16]'
                  : 'border-error/30 bg-error/[0.08] text-error hover:bg-error/[0.16]'
              }`}
            >
              {c.direction === 'into_gain' ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
              <span className="text-base-content/85">{c.ticker}</span>
              <span>{c.direction === 'into_gain' ? 'into gain' : 'into loss'}</span>
            </button>
          ))}
        </div>
      )}

      {/* Top contributors to the move */}
      {delta.top_contributors.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {delta.top_contributors.map(c => {
            const cu = c.amount >= 0;
            return (
              <button
                key={c.ticker}
                onClick={() => onFocus(c.ticker, 'overview')}
                className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-base-200/40 px-2 py-1 text-[11px] transition-colors hover:bg-base-200/70"
              >
                <span className="font-bold text-base-content/80">{c.ticker}</span>
                <span className={`tabular-nums font-semibold ${cu ? 'text-success' : 'text-error'}`}>
                  {money(c.amount, { signed: true })}
                </span>
                {c.pct_move != null && (
                  <span className="text-[10px] text-base-content/40 tabular-nums">{pct(c.pct_move)}</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* Sector drift / new positions footnote */}
      {(delta.sector_drift || delta.positions_added.length > 0) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-base-content/40">
          {delta.sector_drift && (
            <span>
              {delta.sector_drift.sector} {delta.sector_drift.drift_pp >= 0 ? '↑' : '↓'}
              {Math.abs(delta.sector_drift.drift_pp).toFixed(1)}pp → {delta.sector_drift.now_weight.toFixed(0)}% of book
            </span>
          )}
          {delta.positions_added.length > 0 && (
            <span>New: {delta.positions_added.join(', ')}</span>
          )}
        </div>
      )}
    </div>
  );
}

function MoverHeadline({ ticker, tone, news }: { ticker: string; tone: 'pos' | 'neg'; news: BriefMoverNews }) {
  const dot = tone === 'pos' ? 'text-success' : 'text-error';
  const body = (
    <>
      <Newspaper className={`h-3 w-3 shrink-0 mt-[1px] ${dot}`} />
      <span className="min-w-0">
        <span className="font-semibold text-base-content/70">{ticker}</span>
        <span className="text-base-content/45"> · {news.title}</span>
        {news.publisher && <span className="text-base-content/30"> — {news.publisher}</span>}
      </span>
    </>
  );
  const cls = 'flex items-start gap-1.5 text-[11px] leading-snug';
  return news.url
    ? <a href={news.url} target="_blank" rel="noopener noreferrer" className={`${cls} hover:underline`}>{body}</a>
    : <div className={cls}>{body}</div>;
}

function BriefSkeleton() {
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-base-200/20 p-4 sm:p-5 animate-pulse">
      <div className="flex items-center gap-2 mb-4">
        <div className="h-7 w-7 rounded-lg bg-base-300/40" />
        <div className="h-3 w-28 rounded bg-base-300/40" />
      </div>
      <div className="space-y-2 mb-4">
        <div className="h-3.5 w-full rounded bg-base-300/30" />
        <div className="h-3.5 w-4/5 rounded bg-base-300/30" />
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
        {[0, 1, 2, 3].map(i => <div key={i} className="h-16 rounded-xl bg-base-300/25" />)}
      </div>
    </div>
  );
}
