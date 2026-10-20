/**
 * MoveSteps — what a defense actually asks you to DO, spelled out against the position you already hold, plus the "when".
 *
 * The cards used to list only the NEW legs ("−1 P$75 Dec18  +1 P$65 Dec18"), so you could not tell what to close, what it
 * costs to close it, or what the trade you are already in adds up to. Here every alternative reads:
 *   close  — the legs you drop, at today's mark (buy to close a short, sell to close a long)
 *   open   — the legs you add, at today's chain mid
 *   keep   — what stays untouched
 *   net cash today  +  the ALL-IN picture: collected so far · what closing locks in · best / worst case / break-even of the
 *   WHOLE position (old trade + this move) — the same numbers as the card's Max gain / Max loss, now named and bridged.
 * `TimingBlock` is the "when": earnings print + the move the term structure prices for it, IV vs realized vol, what waiting costs.
 *
 * Shared by the classic Defend panel and the Beta one — plain classes only, nothing from either skin.
 */
import { AlertTriangle, CalendarClock, Clock, Gauge, Thermometer } from 'lucide-react';
import type { MoveStep, MoveTiming, RepairAlternative } from '../../api';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** "2026-11-20" → "Nov 20" (parses the string — no Date(), so it is timezone-proof). */
export function shortDate(iso?: string | null): string {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null;
  return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : '';
}

