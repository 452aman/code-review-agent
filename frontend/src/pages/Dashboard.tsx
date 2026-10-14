import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api'
import ScoreRing from '../components/ScoreRing'
import { SEVERITIES, type ReviewSummary, type Stats } from '../types'

const Tile = ({ label, value }: { label: string; value: string | number }) => (
  <div className="card p-5"><div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div><div className="mt-1 text-3xl font-bold">{value}</div></div>
)

export default function Dashboard() {
  const { data: s } = useQuery({ queryKey: ['stats'], queryFn: async () => (await api.get<Stats>('/stats')).data })
  const { data: reviews = [] } = useQuery({ queryKey: ['reviews'], queryFn: async () => (await api.get<ReviewSummary[]>('/reviews')).data, refetchInterval: 4000 })
  const sev = SEVERITIES.map((k) => ({ name: k, count: s?.by_severity[k] ?? 0 }))
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between"><h1 className="text-2xl font-bold">Review dashboard</h1><Link to="/new" className="btn">New review</Link></div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Tile label="Reviews" value={s?.reviews ?? 0} /><Tile label="Avg score" value={s?.avg_score ?? '–'} />
        <Tile label="Avg duration" value={s?.avg_duration_s != null ? `${s.avg_duration_s}s` : '–'} />
        <Tile label="Issues found" value={Object.values(s?.by_severity ?? {}).reduce((a, b) => a + b, 0)} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="card p-5 lg:col-span-2"><h2 className="mb-3 font-semibold">Code quality trend</h2>
          <ResponsiveContainer width="100%" height={220}><LineChart data={s?.score_trend ?? []}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="at" tickFormatter={(d) => new Date(d).toLocaleDateString()} fontSize={12} /><YAxis domain={[0, 100]} fontSize={12} /><Tooltip labelFormatter={(d) => new Date(d as string).toLocaleString()} /><Line dataKey="score" stroke="#7c5cff" strokeWidth={2} dot /></LineChart></ResponsiveContainer></div>
        <div className="card p-5"><h2 className="mb-3 font-semibold">Findings by severity</h2>
          <ResponsiveContainer width="100%" height={220}><BarChart data={sev}><XAxis dataKey="name" fontSize={12} /><YAxis allowDecimals={false} fontSize={12} /><Tooltip /><Bar dataKey="count" fill="#7c5cff" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div>
      </div>
      {s && s.top_issues.length > 0 && <div className="card p-5"><h2 className="mb-3 font-semibold">Most common issues</h2>
        <ul className="grid gap-2 sm:grid-cols-2">{s.top_issues.map((t) => <li key={t.title} className="flex justify-between rounded-lg bg-slate-50 px-3 py-2 text-sm"><span>{t.title}</span><b>{t.count}</b></li>)}</ul></div>}
      <div className="card divide-y divide-slate-100">
        <h2 className="p-4 font-semibold">Recent reviews</h2>
        {reviews.length === 0 && <p className="p-6 text-center text-sm text-slate-500">No reviews yet. Start one!</p>}
        {reviews.map((r) => (
          <Link key={r.id} to={`/reviews/${r.id}`} className="flex items-center gap-4 p-4 hover:bg-slate-50">
            <ScoreRing score={r.score} grade={r.grade} size={56} />
            <div className="min-w-0 flex-1"><div className="truncate font-medium">{r.title}</div>
              <div className="text-xs text-slate-500">{r.source.replace('_', ' ')} · {new Date(r.created_at).toLocaleString()}{r.duration_s != null && ` · ${r.duration_s}s`}</div></div>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${r.status === 'done' ? 'bg-emerald-50 text-emerald-700' : r.status === 'failed' ? 'bg-rose-50 text-rose-700' : 'bg-violet-50 text-violet-700'}`}>{r.status}</span>
          </Link>
        ))}
      </div>
    </div>
  )
}
