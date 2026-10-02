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
the profiles repo doesn't cover it. Say so and suggest re-saving it in their slicer
for the X2D or U1 first, which is their workflow; analysis can still run meanwhile.

## 2. Per piece: fins, slicer supports, or nothing

`analyze_pieces.mjs` gives each piece a verdict. Trust it, and know what's behind it:

- **fins-only**: every real overhang is held by a fin, there's no loose piece, and
  at least one wall was built. Plan: merge the fins into that piece and set its
  `enable_support` to `0`. This is the tool's whole point: no tree supports, no scars.
- **supports**: some overhang no fin can reach (a curved roof, a ledge with the part
  in the way, an overhang over the part itself), or a loose piece. Plan: leave the
  piece to the slicer's supports, and don't add fins to it. The slicer doesn't know
  fins exist; it would grow tree supports between the fin walls as well.
- **none-needed**: nothing to support.

Not counted against fins-only: overhangs *too tight* for any support (print-in-place
gaps: a support there would fuse the joint) and *near-bed* ones under 1 mm (the
first layers carry them). Report both, don't act on them.

On large organic figures (ZKULL's head, body and hat; Jack), expect "supports":
fins hold the straight runs but not the domes and the overhangs over the part. On
mechanical parts and simple figures (ZKULL's legs), fins often hold everything.

**Material.** The engine knows PLA and PETG. PETG fuses to supports far harder, so
its fins stand off further: always pass `--material petg` for a PETG piece.
Anything else (ABS, TPU, CF blends): don't fin it; say why.

**Tines are one layer tall** and must be cut for the layer height that piece prints
at (project layer height, or its own override). `build_tuned.py` refuses a mismatch.

**Pose.** Keep the pose the user placed. It's their plate layout and usually the
designer's choice of which faces show. Run `--suggest` only for pieces that stand
on a point or an edge with a small contact area, and *recommend* a turn only when
it is clearly better (a real face on the bed and much less overhang). The builder
doesn't rotate objects; the user does that in the slicer and re-runs.

## 3. Supports for the pieces that keep them

**Build plate only.** The profiles default to supports from the build plate only.
Turn that off (`support_on_build_plate_only: 0`) when a "supports" piece has bare
overhangs **over the part**: from the plate only, nothing reaches them and they
print into air (ZKULL: inside the head, under the hood, inside the hat). Then warn:
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

Use it only when `inspect_project.py` lists a loadable interface candidate, a free
slot holding the other material. On the U1 only slots 1-4 are toolheads. If there's
none, tell the user what to load in which slot (one dropdown in the slicer: their
own preset `PLA (X2D 0.4HS)` / `PETG (Snapmaker U1 0.4 Stainless Steel)`, etc.) and
either wait for them to re-save, or fall back as below. Never set a zero gap
without it: same-material supports weld on. `build_tuned.py` refuses.

Snapmaker also suggests PLA at 230 °C and the PETG bed at 65 °C for this pairing.
Those are filament settings, so mention them; don't write them.

**No second material:** keep the profiles' gap rule, `ceil(0.15 / layer) × layer`
(0.16 at 0.16 mm layers) with their gapped interface (3 layers, spacing 0.2). The
slicer rounds Z gaps **up to whole layers**: "0.24" at 0.16 mm layers is really
0.32 and the underside droops. Don't write fractional-layer gaps.

## 4. Brim: per piece, not global

A piece that stands on a point, an edge, or under ~50 mm² of contact (ZKULL's head
8 mm², hat 9 mm²) gets a brim of its own: `brim_type outer_only`, width 8 (ABS 10),
gap 0.2, which is the profiles' convention. Leave the global brim alone. A brim on
pieces that sit fine only adds cleanup and marks on the bottom edge.

## 5. Everything else: keep the preset unless this model says otherwise

Walls, shells, infill, speeds, temperatures, seams: the preset is the user's
quality choice and the profiles are tuned and documented. Change one only with a
reason from *this* model, and give the reason:

- a functional part under load: more walls **per object** (the profiles' own
  advice: "add walls per object for functional PETG parts rather than raising the
  default");
- a feature the preset can't print (wall thinner than two lines...).

"It saves time" is not a reason on a quality print. Cutting ZKULL's 9/7 shells to
6/4 was a mistake, reverted.

## 6. Out of scope: say it, don't do it

- Filament slots, temperatures, colours, printer settings, plate layout: the user
  sets these in the slicer. A filament slot carries dozens of per-filament arrays;
  editing them by hand produces presets the slicer only half believes.
- Slicing: the slicers' command lines don't work on this machine (Snapmaker Orca
  2.4.0 rejects the file's version, then crashes with `--allow-newer-file`).
  Verification is reading the file back; the user checks the slice preview.
- Loose pieces (a body hanging over the rest of the model): report with its height.
  The slicer's supports must hold it, so keep supports on for that piece.
