import type { LlmMode } from "./types";

const VENDOR: Record<string, string> = { anthropic: "Claude", gemini: "Gemini via Vertex AI" };

/** The active real model vendor, or null in mock mode. */
export function llmVendor(mode: string | undefined): string | null {
  return (mode && VENDOR[mode]) || null;
}

export function isLiveLlm(mode: LlmMode | string | undefined): boolean {
  return llmVendor(mode) !== null;
}
