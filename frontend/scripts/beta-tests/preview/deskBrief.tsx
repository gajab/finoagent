// Visual preview of the single-ticker desk (classic DeskReview) with a REAL rank_desk payload
// (gen_desk_brief_fixture.py): the "Before you trade" technical brief + info-only factors moved out of the grade.
//   npm run dev  →  http://localhost:3000/scripts/beta-tests/preview/deskBrief.html
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '../../../src/index.css';
import { DeskReview } from '../../../src/components/DeskReview';
import fixture from './deskBriefFixture.json';

const real = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === 'string' ? input : (input as Request).url ?? input);
  if (!url.includes('/api/')) return real(input, init);
  return new Response(JSON.stringify({ detail: 'mock: not available in the preview' }), { status: 404 });
}) as typeof window.fetch;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><MemoryRouter>
    <div className="container-app py-5">
      <section className="bg-gray-900/60 rounded-2xl border border-white/10 p-6">
        <DeskReview ticker={(fixture as any).ticker} data={fixture as any}
          params={{ min_prob: 0.85, min_income: 20, structures: ['covered_call', 'cash_secured_put'] } as any} />
      </section>
    </div>
  </MemoryRouter></React.StrictMode>);
