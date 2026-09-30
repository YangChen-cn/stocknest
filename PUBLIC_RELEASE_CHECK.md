# StockNest public release checks

This candidate is a clean source distribution, not a publication of the personal repository. Publication is authorized; this clean candidate is prepared for a new public repository.

## Result

- A new repository with one root commit, no inherited parents and no private remote. Commit identity is generic. Draft reflogs/unreachable objects are removed before packaging.
- Tracked source and reachable history scanned for private paths, known personal identifiers, common credential formats, private keys and local macOS paths. No matches remain.
- No tracked personal configuration, transactions, alert state, performance history, environment files, SMTP credentials, logs, reports or caches.
- Local configuration and all `data/` files are ignored by default. Public daily workflows are gated off; personal automation requires a private copy. Git sync verifies GitHub repository privacy.
- Demo symbols, prices, trades and notes are fictional. Screenshots were captured from Demo mode and manually reviewed. Ordinary company-name search aliases and mock test credentials are functional examples, not personal configuration.
- Public and private editions share identical core code, tests, dependency manifests and workflows. Only release documentation/screenshots, the public branding entry point and personal inputs differ.

## Verification

| Check | Result |
| --- | --- |
| Offline regression suite | 150 tests passed in each edition |
| Full dependency environment | `pip check` passed |
| Runtime-only environment | `pip check` and offline daily preview passed without Streamlit, Plotly or pytest |
| Desktop browser | Demo dashboard, refresh, performance and watchlist interactions passed at 1440 × 1000 |
| Mobile browser | Demo performance page/drawer checked at 390 × 844 |
| Browser errors | None observed |
| Current files and Git history | Automated scan passed; synthetic inputs and screenshots manually reviewed |
| Archive contents | Generated from tracked Git source only; no personal files or `.git` |

Browser testing used regular Playwright because the Browser plugin was unavailable. Screenshots are in `docs/images/`.

## Important publication boundary

The original private repository still has personal configuration and transaction/state files in its history. **Do not change that repository to public**, or copy its `.git` directory into this candidate. Publish only this clean source distribution or the new root repository.

The scanner is a heuristic, not a security guarantee. Before a later publication, rerun `python tools/check_public_release.py`, inspect newly added files/screenshots, and verify repository visibility and Git history. Do not upload private workflow artifacts. After making a private personal copy, keep personal commits there and merge shared source updates from the public upstream.
