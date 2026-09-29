import hashlib
import hmac
import io
import json
import tempfile
import time
from unittest.mock import patch
from cryptography.fernet import Fernet
from PIL import Image
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase,override_settings
from django.utils import timezone
from .models import Shop,Product,ProductImage,Conversation,Message,Order,Connection,Job,WebhookEvent
from .secrets import seal,reveal
from .providers import ProviderError,sales_reply
from .workflow import checkout,receive,verify_payment,agent_job
from .connections import enqueue

@override_settings(CREDENTIAL_ENCRYPTION_KEY=Fernet.generate_key().decode())
class IntegrationTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('merchant')
        self.shop=Shop.objects.create(owner=self.user,name='Test shop',delivery=2000)
        self.product=Product.objects.create(shop=self.shop,name='Linen shirt',description='White, size M',price=20000,stock=5)
        self.connection=Connection.objects.create(shop=self.shop,public_url='https://shop.example.com',ghala_token=seal('ghala-example'),ghala_secret=seal('ghala-secret'),snippe_token=seal('snippe-example'),snippe_secret=seal('snippe-secret'),openai_key=seal('ai-example'),agent_enabled=True,mapping_confirmed=True,ghala_auto_reply_disabled=True,ghala_map={'phone':'data.from','text':'data.text','message_id':'data.id','timestamp':'data.timestamp'})
        self.client.force_login(self.user)
    def post(self,url,data):
        return self.client.post(url,json.dumps(data),content_type='application/json')
    def conversation(self):
        return Conversation.objects.create(shop=self.shop,name='Customer',phone='255712345678',is_demo=False,last_inbound_at=timezone.now(),preview='Hello')
    def order(self):
        return Order.objects.create(shop=self.shop,conversation=self.conversation(),name='Customer',item=self.product.name,total=22000,is_demo=False,unit_price=20000,delivery_fee=2000,product=self.product)
    def event(self,text='Hello',external='msg-1'):
        return WebhookEvent.objects.create(shop=self.shop,provider='ghala',delivery_id=external,event_type='message.received',payload={'data':{'from':'255712345678','text':text,'id':external,'timestamp':int(time.time())}})
    def signed_post(self,provider,payload,delivery='delivery-1',timestamp=None,secret=None):
        raw=json.dumps(payload).encode()
        stamp=str(timestamp or int(time.time()))
        sig=hmac.new((secret or provider+'-secret').encode(),stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        headers={'HTTP_X_GHALA_SIGNATURE':'sha256='+sig,'HTTP_X_GHALA_TIMESTAMP':stamp,'HTTP_X_GHALA_DELIVERY':delivery,'HTTP_X_GHALA_EVENT':'message.received'} if provider=='ghala' else {'HTTP_X_WEBHOOK_SIGNATURE':sig,'HTTP_X_WEBHOOK_TIMESTAMP':stamp}
        return self.client.post(f'/webhooks/{provider}/{self.connection.webhook_id}/',raw,content_type='application/json',**headers)
    def test_secrets_are_encrypted_write_only_and_partial_saves_keep_keys(self):
        response=self.post('/api/connections/',{'ghala_token':'new-secret','agent_name':'Asha'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn('new-secret',response.content.decode())
        self.connection.refresh_from_db()
        self.assertNotEqual(self.connection.ghala_token,'new-secret')
        self.assertEqual(reveal(self.connection.ghala_token),'new-secret')
        self.post('/api/connections/',{'ghala_token':''})
        self.connection.refresh_from_db()
        self.assertEqual(reveal(self.connection.ghala_token),'new-secret')
        self.assertEqual(self.post('/api/connections/',{'public_url':'http://localhost:8000'}).status_code,400)
        self.assertEqual(self.post('/api/connections/',{'agent_enabled':True,'mapping_confirmed':False}).status_code,400)
    def test_signed_webhook_tampering_freshness_and_deduplication(self):
        payload={'data':{'from':'255712345678','text':'Hello','id':'m1','timestamp':int(time.time())}}
        self.assertEqual(self.signed_post('ghala',payload,secret='wrong').status_code,401)
        self.assertEqual(self.signed_post('ghala',payload,timestamp=int(time.time())-600).status_code,401)
        for _ in range(2):
            self.assertEqual(self.signed_post('ghala',payload).status_code,200)
        self.assertEqual(WebhookEvent.objects.count(),1)
        self.assertEqual(Job.objects.count(),1)
        self.assertEqual(Conversation.objects.count(),0) # The request only queues work.
        receive(WebhookEvent.objects.first(),self.connection)
        receive(WebhookEvent.objects.first(),self.connection)
        self.assertEqual(Message.objects.count(),1)
        self.assertEqual(Job.objects.filter(kind='agent').count(),1)
    def test_unconfirmed_mapping_fails_without_guessing(self):
        self.connection.mapping_confirmed=False
        with self.assertRaises(ProviderError):
            receive(self.event(),self.connection)
        self.assertEqual(Conversation.objects.count(),0)
    def test_photo_is_reencoded_persists_and_rejects_non_images(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(MEDIA_ROOT=folder):
            buffer=io.BytesIO();Image.new('RGB',(80,60),'green').save(buffer,'PNG')
            response=self.client.post('/api/products/',{'name':'Photo shirt','price':1000,'stock':2,'description':'Cotton','image':SimpleUploadedFile('unsafe-name.png',buffer.getvalue(),content_type='image/png')})
            self.assertEqual(response.status_code,201,response.content)
            product=Product.objects.get(id=response.json()['id'])
            self.assertTrue(product.image.name.endswith('.jpg'))
            self.assertNotIn('unsafe-name',product.image.name)
            with product.image.open('rb') as image:
                self.assertEqual(Image.open(image).format,'JPEG')
            bad=self.client.post(f'/api/products/{product.id}/photo/',{'image':SimpleUploadedFile('fake.png',b'not a picture',content_type='image/png')})
            self.assertEqual(bad.status_code,400)
            huge=self.client.post(f'/api/products/{product.id}/photo/',{'image':SimpleUploadedFile('large.jpg',b'x'*(5*1024*1024+1),content_type='image/jpeg')})
            self.assertEqual(huge.status_code,400)
    def test_product_gallery_requires_cover_caps_at_15_and_scopes_deletes(self):
        with tempfile.TemporaryDirectory() as folder,override_settings(MEDIA_ROOT=folder):
            def photo(name='p.png'):
                buffer=io.BytesIO();Image.new('RGB',(80,60),'blue').save(buffer,'PNG')
                return SimpleUploadedFile(name,buffer.getvalue(),content_type='image/png')
            self.assertEqual(self.client.post(f'/api/products/{self.product.id}/photos/',{'image':photo()}).status_code,400)
            self.client.post(f'/api/products/{self.product.id}/photo/',{'image':photo('cover.png')})
            added=self.client.post(f'/api/products/{self.product.id}/photos/',{'image':photo()})
            self.assertEqual(added.status_code,200,added.content)
            photo_id=added.json()['id']
            self.assertEqual(self.client.get('/api/state/').json()['products'][0]['gallery'][0]['id'],photo_id)
            for _ in range(13):
                self.assertEqual(self.client.post(f'/api/products/{self.product.id}/photos/',{'image':photo()}).status_code,200)
            self.assertEqual(ProductImage.objects.filter(product=self.product).count(),14)
            self.assertEqual(self.client.post(f'/api/products/{self.product.id}/photos/',{'image':photo()}).status_code,400)
            other=User.objects.create_user('gallery-other');Shop.objects.create(owner=other)
            self.client.force_login(other)
            self.assertEqual(self.client.post(f'/api/products/{self.product.id}/photos/{photo_id}/delete/',{}).status_code,404)
            self.client.force_login(self.user)
            self.assertEqual(self.client.post(f'/api/products/{self.product.id}/photos/{photo_id}/delete/',{}).status_code,200)
            self.assertEqual(ProductImage.objects.filter(product=self.product).count(),13)
    @patch('shop.workflow.call')
    def test_checkout_uses_sessions_contract_and_stable_key(self,provider):
        order=self.order()
        provider.return_value={'data':{'reference':'sess_123','checkout_url':'https://snippe.me/checkout/123'}}
        url=checkout(order,self.connection)
        self.assertEqual(url,'https://snippe.me/checkout/123')
        self.assertEqual(provider.call_args.args[:2],('Snippe','/api/v1/sessions'))
        payload=provider.call_args.args[3]
        self.assertEqual(payload['amount'],order.total)
        self.assertEqual(payload['metadata']['payment_key'],str(order.payment_key))
        self.assertEqual(provider.call_args.kwargs['key'],order.payment_key)
        self.assertEqual(checkout(order,self.connection),url)
        self.assertEqual(provider.call_count,1)
        order.is_demo=True
        with self.assertRaises(ProviderError):checkout(order,self.connection)
    def payment_event(self,order):
        return WebhookEvent.objects.create(shop=self.shop,provider='snippe',delivery_id='evt_1',event_type='payment.completed',payload={'id':'evt_1','type':'payment.completed','data':{'reference':'pi_1','status':'completed','amount':{'value':order.total,'currency':'TZS'},'metadata':{'shop_id':str(self.shop.id),'order_id':str(order.id),'payment_key':str(order.payment_key)}}})
    @patch('shop.workflow.call')
    def test_payment_requires_matching_amount_and_server_verification(self,provider):
        order=self.order();order.checkout_reference='sess_1';order.save()
        event=self.payment_event(order)
        provider.return_value={'data':{'reference':'sess_1','status':'pending','amount':order.total,'currency':'TZS','metadata':{'payment_key':str(order.payment_key)}}}
        with self.assertRaises(ProviderError):verify_payment(event,self.connection)
        order.refresh_from_db();self.assertEqual(order.status,'Pending payment')
        provider.return_value['data']['status']='completed'
        event.payload['data']['amount']['value']=1
        with self.assertRaises(ProviderError):verify_payment(event,self.connection)
        event.payload['data']['amount']['value']=order.total
        for _ in range(2):verify_payment(event,self.connection)
        order.refresh_from_db();self.assertEqual(order.status,'Paid')
        self.assertEqual(Job.objects.filter(key=f'paid-{order.id}').count(),1)
    def test_snippe_signature_and_live_payment_cannot_be_simulated(self):
        order=self.order()
        self.assertEqual(self.post(f'/api/conversations/{order.conversation_id}/payment/',{}).status_code,400)
        payload=self.payment_event(order).payload
        WebhookEvent.objects.all().delete()
        self.assertEqual(self.signed_post('snippe',payload).status_code,200)
        self.assertEqual(self.signed_post('snippe',payload).status_code,200)
        self.assertEqual(WebhookEvent.objects.count(),1)
    @patch('shop.workflow.call')
    @patch('shop.workflow.sales_reply')
    def test_agent_quotes_then_confirmation_reserves_stock_once(self,ai,provider):
        c=self.conversation()
        m=Message.objects.create(conversation=c,direction='in',body='One linen shirt to Sinza please')
        ai.return_value={'reply':'Okay','intent':'offer_order','product_id':self.product.id,'quantity':1,'delivery_address':'Sinza'}
        provider.return_value={'id':'sent_1'}
        job=enqueue(self.shop,'agent',f'agent-{m.id}',{'conversation_id':c.id,'message_id':m.id})
        agent_job(job,self.connection)
        c.refresh_from_db();self.assertEqual(c.quote['unit_price'],20000)
        self.assertFalse(Order.objects.exists())
        self.assertIn('CONFIRM',Message.objects.filter(direction='out').first().body)
        m2=Message.objects.create(conversation=c,direction='in',body='THIBITISHA')
        job2=enqueue(self.shop,'agent',f'agent-{m2.id}',{'conversation_id':c.id,'message_id':m2.id})
        def response(name,path,*args,**kwargs):
            return {'data':{'reference':'sess_new','checkout_url':'https://snippe.me/checkout/new'}} if name=='Snippe' else {'id':'sent_2'}
        provider.side_effect=response
        agent_job(job2,self.connection);agent_job(job2,self.connection)
        self.assertEqual(Order.objects.count(),1)
        self.product.refresh_from_db();self.assertEqual(self.product.stock,4)
        self.assertEqual(Order.objects.get().status,'Pending payment')
        self.assertEqual(ai.call_count,1)
        self.assertEqual(Message.objects.filter(direction='out').count(),2)
    @patch('shop.workflow.sales_reply')
    def test_handover_and_stale_message_stop_agent(self,ai):
        c=self.conversation();c.human=True;c.save()
        m=Message.objects.create(conversation=c,direction='in',body='Hello')
        job=enqueue(self.shop,'agent','paused',{'conversation_id':c.id,'message_id':m.id})
        agent_job(job,self.connection);ai.assert_not_called()
        c.human=False;c.save()
        Message.objects.create(conversation=c,direction='in',body='New request')
        agent_job(job,self.connection);ai.assert_not_called()
    @patch('shop.workflow.call')
    @patch('shop.workflow.sales_reply')
    def test_failed_quote_delivery_cannot_be_confirmed(self,ai,provider):
        c=self.conversation()
        m=Message.objects.create(conversation=c,direction='in',body='One shirt to Sinza')
        ai.return_value={'reply':'Okay','intent':'offer_order','product_id':self.product.id,'quantity':1,'delivery_address':'Sinza'}
        provider.side_effect=ProviderError('Ghala unavailable')
        job=enqueue(self.shop,'agent','failed-quote',{'conversation_id':c.id,'message_id':m.id})
        with self.assertRaises(ProviderError):agent_job(job,self.connection)
        c.refresh_from_db();self.assertEqual(c.quote,{})
        self.assertFalse(Order.objects.exists())
    @patch('shop.workflow.call')
    def test_payment_metadata_cannot_mark_another_shop_order_paid(self,provider):
        order=self.order();order.checkout_reference='sess_1';order.save()
        event=self.payment_event(order)
        other=User.objects.create_user('payment-other');shop=Shop.objects.create(owner=other)
        foreign=Connection.objects.create(shop=shop)
        with self.assertRaises(ProviderError):verify_payment(event,foreign)
        order.refresh_from_db();self.assertEqual(order.status,'Pending payment')
        provider.assert_not_called()
    @patch('shop.providers.call')
    def test_real_ai_contract_and_foreign_product_rejection(self,provider):
        result={'reply':'Here you are','intent':'offer_order','product_id':999999,'quantity':1,'delivery_address':'Sinza'}
        provider.return_value={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]}
        with self.assertRaises(ProviderError):sales_reply(self.connection,[{'role':'user','content':'Order please'}])
        payload=provider.call_args.args[3]
        self.assertFalse(payload['store'])
        self.assertEqual(payload['text']['format']['type'],'json_schema')
        self.assertIn('Linen shirt',payload['instructions'])
    def test_cross_shop_access_and_failed_jobs_are_visible_and_retryable(self):
        other=User.objects.create_user('othermerchant');other_shop=Shop.objects.create(owner=other)
        foreign=Product.objects.create(shop=other_shop,name='Private',price=1000)
        self.assertEqual(self.client.post(f'/api/products/{foreign.id}/photo/',{}).status_code,404)
        c=Conversation.objects.create(shop=other_shop,name='Private',phone='255712345679',is_demo=False)
        self.assertEqual(self.post(f'/api/live/{c.id}/send/',{'text':'hello'}).status_code,404)
        self.connection.mapping_confirmed=False;self.connection.save()
        event=self.event();job=enqueue(self.shop,'webhook','failed-event',{'event_id':event.id})
        call_command('process_jobs',once=True,stdout=io.StringIO(),stderr=io.StringIO())
        job.refresh_from_db();self.assertEqual(job.status,'failed')
        self.assertIn('mapping',self.client.get('/api/connections/').json()['jobs'][0]['error'])
        self.assertEqual(self.post(f'/api/jobs/{job.id}/retry/',{}).status_code,200)
        job.refresh_from_db();self.assertEqual(job.status,'pending')
