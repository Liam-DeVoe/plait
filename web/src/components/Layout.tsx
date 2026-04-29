import { useEffect, useRef } from "react";
import {
  NavLink,
  Outlet,
  ScrollRestoration,
  useRevalidator,
} from "react-router-dom";
import { connectWebSocket } from "../api";

export default function Layout() {
  const revalidator = useRevalidator();
  // Stash in a ref so the WS effect doesn't re-subscribe when the
  // revalidator's identity changes between renders.
  const revalidatorRef = useRef(revalidator);
  useEffect(() => {
    revalidatorRef.current = revalidator;
  });

  useEffect(() => {
    const ws = connectWebSocket(() => revalidatorRef.current.revalidate());
    return () => ws.close();
  }, []);

  return (
    <div className="layout">
      <ScrollRestoration />
      <div className="layout__header">
        <div className="layout__header-inner">
          <NavLink to="/" className="layout__logo">
            <img
              src="/favicon.svg"
              alt=""
              aria-hidden="true"
              className="layout__logo-icon"
            />
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
        <Outlet />
      </div>
    </div>
  );
}
