# Falaj Labs — company website

Bilingual (English/Arabic) website for **Falaj Labs**, a software engineering studio in Dubai. A *falaj* (plural *aflaj*) is the traditional water channel of the UAE and Oman; the brand story is software built to keep running the way the best aflaj still do.

This folder is a standalone static site. It is **not** part of the portfolio build in `site/` and is not deployed by `.github/workflows/site.yml`.

## Files

| File | Purpose |
|---|---|
| `index.html` | The whole site: markup, styles, Arabic dictionary and script in one file. |
| `favicon.svg` | Browser-tab icon (the branching-channel mark). |

## Preview locally

```bash
python -m http.server -d falaj 8001   # then open http://localhost:8001
```

Add `#ar` to the URL to open it in Arabic. The visitor's language choice is remembered in their browser.

## How the page works

- **Languages.** English is written in the markup. Arabic lives in the JSON block `<script type="application/json" id="i18n">`, keyed by each element's `data-i18n` value (`data-i18n-attr="attribute:key"` for attributes such as placeholders). Switching to Arabic also sets `dir="rtl"`; the layout uses logical CSS properties, so it mirrors without separate styles. Strings built by the script (clock status, copy messages, brief subject) are in both the `en` and `ar` sections of that block.
- **Live details.** The code panel in the hero runs in the visitor's browser: the AED price, Dubai time and Umm al-Qura Hijri date are produced by the `Intl` calls shown. The Dubai clock marks the studio open Monday to Friday, 09:00–18:00 Gulf Standard Time (UTC+4).
- **Contact form.** There is no server. The form builds a project brief that the visitor can email (a `mailto:` link) or copy. Nothing is sent until they do.
- **Motion.** The hero's water-channel animation pauses off-screen and is replaced by a still frame for visitors who prefer reduced motion.

`tests/test_falaj_site.py` checks that every translatable element has an Arabic entry, that there are no unused or empty entries, and that form controls have labels. Run it with `python -m pytest tests/test_falaj_site.py`.

## Before going live

These need real values from the business; the page currently uses proposed ones:

1. **Email address.** `hello@falajlabs.ae` appears in the `EMAIL` constant in the script, the email in the Contact section, and the `mailto:` link of the brief. Replace all three. `.ae` domains have eligibility rules set by the .ae Domain Administration (aeDA); confirm them with your registrar.
2. **Name availability.** `falaj.ae`, `falaj.io` and `falaj.studio` already resolve in DNS, so someone has registered them. `falajlabs.ae` did not resolve when checked, which does not prove it is free. Check the domain with a registrar, and the trade name with the authority you will license under (Dubai's Department of Economy and Tourism for mainland companies, or your free zone), before committing to "Falaj Labs".
3. **Business promises.** Response time (one working day), office hours, stage durations and engagement terms are proposed copy. Adjust them to what the company will actually commit to.
4. **Hosting.** To publish, put this folder in its own repository and enable GitHub Pages, or deploy it to any static host. It needs no build step.
