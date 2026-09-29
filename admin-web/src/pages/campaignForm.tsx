import { Field, Input, Select, TemplateTagsTable, Textarea, type Page } from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api";

import type { Assignment, Campaign } from "../types";

export function campaignBody(f: FormData): Record<string, unknown> {
  const v = (k: string) => {
    const s = String(f.get(k) ?? "").trim();
    return s === "" ? null : s;
  };
  return {
    name: v("name"),
    subject: String(f.get("subject") ?? ""),
    from_name: v("from_name"),
    from_email: v("from_email"),
    reply_to: v("reply_to"),
    html_body: v("html_body"),
    text_body: v("text_body"),
    provider_id: v("provider_id"),
    batch_size: f.get("batch_size") ? Number(f.get("batch_size")) : null,
    message_list: String(f.get("message_list") ?? "")
      .split("\n")
      .map((m) => m.trim())
      .filter(Boolean),
  };
}

export function CampaignFields({ userId, campaign }: { userId: string | null; campaign?: Campaign }) {
  const providers = useQuery({
    queryKey: ["assignments", { user: userId }],
    queryFn: () => api.get<Page<Assignment>>("/admin/assignments", { user_id: userId, limit: 200 }),
    enabled: !!userId,
  });
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Campaign name" className="sm:col-span-2">{(id) => <Input id={id} name="name" defaultValue={campaign?.name} required maxLength={200} />}</Field>
      <Field label="Subject" hint="Use {{first_name}} (any CSV column) or tags such as #INVOICE#." className="sm:col-span-2">
        {(id, d) => <Input id={id} name="subject" defaultValue={campaign?.subject} aria-describedby={d} maxLength={998} />}
      </Field>
      <Field label="From name" optional>{(id) => <Input id={id} name="from_name" defaultValue={campaign?.from_name ?? ""} />}</Field>
      <Field label="From email">{(id) => <Input id={id} name="from_email" type="email" defaultValue={campaign?.from_email ?? ""} />}</Field>
      <Field label="Reply-to" optional>{(id) => <Input id={id} name="reply_to" type="email" defaultValue={campaign?.reply_to ?? ""} />}</Field>
      <Field label="Provider" hint={userId ? undefined : "Choose a user first"}>
        {(id, d) => (
          <Select id={id} name="provider_id" defaultValue={campaign?.provider_id ?? ""} aria-describedby={d} disabled={!userId}>
            <option value="">Select a provider</option>
            {providers.data?.items.map((a) => (
              <option key={a.provider_id} value={a.provider_id}>
                {a.provider_name}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="Batch size">
        {(id) => (
          <Select id={id} name="batch_size" defaultValue={String(campaign?.batch_size ?? 1000)}>
            {[500, 1000, 5000].map((s) => (
              <option key={s} value={s}>
                {s.toLocaleString()}
              </option>
            ))}
          </Select>
        )}
      </Field>
      <Field label="HTML body" className="sm:col-span-2" hint="An unsubscribe link is appended automatically unless you include {{unsubscribe_url}}.">
        {(id, d) => <Textarea id={id} name="html_body" defaultValue={campaign?.html_body ?? ""} rows={8} className="font-mono text-xs" aria-describedby={d} />}
      </Field>
      <Field label="Plain-text body" optional className="sm:col-span-2" hint="Generated from the HTML when empty.">
        {(id, d) => <Textarea id={id} name="text_body" defaultValue={campaign?.text_body ?? ""} rows={4} className="font-mono text-xs" aria-describedby={d} />}
      </Field>
      <Field label="Messages for #MASSAGE#" optional className="sm:col-span-2" hint="One per line; each email gets one at random.">
        {(id, d) => <Textarea id={id} name="message_list" defaultValue={(campaign?.message_list ?? []).join("\n")} rows={3} aria-describedby={d} />}
      </Field>
      <details className="group sm:col-span-2" open>
        <summary className="cursor-pointer text-sm font-medium text-fg-secondary">Available tags</summary>
        <TemplateTagsTable className="mt-2" />
      </details>
    </div>
  );
}
