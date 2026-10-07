/**
 * BetaUnderlyingAnalysis — Beta layout for Manage ▸ Underlying Analysis (the hold / exit desk).
 *
 * Same data and endpoints as the classic TradeManagerPanel (POST /trade-manager, /trade-manager/ai); a different
 * reading order and look:
 *
 *   Technical Analysis  → the underlying's chart read, collapsed, on top
 *   Decision hero       → score ring · signal · confidence · what you've done since entry
 *   What to do          → the steps, in words
 *   How the score is built → a zone scale (strong exit … strong hold) + the four lenses, tap one to open its evidence
 *   Exit plan           → a PRICE LADDER (stops below spot, targets above) instead of four text groups
 *   Watch next          → levels above / below with what a close through each means
 *   Evidence · AI assist→ the classic evidence views and the explicit-click LLM, behind accordions
 *
 * Nothing the classic panel shows is dropped — it is re-ordered, enlarged to a readable size, and folded.
 */
import React, { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Loader2, AlertTriangle, RefreshCw, Target, Eye, Layers, Sparkles, FileJson, Copy, ShieldAlert, Flag } from 'lucide-react';
import { fetchTradeManager, runTradeManagerAI } from '../api';
import type { LivePnlResponse, SavedStrategyItem, TradeManagerResult, TradeManagerAI, ManagerExitItem, ManagerSignal, ManagerWatchLevel, ManagerSinceEntry } from '../api';
import { QuantView, TechnicalView, FundamentalView, EventView, AIResult, gloss, type LensKey } from '../components/trades/TradeManagerPanel';
import { LazyTechnicals } from '../components/DerivativeIncome';
import { Accordion, Callout, Chip, Meter, ScoreRing, SubHead, Tile, TONE, type Tone } from './BetaKit';

