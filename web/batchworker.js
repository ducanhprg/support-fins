// One object of a multi-object file, finned start to finish off the main thread
// (ui/batch.js): weld, analyse in the pose it has on the file's plate, build.
//
// finworker.js is handed a topology the page already welded, for the one part
// on screen. A whole plate is several parts the page never loaded, and welding
// a big one takes most of a second (the Flexi bee: 0.7 s), so this worker welds
// too -- the picker stays live and can show progress while it runs.
import { buildTopology, analyze, IDENTITY3 } from './overhangs.js';
import { buildFins } from './fins.js';

self.onmessage = (e) => {
  const { id, positions, threshold, opts } = e.data;
  try {
    const topo = buildTopology({ getAttribute: (k) => (k === 'position' ? { array: positions } : null) });
    const result = analyze(topo, threshold, IDENTITY3);
    const built = buildFins(topo, result, IDENTITY3, opts);
    // the part, seated on the plate exactly as its fins were built against it
    const o = result.offset, { pos } = topo;
    const partTris = new Array(topo.nFaces * 3);
    for (let v = 0; v < partTris.length; v++) {
      partTris[v] = [pos[v * 3] + o.x, pos[v * 3 + 1] + o.y, pos[v * 3 + 2] + o.z];
    }
    self.postMessage({
      id, partTris,
      finTris: [...built.triangles, ...(built.padTriangles ?? [])],
      stats: {
        regions: result.regions.length,
        walls: (built.braceCount ?? 0) + (built.propCount ?? 0),
        tines: built.tines ?? 0,
        unserved: built.unserved ?? 0,
        tight: built.unservedTight ?? 0,
        floating: built.floating?.length ?? 0,
      },
    });
  } catch (err) {
    self.postMessage({ id, error: String((err && err.message) || err) });
  }
};
