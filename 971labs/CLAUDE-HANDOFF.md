# 971 Labs website handoff

This package contains the complete 971 Labs website design.

## Open the website

Open `index.html` directly in a browser, or serve this directory with any static web server.

## Package contents

- `index.html` — complete page structure, responsive styling, animations, and interactions
- `assets/971-monolith.jpg` — full-resolution (1672×941) cinematic hero background, saved as JPEG (quality 90) from the original 1.7 MB PNG

The page is plain HTML, CSS, and JavaScript with no build step or framework required. The CSS and JavaScript are embedded inside `index.html`, making it easy to import into another project or ask Claude to modify.

## Design system

- Near-black background with electric cyan and warm amber accents
- Cinematic pinned hero with scroll-linked parallax
- Animated grid and scanning-light layers
- 3D pointer response on service cards
- Intersection-based section reveals
- Responsive mobile layouts
- `prefers-reduced-motion` accessibility fallback

## Notes for Claude

Preserve the existing brand, copy, content order, responsive behavior, and reduced-motion support unless explicitly asked to change them. The typefaces are loaded from Google Fonts, so an internet connection is required for the exact typography; system fallbacks are already defined.
