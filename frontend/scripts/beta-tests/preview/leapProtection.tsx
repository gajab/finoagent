// Visual preview of the LEAP Protection strategy with a REAL engine payload (gen_leap_protection_fixture.py).
//   npm run dev  →  http://localhost:3000/scripts/beta-tests/preview/leapProtection.html  (click "Find protection"; "Premium goes to" switches index ⇄ bond fixtures)
import React from 'react';
import ReactDOM from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import '../../../src/index.css';
import { LeapProtection } from '../../../src/components/LeapProtection';
import fixture from './leapProtectionFixture.json';

const real = window.fetch.bind(window);
window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(typeof input === 'string' ? input : (input as Request).url ?? input);
  if (!url.includes('/api/')) return real(input, init);
  if (url.includes('/api/stock/leap-protection')) {
    const body = JSON.parse(String(init?.body ?? '{}'));
    const payload = (fixture as Record<string, unknown>)[body.proceeds_to === 'bond' ? 'bond' : 'index'];
    await new Promise(r => setTimeout(r, 400));
    return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }
  return new Response(JSON.stringify({ detail: 'mock: not available in the preview' }), { status: 404 });
}) as typeof window.fetch;

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><MemoryRouter>
    <div className="container-app py-5">
      <section className="glass-card"><div className="card-body"><LeapProtection /></div></section>
    </div>
  </MemoryRouter></React.StrictMode>);
