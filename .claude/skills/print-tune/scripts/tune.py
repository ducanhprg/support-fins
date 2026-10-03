#!/usr/bin/env python3
"""
The whole tune in one command: read the project, decide per object (no support,
fins, or trees), make the fins, write the plan, build "<project> - tuned.3mf" and
verify it. Prints one line per object and the verdict of the verifier.

    python tune.py PROJECT.3mf [--work DIR] [--out TUNED.3mf] [--coverage 0-100] [--analyze-only]
                   [--rotate "Name=X,Y" ...]   (turn a piece first: world X, then Y, degrees; pose_visible.mjs picks them)

The decision itself lives in analyze_pieces.mjs (references/playbook.md says why);
this script only carries its answers into the plan, so a run costs one command and
its summary, not a session of hand-made scripts.
"""
import argparse, json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..', '..', '..'))
NODE = ['node', '--max-old-space-size=12000']
PRINTERS = ('X2D', 'U1')
# Zero-gap interface in a low-adhesion second material (playbook section 3).
INTERFACE_RECIPE = {'support_top_z_distance': '0', 'support_interface_spacing': '0',
                    'support_bottom_interface_spacing': '0', 'support_interface_top_layers': '3',
                    'support_interface_pattern': 'rectilinear_interlaced'}


def run(cmd, what):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', cwd=REPO)
    if r.returncode not in (0,):
        sys.exit(f'{what} failed ({r.returncode}):\n{(r.stdout + r.stderr)[-1500:]}')
    return r.stdout


