"""czdev CLI entry point — Python wrapper replacing the Rust binary for publish/unpublish/auth/bump."""

import argparse
import sys

from .github_client import GitHubError


def main():
    parser = argparse.ArgumentParser(
        prog="czdev",
        description="CardputerZero developer CLI. Build, publish and manage apps.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # new
    new_parser = subparsers.add_parser(
        "new", help="Scaffold a new app from the CardputerZero project template.")
    new_parser.add_argument("name", help="App name (also the Debian package name)")
    new_parser.add_argument("--dir", default=None, help="Target directory (default: ./<name>)")
    new_parser.add_argument("--template", default="CardputerZero/Template",
                            help="Template repo as owner/repo or a git URL "
                                 "(default: CardputerZero/Template)")
    new_parser.add_argument("--ref", default="main",
                            help="Template branch to copy the latest commit of (default: main)")
    new_parser.add_argument("--display-name", default=None,
                            help="Launcher display name (default: derived from the app name)")
    new_parser.add_argument("--no-git", action="store_true",
                            help="Do not create a git repository in the new project")

    # login
    subparsers.add_parser("login", help="Authenticate with GitHub (device flow).")

    # logout
    subparsers.add_parser("logout", help="Remove stored GitHub credentials.")

    # bump
    bump_parser = subparsers.add_parser("bump", help="Show next version (patch bump) for a package.")
    bump_parser.add_argument("--deb", default=None, help="Path to .deb file. If omitted, searches ./build/*.deb")

    # publish
    pub_parser = subparsers.add_parser("publish", help="Publish a .deb package to the CardputerZero app store.")
    pub_parser.add_argument("--deb", default=None, help="Path to .deb file. If omitted, searches ./build/*.deb")

    # unpublish
    unpub_parser = subparsers.add_parser("unpublish", help="Create a PR to remove a published package.")
    unpub_parser.add_argument("package", help="Package name to remove")
    unpub_parser.add_argument("--version", required=True, help="Version to remove")
    unpub_parser.add_argument("--arch", default="arm64", help="Architecture (default: arm64)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        dispatch(args, parser)
    except GitHubError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        sys.exit(130)


def dispatch(args, parser):
    if args.command == "new":
        from .new import run
        run(name=args.name, dir=args.dir, template=args.template, ref=args.ref,
            display_name=args.display_name, no_git=args.no_git)
    elif args.command == "login":
        from .auth import login
        login()
    elif args.command == "logout":
        from .auth import logout
        logout()
    elif args.command == "bump":
        from .bump import run
        run(deb=args.deb)
    elif args.command == "publish":
        from .publish import run
        run(deb=args.deb)
    elif args.command == "unpublish":
        from .unpublish import run
        run(package=args.package, version=args.version, arch=args.arch)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
