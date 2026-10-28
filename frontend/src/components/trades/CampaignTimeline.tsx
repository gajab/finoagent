/**
 * CampaignTimeline — the option journal as a STORY of what you did, not a bag of rows.
 *
 * Every fill is grouped into the event it belonged to — "Opened iron condor", "Closed call credit spread",
 * "Rolled short put $80 → short put $75" — and every leg says exactly what it was: SOLD TO OPEN · BOUGHT TO
 * CLOSE …, on which instrument, at what price (a close shows opened → closed), its signed cash (×100) and what it
 * realized. Each event ends with the running net cash, so reading top to bottom builds up to the all-in number.
 *
 * Built server-side from the same legs + closed legs the live P&L reconciles to (`/campaign-ledger`). Used by
 * the classic card, the classic History panel and the Beta Journal tab — one journal, three places.
 */
import { useState } from 'react';
import { Loader2, AlertCircle, PlusCircle, MinusCircle, RotateCcw } from 'lucide-react';
import type { CampaignLedgerResponse, CampaignEvent, CampaignLedgerRow } from '../../api';
import { fmtMoney, fmtDate } from '../../lib/tradeFormat';
import { closeOutAdvice } from '../../lib/tradePnl';
import { fillLabel, instrumentLabel, EVENT_KIND_LABEL, campaignHeadline } from '../../lib/ledger';

const tone = (v: number) => (v > 0.004 ? 'text-success' : v < -0.004 ? 'text-error' : 'text-base-content/50');
const px = (v: number) => `$${v.toFixed(2)}`;

const KIND = {
  open:  { Icon: PlusCircle,  edge: 'border-l-success/60', chip: 'bg-success/15 text-success' },
  close: { Icon: MinusCircle, edge: 'border-l-warning/60', chip: 'bg-warning/15 text-warning' },
  roll:  { Icon: RotateCcw,   edge: 'border-l-primary/60', chip: 'bg-primary/15 text-primary' },
} as const;

function Tile({ label, value, color = '', hint }: { label: string; value: string; color?: string; hint?: string }) {
  return (
    <div className={`flex flex-col px-3 py-1.5 rounded-lg bg-base-300/30 min-w-[112px] ${hint ? 'cursor-help' : ''}`} title={hint}>
      <span className="text-[9px] uppercase tracking-wider text-base-content/40">{label}</span>
      <span className={`text-sm font-semibold font-mono tabular-nums mt-0.5 ${color}`}>{value}</span>
    </div>
  );
}

/** One leg of one event: WHAT it was, on which instrument, at what price, the cash, and what it realized. */
function Fill({ r }: { r: CampaignLedgerRow }) {
  const closing = r.kind === 'close';
  return (
    <div className="grid grid-cols-[auto_1fr_auto] sm:grid-cols-[8.5rem_1fr_6.5rem_7rem] items-baseline gap-x-3 gap-y-0.5 py-1.5" data-testid="fill">
      <span className={`text-[10px] font-bold uppercase tracking-wide whitespace-nowrap ${r.cash >= 0 ? 'text-success' : 'text-error'}`} data-testid="fill-label">
        {fillLabel(r)}
      </span>
      <span className="min-w-0 text-[12px] text-base-content/85">
        <b className="font-semibold">{instrumentLabel(r)}</b>
        {r.expiration ? <span className="text-base-content/45"> · exp {fmtDate(r.expiration)}</span> : null}
        {r.qty !== 1 ? <span className="text-base-content/45"> · ×{r.qty}</span> : null}
        <span className="text-base-content/50">
          {' · '}{closing && r.entry_price != null ? `opened ${px(r.entry_price)} → closed ${px(r.price)}` : `@ ${px(r.price)}`}
        </span>
        {closing && r.roll && <span className="ml-1.5 badge badge-xs badge-warning badge-outline">rolled out</span>}
        {r.still_open && <span className="ml-1.5 badge badge-xs badge-success badge-outline">open now</span>}
      </span>
      <span className={`text-right font-mono tabular-nums text-[12px] font-semibold ${tone(r.cash)}`} title="Cash = price × 100 × contracts (sold +, bought −)">
        {fmtMoney(r.cash, { signed: true })}
      </span>
      <span className={`hidden sm:block text-right font-mono tabular-nums text-[11px] ${r.realized == null ? 'text-base-content/20' : tone(r.realized)}`}>
        {r.realized == null ? '' : <>P&amp;L {fmtMoney(r.realized, { signed: true })}</>}
      </span>
    </div>
  );
}

