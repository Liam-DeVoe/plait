import { useEffect, useState, useRef } from "react";
import { type NavigateFunction } from "react-router-dom";
import { type Worktop } from "../api";

export const BADGE_STATUSES = new Set([
  "passing", "current", "open", "pending", "running",
  "failing", "failed", "behind", "unknown", "archived", "closed", "completed",
  "local",
]);

export function StatusBadge({ status, label }: { status: string; label: string }) {
  const modifier = BADGE_STATUSES.has(status) ? status : "unknown";
  return <span className={`badge badge--${modifier}`}>{label}</span>;
}

/**
 * Tend icon: a 270° cycle arrow around a small sprout.
 *
 * `filled` toggles the sprout fill — use `true` for active states
 * (manual Tend button, auto-tend on) and `false` for the auto-tend
 * off state. The cycle arrow stays stroked either way.
 */
export function TendIcon({
  size = 16,
  filled = true,
}: {
  size?: number;
  filled?: boolean;
}) {
  const leafFill = filled ? "currentColor" : "none";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M22 12a10 10 0 1 1-3.34-7.46" />
      <polyline points="22 4 22 9 17 9" />
      <path d="M12 17v-3" strokeWidth={1.5} />
      <path
        d="M12 14c0-1.6 1.1-2.7 2.7-2.7 0 1.6-1.1 2.7-2.7 2.7z"
        fill={leafFill}
        strokeWidth={1.2}
      />
      <path
        d="M12 14c0-1.6-1.1-2.7-2.7-2.7 0 1.6 1.1 2.7 2.7 2.7z"
        fill={leafFill}
        strokeWidth={1.2}
      />
    </svg>
  );
}

export type PrState = "open" | "merged" | "closed";

export function prStateOf(worktop: Worktop): PrState | null {
  if (!worktop.pr_url || worktop.pr_number == null) return null;
  if (worktop.status === "archived") {
    if (worktop.archive_reason === "merged") return "merged";
    if (worktop.archive_reason === "closed") return "closed";
  }
  return "open";
}

function PrStateIcon({ state }: { state: PrState }) {
  return (
    <svg
      className="pr-btn__icon"
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
    >
      {state === "open" && (
        <path d="M1.5 3.25a2.25 2.25 0 1 1 3 2.122v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.25 2.25 0 0 1 1.5 3.25Zm5.677-.177L9.573.677A.25.25 0 0 1 10 .854V2.5h1A2.5 2.5 0 0 1 13.5 5v5.628a2.251 2.251 0 1 1-1.5 0V5a1 1 0 0 0-1-1h-1v1.646a.25.25 0 0 1-.427.177L7.177 3.427a.25.25 0 0 1 0-.354ZM3.75 2.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 9.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm8.25.75a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0Z" />
      )}
      {state === "merged" && (
        <path d="M5.45 5.154A4.25 4.25 0 0 0 9.25 7.5h1.378a2.251 2.251 0 1 1 0 1.5H9.25A5.734 5.734 0 0 1 5 7.123v3.505a2.25 2.25 0 1 1-1.5 0V5.372a2.25 2.25 0 1 1 1.95-.218ZM4.25 13.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm8.5-4.5a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM5 3.25a.75.75 0 1 0 0 .005V3.25Z" />
      )}
      {state === "closed" && (
        <path d="M3.25 1A2.25 2.25 0 0 1 4 5.372v5.256a2.251 2.251 0 1 1-1.5 0V5.372A2.251 2.251 0 0 1 3.25 1Zm9.5 5.5a.75.75 0 0 1 .75.75v3.378a2.251 2.251 0 1 1-1.5 0V7.25a.75.75 0 0 1 .75-.75Zm-2.03-5.273a.75.75 0 0 1 1.06 0l.97.97.97-.97a.748.748 0 0 1 1.265.332.75.75 0 0 1-.205.729l-.97.97.97.97a.751.751 0 0 1-.018 1.042.751.751 0 0 1-1.042.018l-.97-.97-.97.97a.749.749 0 0 1-1.275-.326.749.749 0 0 1 .215-.734l.97-.97-.97-.97a.75.75 0 0 1 0-1.06ZM2.5 3.25a.75.75 0 1 0 1.5 0 .75.75 0 0 0-1.5 0ZM3.25 12a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm9.5 0a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z" />
      )}
    </svg>
  );
}

const PR_STATE_LABEL: Record<PrState, string> = {
  open: "Open",
  merged: "Merged",
  closed: "Closed",
};

export function PrPill({
  worktop,
  size = "sm",
}: {
  worktop: Worktop;
  size?: "sm" | "md";
}) {
  const state = prStateOf(worktop);
  if (!state) return null;
  return (
    <a
      href={worktop.pr_url!}
      target="_blank"
      rel="noopener noreferrer"
      className={`btn btn--${size} pr-btn pr-btn--${state}`}
      onClick={(e) => e.stopPropagation()}
      title={`${PR_STATE_LABEL[state]} PR — open in new tab`}
    >
      <PrStateIcon state={state} />
      #{worktop.pr_number}
    </a>
  );
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

export function navigateTo(e: React.MouseEvent, to: string, navigate: NavigateFunction) {
  if (e.metaKey || e.ctrlKey) {
    window.open(to, "_blank");
  } else {
    navigate(to);
  }
}
