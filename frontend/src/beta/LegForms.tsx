/**
 * LegForms — the Beta's leg actions, inline in the inspector's Legs section so nobody has to switch to classic:
 *   RollForm     buy back one leg and open a replacement (POST /roll-position — the backend banks the buy-back as a
 *                cost-basis adjustment and keeps the trade active, exactly as classic's Roll does)
 *   EditLegForm  correct a leg's side / type / strike / qty / expiry / entry (PUT the trade with the edited legs)
 *   AddLegForm   append an option leg (PUT the trade with the extra leg)
 *
 * The request bodies come from betaLegEdit.ts (same fields as classic, validated first) and the roll decision aids
 * from lib/rollMath.ts (shared with the classic roll form). Nothing is sent until the user presses Confirm.
 */
import React, { useMemo, useState } from 'react';
import { Loader2, RotateCcw, Pencil, Plus, Target } from 'lucide-react';
import type { SavedStrategyItem, LivePnlResponse } from '../api';
import { rollPosition, updateSavedStrategy } from '../api';
import { fmtMoney } from '../lib/tradeFormat';
import { rollPreview } from '../lib/rollMath';
import {
  rollDefaults, buildRollPayload, editDefaults, buildEditPayload, addDefaults, buildAddPayload, legEntryPrice,
  type RollFields, type EditFields, type AddFields, type Side, type Kind,
} from './betaLegEdit';

const sign = (v: number) => `${v >= 0 ? '+' : '−'}${fmtMoney(Math.abs(v))}`;
const tone = (v: number) => (v >= 0 ? 'text-success' : 'text-error');

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="block min-w-0">
      <span className="text-[11px] text-base-content/55 block mb-1">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-base-content/40 block mt-0.5">{hint}</span>}
    </label>
  );
}

function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { v: T; text: string; cls: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="flex gap-1" role="group" aria-label={label}>
      {options.map(o => (
        <button key={o.v} type="button" onClick={() => onChange(o.v)} aria-pressed={value === o.v}
          className={`px-3 py-1.5 rounded-md text-xs font-semibold border transition-colors ${value === o.v ? o.cls : 'border-white/10 text-base-content/50 hover:border-white/25'}`}>{o.text}</button>
      ))}
    </div>
  );
}
const SIDE = [{ v: 'buy' as Side, text: 'Buy', cls: 'border-success/40 bg-success/10 text-success' }, { v: 'sell' as Side, text: 'Sell', cls: 'border-error/40 bg-error/10 text-error' }];
const KIND = [{ v: 'call' as Kind, text: 'Call', cls: 'border-primary/40 bg-primary/10 text-primary' }, { v: 'put' as Kind, text: 'Put', cls: 'border-primary/40 bg-primary/10 text-primary' }];
const inp = 'input input-bordered input-sm w-full';

function Shell({ title, icon, accent, summary, error, saving, confirm, onConfirm, onCancel, children }: {
  title: string; icon: React.ReactNode; accent: string; summary?: React.ReactNode; error: string | null; saving: boolean;
  confirm: string; onConfirm: () => void; onCancel: () => void; children: React.ReactNode;
}) {
  return (
    <div className={`rounded-xl border p-3 space-y-3 ${accent}`} onKeyDown={e => { if (e.key === 'Escape') onCancel(); }}>
      <div className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</div>
      {children}
      {summary && <div className="text-xs text-base-content/70 leading-relaxed">{summary}</div>}
      {error && <div role="alert" className="text-xs text-error">{error}</div>}
      <div className="flex gap-2">
        <button className="btn btn-primary btn-sm gap-1.5" onClick={onConfirm} disabled={saving}>
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}{confirm}
        </button>
        <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>Cancel</button>
      </div>
    </div>
  );
}

const describe = (a: string, t: string, k: string | number, exp: string) => `${a === 'buy' ? 'long' : 'short'} ${t} ${k}${exp ? ` · ${exp}` : ''}`;

// ── ROLL ─────────────────────────────────────────────────────────────────────

