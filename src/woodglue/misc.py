import shutil
from collections.abc import Callable
from pathlib import Path


def ensure_dir(dir: Path) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    return dir


def _tabula_rasa(p: Path | str, rm_logic: Callable[[Path], None]) -> Path:
    p = Path(p)
    if p.exists():
        rm_logic(p)
    if not p.parent.is_dir():
        p.parent.mkdir(parents=True, exist_ok=True)
    return p


def tabula_rasa_file(test_file_path: Path | str) -> Path:
    """Make sure that file does not exists, but parent dir does"""
    return _tabula_rasa(test_file_path, lambda p: p.unlink())


def tabula_rasa_dir(test_dir_path: Path | str) -> Path:
    """Make sure that dir does not exists, but parent dir does"""
    return _tabula_rasa(test_dir_path, lambda p: shutil.rmtree(p))
