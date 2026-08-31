import { useEffect, useRef, useState } from "react";

export type TurnstileAction = "register" | "login" | "create_domain" | "refresh_domain";

type TurnstileApi = {
  render: (container: HTMLElement, options: {
    sitekey: string;
    action: TurnstileAction;
    language: string;
    theme: "auto";
    callback: (token: string) => void;
    "expired-callback": () => void;
    "error-callback": () => void;
  }) => string;
  remove: (widgetId: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const SCRIPT_SOURCE = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let scriptPromise: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (scriptPromise) return scriptPromise;
  const promise = new Promise<TurnstileApi>((resolve, reject) => {
    const ready = () => window.turnstile ? resolve(window.turnstile) : reject(new Error("Turnstile 加载失败"));
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT_SOURCE}"]`);
    if (existing) {
      existing.addEventListener("load", ready, { once: true });
      existing.addEventListener("error", () => reject(new Error("Turnstile 加载失败")), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = SCRIPT_SOURCE;
    script.async = true;
    script.defer = true;
    script.addEventListener("load", ready, { once: true });
    script.addEventListener("error", () => reject(new Error("Turnstile 加载失败")), { once: true });
    document.head.appendChild(script);
  }).catch((error) => {
    scriptPromise = null;
    throw error;
  });
  scriptPromise = promise;
  return promise;
}

export function TurnstileChallenge({ siteKey, action, onChange }: {
  siteKey: string;
  action: TurnstileAction;
  onChange: (token: string | null) => void;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const element = container.current;
    let widgetId: string | null = null;
    let cancelled = false;
    onChange(null);
    setError(null);
    if (!siteKey) {
      setError("Turnstile Site key 未配置");
      return;
    }
    loadTurnstile().then((turnstile) => {
      if (cancelled || !element) return;
      widgetId = turnstile.render(element, {
        sitekey: siteKey,
        action,
        language: "zh-cn",
        theme: "auto",
        callback: (token) => { setError(null); onChange(token); },
        "expired-callback": () => onChange(null),
        "error-callback": () => { onChange(null); setError("Turnstile 校验失败，请重试"); },
      });
    }).catch(() => {
      if (!cancelled) setError("Turnstile 加载失败，请检查网络后重试");
    });
    return () => {
      cancelled = true;
      onChange(null);
      if (widgetId && window.turnstile) window.turnstile.remove(widgetId);
    };
  }, [action, onChange, siteKey]);

  return <div className="turnstile-challenge"><div ref={container} />{error && <div className="form-error" role="alert">{error}</div>}</div>;
}
