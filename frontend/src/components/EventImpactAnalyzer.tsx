/**
 * EventImpactAnalyzer — analyze the historical market fallout of a specific
 * event (e.g. a tweet, Fed statement, earnings print).
 *
 * Extracted from the former MarketImpact monolith. Uses yfinance historical
 * bars + LLM context analysis to produce a "what moved, why, and by how much"
 * dashboard: sector sentiment badges, ticker-level price/volume charts, and
 * macro index reaction.
 *
 * Endpoint: POST /api/stock/market/impact
 */

import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { apiBase } from '../api';
import {
    CalendarClock, Zap, LineChart as LineChartIcon,
    LayoutDashboard, Globe, Layers,
} from 'lucide-react';
import {
    Chart as ChartJS, CategoryScale, LinearScale, PointElement,
    LineElement, BarElement, Title, Tooltip, Legend,
} from 'chart.js';
import { Line } from 'react-chartjs-2';

ChartJS.register(
    CategoryScale, LinearScale, PointElement, LineElement, BarElement, Title, Tooltip, Legend,
);

interface ChartData {
    labels: string[];
    prices: number[];
    volumes: number[];
    opens?: number[];
    previous_close?: number;
}

interface MarketImpactResult {
    llm_analysis: {
        historical_context: string;
        top_sectors: string[];
        index_analysis: string;
        sentiments?: {
            sectors: Record<string, string>;
            stocks: Record<string, string>;
            etfs: Record<string, string>;
            indices: Record<string, string>;
        };
    };
    targets: { stocks: string[]; etfs: string[]; indices: string[] };
    chart_data: Record<string, ChartData>;
    metadata: { interval_used: string; start_tracked: string; event_datetime: string };
}

/** Dual-axis (price + volume) chart with sentiment-tinted border. */
const ImpactChart = ({ title, data, sentiment }: { title: string; data: ChartData; sentiment?: string }) => {
    if (!data || !data.labels || data.labels.length === 0) {
        return <div className="p-4 border border-base-200 rounded-xl bg-base-100 flex items-center justify-center opacity-50 h-48">{title} Data Unavailable</div>;
    }

    const formattedLabels = data.labels.map(lbl => {
        if (lbl.includes('T')) {
            const d = new Date(lbl);
            if (!isNaN(d.getTime())) {
                return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' });
            }
        }
        return lbl;
    });

    const volumeColors = data.volumes.map((_, i) => {
        if (data.opens && data.opens.length > i) {
            return data.prices[i] >= data.opens[i] ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)';
        }
        if (i === 0) return 'rgba(34, 197, 94, 0.5)';
        return data.prices[i] >= data.prices[i - 1] ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)';
    });

    const chartData = {
        labels: formattedLabels,
        datasets: [
            { type: 'line' as const, label: 'Price', data: data.prices, borderColor: '#3b82f6', backgroundColor: '#3b82f6', borderWidth: 2, pointRadius: 0, yAxisID: 'y' },
            { type: 'bar' as const, label: 'Volume', data: data.volumes, backgroundColor: volumeColors, yAxisID: 'y1' },
        ],
    };

    const options = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index' as const, intersect: false },
        plugins: {
            legend: { display: false },
            tooltip: {
                callbacks: {
                    label: (context: any) =>
                        context.dataset.label === 'Price'
                            ? `Price: $${context.parsed.y.toFixed(2)}`
                            : `Vol: ${context.parsed.y.toLocaleString()}`,
                },
            },
        },
        scales: {
            y: { type: 'linear' as const, display: true, position: 'left' as const, grid: { display: false } },
            y1: { type: 'linear' as const, display: false, position: 'right' as const, grid: { display: false } },
            x: { ticks: { maxTicksLimit: 5 }, grid: { display: false } },
        },
    };

    const sentimentColor = sentiment === 'positive' ? 'text-success'
        : sentiment === 'negative' ? 'text-error'
        : sentiment === 'unknown' ? 'text-warning'
        : 'text-base-content';
    const sentimentBorder = sentiment === 'positive' ? 'border-success/30 hover:border-success/60 bg-success/5'
        : sentiment === 'negative' ? 'border-error/30 hover:border-error/60 bg-error/5'
        : sentiment === 'unknown' ? 'border-warning/30 hover:border-warning/60 bg-warning/5'
        : 'border-base-200 bg-base-100/50 hover:border-primary/30';

    let pctStr = '0.00%', pctColor = 'text-base-content';
    if (data.prices && data.prices.length > 0) {
        const first = data.prices[0];
        const last = data.prices[data.prices.length - 1];
        const base = (data.previous_close && data.previous_close > 0) ? data.previous_close : first;
        if (base > 0) {
            const pct = ((last - base) / base) * 100;
            pctStr = (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%';
            pctColor = pct >= 0 ? 'text-success' : 'text-error';
        }
    }

    return (
        <div className={`border p-3 rounded-xl shadow-sm transition-colors ${sentimentBorder}`}>
            <h4 className={`font-bold text-sm mb-2 opacity-80 ${sentimentColor}`}>
                <Link to={`/dashboard?ticker=${title}`} className="hover:underline">{title} ↗</Link>
                <span className={`ml-2 font-mono ${pctColor}`}>{pctStr}</span>
            </h4>
            <div className="h-40 w-full">
                {/* @ts-ignore */}
                <Line data={chartData} options={options} />
            </div>
        </div>
    );
};

