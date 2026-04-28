import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import WorktopsPage from "./pages/WorktopsPage";
import WorktopDetailPage from "./pages/WorktopDetailPage";
import SlatesPage from "./pages/SlatesPage";
import SlateDetailPage from "./pages/SlateDetailPage";
import "./App.css";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/worktops" replace />} />
          <Route path="/worktops" element={<WorktopsPage />} />
          <Route path="/worktops/:id" element={<WorktopDetailPage />} />
          <Route path="/slates" element={<SlatesPage />} />
          <Route path="/slates/:id" element={<SlateDetailPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
