/**
 * BetaTechnical — the opt-in redesign of the Technical tab (?ui=beta).
 *
 * Classic has four sections (Setups · Patterns · Indicators · Advanced), five chart styles, three timeframe
 * controls and six overlapping opinions. Beta reads like the My Trades / Income Desk Beta: ONE verdict on top,
 * then a cockpit with the chart in the CENTRE — six lenses on the left (click one to draw it), the trade plan
 * and its honest odds on the right — and the classic evidence panels one click down, loaded only when opened.
 *
 * Everything shown comes from the same endpoints the classic tab uses; the only new maths is the first-touch
 * odds (firstPassage.ts), which is validated against independent numerical solutions.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, RefreshCw, Layers, CandlestickChart, ListChecks, Table2, FileSearch, AlertTriangle } from 'lucide-react';
import { fetchTradeSetups, fetchDayTradeSetups, fetchMarketStructure, fetchMicrostructure, fetchRegime, fetchDealerPositioning } from '../../api';
import type { CandleInterval, TechnicalData, TradeSetup, TradeSetupsData } from '../../types';
import { structureLayers, liquidityLayers, vpLayers, regimeLayers, gammaLayers, type TALayer } from '../../components/taLayers';
import { useLocalState } from '../../components/taShared';
import ErrorBoundary from '../../components/ErrorBoundary';
import TradeSetupCards from '../../components/TradeSetupCards';
import ChartPatternsPanel from '../../components/ChartPatternsPanel';
import VolumePanel from '../../components/VolumePanel';
import RegimeEdgePanel from '../../components/RegimeEdgePanel';
import MicrostructurePanel from '../../components/MicrostructurePanel';
import MarketStructurePanel from '../../components/MarketStructurePanel';
import RegimePanel from '../../components/RegimePanel';
import DealerPositioningPanel from '../../components/DealerPositioningPanel';
import { PricePrediction } from '../../components/PricePrediction';
import { BetaBadge, BackToClassicButton } from '../BetaChrome';
import { Chip, Accordion, SubHead, type Tone } from '../BetaKit';
import BetaTaChart from './BetaTaChart';
import { LensRail, Disagree, PlanCard, NoPlan, LevelsTable, IndicatorGrid } from './BetaTaPanels';
import {
  deriveVerdict, deriveLenses, disagreements, setupsForStyle, planLevels, planOdds, levelRows, settleLevels, windowLabel, money,
  type LayerKey, type Style,
} from './betaTaModel';
import { levelsFromPlan, levelsFromZones, levelsFromLayers, dedupeLevels, tierForInterval, COLOR, type ChartLevel } from './betaTaChartModel';

const STYLES: { k: Style; label: string; hint: string; iv: CandleInterval }[] = [
  { k: 'day', label: 'Day', hint: 'intraday', iv: '15m' },
  { k: 'swing', label: 'Swing', hint: 'days to weeks', iv: '1d' },
  { k: 'position', label: 'Position', hint: 'weeks to months', iv: '1wk' },
];
const INTERVALS: { k: CandleInterval; label: string }[] = [{ k: '15m', label: '15m' }, { k: '1h', label: '1H' }, { k: '1d', label: '1D' }, { k: '1wk', label: '1W' }];

const CHIPS: { k: LayerKey; label: string; hint: string }[] = [
  { k: 'plan', label: 'Plan', hint: 'Entry, stop and targets of the setup on the right' },
  { k: 'levels', label: 'Key levels', hint: 'Zones where several methods agree, plus swing support and resistance' },
  { k: 'structure', label: 'Structure', hint: 'Swing highs and lows and the last break of structure' },
  { k: 'blocks', label: 'Order blocks', hint: 'Unmitigated order blocks and unfilled fair-value gaps' },
  { k: 'liquidity', label: 'Liquidity', hint: 'Resting stop pools and multi-timeframe confluence' },
  { k: 'dealer', label: 'Dealer gamma', hint: 'Gamma flip, call and put walls, the 30-day expected move' },
  { k: 'value', label: 'Value & volume', hint: 'Point of control, value area, naked POCs and anchored VWAP' },
];
type Source = 'structure' | 'micro' | 'dealer';
const SOURCE_OF: Partial<Record<LayerKey, Source>> = { structure: 'structure', blocks: 'structure', liquidity: 'structure', dealer: 'dealer', value: 'micro' };

const VERDICT_TONE = { long: 'good', short: 'bad', neutral: 'warn' } as const;

interface Props { ticker: string; technical: TechnicalData | null | undefined; onLeave: () => void }

export default function BetaTechnical({ ticker, technical, onLeave }: Props) {
  const [style, setStyle] = useState<Style>('swing');
  const [interval, setIntervalState] = useState<CandleInterval>('1d');
  const [planIdx, setPlanIdx] = useState(0);
  const [layersArr, setLayersArr] = useLocalState<LayerKey[]>('beta:ta:layers', ['plan', 'levels']);
  const layers = useMemo(() => new Set<LayerKey>(Array.isArray(layersArr) ? layersArr : ['plan', 'levels']), [layersArr]);   // saved in the browser: never trust its shape

  // ── the engine read (verdict, zones, setups) — fetched once per ticker, never blocks the chart ──
  const [setups, setSetups] = useState<TradeSetupsData | null>(null);
  const [setupsErr, setSetupsErr] = useState<string | null>(null);
  const [setupsLoading, setSetupsLoading] = useState(false);
  const fetched = useRef<string | null>(null);
  const loadSetups = useCallback(() => {
    fetched.current = ticker;
    setSetups(null); setSetupsErr(null); setSetupsLoading(true);
    const t = ticker;
    fetchTradeSetups(t)
      .then(r => { if (fetched.current === t) setSetups(r.trade_setups); })
      .catch(e => { if (fetched.current === t) setSetupsErr(e?.message || 'Could not read the market'); })
      .finally(() => { if (fetched.current === t) setSetupsLoading(false); });
  }, [ticker]);
  useEffect(() => { if (fetched.current !== ticker) loadSetups(); }, [ticker, loadSetups]);

  // ── day-trade setups: lazy, only when the Day style is opened ──
  const [dayData, setDayData] = useState<TradeSetup[] | null>(null);
  const [dayLoading, setDayLoading] = useState(false);
  const [dayErr, setDayErr] = useState<string | null>(null);
  const dayFor = useRef<string | null>(null);
  useEffect(() => { dayFor.current = null; setDayData(null); setDayErr(null); }, [ticker]);
  useEffect(() => {
    if (style !== 'day' || dayFor.current === ticker) return;
    dayFor.current = ticker; setDayLoading(true); setDayErr(null);
    fetchDayTradeSetups(ticker)
      .then(r => setDayData(r.day_trade_setups?.setups || []))
      .catch(e => setDayErr(e instanceof Error ? `${e.message} — intraday setups need live market-hours data.` : 'No intraday data available'))
      .finally(() => setDayLoading(false));
  }, [style, ticker]);

  // ── method layers: each loads on first use ──
  const [store, setStore] = useState<Partial<Record<Source, TALayer[]>>>({});
  const [loadingSrc, setLoadingSrc] = useState<Partial<Record<Source, boolean>>>({});
  const [srcErr, setSrcErr] = useState<Partial<Record<Source, string>>>({});
  const asked = useRef<Set<string>>(new Set());
  const tickerNow = useRef(ticker); tickerNow.current = ticker;      // a layer answer for a ticker the user has left is dropped
  useEffect(() => { asked.current = new Set(); setStore({}); setSrcErr({}); setLoadingSrc({}); }, [ticker]);
  const ensure = useCallback((src: Source) => {
    const key = `${ticker}|${src}`;
    if (asked.current.has(key)) return;
    asked.current.add(key);
    setLoadingSrc(p => ({ ...p, [src]: true }));
    const job: Promise<TALayer[]> =
      src === 'structure' ? fetchMarketStructure(ticker).then(r => [...structureLayers(r.market_structure), ...liquidityLayers(r.market_structure)])
        : src === 'dealer' ? fetchDealerPositioning(ticker).then(r => gammaLayers(r.dealer_positioning))
          : Promise.all([fetchMicrostructure(ticker).then(r => vpLayers(r.microstructure)), fetchRegime(ticker).then(r => regimeLayers(r.regime)).catch(() => [] as TALayer[])]).then(([a, b]) => [...a, ...b]);
    const t = ticker, live = () => tickerNow.current === t;
    job.then(ls => { if (live()) setStore(p => ({ ...p, [src]: ls })); })
      .catch(e => { if (!live()) return; asked.current.delete(key); setSrcErr(p => ({ ...p, [src]: e instanceof Error ? e.message : 'Failed to load' })); })
      .finally(() => { if (live()) setLoadingSrc(p => ({ ...p, [src]: false })); });
  }, [ticker]);
  useEffect(() => { layers.forEach(k => { const s = SOURCE_OF[k]; if (s) ensure(s); }); }, [layers, ensure]);

  const toggleLayer = useCallback((k: LayerKey) => {
    setLayersArr(prev => { const cur = Array.isArray(prev) ? prev : ['plan', 'levels'] as LayerKey[]; return cur.includes(k) ? cur.filter(x => x !== k) : [...cur, k]; });
    const s = SOURCE_OF[k]; if (s) { setSrcErr(p => ({ ...p, [s]: undefined })); ensure(s); }
  }, [ensure, setLayersArr]);

  const pickStyle = (s: Style) => { setStyle(s); setPlanIdx(0); setIntervalState(STYLES.find(x => x.k === s)!.iv); };
  useEffect(() => { setPlanIdx(0); }, [ticker]);

  // ── derived read ──
  const ctx = setups?.context ?? null;
  const [chartSpot, setChartSpot] = useState<number | null>(null);
  useEffect(() => { setChartSpot(null); }, [ticker]);
  const techSpot = technical?.prices?.length ? technical.prices[technical.prices.length - 1] : null;
  const spot = setups?.price ?? chartSpot ?? techSpot ?? null;

  const verdict = useMemo(() => deriveVerdict(ctx), [ctx]);
  const lenses = useMemo(() => deriveLenses(ctx, technical && !technical.error ? technical : null, spot), [ctx, technical, spot]);
  const list = useMemo(() => setupsForStyle(setups, style, dayData), [setups, style, dayData]);
  const plan = list[Math.min(planIdx, Math.max(0, list.length - 1))] ?? null;
  const lv = useMemo(() => planLevels(plan), [plan]);
  const odds = useMemo(() => planOdds(plan, ctx, spot, style), [plan, ctx, spot, style]);
  const notes = useMemo(() => disagreements(lenses, verdict, plan, odds), [lenses, verdict, plan, odds]);
  const styleLabel = STYLES.find(s => s.k === style)!.label;

  const tier = tierForInterval(interval);
  const chartLevels: ChartLevel[] = useMemo(() => {
    const out: ChartLevel[] = [];
    if (layers.has('plan')) out.push(...levelsFromPlan(lv));
    if (layers.has('levels')) {
      out.push(...levelsFromZones(setups?.confluence_zones, spot));
      if (technical && !technical.error) {
        if (technical.supportLevel) out.push({ id: 'ta-s', layer: 'levels', kind: 'line', price: technical.supportLevel, label: `Swing sup ${technical.supportLevel.toFixed(2)}`, color: COLOR.up, dashed: true, priority: 5 });
        if (technical.resistanceLevel) out.push({ id: 'ta-r', layer: 'levels', kind: 'line', price: technical.resistanceLevel, label: `Swing res ${technical.resistanceLevel.toFixed(2)}`, color: COLOR.rose, dashed: true, priority: 5 });
      }
    }
    for (const k of ['structure', 'blocks', 'liquidity', 'dealer', 'value'] as LayerKey[]) {
      if (!layers.has(k)) continue;
      const src = SOURCE_OF[k]!;
      out.push(...levelsFromLayers(store[src], k, tier));
    }
    return dedupeLevels(out);
  }, [layers, lv, setups, spot, technical, store, tier]);

  const extraRows = useMemo(() => {
    const ex: { id: string; price: number; label: string; kind: string }[] = [];
    for (const l of store.dealer || []) {
      if (l.status || l.price == null) continue;
      if (['gamma_call_res', 'gamma_put_sup', 'gamma_hvl'].includes(l.id)) ex.push({ id: l.id, price: l.price, label: l.label.replace(/\s*\$[\d.,]+/, ''), kind: 'dealer' });
    }
    for (const l of store.micro || []) if (l.id === 'vp_daily_poc' && l.price != null) ex.push({ id: l.id, price: l.price, label: 'Point of control (daily)', kind: 'structure' });
    return ex;
  }, [store]);
  const rows = useMemo(() => levelRows(spot, lv, setups?.confluence_zones, technical && !technical.error ? technical : null, ctx, extraRows), [spot, lv, setups, technical, ctx, extraRows]);

  const pending = setupsLoading && !setups;
  const tone: Tone = verdict ? (verdict.mixed ? 'warn' : VERDICT_TONE[verdict.dir]) : 'neutral';
  const settle = useMemo(() => (verdict && verdict.dir === 'neutral' ? settleLevels(setups?.confluence_zones, spot) : null), [verdict, setups, spot]);
  const wrongIf = lv ? `${lv.direction === 'long' ? 'below' : 'above'} ${money(lv.stop)}` : null;

  const evidenceBody = (children: React.ReactNode) => <ErrorBoundary label="Technical evidence">{children}</ErrorBoundary>;
  const tech = technical && !technical.error ? technical : null;

  return (
    <div className="beta-readable space-y-3 animate-fade-in" data-testid="beta-technical">
      {/* header */}
      <div className="flex items-center gap-2 flex-wrap">
        <BetaBadge />
        <span className="text-lg font-semibold">{ticker}</span>
        {spot != null && <span className="text-lg font-semibold tabular-nums">{money(spot)}</span>}
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <div className="inline-flex rounded-lg border border-white/10 p-0.5 bg-base-200/40" role="tablist" aria-label="Trade style">
            {STYLES.map(s => (
              <button key={s.k} role="tab" aria-selected={style === s.k} title={s.hint} onClick={() => pickStyle(s.k)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${style === s.k ? 'bg-primary/15 text-primary' : 'text-base-content/55 hover:text-base-content'}`}>{s.label}</button>
            ))}
          </div>
          <BackToClassicButton onClick={onLeave} />
        </div>
      </div>

      {/* verdict */}
      <div className={`rounded-xl border px-4 py-3 ${verdict ? `${tone === 'good' ? 'border-success/30 bg-success/[0.05]' : tone === 'bad' ? 'border-error/30 bg-error/[0.05]' : 'border-warning/30 bg-warning/[0.05]'}` : 'border-white/[0.08] bg-base-200/25'}`} data-testid="verdict">
        {verdict ? (
          <div className="flex items-center gap-x-3 gap-y-1 flex-wrap">
            <Chip tone={tone} className="!text-sm !px-3 !py-1">{verdict.label}</Chip>
            {verdict.conviction && <span className="text-xs text-base-content/55">{verdict.conviction} conviction</span>}
            <span className="text-sm text-base-content/80 flex-1 min-w-[16rem]">{verdict.headline}</span>
            {wrongIf && <span className="text-xs text-base-content/55">Wrong if price trades <b className="text-base-content/80 tabular-nums">{wrongIf}</b>.</span>}
            {settle && (settle.above != null || settle.below != null) && (
              <span className="text-xs text-base-content/55 basis-full">
                What would settle it: {settle.above != null && <>resistance at <b className="text-base-content/80 tabular-nums">{money(settle.above)}</b></>}{settle.above != null && settle.below != null && ' and '}{settle.below != null && <>support at <b className="text-base-content/80 tabular-nums">{money(settle.below)}</b></>} — a break of either shows which side is winning.
              </span>
            )}
          </div>
        ) : setupsErr ? (
          <div className="flex items-center gap-3 flex-wrap text-sm"><AlertTriangle className="w-4 h-4 text-warning" /><span className="text-base-content/70">Couldn't read the market: {setupsErr}</span><button className="btn btn-xs btn-ghost gap-1" onClick={loadSetups}><RefreshCw className="w-3 h-3" /> Retry</button></div>
        ) : (
          <div className="flex items-center gap-2 text-sm text-base-content/60"><Loader2 className="w-4 h-4 animate-spin" /> Reading structure, regime and dealer flow — the chart is ready now. <span className="text-base-content/40">Nothing is guessed in the meantime.</span></div>
        )}
      </div>

      {/* cockpit: lenses | CHART | plan */}
      <div className="grid gap-3 xl:grid-cols-[15.5rem_minmax(0,1fr)_20rem] items-start">
        <div className="space-y-3 order-2 xl:order-1">
          <LensRail lenses={lenses} pending={pending} active={layers} onToggle={toggleLayer} />
          {!pending && <Disagree items={notes} />}
        </div>

        <div className="order-1 xl:order-2 min-w-0 rounded-xl border border-white/[0.08] bg-base-200/25 p-2">
          <div className="flex items-center gap-1.5 flex-wrap px-1 pb-2">
            <span className="text-[11px] text-base-content/45 mr-1">Layers</span>
            {CHIPS.map(c => {
              const on = layers.has(c.k), src = SOURCE_OF[c.k];
              const busy = on && src && loadingSrc[src] && !store[src];
              const bad = on && src && srcErr[src] && !store[src];
              return (
                <button key={c.k} type="button" aria-pressed={on} title={bad ? `${c.hint} — ${srcErr[src!]}` : c.hint} onClick={() => toggleLayer(c.k)}
                  className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs transition-colors ${on ? 'border-primary/40 bg-primary/10 text-primary' : 'border-white/10 text-base-content/55 hover:text-base-content hover:border-white/25'} ${bad ? '!border-error/40 !text-error' : ''}`}>
                  {busy && <Loader2 className="w-3 h-3 animate-spin" />}{c.label}
                </button>
              );
            })}
            <div className="ml-auto inline-flex rounded-lg border border-white/10 p-0.5 bg-base-200/40" role="tablist" aria-label="Chart timeframe">
              {INTERVALS.map(i => (
                <button key={i.k} role="tab" aria-selected={interval === i.k} onClick={() => setIntervalState(i.k)}
                  className={`px-2.5 py-0.5 rounded-md text-xs font-medium ${interval === i.k ? 'bg-primary/15 text-primary' : 'text-base-content/55 hover:text-base-content'}`}>{i.label}</button>
              ))}
            </div>
          </div>
          <ErrorBoundary label="Chart">
            <BetaTaChart ticker={ticker} interval={interval} levels={chartLevels} height={540} onSpot={setChartSpot} />
          </ErrorBoundary>
          <p className="px-1 pt-1 text-[11px] text-base-content/40">Scroll or drag to zoom and pan. Levels are labelled at the right, fanned apart so none overlap; the dotted line is the last price.</p>
        </div>

        <div className="order-3 min-w-0 xl:sticky xl:top-3">
          {plan ? (
            <PlanCard ticker={ticker} plan={plan} planIdx={planIdx} count={list.length} onPick={setPlanIdx} lv={lv} spot={spot} odds={odds} styleLabel={styleLabel} window={odds && odds.ok ? windowLabel(odds.days, style) : null} />
          ) : (
            <NoPlan style={styleLabel.toLowerCase()} loading={style === 'day' ? dayLoading : pending} error={style === 'day' ? dayErr : setupsErr} />
          )}
        </div>
      </div>

      {/* levels + evidence */}
      <Accordion defaultOpen title="Levels that matter" icon={<Table2 className="w-4 h-4" />}
        summary={rows.length ? `${rows.filter(r => r.kind !== 'plan').length} levels around ${money(spot)}` : undefined}>
        <LevelsTable rows={rows} spot={spot} />
      </Accordion>

      <SubHead hint="loaded only when you open one">Evidence</SubHead>
      <div className="space-y-2">
        <Accordion title="Indicators" icon={<ListChecks className="w-4 h-4" />}
          summary={tech ? `RSI ${tech.currentRSI != null ? tech.currentRSI.toFixed(0) : '—'} · MACD ${tech.macd?.histogram != null ? (tech.macd.histogram > 0 ? '+' : '') + tech.macd.histogram.toFixed(2) : '—'} · %B ${tech.bollingerBands?.percentB != null ? (tech.bollingerBands.percentB * 100).toFixed(0) : '—'}` : undefined}>
          <IndicatorGrid tech={tech} />
        </Accordion>
        <Accordion title="Chart patterns" icon={<Layers className="w-4 h-4" />} summary="double tops and bottoms, head and shoulders, triangles, flags, cup and handle">
          {evidenceBody(<ChartPatternsPanel ticker={ticker} />)}
        </Accordion>
        <Accordion title="Volume" icon={<CandlestickChart className="w-4 h-4" />} summary="relative volume, dry-up and expansion, climax, accumulation and distribution">
          {evidenceBody(<VolumePanel ticker={ticker} defaultOpen />)}
        </Accordion>
        <Accordion title="Historical edge by regime" icon={<FileSearch className="w-4 h-4" />} summary="how often each signal has worked on this name, split by trending and choppy markets">
          {evidenceBody(<RegimeEdgePanel ticker={ticker} defaultOpen />)}
        </Accordion>
        <Accordion title="Volume profile and anchored VWAP" summary="point of control, value area, naked POCs">
          {evidenceBody(<MicrostructurePanel ticker={ticker} price={spot ?? undefined} confluenceZones={setups?.confluence_zones} />)}
        </Accordion>
        <Accordion title="Market structure and liquidity" summary="breaks of structure and change of character across daily, 4H and 1H">
          {evidenceBody(<MarketStructurePanel ticker={ticker} price={spot ?? undefined} confluenceZones={setups?.confluence_zones} />)}
        </Accordion>
        <Accordion title="Regime" summary="Hurst, efficiency ratio and distance from the 50-day VWAP">
          {evidenceBody(<RegimePanel ticker={ticker} price={spot ?? undefined} confluenceZones={setups?.confluence_zones} />)}
        </Accordion>
        <Accordion title="Dealer positioning" summary="net gamma, flip, walls and the expected-move cone">
          {evidenceBody(<DealerPositioningPanel ticker={ticker} price={spot ?? undefined} confluenceZones={setups?.confluence_zones} />)}
        </Accordion>
        <Accordion title="Every setup and named strategy" summary="all ranked setups, options versions, Momentum Swing, Qullamäggie, Connors RSI-2">
          {setups ? evidenceBody(<TradeSetupCards data={setups} ticker={ticker} />) : <p className="text-xs text-base-content/50">Waiting for the setup read…</p>}
        </Accordion>
        <Accordion title="Statistical price model" summary="a separate forecast — not part of the call above">
          {evidenceBody(<PricePrediction ticker={ticker} />)}
        </Accordion>
      </div>
    </div>
  );
}
