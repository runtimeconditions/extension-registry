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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import yaml

SCHEMA_VERSION = 1
EXPECTED_HOST = "runtimeconditions.io"
DEFINITION_KIND = "RuntimeConditionsExtensionDefinition"
PACKAGE_CATALOG = Path("tooling/extension-bindings/packages.yaml")
LANGUAGES = ("go", "python", "typescript", "java")
BINDING_MANIFEST = "runtimeconditions.bindings.yaml"
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


def load_targets(root: Path) -> list[dict]:
    """Every language target in the package catalog."""
    catalog_path = root / PACKAGE_CATALOG
    if not catalog_path.is_file():
        raise RegistryError(f"{PACKAGE_CATALOG} is missing")
    catalog = read_yaml(catalog_path)
    targets = []
    for key, package in sorted((catalog.get("packages") or {}).items()):
        extension_id = package.get("rootExtension")
        if not extension_id:
            raise RegistryError(f"{PACKAGE_CATALOG}: package {key!r} has no rootExtension")
        for language in LANGUAGES:
            target = (package.get("languages") or {}).get(language)
            if target:
                targets.append(
                    {
                        "key": key,
                        "language": language,
                        "coordinate": str(target["coordinate"]),
                        "version": str(target["version"]),
                        "sourceDirectory": str(target["sourceDirectory"]).rstrip("/"),
                        "rootExtension": extension_id,
                    }
                )
    return targets


# Package history: which package versions were generated from which extension version.


def git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def has_history(root: Path) -> bool:
    """True for a full git checkout. A shallow clone has no usable history."""
    return (git(root, "rev-parse", "--is-shallow-repository") or "").strip() == "false"


def read_blobs(root: Path, specs: list[str]) -> dict[str, str | None]:
    """Read many commit:path blobs with one git process. Missing ones are None."""
    if not specs:
        return {}
    process = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch"],
        input="".join(f"{spec}\n" for spec in specs).encode(),
        capture_output=True,
        check=True,
    )
    data = process.stdout
    blobs: dict[str, str | None] = {}
    position = 0
    for spec in specs:
        newline = data.index(b"\n", position)
        header = data[position:newline].decode()
        position = newline + 1
        if header.endswith(" missing") or header.endswith(" ambiguous"):
            blobs[spec] = None
            continue
        size = int(header.rsplit(" ", 1)[1])
        blobs[spec] = data[position : position + size].decode("utf-8", errors="replace")
        position += size + 1
    return blobs


LEGACY_ID = re.compile(r"^https://runtimeconditions\.io/(?:([^/:]+)/)?([^/:]+):([^/:]+)$")


def canonical_id(extension_id: str, catalog_ids: set[str]) -> str:
    """Map an early id such as https://runtimeconditions.io/common-integrations:v1alpha1
    to the catalog id of the same extension and version."""
    match = LEGACY_ID.match(extension_id)
    if extension_id in catalog_ids or not match:
        return extension_id
    _, name, version = match.groups()
    for candidate in sorted(catalog_ids):
        try:
            namespace, catalog_name, catalog_version = parse_id(candidate)
        except RegistryError:
            continue  # build() reports bad ids with their file
        if catalog_version == version and name in (catalog_name, f"{namespace}-{catalog_name}"):
            return candidate
    return extension_id


def normalize_version(version) -> str:
    """Go manifests write v0.2.0; registries and the package catalog write 0.2.0."""
    return str(version or "").removeprefix("v")


