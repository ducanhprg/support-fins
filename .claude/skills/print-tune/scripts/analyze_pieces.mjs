// Every piece of a project 3MF through the Support Fins engine, in the pose it
// has on the slicer's plate, with the material and layer height the project uses.
//
//   node --max-old-space-size=12000 analyze_pieces.mjs PROJECT.3mf --inspect project.json
//        [--only "Name,Name"] [--suggest "Name,Name"] [--json pieces.json]
//
// --inspect   inspect_project.py's --json output (material per object, layer height)
// --suggest   also rank orientations for these pieces (slow: ~20 s each on 1M triangles)
//
// Per piece: bed contact and how it sits, overhang regions, what fins would hold
// at this pose, every overhang they can't (area, height, over the plate or over
// the part), loose pieces, and a verdict -- fins-only / supports / none.
// The fins are built the way the CLI builds them (plugins/shared/engine
// computeFins: soup seated in float64, the material's clearances), so the counts
// here are the counts of the fins the builder merges.
import { readFileSync, writeFileSync } from 'node:fs';

const REPO = new URL('../../../../', import.meta.url);
const imp = (p) => import(new URL(p, REPO).href);
const { buildTopology, analyze, IDENTITY3: I } = await imp('web/overhangs.js');
const { readThreeMF } = await imp('web/threemf.js');
const { buildFins } = await imp('web/fins.js');
const { insidePart } = await imp('web/inside.js');
const { suggestOrientations } = await imp('web/orient.js');
const { ENGINE_DEFAULTS } = await imp('plugins/shared/engine/fins_entry.js');
const { clearances, seatSoup } = await imp('plugins/shared/engine/seat.js');

const args = process.argv.slice(2);
const flag = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const FILE = args[0];
const info = JSON.parse(readFileSync(flag('--inspect'), 'utf8'));
const only = flag('--only')?.split(',').map((s) => s.trim());
const suggest = new Set(flag('--suggest')?.split(',').map((s) => s.trim()) ?? []);
const projectLayer = Number(info.process.layer_height);
const MATERIALS = { PLA: 'pla', PETG: 'petg' };      // the engine's two; others fin as PLA, flagged
// An overhang this close to the plate prints on the first layers' own squish (the
// engine builds nothing under PROP.minHeightSquat, 0.6 mm, for that reason), so it
// doesn't count against fins-only. Reported, not counted.
const NEAR_BED = 1.0;   // mm

const euler = (e) => {    // three.js Euler XYZ of a column-major matrix, degrees (the site's readout)
  const c = (v) => Math.max(-1, Math.min(1, v)), d = (r) => Math.round(r * 180 / Math.PI);
  const y = Math.asin(c(e[6]));
  return Math.abs(e[6]) < 0.9999999 ? [d(Math.atan2(-e[7], e[8])), d(y), d(Math.atan2(-e[3], e[0]))]
                                    : [d(Math.atan2(e[5], e[4])), d(y), 0];
};
const volume = (lists) => {
  let v = 0;
  for (const t of lists) for (let i = 0; i < t.length; i += 3) {
    const [a, b, c] = [t[i], t[i + 1], t[i + 2]];
    v += a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0]);
  }
  return Math.abs(v) / 6;
};

