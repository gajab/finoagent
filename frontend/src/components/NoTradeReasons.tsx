import { AlertTriangle } from 'lucide-react';

/** Empty-scan banner: the headline plus EVERY reason the scan found no trade (from the backend's note_reasons). */
export default function NoTradeReasons({ note, reasons, fallback, className = 'text-sm' }: {
  note?: string | null; reasons?: string[] | null; fallback: string; className?: string;
}) {
  return (
    <div className={`alert alert-warning items-start ${className}`}>
      <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
      <div className="min-w-0">
        <div className="font-medium">{note || fallback}</div>
        {reasons && reasons.length > 0 && (
          <ul className="mt-1 list-disc pl-4 space-y-0.5 font-normal">
            {reasons.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}
