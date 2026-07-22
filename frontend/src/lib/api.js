const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed: ${res.status}`)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  requestLoginLink: (work_email) =>
    request('/auth/request-link', { method: 'POST', body: JSON.stringify({ work_email }) }),
  verifyMagicLink: (token) => request(`/auth/verify?token=${encodeURIComponent(token)}`),
  logout: () => request('/auth/logout', { method: 'POST' }),

  getEngagements: () => request('/dashboard/engagements'),

  getProforma: (engagementId) => request(`/engagements/${engagementId}/proforma`),
  validateProforma: (engagementId, data) =>
    request(`/engagements/${engagementId}/proforma/validate`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  submitInternshipEvaluation: (engagementId, data) =>
    request(`/engagements/${engagementId}/internship-evaluation`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  submitEmployerSurvey: (engagementId, data) =>
    request(`/engagements/${engagementId}/employer-survey`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}
