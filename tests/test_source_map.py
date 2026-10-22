import codecs
import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import git_command, write_file
from pydantic import ValidationError

import repotrail.analysis as analysis
from repotrail.analysis import analyze_python_repository
from repotrail.cli import main
from repotrail.git import InventoryError
from repotrail.source_models import AnalysisLimits, PythonSourceMap

DECLARATIONS_SOURCE = """import os.path as op, sys
from .helpers import item as alias
from . import sibling
from package import *
__import__("dynamic_only")

@decorate
class Service:
    if ENABLED:
        @staticmethod
        def run():
            import json as js
            def inner():
                pass
            return inner
    @classmethod
    async def fetch(cls):
        from ..net import client as c
        class Result:
            pass
        return Result

@(
    decorate
)
def outer():
    try:
        class Local:
            async def act(self):
                pass
    except Exception:
        def fallback():
            pass
    return Local

if FLAG:
    def repeated():
        pass
else:
    def repeated():
        pass

try:
    import package.submodule as dotted
except ImportError:
    from fallback import (
        replacement as other,
    )

async def top_async():
    if FLAG:
        def nested():
            pass
    return None
"""


def test_exact_declarations_and_static_imports(repo):
    write_file(repo, "map.py", DECLARATIONS_SOURCE)
    result = analyze_python_repository(repo)
    file = result.files[0]
    assert result.complete and file.line_count == 54
    expected = [
        ("Service", "class", None, False, 8, 7, 21),
        ("Service.run", "method", "Service", False, 11, 10, 15),
        ("Service.run.inner", "function", "Service.run", False, 13, 13, 14),
        ("Service.fetch", "method", "Service", True, 17, 16, 21),
        ("Service.fetch.Result", "class", "Service.fetch", False, 19, 19, 20),
        ("outer", "function", None, False, 26, 23, 34),
        ("outer.Local", "class", "outer", False, 28, 28, 30),
        ("outer.Local.act", "method", "outer.Local", True, 29, 29, 30),
        ("outer.fallback", "function", "outer", False, 32, 32, 33),
        ("repeated", "function", None, False, 37, 37, 38),
        ("repeated", "function", None, False, 40, 40, 41),
        ("top_async", "function", None, True, 50, 50, 54),
        ("top_async.nested", "function", "top_async", False, 52, 52, 53),
    ]
    assert [
        (
            symbol.qualified_name,
            symbol.kind,
            symbol.parent_qualified_name,
            symbol.is_async,
            symbol.definition_line,
            symbol.start_line,
            symbol.end_line,
        )
        for symbol in file.symbols
    ] == expected
    for symbol, (qualified, _kind, _parent, _async, line, _start, _end) in zip(
        file.symbols, expected, strict=True
    ):
        assert symbol.name == qualified.rsplit(".", 1)[-1]
        assert symbol.id == f"map.py::{qualified}:{line}"
    assert [statement.model_dump() for statement in file.imports] == [
        {
            "kind": "import",
            "module": None,
            "level": 0,
            "names": [{"name": "os.path", "asname": "op"}, {"name": "sys", "asname": None}],
            "scope": None,
            "start_line": 1,
            "end_line": 1,
        },
        {
            "kind": "from",
            "module": "helpers",
            "level": 1,
            "names": [{"name": "item", "asname": "alias"}],
            "scope": None,
            "start_line": 2,
            "end_line": 2,
        },
        {
            "kind": "from",
            "module": None,
            "level": 1,
            "names": [{"name": "sibling", "asname": None}],
            "scope": None,
            "start_line": 3,
            "end_line": 3,
        },
        {
            "kind": "from",
            "module": "package",
            "level": 0,
            "names": [{"name": "*", "asname": None}],
            "scope": None,
            "start_line": 4,
            "end_line": 4,
        },
        {
            "kind": "import",
            "module": None,
            "level": 0,
            "names": [{"name": "json", "asname": "js"}],
            "scope": "Service.run",
            "start_line": 12,
            "end_line": 12,
        },
        {
            "kind": "from",
            "module": "net",
            "level": 2,
            "names": [{"name": "client", "asname": "c"}],
            "scope": "Service.fetch",
            "start_line": 18,
            "end_line": 18,
        },
        {
            "kind": "import",
            "module": None,
            "level": 0,
            "names": [{"name": "package.submodule", "asname": "dotted"}],
            "scope": None,
            "start_line": 44,
            "end_line": 44,
        },
        {
            "kind": "from",
            "module": "fallback",
            "level": 0,
            "names": [{"name": "replacement", "asname": "other"}],
            "scope": None,
            "start_line": 46,
            "end_line": 48,
        },
    ]
    assert result.counts.model_dump() == {
        "total_python_files": 1,
        "analyzed_files": 1,
        "failed_files": 0,
        "declarations": 13,
        "import_statements": 8,
    }
    assert PythonSourceMap.model_validate_json(result.model_dump_json()) == result