export function RollForm({ trade, pnl, idx, onDone, onCancel }: {
  trade: SavedStrategyItem; pnl?: LivePnlResponse; idx: number; onDone: () => void; onCancel: () => void;
}) {
  const d = useMemo(() => rollDefaults(trade, idx, pnl), [trade.id, idx]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [f, setF] = useState<RollFields>(() => d?.fields as RollFields);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!d) return null;
  const set = <K extends keyof RollFields>(k: K, v: RollFields[K]) => { setF(s => ({ ...s, [k]: v })); setError(null); };

  const leg = (trade.legs_data || [])[idx] || {};
  const oldShort = /sell|short/i.test(leg.action || '');
  const oldQty = Number(leg.qty ?? leg.contracts ?? 1) || 1;
  const rp = rollPreview({
    oldIsShort: oldShort, oldQty, oldEntry: legEntryPrice(trade, idx),
    buyback: parseFloat(f.closePrice), newAction: f.newAction, newPremium: parseFloat(f.newPremium), newQty: Number(f.newContracts) || 1,
    priorRoll: Number(trade.roll?.roll_realized_pnl ?? 0), priorCount: Number(trade.roll?.count ?? 0),
  });

  const submit = async () => {
    const r = buildRollPayload(idx, f);
    if (!r.ok) { setError(r.error); return; }
    setSaving(true); setError(null);
    try { await rollPosition(trade.id, r.data); onDone(); }
    catch (e: any) { setError(e?.message || 'The roll could not be saved.'); setSaving(false); }
  };

  return (
    <Shell title={`Roll leg ${idx + 1}`} icon={<RotateCcw className="w-4 h-4 text-warning" />} accent="border-warning/25 bg-warning/[0.04]"
      error={error} saving={saving} confirm="Confirm roll" onConfirm={submit} onCancel={onCancel}
      summary={<>Closes <b>{describe(oldShort ? 'sell' : 'buy', String(leg.type || '').toLowerCase(), leg.strike, String(leg.expiration || leg.expiry || '').slice(0, 10))}</b> and opens <b>{describe(f.newAction, f.newType, f.newStrike || '—', f.newExpiration)}</b>. The buy-back is banked as a cost-basis adjustment; the trade stays open as one continuing campaign.</>}>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Field label="Buy-back price ($ per share)" hint={d.priceNote || undefined}>
          <input type="number" step="0.01" min="0" className={inp} value={f.closePrice} placeholder="your fill" onChange={e => set('closePrice', e.target.value)} />
        </Field>
        <Field label="New action"><Seg label="New action" value={f.newAction} options={SIDE} onChange={v => set('newAction', v)} /></Field>
        <Field label="New type"><Seg label="New type" value={f.newType} options={KIND} onChange={v => set('newType', v)} /></Field>
        <Field label="Contracts"><input type="number" min={1} step={1} className={inp} value={f.newContracts} onChange={e => set('newContracts', parseInt(e.target.value) || 1)} /></Field>
        <Field label="New strike"><input type="number" step="0.5" min="0" className={inp} value={f.newStrike} onChange={e => set('newStrike', e.target.value)} /></Field>
        <Field label="New expiry"><input type="date" className={inp} value={f.newExpiration} onChange={e => set('newExpiration', e.target.value)} /></Field>
        <Field label="New premium ($ per share)"><input type="number" step="0.01" min="0" className={inp} value={f.newPremium} placeholder="e.g. 1.10" onChange={e => set('newPremium', e.target.value)} /></Field>
      </div>

      {rp.hasAny && (
        <div className="rounded-lg bg-base-100/60 border border-white/[0.08] p-2.5 space-y-2 text-xs">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {rp.realizedClose != null && <div className="flex justify-between gap-2"><span className="text-base-content/50">Realized on buy-back</span><b className={tone(rp.realizedClose)}>{sign(rp.realizedClose)}</b></div>}
            {rp.netRollCash != null && <div className="flex justify-between gap-2"><span className="text-base-content/50">Net roll {rp.netRollCash >= 0 ? 'credit' : 'debit'}</span><b className={tone(rp.netRollCash)}>{sign(rp.netRollCash)}</b></div>}
            {rp.campaignAfter != null && <div className="flex justify-between gap-2"><span className="text-base-content/50">Campaign realized after{rp.priorCount > 0 ? ` (${rp.priorCount} prior)` : ''}</span><b className={tone(rp.campaignAfter)}>{sign(rp.campaignAfter)}</b></div>}
            {rp.newCredit != null && <div className="flex justify-between gap-2"><span className="text-base-content/50">New leg {rp.newCredit >= 0 ? 'credit' : 'debit'}</span><b>{sign(rp.newCredit)}</b></div>}
          </div>
          {rp.targetCredit != null && (
            <div className={`flex items-center gap-2 rounded-md px-2 py-1.5 ${rp.meets == null ? 'bg-base-200/40' : rp.meets ? 'bg-success/10' : 'bg-error/10'}`}>
              <Target className={`w-3.5 h-3.5 shrink-0 ${rp.meets == null ? 'text-warning' : rp.meets ? 'text-success' : 'text-error'}`} />
              <span className="text-base-content/75">
                {rp.targetCredit <= 0
                  ? 'The break-even cushion already covers the buy-back — any credit keeps the campaign green.'
                  : <>To keep the campaign break-even, collect <b>at least {fmtMoney(rp.targetCredit)}</b>{rp.targetPerShare != null ? ` ($${Math.abs(rp.targetPerShare).toFixed(2)}/sh × ${rp.newQty})` : ''} on the new leg.</>}
              </span>
              {rp.meets != null && rp.newCredit != null && rp.targetCredit > 0 && (
                <b className={`ml-auto shrink-0 ${rp.meets ? 'text-success' : 'text-error'}`}>{rp.meets ? 'on track' : `short ${fmtMoney(rp.targetCredit - rp.newCredit)}`}</b>
              )}
            </div>
          )}
        </div>
      )}
    </Shell>
  );
}

