"""Activate optional wheels before Transformers probes backend availability."""
import sys
from pathlib import Path

# Preserve source for standard-library introspection by torch JIT/dynamo.
stdlib = str(Path(sys._MEIPASS) / "runtime_stdlib")
sys.path.insert(0, stdlib)
from thundertalk.core.runtime import activate  # noqa: E402
activate()
