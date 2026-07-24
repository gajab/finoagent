import React from 'react';
import { Sparkles } from 'lucide-react';
import type { TechnicalData } from '../types';

// ── Underlying "read" + price-levels ladder ───────────────────────────────────
// Replaces the six-chart dump as the top-of-fold of the Underlying drawer: a
// plain-English read + signal chips, then ONE price ladder that folds the
// scattered support / resistance / POC / value-area numbers into a single visual.
// The full charts stay available, collapsed behind a "Full technicals" expander.

const fmt = (n: number | null | undefined, d = 2) =>
  n == null ? '—' : `$${n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })}`;

// Bullish → success, bearish → danger, otherwise neutral.
const toneFor = (s?: string | null): 'good' | 'bad' | 'flat' => {
  const u = (s || '').toLowerCase();
  if (/bull|above|rising|golden|up|strong|accumulat|expand/.test(u)) return 'good';
  if (/bear|below|falling|death|down|weak|distribut|contract/.test(u)) return 'bad';
  return 'flat';
};
const chipClass = (t: 'good' | 'bad' | 'flat') =>
  t === 'good' ? 'bg-success/10 text-success border-success/20'
    : t === 'bad' ? 'bg-error/10 text-error border-error/20'
      : 'border-white/[0.08] text-base-content/60';

function Chip({ tone = 'flat', children }: { tone?: 'good' | 'bad' | 'flat'; children: React.ReactNode }) {
  return <span className={`text-[11px] px-2 py-0.5 rounded-full border ${chipClass(tone)}`}>{children}</span>;
}

// A proportional vertical price ladder. Only levels that are present get drawn.
// Dark-theme-only app, so explicit rgb (daisyUI v4 stores theme colors as OKLCH,
// which breaks inline hsl(var(--x))). Labels are de-collided with a min-gap pass
// and connected to their true tick position with a thin leader line.
type LadderEntry = { y0: number; y1: number; text: string; color: string; strong?: boolean; tick?: string; spot?: boolean };

function LevelsLadder({ tech, spot, week52 }: {
  tech: TechnicalData; spot?: number; week52?: { low: number; high: number };
}) {
  const vp = tech.institutional?.volume_profile ?? null;
  const s = spot ?? tech.institutional?.price;
  const R = tech.resistanceLevel > 0 ? tech.resistanceLevel : null;
  const S = tech.supportLevel > 0 ? tech.supportLevel : null;
  const poc = vp?.poc ?? null;
  const vah = vp?.vah ?? null;
  const val = vp?.val ?? null;
  const hi52 = week52?.high ?? null;
  const lo52 = week52?.low ?? null;

  const domain = [s, R, S, poc, vah, val, hi52, lo52].filter((x): x is number => x != null && x > 0);
  if (domain.length < 2) return null;
  let lo = Math.min(...domain), hi = Math.max(...domain);
  const pad = Math.max((hi - lo) * 0.05, 0.5);
  lo -= pad; hi += pad;
  if (hi <= lo) hi = lo + 1;

  const W = 190, TOP = 14, BOT = 278, RAIL_X = 18, LABEL_X = 34, GAP = 15;
  const y = (p: number) => TOP + ((hi - p) / (hi - lo)) * (BOT - TOP);

  const MUTED = 'rgba(226,232,240,0.5)', BRIGHT = 'rgb(226,232,240)';
  const RED = 'rgb(248,113,113)', GREEN = 'rgb(74,222,128)', AMBER = 'rgb(251,191,36)';

  const entries: LadderEntry[] = [];
  const add = (p: number | null, text: string, color: string, opts: Partial<LadderEntry> = {}) => {
    if (p == null) return;
    entries.push({ y0: y(p), y1: y(p), text, color, ...opts });
  };
  add(hi52, `52W high ${fmt(hi52, 0)}`, MUTED);
  add(R, `Resistance ${fmt(R, 0)}`, RED, { tick: RED });
  add(s ?? null, `Spot ${fmt(s)}`, BRIGHT, { strong: true, spot: true });
  if (vah != null && val != null) add((vah + val) / 2, `Value area ${fmt(val, 0)}–${fmt(vah, 0)}`, MUTED);
  add(poc, `POC ${fmt(poc, 0)}`, AMBER, { tick: AMBER });
  add(S, `Support ${fmt(S, 0)}`, GREEN, { tick: GREEN });
  add(lo52, `52W low ${fmt(lo52, 0)}`, MUTED);

  // De-collide: push labels down to keep a min gap, then clamp back up from bottom.
  entries.sort((a, b) => a.y0 - b.y0);
  let prev = TOP - GAP;
  for (const e of entries) { e.y1 = Math.max(e.y0, prev + GAP); prev = e.y1; }
  let next = BOT + GAP;
  for (let i = entries.length - 1; i >= 0; i--) { entries[i].y1 = Math.min(entries[i].y1, next - GAP); next = entries[i].y1; }

  return (
    <svg viewBox={`0 0 ${W} ${BOT + 14}`} width="100%" role="img"
      aria-label={`Price ladder: spot ${fmt(s)}, resistance ${fmt(R)}, POC ${fmt(poc)}, support ${fmt(S)}, 52-week range ${fmt(lo52)} to ${fmt(hi52)}.`}>
      {vah != null && val != null && (
        <rect x={11} y={y(vah)} width={14} height={Math.max(2, y(val) - y(vah))} rx={2} fill="rgba(124,122,221,0.18)" />
      )}
      <line x1={RAIL_X} y1={TOP} x2={RAIL_X} y2={BOT} stroke="rgba(226,232,240,0.18)" strokeWidth={2} />
      {entries.map((e, i) => (
        <g key={i}>
          {e.spot
            ? <circle cx={RAIL_X} cy={e.y0} r={4.5} fill={e.color} />
            : e.tick
              ? <line x1={11} y1={e.y0} x2={25} y2={e.y0} stroke={e.tick} strokeWidth={2} />
              : <line x1={13} y1={e.y0} x2={23} y2={e.y0} stroke={e.color} strokeWidth={1} />}
          {Math.abs(e.y1 - e.y0) > 2 && (
            <line x1={26} y1={e.y0} x2={LABEL_X - 2} y2={e.y1} stroke={e.color} strokeWidth={0.75} strokeOpacity={0.5} />
          )}
          <text x={LABEL_X} y={e.y1 + 3.5} fontSize={e.strong ? 12.5 : 11} fontWeight={e.strong ? 500 : 400} fill={e.color}>{e.text}</text>
        </g>
      ))}
    </svg>
  );
}

