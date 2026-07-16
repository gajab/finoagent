import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Chart as ChartJS, LinearScale, PointElement, LineElement,
  Tooltip, Legend, Title, ScatterController,
} from 'chart.js';
import { Chart } from 'react-chartjs-2';
import {
  Loader2, RefreshCw, AlertCircle, Scale, Sparkles, Info,
  ArrowRight, Target, TrendingDown, SlidersHorizontal, RotateCcw, Repeat,
} from 'lucide-react';
import { fetchPortfolioOptimization } from '../api';
import type { OptimizeOpts } from '../api';
import type { PortfolioOptimization } from '../types';

ChartJS.register(ScatterController, LinearScale, PointElement, LineElement, Tooltip, Legend, Title);

type MethodKey = 'min_volatility' | 'hrp';

const pct = (v?: number | null, d = 1) => (v == null ? '—' : `${v.toFixed(d)}%`);
const money = (v?: number | null) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

const ACTION_CLS: Record<string, string> = {
  BUY: 'text-success', SELL: 'text-error', HOLD: 'text-base-content/40',
};

/** Current → target stat, coloured by whether the move is favourable. */
function DeltaStat({ label, cur, tgt, unit, lowerIsBetter }: {
  label: string; cur?: number | null; tgt?: number | null; unit: string; lowerIsBetter?: boolean;
}) {
  const improved = cur != null && tgt != null
    ? (lowerIsBetter ? tgt < cur : tgt > cur)
    : null;
  const fmt = (v?: number | null) => (v == null ? '—' : `${v.toFixed(2)}${unit}`);
  return (
    <div className="glass-card p-3.5">
      <div className="text-xs font-medium text-base-content/40 uppercase tracking-wider mb-1">{label}</div>
      <div className="flex items-center gap-2">
        <span className="text-base-content/50 text-sm font-mono">{fmt(cur)}</span>
        <ArrowRight className="w-3.5 h-3.5 text-base-content/30" />
        <span className={`text-lg font-black tracking-tight ${improved == null ? '' : improved ? 'text-success' : 'text-warning'}`}>
          {fmt(tgt)}
        </span>
      </div>
    </div>
  );
}

