"""Template tags (design DS-24): formats, per-message determinism, HTML safety, both render paths."""

from __future__ import annotations

import re
from datetime import datetime
from email import message_from_bytes, policy
from zoneinfo import ZoneInfo

from app.sender import tags
from app.sender.message import CompiledCampaign, build, render

SEED = "a" * 64


def _values(rid: object = 1, email: str = "mahdi@gmail.com", **kw) -> tags.TagValues:  # noqa: ANN003
    return tags.TagValues(SEED, rid, email, **kw)


def test_formats_match_the_reference_table() -> None:
    v = _values(messages=["Hello", "Hi", "Welcome"], now=datetime(2026, 9, 23, 16, 59, 42))
    assert v.get("USERID") == "mahdi" and v.get("EMAIL") == "mahdi@gmail.com"
    assert re.fullmatch(r"[1-9]\d{6}", v.get("RANDOM"))
    assert re.fullmatch(r"[0-9A-F]{10}", v.get("SUBSID"))
    assert re.fullmatch(r"[0-9A-F]{7}", v.get("INVOICE"))
    assert re.fullmatch(r"[0-9A-F]{8}", v.get("REF"))
    assert re.fullmatch(r"[0-9a-f]{64}", v.get("HASH")), "32 bytes as hex"
    assert v.get("DATE") == "2026-09-23" and v.get("TIME") == "16:59:42"
    assert re.fullmatch(r"\d{6}", v.get("OTP"))
    assert 10 <= int(v.get("$$")) <= 99
    assert v.get("MASSAGE") in {"Hello", "Hi", "Welcome"} and v.get("MESSAGE") == v.get("MASSAGE")
    assert _values().get("MASSAGE") == "", "no message list → empty"


def test_values_are_stable_per_message_and_differ_between_recipients() -> None:
    a1, a2, b = _values(1), _values(1), _values(2)
    keys = ["RANDOM", "SUBSID", "INVOICE", "REF", "HASH", "OTP"]
    assert [a1.get(k) for k in keys] == [a2.get(k) for k in keys], "retry renders the same message"
    assert [a1.get(k) for k in keys] != [b.get(k) for k in keys]
    other_campaign = tags.TagValues("b" * 64, 1, "mahdi@gmail.com")
    assert other_campaign.get("HASH") != a1.get("HASH"), "unpredictable without the campaign seed"
    # $$ spreads across 10–99.
    assert len({_values(i).get("$$") for i in range(300)}) > 60


def test_timezone() -> None:
    v = _values(tz="Asia/Dhaka")
    assert v.get("DATE") == datetime.now(ZoneInfo("Asia/Dhaka")).strftime("%Y-%m-%d")
    assert _values(tz="Not/AZone").get("TIME")  # invalid timezone falls back to UTC


def test_render_escapes_and_leaves_unknown_tags() -> None:
    v = tags.TagValues(SEED, 1, "x@example.org", messages=["<b>50% off</b> & more"])
    out = render("#MASSAGE# #UNKNOWN# #USERID# {{first_name}}", {"first_name": "#EMAIL#"}, "html", v)
    assert out == "&lt;b&gt;50% off&lt;/b&gt; &amp; more #UNKNOWN# x #EMAIL#", \
        "HTML-escaped; recipient data is never re-scanned for tags"
    assert render("Invoice #INVOICE# for #USERID#", {}, "header", v) == f"Invoice {v.get('INVOICE')} for x"
    evil = tags.TagValues(SEED, 1, "x@example.org", messages=["hi\r\nBcc: victim@example.org"])
    header = render("#MASSAGE#", {}, "header", evil)
    assert "\r" not in header and "\n" not in header, "no header injection through tag values"
    assert render("#REF#", {}, "text", None) == "#REF#", "no tag context → untouched"


def _campaign(**kw) -> dict:  # noqa: ANN003
    return {"id": "c1", "subject": "Invoice #INVOICE# for #USERID#", "from_email": "news@brand.example",
            "html_body": "<p>Hi #USERID#, ref #REF#, otp #OTP#, #MASSAGE#</p>", "tag_seed": SEED,
            "message_list": ["Welcome"], "tag_timezone": "UTC", **kw}


def _recipient(i: int, email: str) -> dict:
    return {"id": i, "email": email, "variables": {}, "unsubscribe_url": "https://u.example/x"}


def test_same_values_in_subject_and_body_and_both_paths_agree() -> None:
    compiled = CompiledCampaign(_campaign())
    raw, _ = compiled.render(_recipient(7, "mahdi@gmail.com"))
    msg = message_from_bytes(raw, policy=policy.default)
    v = _values(7)
    assert msg["Subject"] == f"Invoice {v.get('INVOICE')} for mahdi"
    html = msg.get_body(("html",)).get_content()
    assert f"Hi mahdi, ref {v.get('REF')}, otp {v.get('OTP')}, Welcome" in html
    text = msg.get_body(("plain",)).get_content()
    assert v.get("OTP") in text
    # The EmailMessage path (used for internationalised addresses) renders the same values.
    built, _ = build(_campaign(), _recipient(7, "mahdi@gmail.com"))
    assert built["Subject"] == msg["Subject"]
    # Rendering again (a retry) is identical apart from Message-ID/Date.
    raw2, _ = compiled.render(_recipient(7, "mahdi@gmail.com"))
    assert message_from_bytes(raw2, policy=policy.default)["Subject"] == msg["Subject"]


def test_static_subject_optimisation_respects_tags() -> None:
    compiled = CompiledCampaign(_campaign(subject="Order #REF#"))
    s1 = message_from_bytes(compiled.render(_recipient(1, "a@x.example"))[0], policy=policy.default)["Subject"]
    s2 = message_from_bytes(compiled.render(_recipient(2, "b@x.example"))[0], policy=policy.default)["Subject"]
    assert s1 != s2 and s1.startswith("Order ")
