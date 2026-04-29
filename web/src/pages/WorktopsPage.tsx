import { useState } from "react";
import { useNavigate, NavLink, useLoaderData, useRevalidator } from "react-router-dom";
import {
  fetchWorktops,
  fetchDaemonRuns,
  fetchRepos,
  triggerDaemonRun,
  createWorktop,
  createLocalWorktop,
  archiveWorktop,
  triggerSync,
  deleteWorktop,
  openInVSCode,
  createInteractiveSession,
  type Worktop,
  type DaemonRun,
} from "../api";
import { StatusBadge, OverflowMenu, timeAgo, navigateTo } from "../components/shared";

export async function worktopsLoader() {
  const [worktops, repos, runs] = await Promise.all([
    fetchWorktops(),
    fetchRepos(),
    fetchDaemonRuns(10),
  ]);
  return { worktops, repos, runs };
}

function WorktopRow({
  worktop,
  isLocal,
  onSync,
  onArchive,
  onDelete,
  onVSCode,
}: {
  worktop: Worktop;
  isLocal: boolean;
  onSync: () => void;
  onArchive: () => void;
  onDelete: () => void;
  onVSCode: () => void;
}) {
  const navigate = useNavigate();
  const needsAttention = !isLocal && worktop.ci_status === "failing";

  return (
    <tr
      className={`worktop-row${needsAttention ? " worktop-row--attention" : ""}`}
      onClick={(e) => navigateTo(e, `/worktops/${worktop.id}`, navigate)}
    >
      <td className="table__worktop">
        <div className="worktop-row__branch">{worktop.branch}</div>
      </td>
      <td className="table__worktop">
        {isLocal ? (
          <span className="worktop-row__no-pr">—</span>
        ) : worktop.pr_url ? (
          <a
            href={worktop.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="link"
            onClick={(e) => e.stopPropagation()}
          >
            #{worktop.pr_number}
          </a>
        ) : (
          <span className="worktop-row__no-pr">no PR</span>
        )}
      </td>
      <td className="table__worktop">
        {isLocal ? (
          <span className="worktop-row__no-pr">—</span>
        ) : (
          <StatusBadge status={worktop.ci_status} label={`CI: ${worktop.ci_status}`} />
        )}
      </td>
      <td className="table__worktop">
        <StatusBadge
          status={worktop.tend_status}
          label={`Tend: ${worktop.tend_status}`}
        />
      </td>
      <td className="table__worktop">
        <div className="worktop-row__actions" onClick={(e) => e.stopPropagation()}>
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

function CreateWorktopForm({
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
      await createWorktop(prUrl);
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
  const okCount = run.results.filter((r) => r.decision === "ok").length;

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
  if (okCount > 0) parts.push(`${okCount} ok`);
  const deferred = run.results.filter(
    (r) => r.decision === "deferred",
  ).length;
  if (deferred > 0) parts.push(`${deferred} deferred`);
  const warned = run.results.filter(
    (r) => r.warnings?.length > 0,
  ).length;
  if (warned > 0) parts.push(`${warned} warned`);

  return parts.join(", ");
}

function DaemonLog({ runs }: { runs: DaemonRun[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

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
                setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(run.id)) next.delete(run.id);
                  else next.add(run.id);
                  return next;
                })
              }
            >
              <span className="daemon-log__run-arrow">
                {expanded.has(run.id) ? "▾" : "▸"}
              </span>
              <span className="daemon-log__run-time">
                {timeAgo(run.started_at)}
              </span>
              <span className="daemon-log__run-summary">
                {runSummary(run)}
              </span>
            </div>
            {expanded.has(run.id) && (
              <div className="daemon-log__run-details">
                {run.results.map((r) => (
                  <div key={r.worktop_id} className="daemon-log__worktop-result">
                    <NavLink
                      to={`/worktops/${r.worktop_id}`}
                      className="daemon-log__worktop-name"
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
                          : r.decision === "skipped" ||
                              r.decision === "deferred"
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
                    {r.warnings?.length > 0 && (
                      <span className="daemon-log__warnings">
                        {r.warnings.join(", ")}
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

function WorktopTable({
  worktops,
  isLocal,
  onSync,
  onArchive,
  onDelete,
  onVSCode,
}: {
  worktops: Worktop[];
  isLocal: boolean;
  onSync: (id: string) => void;
  onArchive: (id: string) => void;
  onDelete: (id: string) => void;
  onVSCode: (id: string) => void;
}) {
  const [showArchived, setShowArchived] = useState(false);
  const activeWorktops = worktops.filter((c) => c.status !== "archived");
  const archivedWorktops = worktops.filter((c) => c.status === "archived");

  const renderRows = (rows: Worktop[]) =>
    rows.map((worktop) => (
      <WorktopRow
        key={worktop.id}
        worktop={worktop}
        isLocal={isLocal}
        onSync={() => onSync(worktop.id)}
        onArchive={() => onArchive(worktop.id)}
        onDelete={() => onDelete(worktop.id)}
        onVSCode={() => onVSCode(worktop.id)}
      />
    ));

  if (activeWorktops.length === 0 && archivedWorktops.length === 0) {
    return (
      <div className="muted" style={{ padding: "12px 16px" }}>
        No worktops
      </div>
    );
  }

  return (
    <>
      {activeWorktops.length > 0 ? (
        <table className="table">
          <thead className="table__head">
            <tr>
              <th className="table__header-worktop">Branch</th>
              <th className="table__header-worktop">PR</th>
              <th className="table__header-worktop">CI</th>
              <th className="table__header-worktop">Tend</th>
              <th className="table__header-worktop">Actions</th>
            </tr>
          </thead>
          <tbody>{renderRows(activeWorktops)}</tbody>
        </table>
      ) : (
        <div className="muted" style={{ padding: "12px 16px" }}>
          No open worktops
        </div>
      )}
      {archivedWorktops.length > 0 && (
        <div
          className="worktops-page__archived-toggle"
          onClick={() => setShowArchived(!showArchived)}
        >
          <span className="worktops-page__archived-arrow">
            {showArchived ? "▾" : "▸"}
          </span>
          Archived ({archivedWorktops.length})
        </div>
      )}
      {showArchived && archivedWorktops.length > 0 && (
        <table className="table worktops-page__archived-table">
          <tbody>{renderRows(archivedWorktops)}</tbody>
        </table>
      )}
    </>
  );
}

export default function WorktopsPage() {
  const { worktops, repos, runs } = useLoaderData() as Awaited<
    ReturnType<typeof worktopsLoader>
  >;
  const navigate = useNavigate();
  const revalidator = useRevalidator();
  const [importOpen, setImportOpen] = useState(false);

  const refresh = () => revalidator.revalidate();

  const handleNewLocalWorktop = async (repo: string) => {
    const worktop = await createLocalWorktop(repo);
    const session = await createInteractiveSession(worktop.id);
    navigate(`/worktops/${worktop.id}`, { state: { autoFocusSessionId: session.id } });
  };

  // Group worktops by repo, include repos with no worktops
  const grouped = new Map<string, Worktop[]>();
  for (const repo of repos) {
    grouped.set(repo.id, []);
  }
  for (const worktop of worktops) {
    if (!grouped.has(worktop.repo)) grouped.set(worktop.repo, []);
    grouped.get(worktop.repo)!.push(worktop);
  }
  const repoKind = new Map<string, "remote" | "local">();
  for (const repo of repos) repoKind.set(repo.id, repo.kind);

  return (
    <>
      <div className="page-header">
        <div className="page-title">Worktops</div>
        {!importOpen && (
          <div className="btn btn--gray" onClick={() => setImportOpen(true)}>
            Import Worktop
          </div>
        )}
      </div>
      <CreateWorktopForm
        open={importOpen}
        onClose={() => setImportOpen(false)}
        onCreated={refresh}
      />

      {repos.length === 0 ? (
        <div className="empty-state">
          <div>No repos configured. Add repos to config.toml to get started.</div>
        </div>
      ) : (
        <div className="worktops-page__groups">
          {[...grouped.entries()].map(([repo, repoWorktops]) => {
            const isLocal = repoKind.get(repo) === "local";
            return (
              <div key={repo} className="card card--clipped">
                <div className="worktops-page__group-header">
                  <div className="worktops-page__group-title">
                    {repo}
                    {isLocal && (
                      <StatusBadge status="local" label="local" />
                    )}
                  </div>
                  <div
                    className="btn btn--sm btn--blue worktops-page__add-btn"
                    onClick={() => handleNewLocalWorktop(repo)}
                    title="New worktop"
                  >
                    +
                  </div>
                </div>
                <WorktopTable
                  worktops={repoWorktops}
                  isLocal={isLocal}
                  onSync={(id) => triggerSync(id)}
                  onArchive={async (id) => {
                    await archiveWorktop(id);
                    refresh();
                  }}
                  onDelete={async (id) => {
                    await deleteWorktop(id);
                    refresh();
                  }}
                  onVSCode={(id) => openInVSCode(id)}
                />
              </div>
            );
          })}
        </div>
      )}

      <DaemonLog runs={runs} />
    </>
  );
}
