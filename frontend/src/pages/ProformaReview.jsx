import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'

const FIELDS = [
  { key: 'organization_name', label: 'Organization name' },
  { key: 'role_designation', label: 'Role / designation' },
  { key: 'department_served', label: 'Department served' },
  { key: 'supervisor_name', label: "Supervisor's name" },
  { key: 'supervisor_designation', label: "Supervisor's designation" },
  { key: 'contact_email', label: 'Contact email' },
  { key: 'contact_phone', label: 'Contact phone' },
  { key: 'linkedin_url', label: 'LinkedIn URL' },
]

export default function ProformaReview() {
  const { engagementId } = useParams()
  const navigate = useNavigate()
  const [form, setForm] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    api.getProforma(engagementId).then(setForm).catch((err) => setError(err.message))
  }, [engagementId])

  async function handleSubmit(e) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      const payload = Object.fromEntries(FIELDS.map(({ key }) => [key, form[key] || null]))
      await api.validateProforma(engagementId, payload)
      navigate('/dashboard')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (error && !form) return <div className="page-narrow"><p className="error-text">{error}</p></div>
  if (!form) return <div className="page-narrow"><p className="subtitle">Loading...</p></div>

  return (
    <div className="page-narrow">
      <h1>Organization proforma</h1>
      <p className="subtitle">Confirm the details below are correct, or edit anything that isn't.</p>

      <form onSubmit={handleSubmit}>
        {FIELDS.map(({ key, label }) => (
          <div key={key} className="field">
            <label>{label}</label>
            <input
              type="text"
              value={form[key] || ''}
              onChange={(e) => setForm({ ...form, [key]: e.target.value })}
            />
          </div>
        ))}

        {error && <p className="error-text">{error}</p>}

        <button type="submit" className="primary" disabled={saving} style={{ width: '100%' }}>
          {saving ? 'Saving...' : 'Confirm and continue'}
        </button>
      </form>
    </div>
  )
}
