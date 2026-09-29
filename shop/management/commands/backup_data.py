"""SQLite-scope backup: copies db.sqlite3 and media/ into one timestamped archive. This is the
dev/local-scale backup strategy; DISASTER_RECOVERY.md documents the PostgreSQL-target strategy
(pg_dump + object storage versioning) for once a real Postgres instance exists."""
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = 'Back up db.sqlite3 and media/ into one timestamped .tar.gz archive under backups/.'

    def add_arguments(self, parser):
        parser.add_argument('--out-dir', default=str(settings.BASE_DIR / 'backups'))

    def handle(self, *args, **options):
        db_path = settings.DATABASES['default'].get('NAME')
        if not db_path or settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            self.stderr.write('This command only backs up SQLite. For PostgreSQL, use pg_dump (see DISASTER_RECOVERY.md).')
            return
        db_path = Path(db_path)
        media_root = Path(settings.MEDIA_ROOT)
        out_dir = Path(options['out_dir'])
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        archive_path = out_dir / f'duka-backup-{stamp}.tar.gz'
        with tarfile.open(archive_path, 'w:gz') as archive:
            if db_path.exists():
                archive.add(db_path, arcname='db.sqlite3')
            if media_root.exists():
                archive.add(media_root, arcname='media')
        self.stdout.write(f'Backup written to {archive_path} ({archive_path.stat().st_size} bytes)')
        return str(archive_path)
