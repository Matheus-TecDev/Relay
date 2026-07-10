export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

type ApiClientConfig = {
  getToken?: () => string | null;
  onUnauthorized?: () => void;
};

let getToken: () => string | null = () => null;
let onUnauthorized: (() => void) | null = null;

export type ApiErrorPayload = {
  detail?: string;
};

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export function configureApiClient(config: ApiClientConfig) {
  getToken = config.getToken ?? (() => null);
  onUnauthorized = config.onUnauthorized ?? null;
}

export async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  const token = getToken();
  const headers = new Headers(options?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  } catch (error) {
    throw new ApiError(error instanceof Error ? error.message : "Backend unavailable", 0);
  }

  const payload = await parseJson(response);

  if (!response.ok) {
    const detail = isApiErrorPayload(payload) && payload.detail ? payload.detail : "Request failed";
    if (response.status === 401 && path !== "/api/auth/login") {
      onUnauthorized?.();
    }
    throw new ApiError(detail, response.status, payload);
  }

  return payload as T;
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch (error) {
    throw new ApiError("Invalid response from backend", response.status, text);
  }
}

function isApiErrorPayload(payload: unknown): payload is ApiErrorPayload {
  return typeof payload === "object" && payload !== null && "detail" in payload;
}
