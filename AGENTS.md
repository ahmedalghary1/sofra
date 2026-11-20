# Working rules for this repository

- Read `safra_restaurant_saas_complete_spec.md` and `docs/IMPLEMENTATION_PLAN.md` before extending product scope.
- Keep Arabic user interfaces RTL and responsive. Use the shared CSS and templates.
- Scope every staff query and mutation to the authenticated membership's restaurant. Public QR tokens grant access only to their table menu and order submission.
- Recalculate money with `Decimal` on the server. Keep order item name and price snapshots, idempotency, and status history intact.
- Never claim a feature is production ready without tests and deployment verification. Update the implementation plan when a feature changes status.
- Do not commit secrets, SQLite databases, or uploaded media. Run `python manage.py check` and `python manage.py test core` after code changes.
- Keep public feature availability and plan descriptions in the shared bilingual catalog. Label illustrative pricing and browser-only demos wherever they appear.
- Verify each changed screen in Arabic RTL and English LTR. Keep merchant-entered names, prices, order snapshots and historic records intact.
