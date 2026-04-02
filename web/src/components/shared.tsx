import { useEffect, useState, useRef } from "react";

export const BADGE_STATUSES = new Set([
  "passing", "current", "active", "pending", "syncing", "running",
  "failing", "failed", "conflict", "unknown", "archived", "completed",
]);

export function StatusBadge({ status, label }: { status: string; label: string }) {
  const modifier = BADGE_STATUSES.has(status) ? status : "unknown";
  return <span className={`badge badge--${modifier}`}>{label}</span>;
}

export function OverflowMenu({
  items,
}: {
  items: { label: string; onClick: () => void; danger?: boolean }[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div className="overflow-menu" ref={ref}>
      <div className="overflow-menu__toggle" onClick={() => setOpen(!open)}>
        <svg
          className={`overflow-menu__icon${open ? " overflow-menu__icon--open" : ""}`}
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path
            fillRule="evenodd"
            d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
            clipRule="evenodd"
          />
        </svg>
      </div>
      {open && (
        <div className="overflow-menu__dropdown">
          {items.map((item) => (
            <div
              key={item.label}
              onClick={() => {
                item.onClick();
                setOpen(false);
              }}
              className={`overflow-menu__item${item.danger ? " overflow-menu__item--danger" : ""}`}
            >
              {item.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function timeAgo(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
