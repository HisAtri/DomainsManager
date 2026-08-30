import { solveChallengeWorkers, type Challenge } from "altcha/lib";
import Pbkdf2Worker from "../node_modules/altcha/dist/workers/pbkdf2.js?worker";
import { AlertCircle, CheckCircle2, LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export type PowOperation = "create_domain" | "refresh_domain";
export type PowState = "idle" | "solving" | "verified" | "failed";

const TIMEOUT_MS = 90_000;

function abortedError() {
  return new DOMException("Aborted", "AbortError");
}

function isAbortError(error: unknown) {
  return (error instanceof DOMException && error.name === "AbortError") || (error instanceof Error && error.name === "AbortError");
}

export async function solvePow(operation: PowOperation, signal: AbortSignal): Promise<string> {
  if (signal.aborted) throw abortedError();
  const challenge = await api.powChallenge(operation) as unknown as Challenge;
  if (signal.aborted) throw abortedError();
  if (challenge.parameters?.algorithm !== "PBKDF2/SHA-256") {
    throw new Error("不支持的校验算法");
  }
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  signal.addEventListener("abort", onAbort);
  try {
    const solution = await solveChallengeWorkers({
      challenge,
      controller,
      concurrency: Math.min(navigator.hardwareConcurrency || 2, 4),
      timeout: TIMEOUT_MS,
      createWorker: () => new Pbkdf2Worker(),
    });
    if (signal.aborted) throw abortedError();
    if (!solution) throw new Error("安全校验超时，请重试");
    return btoa(JSON.stringify({ challenge, solution }));
  } finally {
    signal.removeEventListener("abort", onAbort);
  }
}

export function usePowChallenge({ operation, enabled, active }: { operation: PowOperation; enabled: boolean; active: boolean }) {
  const [state, setState] = useState<PowState>("idle");
  const [payload, setPayload] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!enabled || !active) {
      setState("idle");
      setPayload(null);
      return;
    }
    const controller = new AbortController();
    setState("solving");
    setPayload(null);
    solvePow(operation, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setPayload(value);
      setState("verified");
    }, (error: unknown) => {
      if (controller.signal.aborted || isAbortError(error)) return;
      setPayload(null);
      setState("failed");
    });
    return () => controller.abort();
  }, [operation, enabled, active, generation]);

  const retry = useCallback(() => setGeneration((value) => value + 1), []);
  return { state: enabled && active ? state : "idle" as PowState, payload, ready: !enabled || state === "verified", retry };
}

export function PowStatus({ state, onRetry }: { state: PowState; onRetry: () => void }) {
  if (state === "idle") return null;
  if (state === "solving") return <div className="pow-status" role="status"><LoaderCircle className="spin" size={16} />正在进行安全校验</div>;
  if (state === "verified") return <div className="pow-status pow-status-verified"><CheckCircle2 size={16} />安全校验已通过</div>;
  return <div className="pow-status pow-status-failed" role="alert"><AlertCircle size={16} />安全校验失败<button type="button" onClick={onRetry}>重试</button></div>;
}
