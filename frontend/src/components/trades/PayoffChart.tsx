/**
 * PayoffChart — expiry & mark-to-market P&L vs the underlying price.
 *
 * Answers "what happens if the underlying moves ±5/10/15/20/25%": plots the
 * structure's P&L at expiry (intrinsic) and now (BS-priced) across the backend
 * `scenarios` grid, and marks the max-profit / max-loss price points, the
 * breakevens (zero crossings), and where spot sits today.
 */
import React, { useMemo } from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement, LineElement,
  Tooltip, Legend, Filler,
} from 'chart.js';
import type { LivePnlResponse } from '../../api';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Legend, Filler);

const money = (v: number) => `${v < 0 ? '-' : ''}$${Math.abs(Math.round(v)).toLocaleString()}`;

export default function PayoffChart({ pnl }: { pnl: LivePnlResponse }) {
  const scenarios = pnl.scenarios || [];

  const model = useMemo(() => {
    if (scenarios.length < 2) return null;
    const spot = pnl.underlying_price;
    const prices = scenarios.map(s => s.price);
    const expiry = scenarios.map(s => s.pnl_at_expiry);
    const now = scenarios.map(s => s.pnl_now);

    const nearest = (target: number) => {
      let idx = 0;
      prices.forEach((p, i) => { if (Math.abs(p - target) < Math.abs(prices[idx] - target)) idx = i; });
      return idx;
    };

    // Mark max profit/loss at the EXACT structural price points from the backend
    // (fall back to the window extremes only if those aren't provided).
    let iMax: number, iMin: number;
    if (pnl.max_profit_price != null) iMax = nearest(pnl.max_profit_price);
    else { iMax = 0; expiry.forEach((v, i) => { if (v > expiry[iMax]) iMax = i; }); }
    if (pnl.max_loss_price != null) iMin = nearest(pnl.max_loss_price);
    else { iMin = 0; expiry.forEach((v, i) => { if (v < expiry[iMin]) iMin = i; }); }

    // Breakevens = zero crossings of the expiry curve (interpolated).
    const bes: number[] = [];
    for (let i = 0; i < expiry.length - 1; i++) {
      const a = expiry[i], b = expiry[i + 1];
      if ((a <= 0 && b > 0) || (a >= 0 && b < 0)) {
        const t = a / (a - b);
        bes.push(prices[i] + t * (prices[i + 1] - prices[i]));
      }
    }
    const iSpot = nearest(spot);

    return { spot, prices, expiry, now, iMax, iMin, bes, iSpot };
  }, [scenarios, pnl.underlying_price, pnl.max_profit_price, pnl.max_loss_price]);

  if (!model) {
    return <div className="text-[10px] text-base-content/40 py-2">Payoff curve needs live quotes — hit Refresh P&L.</div>;
  }

  const { prices, expiry, now, iMax, iMin, bes, iSpot } = model;
  const cssVar = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || undefined;

  const data = {
    labels: prices.map(p => `$${p.toFixed(0)}`),
    datasets: [
      {
        label: 'P&L at expiry',
        data: expiry,
        borderColor: 'rgb(34,197,94)',
        segment: {
          borderColor: (ctx: any) => ((ctx.p0.parsed.y + ctx.p1.parsed.y) / 2 >= 0 ? 'rgb(34,197,94)' : 'rgb(239,68,68)'),
        },
        borderWidth: 2,
        pointRadius: (ctx: any) => ([iMax, iMin, iSpot].includes(ctx.dataIndex) ? 4 : 0),
        pointBackgroundColor: (ctx: any) =>
          ctx.dataIndex === iMax ? 'rgb(34,197,94)' :
          ctx.dataIndex === iMin ? 'rgb(239,68,68)' :
          ctx.dataIndex === iSpot ? cssVar('--p') || 'rgb(99,102,241)' : 'transparent',
        tension: 0.1,
        fill: false,
      },
      {
        label: 'P&L now',
        data: now,
        borderColor: 'rgba(148,163,184,0.7)',
        borderWidth: 1,
        borderDash: [4, 3],
        pointRadius: 0,
        tension: 0.25,
        fill: false,
      },
      {
        label: 'Break-even',
        data: prices.map(() => 0),
        borderColor: 'rgba(148,163,184,0.35)',
        borderWidth: 1,
        pointRadius: 0,
      },
    ],
  };

  const options: any = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { labels: { boxWidth: 10, font: { size: 9 }, color: 'rgba(148,163,184,0.8)' } },
      tooltip: {
        callbacks: {
          title: (items: any) => {
            const i = items[0].dataIndex;
            const pct = ((prices[i] / model.spot - 1) * 100).toFixed(1);
            return `Underlying $${prices[i].toFixed(2)} (${Number(pct) >= 0 ? '+' : ''}${pct}%)`;
          },
          label: (item: any) => `${item.dataset.label}: ${money(item.parsed.y)}`,
        },
      },
    },
    scales: {
      x: {
        ticks: { maxTicksLimit: 13, font: { size: 8 }, color: 'rgba(148,163,184,0.6)' },
        grid: { display: false },
      },
      y: {
        ticks: { font: { size: 8 }, color: 'rgba(148,163,184,0.6)', callback: (v: any) => money(v) },
        grid: { color: 'rgba(148,163,184,0.1)' },
      },
    },
  };

  // Entire-trade extremes (from the backend structural payoff, not the window).
  const maxGainTxt = pnl.unbounded_profit ? 'Unlimited'
    : pnl.max_profit != null ? `${money(pnl.max_profit)}${pnl.max_profit_price != null ? ` @ $${pnl.max_profit_price.toFixed(0)}` : ''}`
    : `${money(expiry[iMax])} @ $${prices[iMax].toFixed(0)}`;
  const maxLossTxt = pnl.unbounded_loss ? 'Unlimited'
    : pnl.max_loss != null ? `${money(pnl.max_loss)}${pnl.max_loss_price != null ? ` @ $${pnl.max_loss_price.toFixed(0)}` : ''}`
    : `${money(expiry[iMin])} @ $${prices[iMin].toFixed(0)}`;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between flex-wrap gap-1">
        <div className="text-[9px] uppercase tracking-wider text-base-content/30">Payoff — P&L vs underlying (whole trade)</div>
        <div className="flex gap-2 text-[9px] text-base-content/50">
          <span>● <span className="text-success/90 font-medium">Max gain {maxGainTxt}</span></span>
          <span>● <span className="text-error/90 font-medium">Max loss {maxLossTxt}</span></span>
        </div>
      </div>
      <div style={{ height: 200 }}>
        <Line data={data} options={options} />
      </div>
      <div className="flex flex-wrap gap-1.5">
        <span className="badge badge-ghost badge-xs text-[9px] text-success/80">
          Max profit {pnl.unbounded_profit ? 'Unlimited' : money(pnl.max_profit ?? expiry[iMax])}
        </span>
        <span className="badge badge-ghost badge-xs text-[9px] text-error/80">
          Max loss {pnl.unbounded_loss ? 'Unlimited' : money(pnl.max_loss ?? expiry[iMin])}
        </span>
        {bes.map((b, i) => (
          <span key={i} className="badge badge-ghost badge-xs text-[9px] text-base-content/60">Breakeven ${b.toFixed(2)}</span>
        ))}
        <span className="badge badge-ghost badge-xs text-[9px] text-primary/70">Now ${model.spot.toFixed(2)}</span>
      </div>
    </div>
  );
}
