import { useState } from 'react'
import './App.css'
import { AuthProvider, useAuth } from './AuthContext'
import { ChatScreen } from './ChatScreen'
import { HomePage } from './HomePage'
import { LoginScreen } from './LoginScreen'

type PublicView = 'home' | 'login'

function Gate() {
  const { session, loading } = useAuth()
  const [view, setView] = useState<PublicView>('home')

  if (loading) {
    return null
  }
  if (session) {
    return <ChatScreen />
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
