setTimeout(() => { console.error('WATCHDOG: tests hung'); process.exit(2); }, 90000).unref();
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/', pretendToBeVisual: true });
for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'MutationObserver', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
  try { global[k] = dom.window[k]; } catch { Object.defineProperty(global, k, { value: dom.window[k], configurable: true }); }
}
global.IS_REACT_ACT_ENVIRONMENT = true;
global.sessionStorage = dom.window.sessionStorage; global.localStorage = dom.window.localStorage;
global.__fx = require('./fixtures.cjs');
require('./' + (process.env.HARNESS || 'harness') + '.bundle.cjs');
