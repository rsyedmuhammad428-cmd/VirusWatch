"""Static contract between app/surveillance/queries.py and the routers that execute it.

The routers need FastAPI and SQLAlchemy, which the pure test environment lacks, so this reads their
source with `ast`: every `fetch(conn, queries.X, params)` must pass exactly the bind parameters
statement X declares (none missing, none extra), and every public statement must be used.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.surveillance import queries

ROUTERS = [Path("app/api/v1/surveillance.py"), Path("app/api/v1/sources.py")]
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def statements() -> dict[str, str]:
    return {
        n: v
        for n, v in vars(queries).items()
        if n.isupper() and not n.startswith("_") and isinstance(v, str)
    }


def _function_defs(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _keys(expr: ast.expr, scope: ast.AST, functions: dict) -> set[str]:
    if isinstance(expr, ast.Dict):
        keys: set[str] = set()
        for k, v in zip(expr.keys, expr.values, strict=True):
            keys |= _keys(v, scope, functions) if k is None else {k.value}
        return keys
    if isinstance(expr, ast.Name):  # a local variable assigned once in the same function
        assigned = [
            n.value
            for n in ast.walk(scope)
            if isinstance(n, ast.Assign)
            and any(getattr(t, "id", None) == expr.id for t in n.targets)
        ]
        assert len(assigned) == 1, f"cannot resolve variable {expr.id!r}"
        return _keys(assigned[0], scope, functions)
    if isinstance(expr, ast.Call) and getattr(expr.func, "id", "") in functions:
        helper = functions[expr.func.id]
        returns = [n.value for n in ast.walk(helper) if isinstance(n, ast.Return)]
        assert len(returns) == 1, f"{helper.name} must have a single return"
        return _keys(returns[0], helper, functions)
    raise AssertionError(f"unsupported parameter expression: {ast.dump(expr)[:80]}")


def router_calls() -> list[tuple[str, set[str], str]]:
    """(statement name, parameter keys, location) for every fetch(...) in the routers."""
    calls = []
    for path in ROUTERS:
        tree = ast.parse(path.read_text())
        functions = _function_defs(tree)
        for fn in functions.values():
            for node in ast.walk(fn):
                is_fetch = isinstance(node, ast.Call) and getattr(node.func, "id", "") == "fetch"
                if not is_fetch:
                    continue
                ref = node.args[1]
                assert isinstance(ref, ast.Attribute) and ref.value.id == "queries", (
                    f"{path}:{node.lineno} must pass queries.<NAME> to fetch()"
                )
                calls.append(
                    (ref.attr, _keys(node.args[2], fn, functions), f"{path}:{node.lineno}")
                )
    return calls


def test_the_scan_finds_the_routers_calls():
    assert len(router_calls()) >= 10


@pytest.mark.parametrize(("name", "keys", "where"), router_calls())
def test_each_call_supplies_exactly_the_declared_parameters(name, keys, where):
    declared = set(_PARAM.findall(statements()[name]))
    assert keys == declared, f"{where}: {name}"


def test_every_public_statement_is_used_by_a_router():
    assert {name for name, _, _ in router_calls()} == set(statements())


def test_no_bind_lookalikes_inside_literals_and_no_interpolation():
    for name, statement in statements().items():
        for literal in re.findall(r"'([^']*)'", statement):
            assert not _PARAM.findall(literal), f"{name}: {literal!r}"
        assert "%s" not in statement and "{" not in statement.replace("'{}'", ""), name
