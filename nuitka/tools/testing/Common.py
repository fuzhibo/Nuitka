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
""" Common test infrastructure functions. To be used by test runners. """

from __future__ import print_function

import ast
import atexit
import gc
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from optparse import OptionGroup, OptionParser

from nuitka.freezer.DependsExe import getDependsExePath
from nuitka.Tracing import OurLogger, my_print
from nuitka.utils.AppDirs import getCacheDir
from nuitka.utils.Execution import check_output, withEnvironmentVarOverriden
from nuitka.utils.FileOperations import (
    getFileContentByLine,
    getFileContents,
    getFileList,
    makePath,
    removeDirectory,
)

from .SearchModes import (
    SearchModeAll,
    SearchModeByPattern,
    SearchModeCoverage,
    SearchModeImmediate,
    SearchModeOnly,
    SearchModeResume,
)


def check_result(*popenargs, **kwargs):
    if "stdout" in kwargs:
        raise ValueError("stdout argument not allowed, it will be overridden.")

    process = subprocess.Popen(stdout=subprocess.PIPE, *popenargs, **kwargs)
    _unused_output, _unused_err = process.communicate()
    retcode = process.poll()

    if retcode:
        return False
    else:
        return True


def goMainDir():
    # Go its own directory, to have it easy with path knowledge.
    os.chdir(os.path.dirname(os.path.abspath(sys.modules["__main__"].__file__)))


_python_version_str = None
_python_version = None
_python_arch = None
_python_executable = None
_python_vendor = None


def setup(suite="", needs_io_encoding=False, silent=False, go_main=True):
    if go_main:
        goMainDir()

    if "PYTHON" not in os.environ:
        os.environ["PYTHON"] = sys.executable

    # Allow test code to use this to make caching specific.
    os.environ["NUITKA_TEST_SUITE"] = suite

    # Allow providing 33, 27, and expand that to python2.7
    if (
        len(os.environ["PYTHON"]) == 2
        and os.environ["PYTHON"].isdigit()
        and os.name != "nt"
    ):

        os.environ["PYTHON"] = "python%s.%s" % (
            os.environ["PYTHON"][0],
            os.environ["PYTHON"][1],
        )

    if needs_io_encoding and "PYTHONIOENCODING" not in os.environ:
        os.environ["PYTHONIOENCODING"] = "utf-8"

    version_output = check_output(
        (
            os.environ["PYTHON"],
            "-c",
            """\
import sys, os;\
print(".".join(str(s) for s in list(sys.version_info)[:3]));\
print(("x86_64" if "AMD64" in sys.version else "x86") if os.name == "nt" else os.uname()[4]);\
print(sys.executable);\
print("Anaconda" if os.path.exists(os.path.join(sys.prefix, 'conda-meta')) else "Unknown")\
""",
        ),
        stderr=subprocess.STDOUT,
    )

    global _python_version_str, _python_version, _python_arch, _python_executable, _python_vendor  # singleton, pylint: disable=global-statement

    _python_version_str = version_output.split(b"\n")[0].strip()
    _python_arch = version_output.split(b"\n")[1].strip()
    _python_executable = version_output.split(b"\n")[2].strip()
    _python_vendor = version_output.split(b"\n")[3].strip()

    if str is not bytes:
        _python_version_str = _python_version_str.decode("utf-8")
        _python_arch = _python_arch.decode("utf-8")
        _python_executable = _python_executable.decode("utf-8")
        _python_vendor = _python_vendor.decode("utf-8")

    assert type(_python_version_str) is str, repr(_python_version_str)
    assert type(_python_arch) is str, repr(_python_arch)
    assert type(_python_executable) is str, repr(_python_executable)

    if not silent:
        my_print("Using concrete python", _python_version_str, "on", _python_arch)

    if "COVERAGE_FILE" not in os.environ:
        os.environ["COVERAGE_FILE"] = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", ".coverage"
        )

    _python_version = tuple(int(d) for d in _python_version_str.split("."))

    return _python_version


def getPythonArch():
    return _python_arch


def getPythonVendor():
    return _python_vendor


def getPythonVersionString():
    return _python_version_str


tmp_dir = None


def getTempDir():
    # Create a temporary directory to work in, automatically remove it in case
    # it is empty in the end.
    global tmp_dir  # singleton, pylint: disable=global-statement

    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp(
            prefix=os.path.basename(
                os.path.dirname(os.path.abspath(sys.modules["__main__"].__file__))
            )
            + "-",
            dir=tempfile.gettempdir() if not os.path.exists("/var/tmp") else "/var/tmp",
        )

        def removeTempDir():
            removeDirectory(path=tmp_dir, ignore_errors=True)

        atexit.register(removeTempDir)

    return tmp_dir


