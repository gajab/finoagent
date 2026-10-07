/**
 * SplitPane — a two-pane layout with a draggable vertical divider, used where the Beta puts a list next to a
 * detail pane (My Trades: positions | inspector; Income Desk: ranking | decision panel). Lets the user give the
 * pane they are digging into more room.
 *
 *   drag the divider        → resize (pointer events, so mouse, touch and pen all work)
 *   ← / → on the divider    → resize 2% at a time; Shift = 10%; Home / End = the limits
 *   double-click            → reset to the default
 *   the width is remembered → localStorage[storageKey]
 *
 * Usage: <SplitPane storageKey="…">{list}{selected && detail}</SplitPane> — with only one child (nothing selected)
 * it renders that child alone, no divider. Below the `lg` breakpoint the panes stack (the divider is hidden) — the
 * detail pane there is a full-screen sheet. The right pane is a DIRECT grid child so a `sticky` pane keeps sticking.
 */
import React, { useRef, useState } from 'react';

/** Keep the left pane's share inside [min, max]. */
export function clampSplit(pct: number, min: number, max: number): number {
  if (Number.isNaN(pct)) return min;                 // ±Infinity clamp naturally to the bounds
  return Math.min(max, Math.max(min, pct));
}

/** Left pane share (%) for a pointer at `clientX`, given the container's left edge and width. */
export function splitFromPointer(clientX: number, containerLeft: number, containerWidth: number, min: number, max: number): number {
  if (!(containerWidth > 0)) return min;
  return clampSplit(((clientX - containerLeft) / containerWidth) * 100, min, max);
}

const readStored = (key: string, fallback: number): number => {
  try { const v = Number(localStorage.getItem(key)); return Number.isFinite(v) && v > 0 ? v : fallback; } catch { return fallback; }
};

export default function SplitPane({
  children, storageKey, defaultLeft = 55, min = 28, max = 76,
}: {
  children: React.ReactNode; storageKey: string;
  defaultLeft?: number; min?: number; max?: number;
}) {
  const [pct, setPct] = useState(() => clampSplit(readStored(storageKey, defaultLeft), min, max));
  const [dragging, setDragging] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const latest = useRef(pct);

  const persist = (v: number) => { try { localStorage.setItem(storageKey, String(Math.round(v * 10) / 10)); } catch { /* private mode */ } };
  const apply = (v: number, save = true) => { const c = clampSplit(v, min, max); latest.current = c; setPct(c); if (save) persist(c); };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging || !wrap.current) return;
    const r = wrap.current.getBoundingClientRect();
    apply(splitFromPointer(e.clientX, r.left, r.width, min, max), false);   // save once, on release
  };
  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    setDragging(false);
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    persist(latest.current);
  };
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 10 : 2;
    if (e.key === 'ArrowLeft') { e.preventDefault(); apply(pct - step); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); apply(pct + step); }
    else if (e.key === 'Home') { e.preventDefault(); apply(min); }
    else if (e.key === 'End') { e.preventDefault(); apply(max); }
    else if (e.key === 'Enter') { e.preventDefault(); apply(defaultLeft); }
  };

  const kids = React.Children.toArray(children);
  if (kids.length < 2) return <>{kids[0] ?? null}</>;   // nothing selected → just the list
  const [left, right] = kids;

  return (
    <div ref={wrap}
      className={`grid items-start gap-4 lg:gap-0 lg:grid-cols-[minmax(0,var(--sp-l))_16px_minmax(0,var(--sp-r))] ${dragging ? 'select-none' : ''}`}
      style={{ ['--sp-l' as any]: `${pct}fr`, ['--sp-r' as any]: `${100 - pct}fr` }}>
      {left}
      <div role="separator" aria-orientation="vertical" aria-label="Resize the two panels" tabIndex={0}
        aria-valuemin={min} aria-valuemax={max} aria-valuenow={Math.round(pct)}
        title="Drag to resize · double-click to reset"
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag}
        onDoubleClick={() => apply(defaultLeft)} onKeyDown={onKeyDown}
        className="group relative hidden lg:block self-stretch cursor-col-resize touch-none outline-none">
        <div className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors ${dragging ? 'bg-primary' : 'bg-white/10 group-hover:bg-primary/60 group-focus-visible:bg-primary'}`} />
        <div className={`sticky top-[40vh] mx-auto h-12 w-1.5 rounded-full transition-colors ${dragging ? 'bg-primary' : 'bg-white/25 group-hover:bg-primary/70 group-focus-visible:bg-primary'}`} />
      </div>
      {right}
    </div>
  );
}
