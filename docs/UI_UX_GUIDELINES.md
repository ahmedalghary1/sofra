# Serveza interface guidelines

## Write for the person doing the work

Use short Egyptian Arabic on discovery pages. In the workspace use consistent action verbs, state names and units. Keep the brand name Serveza. Write a separate English rendering that describes the same action. Use the venue's stored wording for dishes, branch names, receipts and order snapshots.

Public templates extend `marketing/base.html` and use the active request language. Existing staff and guest templates use Django gettext. Keep the English source and compiled catalog together under `core/locale/en/LC_MESSAGES/`. Test a switch from a filtered page, a direct English link, an Arabic session and a new browser opening an email link.

## Design a complete task

Use the spacing and line-height variables in `static/css/typography.css` for stacked text. At the default text size, leave 6 px between an item name and its caption, 8 px between a panel title and its description, 12 px after a page title, and 16 px between form fields. The Arabic line-height values give wrapped Cairo text room for ascenders and diacritics. Check title–description pairs with a long name at phone width.

- Link feature claims to a working route or to a specific capability and its status. Read plan capacity from the shared comparison code. Mark forecasts, figures in demos and proposed terms at their point of use.
- A form needs visible labels, validation, a pending state for submissions that take time and a way to recover. A saved confirmation must follow a confirmed server write. Preserve the visitor's entered values on validation or storage failure.
- In the catalog, GET filters make a result shareable. An empty search should explain how to clear it. Use meaningful article and capability fragments.
- Keep desktop disclosures operable with Enter and Escape. Preserve focus when a dialog closes. Use semantic headings, visible focus, logical properties, reduced motion and scrollable regions for wide tables. Pair visual charts with values or summaries.
- Keep the venue, branch and table visible where a user can change operational data. Show a label on every status. Place irreversible or consequential actions near their specific record.
- Only confirm a guest order after the service accepts it. Keep the same retry key for the same pending submission and retain server-calculated Decimal totals. Let the user review a changed total. Surface network and session errors directly at checkout.
- Keep demo interactions in their dedicated sandbox. Even a staff test on a real merchant table creates an operational record.

## Verify changes

Review Arabic and English at 360, 390, 768, 1024, 1440 and 1920 CSS pixels. Use a keyboard, touch-sized viewport, reduced motion and a local test venue. Check long names, empty tables, form errors and mobile navigation. Export only fixture images when recording evidence. Run the repository tests, the relevant browser scenarios under `scripts/` and the production static collection. Update [the feature matrix](FEATURES_MATRIX.md) when capabilities change.
