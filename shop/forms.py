import re
from django import forms
from django.contrib.auth.forms import UserCreationForm, SetPasswordForm
from .models import Product, Shop, OTP_CHANNEL_CHOICES, OTP_CHANNEL_EMAIL, OTP_PHONE_CHANNELS
import io
import uuid
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.files.base import ContentFile

PHONE_RE = re.compile(r'\+[1-9]\d{7,14}')

class SignupForm(UserCreationForm):
    channel = forms.ChoiceField(choices=OTP_CHANNEL_CHOICES, widget=forms.RadioSelect, initial=OTP_CHANNEL_EMAIL,
        help_text='Where should we send your verification code?')
    email = forms.EmailField(required=False, help_text='Required if you choose email verification.')
    phone = forms.CharField(required=False, help_text='Required if you choose WhatsApp or SMS verification, e.g. +255712345678.')
    class Meta(UserCreationForm.Meta):
        fields = ('username', 'email', 'phone', 'channel')

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone and not PHONE_RE.fullmatch(phone):
            raise forms.ValidationError('Enter a phone number in international format, e.g. +255712345678.')
        return phone

    def clean(self):
        cleaned = super().clean()
        channel = cleaned.get('channel')
        if channel == OTP_CHANNEL_EMAIL and not cleaned.get('email'):
            self.add_error('email', 'Enter an email address to receive your code there.')
        if channel in OTP_PHONE_CHANNELS and not cleaned.get('phone'):
            self.add_error('phone', 'Enter a phone number to receive your code by WhatsApp or SMS.')
        return cleaned

class OtpVerifyForm(forms.Form):
    code = forms.CharField(max_length=6, min_length=6, label='Verification code',
        widget=forms.TextInput(attrs={'inputmode':'numeric','autocomplete':'one-time-code'}))

class OtpSetPasswordForm(SetPasswordForm):
    code = forms.CharField(max_length=6, min_length=6, label='Verification code',
        widget=forms.TextInput(attrs={'inputmode':'numeric','autocomplete':'one-time-code'}))
    field_order = ['code', 'new_password1', 'new_password2']

class ProductForm(forms.ModelForm):
    price = forms.IntegerField(min_value=1, max_value=100000000)
    stock = forms.IntegerField(min_value=0, max_value=100000)
    image = forms.ImageField(required=True, error_messages={'required': 'Add at least one product photo.'})
    class Meta:
        model = Product
        fields = ['name', 'price', 'stock', 'description', 'image']

    def clean_image(self):
        return clean_photo(self.cleaned_data.get('image'))

def clean_photo(upload):
    if not upload:
        return upload
    if upload.size > 5 * 1024 * 1024:
        raise forms.ValidationError('Choose an image smaller than 5 MB.')
    try:
        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            image = Image.open(upload)
            if image.format not in {'JPEG','PNG','WEBP'} or image.width * image.height > 20000000:
                raise ValueError()
            image.load()
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1600,1600))
            result = io.BytesIO()
            image.save(result,format='JPEG',quality=88)
        return ContentFile(result.getvalue(), name=f'{uuid.uuid4().hex}.jpg')
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise forms.ValidationError('Upload a valid JPG, PNG or WebP image up to 20 megapixels.') from None

class ShopForm(forms.ModelForm):
    language = forms.ChoiceField(choices=[(v,v) for v in ['Swahili & English','Swahili','English']])
    delivery = forms.IntegerField(min_value=0, max_value=100000000)
    class Meta:
        model = Shop
        fields = ['name', 'city', 'language', 'delivery']
