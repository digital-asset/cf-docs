# Source/output migration verification

The corpus is authored in `docs-source/` and copied/rendered into `docs-main/`.
The 28 former network-variable fragments are inline in their 14 owning source
pages. Mintlify's deployment directory and all public routes stay the same.

Against baseline `aaa9e325`, every retained output file is byte-identical except
network block comments pointing to the new owning source page. This comparison
covers text, link targets, network values, navigation, and binary assets.

The updated validator regenerates the expected content in memory and compares
all output paths and bytes, including missing, extra, and already-dirty files.
It does not write to either tree. Source deletions are mirrored during a build.

Validation:

- 78 focused Python tests and four Node tests passed.
- JSON Ledger OpenAPI regeneration produced no reader-output drift.
- Mintlify build validation passed.
- Full Python suite: 357 passed, 5 skipped, 8 failures; all eight failures also
  reproduce on unchanged baseline `aaa9e325` (four Javadoc version expectations,
  two Protobuf lifecycle expectations, a product-selector navigation expectation,
  and a stale copied cross-reference expectation).
- `git diff --check --find-copies-harder -C aaa9e325 HEAD` passes. Copy detection
  distinguishes inherited corpus whitespace from new changes.

The local preview shows the unchanged static network tabs:

![Network tabs rendered from the source corpus](network-tabs.png)

On the [hosted preview](https://cantonfoundation-codex-network-vars-source-output.mintlify.site/global-synchronizer/deployment/onboarding-process),
“Suggest edits” is absent before consent. After accepting the Osano banner it
points to:

```
https://github.com/canton-network/cf-docs/edit/codex/network-vars-source-output/docs-source/global-synchronizer/deployment/onboarding-process.mdx
```

After navigating through the sidebar to Prerequisites, the link updates to the
matching `docs-source/global-synchronizer/deployment/prerequisites.mdx` file.
The screenshot shows the post-consent toolbar whose destination was verified:

![Post-consent source edit link](source-edit-link.png)

GitHub checks: DCO, network-variable validation, Mintlify build validation, and
hosted deployment passed. Mintlify's separate link-rot check was skipped.
