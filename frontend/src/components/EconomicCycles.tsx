import React, { useEffect, useState } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, TrendingUp, TrendingDown, Minus,
  ChevronDown, ChevronUp, Info, Sparkles, Star, Database,
} from 'lucide-react';
import { fetchBusinessCycle, refreshBusinessCycle } from '../api';
import type {
  BusinessCycleResponse, RegionCycle, CyclePhase, GdpPoint,
} from '../types';
import CycleSectorTA from './CycleSectorTA';

// ---------------------------------------------------------------------------
// Phase config
// ---------------------------------------------------------------------------

const PHASE_CONFIG: Record<CyclePhase, {
  label: string; color: string; bg: string; border: string; textColor: string;
}> = {
  early:     { label: 'Recovery',    color: '#22c55e', bg: 'bg-success/10',  border: 'border-success/30',  textColor: 'text-success'  },
  mid:       { label: 'Expansion',   color: '#3b82f6', bg: 'bg-info/10',     border: 'border-info/30',     textColor: 'text-info'     },
  late:      { label: 'Slowdown',    color: '#f59e0b', bg: 'bg-warning/10',  border: 'border-warning/30',  textColor: 'text-warning'  },
  recession: { label: 'Contraction', color: '#ef4444', bg: 'bg-error/10',    border: 'border-error/30',    textColor: 'text-error'    },
};

const PHASE_ORDER: CyclePhase[] = ['early', 'mid', 'late', 'recession'];

// Phase arc angles: 12 o'clock = -90°, clockwise
const PHASE_ANGLES: Record<CyclePhase, { start: number; mid: number; end: number }> = {
  early:     { start: -90, mid: -45, end: 0   },
  mid:       { start:   0, mid:  45, end: 90  },
  late:      { start:  90, mid: 135, end: 180 },
  recession: { start: 180, mid: 225, end: 270 },
};

// ---------------------------------------------------------------------------
// SVG helpers
// ---------------------------------------------------------------------------

function toRad(d: number) { return (d * Math.PI) / 180; }

function arcPath(cx: number, cy: number, r: number, s: number, e: number) {
  const sx = cx + r * Math.cos(toRad(s)), sy = cy + r * Math.sin(toRad(s));
  const ex = cx + r * Math.cos(toRad(e)), ey = cy + r * Math.sin(toRad(e));
  return `M ${sx} ${sy} A ${r} ${r} 0 0 1 ${ex} ${ey}`;
}

function polarXY(cx: number, cy: number, r: number, deg: number) {
  return { x: cx + r * Math.cos(toRad(deg)), y: cy + r * Math.sin(toRad(deg)) };
}

// ---------------------------------------------------------------------------
// Cycle Clock
// ---------------------------------------------------------------------------