def package_history(
    root: Path, targets: list[dict], catalog_ids: set[str], tags: set[str] | None = None
) -> dict[tuple[str, str], list[dict]]:
    """Package versions and the extension version each was generated from.

    Every generated package carries runtimeconditions.bindings.yaml, which
    names the package version and the extension id. A Go release tag holds
    exactly what was published, so tags decide Go versions. Otherwise the
    git history decides, and for each version the newest commit wins.
    """
    by_directory = {target["sourceDirectory"]: target for target in targets}
    log = git(
        root,
        "log",
        "--format=%x00%H",
        "--name-only",
        "--",
        f":(glob)bindings/**/{BINDING_MANIFEST}",
    )
    if not log:
        return {}
    changes: list[tuple[str, str, dict]] = []
    commit = ""
    for line in log.splitlines():
        if line.startswith("\0"):
            commit = line[1:]
        elif line:
            target = next(
                (target for directory, target in by_directory.items() if line.startswith(directory + "/")), None
            )
            if target:
                changes.append((commit, line, target))

    # Go tags first: the tagged files are what was published.
    tagged: list[tuple[str, str, dict]] = []
    for tag in sorted(tags or ()):
        for directory, target in by_directory.items():
            if target["language"] == "go" and tag.startswith(f"{directory}/v"):
                path = next(
                    (path for commit, path, item in changes if item is target),
                    f"{directory}/{BINDING_MANIFEST}",
                )
                tagged.append((tag, path, target))
    changes = tagged + changes

    manifests = read_blobs(root, [f"{commit}:{path}" for commit, path, _ in changes])
    readmes = read_blobs(root, sorted({f"{commit}:{target['sourceDirectory']}/README.md" for commit, _, target in changes}))
    history: dict[tuple[str, str], dict[str, dict]] = {}
    for commit, path, target in changes:
        content = manifests.get(f"{commit}:{path}")
        if not content:
            continue
        try:
            manifest = yaml.safe_load(content) or {}
        except yaml.YAMLError:
            continue
        version = normalize_version((manifest.get("package") or {}).get("version"))
        extension_id = canonical_id(str((manifest.get("extension") or {}).get("id") or ""), catalog_ids)
        if not version or not extension_id:
            continue
        found = history.setdefault((target["key"], target["language"]), {})
        if version not in found:
            readme = f"{target['sourceDirectory']}/README.md"
            found[version] = {
                "version": version,
                "extensionId": extension_id,
                # A tag name works wherever a commit does.
                "commit": commit,
                "readme": readme if readmes.get(f"{commit}:{readme}") is not None else None,
            }
    return {key: list(found.values()) for key, found in history.items()}


# Publication: which package versions a registry actually has.


