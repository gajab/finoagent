import { useCallback, useEffect, useState } from 'react';
import {
  Crosshair, RefreshCw, ChevronDown, ChevronUp, Check, X, AlertTriangle, Minus,
  Play, LogOut, Trash2, Sparkles, Copy, StickyNote, TrendingUp, TrendingDown,
  Loader2, ShieldAlert, Target, Ban, CircleDot, ClipboardCheck,
} from 'lucide-react';
import {
  listTrackedTrades, refreshTrackedTrade, executeTrackedTrade, closeTradeLifecycle,
  invalidateTrackedTrade, deleteTrackedTrade, askTrackedTradeLLM, updateTrackedTradeNotes,
} from '../api';
import type {
  TrackedTrade, TrackedTradesResponse, TrackEval, TrackCheck, TrackAdvice,
} from '../types';
import { DirectionBadge, InfoTip } from '../components/taUi';

// ---------------------------------------------------------------------------
// small presentation helpers
// ---------------------------------------------------------------------------

type Verdict = string;

function verdictStyle(v?: Verdict): { cls: string; icon: JSX.Element; ring: string } {
  switch (v) {
    case 'execute':   return { cls: 'bg-success/15 text-success border-success/30', ring: 'ring-success/40', icon: <Check className="w-4 h-4" /> };
    case 'scale_out': return { cls: 'bg-success/15 text-success border-success/30', ring: 'ring-success/40', icon: <Target className="w-4 h-4" /> };
    case 'hold':      return { cls: 'bg-info/15 text-info border-info/30', ring: 'ring-info/40', icon: <CircleDot className="w-4 h-4" /> };
    case 'wait':      return { cls: 'bg-warning/15 text-warning border-warning/30', ring: 'ring-warning/40', icon: <Minus className="w-4 h-4" /> };
    case 'tighten':   return { cls: 'bg-warning/15 text-warning border-warning/30', ring: 'ring-warning/40', icon: <ShieldAlert className="w-4 h-4" /> };
    case 'invalid':   return { cls: 'bg-error/15 text-error border-error/30', ring: 'ring-error/40', icon: <Ban className="w-4 h-4" /> };
    case 'exit':      return { cls: 'bg-error/15 text-error border-error/30', ring: 'ring-error/40', icon: <LogOut className="w-4 h-4" /> };
    default:          return { cls: 'bg-base-300/50 text-base-content/60 border-base-300', ring: 'ring-base-300', icon: <Minus className="w-4 h-4" /> };
  }
}

function CheckIcon({ status }: { status: string }) {
  if (status === 'pass') return <Check className="w-4 h-4 text-success shrink-0" />;
  if (status === 'fail') return <X className="w-4 h-4 text-error shrink-0" />;
  if (status === 'warn') return <AlertTriangle className="w-4 h-4 text-warning shrink-0" />;
  return <Minus className="w-4 h-4 text-base-content/30 shrink-0" />;
}

const fmt = (n: number | null | undefined, d = 2) =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : Number(n).toFixed(d);
const pct = (n: number | null | undefined, d = 1) =>
  n === null || n === undefined || Number.isNaN(n) ? '—' : `${n > 0 ? '+' : ''}${Number(n).toFixed(d)}%`;

// ---------------------------------------------------------------------------
// spot-between-stop-and-target progress ladder
// ---------------------------------------------------------------------------

