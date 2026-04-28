export interface Worktop {
  id: string;
  slate_id: string | null;
  repo: string;
  branch: string;
  worktree_path: string;
  pr_number: number | null;
  pr_url: string | null;
  ci_status: "unknown" | "pending" | "passing" | "failing";
  tend_status: "current" | "running";
  status: "open" | "archived";
  archive_reason: "merged" | "closed" | null;
  created_at: string;
  archived_at: string | null;
  sessions?: Session[];
}

export interface Session {
  id: string;
  worktop_id: string;
  role: "daemon" | "user";
  trigger: string | null;
  succeeded: boolean | null;
  transcript: string;
  started_at: string;
  ended_at: string | null;
  alive: boolean;
}

export interface Slate {
  id: string;
  session_id: string | null;
  name: string | null;
  archived: boolean;
  is_archived: boolean;
  created_at: string;
  worktops?: Worktop[];
  session?: Session;
}

export interface DaemonRunResult {
  worktop_id: string;
  repo: string;
  branch: string;
  decision: "ok" | "skipped" | "tended" | "deferred" | "archived" | "error";
  reasons: string[];
  warnings: string[];
  outcome: "succeeded" | "failed" | null;
}

export interface DaemonRun {
  id: string;
  started_at: string;
  ended_at: string;
  results: DaemonRunResult[];
}

export interface Repo {
  id: string;
  path: string;
  upstream: string | null;
  kind: "remote" | "local";
}

const BASE = "/api";

export async function fetchDaemonRuns(limit = 20): Promise<DaemonRun[]> {
  const res = await fetch(`${BASE}/daemon/runs?limit=${limit}`);
  return res.json();
}

export async function triggerDaemonRun(): Promise<void> {
  await fetch(`${BASE}/daemon/runs`, { method: "POST" });
}

export async function fetchRepos(): Promise<Repo[]> {
  const res = await fetch(`${BASE}/repos`);
  return res.json();
}

export async function fetchWorktops(status?: string): Promise<Worktop[]> {
  const params = status ? `?status=${status}` : "";
  const res = await fetch(`${BASE}/worktops${params}`);
  return res.json();
}

export async function fetchWorktop(id: string): Promise<Worktop & { sessions: Session[] }> {
  const res = await fetch(`${BASE}/worktops/${id}`);
  return res.json();
}

export async function createWorktop(prUrl: string): Promise<Worktop> {
  const res = await fetch(`${BASE}/worktops`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pr_url: prUrl }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create worktop");
  }
  return res.json();
}

export async function createLocalWorktop(repo: string): Promise<Worktop> {
  const res = await fetch(`${BASE}/worktops`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create worktop");
  }
  return res.json();
}

export async function archiveWorktop(id: string): Promise<Worktop> {
  const res = await fetch(`${BASE}/worktops/${id}/archive`, { method: "POST" });
  return res.json();
}

export async function reopenWorktop(id: string): Promise<Worktop> {
  const res = await fetch(`${BASE}/worktops/${id}/reopen`, { method: "POST" });
  return res.json();
}

export async function triggerSync(id: string): Promise<void> {
  await fetch(`${BASE}/worktops/${id}/sync`, { method: "POST" });
}

export async function deleteWorktop(id: string): Promise<void> {
  await fetch(`${BASE}/worktops/${id}`, { method: "DELETE" });
}

export async function openInVSCode(id: string): Promise<void> {
  await fetch(`${BASE}/worktops/${id}/vscode`, { method: "POST" });
}

export async function openSessionInVSCode(worktopId: string, sessionId: string): Promise<void> {
  await fetch(`${BASE}/worktops/${worktopId}/sessions/${sessionId}/vscode`, { method: "POST" });
}

export async function fetchSlates(): Promise<(Slate & { worktop_count: number })[]> {
  const res = await fetch(`${BASE}/slates`);
  return res.json();
}

export async function fetchSlate(id: string): Promise<Slate & { worktops: Worktop[] }> {
  const res = await fetch(`${BASE}/slates/${id}`);
  return res.json();
}

export async function createSlate(): Promise<Slate> {
  const res = await fetch(`${BASE}/slates`, { method: "POST" });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create slate");
  }
  return res.json();
}

export async function archiveSlate(id: string): Promise<Slate> {
  const res = await fetch(`${BASE}/slates/${id}/archive`, { method: "POST" });
  return res.json();
}

export async function unarchiveSlate(id: string): Promise<Slate> {
  const res = await fetch(`${BASE}/slates/${id}/unarchive`, { method: "POST" });
  return res.json();
}

export async function deleteSlate(id: string): Promise<void> {
  await fetch(`${BASE}/slates/${id}`, { method: "DELETE" });
}

export async function openSlateInVSCode(id: string): Promise<void> {
  await fetch(`${BASE}/slates/${id}/vscode`, { method: "POST" });
}

export async function createInteractiveSession(worktopId: string, prompt?: string): Promise<Session> {
  const res = await fetch(`${BASE}/worktops/${worktopId}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: prompt ?? "" }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create session");
  }
  return res.json();
}

export async function deleteSession(worktopId: string, sessionId: string): Promise<void> {
  const res = await fetch(`${BASE}/worktops/${worktopId}/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to delete session");
  }
}

export async function fetchXtermState(worktopId: string, sessionId: string): Promise<ArrayBuffer> {
  const res = await fetch(`${BASE}/worktops/${worktopId}/sessions/${sessionId}/xterm-state`);
  if (!res.ok) {
    throw new Error("Failed to fetch xterm state");
  }
  return res.arrayBuffer();
}

export async function resumeSession(worktopId: string, sessionId: string): Promise<Session> {
  const res = await fetch(`${BASE}/worktops/${worktopId}/sessions/${sessionId}/resume`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to resume session");
  }
  return res.json();
}

export async function fetchSlateXtermState(slateId: string, sessionId: string): Promise<ArrayBuffer> {
  const res = await fetch(`${BASE}/slates/${slateId}/sessions/${sessionId}/xterm-state`);
  if (!res.ok) {
    throw new Error("Failed to fetch xterm state");
  }
  return res.arrayBuffer();
}

export async function startSlateSession(slateId: string, sessionId: string): Promise<Session> {
  const res = await fetch(`${BASE}/slates/${slateId}/sessions/${sessionId}/start`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to start session");
  }
  return res.json();
}

export async function resumeSlateSession(slateId: string, sessionId: string): Promise<Session> {
  const res = await fetch(`${BASE}/slates/${slateId}/sessions/${sessionId}/resume`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to resume session");
  }
  return res.json();
}

export function connectWebSocket(onMessage: (data: any) => void): WebSocket {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
  ws.onmessage = (event) => {
    onMessage(JSON.parse(event.data));
  };
  return ws;
}
