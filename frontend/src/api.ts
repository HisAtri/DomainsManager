import type { AdminCheckPage, AdminDomain, AdminSession, AdminUser, AuthResult, Check, Domain, GlobalSetting, NotificationDelivery, NotificationRule, NotificationRuleInput, NotificationRuleUpdate, Page, Settings, Task, Tokens, User } from "./types";

type ApiErrorBody = { detail?: { code?: string; message?: string } | string; message?: string };
export class ApiError extends Error { constructor(public status: number, message: string, public code?: string) { super(message); } }

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
    if (!response.ok) { const body = await response.json().catch(() => ({})) as ApiErrorBody; const detail = typeof body.detail === "object" ? body.detail : undefined; throw new ApiError(response.status, detail?.code === "configuration_encryption_unavailable" ? "服务器尚未配置设置加密密钥，暂时无法保存密码设置。" : detail?.code === "version_conflict" ? "设置已被其他管理员修改，请刷新后重试。" : "操作未完成，请检查输入后重试。", detail?.code); }
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
  login(username: string, password: string) { const body = new URLSearchParams({ username, password }); return this.request<AuthResult>("/auth/login", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body }).then((r) => r.data); }
  register(username: string, password: string, email?: string) { return this.request<AuthResult>("/auth/register", { method: "POST", body: JSON.stringify({ username, password, email: email || null }) }).then((r) => r.data); }
  async logout() { await this.request<void>("/auth/logout", { method: "POST" }, false).catch(() => undefined); this.setTokens(null); }
  me() { return this.request<User>("/auth/me").then((r) => r.data); }
  updateMe(email: string) { return this.request<User>("/auth/me", { method: "PATCH", body: JSON.stringify({ email: email || null }) }).then((r) => r.data); }
  changePassword(current_password: string, new_password: string) { return this.request<void>("/auth/me/password", { method: "POST", body: JSON.stringify({ current_password, new_password }) }); }
  settings() { return this.request<Settings>("/auth/me/settings").then((r) => r.data); }
  updateSettings(values: Partial<Settings>) { return this.request<Settings>("/auth/me/settings", { method: "PATCH", body: JSON.stringify(values) }).then((r) => r.data); }
  domains(params: Record<string, string | number | boolean | undefined> = {}) { return this.request<Page<Domain>>(`/domains?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  domain(id: string) { return this.request<Domain>(`/domains/${id}`); }
  createDomain(name: string, monitor_enabled: boolean) { return this.request<{ domain: Domain }>("/domains", { method: "POST", body: JSON.stringify({ name, monitor_enabled }) }).then((r) => r.data.domain); }
  updateDomain(id: string, version: number, values: Partial<Pick<Domain, "monitor_enabled" | "renewal_mode" | "notes">>) { return this.request<Domain>(`/domains/${id}`, { method: "PATCH", headers: { "If-Match": `"${version}"` }, body: JSON.stringify(values) }); }
  deleteDomain(id: string) { return this.request<void>(`/domains/${id}`, { method: "DELETE" }); }
  refreshDomain(id: string, force_refresh = false) { return this.request<Task>(`/domains/${id}/refresh`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ force_refresh }) }).then((r) => r.data); }
  checks(id: string, params: Record<string, string | number | undefined> = {}) { return this.request<Page<Check>>(`/domains/${id}/checks?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  tasks(params: Record<string, string | number | undefined> = {}) { return this.request<Page<Task>>(`/tasks?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  task(id: string) { return this.request<Task>(`/tasks/${id}`).then((r) => r.data); }
  notificationRules() { return this.request<NotificationRule[]>("/notification-rules").then((r) => r.data); }
  notificationRule(id: string) { return this.request<NotificationRule>(`/notification-rules/${id}`).then((r) => r.data); }
  createNotificationRule(values: NotificationRuleInput) { return this.request<NotificationRule>("/notification-rules", { method: "POST", body: JSON.stringify(values) }).then((r) => r.data); }
  updateNotificationRule(id: string, values: NotificationRuleUpdate) { return this.request<NotificationRule>(`/notification-rules/${id}`, { method: "PATCH", body: JSON.stringify(values) }).then((r) => r.data); }
  deleteNotificationRule(id: string) { return this.request<void>(`/notification-rules/${id}`, { method: "DELETE" }); }
  notificationDeliveries(limit = 50) { return this.request<NotificationDelivery[]>(`/notification-rules/deliveries?limit=${limit}`).then((r) => r.data); }
  adminUsers(params: Record<string, string | number | undefined> = {}) { return this.request<Page<AdminUser>>(`/admin/users?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  banUser(id: string, reason: string) { return this.request<AdminUser>(`/admin/users/${id}/ban`, { method: "POST", body: JSON.stringify({ reason }) }).then((r) => r.data); }
  unbanUser(id: string) { return this.request<AdminUser>(`/admin/users/${id}/unban`, { method: "POST" }).then((r) => r.data); }
  adminSessions(id: string) { return this.request<Page<AdminSession>>(`/admin/users/${id}/sessions`).then((r) => r.data); }
  revokeAdminSession(userId: string, sessionId: string) { return this.request<AdminSession>(`/admin/users/${userId}/sessions/${sessionId}/revoke`, { method: "POST" }).then((r) => r.data); }
  adminDomains(params: Record<string, string | number | undefined> = {}) { return this.request<Page<AdminDomain>>(`/admin/domains?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}`).then((r) => r.data); }
  adminChecks() { return this.request<AdminCheckPage>("/admin/domain-checks").then((r) => r.data); }
  globalSettings() { return this.request<GlobalSetting[]>("/admin/settings").then((r) => r.data); }
  updateGlobalSetting(key: string, value: number | boolean | string | null, version: number) { return this.request<GlobalSetting>(`/admin/settings/${key}`, { method: "PUT", headers: { "If-Match": String(version) }, body: JSON.stringify({ value }) }).then((r) => r.data); }
  updateGlobalSettings(settings: { key: string; value: number | boolean | string | null; version: number }[]) { return this.request<GlobalSetting[]>("/admin/settings", { method: "PUT", body: JSON.stringify({ settings }) }).then((r) => r.data); }
}
export const api = new ApiClient();
