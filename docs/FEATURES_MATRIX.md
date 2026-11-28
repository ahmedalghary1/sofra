# Feature availability matrix

The public guide contains 18 product categories. Each entry below maps to a bilingual detail section and a stable fragment on its feature page. Search combines category, role, status, plan and free text. Guest-facing previews use fictional data.

- **available** means a concrete application path supports the described operation. Deployment verification is tracked separately.
- **planned** describes a proposed capability without a delivery date or a subscription entitlement.
- **in-development** is reserved for a documented, actively implemented capability. No catalog entry currently uses it.
- **not-supported** identifies an integration, scope or contractual service the current product does not supply.

## Commercial source and limits

The pricing view reads only configured public tiers with the explicit Starter, Growth or Business names or the listed Arabic equivalents. The Plan model has no public visibility field. Custom internal, local demo, and negotiated plans can contain private commercial terms and are omitted. Nothing on the public routes creates or alters plans, subscriptions, pricing or entitlements.

The catalog reads configured public tiers and does not invent fallback plans when none exist. Migrations seed Starter, Growth and Business at illustrative monthly prices of 600, 1300 and 2500 EGP when missing. The earlier product specification proposed 399, 799 and negotiated pricing; those are historical hypotheses, not the current catalog. Configured Plan records supply all five limits and the displayed monthly price, including zero. Subscription agreements capture customer terms independently of later edits to the public offer.

Branch capacity and staff count apply per restaurant. Table, menu item and kitchen station limits apply per branch. Pending invitations reserve seats across creation paths. Disabled resources still count; explicit archival frees capacity and preserves history, and restoration checks capacity. Multiple branches require capacity above one and station setup requires positive station capacity. Agreements can record a manual monthly or annual price basis and receipt, but there is no automated checkout, charging or renewal API. An explicitly started initial trial runs for 14 days without collection. Demo illustrations and ROI assumptions do not add live entitlements.

The home and pricing pages render the monthly price and limits directly from the public Plan records. The comparison includes an example allocation across waiters, administration (including the owner), kitchen and cashier roles. Each tier lists the available capabilities that fit its capacity. Owners can review their effective limits in restaurant settings.

## Catalog evidence

### Digital menu

Detail route `/features/digital-menu/`. Implementation references include `Category, MenuItem, MenuSize, ModifierGroup, manage_menu, menu_import, public_menu`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#browse` | available | Mobile browsing without an account or app, with search, categories, photos, descriptions and Arabic or English interface labels. |
| `#choices` | available | Branch-specific prices, size availability, and free or paid extras with selection limits. |
| `#publish` | available | Category visibility and ordering, featured-item flags, and previewed CSV and Excel imports. |
| `#branding` | available | Owner/manager uploads a verified, optimized logo and chooses menu button, background and text colors, with live preview and contrast validation. Applies to every branch of that restaurant; other venues and saved orders retain their data. |
| `#editorial` | planned | Subcategories, manual item sorting, structured allergens and ingredients, and per-item translations. |
| `#themes` | planned | Additional menu layouts, optional branding removal, banners, meal schedules and guest-facing best sellers. |

### QR ordering

Detail route `/features/qr-ordering/`. Implementation references include `public_menu, place_order, track_order, create_order, create_call`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#cart` | available | Table identity, sizes and extras, quantity controls and a note for the whole order. |
| `#track` | available | Confirmation, tracking, additional orders in the current session, waiter calls and bill requests. |
| `#location` | available | Optional per-branch table-order proximity checks. Owner/manager or platform admin sets the centre and a 10–5000 m radius (default 50). Server checks current policy, a signed short-lived challenge, coordinate age/accuracy and distance. Guest coordinates are not retained. Does not attest device location or apply to staff/online orders or service calls; requires HTTPS, permission and on-site testing. |
| `#history` | planned | Optional guest profiles, personal history, repeat baskets and per-item notes. |
| `#takeaway` | available | Owner-enabled `/order/<restaurant-slug>/` storefront using the saved logo/colors. Branch-specific menus, pickup at no fee or delivery with a restaurant-wide flat fee, guest contact details, server-priced totals and immutable fee/item snapshots. Orders enter branch kitchen and staff queues. Payment and delivery are handled by the restaurant; no courier integration or custom-subdomain provisioning. Deployment validation remains separate. |

### Tables and sessions

Detail route `/features/tables/`. Implementation references include `DiningTable, manage_tables, table_qr, create_order`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#table-code` | available | Permanent random links with pinned origins, dark QR and label colors, and separate session rotation. Emergency revocation is owner-only with exact-label confirmation, a reason and reprint acknowledgement. |
| `#session` | available | Multiple guests on one table session, with session validation on orders and calls. |
| `#layout` | planned | Interactive floor plans, zones, floors, occupancy, session duration, transfers and merges. |
| `#print-pack` | available | Atomic, idempotent numbered table batches within plan limits; A4 PDF cards and ZIP packages with PNG, SVG and a URL/checksum manifest. Local origins are marked as previews. Physical and deployed-domain checks remain required. |

