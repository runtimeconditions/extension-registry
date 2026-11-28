import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import build_registry

PACKAGES = """\
apiVersion: runtimeconditions.io/package-catalog/v1alpha1
kind: RuntimeConditionsPackageCatalog
packages:
  demo-queue:
    rootExtension: https://runtimeconditions.io/extensions/demo/queue/0.10.0/runtimeconditions.extension.yaml
    languages:
      go:
        coordinate: runtimeconditions.io/x/demo/queue
        version: 0.3.0
        sourceDirectory: bindings/demo-queue/go
      java:
        coordinate: io.runtimeconditions.bindings:runtimeconditions-demo-queue
        version: 0.1.0
        sourceDirectory: bindings/demo-queue/java
"""


def definition(namespace, name, version, description=None, spec=""):
    lines = [
        "apiVersion: runtimeconditions.io/v1alpha1",
        "kind: RuntimeConditionsExtensionDefinition",
        "metadata:",
        f"  id: https://runtimeconditions.io/extensions/{namespace}/{name}/{version}/runtimeconditions.extension.yaml",
        f"  version: {version}",
    ]
    if description:
        lines.append(f"  description: {description}")
    lines.append("spec:")
    lines.append(textwrap.indent(textwrap.dedent(spec).strip() or "kinds: []", "  "))
    return "\n".join(lines) + "\n"


QUEUE_SPEC = """
kinds:
- name: demo.queue
  description: Your workload needs a queue.
interfaceTypes:
- name: queue
  targetKind: demo.queue
  description: One queue.
interfaceFields:
- name: operations
  targetKind: demo.queue
  targetType: queue
  description: What the workload calls.
fieldValues:
- field: interface.operations[].name
  targetKind: demo.queue
  targetType: queue
  values: [Send, Receive]
  valueDescriptions:
    Send: Send one message.
schemas:
- id: big-schema
  schema: {type: object}
"""


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write("tooling/extension-bindings/packages.yaml", PACKAGES)
        for version in ("0.9.0", "0.10.0"):
            self.write(
                f"catalog/demo/queue/releases/{version}/runtimeconditions.extension.yaml",
                definition("demo", "queue", version, f"Queue {version}.", QUEUE_SPEC),
            )
        self.write("catalog/demo/queue/README.md", "# Queue\n")
        self.write("bindings/demo-queue/go/README.md", "# Go\n")
        self.write(
            "catalog/rc/config/config-v1alpha2.yaml",
            definition(
                "rc",
                "config",
                "v1alpha2",
                spec="""
                conditionFields:
                - name: configuration
                  appliesToKinds: [demo.queue]
                  appliesToInterfaceTypes: [queue]
                  description: How settings reach the workload.
                dependencies:
                - https://runtimeconditions.io/extensions/demo/queue/0.10.0/runtimeconditions.extension.yaml
                """,
            ),
        )
        self.write("catalog/rc/config/config-v1alpha10.yaml", definition("rc", "config", "v1alpha10"))
        self.write("catalog/rc/config/config-v1.yaml", definition("rc", "config", "v1"))
        self.write("catalog/rc/config/notes.yaml", "kind: SomethingElse\n")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def extension(self, registry, slug):
        return next(item for item in registry["extensions"] if item["slug"] == slug)

    def test_versions_sort_newest_first_and_latest_gives_the_description(self):
        registry = build_registry.build(self.root)
        queue = self.extension(registry, "demo/queue")
        self.assertEqual([item["version"] for item in queue["versions"]], ["0.10.0", "0.9.0"])
        self.assertEqual(queue["latestVersion"], "0.10.0")
        self.assertEqual(queue["description"], "Queue 0.10.0.")
        self.assertEqual(queue["readme"], "catalog/demo/queue/README.md")

    def test_definitions_directly_under_the_extension_folder_are_found(self):
        registry = build_registry.build(self.root)
        self.assertEqual([item["slug"] for item in registry["extensions"]], ["demo/queue", "rc/config"])
        config = self.extension(registry, "rc/config")
        self.assertEqual([item["version"] for item in config["versions"]], ["v1", "v1alpha10", "v1alpha2"])
        self.assertIsNone(config["readme"])
        self.assertIsNone(config["description"])
        oldest = config["versions"][-1]
        self.assertEqual(oldest["conditionFields"][0]["kinds"], ["demo.queue"])
        self.assertEqual(oldest["dependencies"], [self.extension(registry, "demo/queue")["versions"][0]["id"]])

    def test_spec_is_summarized_without_schemas(self):
        version = self.extension(build_registry.build(self.root), "demo/queue")["versions"][0]
        self.assertNotIn("schemas", version)
        self.assertEqual(version["kinds"], [{"name": "demo.queue", "description": "Your workload needs a queue."}])
        self.assertEqual(version["interfaceFields"][0]["interfaceType"], "queue")
        self.assertEqual(
            version["fieldValues"][0]["values"],
            [{"value": "Send", "description": "Send one message."}, {"value": "Receive", "description": None}],
        )

    def test_packages_attach_to_the_version_they_are_generated_from(self):
        queue = self.extension(build_registry.build(self.root), "demo/queue")
        latest, older = queue["versions"]
        self.assertEqual(older["packages"], {})
        self.assertEqual(latest["packageKey"], "demo-queue")
        self.assertEqual(sorted(latest["packages"]), ["go", "java"])
        go = latest["packages"]["go"]
        self.assertEqual(go["registryUrl"], "https://pkg.go.dev/runtimeconditions.io/x/demo/queue@v0.3.0")
        self.assertEqual(go["readme"], "bindings/demo-queue/go/README.md")
        java = latest["packages"]["java"]
        self.assertEqual(
            java["registryUrl"],
            "https://central.sonatype.com/artifact/io.runtimeconditions.bindings/runtimeconditions-demo-queue/0.1.0",
        )
        self.assertIsNone(java["readme"])

    def test_a_package_for_an_unknown_extension_fails(self):
        self.write(
            "tooling/extension-bindings/packages.yaml",
            PACKAGES.replace("queue/0.10.0", "queue/9.9.9"),
        )
        with self.assertRaisesRegex(build_registry.RegistryError, "no catalog definition has"):
            build_registry.build(self.root)

    def test_an_id_outside_runtimeconditions_io_fails(self):
        self.write(
            "catalog/other/thing/releases/0.1.0/runtimeconditions.extension.yaml",
            definition("other", "thing", "0.1.0").replace("runtimeconditions.io/extensions", "example.com/extensions"),
        )
        with self.assertRaisesRegex(build_registry.RegistryError, "catalog/other/thing/releases/0.1.0"):
            build_registry.build(self.root)

    def test_main_writes_the_same_json_every_time(self):
        output = self.root / "out" / "registry.json"
        self.assertEqual(build_registry.main(["--extensions", str(self.root), "--output", str(output)]), 0)
        first = output.read_text(encoding="utf-8")
        self.assertEqual(build_registry.main(["--extensions", str(self.root), "--output", str(output)]), 0)
        self.assertEqual(output.read_text(encoding="utf-8"), first)
        registry = json.loads(first)
        self.assertEqual(registry["schemaVersion"], 1)
        self.assertEqual(registry["source"]["repository"], "https://github.com/runtimeconditions/extensions")


