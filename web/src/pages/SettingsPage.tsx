import { useEffect, useState, type DragEvent } from "react";
import { useLoaderData, useRevalidator } from "react-router-dom";
import {
  fetchRepos,
  fetchViews,
  fetchSettings,
  createRepo,
  updateRepo,
  deleteRepo,
  setRepoOrder,
  createView,
  updateView,
  deleteView,
  updateSettings,
  type Repo,
  type View,
  type Settings,
} from "../api";

/** Parse a textarea's contents into a glob list: one glob per line. */
function parseGlobs(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
}

export async function settingsLoader() {
  const [repos, views, settings] = await Promise.all([
    fetchRepos(),
    fetchViews(),
    fetchSettings(),
  ]);
  return { repos, views, settings };
}

function SettingsPanel({ initial }: { initial: Settings }) {
  const revalidator = useRevalidator();
  const [author, setAuthor] = useState(initial.author);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Resync when the loader serves fresh data.
  useEffect(() => {
    setAuthor(initial.author);
  }, [initial.author]);

  const dirty = author !== initial.author;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      await updateSettings({ author });
      revalidator.revalidate();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card form">
      <div style={{ fontWeight: 600, marginBottom: 4 }}>General</div>
      {error && <div className="error-banner">{error}</div>}
      <div className="form__row">
        <label
          className="form__checkbox-label"
          style={{ minWidth: 120, cursor: "default" }}
        >
          GitHub author
        </label>
        <input
          type="text"
          placeholder="github-username"
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          className="form__input form__input--flex"
        />
        <div
          className={`btn btn--blue${!dirty || saving ? " btn--disabled" : ""}`}
          onClick={!dirty || saving ? undefined : handleSave}
        >
          {saving ? "Saving..." : "Save"}
        </div>
      </div>
    </div>
  );
}

