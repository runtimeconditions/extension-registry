"""Build registry.json, the data the registry site shows, from a checkout of
the runtimeconditions/extensions repository.

It finds extension definitions the same way the main site does: release files
under catalog/<namespace>/<name>/releases/<version>/ and definition files
directly under catalog/<namespace>/<name>/. The package catalog
(tooling/extension-bindings/packages.yaml) says which Go, Python, TypeScript
and Java packages are generated from which extension version.

The output has no timestamps, so the same input always gives the same file.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

SCHEMA_VERSION = 1
EXPECTED_HOST = "runtimeconditions.io"
DEFINITION_KIND = "RuntimeConditionsExtensionDefinition"
PACKAGE_CATALOG = Path("tooling/extension-bindings/packages.yaml")
LANGUAGES = ("go", "python", "typescript", "java")
ID_PATH = re.compile(r"^/extensions/([^/]+)/([^/]+)/([^/]+)/runtimeconditions\.extension\.yaml$")
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
KUBE_VERSION = re.compile(r"^v(\d+)(?:(alpha|beta)(\d+))?$")


class RegistryError(Exception):
    """The extensions checkout has data the registry cannot show."""


def read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    return document if isinstance(document, dict) else {}


def find_definitions(root: Path) -> list[Path]:
    catalog = root / "catalog"
    paths = sorted(catalog.glob("*/*/releases/*/runtimeconditions.extension.yaml"))
    paths += sorted(
        path for path in catalog.glob("*/*/*.yaml") if read_yaml(path).get("kind") == DEFINITION_KIND
    )
    return paths


def parse_id(extension_id: str) -> tuple[str, str, str]:
    """Split an extension id URL into namespace, name and version."""
    parsed = urlparse(extension_id)
    match = ID_PATH.match(parsed.path)
    if parsed.scheme != "https" or parsed.netloc != EXPECTED_HOST or not match:
        raise RegistryError(
            f"metadata.id {extension_id!r} is not "
            f"https://{EXPECTED_HOST}/extensions/<namespace>/<name>/<version>/runtimeconditions.extension.yaml"
        )
    return match.group(1), match.group(2), match.group(3)


def version_key(version: str) -> tuple:
    """Sort key so newer versions sort higher. Handles 1.2.3 and v1alpha4 styles."""
    if match := SEMVER.match(version):
        return (2, *(int(part) for part in match.groups()))
    if match := KUBE_VERSION.match(version):
        major, stage, number = match.groups()
        stage_rank = {"alpha": 0, "beta": 1, None: 2}[stage]
        return (1, int(major), stage_rank, int(number or 0))
    return (0, version)


def text(value) -> str | None:
    """Trimmed text, or None when the field is missing or empty."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def summarize_spec(spec: dict) -> dict:
    """Keep what a reader needs. Schemas are left out; the definition URL has them."""
    return {
        "kinds": [
            {"name": kind["name"], "description": text(kind.get("description"))}
            for kind in spec.get("kinds", [])
        ],
        "interfaceTypes": [
            {
                "name": item["name"],
                "kind": item.get("targetKind"),
                "description": text(item.get("description")),
            }
            for item in spec.get("interfaceTypes", [])
        ],
        "interfaceFields": [
            {
                "name": item["name"],
                "kind": item.get("targetKind"),
                "interfaceType": item.get("targetType"),
                "description": text(item.get("description")),
            }
            for item in spec.get("interfaceFields", [])
        ],
        "conditionFields": [
            {
                "name": item["name"],
                "kinds": list(item.get("appliesToKinds", [])),
                "interfaceTypes": list(item.get("appliesToInterfaceTypes", [])),
                "description": text(item.get("description")),
            }
            for item in spec.get("conditionFields", [])
        ],
        "fieldValues": [
            {
                "field": item["field"],
                "kind": item.get("targetKind"),
                "interfaceType": item.get("targetType"),
                "values": [
                    {
                        "value": value,
                        "description": text((item.get("valueDescriptions") or {}).get(value)),
                    }
                    for value in item.get("values", [])
                ],
            }
            for item in spec.get("fieldValues", [])
        ],
        "dependencies": list(spec.get("dependencies", [])),
    }


def registry_url(language: str, coordinate: str, version: str) -> str:
    """Where the published package is listed for this language."""
    if language == "go":
        return f"https://pkg.go.dev/{coordinate}@v{version}"
    if language == "python":
        return f"https://pypi.org/project/{coordinate}/{version}/"
    if language == "typescript":
        return f"https://www.npmjs.com/package/{coordinate}/v/{version}"
    if language == "java":
        group, artifact = coordinate.split(":", 1)
        return f"https://central.sonatype.com/artifact/{group}/{artifact}/{version}"
    raise RegistryError(f"unknown package language {language!r}")


