import json
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Shop, Membership, ROLE_OWNER, UserProfile, OtpCode

class LoginThrottlingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('throttle-user', password='Real-Passw0rd!123')

    def test_locks_out_after_failure_limit_then_recovers_on_correct_password_before_limit(self):
        for _ in range(4):
            response = self.client.post('/login/', {'username': 'throttle-user', 'password': 'wrong'})
            self.assertEqual(response.status_code, 200)  # re-rendered login form, not yet locked
        locked = self.client.post('/login/', {'username': 'throttle-user', 'password': 'wrong'})
        self.assertEqual(locked.status_code, 429)
        still_locked = self.client.post('/login/', {'username': 'throttle-user', 'password': 'Real-Passw0rd!123'})
        self.assertEqual(still_locked.status_code, 429)  # correct password does not bypass an active lockout

    def test_successful_login_under_the_limit_still_works(self):
        for _ in range(2):
            self.client.post('/login/', {'username': 'throttle-user', 'password': 'wrong'})
        ok = self.client.post('/login/', {'username': 'throttle-user', 'password': 'Real-Passw0rd!123'})
        self.assertEqual(ok.status_code, 302)

class PasswordResetTests(TestCase):
    def setUp(self):
        self.shop_owner = User.objects.create_user('reset-owner', password='Old-Passw0rd!123')
        shop = Shop.objects.create(owner=self.shop_owner)
        Membership.objects.create(shop=shop, user=self.shop_owner, role=ROLE_OWNER)

    def test_reset_flow_requires_a_verified_channel_and_resets_the_password(self):
        # No verified email/phone on file yet: the same generic message either way, so a
        # nonexistent account and an unverified one are indistinguishable to the requester.
        step1 = self.client.post('/password-reset/', {'username': 'reset-owner'})
        self.assertContains(step1, 'a code was sent')

        self.shop_owner.email = 'owner@example.com'
        self.shop_owner.save()
        UserProfile.objects.create(user=self.shop_owner, email_verified=True)

        with patch('shop.views.send_otp') as mock_send:
            step1 = self.client.post('/password-reset/', {'username': 'reset-owner'})
            self.assertContains(step1, 'Choose where')
            step2 = self.client.post('/password-reset/', {'username': 'reset-owner', 'channel': 'email'})
        self.assertEqual(step2.status_code, 302)
        code = mock_send.call_args.args[2]

        verify = self.client.post(step2.url, {'code': code, 'new_password1': 'Brand-New-Passw0rd!456', 'new_password2': 'Brand-New-Passw0rd!456'})
        self.assertEqual(verify.status_code, 302)

        self.client.logout()
        relogin = self.client.post('/login/', {'username': 'reset-owner', 'password': 'Brand-New-Passw0rd!456'})
        self.assertEqual(relogin.status_code, 302)

    def test_account_email_rejects_invalid_address(self):
        self.client.force_login(self.shop_owner)
        response = self.client.post('/api/account/email/', data=json.dumps({'email': 'not-an-email'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_account_email_can_be_cleared(self):
        self.client.force_login(self.shop_owner)
        self.client.post('/api/account/email/', data=json.dumps({'email': 'owner@example.com'}), content_type='application/json')
        self.client.post('/api/account/email/', data=json.dumps({'email': ''}), content_type='application/json')
        self.shop_owner.refresh_from_db()
        self.assertEqual(self.shop_owner.email, '')

class SignupEmailTests(TestCase):
    def _signup(self, **fields):
        data = {'username':'emailmerchant','password1':'A-unique-demo-pass-999!','password2':'A-unique-demo-pass-999!','channel':'email','email':'merchant@example.com'}
        data.update(fields)
        with patch('shop.views.send_otp') as mock_send:
            response = self.client.post('/signup/', data)
        return response, mock_send

    def test_signup_via_email_channel_verifies_email(self):
        response, mock_send = self._signup()
        self.assertEqual(response.status_code, 302)
        code = mock_send.call_args.args[2]
        verify = self.client.post(response.url, {'code': code})
        self.assertEqual(verify.status_code, 302)
        user = User.objects.get(username='emailmerchant')
        self.assertEqual(user.email, 'merchant@example.com')
        self.assertTrue(user.profile.email_verified)
        self.assertFalse(user.profile.phone_verified)

    def test_signup_via_whatsapp_channel_verifies_phone_not_email(self):
        response, mock_send = self._signup(username='phonemerchant', channel='whatsapp', email='', phone='+255712345678')
        self.assertEqual(response.status_code, 302)
        code = mock_send.call_args.args[2]
        verify = self.client.post(response.url, {'code': code})
        self.assertEqual(verify.status_code, 302)
        user = User.objects.get(username='phonemerchant')
        self.assertEqual(user.email, '')
        self.assertTrue(user.profile.phone_verified)
        self.assertFalse(user.profile.email_verified)

    def test_signup_requires_destination_matching_chosen_channel(self):
        response = self.client.post('/signup/', {'username':'nodest','password1':'A-unique-demo-pass-997!','password2':'A-unique-demo-pass-997!','channel':'whatsapp','email':'','phone':''})
        self.assertEqual(response.status_code, 200)  # re-rendered with a validation error, not redirected
        self.assertFalse(User.objects.filter(username='nodest').exists())

    def test_signup_rejects_wrong_code_and_locks_out_after_five_attempts(self):
        response, mock_send = self._signup(username='lockoutmerchant')
        for _ in range(5):
            attempt = self.client.post(response.url, {'code': '000000'})
            self.assertEqual(attempt.status_code, 200)
        code = mock_send.call_args.args[2]
        final = self.client.post(response.url, {'code': code})  # correct code, but attempts exhausted
        self.assertEqual(final.status_code, 200)
        self.assertFalse(User.objects.filter(username='lockoutmerchant').exists())

    def test_signup_resend_respects_cooldown(self):
        response, mock_send = self._signup(username='resendmerchant')
        self.client.post(response.url, {'resend': '1'})
        self.assertEqual(mock_send.call_count, 1)  # cooldown blocked the second send

    def test_signup_expired_code_cannot_be_verified(self):
        response, mock_send = self._signup(username='expiredmerchant')
        code = mock_send.call_args.args[2]
        OtpCode.objects.filter(pending_username='expiredmerchant').update(expires_at=timezone.now()-timedelta(seconds=1))
        verify = self.client.post(response.url, {'code': code})
        self.assertEqual(verify.status_code, 200)
        self.assertFalse(User.objects.filter(username='expiredmerchant').exists())

    def test_signup_whatsapp_channel_without_provider_configured_shows_clean_error(self):
        # No GHALA_* settings in the test environment: whatsapp_otp() should raise ProviderError,
        # surfaced as a form error, never a 500 or a created account.
        response = self.client.post('/signup/', {'username':'nowhatsapp','password1':'A-unique-demo-pass-996!','password2':'A-unique-demo-pass-996!','channel':'whatsapp','phone':'+255712345678'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not configured')
        self.assertFalse(User.objects.filter(username='nowhatsapp').exists())

    def test_signup_verify_handles_username_taken_between_steps(self):
        response, mock_send = self._signup(username='racedmerchant')
        code = mock_send.call_args.args[2]
        User.objects.create_user('racedmerchant', password='Someone-Else-Got-Here-First!1')
        verify = self.client.post(response.url, {'code': code})
        self.assertEqual(verify.status_code, 200)
        self.assertContains(verify, 'just taken')
        self.assertEqual(User.objects.filter(username='racedmerchant').count(), 1)  # only the racer's row

@override_settings(GHALA_API_KEY='team-key', GHALA_OTP_TEMPLATE_NAME='otp_verification', GHALA_OTP_TEMPLATE_LANGUAGE='en')
class GhalaWhatsappOtpTests(TestCase):
    def test_reuses_an_existing_contact_instead_of_creating_a_duplicate(self):
        from shop.providers import whatsapp_otp
        responses = [
            {'items': [{'id': 'contact-1', 'phone_number': '+255712345678'}]},  # search hit
            {'id': 'msg-1', 'status': 'SENT'},  # template send
        ]
        with patch('shop.providers._request', side_effect=responses) as mock_request:
            whatsapp_otp('+255712345678', '123456')
        self.assertEqual(mock_request.call_count, 2)
        send_url = mock_request.call_args_list[1].args[1]
        self.assertIn('/inbox/contacts/contact-1/messages/template', send_url)

    def test_creates_a_contact_when_none_exists(self):
        from shop.providers import whatsapp_otp
        responses = [
            {'items': []},  # search miss
            {'id': 'contact-2'},  # contact creation
            {'id': 'msg-2', 'status': 'SENT'},  # template send
        ]
        with patch('shop.providers._request', side_effect=responses) as mock_request:
            whatsapp_otp('+255799999999', '654321')
        self.assertEqual(mock_request.call_count, 3)
        send_url = mock_request.call_args_list[2].args[1]
        self.assertIn('/inbox/contacts/contact-2/messages/template', send_url)

    def test_raises_a_clean_error_when_ghala_never_returns_a_contact_id(self):
        from shop.providers import whatsapp_otp, ProviderError
        with patch('shop.providers._request', side_effect=[{'items': []}, {}]):
            with self.assertRaises(ProviderError):
                whatsapp_otp('+255700000000', '111111')
