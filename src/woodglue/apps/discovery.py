"""Auto-discover public callables from a module and register them into a Namespace."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

from lythonic.compose.namespace import Namespace


def auto_discover(module_path: str) -> Namespace:
    """Import *module_path*, find every public function, and register it.

    If the target is a package (has ``__path__``), all submodules are
    walked recursively so nested public functions are discovered too.

    Returns a populated :class:`Namespace`.
    """
    ns = Namespace()
    module = importlib.import_module(module_path)

    modules_to_scan: list = [module]

    if hasattr(module, "__path__"):
        for _importer, modname, _ispkg in pkgutil.walk_packages(
            module.__path__, prefix=module.__name__ + "."
        ):
            try:
                modules_to_scan.append(importlib.import_module(modname))
            except Exception:  # noqa: BLE001
                continue

    for mod in modules_to_scan:
        for name, func in inspect.getmembers(mod, inspect.isfunction):
            if name.startswith("_"):
                continue
            # Only register functions actually defined in this module
            if getattr(func, "__module__", None) != mod.__name__:
                continue
            ns.register(func)

    return ns
