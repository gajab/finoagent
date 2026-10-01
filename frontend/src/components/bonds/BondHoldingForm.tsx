import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, ClipboardPaste, Info, Loader2, Search, Sparkles, X } from 'lucide-react';
import { createBondHolding, lookupBond, previewBondHolding, updateBondHolding } from '../../api';
import { FUND_PAYOUT_FREQ } from '../../types';
import type { BondHoldingInput, BondKind, BondPreview } from '../../types';
import { ACCOUNTS, ErrorBox, Field, KIND_META, KIND_ORDER, US_STATES, inputCls, num, selectCls } from './bondUi';
import { parseBrokerRow, perHundred } from './bondParse';
import BondValuationPreview from './BondValuationPreview';

const KIND_HELP: Record<BondKind, string> = {
  treasury: 'Bills, notes, bonds — state-tax-free. CUSIP auto-fills + live FedInvest price.',
  tips: 'Inflation-protected Treasuries — principal indexed to CPI.',
  muni: 'Federal-tax-free; state-tax-free in your own state.',
  corporate: 'Investment-grade or high-yield company bonds.',
  agency: 'FHLB / FFCB / Fannie / Freddie debt, often callable.',
  cd: 'Bank or brokered CDs — FDIC-insured to $250k per bank.',
  etf: 'Bond ETFs incl. defined-maturity iBonds / BulletShares.',
  mutual_fund: 'Bond mutual funds (e.g. VBTLX, VWIUX).',
};

const FREQS = [
  { value: 2, label: 'Semiannual' }, { value: 12, label: 'Monthly' }, { value: 4, label: 'Quarterly' },
  { value: 1, label: 'Annual' }, { value: 0, label: 'Zero-coupon / pays at maturity' },
];

const isFundKind = (k: BondKind) => k === 'etf' || k === 'mutual_fund';
const FUND_FREQS: number[] = Object.values(FUND_PAYOUT_FREQ);

const empty = (kind: BondKind): BondHoldingInput => ({
  kind, status: 'held', coupon_freq: kind === 'cd' ? 0 : isFundKind(kind) ? FUND_PAYOUT_FREQ.auto : 2, account_type: 'taxable', amt: false,
});

// Funds keep "how it pays" in coupon_freq and "your yield %" in coupon_rate. Rows saved before these settings
// existed carry the bond default (2) and a coupon value that was never used — start them clean on auto.
const normalize = (h: BondHoldingInput): BondHoldingInput =>
  isFundKind(h.kind) && !FUND_FREQS.includes(h.coupon_freq ?? -1) ? { ...h, coupon_freq: FUND_PAYOUT_FREQ.auto, coupon_rate: null } : h;

const n = (v: string): number | null => (v === '' || v == null ? null : Number(v));
const money = (s: string): number | null => {
  const v = Number(String(s).replace(/[$,\s]/g, ''));
  return s === '' || !Number.isFinite(v) ? null : v;
};
const fmtMoney = (v: number | null | undefined) => (v == null ? '' : String(Math.round(v * 100) / 100));
const GOVT: BondKind[] = ['treasury', 'tips'];

