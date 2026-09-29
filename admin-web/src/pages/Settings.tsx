import { Button, Card, CardBody, CardHeader, ErrorBanner, Field, Input, PageHeader, PageLoader, Checkbox, errorMessage, titleCase, useAction, useAuth } from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import type { FormEvent } from "react";

import { api } from "../api";

type Value = number | boolean | number[];
interface SettingsResponse {
  values: Record<string, Value>;
  defaults: Record<string, Value>;
  descriptions: Record<string, string>;
}

const GROUPS: [string, string[]][] = [
  ["Retries", ["retry_schedule_seconds", "max_attempts", "job_lease_seconds"]],
  ["Workers", ["worker_heartbeat_interval_seconds", "worker_warning_after_seconds", "worker_offline_after_seconds"]],
  ["Provider health", ["provider_health_window_minutes", "provider_health_min_sample", "provider_warning_below", "provider_degraded_below", "provider_disable_below", "provider_disable_after_windows", "provider_degraded_throttle"]],
  ["Campaigns", ["default_batch_size", "allowed_batch_sizes", "max_batch_size", "campaign_failure_threshold", "require_from_domain_match"]],
];

export function SettingsPage() {
  const { can } = useAuth();
  const writable = can("settings.write");
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsResponse>("/admin/settings") });
  const save = useAction((values: Record<string, Value>) => api.put("/admin/settings", { values }), { invalidate: [["settings"]], success: "Settings saved" });
  if (q.isLoading) return <PageLoader />;
  if (q.error || !q.data) return <ErrorBanner message={errorMessage(q.error)} />;
  const { values, defaults, descriptions } = q.data;

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const out: Record<string, Value> = {};
    for (const key of Object.keys(defaults)) {
      const def = defaults[key];
      if (typeof def === "boolean") out[key] = f.get(key) === "on";
      else if (Array.isArray(def)) out[key] = String(f.get(key)).split(",").map((s) => Number(s.trim())).filter((n) => !Number.isNaN(n));
      else out[key] = Number(f.get(key));
    }
    save.mutate(out);
  }

  return (
    <>
      <PageHeader title="Settings" description="Runtime-tunable behaviour. Changes apply within seconds; every change is audit logged." />
      <form onSubmit={submit} className="space-y-6">
        {GROUPS.map(([title, keys]) => (
          <Card key={title}>
            <CardHeader title={title} />
            <CardBody className="grid gap-5 sm:grid-cols-2">
              {keys.map((key) => {
                const v = values[key];
                if (typeof defaults[key] === "boolean")
                  return <Checkbox key={key} name={key} defaultChecked={Boolean(v)} disabled={!writable} label={<><span className="font-medium text-fg">{titleCase(key)}</span><br />{descriptions[key]}</>} />;
                return (
                  <Field key={key} label={titleCase(key)} hint={`${descriptions[key]} (default ${Array.isArray(defaults[key]) ? (defaults[key] as number[]).join(", ") : String(defaults[key])})`}>
                    {(id, d) => <Input id={id} name={key} defaultValue={Array.isArray(v) ? v.join(", ") : String(v)} disabled={!writable} aria-describedby={d} inputMode="decimal" />}
                  </Field>
                );
              })}
            </CardBody>
          </Card>
        ))}
        {writable && (
          <div className="flex justify-end">
            <Button type="submit" loading={save.isPending}>
              Save settings
            </Button>
          </div>
        )}
      </form>
    </>
  );
}
