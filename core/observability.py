"""Request timing without URL tokens, query strings, bodies or account identifiers."""
import json
import logging
from time import monotonic
from uuid import uuid4


class RequestFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({key: getattr(record, key, None) for key in
            ('request_id', 'route', 'method', 'status', 'duration_ms')}, ensure_ascii=True)


def request_timing_middleware(get_response):
    logger = logging.getLogger('serveza.requests')
    def middleware(request):
        started = monotonic()
        request_id = uuid4().hex
        response = None
        try:
            response = get_response(request)
            response['X-Request-ID'] = request_id
            return response
        finally:
            status = response.status_code if response is not None else 500
            duration = round((monotonic()-started)*1000, 1)
            # Keep successful liveness probes out of the normal request stream.
            match = getattr(request, 'resolver_match', None)
            route = getattr(match, 'url_name', None) or 'unresolved'
            if route != 'health' or status >= 500:
                logger.log(logging.ERROR if status >= 500 else logging.WARNING if duration >= 2000 else logging.INFO,
                           'request', extra={'request_id': request_id, 'route': route,
                           'method': request.method, 'status': status, 'duration_ms': duration})
    return middleware
