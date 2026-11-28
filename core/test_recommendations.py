from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Branch, Category, DiningTable, ItemRecommendation, Membership, MenuItem,
    MenuSize, ModifierGroup, ModifierOption, Order, OrderLine, RecommendationConversion,
    RecommendationEvent, RecommendationExposure, RecommendationPairStat, Restaurant, Plan, Subscription)
from .recommendations import (attribute_order, context_key, minimum_price, recommendation_report,
                              prune_recommendation_events, rebuild_stats, select_recommendations)
from .services import create_order
from .online import create_online_order


class RecommendationTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name='مطعم الاختبار', slug='recommendation-tests', online_ordering_enabled=True)
        self.subscription = Subscription.objects.create(restaurant=self.restaurant,
            plan=Plan.objects.create(name='Recommendation tests', max_items=100, max_branches=2),
            status='active', ends_at=timezone.now()+timedelta(days=30))
        self.branch = Branch.objects.create(restaurant=self.restaurant, recommendations_enabled=True)
        self.other = Branch.objects.create(restaurant=self.restaurant, name='Other')
        self.category = Category.objects.create(branch=self.branch, name='Menu')
        self.burger, self.fries, self.drink, self.dessert = [MenuItem.objects.create(category=self.category, name=name, price=price)
            for name, price in [('برجر', '100.25'), ('بطاطس', '25.50'), ('مشروب', '15.75'), ('براوني', '40.00')]]
        self.foreign = MenuItem.objects.create(category=Category.objects.create(branch=self.other, name='Foreign'), name='Foreign', price=1)
        self.table = DiningTable.objects.create(branch=self.branch, name='1')
        self.link(self.burger, self.fries)
        self.link(self.burger, self.drink, 1)
        self.client_id = str(uuid4())
        self.url = reverse('table_recommendations', args=[self.table.public_token])
        self.online_url = reverse('online_recommendations', args=[self.restaurant.slug, self.branch.pk])
        self.owner = User.objects.create_user('recommend-owner')
        Membership.objects.create(user=self.owner, restaurant=self.restaurant, role='owner')

    def link(self, source, target, priority=0):
        return ItemRecommendation.objects.create(branch=self.branch, source_item=source, target_item=target, priority=priority)

    def query(self, **overrides):
        payload = {'item_ids': [self.burger.pk], 'context': self.client_id, 'session_token': str(self.table.session_token)}
        payload.update(overrides)
        return self.client.post(self.url, payload, content_type='application/json')

    def exposure(self):
        response = self.query().json()
        self.assertTrue(response['ok'])
        exposure = response['exposure']
        self.query(kind='shown', exposure=exposure)
        self.query(kind='added', exposure=exposure, item_id=self.fries.pk)
        return exposure

    def payload(self, exposure):
        return {'idempotency_key': str(uuid4()), 'session_token': str(self.table.session_token),
            'lines': [{'item_id': self.burger.pk, 'quantity': 1}, {'item_id': self.fries.pk, 'quantity': 2}],
            'recommendation_context': self.client_id,
            'recommendations': [{'line_index': 1, 'quantity': 2, 'exposure': exposure}]}

    def test_branch_defaults_off_and_links_are_directional_and_tenant_validated_on_save(self):
        self.assertFalse(self.other.recommendations_enabled)
        self.assertFalse(select_recommendations(self.branch, {self.drink.pk})[0])
        for target in (self.burger, self.foreign):
            with self.assertRaises(ValidationError): self.link(self.burger, target)
        with self.assertRaises(ValidationError): self.link(self.burger, self.fries)
        self.burger.category = self.foreign.category
        with self.assertRaises(ValidationError): self.burger.save()
        self.category.branch = self.other
        with self.assertRaises(ValidationError): self.category.save()

    def test_selection_deduplicates_and_excludes_cart_items_with_stable_priority(self):
        self.link(self.drink, self.fries)
        self.link(self.burger, self.dessert, 2)
        self.assertEqual([x.pk for x in select_recommendations(self.branch, {self.burger.pk})[0]],
                         [self.fries.pk, self.drink.pk, self.dessert.pk])
        result, _ = select_recommendations(self.branch, {self.burger.pk, self.fries.pk})
        self.assertEqual([x.pk for x in result], [self.drink.pk, self.dessert.pk])
        self.assertEqual(len(select_recommendations(self.branch, {self.burger.pk, self.drink.pk})[0]), 2)

    def test_unavailable_archived_hidden_and_impossible_modifiers_do_not_get_suggested(self):
        for field in ('available', 'archived'):
            setattr(self.fries, field, field == 'archived'); self.fries.save()
            self.assertNotIn(self.fries.pk, [x.pk for x in select_recommendations(self.branch, {self.burger.pk})[0]])
            setattr(self.fries, field, field == 'available'); self.fries.save()
        group = ModifierGroup.objects.create(item=self.fries, name='Required', min_select=1, max_select=1)
        self.assertIsNone(minimum_price(self.fries))
        self.assertNotIn(self.fries.pk, [x.pk for x in select_recommendations(self.branch, {self.burger.pk})[0]])
        ModifierOption.objects.create(group=group, name='Sauce', price='3.25')
        self.assertEqual(minimum_price(self.fries), (Decimal('28.75'), True))
        MenuSize.objects.create(item=self.fries, name='Small', price='20.00')
        self.assertEqual(minimum_price(self.fries)[0], Decimal('23.25'))
        self.category.visible = False; self.category.save()
        self.assertFalse(select_recommendations(self.branch, {self.burger.pk})[0])

    def test_api_data_is_public_scoped_and_no_store_and_deduplicates_exposure(self):
        first = self.query()
        self.assertEqual(first.status_code, 200)
        self.assertIn('no-store', first['Cache-Control'])
        self.assertEqual(first.json()['exposure'], self.query().json()['exposure'])
        self.assertEqual([row['id'] for row in first.json()['items']], [self.fries.pk, self.drink.pk])
        self.assertNotIn('scope_key', first.content.decode())
        self.assertEqual(RecommendationExposure.objects.count(), 1)
        self.assertEqual(self.query(context=str(uuid4())).status_code, 200)
        self.assertEqual(RecommendationExposure.objects.count(), 2)

    def test_bad_payloads_stale_sessions_and_foreign_items_are_rejected(self):
        for raw in ([self.foreign.pk], [True], ['1'], [-1], [2**64], list(range(41)), None):
            self.assertEqual(self.query(item_ids=raw).status_code, 400)
        self.assertEqual(self.query(context='broken').status_code, 400)
        self.assertEqual(self.query(session_token=str(uuid4())).status_code, 409)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, [], content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(self.url, 'x'*17000, content_type='application/json').status_code, 400)
        self.assertEqual(self.query(item_ids=[]).json()['items'], [])

    def test_paused_contexts_and_online_entitlements(self):
        self.assertEqual(self.client.post(self.online_url, {'context': self.client_id, 'item_ids': [self.burger.pk]},
                                        content_type='application/json').status_code, 200)
        self.subscription.plan.enabled_features = []; self.subscription.plan.save()
        self.assertEqual(self.client.post(self.online_url, {'context': self.client_id, 'item_ids': [self.burger.pk]},
                                        content_type='application/json').status_code, 409)
        self.assertEqual(self.query().status_code, 200)
        self.branch.accepting_orders = False; self.branch.save()
        self.assertEqual(self.query().status_code, 409)

    def test_csrf_is_required_for_read_and_event_posts(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url, {}, content_type='application/json').status_code, 403)

    def test_exposure_failure_still_returns_suggestions(self):
        with patch('core.recommendation_views.exposure_for', side_effect=RuntimeError('test')):
            response = self.query()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['items']), 2)
        self.assertIsNone(response.json()['exposure'])

    def test_events_are_deduplicated_and_must_match_visible_exposure_and_context(self):
        exposure = self.exposure()
        self.query(kind='shown', exposure=exposure)
        self.query(kind='added', exposure=exposure, item_id=self.fries.pk)
        self.assertEqual(RecommendationEvent.objects.count(), 1)
        self.assertEqual(self.query(kind='added', exposure=exposure, item_id=self.burger.pk).status_code, 400)
        self.assertEqual(self.query(kind='shown', exposure=exposure, context=str(uuid4())).status_code, 400)
        RecommendationExposure.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.query(kind='shown', exposure=exposure).status_code, 400)

    def test_attribution_is_once_uses_snapshot_and_does_not_change_idempotency(self):
        exposure = self.exposure()
        payload = self.payload(exposure)
        order = create_order(self.table, payload, require_session=True)
        conversion = RecommendationConversion.objects.get()
        self.assertEqual(conversion.quantity, 2)
        self.assertEqual(conversion.amount, Decimal('51.00'))
        self.assertEqual(order.total, Decimal('151.25'))
        payload['recommendations'] = []
        self.assertEqual(create_order(self.table, payload, require_session=True).pk, order.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.fries.price = Decimal('99'); self.fries.save()
        stats = recommendation_report(Branch.objects.filter(pk=self.branch.pk))
        self.assertEqual(stats['amount'], Decimal('51.00'))
        self.assertEqual(stats['acceptance'], Decimal('100.0'))
        order.status = 'cancelled'; order.save()
        self.assertEqual(recommendation_report([self.branch])['amount'], 0)

    def test_deleted_reduced_and_foreign_provenance_are_not_overcounted(self):
        exposure = self.exposure()
        payload = self.payload(exposure)
        payload['lines'][1]['quantity'] = 1
        order = create_order(self.table, payload, require_session=True)
        self.assertEqual(RecommendationConversion.objects.get().quantity, 1)
        payload = self.payload(exposure)
        create_order(self.table, payload, require_session=True)
        self.assertEqual(RecommendationConversion.objects.count(), 1)
        exposure = self.exposure()
        payload = self.payload(exposure); payload['lines'].pop()
        create_order(self.table, payload, require_session=True)
        self.assertEqual(RecommendationConversion.objects.count(), 1)
        payload = self.payload(exposure); payload['recommendation_context'] = str(uuid4())
        create_order(self.table, payload, require_session=True)
        self.assertEqual(RecommendationConversion.objects.count(), 1)

    def test_telemetry_failure_never_prevents_saving_an_order(self):
        exposure = self.exposure()
        with patch('core.recommendations.RecommendationConversion.objects.get_or_create', side_effect=RuntimeError('test')):
            create_order(self.table, self.payload(exposure), require_session=True)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderLine.objects.count(), 2)
        self.assertEqual(RecommendationConversion.objects.count(), 0)

    def test_online_conversion_uses_same_rules_and_keeps_delivery_out_of_attribution(self):
        data = {'context': self.client_id, 'item_ids': [self.burger.pk]}
        exposure = self.client.post(self.online_url, data, content_type='application/json').json()['exposure']
        for kind in ('shown', 'added'):
            self.client.post(self.online_url, dict(data, kind=kind, exposure=exposure, item_id=self.fries.pk), content_type='application/json')
        payload = dict(self.payload(exposure), fulfillment='pickup', customer_name='Guest', customer_phone='01012345678',
                       reviewed_total='151.25', reviewed_delivery_fee='0.00')
        order = create_online_order(self.branch, payload)
        self.assertEqual(order.source, 'online')
        self.assertEqual(RecommendationConversion.objects.get().amount, Decimal('51.00'))

    def test_settings_and_links_can_only_be_managed_by_scoped_owner_or_manager(self):
        url = reverse('manage_menu', args=[self.restaurant.slug])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url, {}).status_code, 302)
        result = self.client.post(url, {'action': 'recommendation_settings', 'recommendations_enabled': 'on'})
        self.assertEqual(result.status_code, 302)
        self.client.post(url, {'action': 'recommendation_save', 'item_id': self.burger.pk,
                             'target_item': self.foreign.pk, 'priority': 0, 'active': 'on'})
        self.assertFalse(ItemRecommendation.objects.filter(target_item=self.foreign).exists())
        self.client.post(url, {'action': 'recommendation_save', 'item_id': self.burger.pk,
                             'target_item': self.dessert.pk, 'priority': 2, 'active': 'on'})
        link = ItemRecommendation.objects.get(target_item=self.dessert)
        self.client.post(url, {'action': 'recommendation_delete', 'item_id': self.burger.pk, 'link_id': link.pk})
        self.assertFalse(ItemRecommendation.objects.filter(pk=link.pk).exists())
        for language in ('ar', 'en'):
            response = self.client.get(url, {'lang': language})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.burger.name)
        Membership.objects.filter(user=self.owner).update(role='waiter')
        self.assertEqual(self.client.post(url, {'action': 'recommendation_settings'}).status_code, 403)

    def test_learned_ranking_requires_evidence_freshness_and_manual_priority(self):
        self.branch.recommendations_learning_enabled = True
        ItemRecommendation.objects.filter(target_item=self.drink).update(priority=0)
        stat = RecommendationPairStat.objects.create(branch=self.branch, source_item=self.burger, target_item=self.drink,
            eligible_orders=100, source_orders=20, target_orders=20, pair_orders=15,
            period_start=timezone.now()-timedelta(days=90), built_at=timezone.now())
        self.assertEqual(select_recommendations(self.branch, {self.burger.pk})[0][0].pk, self.drink.pk)
        for field, value in [('eligible_orders', 99), ('pair_orders', 9), ('source_orders', 19),
                             ('built_at', timezone.now()-timedelta(hours=49)), ('target_orders', 100)]:
            old = getattr(stat, field); setattr(stat, field, value); stat.save()
            self.assertEqual(select_recommendations(self.branch, {self.burger.pk})[0][0].pk, self.fries.pk)
            setattr(stat, field, old); stat.save()
        ItemRecommendation.objects.filter(target_item=self.drink).update(priority=1)
        self.assertEqual(select_recommendations(self.branch, {self.burger.pk})[0][0].pk, self.fries.pk)

    def test_pair_rebuild_counts_items_once_and_only_completed_orders_of_same_branch(self):
        for status in ('served', 'cancelled', 'new'):
            order = create_order(self.table, self.payload(None), require_session=True)
            order.status = status; order.save()
            if status == 'served':
                OrderLine.objects.create(order=order, item=self.fries, item_name='Snapshot', unit_price='25.50',
                                         quantity=3, line_total='76.50')
        rebuild_stats(self.branch)
        stat = RecommendationPairStat.objects.get(source_item=self.burger, target_item=self.fries)
        self.assertEqual((stat.eligible_orders, stat.source_orders, stat.target_orders, stat.pair_orders), (1, 1, 1, 1))
        rebuild_stats(self.other)
        self.assertFalse(RecommendationPairStat.objects.filter(branch=self.other).exists())
        self.assertEqual(RecommendationPairStat.objects.filter(branch=self.branch).count(), 2)

    def test_retention_keeps_anonymous_counts_and_historical_attribution_without_detailed_context(self):
        exposure = self.exposure()
        order = create_order(self.table, self.payload(exposure), require_session=True)
        past = timezone.now()-timedelta(days=95)
        RecommendationExposure.objects.update(created_at=past, shown_at=past)
        RecommendationConversion.objects.update(shown_at=past)
        before = recommendation_report([self.branch])
        self.assertEqual(prune_recommendation_events(), 1)
        self.assertEqual(prune_recommendation_events(), 0)
        self.assertFalse(RecommendationExposure.objects.exists())
        self.assertFalse(RecommendationEvent.objects.exists())
        self.assertEqual(recommendation_report([self.branch]), before)
        order.status = 'cancelled'; order.save()
        self.assertEqual(recommendation_report([self.branch])['amount'], Decimal('0'))

    def test_report_respects_time_periods_and_does_not_expose_other_branches(self):
        exposure = self.exposure()
        create_order(self.table, self.payload(exposure), require_session=True)
        self.assertEqual(recommendation_report([self.other])['shown'], 0)
        start = timezone.now()+timedelta(days=1)
        self.assertEqual(recommendation_report([self.branch], start, start+timedelta(days=1))['orders'], 0)
        self.client.force_login(self.owner)
        response = self.client.get(reverse('reports',args=[self.restaurant.slug]), {'lang':'en'})
        self.assertEqual(response.context['recommendation_metrics']['orders'], 1)
        self.assertContains(response, 'Attributed item value')

    def test_management_does_not_duplicate_all_target_options_for_every_item_and_has_native_fallback(self):
        self.client.force_login(self.owner)
        url=reverse('manage_menu',args=[self.restaurant.slug])
        response=self.client.get(url)
        self.assertEqual(response.content.decode().count('>'+self.fries.name+'</option>'),1)
        response=self.client.get(url,{'recommend_item':self.burger.pk})
        self.assertEqual(response.content.decode().count('>'+self.fries.name+'</option>'),2)
        self.assertContains(response,'data-recommendation-loaded="true"')

    def test_selection_query_budget_is_constant_with_many_links(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for index in range(25):
            item = MenuItem.objects.create(category=self.category, name=f'Extra {index}', price='2.50')
            self.link(self.burger, item, index+2)
        with CaptureQueriesContext(connection) as captured:
            candidates, _ = select_recommendations(self.branch, {self.burger.pk})
        self.assertEqual(len(candidates), 3)
        self.assertLessEqual(len(captured), 5)

    def test_catalog_marks_implemented_and_future_capabilities_separately(self):
        from .showcase_data import TOPICS
        topic = next(topic for topic in TOPICS if topic.slug == 'upselling')
        states = {entry[0]:entry[1] for entry in topic.capabilities}
        self.assertEqual([states[key] for key in ('suggestions','measurement','learning')], ['available']*3)
        self.assertEqual(states['advanced'], 'planned')
        for language in ('ar','en'):
            response = self.client.get(reverse('feature_detail',args=['upselling']), {'lang':language})
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Browser simulation' if language=='en' else 'محاكاة داخل المتصفح')

    def test_reference_fixture_api_latency_is_recorded_for_release_review(self):
        import json
        import statistics
        from pathlib import Path
        from time import perf_counter
        for index in range(50):
            item = MenuItem.objects.create(category=self.category,name=f'Performance item {index}',price='9.50')
            self.link(self.burger,item,index+2)
        self.branch.recommendations_learning_enabled = True; self.branch.save()
        timings = []
        for _ in range(30):
            started = perf_counter()
            response = self.query(context=str(uuid4()))
            timings.append((perf_counter()-started)*1000)
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.json()['items']),3)
        path = Path('tmp/recommendations-ui'); path.mkdir(parents=True,exist_ok=True)
        (path/'api-performance.json').write_text(json.dumps({'environment':'local Django test client / SQLite',
            'samples':len(timings),'menu_items':54,'approved_links':52,'learning_enabled':True,
            'median_ms':round(statistics.median(timings),2),'p95_ms':round(sorted(timings)[28],2),
            'max_ms':round(max(timings),2),'production_load_verified':False},indent=2),encoding='utf-8')


from django.test import TransactionTestCase, skipUnlessDBFeature


class RecommendationConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_exposure_creation_deduplicates_under_branch_lock(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        from .recommendations import exposure_for
        restaurant = Restaurant.objects.create(name='Race',slug='recommendation-race')
        branch = Branch.objects.create(restaurant=restaurant)
        item = MenuItem.objects.create(category=Category.objects.create(branch=branch,name='Menu'),name='Tea',price='5.00')
        barrier = Barrier(2)
        def run():
            try:
                barrier.wait(timeout=10)
                return exposure_for(branch,'online','s'*64,{42},[item],'rules-v1').pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.submit(run), executor.submit(run)
            self.assertEqual(first.result(timeout=30),second.result(timeout=30))
        self.assertEqual(RecommendationExposure.objects.count(),1)
