import { readFileSync } from 'node:fs';
// The pose check, run for every piece before anything else is decided (the support,
// the fins and the paint are all cut for one pose). Ranks rotations (world X first,
// then Y, in 30° steps, as the slicer's rotation fields apply them) by the needing
// overhang (flatter than THRESHOLD, 2 mm above the plate) on the piece's VISIBLE side,
// then a foot on the plate, then all needing overhang, then height.
//
// Visible side, auto-detected and correctable (--visible):
//   auto     a shell (a mask, a helmet: SHELL or more of its surface faces inward)
//            shows its outside; a solid piece shows every face
//   outside  faces whose normal points away from the surface's centroid
//   all      every face
//   front back left right top bottom (comma-separated): faces turned that way in
//            the pose as placed; front is -Y, toward the viewer on the plate
//
//   node --max-old-space-size=12000 pose_visible.mjs PROJECT.3mf CLI_INDEX THRESHOLD
//        [--visible auto|outside|all|front,top...] [--step 30] [--json] [--all]
const REPO = new URL('../../../../', import.meta.url);
const imp = (p) => import(new URL(p, REPO).href);
const { buildTopology, analyze } = await imp('web/overhangs.js');
const { readThreeMF } = await imp('web/threemf.js');
const { seatSoup } = await imp('plugins/shared/engine/seat.js');

const SHELL = 0.3;        // inward-facing share of the surface that makes a piece a shell
const FOOT = 50;          // mm² on the plate: a foot; less is an edge...
const EDGE = 5;           // ...and under this, a point: never a pose to recommend
const GAIN_ABS = 100;     // a turn is recommended only if it takes this much (mm²)...
const GAIN_REL = 0.5;     // ...and at least this share off the visible overhang
const BAND = (v) => Math.max(25, 0.1 * v);   // visible overhang this close counts as a tie
const DIRS = { front: [0, -1, 0], back: [0, 1, 0], left: [-1, 0, 0], right: [1, 0, 0], top: [0, 0, 1], bottom: [0, 0, -1] };

const argv = process.argv.slice(2);
const flag = (k) => { const i = argv.indexOf(k); return i < 0 ? null : argv[i + 1]; };
const [file, idx, thr] = argv;
const step = Number(flag('--step') ?? 30);
const spec = (flag('--visible') ?? 'auto').toLowerCase();
const m = await readThreeMF(new Uint8Array(readFileSync(file)));
const { pos } = seatSoup(m.objects[Number(idx) - 1].positions);
const topo = buildTopology({ getAttribute: (k) => (k === 'position' ? { array: pos } : null) });
const F = topo.nFaces;
const mid = (f, k) => (topo.pos[f * 9 + k] + topo.pos[f * 9 + 3 + k] + topo.pos[f * 9 + 6 + k]) / 3;

// outside vs inside: outside normals point away from the area-weighted centroid of
// the surface (unlike a bounding-box centre it turns with the piece, so a face is
// outside or inside in every pose alike)
const c = [0, 0, 0]; let at = 0;
for (let f = 0; f < F; f++) { at += topo.area[f]; for (let k = 0; k < 3; k++) c[k] += topo.area[f] * mid(f, k); }
for (let k = 0; k < 3; k++) c[k] /= at;
const outer = new Uint8Array(F); let inward = 0;
for (let f = 0; f < F; f++) {
  let d = 0; for (let k = 0; k < 3; k++) d += topo.nrm[f * 3 + k] * (mid(f, k) - c[k]);
  outer[f] = d > 0; if (!outer[f]) inward += topo.area[f];
}
const share = inward / at;

const vis = new Uint8Array(F);
let mode = spec, why;
if (spec === 'auto') {
  mode = share >= SHELL ? 'outside' : 'all';
  why = `auto: ${Math.round(share * 100)}% of the surface faces inward, ${mode === 'outside' ? 'a shell: its outside shows' : 'a solid piece: every face shows'}`;
} else why = 'as you set it';
if (mode === 'outside') for (let f = 0; f < F; f++) vis[f] = outer[f];
else if (mode === 'all') vis.fill(1);
else {
  const dirs = mode.split(',').map((s) => DIRS[s.trim()]);
  if (dirs.some((d) => !d)) { console.error(`--visible: auto, outside, all, or ${Object.keys(DIRS).join(' ')} (comma-separated)`); process.exit(2); }
  for (let f = 0; f < F; f++) vis[f] = dirs.some((d) => d[0] * topo.nrm[f * 3] + d[1] * topo.nrm[f * 3 + 1] + d[2] * topo.nrm[f * 3 + 2] > 0.2);
}

