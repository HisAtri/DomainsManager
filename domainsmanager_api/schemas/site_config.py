from __future__ import annotations

from pydantic import Field

from domainsmanager_api.schemas.admin import StrictModel


class FooterLink(StrictModel):
    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)


class PublicSiteConfig(StrictModel):
    revision: str
    registration_enabled: bool
    smtp_enabled: bool
    anti_bot_mode: str = "disabled"
    turnstile_site_key: str = ""
    site_name: str
    site_url: str
    site_logo: str
    site_favicon: str
    footer_links: list[FooterLink] = Field(default_factory=list)
    footer_copyright: str = ""
    icp_number: str = ""
    police_record_number: str = ""
    custom_css: str = ""
    custom_javascript: str = ""
    head_html: str = ""
    body_end_html: str = ""
    analytics_code: str = ""
