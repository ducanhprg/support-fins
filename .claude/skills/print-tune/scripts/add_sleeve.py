#!/usr/bin/env python3
"""Stand a sleeve around a tall, slender piece so it cannot sway or topple.

For a piece that needs no support but is tall and thin (a 245 mm screw, 16 mm across), the
risk is not an overhang: it is the piece working loose at its foot as the nozzle drags on it,
then falling over mid-print. The sleeve is printed in the same layers, around the piece:

  - TUBE   follows the piece: at every height its inner wall is --gap outside that layer's convex
           outline (so it twists with a twisted piece), wall --wall, full height. A piece that
           starts to lean rests against it instead of coming off the bed.
  - TINES  a few small tines per height tie the tube to the piece at hull points, so it cannot move
           at all at those heights. They are the only contact with the piece above the foot, and
           the only scars: each is 0.8 mm wide, 0.32 mm tall, --tine-every mm apart (3 per height,
           turned a little each time so the nubs do not line up). --tine-every 0 = no tines.
  - WEB    the first three layers fill the gap, so the piece is anchored through the sleeve's much
           larger footprint. Trim that 0.56 mm web with a knife around the foot afterwards.
  - RIBS   four tapered ribs on the outside (deep at the bed, nothing at the top) give the whole
           thing a wide stance. Each joins the tube through a 0.4 mm neck so it snaps off. A rib that
           would run into a neighbouring piece on the plate is shortened or dropped.
  - TEAR   two 0.4 mm lines along the tube (at 0 and 180 degrees) let it be snapped open.

The sleeve is merged into the piece's own mesh (as build_tuned.py merges fins), so the piece keeps
its name, filament slot and per-object settings. Nothing here slices: check the slicer preview.

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


def _bar(stations, half):
    """A closed bar through `stations` = [(z, centre xy, direction (cos, sin), r_near, r_far)]: at each
    station it spans r_near..r_far along the direction and +-half across it."""
    import trimesh
    V, F = [], []
    for z, c, (ca, sa), r0, r1 in stations:
        rad, tan = np.array([ca, sa]), np.array([-sa, ca])
        for rr, tt in ((r0, -half), (r1, -half), (r1, half), (r0, half)):      # counter-clockwise from above
            q = c + rr * rad + tt * tan
            V.append((q[0], q[1], z))
    m = len(stations)
    for k in range(m - 1):
        for e in range(4):
            a, b = k * 4 + e, k * 4 + (e + 1) % 4
            c2, d = (k + 1) * 4 + (e + 1) % 4, (k + 1) * 4 + e
            F += [(a, b, c2), (a, c2, d)]
    t = (m - 1) * 4
    F += [(0, 2, 1), (0, 3, 2), (t, t + 1, t + 2), (t, t + 2, t + 3)]
    mesh = trimesh.Trimesh(np.array(V), np.array(F), process=False)
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def _box(centre, direction, length, width, height):
    import trimesh
    bx = trimesh.creation.box(extents=[length, width, height])
    bx.apply_transform(trimesh.transformations.rotation_matrix(math.atan2(direction[1], direction[0]), [0, 0, 1]))
    bx.apply_translation(centre)
    return bx


def sleeve_mesh(piece, gap, wall, web_h, step, rib_len=(0, 0, 0, 0), rib_t=1.2, tine_every=12.0,
                tine_w=0.8, tine_h=0.32, tines_per=3, tear_w=1.0, tear_keep=0.4, neck_t=0.4, neck_len=1.0, n=96):
    """Tube + web + tear lines + outer ribs + inner tines, as one trimesh (see the module doc).
    `rib_len` = reach at the bed of the ribs at 0/90/180/270 degrees (world X+, Y+, X-, Y-)."""
    import trimesh
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient
    H = float(piece.bounds[1][2])
    thetas = np.linspace(0, 2 * math.pi, n, endpoint=False)
    zs = [web_h]
    while zs[-1] + step < H - 1e-6:
        zs.append(zs[-1] + step)
    zs.append(H)
    zs = np.array(zs)
    hulls = layer_outline(piece, list(np.clip(zs, 0.1, H - 0.1)) + [0.3])
    web_hull = hulls.pop()
    d = np.minimum(thetas, 2 * math.pi - thetas)       # angular distance to the tear lines at 0 and 180
    d = np.minimum(d, np.abs(thetas - math.pi))

    def ring(z, hull, web):
        c, rho = polar_profile(hull, gap, thetas)
        r_in = np.maximum(rho - (gap + 1.0), 1.0) if web else rho
        groove = (d * (rho + wall)) < tear_w / 2
        r_out = np.where(groove & (not web), rho + tear_keep, rho + wall)
        pin = np.c_[c[0] + r_in * np.cos(thetas), c[1] + r_in * np.sin(thetas), np.full(n, z)]
        pout = np.c_[c[0] + r_out * np.cos(thetas), c[1] + r_out * np.sin(thetas), np.full(n, z)]
        return pin, pout, c, rho, z

    rings = [ring(0.0, web_hull, True), ring(web_h, web_hull, True)]
    rings += [ring(z, h, False) for z, h in zip(zs, hulls)]   # rings 1 and 2 share z = web_h: the step where the web ends

    V = np.vstack([np.vstack([r[0], r[1]]) for r in rings])
    F = []
    idx = lambda k, side, j: k * 2 * n + side * n + (j % n)
    for k in range(len(rings) - 1):
        for j in range(n):
            o0, o1, o2, o3 = idx(k, 1, j), idx(k, 1, j + 1), idx(k + 1, 1, j + 1), idx(k + 1, 1, j)
            i0, i1, i2, i3 = idx(k, 0, j), idx(k, 0, j + 1), idx(k + 1, 0, j + 1), idx(k + 1, 0, j)
            F += [(o0, o1, o2), (o0, o2, o3), (i0, i2, i1), (i0, i3, i2)]   # outer outward, inner inward
    k = len(rings) - 1
    for j in range(n):                                                      # bottom (down) and top (up) annuli
        o0, o1, i0, i1 = idx(0, 1, j), idx(0, 1, j + 1), idx(0, 0, j), idx(0, 0, j + 1)
        F += [(o0, i0, o1), (o1, i0, i1)]
        o0, o1, i0, i1 = idx(k, 1, j), idx(k, 1, j + 1), idx(k, 0, j), idx(k, 0, j + 1)
        F += [(o0, o1, i0), (o1, i1, i0)]
    tube = trimesh.Trimesh(V, np.array(F), process=False)
    if tube.volume < 0:
        tube.invert()
    parts = [tube]

    # outer ribs: a thin neck (snaps) then a plate that is deep at the bed and tapers to nothing at the top
    for q, L0 in enumerate(rib_len):
        if L0 <= 0:
            continue
        j0 = q * (n // 4)
        dirn = (math.cos(thetas[j0]), math.sin(thetas[j0]))
        st_neck, st_plate = [], []
        for _, _, c, rho, z in rings:
            r = float(rho[j0])
            L = L0 * max(0.0, 1 - z / H)
            st_neck.append((z, c, dirn, r + 0.2, r + wall + neck_len))
            st_plate.append((z, c, dirn, r + wall + neck_len - 0.2, r + wall + neck_len + 0.2 + L))
        parts += [_bar(st_neck, neck_t / 2), _bar(st_plate, rib_t / 2)]

    # inner tines: tie the tube to the piece at hull points; few, small, and turned each time
    if tine_every > 0:
        zt = np.arange(web_h + 6.0, H - 3.0, tine_every)
        for i, (z, hull) in enumerate(zip(zt, layer_outline(piece, list(zt)))):
            poly = orient(Polygon(hull), 1.0)                                # counter-clockwise
            v = np.array(poly.exterior.coords)[:-1]
            c = np.array(poly.centroid.coords[0])
            ang = np.arctan2(v[:, 1] - c[1], v[:, 0] - c[0])
            used = set()
            for t in range(tines_per):
                want = math.radians(47 * i) + 2 * math.pi * t / tines_per
                kk = int(np.argmin(np.abs(np.angle(np.exp(1j * (ang - want))))))
                if kk in used:
                    continue
                used.add(kk)
                p0, p1, p2 = v[kk - 1], v[kk], v[(kk + 1) % len(v)]
                n1 = np.array([(p1 - p0)[1], -(p1 - p0)[0]])                 # outward normals of the two edges
                n2 = np.array([(p2 - p1)[1], -(p2 - p1)[0]])
                nn = n1 / np.linalg.norm(n1) + n2 / np.linalg.norm(n2)
                nn /= np.linalg.norm(nn)
                parts.append(_box(np.r_[p1 + nn * (gap + 0.1) / 2, z], nn, 0.4 + gap + 0.5, tine_w, tine_h))
    return trimesh.util.concatenate(parts)


def plates(ms):
    """{object id: plate id} from model_settings.config."""
    out = {}
    for p in re.finditer(r'<plate>(.*?)</plate>', ms, re.S):
        pid = re.search(r'key="plater_id" value="(\d+)"', p.group(1)).group(1)
        for o in re.finditer(r'key="object_id" value="(\d+)"', p.group(1)):
            out[o.group(1)] = pid
    return out


def rib_limits(me, others, l0, margin=4.0, min_len=6.0):
    """Reach of the four ribs (X+, Y+, X-, Y-) from piece `me` = (cx, cy, r): stop short of every circle
    in `others` = [(cx, cy, r, shares)], sharing the free space with a neighbour that also grows ribs."""
    cx, cy, r = me
    res = []
    for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
        free = l0
        for ox, oy, orad, shares in others:
            vx, vy = ox - cx, oy - cy
            proj = vx * dx + vy * dy
            perp2 = vx * vx + vy * vy - proj * proj
            if proj <= 0 or perp2 >= (orad + 2) ** 2:
                continue                                    # not in the way of this rib
            hit = proj - math.sqrt(max((orad + 2) ** 2 - perp2, 0.0))
            f = hit - r - margin
            free = min(free, f / 2 if shares else f)
        res.append(free if free >= min_len else 0.0)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--object', action='append', required=True, help='object name (repeat for several)')
    ap.add_argument('--gap', type=float, default=0.6, help='air between piece and sleeve, mm (default 0.6)')
    ap.add_argument('--wall', type=float, default=1.2, help='sleeve wall, mm (default 1.2 = three perimeters)')
    ap.add_argument('--step', type=float, default=0.32, help='height between lofted rings, mm (two layers at 0.16)')
    ap.add_argument('--web-layers-mm', type=float, default=0.56, help='thickness of the anchoring web, mm (first 3 layers)')
    ap.add_argument('--rib-length', type=float, default=25.0, help='reach of the outer ribs at the bed, mm (0 = no ribs)')
    ap.add_argument('--tine-every', type=float, default=12.0, help='height between tine rows, mm (0 = no tines)')
    ap.add_argument('--tines-per-row', type=int, default=3)
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
    plate_of = plates(ms)
    names = {o: n for n, os_ in ids.items() for o in os_}

    cache, targets = {}, {}
    for name in a.object:
        assert name in ids, f'no object named {name!r}; have {sorted(ids)}'
        assert len(ids[name]) == 1, f'{name!r} is ambiguous (two objects with that name)'
        oid = ids[name][0]
        piece = world_mesh(zin, comps, oid, items[oid], cache)
        lo, hi = piece.bounds
        assert abs(lo[2]) < 0.05, f'{name!r} does not sit on the bed (lowest z {lo[2]:.2f})'
        cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
        r = float(np.hypot(piece.vertices[:, 0] - cx, piece.vertices[:, 1] - cy).max()) + a.gap + a.wall
        targets[oid] = dict(name=name, piece=piece, c=(cx, cy), r=r, plate=plate_of.get(oid))

    # rib reach: stop short of anything else on the same plate
    for oid, t in targets.items():
        others = []
        for o2, p in plate_of.items():
            if p != t['plate'] or o2 == oid:
                continue
            if o2 in targets:
                u = targets[o2]
                others.append((u['c'][0], u['c'][1], u['r'], True))
            else:
                v = np.array(B.world_vertices(zin, comps, o2, items[o2], cache))
                lo2, hi2 = v.min(0), v.max(0)
                oc = ((lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2)
                others.append((oc[0], oc[1], float(np.hypot(v[:, 0] - oc[0], v[:, 1] - oc[1]).max()), False))
        t['ribs'] = rib_limits((t['c'][0], t['c'][1], t['r']), others, a.rib_length) if a.rib_length > 0 else [0.0] * 4

    changed, report, built = {}, [], {}
    for oid, t in targets.items():
        name, piece = t['name'], t['piece']
        sl = sleeve_mesh(piece, a.gap, a.wall, a.web_layers_mm, a.step, rib_len=t['ribs'], tine_every=a.tine_every,
                         tines_per=a.tines_per_row)
        assert sl.volume > 0, f'sleeve for {name!r} is not a closed solid'
        built[oid] = sl
        tris = [tuple(sl.vertices[i][k] for i in f for k in range(3)) for f in sl.faces]
        path, mesh_oid, ct = comps[oid][0]
        place = B.compose(ct, items[oid]) if ct else items[oid]
        inv = B.inverse(place)
        xml = (changed[path] if path in changed else zin.read(path)).decode('utf-8')
        xml, nv, nf = B.merge_fins(xml, mesh_oid, tris, lambda p, inv=inv: B.apply(inv, p))
        changed[path] = xml.encode('utf-8')
        ribs = ', '.join(f'{lbl} {L:.0f}' for lbl, L in zip(('X+', 'Y+', 'X-', 'Y-'), t['ribs']) if L > 0) or 'none'
        report.append(f'{name} (plate {t["plate"]}): {piece.bounds[1][2]:.0f} mm tall, sleeve ~{sl.volume / 1000:.1f} cm3 '
                      f'(~{sl.volume * 1.24 / 1000:.0f} g PLA); ribs at the bed (mm): {ribs}; {nf} triangles merged into {path}')

    # two sleeves on one plate must stay clear of each other
    from scipy.spatial import cKDTree
    oids = list(built)
    for i in range(len(oids)):
        for j in range(i + 1, len(oids)):
            if targets[oids[i]]['plate'] != targets[oids[j]]['plate']:
                continue
            dmin = cKDTree(built[oids[j]].vertices).query(built[oids[i]].vertices[::7])[0].min()
            assert dmin > 3.0, f'{targets[oids[i]]["name"]} and {targets[oids[j]]["name"]} sleeves come within {dmin:.1f} mm'
            report.append(f'{targets[oids[i]]["name"]} / {targets[oids[j]]["name"]}: closest approach {dmin:.1f} mm')

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
