// Every piece of a project 3MF through the Support Fins engine, in the pose it
// has on the slicer's plate, with the material and layer height the project uses.
//
//   node --max-old-space-size=12000 analyze_pieces.mjs PROJECT.3mf --inspect project.json
//        [--only "Name,Name"] [--suggest "Name,Name"] [--json pieces.json] [--paint DIR] [--coverage 0-100]
//
// --inspect   inspect_project.py's --json output (material per object, layer height)
// --suggest   also rank orientations for these pieces (slow: ~20 s each on 1M triangles)
// --paint     write DIR/paint-<name>.json for every fins+paint piece: the triangles
//             (indices into the object's mesh, in file order) the slicer should
//             support, for build_tuned.py to paint as support enforcers
//
// Per piece: bed contact and how it sits, overhang regions, what fins would hold
// at this pose, every overhang they can't (area, height, over the plate or over
// the part), loose pieces, and a verdict -- fins-only / fins+paint / supports / none.
// The fins are built the way the CLI builds them (plugins/shared/engine
// computeFins: soup seated in float64, the material's clearances), so the counts
// here are the counts of the fins the builder merges.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';

const REPO = new URL('../../../../', import.meta.url);
const imp = (p) => import(new URL(p, REPO).href);
const { buildTopology, analyze, IDENTITY3: I } = await imp('web/overhangs.js');
const { readThreeMF } = await imp('web/threemf.js');
const { buildFins } = await imp('web/fins.js');
const { insidePart } = await imp('web/inside.js');
const { suggestOrientations } = await imp('web/orient.js');
const { tightRegions } = await imp('web/fins/tight.js');
const { ENGINE_DEFAULTS } = await imp('plugins/shared/engine/fins_entry.js');
const { clearances, seatSoup } = await imp('plugins/shared/engine/seat.js');

const args = process.argv.slice(2);
const flag = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
const FILE = args[0];
const info = JSON.parse(readFileSync(flag('--inspect'), 'utf8'));
const only = flag('--only')?.split(',').map((s) => s.trim());
const suggest = new Set(flag('--suggest')?.split(',').map((s) => s.trim()) ?? []);
const projectLayer = Number(info.process.layer_height);
const paintDir = flag('--paint');
// Wide-face coverage, 0-100 as the CLI and the site take it (the engine wants 0-1).
// A broad near-level ceiling needs its walls close: 100 for PETG and for any big flat
// underside (ZKULL's head sagged between walls at the default 50).
const COVERAGE = flag('--coverage');
const VERBOSE = args.includes('--verbose');
// Pieces the user wants finned whatever the numbers say: fins, plus trees painted on
// what the fins leave more than NEAR_FIN away.
const FORCE_FINS = new Set(flag('--force-fins')?.split(',').map((x) => x.trim()) ?? []);   // every support spot, one line each
// One option, two spellings: Bambu Studio's tree_organic is Orca's organic (the
// profiles write each machine's own token).
const ORGANIC = /bambu/i.test(info.application ?? '') ? 'tree_organic' : 'organic';
if (paintDir) mkdirSync(paintDir, { recursive: true });
const MATERIALS = { PLA: 'pla', PETG: 'petg' };      // the engine's two; others fin as PLA, flagged
// An overhang this close to the plate prints on the first layers' own squish (the
// engine builds nothing under PROP.minHeightSquat, 0.6 mm, for that reason), so it
// doesn't count against fins-only. Reported, not counted. Judged by the region's
// TOP: ZKULL's shoe soles curl up to 1.8 mm and print fine; the head's chin slopes
// from 0.3 to 3.6 mm and is in the air at its top.
const NEAR_BED = 2.0;   // mm
// Fins go wherever they hold an overhang, however small their share (the user's
// rule: support is the last option); the rest is painted for the slicer
// (tree(manual)), so the two never overlap.
// A second support material (zero-gap interface) is worth asking about when a
// piece keeps this much supported area, or one flat ceiling this big: below it
// the gapped single-material underside isn't worth loading a spool for.
const PAYS_TOTAL = 150, PAYS_CEILING = 100;   // mm²
// Finish, for every piece. Flat tops of this much area are worth a note: ironing
// pays on a level face that shows, but on a figure's parts most level tops are
// sockets and mating faces (ZKULL: head, body, legs, shoes all 450-1000 mm²), so
// it's the user's call, never written unasked (the profiles keep it off).
const IRON_AREA = 300;   // mm² of level upward faces
// A foot that tapers to a feather edge: this much sloped underside within 1 mm of
// the plate, against the area actually touching it, lifts on a raft instead
// (profiles section 5: "bottoms that taper to a feather edge can't adhere").
const FEATHER = 2;       // sloped-near-bed area / contact area
// Why the engine built no fin under a spot (web/prop.js regionSkips), in words.
const WHY = {
  noLine: 'curved: no straight run for a wall', stub: 'too short for a wall',
  blocked: 'the part stands in the way of a wall', wanders: 'its edge curves too much for a wall',
  weld: 'a wall would weld into a hole', buried: 'a wall would stand inside the part',
  degenerate: 'no usable shape', sliver: 'too small for a wall',
  noTrack: 'no wall run fits under it: the part is in the way, or every run is too short',
};
// A spot this close to horizontal (the overhang's downward normal, area-weighted)
// is a ceiling: an even block of support gives it the flattest underside.
const CEILING = 0.94;   // cos 20 deg

