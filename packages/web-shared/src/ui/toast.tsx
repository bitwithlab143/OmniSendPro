import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { cn } from "../lib/cn";

type Kind = "success" | "error" | "info";
interface Toast {
  id: number;
  kind: Kind;
  message: ReactNode;
}

const ToastContext = createContext<(kind: Kind, message: ReactNode) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));
  const push = useCallback((kind: Kind, message: ReactNode) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, kind, message }]);
    setTimeout(() => dismiss(id), kind === "error" ? 7000 : 4000);
  }, []);
  const icon = { success: <CheckCircle2 className="text-success" />, error: <AlertCircle className="text-danger" />, info: <Info className="text-primary" /> };
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex flex-col items-end gap-2 sm:inset-x-auto sm:right-4">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            className={cn(
              "pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border border-border bg-surface px-4 py-3 text-sm shadow-pop [&_svg]:size-4 [&_svg]:shrink-0",
            )}
          >
            <span className="mt-0.5">{icon[t.kind]}</span>
            <div className="flex-1 text-fg">{t.message}</div>
            <button className="text-fg-muted hover:text-fg" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <X />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const push = useContext(ToastContext);
  return {
    success: (m: ReactNode) => push("success", m),
    error: (m: ReactNode) => push("error", m),
    info: (m: ReactNode) => push("info", m),
  };
}
