/**
 * PreTradeAdvisor — the desk-review panels (Risk · Trader · PM · Quant) for a
 * structure the user is STILL EVALUATING (not yet placed). Shares the metric
 * grids with My Trades (DeskMetrics) and adds:
 *   Quant   → an ALGORITHMIC recommendation (non-LLM) from the whole payoff
 *             distribution — Omega · PoP · Sortino · CVaR · carry → ENTER/CONSIDER/AVOID
 *             (plus any pricing-confidence signals passed by the caller).
 *   Risk/Trader/PM → the same desk metric grids as My Trades + an "Ask" agent
 *             that reasons ONLY from its role's lens and calls out blind spots.
 *
 * `lazy` renders it collapsed; the (network) desk-metrics computation fires only
 * when the user expands it.
 */
import { useState, useEffect } from 'react';
import { Shield, LineChart, Activity, Cpu, Loader2, Play, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react';
import { runPreTradeAgent, fetchPreTradeMetrics } from '../api';
import type { LifecycleAgentResult, PreTradeLeg, PreTradeScenario, PreTradeDeskMetrics } from '../api';
import CollapsibleSection from './trades/CollapsibleSection';
import { TraderGrid, PmGrid, RiskGrid, Metric } from './trades/DeskMetrics';

export interface AdvisorMetric { label: string; value: string; hint?: string; tone?: 'good' | 'bad' | 'warn' | ''; }
export interface QuantSignal { label: string; value: string; sub?: string; }

type Role = 'risk' | 'pm' | 'trader';

export interface PreTradeAdvisorProps {
  ticker: string;
  strategyType: string;
  legs: PreTradeLeg[];
  llmMetrics: Record<string, string | number | null | undefined>;
  scenarios?: PreTradeScenario[];
  breakevens?: (string | number)[];
  // Inputs for the shared desk-metrics engine:
  expiration?: string | null;
  spot?: number;
  capital?: number;
  dte?: number;
  stockShares?: number;
  maxLoss?: number | null;
  maxProfit?: number | null;
  /** Pricing-confidence signals (RND/SVI/Heston/expected move…) merged into Quant. */
  quantSignals?: QuantSignal[];
  /** Fallback deterministic grid when no payoff curve/metrics are available. */
  quantMetrics?: AdvisorMetric[];
  quantVerdict?: { label: string; tone: 'good' | 'warn' | 'bad'; note?: string };
  notes?: string;
  disabled?: boolean;
  /** Render collapsed; compute lazily on first expand. */
  lazy?: boolean;
}

const toneClass = (t?: string) =>
  t === 'good' ? 'text-success' : t === 'bad' ? 'text-error' : t === 'warn' ? 'text-warning' : 'text-base-content/80';

function SubBar({ label, v }: { label: string; v: number }) {
  const col = v >= 66 ? 'bg-success' : v >= 45 ? 'bg-warning' : 'bg-error';
  return (
    <div className="flex-1 min-w-[52px]">
      <div className="flex justify-between text-[9px] text-base-content/40 mb-0.5"><span>{label}</span><span>{v}</span></div>
      <div className="h-1 rounded bg-base-300/40 overflow-hidden"><div className={`h-full ${col}`} style={{ width: `${Math.max(2, v)}%` }} /></div>
    </div>
  );
}

export default function PreTradeAdvisor(props: PreTradeAdvisorProps) {
  const {
    ticker, strategyType, legs, llmMetrics, scenarios, breakevens, notes, disabled, lazy,
    expiration, spot, capital, dte, stockShares, maxLoss, maxProfit, quantSignals, quantMetrics, quantVerdict,
  } = props;

  const [open, setOpen] = useState(!lazy);
  const [running, setRunning] = useState<Role | null>(null);
  const [results, setResults] = useState<Partial<Record<Role, LifecycleAgentResult>>>({});
  const [err, setErr] = useState<string | null>(null);
  const [desk, setDesk] = useState<PreTradeDeskMetrics | null>(null);
  const [deskLoading, setDeskLoading] = useState(false);

  // A payoff curve OR priced legs (backend can synthesize one) lets us compute the desk metrics.
  const hasPriced = legs.some(l => (l.price ?? 0) !== 0);
  const canComputeDesk = !!(spot && capital && dte && ((scenarios && scenarios.length >= 3) || hasPriced));
  const scenKey = scenarios ? JSON.stringify(scenarios) : '';
  const legKey = JSON.stringify(legs);

  useEffect(() => {
    if (!open || !canComputeDesk) { return; }
    let cancelled = false;
    setDeskLoading(true);
    fetchPreTradeMetrics({
      ticker, expiration, spot: spot!, capital: capital!, dte: dte!, stockShares,
      legs, scenarios: scenarios ?? [], maxLoss, maxProfit, strategyType,
    })
      .then(m => { if (!cancelled) setDesk(m); })
      .catch(() => { if (!cancelled) setDesk(null); })
      .finally(() => { if (!cancelled) setDeskLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, ticker, expiration, spot, capital, dte, scenKey, legKey]);

  const ask = async (role: Role) => {
    if (disabled) return;
    setRunning(role); setErr(null);
    try {
      const r = await runPreTradeAgent({ role, ticker, strategyType, legs, metrics: llmMetrics, scenarios, breakevens, deskMetrics: desk, notes });
      setResults(s => ({ ...s, [role]: r }));
    } catch (e: any) {
      setErr(e?.message || `Failed to run ${role} agent`);
    } finally {
      setRunning(null);
    }
  };

  const AskButton = ({ role, label, accent }: { role: Role; label: string; accent: string }) => {
    const r = results[role];
    const cls = disabled ? 'btn-outline btn-disabled'
      : r ? (r.action_needed ? 'btn-warning' : 'btn-success') : `btn-outline btn-${accent}`;
    return (
      <span role="button" className={`btn btn-xs gap-1 ${cls}`}
        onClick={(e) => { e.stopPropagation(); ask(role); }}
        title={disabled ? 'Evaluate the structure first' : `Ask the ${label} desk whether to enter`}>
        {running === role ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
        {r ? 'Re-ask' : 'Ask'} {label}
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
            {r.verdict || (r.action_needed ? 'Review' : 'OK')}
          </span>
          <span className="text-[9px] text-base-content/30">· {r.title} · {r.model}</span>
        </div>
        <div className="text-[11px] text-base-content/80 whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto">{r.content}</div>
      </div>
    );
  };

  const deskHint = (text: string) =>
    deskLoading ? <p className="text-[10px] text-base-content/40 flex items-center gap-1"><Loader2 className="w-3 h-3 animate-spin" /> Computing desk metrics…</p>
      : !desk ? <p className="text-[10px] text-base-content/40">{text}</p> : null;

  const q = desk?.quant;

  const body = (
    <div className="space-y-2">
      {err && <div className="text-[10px] text-error">{err}</div>}

      {/* Risk */}
      <CollapsibleSection title="Risk · capital at risk" accent="warning"
        icon={<Shield className="w-3 h-3" />} subtitle="VaR · CVaR · max loss · sizing"
        badge={<AskButton role="risk" label="Risk" accent="warning" />}>
        {desk ? <RiskGrid r={desk.risk} /> : deskHint('Ask the risk desk whether the downside is acceptable to put on.')}
        <AgentResult role="risk" />
      </CollapsibleSection>

      {/* Trader */}
      <CollapsibleSection title="Trader · dynamic Greeks" accent="info"
        icon={<Activity className="w-3 h-3" />} subtitle="Δ · Γ · ν · Θ · Vanna · Charm · Volga"
        badge={<AskButton role="trader" label="Trader" accent="info" />}>
        {desk ? <TraderGrid t={desk.trader} /> : deskHint('Ask the trader if strikes, pricing and timing make this a good entry.')}
        <AgentResult role="trader" />
      </CollapsibleSection>

      {/* PM */}
      <CollapsibleSection title="PM · risk-adjusted quality" accent="success"
        icon={<LineChart className="w-3 h-3" />} subtitle="Omega · Sortino · Calmar · PoP · EV · Kelly"
        badge={<AskButton role="pm" label="PM" accent="success" />}>
        {desk ? <PmGrid pm={desk.pm} /> : deskHint('Ask the PM whether the risk-adjusted edge is worth entering vs cash.')}
        <AgentResult role="pm" />
      </CollapsibleSection>

      {/* Quant — algorithmic recommendation (non-LLM) + pricing-confidence signals */}
      <CollapsibleSection title="Quant · algorithmic recommendation" accent="secondary" defaultOpen
        icon={<Cpu className="w-3 h-3" />} subtitle="Omega · PoP · Sortino · CVaR · carry → score">
        {q ? (
          <div className="space-y-2">
            <div className={`flex items-center gap-2 rounded-lg border p-2 ${
              q.tone === 'good' ? 'border-success/30 bg-success/5' : q.tone === 'bad' ? 'border-error/30 bg-error/5' : 'border-warning/30 bg-warning/5'}`}>
              <span className={`text-sm font-bold uppercase tracking-wider ${toneClass(q.tone)}`}>{q.verdict}</span>
              <span className="text-xs text-base-content/50">algorithmic score</span>
              <span className={`ml-auto text-lg font-bold ${toneClass(q.tone)}`}>{q.score}<span className="text-[10px] text-base-content/40">/100</span></span>
            </div>
            <div className="flex flex-wrap gap-2">
              <SubBar label="Edge" v={q.subscores.edge} />
              <SubBar label="PoP" v={q.subscores.pop} />
              <SubBar label="Risk-adj" v={q.subscores.sortino} />
              <SubBar label="Tail" v={q.subscores.tail} />
              <SubBar label="Carry" v={q.subscores.carry} />
            </div>
            <ul className="text-[11px] text-base-content/70 space-y-0.5">
              {q.reasons.map((r, i) => <li key={i} className="flex gap-1.5"><span className="text-base-content/30">·</span>{r}</li>)}
            </ul>
            <p className="text-[9px] text-base-content/30">Deterministic — blends Omega, Probability of Profit, Sortino, CVaR (expected shortfall) and carry vs the risk-free hurdle over the payoff distribution.</p>
          </div>
        ) : quantMetrics ? (
          <>
            <div className="grid grid-cols-3 sm:grid-cols-4 gap-2">
              {quantMetrics.map((m, i) => <Metric key={i} label={m.label} value={m.value} hint={m.hint} color={toneClass(m.tone)} />)}
            </div>
            {quantVerdict && (
              <div className={`mt-2 rounded-lg border p-2 text-[11px] ${
                quantVerdict.tone === 'good' ? 'border-success/30 bg-success/5' : quantVerdict.tone === 'bad' ? 'border-error/30 bg-error/5' : 'border-warning/30 bg-warning/5'}`}>
                <span className={`font-semibold uppercase tracking-wider ${toneClass(quantVerdict.tone)}`}>{quantVerdict.label}</span>
                {quantVerdict.note && <span className="text-base-content/70"> — {quantVerdict.note}</span>}
              </div>
            )}
          </>
        ) : deskHint('Evaluate the structure to get an algorithmic read.')}

        {quantSignals && quantSignals.length > 0 && (
          <div className="mt-3 pt-2 border-t border-white/[0.06]">
            <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1.5">Pricing confidence — why to trust (or doubt) the numbers</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {quantSignals.map((s, i) => (
                <div key={i} className="bg-base-300/20 rounded-lg p-2 text-center" title={s.sub}>
                  <div className="text-[9px] uppercase text-base-content/30 tracking-wider">{s.label}</div>
                  <div className="text-xs font-semibold mt-0.5">{s.value}</div>
                  {s.sub && <div className="text-[9px] text-base-content/40 mt-0.5">{s.sub}</div>}
                </div>
              ))}
            </div>
          </div>
        )}
      </CollapsibleSection>
    </div>
  );

  // Lazy mode: a single header the user expands (metrics compute on first open).
  if (lazy) {
    return (
      <div className="rounded-xl border border-secondary/20 bg-secondary/[0.03] overflow-hidden">
        <button className="w-full flex items-center gap-2 px-3 py-2 hover:bg-secondary/[0.06] transition-colors text-left"
          onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}>
          {open ? <ChevronDown className="w-3.5 h-3.5 text-base-content/40" /> : <ChevronRight className="w-3.5 h-3.5 text-base-content/40" />}
          <Shield className="w-3.5 h-3.5 text-secondary" />
          <span className="text-xs font-semibold text-secondary">Desk Review — should you enter?</span>
          <span className="text-[9px] text-base-content/40">Risk · Trader · PM · Quant</span>
          {deskLoading && <Loader2 className="w-3 h-3 animate-spin text-base-content/40 ml-auto" />}
        </button>
        {open && <div className="px-3 pb-3 pt-1">{body}</div>}
      </div>
    );
  }

  return (
    <div className="glass-card">
      <div className="p-5 space-y-2">
        <div className="flex items-center gap-2 mb-1">
          <Shield className="w-4 h-4 text-secondary" />
          <h3 className="font-bold text-sm">Desk Review — should you enter?</h3>
          <span className="text-[10px] text-base-content/40">Risk · Trader · PM · Quant · pre-trade</span>
        </div>
        {body}
      </div>
    </div>
  );
}
