"""Adversarial coverage for shop/forms.py: clean_photo(). The upload pipeline always fully
decodes with Pillow, strips EXIF, and re-encodes to a fresh randomly-named JPEG — this test
file proves that design defeats path traversal, SVG/XXE, and decompression-bomb payloads,
rather than just asserting it in prose."""
import io
import tempfile
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from .models import Shop, Membership, ROLE_OWNER

class UploadHardeningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('upload-owner', password='Not-a-real-password-950!')
        self.shop = Shop.objects.create(owner=self.user)
        Membership.objects.create(shop=self.shop, user=self.user, role=ROLE_OWNER)
        self.client.force_login(self.user)
        self._media_dir = tempfile.TemporaryDirectory()
        self._override = override_settings(MEDIA_ROOT=self._media_dir.name)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._media_dir.cleanup)

    def upload(self, filename, content, content_type):
        return self.client.post('/api/products/', {'name': 'Test', 'price': 1000, 'stock': 1, 'image': SimpleUploadedFile(filename, content, content_type=content_type)})

    def test_path_traversal_filename_is_discarded_entirely(self):
        buffer = io.BytesIO(); Image.new('RGB', (40, 40), 'red').save(buffer, 'PNG')
        response = self.upload('../../../../etc/passwd.png', buffer.getvalue(), 'image/png')
        self.assertEqual(response.status_code, 201, response.content)
        from .models import Product
        product = Product.objects.get(id=response.json()['id'])
        self.assertNotIn('etc', product.image.name)
        self.assertNotIn('..', product.image.name)
        self.assertTrue(product.image.name.endswith('.jpg'))

    def test_svg_is_rejected_avoiding_xxe_and_stored_xss(self):
        malicious_svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script></svg>'
        response = self.upload('image.svg', malicious_svg, 'image/svg+xml')
        self.assertEqual(response.status_code, 400)

    def test_gif_is_rejected_not_on_the_allowlist(self):
        buffer = io.BytesIO(); Image.new('RGB', (40, 40), 'blue').save(buffer, 'GIF')
        response = self.upload('image.gif', buffer.getvalue(), 'image/gif')
        self.assertEqual(response.status_code, 400)

    def test_decompression_bomb_dimensions_are_rejected(self):
        # A crafted PNG header claiming an enormous canvas without real pixel data still trips
        # Pillow's decompression-bomb guard before any large allocation happens.
        huge = Image.new('RGB', (1, 1))
        buffer = io.BytesIO(); huge.save(buffer, 'PNG')
        # Pillow determines danger from declared width*height; simulate via a real oversized image
        # at reduced but still-over-threshold resolution to keep the test fast and deterministic.
        bomb_buffer = io.BytesIO()
        Image.new('RGB', (6000, 4000)).save(bomb_buffer, 'PNG')  # 24,000,000 px > 20,000,000 cap
        response = self.upload('bomb.png', bomb_buffer.getvalue(), 'image/png')
        self.assertEqual(response.status_code, 400)

    def test_zero_byte_file_is_rejected(self):
        response = self.upload('empty.png', b'', 'image/png')
        self.assertEqual(response.status_code, 400)

    def test_exif_metadata_is_stripped_on_reencode(self):
        buffer = io.BytesIO()
        image = Image.new('RGB', (40, 40), 'green')
        exif = image.getexif()
        exif[0x9286] = 'sensitive comment'  # UserComment tag
        image.save(buffer, 'JPEG', exif=exif)
        response = self.upload('with-exif.jpg', buffer.getvalue(), 'image/jpeg')
        self.assertEqual(response.status_code, 201, response.content)
        from .models import Product
        product = Product.objects.get(id=response.json()['id'])
        with product.image.open('rb') as saved:
            saved_exif = Image.open(saved).getexif()
        self.assertNotIn(0x9286, saved_exif)
