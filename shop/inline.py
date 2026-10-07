"""Processes queued jobs inside the web process, for hosts that run no separate `process_jobs`
worker (e.g. Render's free plan). Enabled with INLINE_JOB_PROCESSING=1. With a real worker
running, leave it off so two runners don't compete — claiming is race-safe either way."""
import threading
from datetime import timedelta
from django.conf import settings
from django.db import connections as db_connections
from django.utils import timezone
from .models import Job

def drain_jobs(limit=25):
    from .management.commands.process_jobs import claim_job
    from .workflow import run_job, expire_reservations
    now = timezone.now()
    # A web process killed mid-job (deploys, free-plan sleep) leaves it 'running' forever otherwise.
    Job.objects.filter(status='running', locked_at__lt=now-timedelta(minutes=5)).update(status='pending', available_at=now)
    for _ in range(limit):
        job = claim_job(timezone.now())
        if not job:
            break
        run_job(job)
    expire_reservations()

def kick():
    """Starts a background drain after the HTTP response is decided; no-op unless enabled."""
    if not settings.INLINE_JOB_PROCESSING:
        return
    def target():
        try:
            drain_jobs()
        finally:
            db_connections.close_all()
    threading.Thread(target=target, daemon=True).start()
