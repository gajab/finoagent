import React, { useCallback, useState } from 'react';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';

/**
 * CollapsibleSidebar — reusable hide/unhide wrapper for the per-page left nav
 * (Strategies, AI Research, Markets, Calculators, …).
 *
 * The dense content panes on these pages benefit from the full screen width, so
 * this collapses the left column down to a thin reopen rail. The neighbouring
 * content pane must be `lg:flex-1 min-w-0` (not a fixed `lg:w-3/4`) so it grows
 * to fill the reclaimed space when collapsed.
 *
 * The collapsed preference is persisted per page via `storageKey`.
 */
interface CollapsibleSidebarProps {
  /** Sidebar body — the nav buttons / cards. */
  children: React.ReactNode;
  /** Unique localStorage key so the collapsed preference persists per page. */
  storageKey: string;
  /** Small label shown in the header and on the reopen control (e.g. "Strategies"). */
  label: string;
  /** Expanded width on lg+ screens. Default `lg:w-1/4`. */
  widthClass?: string;
  /** Accent colour used for the reopen-rail hover affordance. */
  accent?: 'primary' | 'secondary';
  /** Pin the whole column while the content scrolls (lg+). */
  sticky?: boolean;
}

const readCollapsed = (key: string): boolean => {
  try {
    return localStorage.getItem(key) === '1';
  } catch {
    return false; // private mode / storage disabled → default expanded
  }
};

export function CollapsibleSidebar({
  children,
  storageKey,
  label,
  widthClass = 'lg:w-1/4',
  accent = 'primary',
  sticky = false,
}: CollapsibleSidebarProps) {
  const [collapsed, setCollapsed] = useState<boolean>(() => readCollapsed(storageKey));

  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(storageKey, next ? '1' : '0');
      } catch {
        /* ignore — storage may be unavailable */
      }
      return next;
    });
  }, [storageKey]);

  const hoverAccent =
    accent === 'secondary'
      ? 'hover:border-secondary/50 hover:text-secondary'
      : 'hover:border-primary/50 hover:text-primary';

  // Collapsed → a slim reopen control. Full-width bar on mobile, a sticky 44px
  // icon rail on desktop so the content pane spans (almost) the whole screen.
  if (collapsed) {
    return (
      <div className="w-full lg:w-11 shrink-0">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={false}
          title={`Show ${label}`}
          className={`w-full lg:sticky lg:top-6 flex items-center justify-center gap-2 rounded-xl border-2 border-base-300 bg-base-200 py-2.5 text-base-content/55 transition-all ${hoverAccent}`}
        >
          <PanelLeftOpen className="w-5 h-5" />
          <span className="text-sm font-semibold lg:hidden">Show {label}</span>
        </button>
      </div>
    );
  }

  return (
    <div className={`w-full ${widthClass} shrink-0`}>
      <div className={sticky ? 'lg:sticky lg:top-6' : undefined}>
        <div className="flex items-center justify-between px-1 mb-2">
          <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/30">
            {label}
          </span>
          <button
            type="button"
            onClick={toggle}
            aria-expanded
            title={`Hide ${label}`}
            className="p-1.5 -mr-1 rounded-lg text-base-content/40 transition-colors hover:bg-base-200 hover:text-base-content"
          >
            <PanelLeftClose className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
