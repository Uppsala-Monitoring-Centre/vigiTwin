"""The name this package is distributed under, and its installed version.

One module so that one string exists. Every lookup site swallows
``PackageNotFoundError`` and returns ``None``, which is correct for a source
checkout and silent for a typo: a wrong name here yields a manifest with
``dev_package.version: null`` and no error anywhere. On a wheel install
``dev_git_commit`` is ``None`` by design (see :mod:`vigitwin.run_manifest._git`),
so a wrong name there leaves a manifest with no provenance at all. Before 1.3.0
the name was spelled out at nine separate call sites.

This module deliberately imports nothing from the package, so it cannot take
part in an import cycle — the subpackage ``__init__`` files are eager
re-export hubs, and every one of them would be a cycle from here.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

#: The name this package is distributed and installed under, as it appears on
#: PyPI and in ``pip install``. Emitted as ``dev_package.name`` in every
#: manifest. Was ``llm-dup-dev`` through 1.2.7.
DISTRIBUTION_NAME = "vigitwin"


def installed_version() -> str | None:
    """This distribution's version, or ``None`` if it is not installed."""
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None
