export interface Cell {
  id: string;
  sortie_id: string | null;
  repo: string;
  branch: string;
  worktree_path: string;
  pr_number: number | null;
  pr_url: string | null;
  ci_status: "unknown" | "pending" | "passing" | "failing";
  tend_status: "current" | "running";
  status: "active" | "archived";
  created_at: string;
  archived_at: string | null;
  sessions?: Session[];
}

export interface Session {
  id: string;
  cell_id: string;
  role: "daemon" | "user";
  trigger: string | null;
  succeeded: boolean | null;
  transcript: string;
  started_at: string;
  ended_at: string | null;
  alive: boolean;
}

export interface Sortie {
  id: string;
  prompt: string;
  session_id: string | null;
  status: "active" | "completed";
  created_at: string;
  cells?: Cell[];
  session?: Session;
}

export interface DaemonRunResult {
  cell_id: string;
  repo: string;
  branch: string;
  decision: "idle" | "skipped" | "tended" | "error";
  reasons: string[];
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
  upstream: string;
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

export async function fetchCells(status?: string): Promise<Cell[]> {
  const params = status ? `?status=${status}` : "";
  const res = await fetch(`${BASE}/cells${params}`);
  return res.json();
}

export async function fetchCell(id: string): Promise<Cell & { sessions: Session[] }> {
  const res = await fetch(`${BASE}/cells/${id}`);
  return res.json();
}

export async function createCell(prUrl: string): Promise<Cell> {
  const res = await fetch(`${BASE}/cells`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pr_url: prUrl }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create cell");
  }
  return res.json();
}

export async function createLocalCell(repo: string): Promise<Cell> {
  const res = await fetch(`${BASE}/cells`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create cell");
  }
  return res.json();
}

export async function archiveCell(id: string): Promise<Cell> {
  const res = await fetch(`${BASE}/cells/${id}/archive`, { method: "POST" });
  return res.json();
}

export async function reopenCell(id: string): Promise<Cell> {
  const res = await fetch(`${BASE}/cells/${id}/reopen`, { method: "POST" });
  return res.json();
}

export async function triggerSync(id: string): Promise<void> {
  await fetch(`${BASE}/cells/${id}/sync`, { method: "POST" });
}

export async function deleteCell(id: string): Promise<void> {
  await fetch(`${BASE}/cells/${id}`, { method: "DELETE" });
}

export async function openInVSCode(id: string): Promise<void> {
  await fetch(`${BASE}/cells/${id}/vscode`, { method: "POST" });
}

export async function openSessionInVSCode(cellId: string, sessionId: string): Promise<void> {
  await fetch(`${BASE}/cells/${cellId}/sessions/${sessionId}/vscode`, { method: "POST" });
}

export async function fetchSorties(): Promise<(Sortie & { cell_count: number })[]> {
  const res = await fetch(`${BASE}/sorties`);
  return res.json();
}

export async function fetchSortie(id: string): Promise<Sortie & { cells: Cell[] }> {
  const res = await fetch(`${BASE}/sorties/${id}`);
  return res.json();
}

export async function createSortie(prompt: string): Promise<Sortie> {
  const res = await fetch(`${BASE}/sorties`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to create sortie");
  }
  return res.json();
}

export async function createInteractiveSession(cellId: string, prompt?: string): Promise<Session> {
  const res = await fetch(`${BASE}/cells/${cellId}/sessions`, {
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

export async function deleteSession(cellId: string, sessionId: string): Promise<void> {
  const res = await fetch(`${BASE}/cells/${cellId}/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to delete session");
  }
}

export async function fetchXtermState(cellId: string, sessionId: string): Promise<ArrayBuffer> {
  const res = await fetch(`${BASE}/cells/${cellId}/sessions/${sessionId}/xterm-state`);
  if (!res.ok) {
    throw new Error("Failed to fetch xterm state");
  }
  return res.arrayBuffer();
}

export async function resumeSession(cellId: string, sessionId: string): Promise<Session> {
  const res = await fetch(`${BASE}/cells/${cellId}/sessions/${sessionId}/resume`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to resume session");
  }
  return res.json();
}

export async function fetchSortieXtermState(sortieId: string, sessionId: string): Promise<ArrayBuffer> {
  const res = await fetch(`${BASE}/sorties/${sortieId}/sessions/${sessionId}/xterm-state`);
  if (!res.ok) {
    throw new Error("Failed to fetch xterm state");
  }
  return res.arrayBuffer();
}

export async function resumeSortieSession(sortieId: string, sessionId: string): Promise<Session> {
  const res = await fetch(`${BASE}/sorties/${sortieId}/sessions/${sessionId}/resume`, {
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
