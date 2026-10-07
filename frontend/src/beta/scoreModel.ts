/**
 * scoreModel.ts — turns a ranked desk trade into the numbers the Beta score card shows.
 *
 * Pure (no React): the SAME fields the classic Quant Analysis panel reads (base_quality, grade_adjustments,
 * ta_factors, desk_score, per-lens detail), only summarised into a build-up that reconciles exactly:
 *
 *     base + Σ option-math factors + Σ technical factors = raw        (raw may exceed 0..100)
 *     desk_score = raw clamped to [0, 100]                            (the cap is stated, never hidden)
 */
import type { DeskRankedTrade } from '../types';

export interface ScoreFactor { label: string; points: number; detail?: string; dimension?: string | null }
export interface ScoreLens { label: string; score: number; weight?: number; contribution?: number; note?: string }

export interface ScoreModel {
  grade: string;
  base: number;
  optNet: number;               // Σ option-math factor points
  taNet: number;                // Σ technical / regime factor points
  raw: number;                  // base + optNet + taNet
  final: number;                // the desk score actually shown
  clamped: 'cap' | 'floor' | null;
  vetoed: boolean;
  waiting: boolean;             // timing hold: the quality letter stands, the desk is waiting
  lenses: ScoreLens[];
  boosts: ScoreFactor[];        // top 3 positive contributors
  drags: ScoreFactor[];         // top 3 negative contributors
  dims: { dim: string; net: number }[];
  vetoReasons: string[];
  waitReasons: string[];
}

const r1 = (n: number) => Math.round(n * 10) / 10;

export function buildScore(t: DeskRankedTrade, dimOrder: string[]): ScoreModel {
  const adjs: ScoreFactor[] = (t.grade_adjustments || []) as ScoreFactor[];
  const tas: ScoreFactor[] = (t.ta_factors || []) as ScoreFactor[];
  const q: any = t.desk_metrics?.quant || {};
  const base = Math.round(t.base_quality ?? q.score ?? 0);
  const optNet = r1(adjs.reduce((s, a) => s + a.points, 0));
  const taNet = r1(tas.reduce((s, a) => s + a.points, 0));
  const raw = r1(base + optNet + taNet);
  const final = t.desk_score;
  const clamped = Math.abs(raw - final) > 0.5 ? (raw > final ? 'cap' : 'floor') : null;

  const all = [...adjs, ...tas].filter(f => f.points !== 0);
  const boosts = all.filter(f => f.points > 0).sort((a, b) => b.points - a.points).slice(0, 3);
  const drags = all.filter(f => f.points < 0).sort((a, b) => a.points - b.points).slice(0, 3);

  const byDim = new Map<string, number>();
  for (const f of [...adjs, ...tas]) {
    const d = f.dimension || 'Other';
    byDim.set(d, r1((byDim.get(d) ?? 0) + f.points));
  }
  const dims = [...dimOrder, 'Other'].filter(d => byDim.has(d)).map(d => ({ dim: d, net: byDim.get(d) as number }));

  const lenses: ScoreLens[] = Array.isArray(q.lenses) ? q.lenses.map((l: any) => ({
    label: String(l.label), score: Number(l.score), weight: l.weight, contribution: l.contribution, note: l.note,
  })) : [];

  const vetoReasons = t.grade_blocking || [];
  const waitReasons = t.grade_timing_hold || [];
  return {
    grade: t.algo_grade || '—', base, optNet, taNet, raw, final, clamped,
    vetoed: vetoReasons.length > 0, waiting: vetoReasons.length === 0 && waitReasons.length > 0,
    lenses, boosts, drags, dims, vetoReasons, waitReasons,
  };
}
