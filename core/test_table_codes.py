from datetime import timedelta
from decimal import Decimal
import csv
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import zipfile

import fitz
from PIL import Image
from pypdf import PdfReader
import zxingcpp
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (AuditLog,Branch,Category,DiningTable,Membership,MenuItem,Order,Plan,Restaurant,Subscription,TableBatch)
from .services import create_order
from .table_codes import (artwork,create_table_batch,qr_pdf,qr_png,qr_svg,qr_zip,revoke_table_code,stable_table_url)


class TableCodeTests(TestCase):
    def test_print_font_covers_shaped_arabic_letters_in_every_caption(self):
        from reportlab.pdfbase.ttfonts import TTFont
        from .table_codes import FONT, visual_text
        face = TTFont('QRArabicCoverage', str(FONT)).face
        samples = ['طاولة', 'رموز الطاولات', 'امسح لعرض المنيو', 'مطعم السلام',
                   'فرع القاهرة ١٢ · Cairo 12', 'ركن النافذة · Window', 'لا إله إلا الله']
        # Include isolated and contextual forms: the old font missed isolated waw/alef/reh.
        for letter in 'ءآأؤإئابتثجحخدذرزسشصضطظعغفقكلمنهويىة':
            samples.extend([letter, 'ب' + letter + 'ب', letter + 'ب', 'ب' + letter])
        missing = {character for text in samples for character in visual_text(text)
                   if not face.charToGlyph.get(ord(character))}
        self.assertFalse(missing, 'The QR export font is missing shaped Arabic glyphs: ' + repr(missing))

    def test_svg_and_pdf_embed_the_same_arabic_print_font_and_preserve_the_label(self):
        import base64
        import re
        from .table_codes import FONT
        self.restaurant.name = 'مطعم السلام · Garden 12'
        self.branch.name = 'فرع القاهرة ١٢ · Cairo'
        table = DiningTable.objects.create(branch=self.branch, name='طاولة العائلة ١٢ · VIP 12')
        original_url = stable_table_url(table, self.restaurant)
        svg = qr_svg(table, self.restaurant).decode()
        embedded = re.search(r'data:font/ttf;base64,([A-Za-z0-9+/=]+)', svg)
        self.assertIsNotNone(embedded)
        self.assertEqual(base64.b64decode(embedded[1]), FONT.read_bytes())
        self.assertIn(table.name, svg)
        document = fitz.open(stream=qr_pdf([table], self.restaurant, self.branch), filetype='pdf')
        self.assertTrue(any('Amiri-Regular' in item[3] for item in document[0].get_fonts()))
        self.assertEqual(self.decode(qr_png(table, self.restaurant)), original_url)
        table.refresh_from_db()
        self.assertEqual(table.name, 'طاولة العائلة ١٢ · VIP 12')
        self.assertEqual(stable_table_url(table, self.restaurant), original_url)

    def setUp(self):
        self.restaurant=Restaurant.objects.create(name='مطعم الحديقة · Garden restaurant',slug='table-code-tests')
        self.plan=Plan.objects.create(name='Table QR test',max_tables=60,max_branches=2)
        Subscription.objects.create(restaurant=self.restaurant,plan=self.plan,status='active',ends_at=timezone.now()+timedelta(days=30))
        self.branch=Branch.objects.create(restaurant=self.restaurant)
        self.other=Branch.objects.create(restaurant=self.restaurant,name='Other branch')
        self.owner=User.objects.create_user('table-code-owner')
        self.member=Membership.objects.create(user=self.owner,restaurant=self.restaurant,role='owner')
        self.client.force_login(self.owner)
        self.url=reverse('manage_tables',args=[self.restaurant.slug])

    def batch(self,count=50,start=1,key=None,branch=None):
        return create_table_batch(self.restaurant,branch or self.branch,self.owner,submission_key=key or uuid4(),count=count,start_number=start)

    def test_manager_can_create_fifty_from_dashboard_and_download_all_without_changing_existing_tables(self):
        self.member.role = 'manager'
        self.member.save(update_fields=['role'])
        original = DiningTable.objects.create(branch=self.branch, name='Window corner')
        token = original.public_token
        for language in ('ar', 'en'):
            response = self.client.get(reverse('dashboard', args=[self.restaurant.slug]), {'lang': language})
            self.assertContains(response, 'id="dashboardTableBatch"')
            self.assertContains(response, f'action="{self.url}"')
            self.assertContains(response, 'data-table-preset="50"')
            self.assertEqual(response.context['batch_form'].initial['branch_id'], self.branch.pk)
        form = response.context['batch_form']
        payload = dict(form.initial, action='bulk_create', count=50, start_number=101)
        result = self.client.post(self.url, payload)
        self.assertEqual(result.status_code, 302)
        self.assertEqual(list(self.branch.tables.exclude(pk=original.pk).order_by('number').values_list('number', flat=True)),
                         list(range(101, 151)))
        self.assertFalse(self.other.tables.exists())
        self.client.post(self.url, payload)
        self.assertEqual(self.branch.tables.count(), 51)
        download = self.client.get(reverse('table_qr_pack', args=[self.restaurant.slug, self.branch.pk]), {'format': 'pdf'})
        self.assertEqual(download.status_code, 200)
        self.assertEqual(len(PdfReader(io.BytesIO(b''.join(download.streaming_content))).pages), 13)
        original.refresh_from_db()
        self.assertEqual(original.public_token, token)
        self.assertEqual(original.name, 'Window corner')

    def test_dashboard_batch_form_is_hidden_from_other_staff_and_respects_plan_and_branch(self):
        for role in ('cashier', 'kitchen', 'waiter'):
            self.member.role = role
            self.member.save(update_fields=['role'])
            self.member.branches.add(self.branch)
            response = self.client.get(reverse('dashboard', args=[self.restaurant.slug]))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'data-table-batch')
            self.assertEqual(self.client.post(self.url, {'action': 'bulk_create', 'branch_id': self.branch.pk,
                'submission_key': uuid4(), 'count': 50, 'start_number': 1}).status_code, 403)
        self.member.role = 'manager'
        self.member.save(update_fields=['role'])
        self.plan.max_tables = 20
        self.plan.save(update_fields=['max_tables'])
        response = self.client.get(reverse('dashboard', args=[self.restaurant.slug]))
        self.assertContains(response, 'data-table-preset="50" disabled')
        self.assertEqual(response.context['remaining_tables'], 20)
        payload = dict(response.context['batch_form'].initial, action='bulk_create', count=50)
        self.client.post(self.url, payload)
        self.assertFalse(self.branch.tables.exists())
        payload.update(count=10, branch_id=self.other.pk)
        self.client.post(self.url, payload)
        self.assertFalse(self.other.tables.exists())
        self.assertFalse(self.branch.tables.exists())

    def decode(self,png):
        codes=zxingcpp.read_barcodes(Image.open(io.BytesIO(png)),formats=zxingcpp.BarcodeFormat.QRCode)
        self.assertEqual(len(codes),1)
        return codes[0].text

    def test_fifty_tables_are_atomic_numbered_and_each_identity_is_unique(self):
        batch,created=self.batch()
        self.assertTrue(created)
        tables=list(batch.tables.order_by('number'))
        self.assertEqual([table.name for table in tables],[str(n) for n in range(1,51)])
        self.assertEqual([table.number for table in tables],list(range(1,51)))
        self.assertEqual(len({table.public_token for table in tables}),50)
        self.assertEqual(AuditLog.objects.filter(action='table.batch.create').count(),1)

    def test_same_submission_never_duplicates_and_conflicting_reuse_is_rejected(self):
        key=uuid4();batch,_=self.batch(key=key)
        replay,created=self.batch(key=key)
        self.assertFalse(created);self.assertEqual(replay.pk,batch.pk)
        self.assertEqual(DiningTable.objects.count(),50)
        with self.assertRaises(ValidationError):self.batch(count=3,key=key)
        self.assertEqual(TableBatch.objects.count(),1)

    def test_limit_and_existing_number_fail_without_partial_creation(self):
        self.plan.max_tables=20;self.plan.save()
        with self.assertRaises(ValidationError):self.batch()
        self.assertFalse(DiningTable.objects.exists());self.assertFalse(TableBatch.objects.exists())
        DiningTable.objects.create(branch=self.branch,name='طاولة 01',archived=True)
        with self.assertRaises(ValidationError):self.batch(count=3)
        self.assertEqual(DiningTable.objects.count(),1)
        self.assertFalse(TableBatch.objects.exists())

    def test_validation_rejects_fractional_boolean_and_out_of_range_values(self):
        for count,start in [(0,1),(201,1),(True,1),(1.5,1),(1,0),(2,99999)]:
            with self.assertRaises(ValidationError):self.batch(count=count,start=start)
        self.assertFalse(DiningTable.objects.exists())

    def test_custom_numbered_labels_cannot_duplicate_batch_or_legacy_numbers(self):
        self.batch(count=2)
        self.client.post(self.url,{'action':'create','name':'طاولة 01'})
        self.assertEqual(DiningTable.objects.count(),2)
        DiningTable.objects.create(branch=self.branch,name='Table 03')
        self.client.post(self.url,{'action':'create','name':'3'})
        self.assertEqual(DiningTable.objects.count(),3)
        self.client.post(self.url,{'action':'create','name':'طاولة 04'})
        self.assertEqual(self.branch.tables.get(name='طاولة 04').number,4)

    def test_token_collision_rolls_back_whole_batch(self):
        with patch('core.models.secrets.token_urlsafe',return_value='duplicate-qr-token'):
            from django.db import IntegrityError
            with self.assertRaises(IntegrityError):self.batch(count=3)
        self.assertFalse(DiningTable.objects.exists());self.assertFalse(TableBatch.objects.exists())

    def test_bulk_ui_validates_plan_branch_context_and_csrf(self):
        payload={'action':'bulk_create','branch_id':self.branch.pk,'submission_key':str(uuid4()),'count':50,'start_number':1}
        result=self.client.post(self.url,payload)
        self.assertEqual(result.status_code,302)
        self.assertEqual(DiningTable.objects.count(),50)
        self.client.post(self.url,payload);self.assertEqual(DiningTable.objects.count(),50)
        self.assertEqual(Client(enforce_csrf_checks=True).post(self.url,payload).status_code,403)
        payload.update(branch_id=self.other.pk,submission_key=str(uuid4()),start_number=51,count=2)
        self.assertEqual(self.client.post(self.url,payload).status_code,200)
        self.assertFalse(self.other.tables.exists())

    def test_every_staff_endpoint_is_tenant_scoped_and_downloads_are_read_only(self):
        batch,_=self.batch(count=2)
        table=batch.tables.first()
        endpoint=reverse('table_qr',args=[self.restaurant.slug,table.pk])
        self.assertEqual(self.client.post(endpoint).status_code,405)
        self.assertEqual(Client().get(endpoint).status_code,302)
        Membership.objects.filter(user=self.owner).update(role='waiter')
        self.assertEqual(self.client.get(endpoint).status_code,403)
        self.assertEqual(self.client.get(reverse('table_qr_pack',args=[self.restaurant.slug,self.branch.pk])).status_code,403)
        Membership.objects.filter(user=self.owner).update(role='owner')
        session=self.client.session;session[f'safra_branch_{self.restaurant.pk}']=self.other.pk;session.save()
        self.assertEqual(self.client.get(endpoint).status_code,404)
        self.assertEqual(self.client.get(reverse('table_qr_pack',args=[self.restaurant.slug,self.branch.pk])).status_code,409)

    def test_style_is_separate_for_code_and_number_and_cannot_change_origin(self):
        self.batch(count=1)
        table=self.branch.tables.first();url=stable_table_url(table,self.restaurant)
        original=self.restaurant.qr_public_origin
        for colors in [('#ffffff','#193b34'),('#123456','bad'),('#ffeecc','#123456')]:
            self.assertEqual(self.client.post(self.url,{'action':'qr_style','branch_id':self.branch.pk,
                'qr_color':colors[0],'qr_number_color':colors[1]}).status_code,200)
            self.restaurant.refresh_from_db();self.assertEqual(self.restaurant.qr_color,'#193b34')
        self.assertEqual(self.client.post(self.url,{'action':'qr_style','branch_id':self.branch.pk,
            'qr_color':'#123456','qr_number_color':'#38245c','qr_public_origin':'https://attacker.example'}).status_code,302)
        self.restaurant.refresh_from_db()
        self.assertEqual((self.restaurant.qr_color,self.restaurant.qr_number_color),('#123456','#38245c'))
        self.assertEqual(self.restaurant.qr_public_origin,original)
        self.assertEqual(self.decode(qr_png(table,self.restaurant)),url)
        self.assertEqual(self.branch.tables.get().public_token,table.public_token)

    def test_url_is_pinned_across_site_settings_names_colors_and_sessions(self):
        self.batch(count=1);table=self.branch.tables.get();url=stable_table_url(table,self.restaurant)
        with override_settings(SITE_URL='https://new-canonical.example'):
            self.assertEqual(stable_table_url(table,self.restaurant),url)
            self.assertEqual(self.decode(self.client.get(reverse('table_qr',args=[self.restaurant.slug,table.pk]),HTTP_HOST='localhost').content),url)
        self.client.post(self.url,{'action':'close_session','table_id':table.pk})
        table.refresh_from_db();self.assertEqual(stable_table_url(table,self.restaurant),url)
        self.restaurant.name='اسم تجاري جديد';self.restaurant.save()
        self.assertEqual(self.decode(qr_png(table,self.restaurant)),url)
        self.restaurant.qr_public_origin='https://changed.example'
        with self.assertRaises(ValidationError):self.restaurant.save()
        table.public_token='changed-token'
        with self.assertRaises(ValidationError):table.save()

    def test_emergency_revocation_requires_owner_all_acknowledgements_and_fresh_token(self):
        self.batch(count=1);table=self.branch.tables.get();old=table.public_token
        data={'expected_token':old,'confirmation_name':table.name,'confirm_reprint':'on','reason':'Printed code was copied'}
        for field in data:
            bad=dict(data);bad.pop(field)
            with self.assertRaises(ValidationError):revoke_table_code(self.restaurant,self.branch,self.member,table.pk,bad)
        self.member.role='manager'
        with self.assertRaises(PermissionDenied):revoke_table_code(self.restaurant,self.branch,self.member,table.pk,data)
        self.member.role='owner';revoke_table_code(self.restaurant,self.branch,self.member,table.pk,data)
        table.refresh_from_db();new=table.public_token;self.assertNotEqual(new,old)
        self.assertEqual(self.client.get(reverse('public_menu',args=[old])).status_code,404)
        self.assertEqual(self.client.get(reverse('public_menu',args=[new])).status_code,200)
        with self.assertRaises(ValidationError):revoke_table_code(self.restaurant,self.branch,self.member,table.pk,data)
        table.refresh_from_db();self.assertEqual(table.public_token,new)
        self.assertEqual(AuditLog.objects.filter(action='table.qr.revoke').count(),1)

    def test_qr_files_are_private_and_malformed_export_filters_are_rejected(self):
        self.batch(count=1);table=self.branch.tables.get()
        response=self.client.get(reverse('table_qr',args=[self.restaurant.slug,table.pk]))
        self.assertEqual(response['Content-Type'],'image/png');self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(self.decode(response.content),stable_table_url(table,self.restaurant))
        endpoint=reverse('table_qr_pack',args=[self.restaurant.slug,self.branch.pk])
        other_batch,_=self.batch(count=1,branch=self.other)
        self.assertEqual(self.client.get(endpoint,{'batch':other_batch.pk}).status_code,404)
        self.assertEqual(self.client.get(endpoint,{'format':'exe'}).status_code,400)
        table.archived=True;table.save()
        self.assertEqual(self.client.get(reverse('table_qr',args=[self.restaurant.slug,table.pk])).status_code,404)

    def test_matrix_keeps_quiet_zone_and_decodes_after_scaling_and_rotation(self):
        self.batch(count=1);table=self.branch.tables.get()
        matrix,step,_,_=artwork(table,self.restaurant)
        for row in matrix[:4]+matrix[-4:]:self.assertFalse(any(row))
        self.assertTrue(all(not any(row[:4]+row[-4:]) for row in matrix))
        self.assertGreaterEqual(step,4)
        image=Image.open(io.BytesIO(qr_png(table,self.restaurant)))
        for processed in (image.resize((900,1100)),image.rotate(90,expand=True)):
            codes=zxingcpp.read_barcodes(processed,formats=zxingcpp.BarcodeFormat.QRCode)
            self.assertEqual([code.text for code in codes],[stable_table_url(table,self.restaurant)])

    def test_pack_of_fifty_png_svg_and_thirteen_pdf_pages_all_decode_to_exact_tables(self):
        batch,_=self.batch();tables=list(batch.tables.order_by('number'))
        expected={stable_table_url(table,self.restaurant) for table in tables}
        archive=zipfile.ZipFile(io.BytesIO(qr_zip(tables,self.restaurant)))
        self.assertEqual(len(archive.namelist()),101)
        manifest=list(csv.DictReader(io.StringIO(archive.read('tables.csv').decode('utf-8-sig'))))
        self.assertEqual([int(row['number']) for row in manifest],list(range(1,51)))
        for table,row in zip(tables,manifest):
            stem=f'table-{table.number:03d}-{table.pk}'
            png=archive.read(stem+'.png');self.assertEqual(self.decode(png),row['url'])
            self.assertEqual(hashlib.sha256(png).hexdigest(),row['png_sha256'])
            self.assertIn(table.name,archive.read(stem+'.svg').decode())
        pdf=qr_pdf(tables,self.restaurant,self.branch)
        self.assertEqual(len(PdfReader(io.BytesIO(pdf)).pages),13)
        folder=Path('tmp/pdfs/table-codes');folder.mkdir(parents=True,exist_ok=True)
        (folder/'fifty-tables.pdf').write_bytes(pdf)
        found=[]
        document=fitz.open(stream=pdf,filetype='pdf')
        for index,page in enumerate(document):
            pixmap=page.get_pixmap(matrix=fitz.Matrix(2,2))
            image=Image.frombytes('RGB',[pixmap.width,pixmap.height],pixmap.samples)
            codes=zxingcpp.read_barcodes(image,formats=zxingcpp.BarcodeFormat.QRCode);found.extend(code.text for code in codes)
            self.assertEqual(len(codes),4 if index<12 else 2)
            codes.sort(key=lambda code:(code.position.top_left.y//(image.height//2),code.position.top_left.x))
            page_tables=tables[index*4:index*4+4]
            self.assertEqual([code.text for code in codes],[stable_table_url(table,self.restaurant) for table in page_tables])
            for slot,table in enumerate(page_tables):
                x=12+(slot%2)*96;top=30+(slot//2)*120
                label=page.get_textbox(fitz.Rect(x*72/25.4,(top+88)*72/25.4,(x+90)*72/25.4,(top+106)*72/25.4))
                self.assertIn(table.name,label.split())
            pixmap.save(str(folder/f'page-{index+1:02d}.png'))
        self.assertEqual(set(found),expected);self.assertEqual(len(found),50)
        (folder/'verification.json').write_text(json.dumps({'pngs_decoded':50,'svg_files':50,'pdf_pages':13,
            'pdf_codes_decoded':len(found),'all_links_match':set(found)==expected},indent=2))

    def test_merchant_labels_are_escaped_and_long_arabic_names_render_without_changes(self):
        from .table_codes import font, visual_text
        for label in ('ركن النافذة · Window', 'طاولة الحديقة الخارجية بجوار النافذة'):
            sample=DiningTable(branch=self.branch,name=label)
            texts=artwork(sample,self.restaurant)[3]
            caption=next(index for index,value in enumerate(texts) if value[0]=='TABLE · طاولة')
            prior_bottom=None
            for text,baseline,size,color in texts[caption:]:
                bounds=font(size).getbbox(visual_text(text),anchor='ms')
                top,bottom=baseline+bounds[1],baseline+bounds[3]
                if prior_bottom is not None:self.assertGreater(top-prior_bottom,4,label)
                prior_bottom=bottom
        table=DiningTable.objects.create(branch=self.branch,name='ركن <الشباك> & المطبخ')
        original=table.name
        svg=qr_svg(table,self.restaurant).decode()
        self.assertIn('&lt;الشباك&gt; &amp;',svg)
        self.assertNotIn('<الشباك>',svg)
        self.assertEqual(self.decode(qr_png(table,self.restaurant)),stable_table_url(table,self.restaurant))
        table.refresh_from_db();self.assertEqual(table.name,original)
        self.restaurant.name='مطعم الحديقة العربية للمأكولات والمشروبات الطازجة '*2
        self.assertEqual(self.decode(qr_png(table,self.restaurant)),stable_table_url(table,self.restaurant))

    def test_concurrent_pdf_builds_remain_independent(self):
        from concurrent.futures import ThreadPoolExecutor
        batch,_=self.batch(count=2);tables=list(batch.tables.order_by('number'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(qr_pdf,[table],self.restaurant,self.branch) for table in tables]
            documents=[future.result(timeout=30) for future in futures]
        for table,content in zip(tables,documents):
            document=fitz.open(stream=content,filetype='pdf');pixmap=document[0].get_pixmap(matrix=fitz.Matrix(2,2))
            image=Image.frombytes('RGB',[pixmap.width,pixmap.height],pixmap.samples)
            codes=zxingcpp.read_barcodes(image,formats=zxingcpp.BarcodeFormat.QRCode)
            self.assertEqual([code.text for code in codes],[stable_table_url(table,self.restaurant)])

    def test_both_languages_render_bound_errors_and_preserve_existing_order_snapshots(self):
        table=DiningTable.objects.create(branch=self.branch,name='Window 4')
        item=MenuItem.objects.create(category=Category.objects.create(branch=self.branch,name='Drinks'),name='Old menu label',price='10.25')
        order=create_order(table,{'idempotency_key':str(uuid4()),'lines':[{'item_id':item.pk,'quantity':1}]})
        for lang in ('ar','en'):
            response=self.client.get(self.url,{'lang':lang})
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Window 4')
        self.batch(count=5,start=10)
        line=order.lines.get();self.assertEqual((line.item_name,line.unit_price),('Old menu label',Decimal('10.25')))
        self.assertEqual(Order.objects.count(),1)


from django.test import TransactionTestCase, skipUnlessDBFeature


class TableBatchConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_two_simultaneous_batch_requests_share_one_result(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        venue=Restaurant.objects.create(name='Race venue',slug='table-batch-race')
        Subscription.objects.create(restaurant=venue,plan=Plan.objects.create(name='Table race',max_tables=60),
                                    status='active',ends_at=timezone.now()+timedelta(days=1))
        branch=Branch.objects.create(restaurant=venue);actor=User.objects.create_user('table-batch-race-owner')
        key=uuid4();barrier=Barrier(2)
        def run():
            try:
                barrier.wait(timeout=10)
                batch,created=create_table_batch(venue,branch,actor,submission_key=key,count=50,start_number=1)
                return batch.pk,created
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first,second=pool.submit(run),pool.submit(run)
            a,b=first.result(timeout=30),second.result(timeout=30)
        self.assertEqual(a[0],b[0]);self.assertNotEqual(a[1],b[1]);self.assertEqual(DiningTable.objects.count(),50)
