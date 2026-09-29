"""Audit trail helper. Never pass secrets/credential values as metadata here."""
from .models import AuditLog

def log_action(request, action, target=None, result='ok', shop=None, actor=None, **metadata):
    if actor is None:
        actor = request.user if getattr(request, 'user', None) and request.user.is_authenticated else None
    AuditLog.objects.create(
        shop=shop,
        actor=actor,
        action=action,
        target_type=type(target).__name__ if target is not None else '',
        target_id=str(getattr(target, 'id', '')) if target is not None else '',
        result=result,
        metadata=metadata,
        ip_address=request.META.get('REMOTE_ADDR') if hasattr(request, 'META') else None,
    )
