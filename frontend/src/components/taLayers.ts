// Normalized TA layers for the Unified workspace — each method's raw API payload → a common
// TALayer shape. `status:true` layers are read-only OUTCOMES (regime state, Net GEX sign) shown
// as badges, not drawable chart levels. Self-contained so the per-method panels stay untouched.
import type {
  MicrostructureData, MarketStructureData, RegimeData, DealerPositioningData,
  AnchoredVWAP, TimeframeBlock,
} from '../types';
import type { OverlayLine, OverlayBand } from './taOverlay';
import type { ProxItem } from './taShared';

export interface TALayer {
  id: string; group: string; label: string; tone: string; color: string;
  lines?: OverlayLine[]; bands?: OverlayBand[]; price?: number; points?: number[];
  status?: boolean;                    // true = a read-only status/outcome, not a drawable level
  json: Record<string, unknown>;
}

const GREEN = 'rgb(34,197,94)', RED = 'rgb(239,68,68)';
const TF_TIERS = [
  ['daily', 'Daily', 'rgb(139,92,246)', 'text-violet-400'],
  ['h4', '4H', 'rgb(56,189,248)', 'text-sky-400'],
  ['h1', '1H', 'rgb(251,191,36)', 'text-amber-400'],
] as const;

// ── Volume Profile (native lookback horizons: macro / swing / micro) ──
export function vpLayers(d: MicrostructureData): TALayer[] {
  const out: TALayer[] = [];
  const tfs = [
    ['daily', d.timeframe_profiles.daily, 'rgb(139,92,246)', 'text-violet-400'],
    ['h4', d.timeframe_profiles.h4, 'rgb(56,189,248)', 'text-sky-400'],
    ['h1', d.timeframe_profiles.h1, 'rgb(251,191,36)', 'text-amber-400'],
  ] as const;
  for (const [k, tf, color, tone] of tfs) {
    if (!tf) continue;
    const g = tf.label;
    out.push({ id: `vp_${k}_poc`, group: g, label: `POC $${tf.poc}`, tone, color, price: tf.poc,
      lines: [{ price: tf.poc, label: `${k} POC`, color }], json: { indicator: 'volume_profile_poc', timeframe: k, poc: tf.poc } });
    out.push({ id: `vp_${k}_va`, group: g, label: `Value Area`, tone, color, price: (tf.vah + tf.val) / 2,
      bands: [{ top: tf.vah, bottom: tf.val, label: `${k} VA`, color }], json: { indicator: 'value_area', timeframe: k, vah: tf.vah, val: tf.val } });
    const lvns = (tf.lvns || []).map(n => n.price).filter((x): x is number => x != null);
    if (lvns.length) out.push({ id: `vp_${k}_lvn`, group: g, label: `LVNs (${lvns.length})`, tone, color, points: lvns,
      lines: lvns.map(p => ({ price: p, label: 'LVN', color, dash: [2, 2] })), json: { indicator: 'low_volume_nodes', timeframe: k, nodes: tf.lvns } });
  }
  const nk = d.naked_pocs || [];
  if (nk.length) out.push({ id: 'vp_naked', group: 'Naked POCs', label: `Naked POCs (${nk.length})`, tone: 'text-rose-400', color: 'rgb(244,63,94)', points: nk.map(n => n.price),
    lines: nk.map(n => ({ price: n.price, label: `Naked $${n.price}`, color: 'rgb(244,63,94)', dash: [6, 3] })), json: { indicator: 'naked_pocs', levels: nk } });
  const av: [string, AnchoredVWAP | null][] = [['ytd', d.avwap.ytd], ['earnings', d.avwap.earnings], ['high_52w', d.avwap.high_52w], ['low_52w', d.avwap.low_52w]];
  for (const [k, a] of av) {
    if (!a) continue;
    out.push({ id: `vp_avwap_${k}`, group: 'Anchored VWAP', label: `${a.label} $${a.value}`, tone: 'text-emerald-400', color: 'rgb(16,185,129)', price: a.value,
      lines: [{ price: a.value, label: a.label, color: 'rgb(16,185,129)', dash: [8, 4] }], json: { indicator: 'anchored_vwap', anchor: k, value: a.value } });
  }
  return out;
}

