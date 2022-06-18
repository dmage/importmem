#!/usr/bin/env python
import argparse
import logging
import logging.config
import platform
from subprocess import check_call

from importmem import (collapse_modules, get_modules, print_dot,
                       set_rss_for_modules)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-12s %(levelname)-8s %(message)s",
    datefmt="%m-%d %H:%M",
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze memory usage of Python module imports."
    )
    parser.add_argument(
        "module_name", nargs=1, metavar="MODULE", help="name of the module to analyze"
    )
    parser.add_argument("-o", "--output", default="-", help="name for output DOT file")
    parser.add_argument(
        "--generate-svg",
        default=False,
        action="store_true",
        help="convert DOT into SVG file",
    )
    parser.add_argument(
        "--open",
        default=False,
        action="store_true",
        help="automatically open generated file",
    )
    args = parser.parse_args()

    module_name = args.module_name[0]
    if "/" in module_name:
        if module_name.startswith("./"):
            module_name = module_name[len("./") :]
        if module_name.endswith(".py"):
            module_name = module_name[: -len(".py")]
        module_name = module_name.replace("/", ".")

    modules, root = get_modules(module_name)
    collapse_modules(modules, root)
    set_rss_for_modules(modules)

    dot_output = args.output
    generate_svg = args.generate_svg
    if args.open:
        generate_svg = True
    if generate_svg and dot_output == "-":
        dot_output = "./" + module_name.replace(".", "_") + ".dot"
    if dot_output != "-":
        with open(dot_output, "w") as f:
            print_dot(modules, f)
    else:
        print_dot(modules)

    svg_output = dot_output
    if svg_output.endswith(".dot"):
        svg_output = svg_output[: -len(".dot")]
    svg_output += ".svg"
    if generate_svg:
        check_call(["dot", "-Tsvg", dot_output, "-o", svg_output])

    if args.open:
        if platform.system() == "Darwin":
            check_call(["open", svg_output])
        else:
            check_call(["xdg-open", svg_output])


if __name__ == "__main__":
    main()
