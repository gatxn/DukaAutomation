"""End-to-end coverage for FR-18 (merchant takeover of a live conversation), which previously had
no automated test beyond the demo-only handover action already covered in tests.py."""
import json
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Shop, Connection, Conversation, Job, Membership, ROLE_OWNER
from .secrets import seal

@override_settings(CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode())
class TakeoverFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('takeover-owner', password='Not-a-real-password-980!')
        self.shop = Shop.objects.create(owner=self.user)
        Membership.objects.create(shop=self.shop, user=self.user, role=ROLE_OWNER)
        self.connection = Connection.objects.create(shop=self.shop, ghala_token=seal('ghala-token'))
        self.conversation = Conversation.objects.create(shop=self.shop, name='Live customer', phone='255711000001', is_demo=False, last_inbound_at=timezone.now(), quote={'product_id': 1, 'unit_price': 1000, 'quantity': 1, 'delivery': 0, 'address': 'x', 'created': timezone.now().timestamp()})
        self.client.force_login(self.user)

    def post(self, url, data=None):
        return self.client.post(url, data=json.dumps(data or {}), content_type='application/json')

    def test_manual_reply_pauses_the_assistant_and_clears_the_pending_quote(self):
        response = self.post(f'/api/live/{self.conversation.id}/send/', {'text': 'Mimi ni mhudumu, nitakusaidia.'})
        self.assertEqual(response.status_code, 200, response.content)
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.human)
        self.assertEqual(self.conversation.quote, {})

    def test_manual_reply_is_queued_and_actually_sent_through_the_provider(self):
        self.post(f'/api/live/{self.conversation.id}/send/', {'text': 'Habari, mimi ni mhudumu wa duka.'})
        job = Job.objects.get(shop=self.shop, kind='send')
        self.assertEqual(job.payload['text'], 'Habari, mimi ni mhudumu wa duka.')
        self.assertTrue(job.payload.get('manual'))
        with patch('shop.workflow.call') as provider:
            provider.return_value = {'id': 'sent-manual-1'}
            call_command('process_jobs', once=True)
        job.refresh_from_db()
        self.assertEqual(job.status, 'done')
        self.assertEqual(provider.call_args.args[:2], ('Ghala', '/api/v2/messages'))
        self.assertTrue(self.conversation.messages.filter(direction='out', body='Habari, mimi ni mhudumu wa duka.', delivery_status='accepted').exists())

    def test_resuming_the_assistant_after_takeover_clears_human_flag(self):
        self.post(f'/api/live/{self.conversation.id}/send/', {'text': 'One moment please.'})
        self.conversation.refresh_from_db()
        self.assertTrue(self.conversation.human)
        # conversation_action's 'handover' branch works for live conversations too (only the
        # other demo-only actions are blocked for is_demo=False), so resuming reuses that endpoint.
        resume = self.post(f'/api/conversations/{self.conversation.id}/handover/', {'human': False})
        self.assertEqual(resume.status_code, 200, resume.content)
        self.conversation.refresh_from_db()
        self.assertFalse(self.conversation.human)

    def test_demo_only_actions_are_still_rejected_for_a_live_conversation(self):
        for action in ['reply', 'order', 'payment']:
            self.assertEqual(self.post(f'/api/conversations/{self.conversation.id}/{action}/').status_code, 400)
