"""Static contract between app/ingestion/sql.py and app/ingestion/sql_repository.py.

The repository needs SQLAlchemy/asyncpg, which pure unit tests do not have, so this test reads its
source with `ast` and checks that every `conn.execute(text(sql.X), {...})` supplies exactly the
bind parameters statement X declares: no missing (runtime error) and no extra (dead/misspelled).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.ingestion import sql

REPOSITORY = Path(sql.__file__).with_name("sql_repository.py")
# `:name` bind parameters; a preceding ':' (a ::cast) or word character excludes a match.
_PARAM = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")


def sql_constants() -> dict[str, str]:
    return {n: v for n, v in vars(sql).items() if n.isupper() and isinstance(v, str)}


def declared_params(statement: str) -> set[str]:
    return set(_PARAM.findall(statement))


def repository_calls() -> dict[str, list[set[str]]]:
    """constant name -> key sets of every params dict passed alongside it."""
    calls: dict[str, list[set[str]]] = {}
    for node in ast.walk(ast.parse(REPOSITORY.read_text())):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
            continue
        first = node.args[0] if node.args else None
        if not (isinstance(first, ast.Call) and getattr(first.func, "id", "") == "text" and first.args):
            continue
        ref = first.args[0]
        if not (isinstance(ref, ast.Attribute) and getattr(ref.value, "id", "") == "sql"):
            continue
        keys: set[str] = set()
        if len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
            keys = {k.value for k in node.args[1].keys if isinstance(k, ast.Constant)}
        calls.setdefault(ref.attr, []).append(keys)
    return calls


def test_every_statement_is_used_by_the_repository():
    assert set(repository_calls()) == set(sql_constants())


def test_each_call_supplies_exactly_the_declared_parameters():
    constants = sql_constants()
    for name, key_sets in repository_calls().items():
        for keys in key_sets:
            assert keys == declared_params(constants[name]), name


def test_no_bind_lookalikes_inside_sql_string_literals():
    # A literal such as 'a:b' would be mistaken for a bind parameter by SQLAlchemy's text() and by
    # tests/db/psql_harness.py. (A colon followed by a space, as in 'Abandoned: ...', is harmless.)
    for name, statement in sql_constants().items():
        for literal in re.findall(r"'([^']*)'", statement):
            assert not _PARAM.findall(literal), f"{name}: bind-like text in literal {literal!r}"


def test_statements_never_interpolate_values():
    # Parameters must be bound, never formatted into the SQL text.
    for name, statement in sql_constants().items():
        assert "%s" not in statement and "{" not in statement.replace("'{}'", ""), name
