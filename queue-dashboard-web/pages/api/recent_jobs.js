export default async function handler(req, res) {
  const base = process.env.DASHBOARD_API_URL || "http://queue-dashboard-api:8000";
  const limit = req.query.limit ? Number(req.query.limit) : 50;
  const r = await fetch(`${base}/recent_jobs?limit=${limit}`);
  const data = await r.json();
  res.status(r.status).json(data);
}

