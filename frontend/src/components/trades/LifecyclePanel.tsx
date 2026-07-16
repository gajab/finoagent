/**
 * LifecyclePanel — per-trade Continuous Lifecycle Management (Trader + PM).
 *
 *   Trader → Delta, Gamma, Vega, Theta, Vanna, Charm, Volga (delta-hedging)
 *   PM     → Omega, Sortino, Calmar, Expected return, PoP, EV (thesis quality)
 *
 * Risk is a book-level desk — it lives in the portfolio Risk Desk panel at the
 * top of My Trades, not on each trade. Each desk here runs an on-demand agent
 * that only prescribes action when something needs executing.
 */
import { useState } from 'react';
import { LineChart, Activity, Loader2, Play, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { runLifecycleAgent } from '../../api';
import type { LivePnlResponse, SavedStrategyItem, LifecycleAgentResult } from '../../api';
import CollapsibleSection from './CollapsibleSection';
import { TraderGrid, PmGrid } from './DeskMetrics';

type Role = 'trader' | 'pm';

export default function LifecyclePanel({ trade, pnl }: {
  trade: SavedStrategyItem;
  pnl: LivePnlResponse;
}) {
  const [running, setRunning] = useState<Role | null>(null);
  const [results, setResults] = useState<Partial<Record<Role, LifecycleAgentResult>>>({});
  const [err, setErr] = useState<string | null>(null);

  const t = pnl.lifecycle?.trader ?? {};
  const pm = pnl.lifecycle?.pm ?? {};
  const a = pnl.analysis;
  if (!pnl.lifecycle) return null;

  const ask = async (role: Role) => {
    setRunning(role); setErr(null);
    try {
      const r = await runLifecycleAgent(trade.id, role, pnl, null);
      setResults(s => ({ ...s, [role]: r }));
    } catch (e: any) {
      setErr(e?.message || `Failed to run ${role} agent`);
    } finally {
      setRunning(null);
    }
  };

  const AskButton = ({ role, label, accent }: { role: Role; label: string; accent: string }) => {
    const r = results[role];
    return (
      <span
        role="button"
        className={`btn btn-xs gap-1 ${r ? (r.action_needed ? 'btn-warning' : 'btn-success') : `btn-outline btn-${accent}`}`}
        onClick={(e) => { e.stopPropagation(); ask(role); }}
        title={`Run the ${label} agent on this trade`}
      >
        {running === role ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
        {r ? 'Re-run' : 'Ask'} {label}
      </span>
    );
  };

  const AgentResult = ({ role }: { role: Role }) => {
    const r = results[role];
    if (!r) return null;
    return (
      <div className={`mt-2 rounded-lg border p-2 ${r.action_needed ? 'border-warning/30 bg-warning/5' : 'border-success/30 bg-success/5'}`}>
        <div className="flex items-center gap-1.5 mb-1">
          {r.action_needed ? <AlertTriangle className="w-3 h-3 text-warning" /> : <CheckCircle2 className="w-3 h-3 text-success" />}
          <span className={`text-[10px] font-semibold uppercase tracking-wider ${r.action_needed ? 'text-warning' : 'text-success'}`}>
            {r.verdict || (r.action_needed ? 'Action needed' : 'OK')}
          </span>
          <span className="text-[9px] text-base-content/30">· {r.title} · {r.model}</span>
        </div>
        <div className="text-[11px] text-base-content/80 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto">
          {r.content}
        </div>
      </div>
    );
  };

  return (
    <div className="space-y-1.5">
      {err && <div className="text-[10px] text-error">{err}</div>}

      {/* Trader */}
      <CollapsibleSection
        title="Trader · dynamic Greeks" accent="info"
        icon={<Activity className="w-3 h-3" />}
        subtitle="Δ · Γ · ν · Θ · Vanna · Charm · Volga"
        badge={<AskButton role="trader" label="Trader" accent="info" />}
      >
        <TraderGrid t={{ ...t, avg_iv_pct: pnl.lifecycle.avg_iv_pct }} />
        <AgentResult role="trader" />
      </CollapsibleSection>

      {/* PM */}
      <CollapsibleSection
        title="PM · risk-adjusted quality" accent="success"
        icon={<LineChart className="w-3 h-3" />}
        subtitle="Omega · Sortino · Calmar · PoP · EV"
        badge={<AskButton role="pm" label="PM" accent="success" />}
      >
        <PmGrid pm={{ ...pm, pop: a?.probability_of_profit ?? null, expected_value: a?.expected_value ?? null, kelly_fraction: a?.kelly_fraction ?? null }} />
        <AgentResult role="pm" />
      </CollapsibleSection>
    </div>
  );
}
