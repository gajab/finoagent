import React, { useState } from 'react';
import { AlertCircle, Calculator, Plus, Trash2, CalendarClock, ShieldCheck, Hourglass, Ban } from 'lucide-react';
import { computeConcentrationLadder } from '../api';
import type { LadderResponse, LadderPosition, LadderRung, LadderTiming, LadderOverlay } from '../types';

interface LotRow { shares: string; basis: string }
interface PosRow { ticker: string; lots: LotRow[] }

const usd = (n: number | null | undefined) =>
    n == null || !Number.isFinite(n) ? '—' : `${n < 0 ? '−' : ''}$${Math.abs(Math.round(n)).toLocaleString()}`;
const pct = (n: number | null | undefined, d = 1) => (n == null || !Number.isFinite(n) ? '—' : `${n.toFixed(d)}%`);

const VERDICT: Record<LadderTiming['verdict'], { label: string; cls: string; Icon: React.ElementType }> = {
    PUT_ON_NOW: { label: 'Put the ladder on now', cls: 'badge-success', Icon: ShieldCheck },
    STAGGER: { label: 'Leg in (stagger)', cls: 'badge-warning', Icon: CalendarClock },
    WAIT: { label: 'Wait', cls: 'badge-error', Icon: Hourglass },
    JUST_SELL: { label: 'Just trim / sell', cls: 'badge-ghost', Icon: Ban },
    NOT_APPLICABLE: { label: 'Nothing to defer', cls: 'badge-ghost', Icon: Ban },
};

