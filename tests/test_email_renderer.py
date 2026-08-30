from domainsmanager_api.email_renderer import (
    load_email_template,
    render_notification_email,
)


def test_loads_html_template_from_package() -> None:
    template = load_email_template()

    assert "$title" in template.template


def test_renders_html_and_plain_text_notification() -> None:
    rendered = render_notification_email(
        {
            "type": "domain.expiration_warning",
            "created_at": "2026-08-30T10:00:00Z",
            "data": {"domain_id": "example.com", "days_before": 7, "note": "<unsafe>"},
        },
        site_name="我的域名中心",
    )

    assert rendered.subject == "我的域名中心：域名即将到期"
    assert "我的域名中心" in rendered.html
    assert "example.com" in rendered.text
    assert "&lt;unsafe&gt;" in rendered.html
    assert "<unsafe>" not in rendered.html
    assert "text/html" not in rendered.html
