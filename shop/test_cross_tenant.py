"""Systematic cross-tenant isolation sweep: every tenant-scoped endpoint must return 404
(never 403 — never confirm the resource exists) when accessed by a user from a different shop.
Complements the individual spot-checks already in tests.py/test_integrations.py/test_rbac.py."""
import json
from django.contrib.auth.models import User
from django.test import TestCase
from .models import Shop, Product, ProductImage, Conversation, Order, Job, Membership, ROLE_OWNER
from .seed import seed_demo

class CrossTenantSweepTests(TestCase):
    def setUp(self):
        self.owner_a = User.objects.create_user('tenant-a-owner', password='Not-a-real-password-901!')
        self.shop_a = Shop.objects.create(owner=self.owner_a, name='Shop A')
        Membership.objects.create(shop=self.shop_a, user=self.owner_a, role=ROLE_OWNER)
        seed_demo(self.shop_a)
        self.product_a = Product.objects.create(shop=self.shop_a, name='A product', price=1000, stock=5)
        self.photo_a = ProductImage.objects.create(product=self.product_a, image='products/a.jpg', position=1)
        self.conversation_a = Conversation.objects.create(shop=self.shop_a, name='A customer', phone='255700000001', is_demo=False)
        self.order_a = Order.objects.create(shop=self.shop_a, name='A customer', item='A product', total=1000, product=self.product_a, is_demo=False)
        self.job_a = Job.objects.create(shop=self.shop_a, kind='send', key='sweep-a', status='failed', available_at='2026-01-01T00:00:00Z')

        self.owner_b = User.objects.create_user('tenant-b-owner', password='Not-a-real-password-902!')
        self.shop_b = Shop.objects.create(owner=self.owner_b, name='Shop B')
        self.membership_b = Membership.objects.create(shop=self.shop_b, user=self.owner_b, role=ROLE_OWNER)
        self.client.force_login(self.owner_b)

    def post(self, url, data=None):
        return self.client.post(url, data=json.dumps(data or {}), content_type='application/json')

    def test_every_tenant_scoped_endpoint_rejects_a_foreign_shops_resources(self):
        cases = [
            ('POST', f'/api/products/{self.product_a.id}/photo/'),
            ('POST', f'/api/products/{self.product_a.id}/photos/'),
            ('POST', f'/api/products/{self.product_a.id}/photos/{self.photo_a.id}/delete/'),
            ('POST', f'/api/live/{self.conversation_a.id}/send/'),
            ('POST', f'/api/orders/{self.order_a.id}/checkout/'),
            ('POST', f'/api/orders/{self.order_a.id}/cancel/'),
            ('POST', f'/api/jobs/{self.job_a.id}/retry/'),
            ('POST', f'/api/staff/{self.membership_b.id}/role/'),  # sanity: own membership works
        ]
        for method, url in cases[:-1]:
            response = self.post(url) if method == 'POST' else self.client.get(url)
            self.assertEqual(response.status_code, 404, f'{method} {url} should 404 for a foreign shop, got {response.status_code}')

    def test_staff_endpoints_reject_a_foreign_shops_membership(self):
        foreign_membership = Membership.objects.get(user=self.owner_a, shop=self.shop_a)
        self.assertEqual(self.post(f'/api/staff/{foreign_membership.id}/role/', {'role': 'manager'}).status_code, 404)
        self.assertEqual(self.post(f'/api/staff/{foreign_membership.id}/remove/').status_code, 404)

    def test_conversation_actions_reject_a_foreign_shops_conversation(self):
        demo_conversation_a = Conversation.objects.filter(shop=self.shop_a, is_demo=True).first()
        for action in ['reply', 'order', 'payment', 'handover']:
            self.assertEqual(self.post(f'/api/conversations/{demo_conversation_a.id}/{action}/').status_code, 404)
