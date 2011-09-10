"""
    borealis.backends.aur
    ~~~~~~~~~~~~~~~~~~~~~

    Yet another frontend for the Archlinux User Repository.

    :copyright: (c) 2011 David Gidwani
    :license: New BSD, see LICENSE
"""
import getpass
import json
import pexpect
import urllib2
import urlparse
import sys
import tarfile

from ufl.core.structures.dict import AttrDict
from ufl.io.fs import path
from ufl.io.shell import execute, STDOUT, PIPE
from borealis import log
from borealis.backends import CAPABS, DeferToNextBackend, \
                              PackageManagementBackend
from borealis.backends.pacman.backend import ALPMPackage
from borealis.objects import Package, PackageMetadata
from borealis.util import deptest, download


__all__ = ["AURBackend"]


CATEGORIES = (
    "none",
    "daemons",
    "devel",
    "editors",
    "emulators",
    "games",
    "gnome",
    "i18n",
    "kde",
    "lib",
    "modules",
    "multimedia",
    "network",
    "office",
    "science",
    "system",
    "x11",
    "xfce",
    "kernels"
)


class AURBackend(PackageManagementBackend):

    capabs = CAPABS.ALL ^ (CAPABS.SEARCH_REGEX
                           | CAPABS.SEARCH_PYTHONREGEX
                           | CAPABS.REMOVE
                           | CAPABS.QUERY)
    config = AttrDict({
        "base_url": "http://aur.archlinux.org/",
        "rpc_url": "http://aur.archlinux.org/rpc.php?type={type}&arg={arg}",
        "staging_area": "$HOME/Build/",
        "prefix_output_fmt": "aur:{category}"
    })

    def initialize(self):
        super(AURBackend, self).initialize()
        staging_area = path(self.config.staging_area)
        if not staging_area.exists:
            log.info("creating staging area: " + staging_area)
            staging_area.create()

    def query(self, package=None):
        if not package:
            raise DeferToNextBackend
        data = self.rpc("info", package)
        if data["type"] != "info":
            return
        return ALPMPackage(metadata=self.sanitize_json_metadata(data["results"]))

    def remove(self, package):
        raise NotImplementedError

    def search(self, *terms):
        data = self.rpc("search", " ".join(terms))
        if data[u"type"] == u"search":
            for result in data["results"]:
                yield ALPMPackage(metadata=self.sanitize_json_metadata(result))

    def sync(self, package):
        package = self.query(package)
        if not package:
            raise DeferToNextBackend
        package_dir = path(self.config.staging_area).join(package.name)
        if not package_dir.exists:
            package_dir.create()
        filename = package_dir.join(path(package.metadata.urlpath).name)
        if not filename.exists:
            download(urlparse.urljoin(self.config.base_url,
                                      package.metadata.urlpath), filename)
        if not package_dir.join(package.name).exists:
            with tarfile.open(filename.absolute) as tar:
                tar.extractall(package_dir)
                tar.close()
        srcdir = package_dir.join(package.name)
        if not package.depends:
            package.metadata = PackageMetadata.from_pkgbuild(
                srcdir.join("PKGBUILD"), name=package.name)
        unmet_deps = deptest(*package.depends)
        for dependency in unmet_deps:
            log.info("resolving dependency: " + str(dependency))
            self.frontend.dispatch(CAPABS.SYNC, [dependency.name])
        child = pexpect.spawn("makepkg -si", cwd=srcdir)
        if child.expect("Password: ") == 0:
            child.sendline(getpass.getpass())
        child.logfile = sys.stdout
        index = child.expect([
            r".*Failed to install built package\(s\).",
            r".*Finished making: (.+?) ([^ ]+)",
            r".*\[Y/n\]",
        ])
        if index == 2:
            child.setecho(False)
            child.sendline("y")
        child.expect(pexpect.EOF)

    def upgrade(self):
        raise NotImplementedError

    def rpc(self, type_, arg):
        return json.load(urllib2.urlopen(self.config["rpc_url"]\
                                         .format(type=type_, arg=arg)))

    def sanitize_json_metadata(self, data):
        if isinstance(data, list):
            for result in data:
                sanitize_json(result)
        else:
            if isinstance(data, (str, unicode)):
                data = json.loads(data)
            metadata = {}
            for key, value in data.items():
                key = key.lower()
                if key == "version":
                    metadata["pkgver"], metadata["pkgrel"] = value.split("-")
                    continue
                elif key == "categoryid":
                    key = "category"
                elif key == "url":
                    key = "site"
                metadata[key] = value
            metadata["category"] = CATEGORIES[int(metadata["category"])-1]
            metadata["repo"] = self.config.get("prefix_output_fmt", "")\
                               .format(**metadata)
            return PackageMetadata(**metadata)