import { useInfiniteQuery, type QueryKey } from "@tanstack/react-query";

import type { Page } from "../lib/api";
import { Button } from "./button";

/** Cursor pagination (ARCHITECTURE.md §54) with a "Load more" affordance. */
export function usePaged<T>(key: QueryKey, fetchPage: (cursor: string | undefined) => Promise<Page<T>>, options?: { refetchInterval?: number; enabled?: boolean }) {
  const q = useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: options?.refetchInterval,
    enabled: options?.enabled,
  });
  const items = q.data?.pages.flatMap((p) => p.items) ?? [];
  return { ...q, items };
}

export function LoadMore({ hasNext, loading, onClick }: { hasNext: boolean; loading: boolean; onClick: () => void }) {
  if (!hasNext) return null;
  return (
    <div className="flex justify-center border-t border-border p-3">
      <Button variant="ghost" size="sm" loading={loading} onClick={onClick}>
        Load more
      </Button>
    </div>
  );
}
