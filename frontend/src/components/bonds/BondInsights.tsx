import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Bot, Lightbulb, Loader2 } from 'lucide-react';
import { askBondAdvisor } from '../../api';
import type { BondPortfolio, BondRecommendation } from '../../types';
import { Card, Empty, ErrorBox, Note, Seg, SeverityBadge, usd } from './bondUi';

const CATS: { value: string; label: string }[] = [
  { value: 'all', label: 'All' }, { value: 'tax', label: 'Tax' }, { value: 'risk', label: 'Risk' },
  { value: 'income', label: 'Income' }, { value: 'ladder', label: 'Ladder' }, { value: 'cost', label: 'Cost' },
  { value: 'market', label: 'Market' }, { value: 'data', label: 'Data' },
];

export default function BondInsights({ data, onGotoHolding }: { data: BondPortfolio; onGotoHolding: () => void }) {
  const [cat, setCat] = useState('all');
  const [ai, setAi] = useState<{ markdown: string; model: string } | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiErr, setAiErr] = useState<string | null>(null);
  const byId = useMemo(() => Object.fromEntries([...data.holdings, ...data.watchlist].map(h => [h.id, h.label])), [data]);
  const recs = data.recommendations.filter(r => cat === 'all' || r.category === cat);
  const totalImpact = data.recommendations
    .filter(r => r.impact_usd && ['tax', 'income', 'cost'].includes(r.category))
    .reduce((a, r) => a + (r.impact_usd ?? 0), 0);

  const ask = async () => {
    setAiLoading(true); setAiErr(null);
    try { setAi(await askBondAdvisor()); } catch (e) { setAiErr(e instanceof Error ? e.message : 'AI advisor failed'); } finally { setAiLoading(false); }
  };

  const counts = (c: string) => (c === 'all' ? data.recommendations.length : data.recommendations.filter(r => r.category === c).length);

  return (
    <div className="grid gap-4 xl:grid-cols-5">
      <Card className="xl:col-span-3" icon={<Lightbulb className="h-4 w-4 text-warning" />} title="Recommendations"
        subtitle={totalImpact > 0
          ? `Deterministic checks on your book — acting on the tax/income/cost items is worth ~${usd(totalImpact, { compact: true })}/yr`
          : 'Deterministic checks on your book — every number is computed from your holdings and live market data'}
        right={<Seg value={cat} onChange={setCat} options={CATS.filter(c => counts(c.value) > 0 || c.value === 'all').map(c => ({ value: c.value, label: `${c.label} ${counts(c.value)}` }))} />}>
        {recs.length ? (
          <ul className="space-y-2">
            {recs.map((r: BondRecommendation) => (
              <li key={r.id} className="rounded-xl border border-white/[0.05] bg-base-200/40 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <SeverityBadge severity={r.severity} />
                    <span className="text-[10px] uppercase tracking-wide text-base-content/40">{r.category}</span>
                  </div>
                  {r.impact_usd ? (
                    <span className="text-[11px] font-semibold tabular-nums text-base-content/70">
                      ~{usd(r.impact_usd, { compact: true })}{['tax', 'income', 'cost'].includes(r.category) ? '/yr' : ''}
                    </span>
                  ) : null}
                </div>
                <div className="mt-1.5 text-sm font-medium text-base-content/90">{r.title}</div>
                <p className="mt-1 text-xs leading-relaxed text-base-content/60">{r.detail}</p>
                {r.holding_ids.length > 0 && (
                  <button onClick={onGotoHolding} className="mt-1.5 text-[10px] text-primary/80 hover:text-primary">
                    {r.holding_ids.map(id => byId[id]).filter(Boolean).join(' · ')} →
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : <Empty title="Nothing flagged here" body="Your book passes every check in this category." />}
      </Card>

      <Card className="xl:col-span-2" icon={<Bot className="h-4 w-4 text-secondary" />} title="AI bond advisor"
        subtitle="A plain-English review of your book, grounded ONLY on the computed numbers (uses your own API key)"
        right={<button className="btn btn-sm btn-secondary" onClick={ask} disabled={aiLoading}>
          {aiLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Bot className="h-3.5 w-3.5" />} {ai ? 'Refresh' : 'Review my book'}
        </button>}>
        {aiErr && <ErrorBox message={aiErr} />}
        {ai ? (
          <div className="prose prose-sm prose-invert max-w-none text-[13px] prose-headings:text-sm prose-p:my-1.5 prose-li:my-0.5">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{ai.markdown}</ReactMarkdown>
            <p className="mt-3 text-[10px] text-base-content/35">{ai.model} · education, not individualized advice</p>
          </div>
        ) : !aiLoading && (
          <div className="space-y-2 py-4">
            <Note>The advisor sees your summary, allocation, scenarios, recommendations, holdings and today's curve — nothing else — and is told never to invent a number.</Note>
            <Note>Runs only when you click; nothing is sent automatically.</Note>
          </div>
        )}
      </Card>
    </div>
  );
}
