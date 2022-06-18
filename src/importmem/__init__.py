import logging
import logging.config
import os
import re
import sys
from importlib import import_module
from math import sqrt
from subprocess import DEVNULL, Popen
from typing import Dict, Iterable, List, Optional, TextIO, Tuple

logger = logging.getLogger(__name__)

_invalid_dot_chars = re.compile(r"[^0-9A-Za-z_]")

_exclude = [
    "__future__",
    "_ast",
    "_bisect",
    "_blake2",
    "_bz2",
    "_cffi_backend",
    "_compression",
    "_contextvars",
    "_csv",
    "_datetime",
    "_decimal",
    "_hashlib",
    "_json",
    "_lzma",
    "_markupbase",
    "_multibytecodec",
    "_opcode",
    "_random",
    "_scproxy",
    "_sha3",
    "_sha512",
    "_ssl",
    "_uuid",
    "ast",
    "base64",
    "binascii",
    "bisect",
    "calendar",
    "csv",
    "ctypes",
    "ctypes._endian",
    "dataclasses",
    "datetime",
    "decimal",
    "decimal",
    "difflib",
    "dis",
    "encodings",
    "fnmatch",
    "gc",
    "getpass",
    "gettext",
    "grp",
    "hashlib",
    "hmac",
    "inspect",
    "ipaddress",
    "json",
    "json.decoder",
    "json.encoder",
    "json.scanner",
    "locale",
    "mmap",
    "ntpath",
    "numbers",
    "opcode",
    "pkgutil",
    "platform",
    "pprint",
    "pwd",
    "quopri",
    "random",
    "resource",
    "ssl",
    "stringprep",
    "tempfile",
    "textwrap",
    "timeit",
    "unicodedata",
    "uu",
    "uuid",
    "zipfile",
    "zlib",
    # non-standard, but problematic modules
    "oslo_config.sources._environment",
]


def _get_imports(pkg: str) -> List[str]:
    logger.info("Getting imports for {}".format(pkg))
    own_modules = list(sys.modules)
    import_module(pkg)
    new_modules = []
    to_delete = []
    for name in sys.modules:
        if name not in own_modules:
            module = sys.modules[name]
            if module.__spec__ is None:
                logger.debug("Ignoring module {} (__spec__ is None)".format(name))
            elif module.__spec__.name in _exclude:
                logger.debug("Ignoring module {} (_exclude)".format(name))
            else:
                new_modules.append(module.__spec__.name)
            to_delete.append(name)
    for name in to_delete:
        del sys.modules[name]
    return new_modules


def _dot_node_name(unique_id: int, name: str):
    return "node{}_{}".format(unique_id, _invalid_dot_chars.sub("_", name))


def _dot_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _module_name(import_path: str):
    return import_path.split(".")[0]


class Module(object):
    def __init__(self, name: str):
        self.name = name
        self.packages = set()
        self.dependencies = set()
        self.own_rss = 0
        self.total_rss = 0


def get_modules(root_pkg: str) -> Tuple[Dict[str, Module], str]:
    modules = {}
    queue = [root_pkg]
    while queue:
        pkg = queue.pop()
        module_name = _module_name(pkg)
        if module_name not in modules:
            mod = Module(module_name)
            modules[module_name] = mod
        else:
            mod = modules[module_name]
        if pkg in mod.packages:
            continue
        mod.packages.add(pkg)
        for import_path in _get_imports(pkg):
            queue.append(import_path)
            dep = _module_name(import_path)
            if dep == mod.name:
                continue
            mod.dependencies.add(dep)
    logger.debug("Found {} modules".format(modules))
    return modules, _module_name(root_pkg)


def _detect_loop(
    modules: Dict[str, Module],
    start_name: str,
    visited: Optional[List[str]] = None,
) -> Optional[List[str]]:
    if visited is None:
        visited = []
    if start_name in visited:
        idx = visited.index(start_name)
        return visited[idx:]
    visited.append(start_name)
    if start_name not in modules:
        raise Exception("visited: {!r}".format(visited))
    for dep in modules[start_name].dependencies:
        found = _detect_loop(modules, dep, visited)
        if found is not None:
            return found
    visited.pop()
    return None


