// Bundles one Beta module for node with the repo's own esbuild. The classic MyTradesV2 module is replaced by ONLY
// the real helper functions extracted from its source (HELPERS=a,b,c), so tests run the actual code without a DOM.
const path = require('path');
const esbuild = require(path.join(__dirname, '../../node_modules/esbuild'));
const fs = require('fs');
// Replace the heavy classic module with ONLY the real helper functions extracted from its source.
const extractHelpers = {
  name: 'extract-helpers',
  setup(b) {
    b.onLoad({ filter: /components\/trades\/MyTradesV2\.tsx$/ }, (args) => {
      const src = fs.readFileSync(args.path, 'utf8');
      const want = (process.env.HELPERS || 'effectivePnl,expiryFrom,dteFrom').split(',');
      let out = '';
      for (const name of want) {
        const re = new RegExp('export function ' + name + '\\([\\s\\S]*?\\n}\\n', 'm');
        const m = src.match(re);
        if (!m) throw new Error('helper not found: ' + name);
        out += m[0] + '\n';
      }
      return { contents: out, loader: 'ts' };
    });
  },
};
esbuild.buildSync && esbuild.build({
  entryPoints: [process.env.ENTRY], bundle: true, platform: 'node', format: 'cjs', outfile: process.env.OUT,
  define: { 'import.meta.env': '{}' }, loader: { '.tsx': 'tsx', '.ts': 'ts' }, logLevel: 'error', jsx: 'automatic',
  plugins: [extractHelpers],
}).then(() => console.log('built', process.env.OUT)).catch(e => { console.error(e.message); process.exit(1); });
