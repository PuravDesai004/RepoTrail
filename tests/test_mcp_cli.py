"""Official Python client over real stdio; no Node, key or paid calls."""

import asyncio
import json
import os
import subprocess
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from repotrail.cli import main
from repotrail.demo import build_demo


def test_official_stdio_client_tools_containment_and_errors(tmp_path):
    root = tmp_path / "repo"
    base, head = build_demo(root)

    async def exercise():
        environment = dict(os.environ)
        environment.pop("GEMINI_API_KEY", None)
        environment.pop("GOOGLE_API_KEY", None)
        environment["LANGSMITH_TRACING"] = "false"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "repotrail", "mcp", "--repo", str(root)],
            env=environment,
        )
        async with Client(parameters, read_timeout_seconds=30) as client:
            tools = (await client.list_tools()).tools
            assert {tool.name for tool in tools} == {
                "get_project_overview",
                "get_symbol_context",
                "summarize_changes",
            }
            assert all(
                tool.annotations.read_only_hint and tool.annotations.idempotent_hint
                for tool in tools
            )
            overview = await client.call_tool("get_project_overview", {})
            assert not overview.is_error and overview.structured_content["ok"]
            assert overview.structured_content["data"]["packaging"]["name"] == "tiny-shop"
            context = await client.call_tool(
                "get_symbol_context", {"path": "pricing.py", "qualified_name": "total_price"}
            )
            assert context.structured_content["data"]["status"] == "found"
            changes = await client.call_tool(
                "summarize_changes", {"base_ref": base, "head_ref": head}
            )
            assert changes.structured_content["data"]["base_oid"] == base
            for path in ["../outside.py", str(tmp_path / "outside.py"), ".env"]:
                rejected = await client.call_tool(
                    "get_symbol_context", {"path": path, "qualified_name": "x"}
                )
                assert (
                    rejected.is_error
                    or not rejected.structured_content["ok"]
                    or rejected.structured_content["data"]["status"] == "unavailable"
                )
            invalid = await client.call_tool(
                "summarize_changes", {"base_ref": "--help", "head_ref": head}
            )
            assert not invalid.structured_content["ok"]
            disabled = await client.call_tool("explain_project", {})
            assert disabled.is_error
            # Continued protocol success proves no banner/ordinary result polluted stdout.
            assert len((await client.list_tools()).tools) == 3

    asyncio.run(exercise())


def test_enabled_ai_missing_key_and_annotations(tmp_path):
    root = tmp_path / "repo"
    build_demo(root)

    async def exercise():
        environment = dict(os.environ)
        for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            environment.pop(key, None)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "repotrail",
                "mcp",
                "--repo",
                str(root),
                "--enable-ai",
                "--state-dir",
                str(tmp_path / "state"),
            ],
            env=environment,
        )
        async with Client(parameters, read_timeout_seconds=30) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == {
                "get_project_overview",
                "get_symbol_context",
                "summarize_changes",
                "explain_project",
                "review_diff",
            }
            assert not tools["explain_project"].annotations.idempotent_hint
            assert tools["explain_project"].annotations.open_world_hint
            result = await client.call_tool("explain_project", {})
            assert result.structured_content["data"]["status"] == "failed"
            assert result.structured_content["data"]["attempts"] == 0

    asyncio.run(exercise())


def test_cli_new_commands_json_human_and_legacy(tmp_path, capsys, monkeypatch):
    root = tmp_path / "repo"
    assert main(["demo", str(root), "--json"]) == 0
    refs = json.loads(capsys.readouterr().out)
    for command in ["inspect", "symbols", "overview", "history"]:
        assert main([command, str(root), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["schema_version"] == "1"
    assert main(["context", str(root), "pricing.py", "total_price", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "found"
    assert main(["context", str(root), "pricing.py", "missing", "--json"]) == 1
    capsys.readouterr()
    assert (
        main(["changes", str(root), refs["base"], refs["head"], "--json"]) == 1
    )  # binary is explicit incomplete evidence
    assert json.loads(capsys.readouterr().out)["comparison_kind"] == "endpoint_trees"
    assert main(["history", str(root)]) == 0
    assert "resolved_oid:" in capsys.readouterr().out
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert main(["explain", str(root), "--state-dir", str(tmp_path / "state"), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["attempts"] == 0
    assert main(["history", str(root), "--ref=--help", "--json"]) == 2
    assert "repotrail:" in capsys.readouterr().err


def test_core_imports_do_not_import_optional_extras():
    code = "import sys; import repotrail.cli; assert not any(k in sys.modules for k in ('langgraph', 'langchain_google_genai', 'mcp'))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode()
