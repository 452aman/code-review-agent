import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Rule } from '../types'

export default function Rules() {
  const [q, setQ] = useState('sql query built from user input')
  const [dq, setDq] = useState(q)
  const [cat, setCat] = useState('')
  useEffect(() => { const t = setTimeout(() => setDq(q), 400); return () => clearTimeout(t) }, [q])
  const { data = [], isFetching } = useQuery({
    queryKey: ['rules', dq, cat], enabled: dq.length >= 2,
    queryFn: async () => (await api.get<Rule[]>('/rules/search', { params: { q: dq, category: cat || undefined, k: 8 } })).data,
  })
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-2xl font-bold">Review knowledge base</h1>
      <p className="text-sm text-slate-400">Agents ground their reviews in this guidance. Search is semantic (embeddings + FAISS), so describe the problem in your own words.</p>
      <div className="flex gap-2">
        <div className="relative flex-1"><Search size={16} className="absolute left-3 top-2.5 text-slate-500" /><input className="input pl-9" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <select className="input w-auto" value={cat} onChange={(e) => setCat(e.target.value)}><option value="">All</option>{['security', 'bug', 'performance', 'maintainability'].map((c) => <option key={c}>{c}</option>)}</select>
      </div>
      {isFetching && <p className="text-xs text-slate-500">Searching…</p>}
      <ul className="space-y-3">{data.map((r) => (
        <li key={r.id} className="card p-4">
          <div className="flex items-center justify-between"><div className="font-semibold">{r.title}</div><span className="text-xs text-slate-500">similarity {r.score.toFixed(2)}</span></div>
          <div className="mt-0.5 text-xs text-accent">{r.id} · {r.category}</div>
          <p className="mt-2 text-sm text-slate-300">{r.text}</p>
        </li>))}</ul>
    </div>
  )
}
