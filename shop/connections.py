import hashlib
import hmac
import ipaddress
import json
import re
import time
import uuid
from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from .models import Connection, Conversation, Product, ProductImage, Job, WebhookEvent, Order, Membership, MAX_PRODUCT_IMAGES, ROLE_OWNER, ROLE_MANAGER, ROLE_AGENT
from .views import api
from .forms import clean_photo
from .secrets import seal, reveal
from .permissions import shop_for_user
from .audit import log_action
from .providers import call, sales_reply, ProviderError
from . import inline

SECRET_FIELDS = ('ghala_token','ghala_secret','snippe_token','snippe_secret','openai_key','meta_token')
MAP_FIELDS = ('phone','text','message_id','timestamp','name')

def connection_for(user, min_role=ROLE_AGENT):
    shop = shop_for_user(user, min_role=min_role)
    return Connection.objects.get_or_create(shop=shop,defaults={'model':settings.AI_MODEL})[0]

def public_url(value):
    URLValidator(schemes=['https'])(value)
    parsed = urlsplit(value)
    host = parsed.hostname or ''
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/') or host=='localhost' or host.endswith(('.localhost','.local')) or '.' not in host:
        raise ValidationError('Use your public HTTPS domain, without a path or query.')
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValidationError('Use a public HTTPS domain.')
    except ValueError:
        pass
    return value.rstrip('/')

def status(c):
    jobs = list(Job.objects.filter(shop=c.shop).order_by('-id').values('id','kind','status','attempts','error','created_at')[:15])
    return {'public_url':c.public_url,'configured':{key:bool(getattr(c,key)) for key in SECRET_FIELDS},'ghala_map':c.ghala_map,'mapping_confirmed':c.mapping_confirmed,'ghala_subscription':c.ghala_subscription,'meta_phone_number_id':c.meta_phone_number_id,'meta_webhook':f'{c.public_url}/webhooks/meta/' if c.public_url else '','agent_name':c.agent_name,'agent_policies':c.agent_policies,'agent_enabled':c.agent_enabled,'ghala_auto_reply_disabled':c.ghala_auto_reply_disabled,'model':c.model,'worker_active':bool(c.worker_heartbeat and (timezone.now()-c.worker_heartbeat).total_seconds()<90),'jobs':jobs,'webhooks':{provider:f'{c.public_url}/webhooks/{provider}/{c.webhook_id}/' for provider in ('ghala','snippe')}}

