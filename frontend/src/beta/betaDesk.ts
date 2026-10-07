/**
 * betaDesk.ts — ONE desk-score run per position, shared by everything in the inspector that quotes the quant score.
 *
 * The Quant tab shows the FULL institutional desk score (`/desk-score`). The Manage ▸ Underlying Analysis read used to
 * compute its own lighter quant lens (e.g. 4/100 next to the Quant tab's 53/100), so the same trade carried two quant
 * numbers. Now the inspector runs the desk once, the Quant tab renders it, and the Manage read is handed the SAME
 * result (the backend already accepts it as `desk` and replaces the light lens with it, source `full_desk`).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { DeskScoreResult, LivePnlResponse, SavedStrategyItem } from '../api';
import { runDeskScore } from '../api';

export type DeskStatus = 'na' | 'idle' | 'loading' | 'ready' | 'error';
export type DeskFocus = { structure: string; expiration?: string | null; short_strike?: number | null };

export interface DeskState {
  status: DeskStatus;
  desk: DeskScoreResult | null;
  error: string | null;
  run: () => void;
}

/** The desk run has finished one way or the other (or there is nothing to run) — a consumer may stop waiting. */
export const deskSettled = (s: DeskStatus | undefined): boolean => s == null || s === 'ready' || s === 'error' || s === 'na';

/**
 * The slice of a desk result the Trade Manager needs to adopt it. The scan candidate (`opp`) and the rest are not
 * read by the backend — leaving them out keeps the request small. Undefined when there is no usable desk result.
 */
export function deskForManager(desk: DeskScoreResult | null | undefined) {
  if (!desk || !desk.signal || desk.lifecycle_score == null) return undefined;
  return { signal: desk.signal, lifecycle_score: desk.lifecycle_score, overrides: desk.overrides ?? [], management_analysis: desk.management_analysis ?? null };
}

interface Entry { tradeId: number; status: DeskStatus; desk: DeskScoreResult | null; error: string | null }

/**
 * Runs the desk score for `trade` once `enabled` turns true (the Quant tab or the Manage read was opened), and again
 * only when `run()` is called. `focus === null` means the structure has no desk score (stock-only, unsupported shape)
 * → status 'na'. The result is tied to the trade it was computed for, so a stale one from the previously selected row
 * can never be mistaken for the current trade's — the first render after a trade switch reports 'idle'.
 */
export function useDeskScore(trade: SavedStrategyItem, pnl: LivePnlResponse | undefined, focus: DeskFocus | null, enabled: boolean): DeskState {
  const tradeId = trade.id;
  const [entry, setEntry] = useState<Entry>({ tradeId: -1, status: 'idle', desk: null, error: null });
  const startedFor = useRef<number | null>(null);
  const pnlRef = useRef(pnl); pnlRef.current = pnl;
  const focusRef = useRef(focus); focusRef.current = focus;

  const start = useCallback(async () => {
    const p = pnlRef.current, f = focusRef.current;
    if (!f || !p) return;
    startedFor.current = tradeId;
    setEntry({ tradeId, status: 'loading', desk: null, error: null });
    try {
      const r = await runDeskScore(tradeId, p, f, p.quote_source || 'yfinance');
      if (startedFor.current !== tradeId) return;                       // the user moved to another trade meanwhile
      if (!r.matched) setEntry({ tradeId, status: 'error', desk: null, error: r.error || 'This trade is not among the current desk candidates.' });
      else setEntry({ tradeId, status: 'ready', desk: r, error: null });
    } catch (e: any) {
      if (startedFor.current === tradeId) setEntry({ tradeId, status: 'error', desk: null, error: e?.message || 'Desk score failed' });
    }
  }, [tradeId]);

  const ready = !!focus && !!pnl;
  useEffect(() => {
    if (!enabled || !ready || startedFor.current === tradeId) return;   // once per trade — StrictMode must not double-fire the heavy scan
    start();
  }, [enabled, ready, tradeId, start]);

  if (!focus) return { status: 'na', desk: null, error: null, run: () => {} };
  if (entry.tradeId !== tradeId) return { status: 'idle', desk: null, error: null, run: start };
  return { status: entry.status, desk: entry.desk, error: entry.error, run: start };
}
