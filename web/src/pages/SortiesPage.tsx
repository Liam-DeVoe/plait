import { useEffect, useState, useCallback } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { fetchSorties, createSortie, type Sortie } from "../api";
import { navigateTo } from "../components/shared";
import type { LayoutContext } from "../components/Layout";

function SortieRow({ sortie }: { sortie: Sortie & { cell_count: number } }) {
  const navigate = useNavigate();
  return (
    <tr
      className="sortie-row"
      onClick={(e) => navigateTo(e, `/sorties/${sortie.id}`, navigate)}
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

export default function SortiesPage() {
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
