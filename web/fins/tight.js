/**
 * Overhangs too close above the part for any fin (Flexi Factory Honey Bee, Oct
 * 2026). A print-in-place model is full of them: each segment's underside hangs
 * 1-2 mm over the next one's joint. No wall fits in a gap that tight -- the
 * shortest one standing on the part needs its top gap, its foot gap and
 * PROP.minHeight between them -- and a support there would fuse the joint shut.
 * They were reported as "too shallow for a fin, tilt the part steeper", which
 * sent the user turning a part that was already in its designed pose.
 *
 * This only NAMES them. It never decides what gets built: it runs on the regions
 * the builders already left bare.
 */
import { insidePart } from '../inside.js';
import { PROP } from '../prop.js';

const PROBES = 8;       // faces sampled per region
const STEP = 0.25;      // mm between depths probed under each face

/** The headroom a wall standing on the part needs, mm. Read live: PETG widens the gap. */
export const tightGap = () => PROP.gap + PROP.footGap + PROP.minHeight;

/**
 * Which of `regionIdx` (indices into result.regions) sit within tightGap() above
 * the part over most of their faces. Over the plate is never tight: the plate is
 * where a wall stands, not a gap.
 */
export function tightRegions(topo, result, rot, regionIdx) {
  const { pos } = topo, off = result.offset, limit = tightGap();
  const out = [];
  for (const i of regionIdx) {
    const faces = result.regions[i].faces;
    const step = Math.max(1, Math.floor(faces.length / PROBES));
    let probed = 0, tight = 0;
    for (let k = 0; k < faces.length; k += step) {
      const o = faces[k] * 9;
      let x = 0, y = 0, z = 0;
      for (let j = 0; j < 9; j += 3) {
        const px = pos[o + j], py = pos[o + j + 1], pz = pos[o + j + 2];
        x += rot[0] * px + rot[3] * py + rot[6] * pz;
        y += rot[1] * px + rot[4] * py + rot[7] * pz;
        z += rot[2] * px + rot[5] * py + rot[8] * pz;
      }
      x = x / 3 + off.x; y = y / 3 + off.y; z = z / 3 + off.z;
      probed++;
      for (let d = STEP; d <= limit && z - d > 0; d += STEP) {
        if (insidePart(topo, rot, off, x, y, z - d)) { tight++; break; }
      }
    }
    if (probed && tight * 2 >= probed) out.push(i);
  }
  return out;
}
