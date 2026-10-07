"""SMS OTP (Africa's Talking) and the direct Meta WhatsApp Cloud API path (webhook -> AI -> reply).
Provider HTTP is always mocked: nothing here has been exercised against Meta's or Africa's Talking's live APIs."""
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from .connections import enqueue
from .inline import drain_jobs, kick
from .models import Shop, Product, Conversation, Message, Connection, Job, WebhookEvent, Membership, ROLE_OWNER
from .providers import ProviderError, sms_otp, _request
from .secrets import seal, reveal
from .workflow import agent_job, receive_meta, run_job

AT = dict(AFRICASTALKING_USERNAME='duka', AFRICASTALKING_API_KEY='at-key', AFRICASTALKING_SENDER_ID='')
AT_OK = {'SMSMessageData': {'Message': 'Sent to 1/1', 'Recipients': [{'statusCode': 101, 'number': '+255712345678', 'status': 'Success'}]}}

@override_settings(**AT)
class SmsOtpTests(TestCase):
    def test_posts_form_encoded_to_the_live_host_with_the_api_key_header(self):
        with patch('shop.providers._request', return_value=AT_OK) as mock_request:
            sms_otp('+255712345678', '123456')
        provider, url, headers, payload = mock_request.call_args.args
        self.assertEqual(url, 'https://api.africastalking.com/version1/messaging')
        self.assertEqual(headers['apiKey'], 'at-key')
        self.assertTrue(mock_request.call_args.kwargs['form'])
        self.assertEqual(payload['to'], '+255712345678')
        self.assertIn('123456', payload['message'])
        self.assertNotIn('from', payload)  # no sender ID configured

    @override_settings(AFRICASTALKING_USERNAME='sandbox', AFRICASTALKING_SENDER_ID='DUKA')
    def test_sandbox_username_uses_the_sandbox_host_and_sender_id_when_set(self):
        with patch('shop.providers._request', return_value=AT_OK) as mock_request:
            sms_otp('+255712345678', '123456')
        self.assertEqual(mock_request.call_args.args[1], 'https://api.sandbox.africastalking.com/version1/messaging')
        self.assertEqual(mock_request.call_args.args[3]['from'], 'DUKA')

    def test_rejected_recipient_raises_instead_of_pretending_it_was_sent(self):
        rejected = {'SMSMessageData': {'Recipients': [{'statusCode': 403, 'status': 'InvalidPhoneNumber'}]}}
        with patch('shop.providers._request', return_value=rejected), self.assertRaises(ProviderError):
            sms_otp('+255712345678', '123456')
        with patch('shop.providers._request', return_value={}), self.assertRaises(ProviderError):
            sms_otp('+255712345678', '123456')

    @override_settings(AFRICASTALKING_API_KEY='')
    def test_unconfigured_is_a_clean_error(self):
        with self.assertRaisesMessage(ProviderError, 'not configured'):
            sms_otp('+255712345678', '123456')

    def test_request_urlencodes_a_form_payload(self):
        opener = MagicMock()
        response = MagicMock()
        response.read.return_value = b'{}'
        opener.open.return_value.__enter__.return_value = response
        with patch('shop.providers.build_opener', return_value=opener):
            _request('X', 'https://example.com', {'Content-Type': 'application/x-www-form-urlencoded'}, {'to': '+255 7', 'message': 'a&b'}, form=True)
        sent = opener.open.call_args.args[0]
        self.assertEqual(sent.data, b'to=%2B255+7&message=a%26b')

    def test_signup_by_sms_verifies_the_phone_and_reset_offers_both_phone_channels(self):
        with patch('shop.views.send_otp') as mock_send:
            response = self.client.post('/signup/', {'username': 'smsmerchant', 'password1': 'A-unique-demo-pass-881!', 'password2': 'A-unique-demo-pass-881!', 'channel': 'sms', 'phone': '+255712345678'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(mock_send.call_args.args[:2], ('sms', '+255712345678'))
            verify = self.client.post(response.url, {'code': mock_send.call_args.args[2]})
            self.assertEqual(verify.status_code, 302)
        profile = User.objects.get(username='smsmerchant').profile
        self.assertTrue(profile.phone_verified)
        self.assertEqual(profile.phone, '+255712345678')
        self.client.logout()
        with patch('shop.views.send_otp'):
            page = self.client.post('/password-reset/', {'username': 'smsmerchant'})
        self.assertContains(page, 'value="whatsapp"')
        self.assertContains(page, 'value="sms"')

    def test_signup_by_sms_requires_a_phone_number(self):
        response = self.client.post('/signup/', {'username': 'nophone', 'password1': 'A-unique-demo-pass-882!', 'password2': 'A-unique-demo-pass-882!', 'channel': 'sms', 'phone': ''})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='nophone').exists())

    def test_unconfigured_sms_shows_a_clean_error_and_creates_no_account(self):
        with override_settings(AFRICASTALKING_API_KEY=''):
            response = self.client.post('/signup/', {'username': 'nosms', 'password1': 'A-unique-demo-pass-883!', 'password2': 'A-unique-demo-pass-883!', 'channel': 'sms', 'phone': '+255712345678'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not configured')
        self.assertFalse(User.objects.filter(username='nosms').exists())

APP_SECRET = 'meta-app-secret'
NUMBER_ID = '106540352242922'

def meta_payload(text='Habari! Mna bidhaa gani?', message_id='wamid.A1', number_id=NUMBER_ID, sender='255712345678', kind='text'):
    message = {'from': sender, 'id': message_id, 'timestamp': str(int(time.time())), 'type': kind}
    if kind == 'text':
        message['text'] = {'body': text}
    return {'object': 'whatsapp_business_account', 'entry': [{'id': 'waba-1', 'changes': [{'field': 'messages', 'value': {
        'messaging_product': 'whatsapp', 'metadata': {'display_phone_number': '255615445570', 'phone_number_id': number_id},
        'contacts': [{'profile': {'name': 'Asha'}, 'wa_id': sender}], 'messages': [message]}}]}]}

@override_settings(CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode(), META_APP_SECRET=APP_SECRET, META_WEBHOOK_VERIFY_TOKEN='verify-me',
    WHATSAPP_GRAPH_VERSION='v24.0', INLINE_JOB_PROCESSING=False)
class MetaWebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('metamerchant')
        self.shop = Shop.objects.create(owner=self.user, name='Meta shop', delivery=2000)
        Membership.objects.create(shop=self.shop, user=self.user, role=ROLE_OWNER)
        self.product = Product.objects.create(shop=self.shop, name='Linen shirt', description='White, size M', price=20000, stock=5)
        self.connection = Connection.objects.create(shop=self.shop, openai_key=seal('ai-key'), meta_token=seal('meta-token'),
            meta_phone_number_id=NUMBER_ID, agent_enabled=True)

    def signed(self, payload, secret=APP_SECRET, signature=None):
        raw = json.dumps(payload).encode()
        sig = signature if signature is not None else 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return self.client.post('/webhooks/meta/', raw, content_type='application/json', HTTP_X_HUB_SIGNATURE_256=sig)

    def test_verification_handshake_echoes_the_challenge_only_for_the_right_token(self):
        ok = self.client.get('/webhooks/meta/', {'hub.mode': 'subscribe', 'hub.verify_token': 'verify-me', 'hub.challenge': '777'})
        self.assertEqual((ok.status_code, ok.content), (200, b'777'))
        self.assertEqual(self.client.get('/webhooks/meta/', {'hub.mode': 'subscribe', 'hub.verify_token': 'nope', 'hub.challenge': '777'}).status_code, 403)
        with override_settings(META_WEBHOOK_VERIFY_TOKEN=''):
            self.assertEqual(self.client.get('/webhooks/meta/', {'hub.mode': 'subscribe', 'hub.verify_token': '', 'hub.challenge': '777'}).status_code, 403)

    def test_unsigned_wrongly_signed_and_unconfigured_posts_are_rejected_and_store_nothing(self):
        self.assertEqual(self.signed(meta_payload(), secret='wrong').status_code, 401)
        self.assertEqual(self.signed(meta_payload(), signature='').status_code, 401)
        with override_settings(META_APP_SECRET=''):
            self.assertEqual(self.signed(meta_payload()).status_code, 503)
        self.assertEqual(WebhookEvent.objects.count(), 0)
        self.assertEqual(Job.objects.count(), 0)

    def test_signed_message_is_queued_once_even_when_meta_retries(self):
        for _ in range(2):
            self.assertEqual(self.signed(meta_payload()).status_code, 200)
        self.assertEqual(WebhookEvent.objects.filter(provider='meta').count(), 1)
        self.assertEqual(Job.objects.filter(kind='webhook').count(), 1)
        self.assertEqual(Conversation.objects.count(), 0)  # the request only queues work

    def test_unknown_number_is_acknowledged_but_stored_nowhere(self):
        self.assertEqual(self.signed(meta_payload(number_id='999999999')).status_code, 200)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_a_payload_that_is_not_from_a_whatsapp_account_is_rejected(self):
        bad = meta_payload()
        bad['object'] = 'page'
        self.assertEqual(self.signed(bad).status_code, 400)

    def test_message_is_routed_to_the_shop_that_owns_the_number(self):
        other = Shop.objects.create(owner=User.objects.create_user('other'), name='Other')
        Connection.objects.create(shop=other, meta_phone_number_id='555555555', meta_token=seal('t'))
        self.signed(meta_payload())
        self.assertEqual(WebhookEvent.objects.get().shop_id, self.shop.id)

    def test_receive_meta_stores_the_conversation_and_queues_one_agent_job(self):
        self.signed(meta_payload())
        event = WebhookEvent.objects.get()
        receive_meta(event, self.connection)
        receive_meta(event, self.connection)
        conversation = Conversation.objects.get()
        self.assertEqual((conversation.phone, conversation.name, conversation.is_demo), ('255712345678', 'Asha', False))
        self.assertEqual(Message.objects.filter(direction='in').count(), 1)
        self.assertEqual(Job.objects.filter(kind='agent').count(), 1)

    def test_non_text_messages_are_ignored_without_error(self):
        self.signed(meta_payload(kind='image', message_id='wamid.IMG'))
        receive_meta(WebhookEvent.objects.get(), self.connection)
        self.assertEqual(Conversation.objects.count(), 0)

    def test_malformed_sender_is_a_visible_error_not_a_guess(self):
        self.signed(meta_payload(sender='not-a-number'))
        with self.assertRaises(ProviderError):
            receive_meta(WebhookEvent.objects.get(), self.connection)

    @patch('shop.workflow.call')
    @patch('shop.workflow.sales_reply')
    def test_agent_replies_through_the_meta_cloud_api_without_ghala_settings(self, ai, provider):
        # No ghala_auto_reply_disabled, no Ghala token, no public URL: the direct connection needs none of them.
        self.signed(meta_payload())
        receive_meta(WebhookEvent.objects.get(), self.connection)
        job = Job.objects.get(kind='agent')
        ai.return_value = {'reply': 'Karibu! Tuna fulana ya kitani.', 'intent': 'answer', 'product_id': None, 'quantity': 1, 'delivery_address': ''}
        provider.return_value = {'messages': [{'id': 'wamid.OUT'}]}
        agent_job(job, self.connection)
        name, path, token, body = provider.call_args.args
        self.assertEqual((name, path), ('Meta', f'/v24.0/{NUMBER_ID}/messages'))
        self.assertEqual(reveal(token), 'meta-token')
        self.assertEqual(body['to'], '255712345678')
        self.assertEqual(body['text']['body'], 'Karibu! Tuna fulana ya kitani.')
        self.assertEqual(Message.objects.filter(direction='out').count(), 1)

    def test_unconfirmed_send_is_retryable_not_silently_lost(self):
        conversation = Conversation.objects.create(shop=self.shop, name='A', phone='255712345678', is_demo=False, last_inbound_at=timezone.now())
        job = enqueue(self.shop, 'send', 'manual-1', {'conversation_id': conversation.id, 'text': 'Hi'})
        with patch('shop.workflow.call', return_value={}):
            error = run_job(job)
        self.assertIn('did not confirm', error)
        self.assertEqual(Job.objects.get(id=job.id).status, 'failed')

    @patch('shop.connections.call', return_value={'display_phone_number': '+255 615 445 570'})
    def test_saving_credentials_verifies_the_number_with_meta_and_keeps_the_token_sealed(self, provider):
        self.client.force_login(self.user)
        response = self.client.post('/api/connections/', json.dumps({'meta_token': 'EAAG-secret', 'meta_phone_number_id': NUMBER_ID}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('EAAG-secret', response.content.decode())
        self.connection.refresh_from_db()
        self.assertEqual(reveal(self.connection.meta_token), 'EAAG-secret')
        self.assertEqual(provider.call_args.args[1], f'/v24.0/{NUMBER_ID}?fields=display_phone_number')

    @patch('shop.connections.call', side_effect=ProviderError('Meta: Credential rejected. Check the saved token.'))
    def test_a_token_that_does_not_control_the_number_is_refused(self, provider):
        self.client.force_login(self.user)
        response = self.client.post('/api/connections/', json.dumps({'meta_token': 'wrong', 'meta_phone_number_id': NUMBER_ID}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Could not confirm', response.json()['error'])

    @patch('shop.connections.call', return_value={})
    def test_one_number_cannot_be_claimed_by_two_shops(self, provider):
        thief = User.objects.create_user('thief')
        shop = Shop.objects.create(owner=thief, name='Thief')
        Membership.objects.create(shop=shop, user=thief, role=ROLE_OWNER)
        self.client.force_login(thief)
        response = self.client.post('/api/connections/', json.dumps({'meta_token': 'x', 'meta_phone_number_id': NUMBER_ID}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('already connected', response.json()['error'])
        provider.assert_not_called()

    def test_live_replies_can_be_enabled_with_only_the_ai_key_and_meta_connection(self):
        self.connection.agent_enabled = False
        self.connection.save()
        self.client.force_login(self.user)
        with patch('shop.connections.call', return_value={}):
            response = self.client.post('/api/connections/', json.dumps({'agent_enabled': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)

    def test_inline_drain_processes_the_webhook_and_agent_jobs_end_to_end(self):
        self.signed(meta_payload())
        with patch('shop.workflow.sales_reply', return_value={'reply': 'Karibu!', 'intent': 'answer', 'product_id': None, 'quantity': 1, 'delivery_address': ''}), \
             patch('shop.workflow.call', return_value={'messages': [{'id': 'wamid.OUT'}]}):
            drain_jobs()
        self.assertEqual(set(Job.objects.values_list('status', flat=True)), {'done'})
        self.assertEqual(Message.objects.filter(direction='out', body='Karibu!').count(), 1)

    def test_inline_drain_rescues_a_job_stuck_running_from_a_killed_process(self):
        stuck = Job.objects.create(shop=self.shop, kind='bogus', key='stuck', available_at=timezone.now(), status='running', locked_at=timezone.now() - timezone.timedelta(minutes=10))
        drain_jobs()
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, 'failed')  # re-claimed (so it was rescued) and then reported visibly

    @override_settings(INLINE_JOB_PROCESSING=True)
    def test_kick_starts_a_background_thread_only_when_enabled(self):
        with patch('shop.inline.threading.Thread') as thread:
            kick()
        thread.return_value.start.assert_called_once()
        with override_settings(INLINE_JOB_PROCESSING=False), patch('shop.inline.threading.Thread') as thread:
            kick()
        thread.assert_not_called()
