#!/usr/bin/env python3
"""
What a slicer-saved project 3MF says the user wants: printer, process preset,
layer height, filaments per slot, every object with its filament and plate,
per-object overrides, and which spare slots could print a support interface.

    python inspect_project.py PROJECT.3mf [--profiles C:/Work/3d_print_profiles] [--json out.json]

Reads Bambu Studio and Snapmaker Orca projects (both write Metadata/
project_settings.config and Metadata/model_settings.config). Read-only.
"""
import argparse, json, os, re, sys, zipfile

# Material pairs that bond weakly, so one can print the support interface under
# the other with a zero gap (Snapmaker's U1 guide: PLA with PETG, PLA with TPU).
LOW_ADHESION = {frozenset({'PLA', 'PETG'}), frozenset({'PLA', 'TPU'})}

# Physical filament positions per printer. The U1 has four toolheads, so a project
# slot above 4 (a designer's file often carries more) can't print as it stands.
MAX_SLOTS = {'U1': 4}

PROCESS_KEYS = [
    'layer_height', 'initial_layer_print_height', 'wall_loops', 'top_shell_layers', 'bottom_shell_layers',
    'top_shell_thickness', 'bottom_shell_thickness', 'sparse_infill_density', 'sparse_infill_pattern',
    'enable_support', 'support_type', 'support_style', 'support_on_build_plate_only', 'support_threshold_angle',
    'support_top_z_distance', 'support_bottom_z_distance', 'support_object_xy_distance',
    'support_filament', 'support_interface_filament', 'support_interface_top_layers',
    'support_interface_bottom_layers', 'support_interface_spacing', 'support_bottom_interface_spacing',
    'support_interface_pattern', 'support_interface_not_for_body',
    'brim_type', 'brim_width', 'brim_object_gap', 'seam_position', 'wall_generator',
    'enable_prime_tower', 'print_sequence', 'filament_map_mode',
]


def family(t):
    """PLA-CF, PLA Matte... -> PLA; PETG-HF -> PETG. For the adhesion pairs only."""
    t = (t or '').upper()
    for f in ('PETG', 'PLA', 'TPU', 'ABS', 'ASA', 'PA', 'PC'):
        if t.startswith(f):
            return f
    return t