def test_inventory_reuse_opens_only_eligible_python(ordinary_repo, monkeypatch):
    repo = ordinary_repo
    write_file(repo, ".gitignore", (repo / ".gitignore").read_text() + "ignored_python.py\n")
    for name in ["ignored_python.py", ".env.hidden.py", "stub.pyi", "notes.txt", "build/trap.py"]:
        write_file(repo, name, "must never be parsed!!!\n")
    write_file(repo, "extra.PY", "def café():\n    pass\n")
    real_open = Path.open
    opened = []

    def record_open(path, *args, **kwargs):
        if args and args[0] == "rb":
            opened.append(path.relative_to(repo).as_posix())
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", record_open)
    result = analyze_python_repository(repo)
    assert result.complete
    assert opened == ["extra.PY", "staged.py", "tracked.py", "trap.py"]
    assert [(file.path, file.tracked) for file in result.files] == [
        ("extra.PY", False),
        ("staged.py", True),
        ("tracked.py", True),
        ("trap.py", False),
    ]
    assert not (repo / "EXECUTED").exists()


def test_encoding_hashes_diagnostics_and_partial_cli(repo):
    raw_files = {
        "utf8.py": "def naïve():\n    pass\n".encode(),
        "bom.py": codecs.BOM_UTF8 + b"def bom():\n    pass\n",
        "latin.py": b"# coding: latin-1\n" + "def café():\n    pass\n".encode("latin-1"),
        "crlf.py": b"import sys\r\n\r\ndef windows():\r\n    pass\r\n",
        "empty.py": b"",
        "comment.py": b"# comments only\n",
        "syntax.py": b"def broken(:\n   pass\n",
        "invalid.py": b"\xff\n",
        "unknown.py": b"# coding: not-a-codec\n",
        "conflict.py": codecs.BOM_UTF8 + b"# coding: latin-1\n",
    }
    for name, raw in raw_files.items():
        (repo / name).write_bytes(raw)
    result = analyze_python_repository(repo)
    files = {file.path: file for file in result.files}
    assert not result.complete
    assert result.counts.model_dump() == {
        "total_python_files": 10,
        "analyzed_files": 6,
        "failed_files": 4,
        "declarations": 4,
        "import_statements": 1,
    }
    for name, raw in raw_files.items():
        assert files[name].sha256 == hashlib.sha256(raw).hexdigest()
        assert files[name].size_bytes == len(raw)
    assert files["empty.py"].line_count == 0 and files["comment.py"].line_count == 1
    assert files["latin.py"].symbols[0].name == "café" and files["latin.py"].line_count == 3
    assert files["crlf.py"].line_count == 4 and files["crlf.py"].symbols[0].definition_line == 3
    assert files["bom.py"].symbols[0].start_line == 1
    assert files["syntax.py"].diagnostics[0].model_dump() == {
        "code": "syntax_error",
        "message": "Invalid Python syntax.",
        "source_line": 1,
    }
    for name in ["invalid.py", "unknown.py", "conflict.py"]:
        assert (
            files[name].line_count is None and files[name].diagnostics[0].code == "encoding_error"
        )
    for file in files.values():
        if file.status == "error":
            assert not file.symbols and not file.imports and len(file.diagnostics) == 1
    command = [sys.executable, "-m", "repotrail", "symbols", str(repo), "--json"]
    first = subprocess.run(command, capture_output=True, timeout=30)
    second = subprocess.run(command, capture_output=True, timeout=30)
    assert first.returncode == second.returncode == 1
    assert first.stdout == second.stdout and first.stderr == second.stderr == b""
    assert PythonSourceMap.model_validate_json(first.stdout) == result
    assert b"Traceback" not in first.stdout and str(repo).encode() not in first.stdout