function RepoRow({
  repo,
  onSave,
  onDelete,
  onDragStart,
  onDragOver,
  onDrop,
  onDragEnd,
  dragging,
  dragOver,
}: {
  repo: Repo;
  onSave: (patch: {
    path?: string;
    upstream?: string | null;
    kind?: "remote" | "local";
    copy_globs?: string[];
  }) => Promise<void>;
  onDelete: () => Promise<void>;
  onDragStart: (e: DragEvent<HTMLElement>) => void;
  onDragOver: (e: DragEvent<HTMLTableRowElement>) => void;
  onDrop: (e: DragEvent<HTMLTableRowElement>) => void;
  onDragEnd: () => void;
  dragging: boolean;
  dragOver: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [path, setPath] = useState(repo.path);
  const [kind, setKind] = useState<"remote" | "local">(repo.kind);
  const [upstream, setUpstream] = useState(repo.upstream ?? "");
  const [copyGlobs, setCopyGlobs] = useState(repo.copy_globs.join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Resync local edit state when the row's underlying repo changes.
  useEffect(() => {
    if (!editing) {
      setPath(repo.path);
      setKind(repo.kind);
      setUpstream(repo.upstream ?? "");
      setCopyGlobs(repo.copy_globs.join("\n"));
    }
  }, [repo.id, repo.path, repo.kind, repo.upstream, repo.copy_globs.join("\n"), editing]);

  const beginEdit = () => {
    setPath(repo.path);
    setKind(repo.kind);
    setUpstream(repo.upstream ?? "");
    setCopyGlobs(repo.copy_globs.join("\n"));
    setEditing(true);
    setError(null);
  };

  const handleSave = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave({
        path,
        kind,
        upstream: kind === "local" ? null : upstream,
        copy_globs: parseGlobs(copyGlobs),
      });
      setEditing(false);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete repo "${repo.id}"? Any worktops in it will be removed.`)) return;
    setBusy(true);
    try {
      await onDelete();
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <tr
      className={`repos-page__row${dragging ? " repos-page__row--dragging" : ""}${dragOver ? " repos-page__row--drag-over" : ""}`}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <td className="table__worktop repos-page__handle-cell">
        <span
          className="repos-page__drag-handle"
          title="Drag to reorder"
          aria-hidden="true"
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
        >
          ⋮⋮
        </span>
      </td>
      <td className="table__worktop">
        <div style={{ fontWeight: 500 }}>{repo.id}</div>
        {error && (
          <div className="error-banner" style={{ marginTop: 8 }}>
            {error}
          </div>
        )}
      </td>
      <td className="table__worktop">
        {editing ? (
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as "remote" | "local")}
            className="form__input"
          >
            <option value="remote">remote</option>
            <option value="local">local</option>
          </select>
        ) : (
          repo.kind
        )}
      </td>
      <td className="table__worktop">
        {editing ? (
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            className="form__input form__input--full"
          />
        ) : (
          <span style={{ fontFamily: "monospace", fontSize: 12 }}>{repo.path}</span>
        )}
      </td>
      <td className="table__worktop">
        {editing ? (
          kind === "local" ? (
            <span className="muted">—</span>
          ) : (
            <input
              type="text"
              value={upstream}
              onChange={(e) => setUpstream(e.target.value)}
              placeholder="owner/repo"
              className="form__input form__input--full"
            />
          )
        ) : repo.upstream ? (
          <span style={{ fontFamily: "monospace", fontSize: 12 }}>
            {repo.upstream}
          </span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="table__worktop">
        {editing ? (
          <textarea
            value={copyGlobs}
            onChange={(e) => setCopyGlobs(e.target.value)}
            placeholder={".claude/skills/**\none glob per line"}
            className="form__input form__input--full"
            style={{ fontFamily: "monospace", fontSize: 12, minHeight: 56 }}
            title="Gitignored paths to copy into every worktree (one glob per line)"
          />
        ) : repo.copy_globs.length > 0 ? (
          <span style={{ fontFamily: "monospace", fontSize: 12, whiteSpace: "pre-line" }}>
            {repo.copy_globs.join("\n")}
          </span>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="table__worktop">
        <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
          {editing ? (
            <>
              <div
                className={`btn btn--sm btn--blue${busy ? " btn--disabled" : ""}`}
                onClick={busy ? undefined : handleSave}
              >
                {busy ? "..." : "Save"}
              </div>
              <div
                className="btn btn--sm btn--gray"
                onClick={() => {
                  setEditing(false);
                  setError(null);
                }}
              >
                Cancel
              </div>
            </>
          ) : (
            <>
              <div className="btn btn--sm btn--soft-gray" onClick={beginEdit}>
                Edit
              </div>
              <div
                className={`btn btn--sm btn--soft-red${busy ? " btn--disabled" : ""}`}
                onClick={busy ? undefined : handleDelete}
              >
                Delete
              </div>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

function NewRepoForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [path, setPath] = useState("");
  const [kind, setKind] = useState<"remote" | "local">("remote");
  const [upstream, setUpstream] = useState("");
  const [copyGlobs, setCopyGlobs] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setId("");
    setPath("");
    setKind("remote");
    setUpstream("");
    setCopyGlobs("");
    setError(null);
  };

  const handleSubmit = async () => {
    setBusy(true);
    setError(null);
    try {
      await createRepo({
        id: id.trim(),
        path: path.trim(),
        kind,
        upstream: kind === "local" ? null : upstream.trim(),
        copy_globs: parseGlobs(copyGlobs),
      });
      reset();
      setOpen(false);
      onCreated();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div
        className="btn btn--sm btn--blue"
        onClick={() => setOpen(true)}
        style={{ alignSelf: "flex-start" }}
      >
        + Add Repo
      </div>
    );
  }

  return (
    <div className="card form" style={{ marginTop: 12 }}>
      {error && <div className="error-banner">{error}</div>}
      <div className="form__row">
        <input
          type="text"
          placeholder="repo-id (e.g. hegel-core)"
          value={id}
          onChange={(e) => setId(e.target.value)}
          className="form__input form__input--flex"
          autoFocus
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as "remote" | "local")}
          className="form__input"
        >
          <option value="remote">remote</option>
          <option value="local">local</option>
        </select>
      </div>
      <input
        type="text"
        placeholder="/absolute/path/to/local/clone"
        value={path}
        onChange={(e) => setPath(e.target.value)}
        className="form__input form__input--full"
      />
      {kind === "remote" && (
        <input
          type="text"
          placeholder="upstream (owner/repo)"
          value={upstream}
          onChange={(e) => setUpstream(e.target.value)}
          className="form__input form__input--full"
        />
      )}
      <textarea
        placeholder={"gitignored paths to copy into worktrees (one glob per line, optional)"}
        value={copyGlobs}
        onChange={(e) => setCopyGlobs(e.target.value)}
        className="form__input form__input--full"
        style={{ fontFamily: "monospace", fontSize: 12, minHeight: 56 }}
      />
      <div className="form__actions">
        <div
          className={`btn btn--blue${busy ? " btn--disabled" : ""}`}
          onClick={busy ? undefined : handleSubmit}
        >
          {busy ? "Creating..." : "Create"}
        </div>
        <div
          className="btn btn--gray"
          onClick={() => {
            reset();
            setOpen(false);
          }}
        >
          Cancel
        </div>
      </div>
    </div>
  );
}

function ReposSection({ repos }: { repos: Repo[] }) {
  const revalidator = useRevalidator();
  const [order, setOrder] = useState<string[]>(repos.map((r) => r.id));
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);

  // Resync local order with server order when loader data changes.
  useEffect(() => {
    setOrder(repos.map((r) => r.id));
  }, [repos.map((r) => r.id).join(" ")]);

  const refresh = () => revalidator.revalidate();
  const reposById = new Map(repos.map((r) => [r.id, r]));
  const orderedRepos = order
    .map((id) => reposById.get(id))
    .filter((r): r is Repo => !!r);

  const handleDragStart = (id: string) => (e: DragEvent<HTMLElement>) => {
    setDraggingId(id);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
  };

  const handleDragOver = (id: string) => (e: DragEvent<HTMLTableRowElement>) => {
    if (!draggingId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (dragOverId !== id) setDragOverId(id);
  };

  const handleDrop = (targetId: string) => async (e: DragEvent<HTMLTableRowElement>) => {
    e.preventDefault();
    const source = draggingId;
    setDraggingId(null);
    setDragOverId(null);
    if (!source || source === targetId) return;
    const next = order.filter((id) => id !== source);
    const idx = next.indexOf(targetId);
    next.splice(idx === -1 ? next.length : idx, 0, source);
    setOrder(next);
    try {
      await setRepoOrder(next);
    } catch (err) {
      console.error("Failed to persist repo order", err);
      refresh();
    }
  };

  const handleDragEnd = () => {
    setDraggingId(null);
    setDragOverId(null);
  };

  return (
    <div style={{ marginTop: 32 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 16 }}>
        Repositories ({repos.length})
      </div>
      {repos.length === 0 ? (
        <div className="muted" style={{ marginBottom: 12 }}>
          No repos configured yet. Add one below to get started.
        </div>
      ) : (
        <div className="card card--clipped">
          <table className="table">
            <thead className="table__head">
              <tr>
                <th className="table__header-worktop" style={{ width: 32 }}></th>
                <th className="table__header-worktop">ID</th>
                <th className="table__header-worktop">Kind</th>
                <th className="table__header-worktop">Path</th>
                <th className="table__header-worktop">Upstream</th>
                <th className="table__header-worktop" title="Gitignored paths copied into every worktree">
                  Copy globs
                </th>
                <th className="table__header-worktop"></th>
              </tr>
            </thead>
            <tbody>
              {orderedRepos.map((r) => (
                <RepoRow
                  key={r.id}
                  repo={r}
                  onSave={async (patch) => {
                    await updateRepo(r.id, patch);
                    refresh();
                  }}
                  onDelete={async () => {
                    await deleteRepo(r.id);
                    refresh();
                  }}
                  onDragStart={handleDragStart(r.id)}
                  onDragOver={handleDragOver(r.id)}
                  onDrop={handleDrop(r.id)}
                  onDragEnd={handleDragEnd}
                  dragging={draggingId === r.id}
                  dragOver={dragOverId === r.id && draggingId !== r.id}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <NewRepoForm onCreated={refresh} />
      </div>
    </div>
  );
}

function ViewRow({
  view,
  repos,
  onSave,
  onDelete,
}: {
  view: View;
  repos: Repo[];
  onSave: (patch: { name?: string; repo_ids?: string[] }) => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [name, setName] = useState(view.name);
  const [memberIds, setMemberIds] = useState<Set<string>>(new Set(view.repo_ids));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(view.name);
    setMemberIds(new Set(view.repo_ids));
  }, [view.id, view.name, view.repo_ids.join(",")]);

  const dirty =
    name !== view.name ||
    memberIds.size !== view.repo_ids.length ||
    view.repo_ids.some((r) => !memberIds.has(r));

  const toggle = (repoId: string) => {
    setMemberIds((prev) => {
      const next = new Set(prev);
      if (next.has(repoId)) next.delete(repoId);
      else next.add(repoId);
      return next;
    });
  };

  const handleSave = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave({
        name: name.trim(),
        repo_ids: repos.filter((r) => memberIds.has(r.id)).map((r) => r.id),
      });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm(`Delete view "${view.name}"?`)) return;
    setBusy(true);
    try {
      await onDelete();
    } catch (e: any) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ padding: 12, marginBottom: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          cursor: "pointer",
        }}
        onClick={() => setExpanded(!expanded)}
      >
        <span style={{ width: 14 }}>{expanded ? "▾" : "▸"}</span>
        <div style={{ fontWeight: 500, flex: 1 }}>{view.name}</div>
        <div className="muted" style={{ fontSize: 12 }}>
          {view.repo_ids.length} repo{view.repo_ids.length === 1 ? "" : "s"}
        </div>
      </div>
      {expanded && (
        <div style={{ marginTop: 12, paddingLeft: 22 }} onClick={(e) => e.stopPropagation()}>
          {error && <div className="error-banner">{error}</div>}
          <div className="form__row" style={{ marginBottom: 12 }}>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="form__input form__input--flex"
              placeholder="View name"
            />
          </div>
          <div className="form__checkboxes" style={{ marginBottom: 12 }}>
            {repos.length === 0 ? (
              <span className="muted">No repos to add.</span>
            ) : (
              repos.map((r) => (
                <label key={r.id} className="form__checkbox-label">
                  <input
                    type="checkbox"
                    checked={memberIds.has(r.id)}
                    onChange={() => toggle(r.id)}
                  />
                  {r.id}
                </label>
              ))
            )}
          </div>
          <div className="form__actions">
            <div
              className={`btn btn--sm btn--blue${!dirty || busy ? " btn--disabled" : ""}`}
              onClick={!dirty || busy ? undefined : handleSave}
            >
              {busy ? "Saving..." : "Save"}
            </div>
            <div className="btn btn--sm btn--soft-red" onClick={handleDelete}>
              Delete view
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function NewViewForm({ repos, onCreated }: { repos: Repo[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [memberIds, setMemberIds] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName("");
    setMemberIds(new Set());
    setError(null);
  };

  const toggle = (repoId: string) => {
    setMemberIds((prev) => {
      const next = new Set(prev);
      if (next.has(repoId)) next.delete(repoId);
      else next.add(repoId);
      return next;
    });
  };

  const handleSubmit = async () => {
    setBusy(true);
    setError(null);
    try {
      await createView(
        name.trim(),
        repos.filter((r) => memberIds.has(r.id)).map((r) => r.id),
      );
      reset();
      setOpen(false);
      onCreated();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="btn btn--sm btn--blue" onClick={() => setOpen(true)}>
        + New View
      </div>
    );
  }

  return (
    <div className="card form" style={{ marginTop: 12 }}>
      {error && <div className="error-banner">{error}</div>}
      <input
        type="text"
        placeholder="View name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="form__input form__input--full"
        autoFocus
      />
      <div className="form__checkboxes">
        {repos.length === 0 ? (
          <span className="muted">No repos to add.</span>
        ) : (
          repos.map((r) => (
            <label key={r.id} className="form__checkbox-label">
              <input
                type="checkbox"
                checked={memberIds.has(r.id)}
                onChange={() => toggle(r.id)}
              />
              {r.id}
            </label>
          ))
        )}
      </div>
      <div className="form__actions">
        <div
          className={`btn btn--blue${busy || !name.trim() ? " btn--disabled" : ""}`}
          onClick={busy || !name.trim() ? undefined : handleSubmit}
        >
          {busy ? "Creating..." : "Create"}
        </div>
        <div
          className="btn btn--gray"
          onClick={() => {
            reset();
            setOpen(false);
          }}
        >
          Cancel
        </div>
      </div>
    </div>
  );
}

function ViewsSection({ views, repos }: { views: View[]; repos: Repo[] }) {
  const revalidator = useRevalidator();
  const refresh = () => revalidator.revalidate();

  return (
    <div style={{ marginTop: 32 }}>
      <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 16 }}>
        Views ({views.length})
      </div>
      {views.length === 0 ? (
        <div className="muted" style={{ marginBottom: 12 }}>
          No views yet.
        </div>
      ) : (
        <div>
          {views.map((v) => (
            <ViewRow
              key={v.id}
              view={v}
              repos={repos}
              onSave={async (patch) => {
                await updateView(v.id, patch);
                refresh();
              }}
              onDelete={async () => {
                await deleteView(v.id);
                refresh();
              }}
            />
          ))}
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        <NewViewForm repos={repos} onCreated={refresh} />
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const { repos, views, settings } = useLoaderData() as Awaited<
    ReturnType<typeof settingsLoader>
  >;
  return (
    <>
      <div className="page-header">
        <div className="page-title">Settings</div>
      </div>
      <SettingsPanel initial={settings} />
      <ReposSection repos={repos} />
      <ViewsSection views={views} repos={repos} />
    </>
  );
}
