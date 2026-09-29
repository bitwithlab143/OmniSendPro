import { KeyRound, ShieldCheck } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState, type FormEvent, type ReactNode } from "react";

import { errorMessage } from "../lib/api";
import { useAuth, type LoginStep } from "../lib/auth";
import { Button } from "./button";
import { Field, Input } from "./form";
import { CopyField, ErrorBanner, ThemeToggle } from "./misc";

export function LoginScreen({ product, subtitle, footer }: { product: ReactNode; subtitle: string; footer?: ReactNode }) {
  const { login, verifyMfa } = useAuth();
  const [step, setStep] = useState<LoginStep | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");

  async function onLogin(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const next = await login(String(form.get("login")), String(form.get("password")));
      if (next.kind !== "done") setStep(next);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function onVerify(e: FormEvent) {
    e.preventDefault();
    if (!step || step.kind === "done") return;
    setBusy(true);
    setError(null);
    try {
      await verifyMfa(step.mfaToken, code);
    } catch (err) {
      setError(errorMessage(err));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-full flex-col bg-bg">
      <div className="flex justify-end p-4">
        <ThemeToggle />
      </div>
      <main className="flex flex-1 items-start justify-center px-4 pb-16 pt-8 sm:items-center sm:pt-0">
        <div className="w-full max-w-sm">
          <div className="mb-8 text-center">
            <div className="mx-auto mb-4 flex size-11 items-center justify-center rounded-xl bg-primary text-primary-fg shadow-card">
              <KeyRound className="size-5" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">{product}</h1>
            <p className="mt-1 text-sm text-fg-secondary">{subtitle}</p>
          </div>
          <div className="rounded-xl border border-border bg-surface p-6 shadow-card">
            {error && (
              <div className="mb-4">
                <ErrorBanner message={error} />
              </div>
            )}
            {!step || step.kind === "done" ? (
              <form onSubmit={onLogin} className="flex flex-col gap-4" noValidate>
                <Field label="Email or username">
                  {(id) => <Input id={id} name="login" autoComplete="username" required autoFocus />}
                </Field>
                <Field label="Password">
                  {(id) => <Input id={id} name="password" type="password" autoComplete="current-password" required />}
                </Field>
                <Button type="submit" loading={busy} className="mt-1 w-full">
                  Sign in
                </Button>
              </form>
            ) : (
              <form onSubmit={onVerify} className="flex flex-col gap-4">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <ShieldCheck className="size-4 text-primary" />
                  {step.kind === "mfa_setup" ? "Set up two-factor authentication" : "Two-factor verification"}
                </div>
                {step.kind === "mfa_setup" && (
                  <div className="flex flex-col gap-3 text-sm text-fg-secondary">
                    <p>Two-factor authentication is required. Scan this code with an authenticator app, then enter the 6-digit code.</p>
                    <div className="mx-auto rounded-lg bg-white p-3">
                      <QRCodeSVG value={step.otpauthUri} size={168} />
                    </div>
                    <p className="text-xs">Can't scan? Enter this key manually:</p>
                    <CopyField value={step.secret} label="Setup key" />
                  </div>
                )}
                <Field label="Verification code">
                  {(id) => (
                    <Input
                      id={id}
                      value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      placeholder="123456"
                      className="tracking-[0.3em] tabular text-center text-base"
                      autoFocus
                      required
                    />
                  )}
                </Field>
                <Button type="submit" loading={busy} disabled={code.length !== 6} className="w-full">
                  Verify
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setStep(null);
                    setCode("");
                    setError(null);
                  }}
                >
                  Back to sign in
                </Button>
              </form>
            )}
          </div>
          {footer && <div className="mt-6 text-center text-xs text-fg-muted">{footer}</div>}
        </div>
      </main>
    </div>
  );
}