def test_working_tree_hash_changes_with_same_head_and_no_target_writes(repo):
    path = write_file(repo, "app.py", "def before():\n    return 1\n")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "fixture base")
    original = analyze_python_repository(repo)
    changed_bytes = b"def after():\n    return 22\n"
    path.write_bytes(changed_bytes)
    status = git_command(repo, "status", "--porcelain=v1", "-z")
    before = {file: file.read_bytes() for file in repo.rglob("*") if file.is_file()}
    command = [sys.executable, "-m", "repotrail", "symbols", str(repo), "--json"]
    first = subprocess.run(command, capture_output=True, timeout=30, check=True)
    second = subprocess.run(command, capture_output=True, timeout=30, check=True)
    assert first.stdout == second.stdout and first.stderr == second.stderr == b""
    current = PythonSourceMap.model_validate_json(first.stdout)
    assert current.git.head_commit == original.git.head_commit
    assert current.files[0].sha256 != original.files[0].sha256
    assert current.files[0].sha256 == hashlib.sha256(changed_bytes).hexdigest()
    assert current.files[0].symbols[0].name == "after" and current.files[0].tracked
    assert git_command(repo, "status", "--porcelain=v1", "-z") == status
    assert {file: file.read_bytes() for file in repo.rglob("*") if file.is_file()} == before


def test_file_size_exact_boundary(repo):
    (repo / "a.py").write_bytes(b"x=1\n")
    result = analyze_python_repository(
        repo, limits=AnalysisLimits(max_file_bytes=4, max_total_bytes=4)
    )
    assert result.complete and result.files[0].size_bytes == 4