@require_http_methods(['GET','POST'])
@api
def settings_api(request):
    c = connection_for(request.user, min_role=ROLE_AGENT if request.method=='GET' else ROLE_MANAGER)
    if request.method=='GET':
        return JsonResponse(status(c))
    data = request.payload
    try:
        for key in SECRET_FIELDS:
            value = data.get(key,'')
            if not isinstance(value,str) or len(value)>10000:
                raise ValidationError('Invalid credential value.')
            if value.strip():
                if '\n' in value or '\r' in value:
                    raise ValidationError('API credentials must be on one line.')
                setattr(c,key,seal(value.strip()))
        for key in ('agent_name','agent_policies'):
            if key in data:
                value = data[key]
                if not isinstance(value,str) or len(value)>(80 if key=='agent_name' else 6000):
                    raise ValidationError('Assistant name or instructions are too long.')
                setattr(c,key,value)
        if 'meta_phone_number_id' in data:
            number_id = data['meta_phone_number_id']
            if not isinstance(number_id,str) or (number_id.strip() and not re.fullmatch(r'\d{5,40}',number_id.strip())):
                raise ValidationError('The WhatsApp phone number ID is a number, shown in Meta under WhatsApp → API setup.')
            c.meta_phone_number_id = number_id.strip()
        if 'public_url' in data:
            c.public_url = public_url(data['public_url']) if data['public_url'] else ''
        for key in ('agent_enabled','mapping_confirmed','ghala_auto_reply_disabled'):
            if key in data:
                if not isinstance(data[key],bool):
                    raise ValidationError('Invalid switch value.')
                setattr(c,key,data[key])
        if 'ghala_map' in data:
            mapping = data['ghala_map']
            if not isinstance(mapping,dict) or set(mapping)-set(MAP_FIELDS) or any(not isinstance(v,str) or len(v)>200 for v in mapping.values()):
                raise ValidationError('Use valid JSON field paths for Ghala events.')
            c.ghala_map = mapping
        if c.mapping_confirmed and any(not c.ghala_map.get(k) for k in ('phone','text','message_id','timestamp')):
            raise ValidationError('Set phone, text, message ID and timestamp field paths before confirming the mapping.')
        if c.agent_enabled and not (c.openai_key and (c.uses_meta or (c.ghala_token and c.ghala_secret and c.mapping_confirmed and c.ghala_auto_reply_disabled and c.public_url))):
            raise ValidationError('To enable live replies, add your AI key and connect WhatsApp: either the Meta number ID and token, or Ghala credentials, a public URL, verified field mapping, and Ghala native auto-replies turned off.')
        if c.uses_meta and ({'meta_token','meta_phone_number_id'} & set(data)):
            # Proves the token really controls this number before inbound messages are routed to this shop.
            if Connection.objects.filter(meta_phone_number_id=c.meta_phone_number_id).exclude(pk=c.pk).exists():
                raise ValidationError('That WhatsApp number is already connected to another shop.')
            try:
                call('Meta',f'/{settings.WHATSAPP_GRAPH_VERSION}/{c.meta_phone_number_id}?fields=display_phone_number',c.meta_token)
            except ProviderError as exc:
                raise ValidationError(f'Could not confirm this WhatsApp number with Meta. {exc}') from None
        c.save()
    except (ValidationError,ValueError) as exc:
        return JsonResponse({'error':' '.join(exc.messages) if isinstance(exc,ValidationError) else str(exc)},status=400)
    log_action(request,'connection_settings_updated',shop=c.shop)
    return JsonResponse(status(c))

@require_POST
@api
def register_ghala(request):
    c = connection_for(request.user, min_role=ROLE_MANAGER)
    if not c.public_url:
        return JsonResponse({'error':'Save your public HTTPS URL first.'},status=400)
    if c.ghala_subscription:
        return JsonResponse({'error':'A webhook is already registered. Manage or replace it in Ghala Developer → Webhooks.'},status=409)
    try:
        result = call('Ghala','/api/v2/webhooks',c.ghala_token,{'url':status(c)['webhooks']['ghala'],'events':['message.received','message.status'],'description':'Duka WhatsApp commerce'},key=f'duka-webhook-{c.webhook_id}')
        if not result.get('secret') or not result.get('id'):
            raise ProviderError('Ghala did not return a webhook ID and secret. Check Developer → Webhooks before trying again.')
        c.ghala_secret = seal(result['secret'])
        c.ghala_subscription = str(result['id'])
        c.save(update_fields=['ghala_secret','ghala_subscription'])
        log_action(request,'ghala_webhook_registered',shop=c.shop)
        return JsonResponse({'ok':True})
    except (ProviderError,ValueError) as exc:
        return JsonResponse({'error':str(exc)},status=400)

@require_POST
@api
def product_photo(request,product_id):
    shop = shop_for_user(request.user, min_role=ROLE_MANAGER)
    product = get_object_or_404(Product,id=product_id,shop=shop)
    try:
        upload = request.FILES.get('image')
        if not upload:
            raise ValidationError('Choose a photo first.')
        product.image = clean_photo(upload)
        product.save(update_fields=['image'])
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)},status=400)
    return JsonResponse({'image':product.image.url})

@require_POST
@api
def product_gallery(request,product_id):
    shop = shop_for_user(request.user, min_role=ROLE_MANAGER)
    product = get_object_or_404(Product,id=product_id,shop=shop)
    try:
        if not product.image:
            raise ValidationError('Add a cover photo first.')
        upload = request.FILES.get('image')
        if not upload:
            raise ValidationError('Choose a photo first.')
        position = 1 + product.gallery.count()
        if position >= MAX_PRODUCT_IMAGES:
            raise ValidationError(f'You can add up to {MAX_PRODUCT_IMAGES} photos per product.')
        photo = ProductImage.objects.create(product=product,image=clean_photo(upload),position=position)
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)},status=400)
    return JsonResponse({'id':photo.id,'image':photo.image.url})

