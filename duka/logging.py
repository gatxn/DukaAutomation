import json
import logging

class JsonFormatter(logging.Formatter):
    """Minimal structured-log formatter — one JSON object per line to stdout. Never log raw
    provider payloads, customer messages, or secrets (see process_jobs.py's own discipline)."""
    def format(self, record):
        payload = {
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'time': self.formatTime(record, '%Y-%m-%dT%H:%M:%S'),
        }
        if record.exc_info:
            payload['exc_info'] = self.formatException(record.exc_info)
        return json.dumps(payload)
