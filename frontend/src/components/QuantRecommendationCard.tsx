/**
 * QuantRecommendationCard — the standalone ALGORITHMIC (non-LLM) entry
 * recommendation for a proposed structure. Fetches the desk metrics and renders
 * ONLY the Quant verdict (score, sub-lenses, reasons) — no Risk/Trader/PM agents.
 * Used on the Dual Direction Buffer, where the recommendation is buffer-tuned.
 */
import { useState, useEffect } from 'react';
import { Cpu, Loader2, AlertTriangle } from 'lucide-react';
import { fetchPreTradeMetrics } from '../api';
import type { PreTradeDeskMetrics, PreTradeLeg, PreTradeScenario } from '../api';

const toneClass = (t?: string) =>
  t === 'good' ? 'text-success' : t === 'bad' ? 'text-error' : t === 'warn' ? 'text-warning' : 'text-base-content/80';

function SubBar({ label, v }: { label: string; v: number }) {
  const col = v >= 66 ? 'bg-success' : v >= 45 ? 'bg-warning' : 'bg-error';
  return (
    <div className="flex-1 min-w-[56px]">
      <div className="flex justify-between text-[9px] text-base-content/40 mb-0.5"><span>{label}</span><span>{v}</span></div>
      <div className="h-1 rounded bg-base-300/40 overflow-hidden"><div className={`h-full ${col}`} style={{ width: `${Math.max(2, v)}%` }} /></div>
    </div>
  );
}

export interface QuantRecommendationCardProps {
  ticker: string;
  strategyType: string;
  legs: PreTradeLeg[];
  scenarios: PreTradeScenario[];
  expiration?: string | null;
  spot: number;
  capital: number;
  dte: number;
  stockShares?: number;
  maxLoss?: number | null;
  maxProfit?: number | null;
}

export default function QuantRecommendationCard(props: QuantRecommendationCardProps) {
  const { ticker, strategyType, legs, scenarios, expiration, spot, capital, dte, stockShares, maxLoss, maxProfit } = props;
  const [desk, setDesk] = useState<PreTradeDeskMetrics | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const ready = !!(spot && capital && dte && scenarios && scenarios.length >= 3);
  const scenKey = scenarios ? JSON.stringify(scenarios) : '';
  const legKey = JSON.stringify(legs);

  useEffect(() => {
    if (!ready) { setDesk(null); return; }
    let cancelled = false;
    setLoading(true); setErr(null);
    fetchPreTradeMetrics({
      ticker, expiration, spot, capital, dte, stockShares, legs, scenarios, maxLoss, maxProfit, strategyType,
    })
      .then(m => { if (!cancelled) setDesk(m); })
      .catch(e => { if (!cancelled) { setErr(e?.message || 'Could not compute the quant recommendation'); setDesk(null); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, expiration, spot, capital, dte, scenKey, legKey]);

  const q = desk?.quant;

  return (
    <div className="glass-card">
      <div className="p-5">
        <h3 className="font-bold text-sm mb-3 flex items-center gap-2">
          <Cpu className="w-5 h-5 text-secondary" />
          Quant Recommendation
          <span className="text-[10px] font-normal text-base-content/40">algorithmic · tuned for the buffer</span>
        </h3>

        {loading && (
          <p className="text-xs text-base-content/50 flex items-center gap-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Scoring the payoff distribution…
          </p>
        )}
        {err && !loading && (
          <p className="text-xs text-error flex items-center gap-1.5"><AlertTriangle className="w-3.5 h-3.5" /> {err}</p>
        )}

        {q && !loading && (
          <div className="space-y-3">
            <div className={`flex items-center gap-3 rounded-xl border p-3 ${
              q.tone === 'good' ? 'border-success/30 bg-success/5' : q.tone === 'bad' ? 'border-error/30 bg-error/5' : 'border-warning/30 bg-warning/5'}`}>
              <span className={`text-base font-bold uppercase tracking-wider ${toneClass(q.tone)}`}>{q.verdict}</span>
              <span className="text-xs text-base-content/50">algorithmic score</span>
              <span className={`ml-auto text-2xl font-bold ${toneClass(q.tone)}`}>{q.score}<span className="text-xs text-base-content/40">/100</span></span>
            </div>

            <div className="flex flex-wrap gap-2">
              <SubBar label="Edge" v={q.subscores.edge} />
              <SubBar label="PoP" v={q.subscores.pop} />
              <SubBar label="Risk-adj" v={q.subscores.sortino} />
              <SubBar label="Protection" v={q.subscores.tail} />
              <SubBar label="Participation" v={q.subscores.carry} />
            </div>

            <ul className="text-[11px] text-base-content/70 space-y-0.5">
              {q.reasons.map((r, i) => <li key={i} className="flex gap-1.5"><span className="text-base-content/30">·</span>{r}</li>)}
            </ul>
            <p className="text-[9px] text-base-content/30">
              Deterministic (no LLM) — a dual-direction buffer is scored on downside protection, probability of a
              positive outcome and participation, not on beating cash carry.
            </p>
          </div>
        )}

        {!q && !loading && !err && (
          <p className="text-[11px] text-base-content/40">Run &apos;Simulate Dual Direction&apos; and evaluate the payoff to get the algorithmic recommendation.</p>
        )}
      </div>
    </div>
  );
}
