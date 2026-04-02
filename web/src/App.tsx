import { useEffect, useState, useCallback } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  NavLink,
  useParams,
  useNavigate,
  Outlet,
  useOutletContext,
} from "react-router-dom";
import {
  fetchCells,
  fetchCell,
  createCell,
  archiveCell,
  triggerSync,
  deleteCell,
  openInVSCode,
  fetchSorties,
  fetchSortie,
  createSortie,
  createInteractiveSession,
  stopSession,
  resumeSession,
  connectWebSocket,
  type Cell,
  type Session,
  type Sortie,
} from "./api";
import Terminal from "./Terminal";

function StatusBadge({ status, label }: { status: string; label: string }) {
  const colors: Record<string, string> = {
    passing: "bg-green-100 text-green-800",
    current: "bg-green-100 text-green-800",
    active: "bg-blue-100 text-blue-800",
    pending: "bg-yellow-100 text-yellow-800",
    syncing: "bg-yellow-100 text-yellow-800",
    running: "bg-yellow-100 text-yellow-800",
    failing: "bg-red-100 text-red-800",
    failed: "bg-red-100 text-red-800",
    conflict: "bg-red-100 text-red-800",
    unknown: "bg-gray-100 text-gray-800",
    archived: "bg-gray-100 text-gray-800",
    completed: "bg-green-100 text-green-800",
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colors[status] ?? colors.unknown}`}
    >
      {label}
    </span>
  );
}

// --- Layout ---

function Layout() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ws = connectWebSocket(() => setTick((t) => t + 1));
    return () => ws.close();
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Orrery</h1>
          </div>
          <nav className="flex gap-1 bg-gray-100 rounded-lg p-1">
            <NavLink
              to="/cells"
              className={({ isActive }) =>
                `px-4 py-2 text-sm rounded-md ${
                  isActive
                    ? "bg-white shadow font-medium text-gray-900"
                    : "text-gray-600 hover:text-gray-900"
                }`
              }
            >
              Cells
            </NavLink>
            <NavLink
              to="/sorties"
              className={({ isActive }) =>
                `px-4 py-2 text-sm rounded-md ${
                  isActive
                    ? "bg-white shadow font-medium text-gray-900"
                    : "text-gray-600 hover:text-gray-900"
                }`
              }
            >
              Sorties
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-6">
        <Outlet context={tick} />
      </main>
    </div>
  );
}

// --- Cells ---

function CellRow({
  cell,
  onSync,
  onArchive,
  onDelete,
  onVSCode,
}: {
  cell: Cell;
  onSync: () => void;
  onArchive: () => void;
  onDelete: () => void;
  onVSCode: () => void;
}) {
  const navigate = useNavigate();
  return (
    <tr
      className="border-b border-gray-200 hover:bg-gray-50 cursor-pointer"
      onClick={() => navigate(`/cells/${cell.id}`)}
    >
      <td className="px-4 py-3">
        <div className="font-medium text-gray-900">{cell.repo.split("/").pop()}</div>
        <div className="text-sm text-gray-500">{cell.branch}</div>
      </td>
      <td className="px-4 py-3">
        {cell.pr_url ? (
          <a
            href={cell.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline text-sm"
            onClick={(e) => e.stopPropagation()}
          >
            #{cell.pr_number}
          </a>
        ) : (
          <span className="text-gray-400 text-sm">no PR</span>
        )}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={cell.ci_status} label={`CI: ${cell.ci_status}`} />
      </td>
      <td className="px-4 py-3">
        <StatusBadge
          status={cell.sync_status}
          label={`Sync: ${cell.sync_status}`}
        />
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={cell.status} label={cell.status} />
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={onVSCode}
            className="px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded"
            title="Open in VS Code"
          >
            VS Code
          </button>
          <button
            onClick={onSync}
            className="px-2 py-1 text-xs bg-blue-100 hover:bg-blue-200 text-blue-800 rounded"
          >
            Sync
          </button>
          <button
            onClick={onArchive}
            className="px-2 py-1 text-xs bg-yellow-100 hover:bg-yellow-200 text-yellow-800 rounded"
          >
            Archive
          </button>
          <button
            onClick={onDelete}
            className="px-2 py-1 text-xs bg-red-100 hover:bg-red-200 text-red-800 rounded"
          >
            Delete
          </button>
        </div>
      </td>
    </tr>
  );
}

function CreateCellForm({ onCreated }: { onCreated: () => void }) {
  const [prUrl, setPrUrl] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await createCell(prUrl);
      setPrUrl("");
      setOpen(false);
      onCreated();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm"
      >
        New Cell
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white rounded-lg shadow p-4 mb-6 space-y-3"
    >
      {error && (
        <div className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
          {error}
        </div>
      )}
      <div className="flex gap-3">
        <input
          type="text"
          placeholder="https://github.com/hegeldev/hegel-rust/pull/42"
          value={prUrl}
          onChange={(e) => setPrUrl(e.target.value)}
          required
          className="flex-1 px-3 py-2 border rounded text-sm"
        />
      </div>
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50"
        >
          {loading ? "Creating..." : "Create"}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setError(null); }}
          className="px-4 py-2 bg-gray-100 rounded hover:bg-gray-200 text-sm"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function CellsPage() {
  const tick = useOutletContext<number>();
  const [cells, setCells] = useState<Cell[]>([]);

  const loadCells = useCallback(async () => {
    setCells(await fetchCells());
  }, []);

  useEffect(() => {
    loadCells();
  }, [loadCells, tick]);

  return (
    <>
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-lg font-semibold text-gray-900">Cells</h2>
        <CreateCellForm onCreated={loadCells} />
      </div>

      {cells.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <p>No cells yet. Create one to get started.</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Repo / Branch
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  PR
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  CI
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Sync
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody>
              {cells.map((cell) => (
                <CellRow
                  key={cell.id}
                  cell={cell}
                  onSync={() => triggerSync(cell.id)}
                  onArchive={async () => {
                    await archiveCell(cell.id);
                    loadCells();
                  }}
                  onDelete={async () => {
                    await deleteCell(cell.id);
                    loadCells();
                  }}
                  onVSCode={() => openInVSCode(cell.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function CellDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const tick = useOutletContext<number>();
  const [cell, setCell] = useState<(Cell & { sessions: Session[] }) | null>(null);
  const [prompt, setPrompt] = useState("");
  const [launching, setLaunching] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setCell(await fetchCell(id));
  }, [id]);

  useEffect(() => {
    load();
  }, [load, tick]);

  const handleLaunchSession = async () => {
    if (!id) return;
    setLaunching(true);
    try {
      await createInteractiveSession(id, prompt.trim() || undefined);
      setPrompt("");
      load();
    } finally {
      setLaunching(false);
    }
  };

  const handleStopSession = async (sessionId: string) => {
    if (!id) return;
    await stopSession(id, sessionId);
    load();
  };

  const handleResumeSession = async (sessionId: string) => {
    if (!id) return;
    await resumeSession(id, sessionId);
    load();
  };

  if (!cell) return null;

  return (
    <div>
      <button
        onClick={() => navigate(-1)}
        className="mb-4 text-sm text-gray-500 hover:text-gray-700"
      >
        &larr; Back
      </button>
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-2">
          {cell.repo} / {cell.branch}
        </h2>
        <div className="flex gap-3 mb-4">
          <StatusBadge status={cell.status} label={cell.status} />
          <StatusBadge status={cell.ci_status} label={`CI: ${cell.ci_status}`} />
          <StatusBadge
            status={cell.sync_status}
            label={`Sync: ${cell.sync_status}`}
          />
        </div>
        <div className="text-sm text-gray-600 space-y-1">
          <div>
            <span className="font-medium">Worktree:</span> {cell.worktree_path}
          </div>
          {cell.pr_url && (
            <div>
              <span className="font-medium">PR:</span>{" "}
              <a
                href={cell.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                #{cell.pr_number}
              </a>
            </div>
          )}
          <div>
            <span className="font-medium">Created:</span>{" "}
            {new Date(cell.created_at).toLocaleString()}
          </div>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-4 mb-6">
        <h3 className="text-lg font-semibold mb-3">Launch Session</h3>
        <div className="space-y-3">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Optional initial prompt for Claude..."
            rows={2}
            className="w-full px-3 py-2 border rounded text-sm resize-y"
          />
          <button
            onClick={handleLaunchSession}
            disabled={launching}
            className="px-4 py-2 bg-gray-800 text-white rounded hover:bg-gray-900 text-sm disabled:opacity-50"
          >
            {launching ? "Launching..." : "Launch Terminal"}
          </button>
        </div>
      </div>

      <h3 className="text-lg font-semibold mb-3">Sessions</h3>
      {cell.sessions.length === 0 ? (
        <p className="text-gray-500 text-sm">No sessions yet.</p>
      ) : (
        <div className="space-y-3">
          {cell.sessions.map((s) => (
            <div key={s.id} className="bg-white rounded-lg shadow p-4">
              <div className="flex items-center gap-3 mb-2">
                {s.alive ? (
                  <StatusBadge status="running" label="alive" />
                ) : (
                  <StatusBadge
                    status={s.succeeded === true ? "completed" : s.succeeded === false ? "failed" : "unknown"}
                    label={s.succeeded === true ? "succeeded" : s.succeeded === false ? "failed" : "ended"}
                  />
                )}
                <span className="text-xs text-gray-500">
                  {s.role} {s.trigger ? `(${s.trigger})` : ""}
                </span>
                <span className="text-xs text-gray-400">
                  {new Date(s.started_at).toLocaleString()}
                </span>
                {s.alive ? (
                  <button
                    onClick={() => handleStopSession(s.id)}
                    className="ml-auto px-2 py-1 text-xs bg-red-100 hover:bg-red-200 text-red-800 rounded"
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    onClick={() => handleResumeSession(s.id)}
                    className="ml-auto px-2 py-1 text-xs bg-blue-100 hover:bg-blue-200 text-blue-800 rounded"
                  >
                    Resume
                  </button>
                )}
              </div>
              {s.alive ? (
                <Terminal sessionId={s.id} />
              ) : s.transcript ? (
                <pre className="text-xs bg-gray-50 p-3 rounded overflow-auto max-h-60">
                  {s.transcript}
                </pre>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Sorties ---

function CreateSortieForm({ onCreated }: { onCreated: () => void }) {
  const [prompt, setPrompt] = useState("");
  const [reposText, setReposText] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const repos = reposText
      .split(",")
      .map((r) => r.trim())
      .filter(Boolean);
    if (repos.length === 0) {
      setError("Provide at least one repo");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await createSortie(prompt, repos);
      setPrompt("");
      setReposText("");
      setOpen(false);
      onCreated();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm"
      >
        New Sortie
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white rounded-lg shadow p-4 mb-6 space-y-3"
    >
      {error && (
        <div className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
          {error}
        </div>
      )}
      <textarea
        placeholder="Describe the change to make across repos..."
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        required
        rows={3}
        className="w-full px-3 py-2 border rounded text-sm resize-y"
      />
      <input
        type="text"
        placeholder="hegeldev/hegel-rust, hegeldev/hegel-go, ..."
        value={reposText}
        onChange={(e) => setReposText(e.target.value)}
        required
        className="w-full px-3 py-2 border rounded text-sm"
      />
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50"
        >
          {loading ? "Creating..." : "Create"}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setError(null); }}
          className="px-4 py-2 bg-gray-100 rounded hover:bg-gray-200 text-sm"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function SortieRow({ sortie }: { sortie: Sortie & { cell_count: number } }) {
  const navigate = useNavigate();
  return (
    <tr
      className="border-b border-gray-200 hover:bg-gray-50 cursor-pointer"
      onClick={() => navigate(`/sorties/${sortie.id}`)}
    >
      <td className="px-4 py-3">
        <div className="text-sm text-gray-900 truncate max-w-xs">
          {sortie.prompt}
        </div>
      </td>
      <td className="px-4 py-3 text-sm text-gray-600">
        {sortie.repos.length} repo{sortie.repos.length !== 1 && "s"}
      </td>
      <td className="px-4 py-3 text-sm text-gray-600">
        {sortie.cell_count} / {sortie.repos.length}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={sortie.status} label={sortie.status} />
      </td>
      <td className="px-4 py-3 text-sm text-gray-500">
        {new Date(sortie.created_at).toLocaleDateString()}
      </td>
    </tr>
  );
}

function SortiesPage() {
  const tick = useOutletContext<number>();
  const [sorties, setSorties] = useState<(Sortie & { cell_count: number })[]>([]);

  const loadSorties = useCallback(async () => {
    setSorties(await fetchSorties());
  }, []);

  useEffect(() => {
    loadSorties();
  }, [loadSorties, tick]);

  return (
    <>
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-lg font-semibold text-gray-900">Sorties</h2>
        <CreateSortieForm onCreated={loadSorties} />
      </div>

      {sorties.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          <p>No sorties yet. Create one to coordinate work across repos.</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Prompt
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Repos
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Cells
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Created
                </th>
              </tr>
            </thead>
            <tbody>
              {sorties.map((s) => (
                <SortieRow key={s.id} sortie={s} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function SortieDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const tick = useOutletContext<number>();
  const [sortie, setSortie] = useState<(Sortie & { cells: Cell[] }) | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setSortie(await fetchSortie(id));
  }, [id]);

  useEffect(() => {
    load();
  }, [load, tick]);

  if (!sortie) return null;

  return (
    <div>
      <button
        onClick={() => navigate(-1)}
        className="mb-4 text-sm text-gray-500 hover:text-gray-700"
      >
        &larr; Back to sorties
      </button>
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-xl font-semibold">Sortie</h2>
          <StatusBadge status={sortie.status} label={sortie.status} />
        </div>
        <div className="text-sm text-gray-600 space-y-2">
          <div>
            <span className="font-medium">Prompt:</span>
            <p className="mt-1 bg-gray-50 p-3 rounded">{sortie.prompt}</p>
          </div>
          <div>
            <span className="font-medium">Repos:</span>{" "}
            {sortie.repos.join(", ")}
          </div>
          <div>
            <span className="font-medium">Created:</span>{" "}
            {new Date(sortie.created_at).toLocaleString()}
          </div>
        </div>
      </div>

      <h3 className="text-lg font-semibold mb-3">
        Cells ({sortie.cells.length} / {sortie.repos.length})
      </h3>
      {sortie.cells.length === 0 ? (
        <p className="text-gray-500 text-sm">Cells are being created...</p>
      ) : (
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Repo / Branch
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  PR
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  CI
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {sortie.cells.map((cell) => (
                <tr
                  key={cell.id}
                  className="border-b border-gray-200 hover:bg-gray-50 cursor-pointer"
                  onClick={() => navigate(`/cells/${cell.id}`)}
                >
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">
                      {cell.repo.split("/").pop()}
                    </div>
                    <div className="text-sm text-gray-500">{cell.branch}</div>
                  </td>
                  <td className="px-4 py-3">
                    {cell.pr_url ? (
                      <a
                        href={cell.pr_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-600 hover:underline text-sm"
                        onClick={(e) => e.stopPropagation()}
                      >
                        #{cell.pr_number}
                      </a>
                    ) : (
                      <span className="text-gray-400 text-sm">pending</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge
                      status={cell.ci_status}
                      label={`CI: ${cell.ci_status}`}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={cell.status} label={cell.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// --- App ---

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/cells" replace />} />
          <Route path="/cells" element={<CellsPage />} />
          <Route path="/cells/:id" element={<CellDetailPage />} />
          <Route path="/sorties" element={<SortiesPage />} />
          <Route path="/sorties/:id" element={<SortieDetailPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
