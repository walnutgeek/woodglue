"""Tests for the UI routes."""

import tornado.testing
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.config import WoodglueConfig


class TestUiRoutes(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        return create_app(namespaces={}, config=WoodglueConfig(namespaces={}))

    def test_bare_paths_redirect_to_ui(self):
        for path in ("/", "/ui"):
            resp = self.fetch(path, follow_redirects=False)
            assert resp.code == 301, path
            assert resp.headers["Location"] == "/ui/", path

    def test_ui_index_served(self):
        resp = self.fetch("/ui/")
        assert resp.code == 200
        assert b"<!DOCTYPE html>" in resp.body
