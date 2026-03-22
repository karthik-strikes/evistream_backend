from pathlib import Path
import shutil
import errno
from typing import Iterable

from core.config import CACHE_DIRS


def _iter_children(path: Path) -> Iterable[Path]:
    try:
        yield from path.iterdir()
    except FileNotFoundError:
        return


def _clear_path(cache_path: Path) -> None:
    """
    Attempt to delete `cache_path` and fall back to best-effort cleanup if the
    directory contains stubborn handles (e.g., .nfs temp files).
    """
    try:
        shutil.rmtree(cache_path)
        print(f"Cleared {cache_path}")
        return
    except FileNotFoundError:
        print(f"{cache_path} doesn't exist")
        return
    except OSError as exc:
        if exc.errno != errno.ENOTEMPTY:
            raise

    # Best-effort: remove children individually and retry the directory removal.
    for child in _iter_children(cache_path):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink(missing_ok=True)
            except OSError:
                # Leave the file in place; likely an OS-level lock (.nfs*).
                continue

    try:
        cache_path.rmdir()
        print(f"Cleared {cache_path}")
    except OSError as exc:
        print(
            f"Warning: could not fully clear {cache_path} ({exc}). "
            "Another process may still hold files open."
        )


def clear_cache_directories(cache_root: str = ".") -> None:
    """
    Remove the configured cache directories relative to cache_root.

    Args:
        cache_root: Base directory that contains the cache folders.
    """
    root_path = Path(cache_root).resolve()
    for cache_dir in CACHE_DIRS:
        cache_path = (root_path / cache_dir).resolve()
        if cache_path.exists() and cache_path.is_dir():
            _clear_path(cache_path)
        else:
            print(f"{cache_path} doesn't exist")


if __name__ == "__main__":
    clear_cache_directories()