// ── formatting ───────────────────────────────────────────────────────────────
const n = (v: any, d = 2) => (v == null || Number.isNaN(Number(v)) ? '–' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const signedUsd = (v: any) => (v == null || Number.isNaN(Number(v)) ? '–' : `${Number(v) < 0 ? '−' : '+'}$${Math.abs(Number(v)).toLocaleString(undefined, { maximumFractionDigits: Math.abs(Number(v)) < 10 ? 2 : 0 })}`);
const usd = (v: any) => (v == null || Number.isNaN(Number(v)) ? '–' : `${Number(v) < 0 ? '−' : ''}$${Math.abs(Number(v)).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
const show = (v: any): string => {
  if (v == null) return '';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) return v.map(show).filter(Boolean).join(', ');
  if (typeof v === 'object') { if (v.direction != null) return [v.direction, v.strength].filter(x => x != null && x !== '').join(' · '); if (v.label != null) return String(v.label); try { return JSON.stringify(v); } catch { return ''; } }
  return String(v);
};

// ── vocabulary ───────────────────────────────────────────────────────────────
const SIGNAL: Record<ManagerSignal, { label: string; tone: Tone }> = {
  STRONG_HOLD: { label: 'Strong hold', tone: 'good' }, HOLD: { label: 'Hold', tone: 'good' },
  EXIT: { label: 'Exit', tone: 'warn' }, STRONG_EXIT: { label: 'Strong exit', tone: 'bad' },
};
const ACTION: Record<string, { label: string; tone: Tone }> = {
  EXIT: { label: 'Exit', tone: 'bad' }, DEFEND_OR_EXIT: { label: 'Defend / exit', tone: 'bad' }, TRIM: { label: 'Trim', tone: 'warn' },
  TIGHTEN: { label: 'Tighten', tone: 'warn' }, DERISK: { label: 'De-risk', tone: 'warn' }, TAKE_PROFIT: { label: 'Take profit', tone: 'good' },
  REVIEW: { label: 'Review', tone: 'info' }, WATCH: { label: 'Watch', tone: 'neutral' },
};
const STEP: Record<string, { label: string; tone: Tone }> = {
  EXIT: { label: 'Exit', tone: 'bad' }, TRIM: { label: 'Trim', tone: 'warn' }, PROFIT: { label: 'Take profit', tone: 'good' },
  REVIEW: { label: 'Review', tone: 'info' }, INFO: { label: 'Note', tone: 'neutral' }, HOLD: { label: 'Hold', tone: 'good' },
};
const LEFT_BORDER: Record<Tone, string> = {
  good: 'border-l-success/60', warn: 'border-l-warning/60', bad: 'border-l-error/60', info: 'border-l-info/60', accent: 'border-l-secondary/60', neutral: 'border-l-base-content/20',
};
const wordOf = (v: number): { w: string; tone: Tone } => (v >= 65 ? { w: 'Supportive', tone: 'good' } : v >= 45 ? { w: 'Neutral', tone: 'neutral' } : v >= 30 ? { w: 'Caution', tone: 'warn' } : { w: 'Against', tone: 'bad' });

/** "Dealer put support 1,045 · Gamma flip 1,025" — the reads that agree at a level, each with a plain-English tooltip. */
function SourceChips({ src, labels }: { src?: { label: string; price: number }[]; labels?: string[] }) {
  const rows = src && src.length ? src : (labels || []).map(l => ({ label: l, price: NaN }));
  if (!rows.length) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {rows.map((r, i) => (
        <span key={i} title={gloss(r.label) || r.label} className="inline-flex items-baseline gap-1 rounded-md bg-base-300/30 px-1.5 py-0.5 text-[11px] text-base-content/65 whitespace-nowrap cursor-help">
          {r.label}{Number.isFinite(r.price) && <b className="font-mono text-base-content/85">{n(r.price)}</b>}
        </span>
      ))}
    </div>
  );
}

// ── hero ─────────────────────────────────────────────────────────────────────
function SinceEntry({ se, p }: { se?: ManagerSinceEntry; p: any }) {
  const pnl = p?.pnl || {};
  const items: { label: string; value: ReactNode; sub?: ReactNode; tone?: Tone }[] = [];
  if (se?.days_held != null) {
    const dateTxt = se.entry_date ? new Date(se.entry_date + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : undefined;
    items.push({ label: 'Held', value: `${se.days_held} day${se.days_held === 1 ? '' : 's'}`, sub: dateTxt ? `since ${dateTxt}` : undefined });
  }
  if (pnl.unrealized != null) items.push({ label: 'P&L', value: signedUsd(pnl.unrealized), sub: pnl.pct != null ? `${pnl.pct > 0 ? '+' : ''}${pnl.pct}%` : undefined, tone: pnl.unrealized >= 0 ? 'good' : 'bad' });
  if (se?.underlying_entry != null && se?.move_pct != null) {
    items.push({ label: 'Stock since entry', value: `${n(se.underlying_entry)} → ${n(se.underlying_now)}`,
      sub: `${se.move_pct > 0 ? '+' : ''}${se.move_pct}%${se.vs_you ? ` · ${se.vs_you}` : ''}`, tone: se.vs_you === 'with you' ? 'good' : se.vs_you === 'against you' ? 'bad' : undefined });
  }
  if (se?.high_since_pct != null && se?.low_since_pct != null) items.push({ label: 'Range since entry', value: `high ${se.high_since_pct > 0 ? '+' : ''}${se.high_since_pct}%`, sub: `low ${se.low_since_pct > 0 ? '+' : ''}${se.low_since_pct}%` });
  if ((se?.rolls || 0) > 0) items.push({ label: 'Rolled', value: `${se!.rolls}×`, sub: se!.roll_realized_pnl != null ? `banked ${signedUsd(se!.roll_realized_pnl)}` : undefined });
  if (se?.realized_banked) items.push({ label: 'Realized (partial closes)', value: signedUsd(se.realized_banked) });
  if (!items.length && !p?.notes) return null;
  return (
    <div className="mt-3 space-y-2">
      {items.length > 0 && (
        <>
          <div className="text-[11px] text-base-content/45" title="You are already in this trade — everything below is from here forward, given what you own and paid.">Since you entered</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">{items.map((i, k) => <Tile key={k} label={i.label} value={i.value} sub={i.sub} tone={i.tone} />)}</div>
        </>
      )}
      {p?.notes ? <div className="text-xs text-base-content/55 line-clamp-2"><span className="text-base-content/40">Your thesis · </span>{String(p.notes)}</div> : null}
    </div>
  );
}

function Hero({ res, loading, onRefresh }: { res: TradeManagerResult; loading: boolean; onRefresh: () => void }) {
  const d = res.decision; const sig = SIGNAL[d.signal]; const t = TONE[sig.tone]; const p = res.profile || {}; const isStock = p.kind === 'stock';
  const steps = res.exit_plan.recommendation.steps || [];
  return (
    <div className={`rounded-2xl border p-4 ${t.border} ${t.soft}`}>
      <div className="flex items-center gap-3">
        <ScoreRing value={d.score} tone={sig.tone} label="Hold score" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-lg font-semibold leading-none ${t.text}`}>{sig.label}</span>
            <Chip tone="neutral" title="How consistent the lenses are with each other">confidence {d.confidence}</Chip>
          </div>
          <div className="text-xs text-base-content/55 mt-1.5">{p.label}{p.dte != null && !isStock ? ` · ${p.dte} DTE` : ''}</div>
        </div>
        <button className="btn btn-ghost btn-xs gap-1 self-start" disabled={loading} onClick={onRefresh} title="Re-run the hold / exit read">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />} Refresh
        </button>
      </div>
      <SinceEntry se={res.since_entry} p={p} />

      <div className="mt-3 rounded-xl border border-white/[0.08] bg-base-100/40 p-3">
        <div className="text-xs font-semibold text-base-content/70 flex items-center gap-1.5 mb-2"><Target className="w-3.5 h-3.5" /> What to do from here</div>
        {steps.length > 0 ? (
          <ul className="space-y-2">
            {steps.map((s, i) => {
              const st = STEP[s.tag] || { label: s.tag, tone: 'neutral' as Tone };
              return <li key={i} className="flex items-start gap-2 text-[13px] leading-snug"><Chip tone={st.tone} className="mt-px">{st.label}</Chip><span>{s.text}</span></li>;
            })}
          </ul>
        ) : <div className="text-[13px] leading-snug">{res.exit_plan.recommendation.text}</div>}
      </div>

      {(d.overrides.length > 0 || d.conflicts.length > 0) && (
        <div className="mt-2 space-y-1.5">
          {d.overrides.map((o, i) => <Callout key={`o${i}`} tone="warn" icon={<ShieldAlert className="w-3.5 h-3.5" />}>{o}</Callout>)}
          {d.conflicts.map((o, i) => <Callout key={`c${i}`} tone="neutral" icon={<Flag className="w-3.5 h-3.5" />}>{o}</Callout>)}
        </div>
      )}
    </div>
  );
}

