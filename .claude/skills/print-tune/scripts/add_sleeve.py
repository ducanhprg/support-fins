#!/usr/bin/env python3
"""Stand a sleeve around a tall, slender piece so it cannot sway or topple.

For a piece that needs no support but is tall and thin (a 245 mm screw, 16 mm across), the
risk is not an overhang: it is the piece working loose at its foot as the nozzle drags on it,
then falling over mid-print. A sleeve is a thin tube printed in the same layers, around the
piece, with a small air gap (never fused). A piece that starts to lean rests against the
sleeve instead of coming off the bed.

  - The tube follows the piece: at every height its inner wall is --gap outside that layer's
    convex outline (so it twists with a twisted piece and the piece cannot move more than --gap
    anywhere), wall --wall, full height of the piece.
  - The first few layers fill the gap (a "web"), so the piece is anchored through the sleeve's
    larger footprint and brim. Trim that thin web with a knife when the print is done.
  - Two thin vertical tear lines (one perimeter thick) let the tube be snapped open so the
    piece comes out even when its twist would not let it slide.

The sleeve is merged into the piece's own mesh (as build_tuned.py merges fins), so the piece
keeps its name, filament slot and per-object settings.

  python add_sleeve.py "<project>.3mf" -o "<out>.3mf" --object Screw_1_custom --object Screw_3_custom
"""
import argparse, math, os, re, sys, zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_tuned as B

IDENT = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]


