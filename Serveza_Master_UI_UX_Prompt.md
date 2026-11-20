# Serveza — Master UI/UX Redesign & Product Showcase Prompt

> انسخ هذا الملف بالكامل إلى وكيل البرمجة داخل المستودع الحالي. أرفق `Serveza_Brand_Book.pdf` إن كان متاحًا، وأعط الوكيل صلاحية قراءة المشروع وتشغيله واختباره. هذا البرومت مخصص لإعادة تصميم المشروع القائم، مع تطوير الموقع التسويقي وعرض المميزات المخططة بصورة صادقة؛ وليس أمرًا بإعادة بناء الـBackend من الصفر.

## 01 — Your role and mission

Act as a senior product designer, creative director, UX researcher, accessibility specialist, full-stack frontend engineer and QA engineer. You are responsible for transforming Serveza into a premium, production-ready hospitality SaaS experience. Deliver working code, not only a proposal, screenshots or design descriptions.

Serveza is a multi-tenant subscription platform for Egyptian cafés and restaurants. It connects QR ordering, digital menus, table sessions, waiter service, kitchen displays, payment/billing workflows, menu management, sales growth tools and business analytics. Its positioning is: a smarter table, faster service and more opportunities for sales — without unnecessarily replacing an existing POS.

الهدف هو أن يصبح الموقع بمستوى المواقع الاحترافية مثل Aada وWayta وTybl وLinear وStripe من حيث الإخراج البصري، جودة الـUI/UX، طريقة عرض المنتج، الصفحات التسويقية، سرعة الموقع، التفاعلات، واتساق جميع صفحات النظام، مع شخصية Serveza المستقلة.

Your deliverable includes: complete public website redesign; a comprehensive searchable feature catalog and dedicated feature pages; real or sandboxed interactive product demos; pricing and comparison; responsive redesign of every existing authenticated screen; shared design system; interaction and accessibility improvements; tests and documentation. Do not stop after redesigning the homepage.

## 02 — Audit first, preserve functionality

Before editing:

1. Inspect the entire repository: framework, package manager, folder structure, routes, layouts, authentication, APIs, tenant and branch permissions, subscriptions, feature flags, database interfaces, assets, styles, existing tests and deployment setup.
2. Run the existing project and capture baseline screenshots at mobile, tablet and desktop widths. Record failed routes and existing bugs separately. Do not claim a route was tested if it was inaccessible.
3. Build a route inventory covering public pages, auth flows, onboarding, owner dashboard, waiter, KDS, cashier, guest menu, super admin, settings, errors, dialogs and empty/loading states. Mark what exists, what needs redesign and what is missing.
4. Read the brand book and all existing project instructions. Identify visual inconsistencies, placeholder content, duplicate components and UX friction. If an attached reference is unavailable, document the gap.
5. Make a feature inventory with states `available`, `in-development`, `planned`, `not-supported`, using actual API/code evidence. Preserve existing permission semantics and subscription restrictions. Do not treat this prompt's proposed features as already implemented.
6. Create or update `docs/UI_UX_AUDIT.md`, `docs/DESIGN_SYSTEM.md`, `docs/UI_UX_GUIDELINES.md`, `docs/ROUTE_INVENTORY.md` and `docs/FEATURES_MATRIX.md`. If `AGENTS.md` exists, preserve its instructions and append compatible project-specific rules. Track implementation in `docs/REDESIGN_PROGRESS.md`.
7. Implement incrementally. Run relevant lint, type checks, builds and tests after each phase. Keep working functionality and data intact. Do not delete routes, rewrite business logic, reset databases, expose secrets or make destructive migrations for cosmetic changes. If a critical dependency is missing, use safe alternatives and document what remains.

## 03 — Visual references (study before designing)

