"""Convert uploaded menu images and logos to fresh raster files with generated names."""
from .validation import localized
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile


def prepare_menu_image(upload, *, max_dimension=None):
    if not upload:
        return None
    if upload.size > 3_000_000:
        raise ValidationError('الصورة يجب أن تكون JPG أو PNG أو WebP وأقل من 3 ميجابايت.')
    try:
        with Image.open(upload) as source:
            if source.format not in ('JPEG', 'PNG', 'WEBP') or not (0 < source.width <= 5000 and 0 < source.height <= 5000):
                raise ValidationError('صيغة الصورة أو أبعادها غير مدعومة.')
            source.verify()
        upload.seek(0)
        with Image.open(upload) as source:
            # Rebuild pixels instead of copying EXIF, comments, trailing data or filename.
            picture = ImageOps.exif_transpose(source).convert('RGBA' if source.mode in ('RGBA', 'LA', 'P') else 'RGB')
            if max_dimension:
                picture.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            clean = Image.new(picture.mode, picture.size)
            clean.paste(picture)
            output = BytesIO()
            clean.save(output, format='PNG', optimize=True)
        if output.tell() > 6_000_000:
            raise ValidationError(localized('الصورة بعد المعالجة أكبر من الحد المسموح. استخدم صورة أصغر.', 'The processed image exceeds the size limit. Choose a smaller image.'))
        return ContentFile(output.getvalue(), name=f'{uuid4().hex}.png')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError('ملف الصورة غير صالح.')
    finally:
        upload.seek(0)
