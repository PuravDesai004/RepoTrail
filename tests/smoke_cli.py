"""Run once manually: python tests/smoke_cli.py; target lives only in a temp fixture."""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from repotrail.models import Inventory
from repotrail.source_models import PythonSourceMap

SMOKE_SOURCE = b"from .helpers import value as v\n\n@decorator\nclass Service:\n    async def run(self):\n        return v\n"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="repotrail-smoke-") as temporary:
        root = Path(temporary) / "sample repo Ω"
        root.mkdir()
        subprocess.run(
            ["git", "-c", "init.templateDir=", "init", "-b", "main", str(root)],
            check=True,
            capture_output=True,
            timeout=10,
        )
        (root / "hello.py").write_text(
            "raise RuntimeError('must never execute')\n", encoding="utf-8"
        )
        command = [sys.executable, "-m", "repotrail", "inspect", str(root), "--json"]
        result = subprocess.run(command, check=True, capture_output=True, timeout=30)
        payload = json.loads(result.stdout)
        Inventory.model_validate_json(result.stdout)
        assert payload["git"] == {
            "head_commit": None,
            "branch": "main",
            "detached": False,
            "unborn": True,
        }
        assert payload["files"] == [{"path": "hello.py", "extension": ".py", "tracked": False}]
        assert payload["counts"] == {"total_files": 1, "tracked_files": 0, "untracked_files": 1}
        # The old command still returns its original schema before the new read/parse path.
        assert set(payload) == {"schema_version", "project_name", "git", "files", "counts"}
        (root / "hello.py").write_bytes(SMOKE_SOURCE)
        symbols_command = [sys.executable, "-m", "repotrail", "symbols", str(root), "--json"]
        symbols = subprocess.run(symbols_command, check=True, capture_output=True, timeout=30)
        source_map = PythonSourceMap.model_validate_json(symbols.stdout)
        assert (
            source_map.complete
            and source_map.files[0].sha256 == hashlib.sha256(SMOKE_SOURCE).hexdigest()
        )
        assert [
            (item.qualified_name, item.start_line, item.definition_line, item.end_line)
            for item in source_map.files[0].symbols
        ] == [("Service", 3, 4, 6), ("Service.run", 5, 5, 6)]
        assert source_map.files[0].imports[0].module == "helpers"
        assert source_map.files[0].imports[0].level == 1
        assert source_map.counts.declarations == 2 and source_map.counts.import_statements == 1
        print(symbols.stdout.decode("ascii").replace("\r\n", "\n"), end="")
        print(
            "CLI smoke: PASS (symbols exit 0 and inspect regression; isolated temporary repository)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
