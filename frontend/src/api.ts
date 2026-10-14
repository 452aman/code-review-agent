import axios from 'axios'

export const api = axios.create({ baseURL: '/api/v1' })

export const errMsg = (e: unknown): string => {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x) => x.msg).join('; ')
  return 'Something went wrong'
}

export const sevColor: Record<string, string> = {
  critical: 'bg-rose-100 text-rose-700 ring-rose-200', high: 'bg-orange-100 text-orange-700 ring-orange-200',
  medium: 'bg-amber-100 text-amber-700 ring-amber-200', low: 'bg-sky-100 text-sky-700 ring-sky-200',
  info: 'bg-slate-100 text-slate-600 ring-slate-200',
}
export const gradeColor = (g: string | null) => ({ A: '#10b981', B: '#84cc16', C: '#f59e0b', D: '#f97316', F: '#f43f5e' } as Record<string, string>)[g ?? ''] ?? '#94a3b8'
