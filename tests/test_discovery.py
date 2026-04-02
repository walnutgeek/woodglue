"""Tests for woodglue.apps.discovery.auto_discover."""

import sys
import types

from woodglue.apps.discovery import auto_discover


def test_auto_discover_json_module():
    """auto_discover('json') should find public functions like dumps, loads."""
    ns = auto_discover("json")
    # json.dumps and json.loads are public functions defined in the json module
    node_dumps = ns.get("json:dumps")
    assert node_dumps is not None
    assert node_dumps.nsref == "json:dumps"

    node_loads = ns.get("json:loads")
    assert node_loads is not None
    assert node_loads.nsref == "json:loads"


def test_auto_discover_excludes_private_functions():
    """Private functions (starting with _) should not be registered."""
    mod = types.ModuleType("_test_private_mod")
    mod.__name__ = "_test_private_mod"

    def public_fn():
        return 1

    def _private_fn():
        return 2

    public_fn.__module__ = "_test_private_mod"
    _private_fn.__module__ = "_test_private_mod"
    mod.public_fn = public_fn  # type: ignore[attr-defined]
    mod._private_fn = _private_fn  # type: ignore[attr-defined]

    sys.modules["_test_private_mod"] = mod
    try:
        ns = auto_discover("_test_private_mod")
        # public_fn should be registered
        node = ns.get("_test_private_mod:public_fn")
        assert node is not None
        # _private_fn should NOT be registered
        try:
            ns.get("_test_private_mod:_private_fn")
            raise AssertionError("_private_fn should not be registered")
        except KeyError:
            pass  # expected
    finally:
        del sys.modules["_test_private_mod"]


def test_auto_discover_empty_module():
    """A module with no public functions returns an empty namespace."""
    mod = types.ModuleType("_test_empty_mod")
    mod.__name__ = "_test_empty_mod"

    def _private():
        pass

    _private.__module__ = "_test_empty_mod"
    mod._private = _private  # type: ignore[attr-defined]

    sys.modules["_test_empty_mod"] = mod
    try:
        ns = auto_discover("_test_empty_mod")
        assert len(ns._leaves) == 0
        assert len(ns._branches) == 0
    finally:
        del sys.modules["_test_empty_mod"]
