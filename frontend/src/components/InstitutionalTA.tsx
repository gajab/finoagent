import React, { useState } from 'react';
import {
  TrendingUp, TrendingDown, Minus, Layers, Waves, Zap, ArrowRightLeft,
  Info, ChevronDown, ChevronUp, Target, HelpCircle,
} from 'lucide-react';
import type {
  InstitutionalTA as InstTA, TARegime, VolumeProfile, OrderBlock, FairValueGap,
  LiquiditySweep, MarketStructure,
} from '../types';

const money = (n?: number | null) => (n == null ? '—' : `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
const distLabel = (level: number, spot: number) => {
  const d = spot > 0 ? ((level - spot) / spot) * 100 : 0;
  return `${d >= 0 ? '+' : ''}${d.toFixed(1)}%`;
};
const biasTone = (b: string) =>
  b === 'bullish' ? 'text-success border-success/30 bg-success/10'
    : b === 'bearish' ? 'text-error border-error/30 bg-error/10'
      : 'text-warning border-warning/30 bg-warning/10';

// ── 1. Market State — the plain-English, actionable capstone ──
function MarketStateBanner({ regime }: { regime: TARegime }) {
  const Icon = regime.bias === 'bullish' ? TrendingUp : regime.bias === 'bearish' ? TrendingDown : Minus;
  const modeLabel = regime.mode === 'mean_reversion' ? 'Mean-Reversion'
    : regime.mode === 'trend' ? 'Trend-Following' : 'Range / Balanced';
  return (
    <div className={`rounded-xl border p-4 ${biasTone(regime.bias)}`}>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <Icon className="w-5 h-5 shrink-0" />
          <div>
            <p className="text-base font-bold leading-tight">{regime.state}</p>
            <p className="text-[11px] uppercase tracking-wider opacity-70">{modeLabel} · {regime.bias} bias</p>
          </div>
        </div>
        {regime.favored_income_labels?.length > 0 && (
          <div className="flex flex-wrap gap-1.5 items-center">
            <span className="text-[10px] uppercase tracking-wider opacity-60">Favored income:</span>
            {regime.favored_income_labels.map((l, i) => (
              <span key={i} className="badge badge-sm bg-base-100/60 border-base-content/10">{l}</span>
            ))}
          </div>
        )}
      </div>
      <p className="text-xs text-base-content/70 mt-2">{regime.rationale}</p>
    </div>
  );
}

// ── 2. Volume Profile — where volume actually traded (POC + Value Area) ──
function VolumeProfileView({ vp, spot }: { vp: VolumeProfile; spot: number }) {
  const maxPct = Math.max(...vp.bins.map(b => b.pct), 1);
  const bins = [...vp.bins].reverse(); // high price at the top
  const spotIdx = bins.reduce((best, b, i) => Math.abs(b.price - spot) < Math.abs(bins[best].price - spot) ? i : best, 0);
  const inVA = (p: number) => p >= vp.val && p <= vp.vah;
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/20 p-3">
      <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
        <p className="text-xs font-bold flex items-center gap-1.5"><Layers className="w-4 h-4 text-secondary" /> Volume Profile</p>
        <div className="flex gap-3 text-[10px] text-base-content/60">
          <span><b className="text-secondary">POC</b> {money(vp.poc)}</span>
          <span>Value Area {money(vp.val)}–{money(vp.vah)}</span>
        </div>
      </div>
      <div className="space-y-[2px]">
        {bins.map((b, i) => {
          const isPoc = Math.abs(b.price - vp.poc) < 1e-6;
          const va = inVA(b.price);
          return (
            <div key={i} className="flex items-center gap-2 text-[10px]">
              <span className="w-14 text-right tabular-nums text-base-content/50">{money(b.price)}</span>
              <div className="flex-1 h-3 bg-base-300/20 rounded-sm overflow-hidden">
                <div className={`h-full rounded-sm ${isPoc ? 'bg-secondary' : va ? 'bg-secondary/40' : 'bg-base-content/20'}`}
                  style={{ width: `${(b.pct / maxPct) * 100}%` }} />
              </div>
              <span className="w-14 text-[9px] font-bold">
                {isPoc && <span className="text-secondary">POC</span>}
                {i === spotIdx && <span className="text-warning ml-1">← now</span>}
              </span>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-base-content/40 mt-2">
        <b>POC</b> = the price with the most volume, a fair-value magnet. <b>Value Area</b> = where 70% of volume traded.
        Above it reads "expensive" (rich to sell calls); below reads "cheap" (rich to sell puts).
      </p>
    </div>
  );
}

// ── 3. Smart-Money Zones — order blocks, FVGs, sweeps, displacement, structure ──
function ZoneRow({ icon, tone, title, level, spot, note }: {
  icon: React.ReactNode; tone: string; title: string; level?: number; spot: number; note: string;
}) {
  return (
    <div className="flex items-start gap-2 py-1.5">
      <span className={`mt-0.5 ${tone}`}>{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-semibold">{title}</span>
          {level != null && <span className="text-[11px] font-mono text-base-content/70">{money(level)}</span>}
          {level != null && <span className="text-[10px] text-base-content/40">({distLabel(level, spot)})</span>}
        </div>
        <p className="text-[11px] text-base-content/50 leading-snug">{note}</p>
      </div>
    </div>
  );
}

function SmartMoneyZones({ data, spot }: { data: InstTA; spot: number }) {
  const ms: MarketStructure = data.market_structure;
  const obs = data.order_blocks || [];
  const fvgs = data.fair_value_gaps || [];
  const sweeps = data.liquidity_sweeps || [];
  const disp = data.displacement || [];
  const trendTone = ms.trend === 'up' ? 'text-success' : ms.trend === 'down' ? 'text-error' : 'text-warning';

  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/20 p-3">
      <p className="text-xs font-bold flex items-center gap-1.5 mb-1"><Target className="w-4 h-4 text-secondary" /> Smart-Money Zones &amp; Structure</p>
      <div className="divide-y divide-white/[0.04]">
        {/* Market structure */}
        <ZoneRow icon={<ArrowRightLeft className="w-3.5 h-3.5" />} tone={trendTone}
          title={`Market structure — ${ms.trend === 'range' ? 'ranging' : ms.trend + '-trend'}${ms.change_of_character ? ' · CHoCH ⚠' : ''}`}
          level={ms.bos?.level} spot={spot}
          note={ms.bos ? `${ms.bos.type === 'bullish' ? 'Bullish' : 'Bearish'} break of structure — momentum ${ms.bos.type === 'bullish' ? 'up' : 'down'}${ms.change_of_character ? ', a change of character (possible reversal)' : ' (continuation)'}.`
            : 'No recent structure break — price is rotating between swings.'} />

        {/* Order blocks (institutional demand/supply) */}
        {obs.slice(0, 3).map((ob, i) => (
          <ZoneRow key={`ob${i}`} icon={<Layers className="w-3.5 h-3.5" />}
            tone={ob.type === 'bullish' ? 'text-success' : 'text-error'}
            title={`${ob.type === 'bullish' ? 'Demand' : 'Supply'} Order Block${ob.mitigated ? ' (used)' : ''}`}
            level={ob.price} spot={spot}
            note={`Institutions ${ob.type === 'bullish' ? 'bought' : 'sold'} heavily in ${money(ob.bottom)}–${money(ob.top)} (${ob.strength}× ATR move). ${ob.mitigated ? 'Already retested.' : `Price may ${ob.type === 'bullish' ? 'bounce from' : 'reject at'} it — a spot to place a short ${ob.type === 'bullish' ? 'put' : 'call'} strike beyond.`}`} />
        ))}

        {/* Fair value gaps */}
        {fvgs.slice(0, 2).map((f, i) => (
          <ZoneRow key={`fvg${i}`} icon={<Zap className="w-3.5 h-3.5" />}
            tone={f.type === 'bullish' ? 'text-success' : 'text-error'}
            title={`${f.filled ? 'Filled ' : 'Unfilled '}${f.type === 'bullish' ? 'Bullish' : 'Bearish'} Fair-Value Gap`}
            level={f.mid} spot={spot}
            note={`Price imbalance ${money(f.bottom)}–${money(f.top)}. ${f.filled ? 'Already rebalanced.' : 'Price often returns to "fill" this gap before continuing — a magnet level.'}`} />
        ))}

        {/* Liquidity sweeps */}
        {sweeps.slice(0, 2).map((s, i) => (
          <ZoneRow key={`sw${i}`} icon={<Waves className="w-3.5 h-3.5" />}
            tone={s.type === 'buyside' ? 'text-error' : 'text-success'}
            title={`${s.type === 'buyside' ? 'Buy-side' : 'Sell-side'} Liquidity Sweep`}
            level={s.level} spot={spot}
            note={`Stops were run ${s.type === 'buyside' ? 'above' : 'below'} ${money(s.level)} then price snapped back — a stop-hunt that often marks a ${s.type === 'buyside' ? 'top (fade up-moves)' : 'bottom (fade down-moves)'}.`} />
        ))}

        {/* Displacement */}
        {disp.slice(0, 1).map((d, i) => (
          <ZoneRow key={`disp${i}`} icon={<Zap className="w-3.5 h-3.5" />}
            tone={d.direction === 'up' ? 'text-success' : 'text-error'}
            title={`Recent Displacement — strong ${d.direction} move`} spot={spot}
            note={`A ${d.magnitude}× ATR candle shows high-conviction institutional intent ${d.direction === 'up' ? 'higher' : 'lower'} — confirms momentum.`} />
        ))}
      </div>
    </div>
  );
}

function HowToRead() {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-white/[0.06] bg-base-200/10">
      <button type="button" onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-[11px] font-semibold text-base-content/60">
        <span className="flex items-center gap-1.5"><HelpCircle className="w-3.5 h-3.5" /> How to read this (plain English)</span>
        {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
      </button>
      {open && (
        <div className="px-3 pb-3 text-[11px] text-base-content/60 space-y-1.5">
          <p>This is how institutional desks read a chart — not trendlines, but <b>where volume traded</b> and the <b>footprints of large orders</b>.</p>
          <p><b>POC / Value Area:</b> the price with the most volume, and the range holding 70% of it — the market's idea of "fair."</p>
          <p><b>Order Block:</b> where big players quietly built a position before a sharp move — a demand (buy) or supply (sell) zone price often revisits.</p>
          <p><b>Fair-Value Gap:</b> a price "gap" left by a violent move; price tends to return to fill it before continuing.</p>
          <p><b>Liquidity Sweep:</b> a fake move past a recent high/low that grabs stop orders then reverses — a classic reversal tell.</p>
          <p><b>Displacement / BOS:</b> an outsized candle / a break of a prior swing — signs of real momentum (trend), vs. rotating in a range (mean-reversion).</p>
          <p className="text-base-content/40 italic">Descriptive, not advice — use it to pick <em>where</em> to place option strikes and <em>which</em> income structure fits the state.</p>
        </div>
      )}
    </div>
  );
}

/**
 * Chart.js plugin that draws the smart-money zones directly on the price canvas:
 * Value-Area band + POC, Demand/Supply Order Blocks, unfilled Fair-Value Gaps, and
 * liquidity-sweep levels. Build it with the current institutional data and pass it via
 * the `plugins` prop of the price <Line>. Levels outside the visible y-range are skipped.
 */
export function makeSmartMoneyPlugin(inst?: InstTA | null): any {
  return {
    id: 'smartMoneyZones',
    afterDatasetsDraw(chart: any) {
      if (!inst) return;
      const { ctx, chartArea, scales } = chart;
      const y = scales?.y;
      if (!y || !chartArea) return;
      const L = chartArea.left, R = chartArea.right, W = R - L;
      const inRange = (p: number) => p >= y.min && p <= y.max;
      const py = (p: number) => Math.max(chartArea.top, Math.min(chartArea.bottom, y.getPixelForValue(p)));

      ctx.save();
      ctx.beginPath();
      ctx.rect(L, chartArea.top, W, chartArea.bottom - chartArea.top);
      ctx.clip();

      const band = (top: number, bot: number, fill: string) => {
        if (!inRange(top) && !inRange(bot)) return;
        const yt = py(top), yb = py(bot);
        ctx.fillStyle = fill;
        ctx.fillRect(L, Math.min(yt, yb), W, Math.max(2, Math.abs(yb - yt)));
      };
      const hline = (price: number, color: string, dash: number[], label: string) => {
        if (!inRange(price)) return;
        const yy = py(price);
        ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash);
        ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(R, yy); ctx.stroke(); ctx.setLineDash([]);
        ctx.font = '9px sans-serif'; ctx.fillStyle = color;
        ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        ctx.fillText(label, R - 3, yy - 1);
      };
      const tag = (top: number, color: string, label: string) => {
        if (!inRange(top)) return;
        const yy = py(top);
        ctx.font = '9px sans-serif'; ctx.fillStyle = color;
        ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        ctx.fillText(label, L + 3, yy + 1);
      };

      const vp = inst.volume_profile;
      if (vp) {
        band(vp.vah, vp.val, 'rgba(139,92,246,0.07)');           // value area
        hline(vp.poc, 'rgba(139,92,246,0.9)', [], 'POC');
      }
      (inst.order_blocks || []).filter(o => !o.mitigated).slice(0, 3).forEach(ob => {
        const bull = ob.type === 'bullish';
        band(ob.top, ob.bottom, bull ? 'rgba(34,197,94,0.13)' : 'rgba(239,68,68,0.13)');
        tag(ob.top, bull ? 'rgba(34,197,94,0.9)' : 'rgba(239,68,68,0.9)', bull ? 'Demand OB' : 'Supply OB');
      });
      (inst.fair_value_gaps || []).filter(f => !f.filled).slice(0, 2).forEach(f => {
        band(f.top, f.bottom, 'rgba(250,204,21,0.11)');
        tag(f.top, 'rgba(250,204,21,0.95)', 'FVG');
      });
      (inst.liquidity_sweeps || []).slice(0, 2).forEach(s => {
        const buy = s.type === 'buyside';
        hline(s.level, buy ? 'rgba(239,68,68,0.85)' : 'rgba(34,197,94,0.85)', [3, 3], buy ? 'Sweep ↑' : 'Sweep ↓');
      });
      ctx.restore();
    },
  };
}

// Compact legend for the on-chart overlays.
export function SmartMoneyChartLegend() {
  const item = (color: string, label: string) => (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: color }} /> {label}
    </span>
  );
  return (
    <p className="text-[10px] text-base-content/40 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
      <span className="text-base-content/50">On-chart zones:</span>
      {item('rgba(139,92,246,0.5)', 'Value Area / POC')}
      {item('rgba(34,197,94,0.5)', 'Demand OB')}
      {item('rgba(239,68,68,0.5)', 'Supply OB')}
      {item('rgba(250,204,21,0.6)', 'Fair-Value Gap')}
      <span>dashed = liquidity sweep</span>
    </p>
  );
}

export function InstitutionalTA({ data, price }: { data: InstTA; price?: number }) {
  const spot = price ?? data.price;
  return (
    <div className="space-y-3">
      <MarketStateBanner regime={data.regime} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {data.volume_profile && <VolumeProfileView vp={data.volume_profile} spot={spot} />}
        <SmartMoneyZones data={data} spot={spot} />
      </div>
      <HowToRead />
    </div>
  );
}

export default InstitutionalTA;
