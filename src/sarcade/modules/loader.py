"""Loads the modules shipped with the server (lot C, 1 Nov 2026).

Imported by name, through importlib, so that no Core file imports a module
statically (``tests/test_module_boundaries.py`` checks it).
"""
from __future__ import annotations

import importlib

SHIPPED = ("adrasec",)
_loaded: set[str] = set()


def load() -> None:
    for name in SHIPPED:
        if name in _loaded:
            continue
        importlib.import_module(f"sarcade.modules.{name}").register()
        _loaded.add(name)
