/**
 * Pick & Shovel v2 — structured, filing-grounded research wizard.
 *
 * Seven steps the user steers, grounded in real ETF holdings and real primary
 * documents (SEC EDGAR filings + company investor materials) — NOT one LLM
 * riffing on itself.  Fully self-contained; does not import v1.
 *
 *   ① Components → ② ETFs & Holdings → ③ Your inputs → ④ Match →
 *   ⑤ Deep dive → ⑥ Summary → ⑦ Pick & Shovel
 */

import React, { useMemo, useState, useRef, useCallback, createContext, useContext } from 'react';
import { Link } from 'react-router-dom';
import {
  Search, Loader2, AlertCircle, Sparkles, Check, X, Pencil, Plus,
  ChevronRight, ChevronLeft, ChevronDown, Boxes, Layers, FileText, Network, Gem,
  Target, Cpu, ExternalLink, Link as LinkIcon, Image as ImageIcon,
  Type as TypeIcon, Wand2, ShieldAlert, Lightbulb, Building2, ListChecks,
  ArrowUpRight, ArrowDownRight, FlaskConical, RefreshCw, Bookmark, BookmarkCheck, MessageCircle, Star,
  BarChart2, BookOpen, AlertTriangle, Zap, ShieldCheck,
} from 'lucide-react';
import {
  v2Decompose, v2RefineComponents, v2Etfs, v2EtfHoldings, v2Ingest,
  v2ValidateCompanies, v2Match, v2DeepDive, v2Summary, v2PickShovel, v2ComponentPicks,
  v2FindComponentCompanies, trackCompany, digDeeperPickShovel,
} from '../api';
import type {
  ThemeComponent, EtfInfo, EtfHolding, UserInput, IngestResponse,
  V2Company, MatchResponse, CompanyDeepDive, ResearchSummary,
  PickShovelResponse, PickShovelCompany, DigDeeperLayer, HiddenOpportunity, ComponentPicks,
} from '../types';

// ---------------------------------------------------------------------------
// Global busy lock — one backend request at a time across the whole v2 wizard.
// `run(fn)` ignores new calls while one is in flight (saves the server) and
// `inFlight` lets every control disable + a consistent indicator show.
// ---------------------------------------------------------------------------

interface BusyCtxValue {
  inFlight: boolean;
  run: <T>(fn: () => Promise<T>) => Promise<T | undefined>;
}
const BusyCtx = createContext<BusyCtxValue>({ inFlight: false, run: async () => undefined });
const useBusy = () => useContext(BusyCtx);

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const EXAMPLE_THEMES = [
  'Humanoid robotics', 'AI data center build-out', 'Nuclear energy renaissance',
  'GLP-1 obesity drugs', 'Reshoring US manufacturing', 'Grid modernization',
];

const STEPS = [
  { n: 1, label: 'Components', icon: Boxes },
  { n: 2, label: 'ETFs & Holdings', icon: Layers },
  { n: 3, label: 'Your inputs', icon: FileText },
  { n: 4, label: 'Deep dive', icon: FlaskConical },
  { n: 5, label: 'Research & Picks', icon: Gem },
] as const;

const CATEGORY_COLORS: Record<string, string> = {
  'Mechanical': 'border-amber-500/30 bg-amber-500/5 text-amber-300',
  'Electrical & Power': 'border-yellow-500/30 bg-yellow-500/5 text-yellow-300',
  'Sensing': 'border-emerald-500/30 bg-emerald-500/5 text-emerald-300',
  'Materials': 'border-orange-500/30 bg-orange-500/5 text-orange-300',
  'Compute & Semiconductors': 'border-cyan-500/30 bg-cyan-500/5 text-cyan-300',
  'Software & AI': 'border-violet-500/30 bg-violet-500/5 text-violet-300',
  'Manufacturing & Equipment': 'border-blue-500/30 bg-blue-500/5 text-blue-300',
  'Infrastructure': 'border-teal-500/30 bg-teal-500/5 text-teal-300',
  'Services & Integration': 'border-pink-500/30 bg-pink-500/5 text-pink-300',
};
const catColor = (c: string) => CATEGORY_COLORS[c] || 'border-white/15 bg-base-100/40 text-base-content/70';

const fmtPrice = (v: number | null | undefined) =>
  v != null ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';

const holdingWeight = (h: EtfHolding): number | null =>
  h.weight != null ? h.weight : (h.weight_pct != null ? h.weight_pct : null);

// Distinct color per ETF (shared by the ETF card and its holding chips).
const ETF_COLOR_CLASSES = [
  'border-amber-500/40 text-amber-300 bg-amber-500/10',
  'border-cyan-500/40 text-cyan-300 bg-cyan-500/10',
  'border-violet-500/40 text-violet-300 bg-violet-500/10',
  'border-emerald-500/40 text-emerald-300 bg-emerald-500/10',
  'border-pink-500/40 text-pink-300 bg-pink-500/10',
  'border-blue-500/40 text-blue-300 bg-blue-500/10',
  'border-orange-500/40 text-orange-300 bg-orange-500/10',
  'border-rose-500/40 text-rose-300 bg-rose-500/10',
];

/** Average weight of a holding across the ETFs that list it (top-10). */
function avgWeight(h: EtfHolding): number | null {
  const ws = Object.values(h.etf_weights || {}).filter((w): w is number => w != null);
  if (ws.length) return Math.round((ws.reduce((a, b) => a + b, 0) / ws.length) * 100) / 100;
  return h.weight ?? null;
}

/** Recompute avg weight + in_etfs from etf_weights, ranked breadth-first
 *  (held by more ETFs = stronger conviction) then by avg weight — so a single
 *  ETF's concentrated position can't dominate the top. */
function recomputeHoldings(hs: EtfHolding[]): EtfHolding[] {
  return hs
    .map(h => ({ ...h, in_etfs: Object.keys(h.etf_weights || {}), weight: avgWeight(h) }))
    .sort((a, b) => ((b.in_etfs?.length || 0) - (a.in_etfs?.length || 0)) || ((b.weight || 0) - (a.weight || 0)));
}

// ---------------------------------------------------------------------------
// Small shared bits
// ---------------------------------------------------------------------------

