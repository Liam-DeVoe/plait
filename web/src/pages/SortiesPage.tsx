import { useEffect, useState, useCallback } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import {
  fetchSorties,
  createSortie,
  archiveSortie,
  unarchiveSortie,
  deleteSortie,
  type Sortie,
} from "../api";
import { OverflowMenu, navigateTo } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

function SortieRow({
  sortie,
  onArchive,
  onUnarchive,
  onDelete,
}: {
  sortie: Sortie & { cell_count: number };
  onArchive: () => void;
  onUnarchive: () => void;
  onDelete: () => void;
}) {
  const navigate = useNavigate();

  const menuItems = sortie.is_archived
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
      className="sortie-row"
      onClick={(e) => navigateTo(e, `/sorties/${sortie.id}`, navigate)}
    >
      <td className="table__cell sortie-row__name">
        {sortie.name || <span className="muted">{sortie.id.slice(0, 8)}</span>}
      </td>
      <td className="table__cell sortie-row__meta">
        {sortie.cell_count} cell{sortie.cell_count !== 1 && "s"}
      </td>
      <td className="table__cell sortie-row__date">
        {new Date(sortie.created_at).toLocaleDateString()}
      </td>
      <td className="table__cell">
        <div className="sortie-row__actions" onClick={(e) => e.stopPropagation()}>
          <OverflowMenu items={menuItems} />
        </div>
      </td>
    </tr>
  );
}

export default function SortiesPage() {
  const navigate = useNavigate();
  const { run } = useOutletContext<LayoutContext>();
  const [sorties, setSorties] = useState<(Sortie & { cell_count: number })[]>(
    [],
  );
  const [creating, setCreating] = useState(false);
  const [showArchived, setShowArchived] = useState(false);

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

  const activeSorties = sorties.filter((s) => !s.is_archived);
  const archivedSorties = sorties.filter((s) => s.is_archived);

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
          {activeSorties.length > 0 ? (
            <table className="table">
              <thead className="table__head">
                <tr>
                  <th className="table__header-cell">Name</th>
                  <th className="table__header-cell">Cells</th>
                  <th className="table__header-cell">Created</th>
                  <th className="table__header-cell"></th>
                </tr>
              </thead>
              <tbody>
                {activeSorties.map((s) => (
                  <SortieRow
                    key={s.id}
                    sortie={s}
                    onArchive={async () => {
                      await archiveSortie(s.id);
                      loadSorties();
                    }}
                    onUnarchive={async () => {
                      await unarchiveSortie(s.id);
                      loadSorties();
                    }}
                    onDelete={async () => {
                      await deleteSortie(s.id);
                      loadSorties();
                    }}
                  />
                ))}
              </tbody>
            </table>
          ) : (
            <div className="muted" style={{ padding: "12px 16px" }}>
              No active sorties
            </div>
          )}
          {archivedSorties.length > 0 && (
            <div
              className="sorties-page__archived-toggle"
              onClick={() => setShowArchived(!showArchived)}
            >
              <span className="sorties-page__archived-arrow">
                {showArchived ? "▾" : "▸"}
              </span>
              Archived ({archivedSorties.length})
            </div>
          )}
          {showArchived && archivedSorties.length > 0 && (
            <table className="table sorties-page__archived-table">
              <tbody>
                {archivedSorties.map((s) => (
                  <SortieRow
                    key={s.id}
                    sortie={s}
                    onArchive={async () => {
                      await archiveSortie(s.id);
                      loadSorties();
                    }}
                    onUnarchive={async () => {
                      await unarchiveSortie(s.id);
                      loadSorties();
                    }}
                    onDelete={async () => {
                      await deleteSortie(s.id);
                      loadSorties();
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
