"""Single-host startup: fail before serving requests if initialization fails."""
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def prepare():
    for command in (
        ['check'], ['migrate', '--noinput'], ['collectstatic', '--noinput'], ['bootstrap_operator'],
    ):
        subprocess.run([sys.executable, 'manage.py', *command], check=True)


def main():
    arguments = sys.argv[1:]
    if arguments == ['web']:
        prepare()
        os.execvp('gunicorn', ['gunicorn', 'config.wsgi:application', '-c', 'docker/gunicorn.conf.py'])
    elif arguments == ['scheduler']:
        os.execv(sys.executable, [sys.executable, 'docker/scheduler.py'])
    elif arguments:
        # Run management commands without triggering migrations/bootstrap as a side effect.
        os.execvp(arguments[0], arguments)
    else:
        raise SystemExit('A container command is required.')


if __name__ == '__main__':
    main()