def fetch_url(url: str) -> tuple[int, str] | None:
    """GET a URL. Returns the status and body, or None when the request fails."""
    request = urllib.request.Request(url, headers={"User-Agent": "runtimeconditions-extension-registry"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def registry_versions(language: str, coordinate: str, fetch=fetch_url) -> set[str] | None:
    """Versions a public registry lists for a package, or None when it cannot be asked."""
    if language == "python":
        url = f"https://pypi.org/pypi/{coordinate}/json"
    elif language == "typescript":
        url = "https://registry.npmjs.org/" + coordinate.replace("/", "%2f")
    elif language == "java":
        group, artifact = coordinate.split(":", 1)
        url = f"https://repo1.maven.org/maven2/{group.replace('.', '/')}/{artifact}/maven-metadata.xml"
    else:
        return None
    response = fetch(url)
    if response is None:
        return None
    status, body = response
    if status == 404:
        return set()
    if status != 200:
        return None
    try:
        if language == "python":
            return set(json.loads(body).get("releases", {}))
        if language == "typescript":
            return set(json.loads(body).get("versions", {}))
        return set(re.findall(r"<version>([^<]+)</version>", body))
    except ValueError:
        return None


def go_versions(tags: set[str] | None, source_directory: str) -> set[str] | None:
    """Go modules are published by tags such as bindings/aws-s3/go/v0.2.0."""
    if tags is None:
        return None
    prefix = f"{source_directory}/v"
    return {tag[len(prefix) :] for tag in tags if tag.startswith(prefix)}


def load_packages(
    root: Path,
    catalog_ids: set[str],
    *,
    history: bool = True,
    check_registries: bool = False,
    fetch=fetch_url,
) -> tuple[dict[str, dict], set[str]]:
    """Packages keyed by the extension id they were generated from.

    The package catalog gives today's packages. With history, older extension
    versions get the packages generated from them too. Each language shows
    the newest published version, or the newest generated one when none is
    published; "published" is True, False, or None when it is not known.
    Also returns the extension ids the package catalog names today.
    """
    targets = load_targets(root)
    tag_output = git(root, "tag", "--list", "bindings/*") if history else None
    tags = set(tag_output.split()) if tag_output else None
    found = package_history(root, targets, catalog_ids, tags) if history else {}

    # Registries are asked in parallel; each answer is a set of versions or None.
    published_by_target: dict[int, set[str] | None] = {}
    if check_registries:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                index: pool.submit(registry_versions, target["language"], target["coordinate"], fetch)
                for index, target in enumerate(targets)
                if target["language"] != "go"
            }
            published_by_target = {index: future.result() for index, future in futures.items()}

    by_extension: dict[str, dict] = {}
    for index, target in enumerate(targets):
        key, language = target["key"], target["language"]
        candidates: dict[str, dict[str, dict]] = {}
        for item in found.get((key, language), []):
            candidates.setdefault(item["extensionId"], {})[item["version"]] = item
        current = {
            "version": target["version"],
            "extensionId": target["rootExtension"],
            "commit": None,
            "readme": relative_if_exists(root, root / target["sourceDirectory"] / "README.md"),
        }
        candidates.setdefault(target["rootExtension"], {})[target["version"]] = current

        if language == "go":
            published = go_versions(tags, target["sourceDirectory"])
        else:
            published = published_by_target.get(index)

        for extension_id, versions in candidates.items():
            ordered = sorted(versions.values(), key=lambda item: version_key(item["version"]), reverse=True)
            flags = [None if published is None else item["version"] in published for item in ordered]
            chosen_index = next((index for index, flag in enumerate(flags) if flag), 0)
            chosen = ordered[chosen_index]
            entry = by_extension.setdefault(extension_id, {"key": key, "languages": {}})
            entry["languages"][language] = {
                "coordinate": target["coordinate"],
                "version": chosen["version"],
                "published": flags[chosen_index],
                "versions": [
                    {"version": item["version"], "published": flag} for item, flag in zip(ordered, flags)
                ],
                "sourceDirectory": target["sourceDirectory"],
                "registryUrl": registry_url(language, target["coordinate"], chosen["version"]),
                "readme": chosen["readme"],
                "readmeCommit": chosen["commit"],
            }
    return by_extension, {target["rootExtension"] for target in targets}


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


def build(
    root: Path,
    repository: str | None = None,
    *,
    history: bool | None = None,
    check_registries: bool = False,
    fetch=fetch_url,
) -> dict:
    paths = find_definitions(root)
    if not paths:
        raise RegistryError("no extension definitions found under catalog/")

    if history is None:
        history = has_history(root)
    documents = [(path, read_yaml(path)) for path in paths]
    definition_ids = {str((document.get("metadata") or {}).get("id", "")) for _, document in documents}
    packages, catalog_ids = load_packages(
        root, definition_ids, history=history, check_registries=check_registries, fetch=fetch
    )
    extensions: dict[str, dict] = {}
    seen_ids: dict[str, str] = {}
    errors = []

    for path, document in documents:
        relative = path.relative_to(root).as_posix()
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
        package = packages.get(extension_id)
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

    for extension_id in sorted(catalog_ids - set(seen_ids)):
        errors.append(
            f"{PACKAGE_CATALOG}: package {packages[extension_id]['key']!r} names {extension_id}, "
            "which no catalog definition has"
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
    parser.add_argument(
        "--check-registries",
        action="store_true",
        help="ask PyPI, npm and Maven Central which package versions are published",
    )
    args = parser.parse_args(argv)
    try:
        registry = build(args.extensions.resolve(), args.repository, check_registries=args.check_registries)
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
