from __future__ import annotations

import base64
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import models

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _count_posts(threads: list[dict]) -> int:
    total = 0
    for node in threads:
        total += 1
        total += _count_posts(node.get("replies", []))
    return total


_FAVICON_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".ico": "image/x-icon",
}


def build(data_dir: Path, username: str) -> Path:
    threads = models.load_all(data_dir)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("index.html.j2")

    raw_json = json.dumps(threads, ensure_ascii=False, default=str)
    data_b64 = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")

    avatar_files = sorted(data_dir.glob("avatar.*"))
    favicon_filename = avatar_files[0].name if avatar_files else None
    favicon_mime = (
        _FAVICON_MIME.get(avatar_files[0].suffix.lower(), "image/jpeg")
        if avatar_files else None
    )

    html = template.render(
        username=username,
        data_b64=data_b64,
        post_count=_count_posts(threads),
        favicon_filename=favicon_filename,
        favicon_mime=favicon_mime,
    )

    data_dir.mkdir(parents=True, exist_ok=True)
    out_path = data_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
