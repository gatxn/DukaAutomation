from django.db import migrations

def backfill(apps, schema_editor):
    Shop = apps.get_model('shop', 'Shop')
    Membership = apps.get_model('shop', 'Membership')
    for shop in Shop.objects.all():
        Membership.objects.get_or_create(shop=shop, user=shop.owner, defaults={'role': 'owner'})

def noop(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [('shop', '0004_auditlog_membership')]
    operations = [migrations.RunPython(backfill, noop)]
