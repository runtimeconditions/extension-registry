import json
import tempfile
import unittest
from pathlib import Path

import build_site


def package(coordinate, version, readme=None, directory="bindings/demo/go"):
    return {
        "coordinate": coordinate,
        "version": version,
        "sourceDirectory": directory,
        "registryUrl": f"https://example.test/{coordinate}/{version}",
        "readme": readme,
    }


def version(number, extension_id, description=None, **spec):
    return {
        "version": number,
        "id": extension_id,
        "description": description,
        "definitionPath": f"catalog/demo/{number}.yaml",
        "kinds": spec.get("kinds", []),
        "interfaceTypes": spec.get("interfaceTypes", []),
        "interfaceFields": spec.get("interfaceFields", []),
        "conditionFields": spec.get("conditionFields", []),
        "fieldValues": spec.get("fieldValues", []),
        "dependencies": spec.get("dependencies", []),
        "packages": spec.get("packages", {}),
    }


QUEUE_ID = "https://runtimeconditions.io/extensions/aws/sqs/0.2.0/runtimeconditions.extension.yaml"
OLD_QUEUE_ID = QUEUE_ID.replace("0.2.0", "0.1.0")
CONFIG_ID = "https://runtimeconditions.io/extensions/rc/config/v1alpha1/runtimeconditions.extension.yaml"

REGISTRY = {
    "schemaVersion": 1,
    "source": {"repository": "https://github.com/runtimeconditions/extensions", "commit": "abc123"},
    "extensions": [
        {
            "slug": "aws/sqs",
            "namespace": "aws",
            "name": "sqs",
            "readme": "catalog/aws/sqs/README.md",
            "latestVersion": "0.2.0",
            "description": "Use this when your workload uses <SQS>.\nIt sends `messages`.\n\nMore detail here.",
            "versions": [
                version(
                    "0.2.0",
                    QUEUE_ID,
                    "Use this when your workload uses <SQS>.\nIt sends `messages`.\n\nMore detail here.",
                    kinds=[{"name": "aws.sqs", "description": "Needs SQS.\n\n```condition\nkind: aws.sqs\n```"}],
                    interfaceTypes=[{"name": "queue", "kind": "aws.sqs", "description": "One queue."}],
                    interfaceFields=[
                        {"name": "operations", "kind": "aws.sqs", "interfaceType": "queue", "description": "Calls."}
                    ],
                    fieldValues=[
                        {
                            "field": "interface.operations[].name",
                            "kind": "aws.sqs",
                            "interfaceType": "queue",
                            "values": [{"value": "SendMessage", "description": "Send one message."}],
                        }
                    ],
                    packages={
                        "typescript": package("@runtimeconditions/aws-sqs", "0.2.0", directory="bindings/aws-sqs/typescript"),
                        "go": package(
                            "runtimeconditions.io/x/aws/sqs",
                            "0.2.0",
                            readme="bindings/aws-sqs/go/README.md",
                            directory="bindings/aws-sqs/go",
                        ),
                    },
                ),
                version("0.1.0", OLD_QUEUE_ID, "Old."),
            ],
        },
        {
            "slug": "rc/config",
            "namespace": "rc",
            "name": "config",
            "readme": None,
            "latestVersion": "v1alpha1",
            "description": None,
            "versions": [
                version(
                    "v1alpha1",
                    CONFIG_ID,
                    conditionFields=[
                        {"name": "configuration", "kinds": ["api"], "interfaceTypes": [], "description": "Env vars."}
                    ],
                    fieldValues=[
                        {
                            "field": "configuration.env[].property",
                            "kind": None,
                            "interfaceType": None,
                            "values": [{"value": "url", "description": None}],
                        },
                        {"field": "other", "kind": None, "interfaceType": None, "values": [{"value": "x", "description": None}]},
                    ],
                    dependencies=[QUEUE_ID, "https://example.test/unknown.yaml"],
                )
            ],
        },
    ],
}

README = """<!-- Code generated. -->

# runtimeconditions.io/x/aws/sqs

```go
package main
```

See [the guide](docs/guide.md) and [the spec](https://runtimeconditions.io/spec.html).
"""


