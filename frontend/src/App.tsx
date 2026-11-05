import { useState } from 'react'
import './App.css'
import { AppShell, type AuthedView } from './AppShell'
import { AuthProvider, useAuth } from './AuthContext'
import { ChatScreen } from './ChatScreen'
import { HomePage } from './HomePage'
import { JournalScreen } from './JournalScreen'
import { LoginScreen } from './LoginScreen'
import { MemoryScreen } from './MemoryScreen'
import { ReflectionsScreen } from './ReflectionsScreen'

type PublicView = 'home' | 'login'

function AuthedApp() {
  const [view, setView] = useState<AuthedView>('chat')
  return (
    <AppShell active={view} onNavigate={setView}>
      {view === 'chat' && <ChatScreen />}
      {view === 'journal' && <JournalScreen />}
      {view === 'reflections' && <ReflectionsScreen />}
      {view === 'memory' && <MemoryScreen />}
    </AppShell>
  )
}

function Gate() {
  const { session, loading } = useAuth()
  const [view, setView] = useState<PublicView>('home')

  if (loading) {
    return null
  }
  if (session) {
    return <AuthedApp />
  }
  if (view === 'login') {
    return <LoginScreen onBack={() => setView('home')} />
  }
  return <HomePage onGetStarted={() => setView('login')} />
}

function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  )
}

export default App