// ── Market Structure (swings / order blocks / FVGs), grouped Daily · 4H · 1H ──
export function structureLayers(d: MarketStructureData): TALayer[] {
  const out: TALayer[] = [];
  for (const [k, tag, color, tone] of TF_TIERS) {
    const tf = d.timeframes[k] as TimeframeBlock | null;
    if (!tf) continue;
    const st = tf.structure;
    const lines: OverlayLine[] = [];
    if (st.last_event) lines.push({ price: st.last_event.level, label: `${tag} ${st.last_event.type}`, color });
    if (st.recent_swing_high != null) lines.push({ price: st.recent_swing_high, label: `${tag} swing H`, color, dash: [2, 2] });
    if (st.recent_swing_low != null) lines.push({ price: st.recent_swing_low, label: `${tag} swing L`, color, dash: [2, 2] });
    if (lines.length) out.push({ id: `structure_${k}_swings`, group: tag, label: 'Swings / BOS', tone, color, price: st.last_event?.level,
      lines, json: { indicator: 'market_structure', timeframe: k, trend: tf.trend, last_event: st.last_event } });
    const obs = (tf.order_blocks || []).filter(o => !o.mitigated);
    if (obs.length) out.push({ id: `structure_${k}_ob`, group: tag, label: `Order Blocks (${obs.length})`, tone, color,
      bands: obs.map(o => ({ top: o.top, bottom: o.bottom, label: `${tag} ${o.type === 'bullish' ? 'Demand' : 'Supply'} OB`, color: o.type === 'bullish' ? GREEN : RED })), json: { indicator: 'order_blocks', timeframe: k, blocks: obs } });
    const fvgs = (tf.fair_value_gaps || []).filter(f => !f.filled);
    if (fvgs.length) out.push({ id: `structure_${k}_fvg`, group: tag, label: `FVGs (${fvgs.length})`, tone, color,
      bands: fvgs.map(f => ({ top: f.top, bottom: f.bottom, label: `${tag} FVG`, color: f.type === 'bullish' ? GREEN : RED })), json: { indicator: 'fair_value_gaps', timeframe: k, gaps: fvgs } });
  }
  return out;
}

// ── Liquidity (resting stop pools + MTF confluence), grouped Daily · 4H · 1H ──
export function liquidityLayers(d: MarketStructureData): TALayer[] {
  const out: TALayer[] = [];
  for (const [k, tag, color, tone] of TF_TIERS) {
    const tf = d.timeframes[k] as TimeframeBlock | null;
    if (!tf) continue;
    const pools = tf.liquidity_pools || [];
    if (pools.length) out.push({ id: `liquidity_${k}_pools`, group: tag, label: `Pools (${pools.length})`, tone, color, points: pools.map(p => p.price),
      lines: pools.map(p => ({ price: p.price, label: `${p.type} $${p.price}`, color, dash: [6, 3] })), json: { indicator: 'liquidity_pools', timeframe: k, pools } });
  }
  const cf = d.confluence || [];
  if (cf.length) out.push({ id: 'liquidity_confluence', group: 'Confluence', label: `MTF Confluence (${cf.length})`, tone: 'text-emerald-400', color: 'rgb(16,185,129)',
    bands: cf.map(c => ({ top: c.zone[1], bottom: c.zone[0], label: `${c.bias} confluence`, color: c.bias === 'bullish' ? 'rgb(16,185,129)' : 'rgb(244,63,94)' })), json: { indicator: 'mtf_confluence', zones: cf } });
  return out;
}

// ── Regime — the classifications are STATUS (badges); only the 50-day VWAP is drawable ──
export function regimeLayers(d: RegimeData): TALayer[] {
  const out: TALayer[] = [];
  for (const [k, tag] of [['daily', 'Daily'], ['h4', '4H']] as const) {
    const tf = d.timeframes[k];
    if (!tf) continue;
    out.push({ id: `regime_${k}`, group: 'Regime state', label: `${tag}: ${tf.regime.replace('_', '-')}`,
      tone: tf.regime === 'trending' ? 'text-success' : tf.regime === 'mean_reverting' ? 'text-info' : 'text-warning',
      color: 'rgb(148,163,184)', status: true, json: { indicator: 'regime', timeframe: k, regime: tf.regime, hurst: tf.hurst, efficiency_ratio: tf.efficiency_ratio, confidence: tf.confidence } });
  }
  if (d.zscore?.vwap != null) {
    const z = d.zscore;
    out.push({ id: 'regime_vwap', group: 'Levels', label: `50-day VWAP (z ${z.z})`, tone: 'text-amber-400', color: 'rgb(251,191,36)', price: z.vwap!,
      lines: [{ price: z.vwap!, label: `50d VWAP (z ${z.z})`, color: 'rgb(251,191,36)', dash: [6, 4] }], json: { indicator: 'vwap_zscore', vwap: z.vwap, z: z.z, state: z.state } });
  }
  return out;
}

