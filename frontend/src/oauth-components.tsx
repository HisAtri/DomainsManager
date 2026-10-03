import * as Dialog from "@radix-ui/react-dialog";
import { AlertCircle, CheckCircle2, Link2, LoaderCircle, RotateCw, Unlink2, X } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "./api";
import type { OAuthAccounts, OAuthAvailability, OAuthProvider, User } from "./types";
import "./oauth.css";

const errorText = (error: unknown) => error instanceof Error ? error.message : "操作未完成，请稍后重试。";
const providerNames: Record<OAuthProvider["key"], string> = { github: "GitHub", linuxdo: "LINUX DO" };
const configuredProviders = (availability: OAuthAvailability): OAuthProvider[] => [
  ...(availability.github ? [{ key: "github" as const, display_name: "GitHub" }] : []),
  ...(availability.linuxdo ? [{ key: "linuxdo" as const, display_name: "LINUX DO" }] : []),
];

function ProviderLogo({ provider }: { provider: OAuthProvider["key"] }) {
  return <img className={`oauth-logo oauth-logo-${provider}`} src={`/oauth/${provider}.svg`} alt="" />;
}

function oauthActionMessage(error: unknown): string {
  // OAuth 操作只展示下一步可执行的信息，不能把服务端异常文本放进账户界面。
  if (!(error instanceof Error) || !("code" in error)) return "操作没有完成，请稍后重试。";
  switch (error.code) {
    case "oauth_reauthentication_required":
    case "oauth_session_changed": return "请重新登录后再修改关联账号。";
    case "oauth_identity_in_use": return "这个第三方账号已关联其他用户。";
    case "oauth_provider_already_linked": return "你已关联此平台的另一个账号，请先解除原关联。";
    case "oauth_last_login_method": return "请先设置密码或关联另一种登录方式。";
    case "oauth_provider_not_found": return "此登录方式暂不可用。";
    default: return "操作没有完成，请稍后重试。";
  }
}

export function OAuthCallbackNotice({ authenticated }: { authenticated: boolean }) {
  const [result] = useState(() => {
    const query = new URLSearchParams(location.hash.split("?")[1] || "");
    return { status: query.get("oauth"), error: query.get("oauth_error") };
  });
  useEffect(() => {
    if (!result.status && !result.error) return;
    const [path, search] = location.hash.split("?");
    const query = new URLSearchParams(search);
    query.delete("oauth"); query.delete("oauth_error");
    // 回调只消费结果标记；令牌继续通过 HttpOnly Cookie 和 restoreTokens 恢复。
    history.replaceState(null, "", `${location.pathname}${location.search}${path}${query.size ? `?${query}` : ""}`);
  }, [result]);
  if (!result.status && !result.error) return null;
  const messages: Record<string, string> = {
    access_denied: "已取消授权，你仍可使用其他方式登录。",
    oauth_access_denied: "已取消授权，你仍可使用其他方式登录。",
    account_banned: "账号已被禁用。",
    registration_disabled: "当前未开放新账号注册。",
    oauth_account_already_linked: "这个账号已关联其他用户。",
    oauth_provider_disabled: "此登录方式暂不可用。",
    oauth_provider_not_found: "此登录方式暂不可用，请选择其他方式登录。",
    oauth_invalid_state: "授权已过期，请重新尝试。",
    oauth_reauthentication_required: "请重新登录后再修改关联账号。",
    oauth_provider_error: "暂时无法完成授权，请稍后重试。",
    oauth_identity_in_use: "这个账号已关联其他用户。",
    oauth_provider_already_linked: "你已关联此平台的另一个账号。",
    oauth_last_login_method: "请先设置密码或关联另一种登录方式。",
    oauth_inactive_identity: "此账号目前无法用于登录。",
    oauth_session_changed: "请重新登录后再试。",
  };
  const success = !result.error && authenticated && ["success", "linked"].includes(result.status || "");
  const message = result.error ? messages[result.error] || "未能完成授权，请重新尝试。" : success ? result.status === "linked" ? "账号已成功关联。" : "登录成功。" : "登录未完成，请再试一次。";
  return <div className={`oauth-feedback ${success ? "oauth-feedback-success" : "oauth-feedback-error"}`} role={success ? "status" : "alert"}>
    {success ? <CheckCircle2 size={17} aria-hidden="true" /> : <AlertCircle size={17} aria-hidden="true" />}
    <span>{message}</span>
  </div>;
}