// ── how the score is built ───────────────────────────────────────────────────
function ScoreScale({ score }: { score: number }) {
  const zones: { w: number; label: string; tone: Tone }[] = [
    { w: 28, label: 'Strong exit', tone: 'bad' }, { w: 17, label: 'Exit', tone: 'warn' }, { w: 23, label: 'Hold', tone: 'neutral' }, { w: 32, label: 'Strong hold', tone: 'good' },
  ];
  return (
    <div>
      <div className="relative">
        <div className="flex h-2.5 rounded-full overflow-hidden">{zones.map(z => <div key={z.label} className={`${TONE[z.tone].bar} opacity-60`} style={{ width: `${z.w}%` }} />)}</div>
        <div className="absolute -top-1 h-4.5 w-0.5 bg-base-content rounded" style={{ left: `calc(${Math.max(0, Math.min(100, score))}% - 1px)`, height: 18 }} title={`Score ${Math.round(score)}`} />
      </div>
      <div className="flex mt-1 text-[11px] text-base-content/45">{zones.map(z => <div key={z.label} style={{ width: `${z.w}%` }} className="truncate">{z.label}</div>)}</div>
    </div>
  );
}

function ScoreBuild({ res, onLens }: { res: TradeManagerResult; onLens: (k: LensKey) => void }) {
  const d = res.decision; const L = d.lenses; const isStock = res.profile?.kind === 'stock';
  const quantLabel = L.quant.label || (isStock ? 'Position risk' : 'Quant');
  const first = (x?: string[]) => (x && x[0]) || '';
  const pct = (w: number) => `${Math.round(w * 100)}%`;
  const evAdj = d.event_adj;
  const tiles: { k: LensKey; label: string; word: { w: string; tone: Tone }; score?: number; weight?: string; caption: string; muted?: boolean }[] = [
    { k: 'quant', label: quantLabel, word: L.quant.available === false ? { w: 'Not scored', tone: 'neutral' } : wordOf(L.quant.score), score: L.quant.available === false ? undefined : L.quant.score, weight: L.quant.available === false ? undefined : pct(L.quant.weight), caption: first(L.quant.notes), muted: L.quant.available === false },
    { k: 'technical', label: 'Technical', word: wordOf(L.technical.score), score: L.technical.score, weight: pct(L.technical.weight), caption: first(L.technical.notes) || 'context only' },
    { k: 'fundamental', label: 'Fundamental', word: L.fundamental.available === false ? { w: 'Not scored', tone: 'neutral' } : wordOf(L.fundamental.score), score: L.fundamental.available === false ? undefined : L.fundamental.score, weight: L.fundamental.available === false ? undefined : pct(L.fundamental.weight), caption: L.fundamental.available === false ? 'fund / no company data' : first(L.fundamental.notes), muted: L.fundamental.available === false },
    { k: 'event', label: 'Event risk', word: L.event.clear ? { w: 'Clear', tone: 'good' } : { w: `${evAdj > 0 ? '+' : ''}${evAdj} pts`, tone: evAdj < -6 ? 'bad' : evAdj < 0 ? 'warn' : 'good' }, caption: first(L.event.notes) || 'nothing scheduled that changes the trade' },
  ];
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-200/25 p-3 space-y-3">
      <div className="text-xs font-semibold text-base-content/70">How the score is built <span className="font-normal text-base-content/40">· tap a lens for its evidence</span></div>
      <ScoreScale score={d.score} />
      <div className="grid grid-cols-2 gap-2">
        {tiles.map(t => (
          <button key={t.k} onClick={() => onLens(t.k)} title="See what is behind this"
            className={`text-left rounded-lg border border-white/[0.07] bg-base-100/30 px-2.5 py-2 hover:bg-base-100/60 transition-colors ${t.muted ? 'opacity-60' : ''}`}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs text-base-content/60 truncate">{t.label}{t.weight && <span className="text-base-content/35"> · {t.weight}</span>}</span>
              {t.score != null && <span className="font-mono text-xs text-base-content/55">{Math.round(t.score)}</span>}
            </div>
            <div className={`text-sm font-semibold ${TONE[t.word.tone].text}`}>{t.word.w}</div>
            {t.score != null && <Meter value={t.score} tone={t.word.tone} className="my-1.5" />}
            <div className="text-[11px] text-base-content/50 leading-snug line-clamp-2 min-h-[2.4em]">{t.caption}</div>
          </button>
        ))}
      </div>
      <div className="text-xs text-base-content/50 leading-relaxed">
        Score <b className="text-base-content/75">{Math.round(d.raw_score)}</b> = blend <b className="text-base-content/75">{Math.round(d.blend)}</b> of the scored lenses (a lens without data is left out)
        {evAdj !== 0 ? <> {evAdj < 0 ? '−' : '+'} event risk <b className="text-base-content/75">{Math.abs(evAdj)}</b></> : ' — no event risk to add or subtract'}
        {d.raw_score !== d.score ? <> · capped to <b className="text-warning">{Math.round(d.score)}</b> by a discipline rule above</> : ''}. 50 is neutral.
      </div>
    </div>
  );
}