@require_POST
@api
def delete_gallery_photo(request,product_id,photo_id):
    shop = shop_for_user(request.user, min_role=ROLE_MANAGER)
    get_object_or_404(ProductImage,id=photo_id,product_id=product_id,product__shop=shop).delete()
    return JsonResponse({'ok':True})

@require_POST
@api
def preview_agent(request):
    c = connection_for(request.user, min_role=ROLE_MANAGER)
    message = request.payload.get('message','')
    if not isinstance(message,str) or not 1<=len(message.strip())<=2000:
        return JsonResponse({'error':'Write a message of up to 2,000 characters.'},status=400)
    history = request.session.get('agent_preview',[])[-18:]
    history.append({'role':'user','content':message})
    try:
        result = sales_reply(c,history)
        reply = result['reply']
        if result['intent']=='offer_order':
            p = Product.objects.get(shop=c.shop,id=result['product_id'])
            reply += f'\n\nPreview quote: {result["quantity"]} × {p.name}. Total with delivery: TZS {p.price*result["quantity"]+c.shop.delivery:,}. No order or payment is created in this test.'
        history.append({'role':'assistant','content':reply})
        request.session['agent_preview'] = history
        return JsonResponse({'reply':reply,'intent':result['intent']})
    except ProviderError as exc:
        return JsonResponse({'error':str(exc)},status=400)

def enqueue(shop,kind,key,payload):
    return Job.objects.get_or_create(key=key,defaults={'shop':shop,'kind':kind,'payload':payload,'available_at':timezone.now()})[0]

@require_POST
@api
def send_message(request,contact_id):
    c = connection_for(request.user, min_role=ROLE_AGENT)
    conversation = get_object_or_404(Conversation,id=contact_id,shop=c.shop,is_demo=False)
    body = request.payload.get('text','')
    if not isinstance(body,str) or not 1<=len(body.strip())<=4000:
        return JsonResponse({'error':'Write a message of up to 4,000 characters.'},status=400)
    if not (c.ghala_token or c.uses_meta):
        return JsonResponse({'error':'Connect WhatsApp in Settings first.'},status=400)
    # Manual replies pause the assistant before the message is queued.
    Conversation.objects.filter(id=conversation.id).update(human=True,quote={})
    payload = {'conversation_id':conversation.id,'text':body.strip(),'manual':True}
    product_id = request.payload.get('product_id')
    if product_id:
        p = get_object_or_404(Product,id=product_id,shop=conversation.shop)
        if not p.image or not c.public_url:
            return JsonResponse({'error':'Add a product photo and public HTTPS URL first.'},status=400)
        payload['media_url'] = c.public_url+p.image.url
    job = enqueue(conversation.shop,'send',f'manual-{uuid.uuid4()}',payload)
    return JsonResponse({'queued':True,'job_id':job.id})

@require_POST
@api
def checkout_api(request,order_id):
    from .workflow import checkout
    c = connection_for(request.user, min_role=ROLE_MANAGER)
    order = get_object_or_404(Order,id=order_id,shop=c.shop,is_demo=False)
    try:
        return JsonResponse({'url':checkout(order,c)})
    except ProviderError as exc:
        return JsonResponse({'error':str(exc)},status=400)

@require_POST
@api
def order_action(request,order_id,action):
    shop = shop_for_user(request.user, min_role=ROLE_MANAGER)
    order = get_object_or_404(Order,id=order_id,shop=shop)
    try:
        if action=='ready':
            ok = order.mark_ready_for_delivery()
        elif action=='delivered':
            ok = order.mark_delivered()
        elif action=='cancel':
            ok = order.cancel()
        else:
            return JsonResponse({'error':'Unknown action.'},status=404)
    except ValueError as exc:
        return JsonResponse({'error':str(exc)},status=400)
    if ok:
        log_action(request,f'order_{action}',target=order,shop=shop)
    return JsonResponse({'ok':True})

