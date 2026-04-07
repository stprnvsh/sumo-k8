export default async function handler(req, res) {
  const base = process.env.DASHBOARD_API_URL || "http://queue-dashboard-api:8000";
  const minutes = req.query.minutes ? Number(req.query.minutes) : 60;
  const r = await fetch(`${base}/time_series?minutes=${minutes}`);
  const data = await r.json();
  res.status(r.status).json(data);
}