// ── exit plan: a price ladder ────────────────────────────────────────────────
function LadderRow({ it }: { it: ManagerExitItem }) {
  const [open, setOpen] = useState(false);
  const a = ACTION[it.action] || { label: it.action, tone: 'neutral' as Tone };
  const left = it.level != null ? n(it.level) : it.pnl_level != null ? signedUsd(it.pnl_level) : it.in_days != null ? `in ${it.in_days}d` : '—';
  const dist = it.level != null && it.distance_pct != null && it.kind !== 'breakeven'
    ? `${it.distance_pct > 0 ? '+' : ''}${it.distance_pct}%${it.distance_atr != null ? ` · ${it.distance_atr} ATR` : ''}`
    : it.level != null && it.kind === 'breakeven' && it.distance_pct != null ? `${it.distance_pct > 0 ? '+' : ''}${it.distance_pct}% from price` : '';
  const more = it.detail || it.evidence || it.hard_stop != null || it.kind === 'stop' || (it.source_levels || []).some(s => gloss(s.label));
  const statusTone: Record<string, string> = { good: 'text-success', warn: 'text-warning', bad: 'text-error' };
  return (
    <div className={`grid grid-cols-[88px_1fr] gap-x-3 py-3 pl-3 border-l-2 ${LEFT_BORDER[a.tone]} border-b border-b-white/[0.05] last:border-b-0`}>
      <div>
        <div className="font-mono text-sm font-semibold whitespace-nowrap">{left}</div>
        {dist && <div className="text-[11px] text-base-content/45 leading-tight mt-0.5">{dist}</div>}
      </div>
      <div className="min-w-0">
        <div className="flex items-center flex-wrap gap-x-2 gap-y-1">
          <span className="text-[13px] font-semibold">{it.title || it.kind}</span>
          <Chip tone={a.tone}>{a.label}{it.fraction ? ` ${it.fraction}` : ''}</Chip>
        </div>
        {it.status_text && <div className={`text-xs mt-0.5 ${statusTone[it.status_tone || ''] || 'text-base-content/55'}`}>{it.status_text}</div>}
        <div className="text-xs text-base-content/65 leading-snug mt-1">{it.why}</div>
        <SourceChips src={it.source_levels} />
        {more && <button className="text-xs text-secondary/80 hover:text-secondary mt-1.5" onClick={() => setOpen(v => !v)}>{open ? 'Hide evidence' : 'Why this level'}</button>}
        {open && (
          <div className="mt-1.5 text-xs text-base-content/55 leading-snug space-y-1 border-l border-white/[0.1] pl-2.5">
            {it.detail && <div>{it.detail}</div>}
            {it.hard_stop != null && !it.detail && <div>Intraday hard stop {n(it.hard_stop)}.</div>}
            {it.evidence && <div className="text-secondary/70">{it.evidence}</div>}
            {(it.source_levels || []).map((s, i) => gloss(s.label) ? <div key={i}><b className="text-base-content/70">{s.label}:</b> {gloss(s.label)}</div> : null)}
          </div>
        )}
      </div>
    </div>
  );
}

