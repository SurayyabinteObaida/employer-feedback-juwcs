import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import RatingScale from '../components/RatingScale'

const INDICATORS = [
  ['rating_core_knowledge', 'Knowledge of core computing concepts (programming languages, data structures, algorithms, databases, OS/networks) as applied to assigned tasks'],
  ['rating_problem_solving', 'Ability to identify, break down, debug, and analyze software/technical problems encountered during assigned tasks'],
  ['rating_dev_contribution', 'Contribution to designing, coding, or developing software modules, features, APIs, or systems for the assigned project'],
  ['rating_tool_usage', 'Effective use of modern development tools (IDEs, Git/version control, cloud platforms, frameworks, testing/debugging tools)'],
  ['rating_teamwork', 'Ability to work effectively both independently and as part of a development/project team (e.g. Agile/Scrum, code reviews)'],
  ['rating_communication', 'Communication skills with supervisors and team members, both in speaking and writing'],
  ['rating_societal_awareness', 'Awareness of how the software/technology being built impacts end-users, clients, or society (e.g. usability, accessibility)'],
  ['rating_ethics', 'Professional ethics, data privacy, confidentiality, and integrity while handling code, data, or client information'],
  ['rating_learning_attitude', 'Interest in continuous learning by adopting new tools, technologies, and skills, and adapting to changing work requirements'],
]

const initialState = {
  ...Object.fromEntries(INDICATORS.map(([key]) => [key, null])),
  attendance_bracket: '',
  task_completion: '',
  overall_rating: '',
  recommend: '',
  comments: '',
}

export default function InternshipEvaluation() {
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
      await api.submitInternshipEvaluation(engagementId, form)
      navigate('/dashboard')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="page-narrow">
      <h1>Internship evaluation proforma</h1>
      <p className="subtitle">
        Rate the intern's performance on each indicator, 5 (excellent) to 1 (needs improvement).
      </p>

      <form onSubmit={handleSubmit}>
        {INDICATORS.map(([key, label]) => (
          <RatingScale key={key} label={label} value={form[key]} onChange={(v) => set(key, v)} />
        ))}

        <div className="field" style={{ marginTop: 20 }}>
          <label>Attendance during internship</label>
          <select value={form.attendance_bracket} onChange={(e) => set('attendance_bracket', e.target.value)} required>
            <option value="">Select</option>
            <option value="below_50">Below 50% (irregular)</option>
            <option value="51_70">51%-70% (fairly regular)</option>
            <option value="71_plus">71% or above (regular)</option>
          </select>
        </div>

        <div className="field">
          <label>Task completion</label>
          <select value={form.task_completion} onChange={(e) => set('task_completion', e.target.value)} required>
            <option value="">Select</option>
            <option value="on_time">On time</option>
            <option value="minor_delays">Minor delays</option>
          </select>
        </div>

        <div className="field">
          <label>Overall performance rating</label>
          <select value={form.overall_rating} onChange={(e) => set('overall_rating', e.target.value)} required>
            <option value="">Select</option>
            <option value="excellent">Excellent</option>
            <option value="good">Good</option>
            <option value="fair">Fair</option>
          </select>
        </div>

        <div className="field">
          <label>Would you recommend this intern for future internships or employment?</label>
          <select value={form.recommend} onChange={(e) => set('recommend', e.target.value)} required>
            <option value="">Select</option>
            <option value="yes">Yes</option>
            <option value="no">No</option>
            <option value="maybe">Maybe</option>
          </select>
        </div>

        <div className="field">
          <label>Comments / suggestions for the academic institution</label>
          <textarea rows={4} value={form.comments} onChange={(e) => set('comments', e.target.value)} />
        </div>

        {error && <p className="error-text">{error}</p>}

        <button type="submit" className="primary" disabled={saving} style={{ width: '100%' }}>
          {saving ? 'Submitting...' : 'Submit evaluation'}
        </button>
      </form>
    </div>
  )
}
