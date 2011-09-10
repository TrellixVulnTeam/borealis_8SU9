import os
import nose
import pwd
import sys
import urllib2
import unittest

from ...objects import Package, PackageMetadata
from .backend import AURBackend
from ufl.io.fs import path
from ufl.io.shell import execute


tmp = path("/tmp/borealis-" + pwd.getpwuid(os.getuid())[0] + "/aur3/")


def download(url, location):
    response = urllib2.urlopen(url)
    with path(location).open("wb") as f:
        f.write(response.read())


def parse_meta(meta_file):
    try:
        metadata = AURBackend.sanitize_json_metadata(meta_file.read())
    except ValueError:
        metadata = None
    assert metadata, meta_file


def parse_pkgbuild(pkgbuild):
    try:
        metadata = PackageMetadata.from_pkgbuild(pkgbuild.read())
    except ValueError:
        metadata = None
    assert metadata


class TestParseMetadata(object):

    _multiprocess_can_split_ = False
    extract_path = tmp.join("all_metadata")
    tar_filename = "all_jsons.tar.gz"

    @classmethod
    def setup_class(cls):
        if not cls.extract_path.exists:
            cls.extract_path.create()
            print("downloading AUR metadata to {0}".format(cls.extract_path))
            download("http://aur3.org/" + cls.tar_filename,
                     cls.extract_path.join(cls.tar_filename))
            print("extracting")
            # using :mod:`tarfile` fails with symlink errors (???)
            execute("tar xf " + cls.tar_filename, cwd=cls.extract_path.absolute)
        else:
            print("using existing metadata from {0}".format(cls.extract_path))

    def test_parse(self):
        for meta in self.extract_path.join("rpc"):
            yield parse_meta, meta


class TestParsePkgbuild(TestParseMetadata):

    extract_path = tmp.join("all_pkgbuilds")
    tar_filename = "all_pkgbuilds.tar.gz"

    def test_parse(self):
        for package in self.extract_path.join("mirror"):
            yield parse_pkgbuild, package.join("PKGBUILD")