const cut = -Math.cos(Number(thr) * Math.PI / 180), d2r = Math.PI / 180, rows = [];
for (let ax = -180; ax < 180; ax += step) for (let ay = -90; ay <= 90; ay += step) {
  const a = ax * d2r, b = ay * d2r;
  const Rx = [[1, 0, 0], [0, Math.cos(a), -Math.sin(a)], [0, Math.sin(a), Math.cos(a)]];
  const Ry = [[Math.cos(b), 0, Math.sin(b)], [0, 1, 0], [-Math.sin(b), 0, Math.cos(b)]];
  const M = Ry.map((r, i) => [0, 1, 2].map((j) => r.reduce((s, _, k) => s + Ry[i][k] * Rx[k][j], 0)));
  const rot = [M[0][0], M[1][0], M[2][0], M[0][1], M[1][1], M[2][1], M[0][2], M[1][2], M[2][2]];
  const res = analyze(topo, 45, rot);
  let zmin = Infinity; for (let i = 0; i < topo.pos.length; i += 3) zmin = Math.min(zmin, rot[2] * topo.pos[i] + rot[5] * topo.pos[i + 1] + rot[8] * topo.pos[i + 2]);
  let v = 0, h = 0;
  for (let f = 0; f < F; f++) {
    const nz = rot[2] * topo.nrm[f * 3] + rot[5] * topo.nrm[f * 3 + 1] + rot[8] * topo.nrm[f * 3 + 2];
    if (nz >= cut) continue;
    let z = 0; for (let j = 0; j < 9; j += 3) z += rot[2] * topo.pos[f * 9 + j] + rot[5] * topo.pos[f * 9 + j + 1] + rot[8] * topo.pos[f * 9 + j + 2];
    if (z / 3 - zmin < 2) continue;
    if (vis[f]) v += topo.area[f]; else h += topo.area[f];
  }
  rows.push({ ax, ay, h: Math.round(res.size.z), bed: Math.round(res.bedArea), vis: Math.round(v), hid: Math.round(h) });
}
// visible first; within a tie on it, a foot, then less support overall, then lower.
// Footing is a floor, not a preference: a piece on a foot keeps one, a piece on an
// edge never ends on a point (the solver that balanced a part on a needle, README).
// Giving up a foot for a clean visible side is offered as a trade, the user's call.
const better = (p, q) => (p.bed >= FOOT) - (q.bed >= FOOT) || (q.vis + q.hid) - (p.vis + p.hid) || q.h - p.h;
const now = rows.find((r) => r.ax === 0 && r.ay === 0);
const pick = (set) => {
  const least = Math.min(...set.map((r) => r.vis));
  const best = set.filter((r) => r.vis <= least + BAND(least)).sort((p, q) => better(q, p))[0];
  const tied = now.vis <= least + BAND(least);
  return { best, tied, pays: !tied && now.vis - best.vis >= GAIN_ABS && best.vis <= GAIN_REL * now.vis };
};
const floor = now.bed >= FOOT ? FOOT : Math.min(now.bed, EDGE);
const kept = pick(rows.filter((r) => r.bed >= floor));
// no worse on the visible side but clearly better on the rest: a foot gained, or
// half the support gone (the ZKULL head with its face as the visible side)
const all = (r) => r.vis + r.hid;
const calm = rows.filter((r) => r.bed >= floor && r.vis <= now.vis).sort((p, q) => better(q, p))[0];
const sideways = !kept.pays && calm && calm !== now && (
  (now.bed < FOOT && calm.bed >= FOOT) ||
  (all(now) - all(calm) >= GAIN_ABS && all(calm) <= GAIN_REL * all(now)));
const turn = kept.pays || sideways, best = kept.pays ? kept.best : sideways ? calm : now;
const edgy = !turn && now.bed >= FOOT ? pick(rows.filter((r) => r.bed >= EDGE)) : null;
// (the trade is weighed on the visible side alone: giving up a foot only pays there)
const trade = edgy?.pays ? edgy.best : null;
const say = (r) => `visible overhang ${now.vis} -> ${r.vis} mm² (support overall ${all(now)} -> ${all(r)} mm², bed ${now.bed} -> ${r.bed} mm²)`;
const notes = [];
if (turn && best.bed < FOOT) notes.push(`it stands on ${best.bed} mm² there, an edge: the tune keeps its supports and adds a brim`);
if (!turn && now.bed < FOOT) notes.push(`it stands on ${now.bed} mm² as placed, an edge: the tune keeps its supports and adds a brim`);
if (trade) notes.push(`a trade, your call: X ${trade.ax}°, Y ${trade.ay}° gives ${say(trade)}, but gives up the ${now.bed} mm² foot for an edge`);
if (turn || trade) notes.push('a turn changes how the layer lines run across the visible side: look at it in the preview');
const ordered = [...rows].sort((p, q) => p.vis - q.vis || better(q, p));
const out = {
  visible: { mode, why, inward_share: +share.toFixed(2) },
  now, best, turn, trade,
  why: turn ? say(best)
    : kept.tied ? `as placed is already among the best${now.bed >= FOOT ? ' that keep a foot' : ''} (${now.vis} mm² of visible overhang)`
      : `the best turn${now.bed >= FOOT ? ' that keeps a foot' : ''} only takes ${now.vis - kept.best.vis} mm² off ${now.vis} mm² of visible overhang: not worth a new pose`,
  notes,
  others: ordered.slice(0, 6),
};
if (argv.includes('--json')) { console.log(JSON.stringify(out)); process.exit(0); }
if (argv.includes('--all')) { for (const r of ordered) console.log(JSON.stringify(r)); process.exit(0); }
console.log(`visible: ${mode} (${why})`);
console.log('now (as placed):', JSON.stringify(now));
console.log(turn ? `turn X ${best.ax}°, Y ${best.ay}°: ${out.why}` : `keep: ${out.why}`);
for (const n of notes) console.log(`note: ${n}`);
for (const r of out.others) console.log(JSON.stringify(r));
