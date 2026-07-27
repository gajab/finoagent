/**
 * InstitutionalDesk — the world-class quant PM who MANAGES a live trade.
 *
 * On demand it reasons over the whole placed position (edge, profit banked vs
 * theta left, path/greeks risk, catalysts, capital efficiency) and issues a
 * single lifecycle signal: STRONG HOLD · HOLD · CONSIDER CLOSE · CLOSE, with a
 * concrete management plan and key levels. Sits on top of the deterministic
 * exit engine — it can agree, upgrade, or overrule it.
 */
import { useState } from 'react';
import { Sparkles, Loader2, AlertTriangle } from 'lucide-react';
import { runLifecycleManager } from '../../api';
import type { LivePnlResponse, SavedStrategyItem, LifecycleManagerResult } from '../../api';

const SIGNAL_STYLE: Record<string, { label: string; cls: string; tone: string }> = {
  STRONG_HOLD:    { label: 'STRONG HOLD',    cls: 'badge-success',              tone: 'success' },
  HOLD:           { label: 'HOLD',           cls: 'badge-success badge-outline', tone: 'success' },
  CONSIDER_CLOSE: { label: 'CONSIDER CLOSE', cls: 'badge-warning',              tone: 'warning' },
  CLOSE:          { label: 'CLOSE',          cls: 'badge-error',                tone: 'error' },
};

export default function InstitutionalDesk({ trade, pnl }: {
  trade: SavedStrategyItem;
  pnl: LivePnlResponse;
}) {
  const [result, setResult] = useState<LifecycleManagerResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setLoading(true); setErr(null);
    try { setResult(await runLifecycleManager(trade.id, pnl)); }
    catch (e: any) { setErr(e?.message || 'Institutional desk failed'); }
    finally { setLoading(false); }
  };

  const s = result ? SIGNAL_STYLE[result.signal] : null;

  return (
    <div className={`rounded-lg border p-3 ${s ? `bg-${s.tone}/5 border-${s.tone}/25` : 'bg-secondary/5 border-secondary/20'}`}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 flex-wrap">
          <Sparkles className="w-3.5 h-3.5 text-secondary" />
          <span className="text-[10px] uppercase tracking-wider font-semibold text-secondary/80">
            Institutional Desk · lifecycle manager
          </span>
          {s && <span className={`badge badge-sm font-semibold ${s.cls}`}>{s.label}</span>}
          {result && <span className="text-[9px] text-base-content/30">· {result.model}</span>}
        </div>
        <button
          className="btn btn-secondary btn-xs gap-1.5"
          disabled={loading}
          onClick={(e) => { e.stopPropagation(); run(); }}
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
          {loading ? 'Managing…' : result ? 'Re-run desk' : 'Run Institutional Desk'}
        </button>
      </div>
      {loading && (
        <p className="text-[10px] text-base-content/40 mt-1.5">
          A world-class quant PM reviews the whole trade — edge, profit banked vs theta left, path risk, catalysts, capital (~15–30s).
        </p>
      )}
      {err && (
        <div className="text-[10px] text-error mt-1.5 flex items-center gap-1">
          <AlertTriangle className="w-3 h-3" />{err}
        </div>
      )}
      {result && (
        <div className="mt-2 text-[11px] text-base-content/80 whitespace-pre-wrap leading-relaxed max-h-80 overflow-y-auto">
          {result.content}
        </div>
      )}
      {!result && !loading && !err && (
        <p className="text-[10px] text-base-content/40 mt-1.5">
          Get an institutional read on whether to strong-hold, hold, consider closing, or close — with a management plan and the levels that trigger each.
        </p>
      )}
    </div>
  );
}
