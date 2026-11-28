"""One scheduler per deployment; existing idempotent maintenance commands only."""
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

HEARTBEAT = Path('/tmp/scheduler.heartbeat')
JOBS = (
    ('prune_login_attempts', 3600),
    ('prune_menu_imports', 86400),
    ('rebuild_recommendation_stats', 86400),
    ('prune_recommendation_events', 86400),
)


def run():
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    due = {name: 0 for name, _ in JOBS}
    HEARTBEAT.touch()
    while not stop.is_set():
        for name, interval in JOBS:
            if stop.is_set():
                break
            if time.monotonic() >= due[name]:
                try:
                    subprocess.run([sys.executable, 'manage.py', name], check=True, timeout=1800)
                except (subprocess.SubprocessError, OSError):
                    print(f'Maintenance failed: {name}; retry in one hour.', flush=True)
                    due[name] = time.monotonic() + 3600
                else:
                    due[name] = time.monotonic() + interval
            HEARTBEAT.touch()
        HEARTBEAT.touch()
        stop.wait(30)


if __name__ == '__main__':
    if sys.argv[1:] == ['--healthcheck']:
        try:
            healthy = time.time() - HEARTBEAT.stat().st_mtime < 1900
        except OSError:
            healthy = False
        raise SystemExit(0 if healthy else 1)
    run()
