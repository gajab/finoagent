import React, { useEffect, useState, useCallback } from 'react';
import { Loader2, AlertCircle, Layers, Info } from 'lucide-react';
import { fetchStyleCalendarReturns } from '../api';
import type { StyleCalendarResponse, StyleCalendarYear, StyleYearReturn } from '../types';

// ---------------------------------------------------------------------------
// Categories
// ---------------------------------------------------------------------------

const CATEGORIES = ['Factors', 'Size & Indices', 'Geography'] as const;
type Category = typeof CATEGORIES[number];

// ---------------------------------------------------------------------------
// Per-style color palette — each style keeps its color across all years
// so you can visually track how a style moves up/down over time
// ---------------------------------------------------------------------------

const STYLE_COLORS: Record<string, { bg: string; text: string }> = {
  // Factors
  'Value':           { bg: '#991b1b', text: '#fff' },
  'Growth':          { bg: '#5b21b6', text: '#fff' },
  'Momentum':        { bg: '#1e40af', text: '#fff' },
  'Quality':         { bg: '#065f46', text: '#fff' },
  'Low Volatility':  { bg: '#374151', text: '#e5e7eb' },
  'Dividend Yield':  { bg: '#1e3a5f', text: '#fff' },
  'GARP':            { bg: '#78350f', text: '#fff' },
  'Equal Weight':    { bg: '#4c1d95', text: '#fff' },
  'High Beta':       { bg: '#881337', text: '#fff' },
  // Size & Indices
  'Large Cap':       { bg: '#14532d', text: '#f0fdf4' },
  'Mid Cap':         { bg: '#166534', text: '#f0fdf4' },
  'Small Cap':       { bg: '#15803d', text: '#fff' },
  'S&P 500':         { bg: '#16a34a', text: '#fff' },
  'Nasdaq 100':      { bg: '#0369a1', text: '#fff' },
  'Russell 1000':    { bg: '#075985', text: '#fff' },
  'Russell 2000':    { bg: '#0e7490', text: '#fff' },
  'Russell 3000':    { bg: '#155e75', text: '#fff' },
  'Dow Jones':       { bg: '#0f172a', text: '#94a3b8' },
  // Geography
  'Developed Markets': { bg: '#1d4ed8', text: '#fff' },
  'Emerging Markets':  { bg: '#7c3aed', text: '#fff' },
  'All World':         { bg: '#0891b2', text: '#fff' },
  'Developed Europe':  { bg: '#6d28d9', text: '#fff' },
  'Japan':             { bg: '#be185d', text: '#fff' },
  'Korea':             { bg: '#0f766e', text: '#fff' },
  'Canada':            { bg: '#c2410c', text: '#fff' },
  'Israel':            { bg: '#1d4ed8', text: '#fff' },
  'India':             { bg: '#b45309', text: '#fff' },
  'China':             { bg: '#b91c1c', text: '#fff' },
  'Brazil':            { bg: '#16a34a', text: '#fff' },
  'Latin America':     { bg: '#92400e', text: '#fff' },
};

function styleColor(name: string): { bg: string; text: string } {
  return STYLE_COLORS[name] ?? { bg: '#374151', text: '#fff' };
}

// ---------------------------------------------------------------------------
// ETF → description tooltip text
// ---------------------------------------------------------------------------

