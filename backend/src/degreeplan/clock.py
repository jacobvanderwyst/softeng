"""Single time source so tests can control time (monkeypatch ``degreeplan.clock.now``)."""
import time


def now() -> int:
    """Current UTC time as integer epoch seconds."""
    return int(time.time())
