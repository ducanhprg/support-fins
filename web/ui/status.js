/**
 * Overhang status: which overhangs a fin holds, which still need support, which
 * are too small for a fin, and which sit too close above the part for any
 * support. ui/part.js paints them (regionStates); this module lists them under
 * the stats, where each count is a button that flies the camera to the next
 * overhang of that kind and flashes it through the part.
 *
 * WHY. On a complex part the overhangs are underneath and inside joints. "22
 * regions" said how many and not where, and every one stayed red after Add fins,
 * held or not -- so after a build you could not tell what was left (Flexi bee,
 * Ghost Clicker, Oct 2026).
 */
import * as THREE from 'three';
import { el } from './dom.js';
import { camera, controls } from './scene.js';

// The pose the last fin build was for, and which of its regions it left bare.
// A build for an older pose says nothing about this one, so it is matched by
// the analysis object itself (ui/finbuild.js passes the one it sent).
let built = null;

/** Record what a fin build held (`b` = buildFins' result for analysis `res`), or forget it. */
export function setBuilt(res, b) {
  built = b ? { res, bare: new Set(b.unservedRegions ?? []) } : null;
}

/**
 * Per region of `res`: 'held' (a fin holds it -- only once a build for this very
 * pose is in), 'tight' (too close above the part for any support), else 'need'.
 */
export function regionStates(res) {
  const fresh = built && built.res === res;
  return res.regions.map((_, i) => {
    if (fresh && !built.bare.has(i)) return 'held';
    return res.regionTight?.[i] ? 'tight' : 'need';
  });
}

const GROUPS = [
  { key: 'need', sw: 'sw-over', label: 'need support', aria: 'that needs support' },
  { key: 'held', sw: 'sw-held', label: 'held by a fin', aria: 'held by a fin' },
  { key: 'small', sw: 'sw-small', label: 'too small for a fin', aria: 'too small for a fin' },
  { key: 'tight', sw: 'sw-tight', label: 'too tight — print as is', aria: 'too tight for any support' },
];

/** Every overhang of `res` sorted into GROUPS, biggest first in each. */
function groupsOf(res) {
  const out = { need: [], held: [], small: [], tight: [] };
  regionStates(res).forEach((s, i) => out[s].push(res.regions[i]));
  (res.slivers ?? []).forEach((g, i) => out[res.sliverTight?.[i] ? 'tight' : 'small'].push(g));
  for (const k of Object.keys(out)) out[k].sort((a, b) => b.area - a.area);
  return out;
}

let shownRes = null;     // the analysis the list was last drawn for
let shownSig = '';       // its counts, so an unchanged list isn't rebuilt (and keeps focus)
let groups = null;
let ctx = null;          // { part, topology } the list belongs to, read at click time
const cursor = {};       // per group: index of the overhang last shown

/**
 * Draw the status list for `res` under the stats. Called with every repaint;
 * the buttons are only rebuilt when a count changes, and the per-group cursors
 * reset when the pose does.
 */
export function renderStatus(res, part, topology) {
  const box = el('s-status');
  ctx = { part, topology };
  if (res !== shownRes) {
    shownRes = res;
    for (const k of Object.keys(cursor)) delete cursor[k];
    el('s-status-cap').textContent = '';
  }
  groups = groupsOf(res);
  const sig = GROUPS.map((g) => groups[g.key].length).join(',');
  if (sig === shownSig && box.childElementCount) return;
  shownSig = sig;
  const items = GROUPS.filter((g) => groups[g.key].length).map((g) => {
    const n = groups[g.key].length;
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'st';
    b.dataset.group = g.key;
    b.setAttribute('aria-label', `Show the next overhang ${g.aria} (${n})`);
    const sw = document.createElement('i');
    sw.className = `sw ${g.sw}`;
    b.append(sw, `${n} ${g.label}`);
    b.addEventListener('click', () => focusNext(g));
    return b;
  });
  box.replaceChildren(...items);
  box.hidden = !items.length;
  if (!items.length) el('s-status-cap').textContent = '';
}

