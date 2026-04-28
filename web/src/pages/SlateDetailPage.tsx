import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useNavigate, useOutletContext, Link } from "react-router-dom";
import {
  fetchSlate,
  archiveSlate,
  unarchiveSlate,
  deleteSlate,
  openSlateInVSCode,
  fetchSlateXtermState,
  startSlateSession,
  resumeSlateSession,
  type Worktop,
  type Slate,
} from "../api";
import Terminal from "../Terminal";
import { StatusBadge, OverflowMenu, navigateTo } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

export default function SlateDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { run } = useOutletContext<LayoutContext>();
  const [slate, setSlate] = useState<(Slate & { worktops: Worktop[] }) | null>(
    null,
  );

  const startingRef = useRef(false);

  const load = useCallback(async () => {
    if (!id) return;
    setSlate(await fetchSlate(id));
  }, [id]);

  useEffect(() => {
    load();
  }, [load, run]);

  // Auto-start session on first load (PTY not yet spawned).
  // Uses empty transcript (not ended_at) to detect "never started", because
  // _cleanup_stale_sessions may set ended_at on server restart.
  useEffect(() => {
    if (
      slate?.session &&
      !slate.session.alive &&
      !slate.session.transcript &&
      !startingRef.current
    ) {
      startingRef.current = true;
      startSlateSession(slate.id, slate.session.id).then(() => load());
    }
  }, [slate]);

  if (!slate) return null;

  const sessionReady =
    slate.session && (slate.session.alive || slate.session.transcript);

  return (
    <div>
      <Link to="/slates" className="back-link">
        &larr; Back to slates
      </Link>
      <div className="card slate-detail__card">
        <div className="slate-detail__header">
          <div className="slate-detail__title">{slate.name || "Slate"}</div>
          <div className="slate-detail__actions">
            <div
              className="btn btn--md btn--gray"
              onClick={() => openSlateInVSCode(slate.id)}
            >
              VS Code
            </div>
            <OverflowMenu
              items={[
                slate.is_archived
                  ? {
                      label: "Unarchive",
                      onClick: async () => {
                        await unarchiveSlate(slate.id);
                        load();
                      },
                    }
                  : {
                      label: "Archive",
                      onClick: async () => {
                        await archiveSlate(slate.id);
                        load();
                      },
                    },
                {
                  label: "Delete",
                  onClick: async () => {
                    await deleteSlate(slate.id);
                    navigate("/slates");
                  },
                  danger: true,
                },
              ]}
            />
          </div>
        </div>
        <div className="slate-detail__info">
          <div>
            <span className="slate-detail__label">Created:</span>{" "}
            {new Date(slate.created_at).toLocaleString()}
          </div>
        </div>
        {slate.session && !sessionReady && (
          <div className="muted" style={{ padding: "12px 0" }}>
            Starting session...
          </div>
        )}
        {sessionReady && (
          <Terminal
            sessionId={slate.session!.id}
            alive={slate.session!.alive}
            onResume={async () => {
              await resumeSlateSession(slate.id, slate.session!.id);
              load();
            }}
            fetchXtermState={() => fetchSlateXtermState(slate.id, slate.session!.id)}
          />
        )}
      </div>

      <div className="slate-detail__worktops-header">
        Worktops ({slate.worktops.length})
      </div>
      {slate.worktops.length === 0 ? (
        <div className="muted">
          No worktops yet.
        </div>
      ) : (
        (() => {
          const activeWorktops = slate.worktops.filter((c) => c.status !== "archived");
          const archivedWorktops = slate.worktops.filter((c) => c.status === "archived");

          const renderWorktopRow = (worktop: Worktop) => (
            <tr
              key={worktop.id}
              className="slate-detail__worktop-row"
              onClick={(e) => navigateTo(e, `/worktops/${worktop.id}`, navigate)}
            >
              <td className="table__worktop">
                <div className="slate-detail__worktop-repo">
                  {worktop.repo}
                </div>
                <div className="slate-detail__worktop-branch">
                  {worktop.branch}
                </div>
              </td>
              <td className="table__worktop">
                {worktop.pr_url ? (
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
                  <span className="worktop-row__no-pr">pending</span>
                )}
              </td>
              <td className="table__worktop">
                <StatusBadge
                  status={
                    worktop.status === "archived"
                      ? worktop.archive_reason === "merged" ? "completed" : worktop.archive_reason === "closed" ? "closed" : "archived"
                      : worktop.status
                  }
                  label={
                    worktop.status === "archived"
                      ? worktop.archive_reason === "merged" ? "merged" : worktop.archive_reason === "closed" ? "closed" : "archived"
                      : worktop.status
                  }
                />
              </td>
              <td className="table__worktop">
                <StatusBadge
                  status={worktop.ci_status}
                  label={`CI: ${worktop.ci_status}`}
                />
              </td>
            </tr>
          );

          return (
            <div className="card card--clipped">
              <table className="table">
                <thead className="table__head">
                  <tr>
                    <th className="table__header-worktop">Repo / Branch</th>
                    <th className="table__header-worktop">PR</th>
                    <th className="table__header-worktop">Status</th>
                    <th className="table__header-worktop">CI</th>
                  </tr>
                </thead>
                <tbody>
                  {activeWorktops.map(renderWorktopRow)}
                  {archivedWorktops.length > 0 && (
                    <tr className="slate-detail__section-header">
                      <td colSpan={4}>Archived</td>
                    </tr>
                  )}
                  {archivedWorktops.map(renderWorktopRow)}
                </tbody>
              </table>
            </div>
          );
        })()
      )}
    </div>
  );
}
