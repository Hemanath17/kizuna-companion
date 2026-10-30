import { useState, type FormEvent } from 'react'
import { useAuth } from './AuthContext'

interface LoginScreenProps {
  onBack: () => void
}

export function LoginScreen({ onBack }: LoginScreenProps) {
  const { signIn } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    const { error: signInError } = await signIn(email, password)
    setSubmitting(false)
    if (signInError) {
      setError(signInError)
    }
  }

  return (
    <div className="login-screen">
      <button type="button" className="link-button login-back" onClick={onBack}>
        ← Kizuna
      </button>
      <div className="login-card">
        <h1 className="text-h1">Welcome back</h1>
        <p className="login-subtitle">Sign in to pick up where you left off.</p>
        <form className="login-form" onSubmit={handleSubmit}>
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          {error && (
            <p role="alert" className="login-error">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn-primary login-submit" disabled={submitting}>
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
        <p className="login-footnote">Your conversations stay private — only you can read them.</p>
      </div>
    </div>
  )
}
