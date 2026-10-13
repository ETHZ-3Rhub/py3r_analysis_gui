from urllib.parse import parse_qs, urlparse

from app.report_dialog import QUESTIONS, github_issue_url, report_body, report_subject
from app.tracking_env_setup import get_version


def test_body_has_questions_and_system_details():
    body = report_body("mps")
    for question in QUESTIONS:
        assert question in body
    assert f"App version: v{get_version()}" in body
    assert "System: " in body
    assert "Tracking environment: GPU (Apple Silicon)" in body


def test_github_url_prefills_title_and_body():
    url = urlparse(github_issue_url("cpu"))
    assert url.path.endswith("/issues/new")
    query = parse_qs(url.query)
    assert query["title"] == [report_subject()]
    assert query["body"] == [report_body("cpu")]
