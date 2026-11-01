"""Static contract between each package's sql.py and its sql_repository.py.

The repositories need SQLAlchemy/asyncpg, which pure unit tests do not have, so this reads their
source with `ast` and checks that every `conn.execute(text(sql.X), {...})` supplies exactly the
bind parameters statement X declares: none missing (runtime error), none extra (dead or misspelled).
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

PACKAGES = ["app.ingestion", "app.analysis"]
# `:name` bind parameters; a preceding ':' (a ::cast) or word character excludes a match.
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def sql_module(package: str):
    return importlib.import_module(f"{package}.sql")


def sql_constants(package: str) -> dict[str, str]:
    return {
        n: v for n, v in vars(sql_module(package)).items() if n.isupper() and isinstance(v, str)
    }


def declared_params(statement: str) -> set[str]:
    return set(_PARAM.findall(statement))


def repository_calls(package: str) -> dict[str, list[set[str]]]:
    """constant name -> key sets of every params dict passed alongside it."""
    repository = Path(sql_module(package).__file__).with_name("sql_repository.py")
    calls: dict[str, list[set[str]]] = {}
    for node in ast.walk(ast.parse(repository.read_text())):
        is_execute = (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        )
        if not is_execute:
            continue
        first = node.args[0] if node.args else None
        is_text = (
            isinstance(first, ast.Call) and getattr(first.func, "id", "") == "text" and first.args
        )
        if not is_text:
            continue
        ref = first.args[0]
        if not (isinstance(ref, ast.Attribute) and getattr(ref.value, "id", "") == "sql"):
            continue
        keys: set[str] = set()
        if len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
            keys = {k.value for k in node.args[1].keys if isinstance(k, ast.Constant)}
        calls.setdefault(ref.attr, []).append(keys)
    return calls


@pytest.mark.parametrize("package", PACKAGES)
def test_every_statement_is_used_by_the_repository(package):
    assert set(repository_calls(package)) == set(sql_constants(package))


@pytest.mark.parametrize("package", PACKAGES)
def test_each_call_supplies_exactly_the_declared_parameters(package):
    constants = sql_constants(package)
    for name, key_sets in repository_calls(package).items():
        for keys in key_sets:
            assert keys == declared_params(constants[name]), name


@pytest.mark.parametrize("package", PACKAGES)
def test_no_bind_lookalikes_inside_sql_string_literals(package):
    # A literal such as 'a:b' would be mistaken for a bind parameter by SQLAlchemy's text() and by
    # tests/db/psql_harness.py. (A colon followed by a space, as in 'Abandoned: ...', is harmless.)
    for name, statement in sql_constants(package).items():
        for literal in re.findall(r"'([^']*)'", statement):
            assert not _PARAM.findall(literal), f"{name}: bind-like text in literal {literal!r}"


@pytest.mark.parametrize("package", PACKAGES)
def test_statements_never_interpolate_values(package):
    for name, statement in sql_constants(package).items():
        assert "%s" not in statement and "{" not in statement.replace("'{}'", ""), name