export function UnderlyingSummary({ technical, spot, week52 }: {
  technical: TechnicalData; ticker?: string; spot?: number; week52?: { low: number; high: number };
}) {
  const regime = technical.institutional?.regime ?? null;
  const va = technical.volumeAnalysis;
  const state = regime?.state || va?.phase || 'Neutral';
  const stateTone = toneFor(regime?.bias || va?.priceTrend || state);
  const read = regime?.rationale || (technical.analysisSummary || '').split(/(?<=\.)\s/)[0] || '';

  const macd = technical.macd;
  const ma = technical.movingAverages;
  const volPct = va?.volumeChangePct;

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/20 p-4">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-bold flex items-center gap-1.5"><Sparkles className="w-4 h-4 text-secondary" /> The read</span>
        <span className={`text-[11px] px-2 py-0.5 rounded-full border ${chipClass(stateTone)}`}>{state}</span>
      </div>
      {read && <p className="text-sm text-base-content/70 mt-2 leading-relaxed">{read}</p>}

      <div className="flex flex-wrap gap-1.5 mt-3">
        {technical.currentRSI != null && (
          <Chip tone={toneFor(technical.rsiSignal)}>RSI {technical.currentRSI.toFixed(0)}{technical.rsiSignal ? ` · ${technical.rsiSignal}` : ''}</Chip>
        )}
        {macd?.signal && <Chip tone={toneFor(macd.signal)}>MACD {macd.signal}</Chip>}
        {ma?.priceVsSma50 && <Chip tone={toneFor(ma.priceVsSma50)}>{ma.priceVsSma50} 50-MA</Chip>}
        {ma?.priceVsSma200 && <Chip tone={toneFor(ma.priceVsSma200)}>{ma.priceVsSma200} 200-MA</Chip>}
        {ma?.goldenDeathCross && <Chip tone={toneFor(ma.goldenDeathCross)}>{ma.goldenDeathCross}</Chip>}
        {va?.volumeTrend && (
          <Chip tone={toneFor(va.volumeTrend)}>Volume {va.volumeTrend}{volPct != null ? ` ${volPct >= 0 ? '+' : ''}${volPct.toFixed(0)}%` : ''}</Chip>
        )}
        {regime?.signals?.vs_value_area && <Chip>{regime.signals.vs_value_area} value area</Chip>}
      </div>

      {regime?.favored_income_labels && regime.favored_income_labels.length > 0 && (
        <p className="text-[11px] text-base-content/50 mt-3">
          Regime favors: <span className="text-base-content/70">{regime.favored_income_labels.join(' · ')}</span>
        </p>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-[190px_1fr] gap-4 mt-4 items-start">
        <div className="rounded-lg border border-white/[0.06] p-2.5">
          <p className="text-[11px] text-base-content/50 mb-1 pl-1">Key levels</p>
          <LevelsLadder tech={technical} spot={spot} week52={week52} />
        </div>
        {va?.bigMoneyAnalysis && (
          <div className="rounded-lg border border-white/[0.06] p-3">
            <p className="text-[11px] uppercase tracking-wider text-base-content/50 mb-1">Institutional flow</p>
            <p className="text-xs text-base-content/70 leading-relaxed">{va.bigMoneyAnalysis}</p>
          </div>
        )}
      </div>
    </div>
  );
}
