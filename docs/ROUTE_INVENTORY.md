# Route inventory

The existing Django contract uses trailing slashes. The language switch preserves query filters and sets a first-party preference. A restaurant slug identifies the venue. Staff operations also resolve an active membership and current branch on the server. A QR token and order tracking token have different scopes.

## Public discovery

| Routes | Experience |
| --- | --- |
| `/` | Product story, interactive overview, workflow, features, sandbox, estimates and plan discovery. |
| `/features/`, `/features/<slug>/` | Search/filter index and category pages. FEATURES_MATRIX.md lists the category slugs and subfeatures. |
| `/solutions/`, `/solutions/<slug>/` | Venue and role-specific scenarios. |
| `/how-it-works/`, `/demo/` | Service walkthrough and isolated interactive shift. |
| `/demo/menu/`, `/demo/menu/order/` | Existing menu demo and server-validated demo total. Guest orders are separate. |
| `/pricing/`, `/pricing/summary/` | Comparison and public summary of plan data. |
| `/about/`, `/contact/` | Mission and validated demo request. |
| `/resources/`, `/resources/<slug>/`, `/faq/` | Guides, article pages and questions. |
| `/privacy/`, `/terms/`, `/cookies/` | Legal drafts with review status. |
| `/offline/`, `/maintenance/`, `/public-worker.js` | Recovery guidance. On a previously visited secure origin a small worker supplies a public navigation retry screen when offline. It stores no data and never handles staff, table or mutation requests. |
| `/robots.txt`, `/sitemap.xml` | Machine-readable crawl rules and public page discovery. |

## Access and guest service

| Routes | Existing contract and interface |
| --- | --- |
| `/login/`, `/register/` | Password visibility, account validation, sign-in and owner creation. Registration starts email verification. |
| `/verify/`, `/verify/resend/`, `/verify/<token>/` | Pending, resend and verification, including expired or used links. |
| `/invitation/<token>/` | Accept as the invited account or display unavailable invitation. |
| `/password-reset/`, `/password-reset/done/`, `/reset/<uidb64>/<token>/`, `/reset/complete/` | Request, neutral receipt, password form, invalid link and completion. |
| `/logout/` | Sign out via POST. |
| `/t/<token>/` | Table menu, merchant-supplied content, available modifiers and sizes, category/search filters, cart review, service calls and closed-table state. |
| `/t/<token>/order/`, `/t/<token>/call/` | Submit order, waiter or bill request. Session and server acceptance determine success. Existing order retries remain idempotent. |
| `/track/<token>/` | Status, item snapshots, order total and additional order in an eligible session. |
| HTTP 403, 404, 500 | Shared service branding and recovery links. Django's development debug pages differ from production error handling. |
| `/health/` | Database-backed machine health endpoint with no rendered page. |

## Restaurant workspace

All paths below are relative to `/app/<slug>/`. The shell shows a role-aware menu, authorized venues and branch selection. Role-specific controls supplement the server-side checks.

| Route | Roles / workflow |
| --- | --- |
| Workspace root | Branch summary, recent orders and shortcuts. Owner and manager also see seven- or fourteen-day counts, average order, order value and a resumable five-step setup checklist derived from stored records. |
| `menu/`, `menu/import/` | Owner and manager. Categories, visibility, items, prices, images, sizes, grouped modifiers, station assignment, CSV/XLSX upload, staged preview and confirmation. |
| `tables/`, `tables/<id>/qr/` | Owner and manager. Table creation, activation, session closure, QR rotation, guest preview and PNG download. |
| `tables/<id>/summary/` | The owner, manager or an assigned cashier selects a table in order history and opens its printable current-session summary. The page reads stored line prices and manual collection states and excludes canceled orders. |
| `orders/`, `orders/new/`, `orders/<id>/edit/` | History and table filtering. Owner, manager, cashier and waiter may create or edit eligible orders. A cashier uses this order ledger for manual collection. |
| `orders/<id>/status/`, `orders/<id>/correct/`, `orders/<id>/payment/` | POST commands. Per-transition role checks, reason for correction and manual collection. |
| `kitchen/`, `kitchen/feed/`, `orders/<id>/lines/<line>/status/` | Owner, manager and kitchen. Branch or station tickets, refresh/reconnect, filtering, item preparation, delay flags and whole-order transitions. Fullscreen and opt-in sound depend on browser support. |
| `calls/`, `calls/<id>/status/` | Owner, manager, cashier and waiter. Waiter/bill requests, response thresholds, active/bill filters, pagination, acknowledgement and completion. |
| `reports/` | Owner and manager. Date/station filters, samples and averages, order and collection values, sources, best sellers and streamed CSV export. |
| `staff/` | Owner and manager, with additional role gates. Invitations, direct accounts, role/branch assignment and activation. |
| `branches/`, `branches/select/` | Owner/manager maintain branches. Authorized members select their assigned branch. |
| `settings/` | Profile, guest contact publishing, receiving orders, response thresholds, limits, owner-only preview and subscription payment history. |

## Platform

| Routes | Existing contract and interface |
| --- | --- |
| `/platform/`, `/platform/restaurants/new/` | Superuser operator. Venue search, pagination, venue/owner provisioning and subscription/account review. |
| `/platform/<id>/branches/`, `/platform/<id>/update/`, `/platform/<id>/payments/` | Superuser POST actions for branch, subscription/venue status and manual subscription receipts. |
| `/django-admin/` and registered model descendants | Django's role-checked admin screens, shared brand styling, plan editing, read-only operational records and a superuser demo-request inbox. |

## Evidence and boundaries

The browser pass used the local isolated demo fixture at 360 and 1440 px in Arabic and English. It included all 13 restaurant screen variants, platform list/create, Django admin index, kitchen/waiter accounts, guest menu/tracking, registration, login, verification and reset states. Invalid verification returned 400 and a missing invitation returned 410. There was no document overflow or uncaught page error in this pass. Merchant names, menu items, order snapshots and addresses retain the language entered by the venue. A separate Chrome test using its own temporary Django database exercised an Arabic cashier recording collection after an English guest ordered a large drink, the kitchen moved its ticket through preparation, and a waiter acknowledged the bill request. Run it with `python manage.py test scripts.test_restaurant_ui`.

The platform does not provide a payment gateway, per-session split settlement, a fiscal invoice, floor-plan editor, or a reservation backend. The existing setup checklist persists through venue and branch records. It has no standalone linear wizard or automatic launch switch. SMTP delivery, production-specific errors, backups and deployment require separate environment checks. REDESIGN_REPORT.md records wider visual checks and production prerequisites.
