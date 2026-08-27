import * as Switch from "@radix-ui/react-switch";
import { LoaderCircle, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { assetSource } from "./site-config";
import type { FooterLink, GlobalSetting } from "./types";

type Draft = Record<string, unknown>;

type SettingsSection = { title?: string; keys: readonly string[] };
type SettingsGroup = { id: string; title: string; description: string; sections: readonly SettingsSection[] };

const SITE_GROUPS: readonly SettingsGroup[] = [
  { id: "site", title: "站点信息", description: "站点名称、Logo 和 Favicon", sections: [{ title: "基本信息", keys: ["site_name", "site_logo", "site_favicon"] }] },
  { id: "footer", title: "页脚配置", description: "页脚链接、版权和备案信息", sections: [{ title: "页脚内容", keys: ["footer_links", "footer_copyright"] }, { title: "备案信息", keys: ["icp_number", "police_record_number"] }] },
  { id: "inject", title: "代码注入", description: "自定义样式、脚本与 HTML 注入", sections: [{ title: "样式与脚本", keys: ["custom_css", "custom_javascript"] }, { title: "HTML 注入", keys: ["head_html", "body_end_html"] }, { title: "网站统计", keys: ["analytics_code"] }] },
];

const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
const displayValue = (setting: GlobalSetting) => setting.kind === "boolean" ? Boolean(setting.value) : setting.kind === "json" ? setting.value ?? [] : String(setting.value ?? "");
const apiValue = (setting: GlobalSetting, value: unknown) => setting.kind === "boolean" ? Boolean(value) : setting.kind === "integer" || setting.kind === "number" ? Number(value) : value;

function FooterLinksEditor({ value, onChange }: { value: FooterLink[]; onChange: (value: FooterLink[]) => void }) {
  const update = (index: number, key: keyof FooterLink, next: string) => onChange(value.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: next } : item));
  return <div className="footer-links-editor">{value.map((item, index) => <div className="footer-link-row" key={index}><input aria-label={`链接名称 ${index + 1}`} value={item.label} onChange={(event) => update(index, "label", event.target.value)} placeholder="名称" /><input aria-label={`链接地址 ${index + 1}`} value={item.url} onChange={(event) => update(index, "url", event.target.value)} placeholder="https://example.com" /><button type="button" className="icon-action" aria-label="删除链接" onClick={() => onChange(value.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={15} /></button></div>)}<button type="button" className="text-button add-link" onClick={() => onChange([...value, { label: "", url: "" }])}><Plus size={15} />添加链接</button></div>;
}

function SettingEditor({ setting, value, onChange }: { setting: GlobalSetting; value: unknown; onChange: (value: unknown) => void }) {
  if (setting.kind === "boolean") return <Switch.Root className="switch" checked={Boolean(value)} onCheckedChange={onChange}><Switch.Thumb className="switch-thumb" /></Switch.Root>;
  if (setting.editor === "links") return <FooterLinksEditor value={(value as FooterLink[]) || []} onChange={onChange} />;
  if (setting.editor === "textarea" || setting.editor === "code") return <div className="stacked-editor"><textarea aria-label={setting.label} className={setting.editor === "code" ? "code-editor" : undefined} value={String(value ?? "")} placeholder={setting.placeholder ?? undefined} onChange={(event) => onChange(event.target.value)} /></div>;
  return <div className="stacked-editor"><input aria-label={setting.label} type={setting.kind === "integer" || setting.kind === "number" ? "number" : "text"} step={setting.kind === "number" ? "0.1" : "1"} min={setting.minimum ?? undefined} max={setting.maximum ?? undefined} value={String(value ?? "")} placeholder={setting.placeholder ?? undefined} onChange={(event) => onChange(event.target.value)} />{setting.editor === "asset" && String(value ?? "") && <img className="asset-preview" src={assetSource(String(value))} alt={`${setting.label} 预览`} />}</div>;
}

export function AdminSettingsV2({ onMessage, onDirtyChange }: { onMessage: (message: string) => void; onDirtyChange: (dirty: boolean) => void }) {
  const [settings, setSettings] = useState<GlobalSetting[]>([]);
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState(false);
  const [activeId, setActiveId] = useState("site");
  const load = useCallback(() => api.globalSettings().then((items) => {
    setSettings(items);
    setDraft(Object.fromEntries(items.map((item) => [item.key, displayValue(item)])));
  }).catch((error: unknown) => onMessage(error instanceof Error ? error.message : "设置加载失败")), [onMessage]);
  useEffect(() => { load(); }, [load]);
  const runtimeGroup: SettingsGroup | null = useMemo(() => {
    const runtimeSettings = settings.filter((item) => item.group !== "站点信息" && item.group !== "页面配置");
    if (!runtimeSettings.length) return null;
    const names = Array.from(new Set(runtimeSettings.map((item) => item.group)));
    return {
      id: "runtime",
      title: "系统运行",
      description: "域名监控、任务调度、通知和邮件投递配置",
      sections: names.map((name) => ({ title: name, keys: runtimeSettings.filter((item) => item.group === name).map((item) => item.key) })),
    };
  }, [settings]);
  const groups: readonly SettingsGroup[] = runtimeGroup ? [...SITE_GROUPS, runtimeGroup] : SITE_GROUPS;
  const activeGroup = groups.find((group) => group.id === activeId) ?? groups[0];
  const sections = (activeGroup?.sections ?? []).map((section) => ({ title: section.title, settings: section.keys.map((key) => settings.find((item) => item.key === key)).filter((item): item is GlobalSetting => Boolean(item)) })).filter((section) => section.settings.length);
  const dirtySettings = settings.filter((item) => !same(draft[item.key], displayValue(item)));
  const dirty = dirtySettings.length > 0;
  useEffect(() => { onDirtyChange(dirty); return () => onDirtyChange(false); }, [dirty, onDirtyChange]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (!dirty) return; event.preventDefault(); event.returnValue = ""; }; addEventListener("beforeunload", warn); return () => removeEventListener("beforeunload", warn); }, [dirty]);
  const change = (key: string, value: unknown) => setDraft((current) => ({ ...current, [key]: value }));
  const save = async () => {
    if (!dirty) return;
    setSaving(true);
    try {
      const updated = await api.updateGlobalSettings(dirtySettings.map((item) => ({ key: item.key, version: item.version, value: apiValue(item, draft[item.key]) })));
      setSettings(updated);
      setDraft(Object.fromEntries(updated.map((item) => [item.key, displayValue(item)])));
      dispatchEvent(new Event("site-config-updated"));
      onMessage("设置已保存");
    } catch (error) { onMessage(error instanceof Error ? error.message : "设置保存失败"); } finally { setSaving(false); }
  };
  return <div className="settings-route-shell picasso-settings"><aside className="settings-index" aria-label="系统设置分类"><div className="settings-index-title"><b>系统设置</b></div><nav>{groups.map((group) => <button key={group.id} className={group.id === activeGroup?.id ? "active" : ""} onClick={() => setActiveId(group.id)}><span>{group.title}</span></button>)}</nav></aside><div className="settings-route-main"><header className="picasso-settings-head"><div><h1>{activeGroup?.title || "系统设置"}</h1><p>{activeGroup?.description}</p></div><div className="admin-settings-actions">{dirty && <span className="unsaved-indicator"><i />有未保存的修改</span>}<button className="primary" onClick={save} disabled={saving || !dirty}>{saving && <LoaderCircle className="spin" size={16} />}保存设置</button></div></header><section className="picasso-settings-form">{sections.map((section) => <section className="picasso-setting-section" key={section.title || "default"}>{section.title && <header><h2>{section.title}</h2></header>}<div className="picasso-setting-list">{section.settings.map((setting) => <div className={`picasso-setting-row editor-${setting.editor}`} key={setting.key}><div className="picasso-setting-copy"><b>{setting.label}</b><small>{setting.description}</small></div><div className="picasso-setting-editor"><SettingEditor setting={setting} value={draft[setting.key]} onChange={(value) => change(setting.key, value)} /></div></div>)}</div></section>)}{!settings.length && <div className="empty">正在读取设置…</div>}</section></div></div>;
}
