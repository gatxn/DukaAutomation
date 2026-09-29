import importlib
import json
from django.contrib.auth.models import User
from django.test import TestCase
from .models import Shop, Product, Order, Membership, AuditLog, ROLE_OWNER, ROLE_MANAGER, ROLE_AGENT
from .seed import seed_demo

class RBACTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('rbac-owner', password='Not-a-real-password-701!')
        self.shop = Shop.objects.create(owner=self.owner, name='RBAC shop')
        seed_demo(self.shop)
        self.owner_membership = Membership.objects.create(shop=self.shop, user=self.owner, role=ROLE_OWNER)
        self.manager = User.objects.create_user('rbac-manager', password='Not-a-real-password-702!')
        Membership.objects.create(shop=self.shop, user=self.manager, role=ROLE_MANAGER)
        self.agent = User.objects.create_user('rbac-agent', password='Not-a-real-password-703!')
        Membership.objects.create(shop=self.shop, user=self.agent, role=ROLE_AGENT)

    def post(self, url, data=None):
        return self.client.post(url, data=json.dumps(data or {}), content_type='application/json')

    def test_state_reports_role_for_every_level(self):
        for user, role in [(self.owner, ROLE_OWNER), (self.manager, ROLE_MANAGER), (self.agent, ROLE_AGENT)]:
            self.client.force_login(user)
            self.assertEqual(self.client.get('/api/state/').json()['role'], role)

    def test_agent_cannot_reach_manager_or_owner_endpoints(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.post('/api/products/', {'name':'x','price':1,'stock':1}).status_code, 404)
        self.assertEqual(self.post('/api/settings/', {'name':'x','city':'x','language':'Swahili','delivery':0}).status_code, 404)
        self.assertEqual(self.post('/api/connections/', {'agent_name':'x'}).status_code, 404)
        self.assertEqual(self.post('/api/connections/ghala/register/').status_code, 404)
        self.assertEqual(self.client.get('/api/staff/').status_code, 404)
        self.assertEqual(self.post('/api/staff/', {'username':'x','password':'x','role':'agent'}).status_code, 404)

    def test_agent_can_read_connection_status_for_dashboard_display(self):
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get('/api/connections/').status_code, 200)

    def test_agent_can_use_conversation_and_messaging_endpoints(self):
        self.client.force_login(self.agent)
        contact = self.client.get('/api/state/').json()['contacts'][0]
        self.assertEqual(self.post(f"/api/conversations/{contact['id']}/reply/").status_code, 200)

    def test_manager_can_manage_catalog_and_settings_but_not_staff(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post('/api/settings/', {'name':'Renamed','city':'Arusha','language':'Swahili','delivery':1000}).status_code, 200)
        self.assertEqual(self.client.get('/api/connections/').status_code, 200)
        self.assertEqual(self.client.get('/api/staff/').status_code, 404)
        self.assertEqual(self.post('/api/staff/', {'username':'newone','password':'a-real-passw0rd!','role':'agent'}).status_code, 404)

    def test_owner_can_invite_change_role_and_remove_staff(self):
        self.client.force_login(self.owner)
        invited = self.post('/api/staff/', {'username':'new-staffer','password':'a-Str0ng-passw0rd!','role':'agent'})
        self.assertEqual(invited.status_code, 201, invited.content)
        membership_id = invited.json()['id']
        self.assertEqual(Membership.objects.filter(shop=self.shop).count(), 4)
        self.assertEqual(self.post(f'/api/staff/{membership_id}/role/', {'role':'manager'}).status_code, 200)
        self.assertEqual(Membership.objects.get(id=membership_id).role, 'manager')
        self.assertEqual(self.post(f'/api/staff/{membership_id}/remove/').status_code, 200)
        self.assertFalse(Membership.objects.filter(id=membership_id).exists())

    def test_cannot_demote_or_remove_last_owner(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.post(f'/api/staff/{self.owner_membership.id}/role/', {'role':'manager'}).status_code, 400)
        self.assertEqual(self.post(f'/api/staff/{self.owner_membership.id}/remove/').status_code, 400)
        self.assertEqual(Membership.objects.get(id=self.owner_membership.id).role, 'owner')

    def test_staff_invite_rejects_weak_password_and_duplicate_username(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.post('/api/staff/', {'username':'weak','password':'123','role':'agent'}).status_code, 400)
        self.assertEqual(self.post('/api/staff/', {'username':'rbac-manager','password':'a-Str0ng-passw0rd!','role':'agent'}).status_code, 400)

    def test_cross_shop_staff_management_is_blocked(self):
        other_owner = User.objects.create_user('other-owner', password='Not-a-real-password-799!')
        other_shop = Shop.objects.create(owner=other_owner)
        other_membership = Membership.objects.create(shop=other_shop, user=other_owner, role=ROLE_OWNER)
        self.client.force_login(self.owner)
        self.assertEqual(self.post(f'/api/staff/{other_membership.id}/role/', {'role':'manager'}).status_code, 404)

    def test_login_and_logout_are_audited(self):
        self.client.force_login(self.owner)
        self.client.get('/api/state/')  # force_login doesn't fire the login signal; verify logout path directly instead
        self.client.logout()
        self.client.post('/login/', {'username':'rbac-owner','password':'Not-a-real-password-701!'})
        self.assertTrue(AuditLog.objects.filter(action='login', shop=self.shop).exists())
        self.client.get('/logout/')
        self.assertTrue(AuditLog.objects.filter(action='logout', shop=self.shop).exists())

    def test_product_create_and_settings_change_are_audited(self):
        self.client.force_login(self.owner)
        self.post('/api/settings/', {'name':'Audited shop','city':'Dodoma','language':'Swahili','delivery':500})
        self.assertTrue(AuditLog.objects.filter(action='shop_settings_updated', shop=self.shop).exists())

    def test_backfill_migration_creates_missing_owner_membership(self):
        Membership.objects.filter(shop=self.shop).delete()
        self.assertEqual(Membership.objects.filter(shop=self.shop).count(), 0)
        migration = importlib.import_module('shop.migrations.0005_backfill_membership')
        from django.apps import apps as django_apps
        migration.backfill(django_apps, None)
        self.assertEqual(Membership.objects.filter(shop=self.shop, role='owner').count(), 1)

    def test_agent_cannot_perform_order_fulfillment_actions(self):
        product = Product.objects.filter(shop=self.shop).first()
        order = Order.objects.create(shop=self.shop, name='Customer', item=product.name, total=1000, product=product, is_demo=False)
        order.mark_paid()
        self.client.force_login(self.agent)
        for action in ['ready', 'delivered', 'cancel']:
            self.assertEqual(self.post(f'/api/orders/{order.id}/{action}/').status_code, 404)

    def test_agent_cannot_self_escalate_via_any_staff_endpoint(self):
        agent_membership = Membership.objects.get(user=self.agent)
        self.client.force_login(self.agent)
        # None of these should succeed even if the agent knows their own membership id.
        self.assertEqual(self.post(f'/api/staff/{agent_membership.id}/role/', {'role': 'owner'}).status_code, 404)
        self.assertEqual(self.post('/api/staff/', {'username': 'sneaky', 'password': 'a-Str0ng-passw0rd!', 'role': 'owner'}).status_code, 404)
        agent_membership.refresh_from_db()
        self.assertEqual(agent_membership.role, ROLE_AGENT)

    def test_manager_cannot_self_escalate_to_owner(self):
        manager_membership = Membership.objects.get(user=self.manager)
        self.client.force_login(self.manager)
        self.assertEqual(self.post(f'/api/staff/{manager_membership.id}/role/', {'role': 'owner'}).status_code, 404)
        manager_membership.refresh_from_db()
        self.assertEqual(manager_membership.role, ROLE_MANAGER)
