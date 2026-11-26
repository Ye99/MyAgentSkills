"""Make photo_cull importable from every test module, whatever order pytest runs them in."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
