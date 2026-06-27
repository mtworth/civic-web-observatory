from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class DomainObservation(BaseModel):
    # Identity
    domain: str
    source: Optional[str] = None
    org_name: Optional[str] = None
    org_type: Optional[str] = None
    state: Optional[str] = None
    ein: Optional[str] = None
    sector: Optional[str] = None
    run_id: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    crawler_version: str = "0.1.0"

    # DNS
    dns_resolves: Optional[bool] = None
    dns_error: Optional[str] = None
    a_records: list[str] = Field(default_factory=list)
    aaaa_records: list[str] = Field(default_factory=list)
    cname_records: list[str] = Field(default_factory=list)
    ns_records: list[str] = Field(default_factory=list)

    # Homepage availability
    homepage_attempted: bool = False
    homepage_url: Optional[str] = None
    homepage_final_url: Optional[str] = None
    homepage_status_code: Optional[int] = None
    homepage_content_type: Optional[str] = None
    homepage_response_time_ms: Optional[int] = None
    homepage_size_bytes: Optional[int] = None
    homepage_redirect_count: Optional[int] = None
    homepage_error: Optional[str] = None
    homepage_block_type: str = "none"
    homepage_ua_retry_succeeded: bool = False

    # HTTPS / TLS
    https_available: Optional[bool] = None
    http_available: Optional[bool] = None
    http_redirects_to_https: Optional[bool] = None
    tls_valid: Optional[bool] = None
    tls_error: Optional[str] = None
    tls_issuer: Optional[str] = None
    tls_not_before: Optional[str] = None
    tls_not_after: Optional[str] = None
    tls_days_until_expiry: Optional[int] = None

    # robots.txt
    robots_txt_status_code: Optional[int] = None
    robots_txt_available: Optional[bool] = None
    robots_txt_size_bytes: Optional[int] = None
    robots_txt_error: Optional[str] = None
    robots_allows_homepage: Optional[bool] = None
    robots_sitemaps_listed: Optional[int] = None

    # sitemap.xml
    sitemap_xml_status_code: Optional[int] = None
    sitemap_xml_available: Optional[bool] = None
    sitemap_xml_size_bytes: Optional[int] = None
    sitemap_xml_url_count: Optional[int] = None
    sitemap_xml_error: Optional[str] = None

    # llms.txt
    llms_txt_status_code: Optional[int] = None
    llms_txt_available: Optional[bool] = None
    llms_txt_size_bytes: Optional[int] = None
    llms_txt_has_markdown_headings: Optional[bool] = None
    llms_txt_link_count: Optional[int] = None
    llms_txt_error: Optional[str] = None

    # Basic HTML
    page_title: Optional[str] = None
    page_title_present: Optional[bool] = None
    meta_description_present: Optional[bool] = None
    html_lang_present: Optional[bool] = None
    canonical_url: Optional[str] = None
    mobile_viewport_present: Optional[bool] = None
    h1_count: Optional[int] = None

    # Hosting / Infrastructure
    primary_ip: Optional[str] = None
    host_asn: Optional[str] = None
    host_asn_org: Optional[str] = None
    host_network_name: Optional[str] = None
    nameserver_provider_hint: Optional[str] = None
    cname_provider_hint: Optional[str] = None

    # Static accessibility
    static_a11y_ran: bool = False
    static_a11y_error: Optional[str] = None
    a11y_has_title: Optional[bool] = None
    a11y_has_html_lang: Optional[bool] = None
    a11y_h1_count: Optional[int] = None
    a11y_images_total: Optional[int] = None
    a11y_images_missing_alt: Optional[int] = None
    a11y_inputs_total: Optional[int] = None
    a11y_inputs_missing_labels: Optional[int] = None
    a11y_has_main_landmark: Optional[bool] = None
    a11y_has_nav_landmark: Optional[bool] = None

    # Axe accessibility (stub for V1 test)
    axe_ran: bool = False
    axe_error: Optional[str] = None
    axe_violations_total: Optional[int] = None
    axe_violations_critical: Optional[int] = None
    axe_violations_serious: Optional[int] = None
    axe_violations_moderate: Optional[int] = None
    axe_violations_minor: Optional[int] = None
    axe_rule_ids: list[str] = Field(default_factory=list)
    axe_wcag_tags: list[str] = Field(default_factory=list)

    # Technology fingerprinting
    technologies: list[str] = Field(default_factory=list)

    # Parked / placeholder detection
    is_probably_parked: bool = False
    parked_reason: Optional[str] = None
    social_only_redirect: bool = False
    social_only_platform: Optional[str] = None

    # Error tracking
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    stage: Optional[str] = None

    # Overall status
    collection_status: str = "unknown_error"
