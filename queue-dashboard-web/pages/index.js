import { useEffect, useMemo, useState } from "react";

const POLL_MS = 15000;
const POLL_RECENT_MS = 30000;

function formatIso(d) {
  if (!d) return "";
  return d.replace("T", " ").replace("Z", "");
}

export default function Home() {
  const [stats, setStats] = useState(null);
  const [series, setSeries] = useState([]);
  const [queueStartAt, setQueueStartAt] = useState(null);
  const [recentJobs, setRecentJobs] = useState([]);
  const [error, setError] = useState("");

  const maxSubmitted = useMemo(() => {
    return series.reduce((m, p) => Math.max(m, p.submitted || 0), 0);
  }, [series]);

  async function loadStats() {
    setError("");
    const r = await fetch("/api/stats");
    if (!r.ok) throw new Error(`stats failed: ${r.status}`);
    const data = await r.json();
    setStats(data.counts);
    setQueueStartAt(data.queue_start_at);
  }

  async function loadTimeSeries() {
    setError("");
    const r = await fetch("/api/time_series?minutes=60");
    if (!r.ok) throw new Error(`time_series failed: ${r.status}`);
    const data = await r.json();
    setSeries(data.series || []);
  }

  async function loadRecentJobs() {
    setError("");
    const r = await fetch("/api/recent_jobs?limit=50");
    if (!r.ok) throw new Error(`recent_jobs failed: ${r.status}`);
    const data = await r.json();
    setRecentJobs(data.jobs || []);
  }

  useEffect(() => {
    let alive = true;

    async function boot() {
      try {
        await Promise.all([loadStats(), loadTimeSeries(), loadRecentJobs()]);
      } catch (e) {
        if (!alive) return;
        setError(String(e?.message || e));
      }
    }
    boot();

    const t1 = setInterval(async () => {
      try {
        await Promise.all([loadStats(), loadTimeSeries()]);
      } catch (e) {
        setError(String(e?.message || e));
      }
    }, POLL_MS);

    const t2 = setInterval(async () => {
      try {
        await loadRecentJobs();
      } catch (e) {
        setError(String(e?.message || e));
      }
    }, POLL_RECENT_MS);

    return () => {
      alive = false;
      clearInterval(t1);
      clearInterval(t2);
    };
  }, []);

  return (
    <div style={{ fontFamily: "system-ui, Arial", padding: 16 }}>
      <h1 style={{ marginTop: 0 }}>SUMO-K8 Queue Dashboard</h1>

      {error ? (
        <div style={{ color: "crimson", marginBottom: 12 }}>
          {error}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        <Kpi label="QUEUED" value={stats?.queued ?? 0} />
        <Kpi label="PENDING" value={stats?.pending ?? 0} />
        <Kpi label="RUNNING" value={stats?.running ?? 0} />
        <Kpi label="SUCCEEDED" value={stats?.succeeded ?? 0} />
        <Kpi label="FAILED" value={stats?.failed ?? 0} />
      </div>

      <div style={{ marginTop: 12, marginBottom: 12 }}>
        <strong>Queue start:</strong> {formatIso(queueStartAt)}
      </div>

      <h2 style={{ fontSize: 16, marginBottom: 8 }}>Submissions (last 60 minutes)</h2>
      <div style={{ display: "flex", gap: 6, alignItems: "flex-end", height: 120, overflowX: "auto" }}>
        {series.map((p) => (
          <div key={p.minute} style={{ width: 10 }}>
            <div
              title={p.minute}
              style={{
                background: "#1976d2",
                height: maxSubmitted ? `${(100 * (p.submitted || 0)) / maxSubmitted}%` : "0%",
                minHeight: 2,
                borderRadius: 2
              }}
            />
          </div>
        ))}
      </div>

      <h2 style={{ fontSize: 16, marginTop: 18, marginBottom: 8 }}>Recent Jobs</h2>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <Th>Job</Th>
              <Th>Status</Th>
              <Th>Tenant</Th>
              <Th>Submitted</Th>
              <Th>Started</Th>
              <Th>Finished</Th>
            </tr>
          </thead>
          <tbody>
            {recentJobs.map((j) => (
              <tr key={j.job_id}>
                <Td style={{ fontFamily: "monospace" }}>{j.job_id.slice(0, 8)}</Td>
                <Td>{j.status}</Td>
                <Td>{j.tenant_id}</Td>
                <Td>{formatIso(j.submitted_at)}</Td>
                <Td>{formatIso(j.started_at)}</Td>
                <Td>{formatIso(j.finished_at)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Kpi({ label, value }) {
  return (
    <div style={{ border: "1px solid #ddd", borderRadius: 8, padding: "10px 12px", minWidth: 120 }}>
      <div style={{ fontSize: 12, color: "#555" }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

function Th({ children }) {
  return (
    <th style={{ textAlign: "left", borderBottom: "1px solid #eee", padding: 8, fontSize: 12, color: "#444" }}>
      {children}
    </th>
  );
}

function Td({ children, style }) {
  return (
    <td style={{ padding: 8, borderBottom: "1px solid #f2f2f2", fontSize: 12, ...style }}>
      {children}
    </td>
  );
}

