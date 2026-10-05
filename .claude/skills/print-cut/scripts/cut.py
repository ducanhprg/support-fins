#!/usr/bin/env python3
"""
Split a model into printable parts with hidden alignment pins: the cuts, the
connectors, the checks and the layout, in one command.

    python cut.py MODEL [--object NAME] [--printer u1|x2d|x2d-dual] [--cut SPEC ...]
                  [--connector dowel|peg|none] [--clearance 0.15] [--wall 1.6]
                  [--scale 2] [--out DIR] [--sections AXIS]

MODEL      .stl / .obj / .ply, or a .3mf saved by Bambu Studio / Snapmaker Orca
           (pick a piece with --object NAME when it holds several).
--cut      a plane, repeatable; applied to every part it crosses, in order:
             "z=120"  "x=-40"  (a world axis, plate coordinates)
             "p=X,Y,Z n=NX,NY,NZ"  (any plane: a point on it and its normal)
           Without --cut the cuts are chosen: until every part fits the bed, the
           longest part is cut across its longest side at the narrowest section
           near where it has to be cut (a neck, so the seam stays small).
--sections print the section area along an axis (x, y, z, or "long") and stop:
           the narrow places are where a cut hides best.
--connector
           dowel (default): a hole in both halves and a separate pin. Both halves
           can lie on their flat cut face, which is the best foot a part can have.
           peg: the pin grows from one half (no loose pins; that half can't lie on
           that face). none: flat cuts, glue only.

Writes into DIR (default "<model> - cut" beside it): part_NN.stl, pins.stl, and
"<model> - cut.3mf" with every part lying on its largest cut face and the pins
lying flat, spread over the plate (press Arrange in the slicer), plus report.json.
"""
import argparse, json, math, os, re, sys, zipfile
import numpy as np
import manifold3d as m3d
import trimesh
from shapely.geometry import Point, Polygon
from shapely.ops import polylabel

BEDS = {'u1': (270, 270, 270), 'x2d': (256, 256, 261), 'x2d-dual': (235.5, 256, 261)}
MARGIN = 6            # mm kept clear of the bed edge
PINS = (10, 8, 6, 5, 4, 3)   # pin diameters tried, largest first
MIN_ISLAND = 30       # mm²: a section island smaller than this gets glue, no pin
WINDOW = 0.12         # auto cuts look for a neck within this share of the length
CHAMFER = 0.4


# ---------------------------------------------------------------- reading
def mat(s):
    v = [float(x) for x in s.split()]
    return np.array(v[:9]).reshape(3, 3), np.array(v[9:12])


