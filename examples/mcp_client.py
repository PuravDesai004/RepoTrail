"""Run with RepoTrail[mcp] installed: python examples/mcp_client.py <repo>."""

import argparse
import asyncio
import json
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def inspect_repository(root: str) -> None:
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "repotrail", "mcp", "--repo", root]
    )
    async with Client(parameters, read_timeout_seconds=30) as client:
        print("Tools:", [tool.name for tool in (await client.list_tools()).tools])
        result = await client.call_tool("get_project_overview", {})
        print(json.dumps(result.structured_content, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository")
    asyncio.run(inspect_repository(parser.parse_args().repository))
