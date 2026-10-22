import { useState, type FormEvent } from 'react'
import { useAuth } from '../auth/AuthContext'
import { ApiError } from '../api/client'
import ThemeToggle from '../theme/ThemeToggle'

type Mode = 'login' | 'register'

/** Login / register screen - the entry point of the app. */
export default function LoginPage() {
  const { login, register, sessionExpired } = useAuth()
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const isRegister = mode === 'register'

  const switchMode = (next: Mode) => {
    setMode(next)
    setError(null)
    setConfirm('')
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return

    if (!username.trim() || !password) {
      setError('Enter your username and password.')
      return
    }
    if (isRegister && password !== confirm) {
      setError('The passwords do not match.')
      return
    }

    setBusy(true)
    setError(null)
    try {
      if (isRegister) {
        await register(username.trim(), password)
      } else {
        await login(username.trim(), password)
      }
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : 'Something went wrong. Please try again.'
      setError(message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <ThemeToggle className="auth-theme" />
      <div className="auth-card">
        <div className="auth-brand">
          <span className="brand-mark" aria-hidden>
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
              <circle cx="12" cy="12" r="4" />
            </svg>
          </span>
          <span className="brand-name">GenRAG</span>
        </div>

        <h1 className="auth-title">{isRegister ? 'Create your account' : 'Welcome back'}</h1>
        <p className="auth-sub">
          {isRegister
            ? 'Your documents and chats stay private to your account.'
            : 'Sign in to reach your documents and chats.'}
        </p>

        {sessionExpired && (
          <div className="notice notice-info auth-notice" role="status">
            <span>Your session expired. Please sign in again.</span>
          </div>
        )}

        {error && (
          <div className="notice notice-error auth-notice" role="alert">
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={submit} className="auth-form">
          <label className="auth-field">
            <span>Username</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              placeholder="your-name"
            />
          </label>

          <label className="auth-field">
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={isRegister ? 'new-password' : 'current-password'}
              placeholder="••••••••"
            />
          </label>

          {isRegister && (
            <label className="auth-field">
              <span>Confirm password</span>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                autoComplete="new-password"
                placeholder="••••••••"
              />
            </label>
          )}

          <button className="primary-btn auth-submit" type="submit" disabled={busy}>
            {busy ? 'Please wait…' : isRegister ? 'Create account' : 'Sign in'}
          </button>
        </form>

        <div className="auth-switch">
          {isRegister ? (
            <span>
              Already have an account?{' '}
              <button className="link-btn" onClick={() => switchMode('login')}>
                Sign in
              </button>
            </span>
          ) : (
            <span>
              No account yet?{' '}
              <button className="link-btn" onClick={() => switchMode('register')}>
                Create one
              </button>
            </span>
          )}
        </div>

        <p className="auth-foot">Sessions last 24 hours on this device.</p>
      </div>
    </div>
  )
}
