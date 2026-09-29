import json

DEFAULT_CODES = {
    400: 'invalid_request', 401: 'unauthorized', 403: 'forbidden', 404: 'not_found',
    405: 'method_not_allowed', 409: 'conflict', 413: 'payload_too_large',
    429: 'rate_limited', 500: 'internal_error', 503: 'unavailable',
}

class ErrorEnvelopeMiddleware:
    """Adds a machine-readable `code` to any JSON error response that doesn't already specify
    one, without changing the existing `error`/`fields` contract every view already returns."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code >= 400 and response.get('Content-Type', '').startswith('application/json'):
            try:
                data = json.loads(response.content)
            except ValueError:
                return response
            if isinstance(data, dict) and 'error' in data and 'code' not in data:
                data['code'] = DEFAULT_CODES.get(response.status_code, 'error')
                response.content = json.dumps(data)
        return response