def convertUsing2to3(path, force=False):
    command = [os.environ["PYTHON"], "-m", "py_compile", path]

    if not force:
        with open(path) as source_file:
            if "xrange" not in source_file.read():
                with open(os.devnull, "w") as stderr:
                    if check_result(command, stderr=stderr):
                        return path, False

    filename = os.path.basename(path)

    new_path = os.path.join(getTempDir(), filename)

    # This may already be a temp file, e.g. because of construct creation.
    try:
        shutil.copy(path, new_path)
    except shutil.Error:
        pass

    # For Python2.6 and 3.2 the -m lib2to3 was not yet supported.
    use_binary = sys.version_info[:2] in ((2, 6), (3, 2))

    if use_binary:
        # On Windows, we cannot rely on 2to3 to be in the path.
        if os.name == "nt":
            command = [
                sys.executable,
                os.path.join(os.path.dirname(sys.executable), "Tools/Scripts/2to3.py"),
            ]
        else:
            command = ["2to3"]
    else:
        command = [sys.executable, "-m", "lib2to3"]

    command += ("-w", "-n", "--no-diffs", new_path)

    with open(os.devnull, "w") as devnull:
        try:
            check_output(command, stderr=devnull)

        except subprocess.CalledProcessError:
            if os.name == "nt":
                raise

            command[0:3] = ["2to3"]

            check_output(command, stderr=devnull)

    with open(new_path) as result_file:
        data = result_file.read()

    with open(new_path, "w") as result_file:
        result_file.write("__file__ = %r\n" % os.path.abspath(path))
        result_file.write(data)

    return new_path, True


def decideFilenameVersionSkip(filename):
    """Make decision whether to skip based on filename and Python version.

    This codifies certain rules that files can have as suffixes or prefixes
    to make them be part of the set of tests executed for a version or not.

    Generally, an ening of "<major><minor>.py" indicates that it must be that
    Python version or higher. There is no need for ending in "26.py" as this
    is the minimum version anyway.

    The "_2.py" indicates a maxmimum version of 2.7, i.e. not Python 3.x, for
    language syntax no more supported.
    """

    # This will make many decisions with immediate returns.
    # pylint: disable=too-many-branches,too-many-return-statements

    assert type(filename) is str, repr(filename)

    # Skip runner scripts by default.
    if filename.startswith("run_"):
        return False

    if filename.endswith(".j2"):
        filename = filename[:-3]

    # Skip tests that require Python 2.7 at least.
    if filename.endswith("27.py") and _python_version < (2, 7):
        return False

    # Skip tests that require Python 2 at maximum.
    if filename.endswith("_2.py") and _python_version > (3,):
        return False

    # Skip tests that require Python 3.7 at maximum.
    if filename.endswith("_37.py") and _python_version > (3, 8):
        return False

    # Skip tests that require Python 3.2 at least.
    if filename.endswith("32.py") and _python_version < (3, 2):
        return False

    # Skip tests that require Python 3.3 at least.
    if filename.endswith("33.py") and _python_version < (3, 3):
        return False

    # Skip tests that require Python 3.4 at least.
    if filename.endswith("34.py") and _python_version < (3, 4):
        return False

    # Skip tests that require Python 3.5 at least.
    if filename.endswith("35.py") and _python_version < (3, 5):
        return False

    # Skip tests that require Python 3.6 at least.
    if filename.endswith("36.py") and _python_version < (3, 6):
        return False

    # Skip tests that require Python 3.7 at least.
    if filename.endswith("37.py") and _python_version < (3, 7):
        return False

    # Skip tests that require Python 3.8 at least.
    if filename.endswith("38.py") and _python_version < (3, 8):
        return False

    # Skip tests that require Python 3.9 at least.
    if filename.endswith("39.py") and _python_version < (3, 9):
        return False

    return True


def decideNeeds2to3(filename):
    return _python_version >= (3,) and not filename.endswith(
        ("32.py", "33.py", "34.py", "35.py", "36.py", "37.py", "38.py", "39.py")
    )


def _removeCPythonTestSuiteDir():
    # Cleanup, some tests apparently forget that.
    try:
        if os.path.isdir("@test"):
            removeDirectory("@test", ignore_errors=False)
        elif os.path.isfile("@test"):
            os.unlink("@test")
    except OSError:
        # TODO: Move this into removeDirectory maybe. Doing an external
        # call as last resort could be a good idea.

        # This seems to work for broken "lnk" files.
        if os.name == "nt":
            os.system("rmdir /S /Q @test")

        if os.path.exists("@test"):
            raise


