import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

function initials(name) {
  return name.split(' ').map((p) => p[0]).slice(0, 2).join('').toUpperCase()
}

function statusBadge(engagement) {
  const proforma = engagement.proforma
  if (!proforma) return { label: 'No proforma', tone: 'warning' }
  if (proforma.validation_status === 'pending') return { label: 'Proforma pending review', tone: 'warning' }
  return { label: 'Proforma validated', tone: 'success' }
}

export default function Dashboard() {
  const [engagements, setEngagements] = useState([])
  const [expanded, setExpanded] = useState({})
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  useEffect(() => {
    api.getEngagements()
      .then(setEngagements)
      .catch(() => navigate('/login'))
      .finally(() => setLoading(false))
  }, [navigate])

  function toggle(id) {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  if (loading) return <div className="page"><p className="subtitle">Loading...</p></div>

  return (
    <div className="page">
      <h1>Students under your supervision</h1>
      <p className="subtitle">
        {engagements.length} {engagements.length === 1 ? 'engagement' : 'engagements'}
      </p>

      {engagements.map((eng) => {
        const badge = statusBadge(eng)
        const isOpen = !!expanded[eng.id]

        return (
          <div key={eng.id} className="card">
            <div className="card-header" onClick={() => toggle(eng.id)}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span className="chevron">{isOpen ? '\u25be' : '\u25b8'}</span>
                <div className="avatar">{initials(eng.student.full_name)}</div>
                <div>
                  <p style={{ fontSize: 14, fontWeight: 500, margin: 0 }}>{eng.student.full_name}</p>
                  <p style={{ fontSize: 12, color: 'var(--text-secondary)', margin: '2px 0 0' }}>
                    {eng.student.degree_program}, batch {eng.student.batch} &middot; {eng.type === 'internship' ? 'Internship' : 'Job'}
                  </p>
                </div>
              </div>
              <span className={`badge badge-${badge.tone}`}>{badge.label}</span>
            </div>

            {isOpen && (
              <div className="card-body">
                <button onClick={() => navigate(`/engagements/${eng.id}/proforma`)}>
                  Review organization proforma
                </button>
                <button
                  className="primary"
                  onClick={() =>
                    navigate(
                      eng.type === 'internship'
                        ? `/engagements/${eng.id}/internship-evaluation`
                        : `/engagements/${eng.id}/employer-survey`
                    )
                  }
                  disabled={!eng.proforma || eng.proforma.validation_status === 'pending'}
                >
                  {eng.type === 'internship' ? 'Internship evaluation' : 'Employer survey'}
                </button>
              </div>
            )}
          </div>
        )
      })}

      {engagements.length === 0 && (
        <p className="subtitle">No students are currently assigned to you.</p>
      )}
    </div>
  )
}