// The support for the spots fins can't hold: kind and style from the spots'
// shape, height and what's under them; gap from the interface and the layer.
function supportRecipe(spots, { manual, layer, process, iface }) {
  const ov = {}, why = [];
  const ceilings = spots.filter((x) => x.flat >= CEILING && x.area >= 100);
  if (spots.every((x) => x.over === 'plate' && x.top <= 15) && ceilings.length === spots.length) {
    ov.support_type = manual ? 'normal(manual)' : 'normal(auto)'; ov.support_style = 'grid';
    why.push('every spot is a broad flat ceiling low over the plate: a grid block gives the evenest underside');
  } else {
    ov.support_type = manual ? 'tree(manual)' : 'tree(auto)';
    if (ceilings.some((x) => x.area >= 200)) {
      ov.support_style = 'tree_hybrid';
      why.push(`a ${Math.max(...ceilings.map((x) => x.area))} mm² flat ceiling among other spots: hybrid trees put an even block under it and branches elsewhere`);
    } else {
      ov.support_style = ORGANIC;
      why.push(spots.some((x) => x.over === 'part')
        ? 'curved spots, some over the part: organic trees reach them round the model and touch little else'
        : 'curved or small spots: organic trees touch only them and peel off cleanly');
    }
  }
  // gap: zero only with the interface in a low-adhesion material; otherwise the
  // preset's gap, rounded UP to whole layers (the slicer does, so say so)
  const top = Number(process.support_top_z_distance ?? layer);
  if (iface) {
    why.push(`top Z 0 with the interface in slot ${iface.slot} (${iface.type}): set project-wide (playbook section 3)`);
  } else {
    const whole = Math.max(1, Math.ceil(top / layer - 1e-6)) * layer;
    if (Math.abs(whole - top) > 1e-6) {
      ov.support_top_z_distance = whole.toFixed(2);
      why.push(`top Z ${top} rounds up to ${whole.toFixed(2)} at ${layer} mm layers; written as it will print`);
    } else why.push(`top Z ${top} (the preset's, one layer${top / layer > 1.5 ? 's' : ''} at ${layer} mm): no second material for a zero gap`);
  }
  if (spots.some((x) => x.over === 'part') && Number(process.support_interface_bottom_layers ?? 0) === 0) {
    ov.support_interface_bottom_layers = '2';
    why.push('trees land on the part: 2 bottom interface layers keep the landing marks off it');
  }
  for (const k of Object.keys(ov)) if (process[k] === ov[k]) delete ov[k];
  return { overrides: ov, why };
}
// A piece is tippy when it's tall for the foot it stands on: height over the
// foot's width (sqrt of the contact area) beyond this, or under 50 mm² at all.
// Jack (150 mm on 56 mm²: 20x) and ZKULL's body (74 mm on 67 mm²: 9x) need a
// brim; ZKULL's legs (40 mm on 503 mm²: 1.8x) don't.
const TIPPY = 8, SMALL_FOOT = 50;   // ratio, mm²
const POINT_FOOT = 20;              // mm²: under this a piece stands on a point

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

