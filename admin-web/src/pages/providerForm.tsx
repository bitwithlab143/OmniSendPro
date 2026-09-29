import { Field, Input, Select } from "@omnisend/web-shared";

import type { Provider } from "../types";

export function numOrNull(v: FormDataEntryValue | null): number | null {
  const s = String(v ?? "").trim();
  return s === "" ? null : Number(s);
}

export function providerBody(f: FormData, includePassword: boolean): Record<string, unknown> {
  const body: Record<string, unknown> = {
    provider_name: f.get("provider_name"),
    host: String(f.get("host")).trim(),
    port: Number(f.get("port")),
    username: String(f.get("username") ?? "").trim() || null,
    tls_mode: f.get("tls_mode"),
    from_email: f.get("from_email"),
    from_name: f.get("from_name") || null,
    hourly_limit: numOrNull(f.get("hourly_limit")),
    daily_limit: numOrNull(f.get("daily_limit")),
    per_second_limit: numOrNull(f.get("per_second_limit")),
    max_connections: numOrNull(f.get("max_connections")),
  };
  if (includePassword && f.get("password")) body.password = f.get("password");
  return body;
}

export function ProviderFields({ provider, withPassword }: { provider?: Provider; withPassword?: boolean }) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Provider name" className="sm:col-span-2">{(id) => <Input id={id} name="provider_name" defaultValue={provider?.provider_name} required />}</Field>
      <Field label="SMTP host">{(id) => <Input id={id} name="host" defaultValue={provider?.host} placeholder="smtp.example.com" required />}</Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Port">{(id) => <Input id={id} name="port" type="number" min={1} max={65535} defaultValue={provider?.port ?? 587} required />}</Field>
        <Field label="Security">
          {(id) => (
            <Select id={id} name="tls_mode" defaultValue={provider?.tls_mode ?? "starttls"}>
              <option value="starttls">STARTTLS</option>
              <option value="ssl">SSL/TLS</option>
              <option value="none">None</option>
            </Select>
          )}
        </Field>
      </div>
      <Field label="Username" optional>{(id) => <Input id={id} name="username" defaultValue={provider?.username ?? ""} autoComplete="off" />}</Field>
      {withPassword && (
        <Field label="Password / API key" optional hint="Encrypted at rest; never shown again.">
          {(id, d) => <Input id={id} name="password" type="password" autoComplete="new-password" aria-describedby={d} />}
        </Field>
      )}
      <Field label="From email">{(id) => <Input id={id} name="from_email" type="email" defaultValue={provider?.from_email} required />}</Field>
      <Field label="From name" optional>{(id) => <Input id={id} name="from_name" defaultValue={provider?.from_name ?? ""} />}</Field>
      <Field label="Per-second limit" optional>{(id) => <Input id={id} name="per_second_limit" type="number" min={1} defaultValue={provider?.per_second_limit ?? ""} />}</Field>
      <Field label="Hourly limit" optional>{(id) => <Input id={id} name="hourly_limit" type="number" min={1} defaultValue={provider?.hourly_limit ?? ""} />}</Field>
      <Field label="Daily limit" optional>{(id) => <Input id={id} name="daily_limit" type="number" min={1} defaultValue={provider?.daily_limit ?? ""} />}</Field>
      <Field label="Max connections" optional hint="Simultaneous SMTP sessions per worker allowed by the provider. Higher = faster over real networks.">
        {(id, d) => <Input id={id} name="max_connections" type="number" min={1} max={500} defaultValue={provider?.max_connections ?? ""} aria-describedby={d} />}
      </Field>
    </div>
  );
}
