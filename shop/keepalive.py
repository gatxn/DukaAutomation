"""Keeps a Render free-plan web service awake by requesting its own public URL.

Render sleeps a free web service after ~15 minutes without inbound traffic, and the first
request after that takes 20+ seconds (platform crawlers time out on it). A request to the
service's public URL, made from inside the service, goes through Render's edge and counts
as traffic. It cannot wake a service that is already asleep, so an external monitor is still
worth having; this covers the gaps when none is running.

Runs only on Render (RENDER_EXTERNAL_URL is set there automatically) and never while
DEBUG/tests are active. KEEPALIVE_URL overrides the target; KEEPALIVE=0 turns it off.
"""
import logging
import os
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 8 * 60
_started = False
_lock = threading.Lock()


def target_url():
    if os.getenv('KEEPALIVE', '1') == '0':
        return ''
    base = os.getenv('KEEPALIVE_URL') or os.getenv('RENDER_EXTERNAL_URL', '')
    return base.rstrip('/') + '/healthz/' if base else ''


def _ping(url):
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'duka-keepalive'})
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read(1)
    except Exception as error:  # never let a failed ping affect the app
        logger.warning('keepalive ping failed: %s', error)


def _loop(url, interval):
    while True:
        time.sleep(interval)
        _ping(url)


def start(interval=INTERVAL_SECONDS):
    """Start the background pinger once per process. Returns True if it started."""
    global _started
    url = target_url()
    if not url:
        return False
    with _lock:
        if _started:
            return False
        _started = True
    threading.Thread(target=_loop, args=(url, interval), name='keepalive', daemon=True).start()
    return True
