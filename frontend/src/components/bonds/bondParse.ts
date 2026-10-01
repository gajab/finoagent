// Parse a row copied from a brokerage positions page into Bond Desk fields.
//
// Brokers label the same columns differently (Fidelity "Cost basis total", Schwab "Cost Basis",
// Vanguard "Total cost"…) and copy/paste turns tables into tab/space-separated text like:
//   "Current value  Quantity  Average cost basis  Cost basis total  $25,265.21  25,000  $1.06  $26,551.79"
// We find the column LABELS (longest synonym first, so "cost basis total" never matches as "cost basis"),
// then map the numbers that follow them in order. A CUSIP, coupon % and maturity date anywhere in the
// text (e.g. a pasted description "UNITED STATES TREAS 1.625% 04/15/2030") are picked up too.

export interface ParsedPosition {
  quantity?: number;
  current_value?: number;
  avg_cost?: number;
  cost_total?: number;
  price?: number;
  cusip?: string;
  coupon_pct?: number;
  maturity?: string;       // ISO yyyy-mm-dd
  matched: string[];       // which columns were recognised (for the UI to echo back)
}

type Field = 'quantity' | 'current_value' | 'avg_cost' | 'cost_total' | 'price';

const SYNONYMS: [Field, string[]][] = [
  ['cost_total', ['cost basis total', 'total cost basis', 'total cost', 'cost basis']],
  ['avg_cost', ['average cost basis', 'avg cost basis', 'average cost', 'avg cost', 'unit cost', 'cost per share', 'cost/share', 'avg. cost']],
  ['current_value', ['current value', 'market value', 'mkt value', 'mkt val']],
  ['quantity', ['original face', 'face value', 'par value', 'quantity', 'shares', 'qty', 'face', 'par']],
  ['price', ['last price', 'current price', 'price']],
];

export const FIELD_LABEL: Record<Field, string> = {
  quantity: 'Quantity', current_value: 'Current value', avg_cost: 'Average cost', cost_total: 'Cost basis total', price: 'Price',
};

function toNumber(tok: string): number | null {
  const neg = /^\(.*\)$/.test(tok) || tok.startsWith('-') || tok.startsWith('−');
  const n = Number(tok.replace(/[()$,\s−-]/g, ''));
  if (!Number.isFinite(n)) return null;
  return neg ? -n : n;
}

export function parseBrokerRow(raw: string): ParsedPosition {
  const out: ParsedPosition = { matched: [] };
  if (!raw || !raw.trim()) return out;
  const text = raw.replace(/ /g, ' ');
  const lower = text.toLowerCase();

  // --- identifiers anywhere in the text ---
  const cusip = text.toUpperCase().match(/\b([0-9]{3}[0-9A-Z]{5}[0-9])\b/);
  if (cusip) out.cusip = cusip[1];
  const cpn = text.match(/(\d{1,2}(?:\.\d{1,5})?)\s?%/);
  if (cpn) out.coupon_pct = Number(cpn[1]);
  const mat = text.match(/\b(\d{1,2})\/(\d{1,2})\/(\d{2}|\d{4})\b/);
  if (mat) {
    const y = mat[3].length === 2 ? 2000 + Number(mat[3]) : Number(mat[3]);
    out.maturity = `${y}-${mat[1].padStart(2, '0')}-${mat[2].padStart(2, '0')}`;
  }

  // --- column labels: longest synonym first, masking what's already matched ---
  const all = SYNONYMS.flatMap(([field, syns]) => syns.map(s => ({ field, s })))
    .sort((a, b) => b.s.length - a.s.length);
  const taken = new Array(lower.length).fill(false);
  const hits: { field: Field; start: number; end: number }[] = [];
  for (const { field, s } of all) {
    const re = new RegExp(`(^|[^a-z])(${s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')})(?![a-z])`, 'g');
    let m: RegExpExecArray | null;
    while ((m = re.exec(lower))) {
      const start = m.index + m[1].length;
      const end = start + m[2].length;
      if (taken.slice(start, end).some(Boolean)) continue;
      for (let i = start; i < end; i++) taken[i] = true;
      if (!hits.some(h => h.field === field)) hits.push({ field, start, end });
    }
  }
  if (!hits.length) return out;
  hits.sort((a, b) => a.start - b.start);

  // --- layout 1: a header row of labels, then the values in the same order ---
  const nums = numbersIn(text.slice(hits[hits.length - 1].end));
  if (nums.length >= hits.length) {
    hits.forEach((h, i) => { out[h.field] = nums[i]; out.matched.push(FIELD_LABEL[h.field]); });
    return out;
  }
  // --- layout 2: interleaved "Label value Label value …" ---
  hits.forEach((h, i) => {
    const seg = numbersIn(text.slice(h.end, i + 1 < hits.length ? hits[i + 1].start : undefined));
    if (seg.length) { out[h.field] = seg[0]; out.matched.push(FIELD_LABEL[h.field]); }
  });
  return out;
}

// Numeric tokens in a span, ignoring dates (04/15/2030) and percentages (1.625%).
function numbersIn(span: string): number[] {
  const clean = span
    .replace(/\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/g, ' ')
    .replace(/-?\d+(?:\.\d+)?\s?%/g, ' ');
  return (clean.match(/\(?[-−]?\$?\s?\d[\d,]*(?:\.\d+)?\)?/g) ?? [])
    .map(t => toNumber(t.trim())).filter((n): n is number => n !== null);
}

// Broker "average cost" for bonds is shown either per $1 of face (Fidelity: 1.06) or per 100 (106.21).
export function perHundred(avg: number | null | undefined): number | null {
  if (avg == null || !Number.isFinite(avg) || avg <= 0) return null;
  return avg < 5 ? avg * 100 : avg;
}