def compareWithCPython(
    dirname, filename, extra_flags, search_mode, needs_2to3, on_error=None
):
    """Call the comparison tool. For a given directory filename.

    The search mode decides if the test case aborts on error or gets extra
    flags that are exceptions.

    """

    # Many cases to consider here, pylint: disable=too-many-branches

    if dirname is None:
        path = filename
    else:
        path = os.path.join(dirname, filename)

    # Apply 2to3 conversion if necessary.
    if needs_2to3:
        path, converted = convertUsing2to3(path)
    else:
        converted = False

    if os.getenv("NUITKA_TEST_INSTALLED", "") == "1":
        command = [
            sys.executable,
            "-m",
            "nuitka.tools.testing.compare_with_cpython",
            path,
            "silent",
        ]
    else:
        compare_with_cpython = os.path.join("..", "..", "bin", "compare_with_cpython")
        if os.path.exists(compare_with_cpython):
            command = [sys.executable, compare_with_cpython, path, "silent"]
        else:
            sys.exit("Error, cannot find Nuitka comparison runner.")

    if extra_flags is not None:
        command += extra_flags

    command += search_mode.getExtraFlags(dirname, filename)

    # Cleanup before and after test stage directory.
    _removeCPythonTestSuiteDir()

    try:
        result = subprocess.call(command)
    except KeyboardInterrupt:
        result = 2

    # Cleanup before and after test stage directory.
    _removeCPythonTestSuiteDir()

    if result != 0 and result != 2 and search_mode.abortOnFinding(dirname, filename):
        if on_error is not None:
            on_error(dirname, filename)

        search_mode.onErrorDetected("Error exit! %s" % result)

    if converted:
        os.unlink(path)

    if result == 2:
        sys.stderr.write("Interrupted, with CTRL-C\n")
        sys.exit(2)


def checkCompilesNotWithCPython(dirname, filename, search_mode):
    if dirname is None:
        path = filename
    else:
        path = os.path.join(dirname, filename)

    command = [_python_executable, "-mcompileall", path]

    try:
        result = subprocess.call(command)
    except KeyboardInterrupt:
        result = 2

    if result != 1 and result != 2 and search_mode.abortOnFinding(dirname, filename):
        search_mode.onErrorDetected("Error exit! %s" % result)


def checkSucceedsWithCPython(filename):
    command = [_python_executable, filename]

    with open(os.devnull, "w") as devnull:
        result = subprocess.call(command, stdout=devnull, stderr=subprocess.STDOUT)

    return result == 0


def hasDebugPython():
    # On Debian systems, these work.
    debug_python = os.path.join("/usr/bin/", os.environ["PYTHON"] + "-dbg")
    if os.path.exists(debug_python):
        return True

    # On Windows systems, these work.
    debug_python = os.environ["PYTHON"]
    if debug_python.lower().endswith(".exe"):
        debug_python = debug_python[:-4]
    debug_python = debug_python + "_d.exe"
    if os.path.exists(debug_python):
        return True

    # For other Python, if it's the one also executing the runner, which is
    # very probably the case, we check that. We don't check the provided
    # binary here, this could be done as well.
    if sys.executable == os.environ["PYTHON"] and hasattr(sys, "gettotalrefcount"):
        return True

    # Otherwise no.
    return False


def isExecutableCommand(command):
    path = os.environ["PATH"]

    suffixes = (".exe",) if os.name == "nt" else ("",)

    for part in path.split(os.pathsep):
        if not part:
            continue

        for suffix in suffixes:
            if os.path.isfile(os.path.join(part, command + suffix)):
                return True

    return False


