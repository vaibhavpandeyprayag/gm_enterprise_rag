import { AuthProvider, useAuth } from './auth/AuthContext'
import { ThemeProvider } from './theme/ThemeContext'
import ChatPage from './pages/ChatPage'
import LoginPage from './pages/LoginPage'

/** Splash screen shown while the session is being checked on startup. */
function SessionSplash() {
  return (
    <div className="app-splash">
      <span className="brand-mark" aria-hidden>
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      </span>
      <div className="typing" aria-label="Loading">
        <span className="dot" />
        <span className="dot" />
        <span className="dot" />
      </div>
    </div>
  )
}

/**
 * Application shell: the RAG workspace is only mounted for a signed-in user,
 * everyone else gets the login screen.
 */
function AuthGate() {
  const { user, loading } = useAuth()

  if (loading) return <SessionSplash />
  return user ? <ChatPage /> : <LoginPage />
}

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <AuthGate />
      </AuthProvider>
    </ThemeProvider>
  )
}
