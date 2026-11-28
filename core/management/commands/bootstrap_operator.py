import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction

from config.container_env import file_secret


class Command(BaseCommand):
    help = 'Create the configured initial platform operator once; never reset or promote existing users.'

    @transaction.atomic
    def handle(self, *args, **options):
        email = os.getenv('BOOTSTRAP_ADMIN_EMAIL', '').strip().lower()
        if not email:
            raise CommandError('BOOTSTRAP_ADMIN_EMAIL is required.')
        try:
            validate_email(email)
        except ValidationError:
            raise CommandError('BOOTSTRAP_ADMIN_EMAIL must be a valid email.') from None
        User = get_user_model()
        existing = User.objects.filter(username=email).first()
        if existing:
            if not (existing.is_active and existing.is_staff and existing.is_superuser):
                raise CommandError('Bootstrap username already exists without active operator privileges.')
            self.stdout.write('Initial operator already exists; credentials unchanged.')
            return
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError('Bootstrap email already belongs to another account.')
        try:
            password = file_secret(os.environ, 'BOOTSTRAP_ADMIN_PASSWORD')
        except RuntimeError as error:
            raise CommandError(str(error)) from None
        if len(password) < 20:
            raise CommandError('Bootstrap password must contain at least 20 characters.')
        User.objects.create_superuser(username=email, email=email, password=password)
        self.stdout.write(self.style.SUCCESS('Initial operator created. Read its password from the private secret file.'))
