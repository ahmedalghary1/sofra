# سفرة — SaaS لإدارة المطاعم

تطبيق Django عربي RTL لإدارة المنيو والطاولات وطلبات الضيوف والمطبخ والخدمة. بدأ من مواصفات [المنتج الأصلية](safra_restaurant_saas_complete_spec.md) في هذا المجلد. الواجهة تتكيف مع الهاتف والتابلت وسطح المكتب.

## ما يعمل الآن

- إنشاء حساب مالك ومطعم وفرع وتجربة 14 يومًا، وتسجيل دخول الموظفين بأدوار.
- إدارة التصنيفات والأصناف والأسعار والتوفر والإضافات البسيطة، والطاولات مع تنزيل QR وتجديد الرمز.
- منيو ضيف عام بلا تسجيل، سلة مع كمية وإضافات، إرسال طلب مع إعادة احتساب الأسعار في الخادم ومفتاح منع التكرار.
- شاشة مطبخ تُحدّث بالاستعلام الدوري كل 20 ثانية، انتقالات حالات مسجلة، واستدعاء الويتر أو الحساب.
- طلب تقليدي بسيط من الموظف، تقارير تشغيلية وتصدير CSV، ولوحة Django Admin لمشغل المنصة.

**الحالة:** إصدار تجريبي تشغيلي، وليس جاهزًا للإطلاق التجاري. تفاصيل المؤجل والمخاطر في [خطة التنفيذ](docs/IMPLEMENTATION_PLAN.md).

## التشغيل المحلي

يتطلب Python 3.12+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

افتح `http://127.0.0.1:8000/` ثم أنشئ حساب المطعم. لإنشاء مدير منصة: `python manage.py createsuperuser` ثم افتح `/django-admin/`.

للتطوير عبر PostgreSQL: `docker compose up --build` ثم `docker compose exec web python manage.py migrate`. إعدادات التطوير داخل Compose فقط.

## الاختبارات

```powershell
python manage.py check
python manage.py test core
```

## البيئة

انسخ `.env.example` إلى إعدادات بيئتك المناسبة. Django لا يقرأ ملف `.env` تلقائيًا؛ عرّف المتغيرات في shell أو نظام النشر. `DATABASE_URL` اختياري محليًا ويجب ضبطه للإنتاج على PostgreSQL. لا تستخدم مفتاح التطوير أو `DEBUG=1` في الإنتاج.

## الوثائق

- [المعمارية](docs/ARCHITECTURE.md)
- [القرارات والافتراضات](docs/DECISIONS.md)
- [خطة التنفيذ والحالة](docs/IMPLEMENTATION_PLAN.md)
- [خطة الاختبارات](docs/TEST_PLAN.md)
- [النشر والتشغيل](docs/DEPLOYMENT.md)
