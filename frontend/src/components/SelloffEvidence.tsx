import React, { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { BookOpen, ChevronDown, ChevronUp, ExternalLink, FlaskConical, Loader2, X } from 'lucide-react';
import { fetchSelloffEvidenceReport } from '../api';
import type { EvidenceGrade, SelloffEvidence, SelloffEvidenceItem } from '../types';

// "What the evidence says" — the graded research behind the Selloff Monitor. The grades come from the SAME
// backend registry that grades every tell, so the panel and the chips can never disagree.

const COLUMNS: { grade: EvidenceGrade; label: string; head: string; dot: string }[] = [
  { grade: 'strong', label: 'Strong', head: 'text-success', dot: 'bg-success' },
  { grade: 'moderate', label: 'Moderate', head: 'text-info', dot: 'bg-info' },
  { grade: 'weak', label: 'Weak', head: 'text-warning', dot: 'bg-warning' },
  { grade: 'folklore', label: 'Folklore', head: 'text-base-content/55', dot: 'bg-base-content/40' },
];

const BADGE: Record<string, [string, string]> = {
  skill: ['badge-success', 'has beaten the base rate'],
  no_skill: ['badge-ghost', 'no demonstrated skill vs the base rate'],
  worse: ['badge-error', 'has done worse than the base rate'],
  insufficient: ['badge-ghost', 'not enough history to score'],
};

const pct1 = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v * 100).toFixed(1)}%`);

function Item({ it }: { it: SelloffEvidenceItem }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-t border-white/[0.05] first:border-t-0">
      <button className="w-full text-left py-1.5 group" onClick={() => setOpen(v => !v)} aria-expanded={open}>
        <div className="flex items-start gap-1">
          <span className="text-[11.5px] font-medium text-base-content/85 leading-snug flex-1">{it.signal}</span>
          {open ? <ChevronUp className="w-3 h-3 mt-0.5 text-base-content/30 shrink-0" /> : <ChevronDown className="w-3 h-3 mt-0.5 text-base-content/30 shrink-0 group-hover:text-base-content/60" />}
        </div>
        <div className={`text-[10.5px] text-base-content/55 leading-snug ${open ? '' : 'line-clamp-2'}`}>{it.finding}</div>
      </button>
      {open && (
        <div className="pb-2 space-y-1">
          <div className="text-[10px] text-base-content/60"><span className="text-base-content/40">On this page:</span> {it.on_page}</div>
          {it.sources.length > 0 && (
            <div className="flex flex-wrap gap-x-2 gap-y-0.5">
              {it.sources.map(s => (
                <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer"
                  className="text-[10px] text-primary/80 hover:text-primary inline-flex items-center gap-0.5">
                  {s.label}<ExternalLink className="w-2.5 h-2.5" />
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function ReportReader({ onClose }: { onClose: () => void }) {
  const [md, setMd] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetchSelloffEvidenceReport()
      .then(r => setMd(r.markdown))
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : 'Could not load the report'));
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 p-3 sm:p-6 overflow-y-auto" onClick={onClose} role="dialog" aria-modal="true" aria-label="Research report">
      <div className="relative w-full max-w-4xl rounded-2xl border border-white/10 bg-base-100 shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-center justify-between gap-2 rounded-t-2xl border-b border-white/10 bg-base-100/95 px-4 py-2 backdrop-blur">
          <span className="text-[11px] uppercase tracking-wide text-base-content/50 inline-flex items-center gap-1.5"><BookOpen className="w-3.5 h-3.5" /> Research report</span>
          <button className="btn btn-xs btn-ghost" onClick={onClose} aria-label="Close report"><X className="w-4 h-4" /></button>
        </div>
        <div className="px-4 sm:px-8 py-5">
          {!md && !err && <div className="flex items-center gap-2 text-sm text-base-content/50 py-10 justify-center"><Loader2 className="w-4 h-4 animate-spin" /> Loading the report…</div>}
          {err && <div className="alert alert-error text-sm">{err}</div>}
          {md && (
            <div className="prose prose-sm max-w-none prose-headings:text-base-content prose-p:text-base-content/75 prose-strong:text-base-content prose-li:text-base-content/75 prose-a:text-primary prose-th:text-base-content prose-td:text-base-content/70 prose-table:text-[11px] prose-td:align-top">
              <ReactMarkdown remarkPlugins={[remarkGfm]}
                components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> }}>
                {md}
              </ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function SelloffEvidencePanel({ ev }: { ev: SelloffEvidence }) {
  const [reading, setReading] = useState(false);
  const close = useCallback(() => setReading(false), []);
  const own = ev.own_test;
  return (
    <div className="rounded-xl border border-white/[0.08] bg-base-100/30 p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          <BookOpen className="w-4 h-4 text-primary mt-0.5" />
          <div>
            <h3 className="text-sm font-semibold">What the evidence says</h3>
            <p className="text-[11px] text-base-content/45">How much to trust each signal on this page, graded from a review of the research. {ev.report.sources_note}</p>
          </div>
        </div>
        {ev.report.available && (
          <button className="btn btn-xs btn-primary btn-outline gap-1.5" onClick={() => setReading(true)}>
            <BookOpen className="w-3.5 h-3.5" /> Read the full report
          </button>
        )}
      </div>

      <div className="rounded-lg border border-primary/20 bg-primary/[0.05] px-3 py-2 text-[12px] text-base-content/80 leading-relaxed">
        <span className="font-semibold text-primary">Bottom line: </span>{ev.bottom_line}
      </div>

      {own && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-base-content/65">
          <span className="inline-flex items-center gap-1 text-[9px] uppercase tracking-wide text-base-content/40"><FlaskConical className="w-3 h-3" /> This page's own test</span>
          <span>The history odds of up vs down</span>
          <span className={`badge badge-sm ${BADGE[own.badge][0]}`}>{BADGE[own.badge][1]}</span>
          <span className="tabular-nums text-base-content/50">
            Brier skill {pct1(own.bss)}{own.lo != null && own.hi != null && ` (95% CI ${pct1(own.lo)} to ${pct1(own.hi)})`} on {own.n} selloff days, {own.evaluated_on}.
            {own.analog_fan_coverage != null && own.vix_cone_coverage != null &&
              ` Ranges: the raw history fan held ${own.analog_fan_coverage.toFixed(0)}% of outcomes and a VIX-implied cone ${own.vix_cone_coverage.toFixed(0)}% (target 80%).`}
          </span>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        {COLUMNS.map(col => {
          const items = ev.items.filter(i => i.grade === col.grade);
          return (
            <div key={col.grade} className="rounded-lg border border-white/[0.06] bg-base-200/25 px-3 py-2">
              <div className={`flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide mb-1 ${col.head}`} title={ev.grades[col.grade]}>
                <span className={`w-1.5 h-1.5 rounded-full ${col.dot}`} />{col.label}
                <span className="font-normal normal-case tracking-normal text-base-content/35">· {items.length}</span>
              </div>
              <div className="text-[9.5px] text-base-content/35 leading-snug mb-1">{ev.grades[col.grade]}</div>
              <ul>{items.map(it => <Item key={it.id} it={it} />)}</ul>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-base-content/35">Click a signal for what this page does with it and its sources. The evidence chip on every tell below uses these same grades.</p>
      {reading && <ReportReader onClose={close} />}
    </div>
  );
}
