---
name: print-tune
description: Fine-tune a 3D print project the user saved from their slicer (a .3mf from Bambu Studio for the Bambu X2D, or Snapmaker Orca for the Snapmaker U1) into the best settings for that model, writing "<name> - tuned.3mf" beside it. Reads what the user wants from the project itself (printer, preset and layer height, material and colour per piece, plates), weighs it against their own tuned profiles in C:\Work\3d_print_profiles, decides per piece whether it needs no support, breakaway fins (Support Fins engine) or organic tree supports, and sets supports, brims and per-piece overrides on merit. Use this whenever the user hands over a .3mf (or asks about one in Downloads) and wants it optimized, tuned, made "best quality", checked before printing, or asks which supports/fins/settings a model needs, even if they don't say "tune" or name the skill; also when they say they've set a print up in the slicer and want it finished.
---

# print-tune

Turn a print the user has set up in their slicer into the best version of itself.
They pick printer, preset, materials, colours and plates in Bambu Studio (X2D) or
Snapmaker Orca (U1) and save: that project is the brief. This skill decides the
rest per piece and writes "<name> - tuned.3mf" beside it, never over it.

**The core decision, per piece: no support, fins, or trees**, whichever suits that
piece. `analyze_pieces.mjs` makes it with numbers (references/playbook.md says why).
A tune is one command and its summary; don't rebuild the analysis by hand.

## 1. Run it

```
python .claude/skills/print-tune/scripts/tune.py "<project>.3mf" --work <scratch>/tune
```

About 30-60 s per million triangles. It inspects the project, **checks every
piece's pose first**, then decides every piece, makes the fins at the coverage it
chose, writes the plan, builds and verifies. It prints a pose block, one line per
piece (verdict and why), the overrides it set, notes, and `VERIFIED` with the
output path. `--analyze-only` decides and stops. A project for another printer (a
designer's file) is analyzed but not built: tell the user what to set in one
re-save (printer, process, filament), then run again.

## 2. The pose: always answered before the tune goes on

The whole print depends on the pose: which faces overhang, where the support scars
land, what stands on the plate. So every tune checks it, and when a turn (or a
trade that gives up a foot) would clear the visible side, `tune.py` stops with exit
code 2 before building anything. Show the user, per piece:

- **what it took as visible** (`visible: outside (auto: 48% faces inward, a shell)`).
  They correct it when it's wrong: `--visible "Name=all"`, `=outside`, or directions
  in the pose as placed, `=front,top` (front is -Y, toward the viewer).
- **the offer**: the turn, the visible overhang before and after, the footing, and
  the note that a turn changes how layer lines cross the visible side.

Then run again with their answer: `--rotate "Name=X,Y"` (or they turn it in the
slicer, world-axis fields X first then Y, and re-save) or `--keep-pose "Name"`.
Never pick for them, and never skip the check. The thresholds and their evidence
are in the playbook (section 2, Pose).

## 3. Read the summary, ask only what it flags

- `same name as another object: not tuned`: ask them to rename those in the slicer.
- `a second support material would pay`: ask once, only for a piece whose supports
  touch a visible surface; the usual answer is no.
- A display piece vs a load-bearing one only if it isn't obvious (more walls on a
  load-bearing piece).

For one piece by hand: `scripts/pose_visible.mjs "<project>.3mf" <cli_index>
<threshold> [--visible ...] [--all]` prints the ranking.

## 4. Report

```
Pose: <per piece: kept / turned X,Y, and what shows>
Tuned: <path>  (verified; not sliced)
| Piece | Verdict | Why |  (from the summary, one line each)
Changed: <settings and overrides, each with its reason>
Before you slice / check in the preview: <what the notes say>
```

Nothing here slices (the slicers' command lines don't work on this machine), so
the user's preview is the final check. When they report how a print came off,
that's evidence: say how it changes the decision and, if it does, update the
thresholds in analyze_pieces.mjs and the playbook.

Details: `references/playbook.md` (the judgement, the thresholds and their
evidence), `references/project-3mf.md` (file format), and the single steps
(`inspect_project.py`, `analyze_pieces.mjs --verbose`, `plugins/cli/support-fins.js`,
`build_tuned.py`, `verify_tuned.py`) when a project needs something tune.py can't do.
