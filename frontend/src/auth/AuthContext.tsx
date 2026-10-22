import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { api, ApiError, UNAUTHORIZED_EVENT } from '../api/client'
import type { AuthResponse, User } from '../types'

interface AuthContextValue {
  user: User | null
  /** True while the initial session check is running. */
  loading: boolean
  /** Set when the session expired while the app was open. */
  sessionExpired: boolean
  expiresAt: string | null
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  clearSessionExpired: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

/**
 * Holds the session state for the whole app.
 *
 * The session itself lives in an HttpOnly cookie (24h TTL, managed by the
 * backend). This provider only mirrors who is logged in, and listens for 401
 * responses so an expired session drops the user back to the login screen.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [expiresAt, setExpiresAt] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [sessionExpired, setSessionExpired] = useState(false)
  const expiryTimer = useRef<number | null>(null)

  const clearTimer = useCallback(() => {
    if (expiryTimer.current !== null) {
      window.clearTimeout(expiryTimer.current)
      expiryTimer.current = null
    }
  }, [])

  const applySession = useCallback(
    (session: AuthResponse) => {
      setUser(session.user)
      setExpiresAt(session.expires_at)

      // Log out locally exactly when the server-side session expires.
      clearTimer()
      if (session.expires_at) {
        const delay = new Date(session.expires_at).getTime() - Date.now()
        if (delay > 0) {
          expiryTimer.current = window.setTimeout(() => {
            setUser(null)
            setExpiresAt(null)
            setSessionExpired(true)
          }, delay)
        } else {
          setUser(null)
          setSessionExpired(true)
        }
      }
    },
    [clearTimer],
  )

  // ---- initial session check -------------------------------------------
  useEffect(() => {
    let cancelled = false
    api
      .me()
      .then((session) => {
        if (!cancelled) applySession(session)
      })
      .catch((error) => {
        // A 401 simply means "not logged in" - the login screen handles it.
        if (!cancelled && !(error instanceof ApiError && error.status === 401)) {
          console.warn('[auth] session check failed:', error)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [applySession])

  // ---- react to an expired session anywhere in the app ------------------
  useEffect(() => {
    const onUnauthorized = () => {
      clearTimer()
      setUser(null)
      setExpiresAt(null)
      setSessionExpired(true)
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => {
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
      clearTimer()
    }
  }, [clearTimer])

  const login = useCallback(
    async (username: string, password: string) => {
      setSessionExpired(false)
      applySession(await api.login(username, password))
    },
    [applySession],
  )

  const register = useCallback(
    async (username: string, password: string) => {
      setSessionExpired(false)
      applySession(await api.register(username, password))
    },
    [applySession],
  )

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      /* the cookie is cleared by the backend regardless */
    } finally {
      clearTimer()
      setUser(null)
      setExpiresAt(null)
      setSessionExpired(false)
    }
  }, [clearTimer])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      sessionExpired,
      expiresAt,
      login,
      register,
      logout,
      clearSessionExpired: () => setSessionExpired(false),
    }),
    [user, loading, sessionExpired, expiresAt, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Access the session state (throws when used outside of `AuthProvider`). */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside <AuthProvider>')
  return context
}