- Aada: https://www.aada-app.com/ar and https://www.aada-app.com/features — interactive dashboard tabs, guest flow, audience-specific features.
- Wayta: https://waytaapp.com/ — interactive mobile menu, live order preview, different menu themes and plan presentation.
- Tybl: https://www.tybl.co/ and https://tybl.co/platform — clean editorial typography, grouped product offerings, polished hospitality imagery.
- Linear: https://linear.app/ — product storytelling, restraint, layout precision, motion and interface previews.
- Stripe: https://stripe.com/ — visual narrative, reusable product illustrations, hierarchy and complex features explained simply.
- Component inspiration: https://magicui.design/ | https://ui.aceternity.com/ | https://reactbits.dev/ | https://21st.dev/ | https://tailark.com/ | https://ui.shadcn.com/
- Visual examples: https://dribbble.com/shots/27162924-Restaurant-SaaS-Website-UI (editorial layout and food imagery); https://gromenu.com/features/ar (mobile and tablet preview); https://foodscan.xyz/ (QR stand and dashboard presentation); https://waitery.ca/ (KDS and devices); https://avantixone.com/ (QR scan in hospitality setting).

If browser access is available, inspect the references visually at desktop/mobile, and observe navigation, rhythm, typography, feature storytelling and interactive transitions. Record reusable principles in the audit. If unavailable, use the stated principles and the attached references. Do not claim to have inspected inaccessible sites. Do not clone pixel-for-pixel or copy competitors' texts, logos, screenshots, CSS or proprietary illustrations into the production website. Build original Serveza components and use own screenshots/demo data and licensed assets.

## 04 — Design direction and brand system

Design language: Premium Hospitality SaaS + Editorial Minimalism + Product-led Storytelling. Make it feel intentionally art-directed, calm, confident, warm and technologically capable. No generic AI-generated landing page look.

### Color system

Preserve official brand values, with semantic extensions:

| Token | Value | Usage |
|---|---|---|
| brand-primary | #0F2A3D | headings, primary navigation, dark areas |
| brand-accent | #4BAA9C | highlights, decorative accents, charts |
| brand-accent-strong | #2C7A6F | accessible text/actions on light backgrounds, after contrast check |
| text-secondary | #4A5568 | supporting text |
| canvas | #F6F8FA | background |
| surface | #FFFFFF | cards and panels |
| border | #E2E8F0 | separators and borders |
| dark-canvas | #0A1E2D | selected dark sections |
| dark-surface | #173448 | proposed dark surface |

Create accessible semantic colors for success, warning, error, information, disabled and order status; combine status colors with labels and icons. Never rely on color alone. No white small text on light teal without verified contrast; prefer navy text or a darker background. Colors above beyond the brand book are extensions, not replacements. Use navy broadly in marketing accents and selected dark sections, not as a mandatory 55% fill of every operational screen. Keep logo proportions, clear space, approved variants and brand naming intact.

### Typography

Use Cairo as the official Arabic and English font by default, with Noto Sans Arabic fallback. Optimize font loading, use weights 400/500/600/700/800 only as needed. Arabic letter-spacing: 0. Body line-height 1.5–1.7. Use tabular numbers in operational tables and charts. Use Arabic-friendly bidi handling in mixed Arabic/English text, EGP prices, URLs and numeric codes.

- Marketing H1: fluid clamp, approximately 40–72px desktop and 32–40px mobile; allow Arabic text to wrap naturally.
- H2: 30–48px desktop, 24–32px mobile; H3: 20–28px; body 15–18px; labels 12–14px.
- Dashboard headings 24–32px, compact tables and labels 13–15px; KDS text large enough to read at working distance.
- Use consistent Western digits 0–9 as directed in the brand book. Localize currency, dates, singular/plural and messages.

### Layout and components

Max-width 1200–1280px; 12-column desktop grid, 8-column tablet, 4-column mobile. Use spacing based on 4/8px. Marketing sections normally 80–128px vertical desktop and 48–72px mobile; operational screens use compact spacing appropriate to tasks. Cards 12–24px radius; buttons 10–14px; subtle borders and controlled shadows. Use consistent iconography (Lucide outline, 2px), focus outlines and touch targets around 44px or larger for frequent mobile actions.

Create reusable components and tokens, including: Container, SectionHeader, Button, TextLink, Badge/Status, Input, Select, FormField, Dialog, Sheet, Tooltip, Tabs, DataTable, EmptyState, Skeleton, Toast, ErrorState, Sidebar, Navbar, Breadcrumbs, StatCard, Chart, ProductMockup, FeatureCard, BentoGrid, FeatureNav, PricingCard, CompareTable, DeviceFrame, DemoControls and FAQAccordion. Define variants and states (default/hover/focus/active/disabled/loading/error). Use logical CSS properties and RTL-safe layout. Avoid global styles that break the existing app.

