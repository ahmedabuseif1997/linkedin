# 1997 Labs analytics setup

The website is instrumented for campaign attribution, consent-aware event tracking, lead-generation events, organic search measurement, and CRM source context.

## Activate provider dashboards

1. Add the Google Analytics 4 measurement ID to `analyticsConfig.ga4` in `site/index.html`.
2. Add the Microsoft Clarity project ID to `analyticsConfig.clarity`.
3. Verify `https://1997labs.com/` in Google Search Console using the DNS-domain method.
4. Submit `https://1997labs.com/sitemap.xml` in Search Console.
5. Replace the email fallback with the selected CRM or form endpoint when available.

## Events already instrumented

- `page_view`
- `contact_nav`
- `email_click`
- `generate_lead`
- Campaign/referrer attribution attached to inquiries

Tracking providers load only after the visitor selects **Allow analytics**. The consent banner stays hidden until at least one ID is set, so no visitor is asked about tracking that does not exist.
