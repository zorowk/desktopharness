#!/usr/bin/env bash
# Internal remote functions, prepended to the Treeland provisioning payload.
# Source installs satisfy CMake, but do not change dpkg's package versions.
install_remaining_build_deps() {
  local checkout="$1" control_dir
  control_dir="$(mktemp -d "$src/apt-control.XXXXXXXX")"
  mkdir -p "$control_dir/debian"
  python3 - "$checkout/debian/control" "$control_dir/debian/control" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text()
source_packages = {'ddm', 'libddm', 'libddm-dev', 'treeland-protocols'}
pattern = r'^(Build-Depends(?:-Arch|-Indep)?):([^\n]*(?:\n[ \t][^\n]*)*)'

def remaining(match):
    dependencies = []
    for dependency in match[2].replace('\n', ' ').split(','):
        alternatives = dependency.strip().split('|')
        names = {re.split(r'[\s:(]', item.strip(), maxsplit=1)[0]
                 for item in alternatives}
        if names & source_packages:
            continue
        dependencies.append(dependency.strip())
    return match[1] + ': ' + ',\n '.join(dependencies)

Path(sys.argv[2]).write_text(re.sub(pattern, remaining, text, flags=re.MULTILINE))
PY
  sudo apt-get build-dep -y "$control_dir"
}

build_source_dependency() {
  local name="$1" url="$2" ref="$3" checkout sha
  checkout="$(mktemp -d "/var/tmp/$name-build.XXXXXXXX")"
  git -C "$checkout" init --quiet
  git -C "$checkout" remote add origin "$url"
  timeout 180 git -C "$checkout" fetch --depth 1 --no-tags origin "$ref"
  git -C "$checkout" checkout --detach FETCH_HEAD
  sha="$(git -C "$checkout" rev-parse HEAD)"
  git -C "$checkout" submodule update --init --recursive
  printf 'SOURCE_DEPENDENCY name=%s source=%s ref=%s commit=%s build_dir=%s\n' \
    "$name" "$url" "$ref" "$sha" "$checkout"
  if [[ -f "$checkout/debian/control" ]]; then
    install_remaining_build_deps "$checkout"
  fi
  cmake -S "$checkout" -B "$checkout/build" -GNinja \
    -DCMAKE_BUILD_TYPE=Debug -DCMAKE_INSTALL_PREFIX=/usr \
    2>&1 | tee "$checkout/configure.log"
  cmake --build "$checkout/build" --parallel "${TREELAND_BUILD_JOBS:-2}" \
    2>&1 | tee "$checkout/build.log"
  sudo cmake --install "$checkout/build" 2>&1 | tee "$checkout/install.log"
}

build_treeland_source_dependencies() {
  # Protocol definitions must be installed before configuring DDM.
  build_source_dependency treeland-protocols \
    https://github.com/linuxdeepin/treeland-protocols.git "$TREELAND_PROTOCOLS_REF"
  build_source_dependency ddm https://github.com/linuxdeepin/ddm.git "$DDM_REF"
  sudo ldconfig
  source_dependencies_built=1
}