const m = await readThreeMF(new Uint8Array(readFileSync(FILE)));
const byIndex = new Map(info.objects.map((o) => [o.cli_index, o]));
const out = [];
for (let i = 0; i < m.objects.length; i++) {
  const o = m.objects[i];
  const meta = byIndex.get(i + 1) ?? { name: o.name, materials: [] };
  if (only && !only.includes(meta.name)) continue;
  const fam = meta.materials?.[0] ?? 'PLA';
  const material = MATERIALS[fam] ?? 'pla';
  // An object can carry its own layer height (a per-object override); the tines are
  // one layer tall, so they must be cut for the layer this object actually prints at.
  const layerHeight = Number(meta.overrides?.layer_height ?? projectLayer);
  const opts = { ...ENGINE_DEFAULTS, material, layerHeight };
  const { tunables } = clearances(opts);
  const { pos } = seatSoup(o.positions);
  const topo = buildTopology({ getAttribute: (k) => (k === 'position' ? { array: pos } : null) });
  const res = analyze(topo, opts.threshold, I);
  const b = buildFins(topo, res, I, { mode: 'auto', bedPad: true, tines: opts.tines, tineDensity: opts.tineDensity,
    layerHeight, coverage: opts.coverage, tunables });
  const off = res.offset;
  const bare = (b.unservedRegions ?? []).map((ri) => {
    const g = res.regions[ri];
    let zs = 0, n = 0, overPlate = 0;
    for (let k = 0; k < g.faces.length; k += Math.max(1, Math.floor(g.faces.length / 24))) {
      const f = g.faces[k];
      let x = 0, y = 0, z = 0;
      for (let j = 0; j < 9; j += 3) { x += topo.pos[f * 9 + j]; y += topo.pos[f * 9 + j + 1]; z += topo.pos[f * 9 + j + 2]; }
      x = x / 3 + off.x; y = y / 3 + off.y; z = z / 3 + off.z; zs += z; n++;
      let hit = false;
      for (let d = 0.3; d < z - 0.05; d += 0.5) if (insidePart(topo, I, off, x, y, z - d)) { hit = true; break; }
      if (!hit) overPlate++;
    }
    return { area: +g.area.toFixed(0), z: +(zs / n).toFixed(1), over: overPlate * 2 >= n ? 'plate' : 'part' };
  });
  const tightBare = b.unservedTight ?? 0;
  for (const x of bare) if (x.over === 'plate' && x.z < NEAR_BED) x.over = 'bed';
  const realBare = (b.unserved ?? 0) - tightBare - bare.filter((x) => x.over === 'bed').length;
  const walls = (b.braceCount ?? 0) + (b.propCount ?? 0);
  const verdict = !res.regions.length ? 'none-needed'
    : realBare === 0 && !b.floating.length && walls > 0 ? 'fins-only'
    : 'supports';
  const row = {
    cli_index: i + 1, name: meta.name ?? o.name, material: fam, fin_material: material, layer_height: layerHeight,
    unsupported_material: !MATERIALS[fam],
    tris: topo.nFaces, size: Object.values(res.size).map((v) => +v.toFixed(1)),
    bed_area: +res.bedArea.toFixed(0), seating: b.seating?.kind ?? null,
    regions: res.regions.length, over_area: +res.overArea.toFixed(0), contact_regions: res.restingCount,
    slivers: res.slivers.length,
    fins: { walls, tines: b.tines ?? 0, grams: +(volume([b.triangles, b.padTriangles ?? []]) * (fam === 'PETG' ? 1.27 : 1.24) / 1000).toFixed(2), pad: !!b.pad },
    bare_tight: tightBare, bare, bare_real: realBare,
    floating: b.floating.map((p) => ({ drop: +p.drop.toFixed(1), z: +p.lowest[2].toFixed(1) })),
    verdict,
  };
  if (suggest.has(row.name)) {
    const s = suggestOrientations(topo, { top: 3 });
    row.suggest = { confidence: s.confidence, candidates: s.candidates.map((c) => ({
      rot_xyz: euler(c.rot), height: +c.height.toFixed(1), bed_area: +c.bedArea.toFixed(0),
      over_area: +c.overArea.toFixed(0), regions: c.regions, coverage: +c.coverage.toFixed(2), seating: c.seating })) };
  }
  out.push(row);
  const bareTxt = row.bare.length ? row.bare.map((x) => `${x.area}mm²@${x.z}mm/${x.over}`).join(' ') : '-';
  console.log(`#${row.cli_index} ${row.name}  [${fam}${row.unsupported_material ? ' (fins as PLA!)' : ''}]  `
    + `bed ${row.bed_area}mm² (${row.seating})  regions ${row.regions}  fins ${walls}w/${row.fins.tines}t ${row.fins.grams}g  `
    + `bare: ${bareTxt}${tightBare ? `  (+${tightBare} too tight)` : ''}`
    + `${row.floating.length ? `  LOOSE ${JSON.stringify(row.floating)}` : ''}  => ${verdict}`);
  if (row.suggest) {
    for (const c of row.suggest.candidates) {
      console.log(`     pose X${c.rot_xyz[0]} Y${c.rot_xyz[1]} Z${c.rot_xyz[2]}: h ${c.height} bed ${c.bed_area} over ${c.over_area} `
        + `regions ${c.regions} covered ${Math.round(c.coverage * 100)}% (${c.seating})`);
    }
  }
}
const j = flag('--json');
if (j) writeFileSync(j, JSON.stringify({ file: FILE, layer_height: projectLayer, pieces: out }, null, 1));
