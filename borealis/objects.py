"""
    borealis.objects
    ~~~~~~~~~~~~~~~~

    Abstract representations of package management entities.

    :copyright: (c) 2011 David Gidwani
    :license: New BSD, see LICENSE
"""
import operator
import os
import re
import sys

from distutils.version import LooseVersion
from ufl.core.structures.dict import AttrDict, DictKeySynonymMixin
from ufl.core.structures.enum import MultiKeyEnumeration
from ufl.core.structures.list import flatten
from ufl.io.shell import execute, PIPE


class MetadataAttrDict(DictKeySynonymMixin, AttrDict):

    def __setitem__(self, key, value):
        if key == "_synonym_map":
            object.__setattr__(self, key, value)
        else:
            super(MetadataAttrDict, self).__setitem__(key, value)

    def __getitem__(self, key):
        try:
            value = AttrDict.__getitem__(self, key)
        except KeyError:
            value = self[self._synonym_map[key]]
        if value is None:
            try:
                value = self[self._synonym_map[key]]
            except KeyError:
                pass
        return value


class PackageMetadata(MetadataAttrDict):
    """Represents a single package's attributes."""
    __slots__ = [
        "arch",
        "backup",
        "builddate",
        "category",         # AUR
        "comments",         # AUR
        "conflicts",
        "depends",
        "description",      # => "desc"
        "epoch",
        "groups",
        "installdate",
        "lastupdate",
        "license",
        "location",
        "maintainer",
        "makedepends",
        "md5sums",
        "name",
        "noextract",
        "number",
        "options",
        "outofdate",        # AUR
        "packager",
        "pkgrel",
        "pkgsite",          # => "url"
        "pkgver",
        "provides",
        "repo",
        "require",
        "required_by",
        "sha1sums",
        "sha256sums",
        "sha384sums",
        "sha512sums",
        "source",
        "submitted",
        "tarsize",          # => "size"
        "targz",
        "version",
        "votes",            # AUR
    ]
    __synonyms__ = [
        ("description", "desc"),
        ("name", "pkgname"),
        ("url", "pkgsite"),
        ("repository", "repo"),
        ("size", "tarsize")
    ]
    default_meta = {
        "description": "",
        "depends": [],
        "makedepends": [],
        "required_by": []
    }
    required_meta = [
        "name",
        # "description",
        # "license",
        "pkgrel",
        "pkgver",
        # "tarsize",
        # "targz"
    ]

    def __init__(self, lazy=False, strict=False, **kwargs):
        """Instantiate a new :class:`PackageMetadata` object.

        :param lazy: If not set, raises an exception if a required field is
                     missing. See :attr:`.required_meta` for required metadata.
        :param strict: If **True**, only stores recognized keys instead of all
                       arguments passed to the constructor.
        """
        super(PackageMetadata, self).__init__()
        for slot in self.__slots__:
            self[slot] = self.default_meta.get(slot, None)
        for key in kwargs:
            if strict and key not in self.__slots__:
                raise ValueError("unhandled package attribute: " + key)
            self[key] = kwargs[key]

        if self.version:
            self.version = Version(self.version)
            self.pkgver, self.pkgrel = self.version.epoch, self.version.release
        elif self.pkgver and self.pkgrel:
            self.version = Version("-".join([self.pkgver, self.pkgrel]))

        if not lazy:
            for key in self.required_meta:
                if self.get(key, None) is None:
                    raise ValueError("required metadata is missing: " + key)
        elif "name" not in kwargs:
            raise ValueError("package meta must at least have a name")

        if not filter(lambda o: isinstance(o, Dependency), self.depends):
            self.depends = map(Dependency, self.depends)

    @property
    def checksums(self):
        return filter(None, [getattr(self, alg + "sums") for alg in
                             ("sha1", "sha256", "sha384", "sha512", "md5")])[0]

    @property
    def size(self):
        return self.tarsize

    @classmethod
    def from_pkgbuild(cls, pkgbuild_path, source_timeout=5, **kwargs):
        """Create a :class:`PackageMetadata` object from a **PKGBUILD**.

        This method simply uses bash to do what bash was created to do: and that
        is parse bash scripts. Parsing a PKGBUILD in any language other than
        bash *will* inevitably break on some packages, as many packagers and
        maintainers use parameter expansion, command substitution, and in some
        cases, even conditionally assign package-critical metadata.

        Unfortunately, using bash to source a PKGBUILD has been reported to
        hang on certain 3rd party (i.e., AUR) packages, and blindly sourcing
        an untrusted PKGBUILD is a security hazard anyway. This method takes
        a couple of precautions in light of this:

            1. Fakeroot is used for each call to bash.
            2. The bash subprocess is killed if runtime exceeds
               **source_timeout** (default is 5 seconds).

        Any additional keyword arguments passed will override any variables with
        the same name in the PKGBUILD.
        """
        def filter_variables(line):
            return len(line.strip()) == len(line) and "=" in line

        def parse_array(string):
            array_item_re = "\[\d+\]=\"(.+?)\""
            return re.findall(array_item_re, string) or string

        # Not really necessary to use fakeroot on the first call to bash, but
        # it can't hurt, can it? On my machine, it doesn't slow things down
        # all that much.
        out1 = execute("fakeroot bash -c set", stdout=PIPE).stdout
        out2 = execute("fakeroot bash -c '. {0}; set'".format(pkgbuild_path),
                       stdout=PIPE, timeout=source_timeout).stdout
        dicts = map(lambda out: dict(map(lambda line: line.split("=", 1),
                                       filter(filter_variables,
                                              out.strip().split("\n")))),
                    (out2, out1))
        result = {k: parse_array(v) for k, v in dicts[0].items() if k in
                  set.difference(*map(set, dicts))}

        if "PIPESTATUS" in result:
            result.pop("PIPESTATUS")
        result.update(kwargs)
        return cls(**result)


    @classmethod
    def _from_pkgbuild(cls, pkgbuild):
        """Create a :class:`PackageMetadata` object from a **PKGBUILD**.

        **This method is included for personal reference only.** It doesn't
        support all of bash's parameter expansion syntax, and breaks on a large
        percentage of PKGBUILDs from the AUR.

        If you're reading this and absolutely *must* have a Python parser for
        PKGBUILDs, take a look at the `parched`_ library.

        .. _parched: https://github.com/sebnow/parched

        :param pkgbuild: A string containing the contents of the PKGBUILD.
        """
        variable_re = (r"^([^#\s]+?)=(?:\(([^\)]*)\)|"
                       "['\"]?(.+?)['\"]?(?:\s+?#.+?)?$)")
        metadata = {}

        def clean(string):
            try:
                quote_re = r"\$?(?:'([^']+?)'|\"([^\"]+)\")"
                quote_match = re.search(quote_re, string)
                return quote_match.group(0) or quote_match.group(1)
            except AttributeError:
                # `str.find(...) or str.find(...)` won't work here since one of
                # the values may be -1, in which case that value is used rather
                # than the first positive integer.
                quote_pos = min(filter(lambda i: i != -1,
                                       [string.find("'"), string.find('"')])
                                or [-1])
                string = string.strip("\r\n '\"")
                # Prevent from splitting on a comment character within a legal
                # string.
                if "#" in string and string.find("#") > quote_pos:
                    string = string.rsplit("#", 1)[0].strip()
                if quote_pos == -1 and " " in string:
                    return string.split(" ")
                return string

        for key, mult, single in re.findall(variable_re, pkgbuild, re.M):
            if mult:
                value = flatten(map(lambda s: clean(s),
                                    mult.split("\n") if "\n" in mult else
                                    filter(str.strip, re.split("['\"]", mult))))
                # BUG: quotes left over on cleaned strings
                value = map(lambda s: s.strip("'\""), value)
            else:
                value = clean(single)
            if " " in key:
                continue
            if key == "url":
                key = "pkgsite"
            elif key == "pkgname":
                key = "name"

            if key not in metadata:
                metadata[key] = []

            metadata[key].append(value)

        current_index = None
        expand_re = r"(\$\{?(\w+)(\[@(:\d+(:\d+)?)?\])?\}?)"

        def evaluate(match):
            raw, name, splice, offset, length = match.groups()
            value = metadata.get(name, raw)
            if splice:
                value = value[offset:length]
            else:
                if len(value) == 1 and value[0] != raw:
                    if re.search(expand_re, value[0]):
                        value = re.sub(expand_re, evaluate, value[0])
                    else:
                        value = value[0]
                    metadata[name] = value
            return value

        for key, values in metadata.items():
            for index, value in enumerate(values):
                current_index = index
                values = map(lambda string: re.sub(expand_re, evaluate, string),
                             value if isinstance(value, list) else [value])
            metadata[key] = values

        for key in ("url", "pkgname"):
            if key in metadata:
                del metadata[key]

        return cls(**metadata)


