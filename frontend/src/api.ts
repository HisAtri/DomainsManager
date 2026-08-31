import type { AdminCheckPage, AdminDomain, AdminDomainPage, AdminSession, AdminUser, AdminUserPage, AuthResult, Check, Domain, DomainStats, GlobalSetting, NotificationDelivery, NotificationRule, NotificationRuleInput, NotificationRuleUpdate, OperationalMetrics, Page, PublicSiteConfig, SecurityAuditEvent, Settings, Task, Tokens, User } from "./types";

type ApiErrorDetail = { location?: string; message?: string; code?: string };
type ApiErrorBody = { code?: string; message?: string; details?: ApiErrorDetail[]; request_id?: string };

const errorMessages: Record<string, string> = {
  account_banned: "账号已被禁用。",
  configuration_encryption_unavailable: "服务器尚未配置设置加密密钥，无法保存密码设置。",
  domain_already_managed: "该域名已在您的列表中。",
  idempotency_conflict: "该操作与先前请求冲突，请刷新后重试。",
  invalid_credentials: "用户名或密码不正确。",
  invalid_domain: "域名格式不正确。",
  invalid_token: "登录已失效，请重新登录。",
  not_found: "未找到相关资源。",
  password_mismatch: "当前密码不正确。",
  password_reused: "新密码不能与当前密码相同。",
  precondition_required: "记录已更新，请刷新后重试。",
  refresh_token_replayed: "登录已失效，请重新登录。",
  registration_disabled: "当前不允许注册新账号。",
  subdomain_not_supported: "仅支持可注册域名，不支持子域名。",
  username_taken: "该用户名已被使用。",
  version_conflict: "记录已被更新，请刷新后重试。",
  anti_bot_verification_failed: "安全校验失败，请重试。",
};

const validationMessages: Record<string, string> = {
  "body.email": "邮箱格式不正确。",
  "body.name": "域名格式不正确。",
  "body.password": "密码长度需为 6 至 256 个字符。",
  "body.new_password": "新密码长度需为 6 至 256 个字符。",
  "body.reason": "封禁原因至少需 3 个字符。",
  "body.username": "用户名格式不正确。",
};

const validationMessageTexts: Record<string, string> = {
  "duplicate setting keys": "设置项不能重复。",
  "email is required": "请输入邮箱地址。",
  "no fields supplied": "请至少修改一项内容。",
  "no settings supplied": "请至少修改一项设置。",
  "setting value conflicts with the active runtime policy": "设置组合不符合当前运行规则。",
  "setting value is invalid": "设置值无效。",
  "settings conflict with the active runtime policy": "设置组合不符合当前运行规则。",
};

function messageForError(status: number, code?: string, message?: string, details: ApiErrorDetail[] = []) {
  if (code && errorMessages[code]) return errorMessages[code];
  if (code === "validation_error") {
    const detailMessage = details.map((detail) => detail.location && validationMessages[detail.location]).find(Boolean);
    return detailMessage || (message && validationMessageTexts[message]) || "输入内容不符合要求。";
  }
  if (status === 401) return "登录已失效，请重新登录。";
  if (status === 403) return "您没有执行此操作的权限。";
  if (status === 404) return "未找到相关资源。";
  if (status === 429) return "请求过于频繁，请稍后再试。";
  if (status >= 500) return "服务暂时不可用，请稍后再试。";
  return message || "请求未完成。";
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public code?: string, public details: ApiErrorDetail[] = [], public requestId?: string) { super(message); }
}

