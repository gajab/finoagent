import { useEffect, useState, useCallback } from 'react';
import { fetchCampaignLedger } from '../../api';
import type { CampaignLedgerResponse } from '../../api';

/** Loads a trade's option journal (`/campaign-ledger`). `version` (e.g. the trade's `updated_at`) re-fetches
 *  after a close / roll / edit so the timeline never lags the position. Lazy: nothing is fetched until `enabled`. */
export function useCampaignLedger(tradeId: number, enabled: boolean, version?: string | number | null) {
  const [data, setData] = useState<CampaignLedgerResponse | null>(null);
  const [loading, setLoading] = useState(enabled);       // no empty-state flash before the first fetch starts
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setLoading(true); setError(null);
    fetchCampaignLedger(tradeId)
      .then(d => { if (alive) setData(d); })
      .catch((e: any) => { if (alive) setError(e?.message || 'Failed to load the option journal'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [tradeId, enabled, version, tick]);

  const reload = useCallback(() => setTick(t => t + 1), []);
  return { data, loading, error, reload };
}
