/*
  All backend access goes through here.

  Requests hit a same-origin `/api` path which Vite proxies to the FastAPI
  server in dev (see vite.config.js). One constant to change when this is
  deployed behind a real gateway, instead of a hardcoded localhost URL
  scattered through the components.
*/
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function request(path, options) {
  const res = await fetch(BASE + path, options);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  }
  return res.json();
}

export const api = {
  metrics: () => request("/dashboard/metrics"),
  events: (arm = "agent", limit = 600) =>
    request(`/dashboard/events?arm=${arm}&limit=${limit}`),
  trail: (id) => request(`/dashboard/events/${id}/trail`),
  timeline: () => request("/dashboard/timeline"),
  verifyAudit: () => request("/audit/verify"),
  runBatch: (n, seed = 42) =>
    request(`/batch/run?n=${n}&seed=${seed}`, { method: "POST" }),
  reset: () => request("/batch/reset", { method: "POST" }),
};
