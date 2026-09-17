from __future__ import annotations

import glob
import json
from datetime import datetime
from pathlib import Path

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
VIDEO_EXTS = {"mp4", "mov", "m4v"}
GIF_EXTS = {"gif"}


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def load_posts(data_dir: Path) -> dict[int, dict]:
    posts_dir = data_dir / "posts"
    posts: dict[int, dict] = {}
    if not posts_dir.is_dir():
        return posts
    for path in posts_dir.glob("*.json"):
        try:
            post = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        tweet_id = post.get("tweet_id")
        if tweet_id is None:
            continue
        posts[int(tweet_id)] = post
    return posts


def load_deleted(data_dir: Path) -> set[int]:
    path = data_dir / "deleted.json"
    if not path.is_file():
        return set()
    try:
        return {int(x) for x in json.loads(path.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, OSError):
        return set()


def _media_kind(ext: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in GIF_EXTS:
        return "gif"
    return "other"


def attach_media(posts: dict[int, dict], media_dir: Path) -> None:
    for tweet_id, post in posts.items():
        pattern = str(media_dir / f"{tweet_id}_*")
        files = sorted(glob.glob(pattern))
        media = []
        for f in files:
            name = Path(f).name
            ext = name.rsplit(".", 1)[-1] if "." in name else ""
            media.append({
                "filename": name,
                "kind": _media_kind(ext),
            })
        post["media"] = media


def _stringify_ids(node: dict) -> None:
    """XのツイートID/ユーザーIDはJS Numberの安全範囲(2^53-1)を超えるため、
    表示・URL生成時の精度落ちを避けて文字列化しておく。"""
    for key in ("tweet_id", "reply_id", "quote_id", "quoted_id", "retweet_id",
                "conversation_id", "external_reply_id", "external_quote_id"):
        if node.get(key) is not None:
            node[key] = str(node[key])
    author = node.get("author")
    if isinstance(author, dict) and author.get("id") is not None:
        author["id"] = str(author["id"])


def build_threads(posts: dict[int, dict], deleted: set[int]) -> list[dict]:
    """reply_id / quote_id を辿ってツリー構造を構築する。
    親が取得済みでない場合はフォールバック用のリンク情報のみ持たせる。"""
    nodes: dict[int, dict] = {}
    for tweet_id, post in posts.items():
        node = dict(post)
        node["tweet_id"] = tweet_id
        node["is_deleted"] = tweet_id in deleted
        node["replies"] = []
        nodes[tweet_id] = node

    roots: list[dict] = []
    for tweet_id, node in nodes.items():
        reply_id = node.get("reply_id") or 0
        if reply_id and reply_id in nodes:
            nodes[reply_id]["replies"].append(node)
        else:
            node["external_reply_id"] = reply_id or None
            roots.append(node)

    def snapshot(n: dict) -> dict:
        # 引用カード表示用の軽量コピー(スレッド・多重引用への再帰は行わない)
        return {
            "tweet_id": n["tweet_id"],
            "date": n.get("date"),
            "author": n.get("author"),
            "content": n.get("content"),
            "media": n.get("media", []),
            "is_deleted": n.get("is_deleted", False),
        }

    for node in nodes.values():
        # 注意: gallery-dlの"quote_id"は「自分を引用したツイートのID」(quoted_by)であり、
        # 「自分が引用しているツイートのID」は別フィールドの"quoted_id"。紛らわしいので注意。
        quote_id = node.get("quoted_id") or 0
        if quote_id and quote_id in nodes:
            node["quote"] = snapshot(nodes[quote_id])
            node["external_quote_id"] = None
        elif quote_id:
            node["quote"] = None
            node["external_quote_id"] = quote_id
        else:
            node["quote"] = None
            node["external_quote_id"] = None

    def sort_key(n: dict):
        d = _parse_date(n.get("date"))
        return d or datetime.min

    for node in nodes.values():
        node["replies"].sort(key=sort_key)

    roots.sort(key=sort_key)

    for node in nodes.values():
        _stringify_ids(node)
        if node.get("quote"):
            _stringify_ids(node["quote"])

    return roots


def load_all(data_dir: Path) -> list[dict]:
    posts = load_posts(data_dir)
    deleted = load_deleted(data_dir)
    attach_media(posts, data_dir / "media")
    return build_threads(posts, deleted)
