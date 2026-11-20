"""NexusOS profile for archinstall, offered under Desktop.

archinstall loads it from the path in config.json. The live medium already
points pacman at its Arch snapshot and at the nexusos repository in
/var/lib/nexusos/repo, and archinstall copies both settings to the target;
this profile copies the repository itself, so the installed system follows
the same snapshot until an update moves both.
"""
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, override

from archinstall.default_profiles.profile import DisplayServerType, Profile, ProfileType

if TYPE_CHECKING:
    from archinstall.lib.installer import Installer

REPO = Path('/var/lib/nexusos/repo')


class NexusOSProfile(Profile):
    def __init__(self) -> None:
        super().__init__(
            'NexusOS',
            ProfileType.DesktopEnv,
            support_gfx_driver=True,
            display_server=DisplayServerType.Wayland,
        )

    @property
    @override
    def packages(self) -> list[str]:
        return ['nexusos-keyring', 'nexusos-desktop', 'pipewire-jack']

    @property
    @override
    def services(self) -> list[str]:
        return ['greetd', 'bluetooth']

    @override
    def install(self, install_session: 'Installer') -> None:
        target = install_session.target
        shutil.copytree(REPO, target / REPO.relative_to('/'), symlinks=True, dirs_exist_ok=True)
        # greetd owns its config.toml; nexusos-desktop ships the NexusOS one.
        shutil.copy(target / 'usr/share/nexusos/greetd.toml', target / 'etc/greetd/config.toml')

    @override
    def post_install(self, install_session: 'Installer') -> None:
        # Runs after the additional packages (guided install: profile.install,
        # packages, then this): a system never ends up without a Secret Service,
        # whatever the configuration asked for.
        local = install_session.target / 'var/lib/pacman/local'
        if not any(next(local.glob(f'{name}-[0-9]*'), None) for name in ('keepassxc', 'gnome-keyring')):
            install_session.add_additional_packages(['gnome-keyring'])
        # A derivative must not present itself as Arch Linux in the boot menu.
        for conf in (install_session.target / 'boot').rglob('limine.conf'):
            conf.write_text(conf.read_text().replace('/Arch Linux (', '/NexusOS ('))
