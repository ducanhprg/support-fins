---
name: print-cut
description: Split a 3D model into printable parts with hidden alignment pins (dowels or pegs), the way CatPin does in Blender, but done for the user. Cuts a model that is too big for the bed (Snapmaker U1 270 mm, Bambu X2D 256 mm), or where the user asks (at the neck, the waist, "in half"), places pins sized to each cut face, checks no hole breaks through, lays every part on its best face and writes the parts plus a 3MF ready for the slicer. Use it whenever the user wants a model cut, split, sliced into pieces, printed bigger than the bed, scaled up, or given connectors / pins / keys / dowels between pieces, even if they only say "it doesn't fit"; not for flexi or moving joints (not built yet).
---

# print-cut

Cutting a model is three decisions and a lot of fiddly geometry. Make the decisions
with the user's intent and the numbers; let `scripts/cut.py` do the geometry and the
checks. Never hand-place holes.

## 1. Look before cutting

```
python .claude/skills/print-cut/scripts/cut.py "<model>" [--object NAME] [--scale S] --sections z
```

Input: STL/OBJ/PLY, or a 3MF saved by Bambu Studio / Snapmaker Orca (`--object`
picks a piece; it lists them when there are several). Overlapping shells are merged
as the slicer would. `--sections x|y|z|long` prints the section area along an axis
with the islands at each height: a narrow place with one island is where a seam
hides (neck, waist, wrist, a groove in the sculpt). Islands under 200 mm² mean the
cut would chop off a tip or a strap.

## 2. Decide

- **Where.** If the user named places ("cut the head off", "in half at the
  waist"), turn them into planes with `--sections`: pick the narrow, single-island
  height closest to what they said. Otherwise leave `--cut` out: the script cuts
  until every part fits the bed, at necks near where a cut is due, along the world
  axes or the part's own long axis. Level cuts (`z=`) are the cleanest on a figure;
  say so if the automatic plan is tilted and the user would prefer level ones.
- **Connector.** `dowel` (default): holes in both halves and separate pins. Both
  halves can lie on their flat cut face, the best foot a part can have. `peg` when
  the user doesn't want loose pins (that half can't lie on that face). `none` for
  glue only.
- **Fit.** `--clearance` 0.15 mm per side suits PLA/PETG at 0.4 mm on these
  printers: snug, glued. 0.2 for a looser fit or a rough-surface material; 0.1 for
  a press fit. `--wall` 1.6 mm of material stays round every hole.
- **Printer.** `--printer u1` (270 mm), `x2d` (256 mm), `x2d-dual` (235.5 mm wide:
  the area both X2D nozzles reach, for a multi-material part).

## 3. Cut

```
python .claude/skills/print-cut/scripts/cut.py "<model>" [--object NAME] [--scale S]
       [--printer u1|x2d|x2d-dual] [--cut "z=120" ...] [--connector dowel|peg|none]
```

About 30 s for a million triangles. It prints each cut (section area, and per
island the pins: count, diameter, depth each side, or why it gets glue), each part
(size, fits or not, separate pieces, the face it lies on), the pins to print, and a
volume check (parts plus pins should match the model within a few cm³). Output in
`<model> - cut/`: `part_NN.stl`, `pins.stl`, `<model> - cut.3mf` (parts lying down,
spread out: Arrange in the slicer), `report.json`.

What the script guarantees: two pins or more per island where they fit (one round
pin lets the halves turn; one is used only when two can't fit, and it says so),
every hole with a wall all round (pins move inward until they have one, or the
island gets glue), every part closed and on the plate.

## 4. Report, then the print

```
Cut: <n> parts for <printer> (<scale>), <connector>
| Part | Size | Lies on | Notes |
Cuts: <where and why there>, pins per cut
Pins: <count x Ø x length>, print lying on their flat; glue: <islands without pins>
Next: open the 3MF, set printer and filament, save, then print-tune it (the pose check runs per part)
```

Not built yet: flexi / print-in-place joints. Say so if asked, and offer the joint
kit idea (a negative part that carves the gap and a joint part that prints inside it).
