import { useEffect, useState } from "react";
import {
  useNavigate,
  useLocation,
  useLoaderData,
  useRevalidator,
  Link,
  type LoaderFunctionArgs,
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
  forkSession,
  fetchXtermState,
  type Session,
} from "../api";
import Terminal from "../Terminal";
import { StatusBadge, OverflowMenu, PrPill } from "../components/shared";

export async function worktopDetailLoader({ params }: LoaderFunctionArgs) {
  const id = params.id!;
  const [worktop, repos] = await Promise.all([fetchWorktop(id), fetchRepos()]);
  return { worktop, repos };
}

function CollapsibleSession({
  session,
  worktopId,
  onResume,
  onVSCode,
  onFork,
}: {
  session: Session;
  worktopId: string;
  onResume: () => void;
  onVSCode: () => void;
  onFork: () => void;
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
        <div
          onClick={(e) => {
            e.stopPropagation();
            onFork();
          }}
          className="btn btn--xs btn--soft-gray collapsible-session__resume"
          title="Spawn a new session forked from this one. The original is unaffected."
        >
          Fork
        </div>
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
  const { worktop, repos } = useLoaderData() as Awaited<
    ReturnType<typeof worktopDetailLoader>
  >;
  const navigate = useNavigate();
  const location = useLocation();
  const revalidator = useRevalidator();
  const [launching, setLaunching] = useState(false);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    (location.state as any)?.autoFocusSessionId ?? null,
  );

  const refresh = () => revalidator.revalidate();

  useEffect(() => {
    document.title = `${worktop.branch} | Plait`;
    return () => {
      document.title = "Plait";
    };
  }, [worktop.branch]);

  const handleLaunchSession = async () => {
    setLaunching(true);
    try {
      const session = await createInteractiveSession(worktop.id);
      setSelectedSessionId(session.id);
      refresh();
    } finally {
      setLaunching(false);
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    await deleteSession(worktop.id, sessionId);
    if (selectedSessionId === sessionId) setSelectedSessionId(null);
    refresh();
  };

  const handleResumeSession = async (sessionId: string) => {
    await resumeSession(worktop.id, sessionId);
    refresh();
  };

  const handleForkSession = async (sessionId: string) => {
    const fork = await forkSession(worktop.id, sessionId);
    // Focus the new session so the user lands inside the fork.
    setSelectedSessionId(fork.id);
    refresh();
  };

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
          </div>
          <div className="worktop-detail__actions">
            {worktop.pr_url && <PrPill worktop={worktop} size="md" />}
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
                        refresh();
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
                    className="btn btn--sm btn--soft-gray"
                    onClick={() => handleForkSession(selectedSession.id)}
                    title="Spawn a new session forked from this one. The original is unaffected."
                  >
                    Fork
                  </div>
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
                  refresh();
                }}
                onFork={() => handleForkSession(s.id)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
