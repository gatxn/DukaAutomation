import json
import secrets
from datetime import timedelta
from functools import wraps
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, connection as db_connection, transaction
from django.db.models import F
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from .forms import ProductForm, ShopForm, SignupForm, OtpVerifyForm, OtpSetPasswordForm
from .models import (Shop, Product, Conversation, Message, Order, Connection, Membership, UserProfile, OtpCode,
    ROLE_OWNER, ROLE_MANAGER, ROLE_AGENT, OTP_PURPOSE_SIGNUP, OTP_PURPOSE_RESET, OTP_CHANNEL_EMAIL, OTP_PHONE_CHANNELS, OTP_CHANNEL_CHOICES)
from .permissions import shop_for_user, role_for
from .providers import send_otp, ProviderError
from .audit import log_action
from .seed import seed_demo

OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_PER_HOUR = 5

def _generate_otp():
    return f'{secrets.randbelow(1000000):06d}'

def _resend_otp(otp):
    """Shared by signup_verify and password_reset_verify's 'resend' action."""
    if timezone.now() - otp.last_sent_at < timedelta(seconds=OTP_RESEND_COOLDOWN_SECONDS):
        return 'Please wait a bit before requesting another code.'
    recent = OtpCode.objects.filter(destination=otp.destination, purpose=otp.purpose,
        created_at__gte=timezone.now()-timedelta(hours=1)).count()
    if recent >= OTP_MAX_PER_HOUR:
        return 'Too many codes requested. Try again later.'
    code = _generate_otp()
    try:
        send_otp(otp.channel, otp.destination, code)
    except ProviderError as exc:
        return str(exc)
    otp.code_hash = make_password(code)
    otp.last_sent_at = timezone.now()
    otp.expires_at = timezone.now() + timedelta(seconds=settings.OTP_TTL_SECONDS)
    otp.attempts = 0
    otp.save(update_fields=['code_hash','last_sent_at','expires_at','attempts'])
    return None

def _check_otp(otp, code):
    """Returns an error string, or None on success. Increments attempts as a side effect."""
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        return 'Too many incorrect attempts. Request a new code.'
    if not check_password(code, otp.code_hash):
        otp.attempts += 1
        otp.save(update_fields=['attempts'])
        return 'That code is incorrect.'
    return None

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
        channel = form.cleaned_data['channel']
        destination = form.cleaned_data['email'] if channel == OTP_CHANNEL_EMAIL else form.cleaned_data['phone']
        pending_user = form.save(commit=False)  # hashes the password without touching the DB
        code = _generate_otp()
        try:
            send_otp(channel, destination, code)
        except ProviderError as exc:
            form.add_error(None, str(exc))
            return render(request, 'signup.html', {'form':form})
        otp = OtpCode.objects.create(purpose=OTP_PURPOSE_SIGNUP, channel=channel, destination=destination,
            code_hash=make_password(code), pending_username=pending_user.username,
            pending_password_hash=pending_user.password,
            expires_at=timezone.now()+timedelta(seconds=settings.OTP_TTL_SECONDS))
        log_action(request, 'otp_requested', result='ok', purpose=OTP_PURPOSE_SIGNUP, channel=channel)
        return redirect('signup_verify', token=otp.token)
    return render(request, 'signup.html', {'form':form})

