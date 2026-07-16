/**
 * CollapsibleSection — a lightweight, self-contained expand/collapse wrapper.
 * Used to tame the density of an expanded trade card: each heavy section is
 * collapsed by default so the card opens clean and the user drills into what
 * they want.
 */
import { useState, type ReactNode } from 'react';
import { ChevronRight, ChevronDown } from 'lucide-react';

// Static class strings per accent so Tailwind's JIT generates them (no safelist).
const ACCENT: Record<string, { box: string; hover: string; text: string }> = {
  info: { box: 'border-info/15 bg-info/[0.03]', hover: 'hover:bg-info/[0.06]', text: 'text-info/70' },
  success: { box: 'border-success/15 bg-success/[0.03]', hover: 'hover:bg-success/[0.06]', text: 'text-success/70' },
  warning: { box: 'border-warning/15 bg-warning/[0.03]', hover: 'hover:bg-warning/[0.06]', text: 'text-warning/70' },
  secondary: { box: 'border-secondary/15 bg-secondary/[0.03]', hover: 'hover:bg-secondary/[0.06]', text: 'text-secondary/70' },
  'base-content': { box: 'border-white/[0.06] bg-base-100/20', hover: 'hover:bg-base-100/40', text: 'text-base-content/60' },
};

export default function CollapsibleSection({
  title, icon, subtitle, badge, defaultOpen = false, accent = 'base-content', children,
}: {
  title: string;
  icon?: ReactNode;
  subtitle?: string;
  badge?: ReactNode;
  defaultOpen?: boolean;
  accent?: string;          // 'info' | 'success' | 'warning' | 'secondary' | 'base-content'
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const c = ACCENT[accent] || ACCENT['base-content'];
  return (
    <div className={`rounded-lg border ${c.box} overflow-hidden`}>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(v => !v); }}
        className={`w-full flex items-center gap-2 px-2.5 py-2 ${c.hover} transition-colors text-left`}
      >
        {open ? <ChevronDown className="w-3.5 h-3.5 text-base-content/40 shrink-0" />
              : <ChevronRight className="w-3.5 h-3.5 text-base-content/40 shrink-0" />}
        {icon && <span className={`${c.text} shrink-0`}>{icon}</span>}
        <span className={`text-[10px] uppercase tracking-wider font-semibold ${c.text}`}>{title}</span>
        {subtitle && <span className="text-[9px] text-base-content/35 truncate">{subtitle}</span>}
        <span className="ml-auto shrink-0">{badge}</span>
      </button>
      {open && <div className="px-2.5 pb-2.5 pt-0.5">{children}</div>}
    </div>
  );
}