export function OAuthLoginButtons() {
  const [providers, setProviders] = useState<OAuthProvider[]>([]);
  const [unavailable, setUnavailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    setUnavailable(false);
    api.oauthAvailability().then((value) => setProviders(configuredProviders(value))).catch(() => setUnavailable(true));
  }, []);
  useEffect(load, [load]);
  if (!providers.length && !unavailable) return null;
  return <div className="oauth-login">
    <div className="oauth-divider"><span>其他登录方式</span></div>
    {unavailable ? <div className="oauth-availability-note" role="status"><span>其他登录方式暂不可用</span><button type="button" onClick={load}><RotateCw size={14} aria-hidden="true" />重试</button></div> :
      <div className="oauth-buttons">{providers.map((provider) => <button key={provider.key} type="button" className="oauth-login-button" disabled={busy} onClick={() => { setBusy(true); api.oauthLogin(provider.key); }}><ProviderLogo provider={provider.key} /><span>使用 {provider.display_name} 登录</span></button>)}</div>}
  </div>;
}

export function OAuthBindings({ passwordEnabled }: { passwordEnabled: boolean }) {
  const [providers, setProviders] = useState<OAuthProvider[]>([]);
  const [availabilityKnown, setAvailabilityKnown] = useState(false);
  const [accounts, setAccounts] = useState<OAuthAccounts | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [busy, setBusy] = useState<OAuthProvider["key"] | null>(null);
  const [confirming, setConfirming] = useState<OAuthProvider["key"] | null>(null);
  const [openHelp, setOpenHelp] = useState<OAuthProvider["key"] | null>(null);
  const [feedback, setFeedback] = useState<{ message: string; success: boolean } | null>(null);
  const load = useCallback(async () => {
    const [availability, nextAccounts] = await Promise.allSettled([api.oauthAvailability(), api.oauthAccounts()]);
    setProviders(availability.status === "fulfilled" ? configuredProviders(availability.value) : []);
    // 请求失败表示状态未知，不能把上次结果或空列表当作平台已停用来呈现。
    setAvailabilityKnown(availability.status === "fulfilled");
    if (nextAccounts.status === "fulfilled") setAccounts(nextAccounts.value);
    setLoadError(availability.status === "rejected" || nextAccounts.status === "rejected");
  }, []);
  useEffect(() => { void load(); }, [load, passwordEnabled]);
  useEffect(() => {
    if (!openHelp) return;
    const dismiss = (event: PointerEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest(`[data-oauth-help-provider="${openHelp}"]`)) setOpenHelp(null);
    };
    const onEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpenHelp(null); };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onEscape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", onEscape); };
  }, [openHelp]);
  const act = async (provider: OAuthProvider["key"], linked: boolean) => {
    setBusy(provider); setFeedback(null);
    try {
      if (linked) {
        await api.oauthUnlink(provider);
        // 操作已成功时先更新列表，避免随后刷新失败却仍显示旧关联。
        setAccounts((current) => current ? { ...current, items: current.items.filter((item) => item.provider_key !== provider) } : current);
        await load();
        setFeedback({ message: `${providerNames[provider]} 已解除关联。`, success: true });
      }
      else { const result = await api.oauthLink(provider); location.assign(result.authorization_url); return; }
    } catch (error) { setFeedback({ message: oauthActionMessage(error), success: false }); }
    setBusy(null);
  };
  // 已关联但停用的平台仍保留在列表中，让用户知道现有账户的关联状态。
  const rows = (["github", "linuxdo"] as const).filter((key) => providers.some((provider) => provider.key === key) || accounts?.items.some((account) => account.provider_key === key));
  const onlyLogin = (key: OAuthProvider["key"]) => !accounts?.password_auth_enabled && !accounts?.items.some((account) => account.provider_key !== key && providers.some((provider) => provider.key === account.provider_key));
  return <section className="personal-settings-section oauth-accounts-section"><h2>关联账号</h2>
    {loadError && <div className="oauth-load-note" role="status"><span>关联信息暂时无法完整显示。</span><button type="button" onClick={() => { void load(); }}><RotateCw size={14} aria-hidden="true" />重试</button></div>}
    {feedback && <div className={`oauth-feedback ${feedback.success ? "oauth-feedback-success" : "oauth-feedback-error"}`} role={feedback.success ? "status" : "alert"}>{feedback.success ? <CheckCircle2 size={17} aria-hidden="true" /> : <AlertCircle size={17} aria-hidden="true" />}<span>{feedback.message}</span></div>}
    {!accounts ? !loadError && <p className="oauth-accounts-empty">正在加载关联账号…</p> : <div className="oauth-account-list">{rows.map((key) => {
      const account = accounts.items.find((item) => item.provider_key === key);
      const linked = Boolean(account);
      const available = providers.some((provider) => provider.key === key);
      const locked = linked && onlyLogin(key);
      const identity = account?.provider_username ? `@${account.provider_username}` : account?.display_name;
      return <div className="oauth-account-row" key={key}>
        <span className="oauth-account-icon"><ProviderLogo provider={key} /></span>
        <div className="oauth-account-details"><b>{providerNames[key]}</b><span>{linked ? identity || "已连接" : "未连接"}{linked && availabilityKnown && !available && <em> · 暂不可用于登录</em>}</span></div>
        <div className="oauth-account-actions" data-oauth-help-provider={key} data-help-open={openHelp === key}>
          {/* 唯一登录方式不能解绑，但按钮仍可聚焦、点击以解释原因；真正解绑请求不会发出。 */}
          <button type="button" className={`oauth-account-action ${linked ? "oauth-account-disconnect" : "oauth-account-connect"}`} disabled={busy !== null} aria-disabled={locked || undefined} aria-label={locked ? "解除关联不可用，查看原因" : undefined} aria-expanded={locked ? openHelp === key : undefined} aria-controls={locked ? `oauth-unlink-help-${key}` : undefined} aria-describedby={locked ? `oauth-unlink-help-${key}` : undefined} onFocus={() => { if (locked) setOpenHelp(key); }} onBlur={() => { if (locked) setOpenHelp(null); }} onClick={() => { if (locked) { setOpenHelp(key); return; } if (linked) setConfirming(key); else void act(key, false); }}>
            {busy === key ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : linked ? <Unlink2 size={16} aria-hidden="true" /> : <Link2 size={16} aria-hidden="true" />}
            {busy === key ? "处理中" : linked ? "解除关联" : "连接"}
          </button>
          {locked && <span id={`oauth-unlink-help-${key}`} role="tooltip" className="oauth-account-tooltip">请先设置密码或关联另一账号。</span>}
        </div>
      </div>;
    })}{!rows.length && !loadError && <p className="oauth-accounts-empty">暂无可连接的账号。</p>}</div>}
    <Dialog.Root open={confirming !== null} onOpenChange={(open) => { if (!open) setConfirming(null); }}><Dialog.Portal><Dialog.Overlay className="overlay" /><Dialog.Content className="dialog oauth-disconnect-dialog"><Dialog.Close className="dialog-close" aria-label="关闭"><X size={18} /></Dialog.Close><Dialog.Title>解除账号关联？</Dialog.Title><Dialog.Description>解除后，将无法再通过 {confirming ? providerNames[confirming] : "该平台"} 登录此账号。</Dialog.Description><div className="dialog-actions"><Dialog.Close asChild><button type="button" className="secondary">取消</button></Dialog.Close><button type="button" className="primary" onClick={() => { const key = confirming; setConfirming(null); if (key) void act(key, true); }}>解除关联</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}

