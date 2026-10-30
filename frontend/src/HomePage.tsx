import { Fragment } from 'react'
import { BookOpen, MessageCircle, ShieldCheck, Sparkles, type LucideIcon } from 'lucide-react'

interface HomePageProps {
  onGetStarted: () => void
}

interface Step {
  icon: LucideIcon
  title: string
  body: string
}

const steps: Step[] = [
  { icon: MessageCircle, title: 'Chat', body: 'Talk naturally, like you would with a friend.' },
  { icon: Sparkles, title: 'Remember selectively', body: 'It keeps what matters and lets the rest go.' },
  { icon: BookOpen, title: 'Reflect', body: "See patterns in how you've been feeling." },
  { icon: ShieldCheck, title: 'Stay in control', body: 'Correct or forget anything, anytime.' },
]

export function HomePage({ onGetStarted }: HomePageProps) {
  return (
    <div className="home-page">
      <header className="home-header">
        <span className="wordmark">Kizuna</span>
        <button type="button" className="link-button" onClick={onGetStarted}>
          Sign in
        </button>
      </header>

      <section className="hero">
        <div className="hero-text">
          <h1 className="hero-headline">Someone to talk to who remembers what matters.</h1>
          <p className="hero-lead">
            Kizuna is a companion you can talk through your day with — it remembers the things that
            matter to you, reflects them back over time, and leaves the rest alone. You're always in
            control of what it keeps.
          </p>
          <div className="hero-actions">
            <button type="button" className="btn btn-primary" onClick={onGetStarted}>
              Get started
            </button>
            <a className="btn btn-secondary" href="#how-it-works">
              How it works
            </a>
          </div>
        </div>
        <div className="hero-preview" aria-hidden="true">
          <div className="preview-card">
            <div className="preview-bubble preview-bubble-user">Rough day. Had that interview.</div>
            <div className="preview-bubble preview-bubble-assistant">
              How did it go? You were nervous about the technical round.
            </div>
          </div>
        </div>
      </section>

      <section className="boundary">
        <h2>What Kizuna is — and isn't</h2>
        <p>
          It's a place to think out loud, not a clinical service. Kizuna isn't a therapist and it isn't
          a crisis line — if you're in crisis, please reach out to a real person or a crisis service in
          your area. What it is: a steady, low-stakes place to process your day, with memory that works
          the way you'd want it to.
        </p>
      </section>

      <section className="how-it-works" id="how-it-works">
        <h2>How it works</h2>
        <div className="flow">
          {steps.map((step, i) => (
            <Fragment key={step.title}>
              <div className="flow-step">
                <step.icon size={20} aria-hidden="true" className="flow-icon" />
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
              {i < steps.length - 1 && (
                <span className="flow-arrow" aria-hidden="true">
                  →
                </span>
              )}
            </Fragment>
          ))}
        </div>
      </section>

      <section className="closing-cta">
        <h2>Ready to talk?</h2>
        <button type="button" className="btn btn-primary" onClick={onGetStarted}>
          Get started
        </button>
      </section>

      <footer className="home-footer">
        <p>Kizuna is a companion, not a replacement for real support.</p>
      </footer>
    </div>
  )
}
