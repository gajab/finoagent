/**
 * rollMath.ts — the decision aids shown while filling in a ROLL (buy back one leg, sell/buy a replacement).
 * One implementation, used by BOTH the classic card's roll form and the Beta's, so the numbers a user acts on
 * can never differ between the two screens. Pure (no React). All money figures are in dollars, per position.
 *
 * Conventions (same as the backend's roll-as-extension model):
 *   - a SHORT leg's entry is a credit (+), a LONG leg's entry is a debit (−)
 *   - roll realized folds into the campaign's cost basis: `priorRoll` is what earlier rolls already banked
 *   - `targetCredit` = what the NEW leg must collect to leave the whole campaign at break-even:
 *         buy-back cost − (original entry credit + prior roll realized)
 */
export interface RollPreviewInput {
  oldIsShort: boolean;      // is the leg being rolled a short leg?
  oldQty: number;           // contracts on the old leg
  oldEntry: number;         // the old leg's entry price, per share
  buyback: number;          // price to close the old leg at, per share (NaN = not entered yet)
  newAction: 'buy' | 'sell';
  newPremium: number;       // premium on the replacement leg, per share (NaN = not entered yet)
  newQty: number;
  priorRoll: number;        // roll realized banked by earlier rolls of this campaign ($)
  priorCount: number;       // how many earlier rolls
}

export interface RollPreview {
  hasAny: boolean;                 // false until the user has typed a buy-back price or a new premium
  realizedClose: number | null;    // realized on the buy-back ($)
  buybackCost: number | null;      // cash paid to close ($)
  newCredit: number | null;        // cash on the new leg: credit (+) if selling, debit (−) if buying
  netRollCash: number | null;      // net cash of the whole roll
  campaignAfter: number | null;    // campaign realized after this roll ($)
  targetCredit: number | null;     // credit the new leg must collect for a break-even campaign ($)
  targetPerShare: number | null;
  meets: boolean | null;           // does the new credit reach the target? (null if unknown)
  priorCount: number;
  newQty: number;
}

export function rollPreview(i: RollPreviewInput): RollPreview {
  const hasBB = Number.isFinite(i.buyback);
  const hasNP = Number.isFinite(i.newPremium);
  const none: RollPreview = {
    hasAny: false, realizedClose: null, buybackCost: null, newCredit: null, netRollCash: null,
    campaignAfter: null, targetCredit: null, targetPerShare: null, meets: null, priorCount: i.priorCount, newQty: i.newQty,
  };
  if (!hasBB && !hasNP) return none;

  const realizedClose = hasBB ? (i.oldIsShort ? i.oldEntry - i.buyback : i.buyback - i.oldEntry) * 100 * i.oldQty : null;
  const buybackCost = hasBB ? i.buyback * 100 * i.oldQty : null;
  const oldEntryCredit = (i.oldIsShort ? i.oldEntry : -i.oldEntry) * 100 * i.oldQty;
  const netBasisBefore = oldEntryCredit + i.priorRoll;
  const targetCredit = buybackCost != null ? buybackCost - netBasisBefore : null;
  const targetPerShare = targetCredit != null && i.newQty > 0 ? targetCredit / (100 * i.newQty) : null;
  const newCredit = hasNP ? (i.newAction === 'sell' ? 1 : -1) * i.newPremium * 100 * i.newQty : null;
  const campaignAfter = realizedClose != null ? i.priorRoll + realizedClose : null;
  const netRollCash = buybackCost != null && newCredit != null ? (i.oldIsShort ? -buybackCost : buybackCost) + newCredit : null;
  const meets = newCredit != null && targetCredit != null ? newCredit >= targetCredit - 0.005 : null;

  return { hasAny: true, realizedClose, buybackCost, newCredit, netRollCash, campaignAfter, targetCredit, targetPerShare, meets, priorCount: i.priorCount, newQty: i.newQty };
}
