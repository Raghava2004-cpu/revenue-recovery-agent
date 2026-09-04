/* Indian-numbering formatters, shared so ₹ never renders two different ways. */

export const inr = (n) => "₹" + Math.round(n ?? 0).toLocaleString("en-IN");

/** Compact lakh/crore form, which is how these figures are actually spoken. */
export function inrShort(n) {
  const v = Math.abs(n ?? 0);
  const sign = n < 0 ? "−" : "";
  if (v >= 1e7) return `${sign}₹${(v / 1e7).toFixed(2)} Cr`;
  if (v >= 1e5) return `${sign}₹${(v / 1e5).toFixed(2)} L`;
  return `${sign}₹${Math.round(v).toLocaleString("en-IN")}`;
}

export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export const titleise = (s) =>
  String(s ?? "").replaceAll("_", " ").toLowerCase();

export const when = (s) =>
  s
    ? new Date(s).toLocaleString("en-IN", {
        day: "2-digit", month: "short", hour: "2-digit",
        minute: "2-digit", hour12: false,
      })
    : "—";

export const STATUS_TONE = {
  recovered: "b-good",
  escalated: "b-warning",
  suppressed: "b-danger",
  exhausted: "b-muted",
  in_recovery: "b-brand",
  detected: "b-muted",
};
