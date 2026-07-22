import { useState } from 'react'
import { api } from '../lib/api'

export default function Login() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    setStatus('sending')
    setError('')
    try {
      await api.requestLoginLink(email)
      setStatus('sent')
    } catch (err) {
      setStatus('error')
      setError(err.message)
    }
  }

  return (
    <div className="page-narrow" style={{ maxWidth: 400, marginTop: 80 }}>
      <h1>Employer portal</h1>
      <p className="subtitle">
        Sign in with your work email to review student proformas and submit evaluations.
      </p>

      {status === 'sent' ? (
        <div className="card" style={{ padding: 16 }}>
          <p style={{ fontSize: 14, margin: 0 }}>
            Check {email} for a sign-in link. It expires in 30 minutes.
          </p>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          <div className="field">
            <input
              type="email"
              required
              placeholder="name@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <button type="submit" className="primary" disabled={status === 'sending'} style={{ width: '100%' }}>
            {status === 'sending' ? 'Sending link...' : 'Send sign-in link'}
          </button>
          {status === 'error' && <p className="error-text" style={{ marginTop: 10 }}>{error}</p>}
        </form>
      )}
    </div>
  )
}
