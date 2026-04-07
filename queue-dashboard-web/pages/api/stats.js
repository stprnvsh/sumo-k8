export default async function handler(req, res) {
  const base = process.env.DASHBOARD_API_URL || "http://queue-dashboard-api:8000";
  const r = await fetch(`${base}/stats`);
  const data = await r.json();
  res.status(r.status).json(data);
}

