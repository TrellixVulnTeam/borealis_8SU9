# coding: utf-8
"""
    borealis
    ~~~~~~~~

    A modular frontend to Archlinux's package manager and user repository.

    :copyright: (c) 2011 David Gidwani
    :license: New BSD, see LICENSE
"""
import argparse
import ConfigParser
import contextlib
import getopt
import inspect
import itertools
import signal
import StringIO
import sys
import textwrap
import types

from borealis import log, default_handler
from borealis.util import error
from borealis.backends import ACTIONS, CAPABS, DeferToNextBackend, \
                              PackageManagementBackend
from ufl.core import get_subclass, get_subclasses, import_
from ufl.core.funcutils import cached_property
from ufl.core.structures.dict import AttrDict
from ufl.core.structures.list import flatten_lazy
from ufl.io.config import ClassConfigManager, NoConfigExistsError
from ufl.io.fs import path
from ufl.io.shell import cprint, cformat


class OperationAction(argparse.Action):

    def __init__(self, option_strings, dest, capability=None, default=None,
                 requires_action=None, arg_as_opts=False, required=False,
                 help=None):
        nargs = "?" if arg_as_opts else 0
        dest = "action" if dest in map(str.lower, ACTIONS) else dest
        super(OperationAction, self).__init__(
            option_strings=option_strings,
            dest=dest,
            nargs=nargs,
            default=default,
            required=required,
            help=help
        )
        self.arg_as_opts = arg_as_opts
        self.capability = capability
        self.requires_action = requires_action

    def __call__(self, parser, namespace, values, option_string=None):
        if (self.requires_action is not None and
            namespace.action != self.requires_action):
            error("invalid option")
        if self.arg_as_opts:
            if values:
                if " " in values:
                    raise argparse.ArgumentError(self, "invalid options")
                # FIXME
                parser.frontend.unhandled_opts += map(lambda v: "-" + v, values)
        namespace.action = self.capability