/** Fly to the next overhang in group `g` and flash it. */
function focusNext(g) {
  const list = groups?.[g.key];
  const { part, topology } = ctx ?? {};
  if (!list?.length || !part) return;
  const i = cursor[g.key] = ((cursor[g.key] ?? -1) + 1) % list.length;
  const faces = list[i].faces;
  const { center, normal, size } = measure(faces, part, topology);
  el('s-status-cap').textContent = `${g.label}: ${i + 1} of ${list.length} · `
    + `${list[i].area.toFixed(0)} mm², ${Math.max(0, center.z).toFixed(1)} mm up`;
  flyTo(center, normal, size);
  flash(faces, part, topology);
}

/** World centre, area-weighted world normal and diagonal of `faces` on `part`. */
function measure(faces, part, topology) {
  const { pos, nrm, area } = topology;
  const c = new THREE.Vector3(), n = new THREE.Vector3();
  const box = new THREE.Box3();
  const v = new THREE.Vector3();
  let wsum = 0;
  for (const f of faces) {
    const w = area[f];
    for (let k = 0; k < 3; k++) {
      v.set(pos[f * 9 + k * 3], pos[f * 9 + k * 3 + 1], pos[f * 9 + k * 3 + 2]);
      c.addScaledVector(v, w / 3);
      box.expandByPoint(v);
    }
    n.x += nrm[f * 3] * w; n.y += nrm[f * 3 + 1] * w; n.z += nrm[f * 3 + 2] * w;
    wsum += w;
  }
  c.divideScalar(wsum || 1);
  part.updateMatrixWorld();
  return {
    center: part.localToWorld(c),
    normal: n.applyQuaternion(part.quaternion).normalize(),
    size: box.getSize(v).length(),
  };
}

let flight = 0;
/**
 * Swing the camera to look at `center` from the side the overhang faces (its
 * normal points down, so this looks up at it), keeping a little of the current
 * viewing angle so the move reads as a turn, not a jump.
 */
function flyTo(center, normal, size) {
  const id = ++flight;
  const fromPos = camera.position.clone(), fromTarget = controls.target.clone();
  const back = fromPos.clone().sub(fromTarget);
  const dist = Math.min(Math.max(size * 4, 30), Math.max(back.length(), 30));
  const dir = normal.clone().add(back.normalize().multiplyScalar(0.45)).normalize();
  const toPos = center.clone().addScaledVector(dir, dist);
  const t0 = performance.now(), ms = 450;
  const step = (now) => {
    if (id !== flight) return;                     // a newer click took over
    const t = Math.min(1, (now - t0) / ms);
    const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
    camera.position.lerpVectors(fromPos, toPos, e);
    controls.target.lerpVectors(fromTarget, center, e);
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

let flashMesh = null;
/**
 * Pulse `faces` white for a moment, drawn on top of everything (no depth test),
 * so an overhang inside a joint shows through the part in front of it.
 */
function flash(faces, part, topology) {
  if (flashMesh) { flashMesh.parent?.remove(flashMesh); flashMesh.geometry.dispose(); flashMesh = null; }
  const arr = new Float32Array(faces.length * 9);
  faces.forEach((f, k) => arr.set(topology.pos.subarray(f * 9, f * 9 + 9), k * 9));
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(arr, 3));
  const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
    color: 0xffffff, transparent: true, opacity: 0, depthTest: false, side: THREE.DoubleSide,
  }));
  m.renderOrder = 10;
  part.add(m);                                     // the part's own frame: it moves with it
  flashMesh = m;
  const t0 = performance.now(), ms = 1800;
  const step = (now) => {
    if (flashMesh !== m) return;
    const t = (now - t0) / ms;
    if (t >= 1) {
      m.parent?.remove(m); g.dispose(); m.material.dispose(); flashMesh = null;
      return;
    }
    m.material.opacity = 0.85 * Math.abs(Math.sin(t * Math.PI * 3));
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
