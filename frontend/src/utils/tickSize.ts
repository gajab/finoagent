/** IBKR tick size utilities for index options (SPX/XSP/NDX etc.). */

export function getTickSize(price: number): number {
  return price < 3 ? 0.05 : 0.10;
}

export function roundToTick(price: number): number {
  if (price <= 0) return 0;
  const tick = getTickSize(price);
  return Math.round(Math.round(price / tick) * tick * 100) / 100;
}
