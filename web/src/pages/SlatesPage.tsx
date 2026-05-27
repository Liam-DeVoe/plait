import { useState } from "react";
import { useNavigate, useLoaderData, useRevalidator } from "react-router-dom";
import {
  fetchSlates,
  fetchViews,
  createSlate,
  archiveSlate,
  unarchiveSlate,
  deleteSlate,
  type Slate,
  type View,
} from "../api";
import { OverflowMenu, navigateTo } from "../components/shared";
import {
  ViewFilter,
  useActiveView,
  useActiveViewRepoIds,
} from "../components/ViewFilter";

export async function slatesLoader() {
  const [slates, views] = await Promise.all([fetchSlates(), fetchViews()]);
  return { slates, views };
}

function SlateRow({
  slate,
  views,
  onArchive,
  onUnarchive,
  onDelete,
}: {
  slate: Slate & { worktop_count: number };
  views: View[];
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

  const view = slate.view_id
    ? views.find((v) => v.id === slate.view_id)
    : null;
  const scopeLabel = view
    ? view.name
    : slate.repo_ids.length > 0
      ? `${slate.repo_ids.length} repo${slate.repo_ids.length === 1 ? "" : "s"}`
      : "all repos";

  return (
    <tr
      className="slate-row"
      onClick={(e) => navigateTo(e, `/slates/${slate.id}`, navigate)}
    >
      <td className="table__worktop slate-row__name">
        {slate.name || <span className="muted">{slate.id.slice(0, 8)}</span>}
      </td>
      <td className="table__worktop slate-row__meta">
        <span className="muted">{scopeLabel}</span>
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

function NewSlateModal({
  views,
  defaultViewId,
  onClose,
  onCreated,
}: {
  views: View[];
  defaultViewId: string | null;
  onClose: () => void;
  onCreated: (slate: Slate) => void;
}) {
  const [viewId, setViewId] = useState<string | null>(defaultViewId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    setBusy(true);
    setError(null);
    try {
      const slate = await createSlate(
        viewId ? { view_id: viewId } : {},
      );
      onCreated(slate);
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  };

  const selected = viewId ? views.find((v) => v.id === viewId) : null;
  const repoCount = selected ? selected.repo_ids.length : null;

  return (
    <div className="modal__backdrop" onClick={onClose}>
      <div className="modal__panel" onClick={(e) => e.stopPropagation()}>
        <div className="modal__title">New slate</div>
        {error && <div className="error-banner">{error}</div>}
        <div className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
          Pick the set of repos this slate will operate on. The choice is
          locked in at creation — editing the view later won't change this
          slate's scope.
        </div>
        <div style={{ marginBottom: 16 }}>
          <label
            className="form__checkbox-label"
            style={{ marginBottom: 6, cursor: "pointer" }}
          >
            <input
              type="radio"
              checked={viewId === null}
              onChange={() => setViewId(null)}
            />
            All repos
          </label>
          {views.map((v) => (
            <label
              key={v.id}
              className="form__checkbox-label"
              style={{ marginBottom: 6, cursor: "pointer" }}
            >
              <input
                type="radio"
                checked={viewId === v.id}
                onChange={() => setViewId(v.id)}
              />
              {v.name}{" "}
              <span className="muted" style={{ fontSize: 12 }}>
                ({v.repo_ids.length} repo{v.repo_ids.length === 1 ? "" : "s"})
              </span>
            </label>
          ))}
        </div>
        {repoCount === 0 && (
          <div
            className="error-banner"
            style={{ marginBottom: 12 }}
          >
            This view has no repos — pick another or add repos to it first.
          </div>
        )}
        <div className="form__actions">
          <div
            className={`btn btn--blue${busy || repoCount === 0 ? " btn--disabled" : ""}`}
            onClick={busy || repoCount === 0 ? undefined : handleCreate}
          >
            {busy ? "Creating..." : "Create"}
          </div>
          <div className="btn btn--gray" onClick={onClose}>
            Cancel
          </div>
        </div>
      </div>
    </div>
  );
}

export default function SlatesPage() {
  const { slates, views } = useLoaderData() as Awaited<
    ReturnType<typeof slatesLoader>
  >;
  const navigate = useNavigate();
  const revalidator = useRevalidator();
  const [modalOpen, setModalOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const activeView = useActiveView(views);
  const activeViewRepoIds = useActiveViewRepoIds(views);

  const refresh = () => revalidator.revalidate();

  // Filter slates by the active view. A slate is "in" the view if any of
  // its snapshotted repo_ids appear in the view's repo set. Slates with
  // empty snapshots (legacy data) match every view — they're effectively
  // "all repos" slates.
  const visibleSlates = activeViewRepoIds
    ? slates.filter(
        (s) =>
          s.repo_ids.length === 0 ||
          s.repo_ids.some((r) => activeViewRepoIds.includes(r)),
      )
    : slates;

  const activeSlates = visibleSlates.filter((s) => !s.is_archived);
  const archivedSlates = visibleSlates.filter((s) => s.is_archived);

  return (
    <>
      <div className="page-header">
        <div className="page-title">Slates</div>
        <div
          className="btn btn--blue"
          onClick={() => setModalOpen(true)}
        >
          New Slate
        </div>
      </div>

      <ViewFilter views={views} />

      {modalOpen && (
        <NewSlateModal
          views={views}
          defaultViewId={activeView ? activeView.id : null}
          onClose={() => setModalOpen(false)}
          onCreated={(slate) => navigate(`/slates/${slate.id}`)}
        />
      )}

      {visibleSlates.length === 0 ? (
        <div className="empty-state">
          <div>
            {activeView ? `No slates in view "${activeView.name}".` : "No slates yet."}
          </div>
        </div>
      ) : (
        <div className="card card--clipped">
          {activeSlates.length > 0 ? (
            <table className="table">
              <thead className="table__head">
                <tr>
                  <th className="table__header-worktop">Name</th>
                  <th className="table__header-worktop">Scope</th>
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
                    views={views}
                    onArchive={async () => {
                      await archiveSlate(s.id);
                      refresh();
                    }}
                    onUnarchive={async () => {
                      await unarchiveSlate(s.id);
                      refresh();
                    }}
                    onDelete={async () => {
                      await deleteSlate(s.id);
                      refresh();
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
                    views={views}
                    onArchive={async () => {
                      await archiveSlate(s.id);
                      refresh();
                    }}
                    onUnarchive={async () => {
                      await unarchiveSlate(s.id);
                      refresh();
                    }}
                    onDelete={async () => {
                      await deleteSlate(s.id);
                      refresh();
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
