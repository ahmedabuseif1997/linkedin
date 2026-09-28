# 971 Labs — company website

Website for **971 Labs**, a digital product and engineering studio in Dubai. +971 is the UAE's international calling code. The design comes from the 971 Labs design package; `CLAUDE-HANDOFF.md` is that package's handoff note and lists the design system.

This folder is a standalone static site. It is **not** part of the portfolio build in `site/` and is not deployed by `.github/workflows/site.yml`.

## Files

| File | Purpose |
|---|---|
| `index.html` | The whole page: markup, styles and script in one file. |
| `assets/971-monolith.jpg` | Hero background image (1672×941). |
| `CLAUDE-HANDOFF.md` | Design handoff: what to preserve when changing the page. |

The hero image was delivered as a 1.7 MB PNG. It is stored here as a quality-90 JPEG (258 KB) at the same size, which is visually the same and about 85% smaller. That is the only change from the package's `index.html` (the image `src`).

## Preview locally

```bash
python -m http.server -d 971labs 8001   # then open http://localhost:8001
```

The fonts (Manrope and DM Mono) load from Google Fonts, so the exact typography needs an internet connection.

`tests/test_971labs_site.py` checks that local files the page references exist, in-page links have targets, ids are unique and images have `alt` text. Run it with `python -m pytest tests/test_971labs_site.py`.

## Before going live

1. **Email address.** `hello@971labs.ae` appears in two `mailto:` links and as visible text in the contact section. It is a proposed address until the inbox exists. `.ae` domains have eligibility rules set by the .ae Domain Administration (aeDA); confirm them with your registrar.
2. **Name availability.** `971labs.com` already resolves in DNS, so someone has registered it. `971labs.ae` did not resolve when checked, which does not prove it is free. Check the domain with a registrar, and the trade name with the authority you will license under (Dubai's Department of Economy and Tourism for mainland companies, or your free zone).
3. **Hosting.** Put this folder in its own repository and enable GitHub Pages, or deploy it to any static host. It needs no build step.
