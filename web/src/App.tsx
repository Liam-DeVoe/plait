import { useEffect, useState, useCallback, useRef } from "react";
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
  createLocalCell,
  archiveCell,
  triggerSync,
  deleteCell,
  openInVSCode,
  openSessionInVSCode,
  fetchSorties,
  fetchSortie,
  createSortie,
  createInteractiveSession,
  deleteSession,
  resumeSession,
  connectWebSocket,
  type Cell,
  type Session,
  type Sortie,
} from "./api";
import Terminal from "./Terminal";
import "./App.css";

// --- Shared Components ---

const BADGE_STATUSES = new Set([
  "passing", "current", "active", "pending", "syncing", "running",
  "failing", "failed", "conflict", "unknown", "archived", "completed",
]);

function StatusBadge({ status, label }: { status: string; label: string }) {
  const modifier = BADGE_STATUSES.has(status) ? status : "unknown";
  return <span className={`badge badge--${modifier}`}>{label}</span>;
}

function OverflowMenu({
  items,
}: {
  items: { label: string; onClick: () => void; danger?: boolean }[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div className="overflow-menu" ref={ref}>
      <div className="overflow-menu__toggle" onClick={() => setOpen(!open)}>
        <svg
          className={`overflow-menu__icon${open ? " overflow-menu__icon--open" : ""}`}
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path
            fillRule="evenodd"
            d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
            clipRule="evenodd"
          />
        </svg>
      </div>
      {open && (
        <div className="overflow-menu__dropdown">
          {items.map((item) => (
            <div
              key={item.label}
              onClick={() => {
                item.onClick();
                setOpen(false);
              }}
              className={`overflow-menu__item${item.danger ? " overflow-menu__item--danger" : ""}`}
            >
              {item.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Layout ---

type LayoutContext = { tick: number };

function Layout() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ws = connectWebSocket(() => setTick((t) => t + 1));
    return () => ws.close();
  }, []);

  return (
    <div className="layout">
      <div className="layout__header">
        <div className="layout__header-inner">
          <NavLink to="/" className="layout__logo">
            Orrery
          </NavLink>
          <div className="layout__nav">
            <NavLink
              to="/cells"
              className={({ isActive }) =>
                `layout__nav-link${isActive ? " layout__nav-link--active" : ""}`
              }
            >
              Cells
            </NavLink>
            <NavLink
              to="/sorties"
              className={({ isActive }) =>
                `layout__nav-link${isActive ? " layout__nav-link--active" : ""}`
              }
            >
              Sorties
            </NavLink>
          </div>
        </div>
      </div>
      <div className="layout__main">
        <Outlet context={{ tick } satisfies LayoutContext} />
      </div>
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
  const needsAttention =
    cell.ci_status === "failing" ||
    cell.sync_status === "conflict" ||
    cell.sync_status === "failed";

  return (
    <tr
      className={`cell-row${needsAttention ? " cell-row--attention" : ""}`}
      onClick={() => navigate(`/cells/${cell.id}`)}
    >
      <td className="table__cell">
        <div className="cell-row__branch">{cell.branch}</div>
      </td>
      <td className="table__cell">
        {cell.pr_url ? (
          <a
            href={cell.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="link"
            onClick={(e) => e.stopPropagation()}
          >
            #{cell.pr_number}
          </a>
        ) : (
          <span className="cell-row__no-pr">no PR</span>
        )}
      </td>
      <td className="table__cell">
        <StatusBadge status={cell.ci_status} label={`CI: ${cell.ci_status}`} />
      </td>
      <td className="table__cell">
        <StatusBadge
          status={cell.sync_status}
          label={`Sync: ${cell.sync_status}`}
        />
      </td>
      <td className="table__cell">
        <div className="cell-row__actions" onClick={(e) => e.stopPropagation()}>
          <div
            className="btn btn--sm btn--gray"
            onClick={onVSCode}
            title="Open in VS Code"
          >
            VS Code
          </div>
          <div className="btn btn--sm btn--soft-blue" onClick={onSync}>
            Sync
          </div>
          <OverflowMenu
            items={[
              { label: "Archive", onClick: onArchive },
              { label: "Delete", onClick: onDelete, danger: true },
            ]}
          />
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

  const handleSubmit = async () => {
    if (!prUrl.trim()) return;
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
      <div className="btn btn--gray" onClick={() => setOpen(true)}>
        Import Cell
      </div>
    );
  }

  return (
    <div className="card form">
      {error && <div className="error-banner">{error}</div>}
      <div className="form__row">
        <input
          type="text"
          placeholder="https://github.com/owner/repo/pull/42"
          value={prUrl}
          onChange={(e) => setPrUrl(e.target.value)}
          className="form__input form__input--flex"
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSubmit();
          }}
        />
      </div>
      <div className="form__actions">
        <div
          className={`btn btn--blue${loading ? " btn--disabled" : ""}`}
          onClick={handleSubmit}
        >
          {loading ? "Creating..." : "Create"}
        </div>
        <div
          className="btn btn--gray"
          onClick={() => {
            setOpen(false);
            setError(null);
          }}
        >
          Cancel
        </div>
      </div>
    </div>
  );
}

function CellsPage() {
  const { tick } = useOutletContext<LayoutContext>();
  const [cells, setCells] = useState<Cell[]>([]);

  const loadCells = useCallback(async () => {
    setCells(await fetchCells());
  }, []);

  useEffect(() => {
    loadCells();
  }, [loadCells, tick]);

  const handleNewLocalCell = async (repo: string) => {
    await createLocalCell(repo);
    loadCells();
  };

  // Group cells by repo
  const grouped = new Map<string, Cell[]>();
  for (const cell of cells) {
    const repo = cell.repo;
    if (!grouped.has(repo)) grouped.set(repo, []);
    grouped.get(repo)!.push(cell);
  }

  return (
    <>
      <div className="page-header">
        <div className="page-title">Cells</div>
        <CreateCellForm onCreated={loadCells} />
      </div>

      {cells.length === 0 ? (
        <div className="empty-state">
          <div>No cells yet. Import one or create a sortie to get started.</div>
        </div>
      ) : (
        <div className="cells-page__groups">
          {[...grouped.entries()].map(([repo, repoCells]) => (
            <div key={repo} className="card card--clipped">
              <div className="cells-page__group-header">
                <div className="cells-page__group-title">
                  {repo.split("/").pop()}
                </div>
                <div
                  className="btn btn--sm btn--blue cells-page__add-btn"
                  onClick={() => handleNewLocalCell(repo)}
                  title="New cell"
                >
                  +
                </div>
              </div>
              <table className="table">
                <thead className="table__head">
                  <tr>
                    <th className="table__header-cell">Branch</th>
                    <th className="table__header-cell">PR</th>
                    <th className="table__header-cell">CI</th>
                    <th className="table__header-cell">Sync</th>
                    <th className="table__header-cell">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {repoCells.map((cell) => (
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
          ))}
        </div>
      )}
    </>
  );
}

function CellDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { tick } = useOutletContext<LayoutContext>();
  const [cell, setCell] = useState<(Cell & { sessions: Session[] }) | null>(
    null,
  );
  const [launching, setLaunching] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

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
      const session = await createInteractiveSession(id);
      setSelectedSessionId(session.id);
      load();
    } finally {
      setLaunching(false);
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    if (!id) return;
    await deleteSession(id, sessionId);
    if (selectedSessionId === sessionId) setSelectedSessionId(null);
    load();
  };

  const handleResumeSession = async (sessionId: string) => {
    if (!id) return;
    await resumeSession(id, sessionId);
    load();
  };

  if (!cell) return null;

  const userSessions = cell.sessions.filter((s) => s.role === "user");
  const daemonSessions = cell.sessions.filter((s) => s.role === "daemon");

  // Auto-select: prefer most recent alive user session, else most recent user session
  const effectiveSelectedId = (() => {
    if (selectedSessionId && userSessions.some((s) => s.id === selectedSessionId))
      return selectedSessionId;
    const lastAlive = [...userSessions].reverse().find((s) => s.alive);
    if (lastAlive) return lastAlive.id;
    if (userSessions.length > 0) return userSessions[userSessions.length - 1].id;
    return null;
  })();

  const selectedSession = userSessions.find((s) => s.id === effectiveSelectedId) ?? null;

  return (
    <div>
      <div className="back-link" onClick={() => navigate(-1)}>
        &larr; Back
      </div>
      <div className="card cell-detail__card">
        <div className="cell-detail__header">
          <div>
            <div className="cell-detail__title">
              {cell.repo.split("/").pop()}{" "}
              <span className="cell-detail__title-branch">
                / {cell.branch}
              </span>
            </div>
            <div className="cell-detail__badges">
              <StatusBadge
                status={cell.ci_status}
                label={`CI: ${cell.ci_status}`}
              />
              <StatusBadge
                status={cell.sync_status}
                label={`Sync: ${cell.sync_status}`}
              />
            </div>
            {cell.pr_url && (
              <div className="cell-detail__pr">
                <a
                  href={cell.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="link"
                >
                  PR #{cell.pr_number} ↗
                </a>
              </div>
            )}
          </div>
          <div className="cell-detail__actions">
            <div
              className="btn btn--md btn--gray"
              onClick={() => openInVSCode(cell.id)}
            >
              VS Code
            </div>
            <div
              className="btn btn--md btn--soft-blue"
              onClick={() => triggerSync(cell.id)}
            >
              Sync
            </div>
            <OverflowMenu
              items={[
                {
                  label: "Archive",
                  onClick: async () => {
                    await archiveCell(cell.id);
                    navigate("/cells");
                  },
                },
                {
                  label: "Delete",
                  onClick: async () => {
                    await deleteCell(cell.id);
                    navigate("/cells");
                  },
                  danger: true,
                },
              ]}
            />
          </div>
        </div>
      </div>

      <div className="cell-detail__sessions-header">
        <div className="cell-detail__sessions-title">Sessions</div>
      </div>

      {userSessions.length === 0 ? (
        <div>
          <div className="muted" style={{ marginBottom: 12 }}>No sessions yet.</div>
          <div
            className={`btn btn--blue${launching ? " btn--disabled" : ""}`}
            onClick={handleLaunchSession}
          >
            {launching ? "Launching..." : "New Session"}
          </div>
        </div>
      ) : (
        <div className="terminal-panel">
          <div className="terminal-panel__main">
            {selectedSession && (
              <>
                <div className="terminal-panel__header">
                  {selectedSession.alive && (
                    <div
                      className="btn btn--sm btn--soft-gray"
                      onClick={async () => {
                        await openSessionInVSCode(cell.id, selectedSession.id);
                        load();
                      }}
                    >
                      VS Code
                    </div>
                  )}
                  {!selectedSession.alive && (
                    <div
                      className="btn btn--sm btn--soft-blue"
                      onClick={() => handleResumeSession(selectedSession.id)}
                    >
                      Resume
                    </div>
                  )}
                  <div
                    className="btn btn--sm btn--soft-red"
                    onClick={() => handleDeleteSession(selectedSession.id)}
                  >
                    Delete
                  </div>
                </div>
                <Terminal
                  sessionId={selectedSession.id}
                  cellId={cell.id}
                  alive={selectedSession.alive}
                  autoFocus={selectedSession.id === selectedSessionId}
                  onResume={() => handleResumeSession(selectedSession.id)}
                />
              </>
            )}
          </div>
          <div className="terminal-panel__sidebar">
            <div
              className={`terminal-panel__new-btn${launching ? " btn--disabled" : ""}`}
              onClick={handleLaunchSession}
            >
              {launching ? "Launching..." : "+ New Session"}
            </div>
            {userSessions.map((s) => (
              <div
                key={s.id}
                className={`terminal-panel__tab${s.id === effectiveSelectedId ? " terminal-panel__tab--selected" : ""}`}
                onClick={() => setSelectedSessionId(s.id)}
              >
                <span
                  className={`terminal-panel__tab-dot${s.alive ? " terminal-panel__tab-dot--alive" : ""}`}
                />
                <span className="terminal-panel__tab-label">
                  {s.trigger ? s.trigger : "session"}
                </span>
                <span className="terminal-panel__tab-time">
                  {new Date(s.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {daemonSessions.length > 0 && (
        <div>
          <div className="cell-detail__daemon-title">Daemon sessions</div>
          <div className="cell-detail__daemon-list">
            {[...daemonSessions].reverse().map((s) => (
              <CollapsibleSession
                key={s.id}
                session={s}
                cellId={cell.id}
                onResume={() => handleResumeSession(s.id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CollapsibleSession({
  session,
  cellId,
  onResume,
}: {
  session: Session;
  cellId: string;
  onResume: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div>
      <div
        onClick={() => setExpanded(!expanded)}
        className="collapsible-session__header"
      >
        <span className="collapsible-session__arrow">
          {expanded ? "▾" : "▸"}
        </span>
        <span className="collapsible-session__role">
          {session.role}
          {session.trigger ? ` (${session.trigger})` : ""}
        </span>
        <span className="collapsible-session__time">
          {new Date(session.started_at).toLocaleString()}
        </span>
        <div
          onClick={(e) => {
            e.stopPropagation();
            onResume();
          }}
          className="btn btn--xs btn--soft-blue collapsible-session__resume"
        >
          Resume
        </div>
      </div>
      {expanded && (
        <div className="collapsible-session__body">
          <Terminal
            sessionId={session.id}
            cellId={cellId}
            alive={false}
            onResume={async () => onResume()}
          />
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

  const handleSubmit = async () => {
    if (!prompt.trim()) return;
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
      <div className="btn btn--blue" onClick={() => setOpen(true)}>
        New Sortie
      </div>
    );
  }

  return (
    <div className="card form">
      {error && <div className="error-banner">{error}</div>}
      <textarea
        placeholder="Describe the change to make across repos..."
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={3}
        className="form__textarea"
      />
      <input
        type="text"
        placeholder="owner/repo-one, owner/repo-two, ..."
        value={reposText}
        onChange={(e) => setReposText(e.target.value)}
        className="form__input form__input--full"
        onKeyDown={(e) => {
          if (e.key === "Enter") handleSubmit();
        }}
      />
      <div className="form__actions">
        <div
          className={`btn btn--blue${loading ? " btn--disabled" : ""}`}
          onClick={handleSubmit}
        >
          {loading ? "Creating..." : "Create"}
        </div>
        <div
          className="btn btn--gray"
          onClick={() => {
            setOpen(false);
            setError(null);
          }}
        >
          Cancel
        </div>
      </div>
    </div>
  );
}

function SortieRow({ sortie }: { sortie: Sortie & { cell_count: number } }) {
  const navigate = useNavigate();
  return (
    <tr
      className="sortie-row"
      onClick={() => navigate(`/sorties/${sortie.id}`)}
    >
      <td className="table__cell">
        <div className="sortie-row__prompt">{sortie.prompt}</div>
      </td>
      <td className="table__cell sortie-row__meta">
        {sortie.repos.length} repo{sortie.repos.length !== 1 && "s"}
      </td>
      <td className="table__cell sortie-row__meta">
        {sortie.cell_count} / {sortie.repos.length}
      </td>
      <td className="table__cell">
        <StatusBadge status={sortie.status} label={sortie.status} />
      </td>
      <td className="table__cell sortie-row__date">
        {new Date(sortie.created_at).toLocaleDateString()}
      </td>
    </tr>
  );
}

function SortiesPage() {
  const { tick } = useOutletContext<LayoutContext>();
  const [sorties, setSorties] = useState<(Sortie & { cell_count: number })[]>(
    [],
  );

  const loadSorties = useCallback(async () => {
    setSorties(await fetchSorties());
  }, []);

  useEffect(() => {
    loadSorties();
  }, [loadSorties, tick]);

  return (
    <>
      <div className="page-header">
        <div className="page-title">Sorties</div>
        <CreateSortieForm onCreated={loadSorties} />
      </div>

      {sorties.length === 0 ? (
        <div className="empty-state">
          <div>No sorties yet.</div>
        </div>
      ) : (
        <div className="card card--clipped">
          <table className="table">
            <thead className="table__head">
              <tr>
                <th className="table__header-cell">Prompt</th>
                <th className="table__header-cell">Repos</th>
                <th className="table__header-cell">Cells</th>
                <th className="table__header-cell">Status</th>
                <th className="table__header-cell">Created</th>
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
  const { tick } = useOutletContext<LayoutContext>();
  const [sortie, setSortie] = useState<(Sortie & { cells: Cell[] }) | null>(
    null,
  );

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
      <div className="back-link" onClick={() => navigate(-1)}>
        &larr; Back to sorties
      </div>
      <div className="card sortie-detail__card">
        <div className="sortie-detail__header">
          <div className="sortie-detail__title">Sortie</div>
          <StatusBadge status={sortie.status} label={sortie.status} />
        </div>
        <div className="sortie-detail__info">
          <div>
            <span className="sortie-detail__label">Prompt:</span>
            <div className="sortie-detail__prompt-box">{sortie.prompt}</div>
          </div>
          <div>
            <span className="sortie-detail__label">Repos:</span>{" "}
            {sortie.repos.join(", ")}
          </div>
          <div>
            <span className="sortie-detail__label">Created:</span>{" "}
            {new Date(sortie.created_at).toLocaleString()}
          </div>
        </div>
      </div>

      <div className="sortie-detail__cells-header">
        Cells ({sortie.cells.length} / {sortie.repos.length})
      </div>
      {sortie.cells.length === 0 ? (
        <div className="muted">Cells are being created...</div>
      ) : (
        <div className="card card--clipped">
          <table className="table">
            <thead className="table__head">
              <tr>
                <th className="table__header-cell">Repo / Branch</th>
                <th className="table__header-cell">PR</th>
                <th className="table__header-cell">CI</th>
                <th className="table__header-cell">Status</th>
              </tr>
            </thead>
            <tbody>
              {sortie.cells.map((cell) => (
                <tr
                  key={cell.id}
                  className="sortie-detail__cell-row"
                  onClick={() => navigate(`/cells/${cell.id}`)}
                >
                  <td className="table__cell">
                    <div className="sortie-detail__cell-repo">
                      {cell.repo.split("/").pop()}
                    </div>
                    <div className="sortie-detail__cell-branch">
                      {cell.branch}
                    </div>
                  </td>
                  <td className="table__cell">
                    {cell.pr_url ? (
                      <a
                        href={cell.pr_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="link"
                        onClick={(e) => e.stopPropagation()}
                      >
                        #{cell.pr_number}
                      </a>
                    ) : (
                      <span className="cell-row__no-pr">pending</span>
                    )}
                  </td>
                  <td className="table__cell">
                    <StatusBadge
                      status={cell.ci_status}
                      label={`CI: ${cell.ci_status}`}
                    />
                  </td>
                  <td className="table__cell">
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