def signup_verify(request, token):
    otp = get_object_or_404(OtpCode, token=token, purpose=OTP_PURPOSE_SIGNUP, consumed_at__isnull=True)
    expired = otp.expires_at < timezone.now()
    resend_error = None
    form = OtpVerifyForm(request.POST or None)
    if request.method == 'POST' and 'resend' in request.POST:
        resend_error = _resend_otp(otp)
        expired = False
        form = OtpVerifyForm()
    elif request.method == 'POST' and not expired and form.is_valid():
        check_error = _check_otp(otp, form.cleaned_data['code'])
        if check_error:
            form.add_error(None, check_error)
        else:
            try:
                with transaction.atomic():
                    user = User(username=otp.pending_username, password=otp.pending_password_hash)
                    if otp.channel == OTP_CHANNEL_EMAIL:
                        user.email = otp.destination
                    user.save()
                    UserProfile.objects.create(user=user,
                        phone=otp.destination if otp.channel in OTP_PHONE_CHANNELS else '',
                        phone_verified=otp.channel in OTP_PHONE_CHANNELS,
                        email_verified=otp.channel == OTP_CHANNEL_EMAIL)
                    shop = Shop.objects.create(owner=user, name='Mlimani Shop')
                    Membership.objects.create(shop=shop, user=user, role=ROLE_OWNER)
                    seed_demo(shop)
                    otp.consumed_at = timezone.now()
                    otp.save(update_fields=['consumed_at'])
            except IntegrityError:
                form.add_error(None, 'That username was just taken. Go back and choose another.')
            else:
                log_action(request, 'signup_completed', shop=shop, actor=user, result='ok', channel=otp.channel)
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')
                return redirect('workspace')
    return render(request, 'signup_verify.html', {'form':form, 'otp':otp, 'expired':expired, 'resend_error':resend_error})

def _available_channels(user):
    """Only channels the account actually verified — at signup, or never (existing accounts
    from before this feature shipped have no profile at all and offer nothing)."""
    profile = getattr(user, 'profile', None)
    channels = []
    if not profile:
        return channels
    if user.email and profile.email_verified:
        channels.append(OTP_CHANNEL_EMAIL)
    if profile.phone and profile.phone_verified:
        # The number was proven at signup, so any phone channel can reach its owner.
        channels.extend(OTP_PHONE_CHANNELS)
    return channels

def password_reset_request(request):
    username = ''
    available_channels = []
    checked_user = None
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        try:
            checked_user = User.objects.get(username=username)
        except User.DoesNotExist:
            checked_user = None
        available_channels = _available_channels(checked_user) if checked_user else []
        if 'channel' in request.POST:
            channel = request.POST.get('channel')
            if checked_user and channel in available_channels:
                profile = checked_user.profile
                destination = checked_user.email if channel == OTP_CHANNEL_EMAIL else profile.phone
                code = _generate_otp()
                try:
                    send_otp(channel, destination, code)
                except ProviderError as exc:
                    error = str(exc)
                else:
                    otp = OtpCode.objects.create(purpose=OTP_PURPOSE_RESET, channel=channel, destination=destination,
                        code_hash=make_password(code), user=checked_user,
                        expires_at=timezone.now()+timedelta(seconds=settings.OTP_TTL_SECONDS))
                    log_action(request, 'otp_requested', actor=checked_user, result='ok', purpose=OTP_PURPOSE_RESET, channel=channel)
                    return redirect('password_reset_verify', token=otp.token)
            else:
                error = 'Choose one of the available options.'
        elif not available_channels:
            # Same message whether the account doesn't exist or has nothing verified yet —
            # never confirm which one via a different response.
            return render(request, 'password_reset_request.html', {'no_channels':True})
    channel_labels = dict(OTP_CHANNEL_CHOICES)
    return render(request, 'password_reset_request.html', {
        'username':username, 'available_channels_display':[(c, channel_labels[c]) for c in available_channels],
        'error':error, 'show_channels':bool(username),
    })

def password_reset_verify(request, token):
    otp = get_object_or_404(OtpCode, token=token, purpose=OTP_PURPOSE_RESET, consumed_at__isnull=True)
    expired = otp.expires_at < timezone.now()
    resend_error = None
    form = OtpSetPasswordForm(otp.user, request.POST or None) if not expired else None
    if request.method == 'POST' and 'resend' in request.POST:
        resend_error = _resend_otp(otp)
        expired = False
        form = OtpSetPasswordForm(otp.user)
    elif form and request.method == 'POST' and form.is_valid():
        check_error = _check_otp(otp, form.cleaned_data['code'])
        if check_error:
            form.add_error(None, check_error)
        else:
            otp.user.set_password(form.cleaned_data['new_password1'])
            otp.user.save()
            otp.consumed_at = timezone.now()
            otp.save(update_fields=['consumed_at'])
            log_action(request, 'password_reset_completed', actor=otp.user, result='ok')
            login(request, otp.user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect('workspace')
    return render(request, 'password_reset_verify.html', {'form':form, 'expired':expired, 'resend_error':resend_error})

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