export default function BondHoldingForm({ initial, ladders, onClose, onSaved }: {
  initial?: BondHoldingInput | null;
  ladders?: { id: number; name: string }[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [h, setH] = useState<BondHoldingInput>(initial ? normalize(initial) : empty('treasury'));
  const [step, setStep] = useState<'kind' | 'form'>(initial ? 'form' : 'kind');
  const isFund = h.kind === 'etf' || h.kind === 'mutual_fund';
  const isTips = h.kind === 'tips';
  const qty = (isFund ? h.quantity : h.face_value) ?? null;

  // Statement-style cost inputs. The broker's TOTAL is precise; its "average cost" is usually rounded.
  const initTotal = (): string => {
    if (!initial) return '';
    if (initial.cost_basis != null) return fmtMoney(initial.cost_basis);
    const q = (initial.kind === 'etf' || initial.kind === 'mutual_fund') ? initial.quantity : initial.face_value;
    if (initial.purchase_price != null && q) {
      if (initial.kind === 'etf' || initial.kind === 'mutual_fund') return fmtMoney(initial.purchase_price * q);
      if (initial.kind !== 'tips') return fmtMoney((initial.purchase_price * q) / 100);
    }
    return '';
  };
  const [costTotal, setCostTotal] = useState<string>(initTotal);
  const [avgCost, setAvgCost] = useState<string>('');
  const [brokerValue, setBrokerValue] = useState<string>('');
  const [paste, setPaste] = useState('');
  const [pasteMsg, setPasteMsg] = useState<string | null>(null);
  const [lookupQ, setLookupQ] = useState(initial?.cusip || initial?.ticker || '');
  const [lookupMsg, setLookupMsg] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [preview, setPreview] = useState<BondPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const reqId = useRef(0);
  const set = (patch: Partial<BondHoldingInput>) => setH(prev => normalize({ ...prev, ...patch }));

  // average cost → total (per $1 or per 100 for bonds; per share for funds)
  const onAvg = (v: string) => {
    setAvgCost(v);
    const a = money(v);
    if (a != null && qty) setCostTotal(fmtMoney(isFund ? a * qty : (qty * (perHundred(a) ?? 0)) / 100));
  };
  const avgEcho = useMemo(() => {
    const t = money(costTotal);
    if (!t || !qty) return null;
    return isFund ? `$${num(t / qty, 4)} per share` : `${num((t / qty) * 100, 4)} per 100 face  ($${num(t / qty, 4)} per $1)`;
  }, [costTotal, qty, isFund]);

  const buildBody = (): BondHoldingInput => {
    const body: BondHoldingInput = { ...h };
    const total = money(costTotal);
    if (isFund) {
      body.face_value = null;
      body.cost_basis = total;
      if (total && body.quantity) body.purchase_price = total / body.quantity;
    } else {
      body.ticker = null; body.quantity = null;
      body.cost_basis = total;
      // Nominal bonds: price per 100 = total ÷ face × 100. TIPS: the total includes the index ratio on the
      // purchase date, so leave the real price for the server to derive (unless typed in Advanced).
      if (!isTips && total && body.face_value) body.purchase_price = (total / body.face_value) * 100;
    }
    return body;
  };

  // live valuation preview (debounced)
  useEffect(() => {
    if (step !== 'form') return;
    const t = setTimeout(async () => {
      const id = ++reqId.current;
      setPreviewing(true);
      try {
        const p = await previewBondHolding(buildBody(), money(brokerValue));
        if (id === reqId.current) setPreview(p);
      } catch {
        if (id === reqId.current) setPreview(null);
      } finally {
        if (id === reqId.current) setPreviewing(false);
      }
    }, 650);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [h, costTotal, brokerValue, step]);

  const doLookup = async (q?: string) => {
    const query = (q ?? lookupQ).trim();
    if (!query) return;
    setLooking(true); setLookupMsg(null); setErr(null);
    try {
      const r = await lookupBond(query);
      if (r.type === 'fund') {
        set({ kind: r.kind, ticker: r.ticker, label: h.label || (r.name ?? r.ticker) });
        setLookupMsg(`${r.name} · ${r.distribution_yield_pct ?? '—'}% yield · duration ${r.duration ?? '—'} · expense ratio ${r.expense_ratio_pct ?? '—'}%`);
      } else {
        const kind = (r.kind as BondKind) || h.kind;
        set({
          kind, cusip: r.cusip, issuer: r.issuer ?? h.issuer, coupon_rate: r.coupon_pct ?? h.coupon_rate,
          maturity_date: r.maturity ?? h.maturity_date, issue_date: r.issue_date ?? h.issue_date,
          coupon_freq: r.coupon_freq ?? h.coupon_freq, day_count: r.day_count ?? h.day_count,
          state: r.state ?? h.state, rating: r.rating ?? h.rating, tips_ref_cpi: r.tips_ref_cpi ?? h.tips_ref_cpi,
          label: h.label || (r.description ?? `${kind === 'tips' ? 'TIPS' : r.issuer ?? ''} ${r.coupon_pct ?? ''}% ${r.maturity ?? ''}`.trim()),
          ...(r.federally_taxable_muni ? { federal_taxable: true } : {}),
        });
        setLookupMsg(`${r.source}: ${kind === 'tips' ? 'TIPS' : r.issuer ?? ''} ${r.coupon_pct ?? ''}% due ${r.maturity ?? '?'}`
          + `${r.price ? ` · price ${r.price}` : ''}${r.index_ratio ? ` · index ratio ${r.index_ratio}` : ''}${r.ytm_pct ? ` · ${kind === 'tips' ? 'real ' : ''}yield ${r.ytm_pct}%` : ''}`);
      }
    } catch (e) {
      setLookupMsg(e instanceof Error ? e.message : 'Lookup failed');
    } finally {
      setLooking(false);
    }
  };

  const readPaste = () => {
    const p = parseBrokerRow(paste);
    const got: string[] = [...p.matched];
    const patch: Partial<BondHoldingInput> = {};
    if (p.quantity != null) { if (isFund) patch.quantity = p.quantity; else patch.face_value = p.quantity; }
    if (p.cusip) { patch.cusip = p.cusip; got.push('CUSIP'); setLookupQ(p.cusip); }
    if (p.coupon_pct != null && !isFund) { patch.coupon_rate = p.coupon_pct; got.push('coupon'); }
    if (p.maturity && !isFund) { patch.maturity_date = p.maturity; got.push('maturity'); }
    set(patch);
    const q = p.quantity ?? qty;
    if (p.cost_total != null) setCostTotal(fmtMoney(p.cost_total));
    else if (p.avg_cost != null && q) setCostTotal(fmtMoney(isFund ? p.avg_cost * q : (q * (perHundred(p.avg_cost) ?? 0)) / 100));
    if (p.avg_cost != null) setAvgCost(String(p.avg_cost));
    if (p.current_value != null) setBrokerValue(fmtMoney(p.current_value));
    setPasteMsg(got.length ? `Read: ${got.join(', ')}` : 'Could not recognise columns — paste the header row together with the values.');
    if (p.cusip && !h.maturity_date) doLookup(p.cusip);
  };

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const body = buildBody();
      if (h.id) await updateBondHolding(h.id, body); else await createBondHolding(body);
      onSaved();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[300] flex items-start justify-center overflow-y-auto bg-black/60 p-4 backdrop-blur-sm" onMouseDown={onClose}>
      <div className="mt-8 w-full max-w-3xl rounded-2xl border border-white/10 bg-base-100 shadow-2xl" onMouseDown={e => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-3">
          <div>
            <h2 className="text-sm font-semibold">{h.id ? 'Edit' : 'Add'} {KIND_META[h.kind]?.label ?? 'bond'}</h2>
            <p className="text-[11px] text-base-content/45">{step === 'kind' ? 'What are you adding?' : KIND_HELP[h.kind]}</p>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm btn-circle"><X className="h-4 w-4" /></button>
        </div>

        {step === 'kind' ? (
          <div className="grid grid-cols-2 gap-2 p-5 sm:grid-cols-4">
            {KIND_ORDER.map(k => (
              <button key={k} onClick={() => { setH({ ...empty(k) }); setStep('form'); }}
                className="rounded-xl border border-white/[0.06] bg-base-200/50 p-3 text-left transition hover:border-primary/40 hover:bg-primary/5">
                <div className="text-xs font-bold" style={{ color: KIND_META[k].color }}>{KIND_META[k].label}</div>
                <div className="mt-1 text-[10px] leading-snug text-base-content/50">{KIND_HELP[k]}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="space-y-4 p-5">
            {/* 1 — paste straight from the statement */}
            <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-primary">
                <ClipboardPaste className="h-3 w-3" /> Fastest: paste the row from your broker's positions page (with its header)
              </div>
              <div className="flex gap-2">
                <textarea className="textarea textarea-bordered textarea-sm min-h-[44px] w-full bg-base-200/60 font-mono text-[11px]" rows={2}
                  placeholder={'Current value  Quantity  Average cost basis  Cost basis total\n$25,265.21  25,000  $1.06  $26,551.79'}
                  value={paste} onChange={e => setPaste(e.target.value)} />
                <button className="btn btn-sm btn-primary self-start" onClick={readPaste} disabled={!paste.trim()}>Read it</button>
              </div>
              {pasteMsg && <div className="mt-1 text-[11px] text-base-content/60">{pasteMsg}</div>}
              <div className="mt-2 flex gap-2">
                <input className={inputCls} placeholder={isFund ? 'Ticker, e.g. BND, VWIUX, IBTJ' : 'CUSIP, e.g. 91282CNB3'} value={lookupQ}
                  onChange={e => setLookupQ(e.target.value.toUpperCase())} onKeyDown={e => e.key === 'Enter' && doLookup()} />
                <button className="btn btn-sm btn-outline" onClick={() => doLookup()} disabled={looking}>
                  {looking ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />} Look up
                </button>
              </div>
              {lookupMsg && <div className="mt-1 flex items-start gap-1 text-[11px] text-base-content/60"><Sparkles className="mt-0.5 h-3 w-3 shrink-0 text-primary" />{lookupMsg}</div>}
            </div>

            {/* 2 — what you own, in the broker's own words */}
            <div>
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-base-content/45">What you own — exactly as your statement shows it</div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Field label={isFund ? 'Quantity (shares)' : 'Quantity (face amount)'}
                  hint={isFund ? 'Number of shares' : 'Brokers list bonds by FACE: 25,000 = $25,000 par = 25 bonds of $1,000'}>
                  <input type="number" className={inputCls} placeholder={isFund ? '400' : '25000'}
                    value={(isFund ? h.quantity : h.face_value) ?? ''}
                    onChange={e => (isFund ? set({ quantity: n(e.target.value) }) : set({ face_value: n(e.target.value) }))} />
                </Field>
                <Field label="Cost basis total ($)" hint="The TOTAL from your statement — the most precise number">
                  <input className={inputCls} inputMode="decimal" placeholder="26551.79" value={costTotal} onChange={e => { setCostTotal(e.target.value); setAvgCost(''); }} />
                </Field>
                <Field label={isFund ? 'Average cost / share' : 'Average cost'} hint={isFund ? 'Optional — fills the total' : 'Optional — accepts $ per $1 of face (1.06) or per 100 (106.21)'}>
                  <input className={inputCls} inputMode="decimal" placeholder={isFund ? '72.00' : '1.06 or 106.21'} value={avgCost} onChange={e => onAvg(e.target.value)} />
                </Field>
                <Field label="Current value ($)" hint="Optional — from your statement; we reconcile our value against it">
                  <input className={inputCls} inputMode="decimal" placeholder="25265.21" value={brokerValue} onChange={e => setBrokerValue(e.target.value)} />
                </Field>
                <Field label="Purchase date" hint={isTips ? 'Lets us apply the index ratio you paid' : 'Used for book yield and estimated marks'}>
                  <input type="date" className={inputCls} value={h.purchase_date ?? ''} onChange={e => set({ purchase_date: e.target.value || null })} />
                </Field>
                <div className="col-span-1 flex items-end pb-1.5 text-[10.5px] text-base-content/45 sm:col-span-3">
                  {qty ? <>
                    {!isFund && <>{qty.toLocaleString()} face = {Math.round(qty / 1000).toLocaleString()} bond{Math.round(qty / 1000) === 1 ? '' : 's'} of $1,000{avgEcho ? ' · ' : ''}</>}
                    {avgEcho && <>you paid {avgEcho}{isTips ? ' incl. inflation' : ''}</>}
                  </> : 'Quantity is the face (par) amount for bonds and CDs, shares for funds.'}
                </div>
              </div>
              {isTips && (
                <div className="mt-2 flex gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-2.5 text-[11px] leading-relaxed text-base-content/65">
                  <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                  <span><b>TIPS on a statement:</b> Quantity is the <b>original</b> face (not inflation-adjusted). Current value = Quantity × index ratio × real price ÷ 100.
                    Cost basis total already includes the index ratio on the day you bought. Enter the numbers as shown — add the CUSIP and we apply the index ratio for you.</span>
                </div>
              )}
            </div>

            {/* 3a — funds: how it pays you + your own yield */}
            {isFund && (() => {
              const fr = preview?.row?.fund;
              return (
                <div>
                  <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-base-content/45">How this fund pays you</div>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Field label="Pays out" hint="Box-spread ETFs (BOXX) pay nothing until you sell — the return builds up in the price">
                      <select className={selectCls} value={h.coupon_freq ?? FUND_PAYOUT_FREQ.auto} onChange={e => set({ coupon_freq: Number(e.target.value) })}>
                        <option value={FUND_PAYOUT_FREQ.auto}>Auto-detect{fr?.payout ? ` (${fr.payout === 'accumulates' ? 'accumulates' : 'distributes'})` : ''}</option>
                        <option value={FUND_PAYOUT_FREQ.distributes}>Distributions — cash every month</option>
                        <option value={FUND_PAYOUT_FREQ.accumulates}>Accumulates — paid when sold / at maturity</option>
                      </select>
                    </Field>
                    <Field label="Your yield %" hint="Optional — e.g. the fund's SEC yield. Blank = our estimate">
                      <input type="number" step="0.01" className={inputCls} value={h.coupon_rate ?? ''}
                        placeholder={fr?.est_ytm_pct != null ? `est. ${fr.est_ytm_pct.toFixed(2)}` : fr?.distribution_yield_pct != null ? `${fr.distribution_yield_pct.toFixed(2)}` : 'e.g. 4.10'}
                        onChange={e => set({ coupon_rate: n(e.target.value) })} />
                    </Field>
                    <div className="col-span-2 flex items-end pb-1 text-[10.5px] leading-snug text-base-content/50">
                      {fr ? (
                        <span>
                          {fr.est_ytm_pct != null && <>Estimated yield to maturity <b className="text-base-content/75">{fr.est_ytm_pct.toFixed(2)}%</b>{fr.est_basis ? ` — ${fr.est_basis}` : ''}. </>}
                          Trailing distributions {fr.distribution_yield_pct != null ? `${fr.distribution_yield_pct.toFixed(2)}%` : 'none'}.
                          {fr.payout === 'accumulates' && ' No cash until you sell — taxed as a capital gain then.'}
                        </span>
                      ) : 'Look up the ticker to see our yield estimate.'}
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* 3 — terms (auto-filled by the CUSIP) */}
            {!isFund && (
              <div>
                <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-base-content/45">Bond terms {h.cusip ? '(from CUSIP — edit if needed)' : ''}</div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <Field label="CUSIP"><input className={inputCls} value={h.cusip ?? ''} onChange={e => set({ cusip: e.target.value.toUpperCase() })} /></Field>
                  <Field label={h.kind === 'cd' ? 'Bank' : 'Issuer'}><input className={inputCls} value={h.issuer ?? ''} onChange={e => set({ issuer: e.target.value })} /></Field>
                  <Field label={h.kind === 'cd' && h.coupon_freq === 0 ? 'APY %' : 'Coupon %'}>
                    <input type="number" step="0.001" className={inputCls} value={h.coupon_rate ?? ''} onChange={e => set({ coupon_rate: n(e.target.value) })} />
                  </Field>
                  <Field label="Maturity"><input type="date" className={inputCls} value={h.maturity_date ?? ''} onChange={e => set({ maturity_date: e.target.value || null })} /></Field>
                  <Field label="Pays">
                    <select className={selectCls} value={h.coupon_freq ?? 2} onChange={e => set({ coupon_freq: Number(e.target.value) })}>
                      {FREQS.map(f => <option key={f.value} value={f.value}>{f.label}</option>)}
                    </select>
                  </Field>
                  {(h.kind === 'muni' || h.kind === 'corporate' || h.kind === 'agency') && (
                    <Field label="Rating"><input className={inputCls} value={h.rating ?? ''} placeholder="AA, A+, Baa1…" onChange={e => set({ rating: e.target.value })} /></Field>
                  )}
                  {h.kind === 'muni' && (
                    <Field label="Issuer state">
                      <select className={selectCls} value={h.state ?? ''} onChange={e => set({ state: e.target.value || null })}>
                        <option value="">—</option>{US_STATES.map(s => <option key={s}>{s}</option>)}
                      </select>
                    </Field>
                  )}
                  {!GOVT.includes(h.kind) && (
                    <>
                      <Field label="Next call date"><input type="date" className={inputCls} value={h.call_date ?? ''} onChange={e => set({ call_date: e.target.value || null })} /></Field>
                      <Field label="Call price"><input type="number" step="0.01" className={inputCls} value={h.call_price ?? ''} placeholder="100" onChange={e => set({ call_price: n(e.target.value) })} /></Field>
                    </>
                  )}
                  {isTips && (
                    <Field label="Ref CPI (dated date)" hint="Auto-filled from TreasuryDirect when the CUSIP is known">
                      <input type="number" step="0.00001" className={inputCls} value={h.tips_ref_cpi ?? ''} placeholder="auto from CUSIP" onChange={e => set({ tips_ref_cpi: n(e.target.value) })} />
                    </Field>
                  )}
                </div>
              </div>
            )}

            {/* 4 — the live valuation / reconciliation */}
            <BondValuationPreview preview={preview} loading={previewing}
              onUseBrokerPrice={isFund ? undefined : price => set({ current_price: Math.round(price * 10000) / 10000, price_as_of: new Date().toISOString().slice(0, 10) })} />

            {/* 5 — account */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Field label="Type">
                <select className={selectCls} value={h.kind} onChange={e => set({ kind: e.target.value as BondKind })}>
                  {KIND_ORDER.map(k => <option key={k} value={k}>{KIND_META[k].label}</option>)}
                </select>
              </Field>
              <Field label="Status">
                <select className={selectCls} value={h.status} onChange={e => set({ status: e.target.value as BondHoldingInput['status'] })}>
                  <option value="held">Held (own it)</option><option value="watch">Watchlist</option>
                  <option value="matured">Matured</option><option value="sold">Sold</option>
                </select>
              </Field>
              <Field label="Account">
                <select className={selectCls} value={h.account_type as string} onChange={e => set({ account_type: e.target.value })}>
                  {ACCOUNTS.map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
                </select>
              </Field>
              <Field label="Label"><input className={inputCls} value={h.label ?? ''} placeholder={isFund ? 'Core bonds' : 'TIPS 1.625% 2030'} onChange={e => set({ label: e.target.value })} /></Field>
            </div>

            <button type="button" onClick={() => setAdvanced(a => !a)} className="flex items-center gap-1 text-[11px] text-base-content/50 hover:text-base-content">
              {advanced ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />} Advanced: price override, tax overrides, day count, ladder, notes
            </button>
            {advanced && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Field label={isFund ? 'Price override ($/share)' : 'Price override (per 100)'} hint="Leave blank to use live / estimated pricing">
                  <input type="number" step="0.0001" className={inputCls} value={h.current_price ?? ''} placeholder="auto" onChange={e => set({ current_price: n(e.target.value), price_as_of: e.target.value ? new Date().toISOString().slice(0, 10) : null })} />
                </Field>
                {isTips && (
                  <Field label="Real purchase price (per 100)" hint="Optional: the un-indexed price you paid. Otherwise derived from cost basis ÷ index ratio at purchase">
                    <input type="number" step="0.0001" className={inputCls} value={h.purchase_price ?? ''} placeholder="derived" onChange={e => set({ purchase_price: n(e.target.value) })} />
                  </Field>
                )}
                <Field label="Account name"><input className={inputCls} value={h.account_name ?? ''} placeholder="e.g. Fidelity joint" onChange={e => set({ account_name: e.target.value })} /></Field>
                <Field label="Federal tax" hint="Default follows the bond type">
                  <select className={selectCls} value={h.federal_taxable == null ? '' : String(h.federal_taxable)}
                    onChange={e => set({ federal_taxable: e.target.value === '' ? null : e.target.value === 'true' })}>
                    <option value="">Default</option><option value="true">Taxable</option><option value="false">Exempt</option>
                  </select>
                </Field>
                <Field label="State tax" hint="e.g. FHLB/FFCB agencies are state-exempt">
                  <select className={selectCls} value={h.state_taxable == null ? '' : String(h.state_taxable)}
                    onChange={e => set({ state_taxable: e.target.value === '' ? null : e.target.value === 'true' })}>
                    <option value="">Default</option><option value="true">Taxable</option><option value="false">Exempt</option>
                  </select>
                </Field>
                {!isFund && (
                  <Field label="Day count">
                    <select className={selectCls} value={h.day_count ?? ''} onChange={e => set({ day_count: e.target.value || null })}>
                      <option value="">Default</option><option>30/360</option><option>ACT/ACT</option><option>ACT/360</option><option>ACT/365</option>
                    </select>
                  </Field>
                )}
                {!isFund && <Field label="Issue / dated date"><input type="date" className={inputCls} value={h.issue_date ?? ''} onChange={e => set({ issue_date: e.target.value || null })} /></Field>}
                {!!ladders?.length && (
                  <Field label="Ladder">
                    <select className={selectCls} value={h.ladder_id ?? ''} onChange={e => set({ ladder_id: e.target.value ? Number(e.target.value) : null })}>
                      <option value="">None</option>{ladders.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
                    </select>
                  </Field>
                )}
                {h.kind === 'muni' && (
                  <label className="flex items-center gap-2 text-xs text-base-content/70">
                    <input type="checkbox" className="checkbox checkbox-xs" checked={!!h.amt} onChange={e => set({ amt: e.target.checked })} /> AMT (private activity)
                  </label>
                )}
                <Field label="Notes" className="col-span-2 sm:col-span-4">
                  <textarea className="textarea textarea-bordered textarea-sm w-full bg-base-200/60" rows={2} value={h.notes ?? ''} onChange={e => set({ notes: e.target.value })} />
                </Field>
              </div>
            )}

            {err && <ErrorBox message={err} />}
            <div className="flex items-center justify-between gap-2 pt-1">
              {!h.id ? <button className="btn btn-ghost btn-sm" onClick={() => setStep('kind')}>← Type</button> : <span />}
              <div className="flex gap-2">
                <button className="btn btn-ghost btn-sm" onClick={onClose}>Cancel</button>
                <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
                  {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />} {h.id ? 'Save changes' : 'Add to book'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
