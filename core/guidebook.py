"""Public help content checked against the branch, guest, service and billing workflows."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Guide:
    slug: str
    category: tuple
    title: tuple
    lead: tuple
    steps: tuple
    feature: str


GUIDES = [
    Guide(
        "first-service",
        ("البداية", "Getting started"),
        ("جهّز أول وردية", "Prepare your first service"),
        (
            "من تفعيل البريد لحد أول طلب تجريه في مطعمك.",
            "From verifying your email to running a test at your own table.",
        ),
        (
            (
                "فعّل بريد حساب المالك ثم افتح لوحة المطعم. اختر الفرع الذي سيستقبل الطلبات.",
                "Verify the owner email, open the workspace and select the branch that will receive orders.",
            ),
            (
                "أضف تصنيفًا وصنفًا متاحًا بسعره. يمكنك معاينة استيراد CSV أو XLSX قبل اعتماده.",
                "Create a category and an available item with a price. You can also preview a CSV or XLSX import before saving.",
            ),
            (
                "أنشئ طاولة ونزّل رمزها. افتح شاشة المطبخ بحساب مخوّل واختبر الرمز من هاتف.",
                "Create a table and download its code. Open the kitchen screen with an authorized account and scan the code from a phone.",
            ),
            (
                "طلب الاختبار على طاولة مطعمك يُسجَّل كطلب فعلي. استخدم الديمو المعزول للتدريب الأولي.",
                "A test on a real venue table creates a real order. Use the isolated demo for initial training.",
            ),
        ),
        "support",
    ),
    Guide(
        "table-qr",
        ("الصالة", "Floor service"),
        ("افحص كود الطاولة قبل الطباعة", "Check a table code before printing"),
        (
            "تأكد أن الضيف يفتح منيو الفرع والطاولة الصحيحين.",
            "Check that guests reach the right branch and table.",
        ),
        (
            (
                "اختر الفرع من لوحة المطعم. افتح الطاولات وتأكد من اسم الطاولة وتفعيلها.",
                "Choose the branch in the workspace. Open Tables and check the table name and active status.",
            ),
            (
                "نزّل صورة PNG وافتح رابطها بكاميرا هاتف. راجع اسم المكان والطاولة قبل طباعة نسخ أخرى.",
                "Download the PNG and scan it with a phone. Check the venue and table name before printing copies.",
            ),
            (
                "بعد تجديد الكود اطبع النسخة الجديدة واستبدل الستاند القديم.",
                "After rotating a code, print the new one and replace the old stand.",
            ),
        ),
        "tables",
    ),
    Guide(
        "menu-import",
        ("المنيو", "Menu management"),
        ("راجع ملف المنيو قبل اعتماده", "Review a menu import"),
        (
            "المعاينة تعرض أخطاء الملف قبل تغيير المنيو.",
            "Preview the file and resolve errors before changing the menu.",
        ),
        (
            (
                "اختر الفرع الصحيح، ثم افتح استيراد المنيو من إدارة قائمة الطعام.",
                "Select the intended branch and open Import from menu management.",
            ),
            (
                "استخدم أعمدة النموذج المعروض، وارفع ملف CSV أو XLSX. راجع الصفوف وأسعارها ورسائل الخطأ.",
                "Use the column names shown on the import form. Upload CSV or XLSX and review the rows, prices and errors.",
            ),
            (
                "اعتمد المعاينة السليمة ثم افتح المنيو من كود الطاولة للتحقق من النتيجة.",
                "Confirm the valid preview, then open the guest menu from a table code to check the result.",
            ),
        ),
        "digital-menu",
    ),
    Guide(
        "kitchen-handoff",
        ("المطبخ", "Kitchen"),
        ("تابع تذكرة من الاستلام للجاهزية", "Follow a ticket from acceptance to ready"),
        (
            "لكل انتقال حالة وقت محفوظ يساعد الفريق على المتابعة.",
            "Each recorded transition gives the team a service timeline.",
        ),
        (
            (
                "افتح شاشة المطبخ للفرع وتحقق من مؤشر الاتصال. استخدم المحطة المناسبة إذا كانت مهيأة.",
                "Open the branch kitchen and check the connection indicator. Select a station when configured.",
            ),
            (
                "راجع الطاولة والكمية والأحجام والإضافات، ثم استخدم زر المرحلة التالية عند بدء العمل الفعلي.",
                "Read the table, quantities, sizes and extras. Advance the ticket when work actually starts.",
            ),
            (
                "حدّد الجاهزية بعد التحضير. يستطيع الزميل المخوّل تسجيل التقديم من سجل الطلبات.",
                "Mark the ticket ready when preparation is complete. An authorized teammate can then record service.",
            ),
        ),
        "kitchen-display",
    ),
    Guide(
        "retry-order",
        ("الطاولة", "At the table"),
        ("انقطع الاتصال أثناء الطلب؟", "Lost connection while ordering?"),
        (
            "تابع تأكيد الطلب قبل بدء طلب جديد.",
            "Look for confirmation before starting another order.",
        ),
        (
            (
                "إذا لم يظهر التأكيد، احتفظ بنفس الصفحة والسلة حتى يرجع الاتصال.",
                "If confirmation has not appeared, keep the same page and basket until the connection returns.",
            ),
            (
                "أعد المحاولة من نفس السلة. يستخدم الخادم مفتاح الطلب لمنع تكراره عند إعادة الإرسال.",
                "Retry the same basket. The server uses its order key to protect against duplicate submissions.",
            ),
            (
                "لو انتهت جلسة الطاولة، راجع فريق الصالة. بعد التأكيد استخدم رابط المتابعة لمعرفة الحالة.",
                "If the table session has ended, speak to the floor team. After confirmation use the tracking link for status.",
            ),
        ),
        "qr-ordering",
    ),
    Guide(
        "end-of-shift",
        ("الحسابات", "Billing"),
        ("راجع التحصيل في نهاية الوردية", "Review collection at shift end"),
        (
            "تابع ما تم تقديمه وما سُجّل تحصيله في فرعك.",
            "Reconcile served orders with collections recorded at your branch.",
        ),
        (
            (
                "افتح سجل الطلبات وحدّد الفرع والطاولة. راجع السطور والإجمالي المحفوظ وقت الطلب.",
                "Open order history for the selected branch and table. Review line items and the captured total.",
            ),
            (
                "سجّل التحصيل اليدوي من حساب مخوّل بعد استلام المبلغ. يتابع النظام التحصيل وحالة التحضير كلًا على حدة.",
                "Record manual collection from an authorized account once funds have been received. Payment and preparation have separate statuses.",
            ),
            (
                "راجع تقارير الفترة وصدّر CSV للمراجعة. لا تعادل سجلات التحصيل فاتورة ضريبية معتمدة.",
                "Review the reporting period and export CSV for reconciliation. Collection records are distinct from certified tax invoices.",
            ),
        ),
        "billing",
    ),
]

FAQ = [
    (
        "هل الضيف محتاج تطبيق أو حساب؟",
        "Does a guest need an app or an account?",
        "يكفي مسح QR الطاولة بكاميرا الموبايل وفتح المنيو في المتصفح.",
        "Guests scan the table code and open the menu in their browser.",
    ),
    (
        "إيه اللي يحصل لو الإنترنت فصل؟",
        "What happens if the connection drops?",
        "الطلب يحتاج تأكيد الخادم. أعد المحاولة من نفس السلة والجلسة عند رجوع الاتصال، أو اطلب المساعدة من الفريق.",
        "An order needs server confirmation. Retry the same basket in the same session once online, or ask the team for help.",
    ),
    (
        "هل أقدر أستخدم الكاشير الحالي؟",
        "Can I keep my current till?",
        "تقدر تستخدم Serveza لتنظيم الطلبات مع الكاشير الموجود. الربط التلقائي مع أنظمة POS خارجية غير متاح حاليًا.",
        "Your team can run table service alongside its current till. An external POS connector is not currently available.",
    ),
    (
        "إزاي أختار باقة؟",
        "How do I choose a plan?",
        "قارن حدود الفروع والطاولات والأصناف والفريق في صفحة الباقات. الأسعار المقترحة موضحة بعلامة مستقلة.",
        "Compare branch, table, item and staff limits on the pricing page. Proposed figures carry a separate label.",
    ),
    (
        "أبدأ الإعداد منين؟",
        "Where do I start setup?",
        "أنشئ حساب مالك وفعّل بريدك. أضف الأصناف والطاولات للفرع وتأكد من الكود قبل الطباعة.",
        "Create an owner account, verify your email, add branch menu items and tables, and test the code before printing.",
    ),
    (
        "هل يمكن تغيير لغة الواجهة؟",
        "Can I change the interface language?",
        "استخدم زر اللغة للعربية أو الإنجليزية. أسماء الأصناف والأوصاف يعرضها النظام كما أدخلها المطعم.",
        "Use the language switch for Arabic or English. Menu names and descriptions appear as entered by the venue.",
    ),
    (
        "مين يقدر يلغي طلب؟",
        "Who can cancel an order?",
        "يطبق الخادم صلاحيات الموظف وشروط حالة الطلب، ويسجل سبب الإلغاء في السجل.",
        "The server checks the staff role and order state, and records the cancellation reason in order history.",
    ),
    (
        "إزاي تتفصل زيارات نفس الطاولة؟",
        "How are visits to one table kept separate?",
        "لكل طاولة جلسة. بعد إغلاقها يرفض النظام الطلبات المربوطة بالجلسة السابقة.",
        "Each table has a session. Once it closes, orders tied to the old session are rejected.",
    ),
    (
        "هل فيه دفع إلكتروني؟",
        "Can guests pay online?",
        "المتاح هو تسجيل التحصيل اليدوي. صفحة الحساب لا تخصم أموالًا من بطاقات الضيوف.",
        "Authorized staff can record manual collections. Online card processing is outside the current product.",
    ),
    (
        "إزاي أوصل لفريق Serveza؟",
        "How do I reach Serveza?",
        "أرسل اسمك ووسيلة التواصل من صفحة طلب العرض ليظهر طلبك لدى مشغّل المنصة.",
        "Submit your name and contact details on the demo request page. The request goes to the platform operator.",
    ),
]

FLOW = (
    (
        "scan",
        "امسح كود الطاولة",
        "Scan at the table",
        "منيو الفرع يفتح على موبايل الضيف.",
        "The branch menu opens on the guest’s phone.",
    ),
    (
        "cup",
        "اختار وأكّد الطلب",
        "Choose and confirm",
        "الحجم والإضافات والسعر قدام الضيف قبل الإرسال.",
        "Guests review sizes, extras and prices before sending.",
    ),
    (
        "ticket",
        "حضّر وقدّم",
        "Prepare and serve",
        "المطبخ يشوف التذكرة والصالة تتابع الجاهزية.",
        "The kitchen gets a ticket and the floor can follow readiness.",
    ),
    (
        "bell",
        "راجع الحساب",
        "Review the bill",
        "الضيف يطلب الحساب والفريق يسجّل التحصيل.",
        "The guest requests the bill and staff record collection.",
    ),
)
