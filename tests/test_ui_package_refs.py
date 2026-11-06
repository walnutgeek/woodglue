"""`package:` references in `uis`: resolution, failures, and the commands they affect."""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from textwrap import dedent

import pytest

from woodglue.cli import main_at
from woodglue.config import UiEntry, UiKind, WoodglueConfig, resolve_package_refs


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An importable package with a script and a built UI, and no import side effects."""
    root = tmp_path / "site" / "wgl_refs_pkg"
    (root / "dist" / "assets").mkdir(parents=True)
    (root / "__init__.py").write_text("raise AssertionError('resolving must not import this')\n")
    (root / "explorer.py").write_text("print('hi')\n")
    (root / "dist" / "index.html").write_text("<!doctype html>")
    (tmp_path / "site" / "wgl_refs_module.py").write_text("")
    monkeypatch.syspath_prepend(tmp_path / "site")
    importlib.invalidate_caches()
    return root


def _config(**entry: str) -> WoodglueConfig:
    return WoodglueConfig(namespaces={}, uis={"explore": UiEntry.model_validate(entry)})


def test_app_and_path_resolve_inside_the_package(package: Path) -> None:
    config = WoodglueConfig(
        namespaces={},
        uis={
            "script": UiEntry(kind=UiKind.streamlit, app="package:wgl_refs_pkg/explorer.py"),
            "built": UiEntry(path="package:wgl_refs_pkg/dist"),
        },
    )
    resolve_package_refs(config)
    assert config.uis["script"].app == str(package / "explorer.py")
    assert config.uis["built"].path == str(package / "dist")


def test_plain_paths_are_untouched(package: Path) -> None:  # pyright: ignore[reportUnusedParameter]
    config = _config(kind="streamlit", app="/srv/app.py")
    resolve_package_refs(config)
    assert config.uis["explore"].app == "/srv/app.py"


@pytest.mark.parametrize(
    ("field", "ref", "complaint"),
    [
        ("app", "package:no_such_pkg_here/app.py", "is not installed"),
        ("app", "package:wgl_refs_module/app.py", "is not a package"),
        ("app", "package:wgl_refs_pkg/missing.py", "no such file"),
        ("app", "package:wgl_refs_pkg/dist", "no such file"),
        ("path", "package:wgl_refs_pkg/explorer.py", "no such directory"),
        ("app", "package:wgl_refs_pkg/../secret.py", "stay inside"),
        ("app", "package:wgl_refs_pkg/dist/../../../secret.py", "stay inside"),
        ("app", "package:wgl_refs_pkg//etc/passwd", "stay inside"),
        ("app", "package:wgl_refs_pkg/C:/secret.py", "stay inside"),
        ("app", "package:wgl_refs_pkg/..\\secret.py", "stay inside"),
        ("app", "package:/etc/passwd", "import name"),
        ("app", "package:my-app/app.py", "import name"),
        ("app", "package:wgl_refs_pkg.dist/app.py", "import name"),
        ("path", "package:wgl_refs_pkg", "a path inside the package"),
        ("path", "package:wgl_refs_pkg/", "a path inside the package"),
    ],
)
def test_bad_references_name_the_ui_and_the_reference(
    package: Path,  # pyright: ignore[reportUnusedParameter]
    field: str,
    ref: str,
    complaint: str,
) -> None:
    entry = {"kind": "streamlit", field: ref} if field == "app" else {field: ref}
    with pytest.raises(ValueError, match=complaint) as exc:
        resolve_package_refs(_config(**entry))
    assert "explore" in str(exc.value)
    assert ref in str(exc.value)


def _write_config(data_dir: Path, ref: str) -> None:
    (data_dir / "woodglue.yaml").write_text(
        dedent(f"""
            namespaces: {{}}
            uis:
              explore:
                path: "{ref}"
            """)
    )


def test_wgl_start_stops_on_a_bad_reference(tmp_path: Path) -> None:
    _write_config(tmp_path, "package:no_such_pkg_here/dist")
    with pytest.raises(SystemExit) as exc:
        main_at.run_args(["wgl", f"--data={tmp_path}", "start"])
    assert "explore" in str(exc.value.code)
    assert "package:no_such_pkg_here/dist" in str(exc.value.code)


def test_wgl_caddy_config_stops_on_a_bad_reference(tmp_path: Path) -> None:
    _write_config(tmp_path, "package:no_such_pkg_here/dist")
    with pytest.raises(SystemExit) as exc:
        main_at.run_args(["wgl", f"--data={tmp_path}", "caddy", "config", "wg.example.com"])
    assert "package:no_such_pkg_here/dist" in str(exc.value.code)


def test_admin_commands_work_with_a_bad_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(tmp_path, "package:no_such_pkg_here/dist")
    result = main_at.run_args(["wgl", f"--data={tmp_path}", "user", "add", "ci"])
    assert result.success, capsys.readouterr().out


def test_caddy_proxies_package_static_uis_to_woodglue(
    tmp_path: Path,
    package: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    shutil.copytree(package / "dist", data_dir / "files")
    (data_dir / "woodglue.yaml").write_text(
        dedent("""
            namespaces: {}
            uis:
              built:
                path: package:wgl_refs_pkg/dist
              local:
                path: files
            """)
    )
    result = main_at.run_args(["wgl", f"--data={data_dir}", "caddy", "config", "wg.example.com"])
    out = capsys.readouterr().out
    assert result.success, out
    assert "handle /ui/local/*" in out
    assert "/ui/built" not in out
    assert str(package) not in out
