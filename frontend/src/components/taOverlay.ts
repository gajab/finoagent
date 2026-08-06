// Shared Chart.js overlay: draws horizontal price levels (lines) and price zones
// (bands) for the institutional-TA panels. Levels outside the visible y-range are skipped.

export interface OverlayLine { price: number; label: string; dash?: number[]; color?: string }
export interface OverlayBand { top: number; bottom: number; label: string; color: string }
export interface OverlayLayer { color: string; lines?: OverlayLine[]; bands?: OverlayBand[] }

export const toFill = (rgb: string, a: number) => rgb.replace('rgb(', 'rgba(').replace(')', `,${a})`);

export function makeLevelPlugin(layers: OverlayLayer[]): any {
  return {
    id: 'taLevels',
    afterDatasetsDraw(chart: any) {
      const { ctx, chartArea, scales } = chart;
      const y = scales?.y;
      if (!y || !chartArea) return;
      const L = chartArea.left, R = chartArea.right;
      const inRange = (p: number) => p >= y.min && p <= y.max;
      const py = (p: number) => Math.max(chartArea.top, Math.min(chartArea.bottom, y.getPixelForValue(p)));

      ctx.save();
      ctx.beginPath(); ctx.rect(L, chartArea.top, R - L, chartArea.bottom - chartArea.top); ctx.clip();

      const hline = (p: number, color: string, dash: number[] | undefined, label: string) => {
        if (!inRange(p)) return;
        const yy = py(p);
        ctx.strokeStyle = color; ctx.lineWidth = 1.25; ctx.setLineDash(dash || []);
        ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(R, yy); ctx.stroke(); ctx.setLineDash([]);
        ctx.font = '9px sans-serif'; ctx.fillStyle = color; ctx.textAlign = 'right'; ctx.textBaseline = 'bottom';
        ctx.fillText(label, R - 3, yy - 1);
      };
      const band = (a: number, b: number, color: string, label: string) => {
        if (!inRange(a) && !inRange(b)) return;
        const yt = py(Math.max(a, b)), yb = py(Math.min(a, b));
        ctx.fillStyle = toFill(color, 0.10); ctx.fillRect(L, yt, R - L, Math.max(2, yb - yt));
        ctx.font = '9px sans-serif'; ctx.fillStyle = color; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        ctx.fillText(label, L + 3, yt + 1);
      };
      for (const l of layers) {
        (l.bands || []).forEach(b => band(b.top, b.bottom, b.color, b.label));
        (l.lines || []).forEach(ln => hline(ln.price, ln.color || l.color, ln.dash, ln.label));
      }
      ctx.restore();
    },
  };
}
