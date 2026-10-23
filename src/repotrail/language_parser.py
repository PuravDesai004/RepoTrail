"""Bounded static syntax facts. No runtimes, compilers, module loading or build tools."""

import time
from functools import lru_cache
from typing import Literal

import tree_sitter_java
import tree_sitter_javascript
from tree_sitter import Language, Node, Parser

from .analysis import decode_source
from .language_models import (
    LanguageDeclaration,
    LanguageImport,
    LanguageName,
    language_for,
    symbol_key,
)
from .python_parser import ParserLimitError, parse_python_source

PARSER_SECONDS = 1.0


def decode_language(raw: bytes, path: str) -> tuple[str, int]:
    if language_for(path) == "python":
        return decode_source(raw)
    source = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return source, source.count("\n") + int(bool(source) and not source.endswith("\n"))


@lru_cache(maxsize=2)
def grammar(language: LanguageName) -> Language:
    return Language(
        tree_sitter_java.language() if language == "java" else tree_sitter_javascript.language()
    )


def parse_source(
    source: str, path: str, max_nodes: int = 50_000
) -> tuple[list[LanguageDeclaration], list[LanguageImport]]:
    language = language_for(path)
    if language is None:
        raise ValueError("Unsupported source language.")
    if language == "python":
        symbols, imports = parse_python_source(source, path, max_nodes)
        return [
            LanguageDeclaration(
                **symbol.model_dump(exclude={"id"}),
                id=symbol.id + ":1",
                language="python",
                syntax_kind=symbol.kind,
                definition_column=1,
            )
            for symbol in symbols
        ], [
            LanguageImport(
                syntax_kind=item.kind,
                module=("." * item.level) + (item.module or ""),
                names=[
                    name.name + (" as " + name.asname if name.asname else "") for name in item.names
                ],
                scope=item.scope,
                start_line=item.start_line,
                end_line=item.end_line,
            )
            for item in imports
        ]
    raw = source.encode("utf-8")
    deadline = time.monotonic() + PARSER_SECONDS

    def reader(offset: int, _point) -> bytes:
        if time.monotonic() >= deadline:
            raise ParserLimitError("Static parser time limit exceeded.")
        return raw[offset : offset + 4096]

    def cancel(_offset: int, _has_error: bool) -> bool:
        return time.monotonic() >= deadline

    tree = Parser(grammar(language)).parse(reader, progress_callback=cancel)
    if tree is None:
        raise ParserLimitError("Static parser time limit exceeded.")
    # Never expose facts recovered from invalid/truncated syntax as a successful parse.
    if tree.root_node.has_error:
        raise SyntaxError("Invalid or unsupported source syntax.")
    declarations: list[LanguageDeclaration] = []
    statements: list[LanguageImport] = []
    stack: list[tuple[Node, LanguageDeclaration | None]] = [(tree.root_node, None)]
    visited = 0

    def text(node: Node) -> str:
        return raw[node.start_byte : node.end_byte].decode("utf-8")

    while stack:
        node, scope = stack.pop()
        visited += 1
        if visited > max_nodes or time.monotonic() >= deadline:
            raise ParserLimitError("Static parser node/time limit exceeded.")
        name_node = node.child_by_field_name("name")
        span = node
        kind: Literal["class", "function", "method"] | None = None
        syntax = node.type
        name = text(name_node) if name_node else None
        if language == "java":
            if syntax in {
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "annotation_type_declaration",
            }:
                kind = "class"
            elif syntax in {
                "method_declaration",
                "constructor_declaration",
                "compact_constructor_declaration",
                "annotation_type_element_declaration",
            }:
                kind = "method" if scope and scope.kind == "class" else "function"
            if syntax in {"import_declaration", "package_declaration"}:
                value = text(node).removeprefix("import").removeprefix("package").strip()
                statements.append(
                    LanguageImport(
                        syntax_kind=syntax,
                        module=value.removesuffix(";").strip(),
                        start_line=node.start_point.row + 1,
                        end_line=node.end_point.row + 1,
                    )
                )
            # Anonymous classes have no stable qualified name; don't misattribute members.
            if (
                syntax == "class_body"
                and node.parent
                and node.parent.type
                in {
                    "object_creation_expression",
                    "enum_constant",
                }
            ):
                continue
        else:
            if syntax in {"class_declaration", "class"}:
                kind = "class"
            elif syntax in {
                "function_declaration",
                "generator_function_declaration",
                "function_expression",
                "generator_function",
                "arrow_function",
            }:
                kind = "function"
            elif syntax == "method_definition":
                # An object literal is not its enclosing class. Do not invent C.method
                # for `class C { field = { method() {} }; }` or a computed key.
                if (
                    node.parent is None
                    or node.parent.type != "class_body"
                    or name_node is None
                    or name_node.type == "computed_property_name"
                ):
                    continue
                kind = "method" if scope and scope.kind == "class" else "function"
            if (
                kind is not None
                and node.parent
                and node.parent.type
                in {
                    "variable_declarator",
                    "field_definition",
                    "pair",
                }
            ):
                parent = node.parent
                binding = (
                    parent.child_by_field_name("name")
                    or parent.child_by_field_name("key")
                    or parent.child_by_field_name("property")
                )
                if binding and binding.type in {
                    "identifier",
                    "property_identifier",
                    "private_property_identifier",
                }:
                    name_node, name, span = binding, text(binding), parent
                    if parent.type == "field_definition" and kind == "function" and scope:
                        kind = "method" if scope.kind == "class" else "function"
            if kind and not name and node.parent and node.parent.type == "export_statement":
                if any(child.type == "default" for child in node.parent.children):
                    name = "default"
            if syntax in {"import_statement", "export_statement"}:
                module = node.child_by_field_name("source")
                if module:
                    statements.append(
                        LanguageImport(
                            syntax_kind=syntax,
                            module=text(module)[1:-1],
                            scope=scope.qualified_name if scope else None,
                            start_line=node.start_point.row + 1,
                            end_line=node.end_point.row + 1,
                        )
                    )
            elif syntax == "call_expression":
                callee = node.child_by_field_name("function")
                arguments = node.child_by_field_name("arguments")
                if callee and text(callee) in {"require", "import"} and arguments:
                    args = arguments.named_children
                    if len(args) == 1 and args[0].type == "string":
                        statements.append(
                            LanguageImport(
                                syntax_kind="literal_" + text(callee),
                                module=text(args[0])[1:-1],
                                scope=scope.qualified_name if scope else None,
                                start_line=node.start_point.row + 1,
                                end_line=node.end_point.row + 1,
                            )
                        )
        next_scope = scope
        if kind is not None and name:
            parent_name = scope.qualified_name if scope else None
            qualified = f"{parent_name}.{name}" if parent_name else name
            definition = name_node or node
            line, column = definition.start_point.row + 1, definition.start_point.column + 1
            # Include export/decorator context without claiming runtime export resolution.
            if span.parent and span.parent.type == "export_statement":
                span = span.parent
            symbol = LanguageDeclaration(
                id=f"{path}::{qualified}:{line}:{column}",
                name=name,
                qualified_name=qualified,
                kind=kind,
                parent_qualified_name=parent_name,
                language=language,
                syntax_kind=syntax,
                definition_column=column,
                is_async=kind != "class" and any(child.type == "async" for child in node.children),
                definition_line=line,
                start_line=span.start_point.row + 1,
                end_line=span.end_point.row + 1 - int(span.end_point.column == 0),
            )
            declarations.append(symbol)
            next_scope = symbol
        elif kind is not None:
            # Don't assign declarations inside unnamed callbacks to a misleading outer scope.
            continue
        stack.extend((child, next_scope) for child in reversed(node.named_children))
    declarations.sort(key=symbol_key)
    statements.sort(
        key=lambda item: (item.start_line, item.end_line, item.syntax_kind, item.module)
    )
    return declarations, statements