def world_mesh(zin, comps, oid, item, cache):
    """The piece as one trimesh in plate coordinates (every component, then the item transform)."""
    import trimesh
    V, F, base = [], [], 0
    for path, mesh_oid, ct in comps[oid]:
        if path not in cache:
            cache[path] = zin.read(path).decode('utf-8')
        blk = B.mesh_block(cache[path], mesh_oid).group(0)
        m = B.compose(ct, item) if ct else item
        v = np.array([[float(a) for a in t] for t in re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', blk)])
        f = np.array([[int(a) for a in t] for t in re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', blk)])
        A = np.array(m[:9]).reshape(3, 3)
        V.append(v @ A + np.array(m[9:]))
        F.append(f + base)
        base += len(v)
    return trimesh.Trimesh(np.vstack(V), np.vstack(F), process=False)


def layer_outline(mesh, heights):
    """Convex hull (as an (n, 2) array) of the piece's cross-section at each height."""
    import trimesh
    from shapely.geometry import MultiPoint
    lines, to_3d, _ = trimesh.intersections.mesh_multiplane(mesh, [0, 0, 0], [0, 0, 1], heights)
    out = []
    for ln, t3 in zip(lines, to_3d):
        assert len(ln), 'empty slice: the piece has no material at a sleeve height'
        p = np.asarray(ln).reshape(-1, 2)
        w = (np.c_[p, np.zeros(len(p)), np.ones(len(p))] @ t3.T)[:, :2]
        out.append(np.array(MultiPoint(np.unique(w.round(4), axis=0)).convex_hull.exterior.coords)[:-1])
    return out


def polar_profile(hull, gap, thetas):
    """Centre and radius at each angle of the hull grown by `gap` (round corners)."""
    from shapely.geometry import Polygon
    poly = Polygon(hull).buffer(gap, resolution=24)
    c = np.array(poly.centroid.coords[0])
    b = np.array(poly.exterior.coords) - c
    ang, rad = np.arctan2(b[:, 1], b[:, 0]), np.hypot(b[:, 0], b[:, 1])
    o = np.argsort(ang)
    ang, rad = ang[o], rad[o]
    return c, np.interp(thetas, ang, rad, period=2 * math.pi)


def sleeve_mesh(piece, gap, wall, web_h, step, tear_w, tear_keep, n=120):
    """A tube whose inner wall follows the piece's convex outline at every height, `gap` outside it.

    Rings are lofted every `step` mm. The first `web_h` mm reach inward past the piece, fusing it
    to the sleeve. Two thin grooves on the outside (at 0 and 180 degrees) are the tear lines."""
    import trimesh
    H = float(piece.bounds[1][2])
    thetas = np.linspace(0, 2 * math.pi, n, endpoint=False)
    zs = [web_h]
    while zs[-1] + step < H - 1e-6:
        zs.append(zs[-1] + step)
    zs.append(H)
    zs = np.array(zs)
    hulls = layer_outline(piece, list(np.clip(zs, 0.1, H - 0.1)) + [0.3])
    web_hull = hulls.pop()
    # per-angle tear-line mask: a groove ~tear_w wide, in angle at the local radius
    d = np.minimum(thetas, 2 * math.pi - thetas)
    d = np.minimum(d, np.abs(thetas - math.pi))

    def ring(z, hull, web):
        c, rho = polar_profile(hull, gap, thetas)
        r_in = rho - (gap + 1.0) if web else rho
        r_in = np.maximum(r_in, 1.0)
        groove = (d * (rho + wall)) < tear_w / 2
        r_out = np.where(groove & (not web), rho + tear_keep, rho + wall)
        pin = np.c_[c[0] + r_in * np.cos(thetas), c[1] + r_in * np.sin(thetas), np.full(n, z)]
        pout = np.c_[c[0] + r_out * np.cos(thetas), c[1] + r_out * np.sin(thetas), np.full(n, z)]
        return pin, pout

    rings = [ring(0.0, web_hull, True), ring(web_h, web_hull, True)]
    rings += [ring(z, h, False) for z, h in zip(zs, hulls)]
    # the second and third rings share z = web_h: that makes the flat step where the web ends
    V = np.vstack([np.vstack([a, b]) for a, b in rings])
    F = []
    idx = lambda k, side, j: k * 2 * n + side * n + (j % n)
    for k in range(len(rings) - 1):
        for j in range(n):
            o0, o1, o2, o3 = idx(k, 1, j), idx(k, 1, j + 1), idx(k + 1, 1, j + 1), idx(k + 1, 1, j)
            i0, i1, i2, i3 = idx(k, 0, j), idx(k, 0, j + 1), idx(k + 1, 0, j + 1), idx(k + 1, 0, j)
            F += [(o0, o1, o2), (o0, o2, o3)]           # outer wall, normals outward
            F += [(i0, i2, i1), (i0, i3, i2)]           # inner wall, normals inward
    for j in range(n):                                  # bottom (down) and top (up) annuli
        o0, o1, i0, i1 = idx(0, 1, j), idx(0, 1, j + 1), idx(0, 0, j), idx(0, 0, j + 1)
        F += [(o0, i0, o1), (o1, i0, i1)]
        k = len(rings) - 1
        o0, o1, i0, i1 = idx(k, 1, j), idx(k, 1, j + 1), idx(k, 0, j), idx(k, 0, j + 1)
        F += [(o0, o1, i0), (o1, i1, i0)]
    m = trimesh.Trimesh(V, np.array(F), process=False)
    if m.volume < 0:
        m.invert()
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--object', action='append', required=True, help='object name (repeat for several)')
    ap.add_argument('--gap', type=float, default=0.6, help='air between piece and sleeve, mm (default 0.6)')
    ap.add_argument('--wall', type=float, default=1.2, help='sleeve wall, mm (default 1.2 = three perimeters)')
    ap.add_argument('--step', type=float, default=0.32, help='height between lofted rings, mm (two layers at 0.16)')
    ap.add_argument('--web-layers-mm', type=float, default=0.56, help='thickness of the anchoring web, mm (first 3 layers)')
    a = ap.parse_args()

    zin = zipfile.ZipFile(a.project)
    root = zin.read('3D/3dmodel.model').decode('utf-8')
    comps = B.components(root)
    items = {}
    for m in re.finditer(r'<item [^>]*>', root):
        t = m.group(0)
        oid = re.search(r'objectid="(\d+)"', t).group(1)
        tr = re.search(r'transform="([^"]+)"', t)
        items[oid] = B.mat(tr.group(1)) if tr else IDENT
    ms = zin.read('Metadata/model_settings.config').decode('utf-8')
    ids = {}
    for m in re.finditer(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]+)"', ms):
        ids.setdefault(m.group(2), []).append(m.group(1))

    cache, changed, report = {}, {}, []
    for name in a.object:
        assert name in ids, f'no object named {name!r}; have {sorted(ids)}'
        assert len(ids[name]) == 1, f'{name!r} is ambiguous (two objects with that name)'
        oid = ids[name][0]
        piece = world_mesh(zin, comps, oid, items[oid], cache)
        lo, hi = piece.bounds
        assert abs(lo[2]) < 0.05, f'{name!r} does not sit on the bed (lowest z {lo[2]:.2f})'
        sl = sleeve_mesh(piece, a.gap, a.wall, a.web_layers_mm, a.step, tear_w=1.0, tear_keep=0.4)
        assert sl.is_watertight and sl.volume > 0, f'sleeve for {name!r} is not a closed solid'
        height, reach = hi[2], a.gap
        tris = [tuple(sl.vertices[i][k] for i in f for k in range(3)) for f in sl.faces]

        path, mesh_oid, ct = comps[oid][0]
        place = B.compose(ct, items[oid]) if ct else items[oid]
        inv = B.inverse(place)
        xml = (changed[path] if path in changed else zin.read(path)).decode('utf-8')
        xml, nv, nf = B.merge_fins(xml, mesh_oid, tris, lambda p, inv=inv: B.apply(inv, p))
        changed[path] = xml.encode('utf-8')
        grams = sl.volume * 1.24 / 1000
        report.append(f'{name}: sleeve follows the piece at {a.gap} mm, wall {a.wall} mm, {height:.0f} mm tall, '
                      f'~{sl.volume / 1000:.1f} cm3 (~{grams:.0f} g PLA), {nf} triangles merged into {path}')

    with zipfile.ZipFile(a.output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            zout.writestr(zi, changed.get(info.filename, zin.read(info.filename)))
    print('wrote', a.output)
    for r in report:
        print(' ', r)


if __name__ == '__main__':
    sys.exit(main())
