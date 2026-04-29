import {
  createBrowserRouter,
  RouterProvider,
  Navigate,
} from "react-router-dom";
import Layout from "./components/Layout";
import WorktopsPage, { worktopsLoader } from "./pages/WorktopsPage";
import WorktopDetailPage, {
  worktopDetailLoader,
} from "./pages/WorktopDetailPage";
import SlatesPage, { slatesLoader } from "./pages/SlatesPage";
import SlateDetailPage, { slateDetailLoader } from "./pages/SlateDetailPage";
import "./App.css";

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/worktops" replace /> },
      {
        path: "/worktops",
        element: <WorktopsPage />,
        loader: worktopsLoader,
      },
      {
        path: "/worktops/:id",
        element: <WorktopDetailPage />,
        loader: worktopDetailLoader,
      },
      {
        path: "/slates",
        element: <SlatesPage />,
        loader: slatesLoader,
      },
      {
        path: "/slates/:id",
        element: <SlateDetailPage />,
        loader: slateDetailLoader,
      },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