const usd = (v: number | null | undefined, signed = true) =>
  v == null || !isFinite(v) ? '—' : `${signed ? (v < 0 ? '−' : '+') : v < 0 ? '−' : ''}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
const px = (v: number | null | undefined) => (v == null || !isFinite(v) ? '—' : `$${v.toFixed(2)}`);

type Kind = 'close' | 'open' | 'keep';
const VERB: Record<Kind, Record<string, string>> = {
  close: { BUY: 'Buy to close', SELL: 'Sell to close' },
  open: { BUY: 'Buy to open', SELL: 'Sell to open' },
  keep: { BUY: 'Keep long', SELL: 'Keep short' },
};

/** One leg: "Buy to close 1× $80 put · Oct 23 @ $3.52". Stock legs read "Buy 100 sh @ $629.50". */
export function describeStep(s: MoveStep, kind: Kind): string {
  if (s.right === 'STK') return `${s.action === 'BUY' ? 'Buy' : 'Sell'} ${s.qty} sh @ ${px(s.price)}`;
  const word = s.right === 'P' ? 'put' : 'call';
  const exp = shortDate(s.expiry);
  return `${VERB[kind][s.action] || s.action} ${s.qty}× $${s.strike} ${word}${exp ? ` · ${exp}` : ''}${kind === 'keep' ? '' : ` @ ${px(s.price)}`}`;
}

/** A leg in position notation, from the POSITION's point of view: closing a BUY means you were short ("−1 P$80"). */
function posLeg(s: MoveStep, kind: Kind): string {
  if (s.right === 'STK') return `${s.action === 'BUY' ? '+' : '−'}${s.qty} sh`;
  const short = kind === 'close' ? s.action === 'BUY' : s.action === 'SELL';
  return `${short ? '−' : '+'}${s.qty} ${s.right}$${s.strike}`;
}

function group(steps: MoveStep[], kind: Kind): string {
  if (!steps.length) return '';
  const exp = shortDate(steps[0].expiry);
  return `${steps.map(s => posLeg(s, kind)).join(' ')}${exp ? ` ${exp}` : ''}`;
}

/** The compact one-liner for a ranked row: "Close −1 P$80 +1 P$70 Oct 23 → Open −1 P$75 +1 P$65 Nov 20". */
export function stepsLine(a: RepairAlternative): string | null {
  const st = a.steps;
  if (!st) return null;
  const parts: string[] = [];
  if (st.close.length) parts.push(`Close ${group(st.close, 'close')}`);
  if (st.open.length) parts.push(`Open ${group(st.open, 'open')}`);
  if (st.stock) parts.push(`${st.stock.action === 'BUY' ? 'Buy' : 'Sell'} ${st.stock.qty} sh`);
  if (!parts.length) return st.keep.length ? 'No trades — keep every leg' : null;
  return parts.join('  →  ');
}

function StepRow({ s, kind }: { s: MoveStep; kind: Kind }) {
  const cash = s.cash;
  return (
    <div className="flex items-baseline gap-2 text-xs leading-snug">
      <span className="min-w-0 flex-1 truncate font-mono text-base-content/80" title={describeStep(s, kind)}>{describeStep(s, kind)}</span>
      {cash != null && kind !== 'keep' && <span className={`tabular-nums shrink-0 ${cash >= 0 ? 'text-success' : 'text-error'}`}>{usd(cash)}</span>}
    </div>
  );
}

/** What to close / open / keep, the net cash today, and the all-in picture including the position you already hold. */
export function MoveStepsBlock({ a, spot }: { a: RepairAlternative; spot?: number }) {
  const st = a.steps, ai = a.all_in;
  if (!st || !ai) return null;
  const nothing = !st.close.length && !st.open.length && !st.stock;
  const noNewMoney = st.net_cash >= -1;
  return (
    <div className="rounded-lg border border-white/[0.08] bg-base-100/30 p-2.5 space-y-2.5" data-testid="move-steps">
      {!nothing && (
        <div className="space-y-2">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-base-content/50">What to do</div>
          {st.close.length > 0 && (
            <div className="space-y-0.5"><div className="text-[11px] text-base-content/50">1 · Close</div>{st.close.map((s, i) => <StepRow key={`c${i}`} s={s} kind="close" />)}</div>
          )}
          {(st.open.length > 0 || st.stock) && (
            <div className="space-y-0.5">
              <div className="text-[11px] text-base-content/50">{st.close.length ? '2 · Open' : '1 · Open'}</div>
              {st.open.map((s, i) => <StepRow key={`o${i}`} s={s} kind="open" />)}
              {st.stock && <StepRow s={st.stock} kind="open" />}
            </div>
          )}
          {st.notes.map((n, i) => <p key={i} className="text-xs text-base-content/65 leading-snug">{n}</p>)}
          <div className="flex items-baseline justify-between border-t border-white/[0.06] pt-1.5 text-xs">
            <span className="font-semibold">Net cash today</span>
            <span className={`tabular-nums font-semibold ${st.net_cash >= 0 ? 'text-success' : 'text-error'}`}>
              {usd(st.net_cash)} <span className="font-normal text-base-content/45">{noNewMoney ? (st.net_cash > 0 ? '· a credit — no new money' : '· even cost') : '· new money'}</span>
            </span>
          </div>
        </div>
      )}
      {st.keep.length > 0 && (
        <div className="space-y-0.5"><div className="text-[11px] text-base-content/50">{nothing ? 'Nothing to trade — you keep' : 'Stays as it is'}</div>{st.keep.map((s, i) => <StepRow key={`k${i}`} s={s} kind="keep" />)}</div>
      )}

      <div className="space-y-1 border-t border-white/[0.06] pt-2">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-base-content/50" title="The position you already hold PLUS this move — so the numbers include what you have already collected and what closing locks in">
          All-in — the trade you hold + this move
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-1.5 text-xs">
          <div><div className="text-[11px] text-base-content/45">Collected at open</div><div className="tabular-nums font-semibold">{usd(ai.collected_at_open)}</div></div>
          <div><div className="text-[11px] text-base-content/45" title="The P&L that closing part or all of the position crystallizes today (vs what you collected)">Locked in by closing</div>
            <div className={`tabular-nums font-semibold ${ai.locked_in < 0 ? 'text-error' : ''}`}>{st.close.length ? usd(ai.locked_in) : '—'}</div></div>
          <div><div className="text-[11px] text-base-content/45" title="Best case for the whole position — e.g. the new spread expires worthless">Best case</div><div className="tabular-nums font-semibold text-success">{usd(ai.best_case)}</div></div>
          <div><div className="text-[11px] text-base-content/45" title="Worst case for the whole position — the loss if the stock goes through everything">Worst case</div><div className="tabular-nums font-semibold text-error">{ai.worst_case == null ? 'undefined' : usd(ai.worst_case)}</div></div>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-base-content/60">
          {ai.at_spot != null && <span>If the stock is unchanged at expiry <b className={ai.at_spot >= 0 ? 'text-success' : 'text-error'}>{usd(ai.at_spot)}</b>{spot ? <span className="text-base-content/35"> (${spot})</span> : null}</span>}
          {ai.breakevens.length > 0 && <span>Break-even <b className="text-base-content/80">{ai.breakevens.map(b => `$${b}`).join(' · ')}</b></span>}
        </div>
      </div>
    </div>
  );
}

const TIMING_TONE: Record<MoveTiming['verdict'], { box: string; text: string }> = {
  now:          { box: 'border-error/30 bg-error/[0.06]', text: 'text-error' },
  before_print: { box: 'border-warning/30 bg-warning/[0.06]', text: 'text-warning' },
  vol_rich:     { box: 'border-success/30 bg-success/[0.06]', text: 'text-success' },
  vol_cheap:    { box: 'border-warning/30 bg-warning/[0.06]', text: 'text-warning' },
  neutral:      { box: 'border-white/[0.1] bg-base-content/[0.04]', text: 'text-base-content/70' },
  hold:         { box: 'border-white/[0.1] bg-base-content/[0.04]', text: 'text-base-content/70' },
};

/** WHEN to act: the print, the move the market prices for it, IV vs realized, and what waiting costs. */
export function TimingBlock({ t }: { t?: MoveTiming | null }) {
  if (!t || !t.title) return null;
  const tone = TIMING_TONE[t.verdict] || TIMING_TONE.neutral;
  const v = t.vol;
  const Icon = t.verdict === 'now' ? AlertTriangle : t.verdict === 'before_print' ? CalendarClock : Clock;
  return (
    <div className={`rounded-lg border p-2.5 space-y-1.5 ${tone.box}`} data-testid="move-timing">
      <div className={`flex items-center gap-1.5 text-xs font-semibold ${tone.text}`}><Icon className="w-3.5 h-3.5 shrink-0" /><span>When to act — {t.title}</span></div>
      {v && (v.iv_pct != null || v.implied_move_pct != null) && (
        <div className="flex flex-wrap gap-1.5 text-[11px] text-base-content/60">
          {v.iv_pct != null && v.hv_pct != null && <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.1] px-2 py-0.5"><Thermometer className="w-3 h-3" />IV {v.iv_pct}% vs HV {v.hv_pct}%{v.vrp_pct != null ? ` (${v.vrp_pct > 0 ? '+' : ''}${v.vrp_pct}%)` : ''}</span>}
          {v.implied_move_pct != null && <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.1] px-2 py-0.5"><Gauge className="w-3 h-3" />earnings move priced ≈ ±{v.implied_move_pct}%</span>}
          {v.earnings_days != null && <span className="inline-flex items-center gap-1 rounded-full border border-white/[0.1] px-2 py-0.5"><CalendarClock className="w-3 h-3" />earnings in {v.earnings_days}d</span>}
        </div>
      )}
      <ul className="space-y-1">
        {t.points.map((p, i) => <li key={i} className="text-xs leading-snug text-base-content/80 flex gap-1.5"><span className="text-base-content/30 shrink-0">•</span><span>{p}</span></li>)}
      </ul>
    </div>
  );
}
