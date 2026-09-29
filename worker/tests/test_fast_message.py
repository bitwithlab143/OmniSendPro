"""The compiled fast path must produce messages equivalent to the email-package reference builder."""

from __future__ import annotations

import time
from email import message_from_bytes, policy
from typing import Any

import pytest

from app.sender.message import CompiledCampaign, build

BASE = {"id": "c-1", "subject": "Hi {{first_name}}", "from_email": "news@brand.example", "from_name": "Brand News",
        "reply_to": "help@brand.example", "html_body": "<h1>Hello {{first_name}}</h1><p>From {{city}}</p>",
        "text_body": None}


def rcpt(i: int = 1, email: str | None = None, **variables: str) -> dict[str, Any]:
    return {"id": i, "email": email or f"r{i}@example.org", "variables": variables,
            "unsubscribe_url": f"https://app.example/api/v1/u/tok{i}"}


def parse(raw: bytes):  # noqa: ANN201
    return message_from_bytes(raw, policy=policy.default)


def fast(campaign: dict[str, Any], recipient: dict[str, Any]):  # noqa: ANN201
    raw, mid = CompiledCampaign(campaign).render(recipient)
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), "CRLF line endings only"
    assert all(len(line) <= 998 for line in raw.split(b"\r\n")), "RFC 5322 line length"
    return parse(raw), mid


@pytest.mark.parametrize(
    ("campaign", "variables"),
    [
        (BASE, {"first_name": "Ann", "city": "Dhaka"}),
        (BASE, {"first_name": "<script>alert(1)</script>", "city": "A & B"}),
        ({**BASE, "subject": "Grüße {{first_name}} — café ☕", "from_name": "Café Zürich"}, {"first_name": "Zoë"}),
        ({**BASE, "subject": "A very long subject " * 12}, {}),
        ({**BASE, "html_body": None, "text_body": "Plain {{first_name}}\nLine two"}, {"first_name": "Tim"}),
        ({**BASE, "html_body": '<a href="{{unsubscribe_url}}">Leave</a>'}, {}),
        ({**BASE, "reply_to": None, "from_name": ""}, {"first_name": "X"}),
    ],
)
def test_fast_path_matches_reference(campaign: dict[str, Any], variables: dict[str, str]) -> None:
    r = rcpt(**variables)
    ref_msg, _ = build(campaign, r)
    ref = parse(ref_msg.as_bytes())
    got, mid = fast(campaign, r)
    for header in ("Subject", "From", "To", "Reply-To", "List-Unsubscribe", "List-Unsubscribe-Post",
                   "X-OmniSend-Campaign"):
        assert str(got[header] or "") == str(ref[header] or ""), header
    assert got["Message-ID"] == mid and mid.endswith("@brand.example>")
    assert got["Date"]
    for kind in ("plain", "html"):
        ref_part, got_part = ref.get_body((kind,)), got.get_body((kind,))
        assert (ref_part is None) == (got_part is None), kind
        if ref_part is not None:
            assert got_part.get_content().replace("\r\n", "\n").strip() == ref_part.get_content().strip(), kind


def test_header_injection_through_variables_is_neutralised() -> None:
    got, _ = fast(BASE, rcpt(first_name="Ann\r\nBcc: victim@example.com"))
    assert got["Bcc"] is None
    assert "victim" in str(got["Subject"])  # kept as text, on the Subject line only


def test_non_ascii_address_falls_back_to_reference_builder() -> None:
    got, mid = fast(BASE, rcpt(email="zoë@example.org"))
    # Raw UTF-8 address (SMTPUTF8), not an RFC 2047 encoded-word, which would be an invalid address.
    assert got["Message-ID"] == mid and str(got["To"]) == "zoë@example.org"


def test_fast_path_is_much_faster() -> None:
    compiled = CompiledCampaign(BASE)
    r = rcpt(first_name="Ann", city="Dhaka")
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        compiled.render(r)
    fast_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(n):
        build(BASE, r)[0].as_bytes()
    ref_s = time.perf_counter() - t0
    print(f"fast={n / fast_s:,.0f}/s reference={n / ref_s:,.0f}/s speedup={ref_s / fast_s:.1f}x")
    assert ref_s / fast_s > 3
