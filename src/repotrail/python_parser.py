"""Static declarations/imports only; never compile, import, or evaluate input code."""

import ast
import io
import tokenize
import warnings
from bisect import bisect_right
from collections.abc import Iterator
from typing import Literal

from .source_models import Declaration, ImportName, StaticImport, declaration_key, import_key


class ParserLimitError(Exception):
    """The iterative AST walk exceeded its node budget."""


def decorator_lines(source: str) -> list[int]:
    """Find statement-leading @ tokens, including parenthesized multiline decorators."""
    lines: list[int] = []
    statement_start = True
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NEWLINE:
            statement_start = True
        elif token.type not in (
            tokenize.NL,
            tokenize.COMMENT,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENCODING,
            tokenize.ENDMARKER,
        ):
            if statement_start and token.type == tokenize.OP and token.string == "@":
                lines.append(token.start[0])
            statement_start = False
    return lines


def parse_python_source(
    source: str, path: str, max_ast_nodes: int
) -> tuple[list[Declaration], list[StaticImport]]:
    """Iterative depth-first traversal; only class/function definitions introduce scopes."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        warnings.simplefilter("ignore", DeprecationWarning)
        tree = ast.parse(source, filename="<repotrail-source>", mode="exec")
    decorators = decorator_lines(source)
    symbols: list[Declaration] = []
    imports: list[StaticImport] = []
    # Iterator frames avoid recursion and avoid materializing a list of all AST nodes.
    frames: list[tuple[Iterator[ast.AST], tuple[tuple[str, str], ...]]] = [(iter((tree,)), ())]
    visited = 0
    while frames:
        children, scope = frames[-1]
        try:
            node = next(children)
        except StopIteration:
            frames.pop()
            continue
        visited += 1
        if visited > max_ast_nodes:
            raise ParserLimitError("Source exceeds the AST node limit.")
        parent = ".".join(name for name, _kind in scope) or None
        child_scope = scope
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            kind: Literal["class", "function", "method"] = (
                "class"
                if isinstance(node, ast.ClassDef)
                else ("method" if scope and scope[-1][1] == "class" else "function")
            )
            qualified = f"{parent}.{node.name}" if parent else node.name
            start = node.lineno
            if node.decorator_list:
                start = decorators[bisect_right(decorators, node.decorator_list[0].lineno) - 1]
            assert node.end_lineno is not None
            symbols.append(
                Declaration(
                    id=f"{path}::{qualified}:{node.lineno}",
                    name=node.name,
                    qualified_name=qualified,
                    kind=kind,
                    parent_qualified_name=parent,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    definition_line=node.lineno,
                    start_line=start,
                    end_line=node.end_lineno,
                )
            )
            child_scope = (*scope, (node.name, kind))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            assert node.end_lineno is not None
            imports.append(
                StaticImport(
                    kind="from" if isinstance(node, ast.ImportFrom) else "import",
                    module=node.module if isinstance(node, ast.ImportFrom) else None,
                    level=node.level if isinstance(node, ast.ImportFrom) else 0,
                    names=[ImportName(name=name.name, asname=name.asname) for name in node.names],
                    scope=parent,
                    start_line=node.lineno,
                    end_line=node.end_lineno,
                )
            )
        frames.append((ast.iter_child_nodes(node), child_scope))
    return sorted(symbols, key=declaration_key), sorted(imports, key=import_key)
