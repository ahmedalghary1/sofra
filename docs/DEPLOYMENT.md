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

## نشر إصلاحات مراجعة 25 نوفمبر

1. أوقف الكتابة مؤقتًا، واحفظ نسخة مستقلة من قاعدة البيانات وmedia قبل `0026_audit_safety_and_records`. لا تعكس هذا الترحيل بعد وجود اتفاقات أو تصحيحات مالية دون خطة حفظ لها. يرفض الترحيل وجود سعر باقة سالب ولا يغيره بالتخمين. يضيف لقطة للإعداد الحالي موسومة كخط أساس؛ لا يغير أسماء المطاعم أو الأصناف أو الأسعار أو الطلبات القديمة.
2. شغّل `python manage.py audit_release_data` وراجع أي نتائج قديمة دون دمج حسابات أو حذف ملفات تلقائيًا. يفضل أصل مستقل لصور المطعم. إذا قدمتها Nginx على النطاق نفسه، امنع أي امتداد غير PNG وJPEG وWebP، وطبّق `nosniff`، مثل الإعداد التالي بعد تعديل المسار وفحصه بـ`nginx -t`:

```nginx
location ^~ /media/ {
    alias /srv/serveza/media/;
    types { image/png png; image/jpeg jpg jpeg; image/webp webp; }
    default_type application/octet-stream;
    add_header X-Content-Type-Options nosniff always;
    if ($uri !~* "\.(png|jpe?g|webp)$") { return 404; }
}
```

3. لتحديد المعدل حسب العميل خلف Nginx، استبدل الترويسة الواردة بـ`proxy_set_header X-Real-IP $remote_addr;`. في اتصال loopback اضبط `DJANGO_TRUSTED_PROXY_NETWORKS=127.0.0.1/32,::1/128`، ولا تضم شبكات غير موثوقة. في اتصال Unix فقط، مع تقييد صلاحية المقبس ومنع الوصول المباشر إلى Django، يمكن ضبط `DJANGO_TRUST_UNIX_PROXY_IP=1`. الافتراضي لا يثق بأي ترويسة عنوان عميل. إعداد الثقة بـTLS مستقل عن إعداد عنوان العميل.
4. تنقل خيارات `DATABASE_URL` مثل `sslmode=require` أو `verify-full` إلى PostgreSQL، مع فك ترميز بيانات الدخول. اختبر شروط الشهادة المطلوبة لدى مزود القاعدة. الإعداد لا يتجاهل الخيارات المجهولة بصمت.
5. ثبت `requirements-dev.txt` في بيئة الاختبار فقط وشغّل الحزمة الأساسية والمتصفح. يجهز `.github/workflows/verify.yml` اختبار SQLite وPostgreSQL والمتصفح في الاتجاهين. كتابة الملف محليًا لا تثبت نجاح GitHub Actions؛ يلزم تشغيله على المستودع واعتماد نتيجته.

## النسخ والاستعادة الآمنة

الملف الناتج يحتوي بيانات مطاعم وحسابات خاصة؛ احفظه خارج Git وفي تخزين مستقل بصلاحية محدودة وتشفير التخزين المناسب. المجلدان `tmp` و`backups` مستبعدان. أوقف الكتابة أثناء النسخ كي تتطابق القاعدة والصور وبيان الأعداد.

```powershell
python manage.py backup_application --output backups/private-backup.zip
python manage.py restore_application_backup --source backups/private-backup.zip --destination tmp/restore-verification
```

لا يستبدل الأمر ملفًا موجودًا أو مجلد استعادة غير فارغ. يفحص المسارات والبصمات والأعداد، وتفحص استعادة SQLite سلامة القاعدة. يتطلب نسخ PostgreSQL وجود `pg_dump` متوافقًا؛ لا يضع الأمر كلمة المرور في سطر الأوامر أو السجل. استعادة PostgreSQL هنا تتحقق من الملف وتستخرجه فقط: استخدم `pg_restore --no-owner --no-acl` إلى **قاعدة جديدة مستقلة** ببيانات اتصال محمية في البيئة أو `PGPASSFILE`، ثم قارن أعداد الجداول في `manifest.json` وبصمات الصور وشغّل فحص البيانات. لا تنفذ `--clean` على قاعدة التشغيل.

## المراقبة والتحقق الخارجي