const ETF_MAP: Record<string, string> = {
  'Value': 'VTV', 'Growth': 'VUG', 'Momentum': 'MTUM', 'Quality': 'QUAL',
  'Low Volatility': 'USMV', 'Dividend Yield': 'VYM', 'GARP': 'SPGP',
  'Equal Weight': 'RSP', 'High Beta': 'SPHB',
  'Large Cap': 'VV', 'Mid Cap': 'IJH', 'Small Cap': 'IJR',
  'S&P 500': 'SPY', 'Nasdaq 100': 'QQQ', 'Russell 1000': 'IWB',
  'Russell 2000': 'IWM', 'Russell 3000': 'IWV', 'Dow Jones': 'DIA',
  'Developed Markets': 'VEA', 'Emerging Markets': 'VWO', 'All World': 'VT',
  'Developed Europe': 'VGK', 'Japan': 'EWJ', 'Korea': 'EWY',
  'Canada': 'EWC', 'Israel': 'EIS', 'India': 'INDA',
  'China': 'MCHI', 'Brazil': 'EWZ', 'Latin America': 'ILF',
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function CategoryTabs({
  active,
  onChange,
}: {
  active: Category;
  onChange: (c: Category) => void;
}) {
  return (
    <div className="flex gap-1 p-1 bg-base-200/60 rounded-lg w-fit">
      {CATEGORIES.map(cat => (
        <button
          key={cat}
          onClick={() => onChange(cat)}
          className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
            active === cat
              ? 'bg-primary text-primary-content shadow'
              : 'text-base-content/50 hover:text-base-content hover:bg-base-200'
          }`}
        >
          {cat}
        </button>
      ))}
    </div>
  );
}

function Legend({ names }: { names: string[] }) {
  return (
    <div className="flex flex-wrap gap-1.5 mt-3">
      {names.map(name => {
        const { bg, text } = styleColor(name);
        const etf = ETF_MAP[name];
        return (
          <span
            key={name}
            title={etf ? `ETF: ${etf}` : undefined}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium cursor-default"
            style={{ backgroundColor: bg, color: text }}
          >
            {name}
            {etf && <span style={{ opacity: 0.7 }}>{etf}</span>}
          </span>
        );
      })}
    </div>
  );
}

function Heatmap({ years }: { years: StyleCalendarYear[] }) {
  if (years.length === 0) return null;
  const numRows = Math.max(...years.map(y => y.styles.length));

  return (
    <div className="overflow-x-auto rounded-xl border border-white/10">
      <table className="border-collapse text-xs" style={{ minWidth: `${years.length * 94}px` }}>
        <thead>
          <tr>
            {years.map(y => (
              <th
                key={y.year}
                className="px-1.5 py-2 text-center font-bold text-gray-300 bg-gray-800/80 border-b border-white/10 whitespace-nowrap"
                style={{ minWidth: 90 }}
              >
                {y.year}{y.in_progress ? <span className="text-yellow-400 ml-0.5">*</span> : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: numRows }).map((_, rank) => (
            <tr key={rank}>
              {years.map(y => {
                const style: StyleYearReturn | undefined = y.styles[rank];
                if (!style) return (
                  <td key={y.year} className="p-0.5 bg-gray-950/80" style={{ minWidth: 90 }} />
                );
                const { bg, text } = styleColor(style.name);
                return (
                  <td key={y.year} className="p-0.5" style={{ backgroundColor: '#03070f', minWidth: 90 }}>
                    <div
                      className="flex flex-col items-center justify-center rounded px-1 py-2 leading-tight select-none"
                      title={ETF_MAP[style.name] ? `${style.name} · ${ETF_MAP[style.name]}` : style.name}
                      style={{ backgroundColor: bg, color: text, minHeight: 46 }}
                    >
                      <span className="font-medium text-center" style={{ fontSize: 9.5, lineHeight: 1.2 }}>
                        {style.name}
                      </span>
                      <span className="font-bold mt-0.5" style={{ fontSize: 11.5 }}>
                        {style.return > 0 ? '+' : ''}{style.return}%
                      </span>
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const CACHE: Partial<Record<Category, StyleCalendarYear[]>> = {};

export function clearStyleCalendarCache() {
  for (const key in CACHE) {
    delete CACHE[key as Category];
  }
}

export default function StyleCalendarHeatmap() {
  const [activeTab, setActiveTab] = useState<Category>('Factors');
  const [data, setData] = useState<StyleCalendarYear[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((cat: Category) => {
    // Return cached result instantly if available
    if (CACHE[cat]) {
      setData(CACHE[cat]!);
      return;
    }
    setLoading(true);
    setError(null);
    fetchStyleCalendarReturns(cat)
      .then(res => {
        CACHE[cat] = res.years;
        setData(res.years);
      })
      .catch(e => setError(e?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(activeTab); }, [activeTab, load]);

  // Collect unique style names present in this data (for legend)
  const styleNames = Array.from(
    new Set(data.flatMap(y => y.styles.map(s => s.name)))
  );

  return (
    <div className="space-y-4">
      {/* Header row */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="flex items-center gap-2">
          <Layers className="w-5 h-5 text-primary shrink-0" />
          <h2 className="text-lg font-semibold">Style Returns by Calendar Year</h2>
        </div>
        <div className="flex items-center gap-3 sm:ml-auto">
          <CategoryTabs active={activeTab} onChange={tab => { setActiveTab(tab); setData([]); }} />
        </div>
      </div>

      {/* Sub-header info */}
      <div className="flex items-center gap-1.5 text-xs text-base-content/35">
        <Info className="w-3.5 h-3.5 shrink-0" />
        <span>
          {activeTab === 'Factors' && 'US factor ETFs · data from 2013 · rows ranked best→worst each year'}
          {activeTab === 'Size & Indices' && 'US size & benchmark ETFs · data from 2007 · rows ranked best→worst'}
          {activeTab === 'Geography' && 'Country & regional ETFs · data from 2008 · some markets may have later starts'}
        </span>
      </div>

      {/* Content */}
      {loading && (
        <div className="flex items-center justify-center gap-2 py-14 text-gray-400">
          <Loader2 className="w-5 h-5 animate-spin" />
          <span>Loading {activeTab.toLowerCase()} returns…</span>
        </div>
      )}
      {error && (
        <div className="flex items-center gap-2 p-4 rounded-lg bg-red-900/20 text-red-400">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {!loading && !error && <Heatmap years={data} />}

      {/* Legend */}
      {styleNames.length > 0 && !loading && <Legend names={styleNames} />}

      <p className="text-[10px] text-base-content/30">
        * Current year in-progress (Jan 1 → today). Hover a cell or legend badge to see the ETF ticker.
        Data via Yahoo Finance. Not investment advice.
      </p>
    </div>
  );
}
