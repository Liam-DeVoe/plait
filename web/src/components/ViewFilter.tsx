import { useSearchParams } from "react-router-dom";
import { type View } from "../api";

export const ALL_VIEW_ID = "__all__";

/** Read the active view id from the current URL.
 *
 * `?view=<id>` selects a stored view; absence (or `?view=__all__`) means
 * "All". Returns `ALL_VIEW_ID` in that case so callers can switch on a
 * single token rather than null-handling everywhere.
 */
export function useActiveViewId(): string {
  const [params] = useSearchParams();
  const v = params.get("view");
  return v && v.length > 0 ? v : ALL_VIEW_ID;
}

/** Resolve the active view to its repo_ids list, or null for "All". */
export function useActiveViewRepoIds(views: View[]): string[] | null {
  const activeId = useActiveViewId();
  if (activeId === ALL_VIEW_ID) return null;
  const view = views.find((v) => v.id === activeId);
  return view ? view.repo_ids : null;
}

/** Find the View object backing the current selection, or null for "All". */
export function useActiveView(views: View[]): View | null {
  const activeId = useActiveViewId();
  if (activeId === ALL_VIEW_ID) return null;
  return views.find((v) => v.id === activeId) ?? null;
}

export function ViewFilter({ views }: { views: View[] }) {
  const [params, setParams] = useSearchParams();
  const active = params.get("view") || ALL_VIEW_ID;

  const select = (id: string) => {
    const next = new URLSearchParams(params);
    if (id === ALL_VIEW_ID) {
      next.delete("view");
    } else {
      next.set("view", id);
    }
    setParams(next, { replace: true });
  };

  return (
    <div className="view-filter">
      <div
        className={`view-filter__tab${active === ALL_VIEW_ID ? " view-filter__tab--active" : ""}`}
        onClick={() => select(ALL_VIEW_ID)}
      >
        All
      </div>
      {views.map((v) => (
        <div
          key={v.id}
          className={`view-filter__tab${active === v.id ? " view-filter__tab--active" : ""}`}
          onClick={() => select(v.id)}
          title={
            v.repo_ids.length > 0
              ? v.repo_ids.join(", ")
              : "Empty view (no repos)"
          }
        >
          {v.name}
        </div>
      ))}
    </div>
  );
}
