import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from config.container_env import configure_environment, file_secret
from config.database import postgres_database
from scripts.configure_docker import configure, gateway_route, normalized_domain, normalized_project


class DockerConfigurationTests(SimpleTestCase):
    def test_two_sites_have_independent_secrets_and_unique_routes_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = [Path(directory) / name for name in ('first', 'second')]
            for root, project in zip(roots, ('site-one', 'site-two')):
                root.mkdir()
                configure(root, project + '.example.com', 'admin@example.com',
                          project=project, shared_proxy=True)
                config = (root / '.env.compose').read_text()
                self.assertIn(f'COMPOSE_PROJECT_NAME={project}', config)
                self.assertIn('docker-compose.yml:docker-compose.multi.yml', config)
                self.assertIn(f'{project}-proxy:8080', (root / '.gateway-route.caddy').read_text())
                self.assertNotIn('site-other', config)
            for name in ('django_secret_key', 'db_password', 'admin_password'):
                self.assertNotEqual((roots[0] / '.docker-secrets' / name).read_text(),
                                    (roots[1] / '.docker-secrets' / name).read_text())

    def test_shared_mode_requires_safe_project_and_gateway_before_writing_files(self):
        for project in ('', 'Site-One', '../site', 'one_site', 'site\nKEY=1', 'site:80', 's' * 41):
            with self.subTest(project=project), self.assertRaises(ValueError):
                normalized_project(project)
        with tempfile.TemporaryDirectory() as directory:
            for options in ({'shared_proxy': True},
                            {'shared_proxy': True, 'project': 'one', 'edge_ip': '8.8.8.8'},
                            {'shared_proxy': True, 'project': 'one', 'edge_ip': '0.0.0.0'},
                            {'shared_proxy': True, 'project': 'one', 'edge_network': '../network'}):
                with self.assertRaises(ValueError):
                    configure(directory, 'example.com', 'admin@example.com', **options)
                self.assertEqual(list(Path(directory).iterdir()), [])
        with self.assertRaises(ValueError):
            gateway_route('example.com { respond bad }', 'site-one')

    def test_repeat_shared_configuration_preserves_domain_route_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configure(root, 'one.example.com', 'admin@example.com', project='site-one', shared_proxy=True)
            original = (root / '.gateway-route.caddy').read_text()
            with self.assertRaises(ValueError):
                configure(root, 'two.example.com', 'admin@example.com', project='site-two', shared_proxy=True)
            self.assertEqual((root / '.gateway-route.caddy').read_text(), original)

    def test_secrets_are_unique_and_rerun_preserves_everything(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configure(root, 'APP.Example.COM', 'Admin@example.com')
            key = (root / '.docker-secrets/django_secret_key').read_text()
            password = (root / '.docker-secrets/db_password').read_text()
            self.assertGreaterEqual(len(key.strip()), 50)
            self.assertNotEqual(key, password)
            self.assertIn('SITE_URL=https://app.example.com', (root / '.env.docker').read_text())
            with self.assertRaisesMessage(ValueError, 'already exists'):
                configure(root, 'other.example.com', 'other@example.com')
            self.assertEqual(key, (root / '.docker-secrets/django_secret_key').read_text())

    def test_domain_and_email_cannot_inject_config_or_caddy_directives(self):
        for value in ('https://example.com', 'example.com:443', '*.example.com', '127.0.0.1',
                      'localhost', 'example.com\nDEBUG=1', 'example.com/path', 'x..com'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalized_domain(value)
        self.assertEqual(normalized_domain('localhost', local=True), 'localhost')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                configure(directory, 'example.com', 'a@example.com\nKEY=value')
            self.assertFalse((Path(directory) / '.docker-secrets').exists())

    def test_runtime_quotes_credentials_and_forces_production(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            password = 'secret$@:/#?with space العربية'
            values = {'DJANGO_SECRET_KEY': 'a' * 64, 'POSTGRES_PASSWORD': password,
                      'DJANGO_EMAIL_HOST_PASSWORD': 'smtp$secret'}
            env = {'DJANGO_DEBUG': '1', 'DJANGO_ALLOW_SQLITE': '1', 'DJANGO_SECURE_SSL_REDIRECT': '0'}
            for name, value in values.items():
                path = root / name
                path.write_text(value + '\n', encoding='utf-8')
                env[f'{name}_FILE'] = str(path)
            configure_environment(env)
            self.assertEqual(postgres_database(env['DATABASE_URL'])['PASSWORD'], password)
            self.assertEqual(env['DJANGO_EMAIL_HOST_PASSWORD'], 'smtp$secret')
            self.assertEqual(env['DJANGO_DEBUG'], '0')
            self.assertEqual(env['DJANGO_ALLOW_SQLITE'], '0')
            self.assertEqual(env['DJANGO_SECURE_SSL_REDIRECT'], '1')
            env['PGHOST'] = 'attacker.example.com'
            with self.assertRaises(RuntimeError):
                configure_environment(env)

    def test_missing_and_empty_secrets_fail_without_leaking_contents(self):
        with self.assertRaises(RuntimeError):
            file_secret({}, 'DJANGO_SECRET_KEY')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secret'
            path.write_text('')
            with self.assertRaises(RuntimeError):
                file_secret({'KEY_FILE': str(path)}, 'KEY')
            self.assertEqual(file_secret({'KEY_FILE': str(path)}, 'KEY', allow_empty=True), '')

    def test_startup_stops_if_migrations_fail_before_bootstrap_or_gunicorn(self):
        spec = importlib.util.spec_from_file_location('docker_entrypoint', settings.BASE_DIR / 'docker/entrypoint.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module.subprocess, 'run') as run:
            run.side_effect = [None, subprocess.CalledProcessError(1, 'migrate')]
            with self.assertRaises(subprocess.CalledProcessError):
                module.prepare()
            self.assertEqual([call.args[0][2] for call in run.call_args_list], ['check', 'migrate'])


class BootstrapOperatorTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.password_file = Path(self.directory.name) / 'admin'
        self.password_file.write_text('random-initial-secret-' + 'x' * 24)
        self.env = patch.dict(os.environ, {
            'BOOTSTRAP_ADMIN_EMAIL': 'operator@example.com',
            'BOOTSTRAP_ADMIN_PASSWORD_FILE': str(self.password_file),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_create_once_does_not_reset_changed_password_or_print_secret(self):
        output = io.StringIO()
        call_command('bootstrap_operator', stdout=output)
        user = get_user_model().objects.get(username='operator@example.com')
        self.assertTrue(user.is_superuser and user.is_active and user.is_staff)
        self.assertTrue(user.check_password(self.password_file.read_text()))
        self.assertNotIn(self.password_file.read_text(), output.getvalue())
        user.set_password('changed-by-operator')
        user.save()
        call_command('bootstrap_operator', stdout=output)
        user.refresh_from_db()
        self.assertTrue(user.check_password('changed-by-operator'))
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_existing_merchant_or_disabled_operator_is_never_promoted(self):
        User = get_user_model()
        user = User.objects.create_user(username='operator@example.com', email='operator@example.com')
        with self.assertRaises(CommandError):
            call_command('bootstrap_operator')
        user.refresh_from_db()
        self.assertFalse(user.is_superuser)
        user.is_superuser = user.is_staff = True
        user.is_active = False
        user.save()
        with self.assertRaises(CommandError):
            call_command('bootstrap_operator')

    def test_email_collision_and_weak_password_cannot_bootstrap(self):
        User = get_user_model()
        User.objects.create_user(username='merchant', email='OPERATOR@example.com')
        with self.assertRaises(CommandError):
            call_command('bootstrap_operator')
        User.objects.all().delete()
        self.password_file.write_text('short')
        with self.assertRaises(CommandError):
            call_command('bootstrap_operator')
        self.assertFalse(User.objects.exists())