// ── Dealer Gamma — Net GEX is STATUS (badge); gamma flip / walls / expected-move are drawable ──
export function gammaLayers(d: DealerPositioningData): TALayer[] {
  const out: TALayer[] = [];
  out.push({ id: 'gamma_netgex', group: 'Dealer state', label: `Net GEX ${d.net_gex.value_millions ?? '—'}M · ${d.net_gex.sign} γ`,
    tone: d.net_gex.sign === 'long' ? 'text-info' : 'text-warning', color: 'rgb(148,163,184)', status: true,
    json: { indicator: 'net_gex', value_millions: d.net_gex.value_millions, sign: d.net_gex.sign, label: d.net_gex.label } });
  if (d.gamma_flip?.level != null) out.push({ id: 'gamma_flip', group: 'Key levels', label: `γ-flip $${d.gamma_flip.level}`, tone: 'text-amber-400', color: 'rgb(251,191,36)', price: d.gamma_flip.level,
    lines: [{ price: d.gamma_flip.level, label: `γ-flip $${d.gamma_flip.level}`, color: 'rgb(251,191,36)', dash: [1, 2] }], json: { indicator: 'gamma_flip', level: d.gamma_flip.level } });
  const gl = d.gamma_levels;
  const add = (id: string, lvl: { strike: number | null } | null | undefined, name: string, color: string, tone: string) => {
    if (!lvl?.strike) return;
    out.push({ id, group: 'Key levels', label: `${name} $${lvl.strike}`, tone, color, price: lvl.strike,
      lines: [{ price: lvl.strike, label: `${name} $${lvl.strike}`, color, dash: [5, 3] }], json: { indicator: id, strike: lvl.strike } });
  };
  if (gl) { add('gamma_call_res', gl.call_resistance, 'Call Resistance', RED, 'text-error'); add('gamma_put_sup', gl.put_support, 'Put Support', GREEN, 'text-success'); add('gamma_hvl', gl.hvl, 'HVL', 'rgb(139,92,246)', 'text-violet-400'); }
  else { if (d.walls.call_wall?.strike != null) add('gamma_call_res', { strike: d.walls.call_wall.strike }, 'Call Resistance', RED, 'text-error'); if (d.walls.put_wall?.strike != null) add('gamma_put_sup', { strike: d.walls.put_wall.strike }, 'Put Support', GREEN, 'text-success'); }
  const em = d.expected_move;
  const emL = (id: string, e: typeof em.em_30d, color: string, tag: string) => {
    if (!e || e.upper == null || e.lower == null) return;
    out.push({ id, group: 'Expected move', label: `${tag} EM ±${e.move_pct}%`, tone: 'text-sky-400', color, price: (e.upper + e.lower) / 2,
      bands: [{ top: e.upper, bottom: e.lower, label: `${tag} ±1σ`, color }], json: { indicator: 'expected_move', horizon: tag, upper: e.upper, lower: e.lower } });
  };
  emL('gamma_em30', em.em_30d, 'rgb(56,189,248)', '30d'); emL('gamma_em45', em.em_45d, 'rgb(139,92,246)', '45d');
  return out;
}

export function taToOverlays(sel: TALayer[]): { lines: OverlayLine[]; bands: OverlayBand[] } {
  return { lines: sel.flatMap(l => l.lines || []), bands: sel.flatMap(l => l.bands || []) };
}
export function taToProx(all: TALayer[]): ProxItem[] {
  const items: ProxItem[] = [];
  for (const l of all) {
    if (l.status) continue;
    if (l.points?.length) l.points.forEach(p => items.push({ id: l.id, price: p, label: `${l.label.split(' (')[0]} $${p}`, color: l.color, json: l.json }));
    else if (l.price != null) items.push({ id: l.id, price: l.price, label: l.label, color: l.color, json: l.json });
  }
  return items;
}
