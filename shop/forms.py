from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import Product, Shop
import io
import uuid
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.files.base import ContentFile

class SignupForm(UserCreationForm):
    email = forms.EmailField(required=False, help_text='Optional, but needed if you ever need to reset your password.')
    class Meta(UserCreationForm.Meta):
        fields = ('username', 'email')

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
