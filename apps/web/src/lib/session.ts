// This browser's demo session id, sent as X-Demo-Session on every request (lib/api.ts): the server records it on
// each AI run a click of this browser caused, for "What the AI did in this demo" (lib/ai.ts). Kept across role
// switches; not an identity.

const SESSION_KEY = "ioc.demoSession";
let memorySession: string | null = null;

/** This browser's demo session id (kept across role switches; not an identity, just "this browser"). */
export function demoSession(): string {
  try {
    let id = localStorage.getItem(SESSION_KEY);
    if (!id) {
      id = `s-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2)}`.slice(0, 40);
      localStorage.setItem(SESSION_KEY, id);
    }
    return id;
  } catch {
    memorySession ??= `s-${Math.random().toString(36).slice(2)}`;
    return memorySession;
  }
}
