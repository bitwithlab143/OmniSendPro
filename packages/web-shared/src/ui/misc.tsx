import { AlertCircle, Check, Copy, Inbox, Loader2, Monitor, Moon, Sun } from "lucide-react";
import { useState, type ReactNode } from "react";

import { cn } from "../lib/cn";
import { applyTheme, getTheme, type ThemeChoice } from "../lib/theme";
import { Button } from "./button";

export function Spinner({ className, label = "Loading" }: { className?: string; label?: string }) {
  return (
    <span role="status" className={cn("inline-flex items-center gap-2 text-sm text-fg-muted", className)}>
      <Loader2 className="size-4 animate-spin" aria-hidden />
      <span>{label}…</span>
    </span>
  );
}

export function PageLoader() {
  return (
    <div className="flex h-64 items-center justify-center">
      <Spinner />
    </div>
  );
}

export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      <div className="mb-3 flex size-11 items-center justify-center rounded-full bg-surface-2 text-fg-muted [&_svg]:size-5">
        {icon ?? <Inbox />}
      </div>
      <h3 className="text-sm font-semibold text-fg">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-sm text-fg-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ message, onRetry }: { message: ReactNode; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      <div className="flex-1">{message}</div>
      {onRetry && (
        <button className="font-medium underline underline-offset-2" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Stat({ label, value, hint, icon, className }: { label: string; value: ReactNode; hint?: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <div className={cn("rounded-xl border border-border bg-surface p-4 shadow-card", className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-sm text-fg-secondary">{label}</span>
        {icon && <span className="text-fg-muted [&_svg]:size-4">{icon}</span>}
      </div>
      <div className="mt-2 text-2xl font-semibold tracking-tight text-fg">{value}</div>
      {hint && <div className="mt-1 text-xs text-fg-muted">{hint}</div>}
    </div>
  );
}

export function Progress({ value, className, label }: { value: number; className?: string; label?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label ?? "Progress"}
      className={cn("h-2 w-full overflow-hidden rounded-full bg-primary-soft", className)}
    >
      <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Meter: fill carries severity; track is a lighter step of the same ramp. */
export function Meter({ used, limit, label }: { used: number; limit: number | null | undefined; label: string }) {
  if (!limit) {
    return (
      <div className="text-sm text-fg-secondary">
        {label}: <span className="font-medium text-fg">{used.toLocaleString()}</span> <span className="text-fg-muted">(no limit)</span>
      </div>
    );
  }
  const ratio = used / limit;
  const fill = ratio >= 0.95 ? "bg-danger" : ratio >= 0.8 ? "bg-[var(--status-warning)]" : "bg-primary";
  const track = ratio >= 0.95 ? "bg-danger-soft" : ratio >= 0.8 ? "bg-warning-soft" : "bg-primary-soft";
  return (
    <div>
      <div className="mb-1.5 flex justify-between text-sm">
        <span className="text-fg-secondary">{label}</span>
        <span className="tabular text-fg">
          {used.toLocaleString()} / {limit.toLocaleString()}
        </span>
      </div>
      <div className={cn("h-2 overflow-hidden rounded-full", track)} role="meter" aria-valuenow={used} aria-valuemin={0} aria-valuemax={limit} aria-label={label}>
        <div className={cn("h-full rounded-full", fill)} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ value, onChange, options, className }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode; count?: number }[]; className?: string }) {
  return (
    <div role="tablist" className={cn("inline-flex flex-wrap gap-1 rounded-lg border border-border bg-surface-2 p-1", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={o.value === value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-md px-3 py-1 text-sm font-medium transition-colors",
            o.value === value ? "bg-surface text-fg shadow-card" : "text-fg-secondary hover:text-fg",
          )}
        >
          {o.label}
          {o.count != null && <span className="ml-1.5 text-xs text-fg-muted tabular">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function CopyField({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <code aria-label={label} className="min-w-0 flex-1 truncate rounded-lg border border-border bg-surface-2 px-3 py-2 font-mono text-xs text-fg">
        {value}
      </code>
      <Button
        variant="secondary"
        size="icon"
        aria-label={copied ? "Copied" : "Copy to clipboard"}
        onClick={async () => {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? <Check /> : <Copy />}
      </Button>
    </div>
  );
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeChoice>(getTheme());
  const next: Record<ThemeChoice, ThemeChoice> = { system: "light", light: "dark", dark: "system" };
  const icons = { system: <Monitor />, light: <Sun />, dark: <Moon /> };
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={`Theme: ${theme}. Switch to ${next[theme]}`}
      title={`Theme: ${theme}`}
      onClick={() => {
        const n = next[theme];
        applyTheme(n);
        setTheme(n);
      }}
    >
      {icons[theme]}
    </Button>
  );
}

export function DefinitionList({ items, columns = 2 }: { items: [ReactNode, ReactNode][]; columns?: 1 | 2 }) {
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-3 text-sm", columns === 2 && "sm:grid-cols-2")}>
      {items.map(([k, v], i) => (
        <div key={i} className="flex flex-col gap-0.5">
          <dt className="text-fg-muted">{k}</dt>
          <dd className="text-fg break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function PageError({ error }: { error: unknown }) {
  const status = (error as { status?: number } | null)?.status;
  const message = (error as { message?: string } | null)?.message;
  return (
    <EmptyState
      icon={<AlertCircle />}
      title={status === 404 ? "Not found" : "Could not load this page"}
      description={status === 404 ? "It may have been deleted, or you may not have access." : (message ?? "Please try again.")}
    />
  );
}
