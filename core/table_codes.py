"""Permanent table identities, atomic bulk creation, and print-safe QR artwork."""
import base64
import csv
import hashlib
import io
import ipaddress
import re
import secrets
import uuid
import zipfile
from functools import lru_cache, wraps
from threading import RLock
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import arabic_reshaper
from bidi import get_display
from PIL import Image, ImageDraw, ImageFont
import qrcode
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .branding import contrast, validate_menu_color
from .limits import check_creation_limit
from .models import DiningTable, Restaurant, TableBatch
from .reporting import csv_safe
from .services import audit
from .validation import localized

MAX_BATCH = 200
FONT = settings.BASE_DIR / 'static/fonts/cairo/Cairo-Variable.ttf'
_font_lock = RLock()


def serialized_pdf(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with _font_lock:
            return function(*args, **kwargs)
    return guarded


def validate_qr_color(value):
    validate_menu_color(value)
    if contrast(value, '#ffffff') < 7:
        raise ValidationError(localized('اختر لونًا داكنًا أوضح على الأبيض (تباين 7:1 على الأقل).',
                                        'Choose a darker color against white (at least 7:1 contrast).'))


def table_number(table):
    if table.number is not None:
        return table.number
    match = re.fullmatch(r'\s*(?:(?:طاولة|ترابيزة|table)\s*)?(\d{1,6})\s*', table.name, re.I)
    return int(match[1]) if match else None


def stable_table_url(table, restaurant=None):
    restaurant = restaurant or table.branch.restaurant
    origin = restaurant.qr_public_origin
    parsed = urlsplit(origin)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValidationError(localized('عنوان QR المثبت غير صالح. راجع مسؤول المنصة قبل الطباعة.',
                                        'The pinned QR origin is invalid. Contact the platform operator before printing.'))
    # This route is a permanent printed-link contract. Never substitute the request Host.
    return origin.rstrip('/') + '/t/' + table.public_token + '/'


def local_print_origin(origin):
    parsed=urlsplit(origin);host=(parsed.hostname or '').lower()
    if parsed.scheme!='https' or host in ('localhost','testserver') or host.endswith(('.local','.localhost','.test','.invalid','.example')):
        return True
    try:return not ipaddress.ip_address(host).is_global
    except ValueError:return False


@transaction.atomic
def create_table_batch(restaurant, branch, actor, *, submission_key, count, start_number):
    if (type(count) is not int or not 1 <= count <= MAX_BATCH or type(start_number) is not int
            or not 1 <= start_number <= 99999 or start_number+count-1 > 99999 or branch.restaurant_id != restaurant.pk):
        raise ValidationError(localized('اختر عددًا من 1 إلى 200 وترقيمًا صالحًا.', 'Choose 1–200 tables and valid numbering.'))
    try:
        key = uuid.UUID(str(submission_key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError(localized('أعد فتح الصفحة قبل إنشاء الدفعة.', 'Reopen the page before creating a batch.'))
    subscription = check_creation_limit_for_replay(restaurant, branch)
    prior = TableBatch.objects.filter(branch=branch, submission_key=key).first()
    if prior:
        if (prior.count, prior.start_number) != (count, start_number):
            raise ValidationError(localized('هذه المحاولة تخص دفعة أخرى. أعد فتح الصفحة.', 'This request belongs to another batch. Reopen the page.'))
        return prior, False
    # The subscription lock above also serializes single-table creation and archives.
    check_creation_limit(restaurant, 'table', branch)
    current = list(branch.tables.all())
    used = {number for table in current if (number := table_number(table)) is not None}
    numbers = list(range(start_number, start_number+count))
    if used.intersection(numbers):
        conflicts = ', '.join(str(number) for number in sorted(used.intersection(numbers))[:8])
        raise ValidationError(localized('أرقام مستخدمة بالفعل، بما فيها الأرشيف: ', 'Numbers already used, including archived tables: ')+conflicts)
    available = subscription.limit('max_tables', branch)-sum(not table.archived for table in current)
    if count > available:
        raise ValidationError(localized(f'المتاح في الباقة {max(available, 0)} طاولة إضافية؛ لم تُنشأ أي طاولة.',
                                        f'The plan allows {max(available, 0)} additional tables; no tables were created.'))
    batch = TableBatch.objects.create(branch=branch, submission_key=key, start_number=start_number,
                                     count=count, created_by=actor)
    DiningTable.objects.bulk_create([DiningTable(branch=branch, batch=batch, number=number, name=str(number)) for number in numbers])
    audit(restaurant, actor, 'table.batch.create', str(batch.pk), changes={
        'branch': branch.pk, 'start_number': start_number, 'count': count})
    return batch, True


def check_creation_limit_for_replay(restaurant, branch):
    from .models import Subscription
    subscription = Subscription.objects.select_for_update(of=('self',)).select_related('plan').filter(restaurant=restaurant).first()
    if subscription is None:
        raise ValidationError(localized('الاشتراك غير متاح.', 'Subscription unavailable.'))
    return subscription


@transaction.atomic
def revoke_table_code(restaurant, branch, membership, table_id, data):
    if (membership.role != 'owner' or membership.restaurant_id != restaurant.pk or
            branch.restaurant_id != restaurant.pk or not membership.active or membership.archived):
        raise PermissionDenied
    table = DiningTable.objects.select_for_update().get(pk=table_id, branch=branch)
    reason = data.get('reason', '').strip()
    if (data.get('expected_token') != table.public_token or data.get('confirmation_name') != table.name
            or data.get('confirm_reprint') != 'on' or not 5 <= len(reason) <= 240):
        raise ValidationError(localized('لتغيير الرمز اكتب اسم الطاولة كما يظهر وسببًا واضحًا وأكد إعادة طباعة الملصق. أعد فتح الصفحة إذا تغيّر الرمز.',
            'Enter the exact table label and a clear reason, and confirm that the sticker must be reprinted. Reopen the page if the code changed.'))
    old_hash = hashlib.sha256(table.public_token.encode()).hexdigest()
    # Only this explicit owner-only operation replaces the immutable public identity.
    DiningTable.objects.filter(pk=table.pk).update(public_token=secrets.token_urlsafe(24), session_token=uuid.uuid4())
    audit(restaurant, membership.user, 'table.qr.revoke', str(table.pk), changes={
        'reason': reason, 'previous_token_sha256': old_hash, 'requires_reprint': True})


@lru_cache(maxsize=80)
def font(size):
    return ImageFont.truetype(str(FONT), size, layout_engine=ImageFont.Layout.BASIC)


def visual_text(text):
    return get_display(arabic_reshaper.reshape(str(text)))


def text_lines(text, maximum, minimum, width, lines=1):
    words = str(text).split()
    for size in range(maximum, minimum-1, -1):
        result, line = [], ''
        for word in words:
            candidate = (line+' '+word).strip()
            if font(size).getlength(visual_text(candidate)) <= width:
                line = candidate
            else:
                if line:
                    result.append(line); line = ''
                for character in word:
                    if line and font(size).getlength(visual_text(line+character)) > width:
                        result.append(line); line = ''
                    line += character
        if line:
            result.append(line)
        if len(result) <= lines:
            return result, size
    raise ValidationError(localized('اسم الطاولة أو المطعم أطول من مساحة التصميم. راجع التصميم قبل الطباعة.',
                                    'The table or venue label does not fit this design. Review before printing.'))


def artwork(table, restaurant):
    for color in (restaurant.qr_color, restaurant.qr_number_color):
        validate_qr_color(color)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, border=4)
    qr.add_data(stable_table_url(table, restaurant)); qr.make(fit=True)
    matrix = qr.get_matrix()
    step = 620 // len(matrix)
    if step < 4:
        raise ValidationError(localized('الرابط أطول من مساحة QR الآمنة.', 'The URL exceeds the safe QR print area.'))
    size = step*len(matrix)
    title, title_size = text_lines(restaurant.name, 42, 18, 800, 3)
    label, label_size = text_lines(table.name, 110, 24, 800, 2)
    if len(label) > 1:
        label, label_size = text_lines(table.name, 64, 24, 800, 2)
    texts = [(line, 70+index*title_size*1.2, title_size, '#193b34') for index, line in enumerate(title)]
    texts.append(('TABLE · طاولة', 220+size+42, 23, restaurant.qr_number_color))
    first_baseline = 1000 if len(label)==1 else 945
    texts.extend((line, first_baseline+index*label_size*1.1, label_size, restaurant.qr_number_color)
                 for index, line in enumerate(label))
    footer=('معاينة محلية فقط · Local preview only' if local_print_origin(restaurant.qr_public_origin)
            else 'امسح لعرض المنيو · Scan to order')
    texts.append((footer,1070,20,'#52675d'))
    return matrix, step, (900-size)//2, texts


def qr_png(table, restaurant):
    matrix, step, left, texts = artwork(table, restaurant)
    image = Image.new('RGB', (1800, 2200), 'white')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8,8,1791,2191), radius=30, outline='#dce5df', width=2)
    draw.rounded_rectangle((56,36,1744,46), radius=5, fill=restaurant.qr_color)
    for row, values in enumerate(matrix):
        for column, dark in enumerate(values):
            if dark:
                x, y = (left+column*step)*2, (220+row*step)*2
                draw.rectangle((x,y,x+step*2-1,y+step*2-1), fill=restaurant.qr_color)
    for text, baseline, size, color in texts:
        draw.text((900,round(baseline*2)), visual_text(text), font=font(size*2), fill=color, anchor='ms')
    output=io.BytesIO(); image.save(output, format='PNG', dpi=(508,508)); return output.getvalue()


