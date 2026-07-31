/**
 * CloseTradeModal — close SOME or ALL of a placed trade at the prices actually
 * received, so realized P&L is banked for long-term tracking (issues #3/#4/#5).
 *
 *  • Each option leg + the stock (if held) is an independently-closable row.
 *  • Exit price prefills to the current mark (pnl.current_quotes / underlying_price)
 *    and stays editable — the user types the real fill.
 *  • Realized P&L is previewed per row and in total, using the SAME sign convention
 *    the backend banks (short: entry−exit, long: exit−entry; options ×100×contracts).
 *  • Closing everything flips the trade to 'closed' (moves to the Closed tab); a
 *    partial close leaves the remaining legs active.
 */
import { useMemo, useState } from 'react';
import { X, Loader2, LogOut, AlertTriangle } from 'lucide-react';
import { closePosition } from '../../api';
import type { SavedStrategyItem, LivePnlResponse } from '../../api';

const money = (v: number) =>
  `${v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

type Row = {
  kind: 'option' | 'stock';
  key: string;
  legIndex: number | null;
  label: string;
  sub: string;
  qty: number;
  entry: number;
  isShort: boolean;
  mark: number | null;
};

export default function CloseTradeModal({ trade, pnl, preselectLeg, onClose, onClosed }: {
  trade: SavedStrategyItem;
  pnl?: LivePnlResponse | null;
  preselectLeg?: number | null;
  onClose: () => void;
  onClosed: () => void;
}) {
  const legs = (trade.legs_data || []) as any[];
  const params: any = trade.parameters || {};
  const entryPrices = (trade.entry_prices || []) as any[];
  const shares = parseFloat(params.shares ?? '0') || 0;
  const isShortStock = /short/i.test(trade.strategy_type || '');
  // On a combo (stock + options), the stock's entry sits at entry_prices[0], so
  // option leg i is at entry_prices[i+1] — mirror the backend's convention.
  const offset = shares > 0 && entryPrices.length === legs.length + 1 ? 1 : 0;

  const markForLeg = (i: number): number | null => {
    const q = (pnl?.current_quotes || []).find((c: any) => c.leg === i);
    return q && q.mid != null ? Number(q.mid) : null;
  };
  const optionEntry = (i: number, leg: any): number => {
    const ep = entryPrices[i + offset]?.price;
    const v = ep != null && ep !== '' ? ep : (leg.premium ?? leg.mid ?? leg.price ?? 0);
    return Number(v) || 0;
  };
  const stockEntry = (): number => {
    const ep = offset === 1 ? entryPrices[0]?.price : undefined;
    const v = ep != null && ep !== '' ? ep : (params.avg_cost ?? 0);
    return Number(v) || 0;
  };

  const rows: Row[] = useMemo(() => {
    const rs: Row[] = legs.map((leg, i) => {
      const isShort = /sell|short/i.test(leg.action || '');
      const qty = Number(leg.qty ?? leg.contracts ?? 1);
      const t = /put/i.test(leg.type || '') ? 'Put' : 'Call';
      const exp = leg.expiration || leg.expiry || '';
      return {
        kind: 'option' as const, key: `leg-${i}`, legIndex: i,
        label: `${isShort ? 'Short' : 'Long'} ${t} $${leg.strike}`,
        sub: `${qty} contract${qty === 1 ? '' : 's'}${exp ? ` · exp ${exp}` : ''}`,
        qty, entry: optionEntry(i, leg), isShort, mark: markForLeg(i),
      };
    });
    if (shares > 0) {
      rs.push({
        kind: 'stock', key: 'stock', legIndex: null,
        label: `${isShortStock ? 'Short' : 'Long'} stock`,
        sub: `${shares} share${shares === 1 ? '' : 's'}`,
        qty: shares, entry: stockEntry(), isShort: isShortStock,
        mark: pnl?.underlying_price != null ? Number(pnl.underlying_price) : null,
      });
    }
    return rs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade, pnl]);

  const [selected, setSelected] = useState<Set<string>>(() =>
    preselectLeg != null ? new Set([`leg-${preselectLeg}`]) : new Set());
  const [exit, setExit] = useState<Record<string, string>>(() => {
    const m: Record<string, string> = {};
    rows.forEach(r => { if (r.mark != null) m[r.key] = String(r.mark); });
    return m;
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const realizedFor = (r: Row): number | null => {
    const raw = exit[r.key];
    if (raw == null || raw === '') return null;
    const x = Number(raw);
    if (!isFinite(x)) return null;
    const per = r.isShort ? r.entry - x : x - r.entry;
    return r.kind === 'option' ? per * 100 * r.qty : per * r.qty;
  };

  const allSelected = rows.length > 0 && rows.every(r => selected.has(r.key));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(rows.map(r => r.key)));
  const toggle = (k: string) =>
    setSelected(prev => { const n = new Set(prev); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  const selectedRows = rows.filter(r => selected.has(r.key));
  const totalRealized = selectedRows.reduce((s, r) => s + (realizedFor(r) ?? 0), 0);
  const missingPrice = selectedRows.some(r => {
    const v = exit[r.key]; return v == null || v === '' || !isFinite(Number(v));
  });
  const willFullyClose = selectedRows.length === rows.length && rows.length > 0;

  const submit = async () => {
    if (selectedRows.length === 0) { setErr('Select at least one leg or the stock to close.'); return; }
    if (missingPrice) { setErr('Enter a closing price for each selected item.'); return; }
    setSaving(true); setErr(null);
    try {
      const optionLegs = selectedRows
        .filter(r => r.kind === 'option')
        .map(r => ({ leg_index: r.legIndex as number, exit_price: Number(exit[r.key]) }));
      const stockRow = selectedRows.find(r => r.kind === 'stock');
      await closePosition(trade.id, {
        legs: optionLegs,
        close_stock: !!stockRow,
        stock_exit_price: stockRow ? Number(exit['stock']) : null,
      });
      onClosed();
    } catch (e: any) {
      setErr(e?.message || 'Close failed');
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={onClose}>
      <div className="bg-base-100 rounded-2xl border border-white/10 w-full max-w-lg max-h-[90vh] overflow-auto shadow-2xl"
           onClick={e => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/10 sticky top-0 bg-base-100 z-10">
          <LogOut className="w-4 h-4 text-error" />
          <span className="font-semibold">Close position — {trade.ticker}</span>
          <button className="ml-auto btn btn-ghost btn-xs btn-circle" onClick={onClose}><X className="w-4 h-4" /></button>
        </div>

        <div className="p-4 space-y-3">
          <p className="text-xs text-base-content/60">
            Enter the price you actually got out at (per share / per contract). Realized P&amp;L is banked for
            long-term tracking. Closing everything moves the trade to <b>Closed</b>; closing part of it keeps the
            rest open.
          </p>

          {rows.length > 1 && (
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" className="checkbox checkbox-xs" checked={allSelected} onChange={toggleAll} />
              <span className="font-medium">Close entire position</span>
            </label>
          )}

          <div className="space-y-2">
            {rows.map(r => {
              const on = selected.has(r.key);
              const realized = realizedFor(r);
              return (
                <div key={r.key}
                     className={`rounded-lg border p-2.5 transition-colors ${on ? 'border-error/40 bg-error/[0.04]' : 'border-white/10'}`}>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox" className="checkbox checkbox-xs" checked={on} onChange={() => toggle(r.key)} />
                    <div className="min-w-0">
                      <div className="text-sm font-medium truncate">{r.label}</div>
                      <div className="text-[10px] text-base-content/50">{r.sub} · entry {money(r.entry)}</div>
                    </div>
                    <div className="ml-auto flex items-center gap-1.5">
                      <span className="text-[10px] text-base-content/40">exit</span>
                      <input
                        type="number" step="0.01" inputMode="decimal"
                        className="input input-bordered input-xs w-24 text-right"
                        placeholder={r.mark != null ? String(r.mark) : '0.00'}
                        value={exit[r.key] ?? ''}
                        disabled={!on}
                        onChange={e => setExit(m => ({ ...m, [r.key]: e.target.value }))}
                      />
                    </div>
                  </label>
                  {on && realized != null && (
                    <div className="text-right text-[11px] mt-1">
                      realized{' '}
                      <span className={realized >= 0 ? 'text-success font-semibold' : 'text-error font-semibold'}>
                        {realized >= 0 ? '+' : ''}{money(realized)}
                      </span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {selectedRows.length > 0 && (
            <div className="flex items-center justify-between rounded-lg bg-base-200/50 px-3 py-2 text-sm">
              <span className="text-base-content/60">
                {willFullyClose ? 'Total realized (full close)' : 'Realized on this close'}
              </span>
              <span className={totalRealized >= 0 ? 'text-success font-bold' : 'text-error font-bold'}>
                {totalRealized >= 0 ? '+' : ''}{money(totalRealized)}
              </span>
            </div>
          )}

          {err && <div className="text-xs text-error flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" />{err}</div>}
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-white/10 sticky bottom-0 bg-base-100">
          {willFullyClose && <span className="text-[10px] text-warning">Trade will move to Closed</span>}
          <button className="btn btn-ghost btn-sm ml-auto" onClick={onClose} disabled={saving}>Cancel</button>
          <button className="btn btn-error btn-sm gap-1" onClick={submit} disabled={saving || selectedRows.length === 0}>
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogOut className="w-4 h-4" />}
            {willFullyClose ? 'Close entire trade' : 'Close selected'}
          </button>
        </div>
      </div>
    </div>
  );
}