DO NOT use excessive gradients, glassmorphism, floating decorative pills, identical icon-card grids, oversized shadows, fake 3D, random blobs, inconsistent icon libraries or clutter. Visual hierarchy and content matter more than visual effects. Do not use generic food stock images as a substitute for product interfaces. Use locally owned or licensed photography, original icons, authentic cafe imagery and component-rendered UI previews. No generated illegible UI or QR codes in production materials.

## 05 — Technical implementation strategy

Respect the current stack. If Next.js/React/TypeScript already exists, improve it rather than migrating blindly. For greenfield React modules, prefer TypeScript, Tailwind CSS, shadcn/ui (customized), Lucide, Motion, and Recharts or the existing chart library. Use GSAP/ScrollTrigger only for justified complex scroll stories, not in parallel with Motion for the same element. Lenis is optional and only if it does not harm native scrolling, navigation, reduced-motion or accessibility. If Django/DRF is the backend, retain its contracts and use current authenticated clients. Avoid introducing packages without checking compatibility, license, bundle impact and project conventions.

Create a shared data-driven content model for features, solutions and plan entitlements; don't duplicate descriptions, routes and pricing across hardcoded components. Use real API data in authenticated screens, not mock numbers. Sandbox demos can use seeded local state or dedicated isolated demo tenants; mark fictional data as demo. Do not leak tenant data, real customer records or production endpoints that permit unauthenticated writes. Respect server-side authorization; hiding a menu item is not an access control.

## 06 — Public navigation, footer and information architecture

Design a sticky header with official Serveza logo, Home, Features, Solutions, How it works, Pricing, Live Demo, Resources, Login, language switch and one primary CTA. On large screens create a keyboard-accessible Features mega menu grouped by Guest Experience, Restaurant Operations, Sales Growth, Management, Integrations. Each entry opens a real route or meaningful section. Mobile: a simple slide-out menu with expandable groups. Close with Escape, outside click and navigation, restore focus; ensure no overflow. Keep navigation bilingual.

Footer: product, solutions, resources, support, legal and contact groups, real social links only, language switch, correct copyright. No dummy links, no dead anchors. Use clear CTAs (جرّب المنيو / احجز عرضًا توضيحيًا / ابدأ تجربتك) with actual destinations. If self-signup/trial is not implemented, use a working demo request flow, not a false activation promise.

## 07 — Homepage: implement section by section