function ExitPlan({ res, spot }: { res: TradeManagerResult; spot: number | null }) {
  const plan = res.exit_plan; const vol = plan.vol; const isStock = res.profile?.kind === 'stock';
  const priced = plan.items.filter(i => i.level != null).sort((a, b) => (b.level as number) - (a.level as number));
  const rules = plan.items.filter(i => i.level == null);
  const above = spot != null ? priced.filter(i => (i.level as number) >= spot) : priced;
  const below = spot != null ? priced.filter(i => (i.level as number) < spot) : [];
  const tiles: { label: string; value: ReactNode; tone?: Tone; title?: string }[] = [];
  if (plan.atr) tiles.push({ label: 'Typical daily move', value: `$${n(plan.atr)}${plan.atr_pct != null ? ` · ${plan.atr_pct}%` : ''}`, title: 'Average True Range (14 days): how far this stock typically moves in a day, including gaps.' });
  if (vol?.sigma_dte_pct != null) tiles.push({ label: `Normal swing · ${vol.horizon_short || 'expiry'}`, value: `±${vol.sigma_dte_pct}%`, title: `σ ${vol.sigma_ann_pct}% annualised` });
  if (plan.risk_reward) tiles.push({ label: 'Reward : risk', value: `${plan.risk_reward.ratio} : 1`, tone: plan.risk_reward.ratio >= 1.5 ? 'good' : 'warn', title: plan.risk_reward.note });
  (vol?.strikes || []).forEach(k => tiles.push({ label: `Short ${k.side} ${k.strike}`, value: k.breached ? 'breached' : `${Math.round((k.p_touch ?? 0) * 100)}% to touch`, tone: k.breached ? 'bad' : (k.p_touch ?? 0) >= 0.35 ? 'warn' : undefined }));
  return (
    <div className="space-y-3">
      {tiles.length > 0 && <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">{tiles.map((t, i) => <Tile key={i} label={t.label} value={t.value} tone={t.tone} title={t.title} />)}</div>}
      {priced.length > 0 && (
        <div>
          <SubHead hint="stops sit below the price, targets above">Price ladder</SubHead>
          <div className="rounded-xl border border-white/[0.07] bg-base-100/20 overflow-hidden">
            {above.map((it, i) => <LadderRow key={`a${i}`} it={it} />)}
            {spot != null && (
              <div className="flex items-center gap-2 px-3 py-1.5 bg-primary/[0.08]">
                <div className="h-px flex-1 bg-primary/40" /><span className="text-xs font-semibold text-primary tabular-nums">Now ${n(spot)}</span><div className="h-px flex-1 bg-primary/40" />
              </div>
            )}
            {below.map((it, i) => <LadderRow key={`b${i}`} it={it} />)}
          </div>
        </div>
      )}
      {rules.length > 0 && (
        <div>
          <SubHead hint="triggers that are about time, events or P&L rather than a price">Rules &amp; timing</SubHead>
          <div className="rounded-xl border border-white/[0.07] bg-base-100/20 overflow-hidden">{rules.map((it, i) => <LadderRow key={i} it={it} />)}</div>
        </div>
      )}
      {plan.hold_odds && !isStock && (
        <details className="rounded-xl border border-white/[0.07] bg-base-100/20 px-3 py-2.5 text-xs text-base-content/60">
          <summary className="cursor-pointer select-none font-semibold text-base-content/75">How often did closing beat holding in similar spots? (backtest)</summary>
          <div className="mt-2 space-y-1.5">
            {plan.hold_odds.rows.map((r, i) => (
              <div key={i} className="grid grid-cols-[130px_1fr] gap-2">
                <span className="text-base-content/75">{r.state}</span>
                <span>closing was better <b className={r.p_close_better >= 25 ? 'text-warning' : ''}>{r.p_close_better}%</b> of the time · holding averaged <b className={r.mean_delta >= 0 ? 'text-success/80' : 'text-error/80'}>{r.mean_delta >= 0 ? '+' : ''}{r.mean_delta}%</b> of collateral · worst-5% hold {r.worst5}% <span className="text-base-content/35">(n={r.n.toLocaleString()})</span></span>
              </div>
            ))}
            <div className="text-[11px] text-base-content/40 pt-1">All open positions: closing better {plan.hold_odds.baseline.p_close_better}% · hold avg +{plan.hold_odds.baseline.mean_delta}%. Synthetic model, 2016-26.</div>
          </div>
        </details>
      )}
    </div>
  );
}

// ── watch next ───────────────────────────────────────────────────────────────
function WatchLevel({ r, atr }: { r: ManagerWatchLevel; atr?: number | null }) {
  const effect: { label: string; tone: Tone } = r.effect_if_break === 'good' ? { label: 'Good for you', tone: 'good' } : r.effect_if_break === 'bad' ? { label: 'Bad for you', tone: 'bad' } : { label: r.effect_note || 'No clear effect', tone: 'neutral' };
  const above = (r.distance_usd ?? 0) >= 0;
  return (
    <div className={`py-3 pl-3 border-l-2 ${LEFT_BORDER[effect.tone]} border-b border-b-white/[0.05] last:border-b-0`}>
      <div className="flex items-baseline flex-wrap gap-x-2.5 gap-y-1">
        <span className="font-mono text-sm font-semibold">{n(r.level)}</span>
        <span className="text-xs text-base-content/45">{signedUsd(r.distance_usd)}{r.distance_pct != null ? ` · ${r.distance_pct > 0 ? '+' : ''}${r.distance_pct}%` : ''} · {above ? 'above' : 'below'} price</span>
        <Chip tone={effect.tone}>{effect.label}</Chip>
      </div>
      <SourceChips src={r.source_levels} labels={r.source_levels?.length ? undefined : (r.what ? r.what.split(', ') : [])} />
      <div className="text-xs leading-snug mt-1.5"><b className="text-base-content/50 font-medium">If it breaks:</b> <span className="text-base-content/75">{r.if_break}</span></div>
      <div className="text-xs leading-snug text-base-content/55"><b className="text-base-content/40 font-medium">If it holds:</b> {r.if_reject || r.if_hold}</div>
      {r.noise && <div className="text-[11px] text-base-content/40 mt-1">Inside one normal day's move{atr ? ` (ATR $${n(atr)})` : ''} — treat it as noise unless it closes through on heavy volume.</div>}
    </div>
  );
}

function WatchNext({ res }: { res: TradeManagerResult }) {
  const m = res.monitor; const atr = res.exit_plan.atr;
  const box = 'rounded-xl border border-white/[0.07] bg-base-100/20 overflow-hidden';
  return (
    <div className="space-y-3">
      <div className="text-xs text-base-content/50">“Breaks” means a daily <b>close</b> through the level (ideally on volume ≥ 1.5× average) — not an intraday wick. Hover a chip for what it means.</div>
      <div>
        <SubHead>Above price</SubHead>
        <div className={box}>{m.up.length ? m.up.map((r, i) => <WatchLevel key={i} r={r} atr={atr} />) : <div className="text-xs text-base-content/40 p-3">No strong level overhead — watch the 20-day high.</div>}</div>
      </div>
      <div>
        <SubHead>Below price</SubHead>
        <div className={box}>{m.down.length ? m.down.map((r, i) => <WatchLevel key={i} r={r} atr={atr} />) : <div className="text-xs text-base-content/40 p-3">No strong level below — watch the 20-day low.</div>}</div>
      </div>
      {m.indicators.length > 0 && (
        <div>
          <SubHead>Indicators to watch</SubHead>
          <div className={box}>
            {m.indicators.map((x, i) => (
              <div key={i} className="grid grid-cols-[130px_1fr] gap-x-3 px-3 py-2.5 border-b border-white/[0.05] last:border-0">
                <div><div className="text-xs font-semibold">{x.metric}</div><div className="font-mono text-xs text-base-content/50">{show(x.now)}</div></div>
                <ul className="text-xs text-base-content/65 leading-snug space-y-0.5">{x.watch.slice(0, 2).map((w, j) => <li key={j}>• {w}</li>)}</ul>
              </div>
            ))}
          </div>
        </div>
      )}
      {m.fundamental_events.length > 0 && (
        <div>
          <SubHead>Fundamentals · events · macro</SubHead>
          <div className={box}>
            {m.fundamental_events.map((x, i) => (
              <div key={i} className="grid grid-cols-[130px_1fr] gap-x-3 px-3 py-2.5 border-b border-white/[0.05] last:border-0">
                <div><div className="text-xs font-semibold">{x.item}</div><div className="font-mono text-[11px] text-base-content/45">{x.when}</div></div>
                <div className="text-xs text-base-content/65 leading-snug">{x.watch}{x.url && <> · <a className="link" href={x.url} target="_blank" rel="noreferrer">open</a></>}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── panel ────────────────────────────────────────────────────────────────────
export default function BetaUnderlyingAnalysis({ trade, pnl }: { trade: SavedStrategyItem; pnl: LivePnlResponse }) {
  const [res, setRes] = useState<TradeManagerResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lens, setLens] = useState<LensKey>('quant');
  const [evidenceSignal, setEvidenceSignal] = useState(0);
  const [ai, setAi] = useState<TradeManagerAI | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiErr, setAiErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const startedFor = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try { setRes(await fetchTradeManager(trade.id, pnl)); }
    catch (e: any) { setErr(e?.message || 'Trade manager failed'); }
    finally { setLoading(false); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade.id]);
  useEffect(() => {
    if (startedFor.current === trade.id) return;     // StrictMode double-invoke must not fire the heavy read twice
    startedFor.current = trade.id;
    setRes(null); setAi(null);
    load();
  }, [load, trade.id]);

  const askAI = async () => {
    setAiLoading(true); setAiErr(null);
    try { setAi(await runTradeManagerAI(trade.id, pnl)); }
    catch (e: any) { setAiErr(e?.message || 'AI assist failed'); }
    finally { setAiLoading(false); }
  };
  const copyJson = async () => {
    if (!res) return;
    try { await navigator.clipboard.writeText(JSON.stringify(res.evidence_json, null, 2)); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard blocked */ }
  };
  const openLens = (k: LensKey) => { setLens(k); setEvidenceSignal(s => s + 1); };

  const spot = Number((pnl as any)?.underlying_price) > 0 ? Number((pnl as any).underlying_price) : null;
  const isStock = res?.profile?.kind === 'stock';
  const L = res?.decision.lenses;
  const quantLabel = L?.quant.label || (isStock ? 'Position risk' : 'Quant');
  const planItems = res?.exit_plan.items || [];
  const nExit = planItems.filter(i => i.action === 'EXIT' || i.action === 'DEFEND_OR_EXIT').length;
  const nProfit = planItems.filter(i => i.action === 'TAKE_PROFIT').length;
  const lensBody = (k: LensKey) => !res || !L ? null
    : k === 'quant' ? <QuantView lens={L.quant} isStock={!!isStock} />
    : k === 'technical' ? <TechnicalView res={res} lens={L.technical} isStock={!!isStock} />
    : k === 'fundamental' ? <FundamentalView res={res} lens={L.fundamental} /> : <EventView res={res} lens={L.event} />;

  return (
    <div className="space-y-3">
      {/* the underlying's chart read — on top, collapsed */}
      <LazyTechnicals ticker={trade.ticker} />

      {loading && !res && (
        <div className="rounded-xl border border-white/[0.07] bg-base-200/25 p-4 space-y-2" aria-live="polite">
          <div className="flex items-center gap-2 text-sm text-base-content/70"><Loader2 className="w-4 h-4 animate-spin text-primary" /> Reading structure, volatility, fundamentals and events for {trade.ticker}…</div>
          <div className="text-xs text-base-content/40">Up to ~30s on a cold read, instant when cached.</div>
          <div className="h-24 rounded-lg bg-base-content/[0.05] animate-pulse" /><div className="h-16 rounded-lg bg-base-content/[0.05] animate-pulse" />
        </div>
      )}
      {err && !res && <Callout tone="bad" icon={<AlertTriangle className="w-4 h-4" />}><span>{err}</span> <button className="underline ml-1" onClick={load}>Retry</button></Callout>}

      {res && (
        <>
          <Hero res={res} loading={loading} onRefresh={load} />
          <ScoreBuild res={res} onLens={openLens} />
          <Accordion title="Exit plan" icon={<Target className="w-4 h-4" />} defaultOpen
            summary={`${planItems.length} levels & rules${nExit ? ` · ${nExit} defend/exit` : ''}${nProfit ? ` · ${nProfit} take-profit` : ''}`}>
            <ExitPlan res={res} spot={spot} />
          </Accordion>
          <Accordion title="What to watch next" icon={<Eye className="w-4 h-4" />}
            summary={`${res.monitor.up.length} above · ${res.monitor.down.length} below · ${res.monitor.indicators.length} indicators`}>
            <WatchNext res={res} />
          </Accordion>
          <Accordion title="Evidence" icon={<Layers className="w-4 h-4" />} summary={`${quantLabel} · Technical · Fundamental · Event & news`} openSignal={evidenceSignal}>
            <div className="space-y-3">
              <div className="flex gap-1.5 flex-wrap">
                {([['quant', quantLabel], ['technical', 'Technical'], ['fundamental', 'Fundamental'], ['event', 'Event & news']] as [LensKey, string][]).map(([k, lab]) => (
                  <button key={k} onClick={() => setLens(k)}
                    className={`px-3 py-1 rounded-full text-xs border transition-colors ${lens === k ? 'border-primary/50 bg-primary/15 text-primary font-semibold' : 'border-white/10 text-base-content/60 hover:border-white/25'}`}>{lab}</button>
                ))}
              </div>
              <div className="beta-readable">{lensBody(lens)}</div>
            </div>
          </Accordion>
          <Accordion title="AI assist" icon={<Sparkles className="w-4 h-4" />} summary="on demand · sends facts only, none of our verdict">
            <div className="space-y-3">
              <Callout tone="accent" icon={<Sparkles className="w-3.5 h-3.5" />}>
                Sends the evidence JSON below — every input we collected, <b>none</b> of our own verdict, scores or trader stances — so the model forms an independent view from the facts and lists what's missing. Runs only when you press the button.
              </Callout>
              <button className="btn btn-sm btn-secondary gap-1.5" disabled={aiLoading} onClick={askAI}>
                {aiLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                {aiLoading ? 'Thinking…' : ai ? 'Re-run' : 'Ask AI to assist the decision'}
              </button>
              {aiErr && <Callout tone="bad" icon={<AlertTriangle className="w-3.5 h-3.5" />}>{aiErr}</Callout>}
              {ai && <div className="beta-readable rounded-xl border border-white/[0.08] bg-base-100/30 p-3"><AIResult r={ai} /></div>}
              <details className="rounded-xl border border-white/[0.07] bg-base-100/20 p-3">
                <summary className="cursor-pointer text-xs font-semibold text-base-content/70 flex items-center gap-1.5 select-none">
                  <FileJson className="w-3.5 h-3.5" /> Evidence JSON (facts only · {Math.round(JSON.stringify(res.evidence_json).length / 1000)}k chars)
                  <button className="btn btn-ghost btn-xs ml-auto gap-1" onClick={e => { e.preventDefault(); copyJson(); }}><Copy className="w-3 h-3" />{copied ? 'Copied' : 'Copy'}</button>
                </summary>
                <pre className="mt-2 text-[11px] leading-snug max-h-96 overflow-auto text-base-content/60">{JSON.stringify(res.evidence_json, null, 2)}</pre>
              </details>
            </div>
          </Accordion>
          <div className="text-[11px] text-base-content/35">As of {res.as_of}{res.cached_evidence ? ' · market evidence cached ≤10 min, position read is live' : ''} · deterministic desk read, educational — not investment advice.</div>
        </>
      )}
    </div>
  );
}
