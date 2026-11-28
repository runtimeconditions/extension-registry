"""Build the registry site from registry.json.

It copies the static files in site/, then writes every page from
templates/page.html:

- / (the home page, templates/home.html)
- /extensions/ (every extension as a card)
- /extensions/<namespace>/<name>/ (the latest version)
- /extensions/<namespace>/<name>/<version>/ (every version)
- /404.html

It also writes /search-index.json for the search box. Markdown is rendered
here, with syntax highlighting, so the pages need no JavaScript to read.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from html import escape
from pathlib import Path

from markdown_it import MarkdownIt
from pygments import highlight as pygments_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

REPO_ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = (("go", "Go"), ("python", "Python"), ("typescript", "TypeScript"), ("java", "Java"))
SITE_TITLE = "Runtime Conditions Extension Registry"
DEFAULT_REPOSITORY = "https://github.com/runtimeconditions/extensions"
LEXER_ALIASES = {"condition": "yaml", "ts": "typescript", "sh": "shell", "text": "text"}
ABSOLUTE_LINK = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.I)


class SiteError(Exception):
    """The registry data or a template cannot be used."""


# Markdown


def highlight(code: str, language: str, _attributes: str) -> str:
    """Highlight a fenced code block. Unknown languages stay plain text."""
    name = LEXER_ALIASES.get(language, language)
    if not name:
        return ""
    try:
        lexer = get_lexer_by_name(name)
    except ClassNotFound:
        return ""
    return pygments_highlight(code, lexer, HtmlFormatter(nowrap=True))


MARKDOWN = MarkdownIt("commonmark", {"html": True, "highlight": highlight}).enable("table")


def render_markdown(text: str | None, base_url: str | None = None, heading_offset: int = 0) -> str:
    """Render Markdown from the extensions repository.

    Relative links point at base_url, so README links keep working on this
    site. Headings move down by heading_offset, so a README's h1 does not
    compete with the page title.
    """
    if not text:
        return ""
    tokens = MARKDOWN.parse(text)
    for token in tokens:
        if token.type in ("heading_open", "heading_close") and heading_offset:
            level = min(6, int(token.tag[1]) + heading_offset)
            token.tag = f"h{level}"
        for child in token.children or []:
            for attribute in ("href", "src"):
                value = child.attrGet(attribute)
                if base_url and value and not ABSOLUTE_LINK.match(value):
                    child.attrSet(attribute, base_url + value)
    return MARKDOWN.renderer.render(tokens, MARKDOWN.options, {})


PROFILE_EXAMPLE = re.compile(r"In a Profile, this declares:\n\n```[^\n]*\n.*?\n```\n*", re.S)
KIND_DESCRIPTION = re.compile(r"(`[^`\n]+` declares an? `[^`\n]+` Condition\.\n\n).*?(?=^Example:|^#|\Z)", re.S | re.M)
EXTENSION_LINE = re.compile(r"^Runtime Conditions extension: .*\n*", re.M)


def package_readme(text: str) -> str:
    """Keep only the language-specific parts of a generated package README.

    The extension page already shows the description, the kinds and the YAML
    each example declares, so this drops them: everything before the install
    command, the extension link, each kind's description, and the
    "In a Profile, this declares" YAML after each code example. A README
    with another layout is left as it is.
    """
    fence = re.search(r"^```", text, re.M)
    if fence:
        text = text[fence.start() :]
    text = EXTENSION_LINE.sub("", text)
    text = KIND_DESCRIPTION.sub(r"\1", text)
    return PROFILE_EXAMPLE.sub("", text)


def summary(description: str | None) -> str:
    """The first paragraph of a description, on one line, without Markdown code marks."""
    if not description:
        return ""
    first = description.strip().split("\n\n", 1)[0]
    return " ".join(first.replace("`", "").split())


def markdown_block(text: str | None, heading_offset: int = 0) -> str:
    """Rendered Markdown in a wrapper, or nothing when the text is empty."""
    html = render_markdown(text, heading_offset=heading_offset)
    return f'<div class="md">{html}</div>' if html else ""


def anchor(*parts: str) -> str:
    return "-".join(re.sub(r"[^a-z0-9]+", "-", part.lower()).strip("-") for part in parts if part)


# URLs


def extension_url(extension: dict, version: str | None = None) -> str:
    base = f"/extensions/{extension['slug']}/"
    return f"{base}{version}/" if version else base


def github_url(registry: dict, path: str, kind: str = "tree", ref: str | None = None) -> str:
    """A link into the extensions repository, at ref or at the commit the registry was built from."""
    source = registry.get("source") or {}
    repository = (source.get("repository") or DEFAULT_REPOSITORY).rstrip("/")
    return f"{repository}/{kind}/{ref or source.get('commit') or 'main'}/{path}"


def readme_key(package: dict) -> str | None:
    """Where a package README lives: a path in the checkout, or commit:path in its history."""
    path = package.get("readme")
    if not path:
        return None
    commit = package.get("readmeCommit")
    return f"{commit}:{path}" if commit else path


# Cards and lists


def badges(packages: dict) -> str:
    items = []
    for key, label in LANGUAGES:
        package = packages.get(key)
        if package:
            unpublished = package.get("published") is False
            title = package["coordinate"] + (" (not published yet)" if unpublished else "")
            items.append(
                f'<li class="badge{" unpublished" if unpublished else ""}" title="{escape(title)}">{label} '
                f'<span>{escape(package["version"])}</span></li>'
            )
    return f'<ul class="badges" aria-label="Packages">{"".join(items)}</ul>' if items else ""


def card(extension: dict) -> str:
    latest = extension["versions"][0]
    kinds = "".join(f"<li>{escape(kind['name'])}</li>" for kind in latest["kinds"])
    kinds_html = f'<ul class="kinds" aria-label="Kinds">{kinds}</ul>' if kinds else ""
    description = summary(extension["description"])
    description_html = f"<p>{escape(description)}</p>" if description else ""
    return (
        '<article class="card">'
        '<div class="card-head">'
        f'<h3><a class="card-link" href="{extension_url(extension)}">{escape(extension["slug"])}</a></h3>'
        f'<span class="version">{escape(extension["latestVersion"])}</span>'
        "</div>"
        f"{description_html}"
        f"{kinds_html}"
        f"{badges(latest['packages'])}"
        "</article>"
    )


def render_cards(extensions: list[dict]) -> str:
    return f'<div class="cards">{"".join(card(extension) for extension in extensions)}</div>'


def render_home_list(registry: dict) -> str:
    grouped: dict[str, list[dict]] = {}
    for extension in registry["extensions"]:
        grouped.setdefault(extension["namespace"], []).append(extension)
    items = []
    for namespace, extensions in grouped.items():
        links = ", ".join(
            f'<a href="{extension_url(extension)}">{escape(extension["name"])}</a>' for extension in extensions
        )
        items.append(f"<li>{escape(namespace)}: {links}</li>")
    return "".join(items)


def count_text(count: int) -> str:
    return f"{count} extension" if count == 1 else f"{count} extensions"


# Extension page


def applies_to(kind: str | None, interface_type: str | None) -> str:
    parts = [f"<code>{escape(part)}</code>" for part in (kind, interface_type) if part]
    return " / ".join(parts)


def values_table(field_values: dict) -> str:
    """A table of values and what they mean. Values without any description
    are shown as a plain list, with no empty Description column."""
    caption = f"Allowed values for <code>{escape(field_values['field'])}</code>"
    if not any(item["description"] for item in field_values["values"]):
        chips = "".join(f"<li>{escape(str(item['value']))}</li>" for item in field_values["values"])
        return f'<div class="values-list"><p class="chips-label">{caption}</p><ul class="kinds">{chips}</ul></div>'
    rows = "".join(
        f"<tr><td><code>{escape(str(item['value']))}</code></td>"
        f"<td>{escape(item['description'] or '')}</td></tr>"
        for item in field_values["values"]
    )
    return (
        '<div class="table-wrap"><table class="values">'
        f"<caption>{caption}</caption>"
        "<thead><tr><th>Value</th><th>Description</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def field_block(name: str, meta: str, description: str | None, identifier: str) -> str:
    meta_html = f'<span class="field-meta">{meta}</span>' if meta else ""
    return (
        f'<div class="field" id="{identifier}">'
        f'<div class="field-head"><code class="field-name">{escape(name)}</code>{meta_html}</div>'
        f"{markdown_block(description, heading_offset=3)}"
        "</div>"
    )


def package_panel(registry: dict, extension: dict, language: str, label: str, package: dict, readmes: dict) -> str:
    key = readme_key(package)
    readme = readmes.get(key) if key else None
    ref = package.get("readmeCommit")
    base = github_url(registry, package["sourceDirectory"].rstrip("/") + "/", "blob", ref)
    body = (
        f'<div class="md readme">{render_markdown(package_readme(readme), base_url=base, heading_offset=2)}</div>'
        if readme
        else ""
    )
    unpublished = package.get("published") is False
    status = ' <span class="status">Not published yet</span>' if unpublished else ""
    meta = [
        f"<div><dt>Package</dt><dd><code>{escape(package['coordinate'])}</code></dd></div>",
        f"<div><dt>Version</dt><dd>{escape(package['version'])}{status}</dd></div>",
    ]
    if not unpublished:
        meta.append(
            f'<div><dt>Registry</dt><dd><a href="{escape(package["registryUrl"])}">'
            f'{escape(package["registryUrl"])}</a></dd></div>'
        )
    others = [
        item["version"]
        for item in package.get("versions", [])
        if item["version"] != package["version"] and item["published"] is not False
    ]
    if others:
        meta.append(f"<div><dt>Also from this version</dt><dd>{escape(', '.join(others))}</dd></div>")
    source = github_url(registry, package["sourceDirectory"], "tree", ref)
    meta.append(f'<div><dt>Source</dt><dd><a href="{escape(source)}">{escape(package["sourceDirectory"])}</a></dd></div>')
    note = (
        '<p class="old-version">This package version is generated but not published to its registry yet.</p>'
        if unpublished
        else ""
    )
    return (
        f'<section class="tab-panel" id="package-{language}" role="tabpanel" aria-labelledby="tab-{language}">'
        f'<h3 class="tab-panel-title">{label}</h3>'
        f'<dl class="package-meta">{"".join(meta)}</dl>'
        f"{note}"
        f"{body}"
        "</section>"
    )


def extension_page(registry: dict, extension: dict, version: dict, readmes: dict, by_id: dict) -> tuple[str, list]:
    """The main content of an extension page and its "On this page" sections."""
    sections: list[tuple[str, str, str]] = []
    latest = extension["versions"][0]["version"]

    options = "".join(
        f'<option value="{extension_url(extension, item["version"])}"'
        f'{" selected" if item["version"] == version["version"] else ""}>'
        f'{escape(item["version"])}{" (latest)" if item["version"] == latest else ""}</option>'
        for item in extension["versions"]
    )
    links = [f'<a href="{escape(version["id"])}">Definition</a>']
    if extension.get("readme"):
        links.append(f'<a href="{escape(github_url(registry, extension["readme"], "blob"))}">Catalog README</a>')
    links.append(f'<a href="{escape(github_url(registry, version["definitionPath"], "blob"))}">Source</a>')
    old_note = (
        f'<p class="old-version">This is an older version. The latest is '
        f'<a href="{extension_url(extension)}">{escape(latest)}</a>.</p>'
        if version["version"] != latest
        else ""
    )
    header = (
        '<header class="extension-head">'
        f"<h1>{escape(extension['slug'])}</h1>"
        '<div class="extension-meta">'
        '<label class="version-select">Version '
        f'<select data-version-select aria-label="Version">{options}</select></label>'
        f'<span class="extension-links">{" · ".join(links)}</span>'
        "</div>"
        f"{old_note}"
        "</header>"
    )

    parts = [header]
    description = markdown_block(version.get("description"), heading_offset=1)
    if description:
        parts.append(f'<section id="overview">{description}</section>')
        sections.append(("overview", "Overview", ""))

    shown_values: set[int] = set()
    values = version.get("fieldValues", [])

    def values_for(kind: str | None, interface_type: str | None, prefix: str) -> str:
        tables = []
        for index, item in enumerate(values):
            if index in shown_values:
                continue
            if item.get("kind") == kind and item.get("interfaceType") == interface_type and item["field"].startswith(prefix):
                shown_values.add(index)
                tables.append(values_table(item))
        return "".join(tables)

    if version["kinds"]:
        blocks = []
        for kind in version["kinds"]:
            types = [item for item in version["interfaceTypes"] if item.get("kind") == kind["name"]]
            chips = "".join(
                f'<li><a href="#{anchor("type", kind["name"], item["name"])}">{escape(item["name"])}</a></li>'
                for item in types
            )
            chips_html = f'<p class="chips-label">Interface types</p><ul class="kinds">{chips}</ul>' if chips else ""
            blocks.append(
                f'<div class="kind" id="{anchor("kind", kind["name"])}">'
                f'<h3><code>{escape(kind["name"])}</code></h3>'
                f'{markdown_block(kind.get("description"), heading_offset=3)}'
                f"{chips_html}"
                "</div>"
            )
        parts.append(f'<section id="kinds"><h2>Kinds</h2>{"".join(blocks)}</section>')
        sections.append(("kinds", "Kinds", ""))

    if version["interfaceTypes"]:
        blocks = []
        for item in version["interfaceTypes"]:
            identifier = anchor("type", item.get("kind") or "", item["name"])
            fields = [
                field
                for field in version["interfaceFields"]
                if field.get("kind") == item.get("kind") and field.get("interfaceType") == item["name"]
            ]
            field_html = "".join(
                field_block(
                    f"interface.{field['name']}",
                    "",
                    field.get("description"),
                    anchor("field", item.get("kind") or "", item["name"], field["name"]),
                )
                for field in fields
            )
            blocks.append(
                f'<div class="interface-type" id="{identifier}">'
                f'<h3><code>{escape(item["name"])}</code> <span class="field-meta">{applies_to(item.get("kind"), None)}</span></h3>'
                f'{markdown_block(item.get("description"), heading_offset=3)}'
                f'{f"<h4>Fields</h4>{field_html}" if field_html else ""}'
                f'{values_for(item.get("kind"), item["name"], "interface.")}'
                "</div>"
            )
        parts.append(f'<section id="interface-types"><h2>Interface types</h2>{"".join(blocks)}</section>')
        sections.append(("interface-types", "Interface types", ""))

    if version["conditionFields"]:
        blocks = []
        for field in version["conditionFields"]:
            meta = "Applies to " + ", ".join(
                [f"<code>{escape(kind)}</code>" for kind in field["kinds"]]
                + [f"<code>{escape(item)}</code>" for item in field["interfaceTypes"]]
            ) if field["kinds"] or field["interfaceTypes"] else ""
            tables = []
            for index, item in enumerate(values):
                if index not in shown_values and item["field"].split(".", 1)[0].split("[", 1)[0] == field["name"]:
                    shown_values.add(index)
                    tables.append(values_table(item))
            blocks.append(
                field_block(field["name"], meta, field.get("description"), anchor("condition-field", field["name"]))
                + "".join(tables)
            )
        parts.append(f'<section id="condition-fields"><h2>Condition fields</h2>{"".join(blocks)}</section>')
        sections.append(("condition-fields", "Condition fields", ""))

    remaining = [item for index, item in enumerate(values) if index not in shown_values]
    if remaining:
        parts.append(
            '<section id="allowed-values"><h2>Other allowed values</h2>'
            + "".join(values_table(item) for item in remaining)
            + "</section>"
        )
        sections.append(("allowed-values", "Other allowed values", ""))

    if version["dependencies"]:
        items = []
        for dependency in version["dependencies"]:
            target = by_id.get(dependency)
            if target:
                dep_extension, dep_version = target
                items.append(
                    f'<li><a href="{extension_url(dep_extension, dep_version)}">{escape(dep_extension["slug"])}</a> '
                    f'<span class="version">{escape(dep_version)}</span></li>'
                )
            else:
                items.append(f'<li><a href="{escape(dependency)}">{escape(dependency)}</a></li>')
        parts.append(
            '<section id="dependencies"><h2>Dependencies</h2>'
            "<p>This extension adds to the extensions below. Declare them in the Profile too.</p>"
            f'<ul class="dependencies">{"".join(items)}</ul></section>'
        )
        sections.append(("dependencies", "Dependencies", ""))

    packages = version["packages"]
    if packages:
        tabs = "".join(
            f'<button type="button" class="tab" role="tab" id="tab-{key}" aria-controls="package-{key}" '
            f'aria-selected="{"true" if index == 0 else "false"}">{label}</button>'
            for index, (key, label) in enumerate((key, label) for key, label in LANGUAGES if key in packages)
        )
        panels = "".join(
            package_panel(registry, extension, key, label, packages[key], readmes)
            for key, label in LANGUAGES
            if key in packages
        )
        parts.append(
            '<section id="packages"><h2>Packages</h2>'
            "<p>Packages generated from this version, to write Conditions from your code.</p>"
            f'<div class="tabs" data-tabs><div class="tab-list" role="tablist" aria-label="Package language">{tabs}</div>'
            f"{panels}</div></section>"
        )
        sections.append(("packages", "Packages", ""))

    return "".join(parts), sections


def on_this_page(sections: list) -> str:
    links = "".join(f'<li><a href="#{identifier}">{escape(title)}</a></li>' for identifier, title, _ in sections)
    return f'<nav class="toc" aria-label="On this page"><h2 class="sidebar-title">On this page</h2><ul>{links}</ul></nav>'


# Pages


def fill_template(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            raise SiteError(f"template needs {{{{{name}}}}}, which the build does not provide")
        return values[name]

    return re.sub(r"\{\{([a-z_]+)\}\}", replace, template)


def page(
    template: str,
    *,
    title: str,
    description: str,
    main: str,
    breadcrumb: list[tuple[str, str | None]],
    current: str = "",
    main_class: str = "",
    sidebar: str = "",
) -> str:
    crumbs = "".join(
        '<span aria-hidden="true">›</span>'
        + (f'<a href="{href}">{escape(label)}</a>' if href else f'<span aria-current="page">{escape(label)}</span>')
        for label, href in breadcrumb
    )
    selected = ' aria-current="page"'
    return fill_template(
        template,
        {
            "title": escape(title),
            "description": escape(description),
            "breadcrumb": crumbs,
            "current_home": selected if current == "home" else "",
            "current_extensions": selected if current in ("extensions", "available") else "",
            "current_available": selected if current == "available" else "",
            "sidebar": sidebar,
            "main_class": main_class,
            "main": main,
        },
    )


def search_index(registry: dict) -> list[dict]:
    entries = []
    for extension in registry["extensions"]:
        latest = extension["versions"][0]
        terms = [
            extension["slug"],
            extension["namespace"],
            extension["name"],
            summary(extension["description"]) if extension["description"] else "",
            *(kind["name"] for kind in latest["kinds"]),
            *(item["name"] for item in latest["interfaceTypes"]),
            *(item["name"] for item in latest["interfaceFields"]),
            *(item["name"] for item in latest["conditionFields"]),
            *(package["coordinate"] for package in latest["packages"].values()),
        ]
        entries.append(
            {
                "slug": extension["slug"],
                "name": extension["name"],
                "namespace": extension["namespace"],
                "url": extension_url(extension),
                "terms": " ".join(term for term in terms if term).lower(),
                "card": card(extension),
            }
        )
    return entries


def read_readmes(registry: dict, extensions_root: Path | None) -> dict[str, str]:
    """Package READMEs, from the checkout or, for older packages, from git history."""
    readmes: dict[str, str] = {}
    if extensions_root is None:
        return readmes
    for extension in registry["extensions"]:
        for version in extension["versions"]:
            for package in version["packages"].values():
                key = readme_key(package)
                if not key or key in readmes:
                    continue
                if package.get("readmeCommit"):
                    result = subprocess.run(
                        ["git", "-C", str(extensions_root), "show", key], capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        readmes[key] = result.stdout
                elif (extensions_root / key).is_file():
                    readmes[key] = (extensions_root / key).read_text(encoding="utf-8")
    return readmes


def write(output: Path, relative: str, content: str) -> None:
    path = output / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build(registry: dict, output: Path, extensions_root: Path | None = None, root: Path = REPO_ROOT) -> int:
    """Write the whole site to output. Returns the number of pages."""
    extensions = registry.get("extensions") or []
    if not extensions:
        raise SiteError("registry.json has no extensions")

    template = (root / "templates/page.html").read_text(encoding="utf-8")
    home = (root / "templates/home.html").read_text(encoding="utf-8")
    shutil.copytree(root / "site", output, dirs_exist_ok=True)

    readmes = read_readmes(registry, extensions_root)
    by_id = {
        version["id"]: (extension, version["version"]) for extension in extensions for version in extension["versions"]
    }
    pages = 0

    write(
        output,
        "index.html",
        page(
            template,
            title=SITE_TITLE,
            description="Find Runtime Conditions extensions and the Go, Python, TypeScript and Java packages generated from them.",
            main=fill_template(home, {"home_list": render_home_list(registry)}),
            breadcrumb=[("Home", None)],
            current="home",
        ),
    )
    pages += 1

    listing = (
        "<h1>Available extensions</h1>"
        f'<p class="lead">{count_text(len(extensions))} you can declare in a Profile. Each one lists its kinds '
        "and the packages generated for <strong>Go, Python, TypeScript and Java</strong>.</p>"
        f"{render_cards(extensions)}"
    )
    write(
        output,
        "extensions/index.html",
        page(
            template,
            title=f"Available extensions - {SITE_TITLE}",
            description="Every Runtime Conditions extension, with its kinds and its Go, Python, TypeScript and Java packages.",
            main=listing,
            breadcrumb=[("Available extensions", None)],
            current="available",
            main_class="list",
        ),
    )
    pages += 1

    for extension in extensions:
        for index, version in enumerate(extension["versions"]):
            main, sections = extension_page(registry, extension, version, readmes, by_id)
            description = summary(version.get("description")) or (
                f"The {extension['slug']} Runtime Conditions extension, version {version['version']}."
            )
            crumbs = [
                ("Available extensions", "/extensions/"),
                (extension["slug"], extension_url(extension) if index else None),
            ]
            if index:
                crumbs.append((version["version"], None))
            html = page(
                template,
                title=f"{extension['slug']} {version['version']} - {SITE_TITLE}",
                description=description,
                main=main,
                breadcrumb=crumbs,
                current="extensions",
                main_class="detail",
                sidebar=on_this_page(sections),
            )
            write(output, extension_url(extension, version["version"]).lstrip("/") + "index.html", html)
            pages += 1
            if index == 0:
                write(output, extension_url(extension).lstrip("/") + "index.html", html)
                pages += 1

    write(
        output,
        "404.html",
        page(
            template,
            title=f"Page not found - {SITE_TITLE}",
            description="This page does not exist.",
            main=(
                "<h1>Page not found</h1>"
                '<p class="lead">This page does not exist. Search for an extension, or open '
                '<a href="/extensions/">Available extensions</a>.</p>'
            ),
            breadcrumb=[("Page not found", None)],
        ),
    )
    pages += 1

    write(output, "search-index.json", json.dumps(search_index(registry), ensure_ascii=False, separators=(",", ":")))
    return pages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, required=True, help="registry.json from build_registry.py")
    parser.add_argument("--output", type=Path, required=True, help="directory to write the site to")
    parser.add_argument("--extensions", type=Path, help="extensions checkout, for package READMEs")
    args = parser.parse_args(argv)
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        pages = build(registry, args.output, args.extensions)
    except (OSError, ValueError, KeyError, SiteError) as error:
        print(f"build_site: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {pages} pages to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
