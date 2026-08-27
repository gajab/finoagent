import React from 'react';
import { Crosshair, TrendingUp, TrendingDown, Minus, ArrowUpRight, ArrowDownRight, Layers, Gauge } from 'lucide-react';
import type { ExitModernTechnical, ExitModernSetup, ExitSetupLevel } from '../../types';

interface Props {
  data?: ExitModernTechnical;
}

function levelStr(l: ExitSetupLevel | number | null | undefined): string {
  if (l == null) return '—';
  if (typeof l === 'number') return `$${l.toFixed(2)}`;
  if (l.low != null && l.high != null) return `$${l.low.toFixed(2)}–$${l.high.toFixed(2)}`;
  if (l.level != null) return `$${(l.level as number).toFixed(2)}`;
  return '—';
}

function biasCfg(dir?: string) {
  const d = (dir || 'neutral').toLowerCase();
  if (d === 'bullish') return { cls: 'text-success', badge: 'badge-success', icon: <TrendingUp size={14} /> };
  if (d === 'bearish') return { cls: 'text-error', badge: 'badge-error', icon: <TrendingDown size={14} /> };
  return { cls: 'text-base-content/70', badge: 'badge-ghost', icon: <Minus size={14} /> };
}

function miniScoreColor(score: number, entry: boolean): string {
  // entry: higher=better (green high). exit: higher=worse (red high).
  if (entry) return score >= 62 ? 'text-success' : score >= 45 ? 'text-info' : 'text-warning';
  return score >= 60 ? 'text-error' : score >= 45 ? 'text-warning' : 'text-success';
}

