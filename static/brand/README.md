# Serveza brand assets

Original transparent artwork extracted losslessly from the embedded images on page 6 of `Serveza_Brand_Book.pdf` supplied by the project owner. These assets match the supplied reference JPEG; they are not redrawn or generated.

- `serveza-logo.png`: primary horizontal lockup, 1435 × 419.
- `serveza-logo-reversed.png`: official lockup for navy backgrounds, 1435 × 419.
- `serveza-icon.png`: original icon mark, 413 × 412.
- `serveza-logo-stacked.png`: official stacked lockup, 1048 × 814.
- `serveza-logo-mono.png`: official monochrome lockup, 1435 × 419.
- Browser PNGs: proportionally downsampled original icon with 10% padding on the light brand background. The legacy `static/favicon.svg` embeds this same PNG.

Preserve aspect ratio and opacity, allow clear space, and do not apply CSS filters, rotation, or shadows to the artwork. Use `_brand_logo.html` / `_brand_icon.html` in product templates. UI colors are defined in `static/css/style.css`; logo pixels retain their original source colors.
