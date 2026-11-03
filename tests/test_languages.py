"""Static mixed-language evidence; fixtures are owned data and are never executed."""

import asyncio
import hashlib
import json
import os
import sys

import pytest
from conftest import git_command, write_file
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from pydantic import ValidationError

from repotrail.agent_evidence import get_commit_evidence, get_history_page, get_revision_source
from repotrail.ai_models import GenerationConfig
from repotrail.analysis import analyze_python_repository
from repotrail.cli import main
from repotrail.evidence import get_project_overview, get_symbol_context
from repotrail.git import InventoryError
from repotrail.history import compare_repository_revisions, compare_revisions
from repotrail.language_models import RepositoryLimits, RepositorySourceMap
from repotrail.language_parser import parse_source
from repotrail.multilang import analyze_repository, get_repository_overview
from repotrail.python_parser import ParserLimitError
from repotrail.selection import collect_evidence


@pytest.mark.parametrize(
    "path,source,names,modules",
    [
        (
            "app.js",
            "import {readFile as read} from 'node:fs';\nexport async function run() {}\n"
            "export const add = (a,b) => a+b;\nclass Box { constructor() {} get value() {return 1;} }\n",
            ["run", "add", "Box", "Box.constructor", "Box.value"],
            ["node:fs"],
        ),
        (
            "app.jsx",
            "import React from 'react';\nexport const App = () => <div>Hi</div>;\n",
            ["App"],
            ["react"],
        ),
        (
            "app.cjs",
            "const os = require('node:os');\nfunction f() { return import('./part.mjs'); }\n",
            ["f"],
            ["node:os", "./part.mjs"],
        ),
        (
            "app.mjs",
            "export {x} from './x.js';\nexport default function() { return 1; }\n",
            ["default"],
            ["./x.js"],
        ),
        (
            "Main.java",
            "package shop;\nimport java.util.List;\nimport static java.lang.Math.*;\n"
            "public class Main { Main() {} public int price() {return 1;} class Inner {} }\n",
            ["Main", "Main.Main", "Main.price", "Main.Inner"],
            ["shop", "java.util.List", "static java.lang.Math.*"],
        ),
        (
            "Types.java",
            "interface I { void run(); }\nenum E { A; void go() {} }\n"
            "record Point(int x) { Point { } int value() {return x;} }\n"
            "@interface Label { String value(); }\n",
            [
                "I",
                "I.run",
                "E",
                "E.go",
                "Point",
                "Point.Point",
                "Point.value",
                "Label",
                "Label.value",
            ],
            [],
        ),
        (
            "a.js",
            "// function fake() {}\nconst text='class Fake {}';\nconst pattern=/function fake/;\n"
            "const f = async () => { function inner() {} };\n",
            ["f", "f.inner"],
            [],
        ),
        (
            "a.java",
            'class A { String x="void fake() {}"; /* class Nope {} */ void real() {} }',
            ["A", "A.real"],
            [],
        ),
        (
            "a.js",
            "const C = class { method() {} };\nclass Outer { field = () => 1; #private() {} }",
            ["C", "C.method", "Outer", "Outer.field", "Outer.#private"],
            [],
        ),
    ],
)
def test_static_declarations_imports_and_ranges(path, source, names, modules):
    symbols, imports = parse_source(source, path)
    assert [symbol.qualified_name for symbol in symbols] == names
    assert [item.module for item in imports] == modules
    assert all(
        1
        <= symbol.start_line
        <= symbol.definition_line
        <= symbol.end_line
        <= len(source.splitlines())
        for symbol in symbols
    )
    assert len({symbol.id for symbol in symbols}) == len(symbols)


@pytest.mark.parametrize(
    "path,source", [("a.js", "function broken( {"), ("A.java", "class A { void bad( {}")]
)
def test_invalid_source_and_node_limits(path, source):
    with pytest.raises(SyntaxError):
        parse_source(source, path)
    with pytest.raises(ParserLimitError):
        parse_source("class A {}", path, 1)


def test_time_cancellation_and_unsupported_extension(monkeypatch):
    monkeypatch.setattr("repotrail.language_parser.PARSER_SECONDS", 0)
    with pytest.raises(ParserLimitError):
        parse_source("const a=1;", "a.js")
    with pytest.raises(ValueError):
        parse_source("interface A {}", "a.ts")


