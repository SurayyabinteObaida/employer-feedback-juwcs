import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Login from './pages/Login'
import VerifyLink from './pages/VerifyLink'
import Dashboard from './pages/Dashboard'
import ProformaReview from './pages/ProformaReview'
import InternshipEvaluation from './pages/InternshipEvaluation'
import EmployerSurvey from './pages/EmployerSurvey'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/login" replace />} />
        <Route path="/login" element={<Login />} />
        <Route path="/auth/verify" element={<VerifyLink />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/engagements/:engagementId/proforma" element={<ProformaReview />} />
        <Route path="/engagements/:engagementId/internship-evaluation" element={<InternshipEvaluation />} />
        <Route path="/engagements/:engagementId/employer-survey" element={<EmployerSurvey />} />
      </Routes>
    </BrowserRouter>
  )
}