def matrix(s):
    v = [float(x) for x in s.split()] if s else None
    return v if v and len(v) == 12 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--profiles', default='C:/Work/3d_print_profiles')
    ap.add_argument('--json')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    z = zipfile.ZipFile(a.project)
    names = set(z.namelist())
    ps = json.loads(z.read('Metadata/project_settings.config'))
    ms = z.read('Metadata/model_settings.config').decode('utf-8') if 'Metadata/model_settings.config' in names else ''
    root = z.read('3D/3dmodel.model').decode('utf-8')
    app = re.search(r'<metadata name="Application">([^<]*)</metadata>', root)

    nfil = len(ps.get('filament_type', []))
    filaments = [{'slot': i + 1, 'type': ps['filament_type'][i],
                  'colour': (ps.get('filament_colour') or [None] * nfil)[i],
                  'preset': (ps.get('filament_settings_id') or [None] * nfil)[i]} for i in range(nfil)]

    # build items and their components (placement in plate space, row-vector 3x4)
    items = {m.group(1): matrix(m.group(2)) for m in re.finditer(
        r'<item objectid="(\d+)"[^>]*?transform="([^"]+)"', root)}
    # the CLI's and the engine's object order (--object N): build items, in file order
    build_order = [m.group(1) for m in re.finditer(r'<item objectid="(\d+)"', root)]
    comps = {}
    for m in re.finditer(r'<object id="(\d+)"[^>]*>\s*<components>(.*?)</components>', root, re.S):
        comps[m.group(1)] = [{'path': c.group(1).lstrip('/'), 'objectid': c.group(2), 'transform': matrix(c.group(3))}
                             for c in re.finditer(r'<component p:path="([^"]+)" objectid="(\d+)"[^>]*?transform="([^"]+)"',
                                                  m.group(2))]

    plates = []
    for p in re.findall(r'<plate>(.*?)</plate>', ms, re.S):
        meta = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', p.split('<model_instance>')[0]))
        plates.append({'id': int(meta.get('plater_id', len(plates) + 1)), 'name': meta.get('plater_name', ''),
                       'objects': re.findall(r'key="object_id" value="(\d+)"', p)})

    objects = []
    for oid, body in re.findall(r'<object id="(\d+)">(.*?)</object>', ms, re.S):
        head = body.split('<part')[0]
        meta = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', head))
        ext = int(meta.get('extruder', '1') or 1) or 1
        parts = []
        for pid, sub, pb in re.findall(r'<part id="(\d+)" subtype="([^"]+)">(.*?)</part>', body, re.S):
            pm = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', pb))
            pe = int(pm.get('extruder', '0') or 0) or ext
            parts.append({'name': pm.get('name'), 'subtype': sub, 'slot': pe})
        slots = sorted({p['slot'] for p in parts if p['subtype'] == 'normal_part'} or {ext})
        t = items.get(oid)
        rotated = bool(t) and any(abs(t[i] - (1 if i in (0, 4, 8) else 0)) > 1e-6 for i in range(9))
        objects.append({
            'id': oid, 'name': meta.get('name'), 'slot': ext, 'slots': slots,
            'cli_index': build_order.index(oid) + 1 if oid in build_order else None,
            'materials': sorted({family(filaments[s - 1]['type']) for s in slots if 0 < s <= nfil}),
            'overrides': {k: v for k, v in meta.items() if k not in ('name', 'extruder')},
            'parts': parts,
            'plate': next((p['id'] for p in plates if oid in p['objects']), None),
            'item_transform': t, 'item_rotated': rotated, 'components': comps.get(oid, []),
        })

    used = sorted({s for o in objects for s in o['slots']})
    used_mats = {family(filaments[s - 1]['type']) for s in used if 0 < s <= nfil}
    free = [f for f in filaments if f['slot'] not in used]
    printer = ps.get('printer_model') or ps.get('printer_settings_id')
    max_slots = next((n for k, n in MAX_SLOTS.items() if printer and k in printer), None)
    interface = [dict(f, loadable=max_slots is None or f['slot'] <= max_slots) for f in free
                 if any(frozenset({family(f['type']), m}) in LOW_ADHESION for m in used_mats)]
    prof_dir = None
    if printer and 'X2D' in printer:
        prof_dir = os.path.join(a.profiles, 'BambuLab', 'X2D')
    elif printer and 'U1' in printer:
        prof_dir = os.path.join(a.profiles, 'Snapmaker', 'U1')
    proc = ps.get('print_settings_id')
    proc_file = os.path.join(prof_dir, 'process', f'{proc}.json') if prof_dir and proc else None

    out = {
        'file': a.project, 'application': app.group(1) if app else None, 'version': ps.get('version'),
        'printer': printer, 'printer_preset': ps.get('printer_settings_id'), 'nozzle': ps.get('nozzle_diameter'),
        'process_preset': proc, 'process': {k: ps.get(k) for k in PROCESS_KEYS if k in ps},
        'filaments': filaments, 'objects': objects, 'plates': plates,
        'slots_used': used, 'materials_used': sorted(used_mats),
        'free_slots': [f['slot'] for f in free], 'max_slots': max_slots,
        'interface_candidates': interface,
        'profiles': {'dir': prof_dir if prof_dir and os.path.isdir(prof_dir) else None,
                     'process_file': proc_file if proc_file and os.path.isfile(proc_file) else None,
                     'progress_md': os.path.join(a.profiles, 'PROGRESS.md') if os.path.isfile(os.path.join(a.profiles, 'PROGRESS.md')) else None},
    }
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=1, ensure_ascii=False)

    # human summary
    p = out['process']
    print(f"{os.path.basename(a.project)}  ({out['application']} {out['version']})")
    print(f"  printer  {out['printer_preset']}   process  {proc}   layer {p.get('layer_height')} mm")
    print(f"  walls {p.get('wall_loops')} · top/bottom {p.get('top_shell_layers')}/{p.get('bottom_shell_layers')} · "
          f"infill {p.get('sparse_infill_density')} {p.get('sparse_infill_pattern')}")
    print(f"  supports {'on' if p.get('enable_support') == '1' else 'off'} {p.get('support_type')}/{p.get('support_style')}"
          f" · plate-only {p.get('support_on_build_plate_only')} · {p.get('support_threshold_angle')}° · "
          f"top Z {p.get('support_top_z_distance')} · interface slot {p.get('support_interface_filament')} · brim {p.get('brim_type')}")
    print('  filaments:')
    for f in filaments:
        tag = 'used' if f['slot'] in used else 'free'
        print(f"    {f['slot']:>2} {f['type']:<6} {f['colour'] or '':<9} {f['preset']}  [{tag}]")
    print('  objects:')
    for o in objects:
        ov = f"  overrides {o['overrides']}" if o['overrides'] else ''
        print(f"    plate {o['plate']}  slot {','.join(map(str, o['slots']))} {'/'.join(o['materials'])}  {o['name']}"
              f"{'  (rotated on plate)' if o['item_rotated'] else ''}{ov}")
    print(f"  interface candidates (free slot, low-adhesion with {'/'.join(sorted(used_mats))}): "
          + (', '.join(f"slot {f['slot']} {f['type']}" + ('' if f['loadable'] else f' (beyond the {max_slots} toolheads)')
                       for f in interface) or 'none'))
    pr = out['profiles']
    print(f"  profiles: {pr['dir'] or 'no matching printer folder'}; process preset file: {pr['process_file'] or 'not in repo'}")


if __name__ == '__main__':
    sys.exit(main())
