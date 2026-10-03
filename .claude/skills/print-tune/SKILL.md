---
name: print-tune
description: Fine-tune a 3D print project the user saved from their slicer (a .3mf from Bambu Studio for the Bambu X2D, or Snapmaker Orca for the Snapmaker U1) into the best settings for that model, writing "<name> - tuned.3mf" beside it. Reads what the user wants from the project itself (printer, preset and layer height, material and colour per piece, plates), weighs it against their own tuned profiles in C:\Work\3d_print_profiles, analyzes every piece with the Support Fins engine, bakes breakaway fins into the pieces they fully hold, and sets supports, support interface, brims and per-piece overrides on merit. Use this whenever the user hands over a .3mf (or asks about one in Downloads) and wants it optimized, tuned, made "best quality", checked before printing, or asks which supports/fins/settings a model needs, even if they don't say "tune" or name the skill; also when they say they've set a print up in the slicer and want it finished.
---

# print-tune

Turn a print the user has set up in their slicer into the best version of itself.
Their workflow: they open the model in Bambu Studio (X2D) or Snapmaker Orca (U1),
pick printer, preset, materials, colours and plates, and save. That saved project
is the brief: it says what they want. This skill reads it, decides the rest on
merit, and writes a tuned copy. It never overwrites their file.

The judgement behind every decision lives in `references/playbook.md`. Read it
before deciding anything; it is short and each rule says why. File-format details
are in `references/project-3mf.md`, for when something doesn't fit the scripts.

Paths below are relative to the repository root (this skill lives in the
support-fins repo, next to the engine it calls). Put intermediate files in a
scratch folder (your scratchpad if you have one), not next to the user's model.
Large models need memory: always run node with `--max-old-space-size=12000`.

## 1. Read the project

```
python .claude/skills/print-tune/scripts/inspect_project.py "<project>.3mf" --json <work>/project.json
```

It prints the printer, preset, layer height, key support/brim/shell values, every
filament slot (used or free), every object with its slot, material, plate and
per-object overrides, the free slots that could print a support interface, and the
matching folder in the user's profiles repo.

Then read the user's profiles for this printer and material: `PROGRESS.md` in
`C:\Work\3d_print_profiles` (the sections on supports, walls/shells and machine
knowledge), and the process preset JSON the inspector found. They explain why each
non-obvious value is what it is. They are the reference to reason from: keep what
they get right for this model, and change what this model needs, saying why.

If the printer isn't the X2D or the U1 (a designer's file, e.g. an H2D project),
don't build a tuned copy: it would carry someone else's machine presets. Analyze it
anyway and report the analysis plus the plan you'll apply after they re-save it
for the X2D or U1, with everything they must set in that one re-save (printer,
process, filament, interface material). The playbook's section 1 has the details.

## 2. Analyze every piece

```
node --max-old-space-size=12000 .claude/skills/print-tune/scripts/analyze_pieces.mjs "<project>.3mf" --inspect <work>/project.json --json <work>/pieces.json --paint <work>
```

About 30-60 s per million triangles. It walks each piece through the user's
decision order and prints the answer to every step:

1. **Does it need support?** Its overhang regions, minus what doesn't count: near
   the bed (top under 2 mm), tight gaps over the part (a support would fuse them),
   slivers. Nothing left: `none-needed`.
2. **Can fins hold it?** The fins the engine builds at this pose (walls, tines,
   grams), exactly as the CLI builds them with the piece's material and layer
   height. All of it held: `fins-only`.
3. **What fins can't hold**, one `spot` line each: area, height, over the plate or
   over the part, how flat, how wide, and *why no fin* (curved, too short, the part
   in the way...). Fins on the rest: `fins+paint` (`--paint` writes the spots'
   triangles to `<work>/paint-<name>.json`). No fin anywhere: `supports`.
4. **The support for those spots only**, a `support:` line: tree or normal, the
   style (organic, hybrid, grid), manual (painted spots, beside fins) or auto, the
   gap, each with its reason. `second support material would pay:` marks a piece
   whose supported underside is big enough for a zero-gap interface to show.
5. **Finish**, for every piece, needing support or not, a `finish:` line: a brim
   for a piece tall for its foot, a raft for a feathered foot without fins; and
   `note:` lines for what's the user's call (ironing a level top that may be a
   mating face).

To rank other poses for pieces standing on a point or an edge with little
contact, add `--suggest "Name,Name"` (slow, ~20 s per piece). Put it on the first
run when the inspection already makes those pieces obvious; otherwise re-run with
`--only "Name,Name" --suggest "Name,Name"` so the other pieces aren't analyzed
twice. Recommend a turn only when it's clearly better; see the playbook.

