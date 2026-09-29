from __future__ import annotations

import asyncio
import time
from typing import Any

import aiosmtplib
import pytest

from app.config import WorkerConfig
from app.health.metrics import RateMeter
from app.rate_limit.bucket import LocalBucket, Unlimited
from app.retry.classify import classify_exception
from app.sender.message import build, html_to_text, render
from app.sender.runner import JobRunner

CAMPAIGN = {"id": "c1", "subject": "Hi {{first_name}}", "from_email": "news@brand.example", "from_name": "Brand",
            "reply_to": None, "html_body": "<p>Hello {{ first_name }}</p>", "text_body": None}


def _recipient(i: int = 1, **variables: str) -> dict[str, Any]:
    return {"id": i, "email": f"r{i}@example.org", "variables": variables,
            "unsubscribe_url": f"https://app.example/api/v1/u/tok{i}"}


def test_render_escapes_html_and_strips_header_newlines() -> None:
    assert render("<b>{{x}}</b>", {"x": "<script>"}, "html") == "<b>&lt;script&gt;</b>"
    assert render("Hi {{x}}", {"x": "a\r\nBcc: evil@x"}, "header") == "Hi a  Bcc: evil@x"
    assert render("{{missing}}!", {}, "text") == "!"


def test_build_message_headers_and_unsubscribe_footer() -> None:
    msg, mid = build(CAMPAIGN, _recipient(first_name="<Ann>"))
    assert msg["Subject"] == "Hi <Ann>"
    assert msg["List-Unsubscribe"] == "<https://app.example/api/v1/u/tok1>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mid.endswith("@brand.example>") and msg["Message-ID"] == mid
    html_part = msg.get_body(("html",)).get_content()
    assert "Hello &lt;Ann&gt;" in html_part and "Unsubscribe</a>" in html_part
    text_part = msg.get_body(("plain",)).get_content()
    assert "Hello <Ann>" in text_part and "Unsubscribe: https://app.example/api/v1/u/tok1" in text_part


def test_no_footer_when_template_has_link() -> None:
    campaign = {**CAMPAIGN, "html_body": '<a href="{{unsubscribe_url}}">Leave</a>'}
    msg, _ = build(campaign, _recipient())
    html_part = msg.get_body(("html",)).get_content()
    assert html_part.count("tok1") == 1


def test_html_to_text() -> None:
    assert html_to_text("<p>One</p><p>Two &amp; three</p>") == "One\nTwo & three"


@pytest.mark.parametrize(
    ("exc", "category", "systemic"),
    [
        (aiosmtplib.SMTPRecipientRefused(550, "5.1.1 User unknown", "x@y"), "rejected", False),
        (aiosmtplib.SMTPRecipientRefused(451, "4.7.1 Try later", "x@y"), "transient", False),
        (aiosmtplib.SMTPDataError(552, "Message too big"), "rejected", False),
        (aiosmtplib.SMTPAuthenticationError(535, "5.7.8 bad creds"), "auth", True),
        (aiosmtplib.SMTPSenderRefused(553, "sender not allowed", "a@b"), "provider", True),
        (aiosmtplib.SMTPServerDisconnected("gone"), "connection", False),
        (TimeoutError(), "timeout", False),
    ],
)
def test_classification(exc: BaseException, category: str, systemic: bool) -> None:
    c = classify_exception(exc)
    assert (c.category, c.systemic) == (category, systemic)


def test_enhanced_code_extracted() -> None:
    c = classify_exception(aiosmtplib.SMTPRecipientRefused(550, "5.1.1 <x@y>: Recipient address rejected", "x@y"))
    assert c.smtp_code == 550 and c.enhanced_code == "5.1.1"


async def test_local_bucket_limits_rate() -> None:
    bucket = LocalBucket(rate=20)
    start = time.monotonic()
    for _ in range(40):  # 20 burst + 20 at 20/s ≈ 1s
        await bucket.acquire()
    assert 0.8 <= time.monotonic() - start < 2.0


