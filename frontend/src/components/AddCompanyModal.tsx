/**
 * AddCompanyModal — lets a user manually add a company to an existing theme
 * in their Tracked Companies list without going through Pick & Shovel.
 *
 * Flow: enter ticker → auto-resolve name/price/sector → confirm → track
 */

import React, { useState, useRef, useEffect } from 'react';
import {
  X, Search, Loader2, AlertCircle, CheckCircle2,
  TrendingUp, TrendingDown, Building2,
} from 'lucide-react';
import { fetchStockData, trackCompany } from '../api';
import type { TrackedCompany, TrackedGroup } from '../types';

interface Props {
  open: boolean;
  onClose: () => void;
  group?: TrackedGroup | null;                  // pre-filled theme; null = manual entry
  onAdded: (company: TrackedCompany) => void;
}

const TIER_OPTIONS = [
  { value: 'direct',  label: 'Theme Core',   desc: 'Core beneficiary' },
  { value: 'enabler', label: 'Backbone',      desc: 'Enabler / supplier' },
  { value: 'deep',    label: 'Hidden Pick',   desc: 'Non-obvious play' },
  { value: 'manual',  label: 'Manual',        desc: 'Your own pick' },
];

export default function AddCompanyModal({ open, onClose, group, onAdded }: Props) {
  const [ticker, setTicker]           = useState('');
  const [theme, setTheme]             = useState('');
  const [looking, setLooking]         = useState(false);
  const [resolved, setResolved]       = useState<{
    name: string; exchange: string | null; sector: string | null;
    price: number | null; chg: number | null;
  } | null>(null);
  const [lookupErr, setLookupErr]     = useState<string | null>(null);
  const [tier, setTier]               = useState<string>('manual');
  const [notes, setNotes]             = useState('');
  const [saving, setSaving]           = useState(false);
  const [saveErr, setSaveErr]         = useState<string | null>(null);
  const inputRef                      = useRef<HTMLInputElement>(null);

  const isManual = !group;
  const effectiveTheme = group ? group.theme_raw : theme.trim();
  const effectiveSlug  = group ? group.theme_slug : theme.trim().slice(0, 50);

  // Focus ticker input on open
  useEffect(() => {
    if (open) {
      setTicker(''); setTheme(''); setResolved(null); setLookupErr(null);
      setTier('manual'); setNotes(''); setSaveErr(null);
      setTimeout(() => inputRef.current?.focus(), 80);
    }
  }, [open]);

  if (!open) return null;

  const lookup = async () => {
    const t = ticker.trim().toUpperCase();
    if (!t) return;
    setLooking(true);
    setLookupErr(null);
    setResolved(null);
    try {
      const data = await fetchStockData(t);
      setResolved({
        name:     data.companyName || t,
        exchange: null,
        sector:   data.sector || null,
        price:    data.price ?? null,
        chg:      data.changePercent ?? null,
      });
    } catch {
      setLookupErr(`Could not find "${t}". Check the ticker and try again.`);
    } finally {
      setLooking(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') lookup();
  };

  const handleSubmit = async () => {
    if (!resolved || !effectiveTheme) return;
    setSaving(true);
    setSaveErr(null);
    try {
      const result = await trackCompany({
        ticker:        ticker.trim().toUpperCase(),
        name:          resolved.name,
        theme_raw:     effectiveTheme,
        theme_slug:    effectiveSlug,
        theme_summary: group?.theme_summary ?? null,
        exchange:      resolved.exchange ?? null,
        sector:        resolved.sector ?? null,
        source_tier:   tier,
        depth_level:   null,
        user_notes:    notes.trim() || null,
        llm_data:      {},
        financial_data: {
          price:        resolved.price,
          price_chg_1d: resolved.chg,
          sector:       resolved.sector,
        },
      } as any);
      onAdded(result);
      onClose();
    } catch (e: any) {
      setSaveErr(e?.message || 'Failed to add company');
    } finally {
      setSaving(false);
    }
  };

  const chgPos = resolved?.chg != null && resolved.chg >= 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="bg-base-200 rounded-2xl border border-white/10 w-full max-w-md shadow-2xl flex flex-col">

        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-white/[0.06]">
          <div>
            <p className="font-semibold text-sm">Add Company</p>
            {group && (
              <p className="text-xs text-base-content/40 mt-0.5 truncate max-w-xs">
                to &ldquo;<span className="text-base-content/70">{group.theme_slug}</span>&rdquo;
              </p>
            )}
            {isManual && (
              <p className="text-xs text-base-content/40 mt-0.5">Manual entry — choose your own theme</p>
            )}
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm btn-square">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-4">

          {/* Theme field — only shown in manual (no group) mode */}
          {isManual && (
            <div>
              <label className="label py-1">
                <span className="label-text text-xs font-medium">Theme / Category</span>
                <span className="label-text-alt text-[10px] text-base-content/40">e.g. AI Infrastructure</span>
              </label>
              <input
                type="text"
                placeholder="What theme does this company belong to?"
                value={theme}
                onChange={e => setTheme(e.target.value)}
                className="input input-bordered input-sm w-full"
                maxLength={100}
              />
            </div>
          )}

          {/* Ticker lookup */}
          <div>
            <label className="label py-1"><span className="label-text text-xs font-medium">Ticker</span></label>
            <div className="flex gap-2">
              <input
                ref={inputRef}
                type="text"
                placeholder="e.g. NVDA"
                value={ticker}
                onChange={e => { setTicker(e.target.value.toUpperCase()); setResolved(null); setLookupErr(null); }}
                onKeyDown={handleKeyDown}
                className="input input-bordered input-sm flex-1 font-mono uppercase tracking-widest"
                maxLength={10}
              />
              <button
                onClick={lookup}
                disabled={!ticker.trim() || looking}
                className="btn btn-sm btn-primary gap-1.5"
              >
                {looking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                Look Up
              </button>
            </div>
            {lookupErr && (
              <p className="text-xs text-error mt-1.5 flex items-center gap-1">
                <AlertCircle className="w-3 h-3 flex-shrink-0" />{lookupErr}
              </p>
            )}
          </div>

          {/* Resolved company card */}
          {resolved && (
            <div className="rounded-xl border border-success/20 bg-success/5 p-3 space-y-1.5">
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-3.5 h-3.5 text-success flex-shrink-0" />
                <span className="font-semibold text-sm">{resolved.name}</span>
              </div>
              <div className="flex items-center gap-3 text-xs text-base-content/60 flex-wrap">
                {resolved.exchange && (
                  <span className="flex items-center gap-1">
                    <Building2 className="w-3 h-3" />{resolved.exchange}
                  </span>
                )}
                {resolved.sector && <span>{resolved.sector}</span>}
                {resolved.price != null && (
                  <span className="flex items-center gap-1 ml-auto">
                    <span className="font-mono font-semibold text-base-content/90">
                      ${resolved.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                    {resolved.chg != null && (
                      <span className={`flex items-center gap-0.5 font-medium ${chgPos ? 'text-success' : 'text-error'}`}>
                        {chgPos ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                        {chgPos ? '+' : ''}{resolved.chg.toFixed(2)}%
                      </span>
                    )}
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Source tier */}
          <div>
            <label className="label py-1">
              <span className="label-text text-xs font-medium">Source Tier</span>
              <span className="label-text-alt text-[10px] text-base-content/40">optional</span>
            </label>
            <div className="grid grid-cols-2 gap-1.5">
              {TIER_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setTier(opt.value)}
                  className={`px-3 py-2 rounded-xl border text-xs text-left transition-all
                    ${tier === opt.value
                      ? 'border-primary/40 bg-primary/10 text-primary'
                      : 'border-white/[0.06] bg-base-100/50 text-base-content/60 hover:border-white/15'
                    }`}
                >
                  <div className="font-semibold">{opt.label}</div>
                  <div className="text-[10px] opacity-60">{opt.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="label py-1">
              <span className="label-text text-xs font-medium">Notes</span>
              <span className="label-text-alt text-[10px] text-base-content/40">optional</span>
            </label>
            <textarea
              className="textarea textarea-bordered w-full text-xs leading-relaxed resize-none"
              rows={2}
              placeholder="Why are you tracking this company?"
              value={notes}
              onChange={e => setNotes(e.target.value)}
            />
          </div>

          {saveErr && (
            <p className="text-xs text-error flex items-center gap-1">
              <AlertCircle className="w-3 h-3 flex-shrink-0" />{saveErr}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-white/[0.06] flex items-center justify-end gap-2">
          <button onClick={onClose} className="btn btn-ghost btn-sm">Cancel</button>
          <button
            onClick={handleSubmit}
            disabled={!resolved || saving || (isManual && !theme.trim())}
            className="btn btn-primary btn-sm gap-2"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
            {saving ? 'Adding…' : 'Add to Tracking →'}
          </button>
        </div>

      </div>
    </div>
  );
}
