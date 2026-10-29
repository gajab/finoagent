import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ArrowRight, Calculator, Check, ShieldCheck, ChevronDown, ChevronRight, SlidersHorizontal, Info, Pencil, Undo2 } from 'lucide-react';
import { Chart as ChartJS, LinearScale, PointElement, LineElement, Tooltip, Legend } from 'chart.js';
import { Line } from 'react-chartjs-2';
import { createPortal } from 'react-dom';
import { computeLeapProtection } from '../api';
import type { LeapProtectionRequest, LeapProtectionResponse, LeapStrategyRow, LeapLedgerLine } from '../types';

ChartJS.register(LinearScale, PointElement, LineElement, Tooltip, Legend);

// ---------------------------------------------------------------- formatting
const usd = (n: number | null | undefined) =>
    n == null || !Number.isFinite(n) ? '—' : `${n < 0 ? '−' : ''}$${Math.abs(Math.round(n)).toLocaleString()}`;
const signed = (n: number) => `${n > 0.5 ? '+' : ''}${usd(n)}`;
const usdK = (n: number) => {
    const a = Math.abs(n);
    const s = a >= 1e6 ? `$${(a / 1e6).toFixed(2)}M` : a >= 1e4 ? `$${Math.round(a / 1e3)}k` : `$${Math.round(a).toLocaleString()}`;
    return `${n < 0 ? '−' : ''}${s}`;
};
const pct = (n: number | null | undefined, d = 0) =>
    n == null || !Number.isFinite(n) ? '—' : `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(d)}%`;
const px = (n: number) => `$${n >= 100 ? Math.round(n).toLocaleString() : n.toFixed(2)}`;
const vsToday = (v: number, base: number) => (v / base - 1) * 100;
const signTone = (p: number) => (p > 0.05 ? 'text-success' : p < -0.05 ? 'text-error' : 'text-base-content/80');
const asDate = (iso: string, opts: Intl.DateTimeFormatOptions) => {
    const t = new Date(`${iso}T00:00:00`);
    return Number.isNaN(+t) ? iso : t.toLocaleDateString('en-US', opts);
};
const monthYear = (iso: string) => asDate(iso, { month: 'short', year: 'numeric' });
const fullDate = (iso: string) => asDate(iso, { month: 'short', day: 'numeric', year: 'numeric' });

// Categorical series colours, fixed order (validated dark steps of the reference palette). Colour follows the
// entity: a plan is always blue, holding always orange, selling today always green.
const C_PLAN = '#3987e5', C_HOLD = '#d95926', C_SELL = '#199e70', C_TODAY = 'rgba(195,194,183,0.45)';
const colorOf = (r: LeapStrategyRow) => (r.id === 'hold' ? C_HOLD : r.id === 'sell_now' ? C_SELL : C_PLAN);
const OBJ = { cagr: ['cagr_pct', 'Expected growth'], ce: ['ce_ann_pct', 'Risk-adjusted return'], mean: ['mean_ann_pct', 'Average return'] } as const;
const OBJ_TIP = {
    cagr: 'Compound growth per year with every outcome weighed by its chance — big swings cost return.',
    ce: 'The sure return per year you would swap the whole range of outcomes for (risk-averse).',
    mean: 'Average outcome per year, every outcome weighed by its chance.',
};
const OBJ_SHORT = { cagr: 'Growth /yr', ce: 'Risk-adj. /yr', mean: 'Average /yr' };
type Mode = 'market' | 'stock_only';
type Objective = LeapProtectionRequest['objective'];

const total = (row: LeapStrategyRow, mode: Mode, i: number) => row.payout[mode].values[i].reduce((a, b) => a + b, 0);

// ---------------------------------------------------------------- small pieces
/** (i) with an explanation that opens on hover, keyboard focus or tap. Rendered in a portal at a fixed position, so a
 *  table's overflow can't clip it, and kept inside the viewport (above the icon when there is no room below). */
function Tip({ text }: { text: string }) {
    const ref = useRef<HTMLButtonElement>(null);
    const [pos, setPos] = useState<{ x: number; y: number; up: boolean } | null>(null);
    const show = () => {
        const r = ref.current?.getBoundingClientRect();
        if (!r) return;
        const half = Math.min(136, window.innerWidth / 2 - 12);
        const up = r.bottom + 170 > window.innerHeight && r.top > 170;
        setPos({ x: Math.min(Math.max(r.left + r.width / 2, half + 12), window.innerWidth - half - 12), y: up ? r.top - 6 : r.bottom + 6, up });
    };
    const hide = () => setPos(null);
    useEffect(() => {
        if (!pos) return;
        window.addEventListener('scroll', hide, true);
        return () => window.removeEventListener('scroll', hide, true);
    }, [pos]);
    return (
        <>
            <button ref={ref} type="button" aria-label={text} onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide}
                onClick={e => { e.stopPropagation(); if (pos) hide(); else show(); }}
                className={`shrink-0 inline-flex rounded-full cursor-help outline-none transition-colors ${pos ? 'text-secondary' : 'text-base-content/50 hover:text-base-content/85 focus-visible:text-base-content/85'}`}>
                <Info className="w-3.5 h-3.5" />
            </button>
            {pos && createPortal(
                <div role="tooltip" style={{ left: pos.x, top: pos.y, transform: `translate(-50%, ${pos.up ? '-100%' : '0'})` }}
                    className="fixed z-[1000] w-[272px] max-w-[calc(100vw-24px)] pointer-events-none rounded-lg border border-base-300 bg-base-100 px-3 py-2 text-xs font-normal normal-case tracking-normal leading-snug text-base-content shadow-xl">
                    {text}
                </div>, document.body)}
        </>
    );
}

function Dot({ c }: { c: string }) {
    return <span className="inline-block w-2.5 h-2.5 rounded-full shrink-0" style={{ background: c }} />;
}

function Eyebrow({ children, tip }: { children: React.ReactNode; tip?: string }) {
    return <div className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-base-content/55">{children}{tip && <Tip text={tip} />}</div>;
}

const TONE = { good: 'text-success', bad: 'text-error', warn: 'text-warning', plain: '' } as const;

function Tile({ label, tip, value, sub, tone = 'plain' }: { label: string; tip?: string; value: string; sub?: string; tone?: keyof typeof TONE }) {
    return (
        <div className="rounded-lg bg-base-200/60 px-3 py-2.5 min-w-0">
            <Eyebrow tip={tip}><span>{label}</span></Eyebrow>
            <div className={`text-2xl font-semibold tabular-nums leading-tight mt-1 ${TONE[tone]}`}>{value}</div>
            {sub && <div className="text-xs text-base-content/55 mt-0.5 leading-snug">{sub}</div>}
        </div>
    );
}

// ---------------------------------------------------------------- the plan: band · four numbers · ticket
/** Where the plan protects you, where you ride the stock and where you give gains up — on the stock's price at expiry. */
function ProtectionBand({ d, row }: { d: LeapProtectionResponse; row: LeapStrategyRow }) {
    const s = row.shape, S = d.spot, tk = d.ticker;
    const P = s.protect_below, C = s.cap_above;
    if (P == null && C == null) return null;
    const prices = d.grid.prices, lo = prices[0], hi = prices[prices.length - 1];
    const x = (p: number) => Math.min(100, Math.max(0, ((p - lo) / (hi - lo)) * 100));
    const preTaxOnly = P != null && !s.floored;
    const segs: { a: number; b: number; label: string; cls: string; tip: string }[] = [];
    if (P != null) segs.push({
        a: 0, b: x(P), label: preTaxOnly ? 'Protected before tax' : 'Protected',
        cls: preTaxOnly ? 'bg-success/10 text-success/80' : 'bg-success/20 text-success',
        tip: preTaxOnly
            ? `Below $${P} the puts pay the difference — but that payout is taxed while the shares' fall isn't, so after tax you still lose some.`
            : `Below $${P} the puts pay the difference: your value stops falling.`,
    });
    segs.push({
        a: P != null ? x(P) : 0, b: C != null ? x(C) : 100,
        label: P == null ? 'Unprotected' : C == null ? 'Gains are all yours' : `You ride ${tk}`,
        cls: P == null ? 'bg-error/10 text-error/80' : 'bg-base-300/80 text-base-content/75',
        tip: P == null ? `No floor: below $${C} you take ${tk}'s fall in full.` : `Between the strikes you gain and lose with ${tk}.`,
    });
    if (C != null) segs.push({ a: x(C), b: 100, label: 'Gains given up', cls: 'bg-warning/15 text-warning', tip: `Above $${C} the calls take the gain: your value stops rising.` });
    const strike = (k: number, side: 'left' | 'right') => (
        <div className={`absolute top-0 whitespace-nowrap ${side === 'left' ? '-translate-x-full pr-1 text-right' : 'pl-1'}`} style={{ left: `${x(k)}%` }}>
            <span className="font-semibold text-base-content/85">${k.toLocaleString()}</span> <span className="text-base-content/50">{pct(vsToday(k, S))}</span>
        </div>
    );
    return (
        <div>
            <Eyebrow>{tk} price at expiry</Eyebrow>
            <div className="relative h-5 mt-1 text-[11px]">
                <div className="absolute bottom-0 -translate-x-1/2 whitespace-nowrap text-base-content/70" style={{ left: `${x(S)}%` }}>today ${S.toLocaleString()} ▾</div>
            </div>
            <div className="relative flex h-8 rounded-md overflow-hidden text-[11px] font-medium">
                {segs.map(g => (
                    <div key={g.label} title={g.tip} className={`grid place-items-center px-1 min-w-0 ${g.cls}`} style={{ width: `${g.b - g.a}%` }}>
                        <span className="truncate max-w-full">{g.label}</span>
                    </div>
                ))}
                <div className="absolute top-0 h-1.5 w-0.5 -translate-x-1/2 bg-base-content/80" style={{ left: `${x(S)}%` }} />
                <div className="absolute bottom-0 h-1.5 w-0.5 -translate-x-1/2 bg-base-content/80" style={{ left: `${x(S)}%` }} />
            </div>
            <div className="relative h-5 mt-1 text-[11px]">
                {P != null && strike(P, 'left')}
                {C != null && strike(C, 'right')}
            </div>
        </div>
    );
}

