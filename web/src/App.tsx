import { useEffect, useState, useCallback } from "react";
import {
  fetchCells,
  fetchCell,
  createCell,
  archiveCell,
  triggerRebase,
  deleteCell,
  openInVSCode,
  connectWebSocket,
  type Cell,
  type Session,
} from "./api";

function StatusBadge({ status, label }: { status: string; label: string }) {
  const colors: Record<string, string> = {
    passing: "bg-green-100 text-green-800",
    current: "bg-green-100 text-green-800",
    active: "bg-blue-100 text-blue-800",
    pending: "bg-yellow-100 text-yellow-800",
    rebasing: "bg-yellow-100 text-yellow-800",
    running: "bg-yellow-100 text-yellow-800",
    failing: "bg-red-100 text-red-800",
    failed: "bg-red-100 text-red-800",
    conflict: "bg-red-100 text-red-800",
    unknown: "bg-gray-100 text-gray-800",
    archived: "bg-gray-100 text-gray-800",
    completed: "bg-green-100 text-green-800",
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colors[status] ?? colors.unknown}`}
    >
      {label}
    </span>
  );
}

function CellRow({
  cell,
  onSelect,
  onRebase,
  onArchive,
  onDelete,
  onVSCode,
}: {
  cell: Cell;
  onSelect: () => void;
  onRebase: () => void;
  onArchive: () => void;
  onDelete: () => void;
  onVSCode: () => void;
}) {
  return (
    <tr
      className="border-b border-gray-200 hover:bg-gray-50 cursor-pointer"
      onClick={onSelect}
    >
      <td className="px-4 py-3">
        <div className="font-medium text-gray-900">{cell.repo.split("/").pop()}</div>
        <div className="text-sm text-gray-500">{cell.branch}</div>
      </td>
      <td className="px-4 py-3">
        {cell.pr_url ? (
          <a
            href={cell.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline text-sm"
            onClick={(e) => e.stopPropagation()}
          >
            #{cell.pr_number}
          </a>
        ) : (
          <span className="text-gray-400 text-sm">no PR</span>
        )}
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={cell.ci_status} label={`CI: ${cell.ci_status}`} />
      </td>
      <td className="px-4 py-3">
        <StatusBadge
          status={cell.rebase_status}
          label={`Rebase: ${cell.rebase_status}`}
        />
      </td>
      <td className="px-4 py-3">
        <StatusBadge status={cell.status} label={cell.status} />
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={onVSCode}
            className="px-2 py-1 text-xs bg-gray-100 hover:bg-gray-200 rounded"
            title="Open in VS Code"
          >
            VS Code
          </button>
          <button
            onClick={onRebase}
            className="px-2 py-1 text-xs bg-blue-100 hover:bg-blue-200 text-blue-800 rounded"
          >
            Rebase
          </button>
          <button
            onClick={onArchive}
            className="px-2 py-1 text-xs bg-yellow-100 hover:bg-yellow-200 text-yellow-800 rounded"
          >
            Archive
          </button>
          <button
            onClick={onDelete}
            className="px-2 py-1 text-xs bg-red-100 hover:bg-red-200 text-red-800 rounded"
          >
            Delete
          </button>
        </div>
      </td>
    </tr>
  );
}

function CellDetail({
  cell,
  sessions,
  onBack,
}: {
  cell: Cell;
  sessions: Session[];
  onBack: () => void;
}) {
  return (
    <div>
      <button
        onClick={onBack}
        className="mb-4 text-sm text-gray-500 hover:text-gray-700"
      >
        &larr; Back to cells
      </button>
      <div className="bg-white rounded-lg shadow p-6 mb-6">
        <h2 className="text-xl font-semibold mb-2">
          {cell.repo} / {cell.branch}
        </h2>
        <div className="flex gap-3 mb-4">
          <StatusBadge status={cell.status} label={cell.status} />
          <StatusBadge status={cell.ci_status} label={`CI: ${cell.ci_status}`} />
          <StatusBadge
            status={cell.rebase_status}
            label={`Rebase: ${cell.rebase_status}`}
          />
        </div>
        <div className="text-sm text-gray-600 space-y-1">
          <div>
            <span className="font-medium">Worktree:</span> {cell.worktree_path}
          </div>
          {cell.pr_url && (
            <div>
              <span className="font-medium">PR:</span>{" "}
              <a
                href={cell.pr_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-600 hover:underline"
              >
                #{cell.pr_number}
              </a>
            </div>
          )}
          <div>
            <span className="font-medium">Created:</span>{" "}
            {new Date(cell.created_at).toLocaleString()}
          </div>
        </div>
      </div>

      <h3 className="text-lg font-semibold mb-3">Sessions</h3>
      {sessions.length === 0 ? (
        <p className="text-gray-500 text-sm">No sessions yet.</p>
      ) : (
        <div className="space-y-3">
          {sessions.map((s) => (
            <div key={s.id} className="bg-white rounded-lg shadow p-4">
              <div className="flex items-center gap-3 mb-2">
                <StatusBadge status={s.status} label={s.status} />
                <span className="text-xs text-gray-500">
                  {s.role} {s.trigger ? `(${s.trigger})` : ""}
                </span>
                <span className="text-xs text-gray-400">
                  {new Date(s.started_at).toLocaleString()}
                </span>
              </div>
              {s.transcript && (
                <pre className="text-xs bg-gray-50 p-3 rounded overflow-auto max-h-60">
                  {s.transcript}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CreateCellForm({ onCreated }: { onCreated: () => void }) {
  const [prUrl, setPrUrl] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await createCell(prUrl);
      setPrUrl("");
      setOpen(false);
      onCreated();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm"
      >
        New Cell
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-white rounded-lg shadow p-4 mb-6 space-y-3"
    >
      {error && (
        <div className="text-sm text-red-600 bg-red-50 px-3 py-2 rounded">
          {error}
        </div>
      )}
      <div className="flex gap-3">
        <input
          type="text"
          placeholder="https://github.com/hegeldev/hegel-rust/pull/42"
          value={prUrl}
          onChange={(e) => setPrUrl(e.target.value)}
          required
          className="flex-1 px-3 py-2 border rounded text-sm"
        />
      </div>
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={loading}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 text-sm disabled:opacity-50"
        >
          {loading ? "Creating..." : "Create"}
        </button>
        <button
          type="button"
          onClick={() => { setOpen(false); setError(null); }}
          className="px-4 py-2 bg-gray-100 rounded hover:bg-gray-200 text-sm"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function App() {
  const [cells, setCells] = useState<Cell[]>([]);
  const [selectedCell, setSelectedCell] = useState<
    (Cell & { sessions: Session[] }) | null
  >(null);

  const loadCells = useCallback(async () => {
    const data = await fetchCells();
    setCells(data);
  }, []);

  const selectCell = async (id: string) => {
    const data = await fetchCell(id);
    setSelectedCell(data);
  };

  useEffect(() => {
    loadCells();
    const ws = connectWebSocket((msg) => {
      if (msg.event === "cell_updated") {
        loadCells();
        if (selectedCell && msg.data.id === selectedCell.id) {
          selectCell(selectedCell.id);
        }
      }
    });
    return () => ws.close();
  }, [loadCells, selectedCell]);

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <h1 className="text-2xl font-bold text-gray-900">Orrery</h1>
        <p className="text-sm text-gray-500">
          PR coordination across hegel repositories
        </p>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-6">
        {selectedCell ? (
          <CellDetail
            cell={selectedCell}
            sessions={selectedCell.sessions}
            onBack={() => setSelectedCell(null)}
          />
        ) : (
          <>
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-lg font-semibold text-gray-900">Cells</h2>
              <CreateCellForm onCreated={loadCells} />
            </div>

            {cells.length === 0 ? (
              <div className="text-center py-12 text-gray-500">
                <p>No cells yet. Create one to get started.</p>
              </div>
            ) : (
              <div className="bg-white rounded-lg shadow overflow-hidden">
                <table className="w-full">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Repo / Branch
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        PR
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        CI
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Rebase
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Status
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {cells.map((cell) => (
                      <CellRow
                        key={cell.id}
                        cell={cell}
                        onSelect={() => selectCell(cell.id)}
                        onRebase={() => triggerRebase(cell.id)}
                        onArchive={async () => {
                          await archiveCell(cell.id);
                          loadCells();
                        }}
                        onDelete={async () => {
                          await deleteCell(cell.id);
                          loadCells();
                        }}
                        onVSCode={() => openInVSCode(cell.id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
