import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api } from "./api";
import type { PublicSiteConfig } from "./types";

export const defaultSiteConfig: PublicSiteConfig = {
  revision: "default",
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

export function SiteFooter() {
  const config = useSiteConfig();
  if (!config.footer_links.length && !config.footer_copyright && !config.icp_number && !config.police_record_number && !config.body_end_html) return null;
  return <footer className="site-footer"><div className="site-footer-links">{config.footer_links.map((link) => <a key={`${link.label}-${link.url}`} href={link.url} target="_blank" rel="noreferrer">{link.label}</a>)}</div>{config.footer_copyright && <span>{config.footer_copyright}</span>}<div className="site-footer-records">{config.icp_number && <a href="https://beian.miit.gov.cn/" target="_blank" rel="noreferrer">{config.icp_number}</a>}{config.police_record_number && <a href="https://www.beian.gov.cn/" target="_blank" rel="noreferrer">{config.police_record_number}</a>}</div>{config.body_end_html && <div className="site-footer-custom" dangerouslySetInnerHTML={{ __html: config.body_end_html }} />}</footer>;
}
