"""Executable import boundaries, including relative imports and nested imports.

This guards dependencies, not the correctness of authorization/business rules.
Concrete adapters are selected only in the composition root.
"""

import ast
from importlib.util import resolve_name
from pathlib import Path
import sys

import pytest

PACKAGE = "airport_assistant"
INTERNAL_ALLOWED = {
    "api": {"api", "modules"},
    "modules": {"modules"},
    "integrations": {"integrations", "modules"},
    "infrastructure": {"infrastructure", "modules"},
    "main": {"api", "config", "composition", "database_config", "auth_config", "modules"},
    "worker": {"modules", "config", "composition"},
    "database_check": {"composition", "config", "database_config", "infrastructure"},
    "migrate": {"config", "database_config", "infrastructure"},
    "provision": {"composition", "database_config", "infrastructure"},
    "auth_setup": {"auth_config", "composition", "database_config", "infrastructure"},
}
EXTERNAL_ALLOWED = {
    "api": {"fastapi", "starlette", "pydantic"},
    "modules": {"email_validator"},
    "integrations": {"httpx"},
    "infrastructure": {"psycopg", "psycopg_pool", "certifi", "sqlalchemy", "alembic"},
    "main": {"fastapi", "uvicorn"},
    "worker": set(),
    "database_check": set(),
    "migrate": set(),
    "provision": {"dotenv"},
    "auth_setup": {"dotenv", "sqlalchemy"},
}
IO_MODULES = {"socket", "urllib", "http", "sqlite3", "subprocess", "dbm", "shelve"}


def violations(source: str, module: str, *, is_package=False) -> list[str]:
    layer = module.removeprefix(PACKAGE + ".").split(".")[0]
    if layer not in INTERNAL_ALLOWED:
        return []  # composition/config are reviewed separately, not business layers.
    errors = []
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = resolve_name("." * node.level + (node.module or ""), package) if node.level else (node.module or "")
            names = [base] + [base + "." + alias.name for alias in node.names]
            # 'from airport_assistant import infrastructure' must check the alias,
            # while a bare package import must not bypass the layer check.
            if base == PACKAGE:
                names = names[1:]
        elif isinstance(node, ast.Name) and node.id in {"__import__", "__builtins__"}:
            errors.append(f"{node.lineno}: dynamic import is not allowed")
        for name in names:
            root = name.split(".")[0]
            if root == PACKAGE:
                target = name.removeprefix(PACKAGE + ".").split(".")[0]
                if target not in INTERNAL_ALLOWED[layer]:
                    errors.append(f"{node.lineno}: {layer} cannot import {name}")
            elif root in {"importlib", "builtins"}:
                errors.append(f"{node.lineno}: dynamic import machinery is not allowed")
            elif root in sys.stdlib_module_names:
                if layer in {"api", "modules"} and root in IO_MODULES:
                    errors.append(f"{node.lineno}: {layer} cannot use I/O module {root}")
            elif root not in EXTERNAL_ALLOWED[layer]:
                errors.append(f"{node.lineno}: {layer} cannot import dependency {root}")
    return errors


def test_source_dependencies_follow_layer_rules():
    root = Path(__file__).resolve().parents[2] / "src" / PACKAGE
    errors = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).with_suffix("")
        parts = relative.parts
        module = PACKAGE + "." + ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        for error in violations(path.read_text(encoding="utf-8"), module, is_package=parts[-1] == "__init__"):
            errors.append(f"{path.relative_to(root)}:{error}")
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("module,source", [
    ("api.route", "import airport_assistant.integrations.source"),
    ("api.route", "from airport_assistant import infrastructure"),
    ("api.route", "from ..infrastructure import database"),
    ("api.route", "import airport_assistant.composition"),
    ("api.route", "def route():\n import httpx"),
    ("api.route", "import sqlalchemy"),
    ("modules.cases.service", "from ...api import schemas"),
    ("modules.cases.service", "import fastapi"),
    ("modules.cases.service", "import airport_assistant.infrastructure.database"),
    ("modules.cases.service", "import airport_assistant.config"),
    ("modules.cases.service", "import urllib.request"),
    ("modules.cases.service", "__import__('httpx')"),
    ("api.route", "import importlib as loader\nloader.import_module('httpx')"),
    ("api.route", "import builtins\nbuiltins.__import__('httpx')"),
    ("modules.cases.service", "from builtins import __import__ as load\nload('httpx')"),
    ("api.route", "loader = __import__\nloader('httpx')"),
    ("modules.cases.service", "__builtins__['__import__']('httpx')"),
    ("integrations.source.client", "import airport_assistant.api"),
    ("infrastructure.database.repo", "import airport_assistant.integrations.source"),
    ("worker", "import airport_assistant.integrations.email"),
    ("main", "import airport_assistant.infrastructure.database"),
])
def test_guard_detects_prohibited_dependency(module, source):
    assert violations(source, PACKAGE + "." + module)


@pytest.mark.parametrize("module,source", [
    ("api.health", "from airport_assistant.modules.system.health import HealthService"),
    ("modules.cases.service", "from .ports import Repository\nfrom uuid import UUID"),
    ("integrations.source.client", "import httpx\nfrom airport_assistant.modules.identity.ports import SourceGrantProvider"),
    ("infrastructure.database.repo", "from airport_assistant.modules.identity.ports import TenantRepository"),
    ("worker", "from airport_assistant.composition import build_services"),
])
def test_guard_allows_intended_dependency(module, source):
    assert violations(source, PACKAGE + "." + module) == []
