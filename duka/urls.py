from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from shop import views
from shop import connections
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz/', views.healthz),
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('signup/', views.signup, name='signup'),
    path('signup/verify/<str:token>/', views.signup_verify, name='signup_verify'),
    path('password-reset/', views.password_reset_request, name='password_reset'),
    path('password-reset/verify/<str:token>/', views.password_reset_verify, name='password_reset_verify'),
    path('', views.workspace, name='workspace'),
    path('api/state/', views.state),
    path('api/account/email/', views.account_email),
    path('api/products/', views.add_product),
    path('api/settings/', views.save_settings),
    path('api/connections/', connections.settings_api),
    path('api/connections/ghala/register/', connections.register_ghala),
    path('api/products/<int:product_id>/photo/', connections.product_photo),
    path('api/products/<int:product_id>/photos/', connections.product_gallery),
    path('api/products/<int:product_id>/photos/<int:photo_id>/delete/', connections.delete_gallery_photo),
    path('api/agent/preview/', connections.preview_agent),
    path('api/live/<int:contact_id>/send/', connections.send_message),
    path('api/orders/<int:order_id>/checkout/', connections.checkout_api),
    path('api/orders/<int:order_id>/<str:action>/', connections.order_action),
    path('api/jobs/<int:job_id>/retry/', connections.retry_job),
    path('api/staff/', connections.staff_api),
    path('api/staff/<int:membership_id>/role/', connections.update_staff_role),
    path('api/staff/<int:membership_id>/remove/', connections.remove_staff),
    path('webhooks/meta/', connections.meta_webhook),
    path('webhooks/<str:provider>/<uuid:webhook_id>/', connections.webhook),
    path('api/conversations/<int:contact_id>/<str:action>/', views.conversation_action),
]
# Always serve /media/ (product photos), not just in DEBUG: this app has no other mechanism
# configured yet (no CDN/reverse-proxy static handling, no object storage) to serve uploaded
# files in production. This is the documented stopgap in PRODUCTION_DEPLOYMENT.md item 4 —
# move to S3-compatible storage once the app needs to run on more than one instance.
urlpatterns += static(settings.MEDIA_URL,document_root=settings.MEDIA_ROOT)
