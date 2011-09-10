"""
    borealis
    ~~~~~~~~

    A modular frontend to Archlinux's package manager and user repository.

    :copyright: (c) 2011 David Gidwani
    :license: New BSD, see LICENSE
"""
import os
import re
import urllib2
import sys

from borealis import log
from borealis.objects import Dependency
from ufl.io.fs import path
from ufl.io.shell import cformat, Command, execute, PIPE


__all__ = ["COLOR_ENABLED", "error", "sudo", "Version"]


COLOR_ENABLED = True


def deptest(*dependencies):
    return map(Dependency.from_depstring, filter(None,
               execute("pacman -T " + " ".join(map(str, dependencies)),
                       stdout=PIPE).stdout.split()))


def download(url, pathname):
    return path(pathname).write(urllib2.urlopen(url).read(), "wb")


def error(message, log=False, log_method=log.critical, fmt="%Rerror: %n{}"):
    if log:
        log_method(message)
    if COLOR_ENABLED:
        message = cformat(fmt.format(message))
    sys.exit(message if not log else 1)


def sudo(command, **kwargs):
    if os.geteuid() != 0:
        if Command("which sudo").run(stdout=PIPE):
            command = "sudo " + command
        else:
            command = "su -c " + command
    return Command(command).wait(**kwargs)