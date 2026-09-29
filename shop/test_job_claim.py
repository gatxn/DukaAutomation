"""Exercises both job-claim code paths in process_jobs.py. The Postgres branch (SELECT ... FOR
UPDATE SKIP LOCKED) can only be *exercised* here since SQLite doesn't provide real
skip-locked concurrency semantics — an actual multi-worker race test requires a real Postgres
instance, which this environment does not have (see PRODUCTION_DEPLOYMENT.md)."""
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from shop.management.commands.process_jobs import claim_job
from shop.models import Shop, Job

class JobClaimTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('claim-owner', password='Not-a-real-password-970!')
        self.shop = Shop.objects.create(owner=user)

    def test_sqlite_path_claims_exactly_one_pending_job(self):
        job = Job.objects.create(shop=self.shop, kind='send', key='claim-1', available_at=timezone.now())
        claimed = claim_job(timezone.now())
        self.assertEqual(claimed.id, job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'running')
        self.assertEqual(job.attempts, 1)

    def test_sqlite_path_returns_none_when_nothing_pending(self):
        self.assertIsNone(claim_job(timezone.now()))

    @patch('shop.management.commands.process_jobs.db_connection')
    def test_postgres_branch_runs_and_claims_a_job(self, mock_connection):
        mock_connection.vendor = 'postgresql'
        job = Job.objects.create(shop=self.shop, kind='send', key='claim-2', available_at=timezone.now())
        claimed = claim_job(timezone.now())
        self.assertEqual(claimed.id, job.id)
        job.refresh_from_db()
        self.assertEqual(job.status, 'running')

    @patch('shop.management.commands.process_jobs.db_connection')
    def test_postgres_branch_returns_none_when_nothing_pending(self, mock_connection):
        mock_connection.vendor = 'postgresql'
        self.assertIsNone(claim_job(timezone.now()))
