import React, { useState } from 'react';
import { Sparkles, Loader2, CheckCircle, AlertTriangle, Trash2, Plus, X } from 'lucide-react';
import { bulkParseHoldings, bulkSaveHoldings } from '../api';
import type { ParsedHolding, HoldingInput } from '../types';

interface BulkImportProps {
  onComplete: () => void;
}

export function BulkImport({ onComplete }: BulkImportProps) {
  const [text, setText] = useState('');
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [parsed, setParsed] = useState<ParsedHolding[] | null>(null);

  const handleParse = async () => {
    if (text.trim().length < 10) {
      setError('Please enter more details about your holdings.');
      return;
    }

    try {
      setParsing(true);
      setError(null);
      setSuccess(null);
      const result = await bulkParseHoldings(text);
      if (result.holdings.length === 0) {
        setError('No holdings could be extracted from the text. Please try rephrasing.');
        return;
      }
      setParsed(result.holdings);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to parse text');
    } finally {
      setParsing(false);
    }
  };

  const handleFieldChange = (idx: number, field: keyof ParsedHolding, value: string) => {
    if (!parsed) return;
    const updated = [...parsed];
    if (field === 'shares' || field === 'cost_basis') {
      (updated[idx] as any)[field] = parseFloat(value) || 0;
    } else {
      (updated[idx] as any)[field] = value;
    }
    setParsed(updated);
  };

  const handleRemoveRow = (idx: number) => {
    if (!parsed) return;
    setParsed(parsed.filter((_, i) => i !== idx));
  };

  const handleSave = async () => {
    if (!parsed || parsed.length === 0) return;

    // Validate
    for (const h of parsed) {
      if (!h.ticker || h.shares <= 0 || h.cost_basis <= 0 || !h.purchase_date) {
        setError('Please fill in all fields correctly for each holding.');
        return;
      }
    }

    const holdings: HoldingInput[] = parsed.map((h) => ({
      ticker: h.ticker.toUpperCase(),
      shares: h.shares,
      cost_basis: h.cost_basis,
      purchase_date: h.purchase_date,
    }));

    try {
      setSaving(true);
      setError(null);
      const result = await bulkSaveHoldings(holdings);
      setSuccess(`Successfully added ${result.count} holdings to your portfolio!`);
      setParsed(null);
      setText('');
      onComplete();
      setTimeout(() => setSuccess(null), 4000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save holdings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="glass-card">
      <div className="p-5">
        <h2 className="font-bold text-sm flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-secondary" />
          Import from Text (AI)
        </h2>
        <p className="text-sm text-base-content/60 -mt-1">
          Paste your holdings in any format — AI will extract the data for you to review.
        </p>

        {/* Alerts */}
        {error && (
          <div className="alert alert-error py-2 mt-2">
            <AlertTriangle className="w-4 h-4" />
            <span className="text-sm">{error}</span>
            <button className="btn btn-ghost btn-xs" onClick={() => setError(null)}>✕</button>
          </div>
        )}
        {success && (
          <div className="alert alert-success py-2 mt-2">
            <CheckCircle className="w-4 h-4" />
            <span className="text-sm">{success}</span>
          </div>
        )}

        {/* Text Input (before parsing) */}
        {!parsed && (
          <div className="mt-2 space-y-3">
            <textarea
              className="textarea textarea-bordered w-full h-32 text-sm"
              placeholder={`Paste your holdings in any format, e.g.:\n\nI bought 100 shares of AAPL at $150 on Jan 5 2024\n50 MSFT at $380 on March 10 2024\n$15,000 of GOOGL at $140 per share in Feb 2024\n200 shares NVDA @ 450, purchased 12/1/2023`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={parsing}
            />
            <div className="flex justify-end">
              <button
                className="btn btn-secondary btn-sm gap-2"
                onClick={handleParse}
                disabled={parsing || text.trim().length < 10}
              >
                {parsing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Sparkles className="w-4 h-4" />
                )}
                {parsing ? 'Parsing...' : 'Parse with AI'}
              </button>
            </div>
          </div>
        )}

        {/* Preview Table (after parsing) */}
        {parsed && parsed.length > 0 && (
          <div className="mt-2 space-y-3">
            <div className="overflow-x-auto">
              <table className="table table-sm table-pro">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Shares</th>
                    <th>Cost Basis</th>
                    <th>Purchase Date</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {parsed.map((h, idx) => (
                    <tr key={idx}>
                      <td>
                        <input
                          type="text"
                          className="input input-bordered input-xs w-20 uppercase font-bold"
                          value={h.ticker}
                          onChange={(e) => handleFieldChange(idx, 'ticker', e.target.value.toUpperCase())}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          className="input input-bordered input-xs w-20"
                          step="0.01"
                          value={h.shares}
                          onChange={(e) => handleFieldChange(idx, 'shares', e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          type="number"
                          className="input input-bordered input-xs w-24"
                          step="0.01"
                          value={h.cost_basis}
                          onChange={(e) => handleFieldChange(idx, 'cost_basis', e.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          type="date"
                          className="input input-bordered input-xs"
                          value={h.purchase_date}
                          onChange={(e) => handleFieldChange(idx, 'purchase_date', e.target.value)}
                        />
                      </td>
                      <td>
                        <button
                          className="btn btn-ghost btn-xs text-error"
                          onClick={() => handleRemoveRow(idx)}
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex justify-between items-center">
              <button
                className="btn btn-ghost btn-sm gap-1"
                onClick={() => { setParsed(null); setError(null); }}
              >
                <X className="w-4 h-4" />
                Start Over
              </button>
              <button
                className="btn btn-primary btn-sm gap-2"
                onClick={handleSave}
                disabled={saving || parsed.length === 0}
              >
                {saving ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Plus className="w-4 h-4" />
                )}
                {saving ? 'Saving...' : `Add ${parsed.length} Holdings`}
              </button>
            </div>
          </div>
        )}

        {/* Empty parsed state */}
        {parsed && parsed.length === 0 && (
          <div className="text-center py-4">
            <p className="text-base-content/50 text-sm">All items removed.</p>
            <button
              className="btn btn-ghost btn-sm mt-2"
              onClick={() => { setParsed(null); }}
            >
              Start Over
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
