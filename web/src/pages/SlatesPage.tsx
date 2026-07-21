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

export default function SlatesPage() {
  const { slates, views } = useLoaderData() as Awaited<
    ReturnType<typeof slatesLoader>
  >;
  const navigate = useNavigate();
  const revalidator = useRevalidator();
  const [showArchived, setShowArchived] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const activeView = useActiveView(views);

  const refresh = () => revalidator.revalidate();

  // Every slate belongs to a view, so creation is only possible when a
  // view tab (not "All") is selected — and only if that view has repos.
  const disabledReason = !activeView
    ? "Select a view tab to create a slate in it"
    : activeView.repo_ids.length === 0
      ? "This view has no repos — add repos to it on the Settings page first"
      : null;
  const canCreate = !disabledReason && !creating;

  const handleCreate = async () => {
    if (!activeView) return;
    setCreating(true);
    setCreateError(null);
    try {
      const slate = await createSlate({ view_id: activeView.id });
      navigate(`/slates/${slate.id}`);
    } catch (e: any) {
      setCreateError(e.message);
      setCreating(false);
    }
  };

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
        {/* title lives on the wrapper: .btn--disabled sets pointer-events:
            none, which would swallow the tooltip on the button itself */}
        <div title={disabledReason ?? undefined}>
          <div
            className={`btn btn--blue${canCreate ? "" : " btn--disabled"}`}
            onClick={canCreate ? handleCreate : undefined}
          >
            {creating ? "Creating..." : "New Slate"}
          </div>
        </div>
      </div>

      <ViewFilter views={views} />

      {createError && <div className="error-banner">{createError}</div>}

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
