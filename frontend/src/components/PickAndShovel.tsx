/**
 * Pick & Shovel Research Panel — Agentic, collaborative thesis → recommendation loop
 *
 * Bloomberg-style thematic research that maps the full investment supply chain:
 *   ● Theme Core   — direct beneficiaries whose core business IS the theme
 *   ◈ The Backbone — infrastructure, supply-chain and tooling enablers
 *   ◆ Hidden Picks — overlooked indirect beneficiaries at the foundation
 *
 * The flow is a three-phase collaboration:
 *   A. THESIS    — user states a thesis
 *   B. BRIEFING  — the AI restates its understanding (the "Research Brief"),
 *                  surfaces the angles it inferred, and asks thesis-specific
 *                  clarifying questions ONLY when genuinely in doubt
 *   C. RESULTS   — three tiers of cards; dropping a card teaches the AI, which
 *                  then backfills only the dropped slots with sharper picks,
 *                  learning from BOTH the kept and dropped names
 */

import React, { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Search, Loader2, AlertCircle, Sparkles, ExternalLink,
  TrendingUp, TrendingDown, Target, Cpu, Gem, Zap,
  ChevronRight, ChevronDown, ArrowUpRight, ArrowDownRight, BarChart2,
  AlertTriangle, BookOpen, Lightbulb, RefreshCw, ArrowLeft, Wand2, Brain,
  Bookmark, BookmarkCheck, X, CheckCheck, ShieldCheck, ShieldAlert, Pencil, Plus, Check,
} from 'lucide-react';
import { analyzePickShovel, digDeeperPickShovel, trackCompany, interpretThesis, refinePickShovel } from '../api';
import type {
  PickShovelCompany, PickShovelResponse, DigDeeperLayer,
  ResearchBrief, ClarifyingQuestion, ClarifyingAnswer, PickShovelTier, DroppedCard,
} from '../types';

// ---------------------------------------------------------------------------
// Example themes shown as quick-start suggestions
// ---------------------------------------------------------------------------

const EXAMPLE_THEMES = [
  'AI infrastructure & data center build-out',
  'GLP-1 obesity drugs boom',
  'US defense & drone proliferation',
  'Onshoring & US manufacturing renaissance',
  'Nuclear energy renaissance',
  'Quantum computing commercialization',
  'EV battery supply chain',
  'Cybersecurity spending surge',
];

// Quick one-tap reasons captured when the user drops a card (all optional).
const DROP_REASONS = [
  'Too obvious',
  'Too large',
  'Wrong sector',
  'Not a pure-play',
  'Already own',
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const fmtPrice = (v: number | null | undefined) =>
  v != null ? `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';

const fmtPE = (v: number | null | undefined) =>
  v != null ? v.toFixed(1) + '×' : '—';

// ---------------------------------------------------------------------------
// 52-week range bar (like Bloomberg terminal)
// ---------------------------------------------------------------------------

function RangeBar({
  price, low, high,
}: { price?: number | null; low?: number | null; high?: number | null }) {
  if (!price || !low || !high || high <= low) {
    return (
      <div className="text-[10px] text-base-content/40 italic">52W data unavailable</div>
    );
  }
  const pct = Math.max(0, Math.min(100, ((price - low) / (high - low)) * 100));
  const nearHigh = pct > 80;
  const nearLow = pct < 20;
  return (
    <div className="space-y-1">
      <div className="relative h-1.5 rounded-full bg-base-300">
        {/* gradient track */}
        <div
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${pct}%`,
            background: nearHigh
              ? 'linear-gradient(to right, #22c55e44, #22c55e)'
              : nearLow
              ? 'linear-gradient(to right, #ef444444, #ef4444)'
              : 'linear-gradient(to right, #6366f144, #6366f1)',
          }}
        />
        {/* current-price dot */}
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full border-2 border-base-100 shadow"
          style={{
            left: `${pct}%`,
            background: nearHigh ? '#22c55e' : nearLow ? '#ef4444' : '#6366f1',
          }}
        />
      </div>
      <div className="flex justify-between text-[9px] text-base-content/40 tabular-nums">
        <span>{fmtPrice(low)}</span>
        <span className={`text-[9px] font-semibold ${nearHigh ? 'text-success' : nearLow ? 'text-error' : 'text-base-content/60'}`}>
          {pct.toFixed(0)}% of 52W range
        </span>
        <span>{fmtPrice(high)}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Individual company card
// ---------------------------------------------------------------------------

type TierKind = 'direct' | 'enabler' | 'deep';

const TIER_PALETTE: Record<TierKind, {
  border: string; header: string; badge: string;
  badgeText: string; dot: string; catPill: string;
}> = {
  direct: {
    border: 'border-amber-500/30 hover:border-amber-500/60',
    header: 'from-amber-500/10 to-transparent',
    badge: 'bg-amber-500/15 border border-amber-500/30',
    badgeText: 'text-amber-400',
    dot: 'bg-amber-400',
    catPill: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  },
  enabler: {
    border: 'border-cyan-500/30 hover:border-cyan-500/60',
    header: 'from-cyan-500/10 to-transparent',
    badge: 'bg-cyan-500/15 border border-cyan-500/30',
    badgeText: 'text-cyan-400',
    dot: 'bg-cyan-400',
    catPill: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20',
  },
  deep: {
    border: 'border-violet-500/30 hover:border-violet-500/60',
    header: 'from-violet-500/10 to-transparent',
    badge: 'bg-violet-500/15 border border-violet-500/30',
    badgeText: 'text-violet-400',
    dot: 'bg-violet-400',
    catPill: 'bg-violet-500/10 text-violet-400 border-violet-500/20',
  },
};

