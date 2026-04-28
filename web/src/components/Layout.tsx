import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { connectWebSocket } from "../api";

export type LayoutContext = { run: number };

export default function Layout() {
  const [run, setRun] = useState(0);
  useEffect(() => {
    const ws = connectWebSocket(() => setRun((r) => r + 1));
    return () => ws.close();
  }, []);

  return (
    <div className="layout">
      <div className="layout__header">
        <div className="layout__header-inner">
          <NavLink to="/" className="layout__logo">
            Plait
          </NavLink>
          <div className="layout__nav">
            <NavLink
              to="/worktops"
              className={({ isActive }) =>
                `layout__nav-link${isActive ? " layout__nav-link--active" : ""}`
              }
            >
              Worktops
            </NavLink>
            <NavLink
              to="/slates"
              className={({ isActive }) =>
                `layout__nav-link${isActive ? " layout__nav-link--active" : ""}`
              }
            >
              Slates
            </NavLink>
          </div>
        </div>
      </div>
      <div className="layout__main">
        <Outlet context={{ run } satisfies LayoutContext} />
      </div>
    </div>
  );
}
