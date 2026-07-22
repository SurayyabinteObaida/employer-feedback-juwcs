import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'

export default function VerifyLink() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState('')

  useEffect(() => {
    const token = searchParams.get('token')
    if (!token) {
      setError('Missing sign-in token.')
      return
    }
    api.verifyMagicLink(token)
      .then(() => navigate('/dashboard', { replace: true }))
      .catch((err) => setError(err.message))
  }, [searchParams, navigate])

  return (
    <div className="page-narrow" style={{ maxWidth: 400, marginTop: 80, textAlign: 'center' }}>
      {error ? (
        <>
          <p className="error-text">{error}</p>
          <p className="subtitle">
            This link may have expired. Request a new one from the sign-in page.
          </p>
        </>
      ) : (
        <p style={{ fontSize: 14 }}>Signing you in...</p>
      )}
    </div>
  )
}
