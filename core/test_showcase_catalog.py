from decimal import Decimal
import re

from django.test import TestCase
from django.urls import reverse

from .models import Plan
from .showcase_catalog import LIMITS
from .showcase_data import SOLUTIONS, TOPICS


class ShowcaseCatalogTests(TestCase):
    def test_public_pages_render_the_same_prices_limits_and_staff_examples(self):
        expected = {
            'starter': ('600', (1, 20, 100, 4, 1), (1, 2, 1, 0)),
            'growth': ('1300', (1, 60, 300, 12, 3), (2, 5, 3, 2)),
            'business': ('2500', (3, 120, 800, 40, 6), (5, 20, 10, 5)),
        }
        for lang in ('ar', 'en'):
            page = self.client.get(reverse('pricing'), {'lang': lang})
            home = self.client.get(reverse('home'), {'lang': lang})
            pages = [(home, 'data-tier="{}"'), (page, 'id="plan-{}"')]
            for slug, (price, limits, staff) in expected.items():
                for response, attribute in pages:
                    source = response.content.decode()
                    card = re.search(r'<article [^>]*' + attribute.format(slug) + r'[^>]*>(.*?)</article>',
                                     source, re.S).group(1)
                    self.assertIn(f'data-tier-price>{price}</bdi>', card)
                    self.assertIn('Monthly price' if lang == 'en' else 'السعر الشهري', card)
                    for key, count in zip(LIMITS, limits):
                        self.assertIn(f'data-tier-limit="{key}">{count}<', card)
                    for role, count in zip(('admin', 'waiter', 'kitchen', 'cashier'), staff):
                        self.assertIn(f'data-tier-role="{role}">{count}<', card)
                self.assertEqual(sum(staff), limits[3])
                self.assertGreaterEqual(staff[2], limits[0])
                self.assertLessEqual(limits[4], staff[2])
                contact = self.client.get(reverse('contact'), {'plan': slug, 'lang': lang})
                for key, count in zip(LIMITS, limits):
                    self.assertContains(contact, f'data-tier-limit="{key}">{count}<')
                self.assertContains(contact, f'<bdi>{price}</bdi>')
                self.assertContains(home, '#plan-' + slug)
            for key, values in [('price', ['600', '1300', '2500']),
                                *((key, [tier[1][i] for tier in expected.values()]) for i, key in enumerate(LIMITS))]:
                row = re.search(rf'<tr data-comparison="{key}">(.*?)</tr>', page.content.decode(), re.S).group(1)
                self.assertEqual(re.findall(r'<td>(.*?)</td>', row), list(map(str, values)))
            self.assertContains(home, 'data-input="subscription" value="600"')
            business = page.context['plans'][2]
            details = {text for group in business['included'] for text in group['items']}
            available = {entry[2 + (lang == 'en')] for topic in TOPICS for entry in topic.capabilities
                         if entry[1] == 'available'}
            self.assertEqual(details, available)
            self.assertNotContains(page, 'By agreement' if lang == 'en' else 'حسب الاتفاق')

    def test_annual_prices_discounts_and_trial_match_the_inquiry(self):
        Plan.objects.filter(name='Starter').update(price_monthly=Decimal('610.77'))
        for lang, direction, trial, annual_label in (
            ('ar', 'rtl', '14 يومًا', 'السعر السنوي'),
            ('en', 'ltr', '14 days', 'Annual price'),
        ):
            with self.subTest(lang=lang):
                page = self.client.get(reverse('pricing'), {'lang': lang, 'billing': 'annual'})
                self.assertContains(page, f'dir="{direction}"')
                self.assertContains(page, annual_label)
                rows = {key: values for key, _, values in page.context['rows']}
                self.assertEqual(rows['price'], ['6596.32', '13260', '24000'])
                self.assertEqual(rows['annual_discount'], ['10%', '15%', '20%'])
                self.assertEqual(rows['trial'], [trial] * 3)
                for price in rows['price']:
                    self.assertContains(page, f'<bdi data-tier-price>{price}</bdi>')
                self.assertContains(page, 'plan=starter&amp;billing=annual')
                contact = self.client.get(reverse('contact'), {'lang': lang, 'plan': 'starter', 'billing': 'annual'})
                self.assertContains(contact, '<bdi>6596.32</bdi>')
                self.assertContains(contact, '<bdi dir="ltr">10%</bdi>')
                self.assertContains(contact, trial)
                self.assertIn('annual' if lang == 'en' else 'السنوية', contact.context['form'].initial['message'])

                monthly = self.client.get(reverse('pricing'), {'lang': lang, 'billing': 'monthly'})
                self.assertContains(monthly, '<bdi data-tier-price>610.77</bdi>')
                self.assertContains(monthly, trial)
                self.assertContains(monthly, 'plan=starter&amp;billing=monthly')
                self.assertNotContains(monthly, '6596.32')
                contact = self.client.get(reverse('contact'), {'lang': lang, 'plan': 'starter', 'billing': 'monthly'})
                self.assertContains(contact, '<bdi>610.77</bdi>')
                self.assertIn('monthly' if lang == 'en' else 'الشهرية', contact.context['form'].initial['message'])

    def test_configured_changes_appear_on_all_public_surfaces(self):
        plan = Plan.objects.get(name='Business')
        Plan.objects.filter(pk=plan.pk).update(price_monthly='3100.50', max_branches=1, max_stations=0)
        for seats in (0, 1, 2, 11):
            plan.max_staff = seats
            plan.save(update_fields=['max_staff'])
            page = self.client.get(reverse('pricing'), {'lang': 'en'})
            business = page.context['plans'][2]
            self.assertEqual(sum(business['staff'].values()), seats)
            self.assertTrue(all(count >= 0 for count in business['staff'].values()))
            self.assertEqual(business['price'], '3100.5')
            self.assertNotContains(page, '2500')
            self.assertNotIn('multi-branch', [g['slug'] for g in business['included']])
            teaser = self.client.get(reverse('plan_teaser')).json()['configured']
            self.assertEqual(teaser[2]['limits']['max_staff'], seats)
        Plan.objects.filter(name='Starter').update(price_monthly='610.75')
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'data-input="subscription" value="610.75"')
        self.assertContains(home, 'data-tier-price>610.75<')

    def test_all_categories_and_solutions_resolve_in_both_languages(self):
        for language in ('ar', 'en'):
            en = language == 'en'
            for name, entries in [('feature_detail', TOPICS), ('solution_detail', SOLUTIONS)]:
                for entry in entries:
                    with self.subTest(language=language, slug=entry.slug):
                        response = self.client.get(reverse(name, args=[entry.slug]), {'lang': language})
                        self.assertContains(response, entry.headline[en])
                        self.assertContains(response, 'lang="' + language + '"')
                        if name == 'feature_detail':
                            for anchor, state, ar, eng in entry.capabilities:
                                self.assertContains(response, 'id="' + anchor + '"')
                                self.assertContains(response, (ar, eng)[en])

    def test_intersect_search_category_role_and_availability(self):
        response = self.client.get(reverse('features'), {
            'q': 'Excel', 'role': 'owner', 'category': 'digital-menu', 'state': 'available', 'lang': 'en',
        })
        entries = response.context['entries']
        self.assertEqual([item['slug'] for item in entries], ['digital-menu'])
        self.assertEqual([item['anchor'] for item in entries[0]['items']], ['publish'])
        self.assertContains(response, reverse('feature_detail', args=['digital-menu']) + '?lang=en#publish')
        self.assertContains(response, 'value="digital-menu" selected')
        empty = self.client.get(reverse('features'), {'q': 'no-matching-term', 'lang': 'en'})
        self.assertContains(empty, 'No matching features')
        self.assertContains(empty, reverse('features') + '?lang=en#catalog-filters')
        self.assertEqual(len(self.client.get(reverse('features')).context['entries']), len(TOPICS))

    def test_only_recognized_configured_tiers_are_public(self):
        Plan.objects.create(name='Private venue agreement', price_monthly=Decimal('100.88'))
        Plan.objects.filter(name='Growth').update(price_monthly=Decimal('1025.25'), max_branches=2, max_tables=43)
        response = self.client.get(reverse('pricing'), {'lang': 'en'})
        self.assertContains(response, '1025.25')
        self.assertContains(response, 'Monthly price')
        self.assertNotContains(response, 'Private venue agreement')
        self.assertNotContains(response, '100.88')
        self.assertEqual(response.context['plans'][1]['limits']['max_tables'], 43)
        self.assertEqual(Plan.objects.count(), 4)

    def test_plan_filters_select_available_capabilities_within_capacity(self):
        Plan.objects.filter(name='Starter').update(max_stations=0)
        Plan.objects.filter(name='Growth').update(max_branches=2)
        for tier, slug, count in [
            ('starter', 'multi-branch', 0), ('growth', 'multi-branch', 1),
            ('starter', 'kitchen-display', 0), ('growth', 'kitchen-display', 1),
            ('common', 'reservations', 0), ('common', 'security', 1), ('unknown', 'security', 0),
        ]:
            with self.subTest(plan=tier, category=slug):
                entries = self.client.get(reverse('features'), {
                    'category': slug, 'plan': tier,
                }).context['entries']
                self.assertEqual(len(entries), count)
                self.assertTrue(all(i['status'] == 'available' for e in entries for i in e['items']))

    def test_unknown_slugs_and_read_only_methods(self):
        for name in ('solution_detail', 'feature_detail'):
            self.assertEqual(self.client.get(reverse(name, args=['absent'])).status_code, 404)
        for name in ('pricing', 'solutions', 'features'):
            self.assertEqual(self.client.post(reverse(name)).status_code, 405)
