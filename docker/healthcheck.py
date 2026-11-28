import http.client
import os
import socket
from urllib.parse import urlsplit


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/run/gunicorn/serveza.sock')


if __name__ == '__main__':
    connection = UnixConnection('localhost', timeout=4)
    try:
        connection.request('GET', '/health/', headers={
            'Host': urlsplit(os.environ['SITE_URL']).netloc,
            'X-Forwarded-Proto': 'https',
        })
        response = connection.getresponse()
        raise SystemExit(0 if response.status == 200 else 1)
    except (OSError, http.client.HTTPException):
        raise SystemExit(1)
    finally:
        connection.close()
