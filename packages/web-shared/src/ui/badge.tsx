import { AlertTriangle, CheckCircle2, CircleDashed, Clock, Loader, MinusCircle, PauseCircle, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { titleCase } from "../lib/format";

export type Tone = "neutral" | "info" | "success" | "warning" | "danger";

const tones: Record<Tone, string> = {
  neutral: "bg-surface-2 text-fg-secondary border-border",
  info: "bg-primary-soft text-primary border-transparent",
  success: "bg-success-soft text-success border-transparent",
  warning: "bg-warning-soft text-warning border-transparent",
  danger: "bg-danger-soft text-danger border-transparent",
};

export function Badge({ tone = "neutral", children, className, icon }: { tone?: Tone; children: ReactNode; className?: string; icon?: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap [&_svg]:size-3.5",
        tones[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

const STATUS: Record<string, [Tone, ReactNode]> = {
  // campaigns
  DRAFT: ["neutral", <CircleDashed key="i" />],
  READY: ["info", <CheckCircle2 key="i" />],
  QUEUED: ["info", <Clock key="i" />],
  PROCESSING: ["info", <Loader key="i" />],
  PAUSED: ["warning", <PauseCircle key="i" />],
  COMPLETED: ["success", <CheckCircle2 key="i" />],
  FAILED: ["danger", <XCircle key="i" />],
  CANCELLED: ["neutral", <MinusCircle key="i" />],
  // providers
  ACTIVE: ["success", <CheckCircle2 key="i" />],
  WARNING: ["warning", <AlertTriangle key="i" />],
  DEGRADED: ["danger", <AlertTriangle key="i" />],
  DISABLED: ["neutral", <MinusCircle key="i" />],
  // workers
  online: ["success", <CheckCircle2 key="i" />],
  warning: ["warning", <AlertTriangle key="i" />],
  offline: ["danger", <XCircle key="i" />],
  // users
  active: ["success", <CheckCircle2 key="i" />],
  suspended: ["warning", <PauseCircle key="i" />],
  disabled: ["neutral", <MinusCircle key="i" />],
  // jobs & recipients
  pending: ["neutral", <Clock key="i" />],
  claimed: ["info", <Loader key="i" />],
  processing: ["info", <Loader key="i" />],
  completed: ["success", <CheckCircle2 key="i" />],
  retry: ["warning", <Clock key="i" />],
  failed: ["danger", <XCircle key="i" />],
  dead_letter: ["danger", <XCircle key="i" />],
  cancelled: ["neutral", <MinusCircle key="i" />],
  queued: ["neutral", <Clock key="i" />],
  sent: ["success", <CheckCircle2 key="i" />],
  delivered: ["success", <CheckCircle2 key="i" />],
  deferred: ["warning", <Clock key="i" />],
  bounced: ["danger", <XCircle key="i" />],
  complained: ["danger", <AlertTriangle key="i" />],
  unsubscribed: ["neutral", <MinusCircle key="i" />],
  suppressed: ["neutral", <MinusCircle key="i" />],
  revoked: ["neutral", <MinusCircle key="i" />],
};

/** Status is never conveyed by color alone: every badge has an icon and a text label. */
export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const [tone, icon] = STATUS[status] ?? ["neutral", null];
  return (
    <Badge tone={tone} icon={icon} className={className}>
      {titleCase(status)}
    </Badge>
  );
}