1. **Hero**: one clear primary message: «نفس الزباين، مبيعات أكتر، وخدمة أسرع». Supporting text: «حوّل كل طاولة إلى نقطة بيع ذكية، من مسح الكود لحد وصول الطلب للمطبخ ومتابعة الحساب». Use a large headline, primary trial/demo CTA and secondary live menu CTA. Art direct an original product composition with owner dashboard, mobile guest menu and small real-time order/KDS preview; include a physical QR stand/cafe photo as an optional supporting detail. Layout may be split or centered depending on breakpoint and Arabic typography. Use a restrained entrance motion, not looping distractions.
2. **Value strip**: three or four truthful benefits such as no app download, table QR, live orders, Arabic/English, subject to implementation. No fictional logos, client counts, ratings, revenue guarantees or percentages.
3. **How It Works**: three clear stages Scan → Order → Prepare/Serve, with optional bill step. Show animated UI states: QR scan, food selection, order card arriving at KDS, ready notification and bill request. Responsive on mobile; provide a static fallback.
4. **Product Showcase**: interactive browser/tablet preview and tabs: Overview, Live Orders, Menu, Tables & QR, Kitchen, Waiter, Billing, Reports. Change the preview and adjacent description on each tab. Use real screenshots captured from the current product or functional mock UI with realistic labeled demo records. Provide keyboard navigation, mobile tabs and a stable layout.
5. **Features Bento**: large asymmetric cards displaying functional miniature product UI, not generic icons. Primary large cards for QR Menu and KDS, secondary cards for Tables, Waiter Calls, Billing, Upselling, Analytics, staff, promotions and multi-branch. Add a prominent link to All Features and accessible links to each feature category. Do not hide all features exclusively behind this section.
6. **Live product demo**: show guest phone + KDS + waiter event view synchronized through demo state. Add item, customize, submit, accept, prepare, ready, serve, call waiter, request bill, and reset. Display estimated totals using demo data; show status updates, errors and order confirmation. Test the full flow. If no backend demo tenant exists, use isolated client-side simulation, explicitly labeled. Never send demo orders to live venues.
7. **Upsell showcase**: a real interaction for selecting coffee and accepting/declining a suggested dessert. Show bill total updating immediately. Example 80 EGP coffee + 70 EGP dessert = 150 EGP. Label illustrative, not a typical guaranteed uplift. Respect availability and consent; don't auto-add products.
8. **Solutions**: separate experience for cafe, restaurant, chain, and venue roles. Use actual context, specific benefits and links to detail pages. Don't advertise unsupported pickup, loyalty or payments as live.
9. **Operations story**: explain one order flowing from guest to kitchen and waiter to cashier and owner's report. Use original SVG/CSS visuals or live UI. Make it understandable with animations disabled.
10. **ROI calculator**: input monthly bills, percentage of digital orders, offer acceptance, incremental add-on value, contribution margin and subscription. Output estimated incremental revenue and net contribution after subscription, with adjustable assumptions, rounding, invalid value handling and a disclaimer that results are illustrative and do not guarantee causal uplift.
11. **Integrations**: show only supported integrations as available; roadmap integrations as planned. Don't use unauthorized partner logos or fabricate payment/POS connections.
12. **Pricing teaser**: Starter 600 EGP, Growth 1300 EGP, Business 2500 EGP per month as proposed business data from brand strategy. Use saved platform configuration if different and flag conflicts rather than overwriting production pricing. Show clear inclusions, limits, plan comparison link, monthly/yearly toggle only when annual terms are approved or explicitly marked illustrative. Do not invent discounts, taxes, service promises or trial durations.
13. **Trust**: show actual security capabilities, available customer support, verified testimonials and case studies only. If none exist, use an honest onboarding/demo section instead of fake reviews and logos.
14. **FAQ**: answer QR, internet outage, POS, subscriptions, setup, languages, canceled orders, table security, payments and support accurately according to current system. Use accessible accordion.
15. **Final CTA + Footer**: one strong next action, contact fallback, consistent brand, no duplicated filler sections.

## 08 — Dedicated All Features page (critical)

Create `/features` or the equivalent existing localized route. Do not make it a flat grid of hundreds of tiny cards. Design an editorial landing page with a clear hero, a sticky category index, searchable/filterable catalog (category, user role, plan and availability), rich product preview cards, multiple full-width feature stories, links to detailed pages, pricing CTA and demo CTA. Ensure filters can be cleared and work correctly. Use informative empty states and URL-safe filter navigation where compatible.

Each category needs its own original visual, user benefit, concise summary, feature list, availability/plan indicators and CTA. Each feature item should be navigable to a dedicated page or anchored detail with meaningful content; generate separate routes for major categories, not thin duplicate SEO pages for every button. Feature categories below are the planned catalog based on Serveza's previous proposal. Display verified `available` features as live, planned ones as roadmap or hide them by default, depending on marketing configuration. Do not create a false impression that everything has been implemented.

### Comprehensive feature catalog to represent

