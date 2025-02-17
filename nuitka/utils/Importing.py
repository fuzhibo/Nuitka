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
""" Helper to import a file as a module.

Used for Nuitka plugins and for test code.
"""

import os
import sys

from nuitka.PythonVersions import python_version


def _importFilePy3NewWay(filename):
    """Import a file for Python versions 3.5+."""
    import importlib.util  # pylint: disable=I0021,import-error,no-name-in-module

    spec = importlib.util.spec_from_file_location(
        os.path.basename(filename).split(".")[0], filename
    )
    user_plugin_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(user_plugin_module)
    return user_plugin_module


def _importFilePy3OldWay(filename):
    """Import a file for Python versions before 3.5."""
    from importlib.machinery import (  # pylint: disable=I0021,import-error,no-name-in-module
        SourceFileLoader,
    )

    # pylint: disable=I0021,deprecated-method
    return SourceFileLoader(filename, filename).load_module(filename)


def importFilePy2(filename):
    """Import a file for Python version 2."""
    import imp

    basename = os.path.splitext(os.path.basename(filename))[0]
    return imp.load_source(basename, filename)


def importFileAsModule(filename):
    """Import Python module given as a file name.

    Notes:
        Provides a Python version independent way to import any script files.

    Args:
        filename: complete path of a Python script

    Returns:
        Imported Python module with code from the filename.
    """
    if python_version < 0x300:
        return importFilePy2(filename)
    elif python_version < 0x350:
        return _importFilePy3OldWay(filename)
    else:
        return _importFilePy3NewWay(filename)


_shared_library_suffixes = None


def getSharedLibrarySuffixes():
    # Using global here, as this is for caching only
    # pylint: disable=global-statement
    global _shared_library_suffixes

    if _shared_library_suffixes is None:
        if python_version < 0x300:
            import imp

            _shared_library_suffixes = []

            for suffix, _mode, module_type in imp.get_suffixes():
                if module_type == imp.C_EXTENSION:
                    _shared_library_suffixes.append(suffix)
        else:
            import importlib.machinery  # pylint: disable=I0021,import-error,no-name-in-module

            _shared_library_suffixes = importlib.machinery.EXTENSION_SUFFIXES

        _shared_library_suffixes = tuple(_shared_library_suffixes)

    return _shared_library_suffixes


def getSharedLibrarySuffix(preferred):
    if preferred and python_version >= 0x300:
        return getSharedLibrarySuffixes()[0]

    result = None

    for suffix in getSharedLibrarySuffixes():
        if result is None or len(suffix) < len(result):
            result = suffix

    return result


def importFromInlineCopy(module_name, must_exist):
    """Import a module from the inline copy stage."""

    # May already be loaded
    if module_name in sys.modules:
        return sys.modules[module_name]

    # Temporarily add the inline path of the module to the import path.
    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(__file__), "..", "build", "inline_copy", module_name
        ),
    )

    # Handle case without inline copy too.
    try:
        return __import__(module_name)
    except ImportError:
        if not must_exist:
            return None

        sys.exit("Error, excepted inline copy of %r is not there." % module_name)
    finally:
        # Do not forget to remove it from sys.path again.
        del sys.path[0]
