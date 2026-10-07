/**
 * BetaChrome — the small shared pieces of the Beta experience: the opt-in switch (URL-driven so a Beta
 * view is a shareable link and "Back to classic" is one click), the Beta badge, state chips and KPI tiles.
 *
 * Beta is OPT-IN: classic stays the default at /my-trades and /strategies. `?ui=beta` flips a page to Beta;
 * removing it flips back. Nothing is stored server-side and no classic behavior changes.
 */
import React, { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowLeft, Sparkles } from 'lucide-react';
import { STATE_META, type ActionState } from './betaStateMeta';

export function useBetaUi(): { isBeta: boolean; enterBeta: () => void; leaveBeta: () => void } {
  const [params, setParams] = useSearchParams();
  const isBeta = params.get('ui') === 'beta';
  const enterBeta = useCallback(() => {
    const n = new URLSearchParams(params); n.set('ui', 'beta'); setParams(n);
  }, [params, setParams]);
  const leaveBeta = useCallback(() => {
    const n = new URLSearchParams(params); n.delete('ui'); setParams(n);
  }, [params, setParams]);
  return { isBeta, enterBeta, leaveBeta };
}

export function BetaBadge({ className = '' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary ${className}`}>
      <Sparkles className="w-3 h-3" /> Beta
    </span>
  );
}

/** Shown on the CLASSIC page: the link into the Beta experience. */
export function TryBetaButton({ onClick, label = 'Try the new Beta' }: { onClick: () => void; label?: string }) {
  return (
    <button
      onClick={onClick}
      className="btn btn-sm gap-2 border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20 hover:border-primary/60"
      title="A faster, cleaner layout for the same data. Your classic view stays one click away."
    >
      <Sparkles className="w-3.5 h-3.5" /> {label}
      <span className="rounded bg-primary/20 px-1.5 text-[11px] leading-5">New</span>
    </button>
  );
}

/** Shown on the BETA page: the way back. */
export function BackToClassicButton({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className="btn btn-ghost btn-sm gap-2 border border-white/10" title="Return to the classic layout">
      <ArrowLeft className="w-3.5 h-3.5" /> Back to classic
    </button>
  );
}

export function StateChip({ state, className = '' }: { state: ActionState; className?: string }) {
  const m = STATE_META[state];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap ${m.chip} ${className}`} title={m.hint}>
      <span className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
      {m.label}
    </span>
  );
}

export function Kpi({ label, value, sub, tone = '', title }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: string; title?: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/40 px-3 py-2.5 min-w-0" title={title}>
      <div className="text-[11px] text-base-content/55">{label}</div>
      <div className={`text-xl font-semibold tabular-nums leading-tight mt-0.5 ${tone}`}>{value}</div>
      {sub != null && <div className="text-[11px] text-base-content/45 mt-0.5 truncate">{sub}</div>}
    </div>
  );
}

/** "5m ago" — the freshness of the saved numbers. */
export function timeAgo(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
