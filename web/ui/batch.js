/**
 * Fin every object of a multi-object file at once, and write them into one 3MF.
 *
 * WHY. A plate from Bambu Studio or MakerWorld is several objects (the Ghost
 * Clicker: two bodies and four boots), and the live view edits ONE part -- so
 * each needed loading, finning and exporting on its own, and the exports then
 * had to be put back together. This is the plate-wide pass the command line
 * already does: each object finned in the pose it has on the file's plate, with
 * the fin settings on screen, each locked to its own fins in the output and
 * laid out side by side so none overlap. To turn one first, load it on its own.
 */
import { writeThreeMFObjects } from '../threemf.js';
import { download } from '../stl.js';
import { finOpts } from './finbuild.js';
import { threshold } from './part.js';

const GAP = 10;            // mm between objects in the exported layout

let worker = null;
let gen = 0;

/** Stop a run in flight (the picker's Cancel). */
export function cancelBatch() {
  gen++;
  if (worker) { worker.terminate(); worker = null; }
}

/**
 * Fin each of `objects` (readThreeMF's, in file order). `onProgress(i, result)`
 * fires before object i starts (result undefined) and once it is done. Resolves
 * to one result per object -- { name, partTris, finTris, stats } or
 * { name, error } -- or null if cancelled.
 */
export async function finAll(objects, onProgress) {
  cancelBatch();
  const run = ++gen;
  worker = new Worker(new URL('../batchworker.js', import.meta.url), { type: 'module' });
  // Draw mode places walls by hand, one part at a time; a plate gets Auto.
  const opts = { ...finOpts(), mode: 'auto' };
  const results = [];
  for (let i = 0; i < objects.length; i++) {
    if (run !== gen) return null;
    onProgress(i);
    const o = objects[i];
    const r = await new Promise((resolve) => {
      worker.onmessage = (e) => resolve(e.data);
      worker.onerror = (e) => resolve({ error: e.message || 'the worker failed' });
      worker.postMessage({ id: i, positions: o.positions, threshold, opts });
    });
    if (run !== gen) return null;
    results.push({ name: o.name, ...r });
    onProgress(i, results[i]);
  }
  cancelBatch();
  return results;
}

/** One line per object for the picker: what it got, and what it didn't. */
export function summary(r) {
  if (r.error) return `failed: ${r.error}`;
  const s = r.stats;
  if (!s.regions && !s.walls) return 'no support needed';
  const bits = [`${s.walls} wall${s.walls === 1 ? '' : 's'}`];
  const need = s.unserved - s.tight;
  if (need) bits.push(`${need} need support`);
  if (s.tight) bits.push(`${s.tight} too tight`);
  if (s.floating) bits.push(`${s.floating} loose piece${s.floating === 1 ? '' : 's'}`);
  return bits.join(' · ');
}

/**
 * Lay the finned objects out in rows, `GAP` apart and centred on the plate
 * origin like a single export, then download them as one 3MF. Objects that
 * failed are left out. Returns the file name written.
 */
export function exportAll(results, fileName) {
  const ok = results.filter((r) => !r.error);
  const boxes = ok.map((r) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const list of [r.partTris, r.finTris]) {
      for (const p of list) {
        if (p[0] < x0) x0 = p[0]; if (p[0] > x1) x1 = p[0];
        if (p[1] < y0) y0 = p[1]; if (p[1] > y1) y1 = p[1];
      }
    }
    return { x0, y0, w: x1 - x0, h: y1 - y0 };
  });
  // Rows about as wide as they are deep overall, but never narrower than the
  // widest object.
  const area = boxes.reduce((s, b) => s + (b.w + GAP) * (b.h + GAP), 0);
  const rowW = Math.max(Math.sqrt(area) * 1.2, ...boxes.map((b) => b.w));
  const at = [];
  let x = 0, y = 0, rowH = 0, W = 0;
  for (const b of boxes) {
    if (x > 0 && x + b.w > rowW) { x = 0; y += rowH + GAP; rowH = 0; }
    at.push([x - b.x0, y - b.y0]);
    x += b.w + GAP; rowH = Math.max(rowH, b.h); W = Math.max(W, x - GAP);
  }
  const H = y + rowH;
  const entries = ok.map((r, i) => {
    const dx = at[i][0] - W / 2, dy = at[i][1] - H / 2;
    const move = (list) => list.map((p) => [p[0] + dx, p[1] + dy, p[2]]);
    return { name: r.name, partTris: move(r.partTris), finTris: move(r.finTris) };
  });
  const base = fileName.replace(/\.(stl|3mf|step|stp)$/i, '') || 'plate';
  const out = `${base}-fins.3mf`;
  download(writeThreeMFObjects(entries, base), out);
  return out;
}
