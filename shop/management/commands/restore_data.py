"""Restores db.sqlite3 and media/ from a backup_data archive. Refuses to overwrite a live
database unless --force is passed, since this is a destructive operation by nature."""
import tarfile
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

class Command(BaseCommand):
    help = 'Restore db.sqlite3 and media/ from a backup_data .tar.gz archive.'

    def add_arguments(self, parser):
        parser.add_argument('archive_path')
        parser.add_argument('--force', action='store_true', help='Overwrite the existing database/media without prompting.')

    def handle(self, *args, **options):
        if settings.DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
            self.stderr.write('This command only restores SQLite backups.')
            return
        archive_path = Path(options['archive_path'])
        if not archive_path.exists():
            raise CommandError(f'No such archive: {archive_path}')
        db_path = Path(settings.DATABASES['default']['NAME'])
        if db_path.exists() and not options['force']:
            raise CommandError(f'{db_path} already exists. Re-run with --force to overwrite it.')
        with tarfile.open(archive_path, 'r:gz') as archive:
            archive.extractall(settings.BASE_DIR, filter='data')
        self.stdout.write(f'Restored db.sqlite3 and media/ from {archive_path}')
