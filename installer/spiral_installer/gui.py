from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .config import Repository
from .errors import InstallError, SpiralError, ValidationError
from .hardware import detect_nvidia
from .models import HardwareReport, InstallPlan, InstallSecrets, ReleaseInfo
from .plan import Approval, InstallChoices, PlanBuilder, confirmation_phrase
from .util import Secret, human_bytes


def _gi():
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib, Gtk

        return Gio, GLib, Gtk
    except (ImportError, ValueError) as exc:
        raise SpiralError(
            "GTK4 Python bindings are missing. Install python3-gi and gir1.2-gtk-4.0."
        ) from exc


def run_gui(repo: Repository, hardware: HardwareReport, release: ReleaseInfo) -> int:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise SpiralError("no graphical display is available; run spiral-installer in a terminal")
    Gio, GLib, Gtk = _gi()

    class Application(Gtk.Application):
        def __init__(self) -> None:
            super().__init__(
                application_id="org.spiral.Installer",
                flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            )

        def do_activate(self) -> None:
            window = WizardWindow(self, repo, hardware, release, Gtk, GLib)
            window.present()

    return Application().run([])


class WizardWindow:
    def __init__(self, app, repo: Repository, hardware: HardwareReport, release: ReleaseInfo, Gtk, GLib) -> None:
        self.Gtk = Gtk
        self.GLib = GLib
        self.repo = repo
        self.hardware = hardware
        self.release = release
        self.repo = repo
        self.plan: Optional[InstallPlan] = None
        self.secrets: Optional[InstallSecrets] = None
        self.pages: list[str] = []
        self.page_titles: list[str] = []
        self.current = 0
        self.window = Gtk.ApplicationWindow(application=app, title="Spiral Installer")
        self.window.set_default_size(900, 700)
        self._build()
        self._show_page(0)

    # -- construction -----------------------------------------------------

    def _build(self) -> None:
        Gtk = self.Gtk
        header = Gtk.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Spiral Installer"))
        self.window.set_titlebar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(14)
        outer.set_margin_bottom(14)
        outer.set_margin_start(18)
        outer.set_margin_end(18)
        self.window.set_child(outer)

        self.step_label = Gtk.Label(label="", xalign=0)
        outer.append(self.step_label)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroller.set_child(self.stack)
        outer.append(scroller)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.END)
        self.back_button = Gtk.Button(label="Back")
        self.back_button.connect("clicked", lambda *_: self._show_page(self.current - 1))
        self.next_button = Gtk.Button(label="Continue")
        self.next_button.connect("clicked", lambda *_: self._next())
        self.install_button = Gtk.Button(label="Install")
        self.install_button.add_css_class("suggested-action")
        self.install_button.connect("clicked", lambda *_: self._start_install())
        self.close_button = Gtk.Button(label="Close")
        self.close_button.connect("clicked", lambda *_: self.window.close())
        self.reboot_button = Gtk.Button(label="Reboot")
        self.reboot_button.connect("clicked", lambda *_: self._reboot())
        for button in (self.back_button, self.next_button, self.install_button, self.reboot_button, self.close_button):
            footer.append(button)
        outer.append(footer)

        self._build_welcome()
        self._build_network()
        self._build_hardware()
        self._build_type()
        self._build_storage()
        self._build_filesystem()
        self._build_encryption()
        self._build_kernel()
        self._build_driver()
        self._build_desktop()
        self._build_software()
        self._build_user()
        self._build_summary()
        self._build_progress()
        self._build_complete()

    def _page(self, name: str, title: str) -> "object":
        Gtk = self.Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        self.stack.add_named(box, name)
        self.pages.append(name)
        self.page_titles.append(title)
        return box

    def _label(self, text: str) -> "object":
        Gtk = self.Gtk
        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        return label

    def _row(self, text: str, widget: "object") -> "object":
        Gtk = self.Gtk
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=text, xalign=0)
        label.set_size_request(190, -1)
        row.append(label)
        widget.set_hexpand(True)
        row.append(widget)
        return row

    def _dropdown(self, labels: list[str], selected: int = 0) -> "object":
        Gtk = self.Gtk
        dropdown = Gtk.DropDown(model=Gtk.StringList.new(labels))
        if labels:
            dropdown.set_selected(selected)
        return dropdown

    def _entry(self, value: str = "") -> "object":
        Gtk = self.Gtk
        entry = Gtk.Entry()
        entry.set_text(value)
        return entry

    def _password(self) -> "object":
        Gtk = self.Gtk
        entry = Gtk.PasswordEntry()
        entry.set_show_peek_icon(True)
        entry.set_hexpand(True)
        return entry

    # -- pages ------------------------------------------------------------

    def _build_welcome(self) -> None:
        box = self._page("welcome", "Welcome")
        box.append(self._label(f"Spiral {self.release.version}"))
        box.append(self._label(f"Debian base: {self.release.version} ({self.release.codename})"))
        box.append(self._label("This installer writes one disk and erases everything on it."))
        self.locale_entry = self._entry(self.repo.installer.default_str("locale"))
        self.keymap_entry = self._entry(self.repo.installer.default_str("keymap"))
        self.timezone_entry = self._entry(self.repo.installer.default_str("timezone"))
        box.append(self._row("Locale", self.locale_entry))
        box.append(self._row("Keyboard layout", self.keymap_entry))
        box.append(self._row("Timezone", self.timezone_entry))
        box.append(self._label("Accessibility: the live desktop provides screen reader and high-contrast options through GNOME accessibility tools. The installer uses the system theme and full keyboard navigation."))

    def _build_network(self) -> None:
        box = self._page("network", "Network")
        adapters = ", ".join(
            f"{item.name} ({item.kind}, {item.state or 'unknown'})" for item in self.hardware.network
        ) or "none detected"
        box.append(self._label(f"Adapters: {adapters}"))
        box.append(self._label("Online installation downloads packages from Debian. The installer verifies availability before touching a disk."))
        Gtk = self.Gtk
        self.offline_switch = Gtk.Switch()
        self.offline_switch.set_active(False)
        if not self.repo.build.offline_enabled:
            self.offline_switch.set_sensitive(False)
            self.offline_switch.set_tooltip_text("This ISO was built without an offline package repository")
        box.append(self._row("Install offline", self.offline_switch))
        self.network_note = self._label(
            "Wi-Fi can be configured with nmtui from a terminal. The installer does not ask for Wi-Fi credentials."
        )
        box.append(self.network_note)

    def _build_hardware(self) -> None:
        box = self._page("hardware", "Hardware")
        hw = self.hardware
        secure = "unknown" if hw.secure_boot is None else ("enabled" if hw.secure_boot else "disabled")
        lines = [
            f"CPU: {hw.cpu_model or 'unknown'} ({hw.cpu_cores} threads)",
            f"Memory: {hw.ram_mib} MiB",
            f"Firmware: {hw.firmware.value.upper()}",
            f"Secure Boot: {secure}",
            f"Virtualization: {hw.virtualization or 'none detected'}",
        ]
        for gpu in hw.gpus:
            lines.append(f"GPU: {gpu.model} [{gpu.vendor.value}]")
        for disk in hw.disks:
            lines.append(
                f"Disk: {disk.stable_path} {human_bytes(disk.size_bytes)} {disk.model} "
                f"({len(disk.partitions)} partitions)"
            )
        for line in lines:
            box.append(self._label(line))

    def _build_type(self) -> None:
        box = self._page("type", "Installation type")
        box.append(self._label("Guided whole-disk installation."))
        box.append(self._label(
            "Manual partitioning, dual-boot reuse and LVM are not implemented in this release. "
            "The installer will erase the selected disk."
        ))

    def _build_storage(self) -> None:
        box = self._page("storage", "Storage")
        self.disks = list(self.hardware.disks)
        labels = [
            f"{disk.stable_path}  {human_bytes(disk.size_bytes)}  {disk.model} {disk.transport}"
            for disk in self.disks
        ] or ["no disks detected"]
        self.disk_dropdown = self._dropdown(labels)
        box.append(self._row("Target disk", self.disk_dropdown))
        for disk in self.disks:
            flags = []
            if disk.removable:
                flags.append("removable")
            if disk.is_busy:
                flags.append("in use")
            if disk.has_foreign_os:
                flags.append("existing operating system partitions")
            if flags:
                box.append(self._label(f"{disk.stable_path}: {', '.join(flags)}"))
        box.append(self._label("The selected partition table is GPT on UEFI, GPT by default on BIOS, or MBR when explicitly selected in the unattended interface."))

    def _build_filesystem(self) -> None:
        box = self._page("filesystem", "Filesystem")
        self.fs_ids = sorted(self.repo.profiles.filesystems)
        self.fs_dropdown = self._dropdown(
            [self.repo.profiles.filesystem(item).label for item in self.fs_ids],
            self.fs_ids.index(self.repo.installer.default_str("filesystem")),
        )
        self.fs_dropdown.connect("notify::selected", self._filesystem_changed)
        box.append(self._row("Filesystem", self.fs_dropdown))

        self.layout_ids: list[str] = []
        self.layout_dropdown = self._dropdown(["standard"])
        box.append(self._row("BTRFS layout", self.layout_dropdown))

        self.compression_ids = sorted(self.repo.installer.compression)
        self.compression_dropdown = self._dropdown(self.compression_ids)
        box.append(self._row("Compression", self.compression_dropdown))

        Gtk = self.Gtk
        self.snapshots_switch = Gtk.Switch()
        self.maintenance_switch = Gtk.Switch()
        box.append(self._row("Snapper snapshots", self.snapshots_switch))
        box.append(self._row("BTRFS scrub timer", self.maintenance_switch))
        box.append(self._label("Snapshots are not backups."))

        self.swap_ids = ["zram", "partition", "none"]
        self.swap_dropdown = self._dropdown(["ZRAM", "Swap partition", "No swap"])
        box.append(self._row("Swap", self.swap_dropdown))
        self._filesystem_changed(self.fs_dropdown, None)

    def _filesystem_changed(self, dropdown, pspec) -> None:
        index = dropdown.get_selected()
        if index == 4294967295 or index >= len(self.fs_ids):
            return
        profile = self.repo.profiles.filesystem(self.fs_ids[index])
        Gtk = self.Gtk
        if profile.kind == "btrfs":
            self.layout_ids = sorted(profile.layouts)
            labels = [profile.layouts[item].label for item in self.layout_ids]
            self.layout_dropdown.set_model(Gtk.StringList.new(labels))
            self.layout_dropdown.set_selected(self.layout_ids.index(profile.default_layout))
            self.layout_dropdown.set_sensitive(True)
            self.compression_dropdown.set_sensitive(True)
            self.snapshots_switch.set_sensitive(True)
            self.maintenance_switch.set_sensitive(True)
            self.swap_ids = ["zram", "file", "partition", "none"]
            labels = ["ZRAM", "BTRFS swapfile", "Swap partition", "No swap"]
        else:
            self.layout_ids = []
            self.layout_dropdown.set_model(Gtk.StringList.new(["not applicable"]))
            self.layout_dropdown.set_selected(0)
            self.layout_dropdown.set_sensitive(False)
            self.compression_dropdown.set_sensitive(False)
            self.snapshots_switch.set_active(False)
            self.snapshots_switch.set_sensitive(False)
            self.maintenance_switch.set_active(False)
            self.maintenance_switch.set_sensitive(False)
            self.swap_ids = ["zram", "partition", "none"]
            labels = ["ZRAM", "Swap partition", "No swap"]
        self.swap_dropdown.set_model(Gtk.StringList.new(labels))
        self.swap_dropdown.set_selected(0)

    def _build_encryption(self) -> None:
        box = self._page("encryption", "Encryption")
        Gtk = self.Gtk
        self.encryption_switch = Gtk.Switch()
        self.encryption_switch.connect("notify::active", self._encryption_changed)
        self.luks_entry = self._password()
        self.luks_repeat = self._password()
        box.append(self._row("Encrypt root (LUKS2)", self.encryption_switch))
        box.append(self._row("Passphrase", self.luks_entry))
        box.append(self._row("Repeat passphrase", self.luks_repeat))
        self.encryption_note = self._label(
            "The passphrase cannot be recovered. Root encryption creates a separate /boot and "
            "disables the swap partition option to avoid leaking swap data."
        )
        box.append(self.encryption_note)
        self._encryption_changed(self.encryption_switch, None)

    def _encryption_changed(self, switch, pspec) -> None:
        active = switch.get_active()
        self.luks_entry.set_sensitive(active)
        self.luks_repeat.set_sensitive(active)
        if active and "partition" in self.swap_ids:
            index = self.swap_ids.index("partition")
            if self.swap_dropdown.get_selected() == index:
                self.swap_dropdown.set_selected(0)

    def _build_kernel(self) -> None:
        box = self._page("kernel", "Kernel")
        self.kernel_ids = sorted(self.repo.profiles.kernels)
        labels = [
            f"{self.repo.profiles.kernels[item].label}: {self.repo.profiles.kernels[item].description}"
            for item in self.kernel_ids
        ]
        self.kernel_dropdown = self._dropdown(labels, self.kernel_ids.index(self.repo.installer.default_str("kernel")))
        box.append(self._row("Kernel profile", self.kernel_dropdown))

    def _build_driver(self) -> None:
        box = self._page("driver", "Graphics drivers")
        self.driver_ids = ["auto"] + sorted(self.repo.profiles.drivers)
        labels = ["Automatic (recommended)"]
        for item in sorted(self.repo.profiles.drivers):
            profile = self.repo.profiles.drivers[item]
            labels.append(f"{profile.label}: {profile.description}")
        self.driver_dropdown = self._dropdown(labels)
        box.append(self._row("Driver policy", self.driver_dropdown))
        box.append(self._label("Secure Boot never gets disabled. Proprietary NVIDIA kernel modules need MOK enrollment before they can load."))

    def _build_desktop(self) -> None:
        box = self._page("desktop", "Desktop environment")
        Gtk = self.Gtk
        self.desktop_checks = {}
        default = self.repo.installer.default_str("desktop")
        scroller = Gtk.ScrolledWindow()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for item in sorted(self.repo.profiles.desktops):
            profile = self.repo.profiles.desktops[item]
            check = Gtk.CheckButton(label=f"{profile.label} [{profile.kind}] {profile.description}")
            check.set_active(item == default)
            self.desktop_checks[item] = check
            inner.append(check)
        scroller.set_child(inner)
        scroller.set_vexpand(True)
        box.append(scroller)

    def _build_software(self) -> None:
        box = self._page("software", "Software")
        Gtk = self.Gtk
        self.group_checks = {}
        scroller = Gtk.ScrolledWindow()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        defaults = set(self.repo.installer.default_groups)
        for item in sorted(self.repo.profiles.packages):
            if item in defaults:
                continue
            group = self.repo.profiles.packages[item]
            check = Gtk.CheckButton(label=f"{group.label}: {group.description}")
            self.group_checks[item] = check
            inner.append(check)
        scroller.set_child(inner)
        scroller.set_vexpand(True)
        box.append(scroller)

    def _build_user(self) -> None:
        box = self._page("user", "User account")
        Gtk = self.Gtk
        self.username_entry = self._entry(self.repo.installer.default_str("username"))
        self.fullname_entry = self._entry("Spiral User")
        self.hostname_entry = self._entry(self.repo.installer.default_str("hostname"))
        self.password_entry = self._password()
        self.password_repeat = self._password()
        self.autologin_switch = Gtk.Switch()
        box.append(self._row("Username", self.username_entry))
        box.append(self._row("Full name", self.fullname_entry))
        box.append(self._row("Hostname", self.hostname_entry))
        box.append(self._row("Password", self.password_entry))
        box.append(self._row("Repeat password", self.password_repeat))
        box.append(self._row("Automatic login", self.autologin_switch))

    def _build_summary(self) -> None:
        box = self._page("summary", "Summary")
        Gtk = self.Gtk
        self.summary_view = Gtk.TextView()
        self.summary_view.set_editable(False)
        self.summary_view.set_cursor_visible(False)
        self.summary_view.set_wrap_mode(Gtk.WrapMode.WORD)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.summary_view)
        scroller.set_vexpand(True)
        box.append(scroller)
        self.warnings_label = self._label("")
        self.warnings_label.set_selectable(True)
        box.append(self.warnings_label)
        self.ack_check = Gtk.CheckButton(label="I acknowledge every critical warning listed above.")
        box.append(self.ack_check)
        self.phrase_label = self._label("")
        box.append(self.phrase_label)
        self.phrase_entry = self._entry("")
        box.append(self._row("Confirmation phrase", self.phrase_entry))

    def _build_progress(self) -> None:
        box = self._page("progress", "Installing")
        Gtk = self.Gtk
        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        box.append(self.spinner)
        self.progress_label = self._label("Preparing installation")
        box.append(self.progress_label)
        box.append(self._label("This can take 20 to 60 minutes. Do not power off."))

    def _build_complete(self) -> None:
        box = self._page("complete", "Complete")
        self.complete_label = self._label("")
        self.complete_label.set_selectable(True)
        box.append(self.complete_label)

    # -- navigation -------------------------------------------------------

    def _show_page(self, index: int) -> None:
        index = max(0, min(index, len(self.pages) - 1))
        self.current = index
        self.stack.set_visible_child_name(self.pages[index])
        self.step_label.set_text(f"Step {index + 1} of 13: {self.page_titles[index]}")
        installing = self.pages[index] in ("progress", "complete")
        self.back_button.set_visible(index > 0 and not installing)
        self.next_button.set_visible(index < 12)
        self.install_button.set_visible(index == 12)
        self.close_button.set_visible(self.pages[index] == "complete")
        self.reboot_button.set_visible(self.pages[index] == "complete")
        if self.pages[index] == "summary":
            self._refresh_summary()

    def _next(self) -> None:
        if self.current < 12:
            self._show_page(self.current + 1)

    # -- plan and install -------------------------------------------------

    def _collect_choices(self) -> InstallChoices:
        def selected(dropdown, values):
            index = dropdown.get_selected()
            if index == 4294967295 or index >= len(values):
                return None
            return values[index]

        disk_index = self.disk_dropdown.get_selected()
        if disk_index == 4294967295 or disk_index >= len(self.disks):
            raise ValidationError("select a target disk")
        filesystem = selected(self.fs_dropdown, self.fs_ids)
        profile = self.repo.profiles.filesystem(filesystem)
        layout = selected(self.layout_dropdown, self.layout_ids) if profile.kind == "btrfs" else None
        compression = selected(self.compression_dropdown, self.compression_ids) if profile.kind == "btrfs" else None
        swap = selected(self.swap_dropdown, self.swap_ids)
        kernel = selected(self.kernel_dropdown, self.kernel_ids)
        driver = selected(self.driver_dropdown, self.driver_ids)
        desktops = tuple(item for item, check in self.desktop_checks.items() if check.get_active())
        if not desktops:
            raise ValidationError("select at least one desktop profile, including minimal")
        groups = tuple(item for item, check in self.group_checks.items() if check.get_active())
        return InstallChoices(
            disk=self.disks[disk_index].stable_path,
            filesystem=filesystem,
            layout=layout,
            compression=compression,
            swap=swap,
            encryption=self.encryption_switch.get_active(),
            kernel=kernel,
            desktops=desktops,
            driver=driver,
            package_groups=groups,
            snapshots=self.snapshots_switch.get_active(),
            btrfs_maintenance=self.maintenance_switch.get_active(),
            username=self.username_entry.get_text().strip(),
            full_name=self.fullname_entry.get_text().strip(),
            hostname=self.hostname_entry.get_text().strip(),
            autologin=self.autologin_switch.get_active(),
            locale=self.locale_entry.get_text().strip(),
            keymap=self.keymap_entry.get_text().strip(),
            timezone=self.timezone_entry.get_text().strip(),
            offline=self.offline_switch.get_active(),
        )

    def _collect_secrets(self) -> InstallSecrets:
        password = self.password_entry.get_text()
        if len(password) < 8:
            raise ValidationError("user password must be at least 8 characters")
        if password != self.password_repeat.get_text():
            raise ValidationError("user passwords do not match")
        luks = None
        if self.encryption_switch.get_active():
            passphrase = self.luks_entry.get_text()
            if len(passphrase) < 8:
                raise ValidationError("LUKS passphrase must be at least 8 characters")
            if passphrase != self.luks_repeat.get_text():
                raise ValidationError("LUKS passphrases do not match")
            luks = Secret(passphrase)
        return InstallSecrets(user_password=Secret(password), luks_passphrase=luks)

    def _refresh_summary(self) -> None:
        Gtk = self.Gtk
        try:
            choices = self._collect_choices()
            builder = PlanBuilder(self.repo, self.hardware, self.release)
            plan = builder.build(choices)
        except (ValidationError, SpiralError) as exc:
            self.summary_view.get_buffer().set_text(f"Cannot build the installation plan:\n\n{exc}")
            self.warnings_label.set_text("")
            self.phrase_label.set_text("")
            return
        self.plan = plan
        lines = [
            f"Debian: {plan.release.version} ({plan.release.codename})",
            f"Disk: {plan.disk.stable_path} {human_bytes(plan.disk.size_bytes)} {plan.disk.model}",
            f"Firmware: {plan.firmware.value.upper()}  Partition table: {plan.partition_table.value.upper()}",
        ]
        for part in plan.partitions:
            size = "rest" if part.size_mib is None else f"{part.size_mib} MiB"
            lines.append(f"  {part.number}: {part.role.value} {size} {part.fs} {part.mountpoint or ''}")
        lines.extend(
            [
                f"Filesystem: {plan.filesystem_id}",
                f"Compression: {plan.compression.mount_option or 'off'}",
                f"Swap: {plan.swap.strategy.value}",
                f"Encryption: {'LUKS2' if plan.encryption.enabled else 'disabled'}",
                f"Kernel: {plan.software.kernel_label}",
                f"Desktop: {', '.join(plan.software.desktop_labels)}",
                f"Driver: {plan.software.driver_label}",
                f"Packages: {len(plan.software.packages)}",
                f"User: {plan.user.username} on {plan.user.hostname}",
            ]
        )
        self.summary_view.get_buffer().set_text("\n".join(lines))
        warnings = [
            f"[{warning.severity.value}] {warning.code}: {warning.message}"
            for warning in plan.warnings
        ]
        self.warnings_label.set_text("\n\n".join(warnings))
        phrase = confirmation_phrase(plan)
        self.phrase_label.set_text(f"Type exactly: {phrase}")

    def _start_install(self) -> None:
        try:
            self.plan = PlanBuilder(self.repo, self.hardware, self.release).build(self._collect_choices())
        except (ValidationError, SpiralError) as exc:
            self._error(str(exc))
            return
        assert self.plan is not None
        phrase = confirmation_phrase(self.plan)
        if self.phrase_entry.get_text() != phrase:
            self._error(f"The confirmation phrase does not match. Type exactly: {phrase}")
            return
        if self.plan.requires_ack and not self.ack_check.get_active():
            self._error("Acknowledge every critical warning before installing.")
            return
        try:
            secrets = self._collect_secrets()
        except ValidationError as exc:
            self._error(str(exc))
            return
        acknowledged = frozenset(w.code for w in self.plan.warnings if w.requires_ack)
        approval = Approval(phrase=phrase, acknowledged=acknowledged)
        self.secrets = secrets
        self.phrase_entry.set_text("")
        self.password_entry.set_text("")
        self.password_repeat.set_text("")
        self.luks_entry.set_text("")
        self.luks_repeat.set_text("")
        self._show_page(13)
        self.spinner.start()
        self.progress_label.set_text("Starting installation")
        threading.Thread(target=self._install_worker, args=(approval,), daemon=True).start()

    def _install_worker(self, approval: Approval) -> None:
        from .engine import Installer

        try:
            installer = Installer(
                self.repo,
                self.plan,
                self.secrets,
                approval,
                echo=False,
                progress=self._progress,
            )
            result = installer.install()
            self.GLib.idle_add(self._install_done, result)
        except InstallError as exc:
            self.GLib.idle_add(self._install_failed, exc)
        except Exception as exc:
            self.GLib.idle_add(self._install_failed, InstallError(f"unexpected failure: {exc}"))

    def _progress(self, record: dict) -> None:
        if record.get("severity") == "error" or (
            record.get("module") == "engine" and record.get("operation") == "phase-start"
        ):
            text = f"{record.get('operation', '')}: {record.get('result', '')}"
            self.GLib.idle_add(self.progress_label.set_text, text)

    def _install_done(self, result) -> None:
        self.spinner.stop()
        plan = self.plan
        assert plan is not None
        self.complete_label.set_text(
            "Installation complete.\n\n"
            f"Debian {plan.release.version} ({plan.release.codename})\n"
            f"Kernel: {plan.software.kernel_id}\n"
            f"Filesystem: {plan.filesystem_id}\n"
            f"Desktop: {', '.join(plan.software.desktop_labels)}\n"
            f"Driver: {plan.software.driver_id}\n"
            f"Log: {result.log_path}\n\n"
            "Reboot to use the installed system."
        )
        self._show_page(14)

    def _install_failed(self, exc: InstallError) -> None:
        self.spinner.stop()
        self.progress_label.set_text(f"Installation failed: {exc.message}")
        self.back_button.set_visible(False)
        self.next_button.set_visible(False)
        self.close_button.set_visible(True)
        self._error(
            f"Installation failed in phase '{exc.phase or 'unknown'}': {exc.message}\n\n"
            "The target may be partially written. Reboot the live medium before retrying."
        )

    def _error(self, message: str) -> None:
        Gtk = self.Gtk
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=message,
        )
        dialog.connect("response", lambda item, response: item.destroy())
        dialog.present()

    def _reboot(self) -> None:
        subprocess.call(["reboot"])