تسجل `serveza.requests` اسم المسار والحالة والزمن ومعرف الطلب، ويعاد `X-Request-ID` للعميل. لا تتضمن هذه السجلات أجسام الطلبات أو رموز الروابط أو كلمات المرور. اجمعها في خدمة السجلات واضبط تنبيهًا خارجيًا للأخطاء وارتفاع الزمن وتعطل `/health/`، ثم جرب استجابة الفريق. نظف حدود المعدل القديمة بالأمر الدوري الموثق.

```powershell
python scripts/probe_service.py --base-url https://your-staging-domain.example --samples 10
```

هذا فحص صحة وتوقيت للقراءة فقط، وليس اختبار حمل طلبات المطعم. يجب قياس إرسال الطلب وظهوره في المطبخ والتحصيل خلال وردية فعلية، وفحص تسليم بريد التفعيل والدعوة والاستعادة إلى صندوق خارجي. [إجراءات التجربة والإقفال التجاري](OPERATING_AND_COMMERCIAL_RUNBOOK.md).

## النطاق الرئيسي وتهيئة Nginx

1. اضبط `SITE_URL=https://servezaflow.com` و`DJANGO_DEBUG=0` و`DJANGO_SECURE_SSL_REDIRECT=1` و`DJANGO_SECURE_HSTS_SECONDS=300` في بيئة Gunicorn. يضيف Django النطاق الرئيسي ونسخة `www` تلقائيًا إلى المضيفين المسموحين وأصول CSRF الموثوقة. استخدم `DJANGO_ALLOWED_HOSTS` و`DJANGO_CSRF_TRUSTED_ORIGINS` لإضافات محددة عند الحاجة. احذف متغير النطاق القديم من بيئة النشر بعد نقل قيمته إلى `SITE_URL` إن كانت مطلوبة.
2. اربط DNS وشهادات HTTPS بالنطاق الرئيسي و`www.servezaflow.com` والأسماء السابقة التي تريد إعادة توجيهها. وجّه طلبات تلك الأسماء إلى Django مع الحفاظ على المضيف الأصلي. يمكن تنفيذ 301 عند Nginx للأسماء البديلة باستخدام `return 301 https://servezaflow.com$request_uri;`، بشرط تمرير `/health/` إلى التطبيق في موقع مطابق له. ضع توجيه `return` داخل `location /` حتى يصل مسار الفحص إلى موقعه المطابق.
3. عند إنهاء TLS في Nginx الموثوق، اضبط `DJANGO_TRUST_PROXY_TLS=1`. استخدم في موقع الوكيل `proxy_set_header Host $host;` و`proxy_set_header X-Forwarded-Proto $scheme;`. إذا استخدمت `DJANGO_USE_X_FORWARDED_HOST=1` فأضف `proxy_set_header X-Forwarded-Host $host;`. اربط Gunicorn بمقبس Unix أو بعنوان داخلي، واستبدل ترويسات الوكيل الواردة من العميل عند Nginx.
4. شغّل `python manage.py check --deploy` و`python manage.py collectstatic --noinput`. تحقّق من إعداد Nginx بـ`nginx -t` ثم أعد تحميله وأعد تشغيل خدمة Gunicorn التي تقرأ متغيرات البيئة. افحص `/health/` و`/robots.txt` و`/sitemap.xml`، ثم افحص استجابة 301 من `www` وأي نطاق بديل مع تمرير مسار واستعلام.
5. أرسل `https://servezaflow.com/sitemap.xml` في Search Console بعد النشر. عنوان العربية هو المسار الأساسي، وعنوان الإنجليزية يضيف `?lang=en`. تعلن النسختان روابط `hreflang` نفسها، ويشير `x-default` إلى العربية. حدّث تواريخ الصفحات في `core/seo.py` عند تغيير المحتوى أو أسعار الباقات المنشورة. اقرأ `/llms.txt` و`/robots.txt` بعد النشر للتحقق من الأسعار وأسماء الزواحف.
6. اجعل Nginx أو CDN يضغط HTML وCSS وJavaScript وXML وملفات النص عند توفر ضغط gzip أو Brotli. تحقّق من `Content-Encoding` و`Vary: Accept-Encoding`. استخدم تخزينًا طويل الأجل مع `immutable` للملفات ذات البصمة فقط، وتخزينًا قصيرًا للأسماء الحالية التي يعاد استخدامها عبر الإصدارات. قِس Cache-Control من النطاق المنشور قبل تغيير السياسة.
