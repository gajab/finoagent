/**
 * BetaTaPanels — the side panels of the Beta Technical cockpit: the lens rail (left), the plan card (right),
 * the levels table and the indicator grid. Presentation only: every number is computed in betaTaModel /
 * firstPassage and passed in, so what is on screen is what the tests check.
 */
import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { LineChart, Loader2, Crosshair, Info, CalendarClock, Eye } from 'lucide-react';
import { trackTrade } from '../../api';
import type { TechnicalData, TradeSetup } from '../../types';
import { TONE, Chip, Callout, SubHead, type Tone } from '../BetaKit';
import { TYPE_LABEL, SetupCard } from '../../components/TradeSetupCards';
import { InfoTip } from '../../components/taUi';
import {
  layoutGutter, money, pctStr, probStr,
  type Lens, type LensState, type LayerKey, type Disagreement, type PlanLevels, type LevelRow,
} from './betaTaModel';
import type { OddsResult } from './firstPassage';

// ── lens rail ────────────────────────────────────────────────────────────────────────────────────────────────────
const STATE: Record<LensState, { word: string; tone: Tone; dot: string }> = {
  supports: { word: 'supports', tone: 'good', dot: 'bg-success' },
  caution: { word: 'caution', tone: 'warn', dot: 'bg-warning' },
  against: { word: 'against', tone: 'bad', dot: 'bg-error' },
  neutral: { word: 'neutral', tone: 'neutral', dot: 'bg-base-content/40' },
  not_scored: { word: 'not scored', tone: 'neutral', dot: 'border border-base-content/40 bg-transparent' },
};
const LAYER_NAME: Record<LayerKey, string> = { plan: 'plan', levels: 'key levels', structure: 'structure', blocks: 'order blocks', liquidity: 'liquidity', dealer: 'dealer gamma', value: 'value & volume' };
const NEEDS_CTX = new Set(['trend', 'dealer', 'regime']);

export function LensRail({ lenses, pending, active, onToggle }: {
  lenses: Lens[]; pending: boolean; active: Set<LayerKey>; onToggle: (k: LayerKey) => void;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/25 p-3">
      <SubHead hint="read on daily data">Six lenses</SubHead>
      <ul className="space-y-0.5">
        {lenses.map(l => {
          const st = STATE[l.state];
          const wait = pending && NEEDS_CTX.has(l.key);
          const clickable = !!l.layer;
          const on = !!l.layer && active.has(l.layer);
          const body = (
            <>
              <div className="flex items-center gap-2">
                <span className={`h-2 w-2 rounded-full shrink-0 ${pending ? 'bg-base-content/25' : st.dot}`} aria-hidden="true" />
                <span className="text-[13px] font-medium flex-1 text-left">{l.label}</span>
                {!l.counted && <span className="text-[11px] text-base-content/35" title="Shown for context — the engine's call does not count it">context</span>}
                <span className={`text-[11px] font-medium ${pending ? 'text-base-content/40' : TONE[st.tone].text}`}>{pending ? 'reading…' : st.word}</span>
                {clickable && <LineChart className={`w-3.5 h-3.5 shrink-0 ${on ? 'text-primary' : 'text-base-content/30'}`} aria-hidden="true" />}
              </div>
              {wait
                ? <div className="mt-1.5 ml-4 h-2 w-3/5 rounded bg-base-content/10 animate-pulse" />
                : <div className="mt-0.5 ml-4 text-[11px] leading-snug text-base-content/55 text-left">{l.detail}</div>}
            </>
          );
          return (
            <li key={l.key}>
              {clickable ? (
                <button type="button" onClick={() => onToggle(l.layer!)} aria-pressed={on}
                  title={`${on ? 'Hide' : 'Show'} ${LAYER_NAME[l.layer!]} on the chart`}
                  className={`w-full rounded-lg px-2 py-1.5 border transition-colors ${on ? 'border-primary/40 bg-primary/10' : 'border-transparent hover:bg-white/[0.04]'}`}>{body}</button>
              ) : <div className="px-2 py-1.5">{body}</div>}
            </li>
          );
        })}
      </ul>
      <p className="mt-2 px-2 text-[11px] text-base-content/40 leading-snug">Trend, momentum and dealer flow are the votes behind the call. Stretch, volume and regime are context.</p>
    </div>
  );
}

