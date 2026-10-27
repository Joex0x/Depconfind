#!/usr/bin/env python3
"""DepConFind - find dependency confusion candidates in dependency manifests.

Reads a manifest (package.json, requirements.txt, Gemfile, Gemfile.lock) from a
URL or a local file, extracts the package names, and checks whether each name
exists on the public registry. A name that returns HTTP 404 is a *candidate*
for dependency confusion (likely an internal/private package that is not
claimed publicly).

Use only against targets you are authorized to test.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

__version__ = "1.0.0"

USER_AGENT = "Mozilla/5.0 (DepConFind)"

REGISTRIES = {
    "npm": "https://registry.npmjs.org/{pkg}",
    "pypi": "https://pypi.org/pypi/{pkg}/json",
    "gem": "https://rubygems.org/api/v1/gems/{pkg}.json",
}

NPM_DEP_KEYS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)

# npm version specs that do not resolve through the public registry
NPM_NON_REGISTRY_PREFIXES = (
    "file:", "link:", "workspace:", "git:", "git+", "github:", "gitlab:",
    "bitbucket:", "http:", "https:", "./", "../", "/", "~/",
)


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------

def detect_ecosystem(source):
    lowered = source.lower()
    if "gemfile" in lowered:
        return "gem"
    if "requirements" in lowered or lowered.endswith(".txt"):
        return "pypi"
    return "npm"  # default, same as the original tool


def to_raw_url(url):
    """Turn a github.com/.../blob/... link into its raw.githubusercontent.com form."""
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/(.+)$", url)
    if m:
        return "https://raw.githubusercontent.com/{}/{}/{}".format(*m.groups())
    return url


def fetch_text(source, timeout):
    if re.match(r"^https?://", source, re.I):
        req = urllib.request.Request(to_raw_url(source), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read().decode("utf-8", errors="replace")
    with open(source, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# --------------------------------------------------------------------------
# Parsers: each returns a sorted list of package names
# --------------------------------------------------------------------------

def parse_npm(raw):
    data = json.loads(raw)
    names = set()
    for key in NPM_DEP_KEYS:
        section = data.get(key)
        if not isinstance(section, dict):
            continue
        for name, spec in section.items():
            spec = str(spec).strip()
            if spec.startswith("npm:"):
                # alias: "foo": "npm:real-pkg@^1.0.0"
                target = spec[4:]
                at = target.rfind("@")
                name = target[:at] if at > 0 else target
            elif spec.startswith(NPM_NON_REGISTRY_PREFIXES):
                continue
            names.add(name)
    return sorted(names)


def parse_pypi(raw):
    names = set()
    for line in raw.splitlines():
        line = re.split(r"\s+#", line)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        if re.search(r"://", line):  # direct URL / VCS references
            continue
        line = line.split(";")[0].strip()  # environment markers
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            names.add(m.group(1))
    return sorted(names)


def parse_gem(raw):
    names = set()
    # Gemfile style: gem 'name', '~> 1.0'
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.match(r"""^gem\s+['"]([A-Za-z0-9._-]+)['"]""", stripped)
        if m and not re.search(r"\b(path|git|github):|:(path|git|github)\s*=>", stripped):
            names.add(m.group(1))
    # Gemfile.lock style: only the public "GEM" section's specs
    in_gem_section = in_specs = False
    for line in raw.splitlines():
        if line and not line.startswith(" "):
            in_gem_section = line.strip() == "GEM"
            in_specs = False
        elif in_gem_section and line.strip() == "specs:":
            in_specs = True
        elif in_gem_section and in_specs:
            m = re.match(r"^ {4}([A-Za-z0-9._-]+)\s+\(", line)
            if m:
                names.add(m.group(1))
    return sorted(names)


PARSERS = {"npm": parse_npm, "pypi": parse_pypi, "gem": parse_gem}


# --------------------------------------------------------------------------
# Registry check
# --------------------------------------------------------------------------

def check_package(ecosystem, name, timeout=15, retries=2):
    """Return (status, http_code). status is 'found', 'missing' or 'error'."""
    url = REGISTRIES[ecosystem].format(pkg=urllib.parse.quote(name, safe=""))
    code = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                code = res.getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        except (urllib.error.URLError, TimeoutError):
            code = None
        if code == 200:
            return "found", 200
        if code == 404:
            return "missing", 404
        time.sleep(1.5 * (attempt + 1))  # rate limit / transient error: back off
    return "error", code


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def scan(source, ecosystem, workers, timeout):
    eco = ecosystem or detect_ecosystem(source)
    print(f"[*] Processing: {source}")
    raw = fetch_text(source, timeout)
    names = PARSERS[eco](raw)
    if not names:
        print("[-] No dependencies found in this file\n")
        return {"source": source, "ecosystem": eco, "results": []}

    print(f"[*] Ecosystem: {eco} | {len(names)} dependencies found")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        statuses = list(pool.map(lambda n: check_package(eco, n, timeout), names))

    results = []
    for name, (status, code) in zip(names, statuses):
        results.append({"package": name, "status": status, "http": code})
        if status == "missing":
            print(f"[!] POSSIBLE DEPENDENCY CONFUSION --> {name} (HTTP 404, not on public registry)")
        elif status == "error":
            print(f"[?] Could not verify --> {name} (HTTP {code})")
        else:
            print(f"[+] Found --> {name}")
    print()
    return {"source": source, "ecosystem": eco, "results": results}


def print_banner():
    print(r"""
  ____             ____            _____ _           _
 |  _ \  ___ _ __ / ___|___  _ __ |  ___(_)_ __   __| |
 | | | |/ _ \ '_ \ |   / _ \| '_ \| |_  | | '_ \ / _` |
 | |_| |  __/ |_) | |__| (_) | | | |  _| | | | | | (_| |
 |____/ \___| .__/ \____\___/|_| |_|_|   |_|_| |_|\__,_|
            |_|                          v{}

 Examples:
   python3 depconfind.py -u https://raw.example.com/package.json
   python3 depconfind.py -u https://raw.example.com/requirements.txt -t pypi
   python3 depconfind.py -u ./Gemfile -t gem
   python3 depconfind.py -l urls.txt -o results.json
""".format(__version__))


def main():
    parser = argparse.ArgumentParser(
        prog="depconfind",
        description="Find dependency confusion candidates in dependency manifests.",
    )
    parser.add_argument("-u", "--url", help="URL or local path of a manifest")
    parser.add_argument("-l", "--list", help="File with one URL/path per line")
    parser.add_argument("-t", "--type", choices=sorted(REGISTRIES),
                        help="Ecosystem (default: guessed from the filename)")
    parser.add_argument("-w", "--workers", type=int, default=5,
                        help="Parallel registry lookups (default: 5)")
    parser.add_argument("--timeout", type=int, default=15,
                        help="HTTP timeout in seconds (default: 15)")
    parser.add_argument("-o", "--output", help="Write results as JSON to this file")
    parser.add_argument("--version", action="version", version=f"DepConFind {__version__}")
    args = parser.parse_args()

    if not args.url and not args.list:
        print_banner()
        return 0

    sources = []
    if args.url:
        sources.append(args.url)
    if args.list:
        try:
            with open(args.list, "r", encoding="utf-8") as f:
                sources += [l.strip() for l in f if l.strip() and not l.startswith("#")]
        except FileNotFoundError:
            print(f"[!] File not found: {args.list}")
            return 1

    report = []
    for source in sources:
        try:
            report.append(scan(source, args.type, args.workers, args.timeout))
        except Exception as e:  # keep going on bad URLs / bad manifests
            print(f"[!] Error processing {source}: {e}\n")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"[*] Results written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
