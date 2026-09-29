"""Runs the integration worker. Safe to run as a single worker (the documented baseline) or as
multiple parallel workers once PostgreSQL is in use: on Postgres, job claiming uses
SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers pick different jobs instead of racing on
the same one; SQLite (which has no SKIP LOCKED) keeps the original conditional-update claim,
which is still race-safe for a single worker."""
import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import connection as db_connection, transaction
from django.utils import timezone
from shop.models import Job, Connection
from shop.workflow import process, expire_reservations
from shop.providers import ProviderError

def claim_job(now):
    """Returns a claimed (status='running') Job, or None if nothing is available."""
    if db_connection.vendor == 'postgresql':
        with transaction.atomic():
            job = Job.objects.select_for_update(skip_locked=True).filter(status='pending', available_at__lte=now).order_by('id').first()
            if not job:
                return None
            job.status = 'running'
            job.locked_at = now
            job.attempts += 1
            job.save(update_fields=['status', 'locked_at', 'attempts'])
            return job
    job = Job.objects.filter(status='pending', available_at__lte=now).order_by('id').first()
    if not job or not Job.objects.filter(id=job.id, status='pending').update(status='running', locked_at=now, attempts=job.attempts+1):
        return None
    return job

class Command(BaseCommand):
    help='Process verified webhooks and queued WhatsApp replies. Safe to run multiple workers on PostgreSQL.'
    def add_arguments(self,parser):
        parser.add_argument('--once',action='store_true',help='Drain currently available jobs, then exit.')
    def handle(self,*args,**options):
        self.stdout.write('Duka integration worker ready. Press Ctrl+C to stop.')
        heartbeat=0
        try:
            while True:
                now=timezone.now()
                if time.monotonic()-heartbeat>30:
                    Connection.objects.update(worker_heartbeat=now)
                    Job.objects.filter(status='running',locked_at__lt=now-timedelta(minutes=5)).update(status='pending',available_at=now)
                    released=expire_reservations()
                    if released:
                        self.stdout.write(f'Released stock for {released} expired reservation(s).')
                    heartbeat=time.monotonic()
                job=claim_job(now)
                if not job:
                    if options['once']:
                        break
                    time.sleep(1)
                    continue
                try:
                    process(job)
                    Job.objects.filter(id=job.id).update(status='done',error='',locked_at=None)
                except Exception as exc:
                    # Never log raw provider payloads, customer messages, or secrets.
                    error=str(exc) if isinstance(exc,ProviderError) else 'Processing failed. Check configuration and retry. Technical error type: '+type(exc).__name__
                    Job.objects.filter(id=job.id).update(status='failed',error=error[:500],locked_at=None)
                    self.stderr.write(f'Job {job.id}: {error[:500]}')
        except KeyboardInterrupt:
            self.stdout.write('Worker stopped.')
