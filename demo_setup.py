"""Prepare an isolated local restaurant demo: python demo_setup.py."""

import os
import sys


if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    from django.core.management import execute_from_command_line

    execute_from_command_line(['manage.py', 'seed_demo', *sys.argv[1:]])
