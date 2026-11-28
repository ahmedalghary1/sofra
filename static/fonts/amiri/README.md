# Amiri QR print font

Unmodified `Amiri-Regular.ttf` from the official **Amiri 1.003** release:
https://github.com/aliftype/amiri/releases/tag/1.003

License: SIL Open Font License 1.1; see the bundled `OFL.txt`.

SHA-256: `cd2550c0f4c05eb341bf97958211aaa39382bca96577ba3a67d4a3b4912c43c0`.

PNG and PDF explicitly reshape/reorder Arabic before using a basic text renderer.
This static font contains the Arabic Presentation Forms, including isolated waw,
alef and reh that were absent from the previous variable web font. SVG embeds the
same TTF and lets its renderer shape the original logical Unicode text. No font
download or system font installation is required at runtime.