def manifest(extension_id, version):
    return (
        "kind: RuntimeConditionsBindingManifest\n"
        f"extension:\n  id: {extension_id}\n"
        f"package:\n  version: {version}\n"
    )


QUEUE = "https://runtimeconditions.io/extensions/demo/queue/{}/runtimeconditions.extension.yaml"


class HistoryTest(unittest.TestCase):
    """Older extension versions get the packages generated from them, read from git."""

    write = RegistryTest.write
    extension = RegistryTest.extension
    tearDown = RegistryTest.tearDown

    def git(self, *args):
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
        )

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def setUp(self):
        RegistryTest.setUp(self)
        go = "bindings/demo-queue/go/runtimeconditions.bindings.yaml"
        java = "bindings/demo-queue/java/src/runtimeconditions.bindings.yaml"
        self.git("init", "-q")
        self.write(go, manifest(QUEUE.format("0.9.0"), "v0.2.0"))
        self.commit("go 0.2.0 from 0.9.0")
        self.git("tag", "bindings/demo-queue/go/v0.2.0")
        # The same version regenerated later is not what the tag published.
        self.write(go, manifest(QUEUE.format("0.10.0"), "v0.2.0"))
        self.commit("go 0.2.0 regenerated")
        self.write(go, manifest(QUEUE.format("0.10.0"), "v0.3.0"))
        self.write(java, manifest("https://runtimeconditions.io/demo-queue:0.10.0", "0.1.0"))
        self.commit("go 0.3.0 and java 0.1.0")

    def fetch(self, url):
        if "maven" in url:
            return 200, "<metadata><versioning><versions><version>0.1.0</version></versions></versioning></metadata>"
        return None

    def test_tags_decide_go_and_older_versions_get_their_packages(self):
        registry = build_registry.build(self.root, check_registries=True, fetch=self.fetch)
        latest, older = self.extension(registry, "demo/queue")["versions"]
        old_go = older["packages"]["go"]
        self.assertEqual((old_go["version"], old_go["published"]), ("0.2.0", True))
        self.assertEqual(old_go["readmeCommit"], "bindings/demo-queue/go/v0.2.0")
        self.assertEqual(old_go["readme"], "bindings/demo-queue/go/README.md")
        go = latest["packages"]["go"]
        self.assertEqual((go["version"], go["published"]), ("0.3.0", False))
        self.assertEqual(go["versions"], [{"version": "0.3.0", "published": False}])
        self.assertIsNone(go["readmeCommit"])

    def test_early_ids_map_to_the_catalog_and_registries_set_published(self):
        registry = build_registry.build(self.root, check_registries=True, fetch=self.fetch)
        latest = self.extension(registry, "demo/queue")["versions"][0]
        self.assertEqual(latest["packages"]["java"]["published"], True)

    def test_without_registry_checks_published_is_unknown(self):
        registry = build_registry.build(self.root)
        java = self.extension(registry, "demo/queue")["versions"][0]["packages"]["java"]
        self.assertIsNone(java["published"])

    def test_registry_answers(self):
        self.assertEqual(build_registry.registry_versions("python", "x", lambda url: (404, "")), set())
        self.assertIsNone(build_registry.registry_versions("python", "x", lambda url: (500, "")))
        self.assertEqual(
            build_registry.registry_versions("typescript", "@scope/x", lambda url: (200, '{"versions": {"1.0.0": {}}}')),
            {"1.0.0"},
        )
        seen = []
        build_registry.registry_versions("typescript", "@scope/x", lambda url: seen.append(url))
        self.assertEqual(seen, ["https://registry.npmjs.org/@scope%2fx"])


if __name__ == "__main__":
    unittest.main()
