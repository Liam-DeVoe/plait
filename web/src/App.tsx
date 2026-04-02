import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import CellsPage from "./pages/CellsPage";
import CellDetailPage from "./pages/CellDetailPage";
import SortiesPage from "./pages/SortiesPage";
import SortieDetailPage from "./pages/SortieDetailPage";
import "./App.css";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/cells" replace />} />
          <Route path="/cells" element={<CellsPage />} />
          <Route path="/cells/:id" element={<CellDetailPage />} />
          <Route path="/sorties" element={<SortiesPage />} />
          <Route path="/sorties/:id" element={<SortieDetailPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
