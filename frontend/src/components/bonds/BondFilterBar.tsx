import React, { useMemo } from 'react';
import { Filter, X } from 'lucide-react';
import type { BondFilters, BondHoldingInput, BondKind } from '../../types';
import { ACCOUNTS, KIND_META, KIND_ORDER } from './bondUi';
import { EMPTY_FILTERS, TAX_ADVANTAGED, filtersActive, matchesFilters } from './bondFilters';

const Chip: React.FC<{ on: boolean; onClick: () => void; color?: string; children: React.ReactNode }> = ({ on, onClick, color, children }) => (
  <button type="button" onClick={onClick}
    className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors ${
      on ? 'border-primary/40 bg-primary/15 text-primary' : 'border-white/[0.08] text-base-content/60 hover:border-white/20 hover:text-base-content'}`}
    style={on && color ? { borderColor: `${color}80`, backgroundColor: `${color}22`, color } : undefined}>
    {children}
  </button>
);

// One filter bar shared by Holdings, Cash Flow and Planner. Options come from what the user actually holds.
export default function BondFilterBar({ holdings, value, onChange, scope }: {
  holdings: BondHoldingInput[];
  value: BondFilters;
  onChange: (f: BondFilters) => void;
  scope: string;   // e.g. "holdings", "cash flow" — shown in the count line
}) {
  const book = useMemo(() => holdings.filter(h => (h.status ?? 'held') !== 'sold' && (h.status ?? 'held') !== 'matured'), [holdings]);
  const kinds = useMemo(() => KIND_ORDER.filter(k => book.some(h => h.kind === k)), [book]);
  const accts = useMemo(() => ACCOUNTS.filter(a => book.some(h => (h.account_type ?? 'taxable') === a.value)), [book]);
  const shown = book.filter(h => matchesFilters(h, value)).length;

  const toggle = (key: keyof BondFilters, v: string) => {
    const cur = value[key];
    onChange({ ...value, [key]: cur.includes(v) ? cur.filter(x => x !== v) : [...cur, v] });
  };
  const presentAdv = accts.map(a => a.value).filter(v => TAX_ADVANTAGED.includes(v));
  const quick = value.accountTypes.length === 0 ? 'all'
    : value.accountTypes.length === 1 && value.accountTypes[0] === 'taxable' ? 'taxable'
    : value.accountTypes.every(a => TAX_ADVANTAGED.includes(a)) && presentAdv.every(a => value.accountTypes.includes(a)) ? 'adv' : 'custom';
  const setQuick = (q: 'all' | 'taxable' | 'adv') =>
    onChange({ ...value, accountTypes: q === 'all' ? [] : q === 'taxable' ? ['taxable'] : (presentAdv.length ? presentAdv : TAX_ADVANTAGED) });

  if (book.length < 2) return null;
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-base-100/60 px-4 py-2.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold text-base-content/60"><Filter className="h-3.5 w-3.5" /> Filter</span>
        {kinds.length > 1 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="mr-0.5 text-[10px] uppercase tracking-wide text-base-content/40">Type</span>
            {kinds.map(k => <Chip key={k} on={value.kinds.includes(k)} color={KIND_META[k as BondKind].color} onClick={() => toggle('kinds', k)}>{KIND_META[k as BondKind].label}</Chip>)}
          </div>
        )}
        {accts.length > 1 && (
          <div className="flex flex-wrap items-center gap-1">
            <span className="mr-0.5 text-[10px] uppercase tracking-wide text-base-content/40">Account</span>
            <Chip on={quick === 'all'} onClick={() => setQuick('all')}>All</Chip>
            {accts.some(a => a.value === 'taxable') && <Chip on={quick === 'taxable'} onClick={() => setQuick('taxable')}>Taxable</Chip>}
            {presentAdv.length > 0 && <Chip on={quick === 'adv'} onClick={() => setQuick('adv')}>Tax-advantaged</Chip>}
            <span className="mx-1 h-3 w-px bg-white/10" />
            {accts.map(a => <Chip key={a.value} on={value.accountTypes.includes(a.value)} onClick={() => toggle('accountTypes', a.value)}>{a.label}</Chip>)}
          </div>
        )}
        <div className="ml-auto flex items-center gap-2 text-[11px] text-base-content/50">
          {filtersActive(value)
            ? <>Showing {shown} of {book.length} holdings in {scope}
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => onChange(EMPTY_FILTERS)}><X className="h-3 w-3" /> Clear</button></>
            : <>All {book.length} holdings</>}
        </div>
      </div>
    </div>
  );
}
