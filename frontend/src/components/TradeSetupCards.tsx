import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Target, Shield, Crosshair, Sparkles, ChevronDown, ChevronUp, Layers3, CalendarClock,
  LineChart, TrendingUp, ShieldCheck, Loader2, Code2, Eye, FlaskConical, Clock, LogIn,
  CheckCircle2, AlertCircle, XCircle, Award, Rocket, Activity, Timer, History, ChevronsUpDown,
} from 'lucide-react';
import type { TradeSetup, TradeSetupsData, SetupVerification, OptionLeg, QullamaggieData, ConnorsData, ConnorsBacktest } from '../types';

// Push an option structure's legs into the Income-Desk → Evaluate tab (sessionStorage handoff).
function researchInEvaluate(ticker: string, legs: OptionLeg[], expiration: string, navigate: (to: string) => void) {
  const evalLegs = (legs || []).map(l => ({
    action: l.action.toUpperCase().startsWith('B') ? 'BUY' : 'SELL',
    type: l.right.toUpperCase().startsWith('C') ? 'CALL' : 'PUT',
    strike: l.strike, expiration,
  }));
  try { sessionStorage.setItem('evaluatePrefill', JSON.stringify({ ticker, legs: evalLegs })); } catch { /* ignore */ }
  navigate('/strategies?strategy=derivative_income&mode=evaluate');
}
import { DirectionBadge, ConfidencePill, InfoTip } from './taUi';
import IndicatorAIConsole from './IndicatorAIConsole';
import PayoffDiagram from './PayoffDiagram';
import { analyzeTa, verifySetup, trackTrade, fetchDayTradeSetups, fetchQullamaggieSetups, fetchConnorsSetups } from '../api';

const money = (n?: number | null) => (n == null ? '—' : `$${n.toFixed(2)}`);
const pctFrom = (n?: number | null) => (n == null ? '—' : `${n > 0 ? '+' : ''}${n}%`);
const d0 = (n?: number | null) => (n == null ? '—' : `${n < 0 ? '-$' : '$'}${Math.abs(Math.round(n)).toLocaleString()}`);

const TYPE_LABEL: Record<string, string> = {
  trend_continuation: 'Trend Pullback', mean_reversion_fade: 'Mean-Reversion Fade',
  range_income: 'Range Income', range_bracket: 'Range Rotation', breakout: 'Breakout',
  trend_momentum: 'Momentum (enter now)', position_accumulate: 'Position Accumulate',
  vwap_hold_long: 'VWAP Hold', vwap_reject_short: 'VWAP Reject', vwap_pullback_long: 'VWAP Pullback',
  vwap_pullback_short: 'VWAP Pullback', opening_range_break_long: 'Opening-Range Break',
  opening_range_break_short: 'Opening-Range Break',
  qm_breakout: 'Momentum Breakout', qm_episodic_pivot: 'Episodic Pivot', qm_parabolic_short: 'Parabolic Short',
};

function EdgeStats({ pop, ev, evLabel, extra }: { pop?: number | null; ev?: number | null; evLabel: string; extra?: React.ReactNode }) {
  if (pop == null && ev == null) return null;
  const evTone = ev == null ? '' : ev >= 0 ? 'text-success' : 'text-error';
  return (
    <div className="flex items-center gap-3 flex-wrap text-[11px] rounded-lg border border-white/[0.06] bg-base-200/20 px-2.5 py-1.5">
      <span className="flex items-center gap-1"><span className="text-base-content/45">PoP</span> <b className="tabular-nums">{pop != null ? `${pop}%` : '—'}</b><InfoTip text="Probability of profit implied by the options market (ATM implied vol / risk-neutral distribution) — the market's own odds this trade pays." /></span>
      <span className="flex items-center gap-1"><span className="text-base-content/45">{evLabel}</span> <b className={`tabular-nums ${evTone}`}>{ev != null ? d0(ev) : '—'}</b><InfoTip text="Expected value = probability-weighted average outcome. Positive = a mathematical edge; negative = the odds don't justify it." /></span>
      {extra}
    </div>
  );
}

