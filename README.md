# Ahmed Abouseif — portfolio website

Bilingual (English/Arabic) executive portfolio, published at **https://ahmedabouseif.com/** with GitHub Pages, plus a MiniMax-powered SEO agent that proposes improvements every week as a pull request.

## Repository layout

| Path | Purpose |
|---|---|
| `site/` | Website source. `index.html` holds the page; images are in `site/assets/`. |
| `site.config.json` | The canonical site URL. The only place the domain is set. |
| `site_tools/build.py` | Builds `site/` into `dist/`: fills in `{{SITE_URL}}`, generates `sitemap.xml` and `robots.txt`. |
| `site_tools/audit.py` | Deterministic SEO audit of `dist/`. Deploys are blocked if it reports errors. |
| `seo_agent/` | The SEO agent. `instructions.md` is its brief; `workspace.py` holds its sandboxed tools. |
| `.github/workflows/site.yml` | On pull requests: tests, build, audit. On `main`: the same, then deploy to GitHub Pages. |
| `.github/workflows/seo-agent.yml` | Weekly SEO agent run that opens a pull request for review. |
| `tests/` | Tests for the build, the audit, the agent's sandbox, and the agent loop. |

## One-time setup

### 1. Publish with GitHub Pages
1. Repository **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Merge this work into `main`. The **Site** workflow builds, audits and deploys it.

### 2. Connect ahmedabouseif.com
1. **Settings → Pages → Custom domain**: enter `ahmedabouseif.com` and save.
2. At your domain registrar, create these DNS records:

   | Type | Host | Value |
   |---|---|---|
   | A | `@` | `185.199.108.153` |
   | A | `@` | `185.199.109.153` |
   | A | `@` | `185.199.110.153` |
   | A | `@` | `185.199.111.153` |
   | AAAA | `@` | `2606:50c0:8000::153` |
   | AAAA | `@` | `2606:50c0:8001::153` |
   | AAAA | `@` | `2606:50c0:8002::153` |
   | AAAA | `@` | `2606:50c0:8003::153` |
   | CNAME | `www` | `ahmedabuseif1997.github.io` |

   Remove any other A/AAAA records on `@` (for example a registrar parking page).
3. When GitHub shows the DNS check as successful, tick **Enforce HTTPS** on the same page.
4. Recommended: verify the domain for your GitHub account (**your profile Settings → Pages → Add a domain**) so nobody else can claim it on GitHub Pages.

Source: [Managing a custom domain for your GitHub Pages site](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site).

### 3. Turn on the SEO agent
1. Create an API key on the [MiniMax platform](https://platform.minimax.io/) (mainland China accounts: [platform.minimaxi.com](https://platform.minimaxi.com/)) and add it as a repository secret named `MINIMAX_API_KEY` (**Settings → Secrets and variables → Actions → New repository secret**).
2. **Settings → Actions → General → Workflow permissions**: tick **Allow GitHub Actions to create and approve pull requests** (the agent needs this to open its pull request).
3. Optional repository variables (**Settings → Secrets and variables → Actions → Variables**):
   - `SEO_AGENT_MODEL` — MiniMax model ID (default `MiniMax-M3`).
   - `MINIMAX_BASE_URL` — only for mainland China accounts: `https://api.minimaxi.com/v1` (default `https://api.minimax.io/v1`).

### 4. Tell search engines about the site
1. [Google Search Console](https://search.google.com/search-console): add a **Domain** property for `ahmedabouseif.com` (verified with a DNS TXT record), then submit `https://ahmedabouseif.com/sitemap.xml` under **Sitemaps**.
2. [Bing Webmaster Tools](https://www.bing.com/webmasters): import the site from Google Search Console.
3. Link to `https://ahmedabouseif.com/` from your LinkedIn profile (Contact info → Website).

## How the SEO agent works

Every Monday at 09:17 Dubai time (or on demand: **Actions → SEO agent → Run workflow**, with optional extra instructions):

1. It builds and audits the current site, and reads recent changes so it does not undo them.
2. The MiniMax model reviews the page, can check the live site, and edits files in `site/` only.
3. Before it can finish, its changes must pass validation: the site builds, no new audit errors, no English text left without an Arabic translation, and the protected facts (metric numbers, email, phone, WhatsApp, section anchors) are unchanged.
4. The workflow re-runs the build and audit, then opens a pull request describing each change and why. **Nothing goes live until you merge it.**

No pull request is opened when there is nothing worth changing, and a run is skipped while a previous SEO pull request is still open. Each pull request lists the tokens the run used.

The agent calls MiniMax's OpenAI-compatible Chat Completions API (`MiniMax-M3` by default) with `reasoning_split` enabled, and sends the model's reasoning back with each step as MiniMax requires for multi-step tool use.

## Local development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m site_tools.build                  # build into dist/
python -m http.server -d dist 8000          # preview at http://localhost:8000
python -m site_tools.audit                  # SEO audit of dist/
python -m pytest                            # tests
python -m seo_agent --dry-run               # show what the agent would start from (no API call)
MINIMAX_API_KEY=... python -m seo_agent     # full agent run; writes seo-report.md and seo-title.txt
```

## Editing the site by hand

- Edit `site/index.html`. The Arabic version is produced in the browser from the `const arabic = { ... }` dictionary near the end of the file: each key is the exact English text of an element and each value its Arabic translation. When you change visible English text, update its dictionary entry too (the audit warns if you forget).
- Use `{{SITE_URL}}` for absolute links to the site itself; the build fills in the URL from `site.config.json`.
- To change the domain, edit `site_url` in `site.config.json`.
