import React, { useState } from 'react';
import { Activity, ChevronDown, ChevronUp, Info } from 'lucide-react';
import type { TaBrief } from '../types';
import { isGraded, type GradeFactor } from '../lib/factors';

export { isGraded };

/** Pre-trade technical context — direction, momentum (MACD/RSI), volume profile, volume, market structure, recent
 *  chart patterns, tape. Built deterministically on the backend from the SAME TA read the desk graded with, and
 *  NEVER scored: it's what to know about the tape before selling into it. */
const TONE: Record<string, string> = {
  bull: 'bg-success', bear: 'bg-error', warn: 'bg-warning', neutral: 'bg-base-content/30',
};

export function TaBriefPanel({ brief, defaultOpen = true, compact = false }: {
  brief?: TaBrief | null; defaultOpen?: boolean; compact?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!brief || !brief.items?.length) return null;
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/30">
      <button type="button" className="w-full flex items-center gap-2 px-3 py-2 text-left" onClick={() => setOpen(o => !o)}>
        <Activity className="w-3.5 h-3.5 text-info" />
        <span className="text-xs font-semibold">Before you trade — technical context</span>
        <span className="text-[10px] text-base-content/45">not scored</span>
        {brief.timeframe && !compact && (
          <span className="ml-auto text-[10px] text-base-content/40">
            levels · {brief.timeframe}{brief.trend_timeframe ? ` · trend · ${brief.trend_timeframe}` : ''}
          </span>
        )}
        {open ? <ChevronUp className={`w-3.5 h-3.5 ${compact || !brief.timeframe ? 'ml-auto' : ''}`} /> : <ChevronDown className={`w-3.5 h-3.5 ${compact || !brief.timeframe ? 'ml-auto' : ''}`} />}
      </button>
      {open && (
        <div className="px-3 pb-2.5 space-y-1.5">
          {brief.items.map(it => (
            <div key={it.key} className="flex items-start gap-2 text-[11px] leading-snug">
              <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${TONE[it.tone] || TONE.neutral}`} />
              <span className="w-28 shrink-0 text-base-content/55">{it.topic}</span>
              <span className="text-base-content/80">{it.text}</span>
            </div>
          ))}
          {brief.note && (
            <p className="text-[10px] text-base-content/40 flex items-start gap-1 pt-1">
              <Info className="w-3 h-3 mt-px shrink-0" /> {brief.note}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** A trade's own not-scored context: the info-only reads (trend vs this structure, systemic beta, the volume node
 *  at its strike, VRP) and any factor that landed on 0 pts — kept as evidence, out of the grade build-up. */
export function TradeContextNotes({ factors }: { factors?: GradeFactor[] }) {
  const info = (factors || []).filter(f => !isGraded(f) && f.detail);
  if (!info.length) return null;
  return (
    <div className="mb-2.5 rounded-md border border-white/[0.05] bg-base-100/30 px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wider text-base-content/45 mb-1">Context for this trade · not scored</div>
      {info.map(f => (
        <div key={f.label} className="text-[10.5px] leading-snug text-base-content/70">
          <span className="text-base-content/50">{f.label}:</span> {f.detail!.replace(/^info only( \(direction isn't scored\))? — /, '')}
        </div>
      ))}
    </div>
  );
}
