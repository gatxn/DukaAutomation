import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'duka.settings')
application = get_wsgi_application()

# Render's free plan sleeps the service after ~15 idle minutes; this pings our own public URL.
# It does nothing unless RENDER_EXTERNAL_URL is set (i.e. only on Render).
from shop import keepalive  # noqa: E402
keepalive.start()
