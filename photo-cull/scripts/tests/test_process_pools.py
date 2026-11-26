"""Worker processes are spawned, never forked.

`run` scans while the analysis threads hold GPU models; forking a process
that has threads (and CUDA state) can deadlock the child.
"""
import inspect
import re

import photo_cull as pc


def test_process_pool_uses_spawn():
    pool = pc._process_pool(1)
    try:
        assert pool._mp_context.get_start_method() == "spawn"
    finally:
        pool.shutdown()


def test_every_process_pool_goes_through_the_spawn_helper():
    source = inspect.getsource(pc)
    helper = inspect.getsource(pc._process_pool)
    calls = re.findall(r"ProcessPoolExecutor\(", source.replace(helper, ""))
    assert calls == [], "create worker processes with _process_pool, which spawns them"
