# Decision playbook

How to turn the inspection and the piece analysis into the best settings for this
particular print. Every rule says why, so an unusual model can be reasoned about
instead of forced through the rule. The user's profiles repo
(`C:\Work\3d_print_profiles`, `PROGRESS.md`) is the evidence for what their two
machines do; read the parts about the material and the setting you're about to
change before changing it. Those profiles are a reference, not a rulebook: deviate
when this model calls for it, and say why in the report.

## 1. Read the brief from the project

The user sets up the print in the slicer before running this skill, so the file
already answers most questions:

| In the project | What it means |
|---|---|
| printer preset (X2D 0.4 / U1 0.4) | which profiles folder and machine limits apply |
| process preset + layer height | the quality level they chose; keep it |
| object-level `layer_height` | that piece prints at its own layer; fins for it must match |
| filament per slot, slot per object | material and colour of each piece; free slots |
| plates | how they grouped the pieces (often one colour per plate) |
| per-object overrides | decisions already made for a piece; keep unless wrong |

Ask only what a file can't say: is this a display piece or does it carry load (and
which way)? If it's obviously a figure or ornament, say so and confirm in one line.

**Printer not X2D or U1** (a designer's file, e.g. an H2D project from MakerWorld):
the profiles repo doesn't cover it, and a tuned copy would carry someone else's
machine and filament presets. Don't build. Run the analysis anyway (the pieces
and their pose don't depend on the printer), and report it together with the
plan you'll apply once it's re-saved, judged against the preset the re-save will
most likely use (their naming: `<layer>mm <material> - x2d 0.4HS`, `<layer> <material>
@Snapmaker U1 (0.4 Stainless Steel nozzle)`). Fold everything they must set in the
slicer into that one re-save: printer, process, filament, and the interface
material if you want one (section 3), so they do it once. Which printer: a Bambu
Studio file goes to the X2D, a Snapmaker Orca file to the U1. If the model only
fits or only makes sense on the other one (size, colours), say so and give both.
`build_tuned.py` refuses other printers anyway.

**A tuned file the user re-saved** (they keep working from it: new plates,
colours) is the brief, but its meshes may still carry fins from the last tune at
their tail, and the analysis would read those as part of the model. Compare each
object's triangle count with the earlier project or tune; when a mesh has extra
triangles at the end that match the old fins STL, strip them into a clean working
copy and tune that (output beside the user's file, e.g. `- tuned v2`).

**A designer's per-object overrides** (a MakerWorld file often carries speeds,
infill and support style tuned for *their* machine) come along through the re-save.
Keep the ones that are about the model (walls and infill for a figurine are the
designer's own advice) and drop the ones that are about their machine (speeds,
accelerations, a support style that overrides the user's): an override set to
`null` in the plan removes it.

## 2. Per piece: fins, fins plus painted supports, slicer supports, or nothing

The order of the decision, for every piece (the user's rule: support is the last
option):

1. **Does anything need support?** Not an overhang near the bed (its top under
   2 mm), not one in a tight gap over the part, not a sliver under 12 mm².
2. **Where?** Each remaining spot: area, height, over the plate or over the part,
   how flat, how wide (the analysis prints a `spot` line for each).
3. **Can a fin hold it?** The engine tries; when it can't, the spot line says why
   (curved with no straight run, too short, the part in the way...).
4. **Only then a support,** on those spots alone, and the right one for them
   (section 3: kind, style, gap).

`analyze_pieces.mjs` gives each piece a verdict. Trust it, and know what's behind it:

- **fins-only**: every real overhang is held by a fin, there's no loose piece, and
  at least one wall was built. Plan: merge the fins into that piece and set its
  `enable_support` to `0`. This is the tool's whole point: no tree supports, no scars.
- **fins+paint**: fins hold some of the overhang, however small a share (the
  user's rule: fin whatever fins hold), and some spots stay bare: a dome, a hem low over the plate, an overhang over the part.
  Plan: merge the fins, set that piece's `support_type` to `tree(manual)`, and paint
  the bare spots as support enforcers (`analyze_pieces.mjs --paint` writes the
  triangles, the plan's `paint_supports` paints them). In manual mode the slicer
  supports only what's painted, so nothing grows between the fins. This is what the
  user wants on big figures: on ZKULL's U1 plate automatic tree supports filled the
  head, hood and hat, and they asked for fins instead (body 89%, hat 95%, head 97%
  held by fins; the rest painted).
- **supports**: fins build nothing on this piece. Plan: the slicer's automatic
  supports with the `support:` line's kind and style. The slicer doesn't know
  fins exist; with automatic supports it would grow tree supports between the fin
  walls as well (`build_tuned.py` refuses fins on a piece with automatic supports).
- **none-needed**: nothing to support.

Not counted: overhangs *too tight* for any support (the part sits within ~2 mm
below: a print-in-place gap or a clearance, where a support would fuse the joint)
and *near-bed* ones under 1 mm (the first layers carry them). The analysis lists
them as "not counted"; report them, don't act on them. A piece whose overhangs are
all of these kinds is `none-needed`.

`none-needed` pieces: leave them alone. Don't switch their supports off just in
case: the engine ignores slivers under 12 mm² that the slicer may still want to
hold. If the slicer is likely to add pointless nubs (a near-bed edge on a big flat
foot), mention that turning supports off for that one piece is an option.

On large organic figures (ZKULL's head, body and hat), expect "fins+paint": fins
hold the straight runs, the painted spots take the domes and the overhangs over the
part. On mechanical parts and simple figures (ZKULL's legs), fins often hold
everything.

**"Held" is not "supported" on a broad, near-level ceiling.** The analysis counts a
region held when a fin reaches it, but a fin is a wall, not a surface: between the
walls a near-level ceiling prints into air. ZKULL's head (X2D, PETG, tuned v2,
2026-10-04) was "97% held" and its overhangs came out terrible: the cranium
ceiling over the jaw (1553 mm², ~25° off level, 10 mm up) had 54% of its area more
than 3 mm from any fin, up to 10 mm, with the engine's default wide-face coverage
(50). For any held ceiling within ~30° of level and over ~200 mm², measure the
distance from its surface to the nearest fin. Most of it should be within 3 mm,
PETG especially (it sags more). If not, build the fins with `--coverage 100` (on
that head, 7% beyond 3 mm instead of 46%, 21 walls instead of 14, +2 g) and paint
what is still far. If coverage can't close the gaps, treat the ceiling as a
support spot.

**The decision, per piece: no support, fins, or trees, whichever suits it** (the
user, 2026-10-04, after a swing each way: fins everywhere, then trees everywhere).
`analyze_pieces.mjs` makes it with numbers; the thresholds are its constants and
each carries its evidence:
1. **Needs support?** Only overhang the slicer itself would support counts: faces
   flatter than the preset's threshold angle (25° at 0.16 mm), 2 mm or more above
   the plate, not over a tight gap, in patches 5 mm wide or more (narrower ones
   step over on their perimeters: overhang perimeters reach ~2-3 mm unsupported per
   a Prusa Core One test; bridge figures of 10-20 mm are for straight spans and
   don't apply to curved ceilings). Under 50 mm² in all: no support.
2. **Fins?** Built at coverage 50, then 100 if that falls short. Fins win when they
   come within 3 mm of at least 90% of the needing area (farther, a ceiling sags:
   ZKULL's head at 51% did) and weigh no more than 15% of the piece or 3 g (the Dr.
   Doom mask's fins weighed 89% of the mask and took 2.5x the time). What's left
   over 50 mm² is painted for trees beside the fins.
3. **Otherwise organic trees** (style per section 3).
Checked against real prints: Dr. Doom mask trees, ZKULL head trees, ZKULL legs
fins, flat pieces none. The Baby Parasaurolophus' 13-16 mm socket roofs stay
counted although its designer prints them unsupported (plates 1-2 printing,
2026-10-04): if they come out clean, that's evidence for that shape only.

**Pose matters most for a shell's visible side.** If a piece's supports land on
its visible face (a mask's brows and nose), `pose_visible.mjs` ranks rotations by
needing overhang outside vs inside. Be_Dr._Doom's face piece: 974 mm² outside as
the designer placed it, 7 mm² at X -120°, Y -60°, with the trees moved inside.
Don't tilt a sculpted piece only to suit fins: that moved ZKULL's head's overhang
onto its face and doubled the first mask's.

**Fin cost, for the report.** Fins print as part of the model: every wall is an
island on every layer, at wall speeds. Tall walls multiply it (the first mask: 227 g
of fins, 41.5 h against 16.5 h with trees). Cutouts save plastic, not time (Lattice
227 to 146 g); raising the overhang threshold barely helps (192-199 g).

A loose piece hanging less than the tight gap over the part (ZKULL's hat tag, 1.2
mm) counts as a tight gap: a support squeezed under it would fuse it on. Mention it
as something to look at in the preview. A loose piece hanging higher gets its
bottom millimetre painted.

Say in the report what is painted on each piece and where, so the user knows what
the green in the slicer's support-painting view is, and can paint more or erase
with the slicer's own tool before slicing.

**Material.** The engine knows PLA and PETG. PETG fuses to supports far harder, so
its fins stand off further: always pass `--material petg` for a PETG piece.
Anything else (ABS, TPU, CF blends): don't fin it; say why.

**Tines are one layer tall** and must be cut for the layer height that piece prints
at (project layer height, or its own override). `build_tuned.py` refuses a mismatch.

**Pose.** Keep the pose the user placed. It's their plate layout and usually the
designer's choice of which faces show. Run `--suggest` only for pieces that stand
on a point or an edge with a small contact area. The output prints the current
pose ("now") above the candidates. A turn is *clearly better* only when it puts a
real face on the bed (seating `face`) **and** cuts the overhang markedly; an edge
traded for another edge is not, however much overhang it saves (ZKULL's head:
38% less overhang, still on an edge: kept). Recommend, don't apply: the builder
doesn't rotate objects; the user turns it in the slicer and re-runs.

## 3. Supports for the pieces that keep them

**Kind and style.** The analysis prints a `support:` line per piece with its pick
and the reasons; put its overrides in the plan. What it weighs:

- **tree, organic** (the profiles' default for figures): curved or small spots,
  and anything over the part. Branches reach round the model, touch only the spot,
  and peel off cleanly; organic trees land softly where they must stand on the part.
- **tree, hybrid**: a broad flat ceiling (within 20° of level, 200 mm² or more)
  among other spots. It puts an even block under the flat one and branches elsewhere.
- **normal (grid)**: every spot is a broad flat ceiling low over the plate (top
  under 15 mm). A grid block gives the evenest underside there, and trees gain
  nothing.
- One kind per piece: the slicer holds `support_type` per object. `(manual)` on a
  fins+paint piece, `(auto)` on a supports piece.

**Gap.** Zero only with the interface in a low-adhesion second material (below).
Otherwise the preset's top Z, a whole number of layers (the slicer rounds up; a
fractional value is written as what it will print, and said). Trees standing on the
part get 2 bottom interface layers if the preset has none.

**Build plate only.** The profiles default to supports from the build plate only.
Turn that off (`support_on_build_plate_only: 0`) when a "supports" or "fins+paint"
piece has bare overhangs **over the part**: from the plate only, nothing reaches them and they
print into air (ZKULL: under the body's hood at 54 mm, inside the hat at 55 mm). Then warn:
check the slice preview for supports inside closed cavities that can't be removed.
If any appear, set that one piece back to build plate only.

**Interface in the other material: the biggest quality lever.** PLA and PETG (and
PLA and TPU) barely bond. With the interface (the few support layers touching the
part) in the other material, the gap can be zero: the supported surface comes out
almost like a top surface and the support still releases. Recipe (Snapmaker's U1
guide, mirrored in the profiles repo `docs/snapmaker/.../pla_and_petg.md`):

```
support_filament                  0          # base in the model's own material
support_interface_filament        <slot>     # the other material
support_top_z_distance            0
support_interface_spacing         0
support_bottom_interface_spacing  0
support_interface_top_layers      3
support_interface_pattern         rectilinear_interlaced
```

Most of the time the user doesn't want a second material for supports. So:

- a free slot already holds the other material (`inspect_project.py` lists it as a
  loadable interface candidate; on the U1 only slots 1-4 are toolheads): use it;
  they loaded it for a reason;
- none, and the analysis says `second support material would pay`: ask once,
  naming the gain (which piece, how much underside) and what loading it takes (one
  dropdown in the slicer, their own preset `PLA (X2D 0.4HS)` / `PETG (Snapmaker U1
  0.4 Stainless Steel)`, in a free slot, then save and run again). A no is the usual
  answer: build single-material, don't bring it up again in the report;
- none, and it wouldn't pay: single material, say nothing.

Never set a zero gap without it: same-material supports weld on. `build_tuned.py`
refuses.

Temperatures for the pairing (Snapmaker's U1 guide; filament settings, so mention
them, don't write them):
- PETG model, PLA interface: PLA at 230 °C, PETG bed 65 °C;
- PLA model, PETG interface: PETG at 265 °C with max volumetric speed 10 mm³/s,
  PLA at 230 °C, bed 65 °C.
On the X2D these are the U1's numbers, a starting point; the user's own X2D
filament presets are the reference there. The X2D has two nozzles fed from AMS:
put the interface material on the *other* nozzle from the model's (Bambu Studio's
filament grouping, "Auto For Flush" usually does it; tell the user to check), or
every interface layer costs a filament change and a purge.

**No second material:** keep the profiles' gap rule, `ceil(0.15 / layer) × layer`
(0.16 at 0.16 mm layers) with their gapped interface (3 layers, spacing 0.2). The
slicer rounds Z gaps **up to whole layers**: "0.24" at 0.16 mm layers is really
0.32 and the underside droops. Don't write fractional-layer gaps.

## 4. Finish: brim, raft, ironing, walls (every piece, supported or not)

The analysis's `finish:` line, per piece. **Brim**: a piece that is tall for the foot it stands on gets a brim of its own: the
analysis marks it `TIPPY->brim` (under 50 mm² of contact, or a height more than 8x
the foot's width, sqrt of the contact area). That catches ZKULL's head (8 mm²) and
hat (9 mm²), its body (74 mm tall on 67 mm²) and Jack (150 mm on 56 mm²), and not the
legs (40 mm on 503 mm²). Use `brim_type outer_only`, width 8 (ABS 10), gap 0.2, the
profiles' convention, and leave the global brim alone: a brim on pieces that sit
fine only adds cleanup and marks on the bottom edge. The profiles already carry
width 8 and gap 0.2 as the global values the brim falls back to, so usually only
`brim_type` goes in the plan; add width/gap only where the project's differ.

**Raft** (`raft_layers 2`, per piece): a foot that tapers to a feather edge, much
more sloped underside near the plate than area touching it (profiles section 5:
feathered bottoms can't adhere; lift them on a raft). Not under fins: they and
their bed pad already hold the foot, and a raft would lift them off the plate too.

**Ironing** stays off (the profiles' default) unless the user says a level top
shows. The analysis notes pieces with 300 mm² or more of level top, but on a
figure's parts those are mostly sockets and glued faces (ZKULL: all of them), so
it's a line in the report, not a setting.

**Walls** for a piece that carries load: 4-5 on that piece (section 5).

## 5. Everything else: keep the preset unless this model says otherwise

Walls, shells, infill, speeds, temperatures, seams: the preset is the user's
quality choice and the profiles are tuned and documented. Change one only with a
reason from *this* model, and give the reason:

- a functional part under load: more walls **per object** (the profiles' own
  advice: "add walls per object for functional PETG parts rather than raising the
  default");
- a feature the preset can't print (wall thinner than two lines...).

"It saves time" is not a reason on a quality print. Cutting the 9 top / 7 bottom
shells of the U1's `0.16 PLA` preset to 6/4 on an early ZKULL tune was a mistake,
reverted.

## 6. Out of scope: say it, don't do it

- Filament slots, temperatures, colours, printer settings, plate layout: the user
  sets these in the slicer. A filament slot carries dozens of per-filament arrays;
  editing them by hand produces presets the slicer only half believes.
- Slicing: the slicers' command lines don't work on this machine (Snapmaker Orca
  2.4.0 rejects the file's version, then crashes with `--allow-newer-file`).
  Verification is reading the file back; the user checks the slice preview.
- Loose pieces (a body hanging over the rest of the model): report with its height.
  The slicer's supports must hold it, so keep supports on for that piece.
