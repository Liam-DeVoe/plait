import { useEffect, useState, useCallback } from "react";
import { useNavigate, NavLink, useOutletContext } from "react-router-dom";
import {
  fetchCells,
  fetchDaemonRuns,
  fetchRepos,
  triggerDaemonRun,
  createCell,
  createLocalCell,
  archiveCell,
  triggerSync,
  deleteCell,
  openInVSCode,
  createInteractiveSession,
  type Cell,
  type DaemonRun,
  type Repo,
} from "../api";
import { StatusBadge, OverflowMenu, timeAgo, navigateTo } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

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
      onClick={(e) => navigateTo(e, `/cells/${cell.id}`, navigate)}
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

export default function CellsPage() {
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