export function Disagree({ items }: { items: Disagreement[] }) {
  if (!items.length) return null;
  return (
    <Callout tone="warn" icon={<Info className="w-3.5 h-3.5" />}>
      <div className="font-semibold mb-1">What disagrees</div>
      <ul className="space-y-1">
        {items.map((d, i) => (
          <li key={i} className="leading-snug"><span className="font-semibold">{d.who}.</span> <span className="opacity-90">{d.text}</span></li>
        ))}
      </ul>
    </Callout>
  );
}

// ── plan card ────────────────────────────────────────────────────────────────────────────────────────────────────
const NOMINAL_BOOK = 25000, RISK_FRAC = 0.01;     // the classic card's own flat-risk basis: 1% of a nominal $25k book

function Ladder({ plan, spot }: { plan: PlanLevels; spot: number }) {
  const H = 176, T = 8, B = H - 8, X = 74;
  const pts = [plan.t2, plan.t1, plan.entry, plan.stop, spot].filter((x): x is number => x != null);
  const lo = Math.min(...pts), hi = Math.max(...pts);
  const pad = (hi - lo) * 0.06 || 1;
  const y = (p: number) => T + ((hi + pad - p) / (hi - lo + 2 * pad)) * (B - T);
  const risk = Math.abs(plan.entry - plan.stop), rew = Math.abs(plan.t1 - plan.entry);
  const rows = [
    ...(plan.t2 != null ? [{ id: 't2', p: plan.t2, text: 'T2', col: 'text-success', pri: 5 }] : []),
    { id: 't1', p: plan.t1, text: `T1  +${rew.toFixed(2)}  (${(rew / risk).toFixed(1)}R)`, col: 'text-success', pri: 9 },
    { id: 'entry', p: plan.entry, text: 'Entry', col: 'text-warning', pri: 9 },
    { id: 'now', p: spot, text: 'Now', col: 'text-base-content', pri: 8 },
    { id: 'stop', p: plan.stop, text: `Stop  −${risk.toFixed(2)}  (1R)`, col: 'text-error', pri: 9 },
  ];
  const { placed } = layoutGutter(rows.map(r => ({ id: r.id, y: y(r.p), priority: r.pri })), T + 6, B - 6, 17);
  const at = new Map(placed.map(p => [p.id, p.at]));
  return (
    <div className="relative" style={{ height: H }} role="img" aria-label={`Price ladder: entry ${plan.entry.toFixed(2)}, stop ${plan.stop.toFixed(2)}, target ${plan.t1.toFixed(2)}, now ${spot.toFixed(2)}`}>
      <svg width="100%" height={H} className="absolute inset-0" aria-hidden="true">
        <line x1={X} x2={X} y1={y(Math.max(plan.t1, plan.entry))} y2={y(Math.min(plan.t1, plan.entry))} stroke="#10b981" strokeWidth={4} strokeLinecap="round" />
        <line x1={X} x2={X} y1={y(Math.max(plan.stop, plan.entry))} y2={y(Math.min(plan.stop, plan.entry))} stroke="#ef4444" strokeWidth={4} strokeLinecap="round" />
        <circle cx={X} cy={y(spot)} r={4.5} fill="#e2e8f0" />
      </svg>
      {rows.map(r => {
        const top = at.get(r.id);
        if (top == null) return null;
        return (
          <React.Fragment key={r.id}>
            <span className="absolute text-[11px] tabular-nums text-right text-base-content/70" style={{ left: 0, width: X - 10, top: top - 7 }}>{r.p.toFixed(2)}</span>
            <span className={`absolute text-[11px] whitespace-nowrap font-medium ${r.col}`} style={{ left: X + 12, top: top - 7 }}>{r.text}</span>
          </React.Fragment>
        );
      })}
    </div>
  );
}

