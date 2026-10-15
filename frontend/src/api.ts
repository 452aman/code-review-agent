import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1' })

export const errMsg = (e: unknown): string => {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x) => x.msg).join('; ')
  return 'Something went wrong'
}

export const sevColor: Record<string, string> = {
  critical: 'bg-rose-400/15 text-rose-300 ring-rose-400/30', high: 'bg-orange-400/15 text-orange-300 ring-orange-400/30',
  medium: 'bg-amber-400/15 text-amber-300 ring-amber-400/30', low: 'bg-sky-400/15 text-sky-300 ring-sky-400/30',
  info: 'bg-white/[0.07] text-slate-300 ring-white/10',
}
export const gradeColor = (g: string | null) => ({ A: '#10b981', B: '#84cc16', C: '#f59e0b', D: '#f97316', F: '#f43f5e' } as Record<string, string>)[g ?? ''] ?? '#94a3b8'