def relative_if_exists(root: Path, path: Path) -> str | None:
    return path.relative_to(root).as_posix() if path.is_file() else None


def load_packages(root: Path) -> dict[str, dict]:
    """Packages from the package catalog, keyed by the extension id they are generated from."""
    catalog_path = root / PACKAGE_CATALOG
    if not catalog_path.is_file():
        raise RegistryError(f"{PACKAGE_CATALOG} is missing")
    catalog = read_yaml(catalog_path)
    by_extension: dict[str, dict] = {}
    for key, package in sorted((catalog.get("packages") or {}).items()):
        extension_id = package.get("rootExtension")
        if not extension_id:
            raise RegistryError(f"{PACKAGE_CATALOG}: package {key!r} has no rootExtension")
        languages = {}
        for language in LANGUAGES:
            target = (package.get("languages") or {}).get(language)
            if not target:
                continue
            coordinate = str(target["coordinate"])
            version = str(target["version"])
            source = root / target["sourceDirectory"]
            languages[language] = {
                "coordinate": coordinate,
                "version": version,
                "sourceDirectory": target["sourceDirectory"],
                "registryUrl": registry_url(language, coordinate, version),
                "readme": relative_if_exists(root, source / "README.md"),
            }
        by_extension[extension_id] = {"key": key, "languages": languages}
    return by_extension


def source_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def build(root: Path, repository: str | None = None) -> dict:
    paths = find_definitions(root)
    if not paths:
        raise RegistryError("no extension definitions found under catalog/")

    packages = load_packages(root)
    extensions: dict[str, dict] = {}
    seen_ids: dict[str, str] = {}
    errors = []

    for path in paths:
        relative = path.relative_to(root).as_posix()
        document = read_yaml(path)
        metadata = document.get("metadata") or {}
        extension_id = str(metadata.get("id", ""))
        try:
            namespace, name, id_version = parse_id(extension_id)
        except RegistryError as error:
            errors.append(f"{relative}: {error}")
            continue
        if extension_id in seen_ids:
            errors.append(f"{relative}: metadata.id is also used by {seen_ids[extension_id]}")
            continue
        seen_ids[extension_id] = relative

        version = str(metadata.get("version") or id_version)
        slug = f"{namespace}/{name}"
        # catalog/<namespace>/<name>/... holds the extension's README for every version.
        directory = root.joinpath(*Path(relative).parts[:3])
        extension = extensions.setdefault(
            slug,
            {
                "slug": slug,
                "namespace": namespace,
                "name": name,
                "readme": relative_if_exists(root, directory / "README.md"),
                "versions": [],
            },
        )
        package = packages.pop(extension_id, None)
        extension["versions"].append(
            {
                "version": version,
                "id": extension_id,
                "description": text(metadata.get("description")),
                "definitionPath": relative,
                **summarize_spec(document.get("spec") or {}),
                "packageKey": package["key"] if package else None,
                "packages": package["languages"] if package else {},
            }
        )

    for extension_id, package in sorted(packages.items()):
        errors.append(
            f"{PACKAGE_CATALOG}: package {package['key']!r} names {extension_id}, which no catalog definition has"
        )
    if errors:
        raise RegistryError("\n".join(errors))

    for extension in extensions.values():
        extension["versions"].sort(key=lambda item: version_key(item["version"]), reverse=True)
        latest = extension["versions"][0]
        extension["latestVersion"] = latest["version"]
        extension["description"] = latest["description"]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": {"repository": repository, "commit": source_commit(root)},
        "extensions": [extensions[slug] for slug in sorted(extensions)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extensions", type=Path, required=True, help="checkout of runtimeconditions/extensions")
    parser.add_argument("--output", type=Path, required=True, help="where to write registry.json")
    parser.add_argument(
        "--repository",
        default="https://github.com/runtimeconditions/extensions",
        help="repository URL recorded in the output",
    )
    args = parser.parse_args(argv)
    try:
        registry = build(args.extensions.resolve(), args.repository)
    except RegistryError as error:
        print(f"build_registry: {error}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    count = sum(len(extension["versions"]) for extension in registry["extensions"])
    print(f"Wrote {len(registry['extensions'])} extensions ({count} versions) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