function OddsBlock({ odds, ticker, window }: { odds: OddsResult | null; ticker: string; window: string | null }) {
  if (!odds) return null;
  if (!odds.ok) {
    const why = odds.reason === 'no-iv' ? `No implied-volatility reading for ${ticker}, so no odds are shown rather than a guess.`
      : odds.reason === 'no-window' ? 'No holding window is defined for this plan.' : 'The plan levels are not on the expected sides of the entry, so no odds are shown.';
    return <p className="text-[11px] text-base-content/50 leading-snug">{why}</p>;
  }
  const seg = (v: number) => Math.max(v > 0 ? 2 : 0, v * 100);
  const verdict = odds.edgePts == null ? 'Nothing resolves inside the window at this volatility.'
    : odds.edgePts >= 2 ? 'Priced in your favour for this reward:risk.'
      : odds.edgePts <= -2 ? 'Priced below what this reward:risk needs.' : 'Priced about fairly for this reward:risk.';
  const vTone: Tone = odds.edgePts == null ? 'neutral' : odds.edgePts >= 2 ? 'good' : odds.edgePts <= -2 ? 'warn' : 'neutral';
  return (
    <div>
      <SubHead hint={`${window ?? `${Math.round(odds.days)} days`} · market-implied`}>Odds</SubHead>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-base-content/10" role="img"
        aria-label={`Reaches target first ${probStr(odds.win)}, stopped out first ${probStr(odds.loss)}, unresolved ${probStr(odds.inside)}`}>
        <div className="bg-success" style={{ width: `${seg(odds.win)}%` }} />
        <div className="bg-error" style={{ width: `${seg(odds.loss)}%` }} />
        <div className="bg-base-content/25" style={{ width: `${seg(odds.inside)}%` }} />
      </div>
      <div className="mt-1.5 grid grid-cols-3 text-[11px] tabular-nums">
        <span className="text-success">T1 first {probStr(odds.win)}</span>
        <span className="text-error text-center">Stop first {probStr(odds.loss)}</span>
        <span className="text-base-content/50 text-right">Open {probStr(odds.inside)}</span>
      </div>
      {odds.fillProb != null && (
        <p className="mt-1.5 text-[11px] text-base-content/60 leading-snug">
          {odds.fillKind === 'pullback' ? 'Needs a pullback to fill:' : 'Needs a break through the entry to fill:'} <b className="tabular-nums">{probStr(odds.fillProb)}</b> chance it fills in time. The three outcomes above assume it does.
        </p>
      )}
      <p className="mt-1.5 text-[11px] leading-snug">
        <span className={TONE[vTone].text}>{verdict}</span>{' '}
        <span className="text-base-content/50">At {odds.rr.toFixed(1)}R it needs {probStr(odds.breakEven)} to break even{odds.resolvedWin != null ? `; of plans that resolve, ${probStr(odds.resolvedWin)} reach T1 first` : ''}.</span>
        <InfoTip text="These odds use the options market's own volatility, so they show whether the reward:risk is priced fairly, not whether this trade will win. A tight stop is touched often even when the stock ends higher — that is why this is lower than a win chance measured only at the end date. The volatility used is the 30–45 day implied vol, applied across the whole window." />
      </p>
    </div>
  );
}

