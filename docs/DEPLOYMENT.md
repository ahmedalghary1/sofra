# النشر والتشغيل

هذا الدليل نقطة بداية للتجربة، وليس إثباتًا لجاهزية الإنتاج.

1. استخدم PostgreSQL واضبط `DATABASE_URL`، و`DJANGO_SECRET_KEY` عشوائيًا وطويلًا، و`DJANGO_DEBUG=0`، و`DJANGO_ALLOWED_HOSTS` و`DJANGO_CSRF_TRUSTED_ORIGINS` للدومين الفعلي.
2. فعّل HTTPS في reverse proxy. `SECURE_SSL_REDIRECT` وHSTS والكوكيز الآمنة مفعلة عندما يكون DEBUG مغلقًا.
   اضبط SMTP بمتغيرات `DJANGO_EMAIL_*` لاستخدام استعادة كلمة المرور؛ وضع التطوير يطبع الرسائل في مخرجات الخادم فقط.
   إذا كان TLS ينتهي في reverse proxy موثوق ولا يمكن الوصول إلى Django مباشرة، اضبط `DJANGO_TRUST_PROXY_TLS=1`. لا تفعل HSTS على النطاقات الفرعية أو preload قبل التأكد من أن كل النطاقات الفرعية تستخدم HTTPS؛ فحص `check --deploy` سيعرض التحذيرين المرتبطين بذلك حتى اتخاذ القرار على النطاق الحقيقي.
3. شغّل `python manage.py check --deploy` و`python manage.py migrate` و`python manage.py collectstatic --noinput`.
4. شغّل Gunicorn خلف reverse proxy، وقدّم `staticfiles/` و`media/` من تخزين مناسب محمي. لا تخدم الملفات المرفوعة مباشرة من Django في الإنتاج.
5. راقب `/health/` مع مراقبة قاعدة البيانات وخطأ التطبيق. افحص السجلات بدون أسرار.
   شغّل `python manage.py prune_login_attempts` دوريًا (مثلًا كل ساعة) لحذف سجلات تحديد معدل الدخول القديمة.
6. انسخ PostgreSQL وملفات media دوريًا إلى مكان منفصل واختبر الاستعادة في بيئة تجريبية. احتفظ بنسخة قبل أي migration غير قابل للتراجع.

إعداد Docker Compose الحالي للتطوير المحلي فقط. لا يشمل reverse proxy أو نسخًا احتياطية أو تخزين ملفات إنتاجيًا.
