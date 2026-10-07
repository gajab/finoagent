/**
 * ScoreCard — the Beta's compact answer to "why this score?", replacing classic's ~900px Quant Analysis page
 * as the FIRST thing you see. It carries: the grade + state, the build-up (base → factors → technical → score,
 * with the cap stated), the five lenses, the biggest boosts/drags and the net points per risk dimension.
 * The full evidence (every lens row, factor and its detail line, Q-vs-P boundary, reconciliation) is one click
 * away — it mounts the classic QuantAnalysisSection unchanged, so nothing is lost, only moved behind a button.
 */
import React, { useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { DeskRankedTrade } from '../types';
import { QuantAnalysisSection, DIM_ORDER } from '../components/DeskReview';
import { buildScore } from './scoreModel';

const sgn = (n: number) => `${n > 0 ? '+' : ''}${n}`;
const pos = (v: number) => Math.max(0, Math.min(100, v));

function gradeTone(g: string, vetoed: boolean, waiting: boolean) {
  if (vetoed) return 'bg-error/15 text-error border-error/30';
  if (waiting) return 'bg-warning/15 text-warning border-warning/30';
  const u = g.toUpperCase();
  if (u === 'A' || u === 'B') return 'bg-success/15 text-success border-success/30';
  if (u === 'C' || u === 'D') return 'bg-warning/15 text-warning border-warning/30';
  return 'bg-base-content/10 text-base-content/70 border-base-content/20';
}

export default function ScoreCard({ t }: { t: DeskRankedTrade }) {
  const s = useMemo(() => buildScore(t, DIM_ORDER), [t]);
  const [audit, setAudit] = useState(false);

  // Build-up bar on a 0–100 scale: base, then each adjustment as a step from where the previous one ended.
  const afterOpt = s.base + s.optNet;
  const seg = (from: number, delta: number) => {
    const a = from, b = from + delta;
    return { left: pos(Math.min(a, b)), width: Math.max(0, pos(Math.max(a, b)) - pos(Math.min(a, b))), up: delta >= 0 };
  };
  const sOpt = seg(s.base, s.optNet);
  const sTa = seg(afterOpt, s.taNet);
  const state = s.vetoed ? { label: 'Vetoed', cls: 'text-error' } : s.waiting ? { label: 'Wait — timing', cls: 'text-warning' } : { label: 'Enter', cls: 'text-success' };

  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/30 overflow-hidden">
      {/* verdict */}
      <div className="flex items-center gap-3 px-3 py-2.5 border-b border-white/[0.06]">
        <div className={`h-11 w-11 shrink-0 rounded-full border flex items-center justify-center text-lg font-bold ${gradeTone(s.grade, s.vetoed, s.waiting)}`}>{s.vetoed ? 'V' : s.grade}</div>
        <div className="min-w-0">
          <div className="text-sm font-semibold">Desk grade {s.vetoed ? 'V' : s.grade}, score {Math.round(s.final)}</div>
          <div className={`text-xs ${state.cls}`}>{state.label}{s.vetoed ? ` — ${s.vetoReasons.join(' · ')}` : s.waiting ? ` — ${s.waitReasons.join(' · ')}` : ''}</div>
        </div>
      </div>

      {/* build-up */}
      <div className="px-3 py-3 border-b border-white/[0.06]">
        <div className="text-xs font-semibold text-base-content/70 mb-2">How the score is built <span className="font-normal text-base-content/40">base quality, then adjustments</span></div>
        <div className="relative h-5 rounded bg-base-content/[0.08] overflow-hidden" role="img"
          aria-label={`Base ${s.base}, option factors ${sgn(s.optNet)}, technical ${sgn(s.taNet)}, raw ${s.raw}, score ${Math.round(s.final)}`}>
          <div className="absolute inset-y-0 left-0 bg-base-content/35" style={{ width: `${pos(s.base)}%` }} />
          {s.optNet !== 0 && <div className={`absolute inset-y-0 ${sOpt.up ? 'bg-success' : 'bg-error'}`} style={{ left: `${sOpt.left}%`, width: `${sOpt.width}%` }} />}
          {s.taNet !== 0 && <div className={`absolute inset-y-0 ${sTa.up ? 'bg-success/70' : 'bg-error/70'}`} style={{ left: `${sTa.left}%`, width: `${sTa.width}%` }} />}
          <div className="absolute inset-y-[-2px] w-0.5 bg-base-content" style={{ left: `calc(${pos(s.final)}% - 1px)` }} title={`Score ${Math.round(s.final)}`} />
        </div>
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 mt-2 text-xs">
          <span className="text-base-content/60">Base <b className="text-base-content/90 tabular-nums">{s.base}</b></span>
          <span className={s.optNet >= 0 ? 'text-success' : 'text-error'}>{sgn(s.optNet)} option factors</span>
          <span className={s.taNet >= 0 ? 'text-success' : 'text-error'}>{sgn(s.taNet)} technical</span>
          <span className="text-base-content/60">= <b className="tabular-nums text-base-content/90">{s.raw}</b>
            {s.clamped && <span className="text-warning"> → {s.clamped === 'cap' ? 'capped at 100' : 'floored at 0'}</span>}
          </span>
        </div>
        {s.clamped === 'cap' && <div className="text-[11px] text-base-content/45 mt-1">Several trades can hit the cap, so the score alone cannot separate them — use the lenses and drags below.</div>}
      </div>

      {/* lenses */}
      {s.lenses.length > 0 && (
        <div className="px-3 py-3 border-b border-white/[0.06]">
          <div className="text-xs font-semibold text-base-content/70 mb-2">Base quality, {s.lenses.length} lenses <span className="font-normal text-base-content/40">weight in brackets</span></div>
          <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(5, s.lenses.length)}, minmax(0, 1fr))` }}>
            {s.lenses.map((l, i) => {
              const sc = Math.max(0, Math.min(100, Math.round(l.score)));
              const bar = sc >= 66 ? 'bg-success' : sc >= 40 ? 'bg-warning' : 'bg-error';
              return (
                <div key={i} className="min-w-0" title={l.note}>
                  <div className="text-base font-semibold tabular-nums leading-none">{sc}</div>
                  <div className="h-1.5 rounded-full bg-base-content/10 my-1.5 overflow-hidden"><div className={`h-full rounded-full ${bar}`} style={{ width: `${sc}%` }} /></div>
                  <div className="text-[11px] text-base-content/55 truncate">{l.label}{l.weight != null ? ` (${l.weight}%)` : ''}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* boosts / drags */}
      {(s.boosts.length > 0 || s.drags.length > 0) && (
        <div className="px-3 py-3 border-b border-white/[0.06] grid sm:grid-cols-2 gap-x-6 gap-y-3">
          {[['Biggest boosts', s.boosts, 'text-success'] as const, ['Biggest drags', s.drags, 'text-error'] as const].map(([title, list, c]) => (
            <div key={title} className="min-w-0">
              <div className="text-xs font-semibold text-base-content/70 mb-1">{title}</div>
              {list.length === 0 ? <div className="text-xs text-base-content/40">Nothing material</div> : list.map((f, i) => (
                <div key={i} className="flex items-baseline justify-between gap-2 py-0.5 text-xs border-b border-white/[0.04] last:border-0" title={f.detail}>
                  <span className="truncate text-base-content/75">{f.label}</span>
                  <span className={`font-mono font-semibold ${c}`}>{sgn(f.points)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* dimensions */}
      {s.dims.length > 0 && (
        <div className="px-3 py-3 border-b border-white/[0.06]">
          <div className="text-xs font-semibold text-base-content/70 mb-2">Where the adjustments come from <span className="font-normal text-base-content/40">net points by risk dimension</span></div>
          <div className="grid gap-1.5" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(96px, 1fr))' }}>
            {s.dims.map(d => (
              <div key={d.dim} className={`rounded-lg px-2 py-1.5 ${d.net > 0 ? 'bg-success/12 text-success' : d.net < 0 ? 'bg-error/12 text-error' : 'bg-base-content/[0.06] text-base-content/60'}`}>
                <div className="text-sm font-semibold tabular-nums leading-tight">{sgn(d.net)}</div>
                <div className="text-[11px] leading-tight opacity-90">{d.dim}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* audit trail */}
      <button className="w-full flex items-center gap-2 px-3 py-2 text-xs text-base-content/60 hover:text-base-content hover:bg-white/[0.03] transition-colors" onClick={() => setAudit(v => !v)}>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${audit ? '' : '-rotate-90'}`} />
        Audit trail — every lens, factor and its evidence
        <span className="ml-auto text-base-content/40">{audit ? 'Hide' : 'Show'}</span>
      </button>
      {audit && <div className="px-3 pb-3"><QuantAnalysisSection t={t} q={t.desk_metrics?.quant || {}} defaultOpen title="Full audit trail" subtitle="base + factors + TA → desk score" /></div>}
    </div>
  );
}
