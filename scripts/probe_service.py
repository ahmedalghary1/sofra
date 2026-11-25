"""Read-only health timing probe; it never submits orders or sends messages."""
import argparse
import json
import math
import statistics
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--samples', type=int, default=10, choices=range(1, 101))
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        parser.error('Supply a plain website origin without credentials or query parameters.')
    durations, failures = [], 0
    for _ in range(args.samples):
        started = monotonic()
        try:
            with urlopen(args.base_url.rstrip('/')+'/health/', timeout=10) as response:
                payload = json.loads(response.read(4096))
                if response.status != 200 or not isinstance(payload, dict) or payload.get('status') != 'ok':
                    failures += 1
        except (HTTPError, URLError, ValueError, TimeoutError):
            failures += 1
        durations.append(round((monotonic()-started)*1000, 1))
    ordered = sorted(durations)
    print(json.dumps({'samples': args.samples, 'failures': failures, 'median_ms': statistics.median(durations),
                      'p95_ms': ordered[max(0, math.ceil(len(ordered)*0.95)-1)]}))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
