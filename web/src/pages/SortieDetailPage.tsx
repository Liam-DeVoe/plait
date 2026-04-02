import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useNavigate, useOutletContext } from "react-router-dom";
import {
  fetchSortie,
  deleteSortie,
  openSortieInVSCode,
  fetchSortieXtermState,
  startSortieSession,
  resumeSortieSession,
  type Cell,
  type Sortie,
} from "../api";
import Terminal from "../Terminal";
import { StatusBadge, OverflowMenu } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

export default function SortieDetailPage() {
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
