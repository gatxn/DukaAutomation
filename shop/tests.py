import io
import json
import tempfile
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from PIL import Image
from .models import Shop, Product, Conversation, Message, Order
from .seed import seed_demo

class WorkspaceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner',password='Not-a-real-password-918!')
        self.other = User.objects.create_user('other',password='Not-a-real-password-919!')
        self.shop = Shop.objects.create(owner=self.owner)
        self.other_shop = Shop.objects.create(owner=self.other)
        seed_demo(self.shop)
        seed_demo(self.other_shop)
        self.client.force_login(self.owner)

    def post(self,url,data=None):
        return self.client.post(url,data=json.dumps(data or {}),content_type='application/json')

    def test_account_isolation_and_anonymous_access(self):
        state = self.client.get('/api/state/').json()
        self.assertEqual(len(state['products']),3)
        foreign = Conversation.objects.filter(shop=self.other_shop).first()
        for action in ['reply','order','payment','handover']:
            self.assertEqual(self.post(f'/api/conversations/{foreign.id}/{action}/').status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get('/api/state/').status_code,401)
        self.assertEqual(self.client.get('/').status_code,302)

    def test_product_persists_and_validates(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=folder):
            buffer=io.BytesIO();Image.new('RGB',(80,60),'green').save(buffer,'PNG')
            photo=lambda: SimpleUploadedFile('p.png',buffer.getvalue(),content_type='image/png')
            self.assertEqual(self.client.post('/api/products/',{'name':'Test bag','price':15000,'stock':4,'image':photo()}).status_code,201)
            self.assertEqual(Product.objects.get(shop=self.shop,name='Test bag').price,15000)
            missing_photo=self.post('/api/products/',{'name':'No photo','price':1000,'stock':1})
            self.assertEqual(missing_photo.status_code,400)
            self.assertIn('image',missing_photo.json()['fields'])
            self.assertEqual(self.client.post('/api/products/',{'name':'Bad','price':-1,'stock':3,'image':photo()}).status_code,400)
            self.assertEqual(self.client.post('/api/products/',{'name':'','price':1,'stock':3,'image':photo()}).status_code,400)
        self.assertEqual(self.post('/api/products/',{'name':'Fraction','price':1,'stock':1.5}).status_code,400)

    def test_order_and_payment_are_idempotent(self):
        c = Conversation.objects.filter(shop=self.shop).first()
        previous_stock = c.product.stock
        for _ in range(2):
            self.assertEqual(self.post(f'/api/conversations/{c.id}/order/').status_code,200)
        self.assertEqual(Order.objects.filter(conversation=c).count(),1)
        c.product.refresh_from_db()
        self.assertEqual(c.product.stock,previous_stock-1)
        order = Order.objects.get(conversation=c)
        self.assertEqual(order.total,c.product.price+self.shop.delivery)
        for _ in range(2):
            self.assertEqual(self.post(f'/api/conversations/{c.id}/payment/').status_code,200)
        order.refresh_from_db()
        self.assertEqual(order.status,'Paid')
        self.assertEqual(Message.objects.filter(conversation=c,body__contains='Demo payment confirmed').count(),1)

    def test_out_of_stock_blocks_order(self):
        c = Conversation.objects.filter(shop=self.shop).first()
        Product.objects.filter(id=c.product_id).update(stock=0)
        self.assertEqual(self.post(f'/api/conversations/{c.id}/order/').status_code,409)
        self.assertFalse(Order.objects.filter(conversation=c).exists())

    def test_csrf_required_and_malformed_input_rejected(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        self.assertEqual(strict.post('/api/products/',data='{}',content_type='application/json').status_code,403)
        self.assertEqual(self.client.post('/api/products/',data='[1]',content_type='application/json').status_code,400)

    def test_settings_handover_and_login_pages(self):
        self.assertEqual(self.post('/api/settings/',{'name':'New shop','city':'Arusha','language':'Swahili','delivery':2000}).status_code,200)
        self.assertEqual(self.client.get('/api/state/').json()['business']['city'],'Arusha')
        c = Conversation.objects.filter(shop=self.shop).first()
        self.assertEqual(self.post(f'/api/conversations/{c.id}/handover/',{'human':True}).status_code,200)
        c.refresh_from_db()
        self.assertTrue(c.human)
        self.assertEqual(self.client.get('/').status_code,200)
        self.client.logout()
        self.assertEqual(self.client.get('/login/').status_code,200)
        self.assertEqual(self.client.get('/signup/').status_code,200)

    def test_signup_creates_private_sample_shop(self):
        self.client.logout()
        response=self.client.post('/signup/',{'username':'newmerchant','password1':'A-unique-demo-pass-785!','password2':'A-unique-demo-pass-785!'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(len(self.client.get('/api/state/').json()['products']),3)
        self.assertTrue(Shop.objects.filter(owner__username='newmerchant').exists())