@require_POST
@api
def retry_job(request,job_id):
    shop = shop_for_user(request.user, min_role=ROLE_MANAGER)
    job = get_object_or_404(Job,id=job_id,shop=shop,status='failed')
    job.status='pending'
    job.attempts=0
    job.error=''
    job.available_at=timezone.now()
    job.save(update_fields=['status','attempts','error','available_at'])
    log_action(request,'job_retried',target=job,shop=shop)
    return JsonResponse({'ok':True})

@require_http_methods(['GET','POST'])
@api
def staff_api(request):
    shop = shop_for_user(request.user, min_role=ROLE_OWNER)
    if request.method == 'GET':
        rows = list(Membership.objects.filter(shop=shop).select_related('user').order_by('id').values('id','user__username','role'))
        return JsonResponse({'staff':[{'id':r['id'],'username':r['user__username'],'role':r['role']} for r in rows]})
    data = request.payload
    username = data.get('username','')
    password = data.get('password','')
    role = data.get('role','')
    if role not in (ROLE_MANAGER, ROLE_AGENT):
        return JsonResponse({'error':'Choose Manager or Agent.'},status=400)
    if not isinstance(username,str) or not 3<=len(username.strip())<=150:
        return JsonResponse({'error':'Choose a username of 3-150 characters.'},status=400)
    username = username.strip()
    if User.objects.filter(username=username).exists():
        return JsonResponse({'error':'That username is already taken.'},status=400)
    if not isinstance(password,str):
        return JsonResponse({'error':'Set a temporary password for this staff member.'},status=400)
    try:
        validate_password(password)
    except ValidationError as exc:
        return JsonResponse({'error':' '.join(exc.messages)},status=400)
    with transaction.atomic():
        user = User.objects.create_user(username=username,password=password)
        membership = Membership.objects.create(shop=shop,user=user,role=role)
    log_action(request,'staff_invited',target=membership,shop=shop,username=username,role=role)
    return JsonResponse({'id':membership.id,'username':username,'role':role},status=201)

@require_POST
@api
def update_staff_role(request,membership_id):
    shop = shop_for_user(request.user, min_role=ROLE_OWNER)
    membership = get_object_or_404(Membership,id=membership_id,shop=shop)
    role = request.payload.get('role','')
    if role not in (ROLE_OWNER, ROLE_MANAGER, ROLE_AGENT):
        return JsonResponse({'error':'Invalid role.'},status=400)
    if membership.role==ROLE_OWNER and role!=ROLE_OWNER and Membership.objects.filter(shop=shop,role=ROLE_OWNER).count()<=1:
        return JsonResponse({'error':'A shop must always have at least one Owner.'},status=400)
    membership.role = role
    membership.save(update_fields=['role'])
    log_action(request,'staff_role_changed',target=membership,shop=shop,new_role=role)
    return JsonResponse({'ok':True})

@require_POST
@api
def remove_staff(request,membership_id):
    shop = shop_for_user(request.user, min_role=ROLE_OWNER)
    membership = get_object_or_404(Membership,id=membership_id,shop=shop)
    if membership.role==ROLE_OWNER and Membership.objects.filter(shop=shop,role=ROLE_OWNER).count()<=1:
        return JsonResponse({'error':'A shop must always have at least one Owner.'},status=400)
    log_action(request,'staff_removed',target=membership,shop=shop,username=membership.user.username)
    membership.delete()
    return JsonResponse({'ok':True})

