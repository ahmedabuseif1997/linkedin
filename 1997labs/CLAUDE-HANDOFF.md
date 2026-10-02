# 1997 Labs website — Claude handoff

This folder holds the current 1997 Labs website. It has not been published yet.

## Open the website

The finished static website is in `site/`. No framework, package installation or build step is needed.

```bash
python -m http.server -d 1997labs/site 8002   # then open http://localhost:8002
```

## Main files

- `site/index.html` — page structure, styling, motion, responsive behaviour, analytics scaffolding and copy.
- `site/assets/1997-labs-hero-v7.jpg` — active hero artwork (quality-88 JPEG of the original v7 PNG, same 1942×809 size). The page recolours it to lime with a CSS filter.
- `site/assets/1997-labs-mark.svg` — logo and favicon.
- `site/assets/og-image.jpg` — 1200×630 share image (hero crop with the same lime filter applied).
- `site/privacy.html`, `site/404.html`, `site/robots.txt`, `site/sitemap.xml`.
- `1997-LABS-BRAND-PROFILE.md` — positioning and brand foundation.
- `ANALYTICS-SETUP.md` — how to switch analytics on.

Older hero and logo files, `LOGO-GUIDE.md` (which describes the earlier 971 Labs logo kit) and `.openai/hosting.json` were not carried over. They remain in the original project zip.

## Current direction

- Brand: 1997 Labs, an **AI & Software Company** in Dubai. The descriptor sits next to the logo on every screen.
- Main hook: “More customers. Less work. More revenue.”
- Primary action: free business evaluation.
- Palette: obsidian `#080B0A`, electric lime `#B8FF3D`, warm white `#F4F5EF`, slate `#87918B`.
- Voice: short, plain, catchy, and understandable to a nontechnical business owner.
- Layout: cinematic hero, then how we help, connected business technology, results and analytics, and the free review.

## Implementation notes

- All copy is written directly in the HTML. The earlier script that rewrote the text after the page loaded has been removed, so search engines, link previews and visitors without JavaScript see the real copy.
- The three unused sections (profile, how we work, process) and their styles were deleted.
- The analytics charts use example data and carry a visible “not client results” label.
- The consent banner only appears once a GA4 or Clarity ID is set in `analyticsConfig`.
- The free-review form opens the visitor's email app addressed to `info@1997labs.com` (no server yet).

## Preserve while editing

- The large metallic `1997` hero artwork and scroll motion.
- The electric-lime brand direction.
- The simplified, benefit-first language, plus the “AI & Software Company” descriptor.
- Desktop and mobile layouts.
- The interactive connected-business demonstration.
- Analytics charts and the free-evaluation form.
- `info@1997labs.com` as the public contact email.
- Accessibility labels and reduced-motion support.

Do not deploy or publish changes unless the owner explicitly approves the preview.
