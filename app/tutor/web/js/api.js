// Requests to the tutor's server. Errors carry the server's own message ("detail") so it can be shown as is.

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function errorFrom(r) {
  const data = await r.json().catch(() => ({}));
  const d = data.detail;
  return new ApiError(typeof d === "string" ? d : d ? JSON.stringify(d) : `Request failed (${r.status})`, r.status);
}

async function send(path, init) {
  try {
    return await fetch(path, init);
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("The tutor's server is not reachable. Is it still running?", 0);
  }
}

// api("/api/x"), api("/api/x", { method: "POST", json: {...} }), api(path, { method: "POST", form: formData })
export async function api(path, { method = "GET", json, form, signal } = {}) {
  const init = { method, headers: {}, signal };
  if (json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  }
  if (form) init.body = form;
  const r = await send(path, init);
  if (!r.ok) throw await errorFrom(r);
  return r.json().catch(() => ({}));
}

// Server-sent events from a POST (EventSource can only GET): yields each `data:` object as soon as it arrives.
// Aborting the signal ends the fetch; the loop then throws an AbortError the caller can tell apart.
export async function* streamEvents(path, body, { signal } = {}) {
  const r = await send(path, { method: "POST", headers: { "Content-Type": "application/json" },
                               body: JSON.stringify(body), signal });
  if (!r.ok) throw await errorFrom(r);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let i;
      while ((i = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, i);
        buffer = buffer.slice(i + 2);
        for (const line of chunk.split("\n")) if (line.startsWith("data: ")) yield JSON.parse(line.slice(6));
      }
    }
  } finally {
    reader.releaseLock();
  }
}
