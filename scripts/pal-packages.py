"""Validate the fixed PAL package recipe; never execute package contents."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

SOURCE_REPOSITORY = "godot-gdunit-labs/gdUnit4Net"
SOURCE_SHA = "a5f20fb8c12864b17c77d433cad14f9bf0094458"
RECIPE_REPOSITORY = "fanxiao92/gdUnit4Net"
SDK_VERSION = "10.0.402"
TFMS = ["net10.0", "net8.0"]
COMPONENTS = {
    "gdUnit4.api": {
        "version": "5.1.0-rc6.pal.a5f20fb8.1",
        "project": "Api",
        "assembly": "GdUnit4Api.dll",
        "assets": ["build/gdUnit4.api.props", "buildTransitive/gdUnit4.api.props"],
    },
    "gdUnit4.test.adapter": {
        "version": "3.2.0-pal.a5f20fb8.1",
        "project": "TestAdapter",
        "assembly": "GdUnit4.TestAdapter.dll",
        "assets": ["build/gdUnit4.test.adapter.targets"],
    },
}
# Unconditional public references in the pinned csproj files and central props;
# therefore identical for net8.0 and net10.0. Fully private references are excluded.
PUBLIC_DEPENDENCIES = {
    "gdUnit4.api": {
        "godotsharp": "4.7.0",
        "microsoft.codeanalysis.csharp": "5.0.0",
        "mono.cecil": "0.11.6",
    },
    "gdUnit4.test.adapter": {
        "godotsharp": "4.7.0",
        "microsoft.testplatform.objectmodel": "18.0.1",
        "microsoft.testplatform.adapterutilities": "18.0.1",
        "gdunit4.api": COMPONENTS["gdUnit4.api"]["version"],
        # CentralPackageTransitivePinningEnabled promotes these public API
        # dependencies even at the same version; they are not optional extras.
        "microsoft.codeanalysis.csharp": "5.0.0",
        "mono.cecil": "0.11.6",
    },
}
RECIPE_FILES = [
    ".github/workflows/pal-pack.yml",
    "scripts/pal-packages.py",
    "scripts/test_pal_packages.py",
    "docs/pal-packages.md",
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(*args, cwd):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def check_checkout(path, expected_sha, repository):
    require(command("git", "rev-parse", "HEAD", cwd=path) == expected_sha,
            f"Wrong source/recipe HEAD: {repository}")
    origin = command("git", "remote", "get-url", "origin", cwd=path).removesuffix(".git")
    require(origin == f"https://github.com/{repository}", "Unexpected checkout origin")
    require(not command("git", "status", "--porcelain", "--untracked-files=normal", cwd=path),
            f"Dirty checkout: {repository}")


def check_inputs(source, recipe):
    env = os.environ
    recipe_sha = env.get("PAL_WORKFLOW_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", recipe_sha), "Missing workflow SHA")
    require(env.get("GITHUB_REPOSITORY") == RECIPE_REPOSITORY, "Wrong workflow repository")
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "Not a manual dispatch")
    require(env.get("GITHUB_SHA") == recipe_sha, "Run/workflow SHA mismatch")
    workflow_ref = env.get("PAL_WORKFLOW_REF", "")
    require(workflow_ref.startswith(f"{RECIPE_REPOSITORY}/.github/workflows/pal-pack.yml@refs/"),
            "Unknown workflow ref")
    require(env.get("PAL_API_VERSION") == COMPONENTS["gdUnit4.api"]["version"], "Wrong API override")
    require(env.get("PAL_ADAPTER_VERSION") == COMPONENTS["gdUnit4.test.adapter"]["version"],
            "Wrong adapter override")
    for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"):
        require(re.fullmatch(r"[1-9][0-9]*", env.get(name, "")), f"Missing {name}")
    for name in ("ImageOS", "ImageVersion"):
        require(re.fullmatch(r"[A-Za-z0-9._-]+", env.get(name, "")), f"Missing runner {name}")
    check_checkout(source, SOURCE_SHA, SOURCE_REPOSITORY)
    check_checkout(recipe, recipe_sha, RECIPE_REPOSITORY)
    sdk = command("dotnet", "--version", cwd=source)
    require(sdk == SDK_VERSION, f"Wrong SDK: {sdk}")
    return {
        "repository": RECIPE_REPOSITORY,
        "ref": workflow_ref,
        "sha": recipe_sha,
        "files": {name: sha256(recipe / name) for name in RECIPE_FILES},
    }


def minimum_dependency_version(value):
    # NuGet's bare version and [version, ) spell the same inclusive lower bound.
    # Do not accept exact/bounded/floating ranges as normalization of that bound.
    value = value.strip()
    bound = re.fullmatch(r"\[([^,\s]+),\s*\)", value)
    if bound:
        value = bound[1]
    match = re.fullmatch(r"([0-9]+)\.([0-9]+)(?:\.([0-9]+))?(?:\.0)?", value)
    require(match is not None, f"Unsupported public dependency range: {value}")
    return ".".join(str(int(part or "0")) for part in match.groups())


def check_public_dependencies(package_id, dependencies):
    expected = PUBLIC_DEPENDENCIES[package_id]
    require(set(dependencies) == set(expected), f"Public dependency set mismatch: {package_id}")
    for identity, version in expected.items():
        actual = dependencies[identity]["version"]
        if identity == "gdunit4.api":
            require(actual in (version, f"[{version}, )", f"[{version}]"), "Wrong API dependency")
        else:
            require(minimum_dependency_version(actual) == version,
                    f"Wrong public dependency version: {identity}")


def inspect_package(path, package_id):
    expected = COMPONENTS[package_id]
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate package entries")
        require(all(not name.startswith(("/", "\\")) and ".." not in name.split("/")
                    and "\\" not in name for name in names), "Unsafe package entry")
        nuspecs = [name for name in names if name.endswith(".nuspec")]
        require(len(nuspecs) == 1, "Expected one nuspec")
        root = ET.fromstring(archive.read(nuspecs[0]))
        # NuGet emits a namespace, but synthetic fixtures need not do so.
        for node in root.iter():
            node.tag = node.tag.rsplit("}", 1)[-1]
        metadata = root.find("metadata")
        require(metadata is not None, "Missing nuspec metadata")
        require(metadata.findtext("id") == package_id, "Wrong package ID")
        require(metadata.findtext("version") == expected["version"], "Wrong package version")
        frameworks = sorted({name.split("/")[1] for name in names if name.startswith("lib/")})
        require(frameworks == TFMS, "Wrong package TFMs")
        required = [f"lib/{tfm}/{expected['assembly']}" for tfm in TFMS]
        required += expected["assets"] + ["LICENSE", "README.md", "icon.png"]
        for name in required:
            require(name in names and archive.getinfo(name).file_size > 0, f"Missing/empty asset: {name}")
        require(not metadata.findall("dependencies/dependency"), "Unexpected ungrouped public dependency")
        groups = metadata.findall("dependencies/group")
        require(sorted(group.get("targetFramework", "") for group in groups) == TFMS,
                "Wrong dependency framework groups")
        graph = {}
        for group in groups:
            dependencies = {}
            for dependency in group.findall("dependency"):
                identity, version = dependency.get("id", ""), dependency.get("version", "")
                require(identity and version and identity.lower() not in dependencies,
                        "Missing/duplicate dependency identity")
                require(identity.lower() != "gdunit4.analyzers", "Unexpected GdUnit analyzers")
                dependencies[identity.lower()] = {"id": identity, "version": version}
            check_public_dependencies(package_id, dependencies)
            graph[group.get("targetFramework")] = sorted(dependencies.values(), key=lambda item: item["id"])
    return {
        "id": package_id,
        "version": expected["version"],
        "tfms": frameworks,
        "dependencies": graph,
        "file": path.name,
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


def inspect_packages(directory, with_manifest=False):
    expected_files = {f"{identity}.{item['version']}.nupkg" for identity, item in COMPONENTS.items()}
    if with_manifest:
        expected_files |= {"provenance.json", "SHA256SUMS"}
    require({path.name for path in directory.iterdir()} == expected_files,
            "Expected exactly two packages (and manifest/checksums only when verifying)")
    return [inspect_package(directory / f"{identity}.{item['version']}.nupkg", identity)
            for identity, item in COMPONENTS.items()]


def restore_identity(source, component):
    path = source / component["project"] / "obj/project.assets.json"
    assets = json.loads(path.read_text(encoding="utf-8-sig"))
    require(sorted(assets["targets"]) == TFMS, "Unexpected restore targets")
    libraries = {}
    for identity, library in assets["libraries"].items():
        kind = library["type"]
        require(kind in ("package", "project"), "Unknown restored library type")
        libraries[identity] = {"type": kind}
        if kind == "project":
            require(component["project"] == "TestAdapter"
                    and library.get("msbuildProject", "").replace("\\", "/") == "../Api/GdUnit4Api.csproj",
                    "Unknown restored project")
        if kind == "package":
            digest = library.get("sha512", "")
            require(len(base64.b64decode(digest, validate=True)) == 64, "Missing restore content hash")
            libraries[identity]["sha512"] = digest
    require(libraries, "Empty restore libraries")
    targets = {}
    for tfm, entries in assets["targets"].items():
        targets[tfm] = {}
        for identity, entry in entries.items():
            require(identity in libraries, "Unknown restore target identity")
            targets[tfm][identity] = {"type": entry["type"], "dependencies": entry.get("dependencies", {})}
        require(targets[tfm], "Empty restore target")
    frameworks = assets["project"]["frameworks"]
    require(sorted(frameworks) == TFMS, "Unknown restored frameworks")
    # Do not export raw assets: project/restore sections contain local paths and feeds.
    return {
        "assetsSha256": sha256(path),
        "libraries": libraries,
        "targets": targets,
        "frameworks": {tfm: {
            "frameworkReferences": sorted(frameworks[tfm].get("frameworkReferences", {})),
            "downloadDependencies": frameworks[tfm].get("downloadDependencies", []),
        } for tfm in TFMS},
    }


def check_restored_public_dependencies(restored):
    for package_id, restoration in restored.items():
        require(sorted(restoration["targets"]) == TFMS, "Unexpected restore targets")
        for target in restoration["targets"].values():
            for identity, version in PUBLIC_DEPENDENCIES[package_id].items():
                if identity == "gdunit4.api":
                    matches = [(key, item) for key, item in target.items() if item["type"] == "project"]
                    require(len(matches) == 1 and matches[0][0].rsplit("/", 1)[-1] == version,
                            "Wrong restored API project version")
                else:
                    matches = [(key, item) for key, item in target.items()
                               if key.rsplit("/", 1)[0].lower() == identity]
                    require(len(matches) == 1 and matches[0][1]["type"] == "package"
                            and matches[0][0].rsplit("/", 1)[-1] == version,
                            f"Wrong restored public dependency: {identity}")


def checksum_text(directory, packages):
    names = sorted([package["file"] for package in packages] + ["provenance.json"])
    return "".join(f"{sha256(directory / name)}  {name}\n" for name in names)


def write_manifest(source, recipe, directory):
    workflow = check_inputs(source, recipe)
    packages = inspect_packages(directory)
    restored = {identity: restore_identity(source, component) for identity, component in COMPONENTS.items()}
    check_restored_public_dependencies(restored)
    runtimes = []
    for line in command("dotnet", "--list-runtimes", cwd=source).splitlines():
        match = re.fullmatch(r"([A-Za-z0-9.]+) ([0-9][A-Za-z0-9.+-]*) \[.+\]", line)
        require(match is not None, "Unknown runtime metadata")
        runtimes.append({"name": match[1], "version": match[2]})
    require(runtimes, "Missing runtime identities")
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    manifest = {
        "schemaVersion": 1,
        "source": {"repository": SOURCE_REPOSITORY, "sha": SOURCE_SHA},
        "workflow": workflow,
        "run": {"id": run_id, "attempt": attempt, "headSha": os.environ["GITHUB_SHA"],
                "url": f"https://github.com/{RECIPE_REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}"},
        "environment": {"sdk": SDK_VERSION, "runner": "ubuntu-24.04",
                        "imageOS": os.environ["ImageOS"], "imageVersion": os.environ["ImageVersion"],
                        "runtimes": runtimes, "python": sys.version.split()[0]},
        "restore": restored,
        "packages": packages,
    }
    (directory / "provenance.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (directory / "SHA256SUMS").write_text(checksum_text(directory, packages), encoding="utf-8")


def verify_bundle(directory):
    packages = inspect_packages(directory, with_manifest=True)
    manifest = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    require(manifest["schemaVersion"] == 1, "Unknown manifest schema")
    require(manifest["source"] == {"repository": SOURCE_REPOSITORY, "sha": SOURCE_SHA}, "Wrong source identity")
    require(manifest["environment"]["sdk"] == SDK_VERSION, "Wrong manifest SDK")
    require(manifest["workflow"]["repository"] == RECIPE_REPOSITORY, "Wrong recipe repository")
    workflow = manifest["workflow"]
    require(re.fullmatch(r"[0-9a-f]{40}", workflow["sha"]), "Missing recipe SHA")
    require(workflow["ref"].startswith(f"{RECIPE_REPOSITORY}/.github/workflows/pal-pack.yml@refs/"),
            "Unknown workflow ref")
    require(set(workflow["files"]) == set(RECIPE_FILES)
            and all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in workflow["files"].values()),
            "Missing recipe file hashes")
    run = manifest["run"]
    require(run["headSha"] == workflow["sha"] and re.fullmatch(r"[1-9][0-9]*", run["id"])
            and re.fullmatch(r"[1-9][0-9]*", run["attempt"]), "Unknown run identity")
    require(run["url"] == f"https://github.com/{RECIPE_REPOSITORY}/actions/runs/{run['id']}/attempts/{run['attempt']}",
            "Unknown run URL")
    require(set(manifest["restore"]) == set(COMPONENTS), "Missing restore identities")
    check_restored_public_dependencies(manifest["restore"])
    require(manifest["packages"] == packages, "Package hash/size/metadata mismatch")
    require((directory / "SHA256SUMS").read_text(encoding="utf-8") == checksum_text(directory, packages),
            "Checksum mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inputs", "manifest", "verify"])
    parser.add_argument("--source", type=Path)
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--packages", type=Path)
    args = parser.parse_args()
    if args.mode in ("inputs", "manifest"):
        require(args.source is not None and args.recipe is not None, "Source and recipe paths required")
    if args.mode in ("manifest", "verify"):
        require(args.packages is not None, "Packages path required")
    if args.mode == "inputs":
        check_inputs(args.source, args.recipe)
    elif args.mode == "manifest":
        write_manifest(args.source, args.recipe, args.packages)
    else:
        verify_bundle(args.packages)
    print(f"PAL package {args.mode}: OK")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        sys.exit(f"PAL package validation failed: {error}")