| Category / suggested slug | Feature inventory to cover |
|---|---|
| Digital Menu / digital-menu | web menu without app or mandatory registration; AR/EN; mobile responsive; categories/subcategories; pictures/descriptions; search; live price and availability; sizes, modifiers, sugar, ingredients, paid/free extras; item notes; allergy information; sort products; Excel import; theme/brand customization; layouts; featured and best sellers; banners; breakfast/lunch/dinner schedules; branch-level pricing; custom branding/removal of Powered by Serveza subject to plan. |
| QR Ordering / qr-ordering | per-table QR, scan flow, cart and quantities, item customization, order submission, table identity, order tracking, repeat orders within session, receipt/bill request, call waiter, confirmations, optional guest accounts, order history, reorder; takeaway where supported. |
| Table Management / tables | generate/download/print QR PNG and PDF; enable/disable tables; open/close sessions; secure session verification; multiple guests per session; occupancy, session duration, transfer/merge tables, table areas, custom QR templates, interactive floor plan, multiple floors/branches. |
| Live Orders / live-orders | real-time orders and sound; accept/reject; manual orders; changes before preparation; cancel reasons; statuses; employee and timestamps; searchable/filterable history; duplicate prevention; print; delays, priorities and scheduled orders; branch-wide monitoring, supported integrations. |
| Kitchen Display / kitchen-display | KDS, live tickets, quantities, notes, extras, table/order ID; status transitions; timers; sound; fullscreen; dark mode; completed history; multiple stations, kitchen/bar/dessert routing; prep-time thresholds; delays; station performance; expediter screen; compatible printing and multi-branch view. |
| Waiter & Service / waiter | active tables, session opening/closing, manual orders, waiter calls and bill requests, ready alerts, mark served, table balance, waiter assignments and zones, response times, staff performance and shifts. |
| Billing & Payments / billing | combine session orders, total, taxes/service charges, manual discounts with authorization, payment method/status, print account summary, closed accounts, split equally/by items, multiple tenders, digital summary, supported online payments, POS/accounting export. Separate account summary from legally compliant tax invoice functionality. |
| Smart Upselling / upselling | linked products, modifiers, complementary items, meal/drink combinations, dessert suggestions, size upgrades, offers at checkout and after order, timing and priorities, availability filtering, time-of-day recommendations, acceptance and revenue tracking, A/B tests, sales-data or margin-driven suggestions, opt-in guest personalization. |
| Promotions / promotions | product discounts, daily offers, bundles, buy-one-get-one, promo codes, happy hour, schedules, dates, minimum spend, time/branch conditions, promotion analytics, customer win-back, birthday offers and vouchers where supported. |
| Analytics / analytics | total recorded sales, order count, daily/monthly reports, best sellers, cancellations, average bill, filters, PDF/Excel, period comparisons, peak hours, slow products, table utilization and turnover, QR adoption, prep time, waiter response, upsell and promo revenue, estimated ROI, scheduled reports, branch comparisons, profitability with costs, retention, forecasting and custom reports. |
| Customer Experience & CRM / customer-experience | service/product ratings, feedback, complaints, satisfaction, optional guest accounts, customer profiles, visits, favorites, history, loyalty points, rewards, VIP, segments, repeat-visit incentives, consent-based messaging. |
| Reservations / reservations | manual/online bookings, guests, date/time, table assignment, confirmation, cancellation, waitlist, table-ready notification and reservation analytics. |
| Staff & Permissions / team | owner, manager, cashier, waiter, kitchen roles; separate activation/deactivation; audit logs; cancel/discount permissions; custom roles; login sessions; owner MFA where implemented; shifts, performance and per-branch access. |
| Multi-Branch / multi-branch | central dashboard, separate branch menus/prices, tables, team roles, kitchens, live orders, centralized reporting, location filters, branch performance and local settings. |
| Notifications / notifications | new orders, ready orders, waiter/bill requests, connection status, subscription expiry, delayed preparation and service, sales trend alerts, daily summary, custom management, WhatsApp/SMS if integrated and consented. |
| Integrations / integrations | actual supported POS, payment, inventory, accounting integrations; external REST API and webhooks only when available; safe onboarding and integration status. |
| Security & Reliability / security | tenant isolation, role-based authorization, secure HTTPS, backup, idempotency, secure table sessions, connectivity indicator, local pending requests and safe retries where implemented, operational fallback, audit/security logging and updates. Core safety is never a paid-only feature. |
| Setup & Support / support | onboarding, training, help center, tutorials, contact, plan-based service levels, account manager and integration help only as approved. |

### Major feature detail page template

For each major category above create a distinct page or existing equivalent route, such as `/features/digital-menu`, `/features/qr-ordering`, `/features/kitchen-display`, `/features/upselling` and `/features/analytics`.

