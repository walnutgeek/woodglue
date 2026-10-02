"""
Logging setup for `wgl start`.

Woodglue configures the root logger itself from `woodglue.yaml`; lythonic only
contributes `NodeRunLogFilter`, which adds `run_id` and `node_label` to records
logged inside a node run.

## Default behavior

Under systemd (detected by `JOURNAL_STREAM` in the environment), records go to
stderr without a timestamp, since the journal adds its own. Every line carries an
sd-daemon priority prefix (`<7>` DEBUG, `<6>` INFO, `<4>` WARNING, `<3>` ERROR,
`<2>` CRITICAL) so journald records the right priority. No file log is written
unless `storage.log_file` is set explicitly.

Outside systemd, records go to `<data>/wgl.log` (or `storage.log_file`).

Every file log is a `RotatingFileHandler`, rotated at `storage.log_max_bytes`
(default 10 MB) keeping `storage.log_backup_count` backups (default 5).

```
storage:
  log_file: logs/wgl.log   # relative to the data dir; null disables the file log
  log_level: INFO          # root level, INFO when unset
  log_max_bytes: 10485760
  log_backup_count: 5
  loggers:                 # per-logger levels
    tornado.access: WARNING
```

With `log_file: null` outside systemd, records go to stderr with a timestamp so
they are not lost.

## Custom setup with `logging:`

A top-level `logging:` section in `woodglue.yaml` is passed to
`logging.config.dictConfig` and replaces everything above, including the
`storage.log_*` settings. Attach `NodeRunLogFilter` to keep the run context:

```
logging:
  version: 1
  disable_existing_loggers: false
  filters:
    noderun:
      (): lythonic.compose.log_context.NodeRunLogFilter
  formatters:
    plain:
      format: "%(asctime)s %(levelname)s [%(name)s] run=%(run_id)s %(message)s"
  handlers:
    console:
      class: logging.StreamHandler
      formatter: plain
      filters: [noderun]
  root:
    level: INFO
    handlers: [console]
```

Keep `disable_existing_loggers: false`: the `dictConfig` default of `true`
silences every logger created before setup, including woodglue's own.
"""

from __future__ import annotations

import logging
import logging.config
import logging.handlers
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from lythonic.compose.log_context import NodeRunLogFilter
from typing_extensions import override

from woodglue.config import WoodglueConfig

_HANDLER_PREFIX = "woodglue."
_RECORD_FORMAT = "%(levelname)-8s [%(name)s] run=%(run_id)s node=%(node_label)s %(message)s"
_TIMESTAMPED_FORMAT = f"%(asctime)s {_RECORD_FORMAT}"

_SD_PRIORITIES = {
    logging.DEBUG: 7,
    logging.INFO: 6,
    logging.WARNING: 4,
    logging.ERROR: 3,
    logging.CRITICAL: 2,
}


def _sd_priority(levelno: int) -> int:
    """
    Map a logging level to an sd-daemon priority, rounding custom levels down.

    >>> [_sd_priority(n) for n in (5, 10, 20, 25, 30, 40, 50, 60)]
    [7, 7, 6, 6, 4, 3, 2, 2]
    """
    priority = 7
    for level, p in _SD_PRIORITIES.items():
        if levelno >= level:
            priority = p
    return priority


class JournalFormatter(logging.Formatter):
    """
    Prefix every output line with the sd-daemon priority of the record's level.

    journald reads the prefix per line, so the continuation lines of a traceback
    keep the record's priority instead of defaulting to INFO.
    """

    @override
    def format(self, record: logging.LogRecord) -> str:
        prefix = f"<{_sd_priority(record.levelno)}>"
        return "\n".join(prefix + line for line in super().format(record).split("\n"))


def _remove_installed_handlers(root: logging.Logger) -> None:
    for h in root.handlers[:]:
        if (h.name or "").startswith(_HANDLER_PREFIX):
            root.removeHandler(h)
            h.close()


def _add_handler(
    root: logging.Logger, name: str, handler: logging.Handler, formatter: logging.Formatter
) -> None:
    handler.set_name(_HANDLER_PREFIX + name)
    handler.setFormatter(formatter)
    handler.addFilter(NodeRunLogFilter())
    root.addHandler(handler)


def setup_logging(config: WoodglueConfig, env: Mapping[str, str] | None = None) -> str:
    """
    Configure the root logger for `wgl start` and return a one-line description
    of what is in effect, e.g. `Logging to journal`.

    Expects `config.storage` already resolved against the data dir. Safe to call
    repeatedly: handlers from a previous call are replaced, not duplicated.
    `env` defaults to `os.environ`.
    """
    env = os.environ if env is None else env
    root = logging.getLogger()
    _remove_installed_handlers(root)

    if config.logging is not None:
        logging.config.dictConfig(config.logging)
        return "Logging per woodglue.yaml logging: section"

    storage = config.storage
    under_systemd = "JOURNAL_STREAM" in env
    log_file: Path | None = storage.log_file
    if under_systemd and not storage.log_file_explicit:
        log_file = None

    targets: list[str] = []
    if under_systemd:
        _add_handler(
            root, "journal", logging.StreamHandler(sys.stderr), JournalFormatter(_RECORD_FORMAT)
        )
        targets.append("journal")
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=storage.log_max_bytes,
            backupCount=storage.log_backup_count,
            encoding="utf-8",
        )
        _add_handler(root, "file", file_handler, logging.Formatter(_TIMESTAMPED_FORMAT))
        targets.append(str(log_file))
    if not targets:
        _add_handler(
            root,
            "stderr",
            logging.StreamHandler(sys.stderr),
            logging.Formatter(_TIMESTAMPED_FORMAT),
        )
        targets.append("stderr")

    root.setLevel(storage.log_level.upper())
    for logger_name, level_name in storage.loggers.items():
        logging.getLogger(logger_name).setLevel(level_name.upper())

    return "Logging to " + " and ".join(targets)