# --------------------------------------------------------------------------- runner with fakes


class FakeApi:
    def __init__(self, renew_action: str = "continue") -> None:
        self.reported: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.renew_action = renew_action
        self.failure_args: dict[str, Any] | None = None

    async def renew(self, job_id: str, attempt_id: str) -> dict[str, Any]:
        self.calls.append("renew")
        return {"action": self.renew_action}

    async def results(self, job_id: str, attempt_id: str, batch: list[dict[str, Any]]) -> dict[str, Any]:
        self.reported.extend(batch)
        return {}

    async def ack(self, job_id: str, attempt_id: str) -> dict[str, Any]:
        self.calls.append("ack")
        return {}

    async def failure(self, job_id: str, attempt_id: str, **kw: Any) -> dict[str, Any]:
        self.calls.append("failure")
        self.failure_args = kw
        return {}

    async def release(self, job_id: str, attempt_id: str, reason: str) -> dict[str, Any]:
        self.calls.append("release")
        return {}


class FakeProvider:
    def __init__(self, fail: dict[str, BaseException] | None = None, connect_error: BaseException | None = None,
                 delay: float = 0.0) -> None:
        self.fail = fail or {}
        self.connect_error = connect_error
        self.sent: list[str] = []
        self.delay = delay

    async def connect(self) -> None:
        if self.connect_error:
            raise self.connect_error

    async def send(self, message: Any, sender: str, recipient: str) -> str:
        if self.delay:
            await asyncio.sleep(self.delay)
        if recipient in self.fail:
            raise self.fail[recipient]
        self.sent.append(recipient)
        return "250 ok"

    async def close(self) -> None:
        return None


def _job(n: int = 3) -> dict[str, Any]:
    return {"job_id": "j1", "attempt_id": "a1", "campaign": CAMPAIGN, "rate_limit_per_second": None,
            "provider": {"id": "p1", "host": "h", "port": 25}, "recipients": [_recipient(i) for i in range(n)]}


def _runner(api: FakeApi, provider: FakeProvider, n: int = 3) -> JobRunner:
    config = WorkerConfig(api_url="http://x", worker_id="w", credential="c", result_flush_interval=0.05)
    return JobRunner(api, config, _job(n), RateMeter(), Unlimited(), provider_factory=lambda cfg: provider)  # type: ignore[arg-type]


async def test_runner_success_reports_and_acks() -> None:
    api, provider = FakeApi(), FakeProvider(fail={"r1@example.org": aiosmtplib.SMTPRecipientRefused(
        550, "5.1.1 no such user", "r1@example.org")})
    assert await _runner(api, provider).run() == "completed"
    assert api.calls[-1] == "ack"
    by_id = {r["recipient_id"]: r for r in api.reported}
    assert by_id[0]["outcome"] == "sent" and by_id[0]["provider_message_id"].startswith("<")
    assert by_id[1]["outcome"] == "failed" and by_id[1]["enhanced_code"] == "5.1.1"
    assert len(api.reported) == 3


async def test_runner_auth_failure_is_job_level() -> None:
    api = FakeApi()
    provider = FakeProvider(connect_error=aiosmtplib.SMTPAuthenticationError(535, "bad credentials"))
    assert await _runner(api, provider).run() == "failed"
    assert api.calls[-1] == "failure" and api.failure_args["category"] == "auth"
    assert api.reported == []


async def test_runner_connection_streak_aborts() -> None:
    api = FakeApi()
    fails = {f"r{i}@example.org": aiosmtplib.SMTPServerDisconnected("down") for i in range(10)}
    assert await _runner(api, FakeProvider(fail=fails), n=10).run() == "failed"
    assert api.failure_args["category"] == "connection"


async def test_runner_stop_releases_remaining() -> None:
    api = FakeApi(renew_action="stop")
    provider = FakeProvider(delay=0.01)
    assert await _runner(api, provider, n=50).run() == "released"
    assert "release" in api.calls and "ack" not in api.calls