export function AccountPassword({ user, onUser, onMessage }: { user: User; onUser: (user: User) => void; onMessage: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const enabled = user.password_auth_enabled;
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const password = String(values.get("new_password"));
    if (password !== values.get("confirm_password")) { setError("两次输入的新密码不一致。"); return; }
    setBusy(true); setError(null);
    try {
      if (enabled) await api.changePassword(String(values.get("current_password")), password);
      else await api.setPassword(password);
      // 修改密码会轮换会话凭据，通过现有刷新流程恢复内存中的访问令牌。
      if (!await api.restoreTokens()) { location.reload(); return; }
      onUser({ ...user, password_auth_enabled: true }); form.reset();
      onMessage(enabled ? "密码已更新，其他会话已被撤销" : "密码已设置，可使用用户名和密码登录");
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  };
  return <section className="personal-settings-section"><h2>{enabled ? "修改密码" : "设置登录密码"}</h2><form className="personal-settings-list" onSubmit={submit}>
    {!enabled && <p>设置密码后，也可使用用户名「{user.username}」登录。</p>}
    {enabled && <label className="personal-setting-row"><span className="personal-setting-copy"><b>当前密码</b></span><span className="personal-setting-control"><input name="current_password" type="password" autoComplete="current-password" required disabled={busy} /></span></label>}
    <label className="personal-setting-row"><span className="personal-setting-copy"><b>新密码</b></span><span className="personal-setting-control"><input name="new_password" type="password" autoComplete="new-password" required minLength={6} maxLength={256} disabled={busy} placeholder="6 至 256 个字符" /></span></label>
    <label className="personal-setting-row"><span className="personal-setting-copy"><b>确认新密码</b></span><span className="personal-setting-control"><input name="confirm_password" type="password" autoComplete="new-password" required minLength={6} maxLength={256} disabled={busy} /></span></label>
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="personal-setting-actions"><button className="primary" disabled={busy}>{busy ? "保存中…" : enabled ? "修改密码" : "设置密码"}</button></div>
  </form></section>;
}
