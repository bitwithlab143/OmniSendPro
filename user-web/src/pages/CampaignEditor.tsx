import {
  Badge,
  Button,
  Card,
  CardBody,
  Checkbox,
  DefinitionList,
  ErrorBanner,
  Field,
  Input,
  PageError,
  PageHeader,
  PageLoader,
  Select,
  Textarea,
  cn,
  errorMessage,
  fmt,
  useAction,
  useConfirm,
} from "@omnisend/web-shared";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, FileUp, Play, Trash2, UploadCloud } from "lucide-react";
import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useParams, useSearchParams } from "react-router";

import { api } from "../api";
import type { Campaign, ImportResult, Provider } from "../types";

const STEPS = [
  { key: "details", label: "Details" },
  { key: "content", label: "Content" },
  { key: "recipients", label: "Recipients" },
  { key: "review", label: "Review & start" },
] as const;
type Step = (typeof STEPS)[number]["key"];

interface Options {
  batch_sizes: number[];
  max_batch_size: number;
  default_batch_size: number;
}

const MISSING_LABEL: Record<string, string> = {
  subject: "a subject",
  from_email: "a From address",
  content: "email content",
  provider: "a provider",
  recipients: "recipients",
};

function Stepper({ step, onStep, enabled }: { step: Step; onStep: (s: Step) => void; enabled: boolean }) {
  const idx = STEPS.findIndex((s) => s.key === step);
  return (
    <ol className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Campaign steps">
      {STEPS.map((s, i) => (
        <li key={s.key}>
          <button
            disabled={!enabled && i > 0}
            onClick={() => onStep(s.key)}
            aria-current={s.key === step ? "step" : undefined}
            className={cn(
              "flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              s.key === step ? "border-primary bg-primary-soft text-primary" : "border-border bg-surface text-fg-secondary hover:text-fg",
            )}
          >
            <span className={cn("flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold", i < idx ? "bg-primary text-primary-fg" : "bg-surface-3 text-fg-secondary")}>
              {i < idx ? <Check className="size-3" /> : i + 1}
            </span>
            {s.label}
          </button>
        </li>
      ))}
    </ol>
  );
}

export function CampaignEditorPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const step = (params.get("step") as Step) || "details";
  const setStep = (s: Step) => setParams({ step: s }, { replace: true });

  const campaign = useQuery({ queryKey: ["campaigns", id], queryFn: () => api.get<Campaign>(`/user/campaigns/${id}`), enabled: !!id });
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<Provider[]>("/user/providers") });
  const options = useQuery({ queryKey: ["options"], queryFn: () => api.get<Options>("/user/options") });

  if (id && campaign.isLoading) return <PageLoader />;
  if (id && (campaign.error || !campaign.data)) return <PageError error={campaign.error} />;
  const c = campaign.data;
  if (c && !["DRAFT", "READY"].includes(c.status)) return <Navigate to={`/campaigns/${c.id}`} replace />;

  return (
    <>
      <PageHeader
        back={
          <Link to="/campaigns" className="inline-flex items-center gap-1 text-sm text-fg-secondary hover:text-fg">
            <ArrowLeft className="size-4" /> Campaigns
          </Link>
        }
        title={c ? c.name : "New campaign"}
        description={c?.missing?.length ? `Still needed: ${c.missing.map((m) => MISSING_LABEL[m] ?? m).join(", ")}` : c ? "Ready to start" : "Set up your campaign in four short steps."}
      />
      <Stepper step={step} onStep={setStep} enabled={!!c} />
      {providers.data && providers.data.length === 0 && (
        <div className="mb-4">
          <ErrorBanner message="No sending provider is assigned to your account yet. Ask your administrator to assign one." />
        </div>
      )}
      {step === "details" && <DetailsStep campaign={c} providers={providers.data ?? []} options={options.data} onDone={(saved) => navigate(`/campaigns/${saved.id}/edit?step=content`, { replace: !c })} />}
      {step === "content" && c && <ContentStep campaign={c} onDone={() => setStep("recipients")} onBack={() => setStep("details")} />}
      {step === "recipients" && c && <RecipientsStep campaign={c} onDone={() => setStep("review")} onBack={() => setStep("content")} />}
      {step === "review" && c && <ReviewStep campaign={c} onBack={() => setStep("recipients")} />}
    </>
  );
}