// ── the entry / stop / target "plan" visual ──
function PriceLadder({ setup, spot }: { setup: TradeSetup; spot: number | null }) {
  const entry = setup.entry.level ?? (setup.entry.low != null && setup.entry.high != null ? (setup.entry.low + setup.entry.high) / 2 : null);
  const stop = setup.stop.level;
  const target = setup.targets?.[0]?.level ?? null;
  const t2 = setup.targets?.[1]?.level ?? null;
  const pts = [entry, setup.entry.low, setup.entry.high, stop, target, t2, spot].filter((x): x is number => x != null && isFinite(x));
  if (pts.length < 2 || entry == null) return null;
  const min = Math.min(...pts), max = Math.max(...pts);
  const pad = (max - min) * 0.16 || max * 0.01 || 1;
  const lo = min - pad, hi = max + pad;
  const y = (p: number) => (1 - (p - lo) / (hi - lo)) * 100;
  const seg = (a: number | null, b: number | null) => (a == null || b == null) ? null : { top: Math.min(y(a), y(b)), height: Math.abs(y(a) - y(b)) };
  const reward = seg(entry, target); const risk = seg(entry, stop);

  const Row = ({ price, label, tone, Icon, strong }: { price: number | null; label: string; tone: string; Icon: any; strong?: boolean }) => {
    if (price == null) return null;
    return (
      <div className="absolute left-0 right-0 flex items-center" style={{ top: `${y(price)}%`, transform: 'translateY(-50%)' }}>
        <span className={`w-16 text-right text-[10px] tabular-nums pr-1.5 font-semibold ${tone}`}>{money(price)}</span>
        <span className={`flex-1 border-t ${strong ? 'border-solid' : 'border-dashed'} ${tone} opacity-40`} />
        <span className={`inline-flex items-center gap-1 text-[9px] pl-1.5 whitespace-nowrap ${tone}`}><Icon className="w-2.5 h-2.5" />{label}</span>
      </div>
    );
  };
  return (
    <div className="relative h-[190px] w-full">
      <div className="absolute" style={{ left: '68px', width: '3px', top: 0, bottom: 0 }}>
        <div className="absolute w-full rounded-full bg-base-content/10" style={{ top: 0, bottom: 0 }} />
        {reward && <div className="absolute w-full rounded-full bg-success/70" style={{ top: `${reward.top}%`, height: `${reward.height}%` }} />}
        {risk && <div className="absolute w-full rounded-full bg-error/70" style={{ top: `${risk.top}%`, height: `${risk.height}%` }} />}
      </div>
      <Row price={t2} label="T2" tone="text-success" Icon={Target} />
      <Row price={target} label="Target" tone="text-success" Icon={Target} strong />
      <Row price={setup.entry.high !== setup.entry.low ? setup.entry.high : null} label="" tone="text-warning" Icon={Crosshair} />
      <Row price={entry} label="Entry" tone="text-warning" Icon={Crosshair} strong />
      <Row price={setup.entry.high !== setup.entry.low ? setup.entry.low : null} label="" tone="text-warning" Icon={Crosshair} />
      <Row price={stop} label="Stop" tone="text-error" Icon={Shield} strong />
      {spot != null && (
        <div className="absolute right-0 flex items-center gap-1" style={{ top: `${y(spot)}%`, transform: 'translateY(-50%)' }}>
          <span className="text-[9px] text-primary font-bold">now →</span>
        </div>
      )}
    </div>
  );
}

function EquityView({ setup, spot }: { setup: TradeSetup; spot: number | null }) {
  const eq = setup.equity_plan;
  const ee = setup.edge?.equity;
  return (
    <div className="space-y-2">
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <PriceLadder setup={setup} spot={spot} />
      <div className="space-y-2">
        {eq ? (
          <div className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2.5 text-[11px] leading-relaxed">
            <div className="text-[10px] uppercase tracking-wider text-base-content/45 mb-1 flex items-center gap-1"><TrendingUp className="w-3 h-3" /> Equity plan — {eq.side}</div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 tabular-nums">
              <span className="text-base-content/50">Entry</span><span className="text-right font-semibold">{money(eq.entry)}</span>
              <span className="text-base-content/50">Stop</span><span className="text-right font-semibold text-error">{money(eq.stop)}</span>
              {eq.targets.map((t, i) => (<React.Fragment key={i}><span className="text-base-content/50">Target {i + 1}</span><span className="text-right font-semibold text-success">{money(t.level)} <span className="opacity-50">(+{money(t.gain_per_share)}/sh)</span></span></React.Fragment>))}
              <span className="text-base-content/50">Risk/share</span><span className="text-right">{money(eq.risk_per_share)}</span>
              <span className="text-base-content/50">Reward:Risk</span><span className="text-right font-bold text-success">{eq.risk_reward}R</span>
            </div>
            <div className="mt-1.5 pt-1.5 border-t border-white/[0.05] text-[11px] text-base-content/70">
              <b>{eq.suggested_shares}</b> shares · risk <b className="text-error">{d0(eq.dollar_risk)}</b> to make <b className="text-success">{d0(eq.dollar_reward_t1)}</b> at T1.
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5 text-[11px] text-base-content/50">No directional equity plan for a neutral setup — see the options play.</div>
        )}
      </div>
    </div>
    {ee && <EdgeStats pop={ee.pop_pct} ev={ee.ev_per_share} evLabel="EV/share"
      extra={ee.half_kelly_risk_pct != null && <span className="flex items-center gap-1"><span className="text-base-content/45">½-Kelly size</span> <b>{ee.half_kelly_risk_pct}% of book</b><InfoTip text="Edge-based position size: half the Kelly-optimal fraction (capped at 2% of account), from the PoP and payoff ratio." /></span>} />}
    </div>
  );
}

