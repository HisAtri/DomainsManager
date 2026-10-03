import { LoaderCircle } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError } from "./api";
import type { User } from "./types";

export function UsernameOnboarding({ brand, onComplete, onLogout }: { brand: ReactNode; onComplete: (user: User) => void; onLogout: () => void }) {
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(null);
    try {
      onComplete(await api.setInitialUsername(username.trim()));
    } catch (problem) {
      // 另一标签页可能已完成引导，恢复最新用户即可，不让用户陷入无法提交的页面。
      if (problem instanceof ApiError && problem.code === "username_already_set") {
        try { onComplete(await api.me()); } catch { setError("暂时无法继续，请稍后重试。"); }
      } else {
        setError(problem instanceof ApiError && problem.code === "username_taken" ? "这个用户名已被使用，请换一个。" : "用户名未保存，请稍后重试。");
      }
    } finally { setBusy(false); }
  };
  return <main className="auth"><section className="auth-card username-onboarding">
    {brand}<h1>设置你的用户名</h1><p>这是你在本站的用户名，确认后无法修改。</p>
    <form onSubmit={submit}>
      <label htmlFor="initial-username">用户名<input id="initial-username" name="username" value={username} onChange={(event) => { setUsername(event.target.value); setError(null); }} required minLength={3} maxLength={128} pattern="[A-Za-z0-9_.\-]+" autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus disabled={busy} aria-describedby="username-help" aria-invalid={Boolean(error)} placeholder="输入你想使用的用户名" /></label>
      <p id="username-help" className="username-help">3–128 个字符，支持字母、数字、点、下划线和短横线。</p>
      {error && <div className="form-error" role="alert">{error}</div>}
      <button type="submit" className="primary wide" disabled={busy || !username.trim()}>{busy && <LoaderCircle className="spin" size={17} />}{busy ? "保存中…" : "确认并继续"}</button>
    </form>
    <button type="button" className="auth-switch" disabled={busy} onClick={onLogout}>退出登录</button>
  </section></main>;
}
