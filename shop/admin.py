from django.contrib import admin
from .models import Shop, Product, Conversation, Message, Order

for model in (Shop, Product, Conversation, Message, Order):
    admin.site.register(model)
admin.site.site_header = 'Duka platform administration'
