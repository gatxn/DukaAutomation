import secrets
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

class Shop(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=80, default='My shop')
    city = models.CharField(max_length=80, default='Dar es Salaam')
    language = models.CharField(max_length=32, default='Swahili & English')
    delivery = models.PositiveIntegerField(default=5000)
    def __str__(self):
        return self.name

ROLE_OWNER = 'owner'
ROLE_MANAGER = 'manager'
ROLE_AGENT = 'agent'
ROLE_CHOICES = [(ROLE_OWNER,'Owner'), (ROLE_MANAGER,'Manager'), (ROLE_AGENT,'Agent')]
ROLE_LEVEL = {ROLE_AGENT:0, ROLE_MANAGER:1, ROLE_OWNER:2}

class Membership(models.Model):
    """One shop per staff account. Shop.owner is kept for backward compatibility
    and always corresponds to the row with role=owner created at signup/backfill."""
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='memberships')
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='membership')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_AGENT)
    created_at = models.DateTimeField(auto_now_add=True)
    def __str__(self):
        return f'{self.user} @ {self.shop} ({self.role})'

class AuditLog(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, null=True, blank=True, related_name='audit_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    result = models.CharField(max_length=16, default='ok')
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-id']
    def __str__(self):
        return f'{self.action} by {self.actor} @ {self.created_at}'

class Product(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    price = models.PositiveIntegerField()
    stock = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True, max_length=2000)
    image = models.ImageField(upload_to='products/%Y/%m/', blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(price__gt=0), name='product_positive_price')]
    def __str__(self):
        return self.name

MAX_PRODUCT_IMAGES = 15

class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='gallery')
    image = models.ImageField(upload_to='products/%Y/%m/')
    position = models.PositiveSmallIntegerField(default=0)
    class Meta:
        ordering = ['position', 'id']

class Conversation(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    preview = models.CharField(max_length=200)
    human = models.BooleanField(default=False)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    phone = models.CharField(max_length=20, blank=True)
    is_demo = models.BooleanField(default=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    quote = models.JSONField(default=dict, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['shop','phone'], condition=~Q(phone=''), name='unique_shop_phone')]

class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    direction = models.CharField(max_length=3, choices=[('in','Incoming'), ('out','Outgoing')])
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    external_id = models.CharField(max_length=255, blank=True)
    delivery_status = models.CharField(max_length=32, default='saved')
    media_url = models.URLField(blank=True, max_length=1000)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['conversation','external_id'],condition=~Q(external_id=''), name='unique_conversation_external_message')]

