import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import RatingScale from '../components/RatingScale'

const INDICATORS = [
  ['rating_core_knowledge', 'Demonstrates strong knowledge of core Computer Science concepts (programming languages, data structures, algorithms, databases, OS/networks) as applied to assigned tasks'],
  ['rating_knowledge_application', 'Applies computing knowledge effectively to accomplish assigned professional tasks'],
  ['rating_problem_solving', 'Able to identify, break down, debug, and analyze software/technical problems encountered during assigned tasks'],
  ['rating_dev_contribution', 'Contributes effectively to designing, coding, or developing software modules, features, APIs, or systems for the assigned project'],
  ['rating_tool_usage', 'Makes effective use of modern development tools (IDEs, Git/version control, cloud platforms, frameworks, testing/debugging tools)'],
  ['rating_teamwork', 'Works effectively both independently and as part of a development/project team (e.g. Agile/Scrum, code reviews)'],
  ['rating_communication', 'Communicates effectively (written and oral) with supervisors and team members, including documentation and code comments'],
  ['rating_professionalism', 'Demonstrates professional responsibility by maintaining punctuality, regular attendance, and fulfilling assigned duties'],
  ['rating_ethics', 'Demonstrates honesty, integrity, and ethical conduct while handling code, data, or client information'],
  ['rating_learning_attitude', 'Demonstrates enthusiasm for learning, accepts feedback positively, and independently acquires new knowledge/skills required for assigned tasks'],
]

const initialState = {
  survey_year: '',
  current_job_role: '',
  employment_department: '',
  employment_duration: '',
  ...Object.fromEntries(INDICATORS.map(([key]) => [key, null])),
  comments: '',
}

export default function EmployerSurvey() {
  const { engagementId } = useParams()
  const navigate = useNavigate()
  const [form, setForm] = useState(initialState)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  function set(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      await api.submitEmployerSurvey(engagementId, form)
      navigate('/dashboard')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page-narrow">
      <h1>Employer survey</h1>
      <p className="subtitle">
        Assessment of program learning outcomes, based on your direct observation of the graduate's performance.
      </p>

      <form onSubmit={handleSubmit}>
        <div className="field">
          <label>Academic year for this survey</label>
          <input
            type="text"
            placeholder="2025-2026"
            value={form.survey_year}
            onChange={(e) => set('survey_year', e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label>Current job role</label>
          <input type="text" value={form.current_job_role} onChange={(e) => set('current_job_role', e.target.value)} />
        </div>
        <div className="field">
          <label>Department</label>
          <input type="text" value={form.employment_department} onChange={(e) => set('employment_department', e.target.value)} />
        </div>
        <div className="field" style={{ marginBottom: 20 }}>
          <label>Duration of employment</label>
          <input type="text" value={form.employment_duration} onChange={(e) => set('employment_duration', e.target.value)} />
        </div>

        {INDICATORS.map(([key, label]) => (
          <RatingScale key={key} label={label} value={form[key]} onChange={(v) => set(key, v)} />
        ))}

        <div className="field" style={{ marginTop: 20 }}>
          <label>General comments</label>
          <textarea rows={4} value={form.comments} onChange={(e) => set('comments', e.target.value)} />
        </div>

        {error && <p className="error-text">{error}</p>}

        <button type="submit" className="primary" disabled={saving} style={{ width: '100%' }}>
          {saving ? 'Submitting...' : 'Submit survey'}
        </button>
      </form>
    </div>
  )
}
