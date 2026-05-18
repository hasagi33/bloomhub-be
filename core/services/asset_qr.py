from io import BytesIO
from urllib.parse import urljoin

import qrcode
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils.text import get_valid_filename


def build_asset_qr_payload(asset) -> str:
    base_url = (
        getattr(settings, "FRONTEND_URL", "") or getattr(settings, "SITE_URL", "")
    ).rstrip("/")
    return urljoin(f"{base_url}/", f"assets/{asset.pk}")


def build_asset_qr_image_path(asset) -> str:
    filename = get_valid_filename(f"Asset-{asset.name}-{asset.pk}-QR.png")
    return f"asset_qr_codes/{asset.pk}/{filename}"


def generate_qr_png_bytes(payload: str) -> bytes:
    image = qrcode.make(payload)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def ensure_asset_qr_code(asset, *, regenerate_image: bool = False):
    payload = build_asset_qr_payload(asset)
    image_path = build_asset_qr_image_path(asset)
    old_image_path = asset.qr_code_image.name
    image_missing = bool(old_image_path) and not asset.qr_code_image.storage.exists(
        old_image_path
    )
    payload_changed = asset.qr_code_payload != payload
    image_path_changed = old_image_path != image_path
    should_save_image = (
        regenerate_image
        or payload_changed
        or image_path_changed
        or not asset.qr_code_image
        or image_missing
    )

    update_fields = []
    if payload_changed:
        asset.qr_code_payload = payload
        update_fields.append("qr_code_payload")

    if should_save_image:
        png_bytes = generate_qr_png_bytes(payload)
        if asset.qr_code_image.storage.exists(image_path):
            asset.qr_code_image.storage.delete(image_path)

        asset.qr_code_image.save(
            image_path,
            ContentFile(png_bytes),
            save=False,
        )
        update_fields.append("qr_code_image")
        if (
            old_image_path
            and old_image_path != asset.qr_code_image.name
            and asset.qr_code_image.storage.exists(old_image_path)
        ):
            asset.qr_code_image.storage.delete(old_image_path)

    if update_fields:
        asset.save(update_fields=update_fields)

    return asset