def test_unmodeled_object_computed_and_anonymous_scopes_are_not_misattributed():
    symbols, _ = parse_source("class C { field = { wrong() {} }; [key]() {} right() {} }", "a.js")
    assert [s.qualified_name for s in symbols] == ["C", "C.right"]
    symbols, _ = parse_source(
        "class C { Object x = new Object() { void wrong() {} }; void right() {} }", "C.java"
    )
    assert [s.qualified_name for s in symbols] == ["C", "C.right"]


@pytest.fixture
def mixed(repo):
    write_file(repo, "README.md", "# Mixed shop\nDo not execute inspected code.\n")
    write_file(repo, "price.py", "def total(x):\n    return x * .9\n")
    write_file(repo, "price.js", "export function total(x) {\n  return x * .9;\n}\n")
    write_file(
        repo,
        "Price.java",
        "class Price {\n  static double total(double x) {\n    return x * .9;\n  }\n}\n",
    )
    write_file(repo, "View.jsx", "export const View = () => <div>Shop</div>;\n")
    write_file(repo, "package.json", '{"scripts":{"postinstall":"touch EXECUTED"}}\n')
    write_file(repo, "pom.xml", '<!DOCTYPE x SYSTEM "file:///NEVER"><project/>\n')
    write_file(repo, "build.gradle", 'throw new RuntimeException("must never run")\n')
    write_file(repo, "node_modules/no.js", "function excluded() {}")
    write_file(repo, "target/Gone.java", "class Excluded {}")
    write_file(repo, ".env.secret.js", "function credential() {}")
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "mixed language base")
    base = git_command(repo, "rev-parse", "HEAD").decode().strip()
    for name in ("price.py", "price.js", "Price.java"):
        write_file(repo, name, (repo / name).read_text().replace("x * .9", "x - .1"))
    git_command(repo, "add", ".")
    git_command(repo, "commit", "-m", "deliberate discount regression")
    head = git_command(repo, "rev-parse", "HEAD").decode().strip()
    write_file(repo, "price.js", "export function dirty() {}\n")
    return repo, base, head


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


def test_mixed_map_overview_schemas_and_no_writes(mixed):
    root, _, _ = mixed
    before = snapshot(root)
    document = analyze_repository(root)
    assert document.complete and document.schema_version == "2"
    assert document.counts.by_language == {"python": 1, "javascript": 2, "java": 1}
    assert RepositorySourceMap.model_validate_json(document.model_dump_json()) == document
    old = analyze_python_repository(root)
    assert old.schema_version == "1" and old.document_type == "python_source_map"
    assert [f.path for f in old.files] == ["price.py"]
    assert get_project_overview(root).schema_version == "1"
    overview = get_repository_overview(root)
    assert {m.format for m in overview.manifests} == {"package.json", "pom.xml", "build.gradle"}
    assert all(m.interpretation == "source_only" for m in overview.manifests)
    assert snapshot(root) == before and not (root / "EXECUTED").exists()


def test_java_overload_ambiguity_columns_unicode_crlf(repo):
    source = "class Cafe { int f() {return 1;} int f(int x) {return x;} }\r\n"
    write_file(repo, "Café.java", source)
    mapping = analyze_repository(repo)
    symbols = mapping.files[0].symbols
    overloads = [s for s in symbols if s.qualified_name == "Cafe.f"]
    assert len(overloads) == 2 and overloads[0].id != overloads[1].id
    assert get_symbol_context(repo, "Café.java", "Cafe.f", 1).status == "ambiguous"
    context = get_symbol_context(
        repo, "Café.java", "Cafe.f", 1, definition_column=overloads[1].definition_column
    )
    assert context.complete and context.excerpt.reference.start_line == 1


