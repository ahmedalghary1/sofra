from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import Plan
from .showcase_catalog import public_plans
from .showcase_data import SOLUTIONS, TOPICS


class ShowcaseCatalogTests(TestCase):
    def setUp(self):
        # These catalog cases exercise fallback and partially configured installations.
        Plan.objects.filter(name__in=('Starter', 'Growth', 'Business')).delete()

    def test_proposed_pricing_seats_and_included_functions(self):
        for lang in ('ar', 'en'):
            page = self.client.get(reverse('pricing'), {'lang': lang})
            starter, growth, business = page.context['plans']
            self.assertEqual([p['price'] for p in (starter, growth, business)], ['600', '1300', '2500'])
            self.assertEqual([p['limits']['max_staff'] for p in (starter, growth, business)], [5, 20, 60])
            self.assertEqual(business['limits']['max_branches'], 3)
            self.assertEqual(business['limits']['max_stations'], 8)
            for plan in (starter, growth, business):
                self.assertEqual(sum(plan['staff'].values()), plan['limits']['max_staff'])
                self.assertContains(page, f'id="plan-{plan["slug"]}"')
            details = {text for group in business['included'] for text in group['items']}
            expected = {entry[2 + (lang == 'en')] for topic in TOPICS for entry in topic.capabilities
                        if entry[1] == 'available'}
            self.assertEqual(details, expected)
            self.assertNotContains(page, 'By agreement' if lang == 'en' else 'حسب الاتفاق')
            home = self.client.get(reverse('home'), {'lang': lang})
            for plan in (starter, growth, business):
                self.assertContains(home, f'#plan-{plan["slug"]}')

    def test_named_configuration_overrides_seats_and_preserves_the_proposal(self):
        plan = Plan.objects.create(name='Business', price_monthly='3100.50', max_branches=1, max_stations=0)
        for seats in (0, 1, 2, 11):
            plan.max_staff = seats
            plan.save(update_fields=['max_staff'])
            page = self.client.get(reverse('pricing'), {'lang': 'en'})
            business = page.context['plans'][2]
            self.assertEqual(sum(business['staff'].values()), seats)
            self.assertTrue(all(count >= 0 for count in business['staff'].values()))
            self.assertEqual(business['price'], '3100.5')
            self.assertContains(page, '2500')
            self.assertNotIn('multi-branch', [g['slug'] for g in business['included']])
            teaser = self.client.get(reverse('plan_teaser')).json()['configured']
            self.assertEqual([(t['slug'], t['limits']['max_staff']) for t in teaser], [('business', seats)])

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
        Plan.objects.create(name='Growth', price_monthly=Decimal('1025.25'), max_branches=2, max_tables=43)
        response = self.client.get(reverse('pricing'), {'lang': 'en'})
        self.assertContains(response, '1025.25')
        self.assertContains(response, 'Configured tier')
        self.assertContains(response, 'Proposed pricing')
        self.assertNotContains(response, 'Private venue agreement')
        self.assertNotContains(response, '100.88')
        self.assertEqual(response.context['plans'][1]['limits']['max_tables'], 43)
        self.assertEqual(Plan.objects.count(), 2)
        with self.assertNumQueries(0):
            self.assertEqual(public_plans(configured=False)[1]['price'], '1300')

    def test_plan_filters_select_available_capabilities_within_capacity(self):
        Plan.objects.create(name='Starter', max_branches=1, max_stations=0)
        Plan.objects.create(name='Growth', max_branches=2, max_stations=3)
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