function FitChips({ d, row }: { d: LeapProtectionResponse; row: LeapStrategyRow }) {
    const cs = row.constructive_sale.risk;
    return (
        <div className="flex flex-wrap gap-1.5">
            {row.family !== 'reference' && (row.eligible
                ? <span className="badge badge-success badge-outline gap-1 h-6"><Check className="w-3 h-3" />Fits your limits</span>
                : <span className="badge badge-warning badge-outline h-6 cursor-help" title={[row.recommended ? d.best_note : null, ...row.reasons].filter(Boolean).join(' — ')}>
                    {row.recommended ? 'Closest fit' : 'Outside limits'} · {row.reason_tags.join(' · ')}</span>)}
            {(cs === 'elevated' || cs === 'high') && (
                <span className={`badge badge-outline h-6 cursor-help ${cs === 'high' ? 'badge-error' : 'badge-warning'}`} title={row.constructive_sale.note}>Taxed-as-sale risk: {cs}</span>
            )}
        </div>
    );
}

type TicketLine = { act: string; qty: string; what: string; sub?: string; when?: string; price?: string; cash: number | null; firstOnly?: boolean; tip?: string };

function TradeTicket({ d, row, roomy }: { d: LeapProtectionResponse; row: LeapStrategyRow; roomy: boolean }) {
    const N = d.shares, tk = d.ticker;
    if (row.id === 'hold') {
        return <div className="rounded-lg border border-base-300 px-3 py-2.5 text-sm text-base-content/70">No trade — you keep the {N.toLocaleString()} shares as they are.</div>;
    }
    const lines: TicketLine[] = [];
    if (row.id === 'sell_now') {
        lines.push({ act: 'SELL', qty: N.toLocaleString(), what: `${tk} shares`, price: `$${d.spot}`, cash: d.position_value });
        lines.push({ act: 'PAY', qty: '', what: 'Long-term capital-gains tax', cash: -d.embedded_tax });
    }
    row.legs.forEach((l, i) => {
        const und = l.und === 'stock' ? tk : l.und;
        if (l.type === 'futures') {
            lines.push({ act: l.action, qty: l.contracts_note ?? '', what: `${und} futures`, sub: `${usdK(l.notional ?? 0)} exposure · no cash up front`, when: monthYear(l.expiry), price: `$${l.strike}`, cash: null, tip: row.how[i] });
            return;
        }
        lines.push({
            act: l.action, qty: String(l.contracts ?? ''), what: `${und} $${l.strike} ${l.type}`,
            sub: [l.pct_of_spot != null ? `${pct(l.pct_of_spot)}${roomy ? ' vs today' : ''}` : null, l.twin ? `as ${l.twin} · European` : null].filter(Boolean).join(' · ') || undefined,
            when: monthYear(l.expiry),
            price: l.px != null ? `$${l.px.toFixed(2)}` : '—', cash: (l.action === 'SELL' ? 1 : -1) * Math.abs(l.qty_per_share) * (l.px ?? 0) * N, tip: row.how[i],
        });
    });
    row.rolls.forEach((r, j) => lines.push({
        act: r.action, qty: String(r.contracts), what: `${tk} ${r.type}s every ~${r.cycle_days} days`, sub: r.rule, when: `first ${monthYear(r.first_expiry)}`,
        price: `$${r.first_bid.toFixed(2)}`, cash: r.first_bid * r.contracts * 100, firstOnly: true, tip: row.how[row.legs.length + j],
    }));
    if (row.loan_total > 0.5) lines.push({ act: 'BORROW', qty: '', what: 'Against the protected shares', cash: row.loan_total });
    const net = lines.reduce((a, l) => a + (l.cash != null && !l.firstOnly ? l.cash : 0), 0);
    const m = row.money, dv = row.dividends;
    const th = 'font-normal py-1.5';
    const by = `by ${monthYear(d.horizon.expiry)}`;
    return (
        <div className="rounded-lg border border-base-300 overflow-hidden">
            <table className="w-full text-sm">
                <thead className="text-[11px] uppercase tracking-wide text-base-content/50 bg-base-200/40">
                    <tr>
                        <th className={`${th} text-left pl-3 w-px`}>Trade</th>
                        <th className={`${th} text-right px-3 w-px`}>Qty</th>
                        <th className={`${th} text-left`}>Contract</th>
                        {roomy && <th className={`${th} text-left`}>Expires</th>}
                        {roomy && <th className={`${th} text-right`}>Price</th>}
                        <th className={`${th} text-right pr-3`}>{roomy ? 'Cash today' : 'Cash'}</th>
                    </tr>
                </thead>
                <tbody>
                    {lines.map((l, i) => (
                        <tr key={i} title={l.tip} className="border-t border-base-300/60 align-top">
                            <td className="pl-3 py-2"><span className="text-[11px] font-bold tracking-wide px-1.5 py-0.5 rounded bg-base-300/80">{l.act}</span></td>
                            <td className="px-3 py-2 text-right tabular-nums">{l.qty}</td>
                            <td className="py-2 pr-2">
                                <div className="font-medium">{l.what}</div>
                                {(l.sub || (!roomy && l.when)) && <div className="text-[11px] text-base-content/50">{[!roomy ? l.when : null, l.sub].filter(Boolean).join(' · ')}</div>}
                            </td>
                            {roomy && <td className="py-2 pr-2 whitespace-nowrap text-base-content/75">{l.when}</td>}
                            {roomy && <td className="py-2 pr-2 text-right tabular-nums text-base-content/75">{l.price}</td>}
                            <td className={`pr-3 py-2 text-right tabular-nums whitespace-nowrap ${l.firstOnly ? 'text-base-content/50' : ''}`}>
                                {l.cash == null ? '—' : signed(l.cash)}{l.firstOnly && <div className="text-[11px]">first sale</div>}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <div className="bg-base-200/60 border-t border-base-300 text-sm divide-y divide-base-300/60">
                <FlowRow label={net < -0.5 ? 'Net cost today' : 'Net today'}>
                    <span className="font-semibold tabular-nums">{Math.abs(net) < 0.5 ? '≈ $0' : signed(net)}</span>
                    {m && (<>
                        <ArrowRight className="w-3.5 h-3.5 text-base-content/40" />
                        <span>into {m.where}{m.kept_as_collateral ? ' (held as collateral)' : ''}</span>
                        <ArrowRight className="w-3.5 h-3.5 text-base-content/40" />
                        <span className="font-semibold tabular-nums">{m.expected ? '≈ ' : ''}{usd(m.at_expiry_total)}</span>
                        <span className="text-base-content/55">{by}, after tax <span className="text-success">{pct(vsToday(m.at_expiry_total, m.today_total), 1)}</span></span>
                    </>)}
                </FlowRow>
                {dv && (
                    <FlowRow label="Dividends" tip={`${d.ticker}'s next ${dv.payments} payments at the latest amount (no growth assumed), taxed at ${dv.rate_pct}%${dv.qualified ? ' (qualified)' : ` — ordinary income: ${dv.why}`}. They go where the premium goes and are in every number.`}>
                        <span className="tabular-nums">{dv.payments} × ${dv.per_payment?.toFixed(2) ?? '—'} = {usd(dv.gross_total)}</span>
                        <ArrowRight className="w-3.5 h-3.5 text-base-content/40" />
                        <span className="tabular-nums">{usd(dv.after_tax_total)} after tax</span>
                        <span className={`badge badge-sm ${dv.qualified ? 'badge-ghost' : 'badge-warning badge-outline'}`}>{dv.rate_pct}%{dv.qualified ? ' qualified' : ' ordinary'}</span>
                        <ArrowRight className="w-3.5 h-3.5 text-base-content/40" />
                        <span>into {dv.where}</span>
                        <ArrowRight className="w-3.5 h-3.5 text-base-content/40" />
                        <span className="font-semibold tabular-nums">{dv.expected ? '≈ ' : ''}{usd(dv.at_expiry_total)}</span>
                        <span className="text-base-content/55">{by}</span>
                    </FlowRow>
                )}
            </div>
        </div>
    );
}

function PlanCard({ d, row, hold, objective, wide, roomy, onReset }: {
    d: LeapProtectionResponse; row: LeapStrategyRow; hold: LeapStrategyRow; objective: Objective; wide: boolean; roomy: boolean; onReset: () => void;
}) {
    const [k, kLabel] = OBJ[objective];
    const W0 = d.starting_wealth, s = row.shape, tk = d.ticker;
    const mine = row[k] as number, theirs = hold[k] as number;
    const isHold = row.id === 'hold';
    const down = Math.abs(d.grid.moves[0]), up = d.grid.moves[d.grid.moves.length - 1];
    const worst = s.floored
        ? { value: pct(vsToday(s.floor_value!, W0)), sub: `${pct(s.floor_ann_pct, 1)}/yr${s.protect_below ? ` · below $${s.protect_below}` : ''}`, tone: 'plain' as const }
        : s.pretax_floored
            ? { value: pct(s.worst_pct), sub: `${pct(s.pretax_floor_pct)} before tax · put payout taxed`, tone: 'warn' as const }
            : { value: 'No floor', sub: `${pct(s.worst_pct)} if ${tk} −${down}%`, tone: 'bad' as const };
    const worstTip = s.floored
        ? 'The least you can end with at expiry, after tax, however far the stock falls.'
        : s.pretax_floored
            ? `The put stops the loss at ${pct(s.pretax_floor_pct)} BEFORE tax. But a put on shares you own is a straddle (§1092): its payout is taxed as a short-term gain (${d.assumptions.st_rate_pct}%), while the shares' fall ${d.assumptions.tax_lens === 'defer' ? 'saves no tax — you never sell' : `only saves long-term tax (${d.assumptions.lt_rate_pct}%) when you sell`}. So after tax the value keeps sliding the further ${tk} falls: ${pct(s.worst_pct)} if it falls ${down}%. The tax-adjusted collar buys extra puts to cover that tax.`
            : `Nothing stops the fall: ${pct(s.worst_pct)} if ${tk} falls ${down}%.`;
    const best = s.capped
        ? { value: pct(vsToday(s.cap_value!, W0)), sub: `${pct(s.cap_ann_pct, 1)}/yr${s.p_cap_pct != null ? ` · ${s.p_cap_pct}% chance` : ''}`, tone: 'plain' as const }
        : { value: 'No cap', sub: `${pct(s.best_pct)} if ${tk} +${up}%`, tone: 'plain' as const };
    const bestTip = s.capped && s.cap_above
        ? `The most you can end with — reached only if ${tk} is at or above $${s.cap_above} at expiry (${s.p_cap_pct}% chance, with ${tk} expected to return ${d.market.stock_drift_pct}%/yr).`
        : s.capped ? 'The most you can end with at expiry, after tax.' : 'Gains keep coming however far the stock rises.';
    return (
        <div className={`rounded-xl border p-4 sm:p-5 space-y-4 ${row.recommended ? 'border-secondary/50 bg-secondary/[0.04]' : 'border-base-300'}`}>
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                <div className="min-w-0">
                    <Eyebrow><ShieldCheck className="w-3.5 h-3.5" />{row.recommended ? 'Recommended' : 'Looking at'} · {row.family_label}</Eyebrow>
                    <div className="text-lg sm:text-xl font-bold leading-snug mt-0.5">{row.name}</div>
                    <div className="text-xs text-base-content/55 mt-0.5">Every number is after tax, at expiry on {fullDate(d.horizon.expiry)} · {d.horizon.years.toFixed(1)} years</div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <FitChips d={d} row={row} />
                    {!row.recommended && <button type="button" className="btn btn-ghost btn-xs gap-1" onClick={onReset}><Undo2 className="w-3.5 h-3.5" />Recommended plan</button>}
                </div>
            </div>
            <ProtectionBand d={d} row={row} />
            <div className={`grid gap-2 ${wide ? 'grid-cols-4' : 'grid-cols-2'}`}>
                <Tile label="Worst case" tip={worstTip} {...worst} />
                <Tile label="Best case" tip={bestTip} {...best} />
                <Tile label={kLabel} tip={OBJ_TIP[objective]} value={`${pct(mine, 1)}/yr`} sub={isHold ? undefined : `holding: ${pct(theirs, 1)}/yr`} />
                <Tile label="Bad case · 1 in 20" tip="Average of the worst 5% of outcomes at expiry, after tax." value={pct(row.cvar5_pct)}
                    sub={isHold ? undefined : `holding: ${pct(hold.cvar5_pct)}`} />
            </div>
            <TradeTicket d={d} row={row} roomy={roomy} />
            <EarlyAssignment d={d} row={row} roomy={roomy} />
        </div>
    );
}

function FlowRow({ label, tip, children }: { label: string; tip?: string; children: React.ReactNode }) {
    return (
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-3 py-2.5">
            <span className="w-24 shrink-0 flex items-center gap-1 text-base-content/60">{label}{tip && <Tip text={tip} />}</span>
            {children}
        </div>
    );
}

const LEVEL = {
    none: ['badge-ghost', 'none'], low: ['badge-success badge-outline', 'low'],
    medium: ['badge-warning badge-outline', 'medium'], high: ['badge-error badge-outline', 'high'],
} as const;

/** Every written option: can it be exercised before expiry, how likely that is, what it would do, what to do instead. */
function EarlyAssignment({ d, row, roomy }: { d: LeapProtectionResponse; row: LeapStrategyRow; roomy: boolean }) {
    if (!row.assignment.length) return null;
    const rule = 'Share of the simulated paths on which exercising early pays the option\'s buyer. A call is only exercised just before an '
        + 'ex-dividend date, when the dividend beats the interest on the strike; a put when it is deep in the money. '
        + 'Every number above already assumes you buy it back (or roll it) at that point — this is how likely you are to need to.';
    return (
        <div className="rounded-lg border border-base-300">
            <div className="flex items-center justify-between gap-2 px-3 py-2 bg-base-200/40 border-b border-base-300">
                <Eyebrow tip={rule}>Early assignment — before expiry</Eyebrow>
            </div>
            <div className="divide-y divide-base-300/60">
                {row.assignment.map(a => {
                    const [cls, word] = LEVEL[a.level];
                    const pctText = !a.american ? 'can\'t happen' : a.p_pct > 0 ? `${a.p_pct < 1 ? '<1' : Math.round(a.p_pct)}% chance` : '≈0%';
                    return (
                        <div key={a.leg} className={`px-3 py-2.5 grid gap-x-4 gap-y-1 text-sm ${roomy ? 'grid-cols-[minmax(9rem,auto)_minmax(8rem,auto)_1fr]' : 'grid-cols-1'}`}>
                            <div className="font-medium">{a.contracts} × {a.leg}</div>
                            <div className="flex items-center gap-1.5">
                                <span className={`badge badge-sm ${cls}`}>{word}</span>
                                <span className="tabular-nums text-base-content/80">{pctText}</span>
                                {a.when && <span className="text-xs text-base-content/50">· around {monthYear(a.when)}</span>}
                            </div>
                            <div className="text-xs text-base-content/65 leading-snug">
                                {!a.american
                                    ? <div className="flex items-start gap-1">{a.action}{a.note && <Tip text={a.note} />}</div>
                                    : a.level === 'none'
                                        ? <div className="flex items-start gap-1">
                                            {a.trigger === 'ex-dividend' ? `${d.ticker}'s dividend is too small for early exercise to pay the buyer.` : 'Never deep enough in the money for early exercise to pay.'}
                                            <Tip text={`If it did happen: ${a.if_assigned}. What to do: ${a.action}`} />
                                        </div>
                                        : <>{a.if_assigned && <div><span className="text-base-content/45">If it happens: </span>{a.if_assigned}</div>}
                                            <div><span className="text-base-content/45">Instead: </span>{a.action}</div></>}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ---------------------------------------------------------------- at expiry: scenario table · chart · breakdown
/** One expiry ledger line in a few words: a label and a short note. */
function ledgerLabel(l: LeapLedgerLine, amount: number, d: LeapProtectionResponse, early: boolean): { label: string; note?: string } {
    switch (l.kind) {
        case 'shares': return { label: `${d.shares.toLocaleString()} ${d.ticker} shares` };
        case 'divs': return { label: 'Dividends', note: `${l.count} payments, after ${l.rate_pct}% tax` };
        case 'rolls': return { label: 'Rolled options', note: 'premium kept − buy-backs' };
        case 'cash': return { label: 'Cash', note: 'premium + interest, after tax' };
        case 'sleeve': return l.dest === 'index'
            ? { label: `${d.index} bought today`, note: 'with its dividends' }
            : { label: `${d.destination.rate_pct}% bond`, note: 'coupons after tax' };
        case 'loan': return { label: 'Loan repaid', note: 'with interest' };
        case 'tax': return { label: 'Tax', note: 'on the options' };
        case 'liq_tax': return { label: 'Tax on a full sale', note: 'selling everything at expiry' };
        case 'leg': {
            if (l.type === 'futures') return { label: `${d.index} futures (${l.action === 'BUY' ? 'long' : 'short'})`, note: 'settled' };
            const name = `${l.contracts} × ${l.und === 'stock' ? '' : `${d.index} `}$${l.strike} ${l.type}${l.contracts === 1 ? '' : 's'}`;
            const per = l.units ? Math.abs(amount / l.units) : 0;
            if (early) return { label: name, note: `${l.type === 'call' ? 'bought back before an ex-dividend date' : 'closed early, deep in the money'} at $${per.toFixed(2)}` };
            if (Math.abs(amount) < 0.5) return { label: name, note: `expire worthless${l.action === 'SELL' ? ' · premium kept' : ''}` };
            if (l.action === 'BUY') return { label: name, note: `pay $${per.toFixed(2)} each` };
            if (l.und === 'stock' && l.type === 'call') return { label: name, note: `bought back at $${per.toFixed(2)} · shares kept` };
            return { label: name, note: `you pay $${per.toFixed(2)} each` };
        }
    }
}

const LEDGER_GROUPS: { label: string; kinds: LeapLedgerLine['kind'][] }[] = [
    { label: 'What you hold', kinds: ['shares', 'divs', 'sleeve', 'cash'] },
    { label: 'Options at expiry', kinds: ['leg', 'rolls'] },
    { label: 'Loan & tax', kinds: ['loan', 'tax', 'liq_tax'] },
];

const HOLDING_KINDS: LeapLedgerLine['kind'][] = ['shares', 'divs', 'sleeve', 'cash'];

/** The expiry outcome at one price as a bridge: each line moves a running total from where the line above left it (what
 *  you hold builds it up, the options and tax move it), so the lines add up exactly to "You end with". Every bar sits on
 *  ONE dollar scale shared with the end value, today's value (the vertical line) and holding / selling today, so bar
 *  lengths are dollars you can compare. Laid out by the panel's own width; below 400px the bar drops under its line. */
function Breakdown({ d, row, mode, idx, others }: {
    d: LeapProtectionResponse; row: LeapStrategyRow; mode: Mode; idx: number; others: { r: LeapStrategyRow; label: string }[];
}) {
    const [ref, w] = useWidth<HTMLDivElement>();
    const compact = w > 0 && w < 400;
    const led = row.payout[mode], vals = led.values[idx], W0 = d.starting_wealth, tk = d.ticker;
    const end = vals.reduce((a, b) => a + b, 0);
    const idxMove = mode === 'market' ? d.grid.index_moves_market[idx] : 0;

    let run = 0;
    const groups = LEDGER_GROUPS.map(g => ({
        label: g.label,
        rows: led.lines.map((l, i) => ({ l, i })).filter(({ l }) => g.kinds.includes(l.kind)).map(({ l, i }) => {
            const from = run;
            run += vals[i];
            return { l, i, a: vals[i], from, to: run };
        }),
    })).filter(g => g.rows.length);
    const alts = others.map(o => ({ ...o, v: total(o.r, mode, idx) }));
    const pts = [0, W0, end, ...groups.flatMap(g => g.rows.map(r => r.to)), ...alts.map(o => o.v)];
    const lo = Math.min(...pts), top = Math.max(...pts), hi = top + (top - lo) * 0.03 || 1;
    const x = (v: number) => ((v - lo) / (hi - lo)) * 100;
    const xToday = x(W0);
    const cols = { gridTemplateColumns: compact ? 'minmax(0,1fr) auto' : w >= 560 ? 'minmax(0,1.2fr) minmax(0,1fr) 7rem' : 'minmax(0,1.4fr) minmax(0,1fr) 6.5rem' };

    // the bar column: today's value as a line that runs unbroken through every row, and the bar from → to
    const bar = (from: number, to: number, cls: string, h: string, color?: string, hover?: string) => {
        const a = x(Math.min(from, to)), b = x(Math.max(from, to));
        return (
            <>
                {lo < 0 && <div className="absolute inset-y-0 w-px bg-base-content/20" style={{ left: `${x(0)}%` }} />}
                <div className="absolute inset-y-0 w-px bg-base-content/25" style={{ left: `${xToday}%` }} />
                {Math.abs(to - from) >= 0.5 && (
                    <div className={`absolute top-1/2 -translate-y-1/2 rounded-[3px] ${h} ${cls}`} style={{ left: `${a}%`, width: `max(2px, ${b - a}%)`, background: color }} />
                )}
                {hover && (
                    <span className={`absolute top-0.5 whitespace-nowrap text-[10px] leading-none text-base-content/60 opacity-0 transition-opacity group-hover:opacity-100 ${x(to) > 70 ? '-translate-x-full pr-1' : 'pl-1'}`}
                        style={{ left: `${x(to)}%` }}>{hover}</span>
                )}
            </>
        );
    };
    const barCell = (node: React.ReactNode) => compact
        ? <div aria-hidden className="relative col-span-2 h-2.5 -mt-0.5 mb-2">{node}</div>
        : <div aria-hidden className="relative self-stretch">{node}</div>;
    const eyebrow = (label: string, first = false) => (
        <div className="grid gap-x-3 px-4" style={cols}>
            <div className="pt-3 pb-1 text-[10px] uppercase tracking-wider text-base-content/45">{label}</div>
            {!compact && (
                <div aria-hidden className="relative self-stretch">
                    <div className="absolute inset-y-0 w-px bg-base-content/25" style={{ left: `${xToday}%` }} />
                    {first && (
                        <span className={`absolute bottom-1 whitespace-nowrap text-[10px] leading-none text-base-content/50 ${xToday > 50 ? '-translate-x-full pr-1.5' : 'pl-1.5'}`}
                            style={{ left: `${xToday}%` }}>today {usdK(W0)}</span>
                    )}
                </div>
            )}
        </div>
    );
    const moveChip = (label: string, move: string, dim = false) => (
        <span className={`rounded-md bg-base-300/70 px-1.5 py-0.5 whitespace-nowrap ${dim ? 'text-base-content/65' : ''}`}>{label} <span className="font-semibold">{move}</span></span>
    );

    return (
        <div ref={ref} className="rounded-lg border border-base-300 overflow-hidden self-start text-sm">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-4 py-3 bg-base-200/60 border-b border-base-300">
                <div className="font-semibold">If {tk} ends at {px(d.grid.prices[idx])}</div>
                <div className="flex items-center gap-1.5 text-xs tabular-nums">
                    {moveChip(tk, pct(d.grid.moves[idx]))}
                    {moveChip(d.index, mode === 'market' ? pct(idxMove) : 'flat', true)}
                </div>
            </div>

            <div className="pb-1">
                {groups.map((g, gi) => (
                    <div key={g.label}>
                        {eyebrow(g.label, gi === 0)}
                        {g.rows.map(({ l, i, a, from, to }) => {
                            const { label, note } = ledgerLabel(l, a, d, l.kind === 'leg' && !!l.early?.[idx]);
                            const holding = HOLDING_KINDS.includes(l.kind) && a >= 0;
                            const fill = holding ? 'bg-base-content/30' : a > 0 ? 'bg-success/70' : 'bg-error/70';
                            return (
                                <div key={i} className="group grid gap-x-3 px-4 transition-colors hover:bg-base-200/40" style={cols}>
                                    <div className="min-w-0 py-1.5 leading-tight">
                                        <div>{label}</div>
                                        {note && <div className="mt-0.5 text-[11px] text-base-content/50">{note}</div>}
                                    </div>
                                    {!compact && barCell(bar(from, to, fill, 'h-2.5', undefined, `→ ${usdK(to)}`))}
                                    <div className={`self-center py-1.5 text-right tabular-nums whitespace-nowrap ${a < -0.5 ? 'text-error' : ''}`}>
                                        {holding ? usd(a) : signed(a)}
                                    </div>
                                    {compact && barCell(bar(from, to, fill, 'h-1.5'))}
                                </div>
                            );
                        })}
                    </div>
                ))}
            </div>

            <div className="grid gap-x-3 px-4 bg-base-200/50 border-y border-base-300" style={cols}>
                <div className="py-2.5 leading-tight">
                    <div className="font-semibold">You end with</div>
                    <div className="mt-0.5 text-[11px] text-base-content/55">vs {usd(W0)} today</div>
                </div>
                {!compact && barCell(bar(0, end, '', 'h-3.5', C_PLAN))}
                <div className="self-center py-2.5 text-right leading-tight">
                    <div className="text-base font-semibold tabular-nums">{usd(end)}</div>
                    <div className={`mt-0.5 text-xs tabular-nums ${signTone(vsToday(end, W0))}`}>{pct(vsToday(end, W0), 1)}</div>
                </div>
                {compact && barCell(bar(0, end, '', 'h-2', C_PLAN))}
            </div>

            {alts.length > 0 && (
                <div className="pb-1.5">
                    {eyebrow('Same price, instead')}
                    {alts.map(({ r, label, v }) => {
                        const gap = end - v;
                        return (
                            <div key={r.id} className="grid gap-x-3 px-4" style={cols}>
                                <div className="min-w-0 py-1.5 leading-tight">
                                    <div className="flex items-center gap-1.5 text-base-content/80"><Dot c={colorOf(r)} /><span className="truncate">{label}</span></div>
                                    <div className={`mt-0.5 text-[11px] tabular-nums ${Math.abs(gap) < 0.5 ? 'text-base-content/50' : gap > 0 ? 'text-success' : 'text-error'}`}>
                                        {Math.abs(gap) < 0.5 ? 'same as the plan' : gap > 0 ? `plan ahead by ${usd(gap)}` : `plan behind by ${usd(-gap)}`}
                                    </div>
                                </div>
                                {!compact && barCell(bar(0, v, 'opacity-80', 'h-1.5', colorOf(r)))}
                                <div className="self-center py-1.5 text-right tabular-nums whitespace-nowrap text-base-content/80">{usd(v)}</div>
                                {compact && barCell(bar(0, v, 'opacity-80', 'h-1', colorOf(r)))}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

const SCENARIO_MOVES = [-40, -20, 0, 20, 40];

function AtExpiry({ d, row, hold, sell, wide, roomy }: {
    d: LeapProtectionResponse; row: LeapStrategyRow; hold: LeapStrategyRow; sell: LeapStrategyRow; wide: boolean; roomy: boolean;
}) {
    const [mode, setMode] = useState<Mode>('market');
    const moves = d.grid.moves, prices = d.grid.prices;
    const [idx, setIdx] = useState(Math.max(0, moves.indexOf(-20)));
    const W0 = d.starting_wealth, tk = d.ticker;
    const name = (r: LeapStrategyRow) => (r.id === 'hold' ? ['Hold unhedged', 'Hold'] : r.id === 'sell_now' ? ['Sell today', 'Sell'] : ['This plan', 'Plan']);
    const series = [row, ...(row.id !== 'hold' ? [hold] : []), ...(row.id !== 'sell_now' ? [sell] : [])]
        .map(r => ({ r, label: name(r)[0], short: name(r)[1] }));
    const cols = SCENARIO_MOVES.map(m => moves.indexOf(m)).filter(i => i >= 0);
    const idxMove = (i: number) => (mode === 'market' ? d.grid.index_moves_market[i] : 0);

    // today's price + the plan's strikes as dashed guides on the chart (read through a ref: the plugin is registered once)
    const marks = useRef<{ x: number; label: string; align: CanvasTextAlign; top: boolean }[]>([]);
    marks.current = [
        { x: d.spot, label: 'today', align: 'center', top: true },
        ...(row.shape.protect_below != null ? [{ x: row.shape.protect_below, label: `$${row.shape.protect_below} put`, align: 'right' as const, top: false }] : []),
        ...(row.shape.cap_above != null ? [{ x: row.shape.cap_above, label: `$${row.shape.cap_above} call`, align: 'left' as const, top: false }] : []),
    ];
    const guides = useMemo(() => [{
        id: 'leapGuides',
        afterDatasetsDraw(chart: any) {
            const { ctx, chartArea: a, scales: { x } } = chart;
            ctx.save();
            ctx.font = '11px system-ui, sans-serif';
            for (const m of marks.current) {
                const X = x.getPixelForValue(m.x);
                if (X < a.left || X > a.right) continue;
                ctx.strokeStyle = 'rgba(195,194,183,0.35)'; ctx.setLineDash([3, 3]);
                ctx.beginPath(); ctx.moveTo(X, a.top); ctx.lineTo(X, a.bottom); ctx.stroke();
                ctx.setLineDash([]); ctx.fillStyle = 'rgba(195,194,183,0.75)'; ctx.textAlign = m.align;
                ctx.fillText(m.label, X + (m.align === 'left' ? 4 : m.align === 'right' ? -4 : 0), m.top ? a.top + 11 : a.bottom - 6);
            }
            ctx.restore();
        },
    }], []);
    const data = {
        datasets: [
            ...series.map(({ r, label }, j) => ({
                label, data: moves.map((_, i) => ({ x: prices[i], y: total(r, mode, i) })),
                borderColor: colorOf(r), backgroundColor: colorOf(r), borderWidth: j === 0 ? 3 : 2, borderDash: r.id === 'hold' && j > 0 ? [6, 4] : undefined,
                pointRadius: moves.map((_, i) => (i === idx ? 5 : 0)), pointHoverRadius: 5, tension: 0,
            })),
            { label: "Today's value", data: moves.map((_, i) => ({ x: prices[i], y: W0 })), borderColor: C_TODAY, backgroundColor: C_TODAY, borderWidth: 1, borderDash: [2, 3], pointRadius: 0, pointHoverRadius: 0 },
        ],
    };
    const options = {
        responsive: true, maintainAspectRatio: false, animation: { duration: 0 },
        interaction: { mode: 'index' as const, intersect: false },
        onClick: (_: unknown, els: { index: number }[]) => { if (els?.length) setIdx(els[0].index); },
        plugins: {
            legend: { position: 'bottom' as const, labels: { color: '#c3c2b7', boxWidth: 12, font: { size: 11 } } },
            tooltip: {
                filter: (c: any) => c.dataset.label !== "Today's value",
                callbacks: {
                    title: (c: any) => `${tk} ${px(prices[c[0].dataIndex])} (${pct(moves[c[0].dataIndex])})`,
                    label: (c: any) => ` ${c.dataset.label}: ${usd(c.parsed.y)} (${pct(vsToday(c.parsed.y, W0), 1)})`,
                },
            },
        },
        scales: {
            x: {
                type: 'linear' as const, min: prices[0], max: prices[prices.length - 1],
                title: { display: true, text: `${tk} price at expiry`, color: '#c3c2b7' },
                ticks: { color: '#9a998f', maxTicksLimit: 8, callback: (v: any) => `$${Math.round(Number(v))}` }, grid: { color: 'rgba(255,255,255,0.04)' },
            },
            y: { title: { display: wide, text: 'You end with, after tax', color: '#c3c2b7' }, ticks: { color: '#9a998f', callback: (v: any) => usdK(Number(v)) }, grid: { color: 'rgba(255,255,255,0.06)' } },
        },
    };

    return (
        <div className="rounded-xl border border-base-300 p-4 sm:p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <div className="font-semibold">What you end up with</div>
                    <div className="text-xs text-base-content/55">After tax, on {fullDate(d.horizon.expiry)} · click a column or the chart for the breakdown</div>
                </div>
                <div className="w-60 max-w-full">
                    <Segmented label="Scenario type" value={mode} onChange={setMode}
                        options={[{ value: 'market', label: 'With market', hint: `${d.index} moves with ${tk} (β ${d.market.beta})` },
                        { value: 'stock_only', label: `${tk} alone`, hint: `${d.index} flat — a ${tk}-only move` }]} />
                </div>
            </div>

            <div className="overflow-x-auto">
                <table className={`w-full tabular-nums ${roomy ? 'text-sm' : 'text-xs'}`}>
                    <thead>
                        <tr>
                            <th className="text-left align-bottom pb-1.5 pr-2 text-[11px] font-normal uppercase tracking-wide text-base-content/50">{roomy ? `${tk} at expiry` : tk}</th>
                            {cols.map(i => (
                                <th key={i} onClick={() => setIdx(i)} className={`cursor-pointer ${roomy ? 'px-1' : 'px-0'} pb-1.5 pt-1 text-center rounded-t-md ${i === idx ? 'bg-secondary/10' : 'hover:bg-base-200/60'}`}>
                                    <div className="font-semibold">{pct(moves[i])}</div>
                                    <div className="text-[11px] font-normal text-base-content/50 whitespace-nowrap">{px(prices[i])}{roomy && mode === 'market' ? ` · ${d.index} ${pct(idxMove(i))}` : ''}</div>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {series.map(({ r, label, short }, j) => (
                            <tr key={r.id} className="border-t border-base-300/60">
                                <td className={`py-2 whitespace-nowrap ${roomy ? 'pr-2' : 'pr-1'}`} title={label}><span className={`flex items-center gap-1.5 ${j === 0 ? 'font-semibold' : 'text-base-content/75'}`}><Dot c={colorOf(r)} />{roomy ? label : short}</span></td>
                                {cols.map(i => {
                                    const v = total(r, mode, i), p = vsToday(v, W0);
                                    const top = series.every(o => total(o.r, mode, i) <= v + 0.5);
                                    return (
                                        <td key={i} onClick={() => setIdx(i)} className={`cursor-pointer ${roomy ? 'px-1' : 'px-0'} py-2 text-center ${i === idx ? 'bg-secondary/10' : ''}`}>
                                            <span className={`inline-block ${roomy ? 'px-1.5' : 'px-1'} rounded ${signTone(p)} ${top ? 'font-bold bg-base-content/10' : ''}`}>{pct(p, 0)}</span>
                                            {roomy && <div className="text-[11px] text-base-content/50">{usdK(v)}</div>}
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
                <div className="text-[11px] text-base-content/45 mt-1">Change vs today's {usdK(W0)}{roomy ? ', and what you end with' : ''}. Highlighted = best of the three.</div>
            </div>

            <div className={`grid gap-4 ${wide ? 'grid-cols-[minmax(0,3fr)_minmax(0,2fr)]' : 'grid-cols-1'}`}>
                <div className="space-y-1 min-w-0">
                    <div className="h-72"><Line data={data} options={options} plugins={guides} /></div>
                    <input type="range" min={0} max={moves.length - 1} value={idx} onChange={e => setIdx(Number(e.target.value))}
                        className="range range-xs range-secondary" aria-label={`${tk} price at expiry`} />
                </div>
                <Breakdown d={d} row={row} mode={mode} idx={idx} others={series.slice(1)} />
            </div>
        </div>
    );
}

// ---------------------------------------------------------------- compare: one range bar per plan
/** Spread of outcomes at expiry: thin line = 9 in 10 outcomes, bar = the middle half, dot = the median. */
function RangeBar({ p, lo, hi, color }: { p: number[]; lo: number; hi: number; color: string }) {
    const x = (v: number) => ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * 100;
    return (
        <div className="relative h-5 w-full" title={`1 in 20 worse than ${pct(p[0])} · middle half ${pct(p[1])} to ${pct(p[3])} · median ${pct(p[2])} · 1 in 20 better than ${pct(p[4])}`}>
            <div className="absolute inset-y-0 w-px bg-base-content/25" style={{ left: `${x(0)}%` }} />
            <div className="absolute top-1/2 h-0.5 -translate-y-1/2 rounded-full" style={{ left: `${x(p[0])}%`, width: `${x(p[4]) - x(p[0])}%`, background: color, opacity: 0.5 }} />
            <div className="absolute top-1/2 h-2.5 -translate-y-1/2 rounded-sm" style={{ left: `${x(p[1])}%`, width: `${Math.max(x(p[3]) - x(p[1]), 0.5)}%`, background: color, opacity: 0.85 }} />
            <div className="absolute top-1/2 w-2.5 h-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-base-100" style={{ left: `${x(p[2])}%`, background: color }} />
            {p[0] < lo && <span className="absolute -left-0.5 top-1/2 -translate-y-1/2 text-[10px] leading-none" style={{ color }}>◀</span>}
            {p[4] > hi && <span className="absolute -right-0.5 top-1/2 -translate-y-1/2 text-[10px] leading-none" style={{ color }}>▶</span>}
        </div>
    );
}

type FamilyGroup = { id: string; label: string; rows: LeapStrategyRow[] };

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
    return (
        <button type="button" onClick={onClick} aria-pressed={on}
            className={`shrink-0 inline-flex items-center gap-1.5 h-7 rounded-full border px-3 text-xs whitespace-nowrap transition-colors ${on
                ? 'border-secondary/60 bg-secondary/15 text-secondary font-semibold' : 'border-base-300 text-base-content/70 hover:border-base-content/30 hover:text-base-content'}`}>
            {children}
        </button>
    );
}

function ComparePlans({ d, selected, onSelect, objective, roomy }: {
    d: LeapProtectionResponse; selected: string; onSelect: (id: string) => void; objective: Objective; roomy: boolean;
}) {
    const [view, setView] = useState<string>('best');          // 'best' · 'all' · a family id
    const [closed, setClosed] = useState<Set<string>>(new Set());
    const [k] = OBJ[objective];
    // families, each ranked inside (recommended → within your limits → the rest); the family holding the recommendation
    // first, then by how good their best plan is; the two reference points last
    const families = useMemo<FamilyGroup[]>(() => {
        const tier = (s: LeapStrategyRow) => (s.recommended ? 0 : s.eligible ? 1 : 2);
        const groups = d.families.map(f => ({
            id: f.id, label: f.label,
            rows: d.strategies.filter(s => s.family === f.id).sort((a, b) => tier(a) - tier(b) || a.rank - b.rank),
        })).filter(g => g.rows.length);
        const key = (g: FamilyGroup) => [g.id === 'reference' ? 1 : 0, tier(g.rows[0]), g.rows[0].rank];
        return groups.sort((a, b) => {
            const x = key(a), y = key(b);
            return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
        });
    }, [d]);
    const fam = families.find(f => f.id === view);
    const shown: { group?: FamilyGroup; rows: LeapStrategyRow[] }[] = view === 'best'
        ? [{ rows: families.flatMap(f => (f.id === 'reference' ? f.rows : f.rows.slice(0, 1))) }]
        : view === 'all' ? families.map(f => ({ group: f, rows: closed.has(f.id) ? [] : f.rows }))
            : fam ? [{ rows: fam.rows }] : [];
    const visible = shown.flatMap(s => s.rows);
    const [lo, hi] = useMemo(() => {
        // the scale follows the plans shown, but no wider than 1.5× holding / selling today — so one leveraged plan can't
        // squash every other bar; anything beyond runs off the edge with an arrow
        const rows = visible.length ? visible : d.strategies;
        const refs = d.strategies.filter(r => r.family === 'reference');
        const capLo = Math.min(-5, ...refs.map(r => r.pctiles_pct[0])) * 1.5, capHi = Math.max(5, ...refs.map(r => r.pctiles_pct[4])) * 1.5;
        const a = Math.max(Math.min(0, ...rows.map(r => r.pctiles_pct[0])), capLo);
        const b = Math.min(Math.max(0, ...rows.map(r => r.pctiles_pct[4])), capHi);
        const pad = (b - a) * 0.04;
        return [a - pad, b + pad];
    }, [visible, d.strategies]);
    const zeroAt = ((0 - lo) / (hi - lo)) * 100;
    const W0 = d.starting_wealth;
    const cols = roomy ? 6 : 3;
    const th = 'font-normal text-[11px] uppercase tracking-wide text-base-content/50 py-2';
    const toggle = (id: string) => setClosed(c => { const n = new Set(c); if (n.has(id)) n.delete(id); else n.add(id); return n; });

    const planRow = (r: LeapStrategyRow, more?: FamilyGroup) => {
        const s = r.shape, on = r.id === selected;
        const risk = Math.max(0, ...r.assignment.filter(a => a.american).map(a => a.p_pct));
        return (
            <tr key={r.id} onClick={() => onSelect(r.id)}
                className={`cursor-pointer border-b border-base-300/50 ${on ? 'bg-secondary/10' : 'hover:bg-base-200/50'} ${!r.eligible && r.family !== 'reference' && !on ? 'opacity-60' : ''}`}>
                <td className={`py-2 ${roomy ? 'pl-2 pr-3 min-w-[12rem]' : 'pr-1 min-w-[7.5rem] text-xs'}`}>
                    <div className={`leading-snug ${on ? 'font-semibold' : ''}`}>{r.recommended && <span className="text-secondary">★ </span>}{r.name}</div>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-0.5 text-[11px] text-base-content/50">
                        {view === 'best' && <span>{r.family_label}</span>}
                        {more && more.id !== 'reference' && more.rows.length > 1 && (
                            <button type="button" className="text-secondary hover:underline" onClick={e => { e.stopPropagation(); setView(more.id); }}>
                                +{more.rows.length - 1} more
                            </button>
                        )}
                        {risk >= 5 && <span className="badge badge-warning badge-outline badge-xs" title="Chance the buyer exercises a short option early">early assignment {Math.round(risk)}%</span>}
                    </div>
                </td>
                <td className={`py-2 ${roomy ? 'px-2' : 'px-1'}`}><RangeBar p={r.pctiles_pct} lo={lo} hi={hi} color={colorOf(r)} /></td>
                <td className={`py-2 text-right tabular-nums font-semibold ${roomy ? 'px-2' : 'pl-1 text-xs'}`}>{pct(r[k] as number, 1)}</td>
                {roomy && <td className="py-2 px-2 text-right tabular-nums whitespace-nowrap">
                    {s.floored ? <span className="text-success">{pct(vsToday(s.floor_value!, W0))}</span> : <span className="text-base-content/50">no floor</span>}
                </td>}
                {roomy && <td className="py-2 px-2 text-right tabular-nums whitespace-nowrap">
                    {s.capped ? <>{pct(vsToday(s.cap_value!, W0))}{s.p_cap_pct != null && <span className="text-[11px] text-base-content/50"> · {s.p_cap_pct}%</span>}</> : <span className="text-success">no cap</span>}
                </td>}
                {roomy && <td className="py-2 pl-2 pr-2 whitespace-nowrap" title={r.reasons.join('; ')}>
                    {r.family === 'reference' ? <span className="text-xs text-base-content/45">reference</span>
                        : r.eligible ? <span className="text-success text-xs inline-flex items-center gap-1"><Check className="w-3 h-3" />fits</span>
                            : r.reason_tags.map(t => <span key={t} className="badge badge-ghost badge-xs mr-1">{t}</span>)}
                </td>}
            </tr>
        );
    };

    return (
        <div className="rounded-xl border border-base-300 p-4 sm:p-5 space-y-3">
            <div>
                <div className="font-semibold">Compare plans</div>
                <div className="text-xs text-base-content/55">{d.strategies.length} plans in {families.length} families · click a plan to see it above</div>
            </div>
            <div className="flex gap-1.5 overflow-x-auto pb-1 -mx-1 px-1">
                <Chip on={view === 'best'} onClick={() => setView('best')}>Best of each family</Chip>
                <Chip on={view === 'all'} onClick={() => setView('all')}>All plans <span className="opacity-60">{d.strategies.length}</span></Chip>
                <span className="shrink-0 w-px my-1 bg-base-300" />
                {families.map(f => (
                    <Chip key={f.id} on={view === f.id} onClick={() => setView(f.id)}>
                        {f.rows.some(r => r.recommended) && <span className="text-secondary">★</span>}{f.label} <span className="opacity-60">{f.rows.length}</span>
                    </Chip>
                ))}
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-base-300">
                            <th className={`${th} text-left ${roomy ? 'pl-2 pr-3' : 'pr-1'}`}>{fam ? fam.label : 'Plan'}</th>
                            <th className={`${th} text-left w-[36%] ${roomy ? 'min-w-[9rem] px-2' : 'min-w-[5.5rem] px-1'}`}>
                                <div className="flex items-center gap-1 whitespace-nowrap">{roomy ? 'Outcomes at expiry' : 'Outcomes'} <Tip text="Thin line: 9 in 10 outcomes · bar: the middle half · dot: the median. The grey line is today's value." /></div>
                                <div className="relative h-3.5 mt-0.5 normal-case tracking-normal">
                                    <span className="absolute left-0">{pct(lo)}</span>
                                    {roomy && zeroAt > 22 && zeroAt < 78 && <span className="absolute -translate-x-1/2" style={{ left: `${zeroAt}%` }}>today</span>}
                                    <span className="absolute right-0">{pct(hi)}</span>
                                </div>
                            </th>
                            <th className={`${th} text-right ${roomy ? 'px-2' : 'pl-1'}`} title={OBJ_TIP[objective]}>{OBJ_SHORT[objective]}</th>
                            {roomy && <th className={`${th} text-right px-2`} title="Worst case at expiry, after tax">Worst</th>}
                            {roomy && <th className={`${th} text-right px-2`} title="Best case at expiry, after tax (and its chance)">Best</th>}
                            {roomy && <th className={`${th} text-left pl-2`}>Your limits</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {shown.map(({ group, rows }) => (
                            <React.Fragment key={group?.id ?? view}>
                                {group && (
                                    <tr className="bg-base-200/50 border-b border-base-300/70 cursor-pointer select-none" onClick={() => toggle(group.id)}>
                                        <td colSpan={cols} className="py-1.5 px-2">
                                            <div className="flex items-center gap-2 text-xs whitespace-nowrap">
                                                {closed.has(group.id) ? <ChevronRight className="w-3.5 h-3.5 shrink-0 text-base-content/50" /> : <ChevronDown className="w-3.5 h-3.5 shrink-0 text-base-content/50" />}
                                                {group.rows.some(r => r.recommended) && <span className="text-secondary">★</span>}
                                                <span className="font-semibold uppercase tracking-wide text-base-content/75 truncate">{group.label}</span>
                                                <span className="text-base-content/45">{group.rows.length}</span>
                                                {roomy && <span className="text-base-content/45">· best {pct(group.rows[0][k] as number, 1)}/yr</span>}
                                            </div>
                                        </td>
                                    </tr>
                                )}
                                {rows.map(r => planRow(r, view === 'best' ? families.find(f => f.id === r.family) : undefined))}
                            </React.Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function Details({ d, row }: { d: LeapProtectionResponse; row: LeapStrategyRow }) {
    const m = d.market, a = d.assumptions, j = m.jumps;
    return (
        <div className="rounded-xl border border-base-300 divide-y divide-base-300">
            <details className="px-4 py-3">
                <summary className="font-semibold cursor-pointer text-sm">Quotes & liquidity</summary>
                {row.legs.length || row.rolls.length ? (
                    <div className="overflow-x-auto mt-2">
                        <table className="table table-xs">
                            <thead><tr><th>Order</th><th>Expiry</th><th className="text-right">Bid / Ask</th><th className="text-right">Priced at</th><th className="text-right">IV</th><th className="text-right">Open int.</th><th className="text-right">Spread</th></tr></thead>
                            <tbody>
                                {row.legs.map((l, i) => (
                                    <tr key={i}>
                                        <td className="whitespace-nowrap font-medium">{l.type === 'futures'
                                            ? `${l.action === 'BUY' ? 'Buy' : 'Sell'} ${l.und} futures ${usd(l.notional)}${l.contracts_note ? ` (${l.contracts_note})` : ''}`
                                            : `${l.action === 'BUY' ? 'Buy' : 'Sell'} ${l.contracts} ${l.und === 'stock' ? d.ticker : l.und} $${l.strike} ${l.type}${l.contracts === 1 ? '' : 's'}`}</td>
                                        <td>{l.expiry}</td>
                                        <td className="text-right whitespace-nowrap">{l.bid != null ? `$${l.bid} / $${l.ask}` : 'fair value'}</td>
                                        <td className="text-right">{l.px != null ? `$${l.px}` : '—'}</td>
                                        <td className="text-right">{l.iv_pct != null ? `${l.iv_pct}%` : '—'}</td>
                                        <td className="text-right">{l.oi?.toLocaleString() ?? '—'}</td>
                                        <td className={`text-right ${(l.spread_pct ?? 0) > 20 ? 'text-warning' : ''}`}>{l.spread_pct != null ? `${l.spread_pct}%` : '—'}</td>
                                    </tr>
                                ))}
                                {row.rolls.map((r, i) => (
                                    <tr key={`r${i}`}>
                                        <td className="font-medium" colSpan={2}>Sell {r.contracts} {r.type}s every ~{r.cycle_days}d — first ${r.first_strike} ({r.first_expiry})</td>
                                        <td className="text-right">bid ${r.first_bid}</td>
                                        <td colSpan={4} className="opacity-60">≈{r.first_yield_pct}% of the stock per cycle</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <div className="text-xs opacity-60 mt-1">Priced at the side you'd trade (buy at ask, sell at bid). Work limit orders near the mid.
                            {row.legs.some(l => l.twin) && ` ${d.index} legs are priced as ${row.legs.find(l => l.twin)?.twin} (European, cash-settled — no early assignment); the ${d.index} bid/ask is shown for reference. Deep in the money the ${d.index} quote is higher because its buyer can exercise early.`}</div>
                    </div>
                ) : <div className="text-sm opacity-70 mt-2">No options — {row.blurb}</div>}
            </details>
            <details className="px-4 py-3">
                <summary className="font-semibold cursor-pointer text-sm">Tax & things to know{row.warnings.length ? <span className="badge badge-warning badge-xs ml-2 align-middle">{row.warnings.length}</span> : null}</summary>
                <div className="text-sm space-y-1.5 mt-2">
                    <div><b>Tax.</b> <span className="opacity-80">{row.tax_note}</span> Expected tax triggered before expiry: {usd(row.expected_tax_total)}.</div>
                    {row.dividends && <div><b>Dividends.</b> <span className="opacity-80">{row.dividends.qualified
                        ? `Taxed as qualified dividends (${row.dividends.rate_pct}%).`
                        : `Taxed as ordinary income (${row.dividends.rate_pct}%) while this is on: ${row.dividends.why}.`}</span></div>}
                    <div><b>Could it be taxed as a sale (§1259)?</b> <span className="opacity-80">{row.constructive_sale.note}</span></div>
                    {row.warnings.map(w => <div key={w} className="text-warning flex gap-1.5"><AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{w}</div>)}
                </div>
            </details>
            <details className="px-4 py-3">
                <summary className="font-semibold cursor-pointer text-sm">How the numbers are made</summary>
                <ul className="text-sm space-y-1 mt-2 opacity-90 list-disc ml-5">
                    <li>Live quotes for {d.ticker} and {d.index} options expiring {d.horizon.expiry}. Results are measured from {usd(d.starting_wealth)} — {a.tax_lens === 'defer' ? "today's value (you never sell, so only tax actually triggered counts)" : "today's after-tax value (everything is sold at expiry)"}.</li>
                    <li>Expected returns include the equity risk premium: {d.ticker} {m.stock_drift_pct}%/yr (β {m.beta}), {d.index} {m.index_drift_pct}%/yr, cash {m.risk_free_pct}%. "Expected growth" is the compound rate, so volatility costs return.</li>
                    {j ? <li>Crash risk is fitted to each option chain ({d.ticker}: {j.stock_idio_jumps_per_yr} own jumps/yr averaging {pct(j.stock_idio_jump_mean_pct)}{j.market_jumps_per_yr != null ? `; market: ${j.market_jumps_per_yr}/yr averaging ${pct(j.market_jump_mean_pct)}` : ''}), so option prices are treated as fair.</li>
                        : <li>Prices follow {a.vol_view === 'realized' ? 'historical' : 'implied'} volatility without jumps.</li>}
                    <li>Short calls that finish in the money are bought back so you keep the shares. Premium, loans, sale proceeds and dividends go to {d.destination.label}{d.destination.rate_pct != null ? ` (${d.destination.rate_pct}%/yr, interest taxed as income)` : ''}.</li>
                    <li>Early assignment is checked on every simulated path: a short stock call just before each ex-dividend date (exercised when the dividend beats the interest on the strike), a short put whenever it is deep in the money. When it pays the buyer to exercise, the plan buys the option back at its intrinsic value — that cost is in every number.</li>
                    <li>Dividends: {d.ticker}'s latest payment carried forward at its usual spacing (no growth), {d.market.div_yield_pct}%/yr. A put on the shares (or an in-the-money call) makes them ordinary income for as long as it is on (§246(c)(4)).</li>
                    <li>Tax: {String(a.lt_rate_pct)}% long-term, {String(a.st_rate_pct)}% short-term; {d.index} options taxed like their cash-settled index twins (§1256) where available.</li>
                    {d.notes.map(n => <li key={n} className="text-warning">{n}</li>)}
                </ul>
                <div className="text-sm mt-3"><b>Not modelled:</b> <span className="opacity-80">{d.alternatives.map(x => x.name).join(' · ')}.</span></div>
            </details>
        </div>
    );
}

// ---------------------------------------------------------------- form primitives
function Segmented<T extends string>({ value, onChange, options, label }: {
    value: T; onChange: (v: T) => void; options: { value: T; label: string; hint?: string }[]; label: string;
}) {
    return (
        <div role="radiogroup" aria-label={label} className="flex w-full rounded-lg bg-base-200 p-1 gap-1">
            {options.map(o => (
                <button key={o.value} type="button" role="radio" aria-checked={value === o.value} title={o.hint} onClick={() => onChange(o.value)}
                    className={`flex-1 min-w-0 truncate rounded-md px-2 h-8 text-sm transition-colors ${value === o.value
                        ? 'bg-secondary text-secondary-content font-medium shadow-sm' : 'text-base-content/65 hover:text-base-content hover:bg-base-300/60'}`}>
                    {o.label}
                </button>
            ))}
        </div>
    );
}

function Field({ label, hint, children, className = '' }: { label: string; hint?: string; children: React.ReactNode; className?: string }) {
    return (
        <div className={`min-w-0 ${className}`}>
            <div className="flex items-center gap-1 mb-1.5 text-xs font-medium text-base-content/70">
                <span className="truncate">{label}</span>
                {hint && <Tip text={hint} />}
            </div>
            {children}
        </div>
    );
}

function NumberInput({ value, onChange, prefix, suffix, step = '1', placeholder, label }: {
    value: string; onChange: (v: string) => void; prefix?: string; suffix?: string; step?: string; placeholder?: string; label: string;
}) {
    return (
        <label className="input input-bordered h-10 min-h-0 flex items-center gap-1.5 w-full px-3">
            {prefix && <span className="text-sm text-base-content/50">{prefix}</span>}
            <input type="number" inputMode="decimal" step={step} aria-label={label} placeholder={placeholder}
                className="grow min-w-0 bg-transparent text-sm tabular-nums" value={value} onChange={e => onChange(e.target.value)} />
            {suffix && <span className="shrink-0 text-xs text-base-content/50">{suffix}</span>}
        </label>
    );
}

function Slider({ label, hint, value, onChange, min, max, step, format }: {
    label: string; hint?: string; value: string; onChange: (v: string) => void; min: number; max: number; step: number; format: (v: number) => string;
}) {
    return (
        <Field label={label} hint={hint}>
            <div className="flex items-center gap-3 h-10">
                <input type="range" min={min} max={max} step={step} value={value} aria-label={label}
                    onChange={e => onChange(e.target.value)} className="range range-secondary range-xs grow" />
                <span className="w-14 text-right text-sm font-semibold tabular-nums">{format(Number(value))}</span>
            </div>
        </Field>
    );
}

/** The form lays itself out by ITS OWN width (the Strategies sidebar takes a variable share of the window). */
function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
    const ref = useRef<T>(null);
    const [w, setW] = useState(0);
    useEffect(() => {
        const el = ref.current;
        if (!el || typeof ResizeObserver === 'undefined') return;
        const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    return [ref, w];
}

function Section({ n, title, desc, children, open, onToggle, side }: {
    n?: number; title: string; desc: string; children: React.ReactNode; open?: boolean; onToggle?: () => void; side: boolean;
}) {
    const head = (
        <>
            <div className="flex items-center gap-2">
                {n != null
                    ? <span className="w-5 h-5 rounded-full bg-secondary/20 text-secondary text-[11px] font-semibold grid place-items-center">{n}</span>
                    : <SlidersHorizontal className="w-4 h-4 text-base-content/60" />}
                <span className="font-semibold">{title}</span>
                {onToggle && (open ? <ChevronDown className="w-4 h-4 text-base-content/60" /> : <ChevronRight className="w-4 h-4 text-base-content/60" />)}
            </div>
            <p className="text-xs text-base-content/55 mt-1 ml-7">{desc}</p>
        </>
    );
    return (
        <div className={`grid gap-x-8 gap-y-3 py-5 first:pt-1 border-t border-base-300 first:border-t-0 ${side ? 'grid-cols-[13rem_1fr]' : 'grid-cols-1'}`}>
            {onToggle
                ? <button type="button" className="text-left self-start" onClick={onToggle} aria-expanded={open}>{head}</button>
                : <div>{head}</div>}
            <div className="min-w-0">{children}</div>
        </div>
    );
}

const INDEXES: [string, string][] = [['SPY', 'S&P 500 · SPY'], ['QQQ', 'Nasdaq-100 · QQQ'], ['IWM', 'Russell 2000 · IWM'], ['DIA', 'Dow 30 · DIA']];
const HORIZON_LABEL: Record<string, string> = { '1': '1 year', '2': '2 years', '3': 'longest LEAP' };

// ---------------------------------------------------------------- the page
export function LeapProtection() {
    const [ticker, setTicker] = useState('AAPL');
    const [shares, setShares] = useState('1000');
    const [basis, setBasis] = useState('40');
    const [index, setIndex] = useState('SPY');
    const [horizon, setHorizon] = useState('2');
    const [expiry, setExpiry] = useState('');
    const [dest, setDest] = useState<LeapProtectionRequest['proceeds_to']>('index');
    const [bondRate, setBondRate] = useState('4.5');
    const [lt, setLt] = useState('23.8');
    const [st, setSt] = useState('40.8');
    const [lens, setLens] = useState<LeapProtectionRequest['tax_lens']>('defer');
    const [objective, setObjective] = useState<Objective>('cagr');
    const [maxLoss, setMaxLoss] = useState('25');
    const [maxSingle, setMaxSingle] = useState('50');
    const [erp, setErp] = useState('5');
    const [view, setView] = useState('');
    const [volView, setVolView] = useState<LeapProtectionRequest['vol_view']>('implied');
    const [borrow, setBorrow] = useState('1.5');
    const [ltv, setLtv] = useState('90');
    const [allowCs, setAllowCs] = useState(false);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [formOpen, setFormOpen] = useState(true);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [data, setData] = useState<LeapProtectionResponse | null>(null);
    const [ranObjective, setRanObjective] = useState<Objective>('cagr');
    const [selected, setSelected] = useState('');

    const [rootRef, width] = useWidth<HTMLDivElement>();
    const side = width >= 1020;                 // section titles in a left column
    const cols2 = width >= (side ? 1020 : 720); // two fields per row in the plan/limits sections
    const cols3 = width >= 560;                 // three fields per row (position, assumptions)
    const wide = width >= 900;                  // results: four tiles in a row, chart beside its breakdown
    const roomy = width >= 600;                 // results: extra columns (expiry, price, $ under each %)
    const planRef = useRef<HTMLDivElement>(null);
    const tk = ticker.trim().toUpperCase() || 'stock';
    const destText = dest === 'index' ? index : dest === 'bond' ? `${bondRate}% bond` : 'T-bills';
    const ready = !!ticker.trim() && Number(shares) > 0 && Number(basis) > 0;

    const run = async (pinExpiry?: string) => {
        if (!ready) return;
        setLoading(true); setError(null);
        const req: LeapProtectionRequest = {
            ticker: tk, shares: Math.round(Number(shares)), cost_basis: Number(basis),
            index, horizon_years: Number(horizon), expiry: (pinExpiry ?? expiry) || null,
            tax: { lt_rate_pct: Number(lt), st_rate_pct: Number(st) }, tax_lens: lens, erp_pct: Number(erp),
            alpha_pct: 0, stock_return_pct: view.trim() === '' ? null : Number(view), vol_view: volView, objective,
            max_loss_pct: Number(maxLoss), max_single_stock_pct: Number(maxSingle), borrow_spread_pct: Number(borrow),
            loan_ltv_pct: Number(ltv), allow_constructive_sale: allowCs, proceeds_to: dest, bond_rate_pct: Number(bondRate),
        };
        try {
            const res = await computeLeapProtection(req);
            setData(res); setRanObjective(objective); setSelected(res.best_id); setFormOpen(false);
        } catch (e: any) {
            setError(e?.message || 'Failed to run LEAP protection.');
        } finally {
            setLoading(false);
        }
    };

    const byId = useMemo(() => Object.fromEntries((data?.strategies ?? []).map(s => [s.id, s])), [data]);
    const row = data ? byId[selected] ?? byId[data.best_id] : null;
    const advancedSummary = `Tax ${lt}% / ${st}% · ${tk} return ${view.trim() === '' ? 'market view' : `${view}%/yr`} · market premium ${erp}% · ${volView === 'implied' ? 'option-implied' : 'historical'} volatility`;

    return (
        <div ref={rootRef} className="space-y-5">
            <div>
                <h2 className="text-2xl font-bold flex items-center gap-2"><ShieldCheck className="w-6 h-6 text-secondary" />LEAP Protection</h2>
                <p className="text-sm text-base-content/65 mt-1">Protect a large, low-basis position without selling it. Every LEAP structure is priced live and compared on what you'd end up with.</p>
            </div>

            {formOpen ? (
                <div className="rounded-xl border border-base-300 bg-base-100/40 px-5 pt-4 pb-5">
                    <Section n={1} side={side} title="Your position" desc="The shares you want to protect.">
                        <div className={`grid gap-4 ${cols3 ? 'grid-cols-3' : 'grid-cols-1'}`}>
                            <Field label="Ticker">
                                <input className="input input-bordered h-10 min-h-0 w-full text-sm uppercase font-medium" value={ticker}
                                    onChange={e => setTicker(e.target.value)} aria-label="Ticker" />
                            </Field>
                            <Field label="Shares held"><NumberInput label="Shares held" value={shares} onChange={setShares} suffix="shares" /></Field>
                            <Field label="Average cost" hint="Your average cost basis per share — sets the tax on any sale">
                                <NumberInput label="Average cost per share" value={basis} onChange={setBasis} prefix="$" suffix="/ share" step="0.01" />
                            </Field>
                        </div>
                    </Section>

                    <Section n={2} side={side} title="Your plan" desc="How long to protect, what to diversify into, and whether you'll ever sell.">
                        <div className={`grid gap-x-6 gap-y-4 ${cols2 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                            <Field label="Protect for">
                                <Segmented label="Protect for" value={horizon} onChange={v => { setHorizon(v); setExpiry(''); }}
                                    options={[{ value: '1', label: '1 year' }, { value: '2', label: '2 years' }, { value: '3', label: 'Longest' }]} />
                            </Field>
                            <Field label="Index to hedge with & diversify into">
                                <select className="select select-bordered h-10 min-h-0 w-full text-sm" value={index} onChange={e => setIndex(e.target.value)} aria-label="Index">
                                    {INDEXES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                                </select>
                            </Field>
                            <Field label="Invest premium collected in" hint="Premium you collect, any loan, and sale proceeds go here — and their return counts in every number">
                                <div className="flex gap-2">
                                    <div className="grow min-w-0">
                                        <Segmented label="Invest premium in" value={dest} onChange={setDest}
                                            options={[{ value: 'index', label: index, hint: `The ${index} index fund` }, { value: 'bond', label: 'Bond', hint: 'A fixed-rate bond at the yield you set' }, { value: 'cash', label: 'T-bills', hint: 'Treasury bills at SOFR' }]} />
                                    </div>
                                    {dest === 'bond' && <div className="w-28 shrink-0"><NumberInput label="Bond yield" value={bondRate} onChange={setBondRate} suffix="%/yr" step="0.1" /></div>}
                                </div>
                            </Field>
                            <Field label="After expiry, will you sell?">
                                <Segmented label="After expiry" value={lens} onChange={setLens}
                                    options={[{ value: 'defer', label: 'Keep the shares', hint: 'Never sell (step-up): only tax actually triggered counts' },
                                    { value: 'liquidate', label: 'Sell everything', hint: "Sell at expiry: results after all tax, from today's after-tax value" }]} />
                                <p className="text-[11px] text-base-content/50 mt-1.5">{lens === 'defer' ? 'Only tax the plan actually triggers counts — the embedded gain is never taxed (step-up).' : "Everything is sold at expiry; results are after all tax."}</p>
                            </Field>
                        </div>
                    </Section>

                    <Section n={3} side={side} title="Your limits" desc="Plans outside these are still shown, but not recommended.">
                        <div className={`grid gap-x-8 gap-y-2 ${cols2 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                            <Slider label="Worst outcome you can accept" hint="Average of the worst 5% of after-tax outcomes over the horizon"
                                value={maxLoss} onChange={setMaxLoss} min={5} max={60} step={5} format={v => `−${v}%`} />
                            <Slider label={`Most of your wealth still riding on ${tk}`} hint={`How much of today's value may still move one-for-one with ${tk}`}
                                value={maxSingle} onChange={setMaxSingle} min={0} max={100} step={5} format={v => `${v}%`} />
                        </div>
                    </Section>

                    <Section title="Assumptions" side={side} desc="Tax rates, return views, financing." open={showAdvanced} onToggle={() => setShowAdvanced(o => !o)}>
                        {showAdvanced ? (
                            <div className={`grid gap-x-6 gap-y-4 ${cols2 && cols3 ? 'grid-cols-3' : cols3 ? 'grid-cols-2' : 'grid-cols-1'}`}>
                                <Field label="Long-term tax rate" hint="Federal LTCG + 3.8% NIIT + state"><NumberInput label="Long-term tax rate" value={lt} onChange={setLt} suffix="%" step="0.1" /></Field>
                                <Field label="Short-term / ordinary tax rate" hint="Ordinary rate + NIIT + state — option premium and interest are taxed here"><NumberInput label="Short-term tax rate" value={st} onChange={setSt} suffix="%" step="0.1" /></Field>
                                <Field label={`Your ${tk} return view`} hint="Your own expected total return for the stock. Blank = the market view (cash + β × market premium)">
                                    <NumberInput label="Your stock return view" value={view} onChange={setView} suffix="%/yr" step="0.5"
                                        placeholder={data && data.ticker === tk ? `market ${data.market.capm_stock_pct}` : 'market view'} />
                                </Field>
                                <Field label="Market premium (ERP)" hint="What the whole stock market earns over cash per year (history ~4–6%) — not the collar's return"><NumberInput label="Market premium" value={erp} onChange={setErp} suffix="%/yr" step="0.1" /></Field>
                                <Field label="Volatility from">
                                    <Segmented label="Volatility from" value={volView} onChange={setVolView}
                                        options={[{ value: 'implied', label: 'Option market' }, { value: 'realized', label: 'History' }]} />
                                </Field>
                                <Field label="Recommend the plan with the best">
                                    <Segmented label="Rank by" value={objective} onChange={setObjective}
                                        options={[{ value: 'cagr', label: 'Growth', hint: 'Compound growth — charges volatility' }, { value: 'ce', label: 'Risk-adj.', hint: 'Certainty-equivalent return' }, { value: 'mean', label: 'Average' }]} />
                                </Field>
                                <Field label="Borrowing cost over SOFR" hint="For plans that borrow against the protected shares"><NumberInput label="Borrow spread" value={borrow} onChange={setBorrow} suffix="%" step="0.1" /></Field>
                                <Field label="Loan size" hint="How much of the put floor a collar loan borrows"><NumberInput label="Loan size" value={ltv} onChange={setLtv} suffix="% of floor" /></Field>
                                <Field label="Tax-sale risk (§1259)" hint="Some tight structures can be taxed as if you sold">
                                    <label className="flex items-center gap-2.5 h-10 cursor-pointer">
                                        <input type="checkbox" className="toggle toggle-secondary toggle-sm" checked={allowCs} onChange={e => setAllowCs(e.target.checked)} />
                                        <span className="text-sm text-base-content/75">Allow those plans</span>
                                    </label>
                                </Field>
                            </div>
                        ) : (
                            <button type="button" className="text-left text-sm text-base-content/60 hover:text-base-content/90 xl:pt-0.5" onClick={() => setShowAdvanced(true)}>
                                {advancedSummary} <span className="text-secondary ml-1">Change</span>
                            </button>
                        )}
                    </Section>

                    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-base-300 pt-4">
                        <span className="text-xs text-base-content/50">Uses live option quotes · takes a few seconds</span>
                        <div className="flex gap-2">
                            {data && <button type="button" className="btn btn-ghost btn-sm h-10" onClick={() => setFormOpen(false)}>Cancel</button>}
                            <button type="button" className="btn btn-secondary h-10 min-h-0 px-6 flex-nowrap" onClick={() => run()} disabled={loading || !ready}>
                                {loading ? <span className="loading loading-spinner loading-sm" /> : <Calculator className="w-4 h-4" />}Find protection
                            </button>
                        </div>
                    </div>
                </div>
            ) : (
                <div className="rounded-xl border border-base-300 bg-base-100/40 px-4 py-3 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm min-w-0">
                        <span className="font-semibold">{tk}</span>
                        <span className="text-base-content/70">{Number(shares).toLocaleString()} shares @ ${basis}</span>
                        <span className="text-base-content/30">·</span><span className="text-base-content/70">{HORIZON_LABEL[horizon]}</span>
                        <span className="text-base-content/30">·</span><span className="text-base-content/70">premium → {destText}</span>
                        <span className="text-base-content/30">·</span><span className="text-base-content/70">{lens === 'defer' ? 'keep the shares' : 'sell at expiry'}</span>
                        <span className="text-base-content/30">·</span><span className="text-base-content/70">worst ≤ −{maxLoss}% · {tk} ≤ {maxSingle}%</span>
                    </div>
                    <button type="button" className="btn btn-sm btn-outline flex-nowrap" onClick={() => setFormOpen(true)}><Pencil className="w-3.5 h-3.5" />Edit inputs</button>
                </div>
            )}

            {error && <div className="alert alert-error text-sm"><AlertCircle className="w-4 h-4" />{error}</div>}
            {loading && !data && <div className="text-sm text-base-content/60 flex items-center gap-2"><span className="loading loading-spinner loading-xs" />Pricing live LEAP chains and testing every structure…</div>}

            {data && row && (
                <div className={`space-y-5 ${loading ? 'opacity-60 pointer-events-none' : ''}`}>
                    <div className={`grid gap-2 ${width >= 700 ? 'grid-cols-4' : 'grid-cols-2'}`}>
                        <Stat label="Position today" value={usd(data.position_value)} sub={`${data.shares.toLocaleString()} × $${data.spot}`} />
                        <Stat label="Unrealised gain" value={usd(data.embedded_gain)} sub={`basis $${data.cost_basis}/share`} />
                        <Stat label="Tax if you sold today" value={usd(data.embedded_tax)} sub="long-term rate" />
                        <div className="rounded-lg border border-base-300 px-3 py-2">
                            <div className="text-[11px] uppercase tracking-wide text-base-content/55">Options expire</div>
                            <select className="select select-ghost select-sm h-8 min-h-0 -ml-2 font-semibold w-full" value={expiry || data.horizon.expiry}
                                onChange={e => { setExpiry(e.target.value); run(e.target.value); }} aria-label="Expiry">
                                {data.horizon.available.map(x => <option key={x.expiry} value={x.expiry}>{x.expiry} · {(x.dte / 365).toFixed(1)} yrs</option>)}
                            </select>
                        </div>
                    </div>
                    <div ref={planRef} className="scroll-mt-4">
                        <PlanCard d={data} row={row} hold={byId.hold} objective={ranObjective} wide={wide} roomy={roomy} onReset={() => setSelected(data.best_id)} />
                    </div>
                    <AtExpiry d={data} row={row} hold={byId.hold} sell={byId.sell_now} wide={wide} roomy={roomy} />
                    <ComparePlans d={data} selected={row.id} objective={ranObjective} roomy={roomy}
                        onSelect={id => { setSelected(id); planRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }} />
                    <Details d={data} row={row} />
                    <p className="text-[11px] text-base-content/50">{data.disclaimer} As of {data.as_of}.</p>
                </div>
            )}
        </div>
    );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
    return (
        <div className="rounded-lg border border-base-300 px-3 py-2 min-w-0">
            <div className="text-[11px] uppercase tracking-wide text-base-content/55">{label}</div>
            <div className="text-base font-semibold tabular-nums">{value}</div>
            {sub && <div className="text-xs text-base-content/50 truncate">{sub}</div>}
        </div>
    );
}
