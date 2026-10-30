# Kizuna — Design System

Every page in `frontend/` references this file. If a new screen needs a color,
radius, type size, or motion value that isn't here, it's wrong — extend this
file first, then use the token.

## Why these choices

Kizuna is a companion app about being known over time, not a SaaS dashboard.
The palette and type are warm and human on purpose: no indigo/blue-to-purple
gradients (the generic "AI product" look), no cold greys, no centered
marketing-template hero. Layout is left-aligned and editorial — text has a
point of view and sits with intention, not inside a symmetrical card grid.

## Typefaces

- **Fraunces** — display/headings only. A warm, soft serif with optical
  sizing (`font-optical-sizing: auto`); let it get more expressive at large
  sizes and quieter at small ones. Weights 300–600, upright only (no italic
  accent-word trick).
- **Hanken Grotesk** — body copy, UI labels, buttons, inputs. Weights
  400/500/600.
- Never Inter. One heading scale (below), used everywhere — no page invents
  its own sizes.
- Loaded from Google Fonts in `index.html` with `display=swap`.

### Type scale (16px root)

| Token | Size | Line-height | Family / weight | Use |
|---|---|---|---|---|
| `--text-display` | `clamp(2.25rem, 1.6rem + 3vw, 3.75rem)` | 1.05 | Fraunces 480 | Home hero headline |
| `--text-h1` | 2rem | 1.1 | Fraunces 480 | Page titles (login "Welcome back") |
| `--text-h2` | 1.5rem | 1.15 | Fraunces 500 | Section headings |
| `--text-h3` | 1.25rem | 1.2 | Fraunces 560 | Wordmark, minor headings |
| `--text-lead` | 1.1875rem | 1.6 | Hanken 400 | Hero/intro paragraphs |
| `--text-body` | 1rem | 1.6 | Hanken 400 | Body copy, messages |
| `--text-ui` | 0.9375rem | 1.45 | Hanken 500 | Buttons, inputs, nav |
| `--text-caption` | 0.8125rem | 1.4 | Hanken 500 | Timestamps, status text |

No all-caps labels, anywhere. A caption reads as a label through size/weight/
color (`muted-ink`), not through letter-casing.

## Color (roles are identical across themes; only the hex changes)

| Role | Light | Dark | Use |
|---|---|---|---|
| `ground` | `#FAF6F0` | `#211E1A` | Page background |
| `surface` | `#FFFFFF` | `#2A2621` | Cards, bubbles, inputs |
| `ink` | `#2B2520` | `#F0EAE2` | Primary text |
| `muted-ink` | `#6B6258` | `#A99F92` | Secondary text, captions |
| `accent` (clay) | `#B85C38` | `#D2794F` | Primary actions, links, focus ring |
| `accent-soft` | `#E8C4B0` | mix at 18% over `surface` | User message bubble, subtle fills |
| `secondary` (sage) | `#7C8A72` | `#94A086` | Icons, quiet affordances, "how it works" markers |
| `hairline` | `#E4DBCE` | `#3A352E` | Borders, dividers — never a shadow for separation |

No indigo, no blue, no purple, no gradients. `accent` doubles as the warm
"needs attention" signal (an inline error) — there is no separate red token;
inventing one outside this table is a DESIGN.md violation.

## Shape & depth

- `--radius-sm: 0.5rem` — buttons, inputs, pills.
- `--radius-lg: 0.75rem` — cards, message bubbles, the login card.
- `--hairline: 1px solid var(--color-hairline)` — the default separator.
- `--shadow` — exactly one recipe, reused everywhere elevation is needed
  (login card, hero preview card). Never a second shadow depth.
  - Light: `0 1px 2px rgba(43,37,32,.05), 0 8px 24px rgba(43,37,32,.08)`
  - Dark: `0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35)`
- No frosted glass, no noise texture, no border-radius-0 broadsheet look.

## Layout

- One content width (`--content-width: 42rem` for text-heavy columns, wider
  for the home page's two-column hero). Left-aligned, editorial rhythm.
- Deliberate asymmetry on the home hero (text left, a small product-preview
  visual right) — not a centered hero, not a pill eyebrow badge.
- Mobile-first: 16px side gutter minimum, no horizontal scroll at any width.

## Icons

[Lucide](https://lucide.dev) (`lucide-react`), 18–20px, `secondary` or
`muted-ink` colored, `aria-hidden`. Used sparingly: the "how it works" steps,
the log-out and resend actions. Never emoji as an icon.

## Motion

Subtle and purposeful only:
- New assistant message: fade + slide-up, 180ms, `ease-out`.
- Typing indicator: three dots, slow staggered opacity pulse.
- All motion is wrapped in `@media (prefers-reduced-motion: no-preference)`
  or disabled under `(prefers-reduced-motion: reduce)` — never both states
  shipped without a reduced-motion fallback.

## Copy voice

Specific and honest. No "Transform/Unlock/Empower," no two-noun feature
names, no invented metrics or testimonials. Kizuna states plainly that it
isn't a therapist or a crisis service.
