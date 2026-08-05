# UI Skill

- Every primary button/form must call a real backend route (create/list/update).
- Empty states beat fake cards; show “暂无数据” until the first write succeeds.
- Keep templates/static assets inside planned_paths; avoid SPA framework sprawl unless the Goal requires it.
- Prefer server-rendered HTML or lightweight JS over unimplemented React shells.
- Preview/smoke must hit a route that proves the UI is served by the live app process.

## Product visual quality (fail-closed)

- Ship a designed product UI, not a browser-default dump (black text + blue links on white/black).
- Include substantial CSS (≥800 chars): typography with a deliberate font stack (not Times/Arial-only defaults), spacing, max-width container, CSS color tokens (`:root { --* }`), list/card treatment, and `:hover` states.
- Wrap primary content in `<main>`; use `<section>` / `<article>` / `<nav>` for structure.
- One composition per primary viewport: brand/title, one clear headline, supporting copy, primary actions — avoid raw unstyled link dumps.
- Forbidden “AI template” looks: generic purple-on-white gradients, cream+terracotta brochure pages, flat blue-gray card grids with system fonts only and no hierarchy. Choose a clear visual direction and stick to it.
- Feed/digest/news products must include: brand hero or masthead, “今日必读” (or equivalent) highlight, category filters or sections, list → detail journey, and a working refresh/control action wired to a real backend route.

## Information architecture (PM)

- List cards: the title (or whole card) must be a real `<a href="…">` to an HTML detail route that returns ≥80 chars of visible body text.
- Detail pages reuse the same stylesheet and navigation chrome as the home page.
- Primary ops actions (refresh, search, filter) must hit real routes (`POST /api/refresh`, `GET /search`, etc.) — not dead buttons.
- Honest empty states when data is missing; never fake “demo user” cards unless Goal explicitly asks for a mockup.

## Preview path safety

- Preview URLs are path-prefixed (`/preview/runtime/<id>/`). Prefer **relative** links (`item/123`, `static/app.css`) over root-absolute (`/item/123`, `/static/app.css`).
- List → detail must navigate to a real HTML detail page under the same app; `#` / dead stubs fail product QA.
- Stylesheets must load through the same Preview origin users open; a 404 CSS is a failed delivery even if HTML 200s.
- Live Preview QA fails closed on weak CSS substance and on list products whose detail links mostly 404.
