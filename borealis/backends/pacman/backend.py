"""
    borealis.backends.pacman
    ~~~~~~~~~~~~~~~~~~~~~~~~

    A simplistic *pacman* wrapper backend.

    :copyright: (c) 2011 David Gidwani
    :license: New BSD, see LICENSE
"""
import ConfigParser
import itertools
import re
import sys
import tarfile

from borealis import log
from borealis.backends import DeferToNextBackend, ACTIONS, CAPABS, \
                              PackageManagementBackend
from borealis.objects import Package, PackageMetadata, Version
from borealis.util import sudo
from ufl.core.structures.dict import AttrDict
from ufl.io.fs import path
from ufl.io.shell import execute, PIPE


__all__ = ["ALPMBackend", "PacmanProxyBackend"]


ALPM_DB_PATH = path("/var/lib/pacman")


class ALPMPackage(Package):
    """Represents an Archlinux package. Provides additional, ALPM specific
    metadata such as :attr:`installed` and :attr:`installed_version`."""

    def __init__(self, *args, **kwargs):
        super(ALPMPackage, self).__init__(*args, **kwargs)
        self._installed_dir = ALPM_DB_PATH.join("local").glob(self.name +
                                                              "-[0-9]*")

    @property
    def installed(self):
        return self._installed_dir != []

    @property
    def installed_version(self):
        if self.installed:
            return Version(self._installed_dir[0].name[len(self.name)+1:])


class ALPMBackend(PackageManagementBackend):
    """A feature incomplete backend for interfacing with ALPM databases.

    This backend only implements the **search** and **query** capabilities, the
    rest are out of the scope of this class considering:

        1. pyALPM does not support Python 2.x
        2. using :mod:`ctypes` to interface with libalpm manually is essentially
           pointless as one can just use :mod:`subprocess` and use pacman as a
           proxy instead, with virtually no performance loss.

    That said, the only really usable capability that this class provides is
    *query*, as *search* has to extract and parse all package metadata in the
    sync db and then match the name and description against a regular
    expression, which is obviously very expensive.
    """

    capabs = CAPABS.ALL ^ (CAPABS.SEARCH_REGEX
                           | CAPABS.SYNC
                           | CAPABS.REMOVE
                           | CAPABS.UPGRADE)
    pacman_conf = path("/etc/pacman.conf")

    def initialize(self):
        super(ALPMBackend, self).initialize()

    @property
    def enabled_repositories(self):
        return filter(lambda s: s != "options", self.pacman_config.sections())

    def load_pacman_config(self):
        self.pacman_config = ConfigParser.SafeConfigParser()
        self.pacman_config.read(self.pacman_conf)

    def query(self, package=None):
        if not package:
            for directory in ALPM_DB_PATH.join("local").directories:
                metadata = self.parse_desc(directory.join("desc").read())
                metadata.name = directory.name.rsplit("-", 2)[0]
                yield ALPMPackage(metadata=metadata, lazy=True)
        else:
            packages = ALPM_DB_PATH.join("local").find_all(package)
            for package in packages:
                desc = packages.join("desc")
                yield ALPMPackage(metadata=self.parse_desc(desc.read()))

    def remove(self, package):
        raise NotImplementedError

    def search(self, *terms):
        """Don't use this. This is painfully slow."""
        desc_re = re.compile(r"%DESC%\n.*{0}.*$".format(".*".join(terms)),
                             re.IGNORECASE | re.MULTILINE)
        for db in ALPM_DB_PATH.join("sync").glob("*.db"):
            if db.name[:-3] not in self.enabled_repositories:
                continue
            seen = []
            try:
                tar = tarfile.open(db, "r")
            except tarfile.ReadError:
                log.error("could not open db file: {0}".format(db))
                continue
            for member in tar.getmembers():
                member_path = path(member.name)
                if member_path.name != "desc":
                    continue
                package = member_path.parent
                if package.name in seen:
                    continue
                desc = tar.extractfile(member).read()
                if (len(terms) == 1 and
                    re.match(terms[0], package.name, re.IGNORECASE)):
                    seen.append(package.name)
                else:
                    if re.search(desc_re, desc):
                        seen.append(package.name)
                    else:
                        continue
                metadata = self.parse_desc(desc)
                yield ALPMPackage(metadata=metadata)

    def sync(self, package):
        raise NotImplementedError

    def upgrade(self):
        raise NotImplementedError

    @classmethod
    def parse_desc(cls, desc):
        metadata = {}
        current_var = None
        for line in desc.split("\n"):
            if not line.strip():
                continue
            if line.startswith("%") and line.endswith("%"):
                current_var = line[1:-1].lower()
                if current_var == "size":
                    current_var = "tarsize"
                elif current_var == "version":
                    current_var = "pkgver"
                metadata[current_var] = []
            else:
                if current_var == "pkgver":
                    pkgver, pkgrel = line.split("-")
                    metadata[current_var].append(pkgver)
                    metadata["pkgrel"] = pkgrel
                else:
                    metadata[current_var].append(line)
        metadata = dict(map(lambda (k, v): (k, v[0] if len(v) == 1 else v),
                            metadata.items()))
        return PackageMetadata(**metadata)


class PacmanProxyBackend(ALPMBackend):

    capabs = CAPABS.ALL ^ CAPABS.SEARCH_PYTHONREGEX
    config = AttrDict({
        "deptest_before_sync": True,
        "parse_output": True,
        "pacman_binary": "/usr/bin/pacman",
        "parse_desc_on_query": False
    })
    output_re = r"(?P<repo>[\w-]+)/(?P<name>[\w-]+) (?P<version>[\d:.-]+)"\
                 "(?: \((?P<group>.+?)\))?"\
                 "(?: \[[^\]]+\])?\n {4}(?P<description>.*)"


    def query(self, package=None):
        arguments, remaining = self.frontend.parsed_args
        proc = self.pass_through(CAPABS.QUERY, args=(package or "",),
                                 stdout=PIPE)
        for line in proc.stdout.split("\n"):
            if not line:
                continue
            name, version = line.split()
            if self.config.parse_desc_on_query:
                args = {
                    "metadata": self.parse_desc(ALPM_DB_PATH.join(
                        "local/{}-{}/desc".format(name, version)).read()),
                    "name": name
                }
            else:
                args = {
                    "metadata": PackageMetadata(lazy=True, name=name,
                                                version=version)
                }
            yield ALPMPackage(**args)

    def search(self, *terms):
        stdout = PIPE if self.config.parse_output else None
        proc = self.pass_through(CAPABS.SEARCH, stdout=stdout, args=terms)
        if proc.returncode != 0:
            raise DeferToNextBackend
        if self.config.parse_output:
            metadata = re.finditer(self.output_re, proc.stdout, re.M)
            return itertools.imap(lambda m: ALPMPackage(**m.groupdict()),
                                  metadata)

    def sync(self, package):
        if (self.config.deptest_before_sync and
            execute("pacman -T " + package, stdout=PIPE).returncode != 0):
                raise DeferToNextBackend
        proc = self.pass_through(CAPABS.SYNC, as_root=True,
                                 args=(package,), stdout=None, stderr=PIPE)
        if "target not found" not in proc.stderr:
            sys.stderr.write(proc.stderr)
        if proc.returncode != 0:
            raise DeferToNextBackend

    def pass_through(self, action=None, as_root=False, args=(), **kwargs):
        arguments, remaining = self.frontend.parsed_args
        switch = self.frontend.get_switch_for_action(action) if action else ""
        method = sudo if as_root else execute
        return method("pacman {} {} {}".format(switch, " ".join(args),
                                               " ".join(remaining)), **kwargs)