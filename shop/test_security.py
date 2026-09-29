import re
import json
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from .models import Shop, Membership, ROLE_OWNER

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

    def test_reset_flow_requires_account_email_and_issues_a_working_link(self):
        # No email on file yet: Django's PasswordResetForm silently finds no match (by design, to avoid
        # leaking which usernames exist) — no mail is sent.
        self.client.post('/password-reset/', {'email': 'nobody@example.com'})
        self.assertEqual(len(mail.outbox), 0)

        self.client.force_login(self.shop_owner)
        set_email = self.client.post('/api/account/email/', data=json.dumps({'email': 'owner@example.com'}), content_type='application/json')
        self.assertEqual(set_email.status_code, 200)
        self.client.logout()

        self.client.post('/password-reset/', {'email': 'owner@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        match = re.search(r'/reset/[^\s]+/', body)
        self.assertIsNotNone(match)
        reset_path = match.group(0)

        confirm_page = self.client.get(reset_path, follow=True)
        self.assertEqual(confirm_page.status_code, 200)
        set_url = confirm_page.redirect_chain[-1][0]
        new_password = self.client.post(set_url, {'new_password1': 'Brand-New-Passw0rd!456', 'new_password2': 'Brand-New-Passw0rd!456'})
        self.assertEqual(new_password.status_code, 302)

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
    def test_signup_accepts_optional_email(self):
        response = self.client.post('/signup/', {'username': 'emailmerchant', 'email': 'merchant@example.com', 'password1': 'A-unique-demo-pass-999!', 'password2': 'A-unique-demo-pass-999!'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username='emailmerchant').email, 'merchant@example.com')

    def test_signup_still_works_without_email(self):
        response = self.client.post('/signup/', {'username': 'noemailmerchant', 'password1': 'A-unique-demo-pass-998!', 'password2': 'A-unique-demo-pass-998!'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.get(username='noemailmerchant').email, '')
