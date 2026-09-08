# Canton Network Docs

This repo manages the contents of the [docs.canton.network](https://docs.canton.network) website.

Copyright (c) 2026 Canton Network. All rights reserved.
SPDX-License-Identifier: Apache-2.0 AND CC-BY-4.0


## Provide Feedback on a Docs Page

Every page on docs.canton.network has two feedback buttons in the footer:
- `Suggest edits`
- `Raise issue`

<img width="340" height="77" alt="image" src="https://github.com/user-attachments/assets/e143643a-484a-43a3-a4cb-b6ccda5f4fef" />

### Suggest edits:
Use this to propose a direct change to the page, fix a typo, update a code sample, improve wording, etc.

**How it works:**

- Click “Suggest edits” in the footer of any page.
- GitHub opens the source file for that exact page.
- Fork the repo, make your edits, and open a Pull Request.
- Canton docs team reviews and merges accepted changes if all checks out.

### Raise Issue:
Use this to report a problem or request new content without editing the source yourself.

**How it works:**

- Click “Raise Issue” in the footer of any page.
- A GitHub Issue opens *Pre-filled* with the Path of the page you were on.
- Describe in detail what's wrong or missing along with the source of information to verify and submit.
- The team reviews it and responds.


## Local Development

### Prerequisites

Either:

- [`direnv`](https://direnv.net/)
- [`nix`](https://nixos.org/download/) with `nix-command` and flakes support

OR:

- [Node.js 24](https://nodejs.org/en/download) (note that `mintlify` is not currently compatible with Node.js 26)
- [Python 3.14](https://www.python.org/downloads/) if you are running any of the machinery for syncing snippets or updating generated docs

### Running the dev server

```bash
direnv allow
cd docs-main && mintlify dev
```

The site will be available at http://localhost:3000.

### Check for broken links

```bash
mintlify broken-links
```

## Generate external snippets

External snippet extraction from source repositories is documented in [config/snippet-config/update-workflows.md](config/snippet-config/update-workflows.md). Use that workflow when updating snippet configs under `config/snippet-config/` or regenerating checked-in snippets under `docs-main/snippets/external/`.

Run this whenever you add, remove, or update a snippet source in `config/snippet-config/`, or when you need to pull in changes from an upstream repo (such as Canton or Splice) that are referenced by existing snippets.

```bash
npm run generate:external-snippets -- --list
npm run generate:external-snippets -- canton --source-dir ../canton
```

## License

This repository uses a dual-license model:

- **Documentation prose** (`.mdx` files, text content): [Creative Commons Attribution 4.0 International (CC-BY-4.0)](https://creativecommons.org/licenses/by/4.0/) — see [LICENSE-DOCS](LICENSE-DOCS)
- **Code snippets and configuration** (embedded code examples, scripts, JSON config): [Apache License 2.0](http://www.apache.org/licenses/LICENSE-2.0) — see [LICENSE](LICENSE)