Required structure: breadcrumb; category-specific headline; value proposition + relevant CTA; unique high-quality product preview; 3–6 key benefits; interactive example where useful; comprehensive grouped subfeatures; how it works; relevant roles and use cases; plan availability linked to real entitlements; related features; FAQ; final CTA. Alternate light and dark product sections only when motivated. Give each page bespoke visuals and original copy, not copy-pasted card layouts. Support metadata, canonical URLs, internal links and Arabic/English equivalents. Add a feature navigation component, not a different header for every page.

## 09 — Remaining public pages (complete redesign, not optional)

- **Pricing `/pricing`**: clear 3-plan cards, detailed grouped comparison with sticky headers, mobile-friendly comparison, available/planned indicators if needed, monthly/yearly only if configured, plan limits, add-ons/fees only when verified, FAQ and choose/demo CTA. Source prices and entitlements from one config/API. Recommended proposal: Starter 1 branch, 20 tables, 150 products, 5 users, 1 KDS station; Growth 1 branch, 60 tables, 500 products, 20 users, 4 stations; Business multi-branch/negotiated limits. These are proposed, not confirmed. Never change existing commercial contracts without explicit approval.
- **Solutions `/solutions`** + detail pages for cafes, restaurants, chains, owners, waiters and kitchen teams as appropriate: show daily workflow pain points, matching features, original previews, example scenario and demo CTA.
- **How it works `/how-it-works`**: illustrated complete flow from signup/setup to QR, table session, order, kitchen, service and receipt/bill; explain connection fallback honestly.
- **Demo `/demo`**: full accessible sandbox with menu/guest/kitchen/waiter/dashboard tabs, realistic coffee shop demo data, reset, onboarding hint, no live writes; link to production demo when explicitly enabled.
- **About `/about`**: real mission, local market focus, brand story, values, contact; no fictional staff photos/history.
- **Contact/Request Demo**: validated accessible form, name, venue, contact and optional message, loading/success/failure states. Use real backend if available; if not, document limitation and use approved working alternative; never fake a success message.
- **Resources/Blog/Help Center/FAQ**: searchable resources if existing data permits, article/detail templates, clear categories, real tutorial or setup information, empty states when none. Avoid generating fake case studies or testimonials. Provide content-ready templates instead.
- **Legal pages**: privacy, terms, cookies where needed. Preserve approved legal copy; mark unapproved drafts as such and do not present them as legal advice or final terms.
- **Authentication**: login, signup, verification, forgot/reset password, error states, invalid/expired sessions, show/hide password and appropriate autocomplete. Follow current auth APIs. Do not introduce insecure demo passwords or bypass authentication.
- **Onboarding**: venue profile → branch → menu/import → tables/QR → staff/stations → test order → launch; show progress, back/resume, validation and real persistence using existing APIs. Only add working steps; mark backend gaps.
- **404/403/500/maintenance/offline**: branded, useful action, recovery navigation; no raw stack traces.
- Include every additional existing route discovered in the audit even if not listed here. Do not remove legitimate pages or workflows.

## 10 — Authenticated product experiences

Use a shared app shell and design system, but adapt density, interactions and layout to each role. Marketing motion should not appear in time-critical operational screens. All lists need pagination or virtualization where appropriate, filtering, loading/empty/error states, permission-aware actions, forms with meaningful feedback and destructive confirmations. Ensure exported reports, print views, QR outputs and localized number formats remain functional.

**Owner Dashboard**: responsive RTL sidebar, branch switcher where allowed, date controls, useful KPI cards, trend charts with accessible summaries; sales/orders/average bill, best sellers, table activity, alerts and actionable quick links. Redesign menu, categories, products, modifiers, pricing, import flows, tables, sessions, QR printing, orders, KDS setup, staff and roles, offers, recommendations, analytics, feedback, integrations, subscriptions, settings and all existing modules. Provide deep links from chart/summary to relevant details and avoid misleading chart axes. Show actual backend data, no fake trends.

