import { useState } from "react";

import { useAuth } from "../auth/AuthContext";
import { DeadLetterPanel } from "../components/DeadLetterPanel";
import { DashboardSummary } from "../components/DashboardSummary";
import { EventsExplorer } from "../components/EventsExplorer";
import { useDeadLetterEvents } from "../hooks/useDeadLetterEvents";
import { useEvents } from "../hooks/useEvents";

type ActiveSection = "dashboard" | "events" | "dlq";

export function Dashboard() {
  const { user, logout } = useAuth();
  const [activeSection, setActiveSection] = useState<ActiveSection>("dashboard");
  const [summaryRefreshKey, setSummaryRefreshKey] = useState(0);
  const { events, isLoading, error, refresh: refreshEvents } = useEvents();
  const {
    deadLetterEvents,
    isLoading: isDeadLetterLoading,
    error: deadLetterError,
    refresh: refreshDeadLetterEvents
  } = useDeadLetterEvents();

  function markSummaryDirty() {
    setSummaryRefreshKey((current) => current + 1);
  }

  function refreshEventsAndSummary() {
    return refreshEvents().then(markSummaryDirty);
  }

  function refreshDeadLettersAndSummary() {
    return refreshDeadLetterEvents().then(markSummaryDirty);
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">Relay</span>
          <h1>Event operations</h1>
          <p>Operational console for asynchronous event processing, retries and DLQ handling.</p>
        </div>
        <div className="topbar-actions">
          <div className="env-pill">{user?.username ?? "admin"}</div>
          <button className="command-button" type="button" onClick={logout}>
            Logout
          </button>
        </div>
      </header>

      <nav className="section-tabs" aria-label="Main sections">
        <button type="button" className={activeSection === "dashboard" ? "active" : ""} onClick={() => setActiveSection("dashboard")}>
          Dashboard
        </button>
        <button type="button" className={activeSection === "events" ? "active" : ""} onClick={() => setActiveSection("events")}>
          Events
        </button>
        <button type="button" className={activeSection === "dlq" ? "active" : ""} onClick={() => setActiveSection("dlq")}>
          Dead Letter Queue
        </button>
      </nav>

      {activeSection === "dashboard" ? (
        <DashboardSummary
          events={events}
          deadLetterEvents={deadLetterEvents}
          isLoading={isLoading || isDeadLetterLoading}
          refreshKey={summaryRefreshKey}
        />
      ) : null}

      {activeSection === "events" ? (
        <EventsExplorer events={events} isLoading={isLoading} error={error} onRefresh={refreshEventsAndSummary} />
      ) : null}

      {activeSection === "dlq" ? (
        <DeadLetterPanel
          events={deadLetterEvents}
          isLoading={isDeadLetterLoading}
          error={deadLetterError}
          onRefresh={refreshDeadLettersAndSummary}
        />
      ) : null}
    </main>
  );
}