function TimingCard({ t }: { t: LadderTiming }) {
    const v = VERDICT[t.verdict];
    return (
        <div className="rounded-xl border border-base-300 p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-3">
                <span className={`badge badge-lg gap-1 ${v.cls}`}><v.Icon className="w-4 h-4" />{v.label}</span>
                {t.score != null && <span className="text-sm opacity-70">Timing score {t.score}/100</span>}
            </div>
            <p className="text-sm">{t.headline}</p>
            {t.factors.length > 0 && (
                <div className="overflow-x-auto">
                    <table className="table table-xs">
                        <thead><tr><th>Factor</th><th className="text-right">Pts</th><th>Read (live data)</th></tr></thead>
                        <tbody>
                            {t.factors.map(f => (
                                <tr key={f.name}>
                                    <td className="whitespace-nowrap">{f.name}</td>
                                    <td className="text-right whitespace-nowrap">{f.points}/{f.weight}</td>
                                    <td className="opacity-80">{f.note}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            {t.triggers.length > 0 && t.verdict !== 'PUT_ON_NOW' && (
                <div className="text-sm">
                    <div className="font-semibold mb-1">What would change this</div>
                    <ul className="list-disc ml-5 opacity-80">{t.triggers.map(x => <li key={x}>{x}</li>)}</ul>
                </div>
            )}
        </div>
    );
}

const legsText = (r: LadderRung) => [
    r.put ? `Buy $${r.put.strike}P` : null,
    r.put2 ? `Sell $${r.put2.strike}P` : null,
    `Sell $${r.call.strike}C`,
].filter(Boolean).join(' · ');

const RISK_CLS = { low: 'badge-success', elevated: 'badge-warning', high: 'badge-error' } as const;

function OverlayCard({ o }: { o: LadderOverlay }) {
    return (
        <div className="rounded-xl border border-base-300 p-4 space-y-2">
            <div className="font-semibold">Income overlay — rolling ~45-day covered calls on {o.shares.toLocaleString()} shares</div>
            <div className="text-xs opacity-70">{o.note}</div>
            <div className="overflow-x-auto">
                <table className="table table-xs">
                    <thead><tr><th>Strike</th><th className="text-right">Δ</th><th className="text-right">Bid</th><th className="text-right">Yield/yr*</th><th className="text-right">P(assigned) / cycle</th><th className="text-right">P(≥1 assignment) in 12m</th><th className="text-right">Premium / yr</th><th className="text-right">After tax</th><th className="text-right">Tax if assigned</th><th className="text-right">Funds</th></tr></thead>
                    <tbody>
                        {o.options.map(x => (
                            <tr key={x.target_delta}>
                                <td>${x.strike} <span className="opacity-60">(+{x.cap_pct}%)</span></td><td className="text-right">{x.delta}</td><td className="text-right">${x.bid}</td>
                                <td className="text-right">{pct(x.annual_yield_pct_if_never_assigned)}</td><td className="text-right">{pct(x.p_assign_cycle_pct)}</td><td className="text-right">{pct(x.p_assign_12m_pct, 0)}</td>
                                <td className="text-right">{usd(x.premium_per_year)}</td><td className="text-right">{usd(x.after_tax_premium_per_year)}</td><td className="text-right">{usd(x.tax_if_assigned)}</td>
                                <td className="text-right">{x.funds_pct_of_assignment_tax != null ? `${x.funds_pct_of_assignment_tax}%` : '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <div className="text-xs opacity-60">*if never assigned and re-sold every cycle. Expiry {o.options[0]?.expiry}. Premium that lapses is short-term income at your ordinary rate.</div>
        </div>
    );
}

function RungRow({ r, open, onToggle }: { r: LadderRung; open: boolean; onToggle: () => void }) {
    const risk = r.constructive_sale.risk;
    return (
        <>
            <tr className="hover cursor-pointer" onClick={onToggle}>
                <td className="whitespace-nowrap font-medium">{r.expiry}<div className="text-xs opacity-60">{r.dte}d · tax yr {r.tax_year}</div>{r.venue === 'FLEX/OTC' && <span className="badge badge-xs badge-outline">FLEX/OTC · indicative</span>}</td>
                <td className="text-right">{r.shares.toLocaleString()}<div className="text-xs opacity-60">{r.contracts} ct @ ${r.avg_basis}</div></td>
                <td className="min-w-[14rem]"><div className="font-medium">{r.structure.name}</div>
                    <div className="text-xs opacity-70">{legsText(r)}</div>
                    <div className="text-xs opacity-60">cap +{r.cap_pct}%{r.floor_pct != null ? ` · floor −${r.floor_pct}%` : ' · no floor'}</div></td>
                <td className={`text-right ${r.net_credit_per_share < -0.005 ? 'text-warning' : r.net_credit_per_share > 0.005 ? 'text-success' : ''}`}>{usd(r.net_credit_total)}
                    <div className="text-xs opacity-60">{r.net_credit_per_share < -0.005 ? 'net debit' : r.net_credit_per_share > 0.005 ? `credit · funds ${r.tax_funding_pct ?? 0}% of tax` : 'zero-cost'}</div></td>
                <td className="text-right">{pct(r.p_called_pct, 0)}<div className="text-xs opacity-60">−{r.stress_pct}% ⇒ −{r.stress_loss_pct}%</div></td>
                <td className="text-right">{usd(r.worst_case_tax)}<div className="text-xs opacity-60">gain {usd(r.worst_case_gain)}</div></td>
                <td className="text-right">{usd(r.expected_tax)}</td>
                <td><span className={`badge badge-sm ${RISK_CLS[risk]}`}>{risk}</span></td>
            </tr>
            {open && (
                <tr>
                    <td colSpan={8} className="bg-base-200/50">
                        <div className="p-3 space-y-3 text-sm">
                            <div>
                                <span className="font-semibold">Lots used (highest basis first): </span>
                                {r.lots.map((l, i) => <span key={i} className="mr-3">{l.shares.toLocaleString()} @ ${l.basis}</span>)}
                            </div>
                            <div className="overflow-x-auto">
                                <div className="font-semibold mb-1">Structure menu for this expiry (ranked on your stance)</div>
                                <table className="table table-xs">
                                    <thead><tr><th>Structure</th><th>Strikes</th><th className="text-right">Net / sh</th><th className="text-right">Funds tax</th><th className="text-right">Tail protection</th><th className="text-right">Upside kept</th><th className="text-right">P(called)</th><th className="text-right">Loss at −{r.stress_pct}%</th><th>§1259</th><th className="text-right">Score</th></tr></thead>
                                    <tbody>
                                        {r.menu.map(m => (
                                            <tr key={m.id} className={m.recommended ? 'font-semibold bg-success/10' : !m.eligible ? 'opacity-50' : ''}>
                                                <td>{m.name}{m.recommended ? ' ✓' : ''}{!m.eligible ? ' (over loss limit)' : ''}</td>
                                                <td className="whitespace-nowrap">{[m.kp ? `+$${m.kp}P` : null, m.kp2 ? `−$${m.kp2}P` : null, `−$${m.kc}C`].filter(Boolean).join(' ')}</td>
                                                <td className="text-right">${m.net_credit_per_share.toFixed(2)}</td><td className="text-right">{m.tax_funding_pct}%</td><td className="text-right">{m.protection_pct}%</td>
                                                <td className="text-right">{m.upside_capture_pct}%</td><td className="text-right">{pct(m.p_called_pct, 0)}</td><td className="text-right">{pct(m.stress_loss_pct)}</td>
                                                <td><span className={`badge badge-xs ${RISK_CLS[m.constructive_risk]}`}>{m.constructive_risk}</span></td><td className="text-right">{m.score}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                                <div className="text-xs opacity-60 mt-1">Funds tax = day-one net premium ÷ tax due if called. Tail protection = cut in the 5%-worst-case after-tax shortfall. Upside kept = share of the stock's expected upside you retain. Pre-tax loss includes premium.</div>
                            </div>
                            <div className="opacity-80">
                                {r.put ? <>Buy {r.contracts} × {r.put.strike} put (spread {pct(r.put.spread_pct, 0)}, OI {r.put.oi}). </> : null}
                                {r.put2 ? <>Sell {r.contracts} × {r.put2.strike} put (OI {r.put2.oi}). </> : null}
                                Sell {r.contracts} × {r.call.strike} call (spread {pct(r.call.spread_pct, 0)}, OI {r.call.oi}).
                                {r.max_loss_vs_spot_pct != null ? <> Worst-case loss from today ≈ {pct(r.max_loss_vs_spot_pct)} of spot.</> : <> Loss is not capped (see stress column).</>}
                                {' '}Probabilities are market-implied ({r.density}), not a forecast.
                            </div>
                            <div className="text-xs opacity-80"><b>Tax treatment:</b> {r.tax_note}</div>
                            {r.rfq && (
                                <div className="rounded-lg border border-info/40 bg-info/5 p-3 space-y-1">
                                    <div className="font-semibold">Quote request (send to the FLEX / OTC desk)</div>
                                    <div>
                                        {r.rfq.underlying} · {r.rfq.contracts} contracts ({r.rfq.shares.toLocaleString()} sh) · expiry {r.rfq.expiry} · {r.rfq.style} · {r.rfq.settlement} · reference spot ${r.rfq.reference_spot}
                                    </div>
                                    <div>{r.rfq.structure}: {r.rfq.buy_put ? `Buy put $${r.rfq.buy_put.strike} (ind. ask $${r.rfq.buy_put.indicative_ask}) · ` : ''}{r.rfq.sell_put ? `Sell put $${r.rfq.sell_put.strike} (ind. bid $${r.rfq.sell_put.indicative_bid}) · ` : ''}Sell call ${r.rfq.sell_call.strike} (ind. bid ${r.rfq.sell_call.indicative_bid}) · indicative net ${r.rfq.indicative_net_per_share}/sh</div>
                                    <div className="text-xs opacity-70">{r.rfq.ask_for}</div>
                                </div>
                            )}
                            <div className="text-xs opacity-80">{r.constructive_sale.note}</div>
                            {r.flags.length > 0 && <ul className="list-disc ml-5 text-warning">{r.flags.map(f => <li key={f}>{f}</li>)}</ul>}
                            <div className="overflow-x-auto">
                                <table className="table table-xs">
                                    <thead><tr><th>Stock at expiry</th><th className="text-right">Price</th><th>Outcome</th><th className="text-right">Tax this yr</th><th className="text-right">After-tax value</th><th className="text-right">Unhedged</th><th className="text-right">Ladder vs unhedged</th></tr></thead>
                                    <tbody>
                                        {r.scenarios.map(s => (
                                            <tr key={s.move_pct}>
                                                <td>{s.move_pct > 0 ? '+' : ''}{s.move_pct}%</td>
                                                <td className="text-right">${s.price}</td>
                                                <td>{s.called ? 'called away' : r.put && s.price < r.put.strike ? 'floor pays' : 'keep shares'}</td>
                                                <td className="text-right">{usd(s.tax_this_year)}</td>
                                                <td className="text-right">{usd(s.after_tax_value)}</td>
                                                <td className="text-right">{usd(s.unhedged_after_tax)}</td>
                                                <td className={`text-right ${s.vs_unhedged >= 0 ? 'text-success' : 'text-error'}`}>{usd(s.vs_unhedged)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                                <div className="text-xs opacity-60 mt-1">After-tax value = proceeds if everything is liquidated right after expiry (embedded gain on shares you keep is taxed at the long-term rate), so ladder and unhedged are compared like-for-like.</div>
                            </div>
                        </div>
                    </td>
                </tr>
            )}
        </>
    );
}

function PositionCard({ p }: { p: LadderPosition }) {
    const [open, setOpen] = useState<string | null>(null);
    if (p.error || !p.summary || !p.timing) {
        return <div className="alert alert-warning text-sm"><AlertCircle className="w-4 h-4" />{p.ticker}: {p.error || 'no data'}</div>;
    }
    const s = p.summary, c = p.context;
    return (
        <div className="rounded-2xl border border-base-300 p-4 space-y-4">
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
                <h4 className="text-xl font-bold">{p.ticker}</h4>
                <span className="opacity-70">spot ${p.spot}</span>
                <span className="opacity-70">{s.shares.toLocaleString()} sh · {usd(s.market_value)}</span>
                <span className="opacity-70">gain {usd(s.unrealized_gain)} ({pct(s.gain_pct, 0)}) · tax if sold today {usd(s.tax_if_sold_today)}</span>
            </div>
            {c && (
                <div className="text-xs opacity-70 flex flex-wrap gap-x-4">
                    <span>IV {pct(c.atm_iv_pct, 0)} / HV30 {pct(c.hv30_pct, 0)} ({c.iv_hv_ratio ?? '—'}×)</span>
                    <span>skew {c.skew_pts != null ? c.skew_pts.toFixed(1) : '—'} pts</span>
                    <span>RSI {c.rsi14 ?? '—'}</span>
                    <span>{c.vs_200d_pct != null ? `${c.vs_200d_pct > 0 ? '+' : ''}${c.vs_200d_pct}% vs 200d` : ''}</span>
                    <span>{c.next_earnings ? `earnings ${c.next_earnings}` : ''}</span>
                </div>
            )}
            <TimingCard t={p.timing} />
            {p.ladder && p.ladder.length > 0 && (
                <div className="space-y-2">
                    <h5 className="font-semibold">Recommended ladder · {s.covered_shares.toLocaleString()} shares across {p.ladder.length} rungs</h5>
                    <div className="overflow-x-auto">
                        <table className="table table-sm">
                            <thead><tr>
                                <th>Expiry</th><th className="text-right">Shares</th><th>Structure</th>
                                <th className="text-right">Net premium</th><th className="text-right">P(called) · stress loss</th><th className="text-right">If called: tax</th><th className="text-right">Expected tax</th><th>§1259</th>
                            </tr></thead>
                            <tbody>
                                {p.ladder.map(r => <RungRow key={r.expiry} r={r} open={open === r.expiry} onToggle={() => setOpen(open === r.expiry ? null : r.expiry)} />)}
                            </tbody>
                        </table>
                    </div>
                    <div className="text-xs opacity-60">Click a rung for the lots, order detail and after-tax scenario table. "If called" is the deterministic worst case for that year's tax.</div>
                </div>
            )}
            {p.overlay && <OverlayCard o={p.overlay} />}
            {p.notes && p.notes.length > 0 && <ul className="list-disc ml-5 text-sm text-info">{p.notes.map(n => <li key={n}>{n}</li>)}</ul>}
        </div>
    );
}

export function ConcentrationLadder() {
    const [positions, setPositions] = useState<PosRow[]>([
        { ticker: 'AAPL', lots: [{ shares: '3000', basis: '25' }, { shares: '1000', basis: '90' }] },
    ]);
    const [stRate, setStRate] = useState('40.8');
    const [ltRate, setLtRate] = useState('23.8');
    const [mode, setMode] = useState<'budget' | 'deferral'>('budget');
    const [budget, setBudget] = useState('250000');
    const [horizon, setHorizon] = useState('3');
    const [rungs, setRungs] = useState('4');
    const [floor, setFloor] = useState('15');
    const [coverage, setCoverage] = useState('100');
    const [deferPut, setDeferPut] = useState(true);
    const [stance, setStance] = useState<'protect' | 'balanced' | 'income'>('balanced');
    const [maxLoss, setMaxLoss] = useState('30');
    const [callProb, setCallProb] = useState('20');
    const [execution, setExecution] = useState<'listed' | 'flex'>('listed');
    const [flexSpread, setFlexSpread] = useState('1.5');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [res, setRes] = useState<LadderResponse | null>(null);

    const setPos = (i: number, f: (p: PosRow) => PosRow) => setPositions(ps => ps.map((p, k) => (k === i ? f(p) : p)));

    const run = async () => {
        setError(null);
        const clean = positions
            .map(p => ({
                ticker: p.ticker.trim().toUpperCase(),
                lots: p.lots.map(l => ({ shares: Math.round(Number(l.shares)), cost_basis: Number(l.basis) }))
                    .filter(l => l.shares > 0 && l.cost_basis > 0),
            }))
            .filter(p => p.ticker && p.lots.length);
        if (!clean.length) { setError('Add a ticker with at least one lot (shares and cost basis).'); return; }
        if (mode === 'budget' && !(Number(budget) > 0)) { setError('Budget mode needs an annual gain budget — or switch to max deferral.'); return; }
        setLoading(true); setRes(null);
        try {
            setRes(await computeConcentrationLadder({
                positions: clean,
                tax: { st_rate_pct: Number(stRate), lt_rate_pct: Number(ltRate) },
                mode, annual_gain_budget: mode === 'budget' ? Number(budget) : null,
                horizon_years: Number(horizon), num_rungs: Number(rungs), floor_pct: Number(floor),
                coverage_pct: Number(coverage), straddle_defers_put_loss: deferPut,
                execution, flex_spread_vol_pts: Number(flexSpread),
                stance, max_loss_pct: Number(maxLoss), call_prob_pct: Number(callProb),
            }));
        } catch (e: any) {
            setError(e?.message || 'Ladder planner failed');
        } finally {
            setLoading(false);
        }
    };

    const num = (label: string, v: string, set: (s: string) => void, extra?: string) => (
        <label className="form-control">
            <span className="label-text text-sm font-medium mb-1">{label}</span>
            <input type="number" className="input input-bordered input-sm w-full" value={v} onChange={e => set(e.target.value)} />
            {extra && <span className="text-xs opacity-60 mt-1">{extra}</span>}
        </label>
    );

    return (
        <div className="space-y-6">
            <div className="border-l-4 border-info bg-info/10 p-4 rounded-r-xl text-sm">
                <div className="font-bold text-info mb-1">Tax-aware collar ladder</div>
                Enter your long-term lots. Using live option chains the planner (1) says whether now is a good time to put a ladder on, and (2) if so builds
                rungs across expiries — a put floor funded by a call cap — assigning your highest-basis lots to be called away first and spreading the
                gain across tax years. All lots are assumed long-term.
            </div>

            <div className="space-y-3">
                {positions.map((p, i) => (
                    <div key={i} className="rounded-xl border border-base-300 p-3 space-y-2">
                        <div className="flex items-center gap-2">
                            <input className="input input-bordered input-sm w-28 uppercase" value={p.ticker} placeholder="Ticker"
                                onChange={e => setPos(i, x => ({ ...x, ticker: e.target.value.toUpperCase() }))} />
                            <span className="text-xs opacity-60">lots (shares · cost basis $/sh)</span>
                            {positions.length > 1 && (
                                <button className="btn btn-ghost btn-xs ml-auto" onClick={() => setPositions(ps => ps.filter((_, k) => k !== i))}><Trash2 className="w-4 h-4" /></button>
                            )}
                        </div>
                        {p.lots.map((l, j) => (
                            <div key={j} className="flex items-center gap-2">
                                <input type="number" className="input input-bordered input-sm w-32" placeholder="Shares" value={l.shares}
                                    onChange={e => setPos(i, x => ({ ...x, lots: x.lots.map((y, k) => (k === j ? { ...y, shares: e.target.value } : y)) }))} />
                                <input type="number" className="input input-bordered input-sm w-32" placeholder="Basis $/sh" value={l.basis}
                                    onChange={e => setPos(i, x => ({ ...x, lots: x.lots.map((y, k) => (k === j ? { ...y, basis: e.target.value } : y)) }))} />
                                {p.lots.length > 1 && (
                                    <button className="btn btn-ghost btn-xs" onClick={() => setPos(i, x => ({ ...x, lots: x.lots.filter((_, k) => k !== j) }))}><Trash2 className="w-3 h-3" /></button>
                                )}
                            </div>
                        ))}
                        <button className="btn btn-ghost btn-xs gap-1" onClick={() => setPos(i, x => ({ ...x, lots: [...x.lots, { shares: '', basis: '' }] }))}><Plus className="w-3 h-3" />Add lot</button>
                    </div>
                ))}
                {positions.length < 5 && (
                    <button className="btn btn-outline btn-sm gap-1" onClick={() => setPositions(ps => [...ps, { ticker: '', lots: [{ shares: '', basis: '' }] }])}><Plus className="w-4 h-4" />Add ticker</button>
                )}
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {num('Long-term rate % (all-in)', ltRate, setLtRate, 'Fed LTCG + NIIT + state')}
                {num('Short-term / ordinary rate %', stRate, setStRate, 'Unassigned call premium is taxed here')}
                <div className="form-control">
                    <span className="label-text text-sm font-medium mb-1">Tax goal</span>
                    <div className="join">
                        <button className={`btn btn-sm join-item ${mode === 'budget' ? 'btn-primary' : ''}`} onClick={() => setMode('budget')}>Annual budget</button>
                        <button className={`btn btn-sm join-item ${mode === 'deferral' ? 'btn-primary' : ''}`} onClick={() => setMode('deferral')}>Max deferral</button>
                    </div>
                    <span className="text-xs opacity-60 mt-1">{mode === 'budget' ? 'Size rungs so each year’s gain fits' : 'Push gains as far out as possible'}</span>
                </div>
                {mode === 'budget' && num('Gain budget / tax year $', budget, setBudget, 'Whole book, long-term gain')}
                {num('Horizon (years)', horizon, setHorizon)}
                {num('Rungs', rungs, setRungs)}
                {num('Put floor % below spot', floor, setFloor, 'Deepened automatically if the band gets too tight')}
                {num('Coverage % of gain shares', coverage, setCoverage)}
                <div className="form-control">
                    <span className="label-text text-sm font-medium mb-1">Structure stance</span>
                    <div className="join">
                        {(['protect', 'balanced', 'income'] as const).map(x => (
                            <button key={x} className={`btn btn-sm join-item capitalize ${stance === x ? 'btn-primary' : ''}`} onClick={() => setStance(x)}>{x}</button>
                        ))}
                    </div>
                    <span className="text-xs opacity-60 mt-1">Ranks collar / put-spread collar / income spread / overwrite</span>
                </div>
                {num('Max loss at −40% stock move, %', maxLoss, setMaxLoss, 'Structures that lose more are ruled out (pre-tax)')}
                {num('Income call: chance of finishing above, %', callProb, setCallProb, 'Lower = further OTM, less premium')}
                <div className="form-control">
                    <span className="label-text text-sm font-medium mb-1">Execution</span>
                    <div className="join">
                        <button className={`btn btn-sm join-item ${execution === 'listed' ? 'btn-primary' : ''}`} onClick={() => setExecution('listed')}>Listed LEAPS</button>
                        <button className={`btn btn-sm join-item ${execution === 'flex' ? 'btn-primary' : ''}`} onClick={() => setExecution('flex')}>FLEX / OTC</button>
                    </div>
                    <span className="text-xs opacity-60 mt-1">{execution === 'flex' ? 'Custom strikes & dates up to 5y (needs the bank/FLEX desk) — indicative prices' : 'Live listed quotes, ~2y max'}</span>
                </div>
                {execution === 'flex' && num('Assumed dealer markup (vol pts/leg)', flexSpread, setFlexSpread, 'Widen for a conservative estimate')}
            </div>
            <label className="label cursor-pointer justify-start gap-3 text-sm">
                <input type="checkbox" className="checkbox checkbox-sm" checked={deferPut} onChange={e => setDeferPut(e.target.checked)} />
                <span>Treat put premium as a straddle loss deferred into basis (conservative; leave on unless your adviser says otherwise)</span>
            </label>

            <button className="btn btn-info" onClick={run} disabled={loading}>
                {loading ? <span className="loading loading-spinner" /> : <Calculator className="w-5 h-5 mr-2" />}
                Check timing & build ladder
            </button>
            {error && <div className="alert alert-error text-sm"><AlertCircle className="w-4 h-4" />{error}</div>}

            {res && (
                <div className="space-y-6">
                    {res.positions.map(p => <PositionCard key={p.ticker} p={p} />)}

                    {res.income && res.income.net_premium_total !== 0 && (
                        <div className="rounded-2xl border border-success/40 bg-success/5 p-4 text-sm">
                            <div className="font-bold mb-1">Premium vs the tax bill</div>
                            Day-one net premium across all rungs is <b>{usd(res.income.net_premium_total)}</b> (≈ {usd(res.income.after_tax_net_premium)} after short-term tax) against{' '}
                            <b>{usd(res.income.tax_if_all_called)}</b> of tax if every rung is called away — it funds <b>{res.income.funding_pct}%</b> of that bill.
                            <div className="text-xs opacity-70 mt-1">{res.income.note}</div>
                        </div>
                    )}

                    {res.by_year.length > 0 && (
                        <div className="rounded-2xl border border-base-300 p-4 space-y-2">
                            <h4 className="font-bold">Tax by year (whole book)</h4>
                            <div className="overflow-x-auto">
                                <table className="table table-sm">
                                    <thead><tr><th>Tax year</th><th>Names</th><th className="text-right">Rungs</th><th className="text-right">Gain if all called</th><th className="text-right">Tax if all called</th><th className="text-right">Expected gain</th><th className="text-right">Expected tax</th><th className="text-right">Budget</th><th className="text-right">Headroom</th></tr></thead>
                                    <tbody>
                                        {res.by_year.map(y => (
                                            <tr key={y.year}>
                                                <td className="font-medium">{y.year}</td><td>{y.tickers.join(', ')}</td><td className="text-right">{y.rungs}</td>
                                                <td className="text-right">{usd(y.worst_case_gain)}</td><td className="text-right">{usd(y.worst_case_tax)}</td>
                                                <td className="text-right">{usd(y.expected_gain)}</td><td className="text-right">{usd(y.expected_tax)}</td>
                                                <td className="text-right">{y.budget != null ? usd(y.budget) : '—'}</td>
                                                <td className={`text-right ${y.within_budget === false ? 'text-error' : ''}`}>{y.headroom != null ? usd(y.headroom) : '—'}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <div className="text-sm opacity-80">
                                Selling these shares today would trigger {usd(res.deferral.tax_if_sold_today)} of tax on the embedded gain; paying it at each rung's expiry instead is worth about{' '}
                                <b>{usd(res.deferral.pv_value_of_deferral)}</b> in present value. {res.deferral.basis}
                            </div>
                        </div>
                    )}

                    {res.execution_guide && (
                        <div className="rounded-2xl border border-base-300 p-4 space-y-2">
                            <h4 className="font-bold">Where to execute — and who to call</h4>
                            <div className="grid md:grid-cols-2 gap-3">
                                {res.execution_guide.map(g => (
                                    <div key={g.venue} className={`rounded-xl border p-3 text-sm ${(res.execution === 'flex' && g.venue !== 'Listed LEAPS') || (res.execution === 'listed' && g.venue === 'Listed LEAPS') ? 'border-info' : 'border-base-300'}`}>
                                        <div className="font-semibold">{g.venue}</div>
                                        <div className="mt-1"><span className="opacity-60">Who: </span>{g.who}</div>
                                        <div className="mt-1 opacity-80">{g.notes}</div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {res.roll_plan.length > 0 && (
                        <div className="rounded-2xl border border-base-300 p-4 space-y-2">
                            <h4 className="font-bold">Review / roll calendar</h4>
                            <table className="table table-xs">
                                <thead><tr><th>Review on</th><th>Name</th><th>Rung expiry</th><th>Action</th></tr></thead>
                                <tbody>{res.roll_plan.map(r => <tr key={r.ticker + r.expiry}><td className="whitespace-nowrap">{r.review_on}</td><td>{r.ticker}</td><td className="whitespace-nowrap">{r.expiry}</td><td className="opacity-80">{r.action}</td></tr>)}</tbody>
                            </table>
                            <div className="text-xs opacity-60">Listed options reach ~2 years; beyond that the ladder is maintained by rolling rungs as they approach expiry and re-running this planner.</div>
                        </div>
                    )}

                    <div className="text-xs opacity-70 space-y-1">
                        <div>Rates: LT {res.assumptions.lt_rate_pct}% · ST {res.assumptions.st_rate_pct}% · risk-free {res.assumptions.risk_free_pct}% ({res.assumptions.risk_free_source}) · cap-to-floor band guard ≥ {res.assumptions.min_band_pct}% · probabilities {res.assumptions.probabilities}.</div>
                        <div>{res.disclaimer}</div>
                    </div>
                </div>
            )}
        </div>
    );
}
