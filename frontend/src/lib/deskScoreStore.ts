/**
 * The deep (full-desk) quant score a user has run for a trade — ONE copy, shared by every panel that shows a
 * quant hold/close read. Quant Analysis writes it when its desk run finishes; the Trade Manager reads it and passes it
 * to the server so its Quant lens IS that same score (the server prefers it over the light `analysis.quant_exit`).
 * Without this the two panels scored the same trade independently — "53/100 STRONG CLOSE" in one, "4/100" in the other.
 *
 * In-memory and per page-load on purpose: it is a convenience cache of a result the user already computed, never
 * persisted, and an absent entry simply means "light read" (the Trade Manager labels it as such).
 */
import type { DeskScoreResult } from '../api';

const results = new Map<number, DeskScoreResult>();
const listeners = new Map<number, Set<() => void>>();

export function setDeskScore(tradeId: number, r: DeskScoreResult | null): void {
  if (r) results.set(tradeId, r); else results.delete(tradeId);
  listeners.get(tradeId)?.forEach(fn => fn());
}

export function getDeskScore(tradeId: number): DeskScoreResult | null {
  return results.get(tradeId) ?? null;
}

export function subscribeDeskScore(tradeId: number, fn: () => void): () => void {
  let set = listeners.get(tradeId);
  if (!set) { set = new Set(); listeners.set(tradeId, set); }
  set.add(fn);
  return () => { set!.delete(fn); };
}