export default function EventImpactAnalyzer() {
    const [eventText, setEventText] = useState('');
    const [durationType, setDurationType] = useState<'relative' | 'exact'>('relative');
    const [durationStr, setDurationStr] = useState('today');
    const [exactDateTime, setExactDateTime] = useState('');
    const [result, setResult] = useState<MarketImpactResult | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const analyze = async () => {
        if (!eventText.trim() || eventText.length < 5) {
            setError('Please provide a substantive event description to analyze.');
            return;
        }
        setLoading(true);
        setError(null);
        setResult(null);

        try {
            const res = await fetch(`${apiBase}/api/stock/market/impact`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${localStorage.getItem('token')}`,
                },
                body: JSON.stringify({
                    event_text: eventText,
                    duration_str: durationType === 'relative' ? durationStr : '',
                    exact_datetime: durationType === 'exact' ? exactDateTime : '',
                }),
            });

            if (!res.ok) {
                const err = await res.json();
                let msg = 'Failed to analyze event';
                if (err.detail) msg = typeof err.detail === 'string' ? err.detail : err.detail[0]?.msg || msg;
                throw new Error(msg);
            }
            setResult(await res.json());
        } catch (err: any) {
            if (err.message?.includes('OpenAI')) {
                setError('OpenAI API Key is missing or invalid. Please configure it in Settings.');
            } else {
                setError(err.message || 'An unexpected error occurred');
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            {/* Intro */}
            <div className="bg-gradient-to-r from-primary/10 to-transparent p-5 rounded-2xl border border-primary/20">
                <div className="flex items-start gap-3">
                    <div className="bg-primary/20 p-2.5 rounded-xl shrink-0">
                        <LineChartIcon className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                        <h2 className="text-lg font-bold">Event Impact Analyzer</h2>
                        <p className="opacity-70 text-sm mt-1">
                            Paste a tweet, Fed statement, or news event to analyze its immediate market fallout.
                            The AI identifies affected sectors and maps price/volume reactions across specific tickers.
                        </p>
                    </div>
                </div>
            </div>

            {/* Input panel */}
            <div className="glass-card">
                <div className="p-6">
                    <div className="form-control w-full">
                        <label className="label">
                            <span className="label-text font-bold text-base flex items-center gap-2">
                                <Globe className="w-4 h-4 text-info" /> The Event
                            </span>
                        </label>
                        <textarea
                            className="textarea textarea-bordered h-24 font-serif text-base"
                            placeholder='e.g., "The Federal Reserve just unexpectedly hiked rates by 50bps." or paste a tweet.'
                            value={eventText}
                            onChange={(e) => setEventText(e.target.value)}
                        />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-2">
                        <div className="space-y-2">
                            <label className="label cursor-pointer justify-start gap-3">
                                <input
                                    type="radio"
                                    name="timeframeType"
                                    className="radio radio-sm radio-primary"
                                    checked={durationType === 'relative'}
                                    onChange={() => setDurationType('relative')}
                                />
                                <span className="label-text font-semibold flex items-center gap-2">
                                    <LineChartIcon className="w-4 h-4 opacity-70" /> Relative Window
                                </span>
                            </label>
                            <select
                                className="select select-bordered w-full"
                                value={durationStr}
                                onChange={(e) => setDurationStr(e.target.value)}
                                disabled={durationType !== 'relative'}
                            >
                                <option value="today">Today (2m intervals)</option>
                                <option value="1hr">Last 1 Hour (2m intervals)</option>
                                <option value="1day">Last 1 Day (5m intervals)</option>
                                <option value="1week">Last 1 Week (1h intervals)</option>
                                <option value="1month">Last 1 Month (1d intervals)</option>
                            </select>
                        </div>

                        <div className="space-y-2">
                            <label className="label cursor-pointer justify-start gap-3">
                                <input
                                    type="radio"
                                    name="timeframeType"
                                    className="radio radio-sm radio-secondary"
                                    checked={durationType === 'exact'}
                                    onChange={() => setDurationType('exact')}
                                />
                                <span className="label-text font-semibold flex items-center gap-2">
                                    <CalendarClock className="w-4 h-4 opacity-70" /> Explicit Target
                                </span>
                            </label>
                            <input
                                type="datetime-local"
                                className="input input-bordered w-full"
                                value={exactDateTime}
                                onChange={(e) => setExactDateTime(e.target.value)}
                                disabled={durationType !== 'exact'}
                            />
                        </div>
                    </div>

                    <div className="mt-6 flex justify-end">
                        <button className="btn btn-primary" onClick={analyze} disabled={loading}>
                            {loading
                                ? <><span className="loading loading-spinner"></span> Synthesizing…</>
                                : <><Zap className="w-5 h-5" /> Execute Analysis</>}
                        </button>
                    </div>

                    {error && (
                        <div className="alert alert-error mt-4 text-sm">
                            <span className="font-bold">Error:</span> {error}
                        </div>
                    )}
                </div>
            </div>

            {/* Results */}
            {result && (
                <div className="space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500 mt-4">
                    {/* Context panel */}
                    <div className="glass-card border-l-4 border-l-secondary">
                        <div className="p-6">
                            <h3 className="font-bold text-sm flex items-center gap-2 mb-2">
                                <LayoutDashboard className="w-5 h-5 text-secondary" />
                                Fundamental Context &amp; Macro Setup
                            </h3>
                            <p className="text-sm opacity-90 leading-relaxed font-serif">
                                {result.llm_analysis.historical_context}
                            </p>
                            <div className="mt-4 pt-4 border-t border-base-300">
                                <h4 className="text-xs font-bold uppercase tracking-wider text-base-content/70 mb-2">Primary Affected Sectors</h4>
                                <div className="flex flex-wrap gap-2">
                                    {result.llm_analysis.top_sectors.map((sec, idx) => {
                                        const sentiment = result.llm_analysis.sentiments?.sectors?.[sec];
                                        const badgeClass = sentiment === 'positive' ? 'badge-success bg-success/10 text-success border-success/30'
                                            : sentiment === 'negative' ? 'badge-error bg-error/10 text-error border-error/30'
                                            : sentiment === 'unknown' ? 'badge-warning bg-warning/10 text-warning border-warning/30'
                                            : 'badge-outline text-base-content border-primary/50';
                                        return (
                                            <span key={idx} className={`badge text-xs py-3 font-semibold ${badgeClass}`}>
                                                {sec} {sentiment && `(${sentiment})`}
                                            </span>
                                        );
                                    })}
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Stock charts */}
                    <div>
                        <h3 className="text-xl font-bold flex items-center gap-2 mb-4 border-b border-base-200 pb-2">
                            <Zap className="w-5 h-5 text-warning" />
                            Direct Casualties &amp; Beneficiaries (Top Stocks)
                        </h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {result.targets.stocks.map(ticker => (
                                <ImpactChart
                                    key={ticker}
                                    title={ticker}
                                    data={result.chart_data[ticker]}
                                    sentiment={result.llm_analysis.sentiments?.stocks?.[ticker]}
                                />
                            ))}
                        </div>
                    </div>

                    {/* ETF charts */}
                    <div>
                        <h3 className="text-xl font-bold flex items-center gap-2 mb-4 border-b border-base-200 pb-2">
                            <Layers className="w-5 h-5 text-success" />
                            Broad Exposure (Top ETFs)
                        </h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                            {result.targets.etfs.map(ticker => (
                                <ImpactChart
                                    key={ticker}
                                    title={ticker}
                                    data={result.chart_data[ticker]}
                                    sentiment={result.llm_analysis.sentiments?.etfs?.[ticker]}
                                />
                            ))}
                        </div>
                    </div>

                    {/* Indices */}
                    <div>
                        <h3 className="text-xl font-bold flex items-center gap-2 mb-4 border-b border-base-200 pb-2">
                            <LineChartIcon className="w-5 h-5 text-info" />
                            Macro Index Reaction
                        </h3>
                        <div className="bg-info/10 p-4 rounded-xl border border-info/20 mb-6 font-serif text-sm opacity-90">
                            {result.llm_analysis.index_analysis}
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {result.targets.indices.map(ticker => (
                                <ImpactChart
                                    key={ticker}
                                    title={ticker}
                                    data={result.chart_data[ticker]}
                                    sentiment={result.llm_analysis.sentiments?.indices?.[ticker]}
                                />
                            ))}
                        </div>
                    </div>

                    <div className="text-right text-xs font-mono opacity-50">
                        {result.targets.stocks.length + result.targets.etfs.length + result.targets.indices.length} assets mapped · Interval: {result.metadata.interval_used}
                    </div>
                </div>
            )}
        </div>
    );
}