def _rename_dependency(modules: Dict[str, Module], name: str, new_name: Optional[str]):
    logger.debug("Renaming dependency {!r} to {!r}".format(name, new_name))
    for mod in modules.values():
        if name in mod.dependencies:
            logger.debug(
                "Renaming {!r} dependency {!r} to {!r}".format(mod.name, name, new_name)
            )
            mod.dependencies.remove(name)
            if new_name is not None:
                mod.dependencies.add(new_name)


def _dependency_can_be_removed(
    modules: Dict[str, Module], name: str, dependency: str
) -> bool:
    for dep in modules[name].dependencies:
        if dep == dependency:
            continue
        if dependency in modules[dep].dependencies:
            return True
    return False


def collapse_modules(modules: Dict[str, Module], start_name: str):
    while True:
        logger.debug("Collapsing loops from {!r}".format(start_name))
        loop = _detect_loop(modules, start_name)
        if loop is None:
            break
        new_module = Module("\n".join(loop))
        modules[new_module.name] = new_module
        for mod in loop:
            new_module.packages.update(modules[mod].packages)
            new_module.dependencies.update(modules[mod].dependencies)
            logger.debug("Collapsed {!r} into {!r}".format(mod, new_module.name))
            del modules[mod]
        for mod in loop:
            _rename_dependency(modules, mod, new_module.name)
            if mod == start_name:
                start_name = new_module.name
        new_module.dependencies.remove(new_module.name)

    # Remove dependencies that won't affect results
    queue = [start_name]
    while queue:
        mod_name = queue.pop()
        while True:
            eliminate = None
            for dep in modules[mod_name].dependencies:
                if _dependency_can_be_removed(modules, mod_name, dep):
                    eliminate = dep
                    break
            if eliminate is None:
                break
            modules[mod_name].dependencies.remove(eliminate)
        for dep in modules[mod_name].dependencies:
            queue.append(dep)


def _imports_memory_usage(imports: Iterable[str]) -> int:
    script = ";".join("import " + i for i in imports)
    p = Popen(["python", "-c", script], stdout=DEVNULL, stderr=sys.stderr)
    _, waitstatus, ru = os.wait4(p.pid, 0)
    if waitstatus != 0:
        raise Exception("Got waitstatus {} for script: {}".format(waitstatus, script))
    return ru.ru_maxrss


def _set_rss_for_module(modules: Dict[str, Module], name: str) -> None:
    logger.info("Getting RSS for {!r}".format(name))
    logger.info("{!r} own packages: {!r}".format(name, modules[name].packages))
    modules[name].total_rss = _imports_memory_usage(modules[name].packages)
    deps = set()
    for dep in modules[name].dependencies:
        deps.update(modules[dep].packages)
    logger.info("{!r} dependencies: {!r}".format(name, deps))
    deps_rss = _imports_memory_usage(deps)
    modules[name].own_rss = modules[name].total_rss - deps_rss
    if modules[name].own_rss < 0:
        logger.warning(
            "{!r} has negative own RSS: {}".format(name, modules[name].own_rss)
        )
        modules[name].own_rss = 0


def set_rss_for_modules(modules: Dict[str, Module]) -> None:
    for name in modules:
        _set_rss_for_module(modules, name)


def print_dot(modules: Dict[str, Module], file: TextIO = sys.stdout) -> None:
    max_rss = 0
    min_rss = sys.maxsize
    for mod in modules.values():
        max_rss = max(max_rss, mod.total_rss)
        min_rss = min(min_rss, mod.total_rss)
    if min_rss == max_rss:
        max_rss += 1
    print("digraph G {", file=file)
    print("nodesep=0.5", file=file)
    print('node [fontsize=10,shape=box,style="rounded,filled"]', file=file)
    counter = 0
    node_names = {}
    for m in modules.values():
        node_name = _dot_node_name(counter, m.name)
        node_names[m.name] = node_name
        label = "{}\n{:.2f} MB ({:.2f} MB)".format(
            m.name, m.own_rss / 1024 / 1024, m.total_rss / 1024 / 1024
        )
        color = "0 {:.4f} 0.9".format(
            sqrt((m.total_rss - min_rss) / (max_rss - min_rss))
        )
        print(
            '{} [label="{}",fillcolor="{}"]'.format(
                node_name, _dot_escape(label), color
            ),
            file=file,
        )
    for m in modules.values():
        for dep in m.dependencies:
            print(
                "{} -> {}".format(node_names[m.name], node_names[dep]),
                file=file,
            )
    print("}", file=file)
