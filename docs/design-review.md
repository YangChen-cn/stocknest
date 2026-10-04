# Warm presentation review

The approved warm-white/green direction is implemented with native Streamlit widgets and Gmail-compatible email markup. Both editions share presentation helpers; their existing names remain unchanged.

| Detail | Implementation and review |
| --- | --- |
| Reading order | Portfolio metrics and holdings precede charts; automation is collapsed at the bottom. |
| Typography | System sans-serif body, restrained serif headings, aligned financial figures and clear signs. |
| Palette | Warm-white surfaces, muted green gains, muted red losses; zero/missing values neutral. |
| Tables | Real data and units retained; average cost labelled explicitly; complete columns available in a disclosure. |
| Charts | Price history, allocation and returns use one Plotly style and actual Demo values. |
| Mobile | Primary value followed by paired metrics; charts stack and tables scroll independently. |
| Email | Shared inline-styled shell across all report types; narrative uses escaped user text. |
| Emotional feedback | Verified, date-labelled NAV highs; original closing lines; no celebration in error reports. |

Intentional adaptations from the concept: native navigation and form controls remain, real historical series replace the illustrative curves, and existing report branding and required data-quality disclosures are retained. No image mockup is embedded in the application.

Browser verification used Playwright because the Browser plugin was unavailable. Desktop captures use 1440px width; mobile checks use 390px. Six pages, Demo navigation, optional chart controls and an actual JSON download were checked. Four report modes plus the error report had no horizontal overflow at 390px. No emails were sent. Screenshots contain synthetic data only.

New-high regression checks cover missing/trailing/omitted sessions, stale cutoff dates, and explicitly labelled intraday estimates. Presentation tests verify neutral zero/missing values, honest history counts and unchanged JSON payloads when closing copy changes. Gmail app-specific font substitution or automatic dark-mode recoloring may differ from browser previews.
