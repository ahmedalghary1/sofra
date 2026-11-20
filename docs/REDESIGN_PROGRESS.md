# Redesign progress

| Phase | Work and evidence |
| --- | --- |
| Baseline | Local demo fixture, clean Django check, 98 passing original tests, 24 captures before template changes. See UI_UX_AUDIT.md. |
| Foundations | Shared language preferences, tokens, Cairo WOFF2 and public/operational navigation. Browser tests cover language persistence, desktop keyboard disclosure, mobile dialog and focus restoration. |
| Homepage | RTL and LTR product story, eight sample views and safe demo controls. Playwright tested both languages at six widths. |
| Product catalog | 18 category pages, 64 capability groups, six solutions and configured plan comparison. Five Django catalog tests and 324 browser layout checks passed. |
| Public workflows | Help articles, legal drafts, recovery pages and validated lead intake. Public-route, replay and validation tests passed. Demo lifecycle, sample-menu checkout and network recovery passed in Chrome. |
| Operations | Branch metrics, KDS, table service, register, invitation, waiter/cashier and superuser workflows. The separate multi-role browser test and the serial suite of 113 core tests passed. |
| Final validation | Static collection, local production-settings smoke, keyboard and axe samples, screenshot review and mobile Lighthouse completed. Local Arabic performance and LCP on both languages missed their targets. Deployment, SMTP delivery and field metrics remain pending. [Report](REDESIGN_REPORT.md) |
