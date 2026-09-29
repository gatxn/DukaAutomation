import tempfile
from pathlib import Path
from django.core.management import call_command
from django.test import TestCase, override_settings

class BackupRestoreTests(TestCase):
    def test_backup_then_restore_round_trips_the_database_file(self):
        with tempfile.TemporaryDirectory() as workdir:
            workdir = Path(workdir)
            db_path = workdir / 'db.sqlite3'
            db_path.write_bytes(b'fake-sqlite-content-for-the-drill')
            media_root = workdir / 'media'
            media_root.mkdir()
            (media_root / 'products').mkdir()
            (media_root / 'products' / 'sample.jpg').write_bytes(b'fake-image-bytes')
            backup_dir = workdir / 'backups'

            with override_settings(
                DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(db_path)}},
                MEDIA_ROOT=str(media_root),
                BASE_DIR=workdir,
            ):
                call_command('backup_data', out_dir=str(backup_dir))
                archives = list(backup_dir.glob('duka-backup-*.tar.gz'))
                self.assertEqual(len(archives), 1)

                db_path.unlink()
                shutil_media = media_root / 'products' / 'sample.jpg'
                shutil_media.unlink()

                call_command('restore_data', str(archives[0]), force=True)

                self.assertTrue(db_path.exists())
                self.assertEqual(db_path.read_bytes(), b'fake-sqlite-content-for-the-drill')
                self.assertTrue((media_root / 'products' / 'sample.jpg').exists())

    def test_restore_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as workdir:
            workdir = Path(workdir)
            db_path = workdir / 'db.sqlite3'
            db_path.write_bytes(b'original')
            backup_dir = workdir / 'backups'
            with override_settings(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(db_path)}}, MEDIA_ROOT=str(workdir / 'media'), BASE_DIR=workdir):
                call_command('backup_data', out_dir=str(backup_dir))
                archive = next(backup_dir.glob('duka-backup-*.tar.gz'))
                from django.core.management.base import CommandError
                with self.assertRaises(CommandError):
                    call_command('restore_data', str(archive))
                self.assertEqual(db_path.read_bytes(), b'original')  # untouched