def read_3mf(path):
    """[(name, V, F)] per build item, vertices in plate coordinates."""
    z = zipfile.ZipFile(path)
    root = z.read('3D/3dmodel.model').decode('utf-8')
    names = {}
    if 'Metadata/model_settings.config' in z.namelist():
        cfg = z.read('Metadata/model_settings.config').decode('utf-8')
        for m in re.finditer(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"', cfg):
            names[m.group(1)] = m.group(2)
    files = {'3D/3dmodel.model': root}

    def text(p):
        p = p.lstrip('/')
        if p not in files:
            files[p] = z.read(p).decode('utf-8')
        return files[p]

    def mesh(xml, oid):
        m = re.search(r'<object id="' + oid + r'"[^>]*>(.*?)</object>', xml, re.S)
        body = m.group(1)
        if '<components>' in body:
            comps = []
            for c in re.finditer(r'<component\s([^>]*?)/?>', body):
                at = dict(re.findall(r'([\w:]+)="([^"]*)"', c.group(1)))
                comps.append((at.get('p:path'), at['objectid'], at.get('transform')))
            return None, comps
        V = np.array(re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', body), float)
        F = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', body), np.int64)
        return (V, F), None

    def world(xml, oid, A, t):
        got, comps = mesh(xml, oid)
        if got is not None:
            V, F = got
            return [(V @ A + t, F)]
        out = []
        for path, coid, tr in comps:
            Ac, tc = mat(tr) if tr else (np.eye(3), np.zeros(3))
            out += world(text(path) if path else xml, coid, Ac @ A, tc @ A + t)
        return out

    items = []
    for it in re.finditer(r'<item\s([^>]*?)/?>', root):
        attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', it.group(1)))
        oid, tr = attrs['objectid'], attrs.get('transform')
        A, t = mat(tr) if tr else (np.eye(3), np.zeros(3))
        parts = world(root, oid, A, t)
        V = np.vstack([p[0] for p in parts])
        off, Fs = 0, []
        for p in parts:
            Fs.append(p[1] + off)
            off += len(p[0])
        items.append((names.get(oid, f'object {oid}'), V, np.vstack(Fs)))
    return items


def load(path, want):
    if path.lower().endswith('.3mf'):
        items = read_3mf(path)
        if want:
            items = [i for i in items if i[0] == want]
            if not items:
                sys.exit(f'no object named {want!r}')
        elif len(items) > 1:
            sys.exit('this project holds several objects; pick one with --object:\n  ' +
                     '\n  '.join(sorted({i[0] for i in items})))
        name, V, F = items[0]
    else:
        tm = trimesh.load(path, force='mesh')
        name, V, F = os.path.basename(path), tm.vertices, tm.faces
    return name, solid(V, F)


def manifold(V, F):
    return m3d.Manifold(m3d.Mesh(vert_properties=np.asarray(V, np.float32),
                                 tri_verts=np.asarray(F, np.uint32)))


def merged(M):
    """Overlapping shells (a button sunk into a body) made one solid, as the slicer
    does; empty or inside-out slivers dropped."""
    shells = [s for s in M.decompose() if s.volume() > 1e-3]
    return M if len(shells) == 1 else m3d.Manifold.batch_boolean(shells, m3d.OpType.Add)


def solid(V, F):
    """A closed manifold from the mesh, repairing the usual faults on the way."""
    tm = trimesh.Trimesh(V, F, process=True)
    tm.merge_vertices()
    M = manifold(tm.vertices, tm.faces)
    if M.status() == m3d.Error.NoError and not M.is_empty():
        return merged(M)
    trimesh.repair.fix_normals(tm)
    trimesh.repair.fill_holes(tm)
    tm.remove_degenerate_faces() if hasattr(tm, 'remove_degenerate_faces') else None
    M = manifold(tm.vertices, tm.faces)
    if M.status() == m3d.Error.NoError and not M.is_empty():
        return merged(M)
    sys.exit(f'the mesh is not a closed solid ({M.status()}), even after repair: '
             'fix it first (the slicer\'s "Fix model", or Blender\'s 3D-Print toolbox)')


def arrays(M):
    m = M.to_mesh()
    return np.asarray(m.vert_properties)[:, :3].astype(float), np.asarray(m.tri_verts).astype(np.int64)


# ---------------------------------------------------------------- frames
def frame(p0, n):
    """3x4 transforms into the plane's frame (n -> +z, p0 -> origin) and back."""
    n = np.asarray(n, float) / np.linalg.norm(n)
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(a, n); u /= np.linalg.norm(u)
    v = np.cross(n, u)
    R = np.vstack([u, v, n])
    to = np.hstack([R, (-R @ np.asarray(p0, float))[:, None]])
    back = np.hstack([R.T, np.asarray(p0, float)[:, None]])
    return to, back


def polygons(cs):
    """Shapely polygons of a cross-section, one per island."""
    out = []
    for part in cs.decompose():
        loops = [np.asarray(l) for l in part.to_polygons()]
        signed = [0.5 * np.sum(l[:, 0] * np.roll(l[:, 1], -1) - np.roll(l[:, 0], -1) * l[:, 1]) for l in loops]
        outer = int(np.argmax(signed))
        holes = [loops[i] for i in range(len(loops)) if i != outer and signed[i] < 0]
        poly = Polygon(loops[outer], holes).buffer(0)
        if not poly.is_empty:
            out.append(poly)
    return out


# ---------------------------------------------------------------- connectors
def cyl(r, z0, z1, chamfer_top=0.0, chamfer_bot=0.0):
    """A cylinder along z from z0 to z1, chamfered at either end."""
    seg = max(24, int(2 * math.pi * r / 0.5))
    body = m3d.Manifold.cylinder(z1 - z0 - chamfer_top - chamfer_bot, r, r, seg).translate((0, 0, z0 + chamfer_bot))
    parts = [body]
    if chamfer_bot:
        parts.append(m3d.Manifold.cylinder(chamfer_bot, max(r - chamfer_bot, 0.1), r, seg).translate((0, 0, z0)))
    if chamfer_top:
        parts.append(m3d.Manifold.cylinder(chamfer_top, r, max(r - chamfer_top, 0.1), seg).translate((0, 0, z1 - chamfer_top)))
    return m3d.Manifold.batch_boolean(parts, m3d.OpType.Add)


def solid_around(part, probe):
    """True when the probe sits wholly in material (no break-through)."""
    v = probe.volume()
    return v > 0 and (part ^ probe).volume() >= 0.995 * v


def depth(part, x, y, r, wall, up):
    """The deepest hole (mm) that keeps a wall all round inside this half."""
    for D in (min(1.5 * 2 * r, 12), 2 * r, max(4, 1.2 * r)):
        z0, z1 = (0.02, D + wall) if up else (-(D + wall), -0.02)
        if solid_around(part, cyl(r + wall, z0, z1).translate((x, y, 0))):
            return D
    return 0


def spots(poly, d, wall, want, inset=0.0):
    """Up to `want` pin centres inside the island, as far apart as it allows (two
    or more pins stop the halves turning on each other); one pin goes in the middle.
    `inset` keeps them that much further in from the edge."""
    inner = poly.buffer(-(d / 2 + wall + inset))
    if inner.is_empty:
        return []
    geoms = list(getattr(inner, 'geoms', [inner]))
    if want == 1:
        big = max(geoms, key=lambda g: g.area)
        p = polylabel(big, tolerance=0.2) if big.area > 1 else big.representative_point()
        return [(p.x, p.y)]
    minx, miny, maxx, maxy = inner.bounds
    step = max(d / 2, 1.0)
    pts = [(x, y) for x in np.arange(minx, maxx + step, step) for y in np.arange(miny, maxy + step, step)
           if inner.contains(Point(x, y))]
    pts += [c for g in geoms for c in g.exterior.coords[:-1]]
    P = np.array(pts, float)
    if len(P) < 2:
        return []
    D = np.linalg.norm(P[:, None] - P[None], axis=2)
    i, j = np.unravel_index(np.argmax(D), D.shape)
    if D[i, j] < d + 3:
        return []
    chosen = [i, j]
    while len(chosen) < want:
        k = int(np.argmax(D[:, chosen].min(axis=1)))
        if D[k, chosen].min() < max(d + 3, 0.3 * D[i, j]):
            break
        chosen.append(k)
    return [tuple(P[k]) for k in chosen]


def connect(top, bot, cs, kind, clr, wall):
    """Holes/pegs across one cut (plane frame: z=0, top above). Returns the halves,
    the pins to print (plane frame, along z) and what was done per island."""
    holes_t, holes_b, pegs, pins, log = [], [], [], [], []
    # the halves near the cut only: every hole is probed against these (fast)
    x0, y0, x1, y1 = cs.bounds()
    reach = 12 + wall + 1
    slab = m3d.Manifold.cube((x1 - x0 + 2, y1 - y0 + 2, reach)).translate((x0 - 1, y0 - 1, 0))
    top_near, bot_near = top ^ slab, bot ^ slab.translate((0, 0, -reach))
    for poly in polygons(cs):
        if poly.area < MIN_ISLAND:
            log.append({'island_mm2': round(poly.area), 'pins': 0, 'why': 'too small for a pin: glue'})
            continue
        rin = poly.boundary.distance(polylabel(poly, tolerance=0.2))
        want = 3 if poly.area >= 2500 else 2
        done = None
        # two pins or more of any size before one big one: a lone round pin lets
        # the halves turn
        # pins start well in from the edge and move further in until their holes
        # keep a wall: a sculpted surface curves away just past the cut face
        for least, ask in ((2, want), (1, 1)):
            for d in PINS:
                base = d / 2 + clr + wall
                if base > rin:
                    continue
                for share in (0.3, 0.55, 0.8):
                    placed = []
                    for x, y in spots(poly, d + 2 * clr, wall, ask, share * (rin - base)):
                        r = d / 2
                        Dt = depth(top_near, x, y, r + clr, wall, True)
                        Db = depth(bot_near, x, y, r + clr, wall, False)
                        if kind == 'peg':
                            Db = 1.0 if Db else 0
                        if Dt and Db:
                            placed.append((x, y, r, Dt, Db))
                    if len(placed) >= least:
                        done = (d, placed)
                        break
                if done:
                    break
            if done:
                break
        if not done:
            log.append({'island_mm2': round(poly.area), 'pins': 0, 'why': 'no pin fits with a wall round it: glue'})
            continue
        d, placed = done
        for x, y, r, Dt, Db in placed:
            hr = r + clr
            holes_t.append(cyl(hr, -0.05, Dt + 0.2, 0, 0).translate((x, y, 0)))
            holes_t.append(m3d.Manifold.cylinder(0.5, hr + 0.5, hr, 48).translate((x, y, -0.01)))
            if kind == 'dowel':
                holes_b.append(cyl(hr, -(Db + 0.2), 0.05).translate((x, y, 0)))
                holes_b.append(m3d.Manifold.cylinder(0.5, hr, hr + 0.5, 48).translate((x, y, -0.49)))
                pins.append({'d': d, 'length': round(Dt + Db - 0.4, 1)})
            else:
                pegs.append(cyl(r, -1.0, Dt - 0.3, chamfer_top=CHAMFER).translate((x, y, 0)))
        log.append({'island_mm2': round(poly.area), 'pins': len(placed), 'd': d,
                    'depth': [round(min(p[3] for p in placed), 1), round(min(p[4] for p in placed), 1)],
                    **({'why': 'one pin only: the halves can turn on it, glue them'} if len(placed) == 1 else {})})
    if holes_t:
        top = top - m3d.Manifold.batch_boolean(holes_t, m3d.OpType.Add)
    if holes_b:
        bot = bot - m3d.Manifold.batch_boolean(holes_b, m3d.OpType.Add)
    if pegs:
        bot = bot + m3d.Manifold.batch_boolean(pegs, m3d.OpType.Add)
    return top, bot, pins, log


def pin_body(d, length):
    """A dowel lying along x with a flat underneath, chamfered both ends."""
    r = d / 2
    p = cyl(r, 0, length, CHAMFER, CHAMFER)                 # along z
    p = p.trim_by_plane((0, 1, 0), -(r - 0.1 * d))          # the flat: keep y >= -(r - 0.1d)
    p = p.rotate((0, 90, 0))                                 # along x
    p = p.rotate((90, 0, 0))                                 # flat face down (-z)
    lo = np.array(p.bounding_box()[:3])
    return p.translate(tuple(-lo))


# ---------------------------------------------------------------- cutting
class Part:
    def __init__(self, M, faces=()):
        self.M, self.faces = M, list(faces)     # faces: (outward normal, area) of cut faces


def cut_part(part, p0, n, kind, clr, wall):
    """Split one part by a plane; None if the plane misses it."""
    to, back = frame(p0, n)
    L = part.M.transform(to)
    lo, hi = L.bounding_box()[2], L.bounding_box()[5]
    if not (lo < -0.5 and hi > 0.5):
        return None
    top = L.trim_by_plane((0, 0, 1), 0.0)
    bot = L.trim_by_plane((0, 0, -1), 0.0)
    if top.is_empty() or bot.is_empty() or top.volume() < 1 or bot.volume() < 1:
        return None
    cs = L.slice(0.0)
    top, bot, pins, log = connect(top, bot, cs, kind, clr, wall)
    n = np.asarray(n, float) / np.linalg.norm(n)
    area = cs.area()
    A = Part(top.transform(back), part.faces + [(-n, area)])
    B = Part(bot.transform(back), part.faces + [(n, area)])
    return A, B, pins, log, area


def obb(M):
    V, F = arrays(M)
    box = trimesh.Trimesh(V, F, process=False).bounding_box_oriented
    T = np.asarray(box.primitive.transform)
    return np.asarray(box.primitive.extents), T[:3, :3], T[:3, 3]


def fits(M, bed):
    ext = sorted(obb(M)[0])
    return all(e <= b - MARGIN for e, b in zip(ext, sorted(bed)))


def sections(L, ts):
    """Per height of a part already in a plane frame: (t, area, islands, smallest island)."""
    out = []
    for t in ts:
        isl = [x.area() for x in L.slice(float(t)).decompose()]
        out.append((float(t), sum(isl), len(isl), min(isl) if isl else 0.0))
    return out


def profile(M, axis, centre, n=41):
    """Section area along a direction, t measured from `centre`."""
    to, _ = frame(centre, axis)
    L = M.transform(to)
    zlo, zhi = L.bounding_box()[2], L.bounding_box()[5]
    return sections(L, np.linspace(zlo + 0.5, zhi - 0.5, n))


def auto_cut(part, bed):
    """The plane for the next cut of a part too big for the bed. Tries the world
    axes the part is too long along, and its own longest axis; on each, looks near
    where a cut is due for a section that leaves no loose bits (an island under
    200 mm² is a tip or a strap cut off), has the fewest islands, and is the
    narrowest (a neck); of equally narrow places, the one nearest where it's due."""
    ext, R, c = obb(part.M)
    limit = max(bed) - MARGIN
    room = sorted(bed)[1] - MARGIN
    axes = [np.array(v, float) for v in ((1, 0, 0), (0, 1, 0), (0, 0, 1))] + [R[:, int(np.argmax(ext))]]
    best = None
    for k, axis in enumerate(axes):
        to, _ = frame(c, axis)
        L = part.M.transform(to)
        zlo, zhi = L.bounding_box()[2], L.bounding_box()[5]
        length = zhi - zlo
        if k < 3 and length <= room:
            continue
        due = zlo + length / math.ceil(length / (limit * 0.97))
        lo = max(zlo + 0.15 * length, due - WINDOW * length)
        hi = max(lo + 1, min(due + WINDOW * length, zlo + limit * 0.97))
        prof = sections(L, np.linspace(lo, hi, 25))
        floor = 0.25 * sorted(a for _, a, _, _ in prof)[len(prof) // 2]
        prof = [p for p in prof if p[1] >= floor] or prof
        clean = min((p[3] < 200, p[2]) for p in prof)
        prof = [p for p in prof if (p[3] < 200, p[2]) == clean]
        least = min(p[1] for p in prof)
        t, area, n, _ = min((p for p in prof if p[1] <= 1.03 * least), key=lambda p: abs(p[0] - due))
        key = (clean, area)
        if best is None or key < best[0]:
            best = (key, c + axis * t, axis)
    return best[1], best[2]


def parse_cut(s):
    m = re.fullmatch(r'\s*([xyz])\s*=\s*(-?[\d.]+)\s*', s)
    if m:
        n = {'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1)}[m.group(1)]
        return np.array(n, float) * float(m.group(2)), np.array(n, float)
    m = re.fullmatch(r'\s*p=([-\d.,\s]+)\s+n=([-\d.,\s]+)\s*', s)
    if m:
        return np.array([float(v) for v in m.group(1).split(',')]), np.array([float(v) for v in m.group(2).split(',')])
    sys.exit(f'--cut {s!r}: use "z=120" or "p=X,Y,Z n=NX,NY,NZ"')


# ---------------------------------------------------------------- layout and output
def face_down(w):
    """Rotation taking direction w to straight down."""
    target = np.array([0, 0, -1.0])
    v = np.cross(w, target)
    s, c = np.linalg.norm(v), float(np.dot(w, target))
    if s < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1, -1])
    k = v / s
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + s * K + (1 - c) * K @ K


def lay_down(part):
    """The pose to print in: as placed, or lying on one of its cut faces (flat, the
    best foot a part can have), whichever puts more on the plate (first-layer
    area), then the lower. The pose check of print-tune weighs the rest."""
    best = None
    for label, R in [('as placed', np.eye(3))] + [(f'a cut face ({round(a)} mm²)', face_down(w))
                                                  for w, a in part.faces]:
        M = part.M.transform(np.hstack([R, np.zeros((3, 1))]))
        lo = np.array(M.bounding_box()[:3])
        M = M.translate(tuple(-lo))
        foot, h = M.slice(0.2).area(), M.bounding_box()[5]
        if best is None or foot > 1.1 * best[0] or (foot >= best[0] / 1.1 and h < best[1]):
            best = (foot, h, M, label)
    return best[2], best[3], best[0]


def write_3mf(path, objects):
    """objects: [(name, V, F, (x, y))]: a plain 3MF both slicers open."""
    res, build = [], []
    for i, (name, V, F, (x, y)) in enumerate(objects, 1):
        vs = ''.join(f'<vertex x="{a:.4f}" y="{b:.4f}" z="{c:.4f}"/>' for a, b, c in V)
        ts = ''.join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F)
        res.append(f'<object id="{i}" type="model" name="{name}"><mesh><vertices>{vs}</vertices>'
                   f'<triangles>{ts}</triangles></mesh></object>')
        build.append(f'<item objectid="{i}" transform="1 0 0 0 1 0 0 0 1 {x:.3f} {y:.3f} 0"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(res)}</resources><build>{"".join(build)}</build></model>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
        z.writestr('_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
        z.writestr('3D/3dmodel.model', model)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('model')
    ap.add_argument('--object')
    ap.add_argument('--printer', choices=sorted(BEDS), default='u1')
    ap.add_argument('--cut', action='append', default=[])
    ap.add_argument('--connector', choices=['dowel', 'peg', 'none'], default='dowel')
    ap.add_argument('--clearance', type=float, default=0.15, help='mm per side between pin and hole')
    ap.add_argument('--wall', type=float, default=1.6, help='mm of material kept round every hole')
    ap.add_argument('--scale', type=float, default=1.0, help='scale the model first (2 = twice the size)')
    ap.add_argument('--out')
    ap.add_argument('--sections')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    name, M = load(a.model, a.object)
    if a.scale != 1:
        M = M.scale((a.scale,) * 3)
    bed = BEDS[a.printer]
    x0, y0, z0, x1, y1, z1 = M.bounding_box()
    print(f'{name}: {x1 - x0:.0f} x {y1 - y0:.0f} x {z1 - z0:.0f} mm, {M.volume() / 1000:.0f} cm³, '
          f'{M.num_tri()} triangles · bed {a.printer} {bed[0]} x {bed[1]} x {bed[2]}')

    if a.sections:
        if a.sections == 'long':
            ext, R, c = obb(M)
            axis = R[:, int(np.argmax(ext))]
        else:
            axis = {'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1)}[a.sections]
            c = np.array([0.0, 0, 0])
        axis = np.asarray(axis, float)
        prof = profile(M, axis, c, 41)
        top = max(p[1] for p in prof) or 1
        for t, area, n, small in prof:
            p = c + axis * t
            print(f'  at {", ".join(f"{v:7.1f}" for v in p)}  {area:8.0f} mm²  {n} island{"s" if n != 1 else " "}'
                  f'{" (one under 200 mm²)" if n and small < 200 else "":20}  {"#" * int(40 * area / top)}')
        return 0

    parts, cuts, pins = [Part(M)], [], []
    plan = [parse_cut(s) for s in a.cut]
    if plan:
        for p0, n in plan:
            nxt = []
            for part in parts:
                r = cut_part(part, p0, n, a.connector, a.clearance, a.wall)
                if r is None:
                    nxt.append(part)
                    continue
                A, B, pp, log, area = r
                nxt += [A, B]
                pins += pp
                cuts.append({'point': [round(float(v), 1) for v in p0], 'normal': [round(float(v), 3) for v in n / np.linalg.norm(n)],
                             'section_mm2': round(area), 'islands': log})
            parts = nxt
    else:
        queue, done, guard = parts, [], 0
        while queue:
            part = queue.pop(0)
            guard += 1
            if fits(part.M, bed) or guard > 24:
                done.append(part)
                continue
            p0, n = auto_cut(part, bed)
            r = cut_part(part, p0, n, a.connector, a.clearance, a.wall)
            if r is None:
                done.append(part)
                continue
            A, B, pp, log, area = r
            queue += [A, B]
            pins += pp
            cuts.append({'point': [round(float(v), 1) for v in p0], 'normal': [round(float(v), 3) for v in n],
                         'section_mm2': round(area), 'islands': log})
        parts = done

    out = a.out or re.sub(r'\.[^.]+$', '', a.model) + ' - cut'
    os.makedirs(out, exist_ok=True)
    stem = os.path.basename(re.sub(r'\.[^.]+$', '', a.model))
    report = {'model': name, 'scale': a.scale, 'printer': a.printer, 'connector': a.connector, 'clearance': a.clearance,
              'wall': a.wall, 'cuts': cuts, 'parts': [], 'pins': []}
    objects, x = [], 0.0
    vol = 0.0
    for i, part in enumerate(parts, 1):
        if part.M.status() != m3d.Error.NoError or part.M.is_empty():
            sys.exit(f'part {i} came out broken ({part.M.status()})')
        L, lies, foot = lay_down(part)
        V, F = arrays(L)
        trimesh.Trimesh(V, F, process=False).export(os.path.join(out, f'part_{i:02d}.stl'))
        b = L.bounding_box()
        size = (b[3] - b[0], b[4] - b[1], b[5] - b[2])
        ok = fits(L, bed)
        vol += part.M.volume()
        pieces = sorted(x.volume() for x in part.M.decompose())
        report['parts'].append({'file': f'part_{i:02d}.stl', 'size': [round(s) for s in size], 'fits': ok,
                                'pieces': len(pieces), 'smallest_cm3': round(pieces[0] / 1000, 2), 'cut_faces': len(part.faces),
                                'lies_on': lies, 'first_layer_mm2': round(foot)})
        objects.append((f'part_{i:02d}', V, F, (x, 0)))
        x += size[0] + 10
    if pins:
        bodies, px = [], 0.0
        for k, p in enumerate(pins):
            body = pin_body(p['d'], p['length']).translate((px, -20 - p['d'], 0))
            bodies.append(body)
            px += p['length'] + 4
            vol += body.volume()
        P = m3d.Manifold.batch_boolean(bodies, m3d.OpType.Add)
        V, F = arrays(P)
        trimesh.Trimesh(V, F, process=False).export(os.path.join(out, 'pins.stl'))
        objects.append(('pins', V, F, (0, 0)))
        sizes = {}
        for p in pins:
            sizes.setdefault((p['d'], p['length']), 0)
            sizes[(p['d'], p['length'])] += 1
        report['pins'] = [{'d': d, 'length': l, 'count': c} for (d, l), c in sorted(sizes.items())]
    tmf = os.path.join(out, f'{stem} - cut.3mf')
    write_3mf(tmf, objects)
    report['volume_check'] = {'model_cm3': round(M.volume() / 1000, 1), 'parts_and_pins_cm3': round(vol / 1000, 1)}
    json.dump(report, open(os.path.join(out, 'report.json'), 'w', encoding='utf-8'), indent=1)

    for c in cuts:
        isl = '; '.join(f"{i['island_mm2']} mm²: " + (f"{i['pins']} x Ø{i['d']} pin, {i['depth'][0]}/{i['depth'][1]} mm deep" if i['pins'] else i['why'])
                        for i in c['islands'])
        print(f"cut at {c['point']} normal {c['normal']}: section {c['section_mm2']} mm² ({isl})")
    for p in report['parts']:
        print(f"{p['file']}: {p['size'][0]} x {p['size'][1]} x {p['size'][2]} mm, {'fits' if p['fits'] else 'DOES NOT FIT'} the bed"
              f"{', ' + str(p['pieces']) + ' separate pieces, the smallest ' + str(p['smallest_cm3']) + ' cm³' if p['pieces'] > 1 else ''}, lies on {p['lies_on']}")
    for p in report['pins']:
        print(f"pins: {p['count']} x Ø{p['d']} x {p['length']} mm (print lying on their flat)")
    vc = report['volume_check']
    print(f"volume: model {vc['model_cm3']} cm³, parts + pins {vc['parts_and_pins_cm3']} cm³")
    print(f'wrote {tmf}')
    return 0 if all(p['fits'] for p in report['parts']) else 1


if __name__ == '__main__':
    sys.exit(main())
