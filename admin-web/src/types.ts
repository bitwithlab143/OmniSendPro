export interface Limits {
  daily_limit: number | null;
  hourly_limit: number | null;
  per_second_limit: number | null;
  max_batch_size: number | null;
  max_recipients_per_campaign: number | null;
  updated_at?: string | null;
}

export interface User {
  id: string;
  email: string;
  username: string;
  full_name: string | null;
  role: string;
  status: "active" | "suspended" | "disabled";
  totp_enabled: boolean;
  created_at: string;
  last_login_at: string | null;
  locked_until: string | null;
  limits: Limits | null;
}

export interface Provider {
  id: string;
  provider_name: string;
  type: string;
  host: string;
  port: number;
  username: string | null;
  tls_mode: "starttls" | "ssl" | "none";
  from_email: string;
  from_name: string | null;
  hourly_limit: number | null;
  daily_limit: number | null;
  per_second_limit: number | null;
  status: "ACTIVE" | "WARNING" | "DEGRADED" | "DISABLED";
  status_reason: string | null;
  health_score: number;
  has_secret: boolean;
  webhook_enabled: boolean;
  last_tested_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string | null;
  created_at: string;
  updated_at: string;
  usage?: { hour: number; day: number } | null;
  assigned_users?: number;
}

export interface Assignment {
  id: string;
  user_id: string;
  provider_id: string;
  status: string;
  assigned_at: string;
  username: string | null;
  provider_name: string | null;
}

export interface Campaign {
  id: string;
  user_id: string;
  provider_id: string | null;
  name: string;
  subject: string;
  from_name: string | null;
  from_email: string | null;
  reply_to: string | null;
  status: string;
  batch_size: number;
  total_recipients: number;
  invalid_recipients: number;
  duplicate_recipients: number;
  processed: number;
  sent: number;
  delivered: number;
  failed: number;
  bounced: number;
  complained: number;
  deferred: number;
  unsubscribed: number;
  skipped_suppressed: number;
  scheduled_at: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  last_error: string | null;
  username: string | null;
  provider_name: string | null;
  progress: { done: number; remaining: number; percent: number } | null;
  missing: string[] | null;
  html_body?: string | null;
  text_body?: string | null;
}

export interface CampaignStats {
  status: string;
  total_recipients: number;
  sent: number;
  delivered: number;
  failed: number;
  bounced: number;
  complained: number;
  deferred: number;
  unsubscribed: number;
  skipped_suppressed: number;
  remaining: number;
  percent: number;
  speed: number;
  jobs: Record<string, number>;
}

export interface Worker {
  id: string;
  worker_id: string;
  name: string;
  version: string | null;
  hostname: string | null;
  capacity: number;
  max_concurrent_jobs: number;
  status: "online" | "warning" | "offline";
  disabled: boolean;
  registered_at: string | null;
  last_heartbeat_at: string | null;
  cpu: number | null;
  memory: number | null;
  active_jobs: number;
  current_rate: number;
  created_at: string;
}

export interface Job {
  id: string;
  campaign_id: string;
  user_id: string;
  provider_id: string;
  worker_id: string | null;
  status: string;
  batch_size: number;
  attempts: number;
  max_attempts: number;
  available_at: string;
  lease_expires_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  last_error: string | null;
}

export interface Suppression {
  id: number;
  scope_user_id: string | null;
  email_normalized: string;
  type: string;
  reason: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: number;
  actor: string | null;
  actor_type: string;
  action: string;
  resource: string;
  resource_id: string | null;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  ip: string | null;
  timestamp: string;
}

export type EventCounts = Record<"sent" | "delivered" | "failed" | "bounced" | "deferred" | "complained" | "unsubscribed" | "suppressed", number>;
