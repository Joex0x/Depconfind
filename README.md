# DepConFind

> Find **dependency confusion** candidates by checking whether the packages in a manifest actually exist on the public registry.

![python](https://img.shields.io/badge/python-3.8%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![deps](https://img.shields.io/badge/dependencies-none-brightgreen)

## What is dependency confusion?

Companies often use **internal (private) packages** whose names are not registered on public registries such as npm, PyPI or RubyGems. If a build system is misconfigured, it may fetch a *public* package with the same name (usually the one with the higher version) instead of the private one. An attacker who registers that name publicly could get code executed inside the company's build or developer machines.

DepConFind automates the first step of finding this: **spotting package names that are referenced in a manifest but do not exist publicly.**

## How it works

1. Downloads a manifest from a URL (or reads a local file).
2. Extracts the package names.
3. Queries the public registry for each name.
4. Reports `HTTP 404` names as **possible dependency confusion** candidates.

Supported ecosystems:

| Ecosystem | Manifest files                              | Registry checked      |
|-----------|---------------------------------------------|-----------------------|
| `npm`     | `package.json`                              | registry.npmjs.org    |
| `pypi`    | `requirements.txt`                          | pypi.org              |
| `gem`     | `Gemfile`, `Gemfile.lock`                   | rubygems.org          |

Smart handling built in:

- npm: reads `dependencies`, `devDependencies`, `peerDependencies`, `optionalDependencies`; supports scoped packages (`@scope/name`) and `npm:` aliases; skips `file:`, `git+`, `workspace:` and URL specs.
- PyPI: strips extras, version specifiers, environment markers and comments; skips `-r`/`-e` lines and VCS/URL references.
- Gem: ignores commented lines and gems pointing to `path:`/`git:`; in `Gemfile.lock` only the public `GEM` section is used.
- GitHub `blob` links are automatically converted to raw links.
- Only a real `404` is flagged; rate limits (`429`) and server errors are retried and then reported as *could not verify*, not as vulnerable.

## Installation

No third-party dependencies. Python 3.8+ only.

```bash
git clone https://github.com/Joex0x/DepConFind.git
cd DepConFind
python3 depconfind.py --help
```

## Usage

```bash
# Single manifest (ecosystem guessed from filename)
python3 depconfind.py -u https://raw.githubusercontent.com/OWNER/REPO/main/package.json

# Choose the ecosystem explicitly
python3 depconfind.py -u https://raw.githubusercontent.com/OWNER/REPO/main/requirements.txt -t pypi
python3 depconfind.py -u ./Gemfile -t gem

# Many manifests from a list (one URL or path per line, # for comments)
python3 depconfind.py -l examples/urls.txt

# Save JSON results, use 10 parallel lookups
python3 depconfind.py -l urls.txt -w 10 -o results.json
```

### Options

| Option            | Description                                                     |
|-------------------|-----------------------------------------------------------------|
| `-u, --url`       | URL or local path of a manifest                                 |
| `-l, --list`      | File containing manifests, one per line                         |
| `-t, --type`      | `npm`, `pypi` or `gem` (default: guessed from the filename)     |
| `-w, --workers`   | Parallel registry lookups (default `5`)                         |
| `--timeout`       | HTTP timeout in seconds (default `15`)                          |
| `-o, --output`    | Write results to a JSON file                                    |
| `--version`       | Show version                                                    |

### Example output

```
[*] Processing: https://raw.githubusercontent.com/OWNER/REPO/main/package.json
[*] Ecosystem: npm | 4 dependencies found
[+] Found --> express
[+] Found --> lodash
[!] POSSIBLE DEPENDENCY CONFUSION --> acme-internal-utils (HTTP 404, not on public registry)
[?] Could not verify --> some-package (HTTP 429)
```

## What to do with a result

A `404` means *candidate*, not confirmed vulnerability. Verify manually:

- Is the name really meant to be private (internal naming, company prefix, scope)?
- For scoped npm packages (`@scope/name`), check whether the **scope itself** is registered; if it is owned by the company the package is not claimable.
- Is the project configured to prefer a private registry (scoped registry, `--index-url` only, lockfile with integrity hashes)? If so, the risk is lower.
- Report through the program's official channel. **Do not publish packages to public registries unless the program's rules explicitly allow it**, and if they do, use a harmless proof (for example, a DNS/HTTP callback with no data collection).

Contributions are welcome: open an issue first for larger changes.