**Guest Menu**: mobile-first, fast image loading, merchant-specific theme, table identity, sticky category navigation, search, product cards, item detail sheet, modifiers, allergies when data exists, clear prices, sticky cart with quantity/total, checkout review, explicit submit/confirm, status tracking, additional orders, call waiter, request bill. No mandatory customer account for browsing. Handle unavailable items, closed sessions, expired QR, pending connection, failed request, duplicate retries, sold-out products and price changes gracefully. Only server confirmation can mean received; never silently submit stale orders to a new session.

**KDS**: landscape tablet/desktop and full-screen, dark or high-contrast option; readable order ticket columns and timers; New / Accepted / Preparing / Ready, with visible delayed/canceled/served context as appropriate. Use labels + icons + semantic colors, per-item and whole-order actions if supported, station filters, audible/visual alerts, mute control and connection status. Prevent accidental taps, duplicate transitions and loss of updates on reconnect. Avoid animations that slow preparation.

**Waiter Interface**: prioritize quick one-hand usage; table grid/floor plan where available, occupied/free/requested bill/service-needed labels, incoming requests, acknowledge, current session, add orders, ready notifications, mark served, transfer/close with authorized roles. Show clear service priorities and offline state. Avoid overfilled dashboards.

**Cashier**: active sessions, detailed items and totals, authorized adjustments, taxes/service charge, cash/card payment recording, split/partial payments where supported, printable summary, closing confirmation, history and exports. Do not invent successful payments or duplicate settlements. Preserve fiscal and POS logic. Never store raw credit card details in the UI.

**Super Admin**: platform overview (verified MRR/ARR, active tenants, churn if derivable), tenant/branch management, subscriptions/plans/entitlements, trials, billing history, leads/demo requests, support, platform settings, content management, audit/security monitoring and backups where supported. Scope privileged actions explicitly, verify server authorization, avoid cross-tenant leaks, use confirmation for destructive actions, no unsafe impersonation shortcuts.

## 11 — Animation and interaction rules

Build purposeful motion using an appropriate existing animation library. Primary examples: hero entrance with subtle stagger, tab transition, order-flow visual, section entrances, card hover (2–6px), small button feedback, accordion transition, graph entry and feature demo transitions. Usual timings 150–300ms for controls, 300–600ms for sections, longer only for a user-controlled explanatory story. Favor opacity/transform, avoid costly continuous animations, scroll hijacking and layout shifts.

Every interactive component must respond correctly to mouse, touch and keyboard. Honor `prefers-reduced-motion`; stop continuous effects and show static content. No autoplay audio; KDS sounds require user activation where browser policy requires it. No required hover-only functionality. Ensure smooth but native-friendly scrolling, visible sticky headers that don't hide anchors, and no interference with screen readers or gestures.

## 12 — Responsive, RTL, accessibility, SEO and performance

- Design and verify widths 360, 390, 768, 1024, 1440, 1920px (or supported devices). No horizontal page overflow, clipped mixed-language strings, inaccessible sticky buttons or broken charts. Large tables may scroll inside a clearly designated container. Test Arabic RTL and English LTR on the actual browser, not just translation files.
- Use `lang`, `dir`, CSS logical properties, localized labels and bidi isolation where appropriate. Mirror directional navigation icons, not logos, charts or nondirectional symbols blindly. No hardcoded lorem ipsum or untranslated labels.
- Target WCAG 2.2 AA accessibility: text contrast, keyboard, visible focus, correct semantic landmarks and headings, labels, accessible dialogs/tabs, descriptions, announcements for live order state, reduced motion, accessible data alternatives and alt text. Validate actual color combinations rather than assuming palette compliance.
- Public SEO: unique title/description per language/page, semantic headings, canonical and hreflang where appropriate, sitemap/robots, Open Graph images with original branding, truthful structured data (Organization/SoftwareApplication as applicable; no fabricated aggregate ratings). Keep authenticated pages out of public indexing.
- Performance: optimize images with WebP/AVIF and responsive sizes, reserve dimensions, lazy-load below fold, avoid lazy-loading LCP image, optimize fonts, code-split demos and charts, server-render/static-render marketing content where compatible, avoid client-only application shell for all public content. Minimize JS and unused plugins; avoid oversized video assets.
- Aim for mobile Lighthouse performance >= 90, accessibility >= 95, SEO >= 95 and Core Web Vitals targets LCP <= 2.5s, INP <= 200ms, CLS <= 0.1 under appropriate measured conditions. These are goals, not claims. Record actual test environment and results; if unavailable, say so.

