import { BookOpen, LayoutDashboard, PlusCircle, ShieldCheck } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'

const cls = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium ${isActive ? 'bg-white/10 text-white' : 'text-slate-300 hover:bg-white/5'}`

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="bg-ink-950">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-4 py-3">
          <div className="flex items-center gap-2 text-lg font-bold text-white"><ShieldCheck className="text-accent" />Code Review Agent</div>
          <nav className="flex gap-1">
            <NavLink to="/" end className={cls}><LayoutDashboard size={16} />Dashboard</NavLink>
            <NavLink to="/new" className={cls}><PlusCircle size={16} />New review</NavLink>
            <NavLink to="/rules" className={cls}><BookOpen size={16} />Rules</NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6"><Outlet /></main>
    </div>
  )
}
