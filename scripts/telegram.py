"""Public Telegram channel preview collection and normalization."""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import aiohttp
from bs4 import BeautifulSoup, Tag

from scripts.common import fetch_telegram_page as default_fetch_telegram_page, iso_now

FetchPage = Callable[..., Awaitable[str]]
STYLE_URL = re.compile(r"url\(['\"]?(.*?)['\"]?\)", re.IGNORECASE)


def normalize_channel_username(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?", "", value, flags=re.I)
    value = value.removeprefix("@").strip("/")
    if not value or "/" in value or "?" in value or "#" in value:
        raise ValueError(f"Invalid public Telegram channel reference: {value!r}")
    return value


def _absolute_url(value: str | None, base: str) -> str | None:
    if not value:
        return None
    result = urljoin(base, value.strip())
    parts = urlsplit(result)
    return result if parts.scheme in {"http", "https"} and parts.netloc else None


def _text(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _background_url(node: Tag | None, base: str) -> str | None:
    if not node:
        return None
    match = STYLE_URL.search(node.get("style", ""))
    return _absolute_url(match.group(1), base) if match else None


def _media_for_message(message: Tag, base: str, post_url: str) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []

    def add(kind: str, node: Tag, direct: str | None = None, preview: str | None = None,
            title: str | None = None) -> None:
        item = {
            "type": kind,
            "url": _absolute_url(direct, base),
            "previewUrl": _absolute_url(preview, base),
            "title": title,
            "postUrl": post_url,
        }
        if item not in media:
            media.append(item)

    for node in message.select(".tgme_widget_message_photo_wrap"):
        add("photo", node, preview=_background_url(node, base))

    for node in message.select(".tgme_widget_message_video_player, .tgme_widget_message_video_wrap"):
        video = node if node.name == "video" else node.select_one("video")
        direct = (video.get("src") or
                  (video.select_one("source").get("src") if video and video.select_one("source") else None)
                  if video else None)
        direct = direct or node.get("data-src") or node.get("data-video")
        preview = _background_url(node, base) or (video.get("poster") if video else None)
        add("video", node, direct=direct, preview=preview,
            title=node.get("aria-label"))

    for selector in (".tgme_widget_message_voice", ".tgme_widget_message_audio"):
        for node in message.select(selector):
            audio = node.select_one("audio")
            direct = audio.get("src") if audio else node.get("data-src")
            title_node = node.select_one(".title, .filename, .performer")
            add("audio", node, direct=direct, title=_text(title_node) or None)

    for node in message.select(".tgme_widget_message_document_wrap, .tgme_widget_message_document"):
        link = node.select_one("a[href]")
        title_node = node.select_one(".filename, .title")
        add("document", node, direct=link.get("href") if link else None,
            title=_text(title_node) or (link.get("title") if link else None))

    if not media:
        for link in message.select("a[href]"):
            href = link.get("href", "")
            path = urlsplit(href).path.lower()
            suffix = path.rsplit(".", 1)[-1] if "." in path else ""
            kind = "audio" if suffix in {"mp3", "m4a", "ogg", "wav"} else (
                "video" if suffix in {"mp4", "m3u8", "webm", "mov"} else None
            )
            if kind:
                add(kind, link, direct=href, title=_text(link) or None)
    return media


def parse_channel_page(html: str, username: str, base_url: str | None = None) -> dict[str, Any]:
    username = normalize_channel_username(username)
    base = base_url or f"https://t.me/s/{username}"
    soup = BeautifulSoup(html, "html.parser")
    title = _text(soup.select_one(".tgme_channel_info_header_title")) or username
    handle = _text(soup.select_one(".tgme_channel_info_header_username")) or f"@{username}"
    description = _text(soup.select_one(".tgme_channel_info_description"))
    subscribers = (_text(soup.select_one(".tgme_channel_info_counter .counter_value"))
                   or _text(soup.select_one(".tgme_channel_info_counter")) or None)
    avatar_node = soup.select_one(".tgme_page_photo_image")
    avatar = _absolute_url(avatar_node.get("src"), base) if avatar_node else None

    posts: dict[str, dict[str, Any]] = {}
    for wrapper in soup.select(".tgme_widget_message_wrap"):
        message = wrapper.select_one(".tgme_widget_message") or wrapper
        post_ref = message.get("data-post") or wrapper.get("data-post")
        date_anchor = message.select_one(".tgme_widget_message_date[href]")
        if not post_ref and date_anchor:
            post_ref = date_anchor.get("href", "")
        post_id = str(post_ref or "").rstrip("/").split("/")[-1]
        if not post_id.isdigit():
            continue
        post_url = f"https://t.me/{username}/{post_id}"
        text_node = message.select_one(".tgme_widget_message_text")
        date_node = message.select_one(".tgme_widget_message_date time[datetime]")
        views_node = message.select_one(".tgme_widget_message_views")
        posts[post_id] = {
            "postId": post_id,
            "postUrl": post_url,
            "publishedAt": date_node.get("datetime") if date_node else None,
            "text": _text(text_node),
            "textHtml": str(text_node) if text_node else "",
            "views": _text(views_node) or None,
            "media": _media_for_message(message, base, post_url),
        }

    ordered = sorted(posts.values(), key=lambda row: int(row["postId"]), reverse=True)
    ids = [int(row["postId"]) for row in ordered]
    return {
        "username": username,
        "title": title,
        "handle": handle,
        "description": description,
        "subscribers": subscribers,
        "avatarUrl": avatar,
        "pageUrl": base,
        "posts": ordered,
        "oldestPostId": str(min(ids)) if ids else None,
        "newestPostId": str(max(ids)) if ids else None,
        "fetchedAt": iso_now(),
    }


async def collect_telegram(
    configured_channels: list[dict[str, Any]],
    session: aiohttp.ClientSession,
    fetch_page: FetchPage = default_fetch_telegram_page,
) -> dict[str, Any]:
    channels: list[dict[str, Any]] = []
    documents: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    semaphore = asyncio.Semaphore(6)

    async def collect_one(config: dict[str, Any]):
        raw = str(config.get("username", "")).strip()
        try:
            username = normalize_channel_username(raw)
        except Exception as error:
            return None, None, {"source": raw or "<missing-username>", "message": str(error)}
        try:
            max_pages = max(1, int(config.get("maxPages", 2)))
        except (TypeError, ValueError):
            max_pages = 2
        try:
            url = f"https://t.me/s/{username}"
            posts: dict[str, dict[str, Any]] = {}
            header: dict[str, Any] | None = None
            before: str | None = None
            async with semaphore:
                for page_index in range(max_pages):
                    page_url = url if before is None else f"{url}?before={before}"
                    html = await fetch_page(session, page_url, more=before is not None)
                    parsed = parse_channel_page(html, username, page_url)
                    if header is None:
                        header = parsed
                    for post in parsed["posts"]:
                        posts.setdefault(post["postId"], post)
                    oldest = parsed["oldestPostId"]
                    if not oldest or (before is not None and int(oldest) >= int(before)):
                        break
                    before = oldest
                    if page_index + 1 >= max_pages:
                        break
            header = header or {"title": username, "handle": f"@{username}", "description": "",
                                "subscribers": None, "avatarUrl": None}
            ordered = sorted(posts.values(), key=lambda row: int(row["postId"]), reverse=True)
            doc = {
                **header,
                "username": username,
                "posts": ordered,
                "postCount": len(ordered),
                "fetchedAt": iso_now(),
            }
            row = {
                "username": username,
                "title": doc.get("title") or username,
                "handle": doc.get("handle") or f"@{username}",
                "description": doc.get("description") or "",
                "subscribers": doc.get("subscribers"),
                "avatarUrl": doc.get("avatarUrl"),
                "postCount": len(ordered),
                "newestPostId": ordered[0]["postId"] if ordered else None,
                "oldestPostId": ordered[-1]["postId"] if ordered else None,
                "contentPath": f"channels/{username}.json",
                "fetchedAt": doc["fetchedAt"],
                "collectionStatus": "complete",
            }
            return row, doc, None
        except Exception as error:
            doc = {
                "username": username, "title": str(config.get("title") or username),
                "handle": f"@{username}", "description": "", "posts": [],
                "postCount": 0, "fetchedAt": iso_now(), "collectionStatus": "error",
                "error": str(error),
            }
            row = {
                "username": username, "title": doc["title"], "handle": doc["handle"],
                "description": "", "postCount": 0, "contentPath": f"channels/{username}.json",
                "fetchedAt": doc["fetchedAt"], "collectionStatus": "error",
                "error": str(error),
            }
            return row, doc, {"source": username, "message": str(error)}

    results = await asyncio.gather(*(collect_one(row) for row in configured_channels))
    for row, document, warning in results:
        if row:
            channels.append(row)
            documents[row["username"]] = document
        if warning:
            warnings.append(warning)
    return {
        "channels": channels,
        "channelDocuments": documents,
        "warnings": warnings,
        "generatedAt": iso_now(),
    }
