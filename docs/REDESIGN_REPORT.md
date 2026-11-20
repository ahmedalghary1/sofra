# Serveza redesign report

**Local implementation and verification, 20 November 2026.** Serveza now has a bilingual public product site, a searchable guide to 18 product areas, a working table-service sandbox and redesigned screens for the existing restaurant and platform roles. The public site and operating application share the Django project and database contracts.

## Changes by route

| Destination | Result |
| --- | --- |
| `/` | Original responsive product scene, eight role/product tabs, service journey, bento feature links, role solutions, synchronized sandbox, optional dessert example, adjustable ROI, plan teaser, FAQ and trial/demo actions. The anonymous initial page remains database-free. The pricing teaser reads the pricing service near the viewport. Existing authenticated redirects are preserved. |
| `/features/` and 18 detail pages | Server-rendered search with category, role, plan and availability intersections, grouped navigation, stable capability links, category previews, workflows, related features and FAQs. |
| `/pricing/`, `/pricing/summary/` | One source for publication-safe configured prices and limits. The three fallback tiers carry an explicit proposal label. No annual terms were supplied. |
| `/solutions/` and six detail pages | Cafés, restaurants, chains, owners, waiters and kitchen teams each have a service scenario and links to the relevant tools. |
| `/demo/`, `/demo/menu/` | A browser-memory simulation links a guest, kitchen, waiter and dashboard. The existing restaurant sample menu still confirms its total at its dedicated database-free endpoint. |
| `/how-it-works/`, `/about/`, `/resources/`, six guide pages, `/faq/` | Service workflow, local positioning, searchable setup and operations guides, and practical answers rooted in the running product. |
| `/contact/`, platform lead inbox | Server-validated form with CSRF, replay-safe saves, confirmation and database-failure handling. Migration `0022_demo_request` adds one table. Only an active superuser can read the inbox. Requests await operator review. |
| Privacy, terms, cookies and recovery | Labeled review drafts, cookie details, branded 403/404/500, maintenance guidance, and a network-failure retry screen for visited public origins. The public worker handles eligible GET navigations without caching records. There is no background order submission. |
| Auth, onboarding, platform and Django admin | Language switch, password visibility, accessible forms and translated email flows. Setup progress is derived from branch records. Platform operators can search and page through venues, handle subscriptions and review demo requests. |
| Owner and branch workspace | Responsive role-aware shell, branch/venue selectors, actual 7/14-day branch activity and sales summaries, menu/import, staff, tables, QR, settings, reports and CSV exports. |
| Guest, kitchen, waiter and cashier | Mobile menu search and cart, size/modifier selection, price-change review, server-confirmed orders, tracking and service calls. KDS retains polling/reconnect and adds full screen and opt-in sound. Authorized staff can print an itemized current-session balance from order history and record manual payment per order. |

All route names, methods and guards are accounted for in the [route inventory](ROUTE_INVENTORY.md). The [feature matrix](FEATURES_MATRIX.md) identifies 35 available, 24 planned and five unsupported capability groups. The catalog reserves an in-development state for work with verified implementation evidence.

## Components, assets and dependencies

The shared identity, Cairo, Django rendering, Decimal pricing, order snapshots, authentication, tenancy checks, idempotency and transition histories remain in use. New reusable parts cover navigation, disclosures, product panels, the sandbox, plan comparison, feature previews, service diagrams, branch activity and printable account summaries. See the [design system](DESIGN_SYSTEM.md) and [UI guidelines](UI_UX_GUIDELINES.md).

Production Python and JavaScript dependencies are unchanged. The new pages use plain JavaScript, CSS, Django templates and local static assets. A one-time FontTools/Brotli conversion reduced the Cairo font from 599,548 to 177,316 bytes. Python Playwright, Chrome, Lighthouse, axe and Ruff are local QA tools. The café image is original illustrative photography generated with ImageGen. The social preview renders the actual product artwork. [Asset credits and prompt](../static/marketing/README.md)

## Verification

Environment: Windows 10, Python 3.14.3, Django 5.2.12 and SQLite. Browser tests used Playwright 1.58.0 and local Chromium/Chrome. The main capture pass used Chrome 158.0.8127.36. Tests and screenshots used isolated fixtures. The additive migration was also applied to the existing local workspace database.