def test_oversize_is_not_opened_or_parsed(repo, monkeypatch):
    (repo / "a.py").write_bytes(b"x=1\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("Oversized content must not be read or parsed")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(analysis, "parse_python_source", forbidden)
    result = analyze_python_repository(repo, limits=AnalysisLimits(max_file_bytes=3))
    file = result.files[0]
    assert not result.complete and file.status == "error" and file.size_bytes == 4
    assert file.sha256 is None and file.line_count is None
    assert file.diagnostics[0].code == "file_too_large"


def test_python_file_count_boundary_and_fatal_cli(repo, monkeypatch, capsys):
    write_file(repo, "a.py", "x=1\n")
    limits = AnalysisLimits(max_python_files=1)
    assert analyze_python_repository(repo, limits=limits).complete
    write_file(repo, "b.py", "y=2\n")
    monkeypatch.setattr(analysis, "DEFAULT_LIMITS", limits)

    def forbidden(*args, **kwargs):
        raise AssertionError("Count overflow must fail before reads")

    monkeypatch.setattr(analysis, "read_source", forbidden)
    assert main(["symbols", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Python-file count limit" in output.err


def test_aggregate_bytes_include_syntax_failures_and_fail_without_json(repo, monkeypatch, capsys):
    (repo / "a.py").write_bytes(b"x=1\n")
    (repo / "b.py").write_bytes(b"bad(\n")
    result = analyze_python_repository(repo, limits=AnalysisLimits(max_total_bytes=9))
    assert not result.complete and result.counts.failed_files == 1
    monkeypatch.setattr(analysis, "DEFAULT_LIMITS", AnalysisLimits(max_total_bytes=8))
    assert main(["symbols", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "aggregate source-byte limit" in output.err


def test_failed_partial_reads_consume_actual_budget(repo, monkeypatch, capsys):
    (repo / "a.py").write_bytes(b"x=1\n")
    (repo / "b.py").write_bytes(b"y=2\n")
    real_open = Path.open

    class FailingRead:
        def __init__(self, stream):
            self.stream = stream
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            self.calls += 1
            if self.calls > 1:
                raise PermissionError("injected failure; no absolute path/source in diagnostic")
            return self.stream.read(size)

    def failing_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        return FailingRead(stream) if path.name == "a.py" else stream

    monkeypatch.setattr(Path, "open", failing_open)
    monkeypatch.setattr(analysis, "READ_CHUNK_BYTES", 2)
    result = analyze_python_repository(repo, limits=AnalysisLimits(max_total_bytes=6))
    assert result.files[0].diagnostics[0].code == "read_error"
    assert result.files[0].sha256 is None and result.files[1].status == "analyzed"
    monkeypatch.setattr(analysis, "DEFAULT_LIMITS", AnalysisLimits(max_total_bytes=5))
    assert main(["symbols", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "aggregate source-byte limit" in output.err


def test_ast_node_boundary_and_recursion_diagnostic(repo, monkeypatch):
    write_file(repo, "a.py", "x=1\n")  # Module, Assign, Name, Store, Constant: five visits.
    assert analyze_python_repository(repo, limits=AnalysisLimits(max_ast_nodes=5)).complete
    result = analyze_python_repository(repo, limits=AnalysisLimits(max_ast_nodes=4))
    assert result.files[0].diagnostics[0].code == "parser_limit"
    assert result.files[0].sha256 is not None and result.files[0].line_count == 1

    def recursion(*args, **kwargs):
        raise RecursionError("private parser details must not be printed")

    monkeypatch.setattr(analysis, "parse_python_source", recursion)
    assert analyze_python_repository(repo).files[0].diagnostics[0].code == "parser_limit"


def test_read_error_is_partial_json_with_exit_one(repo, monkeypatch, capsys):
    write_file(repo, "a.py", "x=1\n")

    def denied(*args, **kwargs):
        raise PermissionError("private absolute path must not be exposed")

    monkeypatch.setattr(Path, "open", denied)
    assert main(["symbols", str(repo), "--json"]) == 1
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == "" and not payload["complete"]
    assert payload["files"][0]["diagnostics"] == [
        {
            "code": "read_error",
            "message": "Unable to read complete source bytes.",
            "source_line": None,
        }
    ]


@pytest.mark.parametrize("change", ["source", "membership", "index", "head"])
def test_observed_changes_are_fatal_with_no_json(repo, monkeypatch, capsys, change):
    write_file(repo, "base.txt")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "fixture base")
    write_file(repo, "a.py", "def before():\n    pass\n")
    real_parse = analysis.parse_python_source

    def parse_then_change(*args):
        parsed = real_parse(*args)
        if change == "source":
            write_file(repo, "a.py", "def after():\n    return 22\n")
        elif change == "membership":
            write_file(repo, "new.py", "x=2\n")
        elif change == "index":
            git_command(repo, "add", "a.py")
        else:
            git_command(repo, "commit", "--allow-empty", "-m", "fixture next")
        return parsed

    monkeypatch.setattr(analysis, "parse_python_source", parse_then_change)
    assert main(["symbols", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "changed" in output.err and "retry" in output.err


def test_disappearance_before_open_is_fatal(repo, monkeypatch, capsys):
    path = write_file(repo, "a.py", "x=1\n")
    real_read = analysis.read_source

    def remove_before_open(*args):
        path.unlink()
        return real_read(*args)

    monkeypatch.setattr(analysis, "read_source", remove_before_open)
    assert main(["symbols", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "eligibility or metadata changed" in output.err


def test_opened_descriptor_mismatch_is_fatal(repo, monkeypatch, capsys):
    write_file(repo, "a.py", "x=1\n")
    monkeypatch.setattr(analysis, "descriptor_signature", lambda stream: (0,) * 6)
    assert main(["symbols", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Opened source differs" in output.err


def test_link_and_reparse_exclusions_prevent_source_opens(repo, monkeypatch):
    write_file(repo, "link.py", "raise RuntimeError('must never read')\n")
    object_id = git_command(repo, "hash-object", "-w", "link.py").decode().strip()
    git_command(repo, "update-index", "--add", "--cacheinfo", f"120000,{object_id},link.py")
    write_file(repo, "junction/hidden.py", "raise RuntimeError('must never read')\n")
    real_lstat = Path.lstat

    def reparse_parent(path, *args, **kwargs):
        if path == repo / "junction":
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return real_lstat(path, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("Excluded link/reparse contents must not be opened")

    monkeypatch.setattr(Path, "lstat", reparse_parent)
    monkeypatch.setattr(Path, "open", forbidden)
    assert analyze_python_repository(repo).files == []


def test_empty_map_is_complete_and_inspect_contract_is_unchanged(repo, capsys):
    write_file(repo, "only.pyi", "not parsed!!!\n")
    assert main(["symbols", str(repo), "--json"]) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert output.err == "" and payload["complete"] and payload["files"] == []
    assert payload["counts"] == {
        "total_python_files": 0,
        "analyzed_files": 0,
        "failed_files": 0,
        "declarations": 0,
        "import_statements": 0,
    }
    assert main(["inspect", str(repo), "--json"]) == 0
    old = json.loads(capsys.readouterr().out)
    assert set(old) == {"schema_version", "project_name", "git", "files", "counts"}
    assert old["schema_version"] == "1" and old["files"] == [
        {"path": "only.pyi", "extension": ".pyi", "tracked": False}
    ]


def test_repository_and_git_failures_stay_fatal(repo, tmp_path, monkeypatch, capsys):
    for invalid in [tmp_path / "missing", tmp_path]:
        assert main(["symbols", str(invalid), "--json"]) == 2
        output = capsys.readouterr()
        assert output.out == "" and output.err.startswith("repotrail:")
    child = repo / "child"
    child.mkdir()
    assert main(["symbols", str(child), "--json"]) == 2
    assert "working-tree root" in capsys.readouterr().err

    def git_failure(*args):
        raise InventoryError("Git command timed out after 10s.")

    monkeypatch.setattr(analysis, "inspect_repository", git_failure)
    assert main(["symbols", str(repo), "--json"]) == 2
    output = capsys.readouterr()
    assert output.out == "" and "Git command timed out" in output.err


def test_unexpected_parser_exception_is_not_success(repo, monkeypatch):
    write_file(repo, "a.py", "x=1\n")

    def programming_error(*args):
        raise RuntimeError("unexpected programming failure")

    monkeypatch.setattr(analysis, "parse_python_source", programming_error)
    with pytest.raises(RuntimeError, match="unexpected programming failure"):
        analyze_python_repository(repo)


@pytest.mark.parametrize(
    "raw,expected", [(b"", 0), (b"# a\r\n# b\r", 2), ("# a\u2028b\n".encode(), 1)]
)
def test_physical_line_count_uses_python_newlines(raw, expected):
    assert analysis.decode_source(raw)[1] == expected


@pytest.mark.parametrize(
    "mutation", ["counts", "complete", "paths", "ids", "range", "status", "kind", "imports"]
)
def test_source_map_contract_rejects_incoherent_evidence(repo, mutation):
    write_file(repo, "a.py", "import os\n\nclass C:\n    def m(self):\n        pass\n")
    payload = analyze_python_repository(repo).model_dump(mode="json")
    if mutation == "counts":
        payload["counts"]["declarations"] += 1
    elif mutation == "complete":
        payload["complete"] = False
    elif mutation == "paths":
        payload["files"].append(payload["files"][0])
    elif mutation == "ids":
        payload["files"][0]["symbols"][0]["id"] = "fabricated"
    elif mutation == "range":
        payload["files"][0]["symbols"][0]["end_line"] = 99
    elif mutation == "status":
        payload["files"][0]["status"] = "error"
    elif mutation == "kind":
        payload["files"][0]["symbols"][1]["kind"] = "function"
    else:
        payload["files"][0]["imports"][0]["level"] = 1
    with pytest.raises(ValidationError):
        PythonSourceMap.model_validate(payload)
