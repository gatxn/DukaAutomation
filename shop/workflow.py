import re
from datetime import datetime, timezone as dt_timezone
from urllib.parse import quote, urlsplit
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.core.exceptions import ValidationError
from .models import Conversation, Message, Product, Order, Connection, WebhookEvent
from .providers import call, sales_reply, ProviderError

def https_url(value):
    try:
        u=urlsplit(value)
        return u.scheme=='https' and bool(u.hostname) and not u.username and not u.password
    except (ValueError,TypeError):
        return False

def checkout(order,connection):
    if order.is_demo or not order.conversation or not order.conversation.phone:
        raise ProviderError('Checkout is available for real WhatsApp orders only.')
    if order.payment_status!=Order.PAYMENT_PENDING:
        raise ProviderError('This order is already paid.')
    if order.checkout_url:
        return order.checkout_url
    if not connection.public_url or not connection.snippe_secret:
        raise ProviderError('Add your public URL and Snippe webhook secret in Settings first.')
    if order.total<500:
        raise ProviderError('Snippe checkout requires at least TZS 500.')
    payload={'amount':order.total,'currency':'TZS','allowed_methods':['mobile_money'],'allow_custom_amount':False,'customer':{'name':order.name,'phone':order.conversation.phone},'webhook_url':f'{connection.public_url}/webhooks/snippe/{connection.webhook_id}/','description':f'{connection.shop.name} · Order #{order.id}','metadata':{'order_id':str(order.id),'shop_id':str(order.shop_id),'payment_key':str(order.payment_key)},'expires_in':3600}
    result=call('Snippe','/api/v1/sessions',connection.snippe_token,payload,key=order.payment_key).get('data',{})
    reference=result.get('reference','')
    url=result.get('checkout_url') or result.get('payment_link_url','')
    if not isinstance(reference,str) or not reference or len(reference)>150 or not https_url(url) or len(url)>1000:
        raise ProviderError('Snippe did not return a valid checkout. Check the provider dashboard before retrying.')
    Order.objects.filter(id=order.id).update(checkout_reference=reference,checkout_url=url)
    order.checkout_reference,order.checkout_url=reference,url
    return url

def verify_payment(event,connection):
    if event.event_type!='payment.completed':
        return # Failed/expired events cannot downgrade a confirmed payment.
    data=event.payload.get('data',{})
    metadata=data.get('metadata',{}) if isinstance(data,dict) else {}
    try:
        order=Order.objects.get(shop=connection.shop,is_demo=False,payment_key=metadata.get('payment_key'),id=int(metadata.get('order_id')),checkout_reference__gt='')
    except (ValueError,TypeError,Order.DoesNotExist,ValidationError):
        raise ProviderError('Payment metadata does not match an order in this shop.') from None
    if str(metadata.get('shop_id'))!=str(order.shop_id) or data.get('status')!='completed' or data.get('amount')!={'value':order.total,'currency':'TZS'}:
        raise ProviderError('Payment amount, currency or merchant reference does not match.')
    verified=call('Snippe','/api/v1/sessions/'+quote(order.checkout_reference,safe=''),connection.snippe_token).get('data',{})
    if verified.get('reference')!=order.checkout_reference or verified.get('status')!='completed' or verified.get('amount')!=order.total or verified.get('currency')!='TZS':
        raise ProviderError('Snippe has not verified this checkout as completed for the expected amount.')
    if verified.get('metadata',{}).get('payment_key')!=str(order.payment_key):
        raise ProviderError('Checkout metadata does not match this order.')
    with transaction.atomic():
        if order.mark_paid(payment_reference=str(data.get('reference',''))[:150]):
            from .connections import enqueue
            enqueue(connection.shop,'send',f'paid-{order.id}',{'conversation_id':order.conversation_id,'text':f'Payment confirmed / Malipo yamethibitishwa. Order #{order.id}: TZS {order.total:,}. Thank you!','transactional':True})

def at_path(payload,path):
    value=payload
    for part in path.split('.'):
        if not part or not isinstance(value,dict) or part not in value:
            raise ProviderError('Ghala event does not match the saved field mapping. Review Developer → Events and update Settings.')
        value=value[part]
    return value

