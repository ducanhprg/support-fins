// CONTACT IS NOT AN OVERHANG (Ghost Clicker, Oct 2026): a multi-body file's boots
// sit on its base 0.0-0.1 mm apart. By its normal alone each boot's sole reads as
// a flat ceiling, so it was painted red and reported as an overhang no fin could
// reach -- but the slicer prints the two surfaces as one, with nothing to bridge.
// analyze() now sets a region with the part a layer under it aside as contact.
import { buildTopology, analyze, block, assert } from './_util.js';
import { RESTS_ON } from '../web/overhangs.js';

const I = [1, 0, 0, 0, 1, 0, 0, 0, 1];
const topoOf = (...parts) => {
  const n = parts.reduce((s, p) => s + p.length, 0), pos = new Float32Array(n);
  let o = 0;
  for (const p of parts) { pos.set(p, o); o += p.length; }
  return buildTopology({ getAttribute: (k) => (k === 'position' ? { array: pos } : null) });
};
// a 30 x 30 base, 10 tall, and a 20 x 20 block above it `gap` mm up
const stack = (gap) => topoOf(block(-15, 15, -15, 15, 0, 10), block(-10, 10, -10, 10, 10 + gap, 16));

Deno.test('contact: a piece sitting flush on another has no overhang under it', () => {
  const res = analyze(stack(0), 45, I);
  assert(res.regions.length === 0, `flush sole counted as ${res.regions.length} region(s)`);
  assert(res.restingCount === 1 && Math.abs(res.restingArea - 400) < 1e-3,
    `expected one 400 mm2 contact region, got ${res.restingCount} / ${res.restingArea}`);
  assert(res.overArea < 1e-6, `contact area left in overArea: ${res.overArea}`);
});

Deno.test('contact: a hair gap (0.1 mm, a print-in-place stack) is still contact', () => {
  const res = analyze(stack(0.1), 45, I);
  assert(res.regions.length === 0 && res.restingCount === 1,
    `0.1 mm gap: ${res.regions.length} regions, ${res.restingCount} resting`);
});

Deno.test('contact: a real gap stays an overhang', () => {
  for (const gap of [RESTS_ON + 0.2, 2, 5]) {
    const res = analyze(stack(gap), 45, I);
    assert(res.regions.length === 1 && res.restingCount === 0,
      `${gap} mm gap: ${res.regions.length} regions, ${res.restingCount} resting`);
  }
});

Deno.test('contact: a ledge over open plate is never contact, in any pose', () => {
  // a column with a ledge out over the plate, then the same part turned 90 about X
  const t = topoOf(block(-5, 5, -5, 5, 0, 20), block(5, 15, -5, 5, 15, 20));
  const flat = analyze(t, 45, I);
  assert(flat.regions.length === 1 && flat.restingCount === 0, 'the ledge is an overhang');
  const rx = [1, 0, 0, 0, 0, 1, 0, -1, 0];
  assert(analyze(t, 45, rx).restingCount === 0, 'turned, still nothing resting');
});
