"""Prints shell `export KEY=value` lines for a build_fixtures.py
manifest, for `eval "$(python print_manifest_env.py <scenario>)"` in
.github/workflows/selfupdate-e2e.yml.

NOT `python -c "...open('$MANIFEST')..."` - confirmed via a real CI run
that interpolating a raw Windows path (e.g. D:\\a\\_temp\\...) into a
Python string LITERAL via bash substitution lets Python's own escape
processing corrupt it (\\a becomes a literal BEL character). Reading
the manifest path from an environment variable (MANIFEST, set by the
workflow's own `env:` block) never touches Python's string-literal
escape handling at all, and shlex.quote() below makes the values this
script prints safe to eval regardless of what characters a path
contains.

Usage: python print_manifest_env.py <bad_sig|bad_hash|rollback>
"""
import json
import os
import shlex
import sys

scenario = sys.argv[1]
manifest_path = os.environ['MANIFEST']

with open(manifest_path) as f:
    manifest = json.load(f)

values = {
    'RELEASE_DIR': manifest['releases'][scenario]['dir'],
    'OLD_BINARY': manifest['old_binary'],
    'OLD_VERSION': manifest['old_version'],
    'NEW_VERSION': manifest['new_version'],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
