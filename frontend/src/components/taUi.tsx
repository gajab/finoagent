import React from 'react';
import { TrendingUp, TrendingDown, Minus, HelpCircle } from 'lucide-react';

// Direction is a STATUS: long = good/emerald, short = critical/rose, neutral = warning/amber.
// Always shipped with an icon + text label — never color alone (accessibility).
export const dirTone = (d?: string) =>
  d === 'long' || d === 'bullish' ? 'success'
    : d === 'short' || d === 'bearish' ? 'error'
      : 'warning';

const TONE_CLASS: Record<string, string> = {
  success: 'text-success border-success/30 bg-success/10',
  error: 'text-error border-error/30 bg-error/10',
  warning: 'text-warning border-warning/30 bg-warning/10',
};

export function DirectionBadge({ direction, strength, size = 'md' }: { direction: string; strength?: string; size?: 'sm' | 'md' | 'lg' }) {
  const tone = dirTone(direction);
  const Icon = tone === 'success' ? TrendingUp : tone === 'error' ? TrendingDown : Minus;
  const label = direction === 'long' || direction === 'bullish' ? 'LONG'
    : direction === 'short' || direction === 'bearish' ? 'SHORT' : 'NEUTRAL';
  const pad = size === 'lg' ? 'px-3 py-1.5 text-sm' : size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2.5 py-1 text-xs';
  const ico = size === 'lg' ? 'w-4 h-4' : 'w-3 h-3';
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-lg border font-bold tracking-wide ${TONE_CLASS[tone]} ${pad}`}>
      <Icon className={ico} /> {label}{strength && <span className="opacity-60 font-medium">· {strength}</span>}
    </span>
  );
}

export function ConfidencePill({ level }: { level: string }) {
  const tone = level === 'high' ? 'success' : level === 'medium' ? 'warning' : 'error';
  const dots = level === 'high' ? 3 : level === 'medium' ? 2 : 1;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${TONE_CLASS[tone]}`}>
      <span className="flex gap-0.5">
        {[0, 1, 2].map(i => <span key={i} className={`w-1 h-1 rounded-full ${i < dots ? 'bg-current' : 'bg-current/25'}`} />)}
      </span>
      {level} confidence
    </span>
  );
}

// A non-chart "stat tile" — the right form for a single headline number (per dataviz form heuristic).
export function StatTile({ label, value, sub, tone, tip }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string; tip?: string }) {
  const valTone = tone === 'success' ? 'text-success' : tone === 'error' ? 'text-error' : tone === 'warning' ? 'text-warning' : 'text-base-content';
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/30 px-3 py-2.5">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-base-content/45">
        {label}{tip && <InfoTip text={tip} />}
      </div>
      <div className={`text-lg font-bold tabular-nums leading-tight mt-0.5 ${valTone}`}>{value}</div>
      {sub && <div className="text-[10px] text-base-content/50 leading-tight mt-0.5">{sub}</div>}
    </div>
  );
}

// Plain-English "?" explainer — identity/meaning never left to jargon alone.
export function InfoTip({ text }: { text: string }) {
  return (
    <span className="tooltip tooltip-top inline-flex align-middle" data-tip={text} aria-label={text}>
      <HelpCircle className="w-3 h-3 text-base-content/30 hover:text-base-content/60 cursor-help" />
    </span>
  );
}

// A small section header with an icon and optional plain-English intro.
export function SectionIntro({ icon, title, children }: { icon?: React.ReactNode; title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-2">
      <h4 className="text-sm font-bold text-base-content/80 flex items-center gap-2">{icon}{title}</h4>
      {children && <p className="text-[11px] text-base-content/50 mt-0.5 leading-snug">{children}</p>}
    </div>
  );
}
