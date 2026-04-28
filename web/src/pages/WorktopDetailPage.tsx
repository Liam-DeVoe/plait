import { useEffect, useState, useCallback } from "react";
import {
  useParams,
  useNavigate,
  useLocation,
  useOutletContext,
  Link,
} from "react-router-dom";
import {
  fetchWorktop,
  fetchRepos,
  archiveWorktop,
  triggerSync,
  deleteWorktop,
  openInVSCode,
  openSessionInVSCode,
  createInteractiveSession,
  deleteSession,
  resumeSession,
  fetchXtermState,
  type Worktop,
  type Session,
  type Repo,
} from "../api";
import Terminal from "../Terminal";
import { StatusBadge, OverflowMenu } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

function CollapsibleSession({
  session,
  worktopId,
  onResume,
  onVSCode,
}: {
  session: Session;
  worktopId: string;
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
            fetchXtermState={() => fetchXtermState(worktopId, session.id)}
          />
        </div>
      )}
    </div>
  );
}

export default function WorktopDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const { run } = useOutletContext<LayoutContext>();
  const [worktop, setWorktop] = useState<(Worktop & { sessions: Session[] }) | null>(
    null,
  );
  const [repos, setRepos] = useState<Repo[]>([]);
  const [launching, setLaunching] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    (location.state as any)?.autoFocusSessionId ?? null,
  );

  const load = useCallback(async () => {
    if (!id) return;
    setWorktop(await fetchWorktop(id));
  }, [id]);

  useEffect(() => {
    load();
  }, [load, run]);

  useEffect(() => {
    fetchRepos().then(setRepos);
  }, []);

  useEffect(() => {
    if (worktop) {
      document.title = `${worktop.branch} | Plait`;
    }
    return () => {
      document.title = "Plait";
    };
  }, [worktop?.branch]);

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

  if (!worktop) return null;

  const isLocal = repos.find((r) => r.id === worktop.repo)?.kind === "local";
  const userSessions = worktop.sessions.filter((s) => s.role === "user");
  const daemonSessions = worktop.sessions.filter((s) => s.role === "daemon");

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
      <Link to={worktop.slate_id ? `/slates/${worktop.slate_id}` : "/worktops"} className="back-link">
        &larr; Back
      </Link>
      <div className="card worktop-detail__card">
        <div className="worktop-detail__header">
          <div>
            <div className="worktop-detail__title">
              {worktop.repo}{" "}
              <span className="worktop-detail__title-branch">
                / {worktop.branch}
              </span>
            </div>
            <div className="worktop-detail__badges">
              {worktop.status === "archived" && (
                <StatusBadge
                  status={worktop.archive_reason === "merged" ? "completed" : "archived"}
                  label={worktop.archive_reason === "merged" ? "Merged" : worktop.archive_reason === "closed" ? "Closed" : "Archived"}
                />
              )}
              {isLocal && <StatusBadge status="local" label="local" />}
              {!isLocal && (
                <StatusBadge
                  status={worktop.ci_status}
                  label={`CI: ${worktop.ci_status}`}
                />
              )}
              <StatusBadge
                status={worktop.tend_status}
                label={`Tend: ${worktop.tend_status}`}
              />
            </div>
            {worktop.pr_url && (
              <div className="worktop-detail__pr">
                <a
                  href={worktop.pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="link"
                >
                  PR #{worktop.pr_number} ↗
                </a>
              </div>
            )}
          </div>
          <div className="worktop-detail__actions">
            <div
              className="btn btn--md btn--gray"
              onClick={() => openInVSCode(worktop.id)}
            >
              VS Code
            </div>
            <div
              className="btn btn--md btn--soft-blue"
              onClick={() => triggerSync(worktop.id)}
            >
              Tend
            </div>
            <OverflowMenu
              items={[
                {
                  label: "Archive",
                  onClick: async () => {
                    await archiveWorktop(worktop.id);
                    navigate("/worktops");
                  },
                },
                {
                  label: "Delete",
                  onClick: async () => {
                    await deleteWorktop(worktop.id);
                    navigate("/worktops");
                  },
                  danger: true,
                },
              ]}
            />
          </div>
        </div>
      </div>

      <div className="worktop-detail__sessions-header">
        <div className="worktop-detail__sessions-title">Sessions</div>
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
                        await openSessionInVSCode(worktop.id, selectedSession.id);
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
                  fetchXtermState={() => fetchXtermState(worktop.id, selectedSession.id)}
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
          <div className="worktop-detail__daemon-title">Daemon sessions</div>
          <div className="worktop-detail__daemon-list">
            {[...daemonSessions].reverse().map((s) => (
              <CollapsibleSession
                key={s.id}
                session={s}
                worktopId={worktop.id}
                onResume={() => handleResumeSession(s.id)}
                onVSCode={async () => {
                  await openSessionInVSCode(worktop.id, s.id);
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
