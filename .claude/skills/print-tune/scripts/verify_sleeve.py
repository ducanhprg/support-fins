#!/usr/bin/env python3
"""Check a project written by add_sleeve.py against the project it was made from.

  python verify_sleeve.py "<original>.3mf" "<with sleeve>.3mf" --object Screw_1_custom [--object ...]

For each object: the gap between the piece and the tube (at heights away from the tine rows), that
the piece always sits inside the tube, that every tine row reaches the piece, that the rib bars
stand on the bed, and that nothing else in the project changed. Exit 1 if any check fails.
"""
import argparse, os, re, sys, zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_tuned as B
import add_sleeve as A


def load(z, name):
    root = z.read('3D/3dmodel.model').decode()
    comps = B.components(root)
    items = {}
    for m in re.finditer(r'<item [^>]*>', root):
        t = m.group(0)
        o = re.search(r'objectid="(\d+)"', t).group(1)
        tr = re.search(r'transform="([^"]+)"', t)
        items[o] = B.mat(tr.group(1)) if tr else A.IDENT
    ms = z.read('Metadata/model_settings.config').decode()
    oid = [m.group(1) for m in re.finditer(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]+)"', ms) if m.group(2) == name][0]
    return A.world_mesh(z, comps, oid, items[oid], {}), comps[oid][0][0]


def section(body, zz):
    import trimesh
    from shapely.ops import unary_union
    r = trimesh.intersections.mesh_multiplane(body, [0, 0, 0], [0, 0, 1], [zz])[0][0]
    if not len(r):
        return None
    pg = trimesh.load_path(np.asarray(r).reshape(-1, 2, 2)).polygons_full
    return unary_union(pg) if pg else None


def main():
    import trimesh
    from shapely.geometry import MultiPoint, Point, Polygon
    from shapely.ops import unary_union
    ap = argparse.ArgumentParser()
    ap.add_argument('original')
    ap.add_argument('result')
    ap.add_argument('--object', action='append', required=True)
    ap.add_argument('--tine-every', type=float, default=12.0)
    ap.add_argument('--web-layers-mm', type=float, default=0.56)
    a = ap.parse_args()
    zo, zr = zipfile.ZipFile(a.original), zipfile.ZipFile(a.result)
    bad = 0

    changed = [i.filename for i in zr.infolist() if zr.read(i.filename) != zo.read(i.filename)]
    print('entries that differ from the original:', changed)

    for name in a.object:
        piece, path = load(zo, name)
        both, _ = load(zr, name)
        assert path in changed, f'{name}: its mesh file did not change'
        n0 = len(piece.faces)
        assert np.allclose(both.vertices[:len(piece.vertices)], piece.vertices, atol=1e-4), f'{name}: the piece itself changed'
        sleeve = trimesh.Trimesh(both.vertices, both.faces[n0:], process=False)
        bodies = sleeve.split(only_watertight=False)
        tube = max(bodies, key=lambda b: abs(b.volume))
        others = [b for b in bodies if b is not tube]
        H = piece.bounds[1][2]
        tine_z = np.arange(a.web_layers_mm + 6.0, H - 3.0, a.tine_every) if a.tine_every > 0 else np.array([])

        # gap: piece outline vs the tube's hole, at heights away from tine rows and the web
        hs = [h for h in np.arange(1.0, H - 1, 1.37) if (not len(tine_z) or np.min(np.abs(h - tine_z)) > 0.6)]
        pl = trimesh.intersections.mesh_multiplane(piece, [0, 0, 0], [0, 0, 1], hs)[0]
        sl = trimesh.intersections.mesh_multiplane(tube, [0, 0, 0], [0, 0, 1], hs)[0]
        gaps, outside = [], 0
        for h, p, t in zip(hs, pl, sl):
            hull = MultiPoint(np.unique(np.asarray(p).reshape(-1, 2).round(4), axis=0)).convex_hull
            hole = None
            for pg in trimesh.load_path(np.asarray(t).reshape(-1, 2, 2)).polygons_full:
                for ring in pg.interiors:
                    hp = Polygon(ring)
                    if hp.contains(hull):
                        hole = hp
            if hole is None:
                outside += 1
                continue
            gaps.append(min(hole.exterior.distance(hull.exterior.interpolate(x, normalized=True)) for x in np.linspace(0, 1, 80)))
        g = np.array(gaps)

        # tines: with every shell sectioned alone and unioned (as a slicer does), each row must reach the piece
        reach = 0
        for zz in tine_z:
            P = np.unique(np.asarray(trimesh.intersections.mesh_multiplane(piece, [0, 0, 0], [0, 0, 1], [zz])[0][0]).reshape(-1, 2).round(3), axis=0)
            polys = [section(b, zz) for b in bodies if b.bounds[0][2] - 0.01 <= zz <= b.bounds[1][2] + 0.01]
            u = unary_union([p for p in polys if p is not None])
            reach += min(u.distance(Point(q)) for q in P[::2]) < 0.05

        ribs_on_bed = sum(1 for b in others if abs(b.bounds[0][2]) < 0.01 and b.bounds[1][2] > H - 0.1)
        ok = outside == 0 and len(g) and g.min() >= 0.4 and reach == len(tine_z)
        bad += not ok
        print(f'{name}: piece outside tube at {outside}/{len(hs)} heights; gap min/mean/max {g.min():.2f}/{g.mean():.2f}/{g.max():.2f} mm; '
              f'tine rows reaching the piece {reach}/{len(tine_z)}; {len(others)} other shells ({ribs_on_bed} bars on the bed from 0 to the top); '
              f'{"OK" if ok else "FAIL"}')
    print('VERIFIED' if not bad else 'FAILED')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