class Order(models.Model):
    PAYMENT_PENDING = 'pending'
    PAYMENT_PAID = 'paid'
    PAYMENT_FAILED = 'failed'
    PAYMENT_EXPIRED = 'expired'
    PAYMENT_STATUS_CHOICES = [(PAYMENT_PENDING,'Pending'), (PAYMENT_PAID,'Paid'), (PAYMENT_FAILED,'Failed'), (PAYMENT_EXPIRED,'Expired')]

    FULFILLMENT_UNFULFILLED = 'unfulfilled'
    FULFILLMENT_READY = 'ready_for_delivery'
    FULFILLMENT_DELIVERED = 'delivered'
    FULFILLMENT_CANCELLED = 'cancelled'
    FULFILLMENT_STATUS_CHOICES = [(FULFILLMENT_UNFULFILLED,'Unfulfilled'), (FULFILLMENT_READY,'Ready for delivery'), (FULFILLMENT_DELIVERED,'Delivered'), (FULFILLMENT_CANCELLED,'Cancelled')]

    shop = models.ForeignKey(Shop, on_delete=models.CASCADE)
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    name = models.CharField(max_length=80)
    item = models.CharField(max_length=80)
    total = models.PositiveIntegerField()
    # `status` is kept as a single display string for backward compatibility with the
    # existing frontend/API contract; `payment_status`/`fulfillment_status` are the
    # authoritative state machine and are kept in sync by the transition methods below.
    status = models.CharField(max_length=32, default='Pending payment', choices=[('Pending payment','Pending payment'),('Paid','Paid'),('Ready for delivery','Ready for delivery'),('Expired','Expired'),('Cancelled','Cancelled')])
    payment_status = models.CharField(max_length=16, choices=PAYMENT_STATUS_CHOICES, default=PAYMENT_PENDING)
    fulfillment_status = models.CharField(max_length=20, choices=FULFILLMENT_STATUS_CHOICES, default=FULFILLMENT_UNFULFILLED)
    reserved_until = models.DateTimeField(null=True, blank=True)
    is_demo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.PositiveIntegerField(default=0)
    delivery_fee = models.PositiveIntegerField(default=0)
    delivery_address = models.CharField(max_length=500, blank=True)
    payment_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    checkout_reference = models.CharField(max_length=150, blank=True)
    checkout_url = models.URLField(max_length=1000, blank=True)
    payment_reference = models.CharField(max_length=150, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    origin_key = models.CharField(max_length=255, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['shop','origin_key'],condition=~Q(origin_key=''),name='unique_order_origin')]

    def mark_paid(self, payment_reference=''):
        """Atomically transitions pending -> paid. Returns False if already resolved (idempotent)."""
        fields = {'payment_status': Order.PAYMENT_PAID, 'status': 'Paid', 'paid_at': timezone.now()}
        if payment_reference:
            fields['payment_reference'] = payment_reference
        changed = Order.objects.filter(id=self.id, payment_status=Order.PAYMENT_PENDING).update(**fields)
        if changed:
            for key, value in fields.items():
                setattr(self, key, value)
        return bool(changed)

    def mark_expired(self):
        """Atomically transitions pending -> expired (used by the stock-reservation expiry sweep)."""
        fields = {'payment_status': Order.PAYMENT_EXPIRED, 'status': 'Expired'}
        changed = Order.objects.filter(id=self.id, payment_status=Order.PAYMENT_PENDING).update(**fields)
        if changed:
            for key, value in fields.items():
                setattr(self, key, value)
        return bool(changed)

    def cancel(self):
        """Cancels an order's fulfillment. Refuses once delivered."""
        if self.fulfillment_status == Order.FULFILLMENT_DELIVERED:
            raise ValueError('Cannot cancel a delivered order.')
        fields = {'fulfillment_status': Order.FULFILLMENT_CANCELLED, 'status': 'Cancelled'}
        changed = Order.objects.filter(id=self.id).exclude(fulfillment_status=Order.FULFILLMENT_DELIVERED).update(**fields)
        if changed:
            for key, value in fields.items():
                setattr(self, key, value)
        return bool(changed)

    def mark_ready_for_delivery(self):
        """Requires payment first; refuses a cancelled/already-delivered order."""
        if self.payment_status != Order.PAYMENT_PAID:
            raise ValueError('Cannot mark an unpaid order ready for delivery.')
        fields = {'fulfillment_status': Order.FULFILLMENT_READY, 'status': 'Ready for delivery'}
        changed = Order.objects.filter(id=self.id, payment_status=Order.PAYMENT_PAID).exclude(fulfillment_status__in=[Order.FULFILLMENT_CANCELLED, Order.FULFILLMENT_DELIVERED]).update(**fields)
        if changed:
            for key, value in fields.items():
                setattr(self, key, value)
        return bool(changed)

    def mark_delivered(self):
        if self.fulfillment_status != Order.FULFILLMENT_READY:
            raise ValueError('Order must be ready for delivery first.')
        changed = Order.objects.filter(id=self.id, fulfillment_status=Order.FULFILLMENT_READY).update(fulfillment_status=Order.FULFILLMENT_DELIVERED)
        if changed:
            self.fulfillment_status = Order.FULFILLMENT_DELIVERED
        return bool(changed)

