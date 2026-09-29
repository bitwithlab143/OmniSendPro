/** Direct browser → object storage upload with a presigned POST (design DS-21). XMLHttpRequest is used
 * because fetch() cannot report upload progress. */
export interface PresignedPost {
  url: string;
  fields: Record<string, string>;
  object_key: string;
  max_bytes: number;
}

export function uploadToStorage(post: PresignedPost, file: File, onProgress?: (fraction: number) => void, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    for (const [k, v] of Object.entries(post.fields)) form.append(k, v);
    form.append("file", file); // must be the last field
    const xhr = new XMLHttpRequest();
    xhr.open("POST", post.url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`Upload failed (HTTP ${xhr.status})`)));
    xhr.onerror = () => reject(new Error("Upload failed: the storage server could not be reached"));
    xhr.onabort = () => reject(new Error("Upload cancelled"));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(form);
  });
}

/** Files above this size go through object storage when it is available. */
export const LARGE_FILE_BYTES = 5 * 1024 * 1024;