function CompanyCard({
  company, tier, theme, themeSummary,
  onDrop, onTrack, isTracked, isDropped,
}: {
  company: PickShovelCompany;
  tier: TierKind;
  theme?: string;
  themeSummary?: string;
  onDrop?: (ticker: string, reason?: string) => void;
  onTrack?: (ticker: string) => void;
  isTracked?: boolean;
  isDropped?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [dropMenu, setDropMenu] = useState(false);
  const pal = TIER_PALETTE[tier];
  const chg = company.price_chg_1d;
  const up = chg != null && chg >= 0;

  if (isDropped) return null;

  const handleTrack = async () => {
    if (isTracked || !onTrack || !theme) return;
    setTracking(true);
    try {
      await trackCompany({
        ticker: company.ticker,
        name: company.name,
        theme_raw: theme,
        theme_slug: themeSummary && themeSummary.length <= 50 ? themeSummary : undefined as any,
        exchange: company.exchange || null,
        sector: company.sector || null,
        source_tier: tier === 'direct' ? 'direct' : tier === 'enabler' ? 'enabler' : 'deep',
        depth_level: null,
        user_notes: null,
        llm_data: {
          thesis: company.thesis,
          catalysts: company.catalysts,
          revenue_exposure: company.revenue_exposure,
          earnings_signal: company.earnings_signal,
          risk: company.risk,
          supply_chain_role: company.supply_chain_role,
          hidden_link: company.hidden_link,
          discovery_insight: company.discovery_insight,
          why_overlooked: company.why_overlooked,
          connection_type: company.connection_type,
          connection_to: company.connection_to,
          why_nobody_covers_this: company.why_nobody_covers_this,
        },
        financial_data: {
          price: company.price,
          price_chg_1d: company.price_chg_1d,
          pe_ratio: company.pe_ratio,
          forward_pe: company.forward_pe,
          market_cap: company.market_cap,
          week52_high: company.week52_high,
          week52_low: company.week52_low,
          sector: company.sector,
        },
      } as any);
      onTrack(company.ticker);
    } catch (e: any) {
      alert(e?.message || 'Track failed');
    } finally {
      setTracking(false);
    }
  };

  const drop = (reason?: string) => {
    setDropMenu(false);
    onDrop?.(company.ticker, reason);
  };

  return (
    <div className={`rounded-2xl border bg-base-100/60 backdrop-blur transition-all duration-200 overflow-hidden ${pal.border}`}>
      {/* Header gradient strip */}
      <div className={`bg-gradient-to-b ${pal.header} px-4 pt-3 pb-2`}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            {/* Ticker badge + name */}
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded-lg ${pal.badge} ${pal.badgeText}`}>
                {company.ticker}
              </span>
              {company.sector && (
                <span className="text-[9px] text-base-content/40 uppercase tracking-wide">
                  {company.sector}
                </span>
              )}
            </div>
            <div className="text-sm font-semibold text-base-content/90 mt-0.5 leading-tight">
              {company.name}
            </div>
            {/* Verification badge */}
            {company.verified === false && (
              <div
                className="flex items-center gap-1 mt-1 text-[9px] font-medium text-amber-400/90 bg-amber-500/10 border border-amber-500/20 rounded-md px-1.5 py-0.5 w-fit"
                title={company.verification_note || 'Evidence confidence is low — verify before acting'}
              >
                <ShieldAlert className="w-2.5 h-2.5 flex-shrink-0" />
                Low evidence confidence
              </div>
            )}
            {company.verified === true && company.verification_note && (
              <div
                className="flex items-center gap-1 mt-1 text-[9px] font-medium text-emerald-400/80 bg-emerald-500/10 border border-emerald-500/20 rounded-md px-1.5 py-0.5 w-fit cursor-help"
                title={company.verification_note}
              >
                <ShieldCheck className="w-2.5 h-2.5 flex-shrink-0" />
                Verified
              </div>
            )}
          </div>

          {/* Price + change */}
          <div className="text-right flex-shrink-0">
            <div className="text-lg font-bold tabular-nums">{fmtPrice(company.price)}</div>
            {chg != null && (
              <div className={`flex items-center justify-end gap-0.5 text-xs font-semibold ${up ? 'text-success' : 'text-error'}`}>
                {up ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                {Math.abs(chg).toFixed(2)}%
              </div>
            )}
            {company.market_cap && (
              <div className="text-[9px] text-base-content/40 mt-0.5">{company.market_cap}</div>
            )}
          </div>
        </div>
      </div>

      {/* P/E row */}
      <div className="px-4 py-2 border-b border-white/[0.04] flex items-center gap-4">
        <div className="text-center">
          <div className="text-[9px] uppercase tracking-wider text-base-content/50">P/E (TTM)</div>
          <div className="text-sm font-semibold tabular-nums">{fmtPE(company.pe_ratio)}</div>
        </div>
        <div className="w-px h-6 bg-white/[0.06]" />
        <div className="text-center">
          <div className="text-[9px] uppercase tracking-wider text-base-content/50">Fwd P/E</div>
          <div className={`text-sm font-semibold tabular-nums ${
            company.forward_pe && company.pe_ratio && company.forward_pe < company.pe_ratio
              ? 'text-success' : ''}`}>
            {fmtPE(company.forward_pe)}
          </div>
        </div>
        {company.forward_pe && company.pe_ratio && (
          <>
            <div className="w-px h-6 bg-white/[0.06]" />
            <div className="text-center">
              <div className="text-[9px] uppercase tracking-wider text-base-content/50">Growth</div>
              <div className={`text-xs font-semibold ${company.forward_pe < company.pe_ratio ? 'text-success' : 'text-warning'}`}>
                {company.forward_pe < company.pe_ratio ? '↓ contracting' : '↑ expanding'}
              </div>
            </div>
          </>
        )}
      </div>

      {/* 52W range */}
      <div className="px-4 py-2.5 border-b border-white/[0.04]">
        <div className="text-[9px] uppercase tracking-wider text-base-content/50 mb-1.5">52-Week Range</div>
        <RangeBar price={company.price} low={company.week52_low} high={company.week52_high} />
      </div>

      {/* Thesis */}
      <div className="px-4 py-3">
        <div className="flex items-start gap-1.5 mb-2">
          <Lightbulb className="w-3 h-3 text-base-content/50 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-base-content/80 leading-relaxed">{company.thesis}</p>
        </div>

        {/* Catalysts */}
        {company.catalysts && company.catalysts.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-2">
            {company.catalysts.map((c, i) => (
              <span key={i} className={`text-[9px] px-1.5 py-0.5 rounded-full border ${pal.catPill}`}>
                {c}
              </span>
            ))}
          </div>
        )}

        {/* Depth insight fields — always visible for Tier 2 & 3 */}
        {company.supply_chain_role && (
          <div className="flex items-start gap-1.5 mb-1.5">
            <Cpu className="w-3 h-3 text-cyan-400/70 mt-0.5 flex-shrink-0" />
            <div>
              <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Supply Chain Role</div>
              <div className="text-[11px] text-base-content/80 font-medium">{company.supply_chain_role}</div>
            </div>
          </div>
        )}
        {company.hidden_link && (
          <div className="flex items-start gap-1.5 mb-1.5">
            <Gem className="w-3 h-3 text-violet-400/70 mt-0.5 flex-shrink-0" />
            <div>
              <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Hidden Connection</div>
              <div className="text-[11px] text-base-content/80 font-medium">{company.hidden_link}</div>
            </div>
          </div>
        )}

        {/* Expandable details */}
        {(company.revenue_exposure || company.earnings_signal || company.risk || company.why_overlooked || company.discovery_insight || company.why_this_not_another) && (
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-[10px] text-base-content/50 hover:text-base-content flex items-center gap-1 mt-1"
          >
            <ChevronRight className={`w-3 h-3 transition-transform ${expanded ? 'rotate-90' : ''}`} />
            {expanded ? 'Less detail' : 'More detail'}
          </button>
        )}

        {expanded && (
          <div className="mt-2 space-y-2 border-t border-white/[0.04] pt-2">
            {company.why_this_not_another && (
              <div className="flex items-start gap-1.5">
                <Target className="w-3 h-3 text-amber-400/70 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Competitive Moat</div>
                  <div className="text-[11px] text-base-content/75">{company.why_this_not_another}</div>
                </div>
              </div>
            )}
            {company.why_overlooked && (
              <div className="flex items-start gap-1.5">
                <Gem className="w-3 h-3 text-violet-400/70 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Why It's Overlooked</div>
                  <div className="text-[11px] text-base-content/75">{company.why_overlooked}</div>
                </div>
              </div>
            )}
            {company.discovery_insight && (
              <div className="flex items-start gap-1.5">
                <Zap className="w-3 h-3 text-violet-400/70 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Discovery Insight</div>
                  <div className="text-[11px] text-base-content/75">{company.discovery_insight}</div>
                </div>
              </div>
            )}
            {company.revenue_exposure && (
              <div className="flex items-start gap-1.5">
                <BarChart2 className="w-3 h-3 text-base-content/40 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Theme Exposure</div>
                  <div className="text-[11px] text-base-content/75">{company.revenue_exposure}</div>
                </div>
              </div>
            )}
            {company.earnings_signal && (
              <div className="flex items-start gap-1.5">
                <BookOpen className="w-3 h-3 text-base-content/40 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Earnings Signal</div>
                  <div className="text-[11px] text-base-content/75 italic">{company.earnings_signal}</div>
                </div>
              </div>
            )}
            {company.risk && (
              <div className="flex items-start gap-1.5">
                <AlertTriangle className="w-3 h-3 text-warning/70 mt-0.5 flex-shrink-0" />
                <div>
                  <div className="text-[9px] uppercase text-base-content/40 mb-0.5">Key Risk</div>
                  <div className="text-[11px] text-warning/80">{company.risk}</div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Drop reason menu (inline, optional) */}
      {dropMenu && onDrop && (
        <div className="px-4 pb-2">
          <div className="rounded-xl border border-error/20 bg-error/5 p-2.5">
            <div className="text-[10px] uppercase tracking-wide text-error/70 mb-1.5 flex items-center gap-1">
              <X className="w-3 h-3" /> Why drop it? <span className="text-base-content/40 normal-case">(optional — teaches the AI)</span>
            </div>
            <div className="flex flex-wrap gap-1">
              {DROP_REASONS.map(r => (
                <button
                  key={r}
                  onClick={() => drop(r)}
                  className="text-[10px] px-2 py-0.5 rounded-full border border-error/20 text-base-content/70
                    hover:bg-error/10 hover:border-error/40 hover:text-error transition-colors"
                >
                  {r}
                </button>
              ))}
              <button
                onClick={() => drop()}
                className="text-[10px] px-2 py-0.5 rounded-full border border-white/10 text-base-content/50 hover:text-base-content hover:border-white/20 transition-colors"
              >
                Just remove
              </button>
              <button
                onClick={() => setDropMenu(false)}
                className="text-[10px] px-2 py-0.5 rounded-full text-base-content/40 hover:text-base-content"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Footer: actions */}
      <div className="px-4 pb-3 flex gap-2">
        <Link
          to={`/dashboard?ticker=${company.ticker}`}
          className="flex items-center justify-center gap-1 flex-1 py-1.5 rounded-xl text-xs font-semibold border border-white/[0.08] text-base-content/60 hover:text-primary hover:border-primary/40 hover:bg-primary/5 transition-all"
        >
          <ExternalLink className="w-3 h-3" />
          Analyze
        </Link>
        {onTrack && (
          <button
            onClick={handleTrack}
            disabled={isTracked || tracking}
            title={isTracked ? 'Already tracking' : 'Track this company'}
            className={`flex items-center justify-center gap-1 px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all ${
              isTracked
                ? 'border-success/40 bg-success/10 text-success cursor-default'
                : 'border-white/[0.08] text-base-content/60 hover:border-success/40 hover:bg-success/10 hover:text-success'
            }`}
          >
            {tracking ? <Loader2 className="w-3 h-3 animate-spin" /> : isTracked ? <BookmarkCheck className="w-3 h-3" /> : <Bookmark className="w-3 h-3" />}
            {isTracked ? 'Tracked' : 'Track'}
          </button>
        )}
        {onDrop && (
          <button
            onClick={() => setDropMenu(m => !m)}
            title="Drop from this analysis"
            className={`flex items-center justify-center px-2 py-1.5 rounded-xl text-xs border transition-all ${
              dropMenu
                ? 'border-error/40 text-error bg-error/10'
                : 'border-white/[0.06] text-base-content/30 hover:border-error/30 hover:text-error hover:bg-error/5'
            }`}
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tier section header + grid
// ---------------------------------------------------------------------------

const TIER_CONFIGS: Record<TierKind, {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  sublabel: string;
  iconCls: string;
  dividerCls: string;
  countLabel: string;
}> = {
  direct: {
    icon: Target,
    label: 'Theme Core',
    sublabel: 'Companies whose core business directly rides the wave',
    iconCls: 'text-amber-400',
    dividerCls: 'border-amber-500/20',
    countLabel: 'Direct plays',
  },
  enabler: {
    icon: Cpu,
    label: 'The Backbone',
    sublabel: 'Specialized equipment, materials & services without which the theme cannot happen physically',
    iconCls: 'text-cyan-400',
    dividerCls: 'border-cyan-500/20',
    countLabel: 'Enablers',
  },
  deep: {
    icon: Gem,
    label: 'Hidden Picks',
    sublabel: 'Second & third-order beneficiaries almost no thematic report covers — utilities, industrials, REITs, specialty materials',
    iconCls: 'text-violet-400',
    dividerCls: 'border-violet-500/20',
    countLabel: 'Deep picks',
  },
};

function TierSection({
  tier, companies, theme, themeSummary,
  onDrop, onTrack, trackedTickers, droppedTickers,
}: {
  tier: TierKind;
  companies: PickShovelCompany[];
  theme?: string;
  themeSummary?: string;
  onDrop?: (ticker: string, tier: TierKind, reason?: string) => void;
  onTrack?: (ticker: string) => void;
  trackedTickers?: Set<string>;
  droppedTickers?: Set<string>;
}) {
  const cfg = TIER_CONFIGS[tier];
  const Icon = cfg.icon;
  const visible = companies.filter(c => !droppedTickers?.has(c.ticker));
  if (!companies || companies.length === 0) return null;
  return (
    <div className="space-y-3">
      <div className={`flex items-center gap-3 pb-2 border-b ${cfg.dividerCls}`}>
        <Icon className={`w-5 h-5 ${cfg.iconCls}`} />
        <div>
          <div className="text-base font-bold">{cfg.label}</div>
          <div className="text-[11px] text-base-content/50">{cfg.sublabel}</div>
        </div>
        <div className={`ml-auto text-[10px] font-semibold ${cfg.iconCls} px-2 py-0.5 rounded-full border ${cfg.dividerCls} bg-opacity-10`}>
          {visible.length}/{companies.length} {cfg.countLabel}
        </div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {companies.map(c => (
          <CompanyCard
            key={c.ticker}
            company={c}
            tier={tier}
            theme={theme}
            themeSummary={themeSummary}
            onDrop={onDrop ? (t, reason) => onDrop(t, tier, reason) : undefined}
            onTrack={onTrack}
            isTracked={trackedTickers?.has(c.ticker)}
            isDropped={droppedTickers?.has(c.ticker)}
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connection-type badge for deeper layers
// ---------------------------------------------------------------------------

const CONNECTION_BADGES: Record<string, { label: string; cls: string }> = {
  supplier:   { label: 'Supplier',     cls: 'border-cyan-500/40 text-cyan-400 bg-cyan-500/5' },
  client:     { label: 'Client',       cls: 'border-green-500/40 text-green-400 bg-green-500/5' },
  bottleneck: { label: 'Bottleneck',   cls: 'border-orange-500/40 text-orange-400 bg-orange-500/5' },
  testing:    { label: 'Testing/Cert', cls: 'border-yellow-500/40 text-yellow-400 bg-yellow-500/5' },
  infra:      { label: 'Infrastructure', cls: 'border-violet-500/40 text-violet-400 bg-violet-500/5' },
  data:       { label: 'Data/Software', cls: 'border-blue-500/40 text-blue-400 bg-blue-500/5' },
  finance:    { label: 'Finance/Insurance', cls: 'border-pink-500/40 text-pink-400 bg-pink-500/5' },
};

// ---------------------------------------------------------------------------
// Dig Deeper section
// ---------------------------------------------------------------------------

function DigDeeperSection({
  layer, theme, themeSummary, onDrop, onTrack, trackedTickers, droppedTickers,
}: {
  layer: DigDeeperLayer;
  theme?: string;
  themeSummary?: string;
  onDrop?: (ticker: string) => void;
  onTrack?: (ticker: string) => void;
  trackedTickers?: Set<string>;
  droppedTickers?: Set<string>;
}) {
  const depthColors = [
    'border-violet-500/30 bg-violet-500/5',
    'border-fuchsia-500/30 bg-fuchsia-500/5',
    'border-pink-500/30 bg-pink-500/5',
    'border-rose-500/30 bg-rose-500/5',
    'border-orange-500/30 bg-orange-500/5',
  ];
  const colorCls = depthColors[(layer.depth_level - 1) % depthColors.length];
  const textColors = [
    'text-violet-400', 'text-fuchsia-400', 'text-pink-400', 'text-rose-400', 'text-orange-400',
  ];
  const textCls = textColors[(layer.depth_level - 1) % textColors.length];

  return (
    <div className="space-y-3">
      {/* Layer header */}
      <div className={`rounded-2xl border p-4 ${colorCls}`}>
        <div className="flex items-center gap-3 mb-2">
          <div className={`text-xs font-mono font-bold px-2 py-0.5 rounded-full border ${colorCls} ${textCls}`}>
            DEPTH {layer.depth_level}
          </div>
          <span className={`text-sm font-bold ${textCls}`}>{layer.depth_label}</span>
          {layer.from_cache && (
            <span className="ml-auto text-[9px] text-base-content/30 border border-white/10 rounded px-1.5 py-0.5">cached</span>
          )}
        </div>
        {layer.depth_rationale && (
          <p className="text-xs text-base-content/65 leading-relaxed">{layer.depth_rationale}</p>
        )}
      </div>

      {/* Companies grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {layer.companies.map(c => {
          const connBadge = c.connection_type ? CONNECTION_BADGES[c.connection_type] : null;
          return (
            <div key={c.ticker} className="relative">
              {/* Connection badge overlay */}
              {connBadge && (
                <div className={`absolute top-2 right-2 z-10 text-[9px] px-1.5 py-0.5 rounded-full border font-semibold ${connBadge.cls}`}>
                  {connBadge.label}
                </div>
              )}
              <CompanyCard
                company={c}
                tier="deep"
                theme={theme}
                themeSummary={themeSummary}
                onDrop={onDrop ? (t) => onDrop(t) : undefined}
                onTrack={onTrack}
                isTracked={trackedTickers?.has(c.ticker)}
                isDropped={droppedTickers?.has(c.ticker)}
              />
              {/* Connection context */}
              {c.connection_to && (
                <div className="mt-1 px-2 text-[10px] text-base-content/40 italic">
                  ↗ Connected to: {c.connection_to}
                </div>
              )}
              {c.why_nobody_covers_this && (
                <div className="mt-0.5 px-2 text-[10px] text-base-content/40 flex items-start gap-1">
                  <Gem className="w-2.5 h-2.5 mt-0.5 flex-shrink-0 text-violet-400/50" />
                  {c.why_nobody_covers_this}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QuestionBlock — renders one clarifying question as chip options (+ custom)
// ---------------------------------------------------------------------------

function QuestionBlock({
  question, selected, onChange,
}: {
  question: ClarifyingQuestion;
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const [customOpen, setCustomOpen] = useState(false);
  const [custom, setCustom] = useState('');

  const toggle = (value: string) => {
    if (question.allow_multiple) {
      onChange(selected.includes(value) ? selected.filter(v => v !== value) : [...selected, value]);
    } else {
      onChange(selected.includes(value) ? [] : [value]);
    }
  };

  const addCustom = () => {
    const v = custom.trim();
    if (!v) return;
    onChange(question.allow_multiple ? [...selected.filter(s => s !== v), v] : [v]);
    setCustom('');
    setCustomOpen(false);
  };

  // Options the AI offered + any custom values the user added (so they stay visible).
  const offered = question.options.map(o => o.value);
  const extras = selected.filter(s => !offered.includes(s));

  return (
    <div>
      <div className="text-xs font-semibold text-base-content/80 mb-1.5 flex items-center gap-1.5">
        <span className="text-primary">?</span>
        {question.question}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {question.options.map(o => {
          const on = selected.includes(o.value);
          return (
            <button
              key={o.value}
              onClick={() => toggle(o.value)}
              className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors flex items-center gap-1 ${
                on
                  ? 'border-primary bg-primary/15 text-primary font-semibold'
                  : 'border-white/10 bg-base-100/50 text-base-content/70 hover:border-primary/40 hover:bg-primary/5'
              }`}
            >
              {on && <Check className="w-3 h-3" />}
              {o.label}
            </button>
          );
        })}
        {extras.map(v => (
          <button
            key={v}
            onClick={() => toggle(v)}
            className="text-[11px] px-2.5 py-1 rounded-full border border-primary bg-primary/15 text-primary font-semibold flex items-center gap-1"
          >
            <Check className="w-3 h-3" />
            {v}
          </button>
        ))}
        {question.allow_custom && (
          customOpen ? (
            <span className="inline-flex items-center gap-1">
              <input
                autoFocus
                value={custom}
                onChange={e => setCustom(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addCustom(); } }}
                placeholder="your answer…"
                className="input input-xs input-bordered h-7 text-[11px] w-32 bg-base-100/70"
              />
              <button onClick={addCustom} className="text-primary hover:text-primary/80"><Check className="w-3.5 h-3.5" /></button>
            </span>
          ) : (
            <button
              onClick={() => setCustomOpen(true)}
              className="text-[11px] px-2 py-1 rounded-full border border-dashed border-white/15 text-base-content/50 hover:border-primary/40 hover:text-primary transition-colors flex items-center gap-1"
            >
              <Plus className="w-3 h-3" /> Other
            </button>
          )
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ResearchBriefCard — the AI's living understanding of the thesis
// ---------------------------------------------------------------------------

function ResearchBriefCard({
  brief, collapsible, onChange, onReinterpret,
}: {
  brief: ResearchBrief;
  collapsible?: boolean;
  onChange: (b: ResearchBrief) => void;
  onReinterpret?: () => void;
}) {
  const [collapsed, setCollapsed] = useState(!!collapsible);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(brief.interpretation);

  const removeScope = (i: number) =>
    onChange({ ...brief, scope_notes: brief.scope_notes.filter((_, idx) => idx !== i) });
  const removeExcl = (i: number) =>
    onChange({ ...brief, exclusions: brief.exclusions.filter((_, idx) => idx !== i) });

  const saveInterp = () => {
    onChange({ ...brief, interpretation: draft.trim() || brief.interpretation });
    setEditing(false);
  };

  return (
    <div className="rounded-2xl border border-primary/20 bg-gradient-to-b from-primary/[0.07] to-transparent overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-primary/10">
        <Brain className="w-4 h-4 text-primary" />
        <span className="text-xs font-bold uppercase tracking-wide text-primary">AI's read on your thesis</span>
        {onReinterpret && (
          <button
            onClick={onReinterpret}
            className="ml-auto text-[10px] text-base-content/50 hover:text-primary flex items-center gap-1"
            title="Re-read with the latest brief"
          >
            <RefreshCw className="w-3 h-3" /> Re-interpret
          </button>
        )}
        {collapsible && (
          <button
            onClick={() => setCollapsed(c => !c)}
            className={`${onReinterpret ? '' : 'ml-auto'} text-base-content/40 hover:text-base-content`}
          >
            <ChevronDown className={`w-4 h-4 transition-transform ${collapsed ? '-rotate-90' : ''}`} />
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="p-4 space-y-3">
          {/* Interpretation */}
          {editing ? (
            <div>
              <textarea
                value={draft}
                onChange={e => setDraft(e.target.value)}
                rows={3}
                className="textarea textarea-bordered w-full text-sm bg-base-100/70 leading-relaxed"
              />
              <div className="flex gap-2 mt-1.5">
                <button onClick={saveInterp} className="btn btn-xs btn-primary gap-1"><Check className="w-3 h-3" /> Save</button>
                <button onClick={() => { setDraft(brief.interpretation); setEditing(false); }} className="btn btn-xs btn-ghost">Cancel</button>
              </div>
            </div>
          ) : (
            <div className="group flex items-start gap-2">
              <p className="text-sm text-base-content/85 leading-relaxed flex-1">{brief.interpretation}</p>
              <button
                onClick={() => { setDraft(brief.interpretation); setEditing(true); }}
                title="Edit the AI's understanding"
                className="opacity-0 group-hover:opacity-100 transition-opacity text-base-content/40 hover:text-primary mt-0.5"
              >
                <Pencil className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {/* Scope notes (angles inferred from the thesis) */}
          {brief.scope_notes.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-1.5">Focus angles inferred from your thesis</div>
              <div className="flex flex-wrap gap-1.5">
                {brief.scope_notes.map((s, i) => (
                  <span key={i} className="group inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full border border-cyan-500/25 bg-cyan-500/5 text-base-content/80">
                    {s}
                    <button onClick={() => removeScope(i)} className="text-base-content/30 hover:text-error opacity-0 group-hover:opacity-100 transition-opacity">
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Exclusions */}
          {brief.exclusions.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-1.5">Avoiding</div>
              <div className="flex flex-wrap gap-1.5">
                {brief.exclusions.map((e, i) => (
                  <span key={i} className="group inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full border border-error/25 bg-error/5 text-base-content/80">
                    {e}
                    <button onClick={() => removeExcl(i)} className="text-base-content/30 hover:text-error opacity-0 group-hover:opacity-100 transition-opacity">
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Learned preferences (AI-managed, from keep/drop) */}
          {brief.preferences_learned.length > 0 && (
            <div className="rounded-xl border border-violet-500/20 bg-violet-500/5 p-2.5">
              <div className="text-[10px] uppercase tracking-wide text-violet-400/80 mb-1.5 flex items-center gap-1">
                <Wand2 className="w-3 h-3" /> What the AI learned from your picks
              </div>
              <ul className="space-y-1">
                {brief.preferences_learned.map((p, i) => (
                  <li key={i} className="text-[11px] text-base-content/75 flex items-start gap-1.5">
                    <span className="text-violet-400 mt-0.5">•</span>{p}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

type Phase = 'input' | 'briefing' | 'results';

export function PickAndShovel() {
  const [phase, setPhase] = useState<Phase>('input');
  const [theme, setTheme] = useState('');

  // Briefing state
  const [brief, setBrief] = useState<ResearchBrief | null>(null);
  const [questions, setQuestions] = useState<ClarifyingQuestion[]>([]);
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [userReply, setUserReply] = useState('');
  const [interpreting, setInterpreting] = useState(false);
  const [interpretError, setInterpretError] = useState<string | null>(null);

  // Results state
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PickShovelResponse | null>(null);
  const [deeperLayers, setDeeperLayers] = useState<DigDeeperLayer[]>([]);
  const [digLoading, setDigLoading] = useState(false);
  const [digError, setDigError] = useState<string | null>(null);

  // Track / drop state
  const [trackedTickers, setTrackedTickers] = useState<Set<string>>(new Set());
  const [droppedTickers, setDroppedTickers] = useState<Set<string>>(new Set());     // cosmetic hide (all)
  const [droppedMeta, setDroppedMeta] = useState<DroppedCard[]>([]);                 // base-tier drops (for refine)
  const [everShown, setEverShown] = useState<Set<string>>(new Set());                // every ticker ever shown/dropped

  // Refine state
  const [refining, setRefining] = useState(false);
  const [refineError, setRefineError] = useState<string | null>(null);
  const [learningNote, setLearningNote] = useState<string | null>(null);
  const [followUp, setFollowUp] = useState<ClarifyingQuestion | null>(null);
  const [followUpAns, setFollowUpAns] = useState<string[]>([]);

  const inputRef = useRef<HTMLInputElement>(null);

  // -- helpers ---------------------------------------------------------------

  const answersAsList = (): ClarifyingAnswer[] =>
    questions
      .filter(q => (answers[q.id] || []).length > 0)
      .map(q => ({ question: q.question, answer: (answers[q.id] || []).join(', ') }));

  /** Fold answered questions into the brief's scope notes for generation. */
  const briefWithAnswers = (b: ResearchBrief): ResearchBrief => {
    const folded = answersAsList().map(a => `${a.question} → ${a.answer}`);
    const merged = [...b.scope_notes];
    folded.forEach(f => { if (!merged.includes(f)) merged.push(f); });
    return { ...b, scope_notes: merged };
  };

  const seedShown = (resp: PickShovelResponse) => {
    const t = [...resp.direct_plays, ...resp.enablers, ...resp.deep_picks].map(c => c.ticker);
    setEverShown(new Set(t));
  };

  // -- phase A → B: interpret -----------------------------------------------

  const runInterpret = async (thesisText?: string, opts?: { reply?: string }) => {
    const thesis = (thesisText ?? theme).trim();
    if (!thesis) return;
    setTheme(thesis);
    setPhase('briefing');
    setInterpreting(true);
    setInterpretError(null);
    try {
      const resp = await interpretThesis({
        thesis,
        brief,
        user_reply: opts?.reply ?? null,
        answers: answersAsList(),
      });
      setBrief(resp.brief);
      setQuestions(resp.clarifying_questions);
      setUserReply('');
    } catch (e: any) {
      setInterpretError(e?.message || 'Could not interpret the thesis');
    } finally {
      setInterpreting(false);
    }
  };

  // -- phase B → C: generate -------------------------------------------------

  const generate = async () => {
    if (!brief) return;
    const effectiveBrief = briefWithAnswers(brief);
    setBrief(effectiveBrief);
    setLoading(true);
    setError(null);
    setResult(null);
    setDeeperLayers([]);
    setDigError(null);
    setTrackedTickers(new Set());
    setDroppedTickers(new Set());
    setDroppedMeta([]);
    setLearningNote(null);
    setFollowUp(null);
    setPhase('results');
    try {
      const data = await analyzePickShovel(effectiveBrief.thesis, effectiveBrief);
      setResult(data);
      seedShown(data);
    } catch (e: any) {
      setError(e?.message || 'Analysis failed');
    } finally {
      setLoading(false);
    }
  };

  const regenerate = async () => {
    // Re-run generation with the current brief (used by the in-results refresh).
    if (!brief) return;
    await generate();
  };

  // -- drop handlers ---------------------------------------------------------

  const handleBaseDrop = (ticker: string, tier: TierKind, reason?: string) => {
    setDroppedTickers(prev => new Set([...prev, ticker]));
    setDroppedMeta(prev => {
      if (prev.some(d => d.ticker === ticker)) return prev;
      const all = [...(result?.direct_plays || []), ...(result?.enablers || []), ...(result?.deep_picks || [])];
      const name = all.find(c => c.ticker === ticker)?.name;
      return [...prev, { ticker, name, tier: tier as PickShovelTier, reason: reason || null }];
    });
  };

  const handleCosmeticDrop = (ticker: string) => {
    setDroppedTickers(prev => new Set([...prev, ticker]));
  };

  const handleTrack = (ticker: string) => {
    setTrackedTickers(prev => new Set([...prev, ticker]));
  };

  // -- refine: learn from kept + dropped, backfill dropped slots -------------

  const handleRefine = async () => {
    if (!result || !brief || droppedMeta.length === 0) return;
    setRefining(true);
    setRefineError(null);
    setLearningNote(null);

    const droppedSet = new Set(droppedMeta.map(d => d.ticker));
    const tiers: { key: TierKind; arr: PickShovelCompany[] }[] = [
      { key: 'direct', arr: result.direct_plays },
      { key: 'enabler', arr: result.enablers },
      { key: 'deep', arr: result.deep_picks },
    ];
    const kept = tiers.flatMap(({ key, arr }) =>
      arr.filter(c => !droppedSet.has(c.ticker))
        .map(c => ({ ticker: c.ticker, name: c.name, tier: key as PickShovelTier, thesis: c.thesis })),
    );
    const allShown = Array.from(new Set([
      ...everShown,
      ...[...result.direct_plays, ...result.enablers, ...result.deep_picks].map(c => c.ticker),
      ...deeperLayers.flatMap(l => l.companies.map(c => c.ticker)),
      ...droppedMeta.map(d => d.ticker),
    ]));

    try {
      const resp = await refinePickShovel({
        brief,
        kept,
        dropped: droppedMeta,
        already_shown: allShown,
      });

      // Splice: drop the dropped tickers from each base tier, append the backfill.
      setResult(prev => {
        if (!prev) return prev;
        const merge = (arr: PickShovelCompany[], add?: PickShovelCompany[]) =>
          [...arr.filter(c => !droppedSet.has(c.ticker)), ...(add || [])];
        return {
          ...prev,
          direct_plays: merge(prev.direct_plays, resp.new_companies.direct),
          enablers:     merge(prev.enablers, resp.new_companies.enabler),
          deep_picks:   merge(prev.deep_picks, resp.new_companies.deep),
        };
      });

      // Grow the permanent exclusion set so future rounds never repeat anything.
      const newTickers = [
        ...(resp.new_companies.direct || []),
        ...(resp.new_companies.enabler || []),
        ...(resp.new_companies.deep || []),
      ].map(c => c.ticker);
      setEverShown(prev => new Set([...prev, ...allShown, ...newTickers]));

      setBrief(resp.brief);
      setLearningNote(resp.learning_note || null);
      setFollowUp(resp.follow_up_question);
      setFollowUpAns([]);
      // Dropped slots are now filled — clear the pending drop state.
      setDroppedTickers(new Set());
      setDroppedMeta([]);
    } catch (e: any) {
      setRefineError(e?.message || 'Refine failed');
    } finally {
      setRefining(false);
    }
  };

  // Answer the AI's follow-up question → fold into the brief for the next round.
  const applyFollowUp = () => {
    if (!followUp || !brief || followUpAns.length === 0) return;
    const note = `${followUp.question} → ${followUpAns.join(', ')}`;
    setBrief({ ...brief, scope_notes: brief.scope_notes.includes(note) ? brief.scope_notes : [...brief.scope_notes, note] });
    setFollowUp(null);
    setFollowUpAns([]);
  };

  // -- dig deeper (unchanged behaviour, uses everShown for exclusion) --------

  const digDeeper = async () => {
    if (!result || !theme) return;
    setDigLoading(true);
    setDigError(null);

    const allShown = Array.from(new Set([
      ...everShown,
      ...[...result.direct_plays, ...result.enablers, ...result.deep_picks].map(c => c.ticker),
      ...deeperLayers.flatMap(l => l.companies.map(c => c.ticker)),
    ]));

    const prevCompanies = deeperLayers.length > 0
      ? deeperLayers[deeperLayers.length - 1].companies
      : result.deep_picks;

    const anchors = prevCompanies.slice(0, 6).map(c => ({
      ticker: c.ticker,
      name: c.name,
      supply_chain_role: c.supply_chain_role || null,
      hidden_link: c.hidden_link || null,
      thesis: c.thesis || null,
    }));

    try {
      const layer = await digDeeperPickShovel({
        theme,
        already_shown: allShown,
        anchor_companies: anchors,
        depth_level: deeperLayers.length + 1,
      });
      setDeeperLayers(prev => [...prev, layer]);
      setEverShown(prev => new Set([...prev, ...layer.companies.map(c => c.ticker)]));
    } catch (e: any) {
      setDigError(e?.message || 'Dig deeper failed');
    } finally {
      setDigLoading(false);
    }
  };

  // -- reset to a fresh thesis -----------------------------------------------

  const startOver = () => {
    setPhase('input');
    setBrief(null);
    setQuestions([]);
    setAnswers({});
    setUserReply('');
    setResult(null);
    setDeeperLayers([]);
    setTrackedTickers(new Set());
    setDroppedTickers(new Set());
    setDroppedMeta([]);
    setEverShown(new Set());
    setLearningNote(null);
    setFollowUp(null);
    setError(null);
    setInterpretError(null);
  };

  const droppedCount = droppedMeta.length;
  const keptCount = result
    ? [...result.direct_plays, ...result.enablers, ...result.deep_picks].filter(c => !droppedTickers.has(c.ticker)).length
    : 0;

  // =========================================================================
  // Render
  // =========================================================================

  return (
    <div className="space-y-6">
      {/* ---- Thesis bar (always visible at top in input & briefing) ---- */}
      {phase !== 'results' && (
        <div className="rounded-2xl border border-primary/20 bg-primary/5 p-5">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles className="w-4 h-4 text-primary" />
            <span className="text-xs font-bold uppercase tracking-wide text-primary">
              Pick & Shovel Research
            </span>
            <span className="ml-auto text-[10px] text-base-content/50">
              Your AI analyst reads your thesis, sharpens it with you, then maps the value chain
            </span>
          </div>
          <form
            onSubmit={e => { e.preventDefault(); runInterpret(); }}
            className="flex gap-2"
          >
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-base-content/40" />
              <input
                ref={inputRef}
                type="text"
                value={theme}
                onChange={e => setTheme(e.target.value)}
                placeholder="State your thesis… e.g. 'AI data center build-out, but avoid the obvious GPU names'"
                disabled={interpreting}
                className="input input-bordered w-full pl-9 text-sm bg-base-100/70"
              />
            </div>
            <button
              type="submit"
              disabled={interpreting || !theme.trim()}
              className="btn btn-primary gap-2 min-w-28"
            >
              {interpreting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Brain className="w-4 h-4" />}
              {interpreting ? 'Reading…' : 'Interpret'}
            </button>
          </form>

          {/* Suggestion chips (only on the empty input phase) */}
          {phase === 'input' && (
            <div className="mt-3">
              <div className="text-[10px] text-base-content/50 mb-2 uppercase tracking-wide">Quick start</div>
              <div className="flex flex-wrap gap-1.5">
                {EXAMPLE_THEMES.map(t => (
                  <button
                    key={t}
                    onClick={() => runInterpret(t)}
                    disabled={interpreting}
                    className="text-[11px] px-2.5 py-1 rounded-full border border-primary/20 bg-base-100/60
                      hover:bg-primary/10 hover:border-primary/40 transition-colors text-base-content/70"
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ---- Phase B: Briefing ---- */}
      {phase === 'briefing' && (
        <div className="space-y-4">
          {interpreting && !brief && (
            <div className="rounded-2xl border border-white/[0.06] bg-base-100/40 p-5 animate-pulse">
              <div className="h-4 w-1/3 bg-base-300 rounded mb-3" />
              <div className="h-4 w-full bg-base-300/60 rounded mb-2" />
              <div className="h-4 w-4/5 bg-base-300/60 rounded" />
              <div className="text-center text-xs text-base-content/50 mt-4 not-prose">Reading your thesis…</div>
            </div>
          )}

          {interpretError && (
            <div className="alert alert-error rounded-2xl">
              <AlertCircle className="w-4 h-4" />
              <span className="text-sm">{interpretError}</span>
              <button onClick={() => runInterpret()} className="btn btn-xs btn-ghost gap-1">
                <RefreshCw className="w-3 h-3" /> Retry
              </button>
            </div>
          )}

          {brief && (
            <>
              <ResearchBriefCard brief={brief} onChange={setBrief} />

              {/* Clarifying questions — only when the AI actually asked some */}
              {questions.length > 0 && (
                <div className="rounded-2xl border border-white/[0.07] bg-base-100/50 p-4 space-y-4">
                  <div className="flex items-center gap-2">
                    <Lightbulb className="w-4 h-4 text-amber-400" />
                    <span className="text-xs font-bold uppercase tracking-wide text-base-content/70">
                      A few things to sharpen
                    </span>
                    <span className="text-[10px] text-base-content/40 ml-1">— optional, but it helps me target better</span>
                  </div>
                  {questions.map(q => (
                    <QuestionBlock
                      key={q.id}
                      question={q}
                      selected={answers[q.id] || []}
                      onChange={vals => setAnswers(prev => ({ ...prev, [q.id]: vals }))}
                    />
                  ))}
                </div>
              )}

              {/* Refine-in-words + actions */}
              <div className="rounded-2xl border border-white/[0.07] bg-base-100/40 p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Wand2 className="w-3.5 h-3.5 text-primary" />
                  <span className="text-[11px] font-semibold text-base-content/70">Not quite right? Tell the AI in your own words</span>
                </div>
                <form
                  onSubmit={e => { e.preventDefault(); if (userReply.trim()) runInterpret(undefined, { reply: userReply }); }}
                  className="flex gap-2"
                >
                  <input
                    type="text"
                    value={userReply}
                    onChange={e => setUserReply(e.target.value)}
                    placeholder="e.g. 'I mean the physical build-out — power, cooling, land — not the chips'"
                    disabled={interpreting}
                    className="input input-bordered input-sm w-full text-sm bg-base-100/70"
                  />
                  <button
                    type="submit"
                    disabled={interpreting || !userReply.trim()}
                    className="btn btn-sm btn-ghost gap-1"
                    title="Re-interpret with this clarification"
                  >
                    {interpreting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                    Update
                  </button>
                </form>

                <div className="flex items-center gap-2 mt-4 pt-3 border-t border-white/[0.05]">
                  <button onClick={startOver} className="btn btn-sm btn-ghost gap-1 text-base-content/60">
                    <ArrowLeft className="w-3.5 h-3.5" /> New thesis
                  </button>
                  <button
                    onClick={generate}
                    disabled={interpreting || loading}
                    className="btn btn-primary btn-sm gap-2 ml-auto min-w-36"
                  >
                    <Zap className="w-4 h-4" />
                    Generate picks
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
                {questions.length === 0 && !interpreting && (
                  <p className="text-[10px] text-base-content/40 mt-2 text-right">
                    No open questions — I've got a clear read. Generate whenever you're ready.
                  </p>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* ---- Phase C: Results ---- */}
      {phase === 'results' && (
        <>
          {/* Error */}
          {error && (
            <div className="alert alert-error rounded-2xl">
              <AlertCircle className="w-4 h-4" />
              <span className="text-sm">{error}</span>
              <button onClick={regenerate} className="btn btn-xs btn-ghost gap-1">
                <RefreshCw className="w-3 h-3" /> Retry
              </button>
              <button onClick={startOver} className="btn btn-xs btn-ghost">New thesis</button>
            </div>
          )}

          {/* Loading skeleton */}
          {loading && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-white/[0.06] bg-base-100/40 p-5 animate-pulse">
                <div className="h-6 w-2/3 bg-base-300 rounded-lg mb-3" />
                <div className="h-4 w-full bg-base-300/60 rounded mb-2" />
                <div className="h-4 w-5/6 bg-base-300/60 rounded mb-2" />
                <div className="h-4 w-3/4 bg-base-300/60 rounded" />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="rounded-2xl border border-white/[0.06] bg-base-100/40 p-4 animate-pulse h-64" />
                ))}
              </div>
              <div className="text-center text-xs text-base-content/50 animate-pulse">
                Researching supply chain, fetching earnings signals, enriching with live prices…
              </div>
            </div>
          )}

          {/* Results */}
          {result && !loading && (
            <div className="space-y-6">
              {/* Living research brief (collapsible) */}
              {brief && (
                <ResearchBriefCard brief={brief} collapsible onChange={setBrief} />
              )}

              {/* Theme overview card */}
              <div className="rounded-2xl border border-white/[0.06] bg-base-100/60 p-5">
                <div className="flex items-start justify-between gap-4 mb-3">
                  <div>
                    <h2 className="text-xl font-bold tracking-tight">{result.theme_title}</h2>
                    <p className="text-sm text-base-content/70 leading-relaxed mt-1 max-w-3xl">
                      {result.theme_summary}
                    </p>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button onClick={regenerate} className="btn btn-sm btn-ghost gap-1" title="Re-generate with current brief">
                      <RefreshCw className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={startOver} className="btn btn-sm btn-ghost gap-1" title="Start a new thesis">
                      <ArrowLeft className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Key trends */}
                {result.key_trends && result.key_trends.length > 0 && (
                  <div>
                    <div className="text-[10px] uppercase tracking-wide text-base-content/50 mb-2">
                      Key structural trends
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {result.key_trends.map((t, i) => (
                        <div
                          key={i}
                          className="flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-xl
                            border border-primary/20 bg-primary/5 text-base-content/80"
                        >
                          <ChevronRight className="w-3 h-3 text-primary" />
                          {t}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Supply chain map */}
                {result.supply_chain_map && (
                  <div className="mt-3 p-3 rounded-xl border border-cyan-500/15 bg-cyan-500/5">
                    <div className="text-[9px] uppercase tracking-wider text-cyan-400/70 mb-1 flex items-center gap-1">
                      <Cpu className="w-3 h-3" /> Supply Chain Map
                    </div>
                    <p className="text-xs text-base-content/75 leading-relaxed">{result.supply_chain_map}</p>
                  </div>
                )}

                {/* Summary stats row */}
                <div className="flex items-center gap-4 mt-4 pt-3 border-t border-white/[0.04]">
                  <div className="flex items-center gap-1.5 text-xs text-amber-400">
                    <Target className="w-3.5 h-3.5" />
                    <span className="font-semibold">{result.direct_plays.length}</span>
                    <span className="text-base-content/50">Theme Core</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-cyan-400">
                    <Cpu className="w-3.5 h-3.5" />
                    <span className="font-semibold">{result.enablers.length}</span>
                    <span className="text-base-content/50">Backbone</span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-violet-400">
                    <Gem className="w-3.5 h-3.5" />
                    <span className="font-semibold">{result.deep_picks.length}</span>
                    <span className="text-base-content/50">Hidden Picks</span>
                  </div>
                  <div className="ml-auto flex items-center gap-2">
                    {result.from_cache && (
                      <span className="text-[9px] text-base-content/30 border border-white/10 rounded px-1.5 py-0.5">cached</span>
                    )}
                    <span className="text-[10px] text-base-content/40">
                      {new Date(result.generated_at).toLocaleTimeString()} · Not investment advice
                    </span>
                  </div>
                </div>
              </div>

              {/* Refine bar — appears once the user has dropped a card */}
              {(droppedCount > 0 || learningNote || followUp) && (
                <div className="rounded-2xl border border-violet-500/25 bg-violet-500/5 p-4 space-y-3">
                  {learningNote && (
                    <div className="flex items-start gap-2 text-xs text-base-content/80">
                      <Wand2 className="w-4 h-4 text-violet-400 flex-shrink-0 mt-0.5" />
                      <span><span className="font-semibold text-violet-300">AI learned:</span> {learningNote}</span>
                    </div>
                  )}

                  {/* Follow-up question after a refine */}
                  {followUp && (
                    <div className="rounded-xl border border-white/[0.07] bg-base-100/40 p-3 space-y-2">
                      <QuestionBlock
                        question={followUp}
                        selected={followUpAns}
                        onChange={setFollowUpAns}
                      />
                      <button
                        onClick={applyFollowUp}
                        disabled={followUpAns.length === 0}
                        className="btn btn-xs btn-primary gap-1"
                      >
                        <Check className="w-3 h-3" /> Apply
                      </button>
                    </div>
                  )}

                  {refineError && (
                    <div className="alert alert-error rounded-xl text-sm">
                      <AlertCircle className="w-4 h-4 flex-shrink-0" />
                      <span>{refineError}</span>
                    </div>
                  )}

                  {droppedCount > 0 && (
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="text-sm text-base-content/80">
                        You dropped <span className="font-bold text-error">{droppedCount}</span>
                        {' '}· keeping <span className="font-bold text-success">{keptCount}</span> — want sharper picks?
                      </span>
                      <button
                        onClick={handleRefine}
                        disabled={refining}
                        className="btn btn-sm gap-2 ml-auto border-violet-500/40 bg-violet-500/10 hover:bg-violet-500/20 text-violet-200"
                      >
                        {refining ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}
                        {refining ? 'Learning from your picks…' : 'Refine & backfill'}
                        {!refining && <ChevronRight className="w-4 h-4" />}
                      </button>
                    </div>
                  )}
                  {refining && (
                    <p className="text-[10px] text-base-content/50">
                      Reading what you kept vs dropped, updating my understanding, and finding replacements that fit — without repeating anything you've already seen.
                    </p>
                  )}
                </div>
              )}

              {/* Three tiers */}
              <TierSection tier="direct"  companies={result.direct_plays} theme={theme} themeSummary={result.theme_title} onDrop={handleBaseDrop} onTrack={handleTrack} trackedTickers={trackedTickers} droppedTickers={droppedTickers} />
              <TierSection tier="enabler" companies={result.enablers}     theme={theme} themeSummary={result.theme_title} onDrop={handleBaseDrop} onTrack={handleTrack} trackedTickers={trackedTickers} droppedTickers={droppedTickers} />
              <TierSection tier="deep"    companies={result.deep_picks}   theme={theme} themeSummary={result.theme_title} onDrop={handleBaseDrop} onTrack={handleTrack} trackedTickers={trackedTickers} droppedTickers={droppedTickers} />

              {/* Deeper layers */}
              {deeperLayers.map(layer => (
                <DigDeeperSection key={layer.depth_level} layer={layer} theme={theme} themeSummary={result.theme_title} onDrop={handleCosmeticDrop} onTrack={handleTrack} trackedTickers={trackedTickers} droppedTickers={droppedTickers} />
              ))}

              {/* Dig Deeper CTA */}
              <div className="flex flex-col items-center gap-3 pt-2">
                {digError && (
                  <div className="alert alert-error rounded-2xl text-sm w-full max-w-lg">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    <span>{digError}</span>
                    <button onClick={digDeeper} className="btn btn-xs btn-ghost gap-1">
                      <RefreshCw className="w-3 h-3" /> Retry
                    </button>
                  </div>
                )}

                {/* Progress trail */}
                {deeperLayers.length > 0 && (
                  <div className="flex items-center gap-1.5 text-[10px] text-base-content/40">
                    <span className="w-2 h-2 rounded-full bg-amber-400/60" />
                    Tier 1–3
                    {deeperLayers.map((l, i) => (
                      <React.Fragment key={l.depth_level}>
                        <span className="text-base-content/20">→</span>
                        <span className={`w-2 h-2 rounded-full ${
                          ['bg-violet-400/60','bg-fuchsia-400/60','bg-pink-400/60','bg-rose-400/60','bg-orange-400/60'][i % 5]
                        }`} />
                        Depth {l.depth_level}
                      </React.Fragment>
                    ))}
                  </div>
                )}

                <button
                  onClick={digDeeper}
                  disabled={digLoading}
                  className="group relative flex items-center gap-3 px-6 py-3 rounded-2xl
                    border border-violet-500/30 bg-violet-500/5 hover:bg-violet-500/10
                    hover:border-violet-500/50 transition-all duration-200
                    text-sm font-semibold text-violet-300 hover:text-violet-200
                    disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {digLoading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Digging deeper — finding hidden gems…
                    </>
                  ) : (
                    <>
                      <Gem className="w-4 h-4 group-hover:animate-pulse" />
                      {deeperLayers.length === 0
                        ? 'Dig Deeper — find hidden gems'
                        : `Go Deeper Again — Level ${deeperLayers.length + 1}`}
                      <span className="text-[10px] font-normal text-violet-400/60 ml-1">
                        {deeperLayers.length === 0
                          ? 'traces suppliers, clients & bottlenecks'
                          : `escalating investigation — never repeats companies`}
                      </span>
                      <ChevronRight className="w-4 h-4 ml-auto opacity-50 group-hover:opacity-100 group-hover:translate-x-0.5 transition-transform" />
                    </>
                  )}
                </button>

                {digLoading && (
                  <p className="text-[10px] text-base-content/40 text-center max-w-sm">
                    {deeperLayers.length === 0
                      ? 'Tracing direct suppliers and clients of companies found so far…'
                      : deeperLayers.length === 1
                      ? 'Investigating testing, certification & physical infrastructure providers…'
                      : deeperLayers.length === 2
                      ? 'Searching for raw material origins, MRO providers & specialty logistics…'
                      : 'Going deeper into financial infrastructure, IP holders & adjacent geographies…'}
                  </p>
                )}
              </div>

              {/* Footer */}
              <div className="text-[10px] text-base-content/30 text-center pb-2">
                Analysis generated by AI · Company identification via GPT-4o · Live prices via Yahoo Finance ·
                Verify independently before investing · Not financial advice
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
