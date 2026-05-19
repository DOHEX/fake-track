from datetime import datetime
from pathlib import Path


def mask_phone(phone: str) -> str:
    text = phone.strip()
    if len(text) <= 4:
        return text
    return f"****{text[-4:]}"


def default_track_image_path(prefix: str = "track-overlay") -> Path:
    stamp = datetime.now().strftime(f"{prefix}-%Y%m%d-%H%M%S.png")
    return Path(".local") / "debug-images" / stamp
