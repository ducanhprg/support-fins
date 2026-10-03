#!/usr/bin/env python3
"""
Write "<project> - tuned.3mf": a copy of a slicer-saved project with the plan
applied -- print settings changed, per-object overrides added, and fins merged
into chosen objects' meshes. Every other entry in the package is copied byte for
byte, so plates, colours, filaments, thumbnails and the slicer's own metadata
survive untouched.

    python build_tuned.py PROJECT.3mf PLAN.json [--out "PROJECT - tuned.3mf"]

PLAN.json:
{
  "settings": {"support_on_build_plate_only": "0", ...},     # project_settings.config
  "objects": {
    "Legs": {"overrides": {"enable_support": "0"},           # model_settings.config, per object
             "fins": "fins-Legs.stl",                         # CLI --fins-only output, plate coords
             "fin_layer_height": 0.16, "fin_material": "petg"},
    "Head": {"overrides": {"support_type": "tree(manual)"},
             "fins": "fins-Head.stl", "fin_layer_height": 0.16, "fin_material": "pla",
             "paint_supports": "paint-Head.json"}                # analyze_pieces.mjs --paint
  }
}

An override set to null removes that key from the object: a designer's per-object
setting that shouldn't follow the print to another machine.

paint_supports marks the listed triangles (the object's own, in file order) as
support enforcers, as the slicer's support-painting tool would ("4" on the
triangle). With a manual support type the slicer then supports only those: the
spots the fins leave bare.

Refuses, rather than writes a file that would print badly:
  - a project for a printer the user's profiles don't cover (only the X2D and the
    U1): the copy would carry someone else's machine presets (--any-printer overrides);
  - a zero top Z gap whose interface filament is not a different, low-adhesion
    material from every object it would touch (supports would weld on);
  - fins cut for a layer height the object doesn't print at (tines must be one layer);
  - fins on an object whose automatic supports are on: the slicer doesn't know fins
    exist and would grow supports between them (turn its supports off, or make
    them manual and paint the spots the fins leave);
  - painted supports on an object whose supports are off (nothing would print);
  - writing over the input.
"""
import argparse, json, os, re, struct, sys, zipfile

LOW_ADHESION = {frozenset({'PLA', 'PETG'}), frozenset({'PLA', 'TPU'})}
PRINTERS = ('X2D', 'U1')   # the machines in the user's profiles repo


def family(t):
    t = (t or '').upper()
    for f in ('PETG', 'PLA', 'TPU', 'ABS', 'ASA', 'PA', 'PC'):
        if t.startswith(f):
            return f
    return t


def mat(s):
    v = [float(x) for x in s.split()]
    assert len(v) == 12, f'bad 3MF transform {s!r}'
    return v


def compose(a, b):
    """b applied after a, row-vector 3x4 (as the 3MF spec and web/threemf.js compose)."""
    m = [0.0] * 12
    for r in range(3):
        for c in range(3):
            m[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c]
    for c in range(3):
        m[9 + c] = a[9] * b[c] + a[10] * b[3 + c] + a[11] * b[6 + c] + b[9 + c]
    return m


