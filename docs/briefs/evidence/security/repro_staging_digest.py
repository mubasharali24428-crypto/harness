"""Run with section4/.venv/bin/python; uses disposable files only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from harness_orchestrator import staging

for case in ('binary', 'truncated_text'):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ws = root / 'run'
        ws.mkdir()
        p = ws / 'data'
        p.write_bytes(b'\xffold' if case == 'binary' else b'old\n' * 60)
        staging.ensure_baseline('run', str(ws), tmp)
        original = b'\xffreviewed' if case == 'binary' else b'new\n' * 60000 + b'reviewed\n'
        altered = b'\xffUNREVIEWED' if case == 'binary' else b'new\n' * 60000 + b'UNREVIEWED\n'
        p.write_bytes(original)
        group = staging.stage('run', str(ws), tmp).groups[0]
        (root / '.staged/run/files/data').write_bytes(altered)
        assert staging.pending_groups('run', tmp)[0].digest == group.digest
        staging.apply_group('run', str(ws), tmp, group.action, group.paths, group.digest)
        assert p.read_bytes() == altered
        print(f'CONFIRMED {case}: changed bytes retain digest and are applied')
