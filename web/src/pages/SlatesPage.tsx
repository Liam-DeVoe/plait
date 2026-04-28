import { useEffect, useState, useCallback } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import {
  fetchSlates,
  createSlate,
  archiveSlate,
  unarchiveSlate,
  deleteSlate,
  type Slate,
} from "../api";
import { OverflowMenu, navigateTo } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

function SlateRow({
  slate,
  onArchive,
  onUnarchive,
  onDelete,
}: {
  slate: Slate & { worktop_count: number };
  onArchive: () => void;
  onUnarchive: () => void;
  onDelete: () => void;
}) {
  const navigate = useNavigate();

  const menuItems = slate.is_archived
    ? [
        { label: "Unarchive", onClick: onUnarchive },
        { label: "Delete", onClick: onDelete, danger: true },
      ]
    : [
        { label: "Archive", onClick: onArchive },
        { label: "Delete", onClick: onDelete, danger: true },
      ];

  return (
    <tr
      className="slate-row"
      onClick={(e) => navigateTo(e, `/slates/${slate.id}`, navigate)}
    >
      <td className="table__worktop slate-row__name">
        {slate.name || <span className="muted">{slate.id.slice(0, 8)}</span>}
      </td>
      <td className="table__worktop slate-row__meta">
        {slate.worktop_count} worktop{slate.worktop_count !== 1 && "s"}
      </td>
      <td className="table__worktop slate-row__date">
        {new Date(slate.created_at).toLocaleDateString()}
      </td>
      <td className="table__worktop">
        <div className="slate-row__actions" onClick={(e) => e.stopPropagation()}>
          <OverflowMenu items={menuItems} />
        </div>
      </td>
    </tr>
  );
}

export default function SlatesPage() {
  const navigate = useNavigate();
  const { run } = useOutletContext<LayoutContext>();
  const [slates, setSlates] = useState<(Slate & { worktop_count: number })[]>(
    [],
  );
  const [creating, setCreating] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

  const loadSlates = useCallback(async () => {
    setSlates(await fetchSlates());
  }, []);

  useEffect(() => {
    loadSlates();
  }, [loadSlates, run]);

  const handleNewSlate = async () => {
    setCreating(true);
    try {
      const slate = await createSlate();
      navigate(`/slates/${slate.id}`);
    } finally {
      setCreating(false);
    }
  };

  const activeSlates = slates.filter((s) => !s.is_archived);
  const archivedSlates = slates.filter((s) => s.is_archived);

  return (
    <>
      <div className="page-header">
        <div className="page-title">Slates</div>
        <div
          className={`btn btn--blue${creating ? " btn--disabled" : ""}`}
          onClick={creating ? undefined : handleNewSlate}
        >
          {creating ? "Creating..." : "New Slate"}
        </div>
      </div>

      {slates.length === 0 ? (
        <div className="empty-state">
          <div>No slates yet.</div>
        </div>
      ) : (
        <div className="card card--clipped">
          {activeSlates.length > 0 ? (
            <table className="table">
              <thead className="table__head">
                <tr>
                  <th className="table__header-worktop">Name</th>
                  <th className="table__header-worktop">Worktops</th>
                  <th className="table__header-worktop">Created</th>
                  <th className="table__header-worktop"></th>
                </tr>
              </thead>
              <tbody>
                {activeSlates.map((s) => (
                  <SlateRow
                    key={s.id}
                    slate={s}
                    onArchive={async () => {
                      await archiveSlate(s.id);
                      loadSlates();
                    }}
                    onUnarchive={async () => {
                      await unarchiveSlate(s.id);
                      loadSlates();
                    }}
                    onDelete={async () => {
                      await deleteSlate(s.id);
                      loadSlates();
                    }}
                  />
                ))}
              </tbody>
            </table>
          ) : (
            <div className="muted" style={{ padding: "12px 16px" }}>
              No active slates
            </div>
          )}
          {archivedSlates.length > 0 && (
            <div
              className="slates-page__archived-toggle"
              onClick={() => setShowArchived(!showArchived)}
            >
              <span className="slates-page__archived-arrow">
                {showArchived ? "▾" : "▸"}
              </span>
              Archived ({archivedSlates.length})
            </div>
          )}
          {showArchived && archivedSlates.length > 0 && (
            <table className="table slates-page__archived-table">
              <tbody>
                {archivedSlates.map((s) => (
                  <SlateRow
                    key={s.id}
                    slate={s}
                    onArchive={async () => {
                      await archiveSlate(s.id);
                      loadSlates();
                    }}
                    onUnarchive={async () => {
                      await unarchiveSlate(s.id);
                      loadSlates();
                    }}
                    onDelete={async () => {
                      await deleteSlate(s.id);
                      loadSlates();
                    }}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  );
}
