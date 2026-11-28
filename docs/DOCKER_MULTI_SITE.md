# أكثر من موقع على نفس السيرفر

يشغّل هذا الوضع بوابة Caddy واحدة على 80 و443 لكل السيرفر، وتوجّه الدومينات إلى مشاريع Docker مستقلة. لكل موقع مجلد كود وإعدادات وأسرار وصورة تطبيق وقاعدة بيانات وصور ونسخ احتياطية خاصة به. هذا مناسب لنسخ مختلفة من المشروع، ويمكن للبوابة توجيه مواقع مبنية بتقنيات أخرى أيضًا. لا تحتاج حاوية منفصلة لكل مطعم داخل SaaS؛ عزل المطاعم داخل التطبيق يظل كما هو.

المتطلبات: خادم Linux مع Docker وCompose 2.30+، وDNS لكل دومين يشير إلى السيرفر. أسماء المشاريع ثابتة وفريدة على السيرفر، مثل `site-one` و`site-two`. [دمج Compose مع `!reset`](https://docs.docker.com/reference/compose-file/merge/) يزيل نشر منافذ الموقع من الإعداد الأساسي، و[استيراد ملفات Caddy](https://caddyserver.com/docs/caddyfile/directives/import) يحمّل مسار كل دومين.

## 1. البوابة — مرة واحدة للسيرفر

من أي نسخة من الكود، انسخ إعداد البوابة إلى مجلد مستقل لا يرتبط بحذف أو تحديث موقع بعينه:

```bash
mkdir -p /srv/serveza-gateway
cp -R docker/gateway/. /srv/serveza-gateway/
docker compose -f /srv/serveza-gateway/compose.yml up -d --wait
```

اسم مشروع البوابة الافتراضي `serveza-edge`، وشبكة الواجهة `serveza-edge`، وعنوان البوابة داخلها `10.240.0.2` على subnet `10.240.0.0/24`. إذا تتعارض مع شبكة قائمة، اختر شبكة خاصة متاحة واضبط `EDGE_SUBNET` و`EDGE_PROXY_IP` و`EDGE_NETWORK` للبوابة **قبل إنشائها**، واستخدم نفس IP واسم الشبكة لكل موقع عبر خيارات التهيئة أدناه. إذا وضعتها في ملف بيئة مخصص، مرّره بـ`--env-file /srv/serveza-gateway/.env` في **كل** أوامر البوابة؛ ملف compose وحده لا يقرأ ملف بيئة من مجلده تلقائيًا عند التشغيل من مجلد آخر.

لا تشغّل بوابة ثانية على 80/443. إذا يوجد وكيل قائم يستخدم المنفذين، يلزم دمج التوجيه معه أو نقل هذه المهمة للبوابة خلال نافذة صيانة، قبل تنفيذ أمر البدء. لا تتوقف عن خدمة الدومين المطبوع على QR أثناء النقل النهائي.

## 2. الموقع الأول

ارفع الكود إلى `/srv/site-one` ثم من داخله ولّد الإعداد مرة واحدة:

```bash
cd /srv/site-one
docker run --rm -it --user "$(id -u):$(id -g)" -v "$PWD:/workspace" -w /workspace python:3.12-slim-bookworm python scripts/configure_docker.py --domain one.example.com --admin-email admin@one.example.com --project-name site-one --shared-proxy
```

أو استخدم Python المثبت على السيرفر:

```bash
python3 scripts/configure_docker.py --domain one.example.com --admin-email admin@one.example.com --project-name site-one --shared-proxy
```

ينشئ `.env.docker` و`.docker-secrets/` كما في [دليل Docker](DOCKER.md)، ويضيف `.env.compose` لاختيار اسم المشروع والوضع، و`.gateway-route.caddy` لتوجيه الدومين. اضبط SMTP والأسرار حسب الدليل قبل استخدام التسجيل والتفعيل. لتخصيص الشبكة أضف `--edge-ip 10.241.0.2 --edge-network my-edge` بما يطابق البوابة الفعلية.

شغّل الموقع:

```bash
docker compose --env-file .env.compose up -d --build --wait --wait-timeout 300
```

ثم سجّل مساره في البوابة:

```bash
install -m 644 .gateway-route.caddy /srv/serveza-gateway/sites/site-one.caddy
docker compose -f /srv/serveza-gateway/compose.yml exec gateway caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose -f /srv/serveza-gateway/compose.yml exec gateway caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

نفّذ reload بعد نجاح validate. إذا فشل التحقق، صحّح ملف المسار أو أعد نسخته السابقة قبل إعادة تشغيل البوابة. إعادة التحميل لا تتطلب إيقاف المواقع الأخرى. يصدر HTTPS عند صحة DNS والمنافذ؛ افحص `https://one.example.com/health/` بشهادة موثوقة دون `-k`.

## 3. الموقع الثاني وأي موقع إضافي

كرّر في **مجلد كود مستقل** مثل `/srv/site-two` مع دومين مختلف واسم مشروع فريد:

```bash
python3 scripts/configure_docker.py --domain two.example.com --admin-email admin@two.example.com --project-name site-two --shared-proxy
docker compose --env-file .env.compose up -d --build --wait --wait-timeout 300
install -m 644 .gateway-route.caddy /srv/serveza-gateway/sites/site-two.caddy
docker compose -f /srv/serveza-gateway/compose.yml exec gateway caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose -f /srv/serveza-gateway/compose.yml exec gateway caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

يمكن استخدام صيغة `docker run` من الخطوة السابقة إذا لا يوجد Python. لا تنسخ أسرار الموقع الأول أو `.env.compose` منه إلى الثاني. لا تكرر الدومين في ملفي توجيه، ولا تعطِ مشروعين نفس الاسم؛ اسم المشروع يحدد الحاويات والشبكات والصورة والـvolumes. يستهدف المسار alias فريدًا مثل `site-two-proxy:8080`، وليس الاسم المشترك `proxy`.

## التشغيل والعزل

من داخل مجلد الموقع، استخدم `--env-file .env.compose` دائمًا:

```bash
docker compose --env-file .env.compose ps
docker compose --env-file .env.compose logs --tail 100 web proxy scheduler
docker compose --env-file .env.compose up -d --build --wait --wait-timeout 300
docker compose --env-file .env.compose down
```

الأمر الأخير يوقف ذلك الموقع ويحفظ بياناته. تبقى المواقع الأخرى والبوابة تعمل. لا تستخدم `down -v` على بيانات حقيقية. لا تغيّر اسم المشروع أثناء التحديث أو مسار مجلد الأسرار. أوامر النسخ والتحقق في `DOCKER.md` نفسها مع إضافة `--env-file .env.compose` بعد `docker compose`. انسخ شهادات البوابة وإعدادها ومساراتها منفصلًا عن نسخ المواقع.

فقط وكيل الموقع ينضم للشبكة المشتركة. تبقى قاعدة البيانات والتطبيق والعامل على شبكة المشروع الخاصة، ولا تُشارك الـvolumes أو أسرار التطبيق مع البوابة أو المواقع الأخرى. يقبل وكيل الموقع طلبات HTTP على 8080 من IP البوابة المحدد فقط؛ البوابة تستبدل `X-Real-IP` قبل التوجيه، ثم يمرره وكيل الموقع إلى Gunicorn عبر Unix socket مع HTTPS. لا توسّع قائمة الثقة لتشمل الشبكة كلها، ولا تنشر 8080 أو Gunicorn على السيرفر. مسؤول Docker على الخادم يظل صاحب صلاحية على جميع الحاويات؛ هذا الإعداد ليس حدًا أمنيًا بين ملاك سيرفر غير موثوقين.

## نقل نسخة قائمة من الوضع الفردي

لا تعِد تشغيل سكريبت التهيئة فوق إعداد قائم؛ لا تحتاج توليد أسرار أو رموز جديدة. خذ نسخة احتياطية، واحتفظ باسم مشروع Compose الحالي بالضبط (من `docker compose config --format json` أو `docker compose ls`). أنشئ `.env.compose` يدويًا:

```dotenv
COMPOSE_PROJECT_NAME=existing-project-name
COMPOSE_PATH_SEPARATOR=:
COMPOSE_FILE=docker-compose.yml:docker-compose.multi.yml
EDGE_PROXY_IP=10.240.0.2
EDGE_NETWORK=serveza-edge
```

أضف ملف توجيه مطابقًا للدومين النهائي في البوابة:

```caddyfile
https://one.example.com {
    reverse_proxy existing-project-name-proxy:8080 {
        header_up X-Real-IP {remote_host}
    }
}
```

خلال نافذة الصيانة، أوقف النسخة القديمة بـ`docker compose stop` لتحرير 80/443، وابدأ البوابة ثم الموقع باستخدام `.env.compose`، وتحقق وأعد تحميل البوابة. احتفظ بـ`.env.docker` والأسرار والـvolumes و`SITE_URL` و`qr_public_origin` ورموز الطاولات كما هي. إذا كان المشروع القديم يستخدم `_` في اسمه، استخدم نفس الاسم في Compose وalias والمسار بعد التحقق من دعمه في DNS الداخلي؛ سكريبت المشاريع الجديدة يقتصر على أحرف صغيرة وأرقام وشرطات لتجنب هذا الالتباس.

## مواقع بتقنيات أخرى

وصّل خادم HTTP الخاص بالمشروع الآخر بشبكة Docker الخارجية `serveza-edge` باسم alias فريد، دون نشر منفذه على المضيف، وأضف ملف `*.caddy` في البوابة يوجّه الدومين إلى ذلك الاسم والمنفذ. مثال: `reverse_proxy other-project-web:3000` داخل بلوك الدومين، مع `header_up X-Real-IP {remote_host}`. اضبط الثقة في الوكيل وإعدادات الأصل وHTTPS حسب إطار ذلك المشروع. لكل مشروع قاعدة وملفات وأسرار مستقلة؛ البوابة لا تحتاج الوصول إلى Docker socket أو بيانات قواعد المواقع.

## التحقق

`scripts/docker_multi_smoke.py` ووظيفة `docker-multi` في CI يبنيان نسختين تجريبيتين وبوابة واحدة، ويفحصان HTTPS حسب الدومين وRTL/LTR والأصول والصور وعزل بيانات الحسابات والملفات، وعدم نشر منافذ المواقع، ورفض انتحال عنوان العميل من الجار أو الإنترنت، وإعادة إنشاء موقع مع استمرار الآخر. ينشئ الاختبار مشاريع وأسرارًا مؤقتة بأسماء عشوائية، ويزيل **حاويات وvolumes اختباره وحدها** عند الانتهاء. واجهة تشخيص IP وشهادة CA المحلية تُضافان إلى نسخ الاختبار المؤقتة فقط. يحتاج Docker على Linux ومنافذ 80/443 متاحة؛ لا تشغّله على سيرفر التشغيل العام.

التحقق المحلي: 329 اختبار core، منها 318 ناجحة و11 PostgreSQL متخطاة، وفحوص Django والترحيلات وCompose 2.30.3 وCaddy 2.11.9 ناجحة. نجحت تجربة HTTP محلية بثلاث عمليات Caddy لتوجيه دومينين وفصل الملفات واستبدال عنوان العميل المزور ورفض مصدر مجاور؛ استخدمت منافذ loopback مؤقتة وخوادم تطبيق وهمية بدل حاويات Docker.

Docker غير مثبت على جهاز التطوير الحالي؛ إضافة اختبار CI لا تثبت نجاح تشغيله، والتجربة المحلية لا تفحص شبكة Docker أو الحفظ بين الحاويات أو شهادة الدومين العام. تبقى وظيفة CI وفحص الدومينات وDNS والبريد على السيرفر الفعلي بوابات اعتماد مفتوحة.
