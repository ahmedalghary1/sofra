from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse


class LoginFormTests(TestCase):
    def test_superuser_can_sign_in_with_email_separate_from_username(self):
        user = User.objects.create_superuser('SiteAdmin', 'Operator@example.com', 'Altitude!73840')
        response = self.client.post(reverse('login'), {'email': 'OPERATOR@example.com', 'password': 'Altitude!73840'})
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_shared_email_does_not_select_a_superuser(self):
        User.objects.create_superuser('SiteAdmin', 'shared@example.com', 'Altitude!73840')
        User.objects.create_user('second', 'Shared@example.com', 'Another!82020')
        response = self.client.post(reverse('login'), {'email': 'shared@example.com', 'password': 'Altitude!73840'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_expired_form_rejects_then_fresh_form_signs_in(self):
        user = User.objects.create_superuser('login@example.com', 'login@example.com', 'Altitude!73840')
        client = Client(enforce_csrf_checks=True)
        url = reverse('login')
        response = client.get(url)
        self.assertIn('no-store', response['Cache-Control'])
        old_token = client.cookies[settings.CSRF_COOKIE_NAME].value
        client.cookies[settings.CSRF_COOKIE_NAME] = 'a' * 32
        payload = {'email': user.username, 'password': 'Altitude!73840', 'csrfmiddlewaretoken': old_token}
        self.assertContains(client.post(url, payload), 'تعذّر التحقق من النموذج', status_code=403)
        self.assertNotIn('_auth_user_id', client.session)
        client.get(url)
        payload['csrfmiddlewaretoken'] = client.cookies[settings.CSRF_COOKIE_NAME].value
        self.assertRedirects(client.post(url, payload), reverse('home'), fetch_redirect_response=False)
        self.assertEqual(int(client.session['_auth_user_id']), user.pk)

    def test_valid_token_still_requires_trusted_origin(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse('login'))
        for origin in ('null', 'https://another-site.example'):
            response = client.post(reverse('login'), {
                'csrfmiddlewaretoken': client.cookies[settings.CSRF_COOKIE_NAME].value,
                'email': 'login@example.com', 'password': 'invalid',
            }, HTTP_ORIGIN=origin)
            self.assertEqual(response.status_code, 403)

    @override_settings(ALLOWED_HOSTS=['serveza.pythonanywhere.com'])
    def test_public_domain_signs_in_through_pythonanywhere_proxy(self):
        user = User.objects.create_superuser('proxy@example.com', 'proxy@example.com', 'Altitude!73840')
        client = Client(enforce_csrf_checks=True, HTTP_HOST='serveza.pythonanywhere.com')
        url = reverse('login')
        client.get(url, secure=True)
        response = client.post(url, {
            'email': user.email, 'password': 'Altitude!73840',
            'csrfmiddlewaretoken': client.cookies[settings.CSRF_COOKIE_NAME].value,
        }, secure=True, HTTP_ORIGIN='https://servezaflow.com',
            HTTP_REFERER='https://servezaflow.com/login/?lang=en')
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.assertEqual(int(client.session['_auth_user_id']), user.pk)