class Connection(models.Model):
    shop = models.OneToOneField(Shop,on_delete=models.CASCADE,related_name='connection')
    webhook_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    public_url = models.URLField(blank=True)
    ghala_token = models.TextField(blank=True)
    ghala_secret = models.TextField(blank=True)
    ghala_subscription = models.CharField(max_length=150,blank=True)
    ghala_map = models.JSONField(default=dict,blank=True)
    mapping_confirmed = models.BooleanField(default=False)
    snippe_token = models.TextField(blank=True)
    snippe_secret = models.TextField(blank=True)
    openai_key = models.TextField(blank=True)
    agent_name = models.CharField(max_length=80,default='Duka Assistant')
    agent_policies = models.TextField(blank=True,max_length=6000)
    agent_enabled = models.BooleanField(default=False)
    ghala_auto_reply_disabled = models.BooleanField(default=False)
    model = models.CharField(max_length=80,default='gpt-4.1-mini')
    worker_heartbeat = models.DateTimeField(null=True,blank=True)
    # Meta WhatsApp Cloud API, connected directly with no middleman. Verified against Meta when
    # saved, so one shop cannot claim another shop's number ID and receive its customers' messages.
    meta_token = models.TextField(blank=True)
    meta_phone_number_id = models.CharField(max_length=40,blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['meta_phone_number_id'],condition=~Q(meta_phone_number_id=''),name='unique_meta_phone_number_id')]

    @property
    def uses_meta(self):
        return bool(self.meta_token and self.meta_phone_number_id)

class WebhookEvent(models.Model):
    shop = models.ForeignKey(Shop,on_delete=models.CASCADE)
    provider = models.CharField(max_length=16)
    delivery_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=80)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['shop','provider','delivery_id'],name='unique_provider_delivery')]

class Job(models.Model):
    shop = models.ForeignKey(Shop,on_delete=models.CASCADE)
    kind = models.CharField(max_length=32)
    key = models.CharField(max_length=255,unique=True)
    payload = models.JSONField(default=dict)
    result = models.JSONField(default=dict)
    status = models.CharField(max_length=16,default='pending')
    attempts = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=500,blank=True)
    available_at = models.DateTimeField()
    locked_at = models.DateTimeField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class UserProfile(models.Model):
    """Extends the stock auth.User with the extra contact details OTP verification needs,
    without swapping AUTH_USER_MODEL this late with real production accounts already in place."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=20, blank=True)
    phone_verified = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)

def generate_otp_token():
    return secrets.token_urlsafe(32)

OTP_PURPOSE_SIGNUP = 'signup'
OTP_PURPOSE_RESET = 'password_reset'
OTP_PURPOSE_CHOICES = [(OTP_PURPOSE_SIGNUP,'Signup'), (OTP_PURPOSE_RESET,'Password reset')]
OTP_CHANNEL_EMAIL = 'email'
OTP_CHANNEL_WHATSAPP = 'whatsapp'
OTP_CHANNEL_SMS = 'sms'
OTP_CHANNEL_CHOICES = [(OTP_CHANNEL_EMAIL,'Email'), (OTP_CHANNEL_WHATSAPP,'WhatsApp'), (OTP_CHANNEL_SMS,'SMS')]
OTP_PHONE_CHANNELS = (OTP_CHANNEL_WHATSAPP, OTP_CHANNEL_SMS)

class OtpCode(models.Model):
    """A one-time code for either a pending signup or a password reset. Signup rows stage the
    not-yet-created account (username + already-hashed password); reset rows point at `user`.
    The plaintext code is never stored — only `code_hash`, checked via check_password()."""
    token = models.CharField(max_length=43, unique=True, default=generate_otp_token, editable=False)
    purpose = models.CharField(max_length=16, choices=OTP_PURPOSE_CHOICES)
    channel = models.CharField(max_length=16, choices=OTP_CHANNEL_CHOICES)
    destination = models.CharField(max_length=255)
    code_hash = models.CharField(max_length=128)
    pending_username = models.CharField(max_length=150, blank=True)
    pending_password_hash = models.CharField(max_length=128, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_sent_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
