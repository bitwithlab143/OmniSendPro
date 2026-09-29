import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";

import { useToast } from "../ui/toast";
import { errorMessage } from "./api";

/** Mutation + toast feedback + cache invalidation, the pattern every admin/user action uses. */
export function useAction<TArgs, TResult>(
  fn: (args: TArgs) => Promise<TResult>,
  opts: {
    success?: string | ((r: TResult) => string);
    invalidate?: QueryKey[];
    onSuccess?: (r: TResult, args: TArgs) => void;
  } = {},
) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (r, args) => {
      if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(r) : opts.success);
      await Promise.all((opts.invalidate ?? []).map((k) => qc.invalidateQueries({ queryKey: k })));
      opts.onSuccess?.(r, args);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
}
