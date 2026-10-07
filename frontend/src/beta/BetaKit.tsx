/**
 * BetaKit — the small shared building blocks the Beta panels are made of, so every Beta surface reads the same:
 * one tone palette (good · warn · bad · info · neutral · accent), accordions with a one-line summary, chips,
 * stat tiles, callouts and a score ring. Nothing below 11px.
 */
import React, { useEffect, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

export type Tone = 'good' | 'warn' | 'bad' | 'info' | 'neutral' | 'accent';

// Static class strings so Tailwind's JIT generates them (no safelist).
export const TONE: Record<Tone, { text: string; soft: string; border: string; chip: string; bar: string; ring: string; dot: string }> = {
  good:    { text: 'text-success', soft: 'bg-success/[0.06]', border: 'border-success/30', chip: 'bg-success/15 text-success border-success/30', bar: 'bg-success', ring: 'stroke-success', dot: 'bg-success' },
  warn:    { text: 'text-warning', soft: 'bg-warning/[0.06]', border: 'border-warning/30', chip: 'bg-warning/15 text-warning border-warning/30', bar: 'bg-warning', ring: 'stroke-warning', dot: 'bg-warning' },
  bad:     { text: 'text-error', soft: 'bg-error/[0.06]', border: 'border-error/30', chip: 'bg-error/15 text-error border-error/30', bar: 'bg-error', ring: 'stroke-error', dot: 'bg-error' },
  info:    { text: 'text-info', soft: 'bg-info/[0.06]', border: 'border-info/30', chip: 'bg-info/15 text-info border-info/30', bar: 'bg-info', ring: 'stroke-info', dot: 'bg-info' },
  accent:  { text: 'text-secondary', soft: 'bg-secondary/[0.06]', border: 'border-secondary/30', chip: 'bg-secondary/15 text-secondary border-secondary/30', bar: 'bg-secondary', ring: 'stroke-secondary', dot: 'bg-secondary' },
  neutral: { text: 'text-base-content/70', soft: 'bg-base-content/[0.04]', border: 'border-white/[0.1]', chip: 'bg-base-content/10 text-base-content/70 border-base-content/20', bar: 'bg-base-content/40', ring: 'stroke-base-content/50', dot: 'bg-base-content/40' },
};

export function Chip({ tone = 'neutral', children, title, className = '' }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return <span title={title} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap ${TONE[tone].chip} ${className}`}>{children}</span>;
}

export function Tile({ label, value, sub, tone, title }: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone; title?: string }) {
  return (
    <div className="rounded-lg bg-base-200/50 px-2.5 py-2 min-w-0" title={title}>
      <div className="text-[11px] text-base-content/50">{label}</div>
      <div className={`text-sm font-semibold tabular-nums truncate ${tone ? TONE[tone].text : ''}`}>{value}</div>
      {sub != null && <div className="text-[11px] text-base-content/45 truncate">{sub}</div>}
    </div>
  );
}

export function Callout({ tone = 'neutral', icon, children, className = '' }: { tone?: Tone; icon?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed ${TONE[tone].border} ${TONE[tone].soft} ${tone === 'neutral' ? 'text-base-content/70' : TONE[tone].text} ${className}`}>
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** Circular score (0–100) in the tone's colour. */
export function ScoreRing({ value, tone = 'neutral', size = 60, label }: { value: number; tone?: Tone; size?: number; label?: string }) {
  const r = (size - 8) / 2, c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={`${label ?? 'Score'} ${Math.round(v)} of 100`}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={5} className="stroke-base-content/10" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={5} strokeLinecap="round" className={TONE[tone].ring}
          strokeDasharray={`${(v / 100) * c} ${c}`} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="text-base font-semibold tabular-nums">{Math.round(v)}</span>
        <span className="text-[11px] text-base-content/40 mt-0.5">/100</span>
      </div>
    </div>
  );
}

/** A thin 0–100 meter. */
export function Meter({ value, tone, className = '' }: { value: number; tone: Tone; className?: string }) {
  return <div className={`h-1.5 rounded-full bg-base-content/10 overflow-hidden ${className}`}><div className={`h-full rounded-full ${TONE[tone].bar}`} style={{ width: `${Math.max(3, Math.min(100, value))}%` }} /></div>;
}

/**
 * Accordion with a one-line summary beside the title, so a collapsed section still tells you what is inside.
 * Children mount on first open and stay mounted (state survives a close). `openSignal` lets a parent open it
 * (e.g. clicking a lens tile opens the Evidence section).
 */
export function Accordion({ title, summary, icon, badge, defaultOpen = false, openSignal, children }: {
  title: string; summary?: ReactNode; icon?: ReactNode; badge?: ReactNode; defaultOpen?: boolean; openSignal?: number; children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [seen, setSeen] = useState(defaultOpen);
  useEffect(() => { if (openSignal) { setOpen(true); setSeen(true); } }, [openSignal]);
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/25 overflow-hidden">
      <button type="button" aria-expanded={open} onClick={() => { setOpen(o => !o); setSeen(true); }}
        className="w-full flex items-center gap-2.5 px-3 py-2.5 text-left hover:bg-white/[0.03] transition-colors">
        {icon && <span className="text-base-content/50 shrink-0">{icon}</span>}
        <span className="text-sm font-semibold shrink-0">{title}</span>
        {summary != null && <span className="text-xs text-base-content/45 truncate min-w-0">{summary}</span>}
        <span className="ml-auto flex items-center gap-2 shrink-0">
          {badge}
          <ChevronDown className={`w-4 h-4 text-base-content/40 transition-transform ${open ? '' : '-rotate-90'}`} />
        </span>
      </button>
      {seen && <div className={open ? 'px-3 pb-3 pt-1' : 'hidden'}>{children}</div>}
    </div>
  );
}

/** Section title used inside Beta panels. */
export function SubHead({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return <div className="flex items-baseline gap-2 mb-1.5"><span className="text-xs font-semibold text-base-content/75">{children}</span>{hint && <span className="text-[11px] text-base-content/40">{hint}</span>}</div>;
}