class MarkdownTest(unittest.TestCase):
    def test_relative_links_point_at_the_base_and_headings_move_down(self):
        html = build_site.render_markdown(README, base_url="https://github.test/blob/abc/dir/", heading_offset=2)
        self.assertIn('href="https://github.test/blob/abc/dir/docs/guide.md"', html)
        self.assertIn('href="https://runtimeconditions.io/spec.html"', html)
        self.assertIn("<h3>runtimeconditions.io/x/aws/sqs</h3>", html)

    def test_code_is_highlighted_and_unknown_languages_stay_plain(self):
        html = build_site.render_markdown("```go\npackage main\n```\n\n```nope\n<b>x</b>\n```")
        self.assertIn('<span class="kn">package</span>', html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertIn('<span class="nt">kind</span>', build_site.render_markdown("```condition\nkind: x\n```"))

    def test_summary_is_the_first_paragraph_on_one_line(self):
        self.assertEqual(build_site.summary("First `line`\nsecond line.\n\nSecond paragraph."), "First line second line.")
        self.assertEqual(build_site.summary(None), "")


GENERATED_README = """<!-- Code generated. -->

# runtimeconditions.io/x/demo

The extension description.

```sh
go get runtimeconditions.io/x/demo@v0.1.0
```

Runtime Conditions extension: https://runtimeconditions.io/extensions/demo/0.1.0/runtimeconditions.extension.yaml

## Queue

`demo.Queue` declares a `queue` Condition.

The kind description.

Example: one queue.

```go
demo.Queue()
```

In a Profile, this declares:

```yaml
kind: queue
```

## Next
"""


class PackageReadmeTest(unittest.TestCase):
    def test_parts_the_page_already_shows_are_dropped(self):
        text = build_site.package_readme(GENERATED_README)
        self.assertTrue(text.startswith("```sh\ngo get"))
        for dropped in ("The extension description.", "Runtime Conditions extension:", "The kind description.", "In a Profile", "kind: queue"):
            self.assertNotIn(dropped, text)
        for kept in ("`demo.Queue` declares a `queue` Condition.", "Example: one queue.", "demo.Queue()", "## Next"):
            self.assertIn(kept, text)

    def test_other_layouts_are_left_alone(self):
        self.assertEqual(build_site.package_readme("Just text.\n"), "Just text.\n")


class ValuesTest(unittest.TestCase):
    def test_values_with_descriptions_get_a_table(self):
        html = build_site.values_table(
            {"field": "f", "values": [{"value": "a", "description": "A."}, {"value": "b", "description": None}]}
        )
        self.assertIn("<th>Description</th>", html)
        self.assertIn("<td></td>", html)

    def test_values_without_descriptions_are_a_plain_list(self):
        html = build_site.values_table({"field": "f", "values": [{"value": "a", "description": None}]})
        self.assertNotIn("<table", html)
        self.assertIn("<li>a</li>", html)


class PageTest(unittest.TestCase):
    def setUp(self):
        self.by_id = {
            item["id"]: (extension, item["version"])
            for extension in REGISTRY["extensions"]
            for item in extension["versions"]
        }

    def page(self, slug, index=0, readmes=None):
        extension = next(item for item in REGISTRY["extensions"] if item["slug"] == slug)
        return build_site.extension_page(REGISTRY, extension, extension["versions"][index], readmes or {}, self.by_id)

    def test_card_escapes_text_links_to_the_page_and_orders_badges(self):
        html = build_site.card(REGISTRY["extensions"][0])
        self.assertIn('href="/extensions/aws/sqs/"', html)
        self.assertIn("&lt;SQS&gt;", html)
        self.assertNotIn("More detail here", html)
        self.assertLess(html.index(">Go "), html.index(">TypeScript "))
        self.assertNotIn("<p>", build_site.card(REGISTRY["extensions"][1]))

    def test_extension_page_shows_kinds_fields_values_and_packages(self):
        html, sections = self.page("aws/sqs", readmes={"bindings/aws-sqs/go/README.md": README})
        self.assertEqual(
            [identifier for identifier, _, _ in sections], ["overview", "kinds", "interface-types", "packages"]
        )
        self.assertIn('<option value="/extensions/aws/sqs/0.2.0/" selected>0.2.0 (latest)</option>', html)
        self.assertIn("<code>interface.operations[].name</code>", html)
        self.assertIn("Send one message.", html)
        self.assertIn('id="tab-go"', html)
        self.assertIn('href="https://github.com/runtimeconditions/extensions/blob/abc123/bindings/aws-sqs/go/docs/guide.md"', html)
        typescript = html[html.index('id="package-typescript"') :]
        self.assertNotIn('class="md readme"', typescript)
        self.assertNotIn("older version", html)

    def test_older_version_points_to_the_latest(self):
        html, _ = self.page("aws/sqs", index=1)
        self.assertIn("This is an older version", html)
        self.assertNotIn('id="packages"', html)

    def test_condition_fields_get_their_values_and_the_rest_are_listed(self):
        html, sections = self.page("rc/config")
        self.assertEqual(
            [identifier for identifier, _, _ in sections],
            ["condition-fields", "allowed-values", "dependencies"],
        )
        self.assertNotIn('id="overview"', html)
        condition = html[html.index('id="condition-fields"') : html.index('id="allowed-values"')]
        self.assertIn("configuration.env[].property", condition)
        self.assertNotIn("<code>other</code>", condition)
        self.assertIn('<a href="/extensions/aws/sqs/0.2.0/">aws/sqs</a>', html)
        self.assertIn('<a href="https://example.test/unknown.yaml">', html)

    def test_search_index_has_terms_and_cards(self):
        entries = build_site.search_index(REGISTRY)
        sqs = entries[0]
        self.assertEqual(sqs["url"], "/extensions/aws/sqs/")
        for term in ("aws/sqs", "aws.sqs", "queue", "operations", "@runtimeconditions/aws-sqs"):
            self.assertIn(term, sqs["terms"])
        self.assertIn('class="card"', sqs["card"])

    def test_unpublished_packages_are_marked_and_have_no_registry_link(self):
        unpublished = dict(
            package("runtimeconditions.io/x/demo", "0.3.0"),
            published=False,
            versions=[{"version": "0.3.0", "published": False}, {"version": "0.2.0", "published": True}],
        )
        self.assertIn('class="badge unpublished"', build_site.badges({"go": unpublished}))
        html = build_site.package_panel(REGISTRY, {}, "go", "Go", unpublished, {})
        self.assertIn("Not published yet", html)
        self.assertNotIn("<dt>Registry</dt>", html)
        self.assertIn("<dt>Also from this version</dt><dd>0.2.0</dd>", html)

    def test_older_readmes_link_to_their_commit(self):
        old = dict(
            package("runtimeconditions.io/x/demo", "0.2.0", readme="bindings/demo/go/README.md"),
            published=True,
            readmeCommit="bindings/demo/go/v0.2.0",
        )
        readmes = {"bindings/demo/go/v0.2.0:bindings/demo/go/README.md": "```sh\ngo get x\n```\n\n[guide](g.md)\n"}
        html = build_site.package_panel(REGISTRY, {}, "go", "Go", old, readmes)
        self.assertIn("/blob/bindings/demo/go/v0.2.0/bindings/demo/go/g.md", html)
        self.assertIn("<dt>Registry</dt>", html)

    def test_template_placeholder_without_a_value_fails(self):
        with self.assertRaisesRegex(build_site.SiteError, "missing"):
            build_site.fill_template("{{missing}}", {})


class BuildTest(unittest.TestCase):
    def test_build_writes_every_page_and_the_search_index(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            extensions = temp / "extensions"
            (extensions / "bindings/aws-sqs/go").mkdir(parents=True)
            (extensions / "bindings/aws-sqs/go/README.md").write_text(README, encoding="utf-8")
            registry_path = temp / "registry.json"
            registry_path.write_text(json.dumps(REGISTRY), encoding="utf-8")
            output = temp / "site"
            code = build_site.main(
                ["--registry", str(registry_path), "--output", str(output), "--extensions", str(extensions)]
            )
            self.assertEqual(code, 0)
            for relative in (
                "index.html",
                "404.html",
                "extensions/index.html",
                "extensions/aws/sqs/index.html",
                "extensions/aws/sqs/0.2.0/index.html",
                "extensions/aws/sqs/0.1.0/index.html",
                "extensions/rc/config/index.html",
                "assets/styles.css",
                "assets/registry.js",
                "assets/theme.js",
            ):
                self.assertTrue((output / relative).is_file(), relative)
            latest = (output / "extensions/aws/sqs/index.html").read_text(encoding="utf-8")
            self.assertIn('<span class="kn">package</span>', latest)
            self.assertNotIn("{{", latest)
            listing = (output / "extensions/index.html").read_text(encoding="utf-8")
            self.assertIn("2 extensions", listing)
            self.assertIn('href="/extensions/" aria-current="page"', listing)
            home = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('<a href="/extensions/aws/sqs/">sqs</a>', home)
            index = json.loads((output / "search-index.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["slug"] for entry in index], ["aws/sqs", "rc/config"])

    def test_empty_registry_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            registry_path = Path(temp) / "registry.json"
            registry_path.write_text('{"extensions": []}', encoding="utf-8")
            self.assertEqual(build_site.main(["--registry", str(registry_path), "--output", str(Path(temp) / "out")]), 1)


if __name__ == "__main__":
    unittest.main()
