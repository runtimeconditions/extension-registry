# extension-registry

A read-only viewer of Runtime Conditions extensions and their documentation,
published at https://extensions.runtimeconditions.io.

Every page is built from the
[extensions repository](https://github.com/runtimeconditions/extensions):

1. `scripts/build_registry.py` reads the extensions catalog and the package
   catalog, and writes `registry.json`.
2. `scripts/build_site.py` copies the static files in `site/` and writes every
   page from `templates/page.html`, plus `search-index.json`.

`.github/workflows/pages.yml` runs both and deploys the result to GitHub
Pages on every push to `main`, and when the extensions repository sends an
`extensions-updated` dispatch.

## Pages

| Path | Shows |
| --- | --- |
| `/` | The home page, from `templates/home.html` |
| `/extensions/` | Every extension as a card |
| `/extensions/<namespace>/<name>/` | The latest version of an extension |
| `/extensions/<namespace>/<name>/<version>/` | One version of an extension |
| `/404.html` | Page not found |

An extension page has the description, the kinds, the interface types with
their fields and allowed values, the Condition fields, the dependencies, and a
tab for each package language with the package README. Markdown and code
highlighting are rendered at build time, so pages read fine without
JavaScript.

`site/assets/theme.js` adds a light and dark toggle to the header, like the one
on runtimeconditions.io; pages follow the system until the reader picks one.
`site/assets/registry.js` adds the search box, the package tabs, copy buttons
and the version chooser. Search looks at names, kinds, interface types, field
names and package names. More than one match shows cards; exactly one match
shows that extension's details. Press `/` to search, and Enter to open the
first result. `?q=` in the URL starts a search.

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

Older extension versions get the packages generated from them too. Every
generated package has a `runtimeconditions.bindings.yaml` that names its
package version and extension id, so the script reads that file through the
git history of the extensions checkout. Go release tags decide Go versions,
because a tag holds exactly what was published. This needs a full clone with
tags; a shallow clone only gets today's packages.

With `--check-registries` the script asks PyPI, npm and Maven Central which
versions exist. Each language then shows the newest published version, and a
generated version that is not published yet is marked as such. Go is always
checked against its tags.

The file has no timestamps, so the same extensions commit and registry
answers always give the same output. The commit it was built from is in
`source.commit`.

## Build locally

Next to a checkout of the extensions repository:

```sh
python3 -m pip install -r requirements.txt
python3 scripts/build_registry.py --extensions ../extensions --output _site/registry.json --check-registries
python3 scripts/build_site.py --registry _site/registry.json --extensions ../extensions --output _site
python3 -m http.server --directory _site 8000
```

Then open http://127.0.0.1:8000. Links start with `/`, so serve `_site` as
the site root.

Run the tests with:

```sh
python3 -m unittest discover -s scripts
```
