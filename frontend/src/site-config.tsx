import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api } from "./api";
import type { PublicSiteConfig } from "./types";

export const defaultSiteConfig: PublicSiteConfig = {
  revision: "default",
  registration_enabled: false,
  smtp_enabled: true,
  site_name: "DomainsManager",
  site_logo: "/default.svg",
  site_favicon: "/default.svg",
  footer_links: [],
  footer_copyright: "",
  icp_number: "",
  police_record_number: "",
  custom_css: "",
  custom_javascript: "",
  head_html: "",
  body_end_html: "",
  analytics_code: "",
};

const SiteConfigContext = createContext(defaultSiteConfig);
export const assetSource = (value: string) => /<svg[\s>]/i.test(value.trim()) ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(value)}` : value;

function replaceStyle(css: string) {
  document.querySelector("style[data-site-custom-css]")?.remove();
  if (!css) return;
  const style = document.createElement("style");
  style.dataset.siteCustomCss = "true";
  style.textContent = css;
  document.head.appendChild(style);
}

function replaceHtml(target: HTMLElement, html: string, marker: string) {
  target.querySelectorAll(`[data-site-injection="${marker}"]`).forEach((node) => node.remove());
  if (!html) return;
  const template = document.createElement("template");
  template.innerHTML = html;
  Array.from(template.content.childNodes).forEach((node) => {
    if (node instanceof HTMLElement) node.dataset.siteInjection = marker;
    target.appendChild(node);
  });
}

function replaceScript(code: string, marker: string) {
  document.querySelectorAll(`script[data-site-script="${marker}"]`).forEach((node) => node.remove());
  if (!code) return;
  const script = document.createElement("script");
  script.dataset.siteScript = marker;
  script.textContent = code;
  document.body.appendChild(script);
}

function replaceAnalytics(code: string) {
  document.querySelectorAll('[data-site-analytics]').forEach((node) => node.remove());
  if (!code) return;
  if (!code.trim().startsWith("<")) {
    const script = document.createElement("script");
    script.dataset.siteAnalytics = "true";
    script.textContent = code;
    document.body.appendChild(script);
    return;
  }
  const template = document.createElement("template");
  template.innerHTML = code;
  Array.from(template.content.childNodes).forEach((node) => {
    if (node instanceof HTMLScriptElement) {
      const script = document.createElement("script");
      Array.from(node.attributes).forEach((attribute) => script.setAttribute(attribute.name, attribute.value));
      script.textContent = node.textContent;
      script.dataset.siteAnalytics = "true";
      document.body.appendChild(script);
    } else if (node instanceof HTMLElement) {
      node.dataset.siteAnalytics = "true";
      document.body.appendChild(node);
    }
  });
}

function applyConfig(config: PublicSiteConfig) {
  document.title = config.site_name || defaultSiteConfig.site_name;
  let favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!favicon) {
    favicon = document.createElement("link");
    favicon.rel = "icon";
    document.head.appendChild(favicon);
  }
  favicon.href = assetSource(config.site_favicon || defaultSiteConfig.site_favicon);
  replaceStyle(config.custom_css);
  replaceHtml(document.head, config.head_html, "head");
  replaceScript(config.custom_javascript, "custom");
  replaceAnalytics(config.analytics_code);
}

export function SiteConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState(defaultSiteConfig);
  useEffect(() => {
    const load = () => api.siteConfig().then((value) => { setConfig(value); applyConfig(value); }).catch(() => applyConfig(defaultSiteConfig));
    load();
    addEventListener("site-config-updated", load);
    return () => {
      removeEventListener("site-config-updated", load);
      replaceStyle("");
      replaceHtml(document.head, "", "head");
      replaceScript("", "custom");
      replaceAnalytics("");
    };
  }, []);
  return <SiteConfigContext.Provider value={config}>{children}</SiteConfigContext.Provider>;
}

export const useSiteConfig = () => useContext(SiteConfigContext);

const YEAR_SCRIPT = /<script[^>]*>\s*document\.write\(\s*(?:new Date\(\)|\(new Date\(\)\))\.getFullYear\(\)\s*\)\s*;?\s*<\/script>/gi;

function renderFooterHtml(html: string) {
  return html.replace(YEAR_SCRIPT, String(new Date().getFullYear()));
}

function digitsOnly(value: string) {
  return value.replace(/\D/g, "");
}

function formatIcpNumber(value: string) {
  const text = value.trim();
  if (!text) return "";
  if (/ICP|备案|备|号/i.test(text)) return text;
  return `ICP备${text}号`;
}

function formatPoliceRecord(value: string) {
  const text = value.trim().replace(/\s+/g, " ");
  if (!text) return "";
  if (/公网安备|公安备案|号/.test(text)) return text;
  return `公网安备 ${text}号`;
}

function policeRecordHref(value: string) {
  const code = digitsOnly(value);
  return code
    ? `https://beian.mps.gov.cn/#/query/webSearch?searchResult=${encodeURIComponent(code)}`
    : "https://beian.mps.gov.cn/#/query/webSearch";
}

function FooterSep() {
  return <span className="site-footer-sep" aria-hidden="true">|</span>;
}

export function SiteFooter() {
  const config = useSiteConfig();
  if (!config.footer_links.length && !config.footer_copyright && !config.icp_number && !config.police_record_number && !config.body_end_html) return null;
  const copyright = config.footer_copyright ? renderFooterHtml(config.footer_copyright) : "";
  const customHtml = config.body_end_html ? renderFooterHtml(config.body_end_html) : "";
  const links = config.footer_links.filter((link) => link.label.trim() || link.url.trim());
  const icpLabel = formatIcpNumber(config.icp_number);
  const policeLabel = formatPoliceRecord(config.police_record_number);
  const hasRecords = Boolean(icpLabel || policeLabel);
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        {links.length > 0 && (
          <nav className="site-footer-links" aria-label="页脚链接">
            {links.map((link, index) => (
              <span className="site-footer-item" key={`${link.label}-${link.url}`}>
                {index > 0 && <span className="site-footer-dot" aria-hidden="true">·</span>}
                <a href={link.url} target="_blank" rel="noreferrer">{link.label}</a>
              </span>
            ))}
          </nav>
        )}
        {copyright && <div className="site-footer-copyright" dangerouslySetInnerHTML={{ __html: copyright }} />}
        {hasRecords && (
          <div className="site-footer-records">
            {icpLabel && (
              <a className="site-footer-record" href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">{icpLabel}</a>
            )}
            {icpLabel && policeLabel ? <FooterSep /> : null}
            {policeLabel && (
              <a className="site-footer-record" href={policeRecordHref(config.police_record_number)} target="_blank" rel="noreferrer">
                {policeLabel}
              </a>
            )}
          </div>
        )}
        {customHtml && <div className="site-footer-custom" dangerouslySetInnerHTML={{ __html: customHtml }} />}
      </div>
    </footer>
  );
}
