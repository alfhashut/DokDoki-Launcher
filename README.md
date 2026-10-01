# DokDoki Launcher

> An unofficial DDLC Mod Manager

DokDoki Launcher helps you install, organize, and play fan-made mods for the
original Doki Doki Literature Club! (DDLC). It creates separate profiles from
your own local DDLC installation, keeping mod files separate from your clean
base installation.

DokDoki Launcher is a fan-made project, unaffiliated with Team Salvato.

## Features

- Manually select your own clean DDLC installation.
- Keep mods in separate profiles, with a Vanilla profile for the original game.
- Install supported mods from ZIP or 7z archives using **Install Mod**.
- Browse, search, and filter mods in the optional **Online Mod Library**.
- Download listed mods through MediaFire, Google Drive, MEGA, or direct links,
  with SHA-256 checksum verification before installation.
- Detect and apply updates for version-tracked Online Mod Library installs,
  preserving profile saves, display names, and notes.
- Review interrupted updates and resolve supported recovery states; other
  states require manual attention.
- Edit mod profile display names and notes, use separate save folders for each
  profile, and optionally keep saves when removing a mod profile.
- Choose **System**, **Light**, or **Dark** themes.

## Downloads

Official builds are intended to be provided through this repository's
**GitHub Releases**. The first public v1.0 release is being prepared.

Planned release formats:

- **Windows x64:** `DokDoki-Launcher.exe`
- **Linux x86_64:** `DokDoki-Launcher-Linux-x86_64.AppImage`

## Requirements

You need your own clean, unmodified copy of the **original Doki Doki Literature
Club!**, obtained from the [official DDLC website](https://ddlc.moe/).

DokDoki Launcher **does not include, distribute, or download DDLC**. You must
obtain the game yourself and manually select its installation folder. Official
game content used in profiles comes from that local installation.

Complete the original DDLC before playing fan mods, as recommended by
[Team Salvato's IP Guidelines](https://teamsalvato.com/ip-guidelines).

You also need enough disk space for separate copies of DDLC and your mods.
An internet connection is needed to use the Online Mod Library.

## Basic Usage

1. Launch DokDoki Launcher.
2. Click **Change Installation** and select your clean DDLC installation folder.
3. After selecting an installation for the first time, close and reopen
   DokDoki Launcher so it can create the **Vanilla** profile.
4. Click **Install Mod** to choose a manually downloaded mod archive, or open
   **Online Mod Library**, select a listed mod, and click **Install Selected Mod**
   when available.
5. Select the desired **Profile**.
6. Click **Play**.

Mods are installed into their own profiles rather than your clean base
installation. Choose an archive that includes a launcher for your operating
system when the mod ships its own runtime.

## Online Mod Library

The Online Mod Library is an optional convenience feature. Its registry contains
mod descriptions, author information, links to mod pages, and download details.
Downloads use the third-party source links configured for each mod in the
registry; the library does not provide a copy of DDLC.

Third-party mods belong to their respective creators, who remain responsible
for their own work. Inclusion in the library does not mean DokDoki Launcher
created, owns, or hosts a mod. Use **View Mod Page** for author/source information,
installation guidance, and mod-specific details. Team Salvato's guidelines call
for DDLC fangames and mods to be freely available.

For version-tracked library installs, use **Update Selected Mod** when available.
Older or manually installed profiles may have unknown update status.
If a mod shows **Recovery needed**, review its details and use **Resolve Update
Data** when offered. Some recovery states require manual attention.

## Supported Platforms

Current intended release targets:

- **Windows x64**
- **Linux x86_64**, distributed as an AppImage

## Disclaimer

DokDoki Launcher is an unofficial, fan-made project. It is not affiliated with,
endorsed by, or associated with Team Salvato.

Doki Doki Literature Club! and related properties belong to Team Salvato.

DokDoki Launcher does not include or distribute Doki Doki Literature Club and
does not download it on behalf of players. Users must obtain their own copy
and select its installation manually.

See the official [Team Salvato IP Guidelines](https://teamsalvato.com/ip-guidelines)
for guidance on fan work. This disclaimer does not grant permission or a license
to use or distribute Team Salvato's intellectual property.
