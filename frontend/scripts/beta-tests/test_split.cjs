const { clampSplit, splitFromPointer } = require(require('path').join(process.env.BUILD_DIR, 'SplitPane.cjs'));
const assert = require('assert');
let n = 0; const ok = (name, fn) => { fn(); n++; console.log('  ok -', name); };
console.log('SplitPane maths');
ok('clamp keeps the left share inside [min, max]', () => {
  assert.equal(clampSplit(10, 28, 76), 28); assert.equal(clampSplit(90, 28, 76), 76); assert.equal(clampSplit(50, 28, 76), 50);
});
ok('a non-finite value falls back to the minimum instead of breaking the layout', () => {
  assert.equal(clampSplit(NaN, 28, 76), 28); assert.equal(clampSplit(Infinity, 28, 76), 76);
});
ok('pointer at the container middle = 50%', () => assert.equal(splitFromPointer(600, 100, 1000, 28, 76), 50));
ok('pointer far left/right is clamped, never off-screen', () => {
  assert.equal(splitFromPointer(-500, 100, 1000, 28, 76), 28); assert.equal(splitFromPointer(5000, 100, 1000, 28, 76), 76);
});
ok('dragging right makes the left pane larger, monotonically', () => {
  let prev = -1; for (const x of [300, 400, 500, 600, 700, 800]) { const v = splitFromPointer(x, 100, 1000, 28, 76); assert.ok(v >= prev); prev = v; }
});
ok('a zero-width container (hidden / not laid out) is safe', () => assert.equal(splitFromPointer(300, 100, 0, 28, 76), 28));
console.log(`\n${n} passed`);
