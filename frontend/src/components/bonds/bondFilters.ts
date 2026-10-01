import type { BondFilters } from '../../types';

// Shared Holdings / Cash Flow / Planner filters. Holdings filters client-side; Cash Flow and Planner send
// the same filters to the server so taxes, fund drawdown and the optimizer run on the filtered book.

export const EMPTY_FILTERS: BondFilters = { kinds: [], accountTypes: [] };
export const TAX_ADVANTAGED = ['ira', 'roth', '401k', '403b', 'hsa', '529'];
const KEY = 'bondDesk.filters.v1';

export function filtersActive(f: BondFilters): boolean {
  return f.kinds.length > 0 || f.accountTypes.length > 0;
}

export function matchesFilters(h: { kind?: string | null; account_type?: string | null }, f: BondFilters): boolean {
  if (f.kinds.length && !f.kinds.includes((h.kind ?? '').toLowerCase())) return false;
  if (f.accountTypes.length && !f.accountTypes.includes((h.account_type ?? 'taxable').toLowerCase())) return false;
  return true;
}

// per-viewer convenience only — storage can be unavailable (private mode), so never let it throw
export function loadFilters(): BondFilters {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return EMPTY_FILTERS;
    const v = JSON.parse(raw);
    return { kinds: v.kinds ?? [], accountTypes: v.accountTypes ?? [] };
  } catch {
    return EMPTY_FILTERS;
  }
}

export function saveFilters(f: BondFilters): void {
  try { window.localStorage.setItem(KEY, JSON.stringify(f)); } catch { /* ignore */ }
}
