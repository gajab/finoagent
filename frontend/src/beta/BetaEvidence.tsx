/**
 * BetaEvidence — the Technical · Fundamental · News & events evidence views of Manage ▸ Underlying Analysis.
 *
 * The classic panel prints these as dense key/value dumps (a wall of 11px "RSI(14) 72.2 · MACD hist 3.56 · …"). Here each is
 * organised as: an at-a-glance header → collapsible sections that each carry a one-line summary → gauges, ladders and
 * plain-English reads instead of bare numbers. The data is exactly what the Trade Manager already returns
 * (`TradeManagerResult`); every word/tone comes from betaEvidenceModel.ts (textbook thresholds, no invented facts).
 */
import React, { useMemo, useState, type ReactNode } from 'react';
import { Check, X, Minus, AlertTriangle, ExternalLink } from 'lucide-react';
import type { TradeManagerResult, ManagerTraderLens } from '../api';
import { gloss } from '../components/trades/TradeManagerPanel';
import { Accordion, Callout, Chip, Meter, SubHead, Tile, TONE, type Tone } from './BetaKit';
import {
  num, fmtNum, fmtSigned, fmtPct, fromFrac, fmtBig, fmtCount, pos, relTime, daysUntil, pctFrom,
  rsiRead, band2080Read, adxRead, macdRead, bbRead, cmfRead, upDownRead, percentileRead, stageRead, trendTone,
  divergingBar, consensusSplit, zoneLadder, edgeSample, EDGE_TONE,
  pressureRead, PILLARS, pillarFacts, pillarTally, fmtFact, targetUpside, PERIOD_LABEL, consensusRead,
  newsRows, earningsPlacement, peerTable, TAPE_ORDER, TAPE_DIRECTIONAL, scoreRead, type Read,
  signalShares, weightedAverage, alignmentView, riskReceipt, stopMap, metricText,
} from './betaEvidenceModel';

const box = 'rounded-xl border border-white/[0.07] bg-base-100/20';
const signed = fmtSigned;
const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

// ── shared bits ──────────────────────────────────────────────────────────────

/** "Dealer put support 1,045 · Gamma flip 1,025" — the reads that agree at a level, each with a plain-English tooltip. */
export function SourceChips({ src, labels }: { src?: { label: string; price: number }[]; labels?: string[] }) {
  const rows = src && src.length ? src : (labels || []).map(l => ({ label: l, price: NaN }));
  if (!rows.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {rows.map((r, i) => (
        <span key={i} title={gloss(r.label) || r.label} className="inline-flex items-baseline gap-1 rounded-md bg-base-300/30 px-1.5 py-0.5 text-[11px] text-base-content/65 whitespace-nowrap cursor-help">
          {r.label}{Number.isFinite(r.price) && <b className="font-mono text-base-content/85">{fmtNum(r.price, 2)}</b>}
        </span>
      ))}
    </div>
  );
}

/** One reading: label · value · (optional gauge) · plain-English read. */
function Reading({ label, value, read, tone, bar, title }: { label: ReactNode; value?: ReactNode; read?: ReactNode; tone?: Tone; bar?: ReactNode; title?: string }) {
  return (
    <div className="grid grid-cols-[116px_1fr] sm:grid-cols-[150px_104px_1fr] gap-x-3 gap-y-0.5 py-2 border-b border-white/[0.05] last:border-0 items-center" title={title}>
      <div className="text-xs text-base-content/55 leading-snug">{label}</div>
      <div className={`font-mono text-[13px] font-semibold tabular-nums ${tone ? TONE[tone].text : ''}`}>{value}</div>
      <div className="col-span-2 sm:col-span-1 min-w-0">
        {bar}
        {read != null && <div className="text-xs text-base-content/60 leading-snug">{read}</div>}
      </div>
    </div>
  );
}

