"""attendance_app.utils – Refactored
=================================================
共通ユーティリティを1カ所に集約。
- **循環 import** を回避するため、**Djangoモデルを直接 import しない**
- QR コード生成を `generate_qr_png()` に一本化（Pillow / qrcode 依存）
- タイムゾーンヘルパを `now_jst()` と `to_jst()` で統一
- 静的型チェック (PEP484) に対応
"""
from __future__ import annotations

import io
from datetime import datetime, timezone as _tz
from typing import Final

import qrcode
from PIL import Image
from django.utils import timezone

__all__: Final[list[str]] = [
    "now_jst",
    "to_jst",
    "generate_qr_png",
]

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_jst() -> datetime:
    """サーバー側で JST の現在時刻を返す (夏時間なし)"""
    return timezone.localtime(timezone.now())


def to_jst(dt: datetime) -> datetime:
    """任意の datetime を JST へ変換して返す。Aware でも naive でも受け付ける。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return timezone.localtime(dt)

# ---------------------------------------------------------------------------
# QR helpers
# ---------------------------------------------------------------------------

def generate_qr_png(token: str) -> Image.Image:
    """与えられたトークン文字列から Pillow Image を返す。
    - box_size / border はデフォルト値で統一
    - 返り値は RGB モード (透明なし)"""
    qr = qrcode.QRCode(box_size=10, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return img

# ---------------------------------------------------------------------------
# Optional: binary helper
# ---------------------------------------------------------------------------

def qr_png_bytes(token: str) -> bytes:
    """`generate_qr_png` の PNG バイナリを bytes で取得したいときに使用。"""
    img = generate_qr_png(token)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
