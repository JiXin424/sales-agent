/** Base API client — typed fetch wrappers with error handling. */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

/** Fields to strip from responses (never expose to frontend). */
const SECRET_FIELDS = ['api_key', 'api_key_env', 'authorization'];

function stripSecrets(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(stripSecrets);
  if (obj && typeof obj === 'object') {
    const cleaned: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (SECRET_FIELDS.includes(key)) {
        cleaned[key] = '****';
      } else {
        cleaned[key] = stripSecrets(value);
      }
    }
    return cleaned;
  }
  return obj;
}

class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`API Error ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  method: string,
  path: string,
  options?: { params?: Record<string, string | number | undefined>; body?: unknown; file?: File },
): Promise<T> {
  const url = new URL(`${BASE_URL}${path}`, window.location.origin);
  if (options?.params) {
    for (const [key, value] of Object.entries(options.params)) {
      if (value !== undefined && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }

  let fetchOptions: RequestInit = { method };

  if (options?.body instanceof FormData) {
    fetchOptions = { ...fetchOptions, body: options.body };
  } else if (options?.file) {
    const formData = new FormData();
    formData.append('file', options.file);
    fetchOptions = { ...fetchOptions, body: formData };
  } else if (options?.body) {
    fetchOptions = {
      ...fetchOptions,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(options.body),
    };
  }

  const response = await fetch(url.toString(), fetchOptions);

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const errBody = await response.json();
      detail = errBody?.detail || errBody?.error?.message || JSON.stringify(errBody);
    } catch {
      // use statusText
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;

  const json = await response.json();
  return stripSecrets(json) as T;
}

/** GET request. */
export function apiGet<T>(
  path: string,
  params?: Record<string, string | number | undefined>,
): Promise<T> {
  return request<T>('GET', path, { params });
}

/** POST request. */
export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('POST', path, { body });
}

/** PUT request. */
export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PUT', path, { body });
}

/** PATCH request. */
export function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('PATCH', path, { body });
}

/** File upload request. */
export function apiUpload<T>(path: string, file: File): Promise<T> {
  return request<T>('POST', path, { file });
}

/** Upload multiple files to an agent ontology endpoint (multipart/form-data). */
export function apiUploadFiles<T>(path: string, files: File[]): Promise<T> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  return request<T>('POST', path, { body: fd });
}

export { ApiError };
