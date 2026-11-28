"""Live-server browsers need separate SQLite connections for concurrent HTTP requests.

Shared in-memory SQLite makes Django share one connection across server threads.
Use a disposable file only in this explicitly selected test configuration.
PostgreSQL settings and the development database are unchanged.
"""
from copy import deepcopy
import os
from pathlib import Path
import tempfile

from .settings import *  # noqa: F403

DATABASES = deepcopy(DATABASES)  # noqa: F405
if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default'].setdefault('TEST', {})['NAME'] = str(
        Path(tempfile.gettempdir()) / f'serveza-browser-tests-{os.getpid()}.sqlite3')
    DATABASES['default'].setdefault('OPTIONS', {})['timeout'] = 30