def safe(name):
    return re.sub(r'[^\w.-]+', '_', name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project')
    ap.add_argument('--work', help='scratch folder (default: a new temp folder)')
    ap.add_argument('--out', help='tuned project path (default: "<project> - tuned.3mf")')
    ap.add_argument('--coverage', help='force the fins\' wide-face coverage, 0-100 (default: decided per object)')
    ap.add_argument('--analyze-only', action='store_true', help='decide and report, build nothing')
    ap.add_argument('--rotate', action='append', default=[], help='"Name=X,Y": turn that piece first (world X, then Y)')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    src = os.path.abspath(a.project)
    work = os.path.abspath(a.work or tempfile.mkdtemp(prefix='print-tune-'))
    os.makedirs(work, exist_ok=True)
    py = sys.executable
    out_path = a.out or re.sub(r'\.3mf$', '', src, flags=re.I) + ' - tuned.3mf'

    # 0. turns first: the analysis, fins and paint are all cut for the final pose
    if a.rotate:
        turns = {}
        for r in a.rotate:
            name, xy = r.rsplit('=', 1)
            turns[name] = {'rotate': [float(v) for v in xy.split(',')]}
        p0 = os.path.join(work, 'plan-turn.json')
        json.dump({'objects': turns}, open(p0, 'w', encoding='utf-8'), indent=1)
        turned = os.path.join(work, 'turned.3mf')
        print(run([py, os.path.join(HERE, 'build_tuned.py'), src, p0, '--out', turned], 'turning').strip())
        v = subprocess.run([py, os.path.join(HERE, 'verify_tuned.py'), src, turned, p0],
                           capture_output=True, text=True, encoding='utf-8', cwd=REPO)
        if v.returncode:
            sys.exit('turning did not verify:\n' + v.stdout[-1500:])
        src = turned

    # 1. the brief
    pj = os.path.join(work, 'project.json')
    run([py, os.path.join(HERE, 'inspect_project.py'), src, '--json', pj], 'inspect_project')
    info = json.load(open(pj, encoding='utf-8'))
    proc = info['process']
    ours = any(p in (info.get('printer') or '') or p in (info.get('printer_preset') or '') for p in PRINTERS)
    print(f"{os.path.basename(src)}: {info.get('printer_preset')} · {info.get('process_preset')} · "
          f"supports {'on' if proc.get('enable_support') == '1' else 'off'} at {proc.get('support_threshold_angle')}°")

    # 2. the decision, per object
    pieces_json = os.path.join(work, 'pieces.json')
    cmd = NODE + [os.path.join(HERE, 'analyze_pieces.mjs'), src, '--inspect', pj, '--json', pieces_json, '--paint', work]
    if a.coverage is not None:
        cmd += ['--coverage', str(a.coverage)]
    run(cmd, 'analyze_pieces')
    pieces = json.load(open(pieces_json, encoding='utf-8'))['pieces']
    names = [p['name'] for p in pieces]
    dup = {n for n in names if names.count(n) > 1}

    rows, objects, settings, notes = [], {}, {}, []
    supports_on = proc.get('enable_support') == '1'
    over_part = False
    for p in pieces:
        v, d = p['verdict'], p['decide']
        if p.get('unsupported_material') and v in ('fins-only', 'fins+paint'):
            v, d['why'] = 'supports', f"{p['material']}: the engine only knows PLA and PETG, so no fins: trees"
        ov = {}
        ov.update((p.get('support') or {}).get('overrides', {}))
        if v == 'fins-only':
            ov = {'enable_support': '0'} if supports_on else {}
        elif v in ('fins+paint', 'supports'):
            if not supports_on:
                ov['enable_support'] = '1'
            over_part = over_part or d.get('spots_over_part', False)
        ov.update(p['finish']['overrides'])
        for n in p['finish'].get('notes', []):
            notes.append(f"{p['name']}: {n}")
        if p.get('interface_pays') and v in ('fins+paint', 'supports'):
            notes.append(f"{p['name']}: a second support material would pay ({p['interface_pays']})")
        rows.append((p, v, d, ov))
        if p['name'] in dup:
            continue
        spec = {}
        if ov:
            spec['overrides'] = ov
        if v in ('fins-only', 'fins+paint'):
            spec.update(fins=f"fins-{safe(p['name'])}.stl", fin_layer_height=p['layer_height'],
                        fin_material=p['fin_material'], _cli=p['cli_index'], _cov=d['coverage'])
        if v == 'fins+paint' and p.get('paint'):
            spec['paint_supports'] = os.path.basename(p['paint']['file'])
        if spec:
            objects[p['name']] = spec
    if over_part and proc.get('support_on_build_plate_only') == '1':
        settings['support_on_build_plate_only'] = '0'
        notes.append('supports from the plate only would leave spots over the part in the air: off. '
                     'Check the preview for supports inside closed cavities.')
    iface = next((c for c in info.get('interface_candidates', []) if c.get('loadable')), None)
    if iface and any(v in ('fins+paint', 'supports') for _, v, _, _ in rows):
        settings.update(INTERFACE_RECIPE, support_interface_filament=str(iface['slot']))
        notes.append(f"interface in slot {iface['slot']} ({iface['type']}) with a zero gap")

    # 3. the table
    print(f"\n{'object':34} {'verdict':12} why")
    for p, v, d, ov in rows:
        tag = ' (same name as another object: not tuned, rename it in the slicer)' if p['name'] in dup else ''
        print(f"{p['name'][:34]:34} {v:12} {d['why']}{tag}")
        if ov and p['name'] not in dup:
            print(f"{'':47}set: {', '.join(f'{k}={v_}' for k, v_ in ov.items())}")
    for n in notes:
        print(f'note: {n}')
    if a.analyze_only or not ours:
        if not ours:
            print('\nnot built: the project is set up for another printer; re-save it for the X2D or the U1 first')
        return 0
    if not objects and not settings:
        print('\nnothing to change: the project prints as it stands')
        return 0

    # 4. the fins, exactly as decided (same coverage), then build and verify
    for name, spec in objects.items():
        if 'fins' in spec:
            run(NODE + [os.path.join(REPO, 'plugins', 'cli', 'support-fins.js'), src, '--object', str(spec.pop('_cli')),
                        '--material', spec['fin_material'], '--layer-height', str(spec['fin_layer_height']),
                        '--coverage', str(spec.pop('_cov')), '--fins-only', '-o', os.path.join(work, spec['fins'])],
                f'fins for {name}')
    plan = os.path.join(work, 'plan.json')
    json.dump({'settings': settings, 'objects': objects}, open(plan, 'w', encoding='utf-8'), indent=1)
    build = [py, os.path.join(HERE, 'build_tuned.py'), src, plan, '--out', out_path]
    out = run(build, 'build_tuned')
    tuned = re.search(r'wrote (.+\.3mf)', out).group(1).strip()
    r = subprocess.run([py, os.path.join(HERE, 'verify_tuned.py'), src, tuned, plan],
                       capture_output=True, text=True, encoding='utf-8', cwd=REPO)
    fails = [l for l in r.stdout.splitlines() if l.strip().startswith('FAIL')]
    print(f"\n{tuned}\n{'VERIFIED' if r.returncode == 0 else 'NOT VERIFIED'} (not sliced)  ·  work: {work}")
    for l in fails:
        print(l)
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
