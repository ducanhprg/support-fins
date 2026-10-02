// TOO TIGHT FOR A FIN (Flexi Factory Honey Bee, Oct 2026): a print-in-place
// model's segments hang 1-2 mm over each other's joints. No wall fits there, and
// the readout told the user the overhangs were "too shallow, tilt steeper". The
// bare ones sitting under tightGap() above the part are now counted apart.
import { buildTopology, analyze, fins, block, assert } from './_util.js';
import { tightGap, tightRegions } from '../web/fins/tight.js';

const I = [1, 0, 0, 0, 1, 0, 0, 0, 1];
const topoOf = (...parts) => {
  const n = parts.reduce((s, p) => s + p.length, 0), pos = new Float32Array(n);
  let o = 0;
  for (const p of parts) { pos.set(p, o); o += p.length; }
  return buildTopology({ getAttribute: (k) => (k === 'position' ? { array: pos } : null) });
};
// a 30 x 30 base, 10 tall, with a 20 x 20 block hanging `gap` mm over it
const hung = (gap) => topoOf(block(-15, 15, -15, 15, 0, 10), block(-10, 10, -10, 10, 10 + gap, 16));
const build = (topo) => fins.buildFins(topo, analyze(topo, 45, I), I,
  { mode: 'auto', bedPad: true, tines: true, coverage: 0.5 });

Deno.test('tight: the headroom is the shortest wall that stands on the part', () => {
  const g = tightGap();
  assert(g > 1.5 && g < 2.5, `tightGap ${g}`);
});

Deno.test('tight: a 1 mm gap over the part gets no fin, and says why', () => {
  const b = build(hung(1));
  assert(b.unserved === 1, `unserved ${b.unserved}`);
  assert(b.unservedTight === 1, `unservedTight ${b.unservedTight}`);
  assert(b.tightGap === tightGap(), `tightGap ${b.tightGap}`);
});

Deno.test('tight: a roomy gap over the part, or open plate, is never tight', () => {
  const roomy = hung(6);
  const res = analyze(roomy, 45, I);
  assert(res.regions.length === 1, 'the hung block has one overhang');
  assert(tightRegions(roomy, res, I, [0]).length === 0, 'a 6 mm gap is room enough');

  // a ledge 8 mm over the bare plate, next to a column: nothing under it but plate
  const ledge = topoOf(block(-5, 5, -5, 5, 0, 12), block(5, 15, -5, 5, 8, 12));
  const lr = analyze(ledge, 45, I);
  assert(tightRegions(ledge, lr, I, lr.regions.map((_, i) => i)).length === 0, 'over the plate');
});

Deno.test('tight: only the regions left bare are classified', () => {
  const b = build(hung(6));
  assert(b.unservedTight === 0, `a served or roomy overhang counted tight: ${b.unservedTight}`);
  assert(b.unservedRegions.length === b.unserved, 'the list and the count agree');
});
