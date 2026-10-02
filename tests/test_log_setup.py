"""Tests for `woodglue.log_setup.setup_logging`, observed through root logger handlers."""

from __future__ import annotations

import logging
import logging.handlers
from collections.abc import Iterator
from pathlib import Path

import pytest

from woodglue.cli import _resolve_storage  # pyright: ignore[reportPrivateUsage]
from woodglue.config import load_config
from woodglue.log_setup import setup_logging

JOURNAL_ENV = {"JOURNAL_STREAM": "8:12345"}


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Keep handlers and levels installed by a test from leaking into other tests."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    for h in root.handlers:
        if h not in handlers:
            h.close()
    # `dictConfig` drops every root handler, including pytest's capture handlers.
    root.handlers[:] = handlers
    root.setLevel(level)


def _configure(data_dir: Path, yaml_text: str, env: dict[str, str]) -> str:
    (data_dir / "woodglue.yaml").write_text("namespaces: {}\n" + yaml_text)
    config = load_config(data_dir)
    _resolve_storage(config, data_dir)
    return setup_logging(config, env=env)


def _added_handlers() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if (h.name or "").startswith("woodglue.")]


def _format(handler: logging.Handler, level: int, msg: str) -> str:
    record = logging.LogRecord("some.logger", level, __file__, 1, msg, None, None)
    for f in handler.filters:
        if isinstance(f, logging.Filter):
            f.filter(record)
    return handler.format(record)


def test_systemd_default_logs_to_journal_without_file(tmp_path: Path) -> None:
    desc = _configure(tmp_path, "", JOURNAL_ENV)
    assert desc == "Logging to journal"
    assert not (tmp_path / "wgl.log").exists()
    [handler] = _added_handlers()
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.FileHandler)
    assert _format(handler, logging.INFO, "hi") == "<6>INFO     [some.logger] run= node= hi"
    assert _format(handler, logging.DEBUG, "x").startswith("<7>")
    assert _format(handler, logging.WARNING, "x").startswith("<4>")
    assert _format(handler, logging.ERROR, "x").startswith("<3>")
    assert _format(handler, logging.CRITICAL, "x").startswith("<2>")


def test_journal_prefixes_every_line_of_multiline_record(tmp_path: Path) -> None:
    _configure(tmp_path, "", JOURNAL_ENV)
    [handler] = _added_handlers()
    lines = _format(handler, logging.ERROR, "boom\nTraceback line").splitlines()
    assert lines == ["<3>ERROR    [some.logger] run= node= boom", "<3>Traceback line"]


def test_default_without_systemd_writes_rotating_wgl_log(tmp_path: Path) -> None:
    desc = _configure(tmp_path, "storage:\n  log_max_bytes: 1234\n  log_backup_count: 2\n", {})
    log_path = tmp_path / "wgl.log"
    assert desc == f"Logging to {log_path}"
    [handler] = _added_handlers()
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert Path(handler.baseFilename) == log_path
    assert handler.maxBytes == 1234
    assert handler.backupCount == 2
    logging.getLogger("woodglue.test").info("written")
    handler.flush()
    text = log_path.read_text()
    assert "INFO     [woodglue.test] run= node= written" in text


def test_rotation_defaults_are_10mb_and_5_backups(tmp_path: Path) -> None:
    _configure(tmp_path, "", {})
    [handler] = _added_handlers()
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 5


def test_explicit_log_file_under_systemd_adds_rotating_file(tmp_path: Path) -> None:
    desc = _configure(
        tmp_path, "storage:\n  log_file: logs/app.log\n  log_max_bytes: 200\n", JOURNAL_ENV
    )
    log_path = tmp_path / "logs" / "app.log"
    assert desc == f"Logging to journal and {log_path}"
    file_handlers = [
        h for h in _added_handlers() if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert len(_added_handlers()) == 2
    for i in range(20):
        logging.getLogger("woodglue.test").warning("line %d padded to force rotation", i)
    assert (tmp_path / "logs" / "app.log.1").exists()


def test_explicit_null_log_file_without_systemd_logs_to_stderr(tmp_path: Path) -> None:
    desc = _configure(tmp_path, "storage:\n  log_file: null\n", {})
    assert desc == "Logging to stderr"
    assert not (tmp_path / "wgl.log").exists()
    [handler] = _added_handlers()
    assert not isinstance(handler, logging.FileHandler)
    formatted = _format(handler, logging.INFO, "hi")
    assert formatted.endswith(" INFO     [some.logger] run= node= hi")
    assert formatted[:4].isdigit(), "stderr fallback must carry a timestamp"


def test_explicit_null_log_file_under_systemd_has_no_file(tmp_path: Path) -> None:
    assert _configure(tmp_path, "storage:\n  log_file: null\n", JOURNAL_ENV) == "Logging to journal"
    assert not any(isinstance(h, logging.FileHandler) for h in _added_handlers())


def test_default_level_is_info_and_explicit_levels_apply(tmp_path: Path) -> None:
    _configure(tmp_path, "", {})
    assert logging.getLogger().level == logging.INFO

    _configure(
        tmp_path,
        "storage:\n  log_level: warning\n  loggers:\n    woodglue.test.noisy: error\n",
        {},
    )
    assert logging.getLogger().level == logging.WARNING
    noisy = logging.getLogger("woodglue.test.noisy")
    try:
        assert noisy.level == logging.ERROR
    finally:
        noisy.setLevel(logging.NOTSET)


def test_repeated_setup_does_not_duplicate_handlers(tmp_path: Path) -> None:
    _configure(tmp_path, "", JOURNAL_ENV)
    _configure(tmp_path, "", JOURNAL_ENV)
    assert len(_added_handlers()) == 1


def test_logging_section_replaces_defaults(tmp_path: Path) -> None:
    log_path = tmp_path / "custom.log"
    desc = _configure(
        tmp_path,
        "logging:\n"
        "  version: 1\n"
        "  disable_existing_loggers: false\n"
        "  filters:\n"
        "    noderun:\n"
        "      (): lythonic.compose.log_context.NodeRunLogFilter\n"
        "  formatters:\n"
        "    plain:\n"
        "      format: 'custom %(levelname)s run=%(run_id)s %(message)s'\n"
        "  handlers:\n"
        "    custom:\n"
        "      class: logging.FileHandler\n"
        f"      filename: {log_path}\n"
        "      formatter: plain\n"
        "      filters: [noderun]\n"
        "  root:\n"
        "    level: DEBUG\n"
        "    handlers: [custom]\n",
        JOURNAL_ENV,
    )
    assert desc == "Logging per woodglue.yaml logging: section"
    [handler] = logging.getLogger().handlers
    assert isinstance(handler, logging.FileHandler)
    assert not isinstance(handler, logging.handlers.RotatingFileHandler)
    assert not (tmp_path / "wgl.log").exists()
    assert logging.getLogger().level == logging.DEBUG
    logging.getLogger("woodglue.test").debug("via dictconfig")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "custom DEBUG run= via dictconfig" in log_path.read_text()


def test_logging_section_removes_previously_installed_defaults(tmp_path: Path) -> None:
    _configure(tmp_path, "", JOURNAL_ENV)
    [journal] = _added_handlers()
    # No `root:` key, so `dictConfig` itself leaves root handlers alone.
    _configure(
        tmp_path,
        "logging:\n  version: 1\n  disable_existing_loggers: false\n",
        JOURNAL_ENV,
    )
    assert journal not in logging.getLogger().handlers
