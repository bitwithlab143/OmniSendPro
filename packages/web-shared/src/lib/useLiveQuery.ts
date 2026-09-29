import { useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import type { ApiClient } from "./api";

/**
 * A query kept fresh by a Server-Sent Events stream (design DS-20), falling back to polling.
 *
 * - While the stream is connected, polling is off and each `update` event replaces the cached data.
 * - The server ends streams after a few minutes (`end: timeout`): reconnect at once.
 * - `end: final` (e.g. campaign completed): stop streaming; the data is final.
 * - Errors switch to polling every `pollMs` and retry the stream with backoff (5 s → 60 s).
 */
export function useLiveQuery<T>(opts: {
  api: ApiClient;
  queryKey: QueryKey;
  queryFn: () => Promise<T>;
  streamPath: string | null;
  pollMs: number | false;
  enabled?: boolean;
}) {
  const { api, queryKey, queryFn, streamPath, pollMs, enabled = true } = opts;
  const qc = useQueryClient();
  const [streaming, setStreaming] = useState(false);
  const keyRef = useRef(queryKey);
  keyRef.current = queryKey;

  const query = useQuery({ queryKey, queryFn, enabled, refetchInterval: streaming ? false : pollMs });

  useEffect(() => {
    if (!enabled || !streamPath || typeof window === "undefined" || !("ReadableStream" in window)) return;
    const controller = new AbortController();
    let delay = 5000;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = async () => {
      let final = false;
      let updates = 0;
      const started = Date.now();
      try {
        await api.stream(
          streamPath,
          (event, data) => {
            if (event === "update") {
              updates += 1;
              setStreaming(true);
              delay = 5000;
              qc.setQueryData(keyRef.current, data as T);
            } else if (event === "end" && (data as { reason?: string })?.reason === "final") {
              final = true;
            }
          },
          controller.signal,
        );
      } catch {
        if (controller.signal.aborted) return;
        setStreaming(false); // polling takes over until the stream is back
        timer = setTimeout(() => void connect(), delay);
        delay = Math.min(delay * 2, 60_000);
        return;
      }
      if (stopped) return;
      if (final) {
        setStreaming(false);
        return;
      }
      if (updates === 0 && Date.now() - started < 2000) {
        // Closed straight away (proxy, restart): back off instead of reconnecting in a tight loop.
        setStreaming(false);
        timer = setTimeout(() => void connect(), delay);
        delay = Math.min(delay * 2, 60_000);
        return;
      }
      void connect(); // server-side timeout: reconnect immediately
    };
    void connect();
    return () => {
      stopped = true;
      controller.abort();
      if (timer) clearTimeout(timer);
      setStreaming(false);
    };
  }, [api, qc, streamPath, enabled]);

  return { ...query, streaming };
}
