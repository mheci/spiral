# NVIDIA and graphics

## Detection

The live environment detects GPUs with `lspci -Dnmm`. PCI vendor IDs map to NVIDIA (`10de`), AMD (`1002`, `1022`) and Intel (`8086`). The NVIDIA path also uses Debian's `nvidia-detect` when the tool is present. Its package recommendation is used only after package names are validated and package availability is checked.

The installer does not assume that one NVIDIA driver supports every GPU generation.

## Policies

`auto` chooses:

| Hardware | Choice |
| --- | --- |
| NVIDIA, Secure Boot off | proprietary `nvidia-driver` or the package nvidia-detect recommends |
| NVIDIA, Secure Boot on | Nouveau, because the proprietary DKMS modules would require MOK enrollment first |
| AMD | in-kernel `amdgpu` with Mesa and firmware |
| Intel | in-kernel `i915` or `xe` with Mesa, VA-API and firmware |
| virtual or unknown | generic Mesa |

Explicit choices are available for:

- `nvidia-proprietary`
- `nvidia-open`, only when Debian ships the open kernel module package
- `nouveau`
- `amd`
- `intel`
- `generic`

If an explicit proprietary choice is unavailable, the installer can fall back to Nouveau and records a warning. It never silently disables Secure Boot.

## Secure Boot

The ISO uses Debian's signed shim and GRUB packages. The installed system receives `grub-efi-amd64-signed` and `shim-signed` on UEFI amd64.

Proprietary NVIDIA kernel modules are built with DKMS and are not signed by Microsoft. With Secure Boot enabled they will not load until a Machine Owner Key is enrolled:

```sh
sudo mokutil --import /var/lib/shim-signed/mok/MOK.der
```

The exact key path depends on the Debian packages. Reboot and complete enrollment in the firmware prompt. This procedure is documented by Debian and is not automated by Spiral.

Spiral does not validate the complete Secure Boot chain on real hardware yet. The policy and package selection are implemented. Hardware validation is pending.

## Hybrid graphics

On Intel or AMD plus NVIDIA laptops, the installer installs one primary driver set and warns that PRIME or offload configuration is desktop-specific. It does not claim that automatic PRIME switching works.

## AMD and Intel

Both use in-kernel drivers. The installer installs Mesa, VA-API packages and the matching firmware. It does not add third-party repositories.

## Known limits

- NVIDIA legacy driver selection depends on `nvidia-detect` being present on the live ISO and on Debian package availability.
- No CUDA installation profile is implemented yet.
- Multi-GPU workstation layouts need manual configuration.
- No physical NVIDIA, AMD or Intel hardware has been validated by this repository yet. Package installation is not the same as a working driver.