@lru_cache(maxsize=1)
def svg_font():
    return base64.b64encode((settings.BASE_DIR/'static/fonts/cairo/Cairo-Variable.woff2').read_bytes()).decode('ascii')


def qr_svg(table, restaurant):
    matrix, step, left, texts = artwork(table, restaurant)
    path = ' '.join(f'M{left+c*step} {220+r*step}h{step}v{step}h-{step}z'
                    for r,row in enumerate(matrix) for c,dark in enumerate(row) if dark)
    labels=''.join(f'<text x="450" y="{baseline}" font-size="{size}" fill="{color}" text-anchor="middle" '
                   f'style="unicode-bidi:plaintext">{escape(text)}</text>' for text,baseline,size,color in texts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="90mm" height="110mm" viewBox="0 0 900 1100">'
        f'<title>{escape(restaurant.name)} - {escape(table.name)}</title><style>'
        f'@font-face{{font-family:CairoQR;src:url(data:font/woff2;base64,{svg_font()})}}text{{font-family:CairoQR,Arial,sans-serif}}'
        f'</style><rect width="900" height="1100" fill="white"/><rect x="4" y="4" width="892" height="1092" rx="15" fill="white" stroke="#dce5df"/>'
        f'<rect x="28" y="18" width="844" height="5" rx="2" fill="{restaurant.qr_color}"/>'
        f'<path d="{path}" fill="{restaurant.qr_color}" shape-rendering="crispEdges"/>{labels}</svg>').encode('utf-8')


