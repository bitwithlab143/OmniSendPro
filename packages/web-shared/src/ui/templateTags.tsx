import { Check, Copy, CornerDownLeft } from "lucide-react";
import { useState } from "react";

import { cn } from "../lib/cn";
import { TEMPLATE_TAGS, copyText } from "../lib/templateTags";
import { Button } from "./button";

/** "Available tags" reference table (design DS-24) with copy-to-clipboard and optional insert. */
export function TemplateTagsTable({ onInsert, compact, className }: { onInsert?: (tag: string) => void; compact?: boolean; className?: string }) {
  const [copied, setCopied] = useState<string | null>(null);
  const copy = async (key: string, text: string) => {
    if (await copyText(text)) {
      setCopied(key);
      setTimeout(() => setCopied((c) => (c === key ? null : c)), 1500);
    }
  };
  const allText = TEMPLATE_TAGS.map((t) => `${t.tag}\t${t.description}\t${t.output}`).join("\n");

  return (
    <div className={cn("overflow-hidden rounded-xl border border-border", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-border bg-surface-2 px-4 py-2.5">
        <div>
          <div className="text-sm font-semibold">Available tags</div>
          <div className="text-xs text-fg-muted">Work in the subject and in HTML/text. Each message gets its own values.</div>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label={copied === "*" ? "Copied all tags" : "Copy all tags"} title="Copy all" onClick={() => void copy("*", allText)}>
          {copied === "*" ? <Check /> : <Copy />}
        </Button>
      </div>
      <span className="sr-only" aria-live="polite">
        {copied && copied !== "*" ? `${copied} copied` : ""}
      </span>
      <div className="max-h-[28rem] overflow-auto">
        <table className="w-full text-left text-sm">
          <thead className="sticky top-0 bg-surface text-xs text-fg-muted">
            <tr>
              <th scope="col" className="px-4 py-2 font-medium">Tag</th>
              <th scope="col" className="px-2 py-2 font-medium">What it does</th>
              {!compact && <th scope="col" className="px-2 py-2 font-medium">Example input</th>}
              {!compact && <th scope="col" className="px-4 py-2 font-medium">Example output</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {TEMPLATE_TAGS.map((t) => (
              <tr key={t.tag} className="align-top">
                <td className="whitespace-nowrap px-4 py-2">
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => void copy(t.tag, t.tag)}
                      className="inline-flex items-center gap-1 rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-fg hover:bg-primary-soft hover:text-primary focus-visible:outline-2 focus-visible:outline-primary"
                      aria-label={`Copy ${t.tag}`}
                      title="Copy"
                    >
                      {t.tag}
                      {copied === t.tag ? <Check className="size-3" /> : <Copy className="size-3 opacity-60" />}
                    </button>
                    {onInsert && (
                      <button
                        type="button"
                        onClick={() => onInsert(t.tag)}
                        className="rounded p-0.5 text-fg-muted hover:text-primary focus-visible:outline-2 focus-visible:outline-primary"
                        aria-label={`Insert ${t.tag}`}
                        title="Insert at cursor"
                      >
                        <CornerDownLeft className="size-3.5" />
                      </button>
                    )}
                  </div>
                </td>
                <td className="px-2 py-2 text-fg-secondary">
                  {t.description}
                  {compact && <div className="mt-0.5 font-mono text-xs text-fg-muted">e.g. {t.output}</div>}
                </td>
                {!compact && <td className="px-2 py-2 font-mono text-xs text-fg-muted">{t.input ?? "—"}</td>}
                {!compact && <td className="px-4 py-2 font-mono text-xs">{t.output}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