### Live orders

Detail route `/features/live-orders/`. Implementation references include `Order, OrderHistory, OrderRevision, manage_orders, staff_order, kitchen_feed`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#capture` | available | QR and staff orders, acceptance, status history, actors and timestamps. |
| `#correct` | available | Controlled edits before preparation, recorded revisions, reasoned cancellation and duplicate protection. |
| `#history` | available | Paginated, filterable branch history by table and a configurable delay threshold. |
| `#dispatch` | planned | Manual priorities, scheduled orders, automatic printing and server-pushed updates. |

### Kitchen display

Detail route `/features/kitchen-display/`. Implementation references include `KitchenStation, OrderLinePrepHistory, kitchen, kitchen_feed, advance_order_line`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#tickets` | available | Tickets show table, quantities, sizes, extras, the order note and arrival time. |
| `#stations` | available | Station routing, per-line status, filters, readiness and preparation timings. |
| `#refresh` | available | Polling and reconnection, branch delay thresholds and completed order history. |
| `#display` | available | High-contrast display, full screen and staff-enabled sound alerts in supporting browsers. |
| `#expediter` | planned | A dedicated expediter display, integrated thermal printing and a combined multi-branch kitchen. |

### Waiter service

Detail route `/features/waiter/`. Implementation references include `ServiceCall, manage_calls, advance_call, staff_order, order_status_change`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#calls` | available | Waiter calls and bill requests, acknowledgement, progress, completion, actor and time. |
| `#floor` | available | Staff table orders, marking orders served, and separate service and bill response thresholds. |
| `#zones` | planned | Waiter zones and shifts, running table balances, individual performance and dedicated ready alerts. |

### Bills and collection

Detail route `/features/billing/`. Implementation references include `record_manual_payment, order_payment_change, table_summary, reports`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#collection` | available | Printable current-session summary with order totals, recorded collections and balance, plus per-order manual settlement. |
| `#export` | available | Payment state, settlement time and CSV export of branch orders. |
| `#cash-ledger` | available | Collection activity and CSV export by collection and correction timestamp, independently of order creation. |
| `#correction` | available | Owner or venue-manager correction of a mistaken manual entry, with a reason and the original entry preserved. Does not refund funds. |
| `#split` | planned | Whole-session settlement, tax, service, authorized discounts, equal or item splits and multiple tenders. |
| `#payments` | not-supported | Online processing, stored cards, certified tax invoices and direct accounting integrations. |

### Upselling

Detail route `/features/upselling/`. The bilingual catalog in `core/showcase_data.py` is the source. Implemented paths include `ItemRecommendation`, `RecommendationExposure`, `RecommendationConversion`, `RecommendationPairStat`, and `core/recommendations.py`. Deployment verification is separate; each branch opts in after linking its own items.

| Fragment | State | Scope |
| --- | --- | --- |
| `#modifiers` | available | Priced sizes and optional paid modifiers selected by the guest. |
| `#suggestions` | available | Up to three complementary items in QR and online carts, restricted to approved links, current availability and items absent from the cart. Optional activation per branch. |
| `#measurement` | available | Actual impressions, adds, acceptance and attributed saved line value. Cancelled orders are excluded; this is not proof of incremental revenue. |
| `#learning` | available | Rank approved links from completed orders after sufficient evidence and a fresh daily rebuild. Manual priorities prevail; sparse or stale data falls back to rules. |
| `#advanced` | planned | Meal bundles, promotions, A/B experiments and post-order suggestions. |

### Promotions

Detail route `/features/promotions/`. Implementation references include `MenuItem.price, OrderLine.unit_price. No promotion or campaign models.`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#pricing` | available | Change branch base prices while retaining historical order prices. |
| `#offer-rules` | planned | Item discounts, daily offers, bundles, buy-one-get-one and promo codes. |
| `#scheduling` | planned | Happy hour, schedules, minimum spend, branch conditions and offer analytics. |
| `#campaigns` | planned | Opt-in win-back campaigns, vouchers and birthday offers. |

### Reports and analytics

Detail route `/features/analytics/`. Implementation references include `reports, OrderHistory, OrderLinePrepHistory, ServiceCall`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#orders` | available | Order counts, sources, statuses, cancellations, value, collections and top items. |
| `#activity` | available | Daily order counts over 7 or 14 days and average non-cancelled order value for owners and managers. |
| `#times` | available | Date filters, CSV exports and average acceptance, readiness, service response and station prep times. |
| `#branch-summary` | available | Owner and venue-manager aggregation and comparison of venue branches for a selected period. |
| `#comparison` | planned | Period comparisons, peak hours, slow items, table utilization and turnover, scheduled PDF and Excel reports. |
| `#intelligence` | planned | Cost-based profitability, average session bill, offer and upsell attribution, ROI, retention, forecasting and custom reports. |