// ── EDIT ─────────────────────────────────────────────────────────────────────

export function EditLegForm({ trade, idx, onDone, onCancel }: { trade: SavedStrategyItem; idx: number; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<EditFields | null>(() => editDefaults(trade, idx));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!f) return null;
  const set = <K extends keyof EditFields>(k: K, v: EditFields[K]) => { setF(s => (s ? { ...s, [k]: v } : s)); setError(null); };

  const submit = async () => {
    const r = buildEditPayload(trade, idx, f);
    if (!r.ok) { setError(r.error); return; }
    setSaving(true); setError(null);
    try { await updateSavedStrategy(trade.id, r.data); onDone(); }
    catch (e: any) { setError(e?.message || 'The change could not be saved.'); setSaving(false); }
  };

  return (
    <Shell title={`Edit leg ${idx + 1}`} icon={<Pencil className="w-4 h-4 text-info" />} accent="border-info/25 bg-info/[0.04]"
      error={error} saving={saving} confirm="Save leg" onConfirm={submit} onCancel={onCancel}
      summary={<>Corrects the recorded leg (use this to fix a mistyped strike, quantity or entry). It does <b>not</b> record a trade — to log a fill, use Update; to replace a leg, use Roll.</>}>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Field label="Action"><Seg label="Action" value={f.action} options={SIDE} onChange={v => set('action', v)} /></Field>
        <Field label="Type"><Seg label="Type" value={f.type} options={KIND} onChange={v => set('type', v)} /></Field>
        <Field label="Contracts"><input type="number" min={1} step={1} className={inp} value={f.qty} onChange={e => set('qty', parseInt(e.target.value) || 1)} /></Field>
        <Field label="Strike"><input type="number" step="0.5" min="0" className={inp} value={f.strike} onChange={e => set('strike', e.target.value)} /></Field>
        <Field label="Expiry"><input type="date" className={inp} value={f.expiration} onChange={e => set('expiration', e.target.value)} /></Field>
        <Field label="Entry price ($ per share)"><input type="number" step="0.01" min="0" className={inp} value={f.entry} onChange={e => set('entry', e.target.value)} /></Field>
      </div>
    </Shell>
  );
}

// ── ADD ──────────────────────────────────────────────────────────────────────

export function AddLegForm({ trade, onDone, onCancel }: { trade: SavedStrategyItem; onDone: () => void; onCancel: () => void }) {
  const [f, setF] = useState<AddFields>(addDefaults);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof AddFields>(k: K, v: AddFields[K]) => { setF(s => ({ ...s, [k]: v })); setError(null); };

  const submit = async () => {
    const r = buildAddPayload(trade, f);
    if (!r.ok) { setError(r.error); return; }
    setSaving(true); setError(null);
    try { await updateSavedStrategy(trade.id, r.data); onDone(); }
    catch (e: any) { setError(e?.message || 'The leg could not be added.'); setSaving(false); }
  };

  return (
    <Shell title="Add an option leg" icon={<Plus className="w-4 h-4 text-success" />} accent="border-success/25 bg-success/[0.04]"
      error={error} saving={saving} confirm="Add leg" onConfirm={submit} onCancel={onCancel}
      summary={<>Adds <b>{describe(f.action, f.type, f.strike || '—', f.expiration)}</b> to this trade at the premium you enter. The trade's structure label updates automatically (a short call on a stock position becomes a covered call).</>}>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <Field label="Action"><Seg label="Action" value={f.action} options={SIDE} onChange={v => set('action', v)} /></Field>
        <Field label="Type"><Seg label="Type" value={f.type} options={KIND} onChange={v => set('type', v)} /></Field>
        <Field label="Contracts"><input type="number" min={1} step={1} className={inp} value={f.contracts} onChange={e => set('contracts', parseInt(e.target.value) || 1)} /></Field>
        <Field label="Strike"><input type="number" step="0.5" min="0" className={inp} value={f.strike} onChange={e => set('strike', e.target.value)} /></Field>
        <Field label="Expiry"><input type="date" className={inp} value={f.expiration} onChange={e => set('expiration', e.target.value)} /></Field>
        <Field label="Premium ($ per share)"><input type="number" step="0.01" min="0" className={inp} value={f.premium} placeholder="e.g. 0.65" onChange={e => set('premium', e.target.value)} /></Field>
      </div>
    </Shell>
  );
}
