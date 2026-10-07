/**
 * BetaStrategies — the Beta replacement for the classic "Every setup and named strategy" block.
 *
 * Classic: three setup-style pills, a dropdown with three named strategies, and a stack of tall cards.
 * Beta: ONE board. Every way of reading the chart is a tile — the engine's own setups (Swing · Position · Day), the three
 * named screens (Momentum Swing · Qullamäggie · Connors RSI-2) and the famous-trader rule-sets from the Trade Manager's
 * "Famous-trader consensus" (13 more) — each with its state visible BEFORE you open it. Pick a tile to read its rules, its
 * exit and its levels, and draw those levels on the cockpit chart.
 *
 * The trader rule-sets run in the backend on the ticker's daily history (trader_strategy_service → the same trader_lenses the
 * Trade Manager uses). They are framed as a discipline / structure read: the stored backtest found no directional edge in
 * them, and the board says so.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Check, X, Minus, LineChart, Loader2, RefreshCw, Info, ArrowUp, ArrowDown, CircleDot } from 'lucide-react';
import { fetchTraderStrategies, fetchDayTradeSetups } from '../../api';
import type { TradeSetup, TradeSetupsData, TraderStrategy, TraderStrategiesData } from '../../types';
import { NAMED_STRATEGIES, StrategyPanel, type NamedStrategy } from '../../components/TradeSetupCards';
import { SetupCard } from '../../components/TradeSetupCards';
import { TONE, Chip, Callout, Meter, SubHead, type Tone } from '../BetaKit';
import { money, pctStr, setupsForStyle } from './betaTaModel';
import type { ChartLevel } from './betaTaChartModel';
import {
  engineTiles, namedTiles, traderTiles, filterTraders, filterCounts, consensusView, stopRead, rulesLine, fmtRuleValue, ruleState, levelDistancePct,
  strategyChartLevels, FAMILY_ORDER, FAMILY_LABEL, GRADE_WORD, NO_FILTER, type Tile, type Filter, type Stance,
} from './betaStrategyModel';

export interface StrategyDraw { key: string; label: string; levels: ChartLevel[] }
interface Props {
  ticker: string;
  setups: TradeSetupsData | null;
  spot: number | null;
  drawnKey: string | null;
  onDraw: (d: StrategyDraw | null) => void;
}
interface StratState { data?: any; loading: boolean; err?: string; tried: boolean }

const STANCE_TONE: Record<Stance, Tone> = { bullish: 'good', neutral: 'neutral', bearish: 'bad' };
const STANCE_WORD: Record<Stance, string> = { bullish: 'Bullish', neutral: 'Neutral', bearish: 'Bearish' };
const StanceIcon = ({ s }: { s: Stance }) => (s === 'bullish' ? <ArrowUp className="w-3.5 h-3.5" aria-hidden="true" /> : s === 'bearish' ? <ArrowDown className="w-3.5 h-3.5" aria-hidden="true" /> : <CircleDot className="w-3 h-3" aria-hidden="true" />);

// ── a tile ───────────────────────────────────────────────────────────────────────────────────────────────────────
function TileButton({ t, selected, drawn, badge, onPick }: { t: Tile; selected: boolean; drawn: boolean; badge?: string | null; onPick: (id: string) => void }) {
  const tone = t.stance ? STANCE_TONE[t.stance] : 'neutral';
  return (
    <button type="button" onClick={() => onPick(t.id)} aria-pressed={selected} data-tile={t.id} title={t.hint}
      className={`group relative text-left rounded-lg border px-2.5 py-2 min-w-0 transition-colors ${selected ? 'border-primary/50 bg-primary/10' : 'border-white/[0.08] bg-base-200/30 hover:bg-white/[0.05] hover:border-white/20'} ${t.available ? '' : 'opacity-60'}`}>
      <div className="flex items-center gap-1.5">
        {t.kind === 'trader' && t.stance && <span className={TONE[tone].text}><StanceIcon s={t.stance} /></span>}
        <span className="text-[13px] font-medium truncate flex-1">{t.label}</span>
        {drawn && <LineChart className="w-3.5 h-3.5 text-primary shrink-0" aria-label="drawn on the chart" />}
      </div>
      {t.kind === 'trader' ? (
        t.available && t.total ? (
          <div className="mt-1.5">
            <Meter value={(t.met! / t.total) * 100} tone={tone} />
            <div className="mt-1 flex justify-between text-[11px] text-base-content/50 tabular-nums"><span>{t.met} of {t.total} rules</span><span className={TONE[tone].text}>{t.stance ? STANCE_WORD[t.stance] : ''}</span></div>
          </div>
        ) : <div className="mt-1.5 text-[11px] text-base-content/45">{t.available ? 'No rules scored' : 'Not scored — needs more history'}</div>
      ) : (
        <div className="mt-1.5 text-[11px] text-base-content/50 truncate">
          {t.kind === 'engine' ? (t.count == null ? 'tap to load' : t.count === 0 ? 'no setup right now' : `${t.count} setup${t.count === 1 ? '' : 's'}`) : (badge || 'tap to run')}
        </div>
      )}
    </button>
  );
}

// ── trader detail ────────────────────────────────────────────────────────────────────────────────────────────────
function RuleIcon({ s }: { s: ReturnType<typeof ruleState> }) {
  return s === 'met' ? <Check className="w-4 h-4 text-success" aria-label="met" /> : s === 'unmet' ? <X className="w-4 h-4 text-error" aria-label="not met" /> : <Minus className="w-4 h-4 text-base-content/30" aria-label="not scored" />;
}

function TraderDetail({ s, spot, data, drawn, onToggleDraw }: { s: TraderStrategy; spot: number | null; data: TraderStrategiesData; drawn: boolean; onToggleDraw: (s: TraderStrategy) => void }) {
  const stance = (s.stance === 'bullish' || s.stance === 'bearish' ? s.stance : 'neutral') as Stance;
  const isLevel = s.exit_is_level !== false;
  const stop = stopRead(s.stop, s.exit_rule, isLevel);
  const bt = s.exit_backtest;
  if (!s.available) {
    return (
      <div className="rounded-xl border border-white/[0.08] bg-base-200/25 p-4" data-testid="trader-detail">
        <div className="flex items-center gap-2"><span className="text-sm font-semibold">{s.trader}</span><Chip>Not scored</Chip></div>
        <p className="mt-2 text-xs text-base-content/60 leading-snug">{s.reason}</p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/25 p-4 space-y-4" data-testid="trader-detail">
      <div className="flex items-start gap-3 flex-wrap">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-semibold">{s.full_name || s.trader}</span>
            <Chip tone={STANCE_TONE[stance]}>{STANCE_WORD[stance]}</Chip>
            <Chip>{GRADE_WORD[s.grade] || s.grade} · {rulesLine(s)}</Chip>
          </div>
          <p className="mt-1.5 text-xs italic text-base-content/55 leading-snug">{s.philosophy}</p>
        </div>
        {s.levels.length > 0 && (
          <button type="button" onClick={() => onToggleDraw(s)} aria-pressed={drawn}
            className={`btn btn-sm gap-1.5 ${drawn ? 'btn-primary' : 'btn-outline'}`}>
            <LineChart className="w-3.5 h-3.5" /> {drawn ? 'Drawn on the chart — remove' : 'Draw on the chart'}
          </button>
        )}
      </div>

      <div>
        <SubHead hint="measured on the daily chart">Their rules</SubHead>
        <ul className="divide-y divide-white/[0.05]">
          {s.rules.map(r => (
            <li key={r.id} className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-x-2.5 py-1.5" data-rule={r.id} data-state={ruleState(r)}>
              <span className="mt-0.5"><RuleIcon s={ruleState(r)} /></span>
              <span className="text-[13px] leading-snug">{r.rule}</span>
              <span className="text-[11px] font-mono text-base-content/55 text-right max-w-[11rem] truncate" title={fmtRuleValue(r.value)}>{fmtRuleValue(r.value)}</span>
            </li>
          ))}
        </ul>
      </div>

      {stop && (
        <Callout tone={stop.tone} icon={<Info className="w-3.5 h-3.5" />}>
          <div className="font-semibold">{stop.headline}</div>
          <div className="mt-0.5 opacity-90">{stop.detail}</div>
        </Callout>
      )}

      {s.levels.length > 0 && (
        <div>
          <SubHead hint="draw them to see them against the candles">Levels this trader watches</SubHead>
          <table className="w-full text-xs">
            <thead className="sr-only"><tr><th>Level</th><th>Price</th><th>From now</th><th>Role</th></tr></thead>
            <tbody>
              {s.levels.map(l => {
                const d = levelDistancePct(l.price, spot);
                return (
                  <tr key={`${l.label}-${l.price}`} className="border-t border-white/[0.05]">
                    <td className="py-1 pr-2">{l.label}</td>
                    <td className="tabular-nums font-medium pr-2">{money(l.price)}</td>
                    <td className={`tabular-nums pr-2 ${d == null ? '' : d >= 0 ? 'text-success/80' : 'text-error/80'}`}>{d == null ? '—' : pctStr(d)}</td>
                    <td className="text-right"><Chip tone={l.role === 'exit' && isLevel ? 'bad' : l.role === 'trigger' ? 'warn' : 'neutral'} className="!text-[11px]">{l.role === 'exit' && isLevel ? 'their exit' : l.role === 'trigger' ? 'their entry' : 'reference'}</Chip></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {bt && (
        <div>
          <SubHead hint={bt.match === 'exact' ? 'this exact exit, backtested' : 'closest backtested exit'}>How this kind of exit has behaved</SubHead>
          <table className="w-full text-xs">
            <thead><tr className="text-base-content/45 text-left"><th className="font-normal">Rule</th><th className="font-normal text-right">avg</th><th className="font-normal text-right">win</th><th className="font-normal text-right">worst 5%</th><th className="font-normal text-right">held</th></tr></thead>
            <tbody>
              {[bt, bt.baseline].map((r, i) => (
                <tr key={i} className="border-t border-white/[0.05]">
                  <td className="py-1 pr-2">{r.label}</td>
                  <td className="text-right font-mono">{r.mean > 0 ? '+' : ''}{r.mean}%</td><td className="text-right font-mono">{r.win}%</td>
                  <td className="text-right font-mono">{r.p5}%</td><td className="text-right font-mono">{r.hold}d</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1 text-[11px] text-base-content/45 leading-snug">{bt.note} The comparison row is simply holding. Wide exits kept the trend; tight ones whipsawed.</p>
        </div>
      )}

      <p className="text-[11px] text-base-content/40 leading-snug">
        These are {s.full_name || s.trader}'s widely published mechanical criteria — a systematic reading, not what they actually did.
        <details className="inline"><summary className="inline cursor-pointer text-base-content/55 hover:text-base-content ml-1">Why this is not a forecast</summary><span className="block mt-1">{data.edge_note}</span></details>
      </p>
    </div>
  );
}

// ── main ─────────────────────────────────────────────────────────────────────────────────────────────────────────
export default function BetaStrategies({ ticker, setups, spot, drawnKey, onDraw }: Props) {
  const [sel, setSel] = useState<string>('swing');
  const [filter, setFilter] = useState<Filter>(NO_FILTER);

  // trader rule-sets — one lazy request, once per ticker
  const [td, setTd] = useState<TraderStrategiesData | null>(null);
  const [tdErr, setTdErr] = useState<string | null>(null);
  const [tdLoading, setTdLoading] = useState(false);
  const reqFor = useRef<string | null>(null);
  const loadTraders = useCallback(() => {
    const t = ticker; reqFor.current = t;
    setTd(null); setTdErr(null); setTdLoading(true);
    fetchTraderStrategies(t)
      .then(r => { if (reqFor.current === t) setTd(r.trader_strategies); })
      .catch(e => { if (reqFor.current === t) setTdErr(e instanceof Error ? e.message : 'Could not run the trader rule-sets'); })
      .finally(() => { if (reqFor.current === t) setTdLoading(false); });
  }, [ticker]);
  useEffect(() => { if (reqFor.current !== ticker) loadTraders(); }, [ticker, loadTraders]);

  // day setups + named strategies — lazy, only when their tile is picked (same endpoints as classic)
  const [day, setDay] = useState<StratState>({ loading: false, tried: false });
  const [strat, setStrat] = useState<Record<string, StratState>>({});
  const tickerNow = useRef(ticker); tickerNow.current = ticker;
  useEffect(() => { setDay({ loading: false, tried: false }); setStrat({}); setSel('swing'); setFilter(NO_FILTER); }, [ticker]);
  const loadDay = useCallback(() => {
    const t = ticker;
    setDay({ loading: true, tried: true });
    fetchDayTradeSetups(t)
      .then(r => { if (tickerNow.current === t) setDay({ data: r.day_trade_setups?.setups || [], loading: false, tried: true }); })
      .catch((e: unknown) => { if (tickerNow.current === t) setDay({ loading: false, tried: true, err: e instanceof Error ? e.message : 'No intraday data available' }); });
  }, [ticker]);
  const loadStrat = useCallback((n: NamedStrategy) => {
    const t = ticker;
    setStrat(p => ({ ...p, [n.key]: { ...p[n.key], loading: true, err: undefined, tried: true } }));
    n.fetch(t)
      .then(d => { if (tickerNow.current === t) setStrat(p => ({ ...p, [n.key]: { data: d, loading: false, tried: true } })); })
      .catch((e: unknown) => { if (tickerNow.current === t) setStrat(p => ({ ...p, [n.key]: { loading: false, tried: true, err: e instanceof Error ? e.message : 'No daily history available' } })); });
  }, [ticker]);

  const pick = useCallback((id: string) => {
    setSel(id);
    if (id === 'day' && !day.tried) loadDay();
    const n = NAMED_STRATEGIES.find(x => x.key === id);
    if (n && !strat[n.key]?.tried) loadStrat(n);
  }, [day.tried, strat, loadDay, loadStrat]);

  // tiles
  const swing = useMemo(() => setupsForStyle(setups, 'swing', null), [setups]);
  const position = useMemo(() => setupsForStyle(setups, 'position', null), [setups]);
  const eng = useMemo(() => engineTiles({ swing: setups ? swing.length : null, position: setups ? position.length : null, day: day.data ? day.data.length : null }), [setups, swing, position, day.data]);
  const named = useMemo(() => namedTiles(NAMED_STRATEGIES), []);
  const traders = useMemo(() => traderTiles(td), [td]);
  const counts = useMemo(() => filterCounts(traders), [traders]);
  const shown = useMemo(() => filterTraders(traders, filter), [traders, filter]);
  const cons = useMemo(() => consensusView(td?.consensus), [td]);
  const badge = (key: string): string | null => {
    const n = NAMED_STRATEGIES.find(x => x.key === key); const st = strat[key];
    if (!n) return null;
    if (st?.loading) return 'running…';
    if (st?.err) return 'unavailable';
    if (st?.data) { const p = n.toPanel(st.data); return `${p.badge} · ${p.subBadge}`; }
    return null;
  };

  const selTrader = td?.strategies.find(s => s.key === sel) || null;
  const selNamed = NAMED_STRATEGIES.find(n => n.key === sel) || null;

  const toggleDraw = (s: TraderStrategy) => {
    if (drawnKey === s.key) { onDraw(null); return; }
    onDraw({ key: s.key, label: s.trader, levels: strategyChartLevels(s) });
    try { document.getElementById('beta-ta-chart')?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }); } catch { /* not critical */ }
  };

  const renderEngine = (id: 'swing' | 'position' | 'day') => {
    const list: TradeSetup[] = id === 'day' ? (day.data || []) : id === 'swing' ? swing : position;
    if (id === 'day' && day.loading) return <div className="p-6 flex items-center justify-center gap-2 text-sm text-base-content/60"><Loader2 className="w-4 h-4 animate-spin" /> Scanning intraday structure, VWAP and the opening range…</div>;
    if (id === 'day' && day.err) return <p className="p-4 text-sm text-base-content/60">{day.err} — intraday setups need live market-hours data.</p>;
    if (!setups && id !== 'day') return <p className="p-4 text-sm text-base-content/55">Waiting for the setup read…</p>;
    if (!list.length) {
      return (
        <div className="p-6 text-center">
          <p className="text-sm font-semibold text-base-content/75">{id === 'position' ? 'No long-term entry lined up' : id === 'day' ? 'No clean intraday setup right now' : 'No high-conviction swing setup right now'}</p>
          <p className="text-xs text-base-content/50 mt-1">{id === 'position' ? 'No strong value zone to accumulate into with a worthwhile reward yet.' : id === 'day' ? 'Price is mid-range against VWAP and the opening range — wait for a break or a VWAP reclaim.' : "The signals don't line up into a clean trade yet — check the other styles or the levels on the chart."}</p>
        </div>
      );
    }
    return <div className="space-y-3">{list.map((s, i) => <SetupCard key={`${id}-${i}`} setup={s} ticker={ticker} spot={id === 'day' ? spot : (setups?.price ?? spot)} dossier={(setups?.dossier || {}) as Record<string, unknown>} />)}</div>;
  };

  const renderNamed = (n: NamedStrategy) => {
    const st = strat[n.key];
    if (!st || st.loading) return <div className="p-6 flex items-center justify-center gap-2 text-sm text-base-content/60"><Loader2 className="w-4 h-4 animate-spin" /> Running the {n.label} screen on the daily history…</div>;
    if (st.err) return <p className="p-4 text-sm text-base-content/60">{st.err} — this strategy needs daily price history.</p>;
    if (!st.data) return null;
    const { setups: ss, ...rest } = st.data;
    const list: TradeSetup[] = ss || [];
    return (
      <div className="space-y-3">
        <StrategyPanel p={n.toPanel(st.data)} />
        {list.length === 0
          ? <p className="p-3 text-center text-xs text-base-content/50">No actionable {n.label} trade right now — the checklist above shows the signal state and what it is waiting for.</p>
          : list.map((s, i) => <SetupCard key={`${n.key}-${i}`} setup={s} ticker={ticker} spot={st.data.price ?? spot} dossier={{ [n.key]: rest }} />)}
      </div>
    );
  };

  const FilterChip = ({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) => (
    <button type="button" aria-pressed={active} onClick={onClick}
      className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${active ? 'border-primary/40 bg-primary/10 text-primary' : 'border-white/10 text-base-content/55 hover:text-base-content hover:border-white/25'}`}>{children}</button>
  );

  return (
    <div className="space-y-4" data-testid="beta-strategies">
      {/* consensus */}
      <div className="rounded-xl border border-white/[0.08] bg-base-200/25 p-3">
        <div className="flex items-baseline justify-between gap-2 flex-wrap">
          <span className="text-xs font-semibold text-base-content/75">Famous-trader consensus</span>
          {cons ? (
            <span className="text-xs text-base-content/55"><b className="text-success">{cons.bull}</b> bullish · {cons.neutral} neutral · <b className="text-error">{cons.bear}</b> bearish <span className="text-base-content/35">of {cons.n}</span></span>
          ) : tdLoading ? <span className="text-xs text-base-content/45 flex items-center gap-1.5"><Loader2 className="w-3 h-3 animate-spin" /> running 15 rule-sets…</span> : null}
        </div>
        {cons && (
          <div className="mt-1.5 flex h-2.5 w-full overflow-hidden rounded-full bg-base-content/10" role="img" aria-label={`${cons.bull} bullish, ${cons.neutral} neutral, ${cons.bear} bearish`}>
            <div className="bg-success" style={{ width: `${cons.pct.bull}%` }} /><div className="bg-base-content/30" style={{ width: `${cons.pct.neutral}%` }} /><div className="bg-error" style={{ width: `${cons.pct.bear}%` }} />
          </div>
        )}
        {tdErr && <div className="mt-1 flex items-center gap-2 text-xs text-base-content/60">Couldn't run the trader rule-sets: {tdErr} <button className="btn btn-ghost btn-xs gap-1" onClick={loadTraders}><RefreshCw className="w-3 h-3" /> Retry</button></div>}
        <p className="mt-1.5 text-[11px] text-base-content/45 leading-snug">
          Each published rule-set is run on this chart exactly as written, then shown with its checklist, its own exit and its levels. A discipline and structure read — in backtests these showed no edge predicting returns, so none is a forecast.
          {td && <details className="inline"><summary className="inline cursor-pointer text-base-content/60 hover:text-base-content ml-1">The numbers</summary><span className="block mt-1">{td.edge_note}</span></details>}
        </p>
      </div>

      {/* engine + named */}
      <div>
        <SubHead hint="the plans the engine builds, and three named screens">Setups and named screens</SubHead>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
          {eng.map(t => <TileButton key={t.id} t={t} selected={sel === t.id} drawn={false} onPick={pick} />)}
          {named.map(t => <TileButton key={t.id} t={t} selected={sel === t.id} drawn={false} badge={badge(t.id)} onPick={pick} />)}
        </div>
      </div>

      {/* traders */}
      <div>
        <div className="flex items-center gap-x-4 gap-y-1.5 flex-wrap mb-1.5">
          <SubHead hint="13 traders — Qullamäggie and Connors are the named screens above">Famous-trader rule-sets</SubHead>
          {traders.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap ml-auto" role="group" aria-label="Filter the trader rule-sets">
              <FilterChip active={filter.family === 'all'} onClick={() => setFilter(f => ({ ...f, family: 'all' }))}>All {counts.family.all}</FilterChip>
              {FAMILY_ORDER.filter(f => counts.family[f]).map(f => <FilterChip key={f} active={filter.family === f} onClick={() => setFilter(x => ({ ...x, family: f }))}>{FAMILY_LABEL[f]} {counts.family[f]}</FilterChip>)}
              <span className="w-px h-4 bg-white/10 mx-0.5" aria-hidden="true" />
              {(['bullish', 'neutral', 'bearish'] as const).filter(k => counts.stance[k]).map(k => <FilterChip key={k} active={filter.stance === k} onClick={() => setFilter(x => ({ ...x, stance: x.stance === k ? 'all' : k }))}>{STANCE_WORD[k]} {counts.stance[k]}</FilterChip>)}
            </div>
          )}
        </div>
        {tdLoading && !td && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2" aria-busy="true">
            {Array.from({ length: 10 }, (_, i) => <div key={i} className="h-[4.25rem] rounded-lg bg-base-content/[0.06] animate-pulse" />)}
          </div>
        )}
        {td && (
          shown.length === 0
            ? <p className="text-xs text-base-content/50 py-3">No trader matches that filter. <button className="underline" onClick={() => setFilter(NO_FILTER)}>Clear the filter</button></p>
            : (
              <div className="space-y-3">
                {FAMILY_ORDER.map(f => {
                  const row = shown.filter(t => t.family === f);
                  if (!row.length) return null;
                  return (
                    <div key={f}>
                      <div className="text-[11px] text-base-content/40 mb-1">{FAMILY_LABEL[f]}</div>
                      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
                        {row.map(t => <TileButton key={t.id} t={t} selected={sel === t.id} drawn={drawnKey === t.id} onPick={pick} />)}
                      </div>
                    </div>
                  );
                })}
              </div>
            )
        )}
      </div>

      {/* detail */}
      <div aria-live="polite">
        {selTrader && td ? <TraderDetail s={selTrader} spot={spot} data={td} drawn={drawnKey === selTrader.key} onToggleDraw={toggleDraw} />
          : selNamed ? renderNamed(selNamed)
            : sel === 'swing' || sel === 'position' || sel === 'day' ? renderEngine(sel)
              : <p className="text-xs text-base-content/50">Pick a strategy above.</p>}
      </div>
    </div>
  );
}