function ProgressLadder({ dir, stop, t1, spot }: { dir: string; stop: number | null; t1: number | null; spot: number | null }) {
  if (stop === null || t1 === null || spot === null) return null;
  const span = t1 - stop;
  if (span === 0) return null;
  let p = ((spot - stop) / span) * 100;
  p = Math.max(0, Math.min(100, p));
  return (
    <div className="mt-2">
      <div className="relative h-2 rounded-full bg-gradient-to-r from-error/40 via-warning/30 to-success/50">
        <div className="absolute -top-1 h-4 w-1 rounded bg-base-content shadow" style={{ left: `calc(${p}% - 2px)` }} title={`Spot $${fmt(spot)}`} />
      </div>
      <div className="flex justify-between text-[10px] text-base-content/50 mt-0.5">
        <span>Stop ${fmt(stop)}</span>
        <span>{dir === 'short' ? 'Target below' : 'Target'} ${fmt(t1)}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// market snapshot chips
// ---------------------------------------------------------------------------

function MarketChips({ ev }: { ev: TrackEval }) {
  const m = ev.market;
  if (!m) return null;
  const chips: { label: string; value: string; tip: string }[] = [];
  if (m.vwap?.z !== null && m.vwap?.z !== undefined)
    chips.push({ label: 'VWAP σ', value: `${m.vwap.z > 0 ? '+' : ''}${fmt(m.vwap.z, 1)}σ`, tip: `Spot vs session VWAP $${fmt(m.vwap.vwap)} in standard deviations. ±2σ = a stretched intraday extreme.` });
  if (m.rsi_1h !== null && m.rsi_1h !== undefined)
    chips.push({ label: '1H RSI', value: fmt(m.rsi_1h, 0), tip: '1-hour Relative Strength. >60 overbought bounce, <40 oversold flush.' });
  const c15 = m.choch_15m?.last_event?.direction;
  if (c15) chips.push({ label: '15m CHOCH', value: c15, tip: 'Latest 15-minute change-of-character / break of structure direction.' });
  const cvd = m.cvd?.divergence;
  chips.push({ label: 'CVD', value: cvd || 'neutral', tip: 'Cumulative Volume Delta (order-flow proxy). A divergence flags passive absorption at the level.' });
  const g = (m.gamma as { net_gex?: { sign?: string } } | null)?.net_gex?.sign;
  if (g) chips.push({ label: 'Dealer γ', value: g === 'long' ? 'long (pin)' : 'short (amp)', tip: 'Dealer gamma. Long = vol-suppressed / mean-reverting; short = moves amplified.' });
  if (m.volume?.ratio) chips.push({ label: 'Vol', value: `${fmt(m.volume.ratio, 1)}×`, tip: 'Latest bar volume vs the recent average. >1.5× = climactic participation.' });
  return (
    <div className="flex flex-wrap gap-1.5 mt-2">
      {chips.map((c) => (
        <span key={c.label} className="inline-flex items-center gap-1 rounded-md bg-base-200 border border-base-300 px-1.5 py-0.5 text-[10px]">
          <span className="text-base-content/50">{c.label}</span>
          <span className="font-semibold">{c.value}</span>
          <InfoTip text={c.tip} />
        </span>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AI advice panel
// ---------------------------------------------------------------------------

function AdvicePanel({ advice }: { advice: TrackAdvice['advice'] }) {
  if (advice.raw) return <div className="text-xs whitespace-pre-wrap text-base-content/70">{advice.raw}</div>;
  return (
    <div className="space-y-2 text-xs">
      <div className="flex items-center gap-2">
        <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 font-semibold border ${advice.agree_with_verdict ? 'bg-success/15 text-success border-success/30' : 'bg-warning/15 text-warning border-warning/30'}`}>
          {advice.agree_with_verdict ? <Check className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
          {advice.agree_with_verdict ? 'Agrees with the verdict' : 'Sees it differently'}
        </span>
        {advice.confidence && <span className="text-base-content/50">confidence: {advice.confidence}</span>}
      </div>
      {advice.assessment && <p className="text-base-content/80">{advice.assessment}</p>}
      {advice.recommended_action && (
        <p className="rounded-md bg-primary/10 border border-primary/20 text-primary px-2 py-1"><span className="font-semibold">Do this: </span>{advice.recommended_action}</p>
      )}
      {!!advice.key_risks?.length && (
        <div><div className="font-semibold text-base-content/60 mb-0.5">Key risks</div>
          <ul className="list-disc list-inside space-y-0.5 text-base-content/70">{advice.key_risks.map((r, i) => <li key={i}>{r}</li>)}</ul></div>
      )}
      {!!advice.what_to_watch?.length && (
        <div><div className="font-semibold text-base-content/60 mb-0.5">What to watch</div>
          <ul className="list-disc list-inside space-y-0.5 text-base-content/70">{advice.what_to_watch.map((r, i) => <li key={i}>{r}</li>)}</ul></div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// one tracked-trade card
// ---------------------------------------------------------------------------

function TrackCard({ trade, onChanged }: { trade: TrackedTrade; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [freshEval, setFreshEval] = useState<TrackEval | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [advice, setAdvice] = useState<TrackAdvice['advice'] | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [copied, setCopied] = useState(false);
  const [form, setForm] = useState<null | 'execute' | 'close' | 'notes'>(null);
  const [price, setPrice] = useState('');
  const [qty, setQty] = useState('');
  const [note, setNote] = useState('');
  const [notes, setNotes] = useState(trade.user_notes || '');

  const ev: TrackEval | null = freshEval || trade.last_eval || null;
  const vs = verdictStyle(ev?.verdict || trade.last_verdict || undefined);
  const isWatching = trade.status === 'watching';
  const isLive = trade.status === 'in_progress';
  const isClosed = trade.status === 'closed' || trade.status === 'invalidated';

  const doRefresh = async () => {
    setBusy('refresh');
    try {
      const res = await refreshTrackedTrade(trade.id);
      setFreshEval(res.evaluation);
      onChanged();
    } catch (e) { /* surfaced by list */ }
    finally { setBusy(null); }
  };
  const doExecute = async () => {
    setBusy('execute');
    try {
      await executeTrackedTrade(trade.id, {
        price: price ? Number(price) : undefined,
        qty: qty ? Number(qty) : undefined,
        note: note || undefined,
      });
      setForm(null); onChanged();
    } finally { setBusy(null); }
  };
  const doClose = async () => {
    setBusy('close');
    try {
      await closeTradeLifecycle(trade.id, { price: price ? Number(price) : undefined, note: note || undefined });
      setForm(null); onChanged();
    } finally { setBusy(null); }
  };
  const doInvalidate = async () => { setBusy('invalidate'); try { await invalidateTrackedTrade(trade.id); onChanged(); } finally { setBusy(null); } };
  const doDelete = async () => { if (!confirm('Delete this tracked trade?')) return; setBusy('delete'); try { await deleteTrackedTrade(trade.id); onChanged(); } finally { setBusy(null); } };
  const doAsk = async () => {
    setBusy('ai');
    try { const res = await askTrackedTradeLLM(trade.id, { refresh: true }); setAdvice(res.advice); setFreshEval((f) => f); onChanged(); }
    catch (e) { setAdvice({ raw: e instanceof Error ? e.message : 'AI review failed.' }); }
    finally { setBusy(null); }
  };
  const doSaveNotes = async () => { setBusy('notes'); try { await updateTrackedTradeNotes(trade.id, notes || null); setForm(null); onChanged(); } finally { setBusy(null); } };
  const copyJson = () => {
    const payload = ev?.payload || ev;
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2)).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  };

  const t1 = trade.target_levels?.[0] ?? null;
  const spot = ev?.spot ?? null;
  const pos = ev?.position;

  return (
    <div className={`rounded-2xl border bg-base-100 shadow-sm overflow-hidden ${open ? `ring-1 ${vs.ring}` : ''}`}>
      {/* header row */}
      <button className="w-full flex items-center gap-3 p-3 sm:p-4 text-left hover:bg-base-200/40 transition" onClick={() => setOpen((o) => !o)}>
        <div className="flex items-center gap-2 min-w-0">
          <span className="font-bold text-base sm:text-lg">{trade.ticker}</span>
          <DirectionBadge direction={trade.direction} size="sm" />
          <span className="hidden sm:inline text-[10px] uppercase tracking-wide rounded bg-base-200 border border-base-300 px-1.5 py-0.5 text-base-content/60">{trade.instrument}</span>
        </div>
        <div className="flex-1 min-w-0 hidden md:block">
          <span className="text-xs text-base-content/60 truncate">
            {trade.setup_type?.replace(/_/g, ' ')}
            {trade.entry_level != null && <> · entry ${fmt(trade.entry_level)}</>}
            {t1 != null && <> · target ${fmt(t1)}</>}
          </span>
        </div>
        {isLive && pos?.open_pnl_pct != null && (
          <span className={`text-xs font-semibold ${pos.open_pnl_pct >= 0 ? 'text-success' : 'text-error'}`}>{pct(pos.open_pnl_pct)}</span>
        )}
        {ev?.verdict && (
          <span className={`inline-flex items-center gap-1 rounded-lg border px-2 py-1 text-[11px] font-semibold ${vs.cls}`}>
            {vs.icon}<span className="hidden sm:inline">{ev.verdict_label || ev.verdict}</span>
          </span>
        )}
        {open ? <ChevronUp className="w-4 h-4 text-base-content/40" /> : <ChevronDown className="w-4 h-4 text-base-content/40" />}
      </button>

      {open && (
        <div className="border-t border-base-200 p-3 sm:p-4 space-y-3">
          {/* verdict banner */}
          {ev ? (
            <div className={`rounded-xl border p-3 ${vs.cls}`}>
              <div className="flex items-start gap-2">
                {vs.icon}
                <div className="min-w-0">
                  <div className="font-semibold text-sm">{ev.headline || ev.verdict_label}</div>
                  {ev.confidence_pct != null && ev.mode === 'entry' && (
                    <div className="mt-1 flex items-center gap-2">
                      <div className="h-1.5 w-28 rounded-full bg-base-content/10 overflow-hidden">
                        <div className="h-full bg-current" style={{ width: `${ev.confidence_pct}%` }} />
                      </div>
                      <span className="text-[11px]">{ev.confidence_pct}% confirmed</span>
                    </div>
                  )}
                  {!!ev.reasons?.length && <ul className="mt-1 text-xs space-y-0.5 opacity-90">{ev.reasons.map((r, i) => <li key={i}>• {r}</li>)}</ul>}
                  {!!ev.need?.length && (
                    <div className="mt-1 text-[11px] opacity-80"><span className="font-semibold">Still needed: </span>{ev.need.join(' · ')}</div>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-xs text-base-content/50">No evaluation yet — press <span className="font-semibold">Refresh</span> to run the live checks.</div>
          )}

          {/* levels + position */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Tile label="Spot" value={spot != null ? `$${fmt(spot)}` : '—'} />
            <Tile label="Entry" value={trade.entry_level != null ? `$${fmt(trade.entry_level)}` : '—'} sub={pos?.dist_to_entry_pct != null && isWatching ? `${pct(pos.dist_to_entry_pct)} away` : undefined} />
            <Tile label="Stop" value={trade.stop_level != null ? `$${fmt(trade.stop_level)}` : '—'} tone="error" />
            <Tile label={isLive ? 'Open P&L' : 'Target'} value={
              isLive
                ? (pos?.open_pnl != null ? `$${fmt(pos.open_pnl, 0)}` : (pos?.open_pnl_pct != null ? pct(pos.open_pnl_pct) : '—'))
                : (t1 != null ? `$${fmt(t1)}` : '—')
            } tone={isLive ? (pos?.open_pnl_pct != null && pos.open_pnl_pct >= 0 ? 'success' : 'error') : undefined}
              sub={isLive && pos?.r_multiple != null ? `${pos.r_multiple}R` : undefined} />
          </div>
          <ProgressLadder dir={trade.direction} stop={trade.stop_level} t1={t1} spot={spot} />

          {ev?.market && <MarketChips ev={ev} />}

          {/* confirmation checklist */}
          {!!ev?.checks?.length && (
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-wide text-base-content/50 mb-1">
                {ev.mode === 'exit' ? 'Exit conditions' : 'Secondary confirmations'}
              </div>
              <div className="space-y-1">
                {ev.checks.map((c: TrackCheck) => (
                  <div key={c.key} className="flex items-start gap-2 text-xs">
                    <CheckIcon status={c.status} />
                    <div className="min-w-0">
                      <span className="font-medium">{c.label}</span>
                      {c.role === 'critical' && <span className="ml-1 text-[9px] uppercase text-error/70">critical</span>}
                      <span className="text-base-content/60"> — {c.detail}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* AI advice */}
          {advice && (
            <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-primary mb-1"><Sparkles className="w-3.5 h-3.5" /> AI desk review</div>
              <AdvicePanel advice={advice} />
            </div>
          )}

          {/* JSON payload */}
          {showJson && (
            <pre className="max-h-64 overflow-auto rounded-xl bg-base-300/40 border border-base-300 p-2 text-[10px] leading-tight">{JSON.stringify(ev?.payload || ev, null, 2)}</pre>
          )}

          {/* inline forms */}
          {form === 'execute' && (
            <InlineForm title="Record your fill" onSubmit={doExecute} busy={busy === 'execute'} submitLabel="Execute → In Progress">
              <NumIn label="Fill price" value={price} onChange={setPrice} placeholder={spot != null ? String(spot) : 'market'} />
              <NumIn label="Quantity" value={qty} onChange={setQty} placeholder="shares / contracts" />
              <TextIn label="Note" value={note} onChange={setNote} />
            </InlineForm>
          )}
          {form === 'close' && (
            <InlineForm title="Close the position" onSubmit={doClose} busy={busy === 'close'} submitLabel="Close & book P&L">
              <NumIn label="Exit price" value={price} onChange={setPrice} placeholder={spot != null ? String(spot) : 'market'} />
              <TextIn label="Note" value={note} onChange={setNote} />
            </InlineForm>
          )}
          {form === 'notes' && (
            <InlineForm title="Notes" onSubmit={doSaveNotes} busy={busy === 'notes'} submitLabel="Save note">
              <TextIn label="Your notes" value={notes} onChange={setNotes} />
            </InlineForm>
          )}

          {/* action bar */}
          <div className="flex flex-wrap gap-1.5 pt-1">
            {!isClosed && (
              <Btn onClick={doRefresh} busy={busy === 'refresh'} icon={<RefreshCw className="w-3.5 h-3.5" />}>Refresh</Btn>
            )}
            {isWatching && (
              <Btn onClick={() => { setForm(form === 'execute' ? null : 'execute'); setPrice(spot != null ? String(spot) : ''); }} primary icon={<Play className="w-3.5 h-3.5" />}>Execute</Btn>
            )}
            {isLive && (
              <Btn onClick={() => { setForm(form === 'close' ? null : 'close'); setPrice(spot != null ? String(spot) : ''); }} icon={<LogOut className="w-3.5 h-3.5" />}>Close</Btn>
            )}
            {!isClosed && (
              <Btn onClick={doAsk} busy={busy === 'ai'} icon={<Sparkles className="w-3.5 h-3.5" />}>Ask AI</Btn>
            )}
            <Btn onClick={copyJson} icon={copied ? <ClipboardCheck className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}>{copied ? 'Copied' : 'Copy JSON'}</Btn>
            <Btn onClick={() => setShowJson((s) => !s)} icon={<Crosshair className="w-3.5 h-3.5" />}>{showJson ? 'Hide' : 'View'} data</Btn>
            <Btn onClick={() => setForm(form === 'notes' ? null : 'notes')} icon={<StickyNote className="w-3.5 h-3.5" />}>Notes</Btn>
            {isWatching && <Btn onClick={doInvalidate} busy={busy === 'invalidate'} icon={<Ban className="w-3.5 h-3.5" />}>Invalidate</Btn>}
            <Btn onClick={doDelete} busy={busy === 'delete'} danger icon={<Trash2 className="w-3.5 h-3.5" />}>Delete</Btn>
          </div>

          {isClosed && trade.realized_pnl != null && (
            <div className={`text-sm font-semibold ${trade.realized_pnl >= 0 ? 'text-success' : 'text-error'}`}>
              Realized P&L: ${fmt(trade.realized_pnl, 0)}
            </div>
          )}
          {trade.user_notes && form !== 'notes' && (
            <div className="text-xs text-base-content/60 border-l-2 border-base-300 pl-2 italic">{trade.user_notes}</div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// tiny atoms
// ---------------------------------------------------------------------------

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  const toneCls = tone === 'success' ? 'text-success' : tone === 'error' ? 'text-error' : '';
  return (
    <div className="rounded-xl bg-base-200/60 border border-base-300 p-2">
      <div className="text-[10px] uppercase tracking-wide text-base-content/50">{label}</div>
      <div className={`text-sm font-bold ${toneCls}`}>{value}</div>
      {sub && <div className="text-[10px] text-base-content/50">{sub}</div>}
    </div>
  );
}

function Btn({ children, onClick, icon, busy, primary, danger }: { children: React.ReactNode; onClick: () => void; icon?: JSX.Element; busy?: boolean; primary?: boolean; danger?: boolean }) {
  const base = primary ? 'bg-primary text-primary-content border-primary hover:bg-primary/90'
    : danger ? 'bg-transparent text-error/80 border-error/30 hover:bg-error/10'
      : 'bg-base-100 border-base-300 hover:bg-base-200';
  return (
    <button onClick={onClick} disabled={busy} className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition disabled:opacity-50 ${base}`}>
      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : icon}{children}
    </button>
  );
}

function InlineForm({ title, children, onSubmit, busy, submitLabel }: { title: string; children: React.ReactNode; onSubmit: () => void; busy?: boolean; submitLabel: string }) {
  return (
    <div className="rounded-xl border border-base-300 bg-base-200/40 p-3 space-y-2">
      <div className="text-xs font-semibold text-base-content/70">{title}</div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">{children}</div>
      <button onClick={onSubmit} disabled={busy} className="inline-flex items-center gap-1 rounded-lg bg-primary text-primary-content px-3 py-1.5 text-xs font-semibold disabled:opacity-50">
        {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}{submitLabel}
      </button>
    </div>
  );
}
function NumIn({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <label className="text-[11px] text-base-content/60">{label}
      <input type="number" step="any" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        className="mt-0.5 w-full rounded-lg border border-base-300 bg-base-100 px-2 py-1 text-xs" />
    </label>
  );
}
function TextIn({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="text-[11px] text-base-content/60 sm:col-span-2">{label}
      <input type="text" value={value} onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded-lg border border-base-300 bg-base-100 px-2 py-1 text-xs" />
    </label>
  );
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

const TABS: { key: 'watching' | 'in_progress' | 'closed' | 'invalidated'; label: string; icon: JSX.Element }[] = [
  { key: 'watching', label: 'Watching', icon: <Crosshair className="w-4 h-4" /> },
  { key: 'in_progress', label: 'In Progress', icon: <TrendingUp className="w-4 h-4" /> },
  { key: 'closed', label: 'Closed', icon: <ClipboardCheck className="w-4 h-4" /> },
  { key: 'invalidated', label: 'Invalidated', icon: <Ban className="w-4 h-4" /> },
];

export default function TradeTrackingPage() {
  const [data, setData] = useState<TrackedTradesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [tab, setTab] = useState<'watching' | 'in_progress' | 'closed' | 'invalidated'>('watching');

  const load = useCallback(async () => {
    try { setErr(null); const res = await listTrackedTrades(); setData(res); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to load tracked trades.'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const counts = data?.counts || {};
  const list = data?.groups?.[tab] || [];

  return (
    <div className="max-w-4xl mx-auto px-3 sm:px-4 py-4 sm:py-6">
      <div className="flex items-start justify-between gap-3 mb-1">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold flex items-center gap-2"><Crosshair className="w-6 h-6 text-primary" /> Trade Tracker</h1>
          <p className="text-sm text-base-content/60 mt-0.5">Track a setup to entry, confirm before you commit, then manage it to the exit — with a live checklist and an AI second opinion at every step.</p>
        </div>
        <button onClick={load} className="inline-flex items-center gap-1 rounded-lg border border-base-300 bg-base-100 px-2.5 py-1.5 text-xs font-semibold hover:bg-base-200">
          <RefreshCw className="w-3.5 h-3.5" /> Reload
        </button>
      </div>

      {/* tabs */}
      <div className="flex gap-1 border-b border-base-200 mt-3 mb-4 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`inline-flex items-center gap-1.5 px-3 py-2 text-sm font-semibold border-b-2 -mb-px whitespace-nowrap transition ${tab === t.key ? 'border-primary text-primary' : 'border-transparent text-base-content/50 hover:text-base-content/80'}`}>
            {t.icon}{t.label}
            <span className="rounded-full bg-base-200 text-base-content/60 text-[10px] px-1.5 py-0.5">{counts[t.key] || 0}</span>
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-16 text-base-content/50"><Loader2 className="w-6 h-6 animate-spin" /></div>
      ) : err ? (
        <div className="rounded-xl border border-error/30 bg-error/10 text-error p-4 text-sm">{err}</div>
      ) : list.length === 0 ? (
        <EmptyState tab={tab} />
      ) : (
        <div className="space-y-2.5">
          {list.map((t) => <TrackCard key={t.id} trade={t} onChanged={load} />)}
        </div>
      )}
    </div>
  );
}

function EmptyState({ tab }: { tab: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-base-300 p-8 text-center">
      <Crosshair className="w-8 h-8 mx-auto text-base-content/30" />
      <div className="mt-2 font-semibold text-base-content/70">
        {tab === 'watching' ? 'No trades on the watchlist yet' : tab === 'in_progress' ? 'No live trades' : tab === 'closed' ? 'No closed trades yet' : 'Nothing invalidated'}
      </div>
      <p className="text-sm text-base-content/50 mt-1">
        Open any stock's <span className="font-semibold">Technical → Setups</span> tab and press <span className="font-semibold">“Track this trade”</span> on a setup to start managing it here.
      </p>
    </div>
  );
}