def getRuntimeTraceOfLoadedFiles(path, trace_stderr=True):
    """ Returns the files loaded when executing a binary. """

    # This will make a crazy amount of work,
    # pylint: disable=I0021,too-many-branches,too-many-locals,too-many-statements

    result = []

    if os.name == "posix":
        if sys.platform == "darwin" or sys.platform.startswith("freebsd"):
            if not isExecutableCommand("dtruss"):
                sys.exit(
                    """\
Error, needs 'dtruss' on your system to scan used libraries."""
                )

            if not isExecutableCommand("sudo"):
                sys.exit(
                    """\
Error, needs 'sudo' on your system to scan used libraries."""
                )

            args = ("sudo", "dtruss", "-t", "open", path)
        else:
            if not isExecutableCommand("strace"):
                sys.exit(
                    """\
Error, needs 'strace' on your system to scan used libraries."""
                )

            args = (
                "strace",
                "-e",
                "file",
                "-s4096",  # Some paths are truncated otherwise.
                path,
            )

        # Ensure executable is not polluted with third party stuff,
        # tests may fail otherwise due to unexpected libs being loaded
        with withEnvironmentVarOverriden("LD_PRELOAD", None):
            process = subprocess.Popen(
                args=args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

            _stdout_strace, stderr_strace = process.communicate()
            exit_strace = process.returncode

            if exit_strace != 0:
                if str is not bytes:
                    stderr_strace = stderr_strace.decode("utf8")

                my_print(stderr_strace, file=sys.stderr)
                sys.exit("Failed to run strace.")

            with open(path + ".strace", "wb") as f:
                f.write(stderr_strace)

            for line in stderr_strace.split(b"\n"):
                if process.returncode != 0 and trace_stderr:
                    my_print(line)

                if not line:
                    continue

                # Don't consider files not found. The "site" module checks lots
                # of things.
                if b"ENOENT" in line:
                    continue

                if line.startswith(b"stat(") and b"S_IFDIR" in line:
                    continue

                # Allow stats on the python binary, and stuff pointing to the
                # standard library, just not uses of it. It will search there
                # for stuff.
                if (
                    line.startswith(b"lstat(")
                    or line.startswith(b"stat(")
                    or line.startswith(b"readlink(")
                ):
                    filename = line[line.find(b"(") + 2 : line.find(b", ") - 1]

                    # At least Python3.7 considers the default Python3 path.
                    if filename == b"/usr/bin/python3":
                        continue

                    if filename in (
                        b"/usr/bin/python3." + version
                        for version in (b"5", b"6", b"7", b"8", b"9")
                    ):
                        continue

                    binary_path = _python_executable
                    if str is not bytes:
                        binary_path = binary_path.encode("utf-8")

                    found = False
                    while binary_path:
                        if filename == binary_path:
                            found = True
                            break

                        if binary_path == os.path.dirname(binary_path):
                            break

                        binary_path = os.path.dirname(binary_path)

                        if filename == os.path.join(
                            binary_path,
                            b"python"
                            + (
                                "%d%d" % (_python_version[0], _python_version[1])
                            ).encode("utf8"),
                        ):
                            found = True
                            continue

                    if found:
                        continue

                result.extend(
                    os.path.abspath(match)
                    for match in re.findall(b'"(.*?)(?:\\\\0)?"', line)
                )

            if sys.version.startswith("3"):
                result = [s.decode("utf-8") for s in result]
    elif os.name == "nt":
        subprocess.call(
            (
                getDependsExePath(),
                "-c",
                "-ot%s" % path + ".depends",
                "-f1",
                "-pa1",
                "-ps1",
                "-pp0",
                "-pl1",
                path,
            )
        )

        inside = False
        for line in getFileContentByLine(path + ".depends"):
            if "| Module Dependency Tree |" in line:
                inside = True
                continue

            if not inside:
                continue

            if "| Module List |" in line:
                break

            if "]" not in line:
                continue

            # Skip missing DLLs, apparently not needed anyway.
            if "?" in line[: line.find("]")]:
                continue

            dll_filename = line[line.find("]") + 2 :].rstrip()
            dll_filename = os.path.normcase(dll_filename)

            assert os.path.isfile(dll_filename), repr(dll_filename)

            # The executable itself is of course exempted.
            if dll_filename == os.path.normcase(os.path.abspath(path)):
                continue

            result.append(dll_filename)

        os.unlink(path + ".depends")

    result = list(sorted(set(result)))

    return result


def checkRuntimeLoadedFilesForOutsideAccesses(loaded_filenames, white_list):
    # A lot of special white listing is required.
    # pylint: disable=too-many-branches,too-many-statements

    result = []

    for loaded_filename in loaded_filenames:
        loaded_filename = os.path.normpath(loaded_filename)
        loaded_filename = os.path.normcase(loaded_filename)
        loaded_basename = os.path.basename(loaded_filename)

        ok = False
        for entry in white_list:
            if loaded_filename.startswith(entry):
                ok = True

            while entry:
                old_entry = entry
                entry = os.path.dirname(entry)

                if old_entry == entry:
                    break

                if loaded_filename == entry:
                    ok = True
                    break
        if ok:
            continue

        if loaded_filename.startswith(("/etc/", "/usr/etc")):
            continue

        if loaded_filename.startswith("/proc/") or loaded_filename == "/proc":
            continue

        if loaded_filename.startswith("/dev/"):
            continue

        if loaded_filename.startswith("/tmp/"):
            continue

        if loaded_filename.startswith("/run/"):
            continue

        if loaded_filename.startswith("/sys/"):
            continue

        if loaded_filename.startswith("/usr/lib/locale/"):
            continue

        if loaded_filename.startswith("/usr/share/locale/"):
            continue

        if loaded_filename.startswith("/usr/share/X11/locale/"):
            continue

        # Themes may of course be loaded.
        if loaded_filename.startswith("/usr/share/themes"):
            continue
        if "gtk" in loaded_filename and "/engines/" in loaded_filename:
            continue

        # Terminal info files are OK too.
        if loaded_filename.startswith("/lib/terminfo/"):
            continue

        # System C libraries are to be expected.
        if loaded_basename.startswith(
            (
                "ld-linux-x86-64.so",
                "libc.so.",
                "libpthread.so.",
                "libm.so.",
                "libdl.so.",
                "libBrokenLocale.so.",
                "libSegFault.so",
                "libanl.so.",
                "libcidn.so.",
                "libcrypt.so.",
                "libmemusage.so",
                "libmvec.so.",
                "libnsl.so.",
                "libnss_compat.so.",
                "libnss_db.so.",
                "libnss_dns.so.",
                "libnss_files.so.",
                "libnss_hesiod.so.",
                "libnss_nis.so.",
                "libnss_nisplus.so.",
                "libpcprofile.so",
                "libresolv.so.",
                "librt.so.",
                "libthread_db-1.0.so",
                "libthread_db.so.",
                "libutil.so.",
            )
        ):
            continue

        # Taking these from system is harmless and desirable
        if loaded_basename.startswith(("libz.so", "libgcc_s.so")):
            continue

        # TODO: Unclear, loading gconv from filesystem of installed system
        # may be OK or not. I think it should be.
        if loaded_basename == "gconv-modules.cache":
            continue
        if "/gconv/" in loaded_filename:
            continue
        if loaded_basename.startswith("libicu"):
            continue

        # GTK may access X files.
        if loaded_basename == ".Xauthority":
            continue

        result.append(loaded_filename)

    return result


def hasModule(module_name):
    with open(os.devnull, "w") as devnull:
        result = subprocess.call(
            (os.environ["PYTHON"], "-c", "import %s" % module_name),
            stdout=devnull,
            stderr=subprocess.STDOUT,
        )

    return result == 0


m1 = {}
m2 = {}


def cleanObjRefCntMaps():
    m1.clear()
    m2.clear()

    # Warm out repr
    for x in gc.get_objects():
        try:
            str(x)
        except Exception:  # Catch all the things, pylint: disable=broad-except
            pass


def snapObjRefCntMap(before):
    # Inherently complex, pylint: disable=too-many-branches

    if before:
        m = m1
    else:
        m = m2

    m.clear()
    gc.collect()

    for x in gc.get_objects():
        # The dictionary is cyclic, and contains itself, avoid that.
        if x is m1 or x is m2:
            continue

        if type(x) is str and (x in m1 or x in m2):
            continue

        if type(x) is not str and isinstance(x, str):
            k = "str_overload_" + x.__class__.__name__ + str(x)
        elif type(x) is dict:
            if "__builtins__" in x:
                k = "<module dict %s>" % x["__name__"]
            elif "__spec__" in x and "__name__" in x:
                k = "<module dict %s>" % x["__name__"]
            else:
                k = str(x)
        elif x.__class__.__name__ == "compiled_frame":
            k = "<compiled_frame at xxx, line %d code %s" % (x.f_lineno, x.f_code)
        else:
            k = str(x)

        c = sys.getrefcount(x)

        if k in m:
            m[k] += c
        else:
            m[k] = c


orig_print = None


def disablePrinting():
    # Singleton, pylint: disable=global-statement
    global orig_print

    if orig_print is None:
        orig_print = __builtins__["print"]
        __builtins__["print"] = lambda *args, **kwargs: None


def reenablePrinting():
    # Singleton, pylint: disable=global-statement
    global orig_print

    if orig_print is not None:
        __builtins__["print"] = orig_print
        orig_print = None


_debug_python = hasattr(sys, "gettotalrefcount")


def getTotalReferenceCount():
    if _debug_python:
        gc.collect()
        return sys.gettotalrefcount()
    else:
        gc.collect()
        all_objects = gc.get_objects()

        # Sum object reference twice, once without the sum value type, then switch
        # the type, and use the type used to avoid the integers before that.
        result = 0.0
        for obj in all_objects:
            if type(obj) is float:
                continue

            result += sys.getrefcount(obj)

        result = int(result)

        for obj in all_objects:
            if type(obj) is not float:
                continue

            result += sys.getrefcount(obj)

        return result


def checkReferenceCount(checked_function, max_rounds=20, explain=False):
    # This is obviously going to be complex, pylint: disable=too-many-branches

    # Clean start conditions.
    assert sys.exc_info() == (None, None, None), sys.exc_info()

    print(checked_function.__name__ + ": ", end="")
    sys.stdout.flush()

    disablePrinting()

    # Make sure reference for these are already taken at the start.
    ref_count1 = 17
    ref_count2 = 17

    if explain:
        cleanObjRefCntMaps()

    assert max_rounds > 0

    result = False

    for count in range(max_rounds):
        if explain and count == max_rounds - 1:
            snapObjRefCntMap(before=True)

        ref_count1 = getTotalReferenceCount()

        checked_function()

        ref_count2 = getTotalReferenceCount()

        # Not allowed, but happens when bugs occur.
        assert sys.exc_info() == (None, None, None), sys.exc_info()

        if ref_count1 == ref_count2:
            result = True
            break

        if explain and count == max_rounds - 1:
            snapObjRefCntMap(before=False)

    reenablePrinting()

    if result:
        print("PASSED")
    else:
        print("FAILED", ref_count1, ref_count2, "leaked", ref_count2 - ref_count1)

        if explain:
            print("REPORT of differences:")
            assert m1
            assert m2

            for key in m1:
                if key not in m2:
                    print("*" * 80)
                    print("extra:", m1[key], key)
                elif m1[key] != m2[key]:
                    print("*" * 80)
                    print(m1[key], "->", m2[key], key)
                else:
                    pass

            for key in m2:
                if key not in m1:
                    print("*" * 80)
                    print("missing:", m2[key], key)

                    # print m1[key]

    assert sys.exc_info() == (None, None, None), sys.exc_info()

    gc.collect()
    sys.stdout.flush()

    return result


def createSearchMode():
    # Dealing with many options, pylint: disable=too-many-branches

    parser = OptionParser()

    select_group = OptionGroup(parser, "Select Tests")

    select_group.add_option(
        "--pattern",
        action="store",
        dest="pattern",
        default="",
        help="""\
Execute only tests matching the pattern. Defaults to all tests.""",
    )
    select_group.add_option(
        "--all",
        action="store_true",
        dest="all",
        default=False,
        help="""\
Execute all tests, continue execution even after failure of one.""",
    )

    parser.add_option_group(select_group)

    debug_group = OptionGroup(parser, "Test features")

    debug_group.add_option(
        "--debug",
        action="store_true",
        dest="debug",
        default=False,
        help="""\
Executing all self checks possible to find errors in Nuitka, good for test coverage.
Defaults to off.""",
    )

    debug_group.add_option(
        "--commands",
        action="store_true",
        dest="show_commands",
        default=False,
        help="""Output commands being done in output comparison.
Defaults to off.""",
    )

    parser.add_option_group(debug_group)

    options, positional_args = parser.parse_args()

    if options.debug:
        addExtendedExtraOptions("--debug")

    if options.show_commands:
        os.environ["NUITKA_TRACE_COMMANDS"] = "1"

    # Default to searching.
    mode = positional_args[0] if positional_args else "search"

    # Avoid having to use options style.
    if mode in ("search", "only"):
        if len(positional_args) >= 2 and not options.pattern:
            options.pattern = positional_args[1]

    if mode == "search":
        if options.all:
            return SearchModeAll()
        elif options.pattern:
            pattern = options.pattern.replace("/", os.path.sep)
            return SearchModeByPattern(pattern)
        else:
            return SearchModeImmediate()
    elif mode == "resume":
        return SearchModeResume(sys.modules["__main__"].__file__)
    elif mode == "only":
        if options.pattern:
            pattern = options.pattern.replace("/", os.path.sep)
            return SearchModeOnly(pattern)
        else:
            assert False
    elif mode == "coverage":
        return SearchModeCoverage()
    else:
        assert False


def reportSkip(reason, dirname, filename):
    case = os.path.join(dirname, filename)
    case = os.path.normpath(case)

    my_print("Skipped, %s (%s)." % (case, reason))


def executeReferenceChecked(prefix, names, tests_skipped, tests_stderr, explain=False):
    gc.disable()

    extract_number = lambda name: int(name.replace(prefix, ""))

    # Find the function names.
    matching_names = tuple(
        name for name in names if name.startswith(prefix) and name[-1].isdigit()
    )

    old_stderr = sys.stderr

    # Everything passed
    result = True

    for name in sorted(matching_names, key=extract_number):
        number = extract_number(name)

        # print(tests_skipped)
        if number in tests_skipped:
            my_print(name + ": SKIPPED (%s)" % tests_skipped[number])
            continue

        # Avoid unraisable output.
        try:
            if number in tests_stderr:
                sys.stderr = open(os.devnull, "wb")
        except OSError:  # Windows
            if not checkReferenceCount(names[name], explain=explain):
                result = False
        else:
            if not checkReferenceCount(names[name], explain=explain):
                result = False

            if number in tests_stderr:
                new_stderr = sys.stderr
                sys.stderr = old_stderr
                new_stderr.close()

    gc.enable()
    return result


def addToPythonPath(python_path, in_front=False):
    if type(python_path) in (tuple, list):
        python_path = os.pathsep.join(python_path)

    if python_path:
        if "PYTHONPATH" in os.environ:
            if in_front:
                os.environ["PYTHONPATH"] = (
                    python_path + os.pathsep + os.environ["PYTHONPATH"]
                )
            else:
                os.environ["PYTHONPATH"] += os.pathsep + python_path
        else:
            os.environ["PYTHONPATH"] = python_path


@contextmanager
def withPythonPathChange(python_path):
    if python_path:
        if type(python_path) not in (tuple, list):
            python_path = python_path.split(os.pathsep)

        python_path = [
            os.path.normpath(os.path.abspath(element)) for element in python_path
        ]

        python_path = os.pathsep.join(python_path)

        if "PYTHONPATH" in os.environ:
            old_path = os.environ["PYTHONPATH"]
            os.environ["PYTHONPATH"] += os.pathsep + python_path
        else:
            old_path = None
            os.environ["PYTHONPATH"] = python_path

    #     print(
    #         "Effective PYTHONPATH in %s is %r" % (
    #             sys.modules["__main__"],
    #             os.environ.get("PYTHONPATH", "")
    #         )
    #     )

    yield

    if python_path:
        if old_path is None:
            del os.environ["PYTHONPATH"]
        else:
            os.environ["PYTHONPATH"] = old_path


def addExtendedExtraOptions(*args):
    old_value = os.environ.get("NUITKA_EXTRA_OPTIONS", None)

    value = old_value

    for arg in args:
        if value is None:
            value = arg
        else:
            value += " " + arg

    os.environ["NUITKA_EXTRA_OPTIONS"] = value

    return old_value


@contextmanager
def withExtendedExtraOptions(*args):
    assert args

    old_value = addExtendedExtraOptions(*args)

    yield

    if old_value is None:
        del os.environ["NUITKA_EXTRA_OPTIONS"]
    else:
        os.environ["NUITKA_EXTRA_OPTIONS"] = old_value


def indentedCode(codes, count):
    """Indent code, used for generating test codes."""
    return "\n".join(" " * count + line if line else "" for line in codes)


def convertToPython(doctests, line_filter=None):
    """Convert give doctest string to static Python code."""
    # This is convoluted, but it just needs to work, pylint: disable=too-many-branches

    import doctest

    code = doctest.script_from_examples(doctests)

    if code.endswith("\n"):
        code += "#\n"
    else:
        assert False

    output = []
    inside = False

    def getPrintPrefixed(evaluated, line_number):
        try:
            node = ast.parse(evaluated.lstrip(), "eval")
        except SyntaxError:
            return evaluated

        if node.body[0].__class__.__name__ == "Expr":
            count = 0

            while evaluated.startswith(" " * count):
                count += 1

            if sys.version_info < (3,):
                modified = (count - 1) * " " + "print " + evaluated
                return (
                    (count - 1) * " "
                    + ("print 'Line %d'" % line_number)
                    + "\n"
                    + modified
                )
            else:
                modified = (count - 1) * " " + "print(" + evaluated + "\n)\n"
                return (
                    (count - 1) * " "
                    + ("print('Line %d'" % line_number)
                    + ")\n"
                    + modified
                )
        else:
            return evaluated

    def getTried(evaluated, line_number):
        if sys.version_info < (3,):
            return """
try:
%(evaluated)s
except Exception as __e:
    print "Occurred", type(__e), __e
""" % {
                "evaluated": indentedCode(
                    getPrintPrefixed(evaluated, line_number).split("\n"), 4
                )
            }
        else:
            return """
try:
%(evaluated)s
except Exception as __e:
    print("Occurred", type(__e), __e)
""" % {
                "evaluated": indentedCode(
                    getPrintPrefixed(evaluated, line_number).split("\n"), 4
                )
            }

    def isOpener(evaluated):
        evaluated = evaluated.lstrip()

        if evaluated == "":
            return False

        return evaluated.split()[0] in (
            "def",
            "with",
            "class",
            "for",
            "while",
            "try:",
            "except",
            "except:",
            "finally:",
            "else:",
        )

    chunk = None
    for line_number, line in enumerate(code.split("\n")):
        # print "->", inside, line

        if line_filter is not None and line_filter(line):
            continue

        if inside and line and line[0].isalnum() and not isOpener(line):
            output.append(getTried("\n".join(chunk), line_number))

            chunk = []
            inside = False

        if inside and not (line.startswith("#") and line.find("SyntaxError:") != -1):
            chunk.append(line)
        elif line.startswith("#"):
            if line.find("SyntaxError:") != -1:
                # print "Syntax error detected"

                if inside:
                    # print "Dropping chunk", chunk

                    chunk = []
                    inside = False
                else:
                    del output[-1]
        elif isOpener(line):
            inside = True
            chunk = [line]
        elif line.strip() == "":
            output.append(line)
        else:
            output.append(getTried(line, line_number))

    return "\n".join(output).rstrip() + "\n"


def compileLibraryPath(search_mode, path, stage_dir, decide, action):
    my_print("Checking standard library path:", path)

    for root, dirnames, filenames in os.walk(path):
        dirnames_to_remove = [dirname for dirname in dirnames if "-" in dirname]

        for dirname in dirnames_to_remove:
            dirnames.remove(dirname)

        dirnames.sort()

        filenames = [filename for filename in filenames if decide(root, filename)]

        for filename in sorted(filenames):
            if not search_mode.consider(root, filename):
                continue

            full_path = os.path.join(root, filename)

            my_print(full_path, ":", end=" ")
            sys.stdout.flush()

            action(stage_dir, path, full_path)


def compileLibraryTest(search_mode, stage_dir, decide, action):
    if not os.path.exists(stage_dir):
        os.makedirs(stage_dir)

    my_dirname = os.path.join(os.path.dirname(__file__), "../../..")
    my_dirname = os.path.normpath(my_dirname)

    paths = [path for path in sys.path if not path.startswith(my_dirname)]

    my_print("Using standard library paths:")
    for path in paths:
        my_print(path)

    for path in paths:
        print("Checking path:", path)
        compileLibraryPath(
            search_mode=search_mode,
            path=path,
            stage_dir=stage_dir,
            decide=decide,
            action=action,
        )

    search_mode.finish()


def run_async(coro):
    """ Execute a coroutine until it's done. """

    values = []
    result = None
    while True:
        try:
            values.append(coro.send(None))
        except StopIteration as ex:
            result = ex.args[0] if ex.args else None
            break
    return values, result


def async_iterate(g):
    """ Execute async generator until it's done. """

    # Test code for Python3, catches all kinds of exceptions.
    # pylint: disable=broad-except

    # Also Python3 only, pylint: disable=I0021,undefined-variable

    res = []
    while True:
        try:
            g.__anext__().__next__()
        except StopAsyncIteration:
            res.append("STOP")
            break
        except StopIteration as ex:
            if ex.args:
                res.append("ex arg %s" % ex.args[0])
            else:
                res.append("EMPTY StopIteration")
                break
        except Exception as ex:
            res.append(str(type(ex)))

    return res


def getTestingCacheDir():
    cache_dir = getCacheDir()

    result = os.path.join(cache_dir, "tests_state")
    makePath(result)
    return result


def getTestingCPythonOutputsCacheDir():
    cache_dir = getCacheDir()

    result = os.path.join(
        cache_dir, "cpython_outputs", os.environ.get("NUITKA_TEST_SUITE", "")
    )

    makePath(result)
    return result


@contextmanager
def withDirectoryChange(path, allow_none=False):
    if path is not None or not allow_none:
        old_cwd = os.getcwd()
        os.chdir(path)

    yield

    if path is not None or not allow_none:
        os.chdir(old_cwd)


def scanDirectoryForTestCases(dirname, template_context=None):
    filenames = os.listdir(dirname)

    filenames = [
        filename
        for filename in filenames
        if (filename.endswith(".py") and not filename + ".j2" in filenames)
        or filename.endswith(".j2")
    ]

    # Jinja2 environment is optional.
    env = None

    for filename in sorted(filenames):
        if not decideFilenameVersionSkip(filename):
            continue

        if filename.endswith(".j2"):
            # Needs to be a dictionary with template arguments.
            assert template_context is not None

            if env is None:
                import jinja2

                env = jinja2.Environment(
                    loader=jinja2.FileSystemLoader("."),
                    trim_blocks=True,
                    lstrip_blocks=True,
                )
                env.undefined = jinja2.StrictUndefined

            template = env.get_template(filename)

            code = template.render(name=template.name, **template_context)

            filename = filename[:-3]
            with open(filename, "w") as output:
                output.write(
                    "'''Automatically generated test, not part of releases or git.\n\n'''\n"
                )

                output.write(code)

        yield filename


def setupCacheHashSalt(test_code_path):
    assert os.path.exists(test_code_path)

    if os.path.exists(os.path.join(test_code_path, ".git")):
        git_cmd = ["git", "ls-tree", "-r", "HEAD", test_code_path]

        process = subprocess.Popen(
            args=git_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        stdout_git, stderr_git = process.communicate()
        assert process.returncode == 0, stderr_git

        salt_value = hashlib.md5(stdout_git)
    else:
        salt_value = hashlib.md5()

        for filename in getFileList(test_code_path):
            if filename.endswith(".py"):
                salt_value.update(getFileContents(filename, mode="rb"))

    os.environ["NUITKA_HASH_SALT"] = salt_value.hexdigest()


def someGenerator():
    yield 1
    yield 2
    yield 3


def someGeneratorRaising():
    yield 1
    raise TypeError(2)


test_logger = OurLogger("", base_style="blue")
