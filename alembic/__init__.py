"""
Shim for local `alembic/` folder.

This repository contains a top-level `alembic/` directory for migration scripts.
Without an `__init__.py`, Python can treat it as a namespace package (PEP 420) and
shadow the *installed* Alembic distribution (`pip install alembic`), breaking
imports like `import alembic.op` in code and tests.

This shim re-routes module loading to the external Alembic package in
site-packages, while still keeping migrations on disk for the Alembic CLI.
"""

from __future__ import annotations

import importlib
import os
import sys
import sysconfig
from types import ModuleType
from typing import Any, Optional


def _find_installed_alembic_package_dir() -> Optional[str]:
    """
    Find the installed `alembic` package directory inside site-packages.

    We avoid `importlib.util.find_spec("alembic")` because while this shim is
    being imported, the spec would resolve to *this* module.
    """
    candidates: list[str] = []
    try:
        paths = sysconfig.get_paths()
        for key in ("purelib", "platlib"):
            p = paths.get(key)
            if p:
                candidates.append(p)
    except Exception:
        pass

    # Also consider user site-packages (pip install --user), common in Docker non-root setups.
    try:
        import site

        usp = site.getusersitepackages()
        if usp:
            candidates.append(usp)
    except Exception:
        pass

    # Best-effort fallback: scan sys.path for site-packages
    for p in sys.path:
        if p and ("site-packages" in p or "dist-packages" in p):
            candidates.append(p)

    for base in candidates:
        pkg_dir = os.path.join(base, "alembic")
        init_py = os.path.join(pkg_dir, "__init__.py")
        if os.path.isfile(init_py):
            return pkg_dir
    return None


_external_pkg_dir = _find_installed_alembic_package_dir()
if _external_pkg_dir:
    __path__ = [_external_pkg_dir]  # type: ignore[name-defined]
    # Help import machinery resolve submodules from the external package.
    try:
        if __spec__ is not None and __spec__.submodule_search_locations is not None:  # type: ignore[name-defined]
            __spec__.submodule_search_locations[:] = list(__path__)  # type: ignore[name-defined]
    except Exception:
        pass


def _get_version() -> str:
    try:
        from importlib.metadata import version

        return version("alembic")
    except Exception:
        # Fallback for older environments
        try:
            import pkg_resources

            return pkg_resources.get_distribution("alembic").version
        except Exception:
            return "unknown"


__version__ = _get_version()


# Eagerly expose the most commonly used modules.
op = importlib.import_module("alembic.op")
context = importlib.import_module("alembic.context")


def __getattr__(name: str) -> Any:
    """
    Lazy-load submodules from the installed Alembic package, e.g.:
    - `alembic.config`
    - `alembic.script`
    """
    try:
        mod: ModuleType = importlib.import_module(f"alembic.{name}")
    except Exception as e:
        raise AttributeError(name) from e
    globals()[name] = mod
    return mod


__all__ = ["op", "context", "__version__"]
