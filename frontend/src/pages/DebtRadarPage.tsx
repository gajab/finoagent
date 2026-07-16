import React, { useState } from 'react';
import {
  Landmark, Loader2, AlertTriangle, Sparkles, Info, Crown, Lock,
  Percent, Coins, Globe, HeartPulse, TrendingUp, Activity, Droplets,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { TickerInput } from '../components/TickerInput';
import DebtPriceHistory from '../components/DebtPriceHistory';
import { fetchDebtEntry, explainDebtInstrument, fetchDebtHistory } from '../api';
import type {
  DebtEntryResponse, DebtSignal, DebtSignalMetric, DebtSpreadPoint, DebtHistoryResponse,
} from '../types';

const EXAMPLES = ['PAAA', 'ICLO', 'JAAA', 'JBBB', 'HYG', 'LQD', 'TLT', 'AGG', 'MUB'];

const VERDICT_STYLES: Record<string, { text: string; bg: string; border: string; dot: string }> = {
  Favorable:   { text: 'text-emerald-400', bg: 'bg-emerald-500/10', border: 'border-emerald-500/30', dot: 'bg-emerald-400' },
  Neutral:     { text: 'text-amber-400',   bg: 'bg-amber-500/10',   border: 'border-amber-500/30',   dot: 'bg-amber-400' },
  Unfavorable: { text: 'text-rose-400',    bg: 'bg-rose-500/10',    border: 'border-rose-500/30',    dot: 'bg-rose-400' },
  'N/A':       { text: 'text-slate-400',   bg: 'bg-slate-500/10',   border: 'border-slate-500/30',   dot: 'bg-slate-400' },
};

const FAMILY_ICONS: Record<string, React.ReactNode> = {
  valuation:    <Percent className="w-4 h-4" />,
  carry:        <Coins className="w-4 h-4" />,
  macro:        <Globe className="w-4 h-4" />,
  credit_cycle: <HeartPulse className="w-4 h-4" />,
  momentum:     <TrendingUp className="w-4 h-4" />,
  volatility:   <Activity className="w-4 h-4" />,
  liquidity:    <Droplets className="w-4 h-4" />,
};

const ASSET_LABELS: Record<string, string> = {
  bondPosition: 'Bonds', cashPosition: 'Cash', stockPosition: 'Stocks',
  preferredPosition: 'Preferred', convertiblePosition: 'Convertibles', otherPosition: 'Other',
};

function verdictOf(score: number | null | undefined): string {
  if (score === null || score === undefined) return 'N/A';
  if (score >= 70) return 'Favorable';
  if (score >= 45) return 'Neutral';
  return 'Unfavorable';
}

const fmtPct = (n: number | null | undefined, d = 2) =>
  n === null || n === undefined ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(d)}%`;
const fmtNum = (n: number | null | undefined, d = 2) =>
  n === null || n === undefined ? '—' : n.toFixed(d);

// ── Verdict pill ────────────────────────────────────────────────────────────
const VerdictPill: React.FC<{ verdict: string; className?: string }> = ({ verdict, className = '' }) => {
  const s = VERDICT_STYLES[verdict] ?? VERDICT_STYLES['N/A'];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold border ${s.bg} ${s.text} ${s.border} ${className}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
      {verdict}
    </span>
  );
};

const ConfidenceChip: React.FC<{ level: string }> = ({ level }) => {
  const map: Record<string, string> = {
    high: 'text-emerald-400/80', medium: 'text-base-content/50', low: 'text-amber-400/80',
  };
  return <span className={`text-[10px] uppercase tracking-wide font-medium ${map[level] ?? 'text-base-content/50'}`}>{level} confidence</span>;
};

// ── Circular score ring ─────────────────────────────────────────────────────
const ScoreRing: React.FC<{ score: number | null; size?: number; stroke?: number; big?: boolean }> = ({
  score, size = 120, stroke = 10, big = false,
}) => {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const pct = score === null ? 0 : Math.max(0, Math.min(100, score)) / 100;
  const verdict = verdictOf(score);
  const s = VERDICT_STYLES[verdict] ?? VERDICT_STYLES['N/A'];
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="text-base-300" stroke="currentColor" />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} strokeLinecap="round"
          className={s.text} stroke="currentColor"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct)}
          style={{ transition: 'stroke-dashoffset 0.7s ease' }}
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className={`font-bold ${s.text} ${big ? 'text-4xl' : 'text-xl'}`}>{score === null ? '—' : Math.round(score)}</span>
        {big && <span className="text-[10px] text-base-content/40 -mt-0.5">/ 100</span>}
      </div>
    </div>
  );
};

// ── Sparkline (spread history) ──────────────────────────────────────────────
const Sparkline: React.FC<{ points: DebtSpreadPoint[] }> = ({ points }) => {
  if (!points || points.length < 3) return null;
  const vals = points.map((p) => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const w = 260, h = 56, pad = 4;
  const span = max - min || 1;
  const path = points
    .map((p, i) => {
      const x = pad + (i / (points.length - 1)) * (w - 2 * pad);
      const y = pad + (1 - (p.value - min) / span) * (h - 2 * pad);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const last = points[points.length - 1];
  const lx = w - pad, ly = pad + (1 - (last.value - min) / span) * (h - 2 * pad);
  return (
    <div>
      <svg width={w} height={h} className="overflow-visible">
        <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} className="text-secondary" />
        <circle cx={lx} cy={ly} r={2.5} className="fill-secondary" />
      </svg>
      <div className="flex justify-between text-[10px] text-base-content/40 mt-0.5">
        <span>{points[0].date.slice(0, 7)} · {Math.round(min)}bps</span>
        <span>{last.date.slice(0, 7)} · {Math.round(last.value)}bps</span>
      </div>
    </div>
  );
};

// ── Metric row ──────────────────────────────────────────────────────────────
const MetricRow: React.FC<{ m: DebtSignalMetric }> = ({ m }) => (
  <div className="flex items-start justify-between gap-3 py-1.5 border-t border-base-300/50 first:border-t-0">
    <div className="min-w-0">
      <div className="text-xs text-base-content/70">{m.label}</div>
      {m.hint && <div className="text-[10px] text-base-content/40 leading-tight mt-0.5">{m.hint}</div>}
    </div>
    <div className="text-xs font-semibold text-base-content whitespace-nowrap">{m.value ?? '—'}</div>
  </div>
);

// ── Signal family card ──────────────────────────────────────────────────────
const SignalCard: React.FC<{ sig: DebtSignal }> = ({ sig }) => {
  const s = VERDICT_STYLES[sig.verdict] ?? VERDICT_STYLES['N/A'];
  return (
    <div className={`rounded-2xl border p-4 bg-base-200/40 ${s.border}`}>
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`${s.text}`}>{FAMILY_ICONS[sig.key]}</span>
          <span className="font-semibold text-sm text-base-content truncate">{sig.family}</span>
        </div>
        <div className={`text-lg font-bold ${s.text}`}>{sig.score === null ? '—' : Math.round(sig.score)}</div>
      </div>
      <div className="flex items-center justify-between gap-2 mb-2">
        <VerdictPill verdict={sig.verdict} />
        <span className="text-[10px] text-base-content/40">weight {Math.round(sig.weight * 100)}%</span>
      </div>
      <p className="text-xs text-base-content/70 mb-2 leading-snug">{sig.headline}</p>
      <div className="mb-2">{sig.metrics.map((m, i) => <MetricRow key={i} m={m} />)}</div>
      <ConfidenceChip level={sig.confidence} />
    </div>
  );
};

// ── Live stat ───────────────────────────────────────────────────────────────
const Stat: React.FC<{ label: string; value: React.ReactNode; accent?: string; sub?: string }> = ({ label, value, accent, sub }) => (
  <div className="rounded-xl bg-base-200/40 border border-base-300/50 px-3 py-2.5">
    <div className="text-[10px] uppercase tracking-wide text-base-content/40">{label}</div>
    <div className={`text-sm font-bold ${accent ?? 'text-base-content'}`}>{value}</div>
    {sub && <div className="text-[10px] text-base-content/40">{sub}</div>}
  </div>
);

export default function DebtRadarPage({ isEmbedded = false }: { isEmbedded?: boolean } = {}) {
  const { isPremium } = useAuth();
  const [data, setData] = useState<DebtEntryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ticker, setTicker] = useState('');
  const [explain, setExplain] = useState<string | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [hist, setHist] = useState<DebtHistoryResponse | null>(null);
  const [histLoading, setHistLoading] = useState(false);

  const search = async (t: string) => {
    setLoading(true); setError(null); setData(null); setExplain(null); setTicker(t);
    // price/dividend history loads in parallel and never blocks the main scorecard
    setHist(null); setHistLoading(true);
    fetchDebtHistory(t).then(setHist).catch(() => setHist(null)).finally(() => setHistLoading(false));
    try {
      setData(await fetchDebtEntry(t));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  const runExplain = async () => {
    if (!ticker) return;
    setExplaining(true); setExplain(null);
    try {
      const r = await explainDebtInstrument(ticker);
      setExplain(r.explanation);
    } catch (e) {
      setExplain(e instanceof Error ? `⚠️ ${e.message}` : '⚠️ Failed');
    } finally {
      setExplaining(false);
    }
  };

  if (!isPremium) {
    return (
      <div className={isEmbedded ? "animate-fade-in" : "container-app py-6 sm:py-8 animate-fade-in"}>
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="relative mb-6">
            <div className="bg-base-200 rounded-full p-8"><Landmark className="w-16 h-16 text-base-content/20" /></div>
            <div className="absolute -top-1 -right-1 bg-yellow-400 rounded-full p-2 shadow-lg"><Lock className="w-4 h-4 text-gray-900" /></div>
          </div>
          <h1 className="text-3xl font-bold tracking-tight mb-3 flex items-center gap-2 justify-center">
            <Crown className="w-7 h-7 text-yellow-400" /> Premium Feature
          </h1>
          <p className="text-base-content/60 max-w-md text-base mb-2">Debt Radar is available exclusively for Premium users.</p>
          <p className="text-base-content/40 max-w-sm text-sm">Contact the admin to upgrade and unlock the bond entry-timing tracker.</p>
        </div>
      </div>
    );
  }

  const cls = data?.classification;
  const live = data?.live;
  const comp = data?.composite;
  const macro = data?.macro_snapshot;

  return (
    <div className={isEmbedded ? "animate-fade-in" : "container-app py-6 sm:py-8 animate-fade-in"}>
      {/* Header */}
      <div className="page-header mb-5">
        <h1 className="page-title tracking-tight flex items-center gap-3">
          <Landmark className="w-7 h-7 text-secondary" /> Debt Radar
        </h1>
        <p className="page-subtitle">
          Real-time entry-timing for debt instruments — classify any bond/credit ETF, see its live
          NAV premium/discount, and score the setup across seven signal families.
        </p>
      </div>

      {/* Search */}
      <div className="glass-card mb-5">
        <div className="card-body py-4">
          <TickerInput onSearch={search} loading={loading} defaultValue={ticker} />
          <div className="flex flex-wrap gap-1.5 mt-3">
            <span className="text-xs text-base-content/40 mr-1 self-center">Try:</span>
            {EXAMPLES.map((t) => (
              <button key={t} onClick={() => search(t)}
                className="text-xs px-2.5 py-1 rounded-lg bg-base-200 hover:bg-secondary/15 hover:text-secondary border border-base-300 transition-colors">
                {t}
              </button>
            ))}
          </div>
          <p className="text-[11px] text-base-content/35 mt-2">
            Enter a ticker or a 9-character CUSIP for a bond/credit ETF or fund (individual-bond CUSIPs aren't supported).
          </p>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20 text-base-content/50">
          <Loader2 className="w-6 h-6 animate-spin mr-2" /> Scoring {ticker}…
        </div>
      )}

      {error && !loading && (
        <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-5 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-rose-400 flex-shrink-0 mt-0.5" />
          <div><div className="font-semibold text-rose-400">Couldn't analyze {ticker}</div>
            <div className="text-sm text-base-content/60">{error}</div></div>
        </div>
      )}

      {data && !data.is_debt && !loading && (
        <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-5 flex items-start gap-3">
          <Info className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <div><div className="font-semibold text-amber-400">{cls?.name ?? ticker} isn't a debt instrument</div>
            <div className="text-sm text-base-content/60">{data.reject_reason}</div></div>
        </div>
      )}

      {data && data.is_debt && !loading && (
        <div className="space-y-5">
          {/* Classification + composite */}
          <div className="glass-card">
            <div className="card-body">
              <div className="flex flex-col lg:flex-row gap-6 items-start">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <h2 className="text-xl font-bold text-base-content">{data.ticker}</h2>
                    <span className="text-base-content/50 text-sm truncate">{cls?.name}</span>
                    {data.resolved_from && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-base-200 border border-base-300 text-base-content/50 font-mono">
                        CUSIP {data.resolved_from}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 flex-wrap mb-3">
                    <span className="text-xs px-2.5 py-0.5 rounded-full bg-secondary/15 text-secondary border border-secondary/30 font-medium">{cls?.class_label}</span>
                    {cls?.tier && <span className="text-xs px-2.5 py-0.5 rounded-full bg-base-200 border border-base-300">{cls.tier}</span>}
                    {cls?.rate_type && <span className="text-xs px-2.5 py-0.5 rounded-full bg-base-200 border border-base-300 capitalize">{cls.rate_type}-rate</span>}
                    {cls?.fund_family && <span className="text-xs text-base-content/40">{cls.fund_family}</span>}
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                    <Stat label="Distribution yield" value={fmtPct(live?.distribution_yield_pct)} accent="text-emerald-400" />
                    <Stat label="Premium / discount"
                      value={fmtPct(live?.premium_discount_pct)}
                      accent={(live?.premium_discount_pct ?? 0) <= 0 ? 'text-emerald-400' : 'text-rose-400'}
                      sub="vs NAV (real-time)" />
                    <Stat label="AUM" value={live?.aum_fmt ?? '—'} />
                    <Stat label="Price / NAV" value={`${fmtNum(live?.price)} / ${fmtNum(live?.nav)}`} />
                    <Stat label="Bid/ask spread" value={live?.bid_ask_bps != null ? `${Math.round(live.bid_ask_bps)} bps` : '—'} sub="liquidity" />
                    <Stat label="Expense ratio" value={live?.expense_ratio_pct != null ? `${live.expense_ratio_pct.toFixed(2)}%` : '—'} />
                  </div>
                </div>

                {/* Composite gauge */}
                <div className="flex flex-col items-center justify-center lg:border-l lg:border-base-300/60 lg:pl-6 self-stretch">
                  <div className="text-xs uppercase tracking-wide text-base-content/40 mb-2">Entry Score</div>
                  <ScoreRing score={comp?.score ?? null} size={130} stroke={12} big />
                  <VerdictPill verdict={comp?.verdict ?? 'N/A'} className="mt-3" />
                  <div className="text-[10px] text-base-content/35 mt-2 text-center max-w-[150px]">
                    ≥70 favorable · 45–70 neutral · &lt;45 unfavorable
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Stress + macro strip */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
            <Stat label="MOVE (rate vol)" value={fmtNum(live?.move, 0)} sub={live?.move_pct != null ? `${live.move_pct}th pct` : undefined} />
            <Stat label="VIX" value={fmtNum(live?.vix, 1)} sub={live?.vix_pct != null ? `${live.vix_pct}th pct` : undefined} />
            <Stat label="SOFR" value={macro?.sofr != null ? `${macro.sofr.toFixed(2)}%` : '—'} />
            <Stat label="Curve 10y-3m" value={macro?.curve_10y3m != null ? `${macro.curve_10y3m > 0 ? '+' : ''}${macro.curve_10y3m.toFixed(2)}%` : '—'} />
            <Stat label="AAA / HY OAS" value={`${macro?.oas_aaa_bps ?? '—'} / ${macro?.oas_hy_bps ?? '—'}`} sub="bps" />
            <Stat label="Fin. conditions" value={macro?.nfci != null ? macro.nfci.toFixed(2) : '—'} sub="NFCI · <0 loose" />
          </div>

          {/* Price chart + dividends */}
          {(hist || histLoading) && <DebtPriceHistory data={hist} loading={histLoading} />}

          {/* Signal families */}
          <div>
            <h3 className="text-sm font-semibold text-base-content/70 mb-3 flex items-center gap-2">
              <Activity className="w-4 h-4 text-secondary" /> Signal families
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {data.signals?.map((sig) => <SignalCard key={sig.key} sig={sig} />)}
            </div>
          </div>

          {/* Holdings / exposure + spread history */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="rounded-2xl border border-base-300/50 bg-base-200/40 p-4">
              <h3 className="text-sm font-semibold text-base-content mb-3">Exposure & holdings</h3>
              {cls?.asset_classes && Object.keys(cls.asset_classes).length > 0 && (
                <div className="space-y-1.5 mb-3">
                  {Object.entries(cls.asset_classes)
                    .filter(([, v]) => Math.abs(v) > 0.5)
                    .sort((a, b) => b[1] - a[1])
                    .map(([k, v]) => (
                      <div key={k} className="flex items-center gap-2">
                        <span className="text-xs text-base-content/60 w-20 flex-shrink-0">{ASSET_LABELS[k] ?? k}</span>
                        <div className="flex-1 h-2 rounded-full bg-base-300 overflow-hidden">
                          <div className="h-full bg-secondary/60 rounded-full" style={{ width: `${Math.max(0, Math.min(100, v))}%` }} />
                        </div>
                        <span className="text-xs font-medium text-base-content/70 w-12 text-right">{v.toFixed(1)}%</span>
                      </div>
                    ))}
                </div>
              )}
              {cls?.top_holdings && cls.top_holdings.length > 0 ? (
                <div className="space-y-1">
                  {cls.top_holdings.slice(0, 8).map((h) => (
                    <div key={h.ticker} className="flex justify-between text-xs">
                      <span className="text-base-content/70 truncate">{h.ticker} · {h.name}</span>
                      <span className="font-medium">{h.weight_pct.toFixed(1)}%</span>
                    </div>
                  ))}
                </div>
              ) : (
                cls?.holdings_note && <p className="text-[11px] text-base-content/40 leading-snug">{cls.holdings_note}</p>
              )}
            </div>

            <div className="rounded-2xl border border-base-300/50 bg-base-200/40 p-4">
              <h3 className="text-sm font-semibold text-base-content mb-1">Implied carry spread vs history</h3>
              <p className="text-[11px] text-base-content/40 mb-3">Distribution yield − SOFR (ETF-implied proxy, not a true OAS).</p>
              {data.spread_history && data.spread_history.length >= 3 ? (
                <Sparkline points={data.spread_history} />
              ) : (
                <p className="text-xs text-base-content/40">Not enough distribution history to reconstruct a spread series for this fund.</p>
              )}
            </div>
          </div>

          {/* AI explainer */}
          <div className="rounded-2xl border border-base-300/50 bg-base-200/40 p-4">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-secondary" />
                <span className="text-sm font-semibold">AI read on holdings & entry setup</span>
              </div>
              <button onClick={runExplain} disabled={explaining}
                className="text-xs px-3 py-1.5 rounded-lg bg-secondary/15 text-secondary border border-secondary/30 hover:bg-secondary/25 transition-colors disabled:opacity-50 flex items-center gap-1.5">
                {explaining ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Thinking…</> : <>Explain</>}
              </button>
            </div>
            {explain && <p className="text-sm text-base-content/75 mt-3 leading-relaxed whitespace-pre-wrap">{explain}</p>}
          </div>

          {/* Methodology footnote */}
          <p className="text-[11px] text-base-content/35 leading-relaxed">
            Data: yfinance + the keyless FRED endpoint (SOFR, Treasury curve, ICE BofA OAS, NFCI, Sahm) and the MOVE/VIX
            indices. The CLO spread figure is an ETF-implied carry proxy (true CLO OAS is not publicly available);
            tranche-level holdings aren't exposed by the data provider for some funds. Research only — not investment advice.
          </p>
        </div>
      )}
    </div>
  );
}
