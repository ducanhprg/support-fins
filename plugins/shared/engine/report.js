// The one-line result every host shows, from computeFins' stats: what was placed
// and -- quality first -- what wasn't: overhangs that got no fin this way up (the
// ones too close above the part for any fin named apart), and pieces that start in
// mid-air. Same facts as the website's readout
// (web/ui/readout.js), shorter.
//
// Python hosts have the same line in py/supportfins_host.py host_report; keep the
// two word for word (plugins/cli/tests/cli.test.js checks them against each other
// when python3 is on the PATH).

// Python's f"{x:.1f}": rounds the double's EXACT value, and an exact tie to even
// (4.25 -> 4.2). toFixed also rounds the exact value but sends a tie up. Scaling
// first (x * 10) rounded before rounding: 1.95 * 10 is 19.5 on the nose, a tie
// Python never sees, since 1.95 is stored as 1.94999999999999995559 -- so the
// two hosts printed "2.0" and "1.9" for the same number.
function oneDecimal(x) {
  const s = x.toFixed(1);
  const exact = Math.abs(x).toFixed(20);                 // the double's own digits
  if (!/^50*$/.test(exact.slice(exact.indexOf('.') + 2))) return s;   // not a tie
  return Number(s[s.length - 1]) % 2 === 0 ? s : (x - Math.sign(x) * 0.05).toFixed(1);
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** @param {object} stats  computeFins(...).stats */
export function reportLine(stats) {
  const walls = (stats.braces || 0) + (stats.props || 0);   // tined + plain, as the site counts
  let head = `${plural(walls, 'wall', 'walls')}, ${plural(stats.tines || 0, 'tine', 'tines')}`;
  // Sway braces hold the tall sides, not an overhang: never added to the walls.
  if (stats.swayBraces) head += `, ${plural(stats.swayBraces, 'sway brace', 'sway braces')}`;
  const parts = [head];
  const unserved = stats.unserved || 0;
  const tight = Math.min(unserved, stats.unservedTight || 0);
  if (tight) {
    parts.push(`${plural(tight, 'overhang sits', 'overhangs sit')} too close above the part for a fin `
      + `(gap under ${oneDecimal(Number(stats.tightGap || 0))} mm)`);
  }
  if (unserved - tight) {
    parts.push(`${plural(unserved - tight, 'overhang', 'overhangs')} got no fin this way up `
      + '(try another pose, or add a wall by hand)');
  }
  const floating = stats.floating || 0;
  if (floating) {
    const drop = oneDecimal(Number(stats.floatingDrop || 0));
    parts.push((floating === 1
      ? `one piece isn't joined to the rest: it starts ${drop} mm up`
      : `${floating} pieces aren't joined to the rest: the first starts ${drop} mm up`)
      + ', held only by supports');
  }
  return parts.join('; ');
}
