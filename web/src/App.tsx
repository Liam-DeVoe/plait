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
  useLocation,
} from "react-router-dom";
import {
  fetchCells,
  fetchCell,
  fetchDaemonRuns,
  fetchRepos,
  triggerDaemonRun,
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
  deleteSortie,
  openSortieInVSCode,
  createInteractiveSession,
  deleteSession,
  resumeSession,
  fetchXtermState,
  fetchSortieXtermState,
  startSortieSession,
  resumeSortieSession,
  connectWebSocket,
  type Cell,
  type DaemonRun,
  type Session,
  type Sortie,
  type Repo,
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

type LayoutContext = { run: number };

function Layout() {
  const [run, setRun] = useState(0);
  useEffect(() => {
    const ws = connectWebSocket(() => setRun((r) => r + 1));
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
        <Outlet context={{ run } satisfies LayoutContext} />
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
  const needsAttention = cell.ci_status === "failing";

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
          status={cell.tend_status}
          label={`Tend: ${cell.tend_status}`}
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
            Tend
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

function CreateCellForm({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [prUrl, setPrUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!prUrl.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await createCell(prUrl);
      setPrUrl("");
      onClose();
      onCreated();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (!open) return null;

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
          autoFocus
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
            onClose();
            setError(null);
          }}
        >
          Cancel
        </div>
      </div>
    </div>
  );
}

function timeAgo(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function runSummary(run: DaemonRun): string {
  const tended = run.results.filter((r) => r.decision === "tended");
  const skipped = run.results.filter((r) => r.decision === "skipped").length;
  const errored = run.results.filter((r) => r.decision === "error").length;
  const idle = run.results.filter((r) => r.decision === "idle").length;

  const parts: string[] = [];
  if (tended.length > 0) {
    const ok = tended.filter((r) => r.outcome === "succeeded").length;
    const fail = tended.filter((r) => r.outcome === "failed").length;
    const sub = [ok > 0 && `${ok} ok`, fail > 0 && `${fail} failed`]
      .filter(Boolean)
      .join(", ");
    parts.push(`${tended.length} tended (${sub})`);
  }
  if (skipped > 0) parts.push(`${skipped} skipped`);
  if (errored > 0) parts.push(`${errored} errored`);
  if (idle > 0) parts.push(`${idle} idle`);

  return parts.join(", ");
}

function DaemonLog({ runs }: { runs: DaemonRun[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (runs.length === 0) return null;

  return (
    <div className="daemon-log">
      <div className="daemon-log__header">
        <div className="daemon-log__title">Daemon log</div>
        <div
          className="btn btn--sm btn--soft-blue"
          onClick={() => triggerDaemonRun()}
        >
          Run Now
        </div>
      </div>
      <div className="daemon-log__list">
        {runs.map((run) => (
          <div key={run.id} className="daemon-log__run">
            <div
              className="daemon-log__run-header"
              onClick={() =>
                setExpanded(expanded === run.id ? null : run.id)
              }
            >
              <span className="daemon-log__run-arrow">
                {expanded === run.id ? "▾" : "▸"}
              </span>
              <span className="daemon-log__run-time">
                {timeAgo(run.started_at)}
              </span>
              <span className="daemon-log__run-summary">
                {runSummary(run)}
              </span>
            </div>
            {expanded === run.id && (
              <div className="daemon-log__run-details">
                {run.results.map((r) => (
                  <div key={r.cell_id} className="daemon-log__cell-result">
                    <NavLink
                      to={`/cells/${r.cell_id}`}
                      className="daemon-log__cell-name"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {r.repo}:{r.branch}
                    </NavLink>
                    <span
                      className={`badge badge--${
                        r.decision === "tended"
                          ? r.outcome === "succeeded"
                            ? "passing"
                            : "failing"
                          : r.decision === "skipped"
                            ? "pending"
                            : r.decision === "error"
                              ? "failing"
                              : "unknown"
                      }`}
                    >
                      {r.decision}
                      {r.outcome ? ` (${r.outcome})` : ""}
                    </span>
                    {r.reasons.length > 0 && (
                      <span className="daemon-log__reasons">
                        {r.reasons.join(", ")}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function CellTable({
  cells,
  onSync,
  onArchive,
  onDelete,
  onVSCode,
}: {
  cells: Cell[];
  onSync: (id: string) => void;
  onArchive: (id: string) => void;
  onDelete: (id: string) => void;
  onVSCode: (id: string) => void;
}) {
  const [showArchived, setShowArchived] = useState(false);
  const activeCells = cells.filter((c) => c.status !== "archived");
  const archivedCells = cells.filter((c) => c.status === "archived");

  const renderRows = (rows: Cell[]) =>
    rows.map((cell) => (
      <CellRow
        key={cell.id}
        cell={cell}
        onSync={() => onSync(cell.id)}
        onArchive={() => onArchive(cell.id)}
        onDelete={() => onDelete(cell.id)}
        onVSCode={() => onVSCode(cell.id)}
      />
    ));

  if (activeCells.length === 0 && archivedCells.length === 0) {
    return (
      <div className="muted" style={{ padding: "12px 16px" }}>
        No cells
      </div>
    );
  }

  return (
    <>
      {activeCells.length > 0 ? (
        <table className="table">
          <thead className="table__head">
            <tr>
              <th className="table__header-cell">Branch</th>
              <th className="table__header-cell">PR</th>
              <th className="table__header-cell">CI</th>
              <th className="table__header-cell">Tend</th>
              <th className="table__header-cell">Actions</th>
            </tr>
          </thead>
          <tbody>{renderRows(activeCells)}</tbody>
        </table>
      ) : (
        <div className="muted" style={{ padding: "12px 16px" }}>
          No active cells
        </div>
      )}
      {archivedCells.length > 0 && (
        <div
          className="cells-page__archived-toggle"
          onClick={() => setShowArchived(!showArchived)}
        >
          <span className="cells-page__archived-arrow">
            {showArchived ? "▾" : "▸"}
          </span>
          Archived ({archivedCells.length})
        </div>
      )}
      {showArchived && archivedCells.length > 0 && (
        <table className="table cells-page__archived-table">
          <tbody>{renderRows(archivedCells)}</tbody>
        </table>
      )}
    </>
  );
}

function CellsPage() {
  const { run } = useOutletContext<LayoutContext>();
  const navigate = useNavigate();
  const [cells, setCells] = useState<Cell[]>([]);
  const [repos, setRepos] = useState<Repo[]>([]);
  const [runs, setRuns] = useState<DaemonRun[]>([]);
  const [importOpen, setImportOpen] = useState(false);

  const loadCells = useCallback(async () => {
    setCells(await fetchCells());
  }, []);

  useEffect(() => {
    fetchRepos().then(setRepos);
  }, []);

  useEffect(() => {
    loadCells();
    fetchDaemonRuns(10).then(setRuns);
  }, [loadCells, run]);

  const handleNewLocalCell = async (repo: string) => {
    const cell = await createLocalCell(repo);
    const session = await createInteractiveSession(cell.id);
    navigate(`/cells/${cell.id}`, { state: { autoFocusSessionId: session.id } });
  };

  // Group cells by repo, include repos with no cells
  const grouped = new Map<string, Cell[]>();
  for (const repo of repos) {
    grouped.set(repo.id, []);
  }
  for (const cell of cells) {
    if (!grouped.has(cell.repo)) grouped.set(cell.repo, []);
    grouped.get(cell.repo)!.push(cell);
  }

  return (
    <>
      <div className="page-header">
        <div className="page-title">Cells</div>
        {!importOpen && (
          <div className="btn btn--gray" onClick={() => setImportOpen(true)}>
            Import Cell
          </div>
        )}
      </div>
      <CreateCellForm
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onCreated={loadCells}
      />

      {repos.length === 0 ? (
        <div className="empty-state">
          <div>No repos configured. Add repos to config.toml to get started.</div>
        </div>
      ) : (
        <div className="cells-page__groups">
          {[...grouped.entries()].map(([repo, repoCells]) => (
            <div key={repo} className="card card--clipped">
              <div className="cells-page__group-header">
                <div className="cells-page__group-title">
                  {repo}
                </div>
                <div
                  className="btn btn--sm btn--blue cells-page__add-btn"
                  onClick={() => handleNewLocalCell(repo)}
                  title="New cell"
                >
                  +
                </div>
              </div>
              <CellTable
                cells={repoCells}
                onSync={(id) => triggerSync(id)}
                onArchive={async (id) => {
                  await archiveCell(id);
                  loadCells();
                }}
                onDelete={async (id) => {
                  await deleteCell(id);
                  loadCells();
                }}
                onVSCode={(id) => openInVSCode(id)}
              />
            </div>
          ))}
        </div>
      )}

      <DaemonLog runs={runs} />
    </>
  );
}

function CellDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { run } = useOutletContext<LayoutContext>();
  const [cell, setCell] = useState<(Cell & { sessions: Session[] }) | null>(
    null,
  );
  const [launching, setLaunching] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    (location.state as any)?.autoFocusSessionId ?? null,
  );

  const load = useCallback(async () => {
    if (!id) return;
    setCell(await fetchCell(id));
  }, [id]);

  useEffect(() => {
    load();
  }, [load, run]);

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
              {cell.repo}{" "}
              <span className="cell-detail__title-branch">
                / {cell.branch}
              </span>
            </div>
            <div className="cell-detail__badges">
              {cell.status === "archived" && (
                <StatusBadge status="archived" label="Archived" />
              )}
              <StatusBadge
                status={cell.ci_status}
                label={`CI: ${cell.ci_status}`}
              />
              <StatusBadge
                status={cell.tend_status}
                label={`Tend: ${cell.tend_status}`}
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
              Tend
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
                  alive={selectedSession.alive}
                  autoFocus={selectedSession.id === selectedSessionId}
                  onResume={() => handleResumeSession(selectedSession.id)}
                  fetchXtermState={() => fetchXtermState(cell.id, selectedSession.id)}
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
                onVSCode={async () => {
                  await openSessionInVSCode(cell.id, s.id);
                  load();
                }}
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
  onVSCode,
}: {
  session: Session;
  cellId: string;
  onResume: () => void;
  onVSCode: () => void;
}) {
  const [expanded, setExpanded] = useState(session.alive);

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
            onVSCode();
          }}
          className="btn btn--xs btn--soft-gray collapsible-session__resume"
        >
          VS Code
        </div>
        {!session.alive && (
          <div
            onClick={(e) => {
              e.stopPropagation();
              onResume();
            }}
            className="btn btn--xs btn--soft-blue collapsible-session__resume"
          >
            Resume
          </div>
        )}
      </div>
      {expanded && (
        <div className="collapsible-session__body">
          <Terminal
            sessionId={session.id}
            alive={session.alive}
            onResume={async () => onResume()}
            fetchXtermState={() => fetchXtermState(cellId, session.id)}
          />
        </div>
      )}
    </div>
  );
}

// --- Sorties ---

function SortieRow({ sortie }: { sortie: Sortie & { cell_count: number } }) {
  const navigate = useNavigate();
  return (
    <tr
      className="sortie-row"
      onClick={() => navigate(`/sorties/${sortie.id}`)}
    >
      <td className="table__cell sortie-row__meta">
        {sortie.cell_count} cell{sortie.cell_count !== 1 && "s"}
      </td>
      <td className="table__cell sortie-row__date">
        {new Date(sortie.created_at).toLocaleDateString()}
      </td>
    </tr>
  );
}

function SortiesPage() {
  const navigate = useNavigate();
  const { run } = useOutletContext<LayoutContext>();
  const [sorties, setSorties] = useState<(Sortie & { cell_count: number })[]>(
    [],
  );
  const [creating, setCreating] = useState(false);

  const loadSorties = useCallback(async () => {
    setSorties(await fetchSorties());
  }, []);

  useEffect(() => {
    loadSorties();
  }, [loadSorties, run]);

  const handleNewSortie = async () => {
    setCreating(true);
    try {
      const sortie = await createSortie();
      navigate(`/sorties/${sortie.id}`);
    } finally {
      setCreating(false);
    }
  };

  return (
    <>
      <div className="page-header">
        <div className="page-title">Sorties</div>
        <div
          className={`btn btn--blue${creating ? " btn--disabled" : ""}`}
          onClick={creating ? undefined : handleNewSortie}
        >
          {creating ? "Creating..." : "New Sortie"}
        </div>
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
                <th className="table__header-cell">Cells</th>
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
  const { run } = useOutletContext<LayoutContext>();
  const [sortie, setSortie] = useState<(Sortie & { cells: Cell[] }) | null>(
    null,
  );

  const startingRef = useRef(false);

  const load = useCallback(async () => {
    if (!id) return;
    setSortie(await fetchSortie(id));
  }, [id]);

  useEffect(() => {
    load();
  }, [load, run]);

  // Auto-start session on first load (PTY not yet spawned).
  // Uses empty transcript (not ended_at) to detect "never started", because
  // _cleanup_stale_sessions may set ended_at on server restart.
  useEffect(() => {
    if (
      sortie?.session &&
      !sortie.session.alive &&
      !sortie.session.transcript &&
      !startingRef.current
    ) {
      startingRef.current = true;
      startSortieSession(sortie.id, sortie.session.id).then(() => load());
    }
  }, [sortie]);

  if (!sortie) return null;

  const sessionReady =
    sortie.session && (sortie.session.alive || sortie.session.transcript);

  return (
    <div>
      <div className="back-link" onClick={() => navigate(-1)}>
        &larr; Back to sorties
      </div>
      <div className="card sortie-detail__card">
        <div className="sortie-detail__header">
          <div className="sortie-detail__title">Sortie</div>
          <div className="sortie-detail__actions">
            <div
              className="btn btn--md btn--gray"
              onClick={() => openSortieInVSCode(sortie.id)}
            >
              VS Code
            </div>
            <OverflowMenu
              items={[
                {
                  label: "Delete",
                  onClick: async () => {
                    await deleteSortie(sortie.id);
                    navigate("/sorties");
                  },
                  danger: true,
                },
              ]}
            />
          </div>
        </div>
        <div className="sortie-detail__info">
          <div>
            <span className="sortie-detail__label">Created:</span>{" "}
            {new Date(sortie.created_at).toLocaleString()}
          </div>
        </div>
        {sortie.session && !sessionReady && (
          <div className="muted" style={{ padding: "12px 0" }}>
            Starting session...
          </div>
        )}
        {sessionReady && (
          <Terminal
            sessionId={sortie.session!.id}
            alive={sortie.session!.alive}
            onResume={async () => {
              await resumeSortieSession(sortie.id, sortie.session!.id);
              load();
            }}
            fetchXtermState={() => fetchSortieXtermState(sortie.id, sortie.session!.id)}
          />
        )}
      </div>

      <div className="sortie-detail__cells-header">
        Cells ({sortie.cells.length})
      </div>
      {sortie.cells.length === 0 ? (
        <div className="muted">
          No cells yet.
        </div>
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
                      {cell.repo}
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
