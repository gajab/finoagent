/**
 * PasteOrderModal — log a trade by pasting a broker order (Fidelity-style rows).
 *
 * Paste one or several order blocks; the backend parses them into legs and decides:
 *   • NEW      → creates the trade,
 *   • CLOSE    → the order is the opposite side of a leg you already hold → records
 *                the close (banks realized P&L) instead of adding a bogus position,
 *   • DUPLICATE→ same side as an existing leg → warns and does NOT add (Log Trade
 *                is offered for adding to a position on purpose).
 */
import { useState } from 'react';
import { X, Loader2, ClipboardPaste, AlertTriangle, LogOut, Plus, CheckCircle2 } from 'lucide-react';
import { importOrder, createManualTrade, closePosition } from '../api';
import type { ImportOrderResult, ImportedLeg, TradePurpose } from '../api';

const PURPOSES: TradePurpose[] = ['income', 'hedge', 'managed_floor', 'managed_buffer', 'dual_directional', 'trade', 'other'];

function legLabel(l: ImportedLeg): string {
  if (l.kind === 'stock') return `${l.action} ${l.qty} ${l.underlying} @ $${l.price ?? '?'}`;
  return `${l.action} ${l.type} $${l.strike} exp ${l.expiration} ×${l.qty} @ $${l.price ?? '?'}`;
}

export default function PasteOrderModal({ open, onClose, onDone, onLogManual }: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
  onLogManual?: () => void;
}) {
  const [text, setText] = useState('');
  const [purpose, setPurpose] = useState<TradePurpose>('income');
  const [preview, setPreview] = useState<ImportOrderResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!open) return null;

  const reset = () => { setText(''); setPreview(null); setErr(null); };
  const close = () => { reset(); onClose(); };

  const parse = async () => {
    if (!text.trim()) return;
    setLoading(true); setErr(null); setPreview(null);
    try { setPreview(await importOrder(text, purpose)); }
    catch (e: any) { setErr(e?.message || 'Could not parse that text'); }
    finally { setLoading(false); }
  };

  const apply = async () => {
    if (!preview) return;
    setApplying(true); setErr(null);
    try {
      if (preview.intent === 'new') {
        await createManualTrade({ ...preview.manual_trade, parameters: { ...preview.manual_trade.parameters, purpose } });
      } else if (preview.intent === 'close') {
        const byTrade: Record<number, { leg_index: number; exit_price: number }[]> = {};
        preview.close_matches.forEach(m => {
          (byTrade[m.trade_id] ||= []).push({ leg_index: m.leg_index, exit_price: m.exit_price ?? 0 });
        });
        for (const [tid, legs] of Object.entries(byTrade)) {
          await closePosition(Number(tid), { legs });
        }
      }
      reset(); onDone();
    } catch (e: any) {
      setErr(e?.message || 'Failed to apply');
    } finally {
      setApplying(false);   // always reset — the modal persists, so a stale true would grey the button forever
    }
  };

  const intentBadge = preview && ({
    new: <span className="badge badge-success gap-1"><Plus className="w-3 h-3" />New trade</span>,
    close: <span className="badge badge-warning gap-1"><LogOut className="w-3 h-3" />Closes an open trade</span>,
    duplicate: <span className="badge badge-error gap-1"><AlertTriangle className="w-3 h-3" />Same side — not added</span>,
  } as const)[preview.intent];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={close}>
      <div className="bg-base-100 rounded-2xl border border-white/10 w-full max-w-xl max-h-[90vh] overflow-auto shadow-2xl"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10 sticky top-0 bg-base-100 z-10">
          <ClipboardPaste className="w-4 h-4 text-primary" />
          <span className="font-semibold">Paste order to log</span>
          <button className="ml-auto btn btn-ghost btn-xs btn-circle" onClick={close}><X className="w-4 h-4" /></button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-xs text-base-content/60">
            Paste the order rows from your broker (Fidelity activity export works as-is). Multiple legs pasted
            together become one multi-leg trade. If it's the opposite side of a trade you already hold, it's
            recorded as a <b>close</b> — not a new position.
          </p>

          <textarea
            className="textarea textarea-bordered w-full h-40 text-xs font-mono"
            placeholder={"Date\nJul-30-2026\nSymbol\n-SMH260918C825\nContracts\n-1\nPrice\n$0.50\n…"}
            value={text}
            onChange={e => { setText(e.target.value); setPreview(null); }}
          />

          <div className="flex items-center gap-2">
            <label className="text-xs text-base-content/50">Purpose</label>
            <select className="select select-bordered select-xs" value={purpose} onChange={e => setPurpose(e.target.value as TradePurpose)}>
              {PURPOSES.map(p => <option key={p} value={p}>{p.replace(/_/g, ' ')}</option>)}
            </select>
            <button className="btn btn-primary btn-sm gap-1 ml-auto" disabled={loading || !text.trim()} onClick={parse}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
              {loading ? 'Parsing…' : 'Parse'}
            </button>
          </div>

          {err && <div className="text-xs text-error flex items-start gap-1"><AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>}

          {preview && (
            <div className="rounded-xl border border-white/10 p-3 space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                {intentBadge}
                <span className="text-sm font-semibold">{preview.underlying}</span>
                <span className="text-xs text-base-content/40">{preview.legs.length} leg{preview.legs.length !== 1 ? 's' : ''}</span>
              </div>

              <div className="space-y-1">
                {preview.legs.map((l, i) => (
                  <div key={i} className="text-[11px] font-mono flex items-center gap-2">
                    <span className={l.action === 'SELL' ? 'text-error' : 'text-success'}>{legLabel(l)}</span>
                  </div>
                ))}
              </div>

              {preview.intent === 'close' && (
                <div className="text-[11px] text-warning/90 rounded-lg bg-warning/[0.06] border border-warning/20 p-2">
                  This is the opposite side of {preview.close_matches.length} open leg
                  {preview.close_matches.length !== 1 ? 's' : ''} in{' '}
                  <b>{[...new Set(preview.close_matches.map(m => m.trade_name))].join(', ')}</b>. Recording it will
                  bank the realized P&amp;L at the pasted price.
                </div>
              )}

              {preview.intent === 'duplicate' && (
                <div className="space-y-1.5">
                  {preview.warnings.map((w, i) => (
                    <div key={i} className="text-[11px] text-error/90 rounded-lg bg-error/[0.06] border border-error/20 p-2">{w}</div>
                  ))}
                </div>
              )}

              {preview.intent === 'new' && (
                <div className="text-[11px] text-base-content/50">
                  Creates <b className="text-base-content/80">{preview.manual_trade?.name}</b> as a {purpose.replace(/_/g, ' ')} trade.
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-white/10 sticky bottom-0 bg-base-100">
          {preview?.intent === 'duplicate' && onLogManual && (
            <button className="btn btn-ghost btn-sm gap-1" onClick={() => { close(); onLogManual(); }}>
              <Plus className="w-4 h-4" /> Log manually instead
            </button>
          )}
          <button className="btn btn-ghost btn-sm ml-auto" onClick={close} disabled={applying}>Cancel</button>
          {preview && preview.intent !== 'duplicate' && (
            <button className="btn btn-primary btn-sm gap-1" onClick={apply} disabled={applying}>
              {applying ? <Loader2 className="w-4 h-4 animate-spin" /> : (preview.intent === 'close' ? <LogOut className="w-4 h-4" /> : <Plus className="w-4 h-4" />)}
              {preview.intent === 'close' ? 'Record close' : 'Add trade'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