function OptionsView({ setup, spot, ticker }: { setup: TradeSetup; spot: number | null; ticker: string }) {
  const op = setup.options_plan;
  const oe = setup.edge?.options;
  const navigate = useNavigate();
  const expiration = op?.expiry?.date || setup.options.expiry?.date || '';
  const research = (legs?: OptionLeg[]) => legs?.length && researchInEvaluate(ticker, legs, expiration, navigate);
  return (
    <div className="space-y-2">
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <div>
        {op?.available && op.payoff?.length ? <PayoffDiagram plan={op} spot={spot ?? 0} />
          : <div className="h-[170px] flex items-center justify-center text-center text-[11px] text-base-content/40 px-3">{op?.note || 'Payoff unavailable — see the option idea in the plan text.'}</div>}
      </div>
      <div className="space-y-2 text-[11px]">
        <div className="rounded-lg border border-white/[0.06] bg-base-200/30 p-2.5">
          <div className="flex items-center justify-between gap-2 flex-wrap mb-0.5">
            <span className="text-[10px] uppercase tracking-wider text-base-content/45 flex items-center gap-1"><Layers3 className="w-3 h-3" /> {op?.structure || setup.options.structure}</span>
            {setup.options.expiry?.date && <span className="inline-flex items-center gap-1 rounded-md bg-primary/10 border border-primary/20 text-primary px-1.5 py-0.5 text-[10px] font-semibold"><CalendarClock className="w-3 h-3" /> {setup.options.expiry.date}{setup.options.expiry.dte != null ? ` · ${setup.options.expiry.dte}DTE` : ''}</span>}
          </div>
          {op?.why && <div className="text-[10px] text-base-content/55 italic leading-snug mb-1">{op.why}</div>}
          {op?.available && op.legs?.length ? (
            <>
              <table className="w-full tabular-nums text-[10.5px]">
                <tbody>
                  {op.legs.map((l, i) => (
                    <tr key={i}><td className={l.action === 'Buy' ? 'text-success' : 'text-error'}>{l.action}</td><td>{l.right}</td><td className="text-right">${l.strike}</td><td className="text-right text-base-content/60">@ {money(l.price)}</td></tr>
                  ))}
                </tbody>
              </table>
              <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 mt-1.5 pt-1.5 border-t border-white/[0.05]">
                <span className="text-base-content/50">Net {op.net_cost_label}</span><span className="text-right font-semibold">{d0(Math.abs(op.net_cost ?? 0))}</span>
                <span className="text-base-content/50">Max profit</span><span className="text-right font-semibold text-success">{d0(op.max_profit)}</span>
                <span className="text-base-content/50">Max loss</span><span className="text-right font-semibold text-error">{d0(op.max_loss)}</span>
                <span className="text-base-content/50">Breakeven</span><span className="text-right font-semibold">{op.breakevens?.map(b => `$${b}`).join(', ') || '—'}</span>
              </div>
              <div className="flex items-center justify-between gap-2 mt-1">
                <span className="text-[9px] text-base-content/35">Priced {op.priced_from}. 1 contract; scale to taste.</span>
                <button className="inline-flex items-center gap-1 text-[10px] text-primary hover:underline" onClick={() => research(op.legs)}><FlaskConical className="w-3 h-3" /> Research in Evaluate</button>
              </div>
            </>
          ) : (
            <div className="text-base-content/60 leading-snug">{setup.options.detail}</div>
          )}
        </div>
      </div>
    </div>
    {!!setup.options_alternatives?.length && (
      <div className="rounded-lg border border-white/[0.06] bg-base-200/20 p-2 text-[10.5px]">
        <div className="text-[9px] uppercase tracking-wider text-base-content/40 mb-1">Alternative structures (primary picked by expected value)</div>
        {setup.options_alternatives.map((a, i) => (
          <div key={i} className="py-1 border-t border-white/[0.04] first:border-0">
            <div className="flex items-center justify-between gap-2">
              <span className="text-base-content/75 font-semibold">{a.structure}</span>
              <span className="tabular-nums whitespace-nowrap">{a.net_cost_label} {d0(Math.abs(a.net_cost ?? 0))} · PoP {a.pop_pct ?? '—'}% · EV <b className={a.ev != null && a.ev >= 0 ? 'text-success' : 'text-error'}>{a.ev != null ? d0(a.ev) : '—'}</b></span>
            </div>
            {!!a.legs?.length && (
              <div className="flex items-center justify-between gap-2 mt-0.5">
                <span className="text-[10px] text-base-content/55">{a.legs.map(l => `${l.action} ${l.right} $${l.strike}${l.price != null ? ` @ ${money(l.price)}` : ''}`).join('  /  ')}</span>
                <button className="inline-flex items-center gap-1 text-[10px] text-primary hover:underline whitespace-nowrap" onClick={() => research(a.legs)}><FlaskConical className="w-3 h-3" /> Research</button>
              </div>
            )}
          </div>
        ))}
      </div>
    )}
    {oe && <EdgeStats pop={oe.pop_pct} ev={oe.ev} evLabel="EV" />}
    </div>
  );
}

const VERDICT_TONE: Record<string, string> = { take: 'badge-success', adjust: 'badge-warning', pass: 'badge-error' };