export function PlanCard({ ticker, plan, planIdx, count, onPick, lv, spot, odds, styleLabel, window }: {
  ticker: string; plan: TradeSetup; planIdx: number; count: number; onPick: (i: number) => void;
  lv: PlanLevels | null; spot: number | null; odds: OddsResult | null; styleLabel: string; window: string | null;
}) {
  const navigate = useNavigate();
  const [tracking, setTracking] = useState(false);
  const [tracked, setTracked] = useState(false);
  const [trackErr, setTrackErr] = useState<string | null>(null);
  const [showFull, setShowFull] = useState(false);
  const long = plan.direction === 'long';
  const risk = lv ? Math.abs(lv.entry - lv.stop) : null;
  const shares = risk && risk > 0 ? Math.max(1, Math.floor((NOMINAL_BOOK * RISK_FRAC) / risk)) : null;

  const doTrack = async () => {
    setTracking(true); setTrackErr(null);
    try {
      await trackTrade({
        ticker, direction: plan.direction, instrument: 'equity', setup_type: plan.type,
        title: `${plan.type.replace(/_/g, ' ')} ${plan.direction}`,
        entry_low: plan.entry.low, entry_high: plan.entry.high, entry_level: plan.entry.level, stop_level: plan.stop.level,
        target_levels: (plan.targets || []).map(t => t.level).filter((n): n is number => n != null),
        setup_snapshot: plan as unknown as Record<string, unknown>, evaluate_now: true,
      });
      setTracked(true);
    } catch (e) { setTrackErr(e instanceof Error ? e.message : 'Could not track this trade'); }
    finally { setTracking(false); }
  };

  const fromNow = plan.from_current;
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/25 p-3 space-y-3">
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          <Chip tone={long ? 'good' : plan.direction === 'short' ? 'bad' : 'warn'}>{long ? 'Long' : plan.direction === 'short' ? 'Short' : 'Neutral'}</Chip>
          <span className="text-sm font-semibold">{TYPE_LABEL[plan.type] || plan.type.replace(/_/g, ' ')}</span>
          <span className="text-[11px] text-base-content/45">{styleLabel}{plan.horizon?.est_days ? ` · ~${plan.horizon.est_days}d to T1` : ''}</span>
        </div>
        {count > 1 && (
          <div className="mt-2 flex items-center gap-1" role="tablist" aria-label="Ranked setups">
            {Array.from({ length: count }, (_, i) => (
              <button key={i} role="tab" aria-selected={i === planIdx} onClick={() => onPick(i)}
                className={`px-2 py-0.5 rounded-md text-[11px] font-medium border ${i === planIdx ? 'border-primary/40 bg-primary/10 text-primary' : 'border-white/10 text-base-content/50 hover:text-base-content'}`}>#{i + 1}</button>
            ))}
          </div>
        )}
        <p className="mt-2 text-xs leading-snug text-base-content/75">{plan.thesis}</p>
      </div>

      {lv && spot != null ? <Ladder plan={lv} spot={spot} /> : <p className="text-[11px] text-base-content/50">This plan has no stock entry/stop/target to draw — see the full setup for its options version.</p>}

      {lv && risk != null && (
        <dl className="grid grid-cols-3 gap-2 text-center">
          {[
            ['Reward : risk', plan.risk_reward != null ? `${plan.risk_reward}R` : '—', plan.risk_reward != null && plan.risk_reward >= 2 ? 'text-success' : ''],
            ['Risk / share', money(risk), 'text-error'],
            ['Size for 1% risk', shares != null ? `${shares} sh` : '—', ''],
          ].map(([k, v, c]) => (
            <div key={k} className="rounded-lg bg-base-200/50 px-1.5 py-1.5">
              <dt className="text-[11px] text-base-content/45">{k}</dt>
              <dd className={`text-sm font-semibold tabular-nums ${c}`}>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {shares != null && <p className="-mt-1 text-[11px] text-base-content/40">Sized to risk 1% of a $25k book — scale to your own.</p>}

      <OddsBlock odds={odds} ticker={ticker} window={window} />

      {(fromNow?.to_entry_pct != null || fromNow?.to_t1_pct != null) && (
        <p className="text-[11px] text-base-content/55">From now: entry <b className="tabular-nums">{pctStr(fromNow?.to_entry_pct)}</b> · T1 <b className="tabular-nums">{pctStr(fromNow?.to_t1_pct)}</b>{plan.entry_style ? ` · ${plan.entry_style.label}` : ''}</p>
      )}
      {plan.event_risk?.warning && (
        <Callout tone="warn" icon={<CalendarClock className="w-3.5 h-3.5" />}>{plan.event_risk.warning}</Callout>
      )}
      {!!plan.what_to_watch?.length && (
        <details className="group">
          <summary className="cursor-pointer text-[11px] font-medium text-base-content/60 hover:text-base-content flex items-center gap-1"><Eye className="w-3 h-3" /> While you're in the trade</summary>
          <ul className="mt-1 list-disc pl-5 text-[11px] text-base-content/60 space-y-0.5 leading-snug">{plan.what_to_watch.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </details>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        {tracked ? (
          <button className="btn btn-xs btn-success btn-outline gap-1" onClick={() => navigate('/trade-tracking')}><Crosshair className="w-3.5 h-3.5" /> Tracking — open</button>
        ) : (
          <button className="btn btn-xs btn-primary gap-1" onClick={doTrack} disabled={tracking} title="Add this setup to the Trade Tracker to confirm entry and manage the exit">
            {tracking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Crosshair className="w-3.5 h-3.5" />} Track this trade
          </button>
        )}
        <button className="btn btn-xs btn-ghost" aria-expanded={showFull} onClick={() => setShowFull(s => !s)}>{showFull ? 'Hide' : 'Options version, verify with AI'}</button>
      </div>
      {trackErr && <p className="text-[11px] text-error">{trackErr}</p>}
      {showFull && <SetupCard setup={plan} ticker={ticker} spot={spot} dossier={{}} />}
    </div>
  );
}

export function NoPlan({ style, loading, error }: { style: string; loading: boolean; error: string | null }) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/25 p-4 text-center">
      {loading ? <div className="flex items-center justify-center gap-2 text-sm text-base-content/60"><Loader2 className="w-4 h-4 animate-spin" /> Looking for a {style} setup…</div>
        : error ? <p className="text-sm text-base-content/60">{error}</p>
          : (<>
            <Crosshair className="w-5 h-5 mx-auto text-base-content/30 mb-1.5" />
            <p className="text-sm font-semibold text-base-content/75">No clean {style} setup right now</p>
            <p className="text-xs text-base-content/50 mt-1 leading-snug">The signals don't line up into a trade with a worthwhile reward:risk. Try another style, or read the levels on the chart.</p>
          </>)}
    </div>
  );
}

// ── levels table ─────────────────────────────────────────────────────────────────────────────────────────────────
const KIND_TONE: Record<string, Tone> = { plan: 'accent', confluence: 'info', dealer: 'warn', range: 'neutral', structure: 'neutral' };

export function LevelsTable({ rows, spot }: { rows: LevelRow[]; spot: number | null }) {
  const withNow = useMemo(() => {
    if (spot == null) return rows.map(r => ({ r, now: false }));
    const out: { r: LevelRow | null; now: boolean }[] = [];
    let inserted = false;
    for (const r of rows) {
      if (!inserted && r.price <= spot) { out.push({ r: null, now: true }); inserted = true; }
      out.push({ r, now: false });
    }
    if (!inserted) out.push({ r: null, now: true });
    return out;
  }, [rows, spot]);
  if (!rows.length) return <p className="text-xs text-base-content/50 py-2">No levels to list yet.</p>;
  return (
    <table className="w-full text-xs">
      <thead className="sr-only"><tr><th>Price</th><th>From now</th><th>Level</th><th>Source</th></tr></thead>
      <tbody>
        {withNow.map((x, i) => x.now ? (
          <tr key={`now-${i}`} className="bg-base-content/[0.06]"><td className="py-1 px-2 font-semibold tabular-nums">{money(spot)}</td><td className="px-2 text-base-content/50">now</td><td className="px-2 font-semibold" colSpan={2}>Price</td></tr>
        ) : (
          <tr key={x.r!.id} className={`border-t border-white/[0.05] ${x.r!.kind === 'plan' ? 'bg-primary/[0.05]' : ''}`}>
            <td className="py-1 px-2 tabular-nums font-medium">{money(x.r!.price)}</td>
            <td className={`px-2 tabular-nums ${x.r!.distPct >= 0 ? 'text-success/80' : 'text-error/80'}`}>{pctStr(x.r!.distPct)}</td>
            <td className="px-2 text-base-content/80">{x.r!.label}</td>
            <td className="px-2 text-right"><Chip tone={KIND_TONE[x.r!.kind] || 'neutral'} className="!text-[11px]">{x.r!.kind === 'plan' ? 'your plan' : x.r!.kind}</Chip></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── indicator grid (the classic Indicators tab's numbers, no second chart) ──────────────────────────────────────
export function IndicatorGrid({ tech }: { tech: TechnicalData | null | undefined }) {
  if (!tech) return <p className="text-xs text-base-content/50">No indicator data.</p>;
  const ma = tech.movingAverages, bb = tech.bollingerBands, macd = tech.macd, ema = tech.emaCrossover;
  const cell = (k: string, v: React.ReactNode, sub?: React.ReactNode) => (
    <div key={k} className="rounded-lg bg-base-200/50 px-2.5 py-2 min-w-0"><div className="text-[11px] text-base-content/50">{k}</div><div className="text-sm font-semibold tabular-nums truncate">{v}</div>{sub != null && <div className="text-[11px] text-base-content/45 truncate">{sub}</div>}</div>
  );
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
      {cell('RSI (14)', tech.currentRSI != null ? tech.currentRSI.toFixed(1) : '—', tech.rsiSignal)}
      {cell('MACD histogram', macd?.histogram != null ? `${macd.histogram > 0 ? '+' : ''}${macd.histogram.toFixed(2)}` : '—', macd?.signal ? `${macd.signal}${macd.crossover && macd.crossover !== 'none' ? ` · ${macd.crossover.replace('_', ' ')}` : ''}` : undefined)}
      {cell('Bollinger %B', bb?.percentB != null ? `${(bb.percentB * 100).toFixed(0)}%` : '—', bb?.position?.replace('_', ' '))}
      {cell('50-day average', ma?.sma50 != null ? money(ma.sma50, 0) : '—', ma?.priceVsSma50 ? `price ${ma.priceVsSma50}` : undefined)}
      {cell('200-day average', ma?.sma200 != null ? money(ma.sma200, 0) : '—', ma?.goldenDeathCross ? ma.goldenDeathCross.replace('_', ' ') : (ma?.priceVsSma200 ? `price ${ma.priceVsSma200}` : undefined))}
      {cell('EMA 12 / 26', ema?.ema12 != null ? `${money(ema.ema12, 0)} / ${money(ema.ema26, 0)}` : '—', ema?.signal)}
      {cell('Swing support', money(tech.supportLevel))}
      {cell('Swing resistance', money(tech.resistanceLevel))}
    </div>
  );
}
