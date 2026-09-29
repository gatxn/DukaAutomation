"""Lists stale pending live orders alongside any Snippe webhook events already on file, for
manual review. Live verification against Snippe's API (to catch e.g. a Snippe-side refund or
chargeback with no local trace) requires a real Snippe account and is documented as an owner
action in OWNER_ACTION_REQUIRED.md — this command only reconciles against data already received."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from shop.models import Order, WebhookEvent

class Command(BaseCommand):
    help = 'List pending live orders older than --hours next to any Snippe webhook events for the same order.'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=24)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options['hours'])
        stale = Order.objects.filter(is_demo=False, payment_status=Order.PAYMENT_PENDING, created_at__lt=cutoff).select_related('shop').order_by('created_at')
        if not stale.exists():
            self.stdout.write('No stale pending orders found.')
            return
        for order in stale:
            events = WebhookEvent.objects.filter(shop=order.shop, provider='snippe')
            matched = any(str(e.payload.get('data', {}).get('metadata', {}).get('order_id')) == str(order.id) for e in events)
            flag = 'HAS a Snippe webhook event but order is still pending — investigate' if matched else 'no Snippe webhook event ever received for this order'
            self.stdout.write(f"Order #{order.id} ({order.shop.name}): TZS {order.total:,}, created {order.created_at:%Y-%m-%d %H:%M}, {flag}")