def test_aggregate_bounds_errors_and_stale_context(repo):
    write_file(repo, "a.js", "function f() {}\n")
    write_file(repo, "A.java", "class A {}\n")
    with pytest.raises(InventoryError, match="combined source-file"):
        analyze_repository(repo, limits=RepositoryLimits(max_source_files=1))
    with pytest.raises(InventoryError, match="aggregate source-byte"):
        analyze_repository(repo, limits=RepositoryLimits(max_total_bytes=5))
    result = analyze_repository(repo, limits=RepositoryLimits(max_file_bytes=5))
    assert not result.complete and result.counts.failed_files == 2
    assert all(f.diagnostics[0].code == "file_too_large" for f in result.files)
    write_file(repo, "bad.js", "function ( {")
    (repo / "bad.java").write_bytes(b"\xff")
    bad = {f.path: f for f in analyze_repository(repo).files}
    assert bad["bad.js"].diagnostics[0].code == "syntax_error"
    assert bad["bad.java"].diagnostics[0].code == "encoding_error"
    mapping = analyze_repository(repo)
    write_file(repo, "a.js", "function g() {}\n")
    with pytest.raises(InventoryError, match="hash is stale"):
        get_symbol_context(repo, "a.js", "f", source_map=mapping)
    invalid = mapping.model_dump()
    invalid["counts"]["by_language"]["java"] = 100
    with pytest.raises(ValidationError):
        RepositorySourceMap.model_validate(invalid)


def test_history_skill_and_optional_ai_evidence_all_languages(mixed):
    root, base, head = mixed
    before = snapshot(root)
    comparison = compare_repository_revisions(root, base, head)
    assert comparison.schema_version == "2" and comparison.complete
    by_path = {f.new_path: f for f in comparison.files}
    for path, name, language in [
        ("price.py", "total", "python"),
        ("price.js", "total", "javascript"),
        ("Price.java", "Price.total", "java"),
    ]:
        changed = by_path[path]
        assert name in [s.qualified_name for s in changed.new_symbols]
        assert all(s.language == language for s in changed.old_symbols)
        assert changed.hunks[0].old_excerpt.reference.commit_oid == base
        assert changed.hunks[0].new_excerpt.reference.commit_oid == head
    assert compare_revisions(root, base, head).schema_version == "1"
    packet = get_commit_evidence(root, head)
    assert packet.metadata["source_file_counts"] == {"python": 1, "javascript": 2, "java": 1}
    assert "dirty" not in packet.model_dump_json()
    assert {"price.py", "price.js", "Price.java"} <= {s.reference.path for s in packet.sources}
    source = get_revision_source(root, base, "Price.java")
    assert source.complete and "x * .9" in source.model_dump_json()
    assert get_history_page(root).metadata["returned"] == 2
    package = collect_evidence(root, "explain_project", GenerationConfig())
    assert package.metadata["source_counts"]["by_language"]["java"] == 1
    review = collect_evidence(root, "review_diff", GenerationConfig(), base, head)
    assert {"price.py", "price.js", "Price.java"} <= {s.reference.path for s in review.sources}
    assert snapshot(root) == before


def test_cli_default_multilanguage_and_explicit_legacy(mixed, capsys):
    root, base, head = mixed
    for command in ("symbols", "overview", "changes"):
        args = [command, str(root)] + ([base, head] if command == "changes" else [])
        assert main([*args, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["schema_version"] == "2"
        assert main([*args, "--python-only", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["schema_version"] == "1"
    assert main(["context", str(root), "Price.java", "Price.total", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "found"


def test_real_stdio_mcp_multilanguage_no_credentials(mixed):
    root, base, head = mixed
    before = snapshot(root)

    async def run():
        environment = {k: v for k, v in os.environ.items() if not k.upper().endswith("_API_KEY")}
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "repotrail", "mcp", "--repo", str(root)],
            env=environment,
        )
        async with Client(parameters, read_timeout_seconds=30) as client:
            tools = (await client.list_tools()).tools
            assert len(tools) == 3 and all(t.annotations.read_only_hint for t in tools)
            result = await client.call_tool("get_project_overview", {})
            assert (
                result.structured_content["data"]["source_map"]["counts"]["by_language"]["java"]
                == 1
            )
            for path, name in [("View.jsx", "View"), ("Price.java", "Price.total")]:
                result = await client.call_tool(
                    "get_symbol_context", {"path": path, "qualified_name": name}
                )
                assert result.structured_content["data"]["status"] == "found"
            result = await client.call_tool(
                "summarize_changes", {"base_ref": base, "head_ref": head}
            )
            assert result.structured_content["data"]["schema_version"] == "2"
            assert any(
                s["language"] == "java"
                for f in result.structured_content["data"]["files"]
                for s in f["new_symbols"]
            )

    asyncio.run(run())
    assert snapshot(root) == before
