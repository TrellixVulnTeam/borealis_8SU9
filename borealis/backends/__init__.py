"""
    borealis.backends
    ~~~~~~~~~~~~~~~~~

    Capability-oriented interfaces to a package management system.

    While **actions** represent package management operations such as *search*
    and *sync*, **capabilities** refer to a broader range of features that a
    particular backend could have. An action itself is also a capability,
    allowing for flexibility when writing backends that only support limited
    operations, and then defer to other backends for anything else.

    :copyright: (c) 2011 David Gidwani
    :license: New BSD, see LICENSE
"""
import abc

from logbook import Logger
from ufl.core.structures.enum import Enumeration, BitmaskEnumeration


__all__ = [
    "ACTIONS",
    "CAPABS",
    "DeferToNextBackend",
    "PackageManagementBackend"
]


# TODO: Use an enumeration for ACTIONs, and add an association dict for
# command-line options and backend capabilities.
# TODO: Sync and search are treated as individual actions or capabilities. They
# should be merged.
ACTIONS = ("QUERY", "REMOVE", "SYNC", "SEARCH", "UPGRADE")
CAPABS = CAPABILITIES = BitmaskEnumeration(
    "SEARCH_REGEX",
    "SEARCH_PYTHONREGEX",
    "SEARCH_DESCRIPTION",
    "SEARCH_NAME",
    *ACTIONS
)


class classproperty(object):

    def __init__(self, function):
        self._function = function

    def __get__(self, instance, owner):
        return self._function(owner)


class DeferToNextBackend(Exception):
    pass


class PackageManagementBackend(object):
    """An abstract base class that defines the basic interface to a PMS.

    :attribute capabs: A bitmask indicating supported operations.
    :attribute config: Used to populate the configuration file(s) with default
                       options. It is updated immediately after instantiation to
                       hold current values from the configuration file.
    """
    __metaclass__ = abc.ABCMeta
    capabs = None
    config = None

    def __init__(self, frontend):
        self.frontend = frontend
        self.initialize()

    @abc.abstractmethod
    def initialize(self):
        self.log = Logger(self.__class__.__name__)

    @classproperty
    def capabilities(cls):
        return cls.capabs

    def get_method(self, capability):
        """Get class method for **capability**."""
        methods = {
            CAPABS.QUERY: self.query,
            CAPABS.REMOVE: self.remove,
            CAPABS.SEARCH: self.search,
            CAPABS.SYNC: self.sync,
            CAPABS.UPGRADE: self.upgrade
        }
        if CAPABS.test(self.capabs, capability):
            return methods[capability]

    @abc.abstractmethod
    def query(self, package):
        pass

    @abc.abstractmethod
    def search(self, *terms):
        pass

    @abc.abstractmethod
    def sync(self, package):
        pass

    @abc.abstractmethod
    def remove(self, package):
        pass

    @abc.abstractmethod
    def upgrade(self):
        pass