function CycleClock({ regions }: { regions: RegionCycle[] }) {
  const cx = 160, cy = 160, OR = 120, IR = 72, FR = 143;
  const byPhase: Record<CyclePhase, RegionCycle[]> = { early: [], mid: [], late: [], recession: [] };
  regions.forEach(r => { if (r.phase in byPhase) byPhase[r.phase].push(r); });

  return (
    <svg viewBox="0 0 320 320" className="w-full max-w-xs mx-auto">
      {/* Phase arcs */}
      {PHASE_ORDER.map(phase => {
        const { start, end } = PHASE_ANGLES[phase];
        const { color } = PHASE_CONFIG[phase];
        const midR = (OR + IR) / 2;
        const sw   = OR - IR - 4;
        return (
          <g key={phase}>
            <path d={arcPath(cx, cy, midR, start + 2, end - 2)}
              fill="none" stroke={color} strokeWidth={sw} strokeOpacity={0.18} />
            <path d={arcPath(cx, cy, midR, start + 2, end - 2)}
              fill="none" stroke={color} strokeWidth={sw - 6} strokeOpacity={0.55} />
          </g>
        );
      })}

      {/* Divider ticks */}
      {[-90, 0, 90, 180].map(deg => {
        const a = toRad(deg);
        return (
          <line key={deg}
            x1={cx + IR * Math.cos(a)} y1={cy + IR * Math.sin(a)}
            x2={cx + OR * Math.cos(a)} y2={cy + OR * Math.sin(a)}
            stroke="rgba(255,255,255,0.15)" strokeWidth={1.5} />
        );
      })}

      {/* Phase labels */}
      {PHASE_ORDER.map(phase => {
        const p = polarXY(cx, cy, (OR + IR) / 2, PHASE_ANGLES[phase].mid);
        return (
          <text key={phase} x={p.x} y={p.y + 4} textAnchor="middle"
            fill={PHASE_CONFIG[phase].color} fontSize="8.5" fontWeight="700" letterSpacing="0.4">
            {PHASE_CONFIG[phase].label.toUpperCase()}
          </text>
        );
      })}

      {/* Center */}
      <text x={cx} y={cy - 8}  textAnchor="middle" fill="rgba(255,255,255,0.65)" fontSize="10" fontWeight="600">Business</text>
      <text x={cx} y={cy + 6}  textAnchor="middle" fill="rgba(255,255,255,0.65)" fontSize="10" fontWeight="600">Cycle</text>
      <text x={cx} y={cy + 19} textAnchor="middle" fill="rgba(255,255,255,0.25)" fontSize="7.5">Clock · OECD CLI</text>

      {/* Region flags */}
      {PHASE_ORDER.map(phase => {
        const pr = byPhase[phase];
        if (!pr.length) return null;
        const { start, end } = PHASE_ANGLES[phase];
        const span = end - start - 10;
        const step = span / pr.length;
        return pr.map((region, i) => {
          const angle = start + 5 + step * i + step / 2;
          const p = polarXY(cx, cy, FR, angle);
          return (
            <g key={region.id}>
              <circle cx={p.x} cy={p.y} r={12.5}
                fill={PHASE_CONFIG[phase].color} fillOpacity={0.13}
                stroke={PHASE_CONFIG[phase].color} strokeOpacity={0.45} strokeWidth={1} />
              <text x={p.x} y={p.y + 5} textAnchor="middle" fontSize="13">{region.flag}</text>
            </g>
          );
        });
      })}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// CLI Sparkline (inline SVG)
// ---------------------------------------------------------------------------

function CliSparkline({ series, phase }: { series: number[]; phase: CyclePhase }) {
  if (series.length < 2) return <span className="text-xs text-base-content/30">No data</span>;
  const w = 120, h = 28;
  const min = Math.min(...series), max = Math.max(...series);
  const range = max - min || 0.01;
  const pts = series.map((v, i) => {
    const x = (i / (series.length - 1)) * w;
    const y = h - ((v - min) / range) * (h - 4) - 2;
    return `${x},${y}`;
  });
  const color = PHASE_CONFIG[phase].color;
  // 100-line position
  const y100 = h - ((100 - min) / range) * (h - 4) - 2;
  const show100 = y100 > 0 && y100 < h;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} className="overflow-visible">
      {show100 && (
        <line x1={0} y1={y100} x2={w} y2={y100}
          stroke="rgba(255,255,255,0.12)" strokeWidth={0.8} strokeDasharray="2,2" />
      )}
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      {/* Last point dot */}
      <circle cx={parseFloat(pts[pts.length - 1].split(",")[0])}
              cy={parseFloat(pts[pts.length - 1].split(",")[1])}
              r={2.5} fill={color} />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// GDP bar chart (last 4–5 years)
// ---------------------------------------------------------------------------

function GdpBars({ series, currentYear }: { series: GdpPoint[]; currentYear: number }) {
  if (!series.length) return <span className="text-xs text-base-content/30">No data</span>;
  const maxAbs = Math.max(...series.map(p => Math.abs(p.value)), 1);
  return (
    <div className="space-y-1">
      {series.map(pt => {
        const pct = (Math.abs(pt.value) / maxAbs) * 100;
        const pos = pt.value >= 0;
        const isEst = pt.year >= currentYear;
        return (
          <div key={pt.year} className="flex items-center gap-2 text-xs">
            <span className="w-10 text-right text-base-content/40 font-mono text-[10px] shrink-0">
              {pt.year}{isEst ? 'f' : ''}
            </span>
            <div className="flex-1 flex items-center gap-1">
              <div className="flex-1 bg-white/[0.04] rounded-sm h-4 relative overflow-hidden">
                <div
                  className="h-full rounded-sm transition-all"
                  style={{
                    width: `${pct}%`,
                    background: pos ? 'rgba(34,197,94,0.55)' : 'rgba(239,68,68,0.55)',
                  }}
                />
              </div>
              <span className={`w-10 text-right font-semibold tabular-nums text-[11px] ${pos ? 'text-success' : 'text-error'}`}>
                {pos ? '+' : ''}{pt.value}%
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stars
// ---------------------------------------------------------------------------

function Stars({ n }: { n: number }) {
  return (
    <span className="flex gap-0.5">
      {[1,2,3,4,5].map(i => (
        <Star key={i} className="w-3 h-3"
          fill={i <= n ? 'currentColor' : 'none'} strokeWidth={1.5}
          style={{ color: i <= n ? '#f59e0b' : 'rgba(255,255,255,0.18)' }} />
      ))}
    </span>
  );
}

function MomentumIcon({ m }: { m: string }) {
  if (m === 'accelerating') return <TrendingUp  className="w-3.5 h-3.5 text-success" />;
  if (m === 'decelerating') return <TrendingDown className="w-3.5 h-3.5 text-error" />;
  return <Minus className="w-3.5 h-3.5 text-base-content/40" />;
}

// ---------------------------------------------------------------------------
// Region card
// ---------------------------------------------------------------------------

function RegionCard({ region, matrix }: {
  region: RegionCycle;
  matrix: BusinessCycleResponse['asset_matrix'];
}) {
  const [expanded, setExpanded] = useState(false);
  const cfg = PHASE_CONFIG[region.phase] ?? PHASE_CONFIG.mid;
  const top3 = matrix[region.phase]?.performers.slice(0, 3) ?? [];
  const cliDir = (region.cli_3m_change ?? 0) > 0 ? '↑' : (region.cli_3m_change ?? 0) < 0 ? '↓' : '→';
  const cliDirColor = (region.cli_3m_change ?? 0) > 0 ? 'text-success' : (region.cli_3m_change ?? 0) < 0 ? 'text-error' : 'text-base-content/40';

  return (
    <div className={`rounded-2xl border ${cfg.border} ${cfg.bg} p-4 flex flex-col gap-3`}>

      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <span className="text-2xl leading-none">{region.flag}</span>
          <div>
            <div className="font-semibold text-sm leading-tight">{region.name}</div>
            {region.proxy_note && (
              <div className="text-[9px] text-base-content/35 italic">{region.proxy_note}</div>
            )}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${cfg.bg} ${cfg.textColor} border ${cfg.border}`}>
            {cfg.label}
          </span>
          {/* Source pill — always visible, tells user how reliable the phase is */}
          {region.phase_source === 'oecd_cli' && (
            <span className="text-[9px] font-semibold px-1.5 py-px rounded-full
              bg-success/10 text-success/80 border border-success/20 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-success/70 inline-block" />
              OECD CLI
            </span>
          )}
          {region.phase_source === 'imf_gdp_fallback' && (
            <span className="text-[9px] font-semibold px-1.5 py-px rounded-full
              bg-warning/10 text-warning/80 border border-warning/20 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-warning/70 inline-block" />
              GDP est.
            </span>
          )}
          {region.phase_source === 'default' && (
            <span className="text-[9px] font-semibold px-1.5 py-px rounded-full
              bg-base-content/5 text-base-content/30 border border-white/10 flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-base-content/20 inline-block" />
              No data
            </span>
          )}
          <div className="flex items-center gap-1">
            <MomentumIcon m={region.momentum} />
            <span className="text-[10px] text-base-content/40 capitalize">{region.momentum}</span>
          </div>
        </div>
      </div>

      {/* ── 4-phase position bar ── */}
      <div className="flex gap-1">
        {PHASE_ORDER.map(p => (
          <div key={p} className="h-1 flex-1 rounded-full"
            style={{ background: p === region.phase ? PHASE_CONFIG[p].color : 'rgba(255,255,255,0.07)' }} />
        ))}
      </div>

      {/* ── OECD CLI ── */}
      <div className="rounded-xl bg-white/[0.03] border border-white/[0.05] p-3 space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/35">OECD CLI</span>
        </div>
        {region.cli_current != null ? (
          <>
            <div className="flex items-center gap-3">
              <div>
                <span className="text-lg font-bold tabular-nums">{region.cli_current.toFixed(2)}</span>
                {region.cli_3m_change != null && (
                  <span className={`ml-1.5 text-xs font-semibold ${cliDirColor}`}>
                    {cliDir} {Math.abs(region.cli_3m_change).toFixed(3)} <span className="font-normal opacity-60">3M</span>
                  </span>
                )}
              </div>
              <div className="text-[10px] text-base-content/40 leading-tight">
                {region.cli_current >= 100
                  ? <span className="text-success/70">Above trend</span>
                  : <span className="text-error/70">Below trend</span>
                }
                {region.months_in_phase && (
                  <div>{region.months_in_phase}m in {cfg.label.toLowerCase()}</div>
                )}
              </div>
            </div>
            <CliSparkline series={region.cli_series} phase={region.phase} />
            <p className="text-[9px] text-base-content/25 leading-relaxed">
              100 = long-run trend · above + rising = expansion · below + falling = contraction
            </p>
          </>
        ) : (
          <p className="text-xs text-base-content/30 italic">
            {region.phase_source === 'imf_gdp_fallback'
              ? 'Phase derived from IMF GDP growth trajectory (OECD CLI unavailable)'
              : 'OECD CLI not available'}
          </p>
        )}
      </div>

      {/* ── IMF GDP Growth ── */}
      <div className="rounded-xl bg-white/[0.03] border border-white/[0.05] p-3 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/35">IMF GDP Growth</span>
          <span className="text-[9px] text-base-content/25">WEO</span>
        </div>
        {region.gdp_current_year != null ? (
          <>
            <div className="flex items-center gap-4">
              <div>
                <div className="text-[10px] text-base-content/40">{region.gdp_year} est.</div>
                <div className={`text-base font-bold tabular-nums ${region.gdp_current_year >= 0 ? 'text-success' : 'text-error'}`}>
                  {region.gdp_current_year > 0 ? '+' : ''}{region.gdp_current_year}%
                </div>
              </div>
              {region.gdp_next_year != null && (
                <div>
                  <div className="text-[10px] text-base-content/40">{region.gdp_year + 1} fcst</div>
                  <div className={`text-base font-bold tabular-nums ${region.gdp_next_year >= 0 ? 'text-success' : 'text-error'}`}>
                    {region.gdp_next_year > 0 ? '+' : ''}{region.gdp_next_year}%
                  </div>
                </div>
              )}
            </div>
            {region.gdp_series.length > 0 && (
              <GdpBars series={region.gdp_series} currentYear={region.gdp_year} />
            )}
          </>
        ) : (
          <p className="text-xs text-base-content/30 italic">IMF GDP data unavailable</p>
        )}
      </div>

      {/* ── LLM outlook + signals ── */}
      {region.outlook_headline && (
        <p className="text-xs text-base-content/70 leading-relaxed italic">"{region.outlook_headline}"</p>
      )}
      {region.key_signals.length > 0 && (
        <ul className="space-y-1">
          {region.key_signals.slice(0, 3).map((s, i) => (
            <li key={i} className="text-xs text-base-content/65 flex gap-1.5 items-start">
              <span className="mt-0.5 w-1.5 h-1.5 rounded-full shrink-0" style={{ background: cfg.color, opacity: 0.7 }} />
              {s}
            </li>
          ))}
        </ul>
      )}
      {region.key_signals.length === 0 && (
        <p className="text-xs text-base-content/30 italic">
          Add an OpenAI key in Settings for AI-generated signals.
        </p>
      )}

      {/* ── Top performers ── */}
      <div className="pt-1 border-t border-white/[0.05]">
        <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/30 mb-1.5">
          Outperformers in {cfg.label}
        </div>
        <div className="space-y-1">
          {top3.map(p => (
            <div key={p.category} className="flex items-center justify-between gap-2">
              <span className="text-xs text-base-content/65 truncate">{p.category}</span>
              <Stars n={p.stars} />
            </div>
          ))}
        </div>
      </div>

      {/* ── Expand/collapse ── */}
      <button onClick={() => setExpanded(x => !x)}
        className="flex items-center gap-1 text-[10px] text-base-content/35 hover:text-base-content/60 transition-colors self-start">
        {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        {expanded ? 'Less detail' : 'More detail'}
      </button>
      {expanded && (
        <div className="space-y-1 pt-1">
          <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/30 mb-1">
            All performers in {cfg.label}
          </div>
          {matrix[region.phase]?.performers.map(p => (
            <div key={p.category} className="flex items-center justify-between gap-2">
              <span className="text-xs text-base-content/60 truncate">{p.category}</span>
              <div className="flex items-center gap-2 shrink-0">
                <Stars n={p.stars} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Asset class performance matrix
// ---------------------------------------------------------------------------

function AssetMatrix({ matrix }: { matrix: BusinessCycleResponse['asset_matrix'] }) {
  const [open, setOpen] = useState(true);
  const allCats = Array.from(new Set(
    PHASE_ORDER.flatMap(p => matrix[p]?.performers.map(x => x.category) ?? [])
  ));
  const lookup = Object.fromEntries(
    allCats.map(cat => [cat, Object.fromEntries(
      PHASE_ORDER.map(p => [p, matrix[p]?.performers.find(x => x.category === cat)])
    )])
  );
  return (
    <div className="rounded-2xl border border-white/[0.06] bg-base-100/40 overflow-hidden">
      <button onClick={() => setOpen(x => !x)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-white/[0.02] transition-colors">
        <div className="flex items-center gap-2">
          <Star className="w-4 h-4 text-warning" />
          <span className="font-semibold text-sm">Asset Class Performance by Phase</span>
          <span className="text-xs text-base-content/35">(historical research-based)</span>
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-base-content/40" /> : <ChevronDown className="w-4 h-4 text-base-content/40" />}
      </button>
      {open && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-t border-white/[0.05]">
                <th className="px-4 py-2 text-left text-base-content/40 font-medium w-48">Asset Class</th>
                {PHASE_ORDER.map(p => (
                  <th key={p} className="px-3 py-2 text-center font-semibold" style={{ color: PHASE_CONFIG[p].color }}>
                    {PHASE_CONFIG[p].label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {allCats.map((cat, i) => (
                <tr key={cat} className={`border-t border-white/[0.03] ${i % 2 === 0 ? 'bg-white/[0.01]' : ''}`}>
                  <td className="px-4 py-2 text-base-content/70 font-medium">{cat}</td>
                  {PHASE_ORDER.map(p => {
                    const entry = lookup[cat][p];
                    return (
                      <td key={p} className="px-3 py-2 text-center">
                        {entry ? (
                          <div className="group relative inline-flex flex-col items-center">
                            <Stars n={entry.stars} />
                            {entry.note && (
                              <div className="absolute bottom-full mb-1.5 left-1/2 -translate-x-1/2 z-10
                                hidden group-hover:block w-52 rounded-lg bg-base-300/95 border border-white/10
                                px-2.5 py-2 text-left text-[10px] text-base-content/70 shadow-xl pointer-events-none">
                                {entry.note}
                              </div>
                            )}
                          </div>
                        ) : <span className="text-base-content/15">—</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-4 py-2 border-t border-white/[0.05]">
            <p className="text-[10px] text-base-content/30">★★★★★ = strongest historical outperformance · ★ = underperforms · hover for research note</p>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Phase legend
// ---------------------------------------------------------------------------

function PhaseLegend() {
  const descriptions: Record<CyclePhase, string> = {
    early:     'GDP bottoming, credit spreads tightening, PMI <50 but rising',
    mid:       'Sustained growth, employment rising, corporate profits strong',
    late:      'Growth decelerating, inflation elevated, yield curve flat/inverted',
    recession: 'GDP contracting, unemployment rising, PMI <50 and falling',
  };
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {PHASE_ORDER.map(phase => {
        const cfg = PHASE_CONFIG[phase];
        return (
          <div key={phase} className={`rounded-xl border ${cfg.border} ${cfg.bg} p-3`}>
            <div className={`text-xs font-bold mb-1 ${cfg.textColor}`}>{cfg.label}</div>
            <p className="text-[10px] text-base-content/45 leading-relaxed">{descriptions[phase]}</p>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Data source attribution
// ---------------------------------------------------------------------------

function Attribution({ sources }: { sources: BusinessCycleResponse['data_sources'] }) {
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] p-3 flex flex-wrap gap-3">
      <div className="flex items-start gap-1.5 text-[10px] text-base-content/40">
        <Database className="w-3 h-3 mt-0.5 shrink-0" />
        <div>
          <span className="font-semibold">Cycle phase: </span>{sources.cycle_phase}
        </div>
      </div>
      <div className="flex items-start gap-1.5 text-[10px] text-base-content/40">
        <Database className="w-3 h-3 mt-0.5 shrink-0" />
        <div>
          <span className="font-semibold">GDP growth: </span>{sources.gdp}
        </div>
      </div>
      <div className="flex items-start gap-1.5 text-[10px] text-base-content/40">
        <Sparkles className="w-3 h-3 mt-0.5 shrink-0" />
        <div>{sources.signals}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function EconomicCycles() {
  const [data, setData]         = useState<BusinessCycleResponse | null>(null);
  const [loading, setLoading]   = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError]       = useState<string | null>(null);

  const load = async () => {
    setLoading(true); setError(null);
    try   { setData(await fetchBusinessCycle()); }
    catch (e: any) { setError(e?.message || 'Failed to load business cycle data'); }
    finally { setLoading(false); }
  };

  const doRefresh = async () => {
    setRefreshing(true); setError(null);
    try   { setData(await refreshBusinessCycle()); }
    catch (e: any) { setError(e?.message || 'Refresh failed'); }
    finally { setRefreshing(false); }
  };

  useEffect(() => { load(); }, []);

  const phaseSummary = data
    ? PHASE_ORDER
        .map(p => ({ phase: p, regions: data.regions.filter(r => r.phase === p) }))
        .filter(x => x.regions.length > 0)
    : [];

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">Global Business Cycle</h2>
          {data && (
            <p className="text-xs text-base-content/40 mt-0.5">
              As of {data.as_of}
              {data.generated_with_llm
                ? <span className="ml-1.5 inline-flex items-center gap-0.5 text-primary"><Sparkles className="w-3 h-3" /> AI signals active</span>
                : <span className="ml-1.5 text-base-content/25">· Add OpenAI key for AI signals</span>
              }
            </p>
          )}
        </div>
        <button onClick={doRefresh} disabled={refreshing || loading} className="btn btn-sm btn-ghost gap-1.5">
          <RefreshCw className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="alert alert-error rounded-2xl">
          <AlertCircle className="w-4 h-4" />
          <span className="text-sm">{error}</span>
          <button onClick={load} className="btn btn-xs btn-ghost gap-1"><RefreshCw className="w-3 h-3" /> Retry</button>
        </div>
      )}

      {loading && !data && (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-base-content/40">
          <Loader2 className="w-6 h-6 animate-spin" />
          <div className="text-center">
            <p className="text-sm">Fetching OECD CLI + IMF GDP data…</p>
            <p className="text-xs mt-1 text-base-content/25">First load may take 5–10 s (external APIs)</p>
          </div>
        </div>
      )}

      {data && (
        <>
          {/* OECD unavailability notice */}
          {!data.oecd_available && (
            <div className="rounded-xl border border-warning/25 bg-warning/5 p-3 flex gap-2.5 text-xs">
              <AlertCircle className="w-4 h-4 text-warning/70 shrink-0 mt-0.5" />
              <div className="text-base-content/60 leading-relaxed">
                <span className="font-semibold text-warning/80">OECD CLI unavailable</span> — phase classification is using the IMF GDP growth trajectory as a fallback.
                This is less precise than CLI (annual vs monthly data, lags real turning points).
                The OECD Data API may be temporarily down; try refreshing later for full data.
              </div>
            </div>
          )}

          {/* Clock + legend */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="rounded-2xl border border-white/[0.06] bg-base-100/40 p-5 flex flex-col items-center gap-4">
              <CycleClock regions={data.regions} />
              <div className="w-full space-y-1.5">
                {phaseSummary.map(({ phase, regions }) => {
                  const cfg = PHASE_CONFIG[phase];
                  return (
                    <div key={phase} className="flex items-center gap-2 text-xs">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: cfg.color }} />
                      <span className={`font-medium ${cfg.textColor} w-20 shrink-0`}>{cfg.label}</span>
                      <span className="text-base-content/45 flex gap-1 flex-wrap">
                        {regions.map(r => <span key={r.id}>{r.flag} {r.name.split(' ')[0]}</span>)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="lg:col-span-2 space-y-4">
              <PhaseLegend />
              <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] p-3 flex gap-2.5">
                <Info className="w-4 h-4 text-base-content/30 shrink-0 mt-0.5" />
                <p className="text-[11px] text-base-content/45 leading-relaxed">
                  Phase classification uses the <strong className="text-base-content/60">OECD Composite Leading Indicator (CLI)</strong>, amplitude-adjusted
                  and trend-restored to 100. The CLI is designed to signal cycle turning points 6–9 months ahead.
                  GDP growth from the <strong className="text-base-content/60">IMF World Economic Outlook</strong>.
                  Phases are categorical — position within the cycle wheel shows direction, not exact timing.
                </p>
              </div>
              <Attribution sources={data.data_sources} />
            </div>
          </div>

          {/* Region cards */}
          <div>
            <div className="text-[10px] font-bold uppercase tracking-widest text-base-content/30 mb-3">
              Current Phase by Region — OECD CLI + IMF GDP
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {data.regions.map(region => (
                <RegionCard key={region.id} region={region} matrix={data.asset_matrix} />
              ))}
            </div>
          </div>

          {/* Asset matrix */}
          <AssetMatrix matrix={data.asset_matrix} />

          {/* US Cycle Sector Technical Analysis */}
          {(() => {
            const usRegion = data.regions.find(r => r.id === 'usa');
            if (!usRegion) return null;
            return (
              <div className="rounded-2xl border border-white/[0.07] bg-base-100/40 p-5">
                <CycleSectorTA usPhase={usRegion.phase} />
              </div>
            );
          })()}
        </>
      )}
    </div>
  );
}
