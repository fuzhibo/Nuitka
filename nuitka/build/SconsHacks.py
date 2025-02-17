#     Copyright 2020, Kay Hayen, mailto:kay.hayen@gmail.com
#
#     Part of "Nuitka", an optimizing Python compiler that is compatible and
#     integrates with CPython, but also works on its own.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
#
""" Hacks for scons that we apply.

We blacklist some tools from the standard scan, there is e.g. no need to ask
what fortran version we have installed to compile with Nuitka.

Also we hack the gcc version detection to fix some bugs in it, and to avoid
scanning for g++ when we have a gcc installer, but only if that is not too
version.

"""


import os
import re
import subprocess

import SCons.Tool.gcc  # pylint: disable=I0021,import-error
from SCons.Script import Environment  # pylint: disable=I0021,import-error

from nuitka.Tracing import scons_details_logger

from .SconsUtils import decodeData, isGccName

# Cache for detected versions.
v_cache = {}

# Prevent these programs from being found, avoiding the burden of tool init.
blacklisted_tools = (
    "g++",
    "c++",
    "f95",
    "f90",
    "f77",
    "gfortran",
    "ifort",
    "javah",
    "tar",
    "dmd",
    "gdc",
    "flex",
    "bison",
    "ranlib",
    "ar",
    "ldc2",
    "pdflatex",
    "pdftex",
    "latex",
    "tex",
    "dvipdf",
    "dvips",
    "gs",
    "swig",
    "ifl",
    "rpcgen",
    "rpmbuild",
    "bk",
    "p4",
    "m4",
    "ml",
    "icc",
    "sccs",
    "rcs",
    "cvs",
    "as",
    "gas",
    "nasm",
)


# From gcc.py of Scons
def myDetectVersion(env, cc):
    """Return the version of the GNU compiler, or None if it is not a GNU compiler."""
    cc = env.subst(cc)
    if not cc:
        return None
    if "++" in os.path.basename(cc):
        return None

    version = None

    clvar = tuple(SCons.Util.CLVar(cc))

    if clvar in v_cache:
        return v_cache[clvar]

    clvar0 = os.path.basename(clvar[0])

    if isGccName(clvar0) or "clang" in clvar0:
        command = list(clvar) + ["-dumpversion"]
    else:
        command = list(clvar) + ["--version"]

    # pipe = SCons.Action._subproc(env, SCons.Util.CLVar(cc) + ['-dumpversion'],
    pipe = SCons.Action._subproc(  # pylint: disable=protected-access
        env, command, stdin="devnull", stderr="devnull", stdout=subprocess.PIPE
    )

    line = pipe.stdout.readline()
    # Non-GNU compiler's output (like AIX xlc's) may exceed the stdout buffer:
    # So continue with reading to let the child process actually terminate.
    while pipe.stdout.readline():
        pass

    ret = pipe.wait()
    if ret != 0:
        return None

    if str is not bytes and type(line) is bytes:
        line = decodeData(line)

    line = line.strip()

    match = re.findall(r"[0-9]+(?:\.[0-9]+)+", line)
    if match:
        version = match[0]
    else:
        # gcc 8 or higher
        version = line.strip()

    version = tuple(int(part) for part in version.split("."))

    scons_details_logger.info(
        "CC %r version check gives %r from %r" % (cc, version, line)
    )

    v_cache[clvar] = version
    return version


def myDetect(self, progs):
    # Don't consider Fortran, tar, D, c++, we don't need it. We do manual
    # fallback
    for blacklisted_tool in blacklisted_tools:
        if blacklisted_tool in progs:
            return None

    return orig_detect(self, progs)


# The original value will be used in our form.
orig_detect = Environment.Detect


def getEnhancedToolDetect():
    SCons.Tool.gcc.detect_version = myDetectVersion

    return myDetect


def makeGccUseLinkerFile(source_dir, source_files, env):
    tmp_linker_filename = os.path.join(source_dir, "@link_input.txt")

    env["LINKCOM"] = env["LINKCOM"].replace(
        "$SOURCES", "@%s" % env.get("ESCAPE", lambda x: x)(tmp_linker_filename)
    )

    with open(tmp_linker_filename, "w") as tmpfile:
        for filename in source_files:
            filename = ".".join(filename.split(".")[:-1]) + ".o"

            if os.name == "nt":
                filename = filename.replace(os.path.sep, "/")

            tmpfile.write('"%s"\n' % filename)

        tmpfile.write(env.subst("$SOURCES"))
