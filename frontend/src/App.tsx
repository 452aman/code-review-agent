import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import NewReview from './pages/NewReview'
import Rules from './pages/Rules'

const ReviewDetail = lazy(() => import('./pages/ReviewDetail')) // pulls in Monaco, so load on demand

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="new" element={<NewReview />} />
        <Route path="rules" element={<Rules />} />
        <Route path="reviews/:id" element={<Suspense fallback={<p className="text-slate-500">Loading…</p>}><ReviewDetail /></Suspense>} />
      </Route>
    </Routes>
  )
}