/** A thin track with a marker (and optional tick marks, e.g. RSI 30/70). */
function Gauge({ value, lo = 0, hi = 100, marks = [], tone = 'neutral', label }: { value: any; lo?: number; hi?: number; marks?: number[]; tone?: Tone; label?: string }) {
  const p = pos(value, lo, hi);
  if (p == null) return null;
  return (
    <div className="relative h-1.5 rounded-full bg-base-content/10 my-2" role="img" aria-label={label}>
      {marks.map(m => { const mp = pos(m, lo, hi); return mp == null ? null : <span key={m} className="absolute -top-0.5 h-2.5 w-px bg-base-content/30" style={{ left: `${mp}%` }} />; })}
      <span className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-3 w-3 rounded-full border-2 border-base-100 ${TONE[tone].dot}`} style={{ left: `${p}%` }} />
    </div>
  );
}

function ReadText({ r, extra }: { r: Read; extra?: ReactNode }) {
  return <><b className={`font-semibold ${TONE[r.tone].text}`}>{r.word}</b>{extra ? <span className="text-base-content/50"> · {extra}</span> : null}</>;
}

function DivBar({ d }: { d: number }) {
  const b = divergingBar(d);
  return (
    <div className="relative h-2 rounded-full bg-base-content/10" aria-hidden>
      <span className="absolute inset-y-0 left-1/2 w-px bg-base-content/30" />
      <span className={`absolute inset-y-0 rounded-full ${TONE[b.tone].bar} opacity-80`} style={{ left: `${b.left}%`, width: `${b.width}%` }} />
    </div>
  );
}

/** A 0–100 track with the range this lens can actually reach shaded, a tick at neutral 50 and a marker at the score. */
function ScoreScale({ score, lo, hi }: { score: any; lo: number; hi: number }) {
  const p = pos(score, 0, 100), a = pos(lo, 0, 100), b = pos(hi, 0, 100);
  if (p == null || a == null || b == null) return null;
  return (
    <div className="pt-1 pb-5" role="img" aria-label={`Score ${score} on a 0 to 100 scale; this lens can reach ${lo} to ${hi}`}>
      <div className="relative h-2 rounded-full bg-base-content/10">
        <div className="absolute inset-y-0 rounded-full bg-info/25" style={{ left: `${a}%`, width: `${b - a}%` }} />
        <span className="absolute -top-0.5 h-3 w-px bg-base-content/35" style={{ left: '50%' }} />
        <span className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-3.5 w-3.5 rounded-full border-2 border-base-100 bg-primary" style={{ left: `${p}%` }} />
        {[[a, `${fmtNum(lo, 0)} floor`], [50, '50 neutral'], [b, `${fmtNum(hi, 0)} ceiling`]].map(([at, lab]) => (
          <span key={String(lab)} className="absolute top-3 -translate-x-1/2 text-[11px] text-base-content/40 whitespace-nowrap" style={{ left: `${at}%` }}>{lab}</span>
        ))}
      </div>
    </div>
  );
}

function StackBar({ parts }: { parts: { pct: number; tone: Tone; label: string }[] }) {
  return (
    <div className="flex h-2.5 rounded-full overflow-hidden bg-base-content/10" role="img" aria-label={parts.map(p => p.label).join(', ')}>
      {parts.filter(p => p.pct > 0).map((p, i) => <div key={i} className={`${TONE[p.tone].bar} ${p.tone === 'neutral' ? 'opacity-30' : 'opacity-80'}`} style={{ width: `${p.pct}%` }} />)}
    </div>
  );
}

function Glance({ lens, label, children, showNotes = true }: { lens: any; label: string; children?: ReactNode; showNotes?: boolean }) {
  const r = lens?.available === false ? { word: 'Not scored', tone: 'neutral' as Tone } : scoreRead(lens?.score);
  return (
    <div className={`rounded-xl border p-3 ${TONE[r.tone].border} ${TONE[r.tone].soft}`}>
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="text-xs text-base-content/55">{label}</span>
        <span className={`text-base font-semibold ${TONE[r.tone].text}`}>{r.word}</span>
        {lens?.available !== false && lens?.score != null && <span className="font-mono text-xs text-base-content/50">{Math.round(lens.score)}/100</span>}
        {lens?.weight != null && lens.available !== false && <span className="text-[11px] text-base-content/40">· {Math.round(lens.weight * 100)}% of the hold score</span>}
      </div>
      {showNotes && (lens?.notes || []).length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {lens.notes.map((t: string, i: number) => <li key={i} className="text-xs text-base-content/65 leading-snug flex gap-1.5"><span className="opacity-40">•</span><span>{t}</span></li>)}
        </ul>
      )}
      {children}
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
// TECHNICAL
// ═════════════════════════════════════════════════════════════════════════════

function RuleIcon({ met }: { met: boolean | null }) {
  if (met === true) return <Check className="w-3.5 h-3.5 text-success shrink-0 mt-0.5" />;
  if (met === false) return <X className="w-3.5 h-3.5 text-error shrink-0 mt-0.5" />;
  return <Minus className="w-3.5 h-3.5 text-base-content/30 shrink-0 mt-0.5" />;
}

function TraderRow({ l }: { l: ManagerTraderLens }) {
  const tone: Tone = l.stance === 'bullish' ? 'good' : l.stance === 'bearish' ? 'bad' : 'neutral';
  return (
    <details className="group border-b border-white/[0.05] last:border-0">
      <summary className="list-none cursor-pointer select-none flex items-center gap-3 py-2 px-1 hover:bg-white/[0.03] rounded">
        <span className="text-[13px] font-medium w-40 shrink-0 truncate">{l.trader}</span>
        <div className="flex-1 min-w-0"><Meter value={l.total ? (l.met / l.total) * 100 : 0} tone={tone} /></div>
        <span className="font-mono text-xs text-base-content/55 w-10 text-right">{l.met}/{l.total}</span>
        <Chip tone={tone} className="w-[72px] justify-center">{l.stance}</Chip>
      </summary>
      <div className="pl-1 pr-2 pb-3 pt-1">
        <div className="text-xs italic text-base-content/50 leading-snug mb-2">{l.philosophy}</div>
        <ul className="space-y-1">
          {l.rules.map(r => (
            <li key={r.id} className="flex items-start gap-1.5 text-xs text-base-content/70 leading-snug">
              <RuleIcon met={r.met} /><span>{r.rule}{r.value != null && r.value !== '' && <span className="text-base-content/40 font-mono"> — {typeof r.value === 'object' ? JSON.stringify(r.value) : String(r.value)}</span>}</span>
            </li>
          ))}
        </ul>
        <div className="mt-2 pt-2 border-t border-white/[0.05] text-xs text-base-content/55">
          <span className="text-base-content/40">Their exit · </span>{l.exit_rule}{l.exit_level != null && <b className="font-mono text-base-content/80"> ({fmtNum(l.exit_level, 2)})</b>}
        </div>
      </div>
    </details>
  );
}

export function TechnicalEvidence({ res, lens, isStock }: { res: TradeManagerResult; lens: any; isStock: boolean }) {
  const tech: any = res.technical || {};
  const suite: any = tech.suite || {};
  const ctx: any = tech.structure_context || {};
  const spot = num(suite.price);
  const cons = consensusSplit(tech.consensus);
  const ladder = useMemo(() => zoneLadder(tech.zones, spot), [tech.zones, spot]);
  const bt = res.backtest;
  const sp = res.profile?.short_premium;
  const signals: any[] = lens.signals || [];
  const shares = signalShares(signals);
  const avg = weightedAverage(signals);
  const align = alignmentView(lens.build, lens.score);
  const regime = ctx.regime || {};
  const bias = ctx.bias || {};
  const ta = ctx.trend_alignment || {};
  const vol: any = tech.volume?.metrics || {};
  const rsi = rsiRead(suite.rsi14), adx = adxRead(suite.adx?.adx, suite.adx?.plus_di, suite.adx?.minus_di);
  const macd = macdRead(suite.macd?.hist, suite.macd?.hist_slope_3d), stoch = band2080Read(suite.stoch?.percent_k), mfi = band2080Read(suite.mfi14);
  const edgeSignals: any[] = tech.regime_edge?.signals || [];
  const firing = edgeSignals.filter(s => s.firing_now);
  const maChips: [string, any][] = [['21 EMA', suite.pct_vs_ema?.['21']], ['50 SMA', suite.pct_vs_sma?.['50']], ['150 SMA', suite.pct_vs_sma?.['150']], ['200 SMA', suite.pct_vs_sma?.['200']]];
  const patterns: any[] = tech.patterns || [];

  return (
    <div className="space-y-3">
      <Glance lens={lens} label="Technical lens">
        <div className="mt-2 flex flex-wrap gap-1.5">
          {regime.label && <Chip tone="info" title={regime.note}>Regime: {regime.label}{regime.hurst != null ? ` · Hurst ${fmtNum(regime.hurst, 2)}` : ''}</Chip>}
          {bias.direction && <Chip tone={bias.direction === 'bullish' ? 'good' : bias.direction === 'bearish' ? 'bad' : 'neutral'} title={bias.rationale}>Bias: {bias.direction}{bias.strength ? ` · ${bias.strength}` : ''}</Chip>}
          {(['daily', 'h4', 'h1'] as const).filter(k => ta[k]).map(k => <Chip key={k} tone={trendTone(ta[k])}>{k === 'daily' ? 'Daily' : k.toUpperCase()} {ta[k] === 'up' ? '↑' : ta[k] === 'down' ? '↓' : '→'}</Chip>)}
          {suite.weekly?.stage != null && <Chip tone={stageRead(suite.weekly.stage).tone}>{stageRead(suite.weekly.stage).word}</Chip>}
          {suite.rsi14 != null && <Chip tone={rsi.tone}>RSI {fmtNum(suite.rsi14, 0)} · {rsi.word.toLowerCase()}</Chip>}
        </div>
        {lens.scope && <div className="mt-2 text-[11px] text-base-content/45 leading-snug">{lens.scope}</div>}
      </Glance>

      {cons && (
        <div className={`${box} p-3`}>
          <div className="flex items-baseline justify-between gap-2 mb-1.5">
            <span className="text-xs font-semibold text-base-content/75">Famous-trader consensus</span>
            <span className="text-xs text-base-content/50"><b className="text-success">{tech.consensus.bull}</b> bullish · {tech.consensus.neutral} neutral · <b className="text-error">{tech.consensus.bear}</b> bearish <span className="text-base-content/35">of {cons.n}</span></span>
          </div>
          <StackBar parts={[{ pct: cons.bull, tone: 'good', label: `${tech.consensus.bull} bullish` }, { pct: cons.neutral, tone: 'neutral', label: `${tech.consensus.neutral} neutral` }, { pct: cons.bear, tone: 'bad', label: `${tech.consensus.bear} bearish` }]} />
          <div className="text-[11px] text-base-content/40 mt-1.5">Each of 15 published rule-sets is run on the chart as written — a systematic reading, not what they would actually do. Their cards are further down.</div>
        </div>
      )}

      {signals.length > 0 && (
        <Accordion title="What moves the technical score" summary={`${signals.length} signals · −1 bearish … +1 bullish`} defaultOpen>
          <div className="text-[11px] text-base-content/50 leading-snug mb-1.5">
            Each row is a <b>direction reading from −1 (bearish) to +1 (bullish)</b> — not points. They are averaged by weight, and that average is then turned into the 0–100 score below.
          </div>
          <div className="space-y-0.5">
            {signals.map((s: any, i: number) => (
              <div key={s.name} className="grid grid-cols-[1fr_96px_44px] sm:grid-cols-[1fr_140px_48px] items-center gap-x-3 py-2 border-b border-white/[0.05] last:border-0">
                <div className="min-w-0">
                  <div className="text-[13px] leading-snug">{s.name}</div>
                  <div className="text-[11px] text-base-content/45 leading-snug">{s.note}{s.w != null ? ` · weight ${s.w} (${fmtNum(shares[i], 0)}% of the vote)` : ''}</div>
                </div>
                <DivBar d={s.d} />
                <span className={`font-mono text-xs text-right ${s.d > 0.15 ? 'text-success' : s.d < -0.15 ? 'text-error' : 'text-base-content/50'}`}>{s.d > 0 ? '+' : ''}{Number(s.d).toFixed(2)}</span>
              </div>
            ))}
          </div>
          {avg != null && (
            <div className="grid grid-cols-[1fr_96px_44px] sm:grid-cols-[1fr_140px_48px] items-center gap-x-3 pt-2 mt-1 border-t border-white/[0.1]">
              <div className="text-[13px] font-semibold">Weighted average</div>
              <DivBar d={avg} />
              <span className={`font-mono text-xs text-right font-semibold ${avg > 0.15 ? 'text-success' : avg < -0.15 ? 'text-error' : 'text-base-content/60'}`}>{avg > 0 ? '+' : ''}{avg.toFixed(2)}</span>
            </div>
          )}
          {align && (
            <div className="mt-3 rounded-lg border border-white/[0.08] bg-base-100/30 px-3 py-2.5 space-y-2" data-testid="tech-build">
              <div className="flex items-baseline gap-2 flex-wrap">
                <span className="text-xs font-semibold text-base-content/70">How the {fmtNum(lens.score, 0)} is built</span>
                <span className="font-mono text-[13px] text-base-content/85">{align.line}</span>
              </div>
              <ScoreScale score={lens.score} lo={align.lo} hi={align.hi} />
              <div className="text-[11px] text-base-content/50 leading-snug">
                {align.ceilingNote}. The range is deliberately narrow — <b>±{fmtNum(align.gain, 0)} points from neutral</b> — because backtests found ~0 directional edge in chart signals, so even a very bullish chart can only lift this lens to {fmtNum(align.hi, 0)}.
                {align.adjust ? <> The {align.adjust < 0 ? '−' : '+'}{fmtNum(Math.abs(align.adjust), 1)} is the volatility / strike-distance terms listed above.</> : null}
              </div>
            </div>
          )}
          <div className="text-[11px] text-base-content/40 mt-2">Backtests found ~0 directional edge in these signals, so they carry a small weight: they supply levels and risk state more than a forecast.</div>
        </Accordion>
      )}

      <Accordion title="Trend & momentum" summary={[adx.word, suite.rsi14 != null && `RSI ${fmtNum(suite.rsi14, 0)}`, macd.word].filter(w => w && w !== '–').join(' · ') || 'indicators not available yet'} defaultOpen>
        <div>
          {(ta.daily || ta.h4 || ta.h1) && (
            <Reading label="Multi-timeframe trend"
              value={<span className="flex gap-1">{(['daily', 'h4', 'h1'] as const).filter(k => ta[k]).map(k => <span key={k} className={TONE[trendTone(ta[k])].text}>{ta[k] === 'up' ? '↑' : ta[k] === 'down' ? '↓' : '→'}</span>)}</span>}
              read={bias.rationale || 'Daily / 4-hour / 1-hour trend direction.'} />
          )}
          {maChips.some(([, v]) => num(v) != null) && (
            <Reading label="Price vs moving averages" value={num(suite.pct_vs_sma?.['50']) != null ? `${signed(suite.pct_vs_sma['50'])}%` : '–'} tone={num(suite.pct_vs_sma?.['50']) == null ? undefined : suite.pct_vs_sma['50'] >= 0 ? 'good' : 'bad'}
              read={<span className="flex flex-wrap gap-1">{maChips.filter(([, v]) => num(v) != null).map(([k, v]) => <Chip key={k} tone={Number(v) >= 0 ? 'good' : 'bad'}>{Number(v) >= 0 ? 'above' : 'below'} {k} {signed(v)}%</Chip>)}</span>}
              title="Percent distance of the price from each moving average" />
          )}
          {suite.adx?.adx != null && (
            <Reading label="Trend strength (ADX)" value={fmtNum(suite.adx.adx, 1)} tone={adx.tone} bar={<Gauge value={suite.adx.adx} lo={0} hi={60} marks={[20, 25, 40]} tone={adx.tone} label="ADX" />}
              read={<ReadText r={adx} extra={`+DI ${fmtNum(suite.adx.plus_di, 1)} · −DI ${fmtNum(suite.adx.minus_di, 1)}`} />} title="ADX above 25 = a real trend; +DI over −DI = buyers in control" />
          )}
          {suite.supertrend && (
            <Reading label="Supertrend" value={String(suite.supertrend.direction || '–').toUpperCase()} tone={trendTone(suite.supertrend.direction)}
              read={`line ${fmtNum(suite.supertrend.line, 2)}${pctFrom(spot, suite.supertrend.line) != null ? ` — price is ${fmtNum(Math.abs(pctFrom(spot, suite.supertrend.line)!), 1)}% ${pctFrom(spot, suite.supertrend.line)! >= 0 ? 'above' : 'below'} it` : ''}${suite.supertrend.flipped_recently ? ' · flipped recently' : ''}`} />
          )}
          {suite.ichimoku && (
            <Reading label="Ichimoku" value={cap(String(suite.ichimoku.price_vs_cloud || '–'))} tone={suite.ichimoku.price_vs_cloud === 'above' ? 'good' : suite.ichimoku.price_vs_cloud === 'below' ? 'bad' : 'neutral'}
              read={`cloud ${fmtNum(suite.ichimoku.cloud_bottom, 0)}–${fmtNum(suite.ichimoku.cloud_top, 0)} · tenkan/kijun cross ${suite.ichimoku.tk_bullish ? 'bullish' : 'bearish'}${suite.ichimoku.chikou_above_price != null ? ` · lagging span ${suite.ichimoku.chikou_above_price ? 'above' : 'below'} price` : ''}`} />
          )}
          {suite.weekly && (
            <Reading label="Weekly chart" value={suite.weekly.stage != null ? `Stage ${suite.weekly.stage}` : '–'} tone={stageRead(suite.weekly.stage).tone}
              read={<ReadText r={stageRead(suite.weekly.stage)} extra={`weekly RSI ${fmtNum(suite.weekly.weekly_rsi14, 0)}${suite.weekly.macd_hist_rising != null ? ` · weekly MACD ${suite.weekly.macd_hist_rising ? 'rising' : 'falling'}` : ''}${suite.weekly.price_above_sma30 != null ? ` · ${suite.weekly.price_above_sma30 ? 'above' : 'below'} 30-week average` : ''}`} />}
              title="Weinstein stages: 1 basing · 2 advancing · 3 topping · 4 declining" />
          )}
          {suite.rsi14 != null && (
            <Reading label="RSI (14)" value={fmtNum(suite.rsi14, 1)} tone={rsi.tone} bar={<Gauge value={suite.rsi14} marks={[30, 70]} tone={rsi.tone} label="RSI" />}
              read={<ReadText r={rsi} extra={[suite.rsi5 != null && `RSI(5) ${fmtNum(suite.rsi5, 0)}`, suite.rsi2 != null && `RSI(2) ${fmtNum(suite.rsi2, 0)}`].filter(Boolean).join(' · ') || undefined} />} title="Above 70 overbought, below 30 oversold" />
          )}
          {suite.macd?.hist != null && (
            <Reading label="MACD" value={signed(suite.macd.hist, 2)} tone={macd.tone}
              read={<ReadText r={macd} extra={`line ${fmtNum(suite.macd.line, 1)} vs signal ${fmtNum(suite.macd.signal_line, 1)}${num(suite.macd.hist_slope_3d) != null ? ` · histogram ${suite.macd.hist_slope_3d >= 0 ? 'rising' : 'falling'} over 3 days` : ''}`} />} />
          )}
          {suite.stoch?.percent_k != null && (
            <Reading label="Stochastic" value={fmtNum(suite.stoch.percent_k, 0)} tone={stoch.tone} bar={<Gauge value={suite.stoch.percent_k} marks={[20, 80]} tone={stoch.tone} label="Stochastic %K" />}
              read={<ReadText r={stoch} extra={`%D ${fmtNum(suite.stoch.percent_d, 0)}`} />} />
          )}
          {suite.mfi14 != null && (
            <Reading label="Money-flow index" value={fmtNum(suite.mfi14, 0)} tone={mfi.tone} bar={<Gauge value={suite.mfi14} marks={[20, 80]} tone={mfi.tone} label="MFI" />} read={<ReadText r={mfi} />} title="Volume-weighted RSI" />
          )}
          {suite.tsmom_12_1_pct != null && (
            <Reading label="12-1 month momentum" value={`${signed(suite.tsmom_12_1_pct, 0)}%`} tone={suite.tsmom_12_1_pct >= 0 ? 'good' : 'bad'}
              read={suite.roc ? `rate of change: 1m ${signed(suite.roc['21'], 0)}% · 3m ${signed(suite.roc['63'], 0)}% · 6m ${signed(suite.roc['126'], 0)}% · 12m ${signed(suite.roc['252'], 0)}%` : undefined} />
          )}
          {suite.relative_strength && (
            <Reading label="Vs the market" value={num(suite.relative_strength.excess_return_pct?.['63']) != null ? `${signed(suite.relative_strength.excess_return_pct['63'])}%` : '–'} tone={num(suite.relative_strength.excess_return_pct?.['63']) == null ? undefined : suite.relative_strength.excess_return_pct['63'] >= 0 ? 'good' : 'bad'}
              read={`excess return over the S&P 500 (63d)${suite.relative_strength.rs_line_vs_50d ? ` · RS line ${suite.relative_strength.rs_line_vs_50d} its 50-day` : ''}${suite.relative_strength.rs_line_new_high_90d ? ' · at a 90-day high' : ''}`} />
          )}
          {['rsi_divergence', 'obv_divergence', 'macd_divergence'].some(k => suite[k]) && (
            <Reading label="Divergences" value="!" tone="warn" read={['rsi_divergence', 'obv_divergence', 'macd_divergence'].filter(k => suite[k]).map(k => `${k.split('_')[0].toUpperCase()}: ${typeof suite[k] === 'string' ? suite[k] : suite[k].type || suite[k].kind || suite[k].direction || 'present'}`).join(' · ')} />
          )}
        </div>
      </Accordion>

      {(num(suite.range?.hi_252) != null || ladder.above.length + ladder.below.length > 0) && (
        <Accordion title="Price map" summary={`${ladder.below.length} support · ${ladder.above.length} resistance zones${suite.range?.pct_from_52w_high != null ? ` · ${fmtNum(Math.abs(suite.range.pct_from_52w_high), 1)}% ${suite.range.pct_from_52w_high < 0 ? 'below' : 'at'} the 52-week high` : ''}`} defaultOpen>
          {num(suite.range?.hi_252) != null && num(suite.range?.lo_252) != null && spot != null && (
            <div className="mb-3">
              <div className="flex justify-between text-[11px] text-base-content/50"><span>52-week low {fmtNum(suite.range.lo_252, 2)}</span><span>52-week high {fmtNum(suite.range.hi_252, 2)}</span></div>
              <Gauge value={spot} lo={suite.range.lo_252} hi={suite.range.hi_252} tone="info" label="Price within its 52-week range" />
              <div className="text-[11px] text-base-content/45 text-center">now {fmtNum(spot, 2)} · {fmtNum(pos(spot, suite.range.lo_252, suite.range.hi_252), 0)}% of the way up the range</div>
            </div>
          )}
          {ladder.above.length + ladder.below.length > 0 && (
            <>
              <SubHead hint="where several independent reads agree — hover a chip for what it means">Confluence zones</SubHead>
              <div className={`${box} overflow-hidden`}>
                {ladder.above.map((z, i) => <ZoneRowView key={`a${i}`} z={z} />)}
                {spot != null && <div className="flex items-center gap-2 px-3 py-1.5 bg-primary/[0.08]"><div className="h-px flex-1 bg-primary/40" /><span className="text-xs font-semibold text-primary tabular-nums">Now ${fmtNum(spot, 2)}</span><div className="h-px flex-1 bg-primary/40" /></div>}
                {ladder.below.map((z, i) => <ZoneRowView key={`b${i}`} z={z} />)}
              </div>
            </>
          )}
        </Accordion>
      )}

      <Accordion title="Volatility & expected move" summary={[suite.atr_pct != null && `ATR ${fmtNum(suite.atr_pct, 1)}% a day`, ctx.expected_move?.pct_30d != null && `±${fmtNum(ctx.expected_move.pct_30d, 1)}% over 30d`].filter(Boolean).join(' · ') || 'range, bands and dealer positioning'}>
        <div>
          {suite.atr14 != null && (
            <Reading label="Typical daily move (ATR)" value={`$${fmtNum(suite.atr14, 2)}`} read={<ReadText r={percentileRead(suite.atr_percentile_1y)} extra={`${fmtNum(suite.atr_pct, 1)}% of price${num(suite.atr_percentile_1y) != null ? ` · ${fmtNum(suite.atr_percentile_1y, 0)}th percentile of the past year` : ''}`} />} title="Average True Range (14 days) — includes gaps" />
          )}
          {suite.realized_vol && (
            <Reading label="Realised volatility" value={`${fmtNum(suite.realized_vol['21'], 0)}%`}
              read={`10d ${fmtNum(suite.realized_vol['10'], 0)}% · 21d ${fmtNum(suite.realized_vol['21'], 0)}% · 63d ${fmtNum(suite.realized_vol['63'], 0)}%${suite.vol_forecast ? ` — blended forecast ${fmtNum(suite.vol_forecast.blend_ann_pct, 0)}% (${suite.vol_forecast.regime})` : ''}`} title="Annualised" />
          )}
          {ctx.expected_move?.pct_30d != null && (
            <Reading label="Expected 30-day move" value={`±${fmtNum(ctx.expected_move.pct_30d, 1)}%`}
              read={`${fmtNum(ctx.expected_move.lower, 0)} – ${fmtNum(ctx.expected_move.upper, 0)} at implied vol ${fmtNum(ctx.expected_move.iv, 1)}%`} title="One-standard-deviation range implied by options" />
          )}
          {suite.bollinger?.pct_b != null && (
            <Reading label="Bollinger %B" value={fmtNum(suite.bollinger.pct_b, 2)} bar={<Gauge value={suite.bollinger.pct_b} lo={0} hi={1} marks={[0.2, 0.8]} label="Bollinger %B" />}
              read={<ReadText r={bbRead(suite.bollinger.pct_b)} extra={`bands ${fmtNum(suite.bollinger.lower, 0)}–${fmtNum(suite.bollinger.upper, 0)} · width ${fmtNum(suite.bollinger.bandwidth_pct, 1)}%${num(suite.bollinger.bandwidth_percentile_1y) != null ? ` (${fmtNum(suite.bollinger.bandwidth_percentile_1y, 0)}th pct)` : ''}`} />} />
          )}
          {suite.squeeze_on != null && (
            <Reading label="Volatility squeeze" value={suite.squeeze_on ? 'ON' : 'off'} tone={suite.squeeze_on ? 'warn' : undefined}
              read={suite.squeeze_on ? 'Bands inside the Keltner channel — compressed range often precedes a larger move.' : `no squeeze${suite.squeeze_momentum ? ` · momentum ${suite.squeeze_momentum.rising ? 'rising' : 'falling'}` : ''}`} />
          )}
          {suite.chandelier && (
            <Reading label="ATR trailing stop" value={fmtNum(suite.chandelier.long_stop, 2)} read={`wide ${fmtNum(suite.chandelier.long_stop_wide, 2)} — a trailing stop under a long position (chandelier exit)`} />
          )}
          {!isStock && ctx.dealer && (
            <Reading label="Dealer positioning" value={cap(String(ctx.dealer.gamma || '–'))} tone={ctx.dealer.gamma === 'long' ? 'good' : ctx.dealer.gamma === 'short' ? 'warn' : undefined}
              read={`${ctx.dealer.note || ''}${num(ctx.dealer.flip) != null ? ` Gamma flip at ${fmtNum(ctx.dealer.flip, 2)}.` : ''}`} />
          )}
        </div>
      </Accordion>

      <Accordion title="Volume & money flow" summary={[suite.rvol != null && `RVOL ${fmtNum(suite.rvol, 2)}×`, suite.cmf20 != null && cmfRead(suite.cmf20).word.toLowerCase(), vol.dryup_state].filter(Boolean).join(' · ') || 'participation behind the move'}>
        <div>
          {(vol.rvol ?? suite.rvol) != null && (
            <Reading label="Relative volume" value={`${fmtNum(vol.rvol ?? suite.rvol, 2)}×`} tone={(vol.rvol ?? suite.rvol) >= 1.5 ? 'warn' : undefined}
              read={vol.rvol_multi ? `10d ${fmtNum(vol.rvol_multi['10'], 2)}× · 20d ${fmtNum(vol.rvol_multi['20'], 2)}× · 50d ${fmtNum(vol.rvol_multi['50'], 2)}× the average${vol.rvol_basis ? ` (${vol.rvol_basis})` : ''}` : 'vs the average'} />
          )}
          {suite.up_down_volume_ratio_20d != null && (
            <Reading label="Up vs down volume" value={`${fmtNum(suite.up_down_volume_ratio_20d, 2)}`} tone={upDownRead(suite.up_down_volume_ratio_20d).tone}
              bar={vol.up_volume_pct != null ? <div className="my-1.5"><StackBar parts={[{ pct: vol.up_volume_pct, tone: 'good', label: `${vol.up_volume_pct}% up volume` }, { pct: 100 - vol.up_volume_pct, tone: 'bad', label: `${100 - vol.up_volume_pct}% down volume` }]} /></div> : undefined}
              read={<ReadText r={upDownRead(suite.up_down_volume_ratio_20d)} extra={vol.up_volume_pct != null ? `${vol.up_volume_pct}% of 20-day volume traded on up days` : undefined} />} />
          )}
          {suite.cmf20 != null && <Reading label="Chaikin money flow" value={signed(suite.cmf20, 2)} tone={cmfRead(suite.cmf20).tone} read={<ReadText r={cmfRead(suite.cmf20)} />} title="Above +0.1 accumulation, below −0.1 distribution" />}
          {suite.accumulation_days_25d != null && (
            <Reading label="Accumulation / distribution days" value={`${suite.accumulation_days_25d} / ${suite.distribution_days_25d ?? '–'}`}
              tone={suite.accumulation_days_25d > (suite.distribution_days_25d ?? 0) ? 'good' : 'warn'} read="of the last 25 sessions" />
          )}
          {(vol.obv_trend || vol.cvd_trend) && (
            <Reading label="OBV · CVD" value={String(vol.obv_trend || '–')} read={`OBV ${vol.obv_trend || '–'} · cumulative volume delta ${vol.cvd_trend || '–'} · price ${vol.price_trend || '–'}${vol.divergence ? ` — ${vol.divergence}` : ''}`} />
          )}
          {vol.dryup_state && <Reading label="Volume trend" value={cap(String(vol.volume_trend || '–'))} read={`${vol.dryup_state}${vol.dryup_ratio != null ? ` (ratio ${fmtNum(vol.dryup_ratio, 2)})` : ''}`} />}
          {(tech.volume?.climax_bars || []).length > 0 && (
            <Reading label="Climax bars" value={String(tech.volume.climax_bars.length)} read={tech.volume.climax_bars.slice(0, 3).map((c: any) => `${c.t} ${c.direction} ${fmtNum(c.ratio, 1)}× at $${fmtNum(c.price, 2)}`).join(' · ')} title="Unusually heavy-volume bars" />
          )}
          {(tech.volume?.read || []).length > 0 && (
            <ul className="pt-2 space-y-1">{tech.volume.read.map((t: string, i: number) => <li key={i} className="text-xs text-base-content/55 leading-snug flex gap-1.5"><span className="opacity-40">•</span><span>{t}</span></li>)}</ul>
          )}
        </div>
      </Accordion>

      {patterns.length > 0 && (
        <Accordion title="Chart patterns" summary={`${patterns.length} detected`}>
          <div className="space-y-2">
            {patterns.map((p: any, i: number) => (
              <div key={i} className={`${box} px-3 py-2`}>
                <div className="flex items-center gap-2 flex-wrap"><span className="text-[13px] font-semibold">{p.name}</span>
                  <Chip tone={/bull|up|long/i.test(String(p.direction)) ? 'good' : /bear|down|short/i.test(String(p.direction)) ? 'bad' : 'neutral'}>{p.direction}</Chip>
                  {p.status && <Chip>{p.status}</Chip>}{p.confidence != null && <span className="text-[11px] text-base-content/45">confidence {p.confidence}</span>}</div>
                <div className="text-xs text-base-content/60 mt-1">
                  {p.breakout?.level ? `breakout ${p.breakout.level}` : ''}{p.target ? ` · target ${typeof p.target === 'object' ? (p.target.price ?? '') : p.target}${p.target?.pct != null ? ` (${p.target.pct > 0 ? '+' : ''}${p.target.pct}%)` : ''}` : ''}{p.stop ? ` · stop ${typeof p.stop === 'object' ? (p.stop.price ?? '') : p.stop}` : ''}
                </div>
              </div>
            ))}
          </div>
        </Accordion>
      )}

      {edgeSignals.length > 0 && (
        <Accordion title="Does the setup work in this regime?" summary={firing.length ? `${firing.length} signal${firing.length === 1 ? '' : 's'} firing now` : 'historical edge of the four classic signals'}>
          <div className="space-y-3">
            {(tech.regime_edge?.read || []).length > 0 && (
              <ul className="space-y-1">{tech.regime_edge.read.map((t: string, i: number) => <li key={i} className="text-xs text-base-content/65 leading-snug flex gap-1.5"><span className="opacity-40">•</span><span>{t}</span></li>)}</ul>
            )}
            <div className={`${box} overflow-hidden`}>
              {edgeSignals.map((s: any) => {
                const cur = s.current || {}; const smp = edgeSample(cur);
                const tone = EDGE_TONE[String(s.current_edge)] || 'neutral';
                return (
                  <div key={s.key} className="px-3 py-2.5 border-b border-white/[0.05] last:border-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[13px] font-semibold">{s.label}</span>
                      {s.firing_now && <Chip tone="warn">firing now</Chip>}
                      <Chip tone={tone} title="Edge in the regime in force now">{String(s.current_edge || 'n/a')} edge here</Chip>
                      <span className="ml-auto text-[11px] font-mono text-base-content/50">{cur.win_rate != null ? `${cur.win_rate}% win · ` : ''}{cur.expectancy != null ? `${signed(cur.expectancy, 2)}R · ` : ''}n={smp.n ?? '–'}</span>
                    </div>
                    {s.thesis && <div className="text-xs text-base-content/50 leading-snug mt-0.5">{s.thesis}</div>}
                    {smp.small && <div className="text-[11px] text-warning/80 mt-0.5">Small sample — treat as context, not a rule.</div>}
                  </div>
                );
              })}
            </div>
            <div className="text-[11px] text-base-content/40">Triple-barrier backtest on this stock's own history (2R target / 1R stop, 10-bar horizon), split by the regime in force when each signal fired.</div>
          </div>
        </Accordion>
      )}

      {res.trader_lenses.length > 0 && (
        <Accordion title="The 15 trader rule-sets" summary={tech.consensus ? `${tech.consensus.bull} bullish · ${tech.consensus.neutral} neutral · ${tech.consensus.bear} bearish — tap a trader for the rules` : undefined}>
          <div className="text-[11px] text-base-content/45 mb-1.5">Does the stock still meet this trader's criteria for HOLDING a position like yours, and where would they sell? A systematic reading of their published rules.</div>
          <div>{[...res.trader_lenses].sort((a, b) => b.d - a.d).map(l => <TraderRow key={l.key} l={l} />)}</div>
        </Accordion>
      )}

      {bt && (
        <Accordion title="Backtest reality-check" summary="how much weight these signals deserve">
          <div className="space-y-2 text-xs text-base-content/65 leading-snug">
            <div><b>Universe:</b> {bt.universe}</div>
            <div><b>Direction:</b> {bt.direction.finding} <span className="text-base-content/45">{bt.direction.so}</span></div>
            <div><b>Volatility:</b> {bt.vol_ratio}</div>
            <div><b>Exits (bullish entries, 126-day max hold):</b> {bt.exit_takeaway}</div>
            <div className="overflow-x-auto"><table className="w-full"><thead><tr className="text-base-content/40 text-left"><th className="font-normal">Exit rule</th><th className="text-right font-normal">avg</th><th className="text-right font-normal">win</th><th className="text-right font-normal">worst-5%</th><th className="text-right font-normal">hold</th></tr></thead>
              <tbody>{Object.entries(bt.exit_rules).map(([k, r]) => (
                <tr key={k} className="border-t border-white/[0.05]"><td className="py-1">{r.label}</td><td className="text-right font-mono">{r.mean > 0 ? '+' : ''}{r.mean}%</td><td className="text-right font-mono">{r.win}%</td><td className="text-right font-mono">{r.p5}%</td><td className="text-right font-mono">{r.hold}d</td></tr>))}</tbody></table></div>
            {sp && bt.hold_state && <div><b>Hold vs close, open short put:</b> {bt.hold_state.takeaway}<div className="text-base-content/50 mt-0.5"><b>Close trigger:</b> {bt.hold_state.close_trigger.rule}. {bt.hold_state.close_trigger.regime_note}</div></div>}
            {sp && bt.short_put && (
              <div><b>Short-put management ({bt.short_put.universe}):</b> {bt.short_put.takeaway}
                <div className="overflow-x-auto"><table className="w-full mt-1"><thead><tr className="text-base-content/40 text-left"><th className="font-normal">Rule</th><th className="text-right font-normal">avg %</th><th className="text-right font-normal">win</th><th className="text-right font-normal">CVaR5</th><th className="text-right font-normal">worst</th><th className="text-right font-normal">mean/sd</th></tr></thead>
                  <tbody>{Object.entries(bt.short_put.rows).map(([k, r]: any) => (
                    <tr key={k} className="border-t border-white/[0.05]"><td className="py-1">{r.label}</td><td className="text-right font-mono">{r.mean}</td><td className="text-right font-mono">{r.win}%</td><td className="text-right font-mono">{r.cvar5}%</td><td className="text-right font-mono">{r.worst}%</td><td className="text-right font-mono">{r.mean_sd}</td></tr>))}</tbody></table></div>
              </div>
            )}
          </div>
        </Accordion>
      )}
    </div>
  );
}

function ZoneRowView({ z }: { z: ReturnType<typeof zoneLadder>['above'][number] }) {
  const tone: Tone = z.kind === 'support' ? 'good' : z.kind === 'resistance' ? 'bad' : 'neutral';
  return (
    <div className="grid grid-cols-[92px_1fr] gap-x-3 px-3 py-2.5 border-b border-white/[0.05] last:border-0">
      <div>
        <div className="font-mono text-sm font-semibold whitespace-nowrap">{fmtNum(z.center, 2)}</div>
        {z.distancePct != null && <div className="text-[11px] text-base-content/45">{signed(z.distancePct, 1)}% from price</div>}
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <Chip tone={tone}>{z.kind || 'zone'}</Chip>
          {z.low != null && z.high != null && <span className="text-[11px] text-base-content/45 font-mono">{fmtNum(z.low, 2)} – {fmtNum(z.high, 2)}</span>}
          <span className="text-[11px] text-base-content/45">{z.nSources} read{z.nSources === 1 ? '' : 's'} agree</span>
        </div>
        <SourceChips src={z.sources} />
      </div>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
// FUNDAMENTAL
// ═════════════════════════════════════════════════════════════════════════════

function PillarRow({ k, score, data }: { k: string; score: number | null; data: any }) {
  const spec = PILLARS[k]; const r = pressureRead(score);
  const facts = pillarFacts(k, data, 4);
  const all = Object.entries(data || {}).filter(([, v]) => v != null && v !== '' && typeof v !== 'object');
  return (
    <details className="border-b border-white/[0.05] last:border-0">
      <summary className="list-none cursor-pointer select-none py-2.5 px-1 hover:bg-white/[0.03] rounded">
        <div className="flex items-center gap-3">
          <span className="text-[13px] font-medium w-44 shrink-0 truncate" title={spec?.blurb}>{spec?.label || cap(k.replace(/_/g, ' '))}</span>
          <div className="flex-1 min-w-0"><Meter value={num(score) ?? 0} tone={r.tone} /></div>
          <span className="font-mono text-xs text-base-content/50 w-8 text-right">{num(score) != null ? Math.round(score!) : '–'}</span>
          <Chip tone={r.tone} className="w-[78px] justify-center">{r.word}</Chip>
        </div>
        {facts.length > 0 && <div className="text-[11px] text-base-content/50 mt-1 leading-snug">{facts.map(f => `${f.label} ${f.value}`).join(' · ')}</div>}
      </summary>
      <div className="px-1 pb-3 pt-1">
        {spec && <div className="text-[11px] italic text-base-content/45 mb-2">{spec.blurb}</div>}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6">
          {all.map(([kk, v]) => (
            <div key={kk} className="flex justify-between gap-3 text-xs border-b border-white/[0.04] py-1"><span className="text-base-content/50">{cap(kk.replace(/_/g, ' '))}</span><span className="font-mono text-base-content/80 text-right">{typeof v === 'number' ? fmtNum(v, Math.abs(v) >= 100 ? 0 : 2) : String(v).replace(/_/g, ' ')}</span></div>
          ))}
        </div>
      </div>
    </details>
  );
}

const TILE_COLS = 'grid grid-cols-2 sm:grid-cols-3 gap-2';

export function FundamentalEvidence({ res, lens }: { res: TradeManagerResult; lens: any }) {
  const f: any = res.fundamental || {};
  const pillars = res.pillars || {};
  const tally = pillarTally(pillars);
  const vm = f.valuation_multiples || {}, pr = f.profitability || {}, bs = f.balance_sheet || {}, dv = f.dividend || {}, sh = f.shares || {};
  const an = f.analyst || {}, est = f.estimates || {}, ins = f.insiders || {};
  const pt = an.price_targets || {};
  const upside = targetUpside(pt);
  const ranked = Object.entries(pillars).filter(([, v]) => num(v.score) != null).sort((a, b) => b[1].score! - a[1].score!);
  const periods = ['0q', '+1q', '0y', '+1y'].filter(p => (est.eps_estimate || []).some((r: any) => r.period === p));
  const byPeriod = (rows: any[] | undefined, p: string) => (rows || []).find((r: any) => r.period === p);
  const hasDiv = num(dv.yield) != null && Number(dv.yield) > 0;
  const netCash = num(bs.totalCash) != null && num(bs.totalDebt) != null ? bs.totalCash - bs.totalDebt : null;
  const surprises: any[] = (est.earnings_surprises || []).filter((s: any) => num(s.surprisePercent) != null);
  const ranges = [num(pt.low ?? an.target_low), num(pt.high ?? an.target_high), num(pt.current)].filter((x): x is number => x != null);

  if (f.is_fund) {
    return (
      <div className="space-y-3">
        <Glance lens={lens} label="Fundamental lens" />
        <Callout tone="neutral">{f.name || res.ticker} is a fund / index — company fundamentals (earnings, margins, balance sheet) do not apply, so this lens is left out of the score.</Callout>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Glance lens={lens} label="Fundamental lens">
        <div className="mt-2 flex items-baseline gap-2 flex-wrap">
          <span className="text-[13px] font-semibold">{f.name || res.ticker}</span>
          <span className="text-xs text-base-content/50">{[f.sector, f.industry].filter(Boolean).join(' · ')}</span>
          {num(f.market_cap) != null && <Chip tone="neutral">Market cap {fmtBig(f.market_cap)}</Chip>}
          {num(sh.beta) != null && <Chip tone="neutral" title="Sensitivity to the market: 1.0 moves with it">Beta {fmtNum(sh.beta, 2)}</Chip>}
        </div>
        <div className="mt-1 text-[11px] text-base-content/40">A position-risk input, not a forecast: it can lower the hold score when the company is stretched or deteriorating, and carries a small weight.</div>
      </Glance>

      {ranked.length > 0 && (
        <Accordion title="Health scorecard" summary={`${tally.healthy} healthy · ${tally.watch} watch · ${tally.pressure} pressure of ${tally.scored}`} defaultOpen>
          <div className="mb-2">
            <StackBar parts={[{ pct: (tally.healthy / tally.scored) * 100, tone: 'good', label: `${tally.healthy} healthy` }, { pct: (tally.watch / tally.scored) * 100, tone: 'warn', label: `${tally.watch} watch` }, { pct: (tally.pressure / tally.scored) * 100, tone: 'bad', label: `${tally.pressure} pressure` }]} />
            <div className="text-[11px] text-base-content/45 mt-1.5">Ten pillars, each scored as <b>exit pressure</b> — lower is healthier. Worst first; tap a row for everything behind it.</div>
          </div>
          <div>{ranked.map(([k, v]) => <PillarRow key={k} k={k} score={v.score} data={v.data} />)}</div>
        </Accordion>
      )}

      <Accordion title="Valuation & profitability" summary={[num(vm.forwardPE) != null && `fwd P/E ${fmtNum(vm.forwardPE, 1)}`, num(pr.profitMargins) != null && `net margin ${fromFrac(pr.profitMargins, 0)}`, num(pr.revenueGrowth) != null && `revenue ${fromFrac(pr.revenueGrowth, 0, true)} y/y`].filter(Boolean).join(' · ')} defaultOpen>
        <div className="space-y-3">
          <div><SubHead hint="what you pay for the earnings">Valuation</SubHead>
            <div className={TILE_COLS}>
              <Tile label="Trailing P/E" value={fmtFact(vm.trailingPE, 'x')} /><Tile label="Forward P/E" value={fmtFact(vm.forwardPE, 'x')} /><Tile label="PEG" value={fmtFact(vm.pegRatio, 'x')} title="P/E ÷ expected growth; under ~1 is cheap for its growth" />
              <Tile label="Price / sales" value={fmtFact(vm.priceToSalesTrailing12Months, 'x')} /><Tile label="Price / book" value={fmtFact(vm.priceToBook, 'x')} /><Tile label="EV / EBITDA" value={fmtFact(vm.enterpriseToEbitda, 'x')} />
            </div></div>
          <div><SubHead hint="how well the business converts sales into profit">Profitability & growth</SubHead>
            <div className={TILE_COLS}>
              <Tile label="Gross margin" value={fromFrac(pr.grossMargins)} /><Tile label="Operating margin" value={fromFrac(pr.operatingMargins)} /><Tile label="Net margin" value={fromFrac(pr.profitMargins)} />
              <Tile label="Return on equity" value={fromFrac(pr.returnOnEquity)} /><Tile label="Return on assets" value={fromFrac(pr.returnOnAssets)} />
              <Tile label="Revenue growth" value={fromFrac(pr.revenueGrowth, 1, true)} sub="year on year" tone={num(pr.revenueGrowth) == null ? undefined : pr.revenueGrowth >= 0 ? 'good' : 'bad'} />
              <Tile label="Earnings growth" value={fromFrac(pr.earningsGrowth, 0, true)} sub="year on year" tone={num(pr.earningsGrowth) == null ? undefined : pr.earningsGrowth >= 0 ? 'good' : 'bad'} />
              {num(pr.earningsQuarterlyGrowth) != null && <Tile label="Latest-quarter earnings" value={fromFrac(pr.earningsQuarterlyGrowth, 0, true)} sub="year on year" tone={pr.earningsQuarterlyGrowth >= 0 ? 'good' : 'bad'} />}
            </div></div>
          <div><SubHead hint="can it fund itself and survive a bad year">Balance sheet & cash</SubHead>
            <div className={TILE_COLS}>
              <Tile label="Cash" value={fmtBig(bs.totalCash)} /><Tile label="Debt" value={fmtBig(bs.totalDebt)} />
              {netCash != null && <Tile label={netCash >= 0 ? 'Net cash' : 'Net debt'} value={fmtBig(Math.abs(netCash))} tone={netCash >= 0 ? 'good' : 'warn'} sub="cash − debt" />}
              <Tile label="Free cash flow" value={fmtBig(bs.freeCashflow)} tone={num(bs.freeCashflow) == null ? undefined : bs.freeCashflow >= 0 ? 'good' : 'bad'} /><Tile label="Operating cash flow" value={fmtBig(bs.operatingCashflow)} />
              <Tile label="Debt / equity" value={num(bs.debtToEquity) != null ? fmtPct(bs.debtToEquity, 1) : '–'} title="Total debt as a percent of shareholder equity" />
              <Tile label="Current ratio" value={fmtFact(bs.currentRatio, 'x')} title="Short-term assets ÷ short-term liabilities; above 1 is comfortable" /><Tile label="Quick ratio" value={fmtFact(bs.quickRatio, 'x')} />
            </div></div>
          <div><SubHead hint="income and positioning">Dividend & ownership</SubHead>
            <div className={TILE_COLS}>
              <Tile label="Dividend yield" value={hasDiv ? fmtPct(dv.yield, 2) : 'None'} sub={hasDiv && num(dv.rate) != null ? `$${fmtNum(dv.rate, 2)} a share` : undefined} />
              {hasDiv && <Tile label="Payout ratio" value={fromFrac(dv.payout_ratio, 0)} tone={num(dv.payout_ratio) != null && dv.payout_ratio > 1 ? 'warn' : undefined} sub={num(dv.payout_ratio) != null && dv.payout_ratio > 1 ? 'paying out more than it earns' : undefined} />}
              {hasDiv && num(dv.five_year_avg_yield) != null && <Tile label="5-year avg yield" value={fmtPct(dv.five_year_avg_yield, 2)} />}
              <Tile label="Held by institutions" value={fromFrac(sh.held_by_institutions, 0)} /><Tile label="Held by insiders" value={fromFrac(sh.held_by_insiders, 1)} /><Tile label="Short % of float" value={fromFrac(sh.short_pct_float, 1)} />
            </div></div>
        </div>
      </Accordion>

      {periods.length > 0 && (
        <Accordion title="Estimates & earnings surprises" summary={(() => {
          const r = byPeriod(est.eps_revisions, '0q'); return r ? `this quarter: ${r.upLast30days} estimate raises vs ${r.downLast30days} cuts in 30d` : `${periods.length} periods`;
        })()}>
          <div className="space-y-3">
            <div className="overflow-x-auto"><table className="w-full text-xs">
              <thead><tr className="text-base-content/45 text-left"><th className="font-normal py-1">Period</th><th className="font-normal text-right">EPS est.</th><th className="font-normal text-right">growth</th><th className="font-normal text-right">vs 90d ago</th><th className="font-normal text-right">revenue est.</th><th className="font-normal text-right">raised / cut (30d)</th></tr></thead>
              <tbody>{periods.map(p => {
                const e = byPeriod(est.eps_estimate, p), rv = byPeriod(est.revenue_estimate, p), rev = byPeriod(est.eps_revisions, p), tr = byPeriod(est.eps_trend, p);
                const drift = tr && num(tr.current) != null && num(tr['90daysAgo']) ? (tr.current / tr['90daysAgo'] - 1) * 100 : null;
                return (
                  <tr key={p} className="border-t border-white/[0.05]">
                    <td className="py-1.5">{PERIOD_LABEL[p] || p}<span className="text-base-content/35"> · {e?.numberOfAnalysts ?? '–'} analysts</span></td>
                    <td className="text-right font-mono">{fmtNum(e?.avg, 2)}</td>
                    <td className={`text-right font-mono ${num(e?.growth) == null ? '' : e.growth >= 0 ? 'text-success' : 'text-error'}`}>{fromFrac(e?.growth, 0, true)}</td>
                    <td className={`text-right font-mono ${drift == null ? '' : drift >= 0 ? 'text-success' : 'text-error'}`}>{drift == null ? '–' : fmtPct(drift, 1, true)}</td>
                    <td className="text-right font-mono">{fmtBig(rv?.avg)}</td>
                    <td className="text-right font-mono"><span className="text-success">{rev?.upLast30days ?? '–'}</span> / <span className="text-error">{rev?.downLast30days ?? '–'}</span></td>
                  </tr>);
              })}</tbody></table></div>
            {surprises.length > 0 && (
              <div>
                <SubHead hint="reported EPS vs what analysts expected">Last {surprises.length} quarters</SubHead>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                  {surprises.map((s, i) => (
                    <Tile key={i} label={new Date(String(s.quarter).slice(0, 10) + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', year: 'numeric' })} value={fromFrac(s.surprisePercent, 1, true)}
                      tone={s.surprisePercent >= 0 ? 'good' : 'bad'} sub={`${fmtNum(s.epsActual, 2)} vs ${fmtNum(s.epsEstimate, 2)} expected`} />
                  ))}
                </div>
              </div>
            )}
          </div>
        </Accordion>
      )}

      {(Object.keys(an).length > 0 || Object.keys(ins).length > 0) && (
        <Accordion title="The Street" summary={[an.recommendation_key && cap(String(an.recommendation_key).replace(/_/g, ' ')), an.n_analysts != null && `${an.n_analysts} analysts`, upside != null && `${signed(upside, 1)}% to mean target`].filter(Boolean).join(' · ')} defaultOpen>
          <div className="space-y-3">
            {an.recommendation_key && (
              <div className="flex items-center gap-2 flex-wrap">
                <Chip tone={consensusRead(an.recommendation_mean, an.recommendation_key).tone}>{consensusRead(an.recommendation_mean, an.recommendation_key).word}</Chip>
                {num(an.recommendation_mean) != null && <span className="text-xs text-base-content/55">mean rating {fmtNum(an.recommendation_mean, 2)} on a 1 (strong buy) – 5 (strong sell) scale</span>}
              </div>
            )}
            {ranges.length >= 2 && num(pt.current) != null && (
              <div>
                <SubHead hint="the analysts' 12-month price targets">Price targets</SubHead>
                <TargetBar lo={Math.min(...ranges)} hi={Math.max(...ranges)} low={num(pt.low ?? an.target_low)} high={num(pt.high ?? an.target_high)} mean={num(pt.mean ?? an.target_mean)} median={num(pt.median ?? an.target_median)} now={num(pt.current)} />
                {upside != null && <div className="text-xs text-base-content/60 mt-1">The average target is <b className={upside >= 0 ? 'text-success' : 'text-warning'}>{signed(upside, 1)}%</b> from the current price{upside < 0 ? ' — the stock has already run past the average analyst target.' : '.'}</div>}
              </div>
            )}
            <div className={TILE_COLS}>
              {an.upgrades_90d != null && <Tile label="Upgrades · 90d" value={an.upgrades_90d} tone={an.upgrades_90d > 0 ? 'good' : undefined} />}
              {an.downgrades_90d != null && <Tile label="Downgrades · 90d" value={an.downgrades_90d} tone={an.downgrades_90d > 0 ? 'bad' : undefined} />}
              {an.reiterated_90d != null && <Tile label="Reiterated · 90d" value={an.reiterated_90d} />}
              {ins.recent_buys != null && <Tile label="Insider buys / sells" value={`${ins.recent_buys} / ${ins.recent_sells ?? 0}`} sub={num(ins.shares_sold) ? `${fmtCount(ins.shares_sold)} shares sold${num(ins.shares_bought) ? ` · ${fmtCount(ins.shares_bought)} bought` : ''}` : undefined} title="Recent insider transactions reported to the SEC (Form 4). Selling is routine for stock-compensated executives; a cluster of buys is the rarer signal." />}
            </div>
            {(an.rating_changes_90d || []).length > 0 && (
              <div>
                <SubHead hint="latest first">Recent analyst actions</SubHead>
                <div className={`${box} overflow-hidden`}>
                  {an.rating_changes_90d.slice(0, 8).map((r: any, i: number) => {
                    const act = String(r.action || '').toLowerCase();
                    const tone: Tone = act === 'up' ? 'good' : act === 'down' ? 'bad' : 'neutral';
                    const lbl = act === 'up' ? 'Upgrade' : act === 'down' ? 'Downgrade' : act === 'init' ? 'Initiated' : act === 'reit' ? 'Reiterated' : 'Maintained';
                    return (
                      <div key={i} className="grid grid-cols-[64px_1fr] sm:grid-cols-[64px_1.2fr_1fr_auto] gap-x-3 gap-y-0.5 px-3 py-2 border-b border-white/[0.05] last:border-0 text-xs items-center">
                        <span className="text-base-content/45 font-mono">{r.date ? new Date(r.date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''}</span>
                        <span className="font-medium truncate">{r.firm}</span>
                        <span className="text-base-content/60">{r.from && r.from !== r.to ? `${r.from} → ` : ''}{r.to}</span>
                        <span className="flex items-center gap-1.5 col-span-2 sm:col-span-1">
                          <Chip tone={tone}>{lbl}</Chip>
                          {num(r.price_target) != null && <span className="font-mono text-base-content/70">PT ${fmtNum(r.price_target, 0)}{r.price_target_action ? <span className={`ml-1 ${/rais/i.test(r.price_target_action) ? 'text-success' : /low/i.test(r.price_target_action) ? 'text-error' : 'text-base-content/40'}`}>{/rais/i.test(r.price_target_action) ? '▲' : /low/i.test(r.price_target_action) ? '▼' : ''}</span> : null}</span>}
                        </span>
                      </div>);
                  })}
                </div>
              </div>
            )}
          </div>
        </Accordion>
      )}
    </div>
  );
}

function TargetBar({ lo, hi, low, high, mean, median, now }: { lo: number; hi: number; low: number | null; high: number | null; mean: number | null; median: number | null; now: number | null }) {
  const p = (v: number | null) => pos(v, lo, hi);
  const marks: { v: number | null; label: string; cls: string }[] = [
    { v: mean, label: 'mean', cls: 'bg-info' }, { v: median, label: 'median', cls: 'bg-base-content/50' }, { v: now, label: 'now', cls: 'bg-primary' },
  ];
  return (
    <div className="pt-5 pb-1">
      <div className="relative h-2 rounded-full bg-base-content/10">
        {p(low) != null && p(high) != null && <div className="absolute inset-y-0 rounded-full bg-info/25" style={{ left: `${p(low)}%`, width: `${p(high)! - p(low)!}%` }} />}
        {marks.map(m => p(m.v) != null && (
          <div key={m.label} className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2" style={{ left: `${p(m.v)}%` }}>
            <span className={`block h-4 w-1 rounded ${m.cls}`} />
            {m.label !== 'median' && <span className="absolute -top-6 left-1/2 -translate-x-1/2 text-[11px] whitespace-nowrap text-base-content/60">{m.label} <b className="font-mono text-base-content/85">{fmtNum(m.v, 0)}</b></span>}
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[11px] text-base-content/45 mt-1.5"><span>low {fmtNum(low, 0)}</span><span>high {fmtNum(high, 0)}</span></div>
    </div>
  );
}

// ═════════════════════════════════════════════════════════════════════════════
// NEWS & EVENTS
// ═════════════════════════════════════════════════════════════════════════════

type FeedKey = 'company' | 'industry' | 'macro' | 'peers';

export function EventsEvidence({ res, lens, isStock }: { res: TradeManagerResult; lens: any; isStock: boolean }) {
  const ev: any = res.events || {};
  const mk: any = res.market || {};
  const flags = ev.headline_flags || null;
  const feeds = useMemo(() => ({
    company: newsRows(ev.news, flags),
    industry: newsRows(ev.industry_news),
    macro: newsRows(ev.macro_geopolitical_news),
    peers: newsRows(mk.peer_news),
  }), [ev.news, ev.industry_news, ev.macro_geopolitical_news, mk.peer_news, flags]);
  const [feed, setFeed] = useState<FeedKey>('company');
  const [all, setAll] = useState(false);
  const rows = feeds[feed]; const shown = all ? rows : rows.slice(0, 7);
  const dte = !isStock ? num(res.profile?.dte) : null;
  const place = earningsPlacement(ev.days_to_earnings, dte);
  const paysDiv = (num(res.fundamental?.dividend?.yield) ?? 0) > 0;
  const peers = peerTable(mk.peers, res.ticker);
  const tape = mk.tape || {};
  const fred = mk.fred || {};
  const filings: any[] = ev.filings || [];
  const nTotal = num(flags?.n_total) ?? 0, nRisk = num(flags?.n_risk) ?? 0, nPos = num(flags?.n_positive) ?? 0;
  const clear = !!lens.clear;
  const tabs: [FeedKey, string][] = [['company', 'Company'], ['industry', 'Industry'], ['macro', 'Macro & geopolitics'], ['peers', 'Peers']];
  const dateTxt = (iso: string) => new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });

  return (
    <div className="space-y-3">
      <div className={`rounded-xl border p-3 ${clear ? TONE.good.border + ' ' + TONE.good.soft : (lens.adjustment ?? 0) < -6 ? TONE.bad.border + ' ' + TONE.bad.soft : TONE.warn.border + ' ' + TONE.warn.soft}`}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="text-xs text-base-content/55">Event risk</span>
          <span className={`text-base font-semibold ${clear ? 'text-success' : (lens.adjustment ?? 0) < -6 ? 'text-error' : 'text-warning'}`}>{clear ? 'Clear' : `${lens.adjustment} pts on the score`}</span>
        </div>
        <ul className="mt-1.5 space-y-0.5">
          {(lens.notes?.length ? lens.notes : ['No earnings, ex-dividend, volatility spike or risky headline inside the trade window — nothing is subtracted from the score.']).map((t: string, i: number) =>
            <li key={i} className="text-xs text-base-content/65 leading-snug flex gap-1.5"><span className="opacity-40">•</span><span>{t}</span></li>)}
        </ul>
      </div>

      <Accordion title="Calendar & filings" summary={[ev.earnings_date && `earnings ${dateTxt(ev.earnings_date)}${ev.days_to_earnings != null ? ` (${ev.days_to_earnings}d)` : ''}`, filings.length > 0 && `${filings.length} recent filing${filings.length === 1 ? '' : 's'}`].filter(Boolean).join(' · ') || 'nothing scheduled'} defaultOpen>
        <div className="space-y-3">
          <div className={TILE_COLS}>
            <Tile label="Next earnings" value={ev.earnings_date ? dateTxt(ev.earnings_date) : '—'} sub={ev.days_to_earnings != null ? `in ${ev.days_to_earnings} day${ev.days_to_earnings === 1 ? '' : 's'}` : undefined} tone={place.when === 'inside' ? 'warn' : undefined} />
            <Tile label="Next ex-dividend" value={ev.ex_dividend_date ? dateTxt(ev.ex_dividend_date) : '—'} sub={ev.ex_dividend_date && daysUntil(ev.ex_dividend_date) != null ? `in ${daysUntil(ev.ex_dividend_date)} days` : undefined} />
            <Tile label="Last ex-dividend" value={paysDiv && ev.last_ex_dividend_date ? dateTxt(ev.last_ex_dividend_date) : '—'} sub={paysDiv ? undefined : 'no dividend'} />
          </div>
          {ev.earnings_date && <Callout tone={place.tone === 'warn' ? 'warn' : place.tone === 'good' ? 'good' : 'neutral'} icon={place.when === 'inside' ? <AlertTriangle className="w-3.5 h-3.5" /> : undefined}>{place.text}.</Callout>}
          {filings.length > 0 && (
            <div>
              <SubHead hint="SEC filings — an 8-K is a fresh corporate event worth reading">Recent filings</SubHead>
              <div className={`${box} overflow-hidden`}>
                {filings.map((x, i) => {
                  const d = daysUntil(x.date);
                  return (
                    <div key={i} className="flex items-center gap-3 px-3 py-2 border-b border-white/[0.05] last:border-0 text-xs">
                      <Chip tone={/^8-K|6-K/.test(String(x.form)) ? 'warn' : 'neutral'}>{x.form}</Chip>
                      <span className="text-base-content/60">{x.date}{d != null ? ` · ${d === 0 ? 'today' : `${Math.abs(d)}d ago`}` : ''}</span>
                      {x.url && /^https?:\/\//.test(String(x.url)) && <a className="link ml-auto inline-flex items-center gap-1" href={x.url} target="_blank" rel="noreferrer">Read on EDGAR <ExternalLink className="w-3 h-3" /></a>}
                    </div>);
                })}
              </div>
            </div>
          )}
        </div>
      </Accordion>

      <Accordion title="News & headlines" summary={nTotal ? `${nTotal} headlines scanned · ${nRisk} risk · ${nPos} positive` : 'no headlines returned'} defaultOpen>
        <div className="space-y-3">
          {nTotal > 0 && (
            <div>
              <StackBar parts={[{ pct: (nRisk / nTotal) * 100, tone: 'bad', label: `${nRisk} risk` }, { pct: ((nTotal - nRisk - nPos) / nTotal) * 100, tone: 'neutral', label: `${nTotal - nRisk - nPos} neutral` }, { pct: (nPos / nTotal) * 100, tone: 'good', label: `${nPos} positive` }]} />
              <div className="text-[11px] text-base-content/40 mt-1.5">{flags?.note || 'A keyword scan of headlines — a prompt for the reader, not a sentiment model.'}</div>
            </div>
          )}
          {(flags?.risk_headlines || []).map((h: string, i: number) => (
            <Callout key={`r${i}`} tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}>{h}</Callout>
          ))}
          <div className="flex gap-1.5 flex-wrap">
            {tabs.map(([k, lab]) => (
              <button key={k} onClick={() => { setFeed(k); setAll(false); }}
                className={`px-3 py-1 rounded-full text-xs border transition-colors ${feed === k ? 'border-primary/50 bg-primary/15 text-primary font-semibold' : 'border-white/10 text-base-content/60 hover:border-white/25'}`}>{lab} <span className="opacity-60">{feeds[k].length}</span></button>
            ))}
          </div>
          {rows.length === 0 ? <div className="text-xs text-base-content/40 py-2">No headlines returned by the data source.</div> : (
            <div className={`${box} overflow-hidden`}>
              {shown.map((r, i) => (
                <div key={i} className="px-3 py-2.5 border-b border-white/[0.05] last:border-0">
                  <div className="flex items-start gap-2">
                    {r.flag && <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${r.flag === 'risk' ? TONE.bad.dot : TONE.good.dot}`} title={r.flag === 'risk' ? 'flagged by the risk-keyword scan' : 'flagged positive by the keyword scan'} />}
                    <div className="min-w-0">
                      {r.link ? <a href={r.link} target="_blank" rel="noreferrer" className="text-[13px] leading-snug hover:text-primary transition-colors">{r.title}</a> : <span className="text-[13px] leading-snug">{r.title}</span>}
                      <div className="text-[11px] text-base-content/45 mt-0.5">{[r.tag && `[${r.tag}]`, r.publisher, r.when].filter(Boolean).join(' · ')}</div>
                    </div>
                  </div>
                </div>
              ))}
              {rows.length > shown.length && <button className="w-full text-xs text-secondary/80 hover:text-secondary py-2" onClick={() => setAll(true)}>Show all {rows.length}</button>}
            </div>
          )}
        </div>
      </Accordion>

      {(peers || Object.keys(tape).length > 0 || Object.keys(fred).length > 0) && (
        <Accordion title="Peers & market backdrop" summary={[peers && num(mk.peers?.self_vs_peers_63d_pct) != null && `${signed(mk.peers.self_vs_peers_63d_pct, 1)}% vs peers (63d)`, tape.SPY && `S&P ${signed(tape.SPY.change_pct_5d, 1)}% (5d)`, fred.VIX != null && `VIX ${fmtNum(fred.VIX, 1)}`].filter(Boolean).join(' · ')}>
          <div className="space-y-3">
            {peers && (
              <div>
                <SubHead hint="total return">Versus peers</SubHead>
                <div className={`${box} overflow-hidden text-xs`}>
                  <div className="grid grid-cols-[1fr_72px_72px] px-3 py-1.5 text-base-content/45 border-b border-white/[0.05]"><span>Ticker</span><span className="text-right">21 days</span><span className="text-right">63 days</span></div>
                  {peers.rows.map(r => (
                    <div key={r.ticker} className={`grid grid-cols-[1fr_72px_72px] px-3 py-1.5 border-b border-white/[0.05] ${r.self ? 'bg-primary/[0.07] font-semibold' : ''}`}>
                      <span>{r.ticker}{r.self && <span className="text-base-content/40 font-normal"> (this stock)</span>}</span>
                      <span className={`text-right font-mono ${r.r21 == null ? '' : r.r21 >= 0 ? 'text-success' : 'text-error'}`}>{r.r21 == null ? '–' : `${signed(r.r21, 1)}%`}</span>
                      <span className={`text-right font-mono ${r.r63 == null ? '' : r.r63 >= 0 ? 'text-success' : 'text-error'}`}>{r.r63 == null ? '–' : `${signed(r.r63, 1)}%`}</span>
                    </div>
                  ))}
                  <div className="grid grid-cols-[1fr_72px_72px] px-3 py-1.5 text-base-content/60">
                    <span>Peer median</span><span className="text-right font-mono">{peers.median.r21 == null ? '–' : `${signed(peers.median.r21, 1)}%`}</span><span className="text-right font-mono">{peers.median.r63 == null ? '–' : `${signed(peers.median.r63, 1)}%`}</span>
                  </div>
                </div>
              </div>
            )}
            {Object.keys(tape).length > 0 && (
              <div>
                <SubHead hint="last price and 5-day change">Market tape</SubHead>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {TAPE_ORDER.filter(k => tape[k]).map(k => {
                    const v = tape[k]; const up = num(v.change_pct_5d);
                    const tone: Tone | undefined = up == null || !TAPE_DIRECTIONAL.has(k) ? undefined : up >= 0 ? 'good' : 'bad';
                    return <Tile key={k} label={`${k} · ${v.label}`} value={fmtNum(v.last, 2)} sub={up == null ? undefined : `${up >= 0 ? '▲' : '▼'} ${fmtNum(Math.abs(up), 1)}% over 5 days`} tone={tone} />;
                  })}
                </div>
              </div>
            )}
            {Object.keys(fred).length > 0 && (
              <div>
                <SubHead hint="FRED">Rates & macro</SubHead>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">{Object.entries(fred).map(([k, v]) => <Tile key={k} label={k} value={fmtNum(v, 2)} />)}</div>
              </div>
            )}
          </div>
        </Accordion>
      )}
    </div>
  );
}