def inverse(m):
    """Inverse of a row-vector affine: world = local @ A + t  ->  local = (world - t) @ A^-1."""
    a, b, c, d, e, f, g, h, i = m[:9]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    assert abs(det) > 1e-12, 'singular object transform'
    inv = [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
           (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
           (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]
    t = m[9:]
    tt = [-(t[0] * inv[c] + t[1] * inv[3 + c] + t[2] * inv[6 + c]) for c in range(3)]
    return inv + tt


def apply(m, p):
    return tuple(p[0] * m[c] + p[1] * m[3 + c] + p[2] * m[6 + c] + m[9 + c] for c in range(3))


def read_stl(path):
    data = open(path, 'rb').read()
    n = struct.unpack_from('<I', data, 80)[0]
    assert len(data) == 84 + 50 * n, f'{path}: not a binary STL'
    return [struct.unpack_from('<9f', data, 84 + i * 50 + 12) for i in range(n)]


def merge_fins(mesh_xml, mesh_object_id, tris, to_local):
    """Append the fin triangles to object `mesh_object_id`'s <mesh> in a part file."""
    m = re.search(r'<object id="' + re.escape(mesh_object_id) + r'"[^>]*>.*?</object>', mesh_xml, re.S)
    assert m, f'mesh object {mesh_object_id} not found'
    block = m.group(0)
    assert block.count('</vertices>') == 1 and block.count('</triangles>') == 1, 'object has no single <mesh>'
    base = block.count('<vertex ')
    index, verts, faces = {}, [], []
    for f in tris:
        ids = []
        for k in range(3):
            p = to_local((f[k * 3], f[k * 3 + 1], f[k * 3 + 2]))
            key = tuple(round(c, 6) for c in p)
            if key not in index:
                index[key] = len(verts)
                verts.append(p)
            ids.append(index[key])
        if len(set(ids)) == 3:
            faces.append(ids)
    ind = re.search(r'\n(\s*)<vertex ', block)
    pad = ind.group(1) if ind else '     '
    vx = ''.join(f'{pad}<vertex x="{p[0]:.9g}" y="{p[1]:.9g}" z="{p[2]:.9g}"/>\n' for p in verts)
    tx = ''.join(f'{pad}<triangle v1="{a + base}" v2="{b + base}" v3="{c + base}"/>\n' for a, b, c in faces)
    new = re.sub(r'(\n\s*)</vertices>', lambda mm: '\n' + vx.rstrip('\n') + mm.group(1) + '</vertices>', block, count=1)
    new = re.sub(r'(\n\s*)</triangles>', lambda mm: '\n' + tx.rstrip('\n') + mm.group(1) + '</triangles>', new, count=1)
    return mesh_xml[:m.start()] + new + mesh_xml[m.end():], len(verts), len(faces)


SKIP_TYPES = ('support', 'surface', 'other')   # web/threemf.js skips these meshes too


def components(root):
    """Root object id -> [(part path, mesh object id, component transform or None)]."""
    comps = {}
    for m in re.finditer(r'<object id="(\d+)"[^>]*>\s*<components>(.*?)</components>', root, re.S):
        comps[m.group(1)] = [(c.group(1).lstrip('/'), c.group(2), mat(c.group(3)) if c.group(3) else None)
                             for c in re.finditer(r'<component p:path="([^"]+)" objectid="(\d+)"[^>]*?(?:transform="([^"]+)")?\s*/?>',
                                                  m.group(2))]
    return comps


def object_meshes(zin, comps, oid, cache):
    """The mesh objects an object's triangles come from, in the order the engine
    reads them (web/threemf.js emitObject): [(path, mesh_oid, n_triangles, skipped)]."""
    out = []
    for path, mesh_oid, _ in comps[oid]:
        if path not in cache:
            cache[path] = zin.read(path).decode('utf-8')
        m = re.search(r'<object id="' + re.escape(mesh_oid) + r'"([^>]*)>(.*?)</object>', cache[path], re.S)
        assert m, f'mesh object {mesh_oid} not found in {path}'
        t = re.search(r'type="([^"]+)"', m.group(1))
        out.append((path, mesh_oid, m.group(2).count('<triangle '), bool(t and t.group(1) in SKIP_TYPES)))
    return out


def mesh_block(xml, mesh_oid):
    m = re.search(r'<object id="' + re.escape(mesh_oid) + r'"[^>]*>.*?</object>', xml, re.S)
    assert m, f'mesh object {mesh_oid} not found'
    return m


def paint_triangles(xml, mesh_oid, indices):
    """Mark triangles `indices` (0-based, this mesh's own order) as support enforcers."""
    m = mesh_block(xml, mesh_oid)
    want, k = set(indices), [0]

    def one(t):
        i = k[0]
        k[0] += 1
        if i not in want:
            return t.group(0)
        tag = re.sub(r'\s+paint_supports="[^"]*"', '', t.group(0))
        return tag[:-2].rstrip() + ' paint_supports="4"/>'
    block = re.sub(r'<triangle [^>]*/>', one, m.group(0))
    return xml[:m.start()] + block + xml[m.end():]


def painted(xml, mesh_oid):
    """Indices of this mesh's triangles painted as whole-triangle enforcers."""
    block = mesh_block(xml, mesh_oid).group(0)
    return [i for i, t in enumerate(re.findall(r'<triangle [^>]*/>', block)) if 'paint_supports="4"' in t]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('plan')
    ap.add_argument('--out')
    ap.add_argument('--any-printer', action='store_true',
                    help='tune a project for a printer the profiles do not cover')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    src = os.path.abspath(a.project)
    out = os.path.abspath(a.out or re.sub(r'\.3mf$', '', src, flags=re.I) + ' - tuned.3mf')
    assert out != src, 'refusing to write over the input project'
    plan = json.load(open(a.plan, encoding='utf-8'))
    SET = {k: str(v) for k, v in plan.get('settings', {}).items()}
    OBJ = plan.get('objects', {})
    plan_dir = os.path.dirname(os.path.abspath(a.plan))

    zin = zipfile.ZipFile(src)
    ps = json.loads(zin.read('Metadata/project_settings.config'))
    ms = zin.read('Metadata/model_settings.config').decode('utf-8')
    root = zin.read('3D/3dmodel.model').decode('utf-8')
    types = ps.get('filament_type', [])
    printer = ps.get('printer_model') or ps.get('printer_settings_id') or ''
    assert a.any_printer or any(p in printer for p in PRINTERS), (
        f'the project is set up for {printer!r}, not the X2D or the U1: re-save it for one '
        'of them in the slicer first (or pass --any-printer)')

    # object name -> model_settings block, slots, overrides
    objects, dup_slots = {}, {}
    for oid, body in re.findall(r'<object id="(\d+)">(.*?)</object>', ms, re.S):
        head = body.split('<part')[0]
        meta = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', head))
        ext = int(meta.get('extruder', '1') or 1) or 1
        slots = set()
        for sub, pb in re.findall(r'<part id="\d+" subtype="([^"]+)">(.*?)</part>', body, re.S):
            if sub == 'normal_part':
                pe = re.search(r'<metadata key="extruder" value="(\d+)"', pb)
                slots.add(int(pe.group(1)) or ext if pe else ext)
        slots = slots or {ext}
        name = meta.get('name')
        # a duplicated name is fine until the plan targets it: then it's ambiguous
        objects[name] = None if name in objects else {'id': oid, 'slots': slots, 'meta': meta}
        dup_slots[name] = dup_slots.get(name, set()) | slots
    for name in OBJ:
        assert name in objects, f'no object named {name!r}; have {sorted(objects)}'
        assert objects[name], f'two objects are named {name!r}: rename one in the slicer first'

    # --- guard: a zero gap needs a different, low-adhesion interface material ---
    merged = dict(ps, **SET)
    if float(merged.get('support_top_z_distance', '1') or 1) == 0 and merged.get('enable_support') == '1':
        islot = int(merged.get('support_interface_filament', '0') or 0)
        assert islot > 0, 'top Z distance 0 with the interface in the model\'s own filament would weld the supports on'
        imat = family(types[islot - 1])
        for name, slots in dup_slots.items():
            o = objects[name]
            own = o['meta'].get('enable_support', '1') if o else '1'
            if OBJ.get(name, {}).get('overrides', {}).get('enable_support', own) == '0':
                continue
            for s in slots:
                pair = frozenset({imat, family(types[s - 1])})
                assert pair in LOW_ADHESION, (f'interface slot {islot} is {imat}, and {name!r} prints in '
                                              f'{family(types[s - 1])}: they bond, a zero gap would weld')

    # --- fins / painting: supports the slicer adds must not land between fins ---
    def effective(name, key):
        ov = OBJ.get(name, {}).get('overrides', {})
        if key in ov:
            return ov[key] if ov[key] is not None else merged.get(key)
        return objects[name]['meta'].get(key, merged.get(key))
    for name, spec in OBJ.items():
        on = effective(name, 'enable_support') == '1'
        manual = (effective(name, 'support_type') or '').endswith('(manual)')
        if spec.get('fins'):
            assert not on or manual, (f'{name!r} gets fins but its automatic supports are on: the slicer would '
                                      'grow supports between the fins. Set its enable_support 0, or its '
                                      'support_type to tree(manual) and paint what the fins leave bare')
        if spec.get('paint_supports'):
            assert on, f'{name!r} has painted supports but its supports are off: nothing would print them'

    # --- fins: resolve where each object's mesh lives and its plate placement ---
    items = {m.group(1): mat(m.group(2)) for m in re.finditer(r'<item objectid="(\d+)"[^>]*?transform="([^"]+)"', root)}
    comps = components(root)
    changed, report, cache = {}, [], {}
    for name, spec in OBJ.items():
        if not spec.get('paint_supports'):
            continue
        o = objects[name]
        paint = json.load(open(os.path.join(plan_dir, spec['paint_supports']), encoding='utf-8'))
        meshes = object_meshes(zin, comps, o['id'], cache)
        total = sum(n for _, _, n, skip in meshes if not skip)
        assert paint['tris'] == total, (f'{name!r}: the paint list was made for {paint["tris"]} triangles, '
                                        f'the object has {total}: re-run the analysis on this project')
        start = 0
        for path, mesh_oid, n, skip in meshes:
            if skip:
                continue
            own = [f - start for f in paint['faces'] if start <= f < start + n]
            if own:
                xml = changed[path].decode('utf-8') if path in changed else cache[path]
                changed[path] = paint_triangles(xml, mesh_oid, own).encode('utf-8')
            start += n
        report.append(f'support painted on {len(paint["faces"])} triangles of {name!r}')
    for name, spec in OBJ.items():
        if not spec.get('fins'):
            continue
        o = objects[name]
        lh = float(spec.get('fin_layer_height', 0))
        want = float(o['meta'].get('layer_height', ps.get('layer_height')))
        assert abs(lh - want) < 1e-6, f'{name!r}: fins cut for {lh} mm layers, the object prints at {want} mm'
        c = comps.get(o['id'])
        assert c, f'{name!r} has no component mesh in 3D/3dmodel.model'
        path, mesh_oid, ct = c[0]
        place = compose(ct, items[o['id']]) if ct else items[o['id']]
        to_local = lambda p, inv=inverse(place): apply(inv, p)
        xml = (changed[path] if path in changed else zin.read(path)).decode('utf-8')
        xml, nv, nf = merge_fins(xml, mesh_oid, read_stl(os.path.join(plan_dir, spec['fins'])), to_local)
        changed[path] = xml.encode('utf-8')
        report.append(f'fins merged into {name!r} ({path}): {nv} vertices, {nf} triangles')

    # --- settings: values, and the "modified" flag the slicer keeps per preset ---
    before = {k: ps.get(k) for k in SET}
    ps.update(SET)
    diff = ps.get('different_settings_to_system')
    if diff and SET:
        diff[0] = ';'.join(sorted(set(k for k in diff[0].split(';') if k) | set(SET)))
    changed['Metadata/project_settings.config'] = json.dumps(ps, indent=4, ensure_ascii=False).encode('utf-8')

    # --- per-object overrides: replace a key the object already has, else add it;
    #     null removes it ---
    for name, spec in OBJ.items():
        for k, v in spec.get('overrides', {}).items():
            oid = objects[name]['id']
            m = re.search(r'<object id="' + oid + r'">\n(.*?)(?=\n\s*<part)', ms, re.S)
            head = m.group(0)
            if v is None:
                assert k not in ('name', 'extruder'), f'refusing to remove {k!r} from {name!r}'
                new = re.sub(r'\n\s*<metadata key="' + re.escape(k) + r'" value="[^"]*"/>', '', head)
            elif re.search(r'<metadata key="' + re.escape(k) + r'" value="[^"]*"/>', head):
                new = re.sub(r'(<metadata key="' + re.escape(k) + r'" value=")[^"]*("/>)', lambda mm: mm.group(1) + str(v) + mm.group(2), head)
            else:
                ind = re.search(r'\n(\s*)<metadata key="name"', head).group(1)
                new = head + f'\n{ind}<metadata key="{k}" value="{v}"/>'
            ms = ms[:m.start()] + new + ms[m.end():]
    changed['Metadata/model_settings.config'] = ms.encode('utf-8')

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            zout.writestr(zi, changed.get(info.filename, zin.read(info.filename)))
    print('wrote', out)
    for r in report:
        print(' ', r)
    print('  settings:', {k: f'{before[k]} -> {SET[k]}' for k in SET if before[k] != SET[k]})
    print('  per-object:', {n: s.get('overrides', {}) for n, s in OBJ.items() if s.get('overrides')})


if __name__ == '__main__':
    sys.exit(main())