function DetailsStep({ campaign, providers, options, onDone }: { campaign?: Campaign; providers: Provider[]; options?: Options; onDone: (c: Campaign) => void }) {
  const [providerId, setProviderId] = useState(campaign?.provider_id ?? providers[0]?.id ?? "");
  const provider = providers.find((p) => p.id === (providerId || providers[0]?.id));
  const domain = provider?.from_email.split("@")[1];
  const save = useAction(
    (body: Record<string, unknown>) => (campaign ? api.patch<Campaign>(`/user/campaigns/${campaign.id}`, body) : api.post<Campaign>("/user/campaigns", body)),
    { invalidate: [["campaigns"]], success: campaign ? "Saved" : "Campaign created", onSuccess: onDone },
  );
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const text = (k: string) => String(f.get(k) ?? "").trim() || null;
    save.mutate({
      name: text("name"),
      subject: String(f.get("subject") ?? ""),
      from_name: text("from_name"),
      from_email: text("from_email"),
      reply_to: text("reply_to"),
      provider_id: text("provider_id"),
      batch_size: Number(f.get("batch_size")),
    });
  }
  const sizes = options?.batch_sizes ?? [500, 1000, 5000];
  return (
    <Card>
      <CardBody>
        <form onSubmit={submit} className="grid gap-5 sm:grid-cols-2">
          {save.error && <div className="sm:col-span-2"><ErrorBanner message={errorMessage(save.error)} /></div>}
          <Field label="Campaign name" className="sm:col-span-2">{(fid) => <Input id={fid} name="name" defaultValue={campaign?.name} required maxLength={200} autoFocus={!campaign} />}</Field>
          <Field label="Subject" className="sm:col-span-2" hint="Personalise with CSV columns, e.g. Hello {{first_name}}">
            {(fid, d) => <Input id={fid} name="subject" defaultValue={campaign?.subject} maxLength={998} aria-describedby={d} />}
          </Field>
          <Field label="Provider">
            {(fid) => (
              <Select id={fid} name="provider_id" value={providerId} onChange={(e) => setProviderId(e.target.value)} required>
                {providers.map((p) => (
                  <option key={p.id} value={p.id} disabled={!["ACTIVE", "WARNING"].includes(p.status)}>
                    {p.provider_name}
                    {!["ACTIVE", "WARNING"].includes(p.status) ? ` (${p.status.toLowerCase()})` : ""}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Batch size" hint="Recipients per job handed to a worker">
            {(fid, d) => (
              <Select id={fid} name="batch_size" defaultValue={String(campaign?.batch_size ?? options?.default_batch_size ?? 1000)} aria-describedby={d}>
                {sizes.map((s) => (
                  <option key={s} value={s}>
                    {s.toLocaleString()}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="From name" optional>{(fid) => <Input id={fid} name="from_name" defaultValue={campaign?.from_name ?? provider?.from_name ?? ""} />}</Field>
          <Field label="From email" hint={domain ? `Must use @${domain}` : undefined}>
            {(fid, d) => <Input id={fid} name="from_email" type="email" defaultValue={campaign?.from_email ?? provider?.from_email ?? ""} required aria-describedby={d} />}
          </Field>
          <Field label="Reply-to" optional className="sm:col-span-2">{(fid) => <Input id={fid} name="reply_to" type="email" defaultValue={campaign?.reply_to ?? ""} />}</Field>
          <div className="flex justify-end sm:col-span-2">
            <Button type="submit" loading={save.isPending}>
              Save & continue <ArrowRight />
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

function ContentStep({ campaign, onDone, onBack }: { campaign: Campaign; onDone: () => void; onBack: () => void }) {
  const [html, setHtml] = useState(campaign.html_body ?? "<h1>Hello {{first_name}}</h1>\n<p>Write your message here.</p>");
  const [text, setText] = useState(campaign.text_body ?? "");
  const save = useAction(() => api.patch<Campaign>(`/user/campaigns/${campaign.id}`, { html_body: html || null, text_body: text || null }), {
    invalidate: [["campaigns"]],
    success: "Content saved",
    onSuccess: onDone,
  });
  const preview = html.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_, k: string) => (k === "unsubscribe_url" ? "#" : `[${k}]`));
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardBody className="flex flex-col gap-4">
          <Field label="HTML body" hint="An unsubscribe link is added automatically unless you include {{unsubscribe_url}}.">
            {(fid, d) => <Textarea id={fid} value={html} onChange={(e) => setHtml(e.target.value)} rows={16} className="font-mono text-xs" aria-describedby={d} spellCheck={false} />}
          </Field>
          <Field label="Plain-text version" optional hint="Generated from the HTML if left empty.">
            {(fid, d) => <Textarea id={fid} value={text} onChange={(e) => setText(e.target.value)} rows={5} className="font-mono text-xs" aria-describedby={d} />}
          </Field>
        </CardBody>
      </Card>
      <Card className="flex flex-col overflow-hidden">
        <div className="border-b border-border px-5 py-3 text-sm">
          <div className="text-fg-muted">Preview</div>
          <div className="truncate font-medium">{campaign.subject || "(no subject)"}</div>
        </div>
        {/* sandbox="" : no scripts, no forms, no same-origin access */}
        <iframe title="Email preview" sandbox="" srcDoc={preview} className="min-h-96 w-full flex-1 bg-white" />
      </Card>
      <div className="flex justify-between lg:col-span-2">
        <Button variant="secondary" onClick={onBack}>
          <ArrowLeft /> Back
        </Button>
        <Button onClick={() => save.mutate(undefined)} loading={save.isPending} disabled={!html.trim() && !text.trim()}>
          Save & continue <ArrowRight />
        </Button>
      </div>
    </div>
  );
}

function RecipientsStep({ campaign, onDone, onBack }: { campaign: Campaign; onDone: () => void; onBack: () => void }) {
  const confirm = useConfirm();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [replace, setReplace] = useState(false);
  const upload = useAction((file: File) => api.upload<ImportResult>(`/user/campaigns/${campaign.id}/recipients`, file, { replace }), {
    invalidate: [["campaigns"]],
    success: (r) => `Imported ${fmt(r.imported)} recipients`,
    onSuccess: setResult,
  });
  const clear = useAction(() => api.del(`/user/campaigns/${campaign.id}/recipients`), { invalidate: [["campaigns"]], success: "Recipients removed", onSuccess: () => setResult(null) });
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files[0];
    if (f) upload.mutate(f);
  };
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardBody className="flex flex-col gap-4">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={onDrop}
            className={cn("flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors", drag ? "border-primary bg-primary-soft" : "border-border-strong")}
          >
            <UploadCloud className="mb-3 size-8 text-fg-muted" />
            <p className="text-sm font-medium">Drop a CSV file here</p>
            <p className="mt-1 text-xs text-fg-muted">An “email” column is required; other columns become merge variables. Up to 50 MB.</p>
            <input
              ref={input}
              type="file"
              accept=".csv,.txt,text/csv"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload.mutate(f);
                e.target.value = "";
              }}
            />
            <Button variant="secondary" className="mt-4" loading={upload.isPending} onClick={() => input.current?.click()}>
              <FileUp /> Choose file
            </Button>
          </div>
          {campaign.total_recipients > 0 && <Checkbox checked={replace} onChange={(e) => setReplace(e.target.checked)} label="Replace the current list instead of adding to it" />}
          {result && (
            <div className="rounded-lg border border-border bg-surface-2 p-4 text-sm">
              <div className="flex flex-wrap gap-2">
                <Badge tone="success">{fmt(result.imported)} imported</Badge>
                <Badge tone={result.invalid ? "warning" : "neutral"}>{fmt(result.invalid)} invalid</Badge>
                <Badge>{fmt(result.duplicates)} duplicates</Badge>
              </div>
              {result.invalid_samples.length > 0 && (
                <p className="mt-3 text-xs text-fg-secondary">
                  Skipped, e.g.: <span className="font-mono">{result.invalid_samples.slice(0, 5).join(", ")}</span>
                </p>
              )}
            </div>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardBody className="flex flex-col gap-4">
          <div>
            <div className="text-sm text-fg-secondary">Recipients in this campaign</div>
            <div className="mt-1 text-3xl font-semibold">{fmt(campaign.total_recipients)}</div>
            {(campaign.invalid_recipients > 0 || campaign.duplicate_recipients > 0) && (
              <div className="mt-1 text-xs text-fg-muted">
                {fmt(campaign.invalid_recipients)} invalid and {fmt(campaign.duplicate_recipients)} duplicate rows skipped
              </div>
            )}
          </div>
          <p className="text-xs text-fg-muted">Addresses on the suppression list (unsubscribes, bounces, complaints) are skipped automatically when sending starts.</p>
          {campaign.total_recipients > 0 && (
            <Button
              variant="danger-ghost"
              size="sm"
              className="self-start"
              onClick={async () => {
                if (await confirm({ title: "Remove all recipients?", message: "You can upload a new list afterwards.", confirmLabel: "Remove", danger: true })) clear.mutate(undefined);
              }}
            >
              <Trash2 /> Remove all
            </Button>
          )}
        </CardBody>
      </Card>
      <div className="flex justify-between lg:col-span-3">
        <Button variant="secondary" onClick={onBack}>
          <ArrowLeft /> Back
        </Button>
        <Button onClick={onDone} disabled={campaign.total_recipients === 0}>
          Review <ArrowRight />
        </Button>
      </div>
    </div>
  );
}

function ReviewStep({ campaign, onBack }: { campaign: Campaign; onBack: () => void }) {
  const navigate = useNavigate();
  const [consent, setConsent] = useState(false);
  const start = useAction(() => api.post<Campaign>(`/user/campaigns/${campaign.id}/start`, { consent_confirmed: consent }), {
    invalidate: [["campaigns"], ["dashboard"]],
    success: "Sending started",
    onSuccess: () => navigate(`/campaigns/${campaign.id}`),
  });
  const missing = campaign.missing ?? [];
  return (
    <Card>
      <CardBody className="space-y-6">
        <DefinitionList
          items={[
            ["Campaign", campaign.name],
            ["Subject", campaign.subject || "—"],
            ["From", campaign.from_email ? `${campaign.from_name ?? ""} <${campaign.from_email}>` : "—"],
            ["Provider", campaign.provider_name ?? "—"],
            ["Recipients", fmt(campaign.total_recipients)],
            ["Batch size", fmt(campaign.batch_size)],
          ]}
        />
        {missing.length > 0 ? (
          <ErrorBanner message={`Before starting, add ${missing.map((m) => MISSING_LABEL[m] ?? m).join(", ")}.`} />
        ) : (
          <div className="rounded-lg border border-border bg-surface-2 p-4">
            <Checkbox
              checked={consent}
              onChange={(e) => setConsent(e.target.checked)}
              label="I confirm every recipient on this list gave permission to receive this email, and the message includes a working unsubscribe option."
            />
          </div>
        )}
        {start.error && <ErrorBanner message={errorMessage(start.error)} />}
        <div className="flex justify-between">
          <Button variant="secondary" onClick={onBack}>
            <ArrowLeft /> Back
          </Button>
          <Button size="lg" onClick={() => start.mutate(undefined)} disabled={!consent || missing.length > 0} loading={start.isPending}>
            <Play /> Start sending
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