class Package(object):
    """Represents a package. (Who knew?)"""

    __slots__ = ["lazy", "metadata"]

    def __init__(self, lazy=False, metadata=None, **kwargs):
        self.lazy = lazy
        if isinstance(metadata, PackageMetadata):
            self.metadata = metadata
            self.metadata.update(kwargs)
        else:
            self.metadata = PackageMetadata(lazy, **kwargs)

    @property
    def depends(self):
        return map(lambda package: Dependency.from_depstring(package),
                   self.metadata.depends)

    @property
    def installed(self):
        raise NotImplementedError

    @property
    def installed_version(self):
        raise NotImplementedError

    def __getattr__(self, key):
        try:
            return self.metadata[key]
        except KeyError:
            return object.__getattribute__(self, key)

    # <http://mail.python.org/pipermail/python-dev/2002-December/030992.html>
    def __getstate__(self):
        state = getattr(self, '__dict__', {}).copy()
        for obj in type(self).mro():
            for name in getattr(obj,'__slots__',()):
                if hasattr(self, name):
                    state[name] = getattr(self, name)
        return state

    def __setstate__(self, state):
        for key,value in state.items():
            setattr(self, key, value)

    def __repr__(self):
        fmt = "<Package('{0}')>"
        if not self.lazy:
            fmt = fmt.format("{name} {pkgver}-{pkgrel}")
        else:
            fmt = fmt.format("{name}")
        return fmt.format(**self.metadata)


