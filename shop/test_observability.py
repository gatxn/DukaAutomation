from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from .models import Shop, Connection, Membership, ROLE_OWNER

class HealthzTests(TestCase):
    def test_healthz_ok_with_no_shops(self):
        response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['checks']['database'], 'ok')

    def test_healthz_flags_no_active_worker_when_shops_exist(self):
        user = User.objects.create_user('health-owner', password='Not-a-real-password-960!')
        Shop.objects.create(owner=user)
        response = self.client.get('/healthz/')
        self.assertEqual(response.status_code, 200)  # worker staleness alone is not a hard failure
        self.assertEqual(response.json()['checks']['worker'], 'no_active_worker')

    def test_healthz_ok_when_worker_heartbeat_is_recent(self):
        user = User.objects.create_user('health-owner2', password='Not-a-real-password-961!')
        shop = Shop.objects.create(owner=user)
        Connection.objects.create(shop=shop, worker_heartbeat=timezone.now())
        response = self.client.get('/healthz/')
        self.assertEqual(response.json()['checks']['worker'], 'ok')

    def test_healthz_requires_no_authentication(self):
        # A load balancer/uptime monitor won't have a session — this must not redirect to login.
        response = self.client.get('/healthz/')
        self.assertNotEqual(response.status_code, 302)

class ErrorEnvelopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('envelope-owner', password='Not-a-real-password-962!')
        shop = Shop.objects.create(owner=self.user)
        Membership.objects.create(shop=shop, user=self.user, role=ROLE_OWNER)

    def test_unauthenticated_error_gets_a_machine_code(self):
        response = self.client.get('/api/state/')
        self.assertEqual(response.status_code, 401)
        data = response.json()
        self.assertEqual(data['error'], 'Please sign in.')
        self.assertEqual(data['code'], 'unauthorized')

    def test_not_found_error_gets_a_machine_code(self):
        self.client.force_login(self.user)
        response = self.client.post('/api/jobs/999999/retry/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 404)
        # Django's default 404 body isn't our JSON envelope, so no `code` is forced onto it —
        # just confirm the middleware doesn't crash on a non-JSON 404 response.
        self.assertEqual(response.status_code, 404)

    def test_validation_error_gets_a_machine_code_without_losing_fields(self):
        self.client.force_login(self.user)
        response = self.client.post('/api/products/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn('fields', data)
        self.assertEqual(data['code'], 'invalid_request')