### Guest experience and CRM

Detail route `/features/customer-experience/`. Implementation references include `create_call, ServiceCall. No customer, feedback or loyalty ledger.`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#assistance` | available | Contact the floor team without a personal account. |
| `#feedback` | planned | Service and item ratings, complaints and satisfaction tracking. |
| `#loyalty` | planned | Opt-in profiles, visit history, favorites, points, rewards, VIP segments, return incentives and consent-based messaging. |

### Reservations

Detail route `/features/reservations/`. Implementation references include `DiningTable manages service sessions. No booking or waitlist model or API.`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#book` | planned | Manual or online bookings, guest count, date, time, table assignment, confirmation and cancellation. |
| `#waiting` | planned | Waitlists, table-ready notifications and reservation analytics. |

### Staff and permissions

Detail route `/features/team/`. Implementation references include `Membership.ROLES, StaffInvitation, scope, branch_for, staff`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#roles` | available | Owner, manager, waiter, kitchen and cashier roles with branch assignments and individual activation. |
| `#audit` | available | Expiring invitations, audit records for sensitive changes and cancellation reasons. |
| `#advanced` | planned | Custom roles, discount permissions, shifts, performance, login session management and MFA. |

### Multiple branches

Detail route `/features/multi-branch/`. Implementation references include `Branch, Membership.branches, branch_for, select_branch, check_creation_limit`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#locations` | available | Branch settings, menus, prices, tables, kitchens, order queues and scoped access. |
| `#reports` | available | Branch selection for reports and operating measures within the employee’s scope. |
| `#aggregate` | planned | Consolidated branch comparisons, group reporting and central menu rollout. |

### Notifications

Detail route `/features/notifications/`. Implementation references include `kitchen_feed, manage_calls, Branch response and delay fields, Subscription`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#signals` | available | On-screen orders, readiness, connection state and late prep or service indicators. |
| `#requests` | available | Waiter calls, bill requests and subscription state in the restaurant workspace. |
| `#delivery` | planned | Daily summaries, subscription expiry and sales trend alerts, channel preferences and opt-in WhatsApp or SMS. |

### Integrations and files

Detail route `/features/integrations/`. Implementation references include `menu_import.py, menu_import, reports CSV branch. No external merchant connectors.`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#files` | available | CSV and Excel menu imports and branch order CSV exports by date. |
| `#connectors` | not-supported | POS, payment, inventory and accounting connectors, a public REST API, webhooks and external integration setup. |

### Security and reliability

Detail route `/features/security/`. Implementation references include `scope, branch_for, create_order, DiningTable.session_token, AuditLog, health`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#boundary` | available | Tenant and branch scoping, role and session checks, order history and sensitive action audit records. |
| `#retry` | available | Idempotent submission, server totals, kitchen connection status and an application health endpoint. |
| `#operations` | planned | Hosting verification for HTTPS, external monitoring and alerts, backup operations and restore drills. |
| `#offline` | not-supported | Fully offline operation or order confirmation before server receipt. |

### Setup and support

Detail route `/features/support/`. Implementation references include `register, verify_owner_email, dashboard, _setup_steps.html, demo_menu`.

| Fragment | State | Scope |
| --- | --- | --- |
| `#checklist` | available | Owner registration, email verification, branch settings and a data-backed setup checklist. |
| `#guides` | available | An isolated menu demo and setup guides for team practice. |
| `#service` | not-supported | Contractual support response times, dedicated account managers or included integration services. |

## Operational verification boundaries

The station feed polls the server. Sound and full screen require browser support, and sound requires user activation. Guest checkout requires server confirmation. Search and the surrounding interface support two languages. Merchant-entered item names and descriptions keep the language supplied by the restaurant.

Reports use branch-specific order and service histories with sample counts. Manual settlement records payment on an eligible order. Tax invoices, card processing, split checks, forecasting and cross-branch consolidated revenue remain outside the available workflows. Test SMTP, HTTPS, PostgreSQL concurrency, physical QR printing and backup restoration in the deployment environment.

## Verification

`python manage.py test core.test_showcase_catalog` exercises both languages for all category and solution details, status and role intersections, recognized and private plan records, fallback pricing, read-only HTTP methods, fragments and unknown slugs.

`python scripts/test_feature_catalog.py http://127.0.0.1:8000 --all-widths` exercises a running local server. The checked Chrome run covered 27 URLs, two languages and six viewport widths, for 324 successful loads with one primary heading and no observed horizontal page overflow or failed asset requests. It also exercised combined search filters, clearing an empty result, following a feature fragment and opening an FAQ with the keyboard. Representative views are under `docs/redesign-evidence/after/catalog-*.jpg`.