// Per-piece finish overrides: what the geometry says, nothing it doesn't. Walls for
// load and the seam are the user's call (asked or left to the preset).
function finishRecipe(row, { feathered, flatTop, fam, process, finned, pad }) {
  const ov = {}, why = [], notes = [];
  if (feathered && !finned) {
    ov.raft_layers = '2';
    why.push(`the foot tapers to a feather edge (${row.feather} mm² sloped within 1 mm of the plate, ${row.bed_area} mm² touching): a 2-layer raft, as the profiles do for feathered bottoms`);
  } else if (row.brim) {
    ov.brim_type = 'outer_only';
    why.push(`tall for its foot (${row.size[2]} mm on ${row.bed_area} mm²): a brim of its own${pad ? '; the fins\' bed pad helps too' : ''}`);
    if (feathered) why.push('the foot is feathered too, but the fins stand on it: brim, not a raft under the fins');
  }
  if (flatTop >= IRON_AREA && (fam === 'PLA' || fam === 'PETG') && (process.ironing_type ?? 'no ironing') === 'no ironing') {
    notes.push(`${Math.round(flatTop)} mm² of level top surface: if it shows (not a socket or a glued face), ironing_type top on this piece`);
  }
  return { overrides: ov, why, notes };
}

// The faces of a loose piece within 1 mm of its lowest point: where the slicer has
// to start holding it.
function looseBottom(topo, off, p) {
  const { pos, nFaces, adjA, adjB } = topo;
  const parent = new Int32Array(nFaces);
  for (let f = 0; f < nFaces; f++) parent[f] = f;
  const find = (x) => { while (parent[x] !== x) x = parent[x] = parent[parent[x]]; return x; };
  for (let e = 0; e < adjA.length; e++) { const a = find(adjA[e]), c = find(adjB[e]); if (a !== c) parent[a] = c; }
  let seed = -1;
  for (let f = 0; f < nFaces && seed < 0; f++) {
    for (let j = 0; j < 9; j += 3) {
      if (Math.abs(pos[f * 9 + j] + off.x - p.lowest[0]) < 1e-6 && Math.abs(pos[f * 9 + j + 1] + off.y - p.lowest[1]) < 1e-6
          && Math.abs(pos[f * 9 + j + 2] + off.z - p.lowest[2]) < 1e-6) { seed = f; break; }
    }
  }
  if (seed < 0) return [];
  const r = find(seed), out = [];
  for (let f = 0; f < nFaces; f++) {
    if (find(f) !== r) continue;
    const z = Math.min(pos[f * 9 + 2], pos[f * 9 + 5], pos[f * 9 + 8]) + off.z;
    if (z <= p.lowest[2] + 1) out.push(f);
  }
  return out;
}

