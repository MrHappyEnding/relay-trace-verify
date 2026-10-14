# RTV project demo

Buildless HTML, CSS and JavaScript for the project presentation on GitHub Pages.
The browser replay is an illustrative protocol walkthrough. It does not launch agents,
make model calls, or represent a captured execution transcript. Recorded validation is
presented separately with links and a scoped, curated [summary](data/evidence.json).

## Preview

From the repository root:

```bash
python3 -m http.server 8770 --bind 127.0.0.1 --directory site
```

Open <http://127.0.0.1:8770>. No package install or build is required.

## Publish

Set **Settings → Pages → Source → GitHub Actions**. The `pages.yml` workflow publishes
only `site/` after changes to this directory reach `main`, or via manual dispatch.
Repository code, `work/`, credentials and raw trajectories are outside that artifact.
The website audience is public; repository visibility is managed separately.

## Content and maintenance

- Keep the site English-only. Use relative asset links to support project subpaths.
- Update recorded numbers only against committed acceptance evidence. Preserve each
  run's scope and distinguish graded branches from solved tasks.
- Playback is user-initiated, pauses when the tab is hidden, and has direct step controls.
- Fonts use Google Fonts with local system fallbacks. There are no analytics or model APIs.
- The dark cover, restrained warm accents and reading-oriented structure were visually
  inspired by [cooking-agi](https://github.com/tianyi-zhang-02/cooking-agi). The layout,
  star field, SVG mark and interaction code were created specifically for RTV; no assets
  or implementation were copied from that project.
