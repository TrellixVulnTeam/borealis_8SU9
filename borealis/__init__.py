"""
    borealis
    ~~~~~~~~

    A modular, object oriented package management API for Archlinux.

    :copyright: (c) 2011 David Gidwani
    :license: New BSD, see LICENSE
"""
from logbook import Logger, StderrHandler
from ufl.io.shell import cformat


__version__ = "0.1-pre"


_message_colors = ["%K", "%B", "%B", "%R", "%R", "%R"]
default_handler = StderrHandler()
default_handler.formatter = (lambda record, handler:
    cformat("{}{}:%n {}".format(_message_colors[record.level - 1],
                              record.level_name.lower(),
                              record.message)))
log = Logger(__name__)