// ---- Fins, trees or nothing: the per-object decision (the user's core ask) --------
// What the slicer would support is what needs support: faces flatter than the
// preset's own threshold angle (its "slope below N degrees"), not the fin engine's
// 45 degrees. The profiles' ladder gives 25 at 0.16 mm, so a 40-degree slope prints
// on its own (the Parasaurolophus' designer prints it without support).
const NEED_MIN = 50;        // mm²: less than this, summed, prints without support
// A needing patch narrower than this (the short side of its footprint) prints on its
// own: anchored on both edges, nothing in it is more than NARROW/2 = 2.5 mm out, and
// a curved ceiling prints as overhang perimeters, which reach ~2-3 mm unsupported
// (MIT HTMAA group test on a Prusa Core One; design guides say ~1.2 mm). Bridge
// figures (10-20 mm) don't apply: those are straight spans laid between two anchors.
// Wider patches stay counted even where a designer prints them unsupported (the Baby
// Parasaurolophus' 13-16 mm socket roofs): a fin or support there costs little.
const NARROW = 5;          // mm
const NEAR_FIN = 3;         // mm: farther than this from a fin, a ceiling sags (ZKULL head, PETG)
const FINS_HOLD = 0.9;      // share of the needing area that must be near a fin for fins to win
const FIN_COST = 0.15;      // fin plastic as a share of the piece's own (solid) plastic...
const FIN_COST_MIN = 3;     // ...or this many grams, whichever is larger
// Each needing face's distance to the nearest fin vertex (hash grid, NEAR_FIN cells).
function nearFin(tris) {
  const H = new Map(), C = NEAR_FIN;
  const key = (x, y, z) => `${Math.floor(x / C)},${Math.floor(y / C)},${Math.floor(z / C)}`;
  for (const v of tris) { const k = key(v[0], v[1], v[2]); if (!H.has(k)) H.set(k, []); H.get(k).push(v); }
  return (x, y, z) => {
    const cx = Math.floor(x / C), cy = Math.floor(y / C), cz = Math.floor(z / C);
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) for (let k = -1; k <= 1; k++) {
      for (const v of H.get(`${cx + i},${cy + j},${cz + k}`) ?? []) {
        const dx = v[0] - x, dy = v[1] - y, dz = v[2] - z;
        if (dx * dx + dy * dy + dz * dz <= C * C) return true;
      }
    }
    return false;
  };
}
function centroid(topo, off, f) {
  let x = 0, y = 0, z = 0;
  for (let j = 0; j < 9; j += 3) { x += topo.pos[f * 9 + j]; y += topo.pos[f * 9 + j + 1]; z += topo.pos[f * 9 + j + 2]; }
  return [x / 3 + off.x, y / 3 + off.y, z / 3 + off.z];
}
function solidGrams(topo, density) {
  let v = 0;
  const P = topo.pos;
  for (let f = 0; f < topo.nFaces; f++) {
    const o = f * 9;
    v += P[o] * (P[o + 4] * P[o + 8] - P[o + 5] * P[o + 7]) - P[o + 1] * (P[o + 3] * P[o + 8] - P[o + 5] * P[o + 6])
       + P[o + 2] * (P[o + 3] * P[o + 7] - P[o + 4] * P[o + 6]);
  }
  return Math.abs(v) / 6 * density / 1000;
}

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
  const opts = { ...ENGINE_DEFAULTS, material, layerHeight, ...(COVERAGE != null ? { coverage: Number(COVERAGE) / 100 } : {}) };
  const { tunables } = clearances(opts);
  const { pos } = seatSoup(o.positions);
  const topo = buildTopology({ getAttribute: (k) => (k === 'position' ? { array: pos } : null) });
  const res = analyze(topo, opts.threshold, I);
  const fins = (coverage) => buildFins(topo, res, I, { mode: 'auto', bedPad: true, tines: opts.tines,
    tineDensity: opts.tineDensity, layerHeight, coverage, tunables });
  let b = fins(opts.coverage);
  const off = res.offset;
  // What needs support: overhang faces flatter than the preset's threshold, above
  // the near-bed band, not over a tight gap (a support there would fuse it).
  const thr = Number(info.process.support_threshold_angle ?? 25);
  const cutNeed = -Math.cos((thr * Math.PI) / 180);
  const tightAll = new Set(tightRegions(topo, res, I, res.regions.map((_, k) => k)));
  const need = [];   // [face, centroid]
  res.regions.forEach((g, ri) => {
    if (tightAll.has(ri)) return;
    for (const f of g.faces) {
      if (topo.nrm[f * 3 + 2] >= cutNeed) continue;
      const c = centroid(topo, off, f);
      if (c[2] >= NEAR_BED) need.push([f, c, ri]);
    }
  });
  // Per region: how narrow its needing patch is (the short side of its footprint) and
  // how far it hangs over what's below. A narrow patch close over the part is a
  // socket or hole roof: each layer steps in a little and it prints unsupported.
  const patch = new Map();
  for (const [f, c, ri] of need) { if (!patch.has(ri)) patch.set(ri, []); patch.get(ri).push(c); }
  const patchInfo = new Map([...patch].map(([ri, cs]) => {
    const mx = cs.reduce((t, c) => t + c[0], 0) / cs.length, my = cs.reduce((t, c) => t + c[1], 0) / cs.length;
    let sxx = 0, syy = 0, sxy = 0;
    for (const c of cs) { const dx = c[0] - mx, dy = c[1] - my; sxx += dx * dx; syy += dy * dy; sxy += dx * dy; }
    const th = 0.5 * Math.atan2(2 * sxy, sxx - syy), ux = Math.cos(th), uy = Math.sin(th);
    let a0 = Infinity, a1 = -Infinity, b0 = Infinity, b1 = -Infinity;
    for (const c of cs) { const u = (c[0] - mx) * ux + (c[1] - my) * uy, v = -(c[0] - mx) * uy + (c[1] - my) * ux;
      a0 = Math.min(a0, u); a1 = Math.max(a1, u); b0 = Math.min(b0, v); b1 = Math.max(b1, v); }
    const drops = [];
    for (let k = 0; k < cs.length; k += Math.max(1, Math.floor(cs.length / 24))) {
      const [x, y, z] = cs[k]; let d = z;
      for (let t = 0.3; t < z - 0.05; t += 0.5) if (insidePart(topo, I, off, x, y, z - t)) { d = t; break; }
      drops.push(d);
    }
    drops.sort((p, q) => p - q);
    return [ri, { width: Math.min(a1 - a0, b1 - b0), drop: drops[drops.length >> 1] }];
  }));
  // A needing patch narrower than NARROW prints on its own: drop it.
  for (let k = need.length - 1; k >= 0; k--) if (patchInfo.get(need[k][2]).width < NARROW) need.splice(k, 1);
  const needArea = need.reduce((t, [f]) => t + topo.area[f], 0);
  // Share of it within NEAR_FIN of a fin; too little at the default coverage: try 100.
  const nearOf = (bb) => { const nf = nearFin(bb.triangles); return need.map(([, c]) => nf(c[0], c[1], c[2])); };
  const shareOf = (near) => needArea ? need.reduce((t, [f], k) => t + (near[k] ? topo.area[f] : 0), 0) / needArea : 1;
  let near = nearOf(b), finShare = shareOf(near), coverage = opts.coverage;
  if (COVERAGE == null && needArea >= NEED_MIN && finShare < FINS_HOLD) {
    const b2 = fins(1), n2 = nearOf(b2), s2 = shareOf(n2);
    if (s2 > finShare) { b = b2; near = n2; finShare = s2; coverage = 1; }
  }
  const tightSet = new Set(tightRegions(topo, res, I, b.unservedRegions ?? []));
  const spotOf = (ri) => {
    const g = res.regions[ri];
    let zs = 0, n = 0, overPlate = 0, top = -Infinity;
    for (const f of g.faces) for (let j = 2; j < 9; j += 3) top = Math.max(top, topo.pos[f * 9 + j] + off.z);
    for (let k = 0; k < g.faces.length; k += Math.max(1, Math.floor(g.faces.length / 24))) {
      const f = g.faces[k];
      let x = 0, y = 0, z = 0;
      for (let j = 0; j < 9; j += 3) { x += topo.pos[f * 9 + j]; y += topo.pos[f * 9 + j + 1]; z += topo.pos[f * 9 + j + 2]; }
      x = x / 3 + off.x; y = y / 3 + off.y; z = z / 3 + off.z; zs += z; n++;
      let hit = false;
      for (let d = 0.3; d < z - 0.05; d += 0.5) if (insidePart(topo, I, off, x, y, z - d)) { hit = true; break; }
      if (!hit) overPlate++;
    }
    let fa = 0, fn = 0, x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const f of g.faces) {
      fa += topo.area[f]; fn += -topo.nrm[f * 3 + 2] * topo.area[f];
      for (let j = 0; j < 9; j += 3) {
        x0 = Math.min(x0, topo.pos[f * 9 + j]); x1 = Math.max(x1, topo.pos[f * 9 + j]);
        y0 = Math.min(y0, topo.pos[f * 9 + j + 1]); y1 = Math.max(y1, topo.pos[f * 9 + j + 1]);
      }
    }
    const reasons = Object.keys(b.regionSkips?.[ri] ?? {});
    return { region: ri, area: +g.area.toFixed(0), z: +(zs / n).toFixed(1), top: +top.toFixed(1),
             over: tightSet.has(ri) ? 'tight' : overPlate * 2 >= n ? 'plate' : 'part',
             flat: +(fn / Math.max(fa, 1e-9)).toFixed(2), span: +Math.max(x1 - x0, y1 - y0).toFixed(0),
             no_fin: reasons.length ? reasons.map((r) => WHY[r] ?? r) : ['no wall could be placed under it'] };
  };
  const bare = (b.unservedRegions ?? []).map(spotOf);
  for (const x of bare) if (x.over === 'plate' && x.top < NEAR_BED) x.over = 'bed';
  // 'tight' (print-in-place gaps: a support would fuse them) and 'bed' (the first
  // layers carry them) are reported but don't need a support
  const realBare = bare.filter((x) => x.over === 'plate' || x.over === 'part').length;
  const tightBare = bare.filter((x) => x.over === 'tight').length;
  const walls = (b.braceCount ?? 0) + (b.propCount ?? 0);
  // A loose piece hanging less than the tight gap over the part is a tight gap too:
  // a support squeezed under it would fuse it on (ZKULL's hat tag, 1.2 mm).
  const loose = b.floating.filter((p) => !(p.drop < (b.tightGap ?? 0)));
  const bareArea = bare.filter((x) => x.over === 'plate' || x.over === 'part').reduce((s, x) => s + x.area, 0);
  const held = res.overArea > 0 ? 1 - bareArea / res.overArea : 1;
  // The decision. Nothing to support: none. Fins when they come near nearly all of
  // what needs support and cost little next to the piece; otherwise trees. Measured
  // on real prints: ZKULL's head (PETG) sagged at 51% near a fin; the Dr. Doom mask's
  // fins weighed 89% of the mask and doubled the print time.
  const dens = fam === 'PETG' ? 1.27 : 1.24;
  const finG = volume([b.triangles, b.padTriangles ?? []]) * dens / 1000;
  const partG = solidGrams(topo, dens);
  const farFaces = need.filter((_, k) => !near[k]).map(([f]) => f);
  const farArea = farFaces.reduce((t, f) => t + topo.area[f], 0);
  const forced = walls > 0 && FORCE_FINS.has(meta.name ?? o.name);
  const finsWin = forced || (walls > 0 && finShare >= FINS_HOLD && finG <= Math.max(FIN_COST_MIN, FIN_COST * partG));
  const verdict = needArea < NEED_MIN && !loose.length ? 'none-needed'
    : finsWin ? (farArea < NEED_MIN && !loose.length ? 'fins-only' : 'fins+paint')
    : 'supports';
  const decideWhy = verdict === 'none-needed'
    ? `${Math.round(needArea)} mm² is flatter than the preset's ${thr}°: prints without support`
    : forced ? `fins by request: within ${NEAR_FIN} mm of ${Math.round(finShare * 100)}% of the ${Math.round(needArea)} mm² that needs support, `
      + `${finG.toFixed(1)} g${coverage === 1 ? ' at coverage 100' : ''}; trees painted on the ${Math.round(farArea)} mm² farther out`
    : finsWin ? `fins come within ${NEAR_FIN} mm of ${Math.round(finShare * 100)}% of the ${Math.round(needArea)} mm² that needs support, `
      + `for ${finG.toFixed(1)} g (the piece: ${partG.toFixed(0)} g solid)${coverage === 1 ? ', at coverage 100' : ''}`
    : needArea < NEED_MIN ? `a loose piece starts ${loose.map((p) => p.drop.toFixed(1)).join(', ')} mm in the air: supports hold it`
    : walls === 0 ? `no fin fits (${Math.round(needArea)} mm² needs support): trees`
    : finShare < FINS_HOLD ? `fins reach only ${Math.round(finShare * 100)}% of the ${Math.round(needArea)} mm² that needs support `
      + `(under ${NEAR_FIN} mm), the rest would sag: trees`
    : `fins would weigh ${finG.toFixed(0)} g, ${Math.round(finG / partG * 100)}% of the piece (${partG.toFixed(0)} g): trees cost less time`;
  // the spots the support covers: every needing region for trees, the far ones beside fins
  const spotRegions = [...new Set((verdict === 'supports' ? need : need.filter((_, k) => !near[k])).map(([, , ri]) => ri))];
  const supportSpots = spotRegions.map(spotOf).filter((x) => x.over === 'plate' || x.over === 'part');
  const tippy = res.bedArea < SMALL_FOOT || res.size.z / Math.sqrt(Math.max(res.bedArea, 1e-6)) > TIPPY;
  // finish: level tops (ironing) and a feathered foot (raft)
  let flatTop = 0, feather = 0;
  for (let f = 0; f < topo.nFaces; f++) {
    const nz = topo.nrm[f * 3 + 2];
    if (nz > 0.995) flatTop += topo.area[f];
    else if (nz < -0.2 && nz > -0.985) {
      const zc = (topo.pos[f * 9 + 2] + topo.pos[f * 9 + 5] + topo.pos[f * 9 + 8]) / 3 + off.z;
      if (zc < 1) feather += topo.area[f];
    }
  }
  const feathered = feather >= 20 && feather >= FEATHER * Math.max(res.bedArea, 1);
  const row = {
    cli_index: i + 1, name: meta.name ?? o.name, material: fam, fin_material: material, layer_height: layerHeight,
    unsupported_material: !MATERIALS[fam],
    tris: topo.nFaces, size: Object.values(res.size).map((v) => +v.toFixed(1)),
    bed_area: +res.bedArea.toFixed(0), seating: b.seating?.kind ?? null,
    regions: res.regions.length, over_area: +res.overArea.toFixed(0), contact_regions: res.restingCount,
    slivers: res.slivers.length,
    fins: { walls, tines: b.tines ?? 0, grams: +(volume([b.triangles, b.padTriangles ?? []]) * (fam === 'PETG' ? 1.27 : 1.24) / 1000).toFixed(2), pad: !!b.pad },
    bare_tight: tightBare, bare, bare_real: realBare, held: +held.toFixed(2),
    floating: b.floating.map((p) => ({ drop: +p.drop.toFixed(1), z: +p.lowest[2].toFixed(1),
                                       tight: !loose.includes(p) })),
    brim: tippy, verdict, flat_top: +flatTop.toFixed(0), feather: +feather.toFixed(0),
    decide: { why: decideWhy, need_area: +needArea.toFixed(0), threshold: thr, near_fin: +finShare.toFixed(2),
              far_area: +farArea.toFixed(0),
              spots_over_part: supportSpots.some((x) => x.over === 'part'),
              need_by_z: [2, 4, 6, 10, 20, Infinity].map((hi, k, arr) => +need.filter(([, c]) => c[2] >= (k ? arr[k - 1] : 0) && c[2] < hi)
                .reduce((t, [f]) => t + topo.area[f], 0).toFixed(0)),
              coverage: Math.round(coverage * 100), fin_g: +finG.toFixed(1), part_g: +partG.toFixed(0) },
  };
  if (suggest.has(row.name)) {
    const s = suggestOrientations(topo, { top: 3 });
    row.suggest = { confidence: s.confidence, candidates: s.candidates.map((c) => ({
      rot_xyz: euler(c.rot), height: +c.height.toFixed(1), bed_area: +c.bedArea.toFixed(0),
      over_area: +c.overArea.toFixed(0), regions: c.regions, coverage: +c.coverage.toFixed(2), seating: c.seating })) };
  }
  if (verdict === 'fins+paint' || verdict === 'supports') {
    const spots = supportSpots;
    const iface = (info.interface_candidates ?? []).find((c) => c.loadable) ?? null;
    row.support = supportRecipe(spots.length ? spots : [{ area: 0, flat: 0, top: 99, over: 'plate' }],
                                { manual: verdict === 'fins+paint', layer: layerHeight, process: info.process, iface });
  }
  // A piece standing on a point (Be Dr. Doom's ear discs turned for least overhang:
  // 1 mm² under 80 mm) keeps the slicer's supports, and the report says to turn it
  // onto a foot: nothing else holds it up.
  const onPoint = res.bedArea < POINT_FOOT;
  if (verdict === 'none-needed' && info.process.enable_support === '1' && !onPoint) {
    row.support = { overrides: { enable_support: '0' }, why: ['nothing past the threshold: the slicer would only add nubs'] };
  }
  row.finish = finishRecipe(row, { feathered, flatTop, fam, process: info.process, finned: walls > 0, pad: !!b.pad });
  if (onPoint && verdict !== 'fins-only' && verdict !== 'fins+paint') {
    row.finish.notes.unshift(`stands on ${row.bed_area} mm² only, ${row.size[2]} mm tall: turn it onto a foot (pose_visible.mjs lists poses with one)`);
  }
  {
    const spots = verdict === 'none-needed' || verdict === 'fins-only' ? [] : supportSpots;
    const total = spots.reduce((t, x) => t + x.area, 0);
    const ceil = spots.filter((x) => x.flat >= CEILING).reduce((m, x) => Math.max(m, x.area), 0);
    row.interface_pays = total >= PAYS_TOTAL || ceil >= PAYS_CEILING
      ? `${total} mm² of supported underside${ceil >= PAYS_CEILING ? `, a ${ceil} mm² flat ceiling` : ''}` : null;
  }
  if (paintDir && verdict === 'fins+paint') {
    // The faces of every overhang the fins leave bare (not the tight or near-bed
    // ones), plus the bottom 1 mm of each loose piece. Face indices are the
    // object's triangles in file order: readThreeMF keeps them, and so do
    // seatSoup and buildTopology.
    const faces = new Set(farFaces);
    for (const p of loose) for (const f of looseBottom(topo, off, p)) faces.add(f);
    const file = `${paintDir}/paint-${row.name.replace(/[^\w.-]+/g, '_')}.json`;
    writeFileSync(file, JSON.stringify({ object: row.name, tris: topo.nFaces, faces: [...faces].sort((a, b) => a - b) }));
    row.paint = { file, faces: faces.size };
  }
  out.push(row);
  console.log(`#${row.cli_index} ${row.name}  [${fam}${row.unsupported_material ? ' (fins as PLA!)' : ''}, ${layerHeight} mm, `
    + `h ${row.size[2]} on ${row.bed_area} mm²${tippy ? ', tippy' : ''}${row.floating.length ? `, loose piece ${JSON.stringify(row.floating)}` : ''}]`
    + `  => ${verdict.toUpperCase()}: ${decideWhy}`);
  if (walls && verdict !== 'supports' && verdict !== 'none-needed') {
    console.log(`     fins: ${walls} walls, ${row.fins.tines} tines, ${row.fins.grams} g, coverage ${row.decide.coverage}`
      + `${row.paint ? `; ${row.paint.faces} faces painted (${row.decide.far_area} mm² beyond ${NEAR_FIN} mm of a fin)` : ''}`);
  }
  if (VERBOSE) {
    for (const x of supportSpots) {
      const pi = patchInfo.get(x.region);
      console.log(`     spot ${x.area}mm² ${x.z}-${x.top}mm over the ${x.over}${pi ? ` (needing patch ${pi.width.toFixed(0)} mm wide, ${pi.drop.toFixed(1)} mm drop)` : ''}, ${x.flat >= CEILING ? 'flat ceiling' : `slope ${Math.round(Math.acos(Math.min(1, x.flat)) * 180 / Math.PI)}° off flat`}, `
        + `${x.span} mm across${b.unservedRegions?.includes(x.region) ? `: no fin (${x.no_fin.join('; ')})` : ''}`);
    }
  }
  if (Object.keys(row.finish.overrides).length) {
    console.log(`     finish: ${Object.entries(row.finish.overrides).map(([k, v]) => `${k}=${v}`).join(' ')}`);
    for (const w of row.finish.why) console.log(`       - ${w}`);
  }
  for (const n of row.finish.notes) console.log(`     note: ${n}`);
  if (row.interface_pays) console.log(`     second support material would pay: ${row.interface_pays}`);
  if (row.support) {
    console.log(`     support: ${Object.entries(row.support.overrides).map(([k, v]) => `${k}=${v}`).join(' ')}`);
    for (const w of row.support.why) console.log(`       - ${w}`);
  }
  if (row.suggest) {
    console.log(`     now:   h ${row.size[2]} bed ${row.bed_area} over ${row.over_area} regions ${row.regions} (${row.seating})`);
    for (const c of row.suggest.candidates) {
      console.log(`     pose X${c.rot_xyz[0]} Y${c.rot_xyz[1]} Z${c.rot_xyz[2]}: h ${c.height} bed ${c.bed_area} over ${c.over_area} `
        + `regions ${c.regions} covered ${Math.round(c.coverage * 100)}% (${c.seating})`);
    }
  }
}
const j = flag('--json');
if (j) writeFileSync(j, JSON.stringify({ file: FILE, layer_height: projectLayer, pieces: out }, null, 1));
