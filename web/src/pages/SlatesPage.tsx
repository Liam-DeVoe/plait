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
import { ViewFilter, useActiveView } from "../components/ViewFilter";

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
  // Every slate must belong to a view. If no defaultViewId is given (e.g.
  // we're on the "All" tab when the modal opens), seed with the first
  // view so the user has something selected on first render.
  const initialId = defaultViewId ?? (views[0]?.id ?? null);
  const [viewId, setViewId] = useState<string | null>(initialId);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!viewId) return;
    setBusy(true);
    setError(null);
    try {
      const slate = await createSlate({ view_id: viewId });
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
        {views.length === 0 ? (
          <div className="muted" style={{ marginBottom: 12 }}>
            No views yet. Create one on the Settings page first.
          </div>
        ) : (
          <div style={{ marginBottom: 16 }}>
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
                  ({v.repo_ids.length} repo
                  {v.repo_ids.length === 1 ? "" : "s"})
                </span>
              </label>
            ))}
          </div>
        )}
        {repoCount === 0 && (
          <div className="error-banner" style={{ marginBottom: 12 }}>
            This view has no repos — pick another or add repos to it first.
          </div>
        )}
        <div className="form__actions">
          <div
            className={`btn btn--blue${busy || !viewId || repoCount === 0 ? " btn--disabled" : ""}`}
            onClick={busy || !viewId || repoCount === 0 ? undefined : handleCreate}
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

  const refresh = () => revalidator.revalidate();

  // A slate is "in" a view iff its view_id matches — that's the view the
  // slate was created from. Slates with view_id = null (legacy slates, or
  // slates created against "All repos" / with an explicit repo_ids list)
  // only appear under the "All" tab.
  const visibleSlates = activeView
    ? slates.filter((s) => s.view_id === activeView.id)
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
