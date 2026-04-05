import runpy
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_missing_files_between_two_folders as cm  # noqa: E402


def write_file(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_normalized_extensions_handles_mixed_input():
    result = cm.normalized_extensions(['.JPG', 'mov', 'TXT'])
    assert result == ('.jpg', '.mov', '.txt')


def test_should_skip_file_metadata_cases():
    assert cm.should_skip_file(('foo', '.Trashes', 'clip.mov'), ('.mov',))
    assert cm.should_skip_file(('foo', '._hidden.mov'), ('.mp4',))
    assert not cm.should_skip_file(('foo', 'video.mp4'), ('.mov',))


def test_iter_files_respects_skip_rules(tmp_path):
    base = tmp_path / 'source'
    (base / 'Backedup').mkdir(parents=True)
    write_file(base / 'Backedup' / 'ignored.bin', b'nope')
    write_file(base / '.Spotlight-V100' / 'metadata.bin', b'skipme')
    write_file(base / 'keep' / 'photo.jpg', b'data')
    write_file(base / 'keep' / 'clip.thm', b'should skip extension')
    skip_ext = cm.normalized_extensions(['.thm'])
    found = list(cm.iter_files(base, ('Backedup',), skip_ext, verbose=True))
    rel_paths = sorted(rel.as_posix() for rel, _ in found)
    assert rel_paths == ['keep/photo.jpg']


def test_build_tree_renders_hierarchy():
    paths = ['folder/sub/file.txt', 'folder/alpha.txt', 'other.doc']
    tree = cm.build_tree(paths)
    assert tree[0] == '|-- folder'
    assert tree[-1] == '`-- other.doc'


def test_build_dest_index_handles_stat_error(monkeypatch, tmp_path, capsys):
    dest = tmp_path / 'dest'
    write_file(dest / 'ok.bin', b'data')
    write_file(dest / 'bad.bin', b'xxx')
    original_stat = Path.stat

    def fake_stat(self, **kwargs):
        if self.name == 'bad.bin':
            raise PermissionError('denied')
        return original_stat(self, **kwargs)

    monkeypatch.setattr(Path, 'stat', fake_stat)
    index = cm.build_dest_index(dest, (), (), verbose=True)
    assert len(index) == 1
    captured = capsys.readouterr()
    assert 'could not stat destination file' in captured.err


def test_parallel_hash_jobs_process_pool_path(monkeypatch, tmp_path):
    files = []
    for idx in range(2):
        path = tmp_path / f'file{idx}.bin'
        write_file(path, f'c{idx}'.encode())
        files.append(cm.HashJob(rel=path.name, path=path, size=path.stat().st_size))

    class DummyFuture:
        def __init__(self, fn, args):
            self.fn = fn
            self.args = args

        def result(self):
            return self.fn(*self.args)

    class DummyExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args):
            return DummyFuture(fn, args)

    def fake_wait(futures, return_when):
        return set(futures), set()

    monkeypatch.setattr(cm, 'ProcessPoolExecutor', DummyExecutor)
    monkeypatch.setattr(cm, 'wait', fake_wait)
    results = list(cm.parallel_hash_jobs(iter(files), chunk_size=4, workers=2))
    assert all(digest for _, digest, error in results if digest)
    assert len(results) == 2


def test_build_dest_hash_sets_handles_hash_errors(monkeypatch, tmp_path, capsys):
    good = tmp_path / 'good.bin'
    bad = tmp_path / 'bad.bin'
    write_file(good, b'a')
    write_file(bad, b'b')
    dest_index = {1: [good], 2: [bad]}

    def fake_parallel(jobs, chunk_size, workers):
        jobs = list(jobs)
        yield jobs[0], 'digest-a', None
        yield jobs[1], None, RuntimeError('fail')

    monkeypatch.setattr(cm, 'parallel_hash_jobs', fake_parallel)
    hashes = cm.build_dest_hash_sets(dest_index, chunk_size=4, workers=2, verbose=True)
    assert hashes[1] == {'digest-a'}
    captured = capsys.readouterr()
    assert 'could not hash destination file' in captured.err


