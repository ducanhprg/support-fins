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

## 2. Ask only what the file can't say

One question, and only if it isn't obvious: a display piece, or does it carry load,
and in which direction? A figure or ornament is display; say "this looks like a
display piece" and confirm. Don't ask about quality, material or printer; the
project already answered.

## 3. Analyze every piece

```
node --max-old-space-size=12000 .claude/skills/print-tune/scripts/analyze_pieces.mjs "<project>.3mf" --inspect <work>/project.json --json <work>/pieces.json
```

About 30-60 s per million triangles. One line per piece: height and bed contact
(`TIPPY->brim` when it's tall for its foot), overhang regions, the fins the engine
would build at this pose (walls, tines, grams), every overhang that still needs a
support (area, height, over the plate or over the part), what's not counted (too
tight for any support, or near the bed), loose pieces, and a verdict: `fins-only`,
`supports` or `none-needed`. The fins are built exactly as the CLI builds them,
with the piece's own material and layer height.

For pieces standing on a point or an edge with little contact, add
`--suggest "Name,Name"` to rank other poses (slow, ~20 s per piece). Recommend a
turn only when it's clearly better; see the playbook.

## 4. Decide, then write the plan

Apply `references/playbook.md` to the inspection and the analysis. Typical outcome:

- `fins-only` pieces: fins merged, `enable_support: 0` for that piece;
- `none-needed` pieces: left alone;
- supports from everywhere if any `supports` piece has an overhang over the part;
- the support interface in the other material when a free slot holds it
  (zero gap); otherwise nothing written (the preset's gap stays) and the report
  says what to load in which slot for next time;
- a per-piece brim for every `TIPPY->brim` piece;
- the rest of the preset kept.

Make the fins for each fins-only piece (N is its `cli_index`, the material its
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
    "Head": {"overrides": {"brim_type": "outer_only", "brim_width": "8", "brim_object_gap": "0.2"}}
  }
}
```

Only put a setting in the plan when it changes something, and know the reason for
each one; the report has to state it.

## 5. Build and verify

```
python .claude/skills/print-tune/scripts/build_tuned.py "<project>.3mf" <work>/plan.json
python .claude/skills/print-tune/scripts/verify_tuned.py "<project>.3mf" "<project> - tuned.3mf" <work>/plan.json
```

The builder writes `<project> - tuned.3mf` beside the original, copying every entry
it doesn't change byte for byte. It refuses a zero gap without a low-adhesion
interface material, fins cut for the wrong layer height, and writing over the input.
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
| Head | tree supports, PLA interface, brim | dome overhangs fins can't reach; 8 mm² on the bed |

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
