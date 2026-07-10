from contextlib import contextmanager
from threading import RLock


_maintenance_lock = RLock()
_maintenance_depth = 0


def is_maintenance_active() -> bool:
    return _maintenance_depth > 0


@contextmanager
def maintenance_mode():
    global _maintenance_depth
    with _maintenance_lock:
        _maintenance_depth += 1
    try:
        yield
    finally:
        with _maintenance_lock:
            _maintenance_depth -= 1
