# PAL source packages

This fork recipe is **unverified until its first reviewed manual Actions run**.
It packages only the upstream API and adapter; it does not test GdUnit or the game.
The later game acceptance gate is separate and runs once under parent approval.

## Fixed inputs

- Source: `godot-gdunit-labs/gdUnit4Net` at
  `a5f20fb8c12864b17c77d433cad14f9bf0094458` (includes upstream #527).
- Recipe: `fanxiao92/gdUnit4Net`, `.github/workflows/pal-pack.yml`, checked out at
  the executing `github.workflow_sha`, independently of the source checkout.
- Build SDK: **10.0.402**, installed into a job-local directory. The helper checks
  the actual SDK selected inside the source directory, failing on any drift.
- Source target frameworks: `net8.0` and `net10.0`. Ordinary remote restore obtains
  targeting packs. The source's GodotSharp dependency remains unchanged; no Godot
  engine or Godot workload is installed or run by this recipe.
- Actions: checkout v7 at `3d3c42e5aac5ba805825da76410c181273ba90b1`,
  setup-dotnet v6 at `a98b56852c35b8e3190ac28c8c2271da59106c68`, and
  upload-artifact v7 at `cf430e030ddbb5b0abf93d22962f4752f3646cd9`.
  These commit pins, not moving tags, are used by the workflow. Setup-dotnet uses
  its bundled installer and also installs its standard LTS CLI/runtime bootstrap;
  the exact SDK assertion remains mandatory. No extra install script is fetched
  or invoked by this recipe.

The source SHA excludes this recipe. Reproducible inputs include the recipe SHA,
script hashes, runner image, SDK and resolved dependencies, not the source SHA
alone. This is provenance, **not a claim of byte-for-byte reproducible builds**.

## Build and package identity

| Package ID | Self-built version |
| --- | --- |
| `gdUnit4.api` | `5.1.0-rc6.pal.a5f20fb8.1` |
| `gdUnit4.test.adapter` | `3.2.0-pal.a5f20fb8.1` |

The workflow passes `GdUnitAPIVersion` and `GdUnitTestAdapterVersion` as MSBuild
command-line global properties on restore, build and pack. They override the
source's component defaults and propagate through the adapter's API project
reference. There is no blanket `Version` override and no source-file rewrite.

One adapter graph build compiles both components. `GeneratePackageOnBuild=false`
avoids implicit packing, then two explicit `dotnet pack --no-build --no-restore`
commands produce the packages in a fresh, explicit output directory. No solution,
example, analyzer project, tests or engine is built/run. Existing compiler/style
analyzers remain enabled as upstream build dependencies; no GdUnit analyzer
package is produced or added to consumers.

Validation requires exactly two expected package filenames, matching nuspec IDs
and versions, both TFMs, nonempty assemblies and the upstream build props/targets,
plus the exact public dependency set and versions below, in each TFM. Missing,
unexpected or wrong-version public dependencies fail validation. Bare versions
and their NuGet inclusive minimum-range spelling are equivalent; bounded,
floating or exclusive ranges are not accepted for the upstream package references.

| Component | Required public package dependencies (both TFMs) |
| --- | --- |
| API | GodotSharp 4.7.0; Microsoft.CodeAnalysis.CSharp 5.0.0; Mono.Cecil 0.11.6 |
| Adapter | GodotSharp 4.7.0; Microsoft.TestPlatform.ObjectModel and AdapterUtilities 18.0.1; self-built API |
| Adapter central promotions | Microsoft.CodeAnalysis.CSharp 5.0.0; Mono.Cecil 0.11.6 (both required) |

The contract comes from the pinned csproj/central props. Central transitive
pinning promotes the API's public CSharp and Mono.Cecil dependencies into the
adapter package even at equal versions; fully private references remain excluded.
The validator also reconciles each required identity/version with both restored
TFM graphs, including the API project version, during creation and verification.

NuGet normally encodes the API project dependency as a minimum version. Consumers
must explicitly pin **both** self-built versions above, not rely on the adapter
dependency alone or an official fallback.

## Manual workflow and handoff

Stage A does not authorize commit, push, dispatch or local .NET execution.
After independent review, the parent may authorize a fast-forward-only recipe
commit to the fork default `master` branch (manual workflows must exist on the
default branch). Recheck the remote before any push; do not force or push upstream.
Keep existing upstream CI, report and cleanup workflows untouched and disabled.
The parent owns any remote settings decision.

The independent workflow has only `workflow_dispatch`, a 15-minute hosted job,
read-only contents permission and credential-free checkouts. Artifact upload uses
GitHub's job-scoped artifact runtime service, not packages-write or a NuGet key.
There is no recipe retry, continue-on-error, publishing or Release step. On a
failed run, retain its evidence and stop; another run needs fresh approval.

Artifact name: `pal-gdunit-a5f20fb8-<run_id>-<run_attempt>` (14-day retention).
Its root contains exactly:

- `gdUnit4.api.5.1.0-rc6.pal.a5f20fb8.1.nupkg`
- `gdUnit4.test.adapter.3.2.0-pal.a5f20fb8.1.nupkg`
- `provenance.json`
- `SHA256SUMS`

The manifest records source and recipe identities, recipe file hashes, run URL,
actual SDK assertion, installed runtime versions, runner image, package
dependencies/TFMs/byte counts/SHA256, and sanitized per-project restore libraries,
SHA512 content hashes, dependency edges and targeting-pack download identities. Raw assets files, absolute paths,
feed configuration, credentials and environment dumps are not uploaded. No
workloads are requested. Actual resolved package/targeting identities remain
unknown until the remote run; the restore is recorded, not lock-file frozen.

After safe extraction into a new evidence directory, use the reviewed recipe:

```sh
python3 scripts/pal-packages.py verify --packages /path/to/extracted-artifact
```

This checks internal consistency, not authenticity against a trusted external
hash. Independently compare manifest source/recipe/run identities with the
approved run, verify the recipe file hashes against that commit, and retain the
actual package SHA256 values. The Actions artifact ZIP digest is **not** either
package's SHA256. Never execute package contents during inspection.

Only then may the separate consumer lane use these exact two packages from a
local NuGet source. After the one unchanged full game gate passes, the parent may
publish the **same accepted bytes** and provenance in a fork prerelease Release.
Do not rebuild, replace the same version, use `latest`, or publish to public NuGet.
A subsequent consumer pins Release asset URLs and package hashes. An Actions
artifact is temporary storage, not a permanent distribution URL or a gate pass.

## Offline checks

Only synthetic ZIP/JSON fixtures are used; no SDK, restore or runtime is invoked:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/test_pal_packages.py -v
```

These tests exercise positive manifest generation, compare the dependency contract
with the pinned source definitions, and reject missing/unexpected/wrong-version
public dependencies (including required central promotions) in each component/TFM.
They also check range normalization, restore reconciliation, wrong source,
SDK/workflow identities, API dependency, package ID/version, missing framework or
asset, extra package, missing restore hash, changed package bytes and checksums.
They do not establish that remote MSBuild or actual game execution succeeds.