class Borealis(object):

    config_locations = (
        path("/etc/borealis.conf"),
        path("$HOME/.config/borealis/borealis.conf")
    )
    config = AttrDict({
        "backend_order": "pacman:PacmanProxyBackend, aur:AURBackend",
        "output_fmt": r"{index:>4}› %%M{repo}/%%W{name} %%G{version}\n"
                       "%%N{description}",
        "output_fmt_query": "%%W{name} %%G{version}",
        # "output_fmt_installed": r"{fmt_lines[0]} %%C[installed]\n"
        #                          "{fmt_lines[1]}",
        "output_fmt_installed": r"{fmt_lines[0]} {installed_diff}\n"
                                 "{fmt_lines[1]}",
        "output_fmt_installed_same": r"",
        "output_fmt_installed_new": r"%%G↑",
        "output_fmt_installed_old": r"%%R↓",
        "output_desc_wrap": 80,
        "output_desc_indent": 10,
    })
    action_switches = {
        CAPABS.SYNC: "-S",
        CAPABS.SEARCH: "-Ss",
        CAPABS.UPGRADE: "-Su",
        CAPABS.QUERY: "-Q",
        CAPABS.REMOVE: "-R",
    }

    def __init__(self):
        self.backends = []
        self.parsed_args = []
        self.unhandled_opts = []
        self.config_manager = ClassConfigManager(autowrite=False)

    @property
    def current_action(self):
        return self.parsed_args[0].action

    @cached_property
    def current_action_name(self):
        return CAPABS.get_name(self.current_action)

    def dispatch(self, action, arguments):
        name = CAPABS.get_name(action)
        results = []
        for index, backend in enumerate(self.backends):
            method = backend.get_method(action)
            if method:
                try:
                    if action == CAPABS.SEARCH:
                        results.append(method(*arguments))
                    else:
                        results.extend(map(method, arguments))
                except DeferToNextBackend:
                    if index != len(self.backends) - 1:
                        next_backend = self.backends[index + 1]
                        log.info("deferring to " +
                                 next_backend.__class__.__name__)
                        continue
            else:
                log.warning("backend {} does not support method {}"\
                            .format(backend.__class__.__name__, name))
        results = flatten_lazy(results)
        try:
            first = next(results)
            results = itertools.chain([first], results)
        except StopIteration:
            log.notice("no results")
            sys.exit(0)
        return getattr(self, "post_" + name.lower())(results)

    def post_query(self, packages):
        return self.post_search(packages)

    def post_search(self, packages):
        for index, package in enumerate(packages):
            self.print_package(package, index=index + 1)

    def post_sync(self, packages):
        pass

    def get_backends(self):
        backend_names = map(str.strip, self.config.backend_order.split(","))
        backends = []
        for name in backend_names:
            try:
                import_("borealis.backends." + name)
                backend = get_subclass(PackageManagementBackend,
                                       name.rsplit(":")[-1])
                if not backend:
                    raise Exception("object exists but isn't a valid backend")
                backends.append(backend(self))
            except Exception, err:
                log.error("failed to load backend {}: {}".format(name,
                          str(err)))
        if not all(backends):
            log.critical("no backends enabled")
            sys.exit(1)
        return backends

    def get_backend_by_name(self, name):
        for backend in self.backends:
            if backend.__name__ == name:
                return backend
        raise NameError("backend {} doesn't exist".format(name))

    def get_default_config(self):
        self.config_manager.read(config=ConfigParser.SafeConfigParser())
        self.config_manager.add(self)
        for backend in self.get_backends():
            self.config_manager.add(backend)
        return self.config_manager.config

    @classmethod
    def get_switch_for_action(cls, action):
        if isinstance(action, types.IntType):
            for capab in cls.action_switches.keys():
                if capab == capab | action:
                    action = capab
        elif isinstance(action, basestring):
            action = CAPABS[action]
        assert action, "invalid action: {}".format(repr(action))
        return cls.action_switches[action]

    def generate_help(self):
        print("usage: {} <operation> [...]".format(sys.argv[0]))
        print("supported operations:")
        for backend in self.backends:
            print("  " + backend.__class__.__name__ + ":")
            for action_name in ACTIONS:
                capab = CAPABS.get(action_name)
                if CAPABS.test(backend.capabs, capab):
                    print("    {} {:<3} [options] [package(s)]".format(
                          sys.argv[0], self.get_switch_for_action(capab)))
            print("")
        # print("use {} '{{-h --help}}' with an operation for available options"
        #       .format(sys.argv[0]))

    def parse_config(self):
        self.config_manager.autowrite = True
        try:
            self.config_manager.read(filenames=[p.absolute for p in
                                     self.config_locations])
        except NoConfigExistsError:
            log.critical("no config file found (looked in {0}); you can create "
                         "one like this:\n\n  $ {1} --default-config > "
                         "{0[0]}\n".format(map(str, self.config_locations),
                                           sys.argv[0]))
            sys.exit(1)
        self.config_manager.add(self)
        self.backends = self.get_backends()
        for backend in self.backends:
            self.config_manager.add(backend)
        self.config_manager.write()

    def parse_arguments(self):
        # TODO: use getopt instead of argparse. argparse is convenient, but a
        # horrible choice for wrapping an application that uses getopt.
        parser = argparse.ArgumentParser(add_help=False)
        parser.frontend = self

        parser.add_argument("packages", nargs="*", default=[])

        sync = parser.add_argument_group("sync")
        sync.add_argument("-S", "--sync", action=OperationAction,
                          capability=CAPABS.SYNC)
        sync.add_argument("-s", "--search", action=OperationAction,
                          capability=CAPABS.SEARCH, requires_action=CAPABS.SYNC)

        query = parser.add_argument_group("query")
        sync.add_argument("-Q", "--query", action=OperationAction,
                          capability=CAPABS.QUERY, arg_as_opts=True)

        parser.add_argument("-h", "--help", action="store_true")
        parser.add_argument("--default-config", action="store_true")

        self.parsed_args = parser.parse_known_args()
        self.parsed_args[1].extend(self.unhandled_opts)
        arguments, remaining = self.parsed_args

        if remaining:
            remaining = (
                [self.get_switch_for_action(arguments.action)] +
                remaining +
                arguments.packages
            )
            print remaining
            arguments, discard = parser.parse_known_args(remaining)
            del discard

        if arguments.default_config:
            self.print_default_config()
            sys.exit(0)
        else:
            self.parse_config()

        action = None

        if arguments.help:
            self.generate_help()
            sys.exit(0)

        if arguments.action:
            # if any backend operator handler has an optional parameter,
            # don't warn about not supplying any on the command line.
            args_are_optional = False
            for backend in self.backends:
                method = backend.get_method(arguments.action)
                if method:
                    if inspect.getargspec(method).defaults:
                        args_are_optional = True
            if not args_are_optional and not arguments.packages:
                error("no targets specified (use -h for help)")
            elif args_are_optional and not arguments.packages:
                arguments.packages = [None]
            self.dispatch(arguments.action, arguments.packages)
        else:
            error("no operation specified (use -h for help)")

    def print_default_config(self):
        config = self.get_default_config()
        with contextlib.closing(StringIO.StringIO()) as config_buf:
            config.write(config_buf)
            print(config_buf.getvalue())

    def print_package(self, package, **kwargs):
        if self.config.output_desc_wrap != -1:
            wrap = self.config.output_desc_wrap
            indent = " " * self.config.output_desc_indent
            package.metadata["description"] = "\n".join(textwrap.wrap(
                package.metadata["description"], width=wrap,
                initial_indent=indent, subsequent_indent=indent))
        kwargs.update(package.metadata)
        try:
            string = self._output_fmt.format(**kwargs)
            if package.installed and self.current_action != CAPABS.QUERY:
                string = self.config.output_fmt_installed.format(
                    fmt=string,
                    fmt_lines=string.split("\\n"),
                    installed_diff={
                        -1: self.config.output_fmt_installed_old,
                        0: self.config.output_fmt_installed_same,
                        1: self.config.output_fmt_installed_new
                    }[package.version.__cmp__(package.installed_version)],
                    **kwargs)
            string = string.replace("\\n", "\n")
            cprint(string)
        except KeyError:
            error("invalid format string")

    @property
    def _output_fmt(self):
        return self.config.get("output_fmt_" + self.current_action_name.lower(),
                               self.config.output_fmt)


def main(handler=default_handler):
    def interrupt(signal, frame):
        print("")
        log.info("interrupted")
        sys.exit(0)
    signal.signal(signal.SIGINT, interrupt)
    with handler.applicationbound():
        app = Borealis()
        app.parse_arguments()