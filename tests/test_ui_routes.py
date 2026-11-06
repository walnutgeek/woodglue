"""Tests for the UI routes."""

import tornado.testing
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.config import AuthConfig, WoodglueConfig


class TestUiRoutes(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        return create_app(
            namespaces={}, config=WoodglueConfig(namespaces={}, auth=AuthConfig(enabled=False))
        )

    def test_bare_paths_redirect_to_console(self):
        resp = self.fetch("/console", follow_redirects=False)
        assert resp.code == 301
        assert resp.headers["Location"] == "/console/"

    def test_root_and_legacy_ui_redirect_temporarily_to_default(self):
        for path in ("/", "/ui", "/ui/"):
            resp = self.fetch(path, follow_redirects=False)
            assert resp.code == 302, path
            assert resp.headers["Location"] == "/console/", path

    def test_console_index_served(self):
        resp = self.fetch("/console/")
        assert resp.code == 200
        assert b"<!DOCTYPE html>" in resp.body
