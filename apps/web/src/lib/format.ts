export const pct = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

export const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export const hours = (minutes: number) => `${(minutes / 60).toFixed(minutes < 600 ? 1 : 0)} h`;

export const LANGUAGE_LABEL: Record<string, string> = { en: "English", fr: "Français", zh: "中文", pa: "ਪੰਜਾਬੀ" };
