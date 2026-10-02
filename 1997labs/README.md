# 1997 Labs — company website

Website for **1997 Labs**, an AI & software company in Dubai. Static HTML in `site/`, with no build step. It is **not** deployed by this repository's Site workflow, which publishes the ahmedabouseif.com portfolio.

## Preview locally

```bash
python -m http.server -d 1997labs/site 8002   # then open http://localhost:8002
```

## What changed from the delivered project (2 Oct 2026)

| Area | Change |
|---|---|
| Copy | The text that a script rewrote after page load is now written in the HTML; the rewrite script is gone. Visible text and the demo data were verified identical before and after. |
| Dead code | Removed the hidden profile, how-we-work and process sections, their 48 CSS selectors and a hidden "Profile" menu link. Page size 69 KB → 49 KB. |
| Images | Hero is a JPEG (314 KB) instead of a 1.9 MB PNG. Only the two images the page uses are included (the delivered `dist/` had 13 files, about 15 MB). |
| Colour | Replaced the remaining hard-coded red glows and chart gradient with the lime accent; merged three colour blocks into one. |
| Tech identity | "AI & Software Company" beside the logo and in the title, description and share cards; the hero sentence names AI agents, websites, apps and CRM; a visible row of five service icons; plain menu labels (Services, AI & Automation, Results). |
| Search & sharing | New title and description, 1200×630 share image, large-image Twitter card, richer schema.org data, sitemap dates, 404 page. |
| Privacy | Privacy notice page, linked from the footer and the form. The analytics consent banner appears only once an analytics ID is configured. |
| Charts | Example numbers kept, with a visible "Example data · for illustration, not client results" label. |

## Before going live

1. Approve the preview.
2. Create the new GitHub repository `1997labs` and put the contents of this `1997labs/` folder at its root (including `.github/workflows/pages.yml`, which publishes `site/` on every push to `main`). Then enable Pages: Settings → Pages → Source: GitHub Actions.
3. Point `1997labs.com` at GitHub Pages at your registrar: four `A` records on `@` (`185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`) and a `CNAME` record for `www` to `<your-user>.github.io`. Remove the records that point the domain elsewhere today.
4. Have the privacy notice reviewed by someone qualified in UAE data protection (Federal Decree-Law 45/2021) before launch.
5. Later: add GA4 and Clarity IDs (`ANALYTICS-SETUP.md`), connect the form to a form service or CRM, and add the Arabic version.
