/**
 * Template tags (design DS-24). The worker computes real values per message; this module lists the tags
 * for the reference table and renders *sample* values for editor previews.
 */
export interface TemplateTag {
  tag: string;
  description: string;
  input: string | null;
  output: string;
}

export const TEMPLATE_TAGS: TemplateTag[] = [
  { tag: "#USERID#", description: "Part of the recipient's email before the @", input: "mahdi@gmail.com", output: "mahdi" },
  { tag: "#RANDOM#", description: "7-digit random number (from a UUID)", input: null, output: "5832147" },
  { tag: "#EMAIL#", description: "The recipient's full email address", input: "mahdi@gmail.com", output: "mahdi@gmail.com" },
  { tag: "#SUBSID#", description: "10-character uppercase ID (from a UUID)", input: null, output: "A7F23B91C2" },
  { tag: "#INVOICE#", description: "7-character uppercase invoice ID (from a UUID)", input: null, output: "9F3A72B" },
  { tag: "#REF#", description: "8-character uppercase reference ID (from a UUID)", input: null, output: "A82C91F4" },
  { tag: "#HASH#", description: "32-byte secure random hexadecimal hash", input: null, output: "a4f82c9e1b7d4f…" },
  { tag: "#DATE#", description: "Current date (send time)", input: null, output: "2026-09-23" },
  { tag: "#TIME#", description: "Current time (send time)", input: null, output: "16:59:42" },
  { tag: "#OTP#", description: "6-digit random OTP", input: null, output: "583921" },
  { tag: "#$$#", description: "Random 2-digit number between 10 and 99", input: null, output: "47" },
  { tag: "#MASSAGE#", description: "Random message from the campaign's message list", input: '["Hello", "Hi", "Welcome"]', output: "Welcome" },
];

const TAG_RE = /#(USERID|RANDOM|EMAIL|SUBSID|INVOICE|REF|HASH|DATE|TIME|OTP|\$\$|MASSAGE|MESSAGE)#/g;

const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** Replace tags with sample values for a preview. Values are HTML-escaped in "html" mode. */
export function applySampleTags(text: string, opts: { email?: string; messages?: string[]; mode?: "html" | "text" } = {}): string {
  const email = opts.email ?? "mahdi@gmail.com";
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const sample: Record<string, string> = {
    USERID: email.split("@")[0],
    EMAIL: email,
    RANDOM: "5832147",
    SUBSID: "A7F23B91C2",
    INVOICE: "9F3A72B",
    REF: "A82C91F4",
    HASH: "a4f82c9e1b7d4f0e8c3b5a6d7e9f10a2b3c4d5e6f708192a3b4c5d6e7f8091a2",
    DATE: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`,
    TIME: `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`,
    OTP: "583921",
    $$: "47",
    MASSAGE: opts.messages?.find((m) => m.trim()) ?? "",
  };
  sample.MESSAGE = sample.MASSAGE;
  return text.replace(TAG_RE, (_, name: string) => (opts.mode === "html" ? escapeHtml(sample[name] ?? "") : (sample[name] ?? "")));
}

/** Copy text to the clipboard, with a fallback for browsers/contexts without the async Clipboard API. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  try {
    const el = document.createElement("textarea");
    el.value = text;
    el.setAttribute("readonly", "");
    el.style.position = "fixed";
    el.style.opacity = "0";
    document.body.appendChild(el);
    el.select();
    const ok = document.execCommand("copy");
    el.remove();
    return ok;
  } catch {
    return false;
  }
}
