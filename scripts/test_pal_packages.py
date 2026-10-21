"""Offline fixtures only: no dotnet, network, Godot, or real package code."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

SPEC = importlib.util.spec_from_file_location("pal_packages", Path(__file__).with_name("pal-packages.py"))
PACK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACK)
API_VERSION = "5.1.0-rc6.pal.a5f20fb8.1"
ADAPTER_VERSION = "3.2.0-pal.a5f20fb8.1"
# Independent fixture expectations, not copied from the validator's contract.
API_DEPENDENCIES = {
    "GodotSharp": "4.7.0", "Microsoft.CodeAnalysis.CSharp": "5.0.0", "Mono.Cecil": "0.11.6",
}
ADAPTER_DEPENDENCIES = {
    "GodotSharp": "4.7.0", "Microsoft.TestPlatform.ObjectModel": "18.0.1",
    "Microsoft.TestPlatform.AdapterUtilities": "18.0.1", "gdUnit4.api": API_VERSION,
    "Microsoft.CodeAnalysis.CSharp": "5.0.0", "Mono.Cecil": "0.11.6",
}


class PalPackagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.packages = self.root / "packages"
        self.packages.mkdir()
        self.source = self.root / "source"
        self.recipe = self.root / "recipe"
        for name in PACK.RECIPE_FILES:
            path = self.recipe / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic recipe\n", encoding="utf-8")
        for project in ("Api", "TestAdapter"):
            path = self.source / project / "obj/project.assets.json"
            path.parent.mkdir(parents=True)
            dependencies = API_DEPENDENCIES if project == "Api" else ADAPTER_DEPENDENCIES
            libraries = {f"{identity}/{version}": {"type": "package", "sha512": base64.b64encode(b"x" * 64).decode()}
                         for identity, version in dependencies.items() if identity != "gdUnit4.api"}
            target = {identity: {"type": "package"} for identity in libraries}
            if project == "TestAdapter":
                identity = f"gdUnit4.api/{API_VERSION}"
                libraries[identity] = {"type": "project", "msbuildProject": "../Api/GdUnit4Api.csproj"}
                target[identity] = {"type": "project", "dependencies": API_DEPENDENCIES}
            path.write_text(json.dumps({
                "targets": {tfm: target for tfm in ("net8.0", "net10.0")},
                "libraries": libraries,
                "project": {"restore": {"projectPath": "DO_NOT_EXPORT", "sources": {"SECRET_FEED": {}}},
                            "frameworks": {tfm: {"frameworkReferences": {"Microsoft.NETCore.App": {}}}
                                           for tfm in ("net8.0", "net10.0")}},
            }), encoding="utf-8")
        self.env = patch.dict(os.environ, {
            "PAL_WORKFLOW_SHA": "b" * 40, "GITHUB_SHA": "b" * 40,
            "GITHUB_REPOSITORY": "fanxiao92/gdUnit4Net", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "PAL_WORKFLOW_REF": "fanxiao92/gdUnit4Net/.github/workflows/pal-pack.yml@refs/heads/master",
            "PAL_API_VERSION": API_VERSION, "PAL_ADAPTER_VERSION": ADAPTER_VERSION,
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
            "ImageOS": "ubuntu24", "ImageVersion": "20261001.1.0",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.make_package("gdUnit4.api", API_VERSION, "GdUnit4Api.dll",
                          ["build/gdUnit4.api.props", "buildTransitive/gdUnit4.api.props"], API_DEPENDENCIES)
        self.make_package("gdUnit4.test.adapter", ADAPTER_VERSION, "GdUnit4.TestAdapter.dll",
                          ["build/gdUnit4.test.adapter.targets"], ADAPTER_DEPENDENCIES)

    def make_package(self, identity, version, assembly, assets, public_dependencies):
        dependencies = "".join(f'<dependency id="{name}" version="{bound}" />'
                               for name, bound in public_dependencies.items())
        groups = "".join(f'<group targetFramework="{tfm}">{dependencies}</group>' for tfm in ("net8.0", "net10.0"))
        nuspec = (f'<package xmlns="http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd"><metadata>'
                  f'<id>{identity}</id><version>{version}</version><dependencies>{groups}</dependencies></metadata></package>')
        path = self.packages / f"{identity}.{version}.nupkg"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{identity}.nuspec", nuspec)
            for name in assets + ["LICENSE", "README.md", "icon.png"]:
                archive.writestr(name, "synthetic asset")
            for tfm in ("net8.0", "net10.0"):
                archive.writestr(f"lib/{tfm}/{assembly}", "not executable: fixture only")

    def fake_command(self, *args, cwd):
        if args == ("git", "rev-parse", "HEAD"):
            return PACK.SOURCE_SHA if cwd == self.source else "b" * 40
        if args == ("git", "remote", "get-url", "origin"):
            repository = PACK.SOURCE_REPOSITORY if cwd == self.source else PACK.RECIPE_REPOSITORY
            return f"https://github.com/{repository}.git"
        if args == ("git", "status", "--porcelain", "--untracked-files=normal"):
            return ""
        if args == ("dotnet", "--version"):
            return "10.0.402"
        if args == ("dotnet", "--list-runtimes"):
            return "Microsoft.NETCore.App 10.0.10 [DO_NOT_EXPORT]"
        self.fail(f"Unexpected subprocess: {args}")

    def manifest(self):
        with patch.object(PACK, "command", side_effect=self.fake_command):
            PACK.write_manifest(self.source, self.recipe, self.packages)

    def rewrite_entry(self, name, transform, package_id="gdUnit4.test.adapter"):
        path = next(self.packages.glob(f"{package_id}.*.nupkg"))
        with zipfile.ZipFile(path) as archive:
            entries = {entry: archive.read(entry) for entry in archive.namelist()}
        entries[name] = transform(entries[name])
        with zipfile.ZipFile(path, "w") as archive:
            for entry, data in entries.items():
                archive.writestr(entry, data)

    def rewrite_dependency(self, package_id, tfm, identity, mutation):
        def transform(data):
            root = ET.fromstring(data)
            group = next(node for node in root.iter() if node.tag.endswith("group")
                         and node.get("targetFramework") == tfm)
            dependency = next(node for node in group if node.get("id") == identity)
            mutation(group, dependency)
            return ET.tostring(root)
        self.rewrite_entry(f"{package_id}.nuspec", transform, package_id)

    def test_public_contract_matches_pinned_project_definitions(self):
        root = Path(__file__).resolve().parents[1]
        versions = {node.tag: node.text for node in ET.parse(root / "ProjectVersions.props").findall("PropertyGroup/*")}
        self.assertEqual("net8.0;net10.0", versions["CommonTargetFrameworks"])
        for node in ET.parse(root / "Directory.Build.props").findall("ItemGroup/PackageReference"):
            self.assertEqual("all", node.findtext("PrivateAssets"))
        central = ET.parse(root / "Directory.Packages.props")
        self.assertEqual("true", central.findtext("PropertyGroup/CentralPackageTransitivePinningEnabled"))
        for key, value in versions.items():
            for _ in range(10):
                value = re.sub(r"\$\(([^)]+)\)", lambda match: versions[match[1]], value)
            versions[key] = value
        central_versions = {}
        for node in central.findall("ItemGroup/PackageVersion"):
            value = re.sub(r"\$\(([^)]+)\)", lambda match: versions[match[1]], node.get("Version"))
            central_versions[node.get("Include").lower()] = "4.7.0" if value == "4.7" else value
        public = {}
        for name, project in (("gdUnit4.api", "Api/GdUnit4Api.csproj"),
                              ("gdUnit4.test.adapter", "TestAdapter/GdUnit4TestAdapter.csproj")):
            public[name] = {}
            for node in ET.parse(root / project).findall("ItemGroup/PackageReference"):
                self.assertIsNone(node.get("Condition"))
                if (node.get("PrivateAssets") or node.findtext("PrivateAssets", "")).lower() != "all":
                    identity = node.get("Include").lower()
                    public[name][identity] = central_versions[identity]
        self.assertEqual(public["gdUnit4.api"], PACK.PUBLIC_DEPENDENCIES["gdUnit4.api"])
        # The adapter's public API project reference supplies the central transitive
        # CSharp and Mono.Cecil pins; GodotSharp is already a direct dependency.
        adapter = ET.parse(root / "TestAdapter/GdUnit4TestAdapter.csproj")
        self.assertEqual(["..\\Api\\GdUnit4Api.csproj"],
                         [node.get("Include") for node in adapter.findall("ItemGroup/ProjectReference")])
        self.assertEqual(public["gdUnit4.test.adapter"] | public["gdUnit4.api"] | {"gdunit4.api": API_VERSION},
                         PACK.PUBLIC_DEPENDENCIES["gdUnit4.test.adapter"])

    def test_missing_public_dependency_rejected_for_each_component_and_tfm(self):
        for package_id, dependencies in (("gdUnit4.api", API_DEPENDENCIES), ("gdUnit4.test.adapter", ADAPTER_DEPENDENCIES)):
            path = next(self.packages.glob(f"{package_id}.*.nupkg"))
            original = path.read_bytes()
            for tfm in ("net8.0", "net10.0"):
                for identity in dependencies:
                    with self.subTest(package_id=package_id, tfm=tfm, dependency=identity):
                        path.write_bytes(original)
                        self.rewrite_dependency(package_id, tfm, identity, lambda group, node: group.remove(node))
                        with self.assertRaisesRegex(ValueError, "Public dependency set mismatch"):
                            PACK.inspect_packages(self.packages)
            path.write_bytes(original)

    def test_wrong_non_api_version_rejected_for_each_component_and_tfm(self):
        for package_id, dependencies in (("gdUnit4.api", API_DEPENDENCIES), ("gdUnit4.test.adapter", ADAPTER_DEPENDENCIES)):
            path = next(self.packages.glob(f"{package_id}.*.nupkg"))
            original = path.read_bytes()
            for tfm in ("net8.0", "net10.0"):
                for identity in dependencies:
                    if identity == "gdUnit4.api":
                        continue
                    with self.subTest(package_id=package_id, tfm=tfm, dependency=identity):
                        path.write_bytes(original)
                        self.rewrite_dependency(package_id, tfm, identity, lambda group, node: node.set("version", "99.0.0"))
                        with self.assertRaisesRegex(ValueError, "Wrong public dependency version"):
                            PACK.inspect_packages(self.packages)
            path.write_bytes(original)

    def test_unexpected_public_dependency_rejected(self):
        for package_id in ("gdUnit4.api", "gdUnit4.test.adapter"):
            path = next(self.packages.glob(f"{package_id}.*.nupkg"))
            original = path.read_bytes()
            for tfm in ("net8.0", "net10.0"):
                with self.subTest(package_id=package_id, tfm=tfm):
                    path.write_bytes(original)
                    self.rewrite_dependency(package_id, tfm, "GodotSharp", lambda group, node:
                                            ET.SubElement(group, node.tag, {"id": "Unexpected.Package", "version": "1.0.0"}))
                    with self.assertRaisesRegex(ValueError, "Public dependency set mismatch"):
                        PACK.inspect_packages(self.packages)
            path.write_bytes(original)

    def test_ungrouped_public_dependency_rejected(self):
        self.rewrite_entry("gdUnit4.test.adapter.nuspec", lambda data:
                           data.replace(b"<dependencies>", b'<dependencies><dependency id="Unexpected" version="1.0.0"/>'))
        with self.assertRaisesRegex(ValueError, "ungrouped public dependency"):
            PACK.inspect_packages(self.packages)

    def test_nuget_minimum_range_normalization(self):
        for value in ("4.7", "4.7.0.0", "[4.7.0, )", "[4.7,)"):
            with self.subTest(version=value):
                self.rewrite_dependency("gdUnit4.test.adapter", "net8.0", "GodotSharp",
                                        lambda group, node: node.set("version", value))
                PACK.inspect_packages(self.packages)

    def test_non_equivalent_public_ranges_rejected(self):
        for value in ("[4.7.0]", "[4.7.0,5.0.0)", "(4.7.0,)", "4.7.*", "[0.0.0,)", "4 .7 .0"):
            with self.subTest(version=value):
                self.rewrite_dependency("gdUnit4.test.adapter", "net8.0", "GodotSharp",
                                        lambda group, node: node.set("version", value))
                with self.assertRaisesRegex(ValueError, "public dependency (range|version)"):
                    PACK.inspect_packages(self.packages)

    def test_wrong_restored_public_version_rejected(self):
        path = self.source / "Api/obj/project.assets.json"
        path.write_text(path.read_text().replace("GodotSharp/4.7.0", "GodotSharp/4.7.1"))
        with self.assertRaisesRegex(ValueError, "Wrong restored public dependency"):
            self.manifest()

    def test_bundle_verification_reconciles_restored_public_versions(self):
        self.manifest()
        path = self.packages / "provenance.json"
        path.write_text(path.read_text().replace("GodotSharp/4.7.0", "GodotSharp/4.7.1"))
        with self.assertRaisesRegex(ValueError, "Wrong restored public dependency"):
            PACK.verify_bundle(self.packages)

    def test_valid_manifest_roundtrip_and_no_raw_restore_paths(self):
        self.manifest()
        PACK.verify_bundle(self.packages)
        text = (self.packages / "provenance.json").read_text(encoding="utf-8")
        self.assertNotIn("DO_NOT_EXPORT", text)
        self.assertNotIn("SECRET_FEED", text)
        manifest = json.loads(text)
        self.assertEqual(2, len(manifest["packages"]))
        self.assertEqual(["net10.0", "net8.0"], manifest["packages"][0]["tfms"])
        self.assertEqual("123", manifest["run"]["id"])

    def test_wrong_source_head_rejected(self):
        with patch.object(PACK, "command", return_value="c" * 40):
            with self.assertRaisesRegex(ValueError, "HEAD"):
                PACK.check_inputs(self.source, self.recipe)

    def test_wrong_sdk_rejected(self):
        def wrong_sdk(*args, cwd):
            return "10.0.403" if args[0] == "dotnet" else self.fake_command(*args, cwd=cwd)
        with patch.object(PACK, "command", side_effect=wrong_sdk):
            with self.assertRaisesRegex(ValueError, "SDK"):
                PACK.check_inputs(self.source, self.recipe)

    def test_wrong_workflow_identity_rejected(self):
        os.environ["GITHUB_SHA"] = "c" * 40
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            PACK.check_inputs(self.source, self.recipe)

    def test_official_api_dependency_rejected(self):
        self.rewrite_entry("gdUnit4.test.adapter.nuspec", lambda data: data.replace(API_VERSION.encode(), b"5.1.0-rc6"))
        with self.assertRaisesRegex(ValueError, "API dependency"):
            self.manifest()

    def test_wrong_package_id_rejected(self):
        self.rewrite_entry("gdUnit4.test.adapter.nuspec", lambda data: data.replace(b"<id>gdUnit4.test.adapter", b"<id>wrong"))
        with self.assertRaisesRegex(ValueError, "package ID"):
            self.manifest()

    def test_wrong_package_version_rejected(self):
        self.rewrite_entry("gdUnit4.test.adapter.nuspec", lambda data: data.replace(ADAPTER_VERSION.encode(), b"3.2.0"))
        with self.assertRaisesRegex(ValueError, "package version"):
            self.manifest()

    def test_changed_package_bytes_rejected(self):
        self.manifest()
        self.rewrite_entry("README.md", lambda data: data + b"changed")
        with self.assertRaisesRegex(ValueError, "hash/size/metadata"):
            PACK.verify_bundle(self.packages)

    def test_missing_framework_rejected(self):
        self.rewrite_entry("gdUnit4.test.adapter.nuspec", lambda data: data.replace(b'net10.0', b'net9.0'))
        with self.assertRaisesRegex(ValueError, "framework groups"):
            self.manifest()

    def test_empty_build_asset_rejected(self):
        self.rewrite_entry("build/gdUnit4.test.adapter.targets", lambda data: b"")
        with self.assertRaisesRegex(ValueError, "Missing/empty asset"):
            self.manifest()

    def test_extra_package_rejected(self):
        (self.packages / "extra.nupkg").write_bytes(b"fake")
        with self.assertRaisesRegex(ValueError, "exactly two"):
            self.manifest()

    def test_missing_restore_hash_rejected(self):
        path = self.source / "Api/obj/project.assets.json"
        data = json.loads(path.read_text())
        data["libraries"]["GodotSharp/4.7.0"].pop("sha512")
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "restore content hash"):
            self.manifest()

    def test_wrong_restored_api_version_rejected(self):
        path = self.source / "TestAdapter/obj/project.assets.json"
        path.write_text(path.read_text().replace(API_VERSION, "5.1.0-rc6"))
        with self.assertRaisesRegex(ValueError, "restored API project version"):
            self.manifest()

    def test_missing_package_rejected(self):
        next(self.packages.glob("gdUnit4.api.*.nupkg")).unlink()
        with self.assertRaisesRegex(ValueError, "exactly two"):
            self.manifest()

    def test_changed_checksum_rejected(self):
        self.manifest()
        (self.packages / "SHA256SUMS").write_text("0" * 64)
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            PACK.verify_bundle(self.packages)

    def test_wrong_manifest_source_rejected(self):
        self.manifest()
        path = self.packages / "provenance.json"
        path.write_text(path.read_text().replace(PACK.SOURCE_SHA, "c" * 40))
        with self.assertRaisesRegex(ValueError, "source identity"):
            PACK.verify_bundle(self.packages)


if __name__ == "__main__":
    unittest.main()
