# 1997 Labs site — notes for Claude

Read this before working here; it replaces re-reading the whole page.

- Site lives in `site/`. One page (`index.html`, ~50 KB, inline CSS and JS), plus `privacy.html`, `404.html`, `robots.txt`, `sitemap.xml`, `assets/`.
- Brand rules and what to preserve: `CLAUDE-HANDOFF.md`. Positioning and palette: `1997-LABS-BRAND-PROFILE.md`.
- Copy is in the HTML. Never add a script that rewrites page text after load.
- Accent colour is `var(--accent)` (`#B8FF3D`). Do not reintroduce red (`#FF3B30`).
- Keep the "AI & Software Company" descriptor next to the logo and in the title and description.
- Example chart numbers must keep the "not client results" label.
- Tests: `python -m pytest tests/test_1997labs_site.py` (assets, links, descriptor, colours, metadata, image budget under 400 KB each and 1 MB total).
- Inspect `index.html` with targeted `grep` or short scripts instead of reading it whole. Check visuals with one screenshot per change, not repeated loops.
- Domain: `https://1997labs.com/` (owner confirmed). Hosting: a new GitHub repository with GitHub Pages, created only after the owner approves the preview.
