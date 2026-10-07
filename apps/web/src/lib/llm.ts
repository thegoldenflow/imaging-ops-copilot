import type { LlmMode } from "./types";

const VENDOR: Record<string, string> = { anthropic: "Claude", gemini: "Gemini" };

/** "Claude" or "Gemini" for a real provider, null in mock mode. */
export function llmVendor(mode: string | undefined): string | null {
  return (mode && VENDOR[mode]) || null;
}

export function isLiveLlm(mode: LlmMode | string | undefined): boolean {
  return llmVendor(mode) !== null;
}
