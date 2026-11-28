import os

bind = 'unix:/run/gunicorn/serveza.sock'
umask = 0o007
workers = int(os.getenv('WEB_WORKERS', '3'))
if not 1 <= workers <= 16:
    raise ValueError('WEB_WORKERS must be between 1 and 16.')
timeout = 120
graceful_timeout = 30
worker_tmp_dir = '/tmp'
# Avoid logging QR tokens, invitation tokens and customer query parameters.
accesslog = None
errorlog = '-'
capture_output = True
