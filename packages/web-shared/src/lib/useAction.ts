import { useMutation, useQueryClient, type QueryKey } from "@tanstack/react-query";

import { useToast } from "../ui/toast";
import { errorMessage } from "./api";

/** Mutation + toast feedback + cache invalidation, the pattern every admin/user action uses. */
export function useAction<TArgs, TResult>(
  fn: (args: TArgs) => Promise<TResult>,
  opts: {
    success?: string | ((r: TResult) => string);
    /** For results that report failure in the body (e.g. connection tests): a message shows an error toast. */
    failed?: (r: TResult) => string | null | undefined;
    invalidate?: QueryKey[];
    onSuccess?: (r: TResult, args: TArgs) => void;
  } = {},
) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (r, args) => {
      const failure = opts.failed?.(r);
      if (failure) toast.error(failure);
      else if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(r) : opts.success);
      await Promise.all((opts.invalidate ?? []).map((k) => qc.invalidateQueries({ queryKey: k })));
      opts.onSuccess?.(r, args);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });
}
