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
    path('password-reset/', auth_views.PasswordResetView.as_view(template_name='password_reset_form.html'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='password_reset_confirm.html'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='password_reset_complete.html'), name='password_reset_complete'),
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
    path('webhooks/<str:provider>/<uuid:webhook_id>/', connections.webhook),
    path('api/conversations/<int:contact_id>/<str:action>/', views.conversation_action),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,document_root=settings.MEDIA_ROOT)
