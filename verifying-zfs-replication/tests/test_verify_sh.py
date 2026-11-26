"""End-to-end tests for verify.sh against fake hosts.

ssh, scp, sudo and zfs are replaced by a fake on PATH: each "host" is a directory used as its HOME, and
each dataset is a directory with its snapshot list, mountpoint and `zfs diff` output. Nothing touches a
real pool or a real remote host.
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'verify.sh'

FAKE = r'''#!/usr/bin/env python3
import os, shutil, subprocess, sys
root = os.environ['FAKE_ROOT']
name = os.path.basename(sys.argv[0])
args = sys.argv[1:]

def home(h):
    p = os.path.join(root, 'hosts', h)
    os.makedirs(p, exist_ok=True)
    return p

def dsdir(ds):
    return os.path.join(root, 'zfs', ds.replace('/', '_'))

if name == 'ssh':
    while args and args[0].startswith('-'):
        args = args[2:] if args[0] == '-o' else args[1:]
    host, cmd = args[0], ' '.join(args[1:])
    env = dict(os.environ, HOME=home(host), FAKE_HOST=host)
    if cmd == 'bash -s':
        sys.exit(subprocess.call(['bash', '-s'], env=env))
    sys.exit(subprocess.call(['bash', '-c', cmd], env=env))
if name == 'scp':
    args = [a for a in args if a != '-q']
    *srcs, dst = args
    def loc(spec):
        if ':' in spec:
            h, p = spec.split(':', 1)
            return os.path.join(home(h), p)
        return spec
    for s in srcs:
        shutil.copy(loc(s), loc(dst))
    sys.exit(0)
if name == 'sudo':
    if args and args[0] == '-n':
        args = args[1:]
    os.execvp(args[0], args)
if name == 'zfs':
    if args[0] == 'list':                    # list -H -t snapshot -d 1 -o name,guid -s createtxg DS
        ds = args[-1]
        for line in open(os.path.join(dsdir(ds), 'snaps')):
            snap, guid, txg = line.split()
            print(f'{ds}@{snap}\t{guid}')
        sys.exit(0)
    if args[0] == 'get':                     # get -H[p] -o value PROP DS[@snap]
        prop, target = args[-2], args[-1]
        ds, _, snap = target.partition('@')
        if prop == 'receive_resume_token':
            print('-')
        elif prop == 'mountpoint':
            print(open(os.path.join(dsdir(ds), 'mountpoint')).read().strip())
        elif prop == 'createtxg':
            for line in open(os.path.join(dsdir(ds), 'snaps')):
                s, guid, txg = line.split()
                if s == snap:
                    print(txg)
        else:
            sys.exit(f'fake zfs: unknown property {prop}')
        sys.exit(0)
    if args[0] == 'diff':                    # diff -FH DS@from DS@to
        ds = args[-1].partition('@')[0]
        sys.stdout.write(open(os.path.join(dsdir(ds), 'diff')).read())
        sys.exit(0)
sys.exit(f'fake: unhandled {name} {args}')
'''


@pytest.fixture
def world(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    fake = bindir / 'fake.py'
    fake.write_text(FAKE)
    fake.chmod(0o755)
    for n in ('ssh', 'scp', 'sudo', 'zfs'):
        (bindir / n).symlink_to(fake)
    env = dict(os.environ, FAKE_ROOT=str(tmp_path), PATH=f'{bindir}:{os.environ["PATH"]}',
               POLL='0.1', THREADS='2', HOME=str(tmp_path / 'local-home'))
    (tmp_path / 'local-home').mkdir()

    class World:
        def dataset(self, name, files, snaps=(('a', 1, 10), ('b', 2, 20)), changed=None):
            d = tmp_path / 'zfs' / name.replace('/', '_')
            d.mkdir(parents=True, exist_ok=True)
            mount = tmp_path / 'mnt' / name.replace('/', '_')
            (d / 'mountpoint').write_text(str(mount))
            (d / 'snaps').write_text(''.join(f'{s} {g} {t}\n' for s, g, t in snaps))
            snapdir = mount / '.zfs' / 'snapshot' / snaps[-1][0]
            if snapdir.exists():
                shutil.rmtree(snapdir)
            snapdir.mkdir(parents=True)
            for rel, content in files.items():
                p = snapdir / rel
                p.write_bytes(content)
                os.utime(p, ns=(1_000_000_000, 1_000_000_000))
            changed = files if changed is None else changed
            (d / 'diff').write_text(''.join(f'+\tF\t{mount}/{rel}\n' for rel in changed))

        def run(self, *args):
            return subprocess.run(['bash', str(SCRIPT), *args], env=env, capture_output=True, text=True,
                                  timeout=60)

    return World()


def args_for(ds_src, ds_dst):
    return ['srchost', ds_src, 'dsthost', ds_dst, 'a', 'b']


def test_matching_replication_passes(world):
    world.dataset('tank/a', {'f.txt': b'same'})
    world.dataset('backup/a', {'f.txt': b'same'})
    r = world.run(*args_for('tank/a', 'backup/a'))
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'PASS' in r.stdout


def test_second_dataset_with_same_snapshot_names_is_not_given_the_first_ones_result(world):
    world.dataset('tank/a', {'f.txt': b'same'})
    world.dataset('backup/a', {'f.txt': b'same'})
    world.dataset('tank/b', {'g.txt': b'source bytes'})
    world.dataset('backup/b', {'g.txt': b'CORRUPT bytes'})
    first = world.run(*args_for('tank/a', 'backup/a'))
    assert 'PASS' in first.stdout, first.stdout + first.stderr
    second = world.run(*args_for('tank/b', 'backup/b'))
    assert 'PASS' not in second.stdout, second.stdout + second.stderr
    assert second.returncode != 0
    assert 'DIFF' in second.stdout


def test_rerun_after_a_re_receive_does_not_reuse_stale_target_results(world):
    world.dataset('tank/a', {'f.txt': b'same'})
    world.dataset('backup/a', {'f.txt': b'same'})
    assert 'PASS' in world.run(*args_for('tank/a', 'backup/a')).stdout
    # the target is destroyed and received again: same names and GUIDs, new createtxg, different bytes
    world.dataset('backup/a', {'f.txt': b'damaged'}, snaps=(('a', 1, 50), ('b', 2, 60)))
    r = world.run(*args_for('tank/a', 'backup/a'))
    assert 'PASS' not in r.stdout, r.stdout + r.stderr
    assert r.returncode != 0


def test_fresh_flag_discards_finished_work(world):
    world.dataset('tank/a', {'f.txt': b'same'})
    world.dataset('backup/a', {'f.txt': b'same'})
    assert 'PASS' in world.run(*args_for('tank/a', 'backup/a')).stdout
    # same snapshot identity, but the target bytes change (simulates a bad cached result)
    world.dataset('backup/a', {'f.txt': b'changed'})
    cached = world.run(*args_for('tank/a', 'backup/a'))
    assert 'PASS' in cached.stdout            # a plain rerun resumes, by design
    fresh = world.run('--fresh', *args_for('tank/a', 'backup/a'))
    assert 'PASS' not in fresh.stdout, fresh.stdout + fresh.stderr
    assert fresh.returncode == 1
    assert 'sha256  f.txt' in fresh.stdout


def test_work_dir_with_other_parameters_is_refused(world, tmp_path):
    world.dataset('tank/a', {'f.txt': b'same'})
    world.dataset('backup/a', {'f.txt': b'same'})
    assert world.run(*args_for('tank/a', 'backup/a')).returncode == 0
    stamps = list((tmp_path / 'hosts' / 'srchost').glob('zfs-verify-*/params'))
    assert len(stamps) == 1
    stamps[0].write_text('something else\n')
    r = world.run(*args_for('tank/a', 'backup/a'))
    assert r.returncode != 0
    assert 'PASS' not in r.stdout
    assert 'params' in (r.stdout + r.stderr).lower()


def test_usage_without_arguments(world):
    r = world.run()
    assert r.returncode == 2