function EventCard({ ev, compact }: { ev: CampaignEvent; compact: boolean }) {
  const K = KIND[ev.kind];
  return (
    <li className={`rounded-xl border border-white/[0.06] border-l-4 ${K.edge} bg-base-200/30`} data-testid="event" data-kind={ev.kind}>
      <div className={`flex flex-wrap items-start gap-x-3 gap-y-1 ${compact ? 'px-2.5 pt-1.5' : 'px-3 pt-2'}`}>
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${K.chip}`}>
          <K.Icon className="w-3 h-3" />{EVENT_KIND_LABEL[ev.kind]}
        </span>
        <div className="min-w-0 flex-1">
          <div className={`font-semibold text-base-content/90 ${compact ? 'text-[12px]' : 'text-sm'}`} data-testid="event-title">{ev.title}</div>
          <div className="text-[11px] text-base-content/45">
            {fmtDate(ev.date)}{ev.subtitle ? ` · ${ev.subtitle.replace(/\d{4}-\d{2}-\d{2}/g, d => fmtDate(d))}` : ''} · {ev.legs} fill{ev.legs === 1 ? '' : 's'}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={`font-mono font-bold tabular-nums ${tone(ev.cash)} ${compact ? 'text-[12px]' : 'text-sm'}`}>{fmtMoney(ev.cash, { signed: true })}</div>
          <div className="text-[9px] uppercase tracking-wider text-base-content/35">net cash</div>
        </div>
      </div>
      <div className={`divide-y divide-white/[0.04] ${compact ? 'px-2.5' : 'px-3'}`}>
        {ev.rows.map((r, i) => <Fill key={i} r={r} />)}
      </div>
      <div className={`flex flex-wrap items-center gap-x-4 gap-y-0.5 border-t border-white/[0.04] text-[11px] ${compact ? 'px-2.5 py-1' : 'px-3 py-1.5'}`}>
        {ev.realized != null && (
          <span className="text-base-content/50">Realized on this {ev.kind === 'roll' ? 'buy-back' : 'close'}{' '}
            <b className={`font-mono tabular-nums ${tone(ev.realized)}`}>{fmtMoney(ev.realized, { signed: true })}</b></span>
        )}
        <span className="ml-auto text-base-content/50">Running net cash{' '}
          <b className={`font-mono tabular-nums ${tone(ev.running_cash)}`}>{fmtMoney(ev.running_cash, { signed: true })}</b></span>
      </div>
    </li>
  );
}

/** The broker-statement view: one line per fill, for reconciling against the statement. */
function Statement({ rows }: { rows: CampaignLedgerRow[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-white/[0.04]" data-testid="statement">
      <table className="table table-xs text-xs w-full">
        <thead>
          <tr className="text-[9px] uppercase tracking-wider text-base-content/30 bg-base-300/20">
            <th className="pl-3">Date</th><th>Transaction</th><th className="text-right">Price</th>
            <th className="text-right">Cash</th><th className="text-right hidden md:table-cell">Realized</th><th className="text-right">Running</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-white/[0.03] hover:bg-base-300/20">
              <td className="pl-3 whitespace-nowrap text-base-content/50 text-[10px]">{fmtDate(r.date)}</td>
              <td className="text-base-content/80">
                <span className={`mr-1.5 text-[9px] font-bold uppercase ${r.cash >= 0 ? 'text-success/80' : 'text-error/80'}`}>{fillLabel(r)}</span>
                {instrumentLabel(r)}{r.expiration ? <span className="text-base-content/40"> · {fmtDate(r.expiration)}</span> : null}
                {r.qty !== 1 ? <span className="text-base-content/40"> ×{r.qty}</span> : null}
              </td>
              <td className="text-right font-mono">{px(r.price)}</td>
              <td className={`text-right font-mono font-semibold ${tone(r.cash)}`}>{fmtMoney(r.cash, { signed: true })}</td>
              <td className={`text-right font-mono hidden md:table-cell ${r.realized == null ? 'text-base-content/25' : tone(r.realized)}`}>
                {r.realized == null ? '—' : fmtMoney(r.realized, { signed: true })}
              </td>
              <td className={`text-right font-mono ${tone(r.running_cash)}`}>{fmtMoney(r.running_cash, { signed: true })}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function CampaignTimeline({ data, loading = false, error = null, compact = false, initialView = 'timeline', initialNewestFirst = false }: {
  data: CampaignLedgerResponse | null; loading?: boolean; error?: string | null;
  initialView?: 'timeline' | 'statement'; initialNewestFirst?: boolean;
  /** Classic card: events only (the card already shows the numbers). */
  compact?: boolean;
}) {
  const [view, setView] = useState<'timeline' | 'statement'>(initialView);
  const [newestFirst, setNewestFirst] = useState(initialNewestFirst);

  if (loading && !data) {
    return <div className="flex items-center gap-2 py-3 text-xs text-base-content/40"><Loader2 className="w-3.5 h-3.5 animate-spin" />Loading the journal…</div>;
  }
  if (error) {
    return <div className="flex items-center gap-1.5 text-xs text-error py-2"><AlertCircle className="w-3.5 h-3.5 shrink-0" />{error}</div>;
  }
  if (!data || data.ledger.rows.length === 0) {
    return <p className="text-xs text-base-content/40 py-2 italic">No option fills recorded.</p>;
  }

  const { ledger, summary, trade_status } = data;
  const live = trade_status !== 'closed';
  const events = newestFirst ? [...ledger.events].reverse() : ledger.events;
  const advice = live ? closeOutAdvice(summary?.close_even) : null;
  const bes = live && summary?.effective_breakevens?.length ? summary.effective_breakevens : null;
  const perShare = advice?.perShare != null ? `${fmtMoney(advice.perShare)}/sh` : fmtMoney(advice?.total ?? 0);
  const openNow = ledger.rows.filter(r => r.still_open);

  return (
    <div className="space-y-3" data-testid="campaign-timeline">
      {!compact && (
        <>
          <div className="rounded-xl border border-white/[0.06] bg-base-200/30 px-3 py-2.5 space-y-2.5">
            <div className="flex flex-wrap gap-2">
              <Tile label="Net cash so far" value={fmtMoney(ledger.net_cash, { signed: true })} color={tone(ledger.net_cash)}
                hint="Every fill ×100 (sold +, bought −): the credit on legs still open plus everything realized on closed legs — your all-in cost basis." />
              <Tile label="Realized (closed legs)" value={fmtMoney(ledger.realized, { signed: true })} color={tone(ledger.realized)}
                hint="Banked on legs already closed — partial closes and roll buy-backs together." />
              {live && <Tile label="Open legs credit" value={fmtMoney(ledger.open_cash, { signed: true })} color={tone(ledger.open_cash)}
                hint="Premium on the legs still open, at their own fills." />}
              {bes && <Tile label="Break-even at expiry" value={bes.map(b => `$${b.toFixed(2)}`).join(' / ')}
                hint="Underlying price where the open legs expire with the WHOLE campaign at $0 — every prior fill included." />}
              {advice?.mode === 'pay' && <Tile label="Close-out price" value={`≤ ${perShare}`} color="text-success"
                hint={`Close everything that is open for a net debit of up to ${fmtMoney(advice.total)} and the whole campaign still finishes ≥ $0.`} />}
              {advice?.mode === 'collect' && <Tile label="Close-out price" value={`≥ ${fmtMoney(advice.total)} credit`} color="text-warning"
                hint="The campaign is under water: you must CLOSE for a net credit of at least this to get back to even." />}
              {advice?.mode === 'even' && <Tile label="Close-out price" value="at cost" hint="Closing at the opening price leaves the campaign at $0." />}
            </div>
            <ul className="space-y-0.5 text-[12px] text-base-content/70 leading-snug" data-testid="headline">
              {campaignHeadline(ledger, summary, live).map((t, i) => <li key={i}>{t}</li>)}
            </ul>
            {live && openNow.length > 0 && (
              <div className="text-[11px] text-base-content/50" data-testid="open-now">
                <span className="uppercase tracking-wider text-[9px] text-base-content/40 mr-1.5">Open now</span>
                {openNow.map((r, i) => (
                  <span key={i} className="mr-3">
                    <b className="text-base-content/80">{r.side === 'sell' ? 'short' : 'long'} {instrumentLabel(r)}</b>
                    {r.expiration ? ` · exp ${fmtDate(r.expiration)}` : ''}{r.qty !== 1 ? ` ×${r.qty}` : ''} · {r.side === 'sell' ? 'sold' : 'bought'} @ {px(r.price)}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-lg border border-white/10 bg-base-100/40 p-0.5 text-xs" role="tablist" aria-label="Journal view">
              {([['timeline', 'Timeline'], ['statement', 'Statement']] as const).map(([k, lbl]) => (
                <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)}
                  className={`px-2.5 py-1 rounded-md transition-colors ${view === k ? 'bg-primary/20 text-primary font-semibold' : 'text-base-content/60 hover:text-base-content'}`}>{lbl}</button>
              ))}
            </div>
            {view === 'timeline' && (
              <button className="btn btn-ghost btn-xs text-[11px] text-base-content/50" onClick={() => setNewestFirst(v => !v)}
                title="Reverse the order of the events">{newestFirst ? 'Newest first' : 'Oldest first'} ⇅</button>
            )}
            <span className="ml-auto text-[10px] text-base-content/30">{ledger.events.length} events · {ledger.rows.length} fills</span>
          </div>
        </>
      )}

      {compact || view === 'timeline'
        ? <ol className={compact ? 'space-y-1.5' : 'space-y-2'} data-testid="timeline">{events.map(ev => <EventCard key={ev.id} ev={ev} compact={compact} />)}</ol>
        : <Statement rows={ledger.rows} />}

      {!compact && (
        <p className="text-[9px] text-base-content/35 leading-snug">
          Cash = price × 100 × contracts (sold +, bought −). Net cash = the credit on what is still open + everything realized on closed legs.
          Prices are the per-share fills you entered — enter your broker&rsquo;s net price (fees included) to match the statement to the cent.
        </p>
      )}
    </div>
  );
}