def receive(event,connection):
    if event.event_type!='message.received':
        return # Status events retained for review; delivery schema is not assumed.
    if not connection.mapping_confirmed:
        raise ProviderError('Confirm the Ghala incoming-event field mapping in Settings, then retry this event.')
    values={key:at_path(event.payload,path) for key,path in connection.ghala_map.items() if path}
    phone=str(values.get('phone','')).lstrip('+')
    text=values.get('text')
    external=values.get('message_id')
    if not re.fullmatch(r'[1-9]\d{7,14}',phone) or not isinstance(text,str) or not 1<=len(text)<=10000 or not isinstance(external,str) or not 1<=len(external)<=255:
        raise ProviderError('Unsupported message: verify phone, text and message ID paths. Only incoming text is handled automatically.')
    raw_time=values.get('timestamp')
    try:
        sent=datetime.fromtimestamp(float(raw_time),tz=dt_timezone.utc) if isinstance(raw_time,(int,float)) or str(raw_time).isdigit() else parse_datetime(str(raw_time))
        if not sent or timezone.is_naive(sent) or sent>timezone.now()+timezone.timedelta(minutes=5):
            raise ValueError()
    except (ValueError,OverflowError,OSError):
        raise ProviderError('Ghala timestamp must map to Unix seconds or an ISO-8601 date with timezone.') from None
    from .connections import enqueue
    with transaction.atomic():
        conversation,_=Conversation.objects.get_or_create(shop=connection.shop,phone=phone,defaults={'name':str(values.get('name') or phone)[:80],'preview':text[:200],'is_demo':False})
        message,created=Message.objects.get_or_create(conversation=conversation,external_id=external,defaults={'direction':'in','body':text,'delivery_status':'received'})
        if created and (not conversation.last_inbound_at or sent>=conversation.last_inbound_at):
            conversation.last_inbound_at=sent
            conversation.preview=text[:200]
            conversation.save(update_fields=['last_inbound_at','preview'])
            if connection.agent_enabled and not conversation.human:
                enqueue(connection.shop,'agent',f'agent-{message.id}',{'conversation_id':conversation.id,'message_id':message.id})

def quote_text(product,quantity,address,delivery):
    return f'{quantity} × {product.name}\nTZS {product.price*quantity:,} + delivery TZS {delivery:,}\nTotal / Jumla: TZS {product.price*quantity+delivery:,}\nDelivery / Mahali: {address}\nReply CONFIRM or THIBITISHA to place this order, or tell us what to change.'

def agent_job(job,connection):
    conversation=Conversation.objects.get(id=job.payload['conversation_id'],shop=job.shop)
    message=Message.objects.get(id=job.payload['message_id'],conversation=conversation,direction='in')
    latest=conversation.messages.filter(direction='in').order_by('-id').first()
    if conversation.human or not connection.agent_enabled or not connection.ghala_auto_reply_disabled or not latest or latest.id!=message.id:
        return
    if not conversation.last_inbound_at or (timezone.now()-conversation.last_inbound_at).total_seconds()>=23*3600:
        raise ProviderError('The WhatsApp reply window has ended. Use an approved template in Ghala.')
    if not job.result:
        order=Order.objects.filter(shop=job.shop,origin_key=f'inbound-{message.id}').first()
        if order or (message.body.strip().upper() in {'CONFIRM','THIBITISHA'} and conversation.quote):
            if not order:
                with transaction.atomic():
                    conversation=Conversation.objects.select_for_update().get(id=conversation.id)
                    q=conversation.quote
                    if not q or conversation.human:
                        return
                    product=Product.objects.get(id=q['product_id'],shop=job.shop)
                    if product.price!=q['unit_price'] or connection.shop.delivery!=q['delivery'] or (timezone.now().timestamp()-q['created'])>1800:
                        conversation.quote={}
                        conversation.save(update_fields=['quote'])
                        job.result={'text':'The quote has expired or the price changed. Please ask for a new quote before confirming.'}
                    elif not Product.objects.filter(id=product.id,shop=job.shop,stock__gte=q['quantity']).update(stock=F('stock')-q['quantity']):
                        conversation.quote={}
                        conversation.save(update_fields=['quote'])
                        job.result={'text':'Sorry, that quantity is no longer available. Please choose another quantity or product.'}
                    else:
                        order=Order.objects.create(shop=job.shop,conversation=conversation,name=conversation.name,item=product.name,total=product.price*q['quantity']+q['delivery'],is_demo=False,product=product,quantity=q['quantity'],unit_price=product.price,delivery_fee=q['delivery'],delivery_address=q['address'],origin_key=f'inbound-{message.id}',reserved_until=timezone.now()+timezone.timedelta(minutes=30))
                        conversation.quote={}
                        conversation.save(update_fields=['quote'])
            if order:
                text=f'Order #{order.id} reserved / Oda imehifadhiwa. Total: TZS {order.total:,}.'
                if connection.snippe_token:
                    text+='\nPay securely / Lipa hapa: '+checkout(order,connection)
                else:
                    text+='\nThe shop will provide payment instructions. Payment is not yet confirmed.'
                job.result={'text':text}
        else:
            history=[{'role':'user' if m.direction=='in' else 'assistant','content':m.body} for m in reversed(list(conversation.messages.order_by('-id')[:20]))]
            plan=sales_reply(connection,history)
            text=plan['reply']
            with transaction.atomic():
                conversation=Conversation.objects.select_for_update().get(id=conversation.id)
                if conversation.human or conversation.messages.filter(direction='in',id__gt=message.id).exists():
                    return
                conversation.quote={}
                pending_quote={}
                if plan['intent']=='offer_order':
                    product=Product.objects.get(shop=job.shop,id=plan['product_id'])
                    pending_quote={'product_id':product.id,'unit_price':product.price,'quantity':plan['quantity'],'delivery':connection.shop.delivery,'address':plan['delivery_address'],'created':timezone.now().timestamp()}
                    text=quote_text(product,plan['quantity'],plan['delivery_address'],connection.shop.delivery)
                if plan['intent']=='handoff':
                    conversation.human=True
                conversation.save(update_fields=['quote','human'])
                job.result={'text':text,'handoff':plan['intent']=='handoff','quote':pending_quote}
        job.save(update_fields=['result'])
    # A human taking over or a newer message cancels a stale generated answer.
    conversation.refresh_from_db()
    connection.refresh_from_db()
    if not connection.agent_enabled or not connection.ghala_auto_reply_disabled or (conversation.human and not job.result.get('handoff')) or conversation.messages.filter(direction='in',id__gt=message.id).exists():
        return
    send(job,connection,{'conversation_id':conversation.id,'text':job.result['text'],'agent':True})
    if job.result.get('quote'):
        # Only accepted, current offers are eligible for customer confirmation.
        with transaction.atomic():
            current=Conversation.objects.select_for_update().get(id=conversation.id)
            if not current.human and not current.messages.filter(direction='in',id__gt=message.id).exists():
                current.quote=job.result['quote']
                current.save(update_fields=['quote'])