class ApiClient {
  private tokens: Tokens | null = null;
  private refreshInFlight: Promise<Tokens> | null = null;
  setTokens(tokens: Tokens | null) { this.tokens = tokens; }
  async restoreTokens() { try { const tokens = await this.refresh(); this.setTokens(tokens); return tokens; } catch { this.setTokens(null); return null; } }
  private async request<T>(path: string, init: RequestInit = {}, retry = true): Promise<{ data: T; etag: string | null }> {
    const headers = new Headers(init.headers); headers.set("Accept", "application/json");
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (this.tokens) headers.set("Authorization", `Bearer ${this.tokens.access_token}`);
    let response = await fetch(`/api/v1${path}`, { ...init, headers });
    if (response.status === 401 && retry && path !== "/auth/token/refresh") {
      try { const refreshed = await this.refresh(); this.setTokens(refreshed); return this.request(path, init, false); } catch { this.setTokens(null); }
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as ApiErrorBody;
      const details = Array.isArray(body.details) ? body.details : [];
      throw new ApiError(response.status, messageForError(response.status, body.code, body.message, details), body.code, details, body.request_id);
    }
    return { data: response.status === 204 ? undefined as T : await response.json() as T, etag: response.headers.get("ETag") };
  }
  private async refresh() {
    if (this.refreshInFlight) return this.refreshInFlight;
    this.refreshInFlight = this.withRefreshLock(async () => {
      const result = await this.request<Tokens>("/auth/token/refresh", { method: "POST" }, false);
      return result.data;
    });
    try { return await this.refreshInFlight; } finally { this.refreshInFlight = null; }
  }
  private async withRefreshLock<T>(action: () => Promise<T>): Promise<T> {
    if (!navigator.locks) return action();
    return navigator.locks.request("domainsmanager.refresh-token", { mode: "exclusive" }, action);
  }
  captcha(operation: "login" | "register") { return this.request<{ image: string; token: string }>(`/anti-bot/captcha?operation=${operation}`, {}, false).then((r) => r.data); }
  powChallenge(operation: "create_domain" | "refresh_domain") { return this.request<Record<string, unknown>>(`/anti-bot/pow?operation=${operation}`, {}, false).then((r) => r.data); }
  login(username: string, password: string, captcha?: { token: string; answer: string }, turnstileToken?: string) { const body = new URLSearchParams({ username, password, ...(captcha ? { captcha_token: captcha.token, captcha_answer: captcha.answer } : {}), ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) }); return this.request<AuthResult>("/auth/login", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body }).then((r) => r.data); }
  register(username: string, password: string, email?: string, captcha?: { token: string; answer: string }, turnstileToken?: string) { return this.request<AuthResult>("/auth/register", { method: "POST", body: JSON.stringify({ username, password, email: email || null, ...(captcha ? { captcha_token: captcha.token, captcha_answer: captcha.answer } : {}), ...(turnstileToken ? { turnstile_token: turnstileToken } : {}) }) }).then((r) => r.data); }
  async logout() { await this.request<void>("/auth/logout", { method: "POST" }, false).catch(() => undefined); this.setTokens(null); }
  me() { return this.request<User>("/auth/me").then((r) => r.data); }
  updateMe(email: string) { return this.request<User>("/auth/me", { method: "PATCH", body: JSON.stringify({ email: email || null }) }).then((r) => r.data); }
  changePassword(current_password: string, new_password: string) { return this.request<void>("/auth/me/password", { method: "POST", body: JSON.stringify({ current_password, new_password }) }); }
  settings() { return this.request<Settings>("/auth/me/settings").then((r) => r.data); }
  updateSettings(values: Partial<Settings>) { return this.request<Settings>("/auth/me/settings", { method: "PATCH", body: JSON.stringify(values) }).then((r) => r.data); }
  domains(params: Record<string, string | number | boolean | undefined> = {}) { return this.request<Page<Domain>>(`/domains?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  domainStats() { return this.request<DomainStats>("/domains/stats").then((r) => r.data); }
  domain(id: string) { return this.request<Domain>(`/domains/${id}`); }
  createDomain(name: string, monitor_enabled: boolean, pow_payload?: string, turnstile_token?: string) { return this.request<{ domain: Domain }>("/domains", { method: "POST", body: JSON.stringify({ name, monitor_enabled, ...(pow_payload ? { pow_payload } : {}), ...(turnstile_token ? { turnstile_token } : {}) }) }).then((r) => r.data.domain); }
  updateDomain(id: string, version: number, values: Partial<Pick<Domain, "monitor_enabled" | "renewal_mode" | "notes">>) { return this.request<Domain>(`/domains/${id}`, { method: "PATCH", headers: { "If-Match": `"${version}"` }, body: JSON.stringify(values) }); }
  deleteDomain(id: string) { return this.request<void>(`/domains/${id}`, { method: "DELETE" }); }
  refreshDomain(id: string, force_refresh = false, pow_payload?: string, turnstile_token?: string) { return this.request<Task>(`/domains/${id}/refresh`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ force_refresh, ...(pow_payload ? { pow_payload } : {}), ...(turnstile_token ? { turnstile_token } : {}) }) }).then((r) => r.data); }
  checks(id: string, params: Record<string, string | number | undefined> = {}) { return this.request<Page<Check>>(`/domains/${id}/checks?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  tasks(params: Record<string, string | number | undefined> = {}) { return this.request<Page<Task>>(`/tasks?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  task(id: string) { return this.request<Task>(`/tasks/${id}`).then((r) => r.data); }
  notificationRules() { return this.request<NotificationRule[]>("/notification-rules").then((r) => r.data); }
  notificationRule(id: string) { return this.request<NotificationRule>(`/notification-rules/${id}`).then((r) => r.data); }
  createNotificationRule(values: NotificationRuleInput) { return this.request<NotificationRule>("/notification-rules", { method: "POST", body: JSON.stringify(values) }).then((r) => r.data); }
  updateNotificationRule(id: string, values: NotificationRuleUpdate) { return this.request<NotificationRule>(`/notification-rules/${id}`, { method: "PATCH", body: JSON.stringify(values) }).then((r) => r.data); }
  deleteNotificationRule(id: string) { return this.request<void>(`/notification-rules/${id}`, { method: "DELETE" }); }
  notificationDeliveries(limit = 50) { return this.request<NotificationDelivery[]>(`/notification-rules/deliveries?limit=${limit}`).then((r) => r.data); }
  adminUsers(params: Record<string, string | number | undefined> = {}) { return this.request<AdminUserPage>(`/admin/users?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  adminUser(id: string) { return this.request<AdminUser>(`/admin/users/${id}`).then((r) => r.data); }
  updateAdminUser(id: string, email: string | null) { return this.request<AdminUser>(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify({ email }) }).then((r) => r.data); }
  banUser(id: string, reason: string) { return this.request<AdminUser>(`/admin/users/${id}/ban`, { method: "POST", body: JSON.stringify({ reason }) }).then((r) => r.data); }
  unbanUser(id: string) { return this.request<AdminUser>(`/admin/users/${id}/unban`, { method: "POST" }).then((r) => r.data); }
  adminSessions(id: string) { return this.request<Page<AdminSession>>(`/admin/users/${id}/sessions`).then((r) => r.data); }
  revokeAdminSession(userId: string, sessionId: string) { return this.request<AdminSession>(`/admin/users/${userId}/sessions/${sessionId}/revoke`, { method: "POST" }).then((r) => r.data); }
  adminDomains(params: Record<string, string | number | undefined> = {}) { return this.request<AdminDomainPage>(`/admin/domains?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  adminDomain(id: string) { return this.request<AdminDomain>(`/admin/domains/${id}`).then((r) => r.data); }
  updateAdminDomain(id: string, version: number, values: Partial<Pick<AdminDomain, "monitor_enabled" | "renewal_mode" | "notes">>) { return this.request<AdminDomain>(`/admin/domains/${id}`, { method: "PATCH", headers: { "If-Match": `"${version}"` }, body: JSON.stringify(values) }).then((r) => r.data); }
  deleteAdminDomain(id: string) { return this.request<void>(`/admin/domains/${id}`, { method: "DELETE", headers: { "X-Confirm-Action": "soft-delete" } }); }
  refreshAdminDomain(id: string) { return this.request<Task>(`/admin/domains/${id}/refresh`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } }).then((r) => r.data); }
  adminChecks(params: Record<string, string | number | undefined> = {}) { return this.request<AdminCheckPage>(`/admin/domain-checks?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([key, value]) => [key, String(value)])).toString()}`).then((r) => r.data); }
  operationalMetrics() { return this.request<OperationalMetrics>("/admin/operations/metrics").then((r) => r.data); }
  securityAuditEvents(params: Record<string, string | number | undefined> = {}) { return this.request<Page<SecurityAuditEvent>>(`/admin/security-audit-events?${new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined).map(([key, value]) => [key, String(value)])).toString()}`).then((r) => r.data); }
  globalSettings() { return this.request<GlobalSetting[]>("/admin/settings").then((r) => r.data); }
  updateGlobalSetting(key: string, value: unknown, version: number) { return this.request<GlobalSetting>(`/admin/settings/${key}`, { method: "PUT", headers: { "If-Match": String(version) }, body: JSON.stringify({ value }) }).then((r) => r.data); }
  updateGlobalSettings(settings: { key: string; value: unknown; version: number }[]) { return this.request<GlobalSetting[]>("/admin/settings", { method: "PUT", body: JSON.stringify({ settings }) }).then((r) => r.data); }
  sendTestEmail(email: string) { return this.request<{ status: string }>("/admin/settings/test-email", { method: "POST", body: JSON.stringify({ email }) }).then((r) => r.data); }
  confirmEmailVerification(token: string) { return this.request<{ status: string; email: string | null }>("/auth/email-verifications/confirm", { method: "POST", body: JSON.stringify({ token }) }).then((r) => r.data); }
  resendEmailVerification() { return this.request<{ status: string; pending_email: string | null }>("/auth/me/email-verifications/resend", { method: "POST" }).then((r) => r.data); }
  siteConfig() { return this.request<PublicSiteConfig>("/site/config", { method: "GET" }, false).then((r) => r.data); }
}
export const api = new ApiClient();
