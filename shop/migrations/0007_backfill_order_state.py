from django.db import migrations

MAP = {
    'Pending payment': ('pending', 'unfulfilled'),
    'Paid': ('paid', 'unfulfilled'),
    'Ready for delivery': ('paid', 'ready_for_delivery'),
}

def backfill(apps, schema_editor):
    Order = apps.get_model('shop', 'Order')
    for status, (payment_status, fulfillment_status) in MAP.items():
        Order.objects.filter(status=status).update(payment_status=payment_status, fulfillment_status=fulfillment_status)

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [('shop', '0006_order_fulfillment_status_order_payment_status_and_more')]
    operations = [migrations.RunPython(backfill, noop)]
