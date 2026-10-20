import { LIMIT_ONLY_NOTE } from '../lib/fills';

/** "Limit order only" — a wide but real market the scan keeps (scored at the expected fill, ranked last). */
export default function LimitOnlyBadge({ o, className = '' }: { o?: { limit_only?: boolean } | null; className?: string }) {
  if (!o?.limit_only) return null;
  return (
    <span className={`badge badge-xs badge-warning badge-outline whitespace-nowrap cursor-help ${className}`} title={LIMIT_ONLY_NOTE}>
      Limit order only
    </span>
  );
}