@serialized_pdf
def qr_pdf(tables, restaurant, branch):
    from reportlab.pdfgen import canvas
    from reportlab.lib.colors import HexColor
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.units import mm
    with _font_lock:
        if 'CairoQR' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('CairoQR', str(FONT)))
    output=io.BytesIO()
    pdf=canvas.Canvas(output,pagesize=(210*mm,297*mm),pageCompression=1)
    pdf.setTitle('Table QR codes'); pdf.setAuthor('Serveza')
    pages=(len(tables)+3)//4
    for offset in range(0,len(tables),4):
        pdf.setFillColor(HexColor('#193b34')); pdf.setFont('CairoQR',12)
        pdf.drawCentredString(105*mm,285*mm,visual_text('رموز الطاولات · Table QR codes'))
        branch_lines, branch_size=text_lines(branch.name,28,16,1800,1)
        pdf.setFont('CairoQR',branch_size*.3)
        pdf.drawCentredString(105*mm,278*mm,visual_text(branch_lines[0]))
        for index, table in enumerate(tables[offset:offset+4]):
            x=(12+(index%2)*96)*mm; y=(157-(index//2)*120)*mm; scale=90*mm/900; height=110*mm
            matrix,step,left,texts=artwork(table,restaurant)
            pdf.setStrokeColor(HexColor('#dce5df'));pdf.setFillColor(HexColor('#ffffff'))
            pdf.roundRect(x,y,90*mm,height,2*mm,stroke=1,fill=1)
            pdf.setFillColor(HexColor(restaurant.qr_color))
            pdf.rect(x+28*scale,y+height-23*scale,844*scale,5*scale,stroke=0,fill=1)
            for row, values in enumerate(matrix):
                for column,dark in enumerate(values):
                    if dark:
                        pdf.rect(x+(left+column*step)*scale,y+height-(220+(row+1)*step)*scale,
                                 step*scale,step*scale,stroke=0,fill=1)
            for text,baseline,size,color in texts:
                pdf.setFillColor(HexColor(color));pdf.setFont('CairoQR',size*scale)
                pdf.drawCentredString(x+450*scale,y+height-baseline*scale,visual_text(text))
        pdf.setFont('CairoQR',8);pdf.setFillColor(HexColor('#52675d'))
        pdf.drawCentredString(105*mm,20*mm,f'{offset//4+1} / {pages}  |  90 x 110 mm  |  Print at 100%')
        pdf.showPage()
    pdf.save();return output.getvalue()


def qr_zip(tables, restaurant):
    output=io.BytesIO();manifest=io.StringIO(newline='');writer=csv.writer(manifest)
    writer.writerow(['table_id','number','table_label','url','png_sha256'])
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for table in tables:
            name=f'table-{table.number if table.number is not None else table.pk:03d}-{table.pk}'
            png=qr_png(table,restaurant)
            archive.writestr(name+'.png',png);archive.writestr(name+'.svg',qr_svg(table,restaurant))
            writer.writerow([table.pk,table.number if table.number is not None else '',csv_safe(table.name),stable_table_url(table,restaurant),hashlib.sha256(png).hexdigest()])
        archive.writestr('tables.csv','\ufeff'+manifest.getvalue())
    return output.getvalue()
