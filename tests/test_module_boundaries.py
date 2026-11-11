"""The Core never imports a module (lot C, card « Séparer le Core et les
modules », 1 Nov 2026): modules plug in through ``sarcade.modules.hooks``
and are loaded by name in ``sarcade.modules.loader``."""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "sarcade"
MODULES = ("adrasec",)


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
            yield from (f"{node.module}.{a.name}" for a in node.names)


def test_core_files_do_not_import_modules():
    bad = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        if rel.parts[:1] == ("modules",) and len(rel.parts) > 2 and rel.parts[1] in MODULES:
            continue  # inside a module
        for name in _imports(path):
            if any(name == f"sarcade.modules.{m}" or name.startswith(f"sarcade.modules.{m}.") for m in MODULES):
                bad.append(f"{rel}: {name}")
    assert not bad, "Core imports a module:\n" + "\n".join(bad)


def test_old_adrasec_packages_are_gone():
    # Python files only: a stale __pycache__ may stay in a working copy.
    assert not list((SRC / "aprs").glob("*.py")) and not list((SRC / "reference").glob("*.py"))
    assert not (SRC / "api" / "aprs.py").exists() and not (SRC / "api" / "signals.py").exists()


def test_adrasec_registers_its_hooks():
    from sarcade.api.app import app
    from sarcade.modules import hooks

    paths = set(app.openapi()["paths"])
    assert "/api/v0.1/reference-sites" in paths
    assert any(p.startswith("/api/v0.1/aprs") for p in paths)
    assert hooks.startups and hooks.position_hooks and hooks.link_providers


def test_reference_sites_empty_for_an_event_without_adrasec():
    """Step S3: the high points and relays belong to the ADRASEC module."""
    from sarcade.db.models import EventRow
    from sarcade.modules.adrasec.api_reference import list_reference_sites

    class Db:
        def __init__(self, event):
            self.event = event

        def get(self, _model, _id):
            return self.event

        def scalars(self, _stmt):
            class R:
                def all(self):
                    return []
            return R()

    args = dict(category=None, q=None, status="active", limit=10)
    assert list_reference_sites(**args, event_id="e", db=Db(EventRow(id="e", settings={"modules": []}))) == []
    assert list_reference_sites(**args, event_id="nope", db=Db(None)) == []
    assert list_reference_sites(**args, event_id="e", db=Db(EventRow(id="e", settings={"modules": ["adrasec"]}))) == []