export function PortfolioOptimizer() {
  const [data, setData] = useState<PortfolioOptimization | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [method, setMethod] = useState<MethodKey>('min_volatility');

  // Constraint inputs (percent / bps as the user types them).
  const [maxWeightPct, setMaxWeightPct] = useState('');
  const [sectorMaxPct, setSectorMaxPct] = useState('');
  const [costBps, setCostBps] = useState('');

  // Stable loader — takes explicit opts so typing in inputs never re-fetches.
  const load = useCallback(async (opts: OptimizeOpts) => {
    opts.forceRefresh ? setRefreshing(true) : setLoading(true);
    setError(null);
    try {
      const res = await fetchPortfolioOptimization(opts);
      setData(res);
      if (res.available && res.targets && !res.targets.min_volatility && res.targets.hrp) {
        setMethod('hrp');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to optimize portfolio');
    } finally {
      setLoading(false); setRefreshing(false);
    }
  }, []);

  // Read the current inputs into an opts object (called at click time, never stale).
  const currentOpts = (force: boolean): OptimizeOpts => ({
    forceRefresh: force,
    maxWeight: maxWeightPct ? Number(maxWeightPct) / 100 : null,
    sectorMax: sectorMaxPct ? Number(sectorMaxPct) / 100 : null,
    transactionCostBps: costBps ? Number(costBps) : 0,
  });

  const resetConstraints = () => {
    setMaxWeightPct(''); setSectorMaxPct(''); setCostBps('');
    load({ forceRefresh: false });
  };

  useEffect(() => { load({}); }, [load]);   // initial: unconstrained baseline

  const target = data?.available ? data.targets?.[method] : undefined;
  const settings = data?.settings;
  const hasConstraints = !!(settings && (settings.max_weight != null || settings.sector_max != null || settings.transaction_cost_bps));

  const chart = useMemo(() => {
    if (!data?.available) return null;
    const frontier = (data.frontier ?? [])
      .filter(p => p.volatility != null && p.expected_return != null)
      .map(p => ({ x: p.volatility as number, y: p.expected_return as number }))
      .sort((a, b) => a.x - b.x);
    const cur = data.current;
    const ms = data.markers?.max_sharpe;

    const datasets: any[] = [{
      label: 'Efficient frontier',
      data: frontier,
      showLine: true, borderColor: 'rgba(148,163,184,0.7)', borderWidth: 2,
      pointRadius: 0, tension: 0, order: 4,
    }];
    if (cur?.volatility != null && cur?.expected_return != null) {
      datasets.push({
        label: 'Your portfolio (today)',
        data: [{ x: cur.volatility, y: cur.expected_return }],
        backgroundColor: 'rgba(245,158,11,0.95)', pointRadius: 8, pointHoverRadius: 10, order: 1,
      });
    }
    if (target?.volatility != null && target?.expected_return != null) {
      datasets.push({
        label: target.label,
        data: [{ x: target.volatility, y: target.expected_return }],
        backgroundColor: 'rgba(34,197,94,0.95)', pointRadius: 8, pointHoverRadius: 10, order: 1,
      });
    }
    if (ms?.volatility != null && ms?.expected_return != null) {
      datasets.push({
        label: 'Max-Sharpe',
        data: [{ x: ms.volatility, y: ms.expected_return }],
        backgroundColor: 'rgba(59,130,246,0.9)', pointRadius: 6, order: 2,
      });
    }
    return { datasets };
  }, [data, target]);

  // ── Weights: union of current + target names, sorted by target weight ──
  const weightRows = useMemo(() => {
    if (!data?.available || !target) return [];
    const cw = data.current?.weights ?? {};
    const tw = target.weights ?? {};
    const tickers = Array.from(new Set([...Object.keys(cw), ...Object.keys(tw)]));
    return tickers
      .map(t => ({ ticker: t, cur: cw[t] ?? 0, tgt: tw[t] ?? 0 }))
      .sort((a, b) => b.tgt - a.tgt);
  }, [data, target]);

  // ── Loading / error / unavailable states ──
  if (loading) {
    return (
      <div className="glass-card p-8 flex flex-col items-center gap-3">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
        <p className="text-sm text-base-content/50">Pulling 2y history and solving the frontier…</p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="glass-card p-4 border-error/20 flex items-center gap-3">
        <AlertCircle className="w-4 h-4 text-error flex-shrink-0" />
        <span className="text-sm text-error flex-1">{error}</span>
        <button className="btn btn-ghost btn-xs rounded-lg gap-1" onClick={() => load(currentOpts(true))}>
          <RefreshCw className="w-3 h-3" /> Retry
        </button>
      </div>
    );
  }
  if (data && !data.available) {
    return (
      <div className="glass-card p-5 space-y-3">
        <div className="flex items-start gap-3">
          <Info className="w-5 h-5 text-info flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="font-bold text-sm">Optimizer needs a bit more to work with</h3>
            <p className="text-sm text-base-content/60 mt-1">{data.reason}</p>
          </div>
        </div>
        {data.excluded?.length > 0 && (
          <div className="text-xs text-base-content/50">
            Skipped: {data.excluded.map(e => `${e.ticker} (${e.reason})`).join(', ')}
          </div>
        )}
      </div>
    );
  }
  if (!data?.available || !target) return null;

  const isMinVol = method === 'min_volatility';

  return (
    <div className="space-y-5">
      {/* Intro + controls */}
      <div className="glass-card p-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary/20 to-secondary/10 flex items-center justify-center flex-shrink-0">
            <Target className="w-4.5 h-4.5 text-primary" />
          </div>
          <div>
            <h3 className="font-bold text-sm">Portfolio Optimizer</h3>
            <p className="text-xs text-base-content/50 mt-0.5">
              {data.tickers?.length} names · {money(data.total_value)} · {data.lookback} history · rf {pct(data.risk_free_rate)} · as of {data.as_of}
            </p>
          </div>
        </div>
        <button
          className="btn btn-ghost btn-sm rounded-xl gap-2 border border-white/[0.06] self-start"
          onClick={() => load(currentOpts(true))}
          disabled={refreshing}
        >
          <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} /> Recompute
        </button>
      </div>

      {/* Constraints (min-vol only) */}
      <div className="glass-card p-4">
        <div className="flex items-center gap-2 mb-3">
          <SlidersHorizontal className="w-4 h-4 text-primary" />
          <h4 className="text-sm font-bold">Constraints & turnover</h4>
          <span className="text-xs text-base-content/40">applies to Minimum Volatility</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 items-end">
          <div>
            <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block">Max / name (%)</label>
            <input type="number" min={1} max={100} placeholder="off"
              className="input input-bordered input-sm w-full rounded-xl bg-base-200/50 border-white/[0.06]"
              value={maxWeightPct} onChange={e => setMaxWeightPct(e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block flex items-center gap-1">
              Max / sector (%)
              {settings && !settings.sectors_available && sectorMaxPct !== '' && (
                <span className="text-warning" title="No cached sector data — cap won't bind. Open the Fundamentals tab first.">⚠</span>
              )}
            </label>
            <input type="number" min={1} max={100} placeholder="off"
              className="input input-bordered input-sm w-full rounded-xl bg-base-200/50 border-white/[0.06]"
              value={sectorMaxPct} onChange={e => setSectorMaxPct(e.target.value)} />
          </div>
          <div>
            <label className="text-xs font-semibold text-base-content/50 uppercase tracking-wider mb-1 block">Turnover cost (bps)</label>
            <input type="number" min={0} step={5} placeholder="0"
              className="input input-bordered input-sm w-full rounded-xl bg-base-200/50 border-white/[0.06]"
              value={costBps} onChange={e => setCostBps(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <button className="btn btn-primary btn-sm rounded-xl gap-2 flex-1" onClick={() => load(currentOpts(false))} disabled={refreshing}>
              {refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <SlidersHorizontal className="w-4 h-4" />} Apply
            </button>
            <button className="btn btn-ghost btn-sm btn-square rounded-xl border border-white/[0.06]" onClick={resetConstraints} title="Clear constraints">
              <RotateCcw className="w-4 h-4" />
            </button>
          </div>
        </div>
        {hasConstraints && (
          <div className="flex flex-wrap gap-1.5 mt-3">
            {settings?.max_weight != null && <span className="badge badge-sm badge-ghost">≤ {settings.max_weight}% / name</span>}
            {settings?.sector_max != null && <span className="badge badge-sm badge-ghost">≤ {settings.sector_max}% / sector</span>}
            {!!settings?.transaction_cost_bps && <span className="badge badge-sm badge-ghost">{settings.transaction_cost_bps} bps turnover cost</span>}
          </div>
        )}
      </div>

      {/* Method toggle */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          className={`btn btn-sm rounded-xl gap-2 ${isMinVol ? 'btn-primary' : 'btn-ghost border border-white/[0.06]'}`}
          onClick={() => setMethod('min_volatility')}
          disabled={!data.targets?.min_volatility}
        >
          <TrendingDown className="w-4 h-4" /> Minimum Volatility
        </button>
        <button
          className={`btn btn-sm rounded-xl gap-2 ${!isMinVol ? 'btn-primary' : 'btn-ghost border border-white/[0.06]'}`}
          onClick={() => setMethod('hrp')}
          disabled={!data.targets?.hrp}
        >
          <Scale className="w-4 h-4" /> Hierarchical Risk Parity
        </button>
        {!isMinVol && hasConstraints && (
          <span className="text-xs text-base-content/40">HRP ignores the constraints above</span>
        )}
      </div>

      {/* Constraint adjustment notices */}
      {isMinVol && target.adjustments && target.adjustments.length > 0 && (
        <div className="glass-card p-3 border-warning/20 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-warning flex-shrink-0 mt-0.5" />
          <div className="text-xs text-warning/90 space-y-0.5">
            {target.adjustments.map((a, i) => <div key={i}>{a}</div>)}
          </div>
        </div>
      )}

      {/* Current → target stats + turnover */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <DeltaStat label="Volatility" cur={data.current?.volatility} tgt={target.volatility} unit="%" lowerIsBetter />
        <DeltaStat label="Expected Return" cur={data.current?.expected_return} tgt={target.expected_return} unit="%" />
        <DeltaStat label="Sharpe" cur={data.current?.sharpe} tgt={target.sharpe} unit="" />
        <div className="glass-card p-3.5">
          <div className="text-xs font-medium text-base-content/40 uppercase tracking-wider mb-1 flex items-center gap-1">
            <Repeat className="w-3 h-3" /> Turnover
          </div>
          <div className="text-lg font-black tracking-tight">{pct(target.turnover)}</div>
          <div className="text-xs text-base-content/40 mt-0.5">of book traded</div>
        </div>
      </div>

      {/* Efficient frontier chart */}
      <div className="glass-card p-4">
        <h4 className="text-sm font-bold mb-1 flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-primary" /> Efficient frontier
        </h4>
        <p className="text-xs text-base-content/50 mb-3">
          Your book sits <span className="text-warning font-semibold">inside</span> the curve; the optimizer moves it toward the
          <span className="text-success font-semibold"> frontier</span> — same risk for more return, or same return for less risk.
        </p>
        <div className="h-72 w-full relative">
          {chart && (
            <Chart
              type="scatter"
              data={chart}
              options={{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: { position: 'top', labels: { usePointStyle: true, boxWidth: 8, font: { size: 11 } } },
                  tooltip: {
                    callbacks: {
                      label: (ctx: any) =>
                        `${ctx.dataset.label}: risk ${ctx.parsed.x?.toFixed(1)}% · return ${ctx.parsed.y?.toFixed(1)}%`,
                    },
                  },
                },
                scales: {
                  x: {
                    type: 'linear', position: 'bottom',
                    title: { display: true, text: 'Risk — annualized volatility (%)' },
                    grid: { color: 'rgba(148,163,184,0.1)' },
                  },
                  y: {
                    title: { display: true, text: 'Expected return (%)' },
                    grid: { color: 'rgba(148,163,184,0.1)' },
                  },
                },
              }}
            />
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Weights: current vs target */}
        <div className="glass-card p-4">
          <h4 className="text-sm font-bold mb-3">Weights — current vs {target.label}</h4>
          <div className="overflow-x-auto max-h-80 overflow-y-auto">
            <table className="table table-xs w-full">
              <thead>
                <tr className="text-base-content/40">
                  <th>Ticker</th>
                  <th className="text-right">Current</th>
                  <th className="text-right">Target</th>
                  <th className="text-right">Δ</th>
                </tr>
              </thead>
              <tbody>
                {weightRows.map(r => {
                  const delta = r.tgt - r.cur;
                  return (
                    <tr key={r.ticker} className="hover">
                      <td className="font-bold">{r.ticker}</td>
                      <td className="text-right font-mono text-base-content/50">{pct(r.cur)}</td>
                      <td className="text-right font-mono font-semibold">{pct(r.tgt)}</td>
                      <td className={`text-right font-mono ${Math.abs(delta) < 0.05 ? 'text-base-content/30' : delta > 0 ? 'text-success' : 'text-error'}`}>
                        {delta > 0 ? '+' : ''}{delta.toFixed(1)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Rebalance trade list */}
        <div className="glass-card p-4">
          <h4 className="text-sm font-bold mb-1">Rebalancing trades</h4>
          <p className="text-xs text-base-content/50 mb-3">
            Whole-share deltas to move today's book to the target. Leftover cash: {money(target.leftover_cash)}
          </p>
          <div className="overflow-x-auto max-h-80 overflow-y-auto">
            <table className="table table-xs w-full">
              <thead>
                <tr className="text-base-content/40">
                  <th>Action</th><th>Ticker</th><th className="text-right">Shares</th><th className="text-right">Price</th>
                </tr>
              </thead>
              <tbody>
                {target.trades.filter(t => t.action !== 'HOLD').map(t => (
                  <tr key={t.ticker} className="hover">
                    <td><span className={`font-bold text-xs ${ACTION_CLS[t.action]}`}>{t.action}</span></td>
                    <td className="font-bold">{t.ticker}</td>
                    <td className="text-right font-mono">
                      <span className="text-base-content/40">{t.current_shares}</span>
                      <ArrowRight className="w-3 h-3 inline mx-1 text-base-content/30" />
                      <span className="font-semibold">{t.target_shares}</span>
                    </td>
                    <td className="text-right font-mono text-base-content/50">{money(t.price)}</td>
                  </tr>
                ))}
                {target.trades.filter(t => t.action !== 'HOLD').length === 0 && (
                  <tr><td colSpan={4} className="text-center text-base-content/40 py-3">No trades — already at target (turnover cost held it in place).</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Excluded + disclaimer */}
      {data.excluded?.length > 0 && (
        <div className="text-xs text-base-content/40">
          Excluded from optimization: {data.excluded.map(e => `${e.ticker} (${e.reason})`).join(', ')}
        </div>
      )}
      <p className="text-[11px] text-base-content/30 leading-relaxed">
        Educational analytics, not investment advice. Expected returns use CAPM (beta to SPY over {data.lookback}) so they stay
        realistic — and min-vol/HRP don't use them for weights anyway. Max-Sharpe is a return-estimate-driven reference point, not
        a recommendation. Constraints and turnover cost apply to minimum-volatility only. Excludes options, bonds and cash.
      </p>
    </div>
  );
}
