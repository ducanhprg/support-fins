// Do the fins in a tuned project sit exactly where the engine put them?
//
//   node --max-old-space-size=12000 verify_fins.mjs TUNED.3mf "Object=fins.stl" ["Object=fins.stl" ...]
//
// Reads the tuned project with the engine's own 3MF reader (web/threemf.js, which
// applies the build-item and component transforms independently of the builder)
// and looks for the CLI's fins-only STL as one contiguous run of each object's triangles.
// Prints one line per object; exits 1 if any vertex is off by more than 1 micron.
import { readFileSync } from 'node:fs';

const REPO = new URL('../../../../', import.meta.url);
const { readThreeMF } = await import(new URL('web/threemf.js', REPO).href);
const { readSTL } = await import(new URL('web/stl.js', REPO).href);

const [file, ...pairs] = process.argv.slice(2);
const m = await readThreeMF(new Uint8Array(readFileSync(file)));
let bad = 0;
for (const pair of pairs) {
  const [name, stl] = pair.split('=');
  // the reader drops a file extension from object names ("HAT.stl" -> "HAT")
  const obj = m.objects.find((o) => o.name === name || o.name === name.replace(/\.(stl|3mf|obj|step|stp)$/i, ''));
  if (!obj) { console.log(`${name}: NOT FOUND in ${file}`); bad++; continue; }
  const fins = readSTL(new Uint8Array(readFileSync(stl)));
  // The builder appends the fins to the object's first mesh, so on a multi-part object
  // they sit after that part's triangles, not at the end: find the contiguous block.
  const P = obj.positions;
  const offset = (s) => {
    let w = 0;
    for (let i = 0; i < fins.length && w <= 1e-3; i++) w = Math.max(w, Math.abs(P[s + i] - fins[i]));
    return w;
  };
  let worst = offset(P.length - fins.length);
  for (let s = 0; worst > 1e-3 && s + fins.length <= P.length; s += 9) worst = Math.min(worst, offset(s));
  const ok = worst <= 1e-3;
  if (!ok) bad++;
  console.log(`${name}: ${fins.length / 9} fin triangles, worst vertex offset ${worst.toExponential(1)} mm ${ok ? 'OK' : 'MISPLACED'}`);
}
process.exit(bad ? 1 : 0);
