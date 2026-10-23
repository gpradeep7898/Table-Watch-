import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { LiveProvider } from "./hooks/useLive";
import { ToastProvider } from "./hooks/useToasts";
import { AlertsPage } from "./pages/Alerts";
import { AnalyticsPage } from "./pages/Analytics";
import { CamerasPage } from "./pages/Cameras";
import { DemoPage } from "./pages/Demo";
import { LiveFloorPage } from "./pages/LiveFloor";
import { OverviewPage } from "./pages/Overview";
import { SettingsPage } from "./pages/Settings";
import { TableHistoryPage } from "./pages/TableHistory";

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/demo" element={<DemoPage />} />
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        <Route path="floor" element={<LiveFloorPage />} />
        <Route path="cameras" element={<CamerasPage />} />
        <Route path="alerts" element={<AlertsPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="history" element={<TableHistoryPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<OverviewPage />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <LiveProvider>
          <AppRoutes />
        </LiveProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
