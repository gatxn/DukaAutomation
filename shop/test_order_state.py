import io
from datetime import timedelta
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .models import Shop, Product, Order, Membership, ROLE_OWNER
from .workflow import expire_reservations

class OrderStateMachineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('state-owner', password='Not-a-real-password-801!')
        self.shop = Shop.objects.create(owner=self.user, name='State shop')
        Membership.objects.create(shop=self.shop, user=self.user, role=ROLE_OWNER)
        self.product = Product.objects.create(shop=self.shop, name='Widget', price=1000, stock=10)

    def order(self, **extra):
        return Order.objects.create(shop=self.shop, name='Customer', item=self.product.name, total=1000, product=self.product, quantity=1, is_demo=False, **extra)

    def test_mark_paid_is_idempotent(self):
        o = self.order()
        self.assertTrue(o.mark_paid(payment_reference='ref-1'))
        self.assertEqual(o.payment_status, Order.PAYMENT_PAID)
        self.assertEqual(o.status, 'Paid')
        self.assertIsNotNone(o.paid_at)
        self.assertFalse(o.mark_paid(payment_reference='ref-2'))
        o.refresh_from_db()
        self.assertEqual(o.payment_reference, 'ref-1')  # second call never overwrote it

    def test_mark_expired_only_from_pending(self):
        o = self.order()
        o.mark_paid()
        self.assertFalse(o.mark_expired())  # already paid, cannot expire
        o.refresh_from_db()
        self.assertEqual(o.payment_status, Order.PAYMENT_PAID)
        o2 = self.order()
        self.assertTrue(o2.mark_expired())
        self.assertEqual(o2.status, 'Expired')

    def test_ready_for_delivery_requires_payment(self):
        o = self.order()
        with self.assertRaises(ValueError):
            o.mark_ready_for_delivery()
        o.mark_paid()
        self.assertTrue(o.mark_ready_for_delivery())
        self.assertEqual(o.status, 'Ready for delivery')

    def test_delivered_requires_ready_first(self):
        o = self.order()
        with self.assertRaises(ValueError):
            o.mark_delivered()
        o.mark_paid()
        o.mark_ready_for_delivery()
        self.assertTrue(o.mark_delivered())
        self.assertEqual(o.fulfillment_status, Order.FULFILLMENT_DELIVERED)

    def test_cannot_cancel_delivered_order(self):
        o = self.order()
        o.mark_paid()
        o.mark_ready_for_delivery()
        o.mark_delivered()
        with self.assertRaises(ValueError):
            o.cancel()

    def test_cancel_before_delivery_succeeds(self):
        o = self.order()
        self.assertTrue(o.cancel())
        self.assertEqual(o.status, 'Cancelled')

class StockReservationExpiryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('expiry-owner', password='Not-a-real-password-802!')
        self.shop = Shop.objects.create(owner=self.user)
        Membership.objects.create(shop=self.shop, user=self.user, role=ROLE_OWNER)
        self.product = Product.objects.create(shop=self.shop, name='Reserved item', price=5000, stock=3)

    def test_expired_reservation_releases_stock_once(self):
        order = Order.objects.create(shop=self.shop, name='Customer', item=self.product.name, total=5000, product=self.product, quantity=2, is_demo=False, reserved_until=timezone.now()-timedelta(minutes=1))
        self.product.stock = 1  # simulate the 2 units already having been decremented at reservation time
        self.product.save(update_fields=['stock'])
        released = expire_reservations()
        self.assertEqual(released, 1)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PAYMENT_EXPIRED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        self.assertEqual(expire_reservations(), 0)  # second sweep does not double-release

    def test_paid_order_past_window_is_not_touched(self):
        order = Order.objects.create(shop=self.shop, name='Customer', item=self.product.name, total=5000, product=self.product, quantity=1, is_demo=False, reserved_until=timezone.now()-timedelta(minutes=1))
        order.mark_paid()
        self.assertEqual(expire_reservations(), 0)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, Order.PAYMENT_PAID)

    def test_demo_orders_are_never_expired(self):
        Order.objects.create(shop=self.shop, name='Demo customer', item=self.product.name, total=5000, product=self.product, quantity=1, is_demo=True, reserved_until=timezone.now()-timedelta(minutes=1))
        self.assertEqual(expire_reservations(), 0)

    def test_reservation_not_yet_due_is_untouched(self):
        Order.objects.create(shop=self.shop, name='Customer', item=self.product.name, total=5000, product=self.product, quantity=1, is_demo=False, reserved_until=timezone.now()+timedelta(minutes=10))
        self.assertEqual(expire_reservations(), 0)

class OrderActionEndpointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('action-owner', password='Not-a-real-password-803!')
        self.shop = Shop.objects.create(owner=self.user)
        Membership.objects.create(shop=self.shop, user=self.user, role=ROLE_OWNER)
        self.product = Product.objects.create(shop=self.shop, name='Item', price=2000, stock=5)
        self.client.force_login(self.user)

    def post(self, url):
        return self.client.post(url, data='{}', content_type='application/json')

    def test_full_fulfillment_lifecycle_via_api(self):
        order = Order.objects.create(shop=self.shop, name='Customer', item=self.product.name, total=2000, product=self.product, quantity=1, is_demo=False)
        self.assertEqual(self.post(f'/api/orders/{order.id}/ready/').status_code, 400)  # unpaid
        order.mark_paid()
        self.assertEqual(self.post(f'/api/orders/{order.id}/ready/').status_code, 200)
        self.assertEqual(self.post(f'/api/orders/{order.id}/delivered/').status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.fulfillment_status, Order.FULFILLMENT_DELIVERED)
        self.assertEqual(self.post(f'/api/orders/{order.id}/cancel/').status_code, 400)

    def test_cross_shop_order_action_is_blocked(self):
        other = User.objects.create_user('action-other', password='Not-a-real-password-804!')
        other_shop = Shop.objects.create(owner=other)
        Membership.objects.create(shop=other_shop, user=other, role=ROLE_OWNER)
        foreign_product = Product.objects.create(shop=other_shop, name='Foreign', price=1000, stock=1)
        foreign = Order.objects.create(shop=other_shop, name='Foreign customer', item=foreign_product.name, total=1000, product=foreign_product, is_demo=False)
        self.assertEqual(self.post(f'/api/orders/{foreign.id}/cancel/').status_code, 404)

class ReconciliationCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('recon-owner', password='Not-a-real-password-805!')
        self.shop = Shop.objects.create(owner=self.user)
        Membership.objects.create(shop=self.shop, user=self.user, role=ROLE_OWNER)
        self.product = Product.objects.create(shop=self.shop, name='Item', price=1000, stock=1)

    def test_reports_stale_orders_with_and_without_events(self):
        old = timezone.now() - timedelta(hours=48)
        stale = Order.objects.create(shop=self.shop, name='A', item='A', total=1000, product=self.product, is_demo=False)
        Order.objects.filter(id=stale.id).update(created_at=old)
        fresh = Order.objects.create(shop=self.shop, name='B', item='B', total=1000, product=self.product, is_demo=False)
        out = io.StringIO()
        call_command('reconcile_payments', stdout=out)
        text = out.getvalue()
        self.assertIn(f'Order #{stale.id}', text)
        self.assertNotIn(f'Order #{fresh.id}', text)
        self.assertIn('no Snippe webhook event ever received', text)