function SetupCard({ s }: { s: ExitModernSetup }) {
  const long = (s.direction || '').toLowerCase() === 'long';
  const short = (s.direction || '').toLowerCase() === 'short';
  const dirCls = long ? 'badge-success' : short ? 'badge-error' : 'badge-ghost';
  const targets = (s.targets || []).filter((t) => t.level != null);
  const bestRr = s.risk_reward ?? (targets.length ? Math.max(...targets.map((t) => t.rr ?? 0)) : null);
  // horizon and entry_style come back as objects ({label, note, ...}) or strings
  const horizonLabel = typeof s.horizon === 'string' ? s.horizon : s.horizon?.label;
  const styleLabel = typeof s.entry_style === 'string' ? s.entry_style : s.entry_style?.label;
  const styleNote = typeof s.entry_style === 'object' && s.entry_style ? s.entry_style.note : undefined;
  const confidence = typeof s.confidence === 'number' ? `${(s.confidence * 100).toFixed(0)}%` : (s.confidence as string | undefined);

  return (
    <div className="bg-base-200/60 rounded-xl p-3.5 border border-white/[0.04]">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        {s.rank != null && <span className="badge badge-xs badge-neutral">#{s.rank}</span>}
        <span className={`badge badge-sm ${dirCls} gap-1`}>
          {long ? <ArrowUpRight size={11} /> : short ? <ArrowDownRight size={11} /> : <Minus size={11} />}
          {s.direction || 'neutral'}
        </span>
        {s.type && <span className="text-[11px] text-base-content/50">{s.type}</span>}
        {bestRr != null && (
          <span className={`badge badge-sm ml-auto ${bestRr >= 2 ? 'badge-success' : bestRr >= 1 ? 'badge-warning' : 'badge-ghost'}`}>
            {bestRr.toFixed(1)}:1 R:R
          </span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 text-center mb-2">
        <div className="bg-base-100/60 rounded-lg p-2">
          <div className="text-[10px] text-base-content/40">Entry</div>
          <div className="text-xs font-bold tabular-nums">{levelStr(s.entry)}</div>
        </div>
        <div className="bg-base-100/60 rounded-lg p-2">
          <div className="text-[10px] text-base-content/40">Stop</div>
          <div className="text-xs font-bold tabular-nums text-error">{levelStr(s.stop)}</div>
        </div>
        <div className="bg-base-100/60 rounded-lg p-2">
          <div className="text-[10px] text-base-content/40">Target{targets.length > 1 ? 's' : ''}</div>
          <div className="text-xs font-bold tabular-nums text-success">
            {targets.length ? targets.map((t) => `$${(t.level as number).toFixed(0)}`).join(' / ') : '—'}
          </div>
        </div>
      </div>

      {s.thesis && <p className="text-[11px] text-base-content/60 leading-relaxed">{s.thesis}</p>}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5 text-[10px] text-base-content/40">
        {horizonLabel && <span>Horizon: {horizonLabel}</span>}
        {styleLabel && <span>Style: {styleLabel}</span>}
        {s.regime_fit && <span>Regime fit: {s.regime_fit}</span>}
        {confidence && <span>Confidence: {confidence}</span>}
      </div>
      {styleNote && <p className="text-[10px] text-base-content/45 mt-1 leading-relaxed">{styleNote}</p>}
    </div>
  );
}

export function TradeSetupPanel({ data }: Props) {
  if (!data || !data.available) {
    return (
      <div className="glass-card p-5">
        <h3 className="font-bold text-sm flex items-center gap-2 mb-2">
          <Crosshair className="w-4 h-4 text-primary" /> Trade-Setup Engine
        </h3>
        <p className="text-sm text-base-content/50">
          The trade-setup engine couldn't produce a read for this ticker (insufficient intraday/options data).
          See the momentum indicators below.
        </p>
      </div>
    );
  }

  const bias = data.bias || { direction: 'neutral', strength: 'weak', score: 0 };
  const bc = biasCfg(bias.direction);
  const setups = data.setups || [];

  return (
    <div className="glass-card p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="font-bold text-sm flex items-center gap-2">
          <Crosshair className="w-4 h-4 text-primary" /> Trade-Setup Engine
          <span className="text-[10px] text-base-content/40 font-normal">bias · regime · structure · ranked setups</span>
        </h3>
        <span className="text-[10px] text-base-content/40">Same engine as the Strategies desk</span>
      </div>

      {/* Bias + regime + timing scores */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        <div className={`rounded-xl p-3 border ${bc.badge === 'badge-success' ? 'bg-success/10 border-success/20' : bc.badge === 'badge-error' ? 'bg-error/10 border-error/20' : 'bg-base-200/60 border-white/[0.06]'}`}>
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1">Directional Bias</div>
          <div className={`text-lg font-black flex items-center gap-1.5 ${bc.cls}`}>{bc.icon}{bias.strength} {bias.direction}</div>
        </div>
        <div className="rounded-xl p-3 border bg-base-200/60 border-white/[0.06]">
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1 flex items-center gap-1"><Gauge size={11} /> Regime</div>
          <div className="text-lg font-black capitalize">{data.regime || '—'}</div>
        </div>
        <div className="rounded-xl p-3 border bg-base-200/60 border-white/[0.06]">
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1">Entry Timing</div>
          <div className={`text-lg font-black tabular-nums ${miniScoreColor(data.entry_timing_score ?? 50, true)}`}>{data.entry_timing_score ?? '—'}</div>
        </div>
        <div className="rounded-xl p-3 border bg-base-200/60 border-white/[0.06]">
          <div className="text-[10px] uppercase tracking-wider text-base-content/40 mb-1">Exit Pressure</div>
          <div className={`text-lg font-black tabular-nums ${miniScoreColor(data.exit_pressure_score ?? 50, false)}`}>{data.exit_pressure_score ?? '—'}</div>
        </div>
      </div>

      {bias.rationale && (
        <p className="text-xs text-base-content/60 leading-relaxed bg-base-200/40 rounded-lg p-3 border border-white/[0.03]">
          <span className="font-semibold text-base-content/70">Why: </span>{bias.rationale}
        </p>
      )}

      {/* Ranked setups */}
      <div>
        <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/40 mb-2 flex items-center gap-1.5">
          <Layers size={13} /> Ranked Setups {setups.length > 0 && `(${setups.length})`}
        </h4>
        {setups.length > 0 ? (
          <div className="space-y-3">
            {setups.map((s, i) => <SetupCard key={i} s={s} />)}
          </div>
        ) : (
          <p className="text-sm text-base-content/50">No clean setup right now — the engine dropped sub-1:1 R:R plays rather than force a trade.</p>
        )}
      </div>
    </div>
  );
}
