import { useEffect, useState, useCallback } from "react";
import {
  useParams,
  useNavigate,
  useLocation,
  useOutletContext,
  Link,
} from "react-router-dom";
import {
  fetchCell,
  archiveCell,
  triggerSync,
  deleteCell,
  openInVSCode,
  openSessionInVSCode,
  createInteractiveSession,
  deleteSession,
  resumeSession,
  fetchXtermState,
  type Cell,
  type Session,
} from "../api";
import Terminal from "../Terminal";
import { StatusBadge, OverflowMenu } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

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

export default function CellDetailPage() {
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
      <Link to="/cells" className="back-link">
        &larr; Back
      </Link>
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
                <StatusBadge
                  status={cell.archive_reason === "merged" ? "completed" : "archived"}
                  label={cell.archive_reason === "merged" ? "Merged" : cell.archive_reason === "closed" ? "Closed" : "Archived"}
                />
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