function Stepper({ step, reached, go }: { step: number; reached: number; go: (n: number) => void }) {
  // Compact, wrapping row — fits all 7 on one line on desktop and wraps to a
  // second line on narrow widths. No horizontal scroll, no separators.
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {STEPS.map(s => {
        const Icon = s.icon;
        const active = s.n === step;
        const done = s.n < reached || (s.n <= reached && s.n !== step);
        const clickable = s.n <= reached;
        return (
          <button
            key={s.n}
            disabled={!clickable}
            onClick={() => clickable && go(s.n)}
            title={s.label}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold transition-all border
              ${active ? 'bg-primary/15 border-primary text-primary'
                : clickable ? 'border-white/10 text-base-content/70 hover:border-primary/40 hover:text-primary'
                : 'border-white/5 text-base-content/30 cursor-not-allowed'}`}
          >
            <span className={`flex items-center justify-center w-4 h-4 rounded-full text-[9px] flex-shrink-0 ${active ? 'bg-primary text-primary-content' : done ? 'bg-success/20 text-success' : 'bg-base-300 text-base-content/50'}`}>
              {done ? <Check className="w-2.5 h-2.5" /> : s.n}
            </span>
            <Icon className="w-3 h-3 flex-shrink-0" />
            {/* Label always shown for the active step; others reveal at lg+ to keep it tight */}
            <span className={active ? 'inline' : 'hidden lg:inline'}>{s.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function Busy({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-sm text-base-content/60">
      <Loader2 className="w-5 h-5 animate-spin text-primary" /> {label}
    </div>
  );
}

function ErrBox({ msg, onRetry }: { msg: string; onRetry?: () => void }) {
  return (
    <div className="alert alert-error rounded-2xl my-3">
      <AlertCircle className="w-4 h-4" />
      <span className="text-sm">{msg}</span>
      {onRetry && <button onClick={onRetry} className="btn btn-xs btn-ghost gap-1"><RefreshCw className="w-3 h-3" /> Retry</button>}
    </div>
  );
}

function NavBar({ onBack, onNext, nextLabel, nextDisabled, busy }: {
  onBack?: () => void; onNext?: () => void; nextLabel?: string; nextDisabled?: boolean; busy?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 pt-4 mt-2 border-t border-white/[0.05]">
      {onBack && (
        <button onClick={onBack} disabled={busy} className="btn btn-sm btn-ghost gap-1 text-base-content/60 disabled:opacity-50">
          <ChevronLeft className="w-4 h-4" /> Back
        </button>
      )}
      {onNext && (
        <button onClick={onNext} disabled={nextDisabled || busy} className="btn btn-primary btn-sm gap-2 ml-auto min-w-36">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          {nextLabel || 'Continue'}
          {!busy && <ChevronRight className="w-4 h-4" />}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 7 — self-contained company card (does not import v1)
// ---------------------------------------------------------------------------

const TIER_V2: Record<'direct' | 'enabler' | 'deep', { label: string; sub: string; icon: any; cls: string; text: string }> = {
  direct:  { label: 'Theme Core', sub: 'Direct beneficiaries', icon: Target, cls: 'border-amber-500/20', text: 'text-amber-400' },
  enabler: { label: 'The Backbone', sub: 'Infrastructure & tooling enablers', icon: Cpu, cls: 'border-cyan-500/20', text: 'text-cyan-400' },
  deep:    { label: 'Hidden Picks', sub: 'Overlooked 2nd/3rd-order beneficiaries', icon: Gem, cls: 'border-violet-500/20', text: 'text-violet-400' },
};

type TierKind = 'direct' | 'enabler' | 'deep';

const fmtPE = (v: number | null | undefined) => (v != null ? v.toFixed(1) + '×' : '—');

// Per-tier card palette (mirrors the v1 Pick & Shovel card look).
const TIER_CARD: Record<TierKind, { border: string; header: string; badge: string; badgeText: string; catPill: string }> = {
  direct:  { border: 'border-amber-500/30 hover:border-amber-500/60', header: 'from-amber-500/10 to-transparent', badge: 'bg-amber-500/15 border border-amber-500/30', badgeText: 'text-amber-400', catPill: 'bg-amber-500/10 text-amber-400 border-amber-500/20' },
  enabler: { border: 'border-cyan-500/30 hover:border-cyan-500/60', header: 'from-cyan-500/10 to-transparent', badge: 'bg-cyan-500/15 border border-cyan-500/30', badgeText: 'text-cyan-400', catPill: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20' },
  deep:    { border: 'border-violet-500/30 hover:border-violet-500/60', header: 'from-violet-500/10 to-transparent', badge: 'bg-violet-500/15 border border-violet-500/30', badgeText: 'text-violet-400', catPill: 'bg-violet-500/10 text-violet-400 border-violet-500/20' },
};

// 52-week range bar (Bloomberg-style), ported from the v1 card.
function RangeBar({ price, low, high }: { price?: number | null; low?: number | null; high?: number | null }) {
  if (!price || !low || !high || high <= low) {
    return <div className="text-[10px] text-base-content/40 italic">52W data unavailable</div>;
  }
  const pct = Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100));
  const nearHigh = pct > 80, nearLow = pct < 20;
  return (
    <div className="space-y-1">
      <div className="relative h-1.5 rounded-full bg-base-300">
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${pct}%`, background: nearHigh ? 'linear-gradient(to right,#22c55e44,#22c55e)' : nearLow ? 'linear-gradient(to right,#ef444444,#ef4444)' : 'linear-gradient(to right,#6366f144,#6366f1)' }} />
        <div className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full border-2 border-base-100 shadow" style={{ left: `${pct}%`, background: nearHigh ? '#22c55e' : nearLow ? '#ef4444' : '#6366f1' }} />
      </div>
      <div className="flex justify-between text-[9px] text-base-content/40 tabular-nums">
        <span>{fmtPrice(low)}</span>
        <span className={`font-semibold ${nearHigh ? 'text-success' : nearLow ? 'text-error' : 'text-base-content/60'}`}>{pct.toFixed(0)}% of 52W range</span>
        <span>{fmtPrice(high)}</span>
      </div>
    </div>
  );
}

async function trackPick(c: PickShovelCompany, tier: TierKind, theme: string) {
  await trackCompany({
    ticker: c.ticker, name: c.name, theme_raw: theme,
    exchange: c.exchange || null, sector: c.sector || null,
    source_tier: tier, depth_level: null, user_notes: null,
    llm_data: {
      thesis: c.thesis, catalysts: c.catalysts, revenue_exposure: c.revenue_exposure,
      earnings_signal: c.earnings_signal, risk: c.risk,
      supply_chain_role: c.supply_chain_role, hidden_link: c.hidden_link,
    },
    financial_data: {
      price: c.price, price_chg_1d: c.price_chg_1d, pe_ratio: c.pe_ratio,
      forward_pe: c.forward_pe, market_cap: c.market_cap,
      week52_high: c.week52_high, week52_low: c.week52_low, sector: c.sector,
    },
  } as any);
}

/** Shared deep-dive result body (clients / suppliers / alpha / sources). */
function DeepDivePanel({ d }: { d: CompanyDeepDive }) {
  return (
    <div className="mt-2.5 space-y-2.5">
      {d.model_only ? (
        <div className="text-[10px] px-2 py-1 rounded-md border border-warning/30 bg-warning/10 text-warning flex items-center gap-1 w-fit">
          <ShieldAlert className="w-3 h-3" /> No filings/documents found — model-only (unverified)
        </div>
      ) : (
        <div className="text-[10px] px-2 py-1 rounded-md border border-success/30 bg-success/10 text-success flex items-center gap-1 w-fit">
          <Check className="w-3 h-3" /> Grounded in {d.sources.length} primary document{d.sources.length === 1 ? '' : 's'}
        </div>
      )}
      <p className="text-[11px] text-base-content/75">{d.what_it_does}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
        <div className="rounded-lg border border-green-500/15 bg-green-500/5 p-2">
          <div className="text-[9px] uppercase tracking-wide text-green-400/80 mb-1">Clients</div>
          {d.clients.length ? d.clients.map((x, i) => (
            <div key={i} className="text-[11px] text-base-content/75">{x.ticker ? <span className="font-mono text-green-300 mr-1">{x.ticker}</span> : null}{x.name}{x.evidence ? <span className="text-base-content/40"> — {x.evidence}</span> : null}</div>
          )) : <div className="text-[10px] text-base-content/40 italic">none surfaced</div>}
        </div>
        <div className="rounded-lg border border-cyan-500/15 bg-cyan-500/5 p-2">
          <div className="text-[9px] uppercase tracking-wide text-cyan-400/80 mb-1">Suppliers</div>
          {d.suppliers.length ? d.suppliers.map((x, i) => (
            <div key={i} className="text-[11px] text-base-content/75">{x.ticker ? <span className="font-mono text-cyan-300 mr-1">{x.ticker}</span> : null}{x.name}{x.evidence ? <span className="text-base-content/40"> — {x.evidence}</span> : null}</div>
          )) : <div className="text-[10px] text-base-content/40 italic">none surfaced</div>}
        </div>
      </div>
      {d.hidden_opportunities.length > 0 && (
        <div className="rounded-lg border border-violet-500/20 bg-violet-500/5 p-2">
          <div className="text-[9px] uppercase tracking-wide text-violet-400/80 mb-1 flex items-center gap-1"><Gem className="w-3 h-3" /> Hidden alpha — who needs them next</div>
          {d.hidden_opportunities.map((o, i) => (
            <div key={i} className="text-[11px] text-base-content/75 mb-1">
              <span className="text-violet-300 font-medium">{o.beneficiary}</span> — {o.insight}
              {o.why_nonobvious && <span className="text-base-content/40"> ({o.why_nonobvious})</span>}
            </div>
          ))}
        </div>
      )}
      {d.social && (d.social.summary || (d.social.themes && d.social.themes.length > 0)) && (
        <div className="rounded-lg border border-sky-500/20 bg-sky-500/5 p-2">
          <div className="text-[9px] uppercase tracking-wide text-sky-400/80 mb-1 flex items-center gap-1">
            <MessageCircle className="w-3 h-3" /> Social chatter
            {d.social.sentiment && <span className="ml-1 px-1 py-0.5 rounded bg-sky-500/10 text-sky-300 normal-case">{d.social.sentiment}</span>}
            <span className="ml-1 text-base-content/30 normal-case">(retail leads — not fact)</span>
          </div>
          {d.social.summary && <p className="text-[11px] text-base-content/75">{d.social.summary}</p>}
          {d.social.themes && d.social.themes.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1">
              {d.social.themes.map((t, i) => <span key={i} className="text-[9px] px-1.5 py-0.5 rounded-full border border-sky-500/20 text-sky-300/80">{t}</span>)}
            </div>
          )}
          {d.social.mentioned && d.social.mentioned.length > 0 && (
            <div className="text-[10px] text-base-content/60 mt-1">
              Mentioned: {d.social.mentioned.map((m, i) => <span key={i}>{i > 0 ? ', ' : ''}{m.ticker ? <span className="font-mono text-sky-300">{m.ticker}</span> : null}{m.ticker ? ' ' : ''}{m.name}</span>)}
            </div>
          )}
        </div>
      )}
      {d.sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {d.sources.map((s, i) => (
            <a key={i} href={s.url} target="_blank" rel="noreferrer" className="text-[9px] px-1.5 py-0.5 rounded border border-white/10 text-base-content/50 hover:text-primary hover:border-primary/30 flex items-center gap-1">
              <FileText className="w-2.5 h-2.5" /> {s.type}{s.date ? ` · ${s.date}` : ''}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

interface CardActions {
  theme: string;
  tracked: Set<string>;
  dropped: Set<string>;
  dives: Record<string, CompanyDeepDive | 'loading' | 'error'>;
  spawning: string | null;
  depth: number;
  inFlight?: boolean;
  onTrack: (c: PickShovelCompany) => void;
  onDrop: (ticker: string) => void;
  onDeepDive: (c: PickShovelCompany) => void;
  onSpawn: (c: PickShovelCompany) => void;
}

function RichCard({ c, tier, theme, tracked, dropped, dives, spawning, depth, inFlight, onTrack, onDrop, onDeepDive, onSpawn }:
  { c: PickShovelCompany; tier: TierKind; accent?: string } & CardActions) {
  const [expanded, setExpanded] = useState(false);
  if (dropped.has(c.ticker)) return null;
  const pal = TIER_CARD[tier];
  const chg = c.price_chg_1d;
  const up = chg != null && chg >= 0;
  const dive = dives[c.ticker];
  const isTracked = tracked.has(c.ticker);
  const diveDone = dive && dive !== 'loading' && dive !== 'error';
  const listed = !c.unlisted && c.ticker && c.ticker !== '—';
  const hasMore = c.why_this_not_another || c.why_overlooked || c.discovery_insight || c.revenue_exposure || c.earnings_signal || c.risk;

  return (
    <div className={`rounded-2xl border bg-base-100/60 backdrop-blur overflow-hidden transition-all duration-200 ${pal.border}`}>
      {/* Header */}
      <div className={`bg-gradient-to-b ${pal.header} px-4 pt-3 pb-2`}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded-lg ${pal.badge} ${pal.badgeText}`}>{c.ticker}</span>
              {c.supply_kind === 'documented' && <span className="text-[8px] uppercase tracking-wide px-1.5 py-0.5 rounded-full border border-green-500/40 bg-green-500/10 text-green-300" title="Named as a supplier in the anchors' filings">documented</span>}
              {c.supply_kind === 'inferred' && <span className="text-[8px] uppercase tracking-wide px-1.5 py-0.5 rounded-full border border-amber-500/40 bg-amber-500/10 text-amber-300" title="Likely supplier inferred from what the anchors make & need">inferred</span>}
              {c.sector && <span className="text-[9px] text-base-content/40 uppercase tracking-wide">{c.sector}</span>}
            </div>
            <div className="text-sm font-semibold text-base-content/90 mt-0.5 leading-tight">{c.name}</div>
            {c.verified === true && (
              <div className="flex items-center gap-1 mt-1 text-[9px] font-medium text-emerald-400/80 bg-emerald-500/10 border border-emerald-500/20 rounded-md px-1.5 py-0.5 w-fit" title={c.verification_note || 'Ticker/identity verified'}>
                <ShieldCheck className="w-2.5 h-2.5 flex-shrink-0" /> Verified
              </div>
            )}
            {c.verified === false && (
              <div className="flex items-center gap-1 mt-1 text-[9px] font-medium text-amber-400/90 bg-amber-500/10 border border-amber-500/20 rounded-md px-1.5 py-0.5 w-fit" title={c.verification_note || 'Evidence confidence is low — verify before acting'}>
                <ShieldAlert className="w-2.5 h-2.5 flex-shrink-0" /> Low evidence confidence
              </div>
            )}
          </div>
          {listed && (
            <div className="text-right flex-shrink-0">
              <div className="text-lg font-bold tabular-nums">{fmtPrice(c.price)}</div>
              {chg != null && (
                <div className={`flex items-center justify-end gap-0.5 text-xs font-semibold ${up ? 'text-success' : 'text-error'}`}>
                  {up ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}{Math.abs(chg).toFixed(2)}%
                </div>
              )}
              {c.market_cap && <div className="text-[9px] text-base-content/40 mt-0.5">{c.market_cap}</div>}
            </div>
          )}
        </div>
      </div>

      {/* P/E row */}
      {listed && (
        <div className="px-4 py-2 border-b border-white/[0.04] flex items-center gap-4">
          <div className="text-center"><div className="text-[9px] uppercase tracking-wider text-base-content/50">P/E (TTM)</div><div className="text-sm font-semibold tabular-nums">{fmtPE(c.pe_ratio)}</div></div>
          <div className="w-px h-6 bg-white/[0.06]" />
          <div className="text-center"><div className="text-[9px] uppercase tracking-wider text-base-content/50">Fwd P/E</div><div className={`text-sm font-semibold tabular-nums ${c.forward_pe && c.pe_ratio && c.forward_pe < c.pe_ratio ? 'text-success' : ''}`}>{fmtPE(c.forward_pe)}</div></div>
          {c.forward_pe && c.pe_ratio && (
            <><div className="w-px h-6 bg-white/[0.06]" /><div className="text-center"><div className="text-[9px] uppercase tracking-wider text-base-content/50">Growth</div><div className={`text-xs font-semibold ${c.forward_pe < c.pe_ratio ? 'text-success' : 'text-warning'}`}>{c.forward_pe < c.pe_ratio ? '↓ contracting' : '↑ expanding'}</div></div></>
          )}
        </div>
      )}

      {/* 52W range */}
      {listed && (
        <div className="px-4 py-2.5 border-b border-white/[0.04]">
          <div className="text-[9px] uppercase tracking-wider text-base-content/50 mb-1.5">52-Week Range</div>
          <RangeBar price={c.price} low={c.week52_low} high={c.week52_high} />
        </div>
      )}

      {/* Body */}
      <div className="px-4 py-3">
        <div className="flex items-start gap-1.5 mb-2">
          <Lightbulb className="w-3 h-3 text-base-content/50 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-base-content/80 leading-relaxed">{c.thesis}</p>
        </div>
        {c.provenance && (
          <div className="text-[10px] text-base-content/55 mb-2 flex items-start gap-1">
            <span className="text-primary flex-shrink-0">◀ why:</span><span className="italic">{c.provenance}</span>
          </div>
        )}
        {c.catalysts && c.catalysts.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-2">
            {c.catalysts.map((cat, i) => <span key={i} className={`text-[9px] px-1.5 py-0.5 rounded-full border ${pal.catPill}`}>{cat}</span>)}
          </div>
        )}
        {c.supply_chain_role && (
          <div className="flex items-start gap-1.5 mb-1.5"><Cpu className="w-3 h-3 text-cyan-400/70 mt-0.5 flex-shrink-0" /><div><div className="text-[9px] uppercase text-base-content/40 mb-0.5">Supply Chain Role</div><div className="text-[11px] text-base-content/80 font-medium">{c.supply_chain_role}</div></div></div>
        )}
        {c.hidden_link && (
          <div className="flex items-start gap-1.5 mb-1.5"><Gem className="w-3 h-3 text-violet-400/70 mt-0.5 flex-shrink-0" /><div><div className="text-[9px] uppercase text-base-content/40 mb-0.5">Hidden Connection</div><div className="text-[11px] text-base-content/80 font-medium">{c.hidden_link}</div></div></div>
        )}
        {hasMore && (
          <button onClick={() => setExpanded(e => !e)} className="text-[10px] text-base-content/50 hover:text-base-content flex items-center gap-1 mt-1">
            <ChevronRight className={`w-3 h-3 transition-transform ${expanded ? 'rotate-90' : ''}`} />{expanded ? 'Less detail' : 'More detail'}
          </button>
        )}
        {expanded && (
          <div className="mt-2 space-y-2 border-t border-white/[0.04] pt-2">
            {c.why_this_not_another && <Detail icon={Target} cls="text-amber-400/70" label="Competitive Moat" text={c.why_this_not_another} />}
            {c.why_overlooked && <Detail icon={Gem} cls="text-violet-400/70" label="Why It's Overlooked" text={c.why_overlooked} />}
            {c.discovery_insight && <Detail icon={Zap} cls="text-violet-400/70" label="Discovery Insight" text={c.discovery_insight} />}
            {c.revenue_exposure && <Detail icon={BarChart2} cls="text-base-content/40" label="Theme Exposure" text={c.revenue_exposure} />}
            {c.earnings_signal && <Detail icon={BookOpen} cls="text-base-content/40" label="Earnings Signal" text={c.earnings_signal} italic />}
            {c.risk && <Detail icon={AlertTriangle} cls="text-warning/70" label="Key Risk" text={c.risk} warn />}
          </div>
        )}
      </div>

      {/* Deep-dive results (once done) */}
      {diveDone && <div className="px-4 pb-2"><DeepDivePanel d={dive as CompanyDeepDive} /></div>}

      {/* Footer actions */}
      <div className="px-4 pb-3 flex flex-wrap gap-2">
        {listed && (
          <Link to={`/dashboard?ticker=${c.ticker}`} target="_blank" rel="noopener noreferrer" className="flex items-center justify-center gap-1 flex-1 min-w-[88px] py-1.5 rounded-xl text-xs font-semibold border border-white/[0.08] text-base-content/60 hover:text-primary hover:border-primary/40 hover:bg-primary/5 transition-all">
            <ExternalLink className="w-3 h-3" /> Analyze
          </Link>
        )}
        {listed && (
          <button onClick={() => onTrack(c)} disabled={isTracked || inFlight} title={isTracked ? 'Already tracking' : 'Track this company'}
            className={`flex items-center justify-center gap-1 px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all disabled:opacity-50 ${isTracked ? 'border-success/40 bg-success/10 text-success cursor-default' : 'border-white/[0.08] text-base-content/60 hover:border-success/40 hover:bg-success/10 hover:text-success'}`}>
            {isTracked ? <BookmarkCheck className="w-3 h-3" /> : <Bookmark className="w-3 h-3" />}{isTracked ? 'Tracked' : 'Track'}
          </button>
        )}
        {listed && !diveDone && (
          <button onClick={() => onDeepDive(c)} disabled={dive === 'loading' || inFlight} title="Pull this company's filings & supply chain"
            className="flex items-center justify-center gap-1 px-3 py-1.5 rounded-xl text-xs font-semibold border border-white/[0.08] text-base-content/60 hover:border-primary/40 hover:bg-primary/5 hover:text-primary transition-all disabled:opacity-50">
            {dive === 'loading' ? <Loader2 className="w-3 h-3 animate-spin" /> : <FlaskConical className="w-3 h-3" />}{dive === 'error' ? 'Retry deep dive' : 'Deep dive'}
          </button>
        )}
        <button onClick={() => onDrop(c.ticker)} title="Drop from this analysis"
          className="flex items-center justify-center px-2.5 py-1.5 rounded-xl text-xs border border-white/[0.06] text-base-content/30 hover:border-error/30 hover:text-error hover:bg-error/5 transition-all ml-auto">
          <X className="w-3 h-3" />
        </button>
      </div>

      {/* Dig deeper into this company's suppliers */}
      {diveDone && depth < 3 && (
        <div className="px-4 pb-3">
          <button onClick={() => onSpawn(c)} disabled={spawning === c.ticker || inFlight}
            className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-xl text-[11px] font-semibold border border-violet-500/30 bg-violet-500/5 text-violet-300 hover:bg-violet-500/10 transition-all disabled:opacity-50">
            {spawning === c.ticker ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Gem className="w-3.5 h-3.5" />} Find suppliers of {c.ticker}
          </button>
        </div>
      )}
    </div>
  );
}

function Detail({ icon: Icon, cls, label, text, italic, warn }: { icon: any; cls: string; label: string; text: string; italic?: boolean; warn?: boolean }) {
  return (
    <div className="flex items-start gap-1.5">
      <Icon className={`w-3 h-3 mt-0.5 flex-shrink-0 ${cls}`} />
      <div><div className="text-[9px] uppercase text-base-content/40 mb-0.5">{label}</div><div className={`text-[11px] ${warn ? 'text-warning/80' : 'text-base-content/75'} ${italic ? 'italic' : ''}`}>{text}</div></div>
    </div>
  );
}

function RichTier({ tier, companies, ...actions }: { tier: TierKind; companies: PickShovelCompany[] } & CardActions) {
  const cfg = TIER_V2[tier];
  const Icon = cfg.icon;
  const visible = companies.filter(c => !actions.dropped.has(c.ticker));
  if (!companies || companies.length === 0) return null;
  return (
    <div className="space-y-3">
      <div className={`flex items-center gap-3 pb-2 border-b ${cfg.cls}`}>
        <Icon className={`w-5 h-5 ${cfg.text}`} />
        <div><div className="text-base font-bold">{cfg.label}</div><div className="text-[11px] text-base-content/50">{cfg.sub}</div></div>
        <span className={`ml-auto text-[10px] font-semibold ${cfg.text}`}>{visible.length}</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {companies.map(c => <RichCard key={c.ticker} c={c} tier={tier} accent={cfg.text} {...actions} />)}
      </div>
    </div>
  );
}

function DeeperLayerView({ layer, ...actions }: { layer: DigDeeperLayer } & CardActions) {
  return (
    <div className="space-y-3">
      <div className="rounded-2xl border border-fuchsia-500/30 bg-fuchsia-500/5 p-4">
        <div className="flex items-center gap-3 mb-1">
          <span className="text-xs font-mono font-bold px-2 py-0.5 rounded-full border border-fuchsia-500/30 text-fuchsia-300">DEPTH {layer.depth_level}</span>
          <span className="text-sm font-bold text-fuchsia-300">{layer.depth_label}</span>
        </div>
        {layer.depth_rationale && <p className="text-xs text-base-content/65 leading-relaxed">{layer.depth_rationale}</p>}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {layer.companies.map(c => <RichCard key={c.ticker} c={c} tier="deep" accent="text-fuchsia-400" {...actions} />)}
      </div>
    </div>
  );
}

/** Recursive pick-and-shovel level: tiers + dig-deeper + per-card deep-dive that
 *  can spawn the next grounded level. */
function PickLevel({ theme, result, depth, emphasis, onAlpha }:
  { theme: string; result: PickShovelResponse; depth: number; emphasis: string[];
    onAlpha: (items: HiddenOpportunity[], source: string) => void }) {
  const [tracked, setTracked] = useState<Set<string>>(new Set());
  const [dropped, setDropped] = useState<Set<string>>(new Set());
  const [dives, setDives] = useState<Record<string, CompanyDeepDive | 'loading' | 'error'>>({});
  const [children, setChildren] = useState<{ ticker: string; result: PickShovelResponse }[]>([]);
  const [spawning, setSpawning] = useState<string | null>(null);
  const [layers, setLayers] = useState<DigDeeperLayer[]>([]);
  const [digLoading, setDigLoading] = useState(false);
  const [digErr, setDigErr] = useState<string | null>(null);

  const allShown = () => Array.from(new Set([
    ...result.direct_plays, ...result.enablers, ...result.deep_picks,
    ...layers.flatMap(l => l.companies),
  ].map(c => c.ticker)));

  const deepDive = async (c: PickShovelCompany) => {
    setDives(d => ({ ...d, [c.ticker]: 'loading' }));
    try {
      const res = await v2DeepDive(theme, c.ticker, c.name);
      setDives(d => ({ ...d, [c.ticker]: res }));
      onAlpha(res.hidden_opportunities || [], c.ticker);
    } catch { setDives(d => ({ ...d, [c.ticker]: 'error' })); }
  };

  const spawn = async (c: PickShovelCompany) => {
    const dive = dives[c.ticker];
    if (!dive || dive === 'loading' || dive === 'error') return;
    if (children.some(x => x.ticker === c.ticker)) return;
    setSpawning(c.ticker);
    try {
      const res = await v2PickShovel(theme, [dive], {}, emphasis);
      setChildren(ch => [...ch, { ticker: c.ticker, result: res }]);
    } catch { /* surfaced via no child added */ }
    finally { setSpawning(null); }
  };

  const track = async (c: PickShovelCompany, tier: TierKind) => {
    try { await trackPick(c, tier, theme); setTracked(t => new Set([...t, c.ticker])); }
    catch (e: any) { alert(e?.message || 'Track failed'); }
  };
  const drop = (t: string) => setDropped(s => new Set([...s, t]));

  const digDeeper = async () => {
    setDigLoading(true); setDigErr(null);
    const anchorSrc = layers.length ? layers[layers.length - 1].companies : result.deep_picks;
    try {
      const layer = await digDeeperPickShovel({
        theme,
        already_shown: allShown(),
        anchor_companies: anchorSrc.slice(0, 6).map(c => ({
          ticker: c.ticker, name: c.name,
          supply_chain_role: c.supply_chain_role || null, hidden_link: c.hidden_link || null, thesis: c.thesis || null,
        })),
        depth_level: layers.length + 1,
      });
      setLayers(p => [...p, layer]);
    } catch (e: any) { setDigErr(e?.message || 'Dig deeper failed'); }
    finally { setDigLoading(false); }
  };

  const actionsFor = (tier: TierKind): CardActions => ({
    theme, tracked, dropped, dives, spawning, depth,
    onTrack: (c) => track(c, tier), onDrop: drop, onDeepDive: deepDive, onSpawn: spawn,
  });

  return (
    <div className="space-y-5">
      <RichTier tier="direct" companies={result.direct_plays} {...actionsFor('direct')} />
      <RichTier tier="enabler" companies={result.enablers} {...actionsFor('enabler')} />
      <RichTier tier="deep" companies={result.deep_picks} {...actionsFor('deep')} />
      {layers.map(l => <DeeperLayerView key={l.depth_level} layer={l} {...actionsFor('deep')} />)}

      <div className="flex flex-col items-center gap-2 pt-1">
        {digErr && <ErrBox msg={digErr} onRetry={digDeeper} />}
        <button onClick={digDeeper} disabled={digLoading}
          className="flex items-center gap-2 px-5 py-2.5 rounded-2xl border border-violet-500/30 bg-violet-500/5 hover:bg-violet-500/10 text-sm font-semibold text-violet-300 disabled:opacity-50">
          {digLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Gem className="w-4 h-4" />}
          {layers.length === 0 ? 'Dig Deeper — suppliers, clients & bottlenecks' : `Go Deeper — Level ${layers.length + 1}`}
        </button>
      </div>

      {children.map(ch => (
        <div key={ch.ticker} className="rounded-2xl border border-violet-500/20 bg-violet-500/[0.03] p-4">
          <div className="text-xs font-bold uppercase tracking-wide text-violet-300 mb-3 flex items-center gap-1.5">
            <Gem className="w-4 h-4" /> Next level — pick &amp; shovels from {ch.ticker}'s deep dive
          </div>
          <PickLevel theme={theme} result={ch.result} depth={depth + 1} emphasis={emphasis} onAlpha={onAlpha} />
        </div>
      ))}
    </div>
  );
}

// ===========================================================================
// SupplierPicks — flat, recursive supplier list for a component (no tiers)
// ===========================================================================

function SupplierPicks({ theme, data, depth, emphasis, onAlpha }:
  { theme: string; data: ComponentPicks; depth: number; emphasis: string[];
    onAlpha: (items: HiddenOpportunity[], source: string) => void }) {
  const { inFlight, run } = useBusy();
  const [tracked, setTracked] = useState<Set<string>>(new Set());
  const [dropped, setDropped] = useState<Set<string>>(new Set());
  const [dives, setDives] = useState<Record<string, CompanyDeepDive | 'loading' | 'error'>>({});
  const [children, setChildren] = useState<{ key: string; ticker: string; data: ComponentPicks }[]>([]);
  const [spawning, setSpawning] = useState<string | null>(null);

  const deepDive = (c: PickShovelCompany) => run(async () => {
    setDives(d => ({ ...d, [c.ticker]: 'loading' }));
    try {
      const res = await v2DeepDive(theme, c.ticker, c.name);
      setDives(d => ({ ...d, [c.ticker]: res }));
      onAlpha(res.hidden_opportunities || [], c.ticker);
    } catch { setDives(d => ({ ...d, [c.ticker]: 'error' })); }
  });
  const spawn = (c: PickShovelCompany) => run(async () => {
    if (children.some(x => x.ticker === c.ticker)) return;
    const dive = dives[c.ticker];
    const dlist = dive && dive !== 'loading' && dive !== 'error' ? [dive] : [];
    setSpawning(c.ticker);
    try {
      const res = await v2ComponentPicks(theme, c.name, [{ ticker: c.ticker, name: c.name }], dlist, emphasis);
      setChildren(ch => [...ch, { key: c.ticker, ticker: c.ticker, data: res }]);
    } catch { /* ignore */ }
    finally { setSpawning(null); }
  });
  const track = (c: PickShovelCompany) => run(async () => {
    try { await trackPick(c, 'enabler', theme); setTracked(s => new Set([...s, c.ticker])); }
    catch (e: any) { alert(e?.message || 'Track failed'); }
  });
  const drop = (t: string) => setDropped(s => new Set([...s, t]));

  const actions: CardActions = { theme, tracked, dropped, dives, spawning, depth, inFlight, onTrack: track, onDrop: drop, onDeepDive: deepDive, onSpawn: spawn };

  return (
    <div className="space-y-3">
      {data.summary && <p className="text-[11px] text-base-content/60 italic">{data.summary}</p>}
      {data.suppliers.length === 0 ? (
        <div className="text-[11px] text-base-content/40 italic">No suppliers surfaced for these companies.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
          {data.suppliers.map(s => <RichCard key={s.ticker + s.name} c={s} tier="enabler" accent="text-cyan-300" {...actions} />)}
        </div>
      )}
      {children.map(ch => (
        <div key={ch.key} className="rounded-xl border border-cyan-500/20 bg-cyan-500/[0.03] p-3">
          <div className="text-[11px] font-bold uppercase tracking-wide text-cyan-300 mb-2 flex items-center gap-1"><Gem className="w-3.5 h-3.5" /> Suppliers of {ch.ticker}</div>
          <SupplierPicks theme={theme} data={ch.data} depth={depth + 1} emphasis={emphasis} onAlpha={onAlpha} />
        </div>
      ))}
    </div>
  );
}

// ===========================================================================
// Main
// ===========================================================================

/** One accumulating alpha lead (kept for the whole session; emphasized ones feed the LLM). */
interface AlphaItem {
  id: string;
  insight: string;
  beneficiary?: string;
  source: string;       // ticker it came from, or "summary"
  emphasized: boolean;
}

export function PickAndShovelV2() {
  const [phase, setPhase] = useState<'input' | 'wizard'>('input');
  const [theme, setTheme] = useState('');
  const [step, setStep] = useState(1);
  const [reached, setReached] = useState(1);

  // §1
  const [themeTitle, setThemeTitle] = useState('');
  const [themeSummary, setThemeSummary] = useState('');
  const [components, setComponents] = useState<ThemeComponent[]>([]);
  const [refineInput, setRefineInput] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  // Per-component discovered/added companies (bottom-up universe for ETF-less themes)
  const [componentCompanies, setComponentCompanies] = useState<Record<string, V2Company[]>>({});
  const [compAddDraft, setCompAddDraft] = useState<Record<string, string>>({});
  const [findingCid, setFindingCid] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState('');

  // §2
  const [etfs, setEtfs] = useState<EtfInfo[]>([]);
  const [holdings, setHoldings] = useState<EtfHolding[]>([]);
  const [selectedHoldings, setSelectedHoldings] = useState<Set<string>>(new Set());
  const [addEtfTicker, setAddEtfTicker] = useState('');

  // §3
  const [addTicker, setAddTicker] = useState('');
  const [userCompanies, setUserCompanies] = useState<V2Company[]>([]);
  const [inputDraft, setInputDraft] = useState('');
  const [pendingInputs, setPendingInputs] = useState<UserInput[]>([]);
  const [ingest, setIngest] = useState<IngestResponse | null>(null);
  const [acceptedSuggestions, setAcceptedSuggestions] = useState<V2Company[]>([]);

  // working company set (built leaving §3)
  const [companies, setCompanies] = useState<V2Company[]>([]);

  // §4 / §5 / §6 / §7
  const [matchRes, setMatchRes] = useState<MatchResponse | null>(null);
  const [matchAddDraft, setMatchAddDraft] = useState<Record<string, { ticker: string; how: string }>>({});
  // Merged ⑤ — per-component collapse + per-component spawned picks + per-company dive expand
  const [expandedComponents, setExpandedComponents] = useState<Set<string>>(new Set());  // empty = all collapsed on render
  const [insightsOpen, setInsightsOpen] = useState(false);  // step-5 insights panel collapsed by default
  const [componentPicks, setComponentPicks] = useState<Record<string, ComponentPicks | 'loading' | 'error'>>({});
  const [expandedDive, setExpandedDive] = useState<Set<string>>(new Set());
  const [trackedTickers, setTrackedTickers] = useState<Set<string>>(new Set());
  const [deepDives, setDeepDives] = useState<Record<string, CompanyDeepDive | 'loading' | 'error'>>({});
  const [runningAll, setRunningAll] = useState(false);
  const [summary, setSummary] = useState<ResearchSummary | null>(null);
  const [pickResult, setPickResult] = useState<PickShovelResponse | null>(null);
  // Accumulating alpha pool — never replaced; emphasized items feed later LLM steps.
  const [alphaPool, setAlphaPool] = useState<AlphaItem[]>([]);

  // generic per-step loading / error
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Global in-flight lock — one backend request at a time across the wizard.
  const [inFlight, setInFlight] = useState(false);
  const inFlightRef = useRef(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    if (inFlightRef.current) return undefined;  // a request is already running — ignore (saves the server)
    inFlightRef.current = true; setInFlight(true);
    try { return await fn(); }
    finally { inFlightRef.current = false; setInFlight(false); }
  }, []);
  const busyValue = useMemo<BusyCtxValue>(() => ({ inFlight, run }), [inFlight, run]);

  const goStep = (n: number) => { setErr(null); setStep(n); setReached(r => Math.max(r, n)); };

  // ---- §1: start -----------------------------------------------------------
  const start = (t?: string) => run(async () => {
    const q = (t ?? theme).trim();
    if (!q) return;
    setTheme(q);
    setPhase('wizard');
    setStep(1); setReached(1);
    // Fresh session for a new theme.
    setEtfs([]); setHoldings([]); setSelectedHoldings(new Set());
    setComponentCompanies({}); setCompAddDraft({});
    setUserCompanies([]); setAcceptedSuggestions([]); setIngest(null); setPendingInputs([]);
    setCompanies([]); setMatchRes(null); setMatchAddDraft({});
    setDeepDives({}); setSummary(null); setPickResult(null); setAlphaPool([]);
    setBusy(true); setErr(null);
    try {
      const r = await v2Decompose(q);
      setThemeTitle(r.theme_title); setThemeSummary(r.summary); setComponents(r.components);
    } catch (e: any) { setErr(e?.message || 'Failed to analyze theme'); }
    finally { setBusy(false); }
  });

  const refineComponents = () => run(async () => {
    if (!refineInput.trim()) return;
    setBusy(true); setErr(null);
    try {
      const r = await v2RefineComponents(theme, components, refineInput);
      setComponents(r.components); setRefineInput('');
    } catch (e: any) { setErr(e?.message || 'Refine failed'); }
    finally { setBusy(false); }
  });

  const deleteComponent = (id: string) => setComponents(cs => cs.filter(c => c.id !== id));
  const saveEdit = (id: string) => {
    setComponents(cs => cs.map(c => c.id === id ? { ...c, name: editDraft.trim() || c.name } : c));
    setEditingId(null);
  };

  // ---- §1: per-component company discovery ---------------------------------
  const findCompanies = (comp: ThemeComponent) => run(async () => {
    setFindingCid(comp.id); setErr(null);
    try {
      const existing = (componentCompanies[comp.id] || []).map(c => c.ticker);
      const r = await v2FindComponentCompanies(theme, comp.name, comp.description, existing);
      setComponentCompanies(m => {
        const have = new Set((m[comp.id] || []).map(c => c.ticker));
        const add = r.companies.filter(c => !have.has(c.ticker)).map(c => ({ ...c, source: 'component' as const }));
        return { ...m, [comp.id]: [...(m[comp.id] || []), ...add] };
      });
    } catch (e: any) { setErr(e?.message || 'Find companies failed'); }
    finally { setFindingCid(null); }
  });
  const removeComponentCompany = (cid: string, ticker: string) =>
    setComponentCompanies(m => ({ ...m, [cid]: (m[cid] || []).filter(c => c.ticker !== ticker) }));
  const addComponentCompanyManual = (comp: ThemeComponent) => run(async () => {
    const t = (compAddDraft[comp.id] || '').trim().toUpperCase();
    if (!t || (componentCompanies[comp.id] || []).some(c => c.ticker === t)) { setCompAddDraft(d => ({ ...d, [comp.id]: '' })); return; }
    try {
      const r = await v2ValidateCompanies([t]);
      const co = r.companies[0] ? { ...r.companies[0], source: 'component' as const } : { ticker: t, name: t, source: 'component' as const };
      setComponentCompanies(m => ({ ...m, [comp.id]: [...(m[comp.id] || []), co] }));
      setCompAddDraft(d => ({ ...d, [comp.id]: '' }));
    } catch (e: any) { setErr(e?.message || 'Could not validate ticker'); }
  });

  // ---- §2: ETFs ------------------------------------------------------------
  const loadEtfs = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await v2Etfs(theme, components);
      setEtfs(r.etfs); setHoldings(recomputeHoldings(r.holdings));
      setSelectedHoldings(new Set(r.holdings.map(h => h.ticker)));
    } catch (e: any) { setErr(e?.message || 'ETF discovery failed'); }
    finally { setBusy(false); }
  };

  const addEtf = () => run(async () => {
    const t = addEtfTicker.trim().toUpperCase();
    if (!t || etfs.some(e => e.ticker === t)) { setAddEtfTicker(''); return; }
    setBusy(true); setErr(null);
    try {
      const r = await v2EtfHoldings(t);
      setEtfs(es => [...es, { ticker: t, name: r.name, holding_count: r.holdings.length }]);
      setHoldings(prev => {
        const map = new Map(prev.map(h => [h.ticker, { ...h, etf_weights: { ...(h.etf_weights || {}) } }]));
        for (const h of r.holdings) {
          const w = h.weight_pct ?? h.weight ?? null;
          const ex = map.get(h.ticker);
          if (ex) { ex.etf_weights = { ...ex.etf_weights, [t]: w }; }
          else { map.set(h.ticker, { ticker: h.ticker, name: h.name, etf_weights: { [t]: w } }); }
        }
        setSelectedHoldings(s => new Set([...s, ...r.holdings.map(h => h.ticker)]));
        return recomputeHoldings(Array.from(map.values()));
      });
      setAddEtfTicker('');
    } catch (e: any) { setErr(e?.message || 'Could not fetch ETF holdings'); }
    finally { setBusy(false); }
  });

  const deleteEtf = (t: string) => {
    setEtfs(es => es.filter(e => e.ticker !== t));
    setHoldings(prev => recomputeHoldings(
      prev
        .map(h => { const ew = { ...(h.etf_weights || {}) }; delete ew[t]; return { ...h, etf_weights: ew }; })
        .filter(h => Object.keys(h.etf_weights || {}).length > 0)
    ));
  };

  const toggleHolding = (t: string) => setSelectedHoldings(s => {
    const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n;
  });

  // ---- §3: user inputs -----------------------------------------------------
  const addUserTicker = () => run(async () => {
    const t = addTicker.trim().toUpperCase();
    if (!t || userCompanies.some(c => c.ticker === t)) { setAddTicker(''); return; }
    setBusy(true); setErr(null);
    try {
      const r = await v2ValidateCompanies([t]);
      if (r.companies[0]) setUserCompanies(cs => [...cs, { ...r.companies[0], source: 'user' }]);
      setAddTicker('');
    } catch (e: any) { setErr(e?.message || 'Could not validate ticker'); }
    finally { setBusy(false); }
  });

  const addPendingText = () => {
    if (!inputDraft.trim()) return;
    setPendingInputs(p => [...p, { type: 'text', value: inputDraft.trim() }]);
    setInputDraft('');
  };
  const addPendingLink = () => {
    const v = inputDraft.trim();
    if (!/^https?:\/\//.test(v)) return;
    setPendingInputs(p => [...p, { type: 'link', value: v }]);
    setInputDraft('');
  };
  const addPendingImage = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => setPendingInputs(p => [...p, { type: 'image', value: String(reader.result) }]);
    reader.readAsDataURL(file);
  };
  const removePending = (i: number) => setPendingInputs(p => p.filter((_, idx) => idx !== i));

  const runIngest = () => run(async () => {
    if (pendingInputs.length === 0) return;
    setBusy(true); setErr(null);
    try {
      const r = await v2Ingest(theme, pendingInputs);
      setIngest(r);
    } catch (e: any) { setErr(e?.message || 'Ingest failed'); }
    finally { setBusy(false); }
  });

  const acceptSuggestion = (s: { ticker: string; name: string }) => {
    if (acceptedSuggestions.some(c => c.ticker === s.ticker)) return;
    setAcceptedSuggestions(cs => [...cs, { ticker: s.ticker, name: s.name, source: 'ingest' }]);
  };

  // ---- build working set & advance to §4 -----------------------------------
  const buildCompanies = (): V2Company[] => {
    const map = new Map<string, V2Company>();
    holdings.filter(h => selectedHoldings.has(h.ticker)).forEach(h =>
      map.set(h.ticker, { ticker: h.ticker, name: h.name, source: 'etf' }));
    // Per-component discovered/added companies (carry their financials).
    Object.values(componentCompanies).flat().forEach(c => { if (!map.has(c.ticker)) map.set(c.ticker, c); });
    userCompanies.forEach(c => map.set(c.ticker, c));
    acceptedSuggestions.forEach(c => { if (!map.has(c.ticker)) map.set(c.ticker, c); });
    return Array.from(map.values());
  };

  // Enrich the full working set (price / P/E / 52W / sector) so the in-component
  // company cards render complete, like the supplier cards. Returns the enriched list.
  const enrichAll = async (cos: V2Company[]): Promise<V2Company[]> => {
    const tickers = cos.map(c => c.ticker).filter(Boolean);
    if (tickers.length === 0) return cos;
    try {
      const r = await v2ValidateCompanies(tickers);
      const byT = new Map(r.companies.map(c => [c.ticker, c]));
      const merged = cos.map(c => {
        const v = byT.get(c.ticker);
        return v ? { ...c, ...v, name: v.name && v.name !== c.ticker ? v.name : c.name } : c;
      });
      setCompanies(merged);
      return merged;
    } catch { return cos; }
  };

  // Force per-component-found companies to stay in their origin component (the
  // user assigned them there in step 1), overriding the LLM match for those.
  const pinComponentCompanies = (r: MatchResponse): MatchResponse => {
    const originOf = new Map<string, string>();
    Object.entries(componentCompanies).forEach(([cid, list]) => list.forEach(c => originOf.set(c.ticker, cid)));
    if (originOf.size === 0) return r;
    let matches = r.matches.map(m => ({ ...m, companies: m.companies.filter(c => !originOf.has(c.ticker) || originOf.get(c.ticker) === m.component_id) }));
    const byId = new Map(matches.map(m => [m.component_id, m]));
    Object.entries(componentCompanies).forEach(([cid, list]) => {
      if (!list.length) return;
      const comp = components.find(c => c.id === cid);
      let entry = byId.get(cid);
      if (!entry) { entry = { component_id: cid, component_name: comp?.name || cid, companies: [] }; matches = [...matches, entry]; byId.set(cid, entry); }
      list.forEach(co => { if (!entry!.companies.some(x => x.ticker === co.ticker)) entry!.companies = [...entry!.companies, { ticker: co.ticker, how: co.why || 'Found for this component' }]; });
    });
    return { ...r, matches };
  };

  const loadMatch = async (cos: V2Company[], dives: CompanyDeepDive[] = []) => {
    setBusy(true); setErr(null);
    try {
      const raw = await v2Match(theme, components, cos, dives);
      const r = pinComponentCompanies(raw);
      setMatchRes(r);
      // Merge any components the matcher created (incl. the "Other relevant" bucket)
      // so every matched company renders under a section.
      if (r.new_components && r.new_components.length) {
        setComponents(cs => {
          const ids = new Set(cs.map(c => c.id));
          const names = new Set(cs.map(c => c.name.toLowerCase()));
          const add = r.new_components!.filter(nc => !ids.has(nc.id) && !names.has(nc.name.toLowerCase()));
          return add.length ? [...cs, ...add] : cs;
        });
      }
    } catch (e: any) { setErr(e?.message || 'Match failed'); }
    finally { setBusy(false); }
  };

  // Editable match (session state) — add / edit / delete a company within a component.
  const updateMatch = (fn: (m: MatchResponse) => MatchResponse) => setMatchRes(prev => prev ? fn(prev) : prev);
  const matchEditHow = (cid: string, idx: number, how: string) =>
    updateMatch(m => ({ ...m, matches: m.matches.map(x => x.component_id === cid ? { ...x, companies: x.companies.map((c, i) => i === idx ? { ...c, how } : c) } : x) }));
  const matchDeleteCompany = (cid: string, idx: number) =>
    updateMatch(m => ({ ...m, matches: m.matches.map(x => x.component_id === cid ? { ...x, companies: x.companies.filter((_, i) => i !== idx) } : x) }));
  const matchAddCompany = (cid: string) => {
    const draft = matchAddDraft[cid];
    const tk = (draft?.ticker || '').trim().toUpperCase();
    if (!tk) return;
    updateMatch(m => ({ ...m, matches: m.matches.map(x => x.component_id === cid
      ? { ...x, companies: x.companies.some(c => c.ticker === tk) ? x.companies : [...x.companies, { ticker: tk, how: (draft?.how || '').trim() }] }
      : x) }));
    setMatchAddDraft(d => ({ ...d, [cid]: { ticker: '', how: '' } }));
  };
  // Fill a gap component (no listed coverage) with ANY ticker → creates a match row.
  const matchAddToGap = (gapName: string) => {
    const draft = matchAddDraft[gapName];
    const tk = (draft?.ticker || '').trim().toUpperCase();
    if (!tk) return;
    const comp = components.find(c => c.name.toLowerCase() === gapName.toLowerCase());
    const cid = comp?.id || gapName.toLowerCase().replace(/\s+/g, '-').slice(0, 40);
    updateMatch(m => {
      const existing = m.matches.find(x => x.component_id === cid);
      const matches = existing
        ? m.matches.map(x => x.component_id === cid
            ? { ...x, companies: x.companies.some(c => c.ticker === tk) ? x.companies : [...x.companies, { ticker: tk, how: (draft?.how || '').trim() }] }
            : x)
        : [...m.matches, { component_id: cid, component_name: gapName, companies: [{ ticker: tk, how: (draft?.how || '').trim() }] }];
      return { ...m, matches, unmatched_components: m.unmatched_components.filter(g => g !== gapName) };
    });
    setMatchAddDraft(d => ({ ...d, [gapName]: { ticker: '', how: '' } }));
  };

  // ---- merged ⑤ helpers ----------------------------------------------------
  // Add ANY ticker to a step-1 component (creates the match entry if missing).
  const componentAddCompany = (cid: string, cname: string) => {
    const draft = matchAddDraft[cid];
    const tk = (draft?.ticker || '').trim().toUpperCase();
    if (!tk) return;
    updateMatch(m => {
      const existing = m.matches.find(x => x.component_id === cid);
      const matches = existing
        ? m.matches.map(x => x.component_id === cid
            ? { ...x, companies: x.companies.some(c => c.ticker === tk) ? x.companies : [...x.companies, { ticker: tk, how: (draft?.how || '').trim() }] }
            : x)
        : [...m.matches, { component_id: cid, component_name: cname, companies: [{ ticker: tk, how: (draft?.how || '').trim() }] }];
      return { ...m, matches, unmatched_components: m.unmatched_components.filter(g => g.toLowerCase() !== cname.toLowerCase()) };
    });
    setMatchAddDraft(d => ({ ...d, [cid]: { ticker: '', how: '' } }));
  };
  const companiesForComponent = (cid: string) => matchRes?.matches.find(x => x.component_id === cid)?.companies || [];
  const toggleComponent = (cid: string) => setExpandedComponents(s => { const n = new Set(s); n.has(cid) ? n.delete(cid) : n.add(cid); return n; });
  const toggleDive = (t: string) => setExpandedDive(s => { const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n; });

  // "Find Pick & Shovel for this component" — sends the component's companies AND
  // all research collected for them (deep-dive docs/clients/suppliers/alpha) to the LLM.
  // Focused: find picks ONLY for this component's matched (anchor) companies +
  // their supply chain, leveraging their deep-dive documents.
  const findPicksForComponent = (comp: ThemeComponent) => run(async () => {
    const matched = companiesForComponent(comp.id);
    const anchors = matched.map(c => ({ ticker: c.ticker, name: companies.find(x => x.ticker === c.ticker)?.name || c.ticker, how: c.how }));
    const dives = matched
      .map(c => deepDives[c.ticker])
      .filter((d): d is CompanyDeepDive => !!d && d !== 'loading' && d !== 'error');
    setComponentPicks(p => ({ ...p, [comp.id]: 'loading' }));
    try {
      const res = await v2ComponentPicks(theme, comp.name, anchors, dives, emphasizedInsights);
      setComponentPicks(p => ({ ...p, [comp.id]: res }));
    } catch {
      setComponentPicks(p => ({ ...p, [comp.id]: 'error' }));
    }
  });

  // Matched companies rendered as cards (anchors) ----------------------------
  const matchDeleteByTicker = (cid: string, ticker: string) =>
    updateMatch(m => ({ ...m, matches: m.matches.map(x => x.component_id === cid ? { ...x, companies: x.companies.filter(c => c.ticker !== ticker) } : x) }));
  const trackAnchor = (c: PickShovelCompany) => run(async () => {
    try { await trackPick(c, 'direct', theme); setTrackedTickers(s => new Set([...s, c.ticker])); }
    catch (e: any) { alert(e?.message || 'Track failed'); }
  });
  const anchorToCard = (mc: { ticker: string; how: string }): PickShovelCompany => {
    const co = companies.find(x => x.ticker === mc.ticker);
    return {
      ticker: mc.ticker, name: co?.name || mc.ticker, sector: co?.sector,
      price: co?.price, price_chg_1d: co?.price_chg_1d, market_cap: co?.market_cap,
      pe_ratio: co?.pe_ratio, forward_pe: co?.forward_pe,
      week52_high: co?.week52_high, week52_low: co?.week52_low,
      thesis: mc.how || `Matched to this component`, catalysts: [],
    };
  };
  const anchorActions = (cid: string): CardActions => ({
    theme, tracked: trackedTickers, dropped: new Set(), dives: deepDives, spawning: null, depth: 3, inFlight,
    onTrack: trackAnchor,
    onDrop: (t) => matchDeleteByTicker(cid, t),
    onDeepDive: (c) => runDeepDive(companies.find(x => x.ticker === c.ticker) || { ticker: c.ticker, name: c.name }),
    onSpawn: () => {},
  });

  // ---- alpha pool (accumulating, selectable) -------------------------------
  const addAlpha = (items: HiddenOpportunity[], source: string) => {
    if (!items || items.length === 0) return;
    setAlphaPool(prev => {
      const seen = new Set(prev.map(a => a.insight.trim().toLowerCase()));
      const next = [...prev];
      items.forEach((it, i) => {
        const insight = (it.insight || '').trim();
        if (!insight) return;
        const key = insight.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        next.push({ id: `${source}-${next.length}-${i}`, insight, beneficiary: it.beneficiary, source, emphasized: false });
      });
      return next;
    });
  };
  const toggleEmphasis = (id: string) =>
    setAlphaPool(prev => prev.map(a => a.id === id ? { ...a, emphasized: !a.emphasized } : a));
  const emphasizedInsights = alphaPool.filter(a => a.emphasized).map(a => a.insight);

  // ---- §4: deep dive -------------------------------------------------------
  // Raw (no lock) so "Run all" can loop it inside a single lock.
  const _deepDive = async (c: V2Company) => {
    setDeepDives(d => ({ ...d, [c.ticker]: 'loading' }));
    try {
      const r = await v2DeepDive(theme, c.ticker, c.name, c.website);
      setDeepDives(d => ({ ...d, [c.ticker]: r }));
      addAlpha(r.hidden_opportunities || [], c.ticker);
    } catch {
      setDeepDives(d => ({ ...d, [c.ticker]: 'error' }));
    }
  };
  const runDeepDive = (c: V2Company) => run(() => _deepDive(c));
  const runAllDeepDives = () => run(async () => {
    setRunningAll(true);
    try {
      for (const c of companies) {
        if (deepDives[c.ticker] && deepDives[c.ticker] !== 'error') continue;
        await _deepDive(c);
      }
    } finally { setRunningAll(false); }
  });

  const completedDives = (): CompanyDeepDive[] =>
    companies.map(c => deepDives[c.ticker]).filter((d): d is CompanyDeepDive => !!d && d !== 'loading' && d !== 'error');

  // ---- §6 / §7 -------------------------------------------------------------
  const loadSummary = async (dives: CompanyDeepDive[]) => {
    setBusy(true); setErr(null);
    try {
      const s = await v2Summary(theme, dives, components, holdings, companies, emphasizedInsights);
      setSummary(s);
      addAlpha((s.alpha || []).map(a => ({ insight: a.insight, beneficiary: (a.beneficiaries || []).join(', ') })), 'summary');
    }
    catch (e: any) { setErr(e?.message || 'Summary failed'); }
    finally { setBusy(false); }
  };

  // advancing logic — open the destination page first (so its busy state shows),
  // then fetch its data; the whole sequence holds the global lock.
  const advance = (to: number) => run(async () => {
    if (to === 2) { goStep(2); if (etfs.length === 0) await loadEtfs(); return; }
    // ④ Deep Dive operates on the working company set — enrich it for full cards.
    if (to === 4) { const cos = buildCompanies(); setCompanies(cos); goStep(4); await enrichAll(cos); return; }
    // ⑤ merged Research & Picks: match (informed by deep dives) + summary together.
    if (to === 5) {
      goStep(5);  // open the page first → step 5 shows its busy state while loading
      let cos = companies.length ? companies : buildCompanies();
      cos = await enrichAll(cos);
      const dives = cos
        .map(c => deepDives[c.ticker])
        .filter((d): d is CompanyDeepDive => !!d && d !== 'loading' && d !== 'error');
      await loadMatch(cos, dives);
      await loadSummary(dives);
      return;
    }
    goStep(to);
  });

  // =========================================================================
  // Render
  // =========================================================================

  if (phase === 'input') {
    return (
      <div className="space-y-6">
        <div className="rounded-2xl border border-primary/20 bg-primary/5 p-5">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles className="w-4 h-4 text-primary" />
            <span className="text-xs font-bold uppercase tracking-wide text-primary">Pick & Shovel v2 — Deep Research</span>
            <span className="ml-auto text-[10px] text-base-content/50">Grounded in real ETF holdings + SEC filings · steered by you</span>
          </div>
          <form onSubmit={e => { e.preventDefault(); start(); }} className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-base-content/40" />
              <input value={theme} onChange={e => setTheme(e.target.value)} disabled={busy}
                placeholder="Enter a theme… e.g. 'Humanoid robotics' or 'Grid modernization'"
                className="input input-bordered w-full pl-9 text-sm bg-base-100/70" />
            </div>
            <button type="submit" disabled={inFlight ||!theme.trim()} className="btn btn-primary gap-2 min-w-32">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Boxes className="w-4 h-4" />}
              {busy ? 'Researching…' : 'Start research'}
            </button>
          </form>
          <div className="mt-3">
            <div className="text-[10px] text-base-content/50 mb-2 uppercase tracking-wide">Try a theme</div>
            <div className="flex flex-wrap gap-1.5">
              {EXAMPLE_THEMES.map(t => (
                <button key={t} onClick={() => start(t)} disabled={inFlight}
                  className="text-[11px] px-2.5 py-1 rounded-full border border-primary/20 bg-base-100/60 hover:bg-primary/10 hover:border-primary/40 transition-colors text-base-content/70">
                  {t}
                </button>
              ))}
            </div>
          </div>
          <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px] text-base-content/60">
            <div className="rounded-xl border border-white/10 p-2.5"><Boxes className="w-3.5 h-3.5 text-primary inline mr-1" /> Breaks the theme into real building blocks — not just chips</div>
            <div className="rounded-xl border border-white/10 p-2.5"><Layers className="w-3.5 h-3.5 text-primary inline mr-1" /> Anchors on actual ETF holdings, deduped</div>
            <div className="rounded-xl border border-white/10 p-2.5"><FlaskConical className="w-3.5 h-3.5 text-primary inline mr-1" /> Mines SEC filings for clients, suppliers & hidden alpha</div>
          </div>
        </div>
      </div>
    );
  }

  const selectedCount = holdings.filter(h => selectedHoldings.has(h.ticker)).length;
  const etfColorOf = (t: string) => ETF_COLOR_CLASSES[Math.max(0, etfs.findIndex(e => e.ticker === t)) % ETF_COLOR_CLASSES.length];
  const matchedCount = new Set((matchRes?.matches || []).flatMap(m => m.companies.map(c => c.ticker))).size;
  const deepDivedCount = Object.values(deepDives).filter(d => d && d !== 'loading' && d !== 'error').length;

  return (
    <BusyCtx.Provider value={busyValue}>
    <div className="space-y-5">
      {/* Consistent global busy indicator — shows whenever a backend request is in flight */}
      {inFlight && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 px-4 py-2 rounded-full bg-base-200/95 border border-primary/30 shadow-xl text-sm font-medium text-primary backdrop-blur">
          <Loader2 className="w-4 h-4 animate-spin" /> Working… please wait
        </div>
      )}
      {/* Header + stepper */}
      <div className="flex items-center gap-2 flex-wrap">
        <h2 className="text-lg font-bold tracking-tight">{themeTitle || theme}</h2>
        {alphaPool.length > 0 && (
          <span className="text-[10px] px-2 py-0.5 rounded-full border border-violet-500/30 bg-violet-500/10 text-violet-300 flex items-center gap-1" title="Accumulating alpha leads — emphasize on the Summary step">
            <Gem className="w-3 h-3" /> {emphasizedInsights.length}★ / {alphaPool.length} alpha
          </span>
        )}
        <button onClick={() => { setPhase('input'); }} className="btn btn-xs btn-ghost gap-1 text-base-content/50 ml-auto">
          <RefreshCw className="w-3 h-3" /> New theme
        </button>
      </div>
      <Stepper step={step} reached={reached} go={goStep} />

      {err && <ErrBox msg={err} onRetry={undefined} />}

      <div className="glass-card">
        <div className="card-body">
          {/* ---------------- STEP 1 ---------------- */}
          {step === 1 && (
            busy && components.length === 0 ? <Busy label="Breaking the theme into its real building blocks…" /> : (
              <div className="space-y-4">
                <div>
                  <div className="text-xs font-bold uppercase tracking-wide text-primary mb-1 flex items-center gap-1.5"><Boxes className="w-4 h-4" /> Building blocks of this theme</div>
                  {themeSummary && <p className="text-sm text-base-content/70 leading-relaxed">{themeSummary}</p>}
                </div>
                {Object.entries(components.reduce((acc, c) => {
                  (acc[c.category] ||= []).push(c); return acc;
                }, {} as Record<string, ThemeComponent[]>)).map(([cat, items]) => (
                  <div key={cat}>
                    <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-1.5">{cat}</div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {items.map(c => (
                        <div key={c.id} className={`group rounded-xl border p-2.5 ${catColor(c.category)}`}>
                          {editingId === c.id ? (
                            <div className="flex gap-1">
                              <input value={editDraft} onChange={e => setEditDraft(e.target.value)} autoFocus
                                onKeyDown={e => { if (e.key === 'Enter') saveEdit(c.id); }}
                                className="input input-xs input-bordered flex-1 bg-base-100/70 text-xs" />
                              <button onClick={() => saveEdit(c.id)} className="text-success"><Check className="w-4 h-4" /></button>
                            </div>
                          ) : (
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <div className="text-sm font-semibold leading-tight">{c.name}</div>
                                <div className="text-[11px] text-base-content/60 mt-0.5">{c.description}</div>
                              </div>
                              <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0">
                                <button onClick={() => { setEditingId(c.id); setEditDraft(c.name); }} className="text-base-content/40 hover:text-primary"><Pencil className="w-3 h-3" /></button>
                                <button onClick={() => deleteComponent(c.id)} className="text-base-content/40 hover:text-error"><X className="w-3.5 h-3.5" /></button>
                              </div>
                            </div>
                          )}
                          {/* Per-component company discovery (bottom-up universe — no ETF needed) */}
                          <div className="mt-2 pt-2 border-t border-white/[0.08]">
                            {(componentCompanies[c.id] || []).length > 0 && (
                              <div className="flex flex-wrap gap-1 mb-1.5">
                                {(componentCompanies[c.id] || []).map(co => (
                                  <span key={co.ticker} title={co.why || co.name} className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border border-white/20 bg-base-100/60">
                                    <span className="font-mono font-semibold">{co.ticker}</span>
                                    <button onClick={() => removeComponentCompany(c.id, co.ticker)} className="text-base-content/30 hover:text-error"><X className="w-2.5 h-2.5" /></button>
                                  </span>
                                ))}
                              </div>
                            )}
                            <div className="flex items-center gap-1.5">
                              <button onClick={() => findCompanies(c)} disabled={inFlight}
                                className="text-[10px] px-2 py-0.5 rounded-full border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 flex items-center gap-1 disabled:opacity-50">
                                {findingCid === c.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />} Find companies
                              </button>
                              <input value={compAddDraft[c.id] || ''} onChange={e => setCompAddDraft(d => ({ ...d, [c.id]: e.target.value }))}
                                onKeyDown={e => { if (e.key === 'Enter') addComponentCompanyManual(c); }}
                                placeholder="+ ticker" className="input input-xs input-bordered w-20 bg-base-100/70 text-[10px] uppercase" />
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                {/* refine */}
                <div className="rounded-xl border border-white/[0.07] bg-base-100/40 p-3">
                  <div className="flex items-center gap-1.5 mb-2 text-[11px] font-semibold text-base-content/70"><Wand2 className="w-3.5 h-3.5 text-primary" /> Missing something? Tell the AI to refine the list</div>
                  <form onSubmit={e => { e.preventDefault(); refineComponents(); }} className="flex gap-2">
                    <input value={refineInput} onChange={e => setRefineInput(e.target.value)} disabled={busy}
                      placeholder="e.g. 'add tactile skin sensors and cycloidal gearboxes; drop generic software'"
                      className="input input-bordered input-sm flex-1 text-sm bg-base-100/70" />
                    <button type="submit" disabled={inFlight ||!refineInput.trim()} className="btn btn-sm btn-ghost gap-1">
                      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />} Refine
                    </button>
                  </form>
                </div>
                <NavBar onNext={() => advance(2)} nextLabel="Find ETFs & holdings" nextDisabled={components.length === 0} busy={inFlight} />
              </div>
            )
          )}

          {/* ---------------- STEP 2 ---------------- */}
          {step === 2 && (
            (busy || inFlight) && etfs.length === 0 ? <Busy label="Finding the most relevant thematic ETFs and pulling their holdings…" /> : (
              <div className="space-y-4">
                <div className="text-xs font-bold uppercase tracking-wide text-primary flex items-center gap-1.5"><Layers className="w-4 h-4" /> Thematic ETFs → real holdings</div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {etfs.map(e => (
                    <div key={e.ticker} className="group rounded-xl border border-white/10 bg-base-100/50 p-3">
                      <div className="flex items-center justify-between">
                        <span className={`text-sm font-mono font-bold px-2 py-0.5 rounded-lg border ${etfColorOf(e.ticker)}`}>{e.ticker}</span>
                        <button onClick={() => deleteEtf(e.ticker)} className="text-base-content/30 hover:text-error opacity-0 group-hover:opacity-100"><X className="w-3.5 h-3.5" /></button>
                      </div>
                      <div className="text-[11px] text-base-content/70 mt-0.5 leading-tight">{e.name}</div>
                      {e.rationale && <div className="text-[10px] text-base-content/50 mt-1">{e.rationale}</div>}
                      <div className="text-[9px] text-base-content/40 mt-1">{e.holding_count ?? 0} holdings</div>
                    </div>
                  ))}
                </div>
                <form onSubmit={e => { e.preventDefault(); addEtf(); }} className="flex gap-2">
                  <input value={addEtfTicker} onChange={e => setAddEtfTicker(e.target.value)} placeholder="Add an ETF ticker (e.g. BOTZ)" className="input input-bordered input-sm w-56 text-sm bg-base-100/70 uppercase" />
                  <button type="submit" disabled={inFlight ||!addEtfTicker.trim()} className="btn btn-sm btn-ghost gap-1"><Plus className="w-3.5 h-3.5" /> Add ETF</button>
                </form>

                {/* Holdings table */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-[10px] uppercase tracking-wide text-base-content/50">Deduplicated holdings — {selectedCount}/{holdings.length} selected · ranked by # of ETFs, then avg weight</div>
                    <div className="flex gap-2">
                      <button onClick={() => setSelectedHoldings(new Set(holdings.map(h => h.ticker)))} className="text-[10px] text-primary hover:underline">Select all</button>
                      <button onClick={() => setSelectedHoldings(new Set())} className="text-[10px] text-base-content/50 hover:underline">Clear</button>
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/[0.07] overflow-hidden">
                    <table className="w-full text-xs">
                      <thead className="bg-base-200/50 text-base-content/50">
                        <tr><th className="w-8 py-1.5"></th><th className="text-left px-2 py-1.5">Ticker</th><th className="text-left px-2">Name</th><th className="text-right px-2">Avg wt</th><th className="text-left px-2">In ETFs (top-10)</th></tr>
                      </thead>
                      <tbody>
                        {holdings.map(h => {
                          const on = selectedHoldings.has(h.ticker);
                          const w = holdingWeight(h);
                          return (
                            <tr key={h.ticker} className={`border-t border-white/[0.04] ${on ? '' : 'opacity-40'}`}>
                              <td className="text-center"><input type="checkbox" checked={on} onChange={() => toggleHolding(h.ticker)} className="checkbox checkbox-xs checkbox-primary" /></td>
                              <td className="px-2 py-1.5 font-mono font-semibold">{h.ticker}</td>
                              <td className="px-2 text-base-content/70 truncate max-w-[180px]">{h.name}</td>
                              <td className="px-2 text-right tabular-nums">{w != null ? `${w.toFixed(1)}%` : '—'}</td>
                              <td className="px-2">
                                <div className="flex gap-0.5 flex-wrap items-center">
                                  {(h.in_etfs || []).map(e => (
                                    <span key={e} title={h.etf_weights?.[e] != null ? `${e}: ${h.etf_weights![e]!.toFixed(1)}%` : e}
                                      className={`text-[8px] px-1 py-0.5 rounded border ${etfColorOf(e)}`}>{e}</span>
                                  ))}
                                </div>
                              </td>
                            </tr>
                          );
                        })}
                        {holdings.length === 0 && <tr><td colSpan={5} className="text-center py-6 text-base-content/40">No holdings found for these ETFs.</td></tr>}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Companies found per component (bottom-up, no-ETF themes) */}
                {Object.values(componentCompanies).some(l => l.length > 0) && (
                  <div>
                    <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-2 flex items-center gap-1.5">
                      <Building2 className="w-3.5 h-3.5" /> Companies you found per component — carried into the research alongside ETF holdings
                    </div>
                    <div className="space-y-2">
                      {components.filter(c => (componentCompanies[c.id] || []).length > 0).map(c => (
                        <div key={c.id} className={`rounded-xl border p-2.5 ${catColor(c.category)}`}>
                          <div className="text-[11px] font-semibold mb-1.5">{c.name}</div>
                          <div className="flex flex-wrap gap-1.5">
                            {(componentCompanies[c.id] || []).map(co => (
                              <span key={co.ticker} title={co.why || co.name} className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full border border-white/20 bg-base-100/60">
                                <span className="font-mono font-semibold">{co.ticker}</span>
                                <span className="text-base-content/50 truncate max-w-[140px]">{co.name}</span>
                                <button onClick={() => removeComponentCompany(c.id, co.ticker)} className="text-base-content/30 hover:text-error"><X className="w-3 h-3" /></button>
                              </span>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <NavBar onBack={() => goStep(1)} onNext={() => goStep(3)} nextLabel="Add your inputs" busy={inFlight} />
              </div>
            )
          )}

          {/* ---------------- STEP 3 ---------------- */}
          {step === 3 && (
            <div className="space-y-4">
              <div className="text-xs font-bold uppercase tracking-wide text-primary flex items-center gap-1.5"><FileText className="w-4 h-4" /> Add your own research</div>

              {/* tickers */}
              <div>
                <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-1.5">Your companies</div>
                <form onSubmit={e => { e.preventDefault(); addUserTicker(); }} className="flex gap-2 mb-2">
                  <input value={addTicker} onChange={e => setAddTicker(e.target.value)} placeholder="Add ticker (e.g. ISRG)" className="input input-bordered input-sm w-48 text-sm bg-base-100/70 uppercase" />
                  <button type="submit" disabled={inFlight ||!addTicker.trim()} className="btn btn-sm btn-ghost gap-1"><Plus className="w-3.5 h-3.5" /> Add</button>
                </form>
                <div className="flex flex-wrap gap-1.5">
                  {userCompanies.map(c => (
                    <span key={c.ticker} className="group inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-full border border-white/15 bg-base-100/50">
                      <span className="font-mono font-semibold">{c.ticker}</span>
                      <span className="text-base-content/50">{c.name}</span>
                      <button onClick={() => setUserCompanies(cs => cs.filter(x => x.ticker !== c.ticker))} className="text-base-content/30 hover:text-error"><X className="w-3 h-3" /></button>
                    </span>
                  ))}
                </div>
              </div>

              {/* materials: text / link / image */}
              <div className="rounded-xl border border-white/[0.07] bg-base-100/40 p-3">
                <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-2">Drop in research — notes, blog/IR/presentation links, or images</div>
                <div className="flex gap-2 flex-wrap items-center">
                  <input value={inputDraft} onChange={e => setInputDraft(e.target.value)} placeholder="Paste a note or a URL…" className="input input-bordered input-sm flex-1 min-w-[200px] text-sm bg-base-100/70" />
                  <button onClick={addPendingText} disabled={!inputDraft.trim()} className="btn btn-xs btn-ghost gap-1"><TypeIcon className="w-3 h-3" /> Note</button>
                  <button onClick={addPendingLink} disabled={!/^https?:\/\//.test(inputDraft.trim())} className="btn btn-xs btn-ghost gap-1"><LinkIcon className="w-3 h-3" /> Link</button>
                  <label className="btn btn-xs btn-ghost gap-1 cursor-pointer">
                    <ImageIcon className="w-3 h-3" /> Image
                    <input type="file" accept="image/*" className="hidden" onChange={e => { if (e.target.files?.[0]) addPendingImage(e.target.files[0]); e.currentTarget.value=''; }} />
                  </label>
                </div>
                {pendingInputs.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mt-2">
                    {pendingInputs.map((p, i) => (
                      <span key={i} className="group inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-full border border-white/10 bg-base-100/60 max-w-[260px]">
                        {p.type === 'text' ? <TypeIcon className="w-3 h-3 text-base-content/40" /> : p.type === 'link' ? <LinkIcon className="w-3 h-3 text-base-content/40" /> : <ImageIcon className="w-3 h-3 text-base-content/40" />}
                        <span className="truncate">{p.type === 'image' ? 'image' : p.value}</span>
                        <button onClick={() => removePending(i)} className="text-base-content/30 hover:text-error"><X className="w-3 h-3" /></button>
                      </span>
                    ))}
                  </div>
                )}
                <button onClick={runIngest} disabled={inFlight ||pendingInputs.length === 0} className="btn btn-sm btn-primary gap-1 mt-3">
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />} Ingest & extract
                </button>

                {ingest && (
                  <div className="mt-3 space-y-2">
                    {ingest.notes && <p className="text-[11px] text-base-content/70">{ingest.notes}</p>}
                    {ingest.suggested_companies.length > 0 && (
                      <div>
                        <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-1">Suggested companies — tap to add</div>
                        <div className="flex flex-wrap gap-1.5">
                          {ingest.suggested_companies.map(s => {
                            const added = acceptedSuggestions.some(c => c.ticker === s.ticker);
                            return (
                              <button key={s.ticker} onClick={() => acceptSuggestion(s)} disabled={added || inFlight}
                                className={`text-[11px] px-2 py-1 rounded-full border flex items-center gap-1 ${added ? 'border-success/40 bg-success/10 text-success' : 'border-primary/30 bg-primary/5 text-primary hover:bg-primary/10'}`}
                                title={s.why}>
                                {added ? <Check className="w-3 h-3" /> : <Plus className="w-3 h-3" />}<span className="font-mono font-semibold">{s.ticker}</span> {s.name}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                    {ingest.sources.some(s => !s.ok) && (
                      <div className="text-[10px] text-warning/80 flex items-center gap-1"><ShieldAlert className="w-3 h-3" /> Some links/images couldn't be read and were skipped.</div>
                    )}
                  </div>
                )}
              </div>

              <div className="text-[11px] text-base-content/50">
                Carrying forward: <b>{selectedCount}</b> ETF holdings · <b>{userCompanies.length}</b> your tickers · <b>{acceptedSuggestions.length}</b> suggested
              </div>
              <NavBar onBack={() => goStep(2)} onNext={() => advance(4)} nextLabel="Match to components" busy={inFlight} />
            </div>
          )}

          {/* ---------------- STEP 4 — Deep Dive (before Match) ---------------- */}
          {step === 4 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 flex-wrap">
                <div className="text-xs font-bold uppercase tracking-wide text-primary flex items-center gap-1.5"><FlaskConical className="w-4 h-4" /> Deep dive — filings & investor materials <span className="normal-case text-base-content/40 font-normal">(findings feed the matching next)</span></div>
                <button onClick={runAllDeepDives} disabled={runningAll || inFlight} className="btn btn-xs btn-primary gap-1 ml-auto">
                  {runningAll ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Wand2 className="w-3.5 h-3.5" />} Run all
                </button>
              </div>
              <div className="space-y-3">
                {companies.map(c => {
                  const d = deepDives[c.ticker];
                  return (
                    <div key={c.ticker} className="rounded-xl border border-white/[0.07] bg-base-100/40 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-sm font-mono font-bold text-primary">{c.ticker}</span>
                          <span className="text-sm text-base-content/70 truncate">{c.name}</span>
                        </div>
                        {(!d || d === 'error') && (
                          <button onClick={() => runDeepDive(c)} disabled={inFlight} className="btn btn-xs btn-ghost gap-1 flex-shrink-0 disabled:opacity-50"><FlaskConical className="w-3 h-3" /> {d === 'error' ? 'Retry' : 'Deep dive'}</button>
                        )}
                        {d === 'loading' && <Loader2 className="w-4 h-4 animate-spin text-primary flex-shrink-0" />}
                      </div>
                      {d && d !== 'loading' && d !== 'error' && <DeepDivePanel d={d} />}
                    </div>
                  );
                })}
                {companies.length === 0 && <div className="text-center py-6 text-base-content/40 text-sm">No companies selected — go back and pick some holdings.</div>}
              </div>
              <NavBar onBack={() => goStep(3)} onNext={() => advance(5)} nextLabel="Match & research" nextDisabled={companies.length === 0} busy={inFlight} />
            </div>
          )}

          {/* ---------------- STEP 5 — Research & Picks (Match + Summary merged) ---------------- */}
          {step === 5 && (
            (busy || inFlight) && !matchRes && !summary ? <Busy label="Matching companies and summarizing the research…" /> : (
              <div className="space-y-3">
                {/* Compact summary bar */}
                <div className="rounded-2xl border border-white/[0.07] bg-base-100/50 p-4">
                  {summary?.headline && <p className="text-sm text-base-content/85 leading-relaxed max-w-3xl">{summary.headline}</p>}
                  <div className="flex flex-wrap gap-2 mt-3">
                    {[
                      { icon: Boxes, label: 'components', value: `${components.length}` },
                      { icon: Building2, label: 'companies', value: `${matchedCount}` },
                      { icon: FlaskConical, label: 'deep-dived', value: `${deepDivedCount}` },
                      { icon: Gem, label: 'alpha ★', value: `${emphasizedInsights.length}/${alphaPool.length}` },
                    ].map((s, i) => {
                      const I = s.icon;
                      return (
                        <span key={i} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-white/10 bg-base-100/60 text-[11px]">
                          <I className="w-3 h-3 text-primary" /><span className="font-semibold tabular-nums">{s.value}</span><span className="text-base-content/50">{s.label}</span>
                        </span>
                      );
                    })}
                  </div>
                </div>

                {/* Collapsible insights — groups + alpha board + don't-miss tucked away to declutter */}
                {(summary || alphaPool.length > 0) && (
                  <div className="rounded-2xl border border-white/[0.07] bg-base-100/40 overflow-hidden">
                    <button onClick={() => setInsightsOpen(o => !o)} className="w-full flex items-center justify-between gap-2 p-3 text-left">
                      <span className="text-xs font-bold uppercase tracking-wide text-base-content/70 flex items-center gap-1.5"><Sparkles className="w-4 h-4 text-violet-400" /> Research insights &amp; alpha</span>
                      <span className="flex items-center gap-2 flex-shrink-0">
                        {alphaPool.length > 0 && <span className="text-[9px] px-1.5 py-0.5 rounded-full border border-violet-500/30 bg-violet-500/10 text-violet-300">{alphaPool.length} alpha</span>}
                        {summary && summary.recommended_deep_dives.length > 0 && <span className="text-[9px] px-1.5 py-0.5 rounded-full border border-amber-500/30 bg-amber-500/10 text-amber-300">{summary.recommended_deep_dives.length} to review</span>}
                        <ChevronDown className={`w-4 h-4 text-base-content/40 transition-transform ${insightsOpen ? '' : '-rotate-90'}`} />
                      </span>
                    </button>
                    {insightsOpen && (
                      <div className="px-3 pb-3 space-y-3">
                        {(summary?.groups || []).length > 0 && (
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                            {(summary?.groups || []).map((g, i) => (
                              <div key={i} className="rounded-xl border border-white/[0.07] bg-base-100/40 p-2.5">
                                <div className="text-[12px] font-bold mb-0.5">{g.title}</div>
                                {g.summary && <div className="text-[10px] text-base-content/60 mb-1">{g.summary}</div>}
                                <div className="flex flex-wrap gap-1">{g.items.map((it, j) => <span key={j} className="text-[9px] px-1.5 py-0.5 rounded-full border border-white/10 text-base-content/70" title={it.note}>{it.label}</span>)}</div>
                              </div>
                            ))}
                          </div>
                        )}
                        {alphaPool.length > 0 && (
                          <div className="rounded-xl border border-violet-500/25 bg-violet-500/5 p-3">
                            <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
                              <div className="text-[10px] uppercase tracking-wide text-violet-400/80 flex items-center gap-1"><Gem className="w-3 h-3" /> Alpha board ({emphasizedInsights.length}★ of {alphaPool.length})</div>
                              <span className="text-[9px] text-base-content/40">★ to emphasize → fed to the LLM for picks</span>
                            </div>
                            <div className="space-y-1.5 max-h-60 overflow-y-auto">
                              {alphaPool.map(a => (
                                <div key={a.id} className={`flex items-start gap-2 rounded-lg p-1.5 border ${a.emphasized ? 'border-amber-500/40 bg-amber-500/5' : 'border-white/[0.05]'}`}>
                                  <button onClick={() => toggleEmphasis(a.id)} title="Emphasize for picks" className={`mt-0.5 flex-shrink-0 ${a.emphasized ? 'text-amber-400' : 'text-base-content/30 hover:text-amber-400'}`}>
                                    <Star className="w-3.5 h-3.5" fill={a.emphasized ? 'currentColor' : 'none'} />
                                  </button>
                                  <div className="flex-1 min-w-0">
                                    <div className="text-[11px] text-base-content/80"><span className="font-medium text-violet-300">{a.insight}</span>{a.beneficiary ? <span className="text-base-content/50"> → {a.beneficiary}</span> : null}</div>
                                    <div className="text-[9px] text-base-content/30 mt-0.5">from {a.source}</div>
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        {summary && (summary.gaps.length > 0 || summary.recommended_deep_dives.length > 0) && (
                          <div className="rounded-xl border border-amber-500/25 bg-amber-500/5 p-3">
                            <div className="text-[10px] uppercase tracking-wide text-amber-400/80 mb-2 flex items-center gap-1"><Lightbulb className="w-3 h-3" /> Don't miss — not covered yet</div>
                            {summary.gaps.length > 0 && (
                              <ul className="space-y-1 mb-2">{summary.gaps.map((g, i) => <li key={i} className="text-[11px] text-base-content/75 flex items-start gap-1.5"><span className="text-amber-400 mt-0.5">•</span>{g}</li>)}</ul>
                            )}
                            {summary.recommended_deep_dives.length > 0 && (
                              <div className="flex flex-wrap gap-1.5">
                                {summary.recommended_deep_dives.map((r, i) => (
                                  <button key={i} title={r.why} disabled={inFlight}
                                    onClick={() => { setCompanies(cs => cs.some(c => c.ticker === r.ticker) ? cs : [...cs, { ticker: r.ticker, name: r.name, source: 'ingest' }]); setUserCompanies(cs => cs.some(c => c.ticker === r.ticker) ? cs : [...cs, { ticker: r.ticker, name: r.name, source: 'user' }]); goStep(4); }}
                                    className="text-[11px] px-2 py-1 rounded-full border border-amber-500/30 bg-amber-500/5 text-amber-300 hover:bg-amber-500/10 flex items-center gap-1 disabled:opacity-50">
                                    <FlaskConical className="w-3 h-3" /><span className="font-mono font-semibold">{r.ticker}</span> {r.name}
                                  </button>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Per-component sections — the main work area; open one to research */}
                <div className="text-xs font-bold uppercase tracking-wide text-primary flex items-center gap-1.5 pt-1"><Network className="w-4 h-4" /> Components — open one to research &amp; find its pick &amp; shovels</div>
                {components.map(comp => {
                  const cid = comp.id;
                  const collapsed = !expandedComponents.has(cid);
                  const matched = companiesForComponent(cid);
                  const picks = componentPicks[cid];
                  const picksReady = picks && picks !== 'loading' && picks !== 'error';
                  const pickCount = picksReady ? picks.suppliers.length : 0;
                  return (
                    <div key={cid} className={`rounded-xl border bg-base-100/40 ${picksReady ? 'border-violet-500/25' : 'border-white/[0.07]'}`}>
                      <button onClick={() => toggleComponent(cid)} className="w-full flex items-center justify-between gap-2 p-3 text-left">
                        <span className="text-sm font-semibold flex items-center gap-2 min-w-0">
                          <ChevronDown className={`w-4 h-4 text-base-content/40 transition-transform flex-shrink-0 ${collapsed ? '-rotate-90' : ''}`} />
                          <span className="truncate">{comp.name}</span>
                          <span className="text-[9px] uppercase tracking-wide text-base-content/40 flex-shrink-0">{comp.category}</span>
                        </span>
                        <span className="flex items-center gap-2 flex-shrink-0">
                          {picksReady && <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded-full border border-violet-500/40 bg-violet-500/10 text-violet-300 flex items-center gap-1"><Gem className="w-2.5 h-2.5" /> {pickCount} picks</span>}
                          {picks === 'loading' && <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-300" />}
                          <span className="text-[10px] text-base-content/50">{matched.length} matched</span>
                        </span>
                      </button>
                      {!collapsed && (
                        <div className="px-3 pb-3 space-y-2">
                          {/* Matched companies as cards (anchors) — deep-dive / track / remove per card */}
                          {matched.length > 0 ? (
                            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                              {matched.map(mc => (
                                <RichCard key={mc.ticker} c={anchorToCard(mc)} tier="direct" accent="text-primary" {...anchorActions(cid)} />
                              ))}
                            </div>
                          ) : (
                            <div className="text-[11px] text-base-content/40 italic">No company yet — add any ticker below (a gap = opportunity).</div>
                          )}

                          {/* add ANY ticker (no dropdown) */}
                          <div className="flex items-center gap-1.5">
                            <input value={matchAddDraft[cid]?.ticker || ''}
                              onChange={e => setMatchAddDraft(d => ({ ...d, [cid]: { ticker: e.target.value, how: d[cid]?.how || '' } }))}
                              placeholder="any ticker" className="input input-xs input-bordered w-24 bg-base-100/70 text-[11px] uppercase" />
                            <input value={matchAddDraft[cid]?.how || ''}
                              onChange={e => setMatchAddDraft(d => ({ ...d, [cid]: { ticker: d[cid]?.ticker || '', how: e.target.value } }))}
                              placeholder="how (optional)" className="input input-xs input-bordered flex-1 bg-base-100/70 text-[11px]" />
                            <button onClick={() => componentAddCompany(cid, comp.name)} disabled={!(matchAddDraft[cid]?.ticker || '').trim()} className="btn btn-xs btn-ghost gap-1"><Plus className="w-3 h-3" /> Add</button>
                          </div>

                          {/* find pick & shovel for this component */}
                          <button onClick={() => findPicksForComponent(comp)} disabled={picks === 'loading' || matched.length === 0 || inFlight}
                            className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-xl text-[11px] font-semibold border border-violet-500/30 bg-violet-500/5 text-violet-300 hover:bg-violet-500/10 disabled:opacity-50">
                            {picks === 'loading' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Gem className="w-3.5 h-3.5" />}
                            Find Pick &amp; Shovel for "{comp.name}"
                          </button>
                          {picks === 'error' && <ErrBox msg="Pick & shovel failed" onRetry={() => findPicksForComponent(comp)} />}
                          {picks && picks !== 'loading' && picks !== 'error' && (
                            <div className="pt-2 border-t border-white/[0.05]">
                              {picks.selection_notes && <p className="text-[10px] text-base-content/55 mb-2 italic">{picks.selection_notes}</p>}
                              <SupplierPicks theme={`${theme} — ${comp.name}`} data={picks} depth={0} emphasis={emphasizedInsights} onAlpha={addAlpha} />
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}

                <NavBar onBack={() => goStep(4)} busy={inFlight} />
                <div className="text-[10px] text-base-content/30 text-center">Expand a component to add tickers, see deep-dive docs, and find its pick &amp; shovels · collapse to keep the page short · Not financial advice</div>
              </div>
            )
          )}

          {/* Steps 6 & 7 removed — Summary is merged into step 5 and Pick & Shovel is launched per-component there. */}
        </div>
      </div>
    </div>
    </BusyCtx.Provider>
  );
}

export default PickAndShovelV2;