## 3. Ask only what the file can't say

At most two questions, in one message, and only when they change the plan:

- **Display, or does it carry load (and which way)?** Only if it isn't obvious. A
  figure or ornament is display; say "this looks like a display piece" and move on.
  A load-bearing piece gets more walls on that piece (the profiles' advice).
- **A second material for the support interface?** Only when the analysis says it
  would pay and no free slot already holds one. The user usually says no: then the
  single-material recipe stands, no follow-up.

Don't ask about quality, material, printer, or anything the preset decides.

## 4. Decide, then write the plan

Apply `references/playbook.md` to the inspection, the analysis and the answers:

- `fins-only`: fins merged, `enable_support: 0` for that piece;
- `fins+paint`: fins merged, the `support:` overrides (manual), its paint file as
  `paint_supports`: the slicer supports only those spots;
- `supports`: the `support:` overrides (auto), no fins;
- every piece: its `finish:` overrides; extra walls if it carries load;
- `support_on_build_plate_only: 0` when a supported spot sits over the part;
- the zero-gap interface recipe only with a second material (asked, or already in
  a free slot); otherwise the preset's gap stays;
- the rest of the preset kept.

Make the fins for each fins-only and fins+paint piece (N is its `cli_index`, the material its
family in lower case, the layer height that piece prints at):

```
node --max-old-space-size=12000 plugins/cli/support-fins.js "<project>.3mf" --object N --material pla|petg --layer-height <h> --fins-only -o <work>/fins-<name>.stl
```

Write `<work>/plan.json` (fins paths relative to the plan):

```json
{
  "settings": {"support_on_build_plate_only": "0", "support_top_z_distance": "0",
               "support_interface_filament": "2", "support_interface_spacing": "0",
               "support_bottom_interface_spacing": "0", "support_interface_top_layers": "3",
               "support_interface_pattern": "rectilinear_interlaced"},
  "objects": {
    "Legs": {"overrides": {"enable_support": "0"}, "fins": "fins-Legs.stl",
             "fin_layer_height": 0.16, "fin_material": "petg"},
    "Head": {"overrides": {"support_type": "tree(manual)", "brim_type": "outer_only"},
             "fins": "fins-Head.stl", "fin_layer_height": 0.16, "fin_material": "petg",
             "paint_supports": "paint-Head.json"},
    "jack1a.stl": {"overrides": {"support_style": null}}
  }
}
```

Only put a setting in the plan when it changes something, and know the reason for
each one; the report has to state it. A per-object override set to `null` removes
it (a designer's machine-specific setting; see the playbook).

## 5. Build and verify

```
python .claude/skills/print-tune/scripts/build_tuned.py "<project>.3mf" <work>/plan.json
python .claude/skills/print-tune/scripts/verify_tuned.py "<project>.3mf" "<project> - tuned.3mf" <work>/plan.json
```

The builder writes `<project> - tuned.3mf` beside the original, copying every entry
it doesn't change byte for byte. It refuses a zero gap without a low-adhesion
interface material, fins cut for the wrong layer height, fins on a piece whose
automatic supports are on, and writing over the input.
If it refuses, the plan is wrong: fix the plan, don't work around the guard.

The verifier checks the result as a slicer would read it: only the planned
settings and overrides differ, plates are intact, XML parses, and every merged fin
sits exactly where the engine placed it (checked with the engine's own reader).
Don't report success unless it prints `VERIFIED`.

## 6. Report

Keep it short and concrete. Use this shape:

```
Tuned: <path to "- tuned.3mf">  (verified; not sliced)

| Piece | What it gets | Why |
|---|---|---|
| Legs | fins (7 walls, 1.1 g), no slicer supports | fins hold its only overhang |
| Head | fins (14 walls), organic tree supports painted on the chin only, brim | fins hold 97%; the chin is too short for a wall; 8 mm² on the bed |

Settings changed (each with its reason): ...
Kept from your preset: ...

Before you slice: <anything the user must do in the slicer: load the interface
filament in slot N, temperatures to consider>
Check in the preview: <where supports should and shouldn't appear, brims, fins>
```

Say plainly what wasn't verified: nothing here slices the file. The slicers'
command lines don't work on this machine, so the user's slice preview is the final
check. When they report back how a print came off, that's evidence for the next
tune: note it in the report and, if it changes a rule, in the playbook.
