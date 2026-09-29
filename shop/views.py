import json
from functools import wraps
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection as db_connection, transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from .forms import ProductForm, ShopForm, SignupForm
from .models import Shop, Product, Conversation, Message, Order, Connection, Membership, ROLE_OWNER, ROLE_MANAGER, ROLE_AGENT
from .permissions import shop_for_user, role_for
from .audit import log_action
from .seed import seed_demo

def api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error':'Please sign in.'}, status=401)
        if request.method == 'POST':
            if request.content_type == 'multipart/form-data':
                request.payload = request.POST
                return view(request, *args, **kwargs)
            try:
                request.payload = json.loads(request.body)
                if not isinstance(request.payload, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                return JsonResponse({'error':'Invalid JSON object.'}, status=400)
        return view(request, *args, **kwargs)
    return wrapped

def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save()
            shop = Shop.objects.create(owner=user, name='Mlimani Shop')
            Membership.objects.create(shop=shop, user=user, role=ROLE_OWNER)
            seed_demo(shop)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return redirect('workspace')
    return render(request, 'signup.html', {'form':form})

@login_required
@ensure_csrf_cookie
def workspace(request):
    return render(request,'workspace.html')

@require_GET
def healthz(request):
    checks = {}
    try:
        with db_connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        checks['database'] = 'ok'
    except Exception:
        checks['database'] = 'error'
    total_shops = Shop.objects.count()
    active_workers = Connection.objects.filter(worker_heartbeat__gte=timezone.now()-timezone.timedelta(minutes=2)).count()
    checks['worker'] = 'ok' if total_shops == 0 or active_workers > 0 else 'no_active_worker'
    healthy = checks['database'] == 'ok'
    return JsonResponse({'status':'ok' if healthy else 'error','checks':checks}, status=200 if healthy else 503)

@require_GET
@api
def state(request):
    shop = shop_for_user(request.user, min_role=ROLE_AGENT)
    contacts = []
    for c in Conversation.objects.filter(shop=shop).prefetch_related('messages','orders').order_by('id'):
        latest = max(c.orders.all(), key=lambda o:o.id, default=None)
        contacts.append({'id':c.id,'name':c.name,'phone':c.phone,'is_demo':c.is_demo,'preview':c.preview,'human':c.human,'orderId':latest.id if latest else None,'messages':list(c.messages.order_by('id').values_list('direction','body','delivery_status','media_url'))})
    products = [{'id':p.id,'name':p.name,'price':p.price,'stock':p.stock,'description':p.description,'image':p.image.url if p.image else '','gallery':[{'id':g.id,'url':g.image.url} for g in p.gallery.all()]} for p in Product.objects.filter(shop=shop).order_by('id').prefetch_related('gallery')]
    return JsonResponse({'role':role_for(request.user),'account_email':request.user.email,'business':{'name':shop.name,'city':shop.city,'language':shop.language,'delivery':shop.delivery},'products':products,'contacts':contacts,'orders':list(Order.objects.filter(shop=shop).order_by('-id').values('id','name','item','total','status','payment_status','fulfillment_status','is_demo','checkout_url','delivery_address'))})

@require_POST
@api
def account_email(request):
    email = request.payload.get('email','')
    if not isinstance(email,str) or len(email)>254:
        return JsonResponse({'error':'Enter a valid email address.'},status=400)
    email = email.strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({'error':'Enter a valid email address.'},status=400)
    request.user.email = email
    request.user.save(update_fields=['email'])
    log_action(request,'account_email_updated')
    return JsonResponse({'ok':True})

@require_POST
@api
def add_product(request):
    shop = shop_for_user(request.user, min_role=ROLE_MANAGER)
    form = ProductForm(request.payload,request.FILES)
    if not form.is_valid():
        return JsonResponse({'error':'Check the product details.','fields':form.errors},status=400)
    product = form.save(commit=False)
    product.shop = shop
    product.save()
    log_action(request,'product_created',target=product,shop=shop,name=product.name)
    return JsonResponse({'id':product.id},status=201)

@require_POST
@api
def save_settings(request):
    shop = shop_for_user(request.user, min_role=ROLE_MANAGER)
    form = ShopForm(request.payload,instance=shop)
    if not form.is_valid():
        return JsonResponse({'error':'Check your shop details.','fields':form.errors},status=400)
    form.save()
    log_action(request,'shop_settings_updated',shop=shop)
    return JsonResponse({'ok':True})

@require_POST
@api
@transaction.atomic
def conversation_action(request,contact_id,action):
    shop = shop_for_user(request.user, min_role=ROLE_AGENT)
    c = get_object_or_404(Conversation.objects.select_for_update().select_related('shop','product'),id=contact_id,shop=shop)
    if not c.is_demo and action != 'handover':
        return JsonResponse({'error':'Use the live message and checkout controls for this customer.'},status=400)
    if action == 'handover':
        desired = request.payload.get('human')
        if not isinstance(desired,bool):
            return JsonResponse({'error':'Specify human as true or false.'},status=400)
        c.human = desired
        if desired:
            c.quote = {}
        c.save(update_fields=['human','quote'])
    elif action == 'reply':
        body = ('Habari, mimi ni mhudumu wa duka. Nitakusaidia kukamilisha oda yako.' if c.human else f'Ndiyo, tunafika Sinza! Gharama ya usafirishaji ni TZS {c.shop.delivery:,}. Tafadhali tuma eneo lako la kufikishiwa.')
        Message.objects.create(conversation=c,direction='out',body=body)
    elif action == 'order':
        if Order.objects.filter(conversation=c).exists():
            return JsonResponse({'ok':True,'detail':'Order already exists.'})
        if not c.product or c.product.shop_id != c.shop_id:
            return JsonResponse({'error':'No product is available for this conversation.'},status=400)
        if not Product.objects.filter(id=c.product_id,shop=c.shop,stock__gt=0).update(stock=F('stock')-1):
            return JsonResponse({'error':'This product is out of stock.'},status=409)
        order = Order.objects.create(shop=c.shop,conversation=c,name=c.name,item=c.product.name,total=c.product.price+c.shop.delivery)
        Message.objects.create(conversation=c,direction='out',body=f'Sample order #{order.id}: {order.item}, including delivery: TZS {order.total:,}. Live checkout requires a connected payment provider.')
    elif action == 'payment':
        order = Order.objects.filter(conversation=c,shop=c.shop,is_demo=True).first()
        if not order:
            return JsonResponse({'error':'Create a sample order first.'},status=400)
        if order.mark_paid():
            Message.objects.create(conversation=c,direction='out',body=f'Demo payment confirmed for order #{order.id}. No money was collected.')
    else:
        return JsonResponse({'error':'Unknown action.'},status=404)
    return JsonResponse({'ok':True})
