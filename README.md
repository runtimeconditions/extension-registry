# extension-registry

A read-only viewer of Runtime Conditions extensions and their documentation,
published at https://extensions.runtimeconditions.io.

The site is the static files in `site/` plus `registry.json`, which
`scripts/build_registry.py` builds from the
[extensions repository](https://github.com/runtimeconditions/extensions).
Every push to `main` builds the data and deploys the site to GitHub Pages with
`.github/workflows/pages.yml`.

## Registry data

`registry.json` lists every extension in the extensions catalog. For each
version it has:

- the id, description and definition path;
- kinds, interface types, fields and allowed values (schemas are left out,
  the definition URL has them);
- dependencies on other extensions;
- the Go, Python, TypeScript and Java packages generated from that version,
  taken from `tooling/extension-bindings/packages.yaml`, with links to each
  package registry and the package README path.

The file has no timestamps, so the same extensions commit always gives the
same output. The commit it was built from is in `source.commit`.

To build it locally, next to a checkout of the extensions repository:

```sh
python3 -m pip install -r requirements.txt
cp -r site _site
python3 scripts/build_registry.py --extensions ../extensions --output _site/registry.json
```

Run the tests with:

```sh
python3 -m unittest discover -s scripts
```

## Preview

```sh
python3 -m http.server --directory _site 8000
```

Then open http://127.0.0.1:8000.
