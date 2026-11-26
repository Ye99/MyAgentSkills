import io
import json
import sys
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import locationiq_nearby_poi as liq  # noqa: E402


def _http_error(code, error):
    return HTTPError("https://us1.locationiq.com/v1/nearby", code, "err", {},
                     io.BytesIO(json.dumps({"error": error}).encode()))


class _Ok:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'[{"name": "A"}]'


def test_per_second_limit_is_retried_with_backoff():
    sleeps = []
    calls = [_http_error(429, "Rate Limited Second"), _http_error(429, "Rate Limited Minute"), _Ok()]

    def fake_urlopen(*a, **k):
        r = calls.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    with patch.object(liq, "urlopen", fake_urlopen):
        pois = liq.fetch_nearby_poi("k", 1.0, 2.0, sleep=sleeps.append)
    assert pois == [{"name": "A"}]
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0] > 0


def test_daily_limit_stops_at_once():
    sleeps = []
    with patch.object(liq, "urlopen", side_effect=_http_error(429, "Rate Limited Day")):
        with pytest.raises(RuntimeError, match="daily"):
            liq.fetch_nearby_poi("k", 1.0, 2.0, sleep=sleeps.append)
    assert sleeps == []


def test_retries_are_bounded():
    sleeps = []
    with patch.object(liq, "urlopen", side_effect=_http_error(429, "Rate Limited Second")) as u:
        with pytest.raises(RuntimeError, match="429"):
            liq.fetch_nearby_poi("k", 1.0, 2.0, sleep=sleeps.append, max_attempts=4)
    assert u.call_count == 4
    assert len(sleeps) == 3


def test_other_http_errors_are_not_retried():
    with patch.object(liq, "urlopen", side_effect=_http_error(401, "Invalid key")) as u:
        with pytest.raises(RuntimeError, match="401"):
            liq.fetch_nearby_poi("k", 1.0, 2.0, sleep=lambda s: None)
    assert u.call_count == 1
