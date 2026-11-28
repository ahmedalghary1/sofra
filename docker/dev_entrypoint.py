import os
import subprocess
import sys

subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], check=True)
os.execvp(sys.argv[1], sys.argv[1:])
