# النشر والتشغيل

هذا الدليل نقطة بداية للتجربة، وليس إثباتًا لجاهزية الإنتاج.

1. استخدم PostgreSQL للتشغيل الإنتاجي واضبط `DATABASE_URL`. اضبط `DJANGO_SECRET_KEY` عشوائيًا بطول 50 حرفًا على الأقل، و`DJANGO_DEBUG=0`، و`DJANGO_ALLOWED_HOSTS` و`DJANGO_CSRF_TRUSTED_ORIGINS` للدومين الفعلي. يتطلب تشغيل SQLite مع إيقاف التصحيح تفعيل `DJANGO_ALLOW_SQLITE=1` صراحةً.
2. فعّل HTTPS في reverse proxy. `SECURE_SSL_REDIRECT` وHSTS والكوكيز الآمنة مفعلة عندما يكون DEBUG مغلقًا.
   اضبط SMTP بمتغيرات `DJANGO_EMAIL_*` لتفعيل بريد المالك واستعادة كلمة المرور ودعوات الموظفين، واختبر تسجيل مالك جديد وتفعيل رابط فعلي وتسليم دعوة؛ وضع التطوير يطبع الرسائل في مخرجات الخادم فقط. يتراجع التسجيل إذا فشل إرسال رابط التفعيل.
   إذا كان TLS ينتهي في reverse proxy موثوق ولا يمكن الوصول إلى Django مباشرة، اضبط `DJANGO_TRUST_PROXY_TLS=1`. لا تفعل HSTS على النطاقات الفرعية أو preload قبل التأكد من أن كل النطاقات الفرعية تستخدم HTTPS؛ فحص `check --deploy` سيعرض التحذيرين المرتبطين بذلك حتى اتخاذ القرار على النطاق الحقيقي.
3. شغّل `python manage.py check --deploy` و`python manage.py migrate` و`python manage.py collectstatic --noinput`.
4. شغّل Gunicorn خلف reverse proxy، وقدّم `staticfiles/` و`media/` من تخزين مناسب محمي. لا تخدم الملفات المرفوعة مباشرة من Django في الإنتاج.
5. راقب `/health/`؛ يعيد 503 إذا تعذر الاتصال بقاعدة البيانات. أضف مراقبة زمن الاستجابة وأخطاء التطبيق وتنبيهات خارجية. افحص السجلات بدون أسرار.
   شغّل `python manage.py prune_login_attempts` دوريًا (مثلًا كل ساعة) لحذف سجلات تحديد معدل الدخول القديمة.
   شغّل `python manage.py prune_menu_imports` يوميًا لحذف بيانات معاينات استيراد المنيو القديمة.
6. انسخ PostgreSQL وملفات media دوريًا إلى مكان منفصل واختبر الاستعادة في بيئة تجريبية. احتفظ بنسخة قبل أي migration غير قابل للتراجع.

إعداد Docker Compose الحالي للتطوير المحلي فقط. لا يشمل reverse proxy أو نسخًا احتياطية أو تخزين ملفات إنتاجيًا.

## تجربة SQLite على PythonAnywhere

1. اضبط المتغيرات التالية في ملف WSGI الخاص بالتطبيق من تبويب **Web** قبل استدعاء `get_wsgi_application()`. اضبط نفس المتغيرات في جلسة Bash عند تشغيل أوامر Django. [متغيرات البيئة في PythonAnywhere](https://help.pythonanywhere.com/pages/EnvironmentVariables/)

   | المتغير | القيمة |
   |---|---|
   | `DJANGO_DEBUG` | `0` |
   | `DJANGO_ALLOW_SQLITE` | `1` |
   | `DATABASE_URL` | فارغ أو غير مضبوط |
   | `DJANGO_SECRET_KEY` | مفتاح عشوائي محفوظ على الاستضافة بطول 50 حرفًا على الأقل |
   | `DJANGO_ALLOWED_HOSTS` | اسم النطاق الفعلي مثل `yourname.pythonanywhere.com` |
   | `DJANGO_CSRF_TRUSTED_ORIGINS` | أصل HTTPS الفعلي مثل `https://yourname.pythonanywhere.com` |

   يستخدم التطبيق ملف `db.sqlite3` في مجلد المشروع. احتفظ بنسخة احتياطية منه قبل تحديث التطبيق أو ترحيل قاعدة البيانات. تستمر إعدادات الكوكيز الآمنة وإعادة التوجيه إلى HTTPS عند إيقاف التصحيح.
2. شغّل `python manage.py check` و`python manage.py migrate --noinput` و`python manage.py collectstatic --noinput`. من **Web → Static Files** اربط `/static/` بالمسار المطلق لمجلد `staticfiles`، و`/media/` بمجلد `media` لصور المنيو. اضغط **Reload** وتحقق من فتح `/health/` وتسجيل الدخول عبر HTTPS. [إعداد الملفات الثابتة في PythonAnywhere](https://help.pythonanywhere.com/pages/DjangoStaticFiles/)
3. جرّب إرسال طلبين في نفس الوقت وتحديث طلب من شاشة المطبخ، وراجع سجل الأخطاء وعدد الطلبات المسجلة. تدعم SQLite التجربة المحدودة، ويبقى اختبار الحمل على الاستضافة جزءًا من التحقق. [ملاحظات PythonAnywhere عن SQLite](https://help.pythonanywhere.com/pages/MySiteIsSlow/#sqlite)