// ═════════════════════════════════════════════════════════════════════════════
// POSITION RISK (shares: the "quant" lens)
// ═════════════════════════════════════════════════════════════════════════════

const TILE_TONE: Record<string, Tone | undefined> = { good: 'good', warn: 'warn', bad: 'bad' };

export function PositionRiskEvidence({ res, lens }: { res: TradeManagerResult; lens: any }) {
  const detail: any = lens.detail || {};
  const groups: Record<string, any[]> = detail.groups || {};
  const receipt = riskReceipt(lens.build, lens.score);
  const suite: any = (res.technical || {}).suite || {};
  const spot = num(res.profile?.spot) ?? num(suite.price);
  const map = lens.build?.kind === 'position_risk' ? stopMap(lens.build.stop, spot, suite.range?.hi_252) : null;
  const stop = lens.build?.stop;
  const rows = (g: string) => (groups[g] || []).filter(m => m.value != null);
  const risk = rows('Risk'), edge = rows('Edge & odds');
  const pTouch = num(stop?.p_touch);

  return (
    <div className="space-y-3">
      <Glance lens={lens} label="Position risk" showNotes={!receipt} />

      {stop && (
        <div className={`rounded-xl border p-3 ${stop.crossed ? TONE.bad.border + ' ' + TONE.bad.soft : (pTouch ?? 0) >= 0.35 ? TONE.warn.border + ' ' + TONE.warn.soft : 'border-white/[0.08] bg-base-100/20'}`} data-testid="stop-card">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-xs font-semibold text-base-content/70">Your −{fmtNum(stop.pct, 0)}% discipline stop</span>
            {stop.crossed
              ? <Chip tone="bad">price is already through it</Chip>
              : <><span className={`text-xl font-semibold tabular-nums ${(pTouch ?? 0) >= 0.35 ? 'text-warning' : (pTouch ?? 0) >= 0.2 ? 'text-base-content/80' : 'text-success'}`}>{fmtNum((pTouch ?? 0) * 100, 0)}%</span>
                <span className="text-xs text-base-content/55">chance of touching it within a month · {fmtNum(stop.z, 1)}σ away</span></>}
          </div>
          {map && (
            <div className="mt-3">
              <div className="relative h-3 rounded-full bg-base-content/10" role="img" aria-label="Stop, cost and price on one axis with the one-standard-deviation month shaded">
                {map.band && <div className="absolute inset-y-0 rounded-full bg-warning/20" style={{ left: `${Math.max(0, map.band.loPct)}%`, width: `${Math.min(100, map.band.hiPct) - Math.max(0, map.band.loPct)}%` }} />}
                {map.marks.map(m => (
                  <span key={m.key} title={`${m.label} ${fmtNum(m.price, 2)}`} className={`absolute top-1/2 -translate-y-1/2 -translate-x-1/2 ${m.key === 'spot' ? 'h-5 w-1.5' : 'h-4 w-1'} rounded ${TONE[m.tone].bar}`} style={{ left: `${m.pct}%` }} />
                ))}
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2.5">
                {[...map.marks].sort((a, b) => b.price - a.price).map(m => (
                  <div key={m.key} className="text-xs"><span className={`inline-block h-2 w-2 rounded-sm mr-1.5 ${TONE[m.tone].bar}`} /><span className="text-base-content/55">{m.label}</span> <b className="font-mono text-base-content/85">${fmtNum(m.price, 2)}</b>
                    {m.fromSpot != null && m.key !== 'spot' && <span className="text-base-content/40"> ({signed(m.fromSpot, 1)}%)</span>}</div>
                ))}
              </div>
              {map.band && <div className="text-[11px] text-base-content/45 mt-1.5"><span className="inline-block h-2 w-3 rounded-sm mr-1.5 bg-warning/30" />A normal month (1σ) is ${fmtNum(map.band.lo, 0)} – ${fmtNum(map.band.hi, 0)}{!stop.crossed && map.band.lo < stop.level ? ' — the stop sits inside that range, which is why the touch odds are high.' : '.'}</div>}
            </div>
          )}
          <div className="text-[11px] text-base-content/40 mt-2">Touch odds are the reflection-principle probability on the blended realised volatility (the higher of implied and realised when options exist). The stop is measured from your cost, not from the price.</div>
        </div>
      )}

      {receipt && (
        <Accordion title={`How the ${fmtNum(lens.score, 0)} is built`} summary={`${fmtNum(receipt.base, 0)} base ${receipt.terms.map(t => `${t.pts < 0 ? '−' : '+'}${fmtNum(Math.abs(t.pts), 0)}`).join(' ')} = ${fmtNum(lens.score, 0)}`} defaultOpen>
          <div data-testid="risk-receipt">
            <div className="flex items-center gap-3 py-2 border-b border-white/[0.05]"><span className="text-[13px] flex-1">Starting point — a share position with nothing unusual</span><span className="font-mono text-sm w-12 text-right">{fmtNum(receipt.base, 0)}</span></div>
            {[...receipt.terms].sort((a, b) => a.pts - b.pts).map(t => (
              <div key={t.label} className="grid grid-cols-[1fr_48px] gap-x-3 py-2 border-b border-white/[0.05]">
                <div className="min-w-0"><div className="text-[13px] font-medium">{t.label}</div><div className="text-xs text-base-content/55 leading-snug">{t.note}</div></div>
                <span className={`font-mono text-sm text-right self-center ${t.pts < 0 ? 'text-error' : t.pts > 0 ? 'text-success' : 'text-base-content/50'}`}>{t.pts > 0 ? '+' : t.pts < 0 ? '−' : ''}{fmtNum(Math.abs(t.pts), 0)}</span>
              </div>
            ))}
            {receipt.terms.length === 0 && <div className="py-2 text-xs text-base-content/55">No elevated risk signal — volatility, drawdown and stop distance are all normal, so nothing is added or taken away.</div>}
            <div className="flex items-center gap-3 pt-2"><span className="text-[13px] font-semibold flex-1">Position-risk score</span><span className="font-mono text-base font-semibold w-12 text-right">{fmtNum(lens.score, 0)}</span></div>
          </div>
          <div className="text-[11px] text-base-content/40 mt-2">Each term is a fixed step set from the backtest, so the score moves in whole points.</div>
        </Accordion>
      )}

      {(risk.length > 0 || edge.length > 0) && (
        <Accordion title="The numbers behind it" summary={[risk.find(m => m.key === 'vol_ann') && `volatility ${metricText(risk.find(m => m.key === 'vol_ann'))}`, risk.find(m => m.key === 'beta') && `beta ${metricText(risk.find(m => m.key === 'beta'))}`].filter(Boolean).join(' · ') || 'volatility, drawdown, beta, momentum'} defaultOpen>
          <div className="space-y-3">
            {risk.length > 0 && <div><SubHead hint="how big a normal move is, and how far the price is from trouble">Risk</SubHead>
              <div className={TILE_COLS}>{risk.map(m => <Tile key={m.key} label={m.label} value={metricText(m)} tone={TILE_TONE[m.tone || '']} sub={m.note} title={m.note} />)}</div></div>}
            {edge.length > 0 && <div><SubHead hint="context, not a forecast">Momentum</SubHead>
              <div className={TILE_COLS}>{edge.map(m => <Tile key={m.key} label={m.label} value={metricText(m)} tone={TILE_TONE[m.tone || '']} sub={m.note} title={m.note} />)}</div></div>}
          </div>
        </Accordion>
      )}

      {detail.how_it_counts && <div className="text-[11px] text-base-content/45 leading-snug px-1">{detail.how_it_counts}</div>}
    </div>
  );
}
