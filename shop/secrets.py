"""Merchant credentials are encrypted at rest and never serialized to clients."""
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

def cipher():
    key = settings.CREDENTIAL_ENCRYPTION_KEY
    if not key:
        if not settings.DEBUG:
            raise ValueError('Set CREDENTIAL_ENCRYPTION_KEY before saving live credentials.')
        key = base64.urlsafe_b64encode(hashlib.sha256(('duka-credentials:'+settings.SECRET_KEY).encode()).digest()).decode()
    return Fernet(key.encode())

def seal(value):
    return cipher().encrypt(value.encode()).decode() if value else ''

def reveal(value):
    if not value:
        return ''
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError('Credential encryption key changed. Re-enter the affected credential.') from None
