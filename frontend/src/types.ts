export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
export const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info']

export interface Finding {
  id: number; file: string; line: number; end_line: number | null; severity: Severity; category: string
  title: string; explanation: string; suggestion: string; fix: string | null; rule_id: string | null
  source: string; confidence: number; triage: 'open' | 'accepted' | 'dismissed'
}
export interface ReviewSummary {
  id: string; title: string; source: string; source_ref: string | null; status: 'queued' | 'running' | 'done' | 'failed'
  score: number | null; grade: string | null; duration_s: number | null; created_at: string
  stats: { by_severity?: Record<Severity, number>; by_category?: Record<string, number>; rejected_hallucinations?: number }
}
export interface ReviewFile { path: string; content: string; language: string; changed_lines: number[] | null }
export interface PipelineEvent { node: string; status: 'running' | 'done' | 'skipped' | 'error'; data: Record<string, unknown>; t: string }
export interface Review extends ReviewSummary { summary: string; error: string | null; files: ReviewFile[]; events: PipelineEvent[]; findings: Finding[] }
export interface Stats {
  reviews: number; avg_score: number | null; avg_duration_s: number | null
  by_severity: Record<string, number>; by_category: Record<string, number>
  top_issues: { title: string; count: number }[]
  score_trend: { id: string; title: string; score: number; at: string }[]
}
export interface Rule { id: string; title: string; category: string; text: string; score: number }