@csrf_exempt
@require_POST
def webhook(request,provider,webhook_id):
    if provider not in {'ghala','snippe'}:
        return JsonResponse({'error':'Unknown provider.'},status=404)
    c = get_object_or_404(Connection,webhook_id=webhook_id)
    secret = getattr(c,provider+'_secret')
    if not secret:
        return JsonResponse({'error':'Webhook is not configured.'},status=503)
    try:
        raw = request.body
        if len(raw)>1024*1024:
            return JsonResponse({'error':'Payload too large.'},status=413)
        timestamp = request.headers.get('X-Ghala-Timestamp' if provider=='ghala' else 'X-Webhook-Timestamp','')
        signature = request.headers.get('X-Ghala-Signature' if provider=='ghala' else 'X-Webhook-Signature','')
        if abs(time.time()-int(timestamp))>300:
            raise ValueError()
        expected = hmac.new(reveal(secret).encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        if provider=='ghala':
            expected='sha256='+expected
        if not hmac.compare_digest(expected,signature):
            raise ValueError()
        payload=json.loads(raw)
        if not isinstance(payload,dict):
            raise ValueError()
        event_type = request.headers.get('X-Ghala-Event','') if provider=='ghala' else payload.get('type','')
        delivery_id = request.headers.get('X-Ghala-Delivery','') if provider=='ghala' else payload.get('id','')
        if not isinstance(delivery_id,str) or not 1<=len(delivery_id)<=255 or not isinstance(event_type,str) or len(event_type)>80:
            raise ValueError()
    except (ValueError,UnicodeDecodeError):
        return JsonResponse({'error':'Invalid signed event.'},status=401)
    with transaction.atomic():
        event,created = WebhookEvent.objects.get_or_create(shop=c.shop,provider=provider,delivery_id=delivery_id,defaults={'event_type':event_type,'payload':payload})
        if created:
            enqueue(c.shop,'webhook',f'event-{event.id}',{'event_id':event.id})
    return JsonResponse({'received':True})

@csrf_exempt
@require_http_methods(['GET','POST'])
def meta_webhook(request):
    """One webhook for the whole platform, as Meta allows a single callback URL per app. Messages are
    routed to a shop by the receiving number's ID. Verified with the app secret (X-Hub-Signature-256)."""
    if request.method=='GET':
        token = settings.META_WEBHOOK_VERIFY_TOKEN
        if token and request.GET.get('hub.mode')=='subscribe' and hmac.compare_digest(request.GET.get('hub.verify_token',''),token):
            return HttpResponse(request.GET.get('hub.challenge',''),content_type='text/plain')
        return HttpResponse(status=403)
    secret = settings.META_APP_SECRET
    if not secret:
        return JsonResponse({'error':'Webhook is not configured.'},status=503)
    raw = request.body
    if len(raw)>1024*1024:
        return JsonResponse({'error':'Payload too large.'},status=413)
    expected = 'sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,request.headers.get('X-Hub-Signature-256','')):
        return JsonResponse({'error':'Invalid signed event.'},status=401)
    try:
        payload = json.loads(raw)
        if not isinstance(payload,dict) or payload.get('object')!='whatsapp_business_account':
            raise ValueError()
    except (ValueError,UnicodeDecodeError):
        return JsonResponse({'error':'Invalid event.'},status=400)
    queued = False
    for entry in payload.get('entry') or []:
        for change in (entry.get('changes') or []) if isinstance(entry,dict) else []:
            value = change.get('value') if isinstance(change,dict) else None
            if not isinstance(value,dict) or change.get('field')!='messages':
                continue
            number_id = (value.get('metadata') or {}).get('phone_number_id')
            c = Connection.objects.select_related('shop').filter(meta_phone_number_id=number_id).first() if isinstance(number_id,str) and number_id else None
            if not c:
                continue # Unknown number: acknowledge so Meta doesn't keep retrying, but store nothing.
            names = {k.get('wa_id'):(k.get('profile') or {}).get('name') for k in value.get('contacts') or [] if isinstance(k,dict)}
            for message in value.get('messages') or []:
                delivery_id = message.get('id') if isinstance(message,dict) else None
                if not isinstance(delivery_id,str) or not 1<=len(delivery_id)<=255:
                    continue
                with transaction.atomic():
                    event,created = WebhookEvent.objects.get_or_create(shop=c.shop,provider='meta',delivery_id=delivery_id,defaults={'event_type':'message.received','payload':{'message':message,'name':names.get(message.get('from'))}})
                    if created:
                        enqueue(c.shop,'webhook',f'event-{event.id}',{'event_id':event.id})
                        queued = True
    if queued:
        inline.kick()
    return JsonResponse({'received':True})
