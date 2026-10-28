import { CheckCircle2, Circle, Loader2, MinusCircle, XCircle } from 'lucide-react'
import type { PipelineEvent } from '../types'

const STAGES: { key: string; label: string; parallel?: boolean }[] = [
  { key: 'ingest', label: 'Ingest' }, { key: 'static_analysis', label: 'Static analysis' }, { key: 'retrieve', label: 'FAISS retrieval' },
  { key: 'security', label: 'Security agent', parallel: true }, { key: 'bugs', label: 'Bug agent', parallel: true },
  { key: 'performance', label: 'Performance agent', parallel: true }, { key: 'maintainability', label: 'Maintainability agent', parallel: true },
  { key: 'verify', label: 'Verify & merge' }, { key: 'calibrate', label: 'Severity check' }, { key: 'summarize', label: 'Summarize' },
]

function status(events: PipelineEvent[], key: string) {
  const mine = events.filter((e) => e.node === key)
  return mine.length ? mine[mine.length - 1] : null
}

const Icon = ({ s }: { s?: string }) =>
  s === 'done' ? <CheckCircle2 size={16} className="text-emerald-500" />
    : s === 'running' ? <Loader2 size={16} className="animate-spin text-accent" />
    : s === 'error' ? <XCircle size={16} className="text-rose-500" />
    : s === 'skipped' ? <MinusCircle size={16} className="text-slate-500" /> : <Circle size={16} className="text-slate-300" />

export default function Pipeline({ events }: { events: PipelineEvent[] }) {
  const row = (st: (typeof STAGES)[number]) => {
    const e = status(events, st.key)
    const detail = e?.data ? Object.entries(e.data).map(([k, v]) => `${v} ${k}`).join(' · ') : ''
    return (
      <li key={st.key} className="flex items-center gap-2 text-sm">
        <Icon s={e?.status} /><span className={e ? 'font-medium' : 'text-slate-500'}>{st.label}</span>
        {e?.status === 'skipped' && <span className="text-xs text-slate-500">no LLM key</span>}
        {e?.status === 'error' && <span className="text-xs text-rose-500">failed, continuing</span>}
        {detail && e?.status === 'done' && <span className="ml-auto text-xs text-slate-500">{detail}</span>}
      </li>
    )
  }
  const seq = STAGES.filter((s) => !s.parallel)
  const par = STAGES.filter((s) => s.parallel)
  return (
    <ul className="space-y-2.5">
      {seq.slice(0, 3).map(row)}
      <li><div className="rounded-lg border border-dashed border-white/15 p-3"><div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">Run in parallel</div><ul className="space-y-2.5">{par.map(row)}</ul></div></li>
      {seq.slice(3).map(row)}
    </ul>
  )
}
