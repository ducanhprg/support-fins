#!/usr/bin/env python3
"""
Check a tuned project against the original it was made from, the way a slicer
would read it: nothing but the planned changes may differ.

    python verify_tuned.py ORIGINAL.3mf TUNED.3mf PLAN.json

Checks: the zip reads; the same entries in the same order; only the mesh files
that got fins and the two settings files changed; changed XML parses; the
settings diff is exactly the plan's; per-object overrides are the plan's; plates
are unchanged; and (via verify_fins.mjs, the engine's own reader) every merged
fin sits exactly where the engine placed it. Exit 1 on any failure.
"""
import json, os, re, subprocess, sys, zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    orig, tuned, plan_path = sys.argv[1:4]
    plan = json.load(open(plan_path, encoding='utf-8'))
    SET = {k: str(v) for k, v in plan.get('settings', {}).items()}
    OBJ = plan.get('objects', {})
    A, B = zipfile.ZipFile(orig), zipfile.ZipFile(tuned)
    fails = []

    def check(ok, msg):
        print(('  ok    ' if ok else '  FAIL  ') + msg)
        if not ok:
            fails.append(msg)

    check(B.testzip() is None, 'zip reads cleanly')
    check([i.filename for i in A.infolist()] == [i.filename for i in B.infolist()], 'same entries, same order')
    changed = [n for n in A.namelist() if A.read(n) != B.read(n)]
    allowed = {'Metadata/project_settings.config', 'Metadata/model_settings.config'}
    meshes = [n for n in changed if n not in allowed]
    check(all(n.startswith('3D/Objects/') for n in meshes) and len(meshes) <= sum(1 for s in OBJ.values() if s.get('fins')),
          f'changed only settings + fin meshes: {changed}')
    for n in changed:
        if n.endswith(('.model', '.config')) and n != 'Metadata/project_settings.config':
            try:
                ET.fromstring(B.read(n))
                check(True, f'XML parses: {n}')
            except ET.ParseError as e:
                check(False, f'XML parses: {n} ({e})')

    pa = json.loads(A.read('Metadata/project_settings.config'))
    pb = json.loads(B.read('Metadata/project_settings.config'))
    diff = {k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k) and k != 'different_settings_to_system'}
    want = {k for k, v in SET.items() if pa.get(k) != v}
    check(diff == want and all(pb[k] == SET[k] for k in want), f'settings diff is exactly the plan: {sorted(diff)}')

    ma = A.read('Metadata/model_settings.config').decode('utf-8')
    mb = B.read('Metadata/model_settings.config').decode('utf-8')
    plates = lambda m: [re.findall(r'key="object_id" value="(\d+)"', p) for p in re.findall(r'<plate>(.*?)</plate>', m, re.S)]
    check(plates(ma) == plates(mb), f'plates unchanged: {plates(mb)}')
    for name, spec in OBJ.items():
        m = re.search(r'<metadata key="name" value="' + re.escape(name) + r'"/>(.*?)<part', mb, re.S)
        got = dict(re.findall(r'<metadata key="([^"]+)" value="([^"]*)"', m.group(1))) if m else {}
        ov = {k: str(v) for k, v in spec.get('overrides', {}).items()}
        check(all(got.get(k) == v for k, v in ov.items()), f'{name}: overrides {ov}')

    pairs = [f'{n}={os.path.join(os.path.dirname(os.path.abspath(plan_path)), s["fins"])}' for n, s in OBJ.items() if s.get('fins')]
    if pairs:
        r = subprocess.run(['node', '--max-old-space-size=12000', os.path.join(HERE, 'verify_fins.mjs'), tuned, *pairs],
                           capture_output=True, text=True, encoding='utf-8')
        for line in r.stdout.strip().splitlines():
            check(r.returncode == 0, 'fins: ' + line)
        if r.returncode not in (0, 1):
            check(False, 'verify_fins.mjs failed: ' + r.stderr[-400:])
    print('VERIFIED' if not fails else f'{len(fails)} CHECK(S) FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
