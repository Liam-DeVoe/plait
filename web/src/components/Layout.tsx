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
            Orrery
          </NavLink>
          <div className="layout__nav">
            <NavLink
              to="/cells"
              className={({ isActive }) =>
                `layout__nav-link${isActive ? " layout__nav-link--active" : ""}`
              }
            >
              Cells
            </NavLink>
            <NavLink
              to="/sorties"
              className={({ isActive }) =>
                `layout__nav-link${isActive ? " layout__nav-link--active" : ""}`
              }
            >
              Sorties
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
