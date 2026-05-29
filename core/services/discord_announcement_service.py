from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.text import Truncator

from core.models import (
    Announcement,
    DiscordAnnouncementChannel,
    DiscordAnnouncementDelivery,
)

logger = logging.getLogger(__name__)

DISCORD_TIMEOUT_SECONDS = 10
DISCORD_DESCRIPTION_LIMIT = 500


def dispatch_discord_announcement(announcement: Announcement) -> dict[str, int]:
    if not announcement.type:
        logger.info(
            "Announcement %s Discord delivery skipped: announcement type is blank",
            announcement.pk,
        )
        return {"queued": 0, "sent": 0, "failed": 0, "skipped": 1}

    channels = list(
        DiscordAnnouncementChannel.objects.filter(
            announcement_type=announcement.type,
            enabled=True,
        )
    )
    if not channels:
        logger.info(
            "Announcement %s Discord delivery skipped: no enabled channel for type %s",
            announcement.pk,
            announcement.type,
        )
        return {"queued": 0, "sent": 0, "failed": 0, "skipped": 1}

    result = {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
    for channel in channels:
        delivery, created = DiscordAnnouncementDelivery.objects.get_or_create(
            announcement=announcement,
            discord_channel=channel,
        )
        if created:
            result["queued"] += 1
        if delivery.status == DiscordAnnouncementDelivery.Status.SENT:
            result["skipped"] += 1
            continue
        if send_discord_delivery(delivery):
            result["sent"] += 1
        else:
            result["failed"] += 1
    return result


def send_discord_delivery(delivery: DiscordAnnouncementDelivery) -> bool:
    if delivery.status == DiscordAnnouncementDelivery.Status.SENT:
        return True

    delivery.attempt_count += 1
    delivery.last_attempt_at = timezone.now()
    delivery.status = DiscordAnnouncementDelivery.Status.PENDING
    delivery.last_error = ""
    delivery.save(
        update_fields=[
            "attempt_count",
            "last_attempt_at",
            "status",
            "last_error",
            "updated_at",
        ]
    )

    try:
        response = requests.post(
            delivery.discord_channel.get_webhook_url(),
            params={"wait": "true"},
            json=_discord_payload(delivery.announcement),
            timeout=DISCORD_TIMEOUT_SECONDS,
        )
        if not 200 <= response.status_code < 300:
            raise DiscordWebhookError(
                f"Discord webhook returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        message_id = ""
        if response.content:
            try:
                data = response.json()
                message_id = str(data.get("id") or "")
            except ValueError:
                message_id = ""

        delivery.status = DiscordAnnouncementDelivery.Status.SENT
        delivery.discord_message_id = message_id
        delivery.sent_at = timezone.now()
        delivery.last_error = ""
        delivery.save(
            update_fields=[
                "status",
                "discord_message_id",
                "sent_at",
                "last_error",
                "updated_at",
            ]
        )
        return True
    except Exception as exc:
        delivery.status = DiscordAnnouncementDelivery.Status.FAILED
        delivery.last_error = str(exc)[:2000]
        delivery.save(update_fields=["status", "last_error", "updated_at"])
        logger.exception(
            "Announcement %s Discord delivery failed for channel %s",
            delivery.announcement_id,
            delivery.discord_channel_id,
        )
        return False


def _discord_payload(announcement: Announcement) -> dict[str, Any]:
    embed = {
        "title": announcement.title,
        "description": _body_preview(announcement.body),
        "fields": [
            {
                "name": "Type",
                "value": (
                    announcement.get_type_display() if announcement.type else "General"
                ),
                "inline": True,
            },
            {
                "name": "Author",
                "value": _author_name(announcement),
                "inline": True,
            },
            {
                "name": "Published",
                "value": timezone.localtime(announcement.published_at).strftime(
                    "%Y-%m-%d %H:%M %Z"
                ),
                "inline": True,
            },
        ],
    }
    url = _announcement_url(announcement)
    if url:
        embed["url"] = url
    return {"embeds": [embed]}


def _body_preview(body: str) -> str:
    markdown = _html_to_discord_markdown(body or "")
    return Truncator(markdown).chars(DISCORD_DESCRIPTION_LIMIT, truncate="...") or " "


def _html_to_discord_markdown(value: str) -> str:
    parser = _DiscordMarkdownParser()
    parser.feed(value)
    parser.close()
    return _normalize_markdown(parser.markdown)


def _normalize_markdown(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _DiscordMarkdownParser(HTMLParser):
    heading_tags = {
        "h1": "#",
        "h2": "##",
        "h3": "###",
        "h4": "###",
        "h5": "###",
        "h6": "###",
    }
    block_tags = {
        "address",
        "article",
        "aside",
        "blockquote",
        "div",
        "footer",
        "header",
        "main",
        "p",
        "pre",
        "section",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
    }
    markdown_tags = {
        "strong": "**",
        "b": "**",
        "em": "*",
        "i": "*",
        "u": "__",
        "s": "~~",
        "strike": "~~",
        "del": "~~",
        "code": "`",
    }

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.link_stack: list[str] = []

    @property
    def markdown(self) -> str:
        return "".join(self.parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.heading_tags:
            self._ensure_block_gap()
            self._append(f"{self.heading_tags[tag]} ")
        elif tag in self.block_tags:
            self._ensure_block_gap()
        elif tag == "br":
            self._append("\n")
        elif tag == "li":
            self._ensure_newline()
            self._append("- ")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self.link_stack.append(href)
            if href:
                self._append("[")
        elif tag in self.markdown_tags:
            self._append(self.markdown_tags[tag])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.markdown_tags:
            self._append(self.markdown_tags[tag])
        elif tag == "a":
            href = self.link_stack.pop() if self.link_stack else ""
            if href:
                self._append(f"]({href})")
        elif tag == "li":
            self._ensure_newline()
        elif tag in self.heading_tags:
            self._ensure_block_gap()
        elif tag in self.block_tags:
            self._ensure_block_gap()

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", unescape(data))
        if text:
            self._append(text)

    def handle_entityref(self, name: str) -> None:
        self.handle_data(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.handle_data(f"&#{name};")

    def _append(self, value: str) -> None:
        self.parts.append(value)

    def _ensure_newline(self) -> None:
        if self.parts and not self.markdown.endswith("\n"):
            self._append("\n")

    def _ensure_block_gap(self) -> None:
        if not self.parts:
            return
        if self.markdown.endswith("\n\n"):
            return
        if self.markdown.endswith("\n"):
            self._append("\n")
        else:
            self._append("\n\n")


def _author_name(announcement: Announcement) -> str:
    author = announcement.author
    if author is None:
        return "BloomHub"
    return author.full_name or author.user.get_full_name() or author.user.username


def _announcement_url(announcement: Announcement) -> str:
    frontend_url = (getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    if not frontend_url:
        return ""
    return f"{frontend_url}/announcements/{announcement.pk}"


class DiscordWebhookError(Exception):
    pass
