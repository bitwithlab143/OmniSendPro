"""Dynamic template tags (#USERID#, #RANDOM#, …) for subject, HTML and text bodies — design DS-24.

Values are derived per message with HMAC-SHA256(campaign tag seed, "<recipient id>:<tag>"):

* **deterministic per message**: the same tag has the same value in the subject and in every body part,
  and a retried send of the same recipient renders exactly the same message;
* **unpredictable**: the seed is a 32-byte secret generated per campaign and never exposed by the API, so a
  recipient cannot compute another recipient's values (#HASH# is a full HMAC digest);
* **safe**: values are HTML-escaped in HTML, stripped of CR/LF in headers; unknown ``#WORDS#`` are left
  untouched. #DATE#/#TIME# are the send time in the configured timezone.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TAG_RE = re.compile(r"#(USERID|RANDOM|EMAIL|SUBSID|INVOICE|REF|HASH|DATE|TIME|OTP|\$\$|MASSAGE|MESSAGE)#")
TAGS = ("USERID", "RANDOM", "EMAIL", "SUBSID", "INVOICE", "REF", "HASH", "DATE", "TIME", "OTP", "$$", "MASSAGE")


def has_tags(text: str | None) -> bool:
    return bool(text) and TAG_RE.search(text) is not None  # type: ignore[arg-type]


class TagValues:
    """Lazily computed, cached tag values for one message."""

    def __init__(self, seed: str | bytes, recipient_id: Any, email: str, messages: list[str] | None = None,
                 tz: str = "UTC", now: datetime | None = None) -> None:
        self._key = seed.encode() if isinstance(seed, str) else seed
        self._rid = str(recipient_id)
        self.email = email
        self.messages = [m for m in (messages or []) if m]
        self._tz = tz
        self._now = now
        self._cache: dict[str, str] = {}

    def _digest(self, tag: str) -> bytes:
        return hmac.new(self._key, f"{self._rid}:{tag}".encode(), hashlib.sha256).digest()

    def _uuid(self, tag: str) -> uuid.UUID:
        return uuid.UUID(bytes=self._digest(tag)[:16], version=4)

    def _int(self, tag: str) -> int:
        return int.from_bytes(self._digest(tag)[:8], "big")

    def _time(self) -> datetime:
        if self._now is None:
            try:
                zone = ZoneInfo(self._tz)
            except (KeyError, ValueError):
                zone = ZoneInfo("UTC")
            self._now = datetime.now(zone)
        return self._now

    def _compute(self, tag: str) -> str:
        if tag == "USERID":
            return self.email.split("@", 1)[0]
        if tag == "EMAIL":
            return self.email
        if tag == "RANDOM":
            return str(self._uuid(tag).int % 9_000_000 + 1_000_000)  # always 7 digits
        if tag == "SUBSID":
            return self._uuid(tag).hex[:10].upper()
        if tag == "INVOICE":
            return self._uuid(tag).hex[:7].upper()
        if tag == "REF":
            return self._uuid(tag).hex[:8].upper()
        if tag == "HASH":
            return self._digest(tag).hex()  # 32 bytes → 64 hex characters
        if tag == "DATE":
            return self._time().strftime("%Y-%m-%d")
        if tag == "TIME":
            return self._time().strftime("%H:%M:%S")
        if tag == "OTP":
            return f"{self._int(tag) % 1_000_000:06d}"
        if tag == "$$":
            return str(self._int(tag) % 90 + 10)  # 10–99
        if tag in ("MASSAGE", "MESSAGE"):
            return self.messages[self._int("MASSAGE") % len(self.messages)] if self.messages else ""
        return ""

    def get(self, tag: str) -> str:
        value = self._cache.get(tag)
        if value is None:
            value = self._cache[tag] = self._compute(tag)
        return value


def apply(text: str, values: TagValues, escape: Callable[[str], str]) -> str:
    if "#" not in text:
        return text
    return TAG_RE.sub(lambda m: escape(values.get(m.group(1))), text)
