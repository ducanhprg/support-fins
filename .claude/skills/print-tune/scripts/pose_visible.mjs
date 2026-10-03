import { readFileSync } from 'node:fs';
// Which pose keeps a shell's supports off its visible side? Ranks rotations (world X
// first, then Y, in 30° steps, as the slicer's rotation fields apply them) by the
// needing overhang (flatter than THRESHOLD, 2 mm above the plate) on the outside
// of the shell, then on the inside. Outside = the face normal points away from the
// bounding-box centre: right for masks, helmets and domes, rough for solid pieces.
//
//   node --max-old-space-size=12000 pose_visible.mjs PROJECT.3mf CLI_INDEX THRESHOLD [STEP=30] [all]
const REPO = new URL('../../../../', import.meta.url);
const imp = (p) => import(new URL(p, REPO).href);
const { buildTopology, analyze } = await imp('web/overhangs.js');
const { readThreeMF } = await imp('web/threemf.js');
const { seatSoup } = await imp('plugins/shared/engine/seat.js');
const [file, idx, thr, stepArg, all] = process.argv.slice(2);
const step = Number(stepArg ?? 30);
const m = await readThreeMF(new Uint8Array(readFileSync(file)));
const { pos } = seatSoup(m.objects[Number(idx) - 1].positions);
const topo = buildTopology({ getAttribute: (k) => (k === 'position' ? { array: pos } : null) });
// outside vs inside of a shell: outside normals point away from the bounding-box centre
// (the area-weighted centroid of the surface: unlike a bounding-box centre it turns
// with the piece, so a face is outside or inside in every pose alike)
const c = [0, 0, 0]; let at = 0;
for (let f = 0; f < topo.nFaces; f++) { const A = topo.area[f]; at += A;
  for (let k = 0; k < 3; k++) c[k] += A * (topo.pos[f * 9 + k] + topo.pos[f * 9 + 3 + k] + topo.pos[f * 9 + 6 + k]) / 3; }
for (let k = 0; k < 3; k++) c[k] /= at;
const outer = new Uint8Array(topo.nFaces);
for (let f = 0; f < topo.nFaces; f++) { let d = 0; for (let k = 0; k < 3; k++) d += topo.nrm[f * 3 + k] * ((topo.pos[f * 9 + k] + topo.pos[f * 9 + 3 + k] + topo.pos[f * 9 + 6 + k]) / 3 - c[k]); outer[f] = d > 0; }
const cut = -Math.cos(Number(thr) * Math.PI / 180), d2r = Math.PI / 180, rows = [];
for (let ax = -180; ax < 180; ax += step) for (let ay = -90; ay <= 90; ay += step) {
  const a = ax * d2r, b = ay * d2r;
  const Rx = [[1, 0, 0], [0, Math.cos(a), -Math.sin(a)], [0, Math.sin(a), Math.cos(a)]];
  const Ry = [[Math.cos(b), 0, Math.sin(b)], [0, 1, 0], [-Math.sin(b), 0, Math.cos(b)]];
  const M = Ry.map((r, i) => [0, 1, 2].map((j) => r.reduce((s, _, k) => s + Ry[i][k] * Rx[k][j], 0)));
  const rot = [M[0][0], M[1][0], M[2][0], M[0][1], M[1][1], M[2][1], M[0][2], M[1][2], M[2][2]];
  const res = analyze(topo, 45, rot);
  let zmin = Infinity; for (let i = 0; i < topo.pos.length; i += 3) zmin = Math.min(zmin, rot[2] * topo.pos[i] + rot[5] * topo.pos[i + 1] + rot[8] * topo.pos[i + 2]);
  let vis = 0, hid = 0;
  for (let f = 0; f < topo.nFaces; f++) {
    const nz = rot[2] * topo.nrm[f * 3] + rot[5] * topo.nrm[f * 3 + 1] + rot[8] * topo.nrm[f * 3 + 2];
    if (nz >= cut) continue;
    let z = 0; for (let j = 0; j < 9; j += 3) z += rot[2] * topo.pos[f * 9 + j] + rot[5] * topo.pos[f * 9 + j + 1] + rot[8] * topo.pos[f * 9 + j + 2];
    if (z / 3 - zmin < 2) continue;
    if (outer[f]) vis += topo.area[f]; else hid += topo.area[f];
  }
  rows.push({ ax, ay, h: Math.round(res.size.z), bed: Math.round(res.bedArea), vis: Math.round(vis), hid: Math.round(hid) });
}
rows.sort((p, q) => p.vis - q.vis || p.hid - q.hid);
if (all === 'all') { for (const r of rows) console.log(JSON.stringify(r)); process.exit(0); }
const now = rows.find((r) => r.ax === 0 && r.ay === 0);
console.log('now (as placed):', JSON.stringify(now));
for (const r of rows.slice(0, 8)) console.log(JSON.stringify(r));
// the same, among poses that stand on something (a foot, not a point)
const footed = rows.filter((r) => r.bed >= 50);
if (footed.length) console.log('with 50 mm² or more on the plate:');
for (const r of footed.slice(0, 6)) console.log(JSON.stringify(r));
