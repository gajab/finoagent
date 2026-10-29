/** Which desk factors belong in the GRADE build-up. Info-only reads (direction, systemic beta, the volume node at the
 *  strike, VRP) and factors that landed on 0 pts are context, shown in "Context · not scored" instead. Breach risk is
 *  THE hold metric, so it stays in the grade even at 0 pts (a benign breach read is reassuring). */
export type GradeFactor = { label: string; points: number; detail?: string; info_only?: boolean; baseline_points?: number };

/** A 0 that an adjustment PRODUCED (earnings-aware with/without, e.g. Structure credit cancelled by the event gap)
 *  is part of the grade's story, so it stays too. */
export const isGraded = (f: GradeFactor): boolean =>
  !f.info_only && (f.points !== 0 || f.label === 'Breach risk' || f.baseline_points != null);