def test_find_missing_files_handles_stat_and_hash_errors(monkeypatch, tmp_path, capsys):
    src = tmp_path / 'src'
    write_file(src / 'match.txt', b'a')
    write_file(src / 'statfail.txt', b'b')
    write_file(src / 'hashfail.txt', b'c')
    write_file(src / 'missing.txt', b'ddd')

    original_stat = Path.stat

    def fake_stat(self, **kwargs):
        if self.name == 'statfail.txt':
            raise PermissionError('cannot stat')
        return original_stat(self, **kwargs)

    monkeypatch.setattr(Path, 'stat', fake_stat)

    def fake_parallel(jobs, chunk_size, workers):
        for job in jobs:
            if job.path.name == 'hashfail.txt':
                yield job, None, RuntimeError('hash boom')
            elif job.path.name == 'match.txt':
                yield job, 'match-digest', None
            elif job.path.name == 'missing.txt':
                yield job, 'missing-digest', None

    monkeypatch.setattr(cm, 'parallel_hash_jobs', fake_parallel)
    dest_hash_sets = {len(b'a'): {'match-digest'}}
    missing = cm.find_missing_files(
        src,
        dest_hash_sets,
        cm.DEFAULT_SRC_SKIP_ROOT_SUBDIRS,
        (),
        chunk_size=4,
        workers=1,
        verbose=True,
    )
    # stat-failed and hash-failed files must be treated as missing (safety:
    # if we can't verify a source file exists in destination, report it).
    assert sorted(missing) == ['hashfail.txt', 'missing.txt', 'statfail.txt']
    err = capsys.readouterr().err
    assert 'could not stat source file' in err
    assert 'could not hash source file' in err


def test_end_to_end_missing_detection(tmp_path):
    src = tmp_path / 'src'
    dest = tmp_path / 'dest'
    write_file(src / 'Backedup' / 'ignore.txt', b'ignore me')
    write_file(src / 'photos' / 'cat.jpg', b'same-bytes')
    write_file(src / 'photos' / 'dog.jpg', b'unique-src')
    write_file(dest / 'photos' / 'cat.jpg', b'same-bytes')
    write_file(dest / 'photos' / 'dog.jpg', b'different-dest')
    skip_ext = cm.normalized_extensions(cm.DEFAULT_SKIP_EXTENSIONS)
    dest_index = cm.build_dest_index(dest, (), skip_ext, verbose=False)
    dest_hash_sets = cm.build_dest_hash_sets(dest_index, chunk_size=4, workers=1, verbose=False)
    missing = cm.find_missing_files(
        src,
        dest_hash_sets,
        cm.DEFAULT_SRC_SKIP_ROOT_SUBDIRS,
        skip_ext,
        chunk_size=4,
        workers=1,
        verbose=False,
    )
    assert set(missing) == {'photos/dog.jpg'}


def test_parse_args_accepts_custom_values(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'prog',
            str(tmp_path / 'src'),
            str(tmp_path / 'dest'),
            '--skip-extension',
            'JPG',
            '--src-skip-root-subdir',
            'Ignore',
            '--dest-skip-root-subdir',
            'Cache',
            '--chunk-size',
            '512',
            '--workers',
            '2',
            '--output',
            str(tmp_path / 'tree.txt'),
            '--verbose',
        ],
    )
    args = cm.parse_args()
    assert Path(args.source).name == 'src'
    assert args.verbose is True
    assert 'Ignore' in args.src_skip_root_subdir
    assert 'Cache' in args.dest_skip_root_subdir
    assert 'JPG' in args.skip_extension
    assert args.chunk_size == 512
    assert args.workers == 2


def test_log_respects_verbose_flag(capsys):
    cm.log('quiet', verbose=False)
    assert capsys.readouterr().err == ''
    cm.log('loud', verbose=True)
    assert 'loud' in capsys.readouterr().err


def test_main_writes_report(tmp_path, monkeypatch):
    src = tmp_path / 'src'
    dest = tmp_path / 'dest'
    write_file(src / 'photos' / 'cat.jpg', b'same')
    write_file(src / 'photos' / 'dog.jpg', b'unique')
    write_file(dest / 'photos' / 'cat.jpg', b'same')
    output = tmp_path / 'result.txt'
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'prog',
            str(src),
            str(dest),
            '--output',
            str(output),
            '--workers',
            '1',
        ],
    )
    cm.main()
    text = output.read_text()
    assert 'dog.jpg' in text


def test_main_missing_source(monkeypatch, tmp_path):
    dest = tmp_path / 'dest'
    dest.mkdir()
    monkeypatch.setattr(sys, 'argv', ['prog', str(tmp_path / 'missing'), str(dest)])
    with pytest.raises(SystemExit) as excinfo:
        cm.main()
    assert 'Source directory not found' in str(excinfo.value)


def test_emit_progress_writes_percentage(capsys):
    import io
    out = io.StringIO()
    cm.emit_progress(10, 84, file=out)
    text = out.getvalue()
    assert '10' in text
    assert '84' in text
    assert '11%' in text  # floor(10/84*100) == 11


def test_emit_progress_writes_100_percent_when_done():
    import io
    out = io.StringIO()
    cm.emit_progress(84, 84, file=out)
    text = out.getvalue()
    assert '100%' in text


def test_entry_point_runs_via_runpy(monkeypatch):
    script = HERE / 'check_missing_files_between_two_folders.py'
    monkeypatch.setattr(sys, 'argv', ['prog', '--help'])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(script), run_name='__main__')
    assert excinfo.value.code == 0
