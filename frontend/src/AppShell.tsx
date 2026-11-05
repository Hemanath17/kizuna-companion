import type { ReactNode } from 'react'
import { LogOut } from 'lucide-react'
import { useAuth } from './AuthContext'

export type AuthedView = 'chat' | 'journal' | 'reflections' | 'memory'

const NAV_ITEMS: { view: AuthedView; label: string }[] = [
  { view: 'chat', label: 'Chat' },
  { view: 'journal', label: 'Journal' },
  { view: 'reflections', label: 'Reflections' },
  { view: 'memory', label: 'Memory' },
]

interface AppShellProps {
  active: AuthedView
  onNavigate: (view: AuthedView) => void
  children: ReactNode
}

// The one shared header (wordmark, nav, log out) for every authenticated
// screen, so adding a screen means adding a nav entry here, not duplicating
// a header per screen.
export function AppShell({ active, onNavigate, children }: AppShellProps) {
  const { signOut } = useAuth()

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1 className="wordmark">Kizuna</h1>
        <nav className="app-nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.view}
              type="button"
              className={`nav-link${active === item.view ? ' nav-link-active' : ''}`}
              aria-current={active === item.view ? 'page' : undefined}
              onClick={() => onNavigate(item.view)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <button type="button" className="icon-button" onClick={() => void signOut()}>
          <LogOut size={18} aria-hidden="true" />
          Log out
        </button>
      </header>
      <div className="app-shell-body">{children}</div>
    </div>
  )
}
