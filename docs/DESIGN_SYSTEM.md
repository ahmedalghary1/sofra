# Serveza design system

The public site tells the story of a table in service. The workspace prioritizes reading an order and taking its next permitted action. Both use the official Serveza mark and local Cairo variable font.

## Foundations

| Element | Implementation |
| --- | --- |
| Brand | Navy `#0F2A3D`, teal `#4BAA9C`, strong teal `#2C7A6F`. The white logo lockup comes from the supplied art. Use an approved variant when the background changes. |
| Surfaces | Canvas `#F6F8FA`, white, dark canvas `#0A1E2D`, dark surface `#173448`. The marketing device illustration uses a soft green stage. |
| Text and boundaries | Secondary text `#4A5568`, border `#E2E8F0`, visible keyboard focus. Status controls have readable labels alongside color. |
| Typography | Cairo is served locally as WOFF2 with the original TTF fallback and OFL license. The full WOFF2 is 177,316 bytes and preserves the source character map and variable weight axis. English preloads a 76,144-byte Latin subset and falls back to full Cairo for other scripts. Use Western digits and isolate mixed-language identifiers. |
| Layout | Up to 1,256px for the public shell, 1,200px for the catalog. Logical inline spacing respects direction. A mobile navigation dialog replaces the wide menu as space narrows. |
| Motion | Short transitions on controls and a brief desktop hero entrance. Reduced motion disables animation and smooth scrolling. Operational transitions preserve actionable content. |

Measured WCAG contrast from the sRGB values is 14.80:1 for navy on white, 5.10:1 for strong teal on white and 7.07:1 for secondary text on canvas. Bright brand teal on white is 2.79:1. Reserve this pair for decoration.

## Shared parts

| Pattern | Source and use |
| --- | --- |
| Tokens, type scale, focus | `static/css/tokens.css`, `typography.css`. Scope density and component rules in marketing, catalog, showcase or operations styles. |
| Public frame | `templates/marketing/base.html`. Branded header and footer, skip link, grouped features disclosure, language switch and native mobile dialog. |
| Copy and navigation | `core/presentation.py`, `core/templatetags/showroom.py`, `core/guidebook.py`. `bi` selects supplied copy and `public_url` resolves named routes. |
| Stories and actions | `flow.html`, `cta.html`, `faq.html`, `.mk-wrap`, `.mk-story-head`, `.mk-btn`. Labeled native disclosures are usable without scripting. |
| Product illustration and demo | `hero_product.html`, `showcase.html`, `sandbox.html`, `upsell.html`, `roi.html`. Demo interfaces are rendered UI, including chart, ticket and device surfaces. All fictional figures are labeled. |
| Feature and solution navigation | `core/showcase_data.py`, `core/showcase_catalog.py`, `templates/marketing/catalog/`. Reuse canonical capability IDs, availability labels and plan capacity values. |
| Workspace | `app_base.html`, `_branch_activity.html`, `_setup_steps.html`, `_ops_language.html`, `operations.css`. Branch selection, role-aware navigation, accessible chart summary, guided setup and language control. |
| Guest and service | `_guest_cart.html`, `guest.js`, `kitchen.js`. Explicit checkout, cart dialog, KDS controls and connection feedback. Buttons for live work rely on the existing server transitions. |
| Forms and states | Native labels, Django field errors, a form-wide save error, persistent sent confirmation, explicit empty/filter states and branded recovery views. |

The bento photo and social image are documented in [asset credits](../static/marketing/README.md). Image-based artwork is decorative where adjacent text supplies the full link purpose.