| Check | Result |
| --- | --- |
| Baseline | `manage.py check` passed. All 98 original core tests passed. Eight routes × three widths produced 24 screenshots. |
| Final Django suite | All 113 core tests passed serially (343.946 seconds) and on a second run using four workers (334.730 seconds). Coverage includes existing business operations, localization, links, catalog filters and pricing, inquiry replay/error handling, protected reads, email links, price revalidation and the printable balance. |
| Browser layout | 20 representative screens × two languages × six widths produced 240 successful loads. No document overflow, unexpected HTTP failure or uncaught page error appeared in this pass. Widths were 360, 390, 768, 1024, 1440 and 1920 CSS pixels. |
| Catalog | 27 catalog, solution and pricing routes × both languages × six widths produced 324 successful checks. Combined GET filters, no-match recovery, anchors and keyboard FAQ interaction passed. |
| Live service | The separate static live-server test took an English guest order with a size choice through kitchen preparation and a waiter bill request to recorded collection by an Arabic cashier. The database and browser sessions were isolated. |
| Public interactions | Bilingual Playwright scenarios exercised showcase tabs, full demo lifecycle and reset, upsell consent, ROI validation, real sample-menu checkout, desktop/mobile menus, focus restoration, language-preserving search and offline→online recovery. |
| QR and print | The authorized PNG from the demo fixture matched the QR matrix for the expected table URL. That URL opened the guest table. A current-session summary loaded from filtered order history in both languages, hid controls in print media and rendered to A4 in Chrome. Physical printer/camera field testing remains open. |
| Accessibility | The marketing axe report covers eight public pages in both languages at 390px. A second run covered eight operational/auth/guest screens in both languages. The checked WCAG A/AA rules reported zero violations in these samples. Keyboard, responsive charts/tables, language direction, focus states and reduced motion also received browser checks. [Marketing axe results](redesign-evidence/axe-marketing.json) |
| Build and tooling | Python compilation, JavaScript syntax checks, migration drift, `git diff --check` and Django static collection passed. New public Python modules and browser scripts passed Ruff. The six public foundation/content/navigation modules checked with Ruff's formatter passed. A separate host-configured Ruff lint across `core config scripts` still reports 84 findings, compared with 85 on the original `core config` checkout. Most concern existing Django class attributes and import ordering. This repository has no frontend package build or configured TypeScript/static type-check pipeline. |
| Release configuration | A local DEBUG=False smoke used an ephemeral secret, explicit hosts, HTTPS requests from the Django client and an isolated SQLite configuration. Public pages and worker returned 200 and a missing path returned 404. `check --deploy` identified W005 and W021 for HSTS subdomain coverage and preloading. These need a domain-level decision. |

### Mobile Lighthouse

The final paired run used Lighthouse 13.4.1 and Chrome on the local Django development server. The mobile preset simulated 150ms RTT, 1,638.4 Kbps throughput and 4× CPU slowdown. Results are a single navigation per language and carry normal run-to-run variance. [Measured environment and results](redesign-evidence/lighthouse-mobile.json)

| Homepage | Performance | Accessibility | SEO | LCP | CLS | Total blocking time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Arabic | 86 | 100 | 100 | 2.71 s | 0.006 | 408 ms |
| English | 92 | 100 | 100 | 3.02 s | 0.005 | 0 ms |

The English homepage exceeded the requested performance score of 90. The Arabic homepage fell four points short. LCP exceeded the 2.5-second target in both runs. The responsive CTA layout removed a measured English font-swap shift from an earlier run (CLS 0.170), and the final run used a 76,144-byte Latin Cairo subset for English text. Repeat on the intended host with its static asset cache and compression, then measure INP with real interactions and field traffic.

### Evidence and repeatable commands

Compare the [original homepage](redesign-evidence/before/home-ar-1440.jpg) with the [redesigned homepage](redesign-evidence/after/home-ar-1440.jpg) and [mobile layout](redesign-evidence/after/home-ar-390.jpg). There are 24 baseline and 62 post-redesign screenshots. Representative discovery, workspace and guest views are in [before](redesign-evidence/before) and [after](redesign-evidence/after). The [coverage results](redesign-evidence/route-coverage.json) identify routes and widths. Supplementary evidence includes the [operations accessibility results](redesign-evidence/axe-operations.json) and [print view](redesign-evidence/after/session-summary-ar-print.jpg).

```powershell
python manage.py check
python manage.py test core
python manage.py test scripts.test_restaurant_ui
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python scripts/test_public_navigation.py http://127.0.0.1:8000
python scripts/test_product_demo.py http://127.0.0.1:8000
python scripts/test_feature_catalog.py http://127.0.0.1:8000 --all-widths
```

## Assumptions and release boundaries

- The local Plan table has no publish flag. The comparison reads recognized public tier names and does not expose arbitrary venue agreements. The newer brief's proposed 600/1,300/2,500 EGP monthly figures differ from the earlier specification. An explicitly configured public tier takes precedence.
- The demo request inbox is the available handoff to the platform operator. Saving a request does not book a meeting or send an email to a lead. Actual delivery of account mail requires configured SMTP.
- Merchant-entered names, notes, addresses and historical records retain their original language. The language switch covers interface text. The six instructional guides were written from current workflows. Legal content awaits the operator's review.
- Live functionality includes manual collection and a printable current-session service summary. Online payments, fiscal invoices, session-wide settlement, split tenders, reservations, CRM/loyalty, commercial promotions, cross-product upsell automation and external POS integrations remain outside the operating backend.
- Onboarding resumes from the existing persisted venue, menu, table and team records. A maintenance page is provided for recovery navigation; an actual maintenance window is controlled by the hosting operator. The marketing worker supplies navigation guidance on network failure. A first visit and real transactions require connectivity.
- The Django deployment path is retained. Docker, a production PostgreSQL host, an outbound SMTP service, a public domain, restore exercises and restaurant devices were not available for deployment verification. Field INP, real traffic and published-host performance remain unmeasured. Before a commercial release, verify these with a real branch and approve public plan records and legal language.
