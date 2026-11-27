# extension-registry

A read-only viewer of Runtime Conditions extensions and their documentation,
published at https://extensions.runtimeconditions.io.

The site is the static files in `site/`. Every push to `main` deploys them to
GitHub Pages with `.github/workflows/pages.yml`.

To preview it locally:

```sh
python3 -m http.server --directory site 8000
```

Then open http://127.0.0.1:8000.
