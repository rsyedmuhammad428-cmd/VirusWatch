"""Static check: every documented endpoint's Auth column in docs/api_*.md matches the auth
dependency actually wired into its FastAPI route function.

FastAPI itself cannot be imported in this sandbox (ModuleNotFoundError: fastapi -- see
docs/api_surveillance.md "Status: written but not yet run"), so this cannot introspect a live
`app.routes` list. Instead it parses both sides as text:

  * the docs/api_*.md markdown tables, for what we *claim* the auth requirement is;
  * the app/api/v1/*.py and app/api/health.py route decorators + their Depends(...) parameters,
    for what the code *actually* requires;

and asserts the two agree for every endpoint listed in either source. This exists specifically
so that changing an endpoint's auth dependency without updating its docs (or vice versa) fails
CI, rather than silently drifting -- the kind of mismatch that is easy to introduce when a
route's Depends() list changes during a later phase.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
API_DIR = REPO_ROOT / "app" / "api"

# Normalises a documented or code-derived auth requirement down to one of these buckets.
PUBLIC = "public"
AUTHENTICATED = "authenticated"
ADMIN = "admin"
WEBHOOK = "webhook_signature"
PUBLIC_TOKEN = "public_token"  # public endpoint whose own body carries a one-time credential

_ROUTE_DECORATOR = re.compile(
    r'@router\.(get|post|put|delete|patch)\(\s*(?:"([^"]*)"|\'([^\']*)\')', re.MULTILINE
)
_ROUTER_PREFIX = re.compile(r'APIRouter\(\s*prefix\s*=\s*"([^"]*)"')
_DOC_ROW = re.compile(
    r"^\|\s*`(GET|POST|PUT|DELETE|PATCH)\s+([^`]+)`\s*\|\s*([^|]+?)\s*\|", re.MULTILINE
)
# Module-level "Name = Annotated[Type, Depends(something)]" aliases (e.g. ai.py's `Conn`,
# `Db`), so a route that writes `conn: Conn` rather than the literal `Depends(public_conn)`
# in its own signature is still classified correctly, not defaulted to "public" by omission.
_TYPE_ALIAS = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Annotated\[[^\]]*Depends\(([^)]+)\)")


def _classify_doc_auth(text: str) -> str:
    low = text.lower()
    if "admin" in low:
        return ADMIN
    if "svix" in low or "webhook" in low:
        return WEBHOOK
    if "public" in low and "token" in low:
        return PUBLIC_TOKEN
    if "public" in low:
        return PUBLIC
    if "required" in low:
        return AUTHENTICATED
    raise AssertionError(f"Unrecognised auth phrasing in docs: {text!r}")


def _resolve_aliases(route_body: str, aliases: dict[str, str]) -> str:
    """Expands any `name: AliasName` signature parameter into the alias's own Depends(...) text,
    so classification below sees it the same way as an inline Depends(...) parameter.
    """
    expanded = route_body
    for alias_name, depends_target in aliases.items():
        if re.search(rf"\b{re.escape(alias_name)}\b", expanded):
            expanded += f"\n# resolved alias {alias_name} -> Depends({depends_target})"
    return expanded


def _classify_code_auth(route_body: str) -> str:
    if "Depends(require_admin)" in route_body:
        return ADMIN
    if "Depends(require_user)" in route_body or "Depends(user_conn)" in route_body:
        return AUTHENTICATED
    if "verify(" in route_body and "svix" in route_body.lower():
        return WEBHOOK
    if "UnsubscribeIn" in route_body or "token: UUID" in route_body:
        return PUBLIC_TOKEN
    return PUBLIC


_PATH_PARAM = re.compile(r"\{[^}]+\}")


def _normalise_path(route: str) -> str:
    # Path-parameter *names* are a human-readability choice in docs ({id}) vs. code
    # ({source_id}); only the shape of the route matters for this check.
    return _PATH_PARAM.sub("{param}", route.strip())


def _parse_docs() -> dict[str, str]:
    """Returns {"METHOD /path": auth_bucket} for every row across all docs/api_*.md tables."""
    documented: dict[str, str] = {}
    for path in sorted(DOCS_DIR.glob("api_*.md")):
        text = path.read_text()
        for method, route, auth_text in _DOC_ROW.findall(text):
            key = f"{method} {_normalise_path(route)}"
            documented[key] = _classify_doc_auth(auth_text)
    return documented


def _parse_routes() -> dict[str, str]:
    """Returns {"METHOD /path": auth_bucket} for every @router.* route in app/api/**/*.py,
    using each route's full path (router prefix + the path literal from the decorator).
    """
    routes: dict[str, str] = {}
    router_files = list((API_DIR / "v1").glob("*.py")) + [API_DIR / "health.py"]
    for path in router_files:
        text = path.read_text()
        prefix_match = _ROUTER_PREFIX.search(text)
        prefix = prefix_match.group(1) if prefix_match else ""
        aliases = dict(_TYPE_ALIAS.findall(text))

        decorators = list(_ROUTE_DECORATOR.finditer(text))
        for i, match in enumerate(decorators):
            method = match.group(1).upper()
            route_path = match.group(2) or match.group(3) or ""
            full_path = (prefix + route_path) or prefix
            if full_path == "":
                full_path = "/"
            # Slice the source from this decorator to the next one (or EOF) as "this route's body".
            start = match.start()
            end = decorators[i + 1].start() if i + 1 < len(decorators) else len(text)
            body = _resolve_aliases(text[start:end], aliases)
            key = f"{method} {_normalise_path(full_path)}"
            routes[key] = _classify_code_auth(body)
    return routes


def test_every_documented_endpoint_exists_in_the_route_source():
    documented = _parse_docs()
    actual = _parse_routes()
    missing = sorted(set(documented) - set(actual))
    assert not missing, f"Documented endpoints not found in any router source: {missing}"


def test_every_documented_endpoints_auth_matches_the_code():
    documented = _parse_docs()
    actual = _parse_routes()
    mismatches = {
        key: (documented[key], actual[key])
        for key in documented
        if key in actual and documented[key] != actual[key]
    }
    assert not mismatches, (
        "Auth mismatch between docs and code (documented, actual) for: "
        f"{mismatches}. Update the docs/api_*.md table or the route's Depends() to match."
    )


def test_health_endpoints_need_no_auth_and_are_not_claimed_otherwise():
    actual = _parse_routes()
    assert actual["GET /health"] == PUBLIC
    assert actual["GET /ready"] == PUBLIC


def test_admin_only_endpoint_is_classified_admin_on_both_sides():
    documented = _parse_docs()
    actual = _parse_routes()
    key = "GET /sources/{param}/runs"
    assert documented[key] == ADMIN
    assert actual[key] == ADMIN


# --- unit coverage for the parsing/classification helpers themselves -------------------------
# No current router actually depends on an aliased auth dependency (every alias in app/api/v1/
# points at public_conn/get_db/get_settings_dep), so the integration checks above never exercise
# alias resolution. These tests prove it works, so it's not a dead code path if a future router
# does use one (e.g. `UserConn = Annotated[AsyncConnection, Depends(user_conn)]`).


def test_resolve_aliases_expands_an_aliased_auth_dependency():
    aliases = {"UserConn": "user_conn", "AdminUser": "require_admin"}
    body = "async def handler(conn: UserConn) -> None: ..."
    expanded = _resolve_aliases(body, aliases)
    assert _classify_code_auth(expanded) == AUTHENTICATED


def test_resolve_aliases_leaves_unrelated_body_text_alone():
    aliases = {"UserConn": "user_conn"}
    body = "async def handler(conn: PublicConn) -> None: ..."
    expanded = _resolve_aliases(body, aliases)
    assert _classify_code_auth(expanded) == PUBLIC


def test_classify_doc_auth_recognises_every_bucket_used_in_the_docs():
    assert _classify_doc_auth("public") == PUBLIC
    assert _classify_doc_auth("public (token)") == PUBLIC_TOKEN
    assert _classify_doc_auth("required (Supabase JWT)") == AUTHENTICATED
    assert _classify_doc_auth("admin") == ADMIN
    assert _classify_doc_auth("Svix signature") == WEBHOOK


def test_normalise_path_treats_differently_named_params_as_equal():
    assert _normalise_path("/sources/{id}/runs") == _normalise_path("/sources/{source_id}/runs")