def send(job,connection,payload):
    conversation=Conversation.objects.get(id=payload['conversation_id'],shop=job.shop,is_demo=False)
    if not conversation.last_inbound_at or (timezone.now()-conversation.last_inbound_at).total_seconds()>=24*3600:
        raise ProviderError('The WhatsApp 24-hour reply window has ended. Use an approved template in Ghala.')
    body={'to':conversation.phone,'type':'text','text':payload['text']}
    if payload.get('media_url'):
        body={'to':conversation.phone,'type':'image','media_url':payload['media_url'],'media_caption':payload['text']}
    result=call('Ghala','/api/v2/messages',connection.ghala_token,body,key=job.key)
    if not result.get('id'):
        raise ProviderError('Ghala did not confirm message acceptance. Retry with the same queued job.')
    Message.objects.get_or_create(conversation=conversation,external_id='duka-'+job.key,defaults={'direction':'out','body':payload['text'],'media_url':payload.get('media_url',''),'delivery_status':'accepted'})

def expire_reservations():
    """Releases stock reserved by live orders whose 30-minute confirmation window has passed
    without payment. Safe to call repeatedly/concurrently: each order is locked and re-checked
    before release, and mark_expired() only ever fires once per order (pending -> expired)."""
    now=timezone.now()
    released=0
    for order_id in list(Order.objects.filter(is_demo=False,payment_status=Order.PAYMENT_PENDING,reserved_until__isnull=False,reserved_until__lt=now).values_list('id',flat=True)):
        with transaction.atomic():
            order=Order.objects.select_for_update().get(id=order_id)
            if order.payment_status!=Order.PAYMENT_PENDING or not order.reserved_until or order.reserved_until>=timezone.now():
                continue
            if order.mark_expired():
                if order.product_id:
                    Product.objects.filter(id=order.product_id).update(stock=F('stock')+order.quantity)
                released+=1
    return released

def process(job):
    connection=Connection.objects.select_related('shop').get(shop=job.shop)
    if job.kind=='webhook':
        event=WebhookEvent.objects.get(id=job.payload['event_id'],shop=job.shop)
        if event.provider=='ghala':
            receive(event,connection)
        else:
            verify_payment(event,connection)
    elif job.kind=='agent':
        agent_job(job,connection)
    elif job.kind=='send':
        send(job,connection,job.payload)
    else:
        raise ProviderError('Unknown queued job type.')
