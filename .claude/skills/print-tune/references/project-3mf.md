# Where things live in a slicer project 3MF

Bambu Studio (X2D) and Snapmaker Orca (U1) write the same layout (both descend
from BambuStudio). The scripts handle all of this; read this when something
doesn't fit them.

| Entry | Holds |
|---|---|
| `3D/3dmodel.model` | build items (one per object, `transform` = placement on the plate) and assembly objects whose `<component p:path="/3D/Objects/....model" objectid=.. transform=..>` point at the meshes |
| `3D/Objects/*.model` | the meshes: `<object id><mesh><vertices>/<triangles>`. Bambu reuses `id="1"` in every file, so ids are scoped per file |
| `Metadata/project_settings.config` | JSON: every process, filament and printer value of the project, flattened. Filament values are arrays, one per slot (some one per slot per nozzle variant) |
| `Metadata/model_settings.config` | XML: per object `<metadata key="name|extruder|<any setting>">` (per-object overrides), its parts (`subtype normal_part / modifier_part / support_blocker...`, each may carry its own `extruder`), and `<plate>` blocks listing object ids |
| `Metadata/plate_N.png`, `slice_info.config`, `cut_information.xml`, `filament_sequence.json` | thumbnails and slicer state; copy untouched |

- **Transforms** are 12 numbers, row-vector 3x4: `world = local · A + t`, A = the
  first nine numbers row by row, t = the last three. A component's transform
  applies first, then its build item's. `build_tuned.py` composes and inverts them;
  `web/threemf.js` does the same independently, which is what `verify_fins.mjs` uses.
- **Fins in an object made of several parts** (a pair of shoes as one object):
  the builder merges them into the object's *first* component mesh, transformed
  into that component's own frame, so they land in the right place on the plate
  whatever the other components are. The slicer unions them like any overlapping
  bodies.
- **Object order**: the CLI's `--object N` and the engine's reader number objects
  by build item order, not model_settings order. `inspect_project.py` reports it as
  `cli_index`.
- **Names**: objects are matched by `name`. Two objects with one name are
  ambiguous only if the plan targets them; then ask the user to rename one.
- **The "modified" list**: `different_settings_to_system` is an array of
  `;`-joined key lists, one per preset; element 0 is the process preset. A key
  changed there should be listed, as the slicer itself does on save;
  `build_tuned.py` adds it.
- **Per-object overrides**: any process key can sit in an object's metadata
  (`enable_support`, `brim_type`, `layer_height`, `wall_loops`...). The slicer
  applies it to that object only.
- **Zip entry names** are UTF-8 with the zip's UTF-8 flag (`Assemblé_3.model`).
  Copy entries by name and they survive.
- **Slots and hardware**: the U1 has 4 toolheads, so slots above 4 don't print as
  they stand. The X2D has two nozzles fed from AMS; `filament_map` /
  `filament_map_mode` decide which nozzle each slot uses (Auto For Flush is fine).
- **Units**: `<model unit="millimeter">`. The reader converts other units; Bambu
  and Orca always write mm.

## Support painting

The slicer's support-painting tool stores its marks on the mesh triangles in
`3D/Objects/*.model`: `<triangle v1=".." v2=".." v3=".." paint_supports="4"/>` is a
whole triangle painted as an enforcer, `"8"` a blocker; longer hex strings are
triangles the brush split (PrusaSlicer's TriangleSelector encoding, kept by Bambu
Studio and Orca). With a per-object `support_type` of `tree(manual)` or
`normal(manual)` the slicer supports only enforcers. Triangle order is file order,
the order `web/threemf.js` reads them, so the engine's face indices are the
triangles' positions in the object's mesh (or meshes, in component order).
