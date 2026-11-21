# النشر والتشغيل

هذا الدليل نقطة بداية للتجربة، وليس إثباتًا لجاهزية الإنتاج.

1. استخدم PostgreSQL للتشغيل الإنتاجي واضبط `DATABASE_URL`. اضبط `DJANGO_SECRET_KEY` عشوائيًا بطول 50 حرفًا على الأقل، و`DJANGO_DEBUG=0`، و`DJANGO_ALLOWED_HOSTS` و`DJANGO_CSRF_TRUSTED_ORIGINS` للدومين الفعلي. يتطلب تشغيل SQLite مع إيقاف التصحيح تفعيل `DJANGO_ALLOW_SQLITE=1` صراحةً.
2. فعّل HTTPS في reverse proxy. `SECURE_SSL_REDIRECT` وHSTS والكوكيز الآمنة مفعلة عندما يكون DEBUG مغلقًا.
   اضبط SMTP بمتغيرات `DJANGO_EMAIL_*` لتفعيل بريد المالك واستعادة كلمة المرور ودعوات الموظفين، واختبر تسجيل مالك جديد وتفعيل رابط فعلي وتسليم دعوة؛ يختار `DJANGO_EMAIL_BACKEND=smtp` الإرسال الفعلي في التطوير والإنتاج. اضبط `SITE_URL` على العنوان العام لروابط تفعيل المالك. يتراجع التسجيل إذا فشل إرسال رابط التفعيل.
   إذا كان TLS ينتهي في reverse proxy موثوق ولا يمكن الوصول إلى Django مباشرة، اضبط `DJANGO_TRUST_PROXY_TLS=1`. لا تفعل HSTS على النطاقات الفرعية أو preload قبل التأكد من أن كل النطاقات الفرعية تستخدم HTTPS؛ فحص `check --deploy` سيعرض التحذيرين المرتبطين بذلك حتى اتخاذ القرار على النطاق الحقيقي.
3. شغّل `python manage.py check --deploy` و`python manage.py migrate` و`python manage.py collectstatic --noinput`.
4. شغّل Gunicorn خلف reverse proxy، وقدّم `staticfiles/` و`media/` من تخزين مناسب محمي. لا تخدم الملفات المرفوعة مباشرة من Django في الإنتاج.
5. راقب `/health/`؛ يعيد 503 إذا تعذر الاتصال بقاعدة البيانات. أضف مراقبة زمن الاستجابة وأخطاء التطبيق وتنبيهات خارجية. افحص السجلات بدون أسرار.
   شغّل `python manage.py prune_login_attempts` دوريًا (مثلًا كل ساعة) لحذف سجلات تحديد معدل الدخول القديمة.
   شغّل `python manage.py prune_menu_imports` يوميًا لحذف بيانات معاينات استيراد المنيو القديمة.
6. انسخ PostgreSQL وملفات media دوريًا إلى مكان منفصل واختبر الاستعادة في بيئة تجريبية. احتفظ بنسخة قبل أي migration غير قابل للتراجع.

إعداد Docker Compose الحالي للتطوير المحلي فقط. لا يشمل reverse proxy أو نسخًا احتياطية أو تخزين ملفات إنتاجيًا.

## النطاق الرئيسي وتهيئة Nginx

1. اضبط `SITE_URL=https://servezaflow.com` و`DJANGO_DEBUG=0` و`DJANGO_SECURE_SSL_REDIRECT=1` و`DJANGO_SECURE_HSTS_SECONDS=300` في بيئة Gunicorn. يضيف Django النطاق الرئيسي ونسخة `www` تلقائيًا إلى المضيفين المسموحين وأصول CSRF الموثوقة. استخدم `DJANGO_ALLOWED_HOSTS` و`DJANGO_CSRF_TRUSTED_ORIGINS` لإضافات محددة عند الحاجة. احذف متغير النطاق القديم من بيئة النشر بعد نقل قيمته إلى `SITE_URL` إن كانت مطلوبة.
2. اربط DNS وشهادات HTTPS بالنطاق الرئيسي و`www.servezaflow.com` والأسماء السابقة التي تريد إعادة توجيهها. وجّه طلبات تلك الأسماء إلى Django مع الحفاظ على المضيف الأصلي. يمكن تنفيذ 301 عند Nginx للأسماء البديلة باستخدام `return 301 https://servezaflow.com$request_uri;`، بشرط تمرير `/health/` إلى التطبيق في موقع مطابق له. ضع توجيه `return` داخل `location /` حتى يصل مسار الفحص إلى موقعه المطابق.
3. عند إنهاء TLS في Nginx الموثوق، اضبط `DJANGO_TRUST_PROXY_TLS=1`. استخدم في موقع الوكيل `proxy_set_header Host $host;` و`proxy_set_header X-Forwarded-Proto $scheme;`. إذا استخدمت `DJANGO_USE_X_FORWARDED_HOST=1` فأضف `proxy_set_header X-Forwarded-Host $host;`. اربط Gunicorn بمقبس Unix أو بعنوان داخلي، واستبدل ترويسات الوكيل الواردة من العميل عند Nginx.
4. شغّل `python manage.py check --deploy` و`python manage.py collectstatic --noinput`. تحقّق من إعداد Nginx بـ`nginx -t` ثم أعد تحميله وأعد تشغيل خدمة Gunicorn التي تقرأ متغيرات البيئة. افحص `/health/` و`/robots.txt` و`/sitemap.xml`، ثم افحص استجابة 301 من `www` وأي نطاق بديل مع تمرير مسار واستعلام.
5. أرسل `https://servezaflow.com/sitemap.xml` في Search Console بعد النشر. عنوان العربية هو المسار الأساسي، وعنوان الإنجليزية يضيف `?lang=en`. تعلن النسختان روابط `hreflang` نفسها، ويشير `x-default` إلى العربية. حدّث تواريخ الصفحات في `core/seo.py` عند تغيير المحتوى أو أسعار الباقات المنشورة. اقرأ `/llms.txt` و`/robots.txt` بعد النشر للتحقق من الأسعار وأسماء الزواحف.
6. اجعل Nginx أو CDN يضغط HTML وCSS وJavaScript وXML وملفات النص عند توفر ضغط gzip أو Brotli. تحقّق من `Content-Encoding` و`Vary: Accept-Encoding`. استخدم تخزينًا طويل الأجل مع `immutable` للملفات ذات البصمة فقط، وتخزينًا قصيرًا للأسماء الحالية التي يعاد استخدامها عبر الإصدارات. قِس Cache-Control من النطاق المنشور قبل تغيير السياسة.
