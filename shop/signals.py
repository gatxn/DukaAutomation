from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from .audit import log_action
from .permissions import get_membership

@receiver(user_logged_in)
def _log_login(sender, request, user, **kwargs):
    membership = get_membership(user)
    log_action(request, 'login', shop=membership.shop if membership else None, actor=user)

@receiver(user_logged_out)
def _log_logout(sender, request, user, **kwargs):
    membership = get_membership(user) if user else None
    log_action(request, 'logout', shop=membership.shop if membership else None, actor=user)
