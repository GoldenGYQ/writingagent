"""Business limits for inbound WebUI messages and attachments.

The WebSocket channel owns its raw frame limit. This module owns semantic
limits inside a decoded WebUI message so transport capacity isn't mistaken
for a text or attachment policy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

MessageRejection = Literal["text_too_large"]


@dataclass(frozen=True)
class MessageIngressLimits:
    max_text_bytes: int = 64 * 1024


@dataclass(frozen=True)
class AttachmentIngressLimits:
    max_count: int = 4
    max_file_bytes: int = 6 * 1024 * 1024
    max_total_bytes: int = 24 * 1024 * 1024


@dataclass(frozen=True)
class WebUIIngressPolicy:
    """Limits applied after the channel has decoded the transport envelope."""

    message: MessageIngressLimits = field(default_factory=MessageIngressLimits)
    attachments: AttachmentIngressLimits = field(default_factory=AttachmentIngressLimits)
    # Covers JSON keys, IDs, attachment names, MIME prefixes, mentions, and
    # other non-content fields when the browser estimates whether a frame fits.
    envelope_reserve_bytes: int = 64 * 1024

    def validate_text(self, content: str) -> MessageRejection | None:
        if len(content.encode("utf-8")) > self.message.max_text_bytes:
            return "text_too_large"
        return None

    def bootstrap_limits(self, *, max_frame_bytes: int) -> dict[str, object]:
        attachments = configured_attachment_limits(self.attachments)
        return {
            "transport": {
                "max_frame_bytes": max_frame_bytes,
                "envelope_reserve_bytes": self.envelope_reserve_bytes,
            },
            "message": {"max_text_bytes": self.message.max_text_bytes},
            "attachments": {
                "max_count": attachments.max_count,
                "max_file_bytes": attachments.max_file_bytes,
                "max_total_bytes": attachments.max_total_bytes,
            },
        }

    def minimum_full_policy_frame_bytes(self) -> int:
        """Conservative frame size needed for every policy-valid message."""
        attachments = configured_attachment_limits(self.attachments)
        encoded_attachments = 4 * math.ceil(attachments.max_total_bytes / 3)
        data_url_allowance = attachments.max_count * 128
        return (
            encoded_attachments
            + data_url_allowance
            + self.message.max_text_bytes
            + self.envelope_reserve_bytes
        )


def configured_attachment_limits(
    fallback: AttachmentIngressLimits | None = None,
) -> AttachmentIngressLimits:
    """Read the current WebUI upload policy without restarting the gateway."""
    base = fallback or AttachmentIngressLimits()
    try:
        from nanobot.config.loader import load_config

        upload = load_config().tools.webui_upload
        return AttachmentIngressLimits(
            max_count=upload.max_count,
            max_file_bytes=upload.max_file_mb * 1024 * 1024,
            max_total_bytes=upload.max_total_mb * 1024 * 1024,
        )
    except Exception:
        return base


DEFAULT_WEBUI_INGRESS_POLICY = WebUIIngressPolicy()