class Dependency(Package):
    """A dependency is a :class:`Package` with a **target** version."""
    __slots__ = ["target", "sign"]
    signs = MultiKeyEnumeration(
        ("gt", "greater_than"),
        ("lt", "less_than"),
        ("eq", "equals"),
        ("gte", "greater_than_or_equals"),
        ("lte", "less_than_or_equals")
    )

    def __init__(self, name, sign=signs.EQUALS, target=None):
        """Instantiate a new :class:`Dependency` object.

        :param name: The name of the package.
        :param target: A dependency string of the form "[<>]?=(version)"
        """
        self.target = target
        self.sign = sign
        super(Dependency, self).__init__(lazy=True, name=name)

    def is_satisfied_by(self, package):
        return package.name == self.name and {
            self.signs.GT: operator.gt,
            self.signs.LT: operator.lt,
            self.signs.EQ: operator.eq,
            self.signs.GTE: operator.ge,
            self.signs.LTE: operator.le
        }[self.sign](package.version, self.target)

    @classmethod
    def from_depstring(cls, depstring):
        """Parse a package dependency string (e.g., foo>=1.2-1)."""
        ops = [">", "<", "=", ">=", "<="]
        pos = [pos for pos in map(depstring.find, ops) if pos != -1]
        if pos:
            name, rest = depstring[:pos[-1]], depstring[pos[-1]:]
            sign = rest[:-len(rest.lstrip("<>="))]
            target = Version(rest[len(sign):])
            name = depstring[:pos[-1]]
            sign = cls.signs.get(dict(zip(ops,
                ("gt", "lt", "eq", "gte", "lte")))[sign])
        else:
            target = sign = None
            name = depstring
        return cls(name, sign, target)

    def __str__(self):
        return self.name + ({
            self.signs.GT: ">",
            self.signs.LT: "<",
            self.signs.EQ: "=",
            self.signs.GTE: ">=",
            self.signs.LTE: "<="
        }[self.sign] + str(self.target) if self.target else "")

    def __repr__(self):
        return "<Dependency('{}')>".format(str(self))


class Version(LooseVersion):
    """Represents a package version string.

    :todo: Provide a more robust implementation similar to **libalpm**'s
           *vercmp*. Right now, this class is just
           :class:`~distutils.version.LooseVersion` with RPM-style epoch and
           release comprehension.
    """

    __slots__ = ["epoch", "version", "release", "vstring"]
    #: [epoch:]version-<release>
    version_re = r"(?:([^:]+)(?::))?(?:([^\-]+))(?:(?:-)([\do]+))?"

    def __init__(self, string):
        match = re.match(self.version_re, string)
        if not match:
            raise Exception("invalid version string format")
        LooseVersion.__init__(self, string)
        self.epoch = match.group(1) or 0
        self.version = match.group(2)
        # someone please inform foobnix's maintainer that the letter "o" should
        # never, ever, ever, *ever* be used to represent zero.
        if match.group(3) == "o":
            self.release = 0
        else:
            self.release = int(match.group(3)) if match.group(3) else 1

    def __str__(self):
        return "{}:{}-{}".format(self.epoch, self.version, self.release)

    def __repr__(self):
        return "<Version('{}')>".format(self.__str__())