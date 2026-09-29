export type EventCounts = Record<"sent" | "delivered" | "failed" | "bounced" | "deferred" | "complained" | "unsubscribed" | "suppressed", number>;

export interface Campaign {
  id: string;
  provider_id: string | null;
  name: string;
  subject: string;
  from_name: string | null;
  from_email: string | null;
  reply_to: string | null;
  html_body?: string | null;
  text_body?: string | null;
  message_list?: string[] | null;
  status: string;
  batch_size: number;
  total_recipients: number;
  invalid_recipients: number;
  duplicate_recipients: number;
  sent: number;
  delivered: number;
  failed: number;
  bounced: number;
  complained: number;
  unsubscribed: number;
  skipped_suppressed: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  last_error: string | null;
  provider_name: string | null;
  progress: { done: number; remaining: number; percent: number } | null;
  missing: string[] | null;
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

export interface Provider {
  id: string;
  provider_name: string;
  from_email: string;
  from_name: string | null;
  status: string;
  per_second_limit: number | null;
  hourly_limit: number | null;
  daily_limit: number | null;
}

export interface ImportResult {
  rows: number;
  imported: number;
  invalid: number;
  duplicates: number;
  invalid_samples: string[];
  total_recipients: number;
  status: string;
  missing: string[];
}

export interface Recipient {
  id: number;
  email: string;
  status: string;
  attempts: number;
  last_error: string | null;
  sent_at: string | null;
}
