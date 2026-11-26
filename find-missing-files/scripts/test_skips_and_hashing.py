import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import check_missing_files_between_two_folders as cm  # noqa: E402


def _args(monkeypatch, *extra):
    monkeypatch.setattr(sys, 'argv', ['prog', 'src', 'dest', *extra])
    return cm.parse_args()


def test_default_skips_apply_without_flags(monkeypatch):
    args = _args(monkeypatch)
    assert args.skip_extension == ['.THM', '.LRV']
    assert args.src_skip_root_subdir == ['Backedup']


def test_extra_skips_add_to_the_defaults(monkeypatch):
    args = _args(monkeypatch, '--skip-extension', '.AAE', '--src-skip-root-subdir', 'Old')
    assert args.skip_extension == ['.THM', '.LRV', '.AAE']
    assert args.src_skip_root_subdir == ['Backedup', 'Old']


def test_no_default_skips_compares_everything(monkeypatch):
    args = _args(monkeypatch, '--no-default-skips')
    assert args.skip_extension == []
    assert args.src_skip_root_subdir == []
    args = _args(monkeypatch, '--no-default-skips', '--skip-extension', '.AAE')
    assert args.skip_extension == ['.AAE']


def _counting_hash(monkeypatch):
    hashed = []
    real = cm.hash_file

    def counting(path, chunk_size):
        hashed.append(Path(path).name)
        return real(path, chunk_size)
    monkeypatch.setattr(cm, 'hash_file', counting)
    return hashed


def test_dest_files_whose_size_is_not_in_the_source_are_not_hashed(monkeypatch, tmp_path):
    src, dest = tmp_path / 'src', tmp_path / 'dest'
    (src).mkdir()
    (dest).mkdir()
    (src / 'a.jpg').write_bytes(b'1234')
    (dest / 'a.jpg').write_bytes(b'1234')
    (dest / 'big.mov').write_bytes(b'x' * 100)
    hashed = _counting_hash(monkeypatch)
    out = tmp_path / 'tree.txt'
    monkeypatch.setattr(sys, 'argv', ['prog', str(src), str(dest), '--workers', '1', '--output', str(out)])
    cm.main()
    assert 'big.mov' not in hashed
    assert 'No missing files' in out.read_text()


def test_build_dest_hash_sets_only_sizes(monkeypatch, tmp_path):
    (tmp_path / 'a').write_bytes(b'12')
    (tmp_path / 'b').write_bytes(b'123')
    index = cm.build_dest_index(tmp_path, (), (), False)
    hashed = _counting_hash(monkeypatch)
    sets = cm.build_dest_hash_sets(index, 1024, 1, False, only_sizes={2})
    assert hashed == ['a']
    assert set(sets) == {2}