## 13 — Content, localization and assets

Arabic marketing copy: friendly Egyptian Arabic, direct and professional. Operational text: simple standard Arabic, consistent labels and terminology. English copy: concise, natural hospitality SaaS language; translate meaning rather than mechanically. Keep the approved name Serveza and tagline Smart Hospitality. Seamless Service. Avoid «نمو مضمون»، «ضاعف أرباحك» or promises to replace staff. Use actual feature availability and verified performance data. All sample metrics and fictional venue names must carry an unobtrusive Demo label.

Use original product UI screenshots or render components in a controlled demo environment. Keep assets organized (e.g. `assets/brand`, `assets/marketing`, `assets/demo` or framework equivalent). Use licensed, documented photos of cafes, drinks and hospitality with warm window light and restrained navy/teal accents. Use optimized SVG illustrations for product flow, not random icons or generated fake dashboards. Generated/illustrative QR codes must be labeled non-functional; production QR must resolve to the correct table and be tested before printing.

## 14 — Development phases and mandatory quality gates

Phase 0: audit, run baseline and create inventory; confirm existing business logic. Phase 1: tokens, typography, foundations, app/public shell, responsive and localization. Phase 2: homepage and real navigational framework. Phase 3: complete Features hub, category detail pages, catalog data and search/filter. Phase 4: pricing, solutions, demo, about, contact, auth/onboarding, help/legal/error pages and other public routes. Phase 5: redesign all existing operational interfaces (owner, guest, waiter, KDS, cashier, super admin). Phase 6: polish motion, SEO, performance, accessibility, security and deployment readiness. Do not mark a phase done until its applicable checks are run and outcomes recorded. Continue through phases without waiting for cosmetic approval unless a real blocker requires a decision; minimize unrelated changes.

For each phase, perform: inspect existing components → implement → run project checks → visually inspect real screenshots at required breakpoints and in both languages → fix regressions → record results. Use available Playwright/browser tooling and screenshot traces; compare before/after. If browser testing isn't available, explicitly state that visual verification was not performed.

Add or adapt automated tests for:
- all public links and pages, feature search/filter, category links, language switch, mobile/desktop menu, demo reset and full order sequence, pricing toggles, forms and error states;
- guest cart/modifiers, table sessions, real-time delivery/reconnect, duplicate protection, permissions, waiter call, KDS status, cashier and branch filters where supported;
- unauthorized access and cross-tenant isolation (API-level where possible), safe route guards, no production writes from demo;
- accessibility smoke checks (e.g. axe if compatible), keyboard navigation, reduced motion, mobile layouts and no unintended overflow;
- existing backend and frontend tests to prevent regressions. Do not bypass assertions or disable tests to make builds green.

Run actual project lint, format check, type check, unit tests, integration/E2E tests and production build commands using the repository's package manager; do not assume commands exist. Capture route coverage, screenshots and report real pass/fail. Check the console/network for unexpected errors, broken assets, dead buttons and API failures. Fix issues or record reproduction and severity.

## 15 — Definition of done and final report

The redesign is complete only when: every existing route is accounted for; the full public site and feature catalog are accessible and navigable; major feature pages are implemented; real existing functions keep working; planned features are labeled honestly; visual components share design tokens; both languages work on desktop and mobile; interactive demos function safely; permissions and subscription gates remain enforced; and actual test outcomes are recorded.

Save a final report in `docs/REDESIGN_REPORT.md` with: changes by route, new and reused components, new dependencies with reasons, before/after screenshot locations, real build/test/Lighthouse results and environment, accessibility and RTL findings, feature availability inventory, incomplete items/blockers, unsupported backend functions, assumptions, known limitations and recommended next steps. Include a concise final summary in the agent response. Do not claim completion, visual parity or performance scores that were not verified.

**Start now by inspecting the repository, running the current application, and creating the audit. Then implement the redesign end-to-end in phases. Do not return only a plan. Preserve working product logic while making Serveza feel like a premium original hospitality SaaS.**
