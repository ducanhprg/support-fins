// Do the fins in a tuned project sit exactly where the engine put them?
//
//   node --max-old-space-size=12000 verify_fins.mjs TUNED.3mf "Object=fins.stl" ["Object=fins.stl" ...]
//
// Reads the tuned project with the engine's own 3MF reader (web/threemf.js, which
// applies the build-item and component transforms independently of the builder)
// and compares the last triangles of each object with the CLI's fins-only STL.
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
  const obj = m.objects.find((o) => o.name === name);
  if (!obj) { console.log(`${name}: NOT FOUND in ${file}`); bad++; continue; }
  const fins = readSTL(new Uint8Array(readFileSync(stl)));
  const tail = obj.positions.subarray(obj.positions.length - fins.length);
  let worst = 0;
  for (let i = 0; i < fins.length; i++) worst = Math.max(worst, Math.abs(tail[i] - fins[i]));
  const ok = worst <= 1e-3;
  if (!ok) bad++;
  console.log(`${name}: ${fins.length / 9} fin triangles, worst vertex offset ${worst.toExponential(1)} mm ${ok ? 'OK' : 'MISPLACED'}`);
}
process.exit(bad ? 1 : 0);