function VerdictView({ v }: { v: SetupVerification }) {
  if (v.raw) return <pre className="text-[10px] bg-base-300/60 rounded p-2 overflow-auto max-h-60 text-base-content/70">{v.raw}</pre>;
  const alt = v.alternate;
  return (
    <div className="space-y-1.5 text-[11px]">
      <div className="flex items-center gap-2">
        {v.verdict && <span className={`badge badge-sm ${VERDICT_TONE[v.verdict] || 'badge-ghost'} uppercase font-bold`}>{v.verdict}</span>}
        {v.confidence && <span className="text-[10px] text-base-content/50">{v.confidence} confidence</span>}
      </div>
      {v.summary && <p className="text-xs text-base-content/80">{v.summary}</p>}
      {v.pnl_check && <p className="text-base-content/60"><b className="text-base-content/75">P&amp;L check:</b> {v.pnl_check}</p>}
      {!!v.verification?.length && <ul className="list-disc pl-4 text-base-content/60 space-y-0.5">{v.verification.map((x, i) => <li key={i}>{x}</li>)}</ul>}
      {!!v.adjustments?.length && <div><b className="text-warning">Adjustments:</b><ul className="list-disc pl-4 text-base-content/60">{v.adjustments.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
      {alt?.name && (
        <div className="rounded-lg border border-primary/20 bg-primary/5 p-2">
          <b className="text-primary">Alternate — {alt.name}</b> <span className="text-base-content/50">({alt.kind}/{alt.direction})</span>
          <div className="text-base-content/70 mt-0.5">{[alt.entry && `entry ${alt.entry}`, alt.stop && `stop ${alt.stop}`, alt.target && `target ${alt.target}`, alt.structure].filter(Boolean).join(' · ')}</div>
          {alt.why && <div className="text-base-content/55 mt-0.5">{alt.why}</div>}
        </div>
      )}
      {!!v.risks?.length && <div><b className="text-error">Risks:</b> {v.risks.join('; ')}</div>}
      {v.enrichment_read && (
        <div className="border-t border-white/5 pt-1 space-y-0.5 text-[10px] text-base-content/55">
          {(['sentiment', 'fundamental', 'analyst'] as const).map(k => v.enrichment_read?.[k] && <div key={k}><b className="capitalize">{k}:</b> {v.enrichment_read[k]}</div>)}
        </div>
      )}
    </div>
  );
}

function SetupCard({ setup, ticker, spot, dossier }: { setup: TradeSetup; ticker: string; spot: number | null; dossier: Record<string, unknown> }) {
  const navigate = useNavigate();
  const [showWhy, setShowWhy] = useState(false);
  const [showAI, setShowAI] = useState(false);
  const [showVerify, setShowVerify] = useState(false);
  const [planView, setPlanView] = useState<'equity' | 'options'>(setup.equity_plan ? 'equity' : 'options');
  const [enrich, setEnrich] = useState({ sentiment: false, fundamental: false, analyst: false });
  const [verifying, setVerifying] = useState(false);
  const [verifyRes, setVerifyRes] = useState<SetupVerification | null>(null);
  const [verifyErr, setVerifyErr] = useState<string | null>(null);
  const [tracking, setTracking] = useState(false);
  const [tracked, setTracked] = useState(false);

  const doTrack = async () => {
    setTracking(true);
    try {
      await trackTrade({
        ticker,
        direction: setup.direction,
        instrument: planView === 'options' ? 'options' : 'equity',
        setup_type: setup.type,
        title: `${setup.type.replace(/_/g, ' ')} ${setup.direction}`,
        entry_low: setup.entry.low,
        entry_high: setup.entry.high,
        entry_level: setup.entry.level,
        stop_level: setup.stop.level,
        target_levels: (setup.targets || []).map(t => t.level).filter((n): n is number => n != null),
        setup_snapshot: setup as unknown as Record<string, unknown>,
        evaluate_now: true,
      });
      setTracked(true);
    } catch { /* ignore — button reverts */ }
    finally { setTracking(false); }
  };

  const rr = setup.risk_reward;
  const rrTone = rr == null ? 'text-base-content/50' : rr >= 2 ? 'text-success' : rr >= 1 ? 'text-warning' : 'text-error';
  const fitWarn = setup.regime_fit === 'counter_regime';

  const runVerify = async () => {
    setVerifying(true); setVerifyErr(null);
    try {
      const r = await verifySetup(ticker, setup as unknown as Record<string, unknown>, dossier || {}, enrich);
      setVerifyRes(r.verification);
    } catch (e: any) { setVerifyErr(e?.message || 'Verification failed'); }
    finally { setVerifying(false); }
  };

  return (
    <div className="glass-card p-4">
      <div className="flex items-start justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-bold text-base-content/40">#{setup.rank}</span>
          <DirectionBadge direction={setup.direction} />
          <span className="text-sm font-bold text-base-content/85">{TYPE_LABEL[setup.type] || setup.type}</span>
          <ConfidencePill level={setup.confidence} />
          {setup.horizon && (
            <span className="badge badge-xs bg-info/15 text-info border-info/30 gap-1" title={setup.horizon.note}>
              <Clock className="w-3 h-3" /> {setup.horizon.label}{setup.horizon.est_days ? ` · ~${setup.horizon.est_days}d` : ''}
            </span>
          )}
          {fitWarn && <span className="badge badge-xs badge-warning gap-1">counter-trend</span>}
        </div>
        <div className="text-right">
          <div className={`text-lg font-bold tabular-nums leading-none ${rrTone}`}>{rr != null ? `${rr}R` : '—'}</div>
          <div className="text-[9px] text-base-content/40 flex items-center gap-0.5 justify-end">reward : risk <InfoTip text="Dollars targeted per dollar risked (to T1). Above 2R is strong." /></div>
        </div>
      </div>

      <p className="text-sm text-base-content/75 leading-snug mt-2.5">{setup.thesis}</p>

      {setup.entry_style && (
        <div className="mt-2 rounded-lg border border-primary/20 bg-primary/5 px-2.5 py-1.5 text-[11px]">
          <div className="flex items-center gap-1.5 font-semibold text-primary flex-wrap">
            <LogIn className="w-3.5 h-3.5 shrink-0" /> {setup.entry_style.label}
            {setup.from_current && (setup.from_current.to_entry_pct != null || setup.from_current.to_t1_pct != null) && (
              <span className="font-normal text-base-content/50">· from now: entry {pctFrom(setup.from_current.to_entry_pct)}, T1 {pctFrom(setup.from_current.to_t1_pct)}</span>
            )}
          </div>
          <p className="text-base-content/60 mt-0.5 leading-snug">{setup.entry_style.note}</p>
        </div>
      )}

      {setup.event_risk && (
        <div className="mt-2 rounded-lg border border-warning/30 bg-warning/10 text-warning px-2.5 py-1.5 text-[11px] flex items-start gap-1.5">
          <CalendarClock className="w-3.5 h-3.5 mt-0.5 shrink-0" /><span>{setup.event_risk.warning}</span>
        </div>
      )}

      {/* Equity ⇄ Options toggle */}
      <div className="join mt-3 bg-base-200/60 p-0.5 rounded-lg border border-white/[0.05]">
        <button className={`join-item btn btn-xs gap-1 ${planView === 'equity' ? 'btn-active bg-primary/20 text-primary border-primary/30' : 'btn-ghost text-base-content/60'}`} onClick={() => setPlanView('equity')}><TrendingUp className="w-3 h-3" /> Stock</button>
        <button className={`join-item btn btn-xs gap-1 ${planView === 'options' ? 'btn-active bg-primary/20 text-primary border-primary/30' : 'btn-ghost text-base-content/60'}`} onClick={() => setPlanView('options')}><LineChart className="w-3 h-3" /> Options</button>
      </div>

      <div className="mt-2">
        {planView === 'equity' ? <EquityView setup={setup} spot={spot} /> : <OptionsView setup={setup} spot={spot} ticker={ticker} />}
      </div>

      {!!setup.what_to_watch?.length && (
        <div className="mt-3 rounded-lg border border-white/[0.06] bg-base-200/20 p-2.5">
          <div className="text-[10px] uppercase tracking-wider text-base-content/45 mb-1 flex items-center gap-1"><Eye className="w-3 h-3" /> While you're in the trade</div>
          <ul className="list-disc pl-4 text-[11px] text-base-content/65 space-y-0.5 leading-snug">
            {setup.what_to_watch.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-2 mt-3 flex-wrap">
        <button className="btn btn-ghost btn-xs gap-1" onClick={() => setShowWhy(w => !w)}>
          {showWhy ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />} Why this setup
        </button>
        <button className={`btn btn-xs gap-1 ${showVerify ? 'btn-secondary' : 'btn-outline btn-secondary'}`} onClick={() => setShowVerify(s => !s)}><ShieldCheck className="w-3.5 h-3.5" /> Verify with AI</button>
        <button className="btn btn-ghost btn-xs gap-1" onClick={() => setShowAI(s => !s)}><Sparkles className="w-3.5 h-3.5" /> Ask AI</button>
        {tracked ? (
          <button className="btn btn-xs gap-1 btn-success btn-outline" onClick={() => navigate('/trade-tracking')}>
            <Crosshair className="w-3.5 h-3.5" /> Tracking ✓ — open
          </button>
        ) : (
          <button className="btn btn-xs gap-1 btn-primary" onClick={doTrack} disabled={tracking}
            title="Add this setup to the Trade Tracker to confirm entry and manage the exit">
            {tracking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Crosshair className="w-3.5 h-3.5" />} Track this trade
          </button>
        )}
      </div>

      {showWhy && (
        <div className="mt-2">
          {setup.evidence?.length ? (
            <div className="flex flex-wrap gap-1">{setup.evidence.filter(Boolean).map((e, i) => <span key={i} className="badge badge-sm bg-base-100/60 border-base-content/10 text-[10px]">{e}</span>)}</div>
          ) : null}
        </div>
      )}

      {showVerify && (
        <div className="mt-2 rounded-lg border border-secondary/20 bg-base-200/20 p-2.5 space-y-2">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <span className="text-[11px] font-semibold text-secondary flex items-center gap-1"><ShieldCheck className="w-3.5 h-3.5" /> AI pre-trade review</span>
            <div className="flex items-center gap-2 text-[10px]">
              {(['sentiment', 'fundamental', 'analyst'] as const).map(k => (
                <label key={k} className="flex items-center gap-1 cursor-pointer capitalize">
                  <input type="checkbox" className="checkbox checkbox-xs" checked={enrich[k]} onChange={() => setEnrich(p => ({ ...p, [k]: !p[k] }))} /> {k}
                </label>
              ))}
              <button className="btn btn-secondary btn-xs gap-1" onClick={runVerify} disabled={verifying}>
                {verifying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />} {verifyRes ? 'Re-verify' : 'Verify'}
              </button>
            </div>
          </div>
          <p className="text-[10px] text-base-content/40">Sends this trade + the full indicator dossier{Object.values(enrich).some(Boolean) ? ' + ' + Object.entries(enrich).filter(([, v]) => v).map(([k]) => k).join('/') : ''} to the AI for an independent verdict, a P&amp;L sanity-check, and an alternate trade.</p>
          {verifyErr && <div className="alert alert-error text-xs py-1.5">{verifyErr}</div>}
          {verifyRes && <VerdictView v={verifyRes} />}
        </div>
      )}

      {showAI && (
        <div className="mt-2">
          <IndicatorAIConsole
            selectionJson={{ ticker, spot, setup }}
            chips={[{ key: 'setup', label: `${TYPE_LABEL[setup.type] || setup.type} (${setup.direction})`, tone: 'text-base-content/70' }]}
            analyzeFn={(sel, msgs) => analyzeTa(ticker, sel, msgs)}
          />
        </div>
      )}
    </div>
  );
}

// ── Generic strategy checklist panel (shared by named strategies: Qullamäggie, Connors, …) ──
const STATUS_ICON: Record<string, { Icon: any; tone: string }> = {
  pass: { Icon: CheckCircle2, tone: 'text-success' },
  warn: { Icon: AlertCircle, tone: 'text-warning' },
  fail: { Icon: XCircle, tone: 'text-error' },
};
const TONE_CLASS: Record<string, string> = {
  good: 'text-success border-success/40 bg-success/10',
  info: 'text-info border-info/40 bg-info/10',
  warn: 'text-warning border-warning/40 bg-warning/10',
  bad: 'text-error border-error/40 bg-error/10',
  neutral: 'text-base-content/60 border-base-content/25 bg-base-content/5',
};

interface PanelCheck { key: string; label: string; status: string; value: string; ideal: string; detail: string }
interface PanelData {
  Icon: any;
  title: string;
  badge: string;          // "A" (grade) or "BUY ZONE" (signal state)
  tone: string;           // good | info | warn | bad | neutral
  score: number;          // /100
  subBadge: string;       // "Candidate" / "Armed"
  subBadgeGood: boolean;
  summary: string;
  stats: { label: string; value: string }[];
  checks: PanelCheck[];
  execution?: { label: string; text: string }[] | null;
  backtest?: ConnorsBacktest | null;
  footer: string;
}

function CheckRow({ c }: { c: PanelCheck }) {
  const [open, setOpen] = useState(false);
  const { Icon, tone } = STATUS_ICON[c.status] || STATUS_ICON.warn;
  return (
    <div className="border-t border-white/[0.05] first:border-0 py-1.5">
      <button className="w-full flex items-center gap-2 text-left" onClick={() => setOpen(o => !o)}>
        <Icon className={`w-4 h-4 shrink-0 ${tone}`} />
        <span className="text-[11.5px] font-semibold text-base-content/80 flex-1">{c.label}</span>
        <span className={`text-[11px] tabular-nums font-bold ${tone}`}>{c.value}</span>
        {open ? <ChevronUp className="w-3 h-3 text-base-content/40" /> : <ChevronDown className="w-3 h-3 text-base-content/40" />}
      </button>
      {open && (
        <div className="pl-6 pr-1 pt-1 text-[10.5px] text-base-content/55 leading-snug">
          {c.detail} <span className="text-base-content/35">· ideal: {c.ideal}</span>
        </div>
      )}
    </div>
  );
}

function Backtest({ bt }: { bt: ConnorsBacktest }) {
  if (!bt || !bt.trades) return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/20 px-3 py-2 text-[10.5px] text-base-content/50">
      <History className="w-3 h-3 inline mr-1" /> No historical signals in the sample window to backtest.
    </div>
  );
  const winTone = (bt.win_rate_pct ?? 0) >= 60 ? 'text-success' : (bt.win_rate_pct ?? 0) >= 45 ? 'text-warning' : 'text-error';
  return (
    <div className="rounded-lg border border-white/[0.06] bg-base-200/25 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-base-content/45 mb-1 flex items-center gap-1"><History className="w-3 h-3" /> In-sample backtest (this name)</div>
      <div className="flex items-center gap-x-4 gap-y-0.5 flex-wrap text-[11px] tabular-nums">
        <span>Win rate <b className={winTone}>{bt.win_rate_pct}%</b></span>
        <span className="text-base-content/50">{bt.trades} signals</span>
        <span>avg <b className={(bt.avg_return_pct ?? 0) >= 0 ? 'text-success' : 'text-error'}>{(bt.avg_return_pct ?? 0) > 0 ? '+' : ''}{bt.avg_return_pct}%</b>/trade</span>
        <span className="text-base-content/50">~{bt.avg_hold_days}d hold</span>
        {bt.payoff != null && <span className="text-base-content/50">payoff {bt.payoff}</span>}
      </div>
      <p className="text-[9.5px] text-base-content/35 mt-0.5">{bt.note}</p>
    </div>
  );
}

function StrategyPanel({ p }: { p: PanelData }) {
  return (
    <div className="glass-card p-4 space-y-3">
      <div className="flex items-start gap-3 flex-wrap">
        <div className={`flex flex-col items-center justify-center rounded-xl border px-3 py-1.5 min-w-[64px] max-w-[130px] text-center ${TONE_CLASS[p.tone] || TONE_CLASS.neutral}`}>
          <span className="text-[13px] font-black leading-tight">{p.badge}</span>
          <span className="text-[9px] tabular-nums opacity-70 mt-0.5">{p.score}/100</span>
        </div>
        <div className="flex-1 min-w-[200px]">
          <div className="flex items-center gap-1.5 text-sm font-bold text-base-content/85">
            <p.Icon className="w-4 h-4 text-primary" /> {p.title}
            <span className={`badge badge-xs ${p.subBadgeGood ? 'badge-success' : 'badge-ghost'} font-semibold`}>{p.subBadge}</span>
          </div>
          <p className="text-[11.5px] text-base-content/65 leading-snug mt-1">{p.summary}</p>
          <div className="flex items-center gap-x-3 gap-y-0.5 flex-wrap text-[10px] text-base-content/50 tabular-nums mt-1.5">
            {p.stats.map((s, i) => <span key={i}>{s.label} <b className="text-base-content/70">{s.value}</b></span>)}
          </div>
        </div>
      </div>
      <div className="rounded-lg border border-white/[0.06] bg-base-200/25 px-3 py-1">
        {p.checks.map(c => <CheckRow key={c.key} c={c} />)}
      </div>
      {p.backtest !== undefined && p.backtest !== null && <Backtest bt={p.backtest} />}
      {p.execution && p.execution.length > 0 && (
        <div className="rounded-lg border border-primary/20 bg-primary/[0.04] px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-primary/80 mb-1 flex items-center gap-1"><Timer className="w-3 h-3" /> Execution — how to actually get in &amp; out</div>
          <div className="space-y-1">
            {p.execution.map((e, i) => (
              <div key={i} className="text-[11px] leading-snug"><b className="text-base-content/75">{e.label}:</b> <span className="text-base-content/60">{e.text}</span></div>
            ))}
          </div>
        </div>
      )}
      <p className="text-[10px] text-base-content/40 leading-snug flex items-start gap-1">
        <p.Icon className="w-3 h-3 mt-0.5 shrink-0" /> {p.footer}
      </p>
    </div>
  );
}

// ── adapters: each named strategy's payload → the generic panel shape ──
function qmPanel(d: QullamaggieData): PanelData {
  const q = d.qualification, m = d.metrics, ma = d.moving_averages;
  const tone = q.grade === 'A' ? 'good' : q.grade === 'B' ? 'info' : q.grade === 'C' ? 'warn' : 'neutral';
  return {
    Icon: Rocket, title: 'Qullamäggie screen', badge: `Grade ${q.grade}`, tone, score: q.score,
    subBadge: q.is_candidate ? 'Candidate' : 'Not a candidate', subBadgeGood: q.is_candidate, summary: q.summary,
    stats: [
      { label: 'ADR', value: `${m.adr_pct ?? '—'}%` },
      { label: 'from 52w-high', value: `${m.pct_from_52w_high ?? '—'}%` },
      { label: 'best move', value: `+${m.moves?.best_pct ?? '—'}%` },
      { label: '10/20/50', value: `${money(ma.ema10)}/${money(ma.ema20)}/${money(ma.sma50)}` },
    ],
    checks: q.checks, execution: null, backtest: null,
    footer: "Kristjan Kullamägi's method: trade only the strongest leaders — a big prior move, high ADR%, price stacked above rising 10/20-EMA & 50-SMA near new highs — buying the break of a tight base (entry refined by the day's opening-range high), a tight stop, then a 10/20-day MA trail. Setups appear below only when the tape supports them.",
  };
}
function connorsPanel(d: ConnorsData): PanelData {
  const s = d.signal, i = d.indicators, v = d.vix, e = d.execution;
  const tone = s.tone === 'buy' ? 'good' : s.tone === 'short' ? 'bad' : s.tone === 'watch' ? 'warn' : 'neutral';
  return {
    Icon: Activity, title: 'Connors RSI-2', badge: s.state, tone, score: s.score,
    subBadge: s.armed ? 'Armed' : 'No signal', subBadgeGood: s.armed, summary: s.summary,
    stats: [
      { label: 'RSI(2)', value: `${i.rsi2 ?? '—'}` },
      { label: 'RSI(5)', value: `${i.rsi5 ?? '—'}` },
      { label: 'RSI(10)', value: `${i.rsi10 ?? '—'}` },
      { label: '5-SMA', value: money(i.sma5) },
      { label: '200-SMA', value: money(i.sma200) },
      { label: 'VIX', value: v ? `${v.level} (${v.pct_above_sma10 > 0 ? '+' : ''}${v.pct_above_sma10}%${v.fear_spike ? ' fear' : ''})` : '—' },
    ],
    checks: s.checks, backtest: d.backtest,
    execution: e ? [
      { label: 'Order', text: e.recommended_order },
      { label: 'Overnight', text: e.overnight_risk },
      { label: 'Open alt', text: e.open_alternative },
      { label: 'Exit', text: e.exit_basis },
      { label: 'Stops', text: e.stops_note },
    ] : null,
    footer: "Larry Connors' 2-Period RSI is a MEAN-REVERSION system: buy short-term oversold dips (RSI-2 < 10) in an uptrend (above the 200-SMA), exit on the close back above the 5-day SMA — the mirror for shorts below the 200-SMA. High win-rate, small targets: the edge is frequency, not reward:risk (see the backtest). A stretched VIX (fear) marks the highest-probability windows.",
}
;
}

type StyleKey = 'swing' | 'position' | 'day';
const STYLE_TABS: { k: StyleKey; label: string; hint: string }[] = [
  { k: 'swing', label: 'Swing', hint: 'days–weeks' },
  { k: 'position', label: 'Position', hint: 'weeks–months' },
  { k: 'day', label: 'Day trade', hint: 'intraday' },
];

// named technical strategies live in the dropdown (extensible — add more here)
interface NamedStrategy { key: string; label: string; hint: string; fetch: (t: string) => Promise<any>; toPanel: (d: any) => PanelData }
const NAMED_STRATEGIES: NamedStrategy[] = [
  { key: 'qullamaggie', label: 'Qullamäggie', hint: 'momentum breakout', fetch: (t) => fetchQullamaggieSetups(t).then(r => r.qullamaggie_setup), toPanel: qmPanel },
  { key: 'connors_rsi2', label: 'Connors RSI-2', hint: 'mean reversion', fetch: (t) => fetchConnorsSetups(t).then(r => r.connors_setup), toPanel: connorsPanel },
];

interface StratState { data?: any; loading: boolean; err?: string; tried: boolean }

export default function TradeSetupCards({ data, ticker }: { data: TradeSetupsData; ticker: string }) {
  const [showDossier, setShowDossier] = useState(false);
  const [tab, setTab] = useState<string>('swing');
  const [dayData, setDayData] = useState<TradeSetup[] | null>(null);
  const [dayLoading, setDayLoading] = useState(false);
  const [dayErr, setDayErr] = useState<string | null>(null);
  const [dayTried, setDayTried] = useState(false);
  const [strat, setStrat] = useState<Record<string, StratState>>({});

  const setups = data.setups || [];
  const dossier = (data.dossier || {}) as Record<string, unknown>;
  const swing = setups.filter(s => (s.style || 'swing') !== 'position');
  const position = setups.filter(s => s.style === 'position');

  const loadDay = () => {
    setDayLoading(true); setDayErr(null); setDayTried(true);
    fetchDayTradeSetups(ticker)
      .then(r => setDayData(r.day_trade_setups?.setups || []))
      .catch((e: unknown) => setDayErr(e instanceof Error ? e.message : 'No intraday data available'))
      .finally(() => setDayLoading(false));
  };
  const loadStrat = (s: NamedStrategy) => {
    setStrat(p => ({ ...p, [s.key]: { ...p[s.key], loading: true, err: undefined, tried: true } }));
    s.fetch(ticker)
      .then(d => setStrat(p => ({ ...p, [s.key]: { data: d, loading: false, tried: true } })))
      .catch((e: unknown) => setStrat(p => ({ ...p, [s.key]: { loading: false, tried: true, err: e instanceof Error ? e.message : 'No daily history available' } })));
  };
  const pick = (t: string) => {
    setTab(t);
    if (t === 'day' && !dayTried) loadDay();
    const ns = NAMED_STRATEGIES.find(s => s.key === t);
    if (ns && !strat[ns.key]?.tried) loadStrat(ns);
    (document.activeElement as HTMLElement | null)?.blur();   // close the dropdown after selecting
  };

  const activeNamed = NAMED_STRATEGIES.find(s => s.key === tab);
  const ns = activeNamed ? strat[activeNamed.key] : undefined;
  const nsData = ns?.data;
  const genCount = (k: string) => k === 'swing' ? swing.length : k === 'position' ? position.length : (dayData?.length ?? null);
  const genActive = tab === 'swing' ? swing : tab === 'position' ? position : (dayData || []);
  // named-strategy cards carry their own context (signal/metrics/backtest) as the Verify/Ask-AI dossier
  const cardDossier = (activeNamed && nsData) ? { [activeNamed.key]: (({ setups: _s, ...rest }) => rest)(nsData) } : dossier;

  return (
    <div className="space-y-3">
      {/* trade-style selector: generic styles as pills + named strategies in a dropdown */}
      <div className="flex items-center gap-1 flex-wrap">
        <div className="flex items-center gap-1 bg-base-200/40 p-1 rounded-xl">
          {STYLE_TABS.map(t => {
            const n = genCount(t.k);
            return (
              <button key={t.k} onClick={() => pick(t.k)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition ${tab === t.k ? 'bg-primary/15 text-primary shadow-sm' : 'text-base-content/50 hover:text-base-content hover:bg-base-100/40'}`}>
                {t.label}{n != null ? ` (${n})` : ''} <span className="text-[9px] font-normal text-base-content/40">{t.hint}</span>
              </button>
            );
          })}
          <div className="dropdown">
            <label tabIndex={0} className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold cursor-pointer transition ${activeNamed ? 'bg-primary/15 text-primary shadow-sm' : 'text-base-content/50 hover:text-base-content hover:bg-base-100/40'}`}>
              {activeNamed ? activeNamed.label : 'Named strategies'}
              {activeNamed && (strat[activeNamed.key]?.data?.setups?.length ?? null) != null ? ` (${strat[activeNamed.key]?.data?.setups?.length})` : ''}
              <ChevronsUpDown className="w-3 h-3 opacity-70" />
            </label>
            <ul tabIndex={0} className="dropdown-content menu z-20 mt-1 w-60 p-1 shadow-lg bg-base-200 rounded-xl border border-white/10">
              {NAMED_STRATEGIES.map(s => (
                <li key={s.key}>
                  <button onClick={() => pick(s.key)} className={`flex items-center justify-between gap-2 ${tab === s.key ? 'active' : ''}`}>
                    <span className="font-semibold text-xs">{s.label}</span>
                    <span className="text-[9px] text-base-content/40">{s.hint}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
        <button className="btn btn-ghost btn-xs gap-1 text-base-content/50 ml-auto" onClick={() => setShowDossier(s => !s)}>
          <Code2 className="w-3.5 h-3.5" /> {showDossier ? 'Hide' : 'View'} JSON
        </button>
      </div>

      {showDossier && (
        <pre className="text-[10px] bg-base-300/60 rounded-lg p-2.5 overflow-auto max-h-72 text-base-content/70 border border-white/[0.05]">
          {JSON.stringify(activeNamed && nsData ? nsData : dossier, null, 2)}
        </pre>
      )}

      {activeNamed ? (
        ns?.loading ? (
          <div className="glass-card p-6 flex items-center justify-center gap-2 text-sm text-base-content/60">
            <Loader2 className="w-4 h-4 animate-spin" /> Running the {activeNamed.label} screen on the daily history…
          </div>
        ) : ns?.err ? (
          <div className="glass-card p-4 text-sm text-base-content/60">{ns.err} — this strategy needs daily price history.</div>
        ) : nsData ? (
          <>
            <StrategyPanel p={activeNamed.toPanel(nsData)} />
            {(nsData.setups || []).length === 0 ? (
              <div className="glass-card p-4 text-center text-xs text-base-content/50">
                No actionable {activeNamed.label} trade right now — see the checklist above for the current signal state and what it's waiting for.
              </div>
            ) : (
              (nsData.setups as TradeSetup[]).map((s, i) => <SetupCard key={`${activeNamed.key}-${i}`} setup={s} ticker={ticker} spot={nsData.price} dossier={cardDossier} />)
            )}
          </>
        ) : null
      ) : (
        <>
          {tab === 'day' && dayLoading && (
            <div className="glass-card p-6 flex items-center justify-center gap-2 text-sm text-base-content/60">
              <Loader2 className="w-4 h-4 animate-spin" /> Scanning intraday 5m/15m structure, VWAP &amp; the opening range…
            </div>
          )}
          {tab === 'day' && dayErr && !dayLoading && (
            <div className="glass-card p-4 text-sm text-base-content/60">{dayErr} — intraday setups need live market-hours data.</div>
          )}
          {!(tab === 'day' && (dayLoading || dayErr)) && (
            genActive.length === 0 ? (
              <div className="glass-card p-6 text-center">
                <Crosshair className="w-6 h-6 mx-auto text-base-content/30 mb-2" />
                <p className="text-sm font-semibold text-base-content/70">
                  {tab === 'position' ? 'No long-term entry lined up' : tab === 'day' ? 'No clean intraday setup right now' : 'No high-conviction swing setup right now'}
                </p>
                <p className="text-xs text-base-content/50 mt-1">
                  {tab === 'position' ? 'No strong value zone to accumulate into with a worthwhile reward yet.'
                    : tab === 'day' ? 'Price is mid-range vs VWAP / the opening range — wait for a break or a VWAP reclaim.'
                    : "The signals don't line up into a clean trade yet — check the other styles or the Advanced levels."}
                </p>
              </div>
            ) : (
              genActive.map((s, i) => <SetupCard key={`${tab}-${i}`} setup={s} ticker={ticker} spot={data.price} dossier={dossier} />)
            )
          )}
        </>
      )}
    </div>
  );
}
