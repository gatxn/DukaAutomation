from unittest.mock import patch
from django.test import SimpleTestCase
from shop import keepalive


class KeepaliveTests(SimpleTestCase):
    def setUp(self):
        keepalive._started = False

    def test_disabled_outside_render(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(keepalive.target_url(), '')
            self.assertFalse(keepalive.start())

    def test_targets_health_endpoint_on_render(self):
        with patch.dict('os.environ', {'RENDER_EXTERNAL_URL': 'https://duka-web.onrender.com/'}, clear=True):
            self.assertEqual(keepalive.target_url(), 'https://duka-web.onrender.com/healthz/')

    def test_override_and_off_switch(self):
        env = {'RENDER_EXTERNAL_URL': 'https://a.onrender.com', 'KEEPALIVE_URL': 'https://dukanibot.com'}
        with patch.dict('os.environ', env, clear=True):
            self.assertEqual(keepalive.target_url(), 'https://dukanibot.com/healthz/')
        with patch.dict('os.environ', {**env, 'KEEPALIVE': '0'}, clear=True):
            self.assertEqual(keepalive.target_url(), '')

    def test_starts_once(self):
        with patch.dict('os.environ', {'RENDER_EXTERNAL_URL': 'https://a.onrender.com'}, clear=True), \
                patch('shop.keepalive.threading.Thread') as thread:
            self.assertTrue(keepalive.start())
            self.assertFalse(keepalive.start())
            self.assertEqual(thread.call_count, 1)

    def test_failed_ping_is_swallowed(self):
        with patch('shop.keepalive.urllib.request.urlopen', side_effect=OSError('down')):
            keepalive._ping('https://a.onrender.com/healthz/')
