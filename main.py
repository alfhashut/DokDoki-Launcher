import asyncio
import hashlib
import json
import os
import stat
import sys
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import tempfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath, PureWindowsPath

import aiohttp
import gdown
import py7zr
import requests
from mega.client import MegaNzClient
from mega.errors import MegaNzError

from PySide6.QtCore import (
    QObject,
    QSettings,
    QStandardPaths,
    QThread,
    QUrl,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


MOD_REGISTRY_URL = (
    "https://raw.githubusercontent.com/"
    "alfhashut/DDLC-Mod-Registry/main/mods.json"
)
MAX_REGISTRY_SIZE = 1024 * 1024
MAX_MEDIAFIRE_PAGE_SIZE = 2 * 1024 * 1024
MEGA_DOWNLOAD_HELPER_FLAG = "--mega-download-helper"
MEGA_HOSTNAMES = {
    "mega.nz",
    "www.mega.nz",
    "mega.co.nz",
    "www.mega.co.nz",
}


class MegaFolderLinkError(ValueError):
    pass


class UnsafeMegaDownloadOutputError(OSError):
    pass


def normalize_mega_public_url(page_url):
    if not isinstance(page_url, str) or not page_url.strip():
        raise ValueError("MEGA URL must be a non-empty string.")

    parsed_url = urllib.parse.urlsplit(page_url)
    hostname = parsed_url.hostname
    username = parsed_url.username
    password = parsed_url.password

    if parsed_url.scheme != "https":
        raise ValueError("MEGA URL must use the https scheme.")

    if not hostname:
        raise ValueError("MEGA URL must include a hostname.")

    if username is not None:
        raise ValueError("MEGA URL must not include a username.")

    if password is not None:
        raise ValueError("MEGA URL must not include a password.")

    if hostname not in MEGA_HOSTNAMES:
        raise ValueError("MEGA URL uses an unsupported hostname.")

    port = parsed_url.port
    canonical_netloc = "mega.nz"

    if port is not None:
        canonical_netloc += f":{port}"

    return urllib.parse.urlunsplit(
        (
            parsed_url.scheme,
            canonical_netloc,
            parsed_url.path,
            parsed_url.query,
            parsed_url.fragment,
        )
    )


def parse_mega_public_file_url(page_url):
    normalized_url = normalize_mega_public_url(page_url)
    url_info = MegaNzClient.parse_url(normalized_url)

    if url_info.is_folder:
        raise MegaFolderLinkError(
            "MEGA folder links are not implemented yet."
        )

    public_handle, public_key = MegaNzClient.parse_file_url(
        normalized_url
    )
    return normalized_url, public_handle, public_key


def validate_mod_registry_entry(mod, index):
    if not isinstance(mod, dict):
        print(
            f"Invalid mod registry entry at index {index}: "
            "entry must be a dictionary."
        )
        return False

    required_fields = (
        "id",
        "name",
        "author",
        "description",
        "page_url",
        "page_source",
    )

    for field in required_fields:
        if field not in mod:
            print(
                f"Invalid mod registry entry at index {index}: "
                f"missing required field '{field}'."
            )
            return False

        value = mod[field]

        if not isinstance(value, str):
            print(
                f"Invalid mod registry entry at index {index}: "
                f"field '{field}' must be a string."
            )
            return False

        if not value.strip():
            print(
                f"Invalid mod registry entry at index {index}: "
                f"field '{field}' must not be empty."
            )
            return False

    mod_id = mod["id"]
    valid_id_characters = set(
        "abcdefghijklmnopqrstuvwxyz0123456789-"
    )

    if any(character not in valid_id_characters for character in mod_id):
        print(
            f"Invalid mod registry entry at index {index}: field 'id' "
            "may contain only lowercase ASCII letters, digits, and "
            "hyphens."
        )
        return False

    if mod_id[0] == "-" or mod_id[-1] == "-":
        print(
            f"Invalid mod registry entry at index {index}: field 'id' "
            "must begin and end with a letter or digit."
        )
        return False

    if "--" in mod_id:
        print(
            f"Invalid mod registry entry at index {index}: field 'id' "
            "must not contain consecutive hyphens."
        )
        return False

    if "version" in mod:
        version = mod["version"]

        if not isinstance(version, str) or not version.strip():
            print(
                f"Invalid mod registry entry at index {index}: field "
                "'version' must be a non-empty string when present."
            )
            return False

    if "download" in mod:
        download = mod["download"]

        if not isinstance(download, dict):
            print(
                f"Invalid mod registry entry at index {index}: "
                "download must be a dictionary."
            )
            return False

        required_download_fields = (
            "source",
            "method",
            "page_url",
            "file_name",
            "sha256",
        )

        for field in required_download_fields:
            if field not in download:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    f"download is missing required field '{field}'."
                )
                return False

            value = download[field]

            if not isinstance(value, str):
                print(
                    f"Invalid mod registry entry at index {index}: "
                    f"download field '{field}' must be a string."
                )
                return False

            if not value.strip():
                print(
                    f"Invalid mod registry entry at index {index}: "
                    f"download field '{field}' must not be empty."
                )
                return False

        download_method = download["method"]

        if download_method not in (
            "mediafire",
            "direct",
            "google_drive",
            "mega",
        ):
            print(
                f"Invalid mod registry entry at index {index}: "
                f"unsupported download method '{download_method}'."
            )
            return False

        try:
            download_page_url = urllib.parse.urlsplit(
                download["page_url"]
            )
            download_hostname = download_page_url.hostname
            download_username = download_page_url.username
            download_password = download_page_url.password
        except ValueError as error:
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'page_url' could not be parsed: "
                f"{error}"
            )
            return False

        if download_page_url.scheme != "https":
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'page_url' must use the https scheme."
            )
            return False

        if not download_hostname:
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'page_url' must include a hostname."
            )
            return False

        if download_username is not None:
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'page_url' must not include a username."
            )
            return False

        if download_password is not None:
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'page_url' must not include a password."
            )
            return False

        if (
            download_method == "google_drive"
            and download_hostname != "drive.google.com"
        ):
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'page_url' must use exactly the "
                "drive.google.com hostname for method 'google_drive'."
            )
            return False

        if (
            download_method == "google_drive"
            and "folders" in download_page_url.path.split("/")
        ):
            print(
                f"Invalid mod registry entry at index {index}: "
                "Google Drive folders are not supported; download field "
                "'page_url' must identify one archive file."
            )
            return False

        if (
            download_method == "mega"
            and download_hostname not in MEGA_HOSTNAMES
        ):
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'page_url' must use a supported MEGA "
                "hostname for method 'mega'."
            )
            return False

        if download_method == "mega":
            try:
                parse_mega_public_file_url(
                    download["page_url"]
                )
            except MegaFolderLinkError as error:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    f"{error}"
                )
                return False
            except (MegaNzError, ValueError) as error:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'page_url' is not a valid public "
                    f"MEGA file URL: {error}"
                )
                return False

        if download_method == "direct":
            if "direct_url" not in download:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download is missing required field 'direct_url' "
                    "for method 'direct'."
                )
                return False

            direct_url = download["direct_url"]

            if not isinstance(direct_url, str):
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'direct_url' must be a string."
                )
                return False

            if not direct_url.strip():
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'direct_url' must not be empty."
                )
                return False

            try:
                parsed_direct_url = urllib.parse.urlsplit(
                    direct_url
                )
                direct_hostname = parsed_direct_url.hostname
                direct_username = parsed_direct_url.username
                direct_password = parsed_direct_url.password
            except ValueError as error:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'direct_url' could not be parsed: "
                    f"{error}"
                )
                return False

            if parsed_direct_url.scheme != "https":
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'direct_url' must use the https "
                    "scheme."
                )
                return False

            if not direct_hostname:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'direct_url' must include a "
                    "hostname."
                )
                return False

            if direct_username is not None:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'direct_url' must not include a "
                    "username."
                )
                return False

            if direct_password is not None:
                print(
                    f"Invalid mod registry entry at index {index}: "
                    "download field 'direct_url' must not include a "
                    "password."
                )
                return False

        download_file_name = download["file_name"]

        if (
            "/" in download_file_name
            or "\\" in download_file_name
            or "\x00" in download_file_name
            or download_file_name in (".", "..")
        ):
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'file_name' must be a filename, not "
                "a filesystem path."
            )
            return False

        if not download_file_name.lower().endswith((".zip", ".7z")):
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'file_name' must end with '.zip' or "
                "'.7z'."
            )
            return False

        download_sha256 = download["sha256"]
        valid_sha256_characters = set(
            "0123456789abcdef"
        )

        if (
            len(download_sha256) != 64
            or any(
                character not in valid_sha256_characters
                for character in download_sha256
            )
        ):
            print(
                f"Invalid mod registry entry at index {index}: "
                "download field 'sha256' must be a 64-character "
                "lowercase hexadecimal SHA-256 value."
            )
            return False

    try:
        page_url = urllib.parse.urlsplit(mod["page_url"])
        hostname = page_url.hostname
        username = page_url.username
        password = page_url.password
    except ValueError as error:
        print(
            f"Invalid mod registry entry at index {index}: field "
            f"'page_url' could not be parsed: {error}"
        )
        return False

    if page_url.scheme != "https":
        print(
            f"Invalid mod registry entry at index {index}: field "
            "'page_url' must use the https scheme."
        )
        return False

    if not hostname:
        print(
            f"Invalid mod registry entry at index {index}: field "
            "'page_url' must include a hostname."
        )
        return False

    if username is not None:
        print(
            f"Invalid mod registry entry at index {index}: field "
            "'page_url' must not include a username."
        )
        return False

    if password is not None:
        print(
            f"Invalid mod registry entry at index {index}: field "
            "'page_url' must not include a password."
        )
        return False

    return True


def fetch_mod_registry():
    request = urllib.request.Request(
        MOD_REGISTRY_URL,
        headers={"User-Agent": "DDLC-Mod-Launcher"},
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            response_data = response.read(
                MAX_REGISTRY_SIZE + 1
            )
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as error:
        print(f"Failed to fetch mod registry: {error}")
        return None

    if len(response_data) > MAX_REGISTRY_SIZE:
        print(
            "Failed to load mod registry: response exceeds the "
            "1 MiB size limit."
        )
        return None

    try:
        registry = json.loads(
            response_data.decode("utf-8")
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        print(f"Failed to parse mod registry: {error}")
        return None

    if not isinstance(registry, dict):
        print(
            "Invalid mod registry: the top-level value must be a "
            "dictionary."
        )
        return None

    format_version = registry.get("format_version")

    if (
        not isinstance(format_version, int)
        or isinstance(format_version, bool)
        or format_version != 1
    ):
        print(
            "Invalid mod registry: format_version must be integer 1."
        )
        return None

    mods = registry.get("mods")

    if not isinstance(mods, list):
        print("Invalid mod registry: mods must be a list.")
        return None

    mod_ids = set()

    for index, mod in enumerate(mods):
        if not validate_mod_registry_entry(mod, index):
            return None

        mod_id = mod["id"]

        if mod_id in mod_ids:
            print(
                f"Invalid mod registry entry at index {index}: "
                f"duplicate mod ID '{mod_id}'."
            )
            return None

        mod_ids.add(mod_id)

    print("Mod registry loaded successfully.")
    print("Registry format version: 1")
    print(f"Mods in registry: {len(mods)}")
    return registry


class _MediaFireDownloadLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.download_url = None

    def handle_starttag(self, tag, attrs):
        if self.download_url is not None or tag != "a":
            return

        attributes = dict(attrs)

        if attributes.get("id") != "downloadButton":
            return

        href = attributes.get("href")

        if href:
            self.download_url = href


def resolve_mediafire_download_url(page_url):
    try:
        parsed_page_url = urllib.parse.urlsplit(page_url)
        page_hostname = parsed_page_url.hostname
        page_username = parsed_page_url.username
        page_password = parsed_page_url.password
    except ValueError as error:
        print(
            "Failed to resolve MediaFire download URL: page URL "
            f"could not be parsed: {error}"
        )
        return None

    if parsed_page_url.scheme != "https":
        print(
            "Failed to resolve MediaFire download URL: page URL "
            "must use the https scheme."
        )
        return None

    if not page_hostname:
        print(
            "Failed to resolve MediaFire download URL: page URL "
            "must include a hostname."
        )
        return None

    if not (
        page_hostname == "mediafire.com"
        or page_hostname.endswith(".mediafire.com")
    ):
        print(
            "Failed to resolve MediaFire download URL: page URL "
            "must use a mediafire.com hostname."
        )
        return None

    if page_username is not None:
        print(
            "Failed to resolve MediaFire download URL: page URL "
            "must not include a username."
        )
        return None

    if page_password is not None:
        print(
            "Failed to resolve MediaFire download URL: page URL "
            "must not include a password."
        )
        return None

    request = urllib.request.Request(
        page_url,
        headers={"User-Agent": "DDLC-Mod-Launcher"},
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            response_data = response.read(
                MAX_MEDIAFIRE_PAGE_SIZE + 1
            )
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as error:
        print(
            "Failed to resolve MediaFire download URL: "
            f"{error}"
        )
        return None

    if len(response_data) > MAX_MEDIAFIRE_PAGE_SIZE:
        print(
            "Failed to resolve MediaFire download URL: page exceeds "
            "the 2 MiB size limit."
        )
        return None

    try:
        page_html = response_data.decode("utf-8")
    except UnicodeDecodeError as error:
        print(
            "Failed to resolve MediaFire download URL: page is not "
            f"valid UTF-8: {error}"
        )
        return None

    parser = _MediaFireDownloadLinkParser()
    parser.feed(page_html)
    discovered_url = parser.download_url

    if discovered_url is None:
        print(
            "Failed to resolve MediaFire download URL: download link "
            "not found."
        )
        return None

    try:
        resolved_url = urllib.parse.urljoin(
            page_url,
            discovered_url,
        )
        parsed_resolved_url = urllib.parse.urlsplit(
            resolved_url
        )
        resolved_hostname = parsed_resolved_url.hostname
        resolved_username = parsed_resolved_url.username
        resolved_password = parsed_resolved_url.password
    except ValueError as error:
        print(
            "Failed to resolve MediaFire download URL: resolved URL "
            f"could not be parsed: {error}"
        )
        return None

    if parsed_resolved_url.scheme != "https":
        print(
            "Failed to resolve MediaFire download URL: resolved URL "
            "must use the https scheme."
        )
        return None

    if not resolved_hostname:
        print(
            "Failed to resolve MediaFire download URL: resolved URL "
            "must include a hostname."
        )
        return None

    if resolved_username is not None:
        print(
            "Failed to resolve MediaFire download URL: resolved URL "
            "must not include a username."
        )
        return None

    if resolved_password is not None:
        print(
            "Failed to resolve MediaFire download URL: resolved URL "
            "must not include a password."
        )
        return None

    if not (
        resolved_hostname.startswith("download")
        and resolved_hostname.endswith(".mediafire.com")
    ):
        print(
            "Failed to resolve MediaFire download URL: resolved URL "
            "must use a MediaFire download hostname."
        )
        return None

    return resolved_url


def download_file_to_temp(
    url,
    max_bytes,
    timeout=10,
    progress_callback=None,
):
    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes <= 0
    ):
        print(
            "Failed to download file: max_bytes must be a positive "
            "integer."
        )
        return None

    if not isinstance(url, str) or not url.strip():
        print(
            "Failed to download file: URL must be a non-empty string."
        )
        return None

    try:
        parsed_url = urllib.parse.urlsplit(url)
        hostname = parsed_url.hostname
        username = parsed_url.username
        password = parsed_url.password
    except ValueError as error:
        print(
            "Failed to download file: URL could not be parsed: "
            f"{error}"
        )
        return None

    if parsed_url.scheme != "https":
        print(
            "Failed to download file: URL must use the https scheme."
        )
        return None

    if not hostname:
        print("Failed to download file: URL must include a hostname.")
        return None

    if username is not None:
        print(
            "Failed to download file: URL must not include a username."
        )
        return None

    if password is not None:
        print(
            "Failed to download file: URL must not include a password."
        )
        return None

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "DDLC-Mod-Launcher"},
        )
    except ValueError as error:
        print(f"Failed to download file: invalid request URL: {error}")
        return None

    temporary_path = None
    file_descriptor = None
    download_complete = False

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            final_url = response.url

            try:
                parsed_final_url = urllib.parse.urlsplit(
                    final_url
                )
                final_hostname = parsed_final_url.hostname
                final_username = parsed_final_url.username
                final_password = parsed_final_url.password
            except ValueError as error:
                print(
                    "Failed to download file: final URL could not be "
                    f"parsed: {error}"
                )
                return None

            if parsed_final_url.scheme != "https":
                print(
                    "Failed to download file: final URL must use the "
                    "https scheme."
                )
                return None

            if not final_hostname:
                print(
                    "Failed to download file: final URL must include "
                    "a hostname."
                )
                return None

            if final_username is not None:
                print(
                    "Failed to download file: final URL must not "
                    "include a username."
                )
                return None

            if final_password is not None:
                print(
                    "Failed to download file: final URL must not "
                    "include a password."
                )
                return None

            total_bytes = None
            content_length_header = response.headers.get(
                "Content-Length"
            )

            if content_length_header is not None:
                try:
                    content_length = int(content_length_header)
                except (TypeError, ValueError):
                    print(
                        "Download warning: ignoring malformed "
                        "Content-Length header."
                    )
                else:
                    if content_length < 0:
                        print(
                            "Download warning: ignoring negative "
                            "Content-Length header."
                        )
                    elif content_length > max_bytes:
                        print(
                            "Failed to download file: Content-Length "
                            f"exceeds the {max_bytes}-byte limit."
                        )
                        return None
                    else:
                        total_bytes = content_length

            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix="ddlc-mod-download-",
                suffix=".part",
            )
            temporary_path = Path(temporary_name)
            temporary_file = os.fdopen(
                file_descriptor,
                "wb",
            )
            file_descriptor = None
            received_bytes = 0

            with temporary_file:
                if progress_callback is not None:
                    progress_callback(
                        received_bytes,
                        total_bytes,
                    )

                while True:
                    chunk = response.read(1024 * 1024)

                    if not chunk:
                        break

                    next_received_bytes = (
                        received_bytes + len(chunk)
                    )

                    if next_received_bytes > max_bytes:
                        print(
                            "Failed to download file: received data "
                            f"exceeds the {max_bytes}-byte limit."
                        )
                        return None

                    temporary_file.write(chunk)
                    received_bytes = next_received_bytes

                    if progress_callback is not None:
                        progress_callback(
                            received_bytes,
                            total_bytes,
                        )

            download_complete = True
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as error:
        print(f"Failed to download file: {error}")
        return None
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError as error:
                print(
                    "Failed to close temporary download file: "
                    f"{error}"
                )

        if temporary_path is not None and not download_complete:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                print(
                    "Failed to clean up temporary download file "
                    f"{temporary_path}: {error}"
                )

    print(
        f"Downloaded file to {temporary_path} "
        f"({received_bytes} bytes)."
    )
    return temporary_path


def calculate_file_sha256(file_path):
    file_path = Path(file_path)
    digest = hashlib.sha256()

    try:
        with open(file_path, "rb") as file:
            while True:
                chunk = file.read(1024 * 1024)

                if not chunk:
                    break

                digest.update(chunk)
    except OSError as error:
        print(
            f"Failed to calculate SHA-256 for {file_path}: {error}"
        )
        return None

    return digest.hexdigest()


def verify_file_sha256(file_path, expected_sha256):
    actual_sha256 = calculate_file_sha256(file_path)

    if actual_sha256 is None:
        return None

    return actual_sha256 == expected_sha256


def download_mediafire_mod(
    download,
    max_bytes,
    progress_callback=None,
):
    if not isinstance(download, dict):
        print(
            "Failed to download MediaFire mod: download metadata "
            "must be a dictionary."
        )
        return None

    if download.get("method") != "mediafire":
        print(
            "Failed to download MediaFire mod: download method must "
            "be 'mediafire'."
        )
        return None

    page_url = download["page_url"]
    expected_sha256 = download["sha256"]
    resolved_url = resolve_mediafire_download_url(
        page_url
    )

    if resolved_url is None:
        return None

    temp_path = download_file_to_temp(
        resolved_url,
        max_bytes,
        progress_callback=progress_callback,
    )

    if temp_path is None:
        return None

    verification_result = verify_file_sha256(
        temp_path,
        expected_sha256,
    )

    if verification_result is True:
        print(
            "MediaFire mod downloaded and checksum verified "
            f"successfully: {temp_path}"
        )
        return temp_path

    if verification_result is False:
        print(
            "Failed to download MediaFire mod: checksum verification "
            "failed."
        )
    else:
        print(
            "Failed to download MediaFire mod: checksum verification "
            "could not be completed."
        )

    try:
        temp_path.unlink(missing_ok=True)
    except OSError as error:
        print(
            "Failed to clean up unverified MediaFire download "
            f"{temp_path}: {error}"
        )

    return None


class DownloadSizeLimitExceeded(Exception):
    pass


def _cleanup_google_drive_download(temp_path):
    if temp_path is None:
        return

    paths_to_remove = [temp_path]

    try:
        for candidate in temp_path.parent.iterdir():
            if (
                candidate.name.startswith(temp_path.name)
                and candidate.name.endswith(".part")
                and candidate != temp_path
            ):
                paths_to_remove.append(candidate)
    except OSError as error:
        print(
            "Failed to inspect temporary Google Drive download files "
            f"for cleanup: {error}"
        )

    for path in paths_to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            print(
                "Failed to clean up temporary Google Drive download "
                f"file {path}: {error}"
            )


def download_google_drive_mod(
    download,
    max_bytes,
    progress_callback=None,
):
    if not isinstance(download, dict):
        print(
            "Failed to download Google Drive mod: download metadata "
            "must be a dictionary."
        )
        return None

    if download.get("method") != "google_drive":
        print(
            "Failed to download Google Drive mod: download method must "
            "be 'google_drive'."
        )
        return None

    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes <= 0
    ):
        print(
            "Failed to download Google Drive mod: max_bytes must be a "
            "positive integer."
        )
        return None

    page_url = download.get("page_url")

    if not isinstance(page_url, str) or not page_url.strip():
        print(
            "Failed to download Google Drive mod: page URL must be a "
            "non-empty string."
        )
        return None

    try:
        parsed_page_url = urllib.parse.urlsplit(page_url)
        page_hostname = parsed_page_url.hostname
        page_username = parsed_page_url.username
        page_password = parsed_page_url.password
    except ValueError as error:
        print(
            "Failed to download Google Drive mod: page URL could not be "
            f"parsed: {error}"
        )
        return None

    if parsed_page_url.scheme != "https":
        print(
            "Failed to download Google Drive mod: page URL must use the "
            "https scheme."
        )
        return None

    if page_username is not None:
        print(
            "Failed to download Google Drive mod: page URL must not "
            "include a username."
        )
        return None

    if page_password is not None:
        print(
            "Failed to download Google Drive mod: page URL must not "
            "include a password."
        )
        return None

    if page_hostname != "drive.google.com":
        print(
            "Failed to download Google Drive mod: page URL must use "
            "exactly the drive.google.com hostname."
        )
        return None

    if "folders" in parsed_page_url.path.split("/"):
        print(
            "Failed to download Google Drive mod: Google Drive folders "
            "are not supported."
        )
        return None

    temp_path = None
    file_descriptor = None
    download_verified = False

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="ddlc-google-drive-download-",
            suffix=".part",
        )
        temp_path = Path(temporary_name)
        os.close(file_descriptor)
        file_descriptor = None
        temp_path.unlink()

        def handle_progress(bytes_so_far, bytes_total):
            if bytes_total is not None and bytes_total > max_bytes:
                raise DownloadSizeLimitExceeded(
                    "Google Drive download size exceeds the "
                    f"{max_bytes}-byte limit."
                )

            if bytes_so_far > max_bytes:
                raise DownloadSizeLimitExceeded(
                    "Google Drive download data exceeds the "
                    f"{max_bytes}-byte limit."
                )

            if progress_callback is not None:
                progress_callback(
                    bytes_so_far,
                    bytes_total,
                )

        result = gdown.download(
            url=page_url,
            output=str(temp_path),
            quiet=True,
            use_cookies=False,
            timeout=10,
            progress=handle_progress,
        )

        if result is None:
            print(
                "Failed to download Google Drive mod: gdown did not "
                "report a successful download."
            )
            return None

        try:
            downloaded_status = temp_path.lstat()
        except FileNotFoundError:
            print(
                "Failed to download Google Drive mod: the expected "
                "temporary output was not created."
            )
            return None

        if stat.S_ISLNK(downloaded_status.st_mode):
            print(
                "Failed to download Google Drive mod: the temporary "
                "output is a symbolic link."
            )
            return None

        if not stat.S_ISREG(downloaded_status.st_mode):
            print(
                "Failed to download Google Drive mod: the temporary "
                "output is not a regular file."
            )
            return None

        if downloaded_status.st_size > max_bytes:
            print(
                "Failed to download Google Drive mod: downloaded file "
                f"exceeds the {max_bytes}-byte limit."
            )
            return None

        verification_result = verify_file_sha256(
            temp_path,
            download.get("sha256"),
        )

        if verification_result is not True:
            if verification_result is False:
                print(
                    "Failed to download Google Drive mod: checksum "
                    "verification failed."
                )
            else:
                print(
                    "Failed to download Google Drive mod: checksum "
                    "verification could not be completed."
                )

            return None

        download_verified = True
        print(
            "Google Drive mod downloaded and checksum verified "
            f"successfully: {temp_path}"
        )
        return temp_path
    except DownloadSizeLimitExceeded as error:
        print(f"Failed to download Google Drive mod: {error}")
        return None
    except gdown.exceptions.DownloadError as error:
        print(f"Failed to download Google Drive mod: {error}")
        return None
    except requests.exceptions.RequestException as error:
        print(f"Failed to download Google Drive mod: {error}")
        return None
    except (TimeoutError, OSError, ValueError) as error:
        print(f"Failed to download Google Drive mod: {error}")
        return None
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError as error:
                print(
                    "Failed to close temporary Google Drive download "
                    f"file: {error}"
                )

        if not download_verified:
            _cleanup_google_drive_download(temp_path)


def download_direct_mod(
    download,
    max_bytes,
    progress_callback=None,
):
    if not isinstance(download, dict):
        print(
            "Failed to download Direct mod: download metadata must be "
            "a dictionary."
        )
        return None

    if download.get("method") != "direct":
        print(
            "Failed to download Direct mod: download method must be "
            "'direct'."
        )
        return None

    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes <= 0
    ):
        print(
            "Failed to download Direct mod: max_bytes must be a "
            "positive integer."
        )
        return None

    direct_url = download.get("direct_url")

    if not isinstance(direct_url, str) or not direct_url.strip():
        print(
            "Failed to download Direct mod: direct URL must be a "
            "non-empty string."
        )
        return None

    try:
        parsed_direct_url = urllib.parse.urlsplit(
            direct_url
        )
        direct_hostname = parsed_direct_url.hostname
        direct_username = parsed_direct_url.username
        direct_password = parsed_direct_url.password
    except ValueError as error:
        print(
            "Failed to download Direct mod: direct URL could not be "
            f"parsed: {error}"
        )
        return None

    if parsed_direct_url.scheme != "https":
        print(
            "Failed to download Direct mod: direct URL must use the "
            "https scheme."
        )
        return None

    if not direct_hostname:
        print(
            "Failed to download Direct mod: direct URL must include a "
            "hostname."
        )
        return None

    if direct_username is not None:
        print(
            "Failed to download Direct mod: direct URL must not "
            "include a username."
        )
        return None

    if direct_password is not None:
        print(
            "Failed to download Direct mod: direct URL must not "
            "include a password."
        )
        return None

    expected_file_name = download.get("file_name")

    if (
        not isinstance(expected_file_name, str)
        or not expected_file_name
        or "/" in expected_file_name
        or "\\" in expected_file_name
        or "\x00" in expected_file_name
        or expected_file_name in (".", "..")
        or not expected_file_name.lower().endswith(
            (".zip", ".7z")
        )
    ):
        print(
            "Failed to download Direct mod: expected file_name must "
            "be a safe ZIP or 7z filename."
        )
        return None

    expected_sha256 = download.get("sha256")

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_sha256
        )
    ):
        print(
            "Failed to download Direct mod: expected SHA-256 is "
            "invalid."
        )
        return None

    temp_path = download_file_to_temp(
        direct_url,
        max_bytes,
        progress_callback=progress_callback,
    )

    if temp_path is None:
        return None

    verification_result = verify_file_sha256(
        temp_path,
        expected_sha256,
    )

    if verification_result is True:
        print(
            "Direct mod downloaded and checksum verified "
            f"successfully: {temp_path}"
        )
        return temp_path

    if verification_result is False:
        print(
            "Failed to download Direct mod: checksum verification "
            "failed."
        )
    else:
        print(
            "Failed to download Direct mod: checksum verification "
            "could not be completed."
        )

    try:
        temp_path.unlink(missing_ok=True)
    except OSError as error:
        print(
            "Failed to clean up unverified Direct download "
            f"{temp_path}: {error}"
        )

    return None


def _write_mega_helper_status(
    status_path,
    success,
    message,
    file_name=None,
):
    status = {
        "success": bool(success),
        "message": str(message)[:2000],
    }

    if file_name is not None:
        status["file_name"] = str(file_name)

    with open(
        status_path,
        "w",
        encoding="utf-8",
    ) as status_file:
        json.dump(
            status,
            status_file,
        )
        status_file.write("\n")


def _validate_mega_helper_paths(
    download_directory,
    status_path,
):
    download_directory = Path(download_directory)
    status_path = Path(status_path)

    if not download_directory.is_absolute():
        raise ValueError(
            "MEGA helper download directory must be absolute."
        )

    if not status_path.is_absolute():
        raise ValueError(
            "MEGA helper status path must be absolute."
        )

    download_status = download_directory.lstat()

    if stat.S_ISLNK(download_status.st_mode):
        raise ValueError(
            "MEGA helper download directory must not be a symbolic "
            "link."
        )

    if not stat.S_ISDIR(download_status.st_mode):
        raise ValueError(
            "MEGA helper download path must be a directory."
        )

    if os.name != "nt":
        if download_status.st_uid != os.getuid():
            raise ValueError(
                "MEGA helper download directory is not owned by the "
                "current user."
            )

        if download_status.st_mode & (
            stat.S_IRWXG | stat.S_IRWXO
        ):
            raise ValueError(
                "MEGA helper download directory must be private."
            )

    status_file_status = status_path.lstat()

    if stat.S_ISLNK(status_file_status.st_mode):
        raise ValueError(
            "MEGA helper status path must not be a symbolic link."
        )

    if not stat.S_ISREG(status_file_status.st_mode):
        raise ValueError(
            "MEGA helper status path must be a regular file."
        )

    if os.name != "nt":
        if status_file_status.st_uid != os.getuid():
            raise ValueError(
                "MEGA helper status file is not owned by the current "
                "user."
            )

        if status_file_status.st_mode & (
            stat.S_IRWXG | stat.S_IRWXO
        ):
            raise ValueError(
                "MEGA helper status file must be private."
            )

    resolved_download_directory = download_directory.resolve(
        strict=True
    )
    resolved_status_path = status_path.resolve(
        strict=True
    )

    if (
        resolved_status_path == resolved_download_directory
        or resolved_download_directory
        in resolved_status_path.parents
    ):
        raise ValueError(
            "MEGA helper status path must be outside the download "
            "directory."
        )

    return resolved_download_directory, resolved_status_path


async def _run_mega_download_helper_async(
    public_handle,
    public_key,
    download_directory,
):
    async with MegaNzClient() as mega_client:
        return await mega_client.download_public_file(
            public_handle,
            public_key,
            download_directory,
        )


def run_mega_download_helper(arguments):
    if len(arguments) != 3:
        print(
            "MEGA helper requires a public URL, download directory, "
            "and status path."
        )
        return 2

    page_url, download_directory, status_path = arguments
    status_path = Path(status_path)

    try:
        (
            resolved_download_directory,
            resolved_status_path,
        ) = _validate_mega_helper_paths(
            download_directory,
            status_path,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Invalid MEGA helper path: {error}")
        return 2

    try:
        normalize_mega_public_url(page_url)
    except ValueError as error:
        try:
            _write_mega_helper_status(
                resolved_status_path,
                False,
                f"Invalid public MEGA file URL: {error}",
            )
        except OSError:
            pass

        return 3

    temporary_directory = str(
        resolved_download_directory
    )

    for variable_name in ("TMPDIR", "TEMP", "TMP"):
        os.environ[variable_name] = temporary_directory

    tempfile.tempdir = temporary_directory

    try:
        configured_temp_directory = Path(
            tempfile.gettempdir()
        ).resolve(strict=True)

        if configured_temp_directory != resolved_download_directory:
            raise RuntimeError(
                "Python did not accept the isolated MEGA temporary "
                "directory."
            )

        with tempfile.NamedTemporaryFile() as probe_file:
            probe_parent = Path(
                probe_file.name
            ).resolve(strict=True).parent

            if probe_parent != resolved_download_directory:
                raise RuntimeError(
                    "Temporary files are not isolated inside the MEGA "
                    "download directory."
                )

        (
            _,
            public_handle,
            public_key,
        ) = parse_mega_public_file_url(page_url)
        result_path = asyncio.run(
            _run_mega_download_helper_async(
                public_handle,
                public_key,
                resolved_download_directory,
            )
        )
        result_path = Path(result_path)
        resolved_result_path = result_path.resolve(
            strict=True
        )

        if (
            resolved_result_path.parent
            != resolved_download_directory
        ):
            raise RuntimeError(
                "MEGA download completed outside the isolated "
                "directory."
            )

        _write_mega_helper_status(
            resolved_status_path,
            True,
            "MEGA download completed.",
            file_name=resolved_result_path.name,
        )
        return 0
    except MegaFolderLinkError as error:
        message = str(error)
    except MegaNzError as error:
        message = f"MEGA download failed: {error}"
    except aiohttp.ClientError as error:
        message = f"MEGA network request failed: {error}"
    except (TimeoutError, OSError, RuntimeError, ValueError) as error:
        message = f"MEGA download failed: {error}"
    except Exception as error:
        message = (
            "MEGA provider failed: "
            f"{type(error).__name__}: {error}"
        )

    try:
        _write_mega_helper_status(
            resolved_status_path,
            False,
            message,
        )
    except OSError:
        pass

    return 4


def _inspect_mega_download_directory(download_directory):
    directory_status = download_directory.lstat()

    if stat.S_ISLNK(directory_status.st_mode):
        raise UnsafeMegaDownloadOutputError(
            "Private MEGA download directory became a symbolic link."
        )

    if not stat.S_ISDIR(directory_status.st_mode):
        raise UnsafeMegaDownloadOutputError(
            "Private MEGA download path is no longer a directory."
        )

    total_bytes = 0
    regular_files = []

    with os.scandir(download_directory) as entries:
        for entry in entries:
            entry_path = Path(entry.path)
            entry_status = entry_path.lstat()

            if stat.S_ISLNK(entry_status.st_mode):
                raise UnsafeMegaDownloadOutputError(
                    "MEGA download created a symbolic link: "
                    f"{entry_path.name}"
                )

            if stat.S_ISREG(entry_status.st_mode):
                total_bytes += entry_status.st_size
                regular_files.append(entry_path)
                continue

            raise UnsafeMegaDownloadOutputError(
                "MEGA download created an unsupported filesystem "
                f"object: {entry_path.name}"
            )

    return total_bytes, regular_files


def _stop_mega_helper_process(process):
    if process is None:
        return

    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            if process.poll() is None:
                process.kill()

        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    else:
        process.wait()


def _read_mega_helper_status(status_path):
    with open(status_path, "rb") as status_file:
        status_data = status_file.read(64 * 1024 + 1)

    if len(status_data) > 64 * 1024:
        raise ValueError("MEGA helper status exceeds the size limit.")

    status = json.loads(
        status_data.decode("utf-8")
    )

    if not isinstance(status, dict):
        raise ValueError("MEGA helper status is not a dictionary.")

    if not isinstance(status.get("success"), bool):
        raise ValueError(
            "MEGA helper status has an invalid success value."
        )

    if not isinstance(status.get("message"), str):
        raise ValueError(
            "MEGA helper status has an invalid message."
        )

    return status


def build_mega_helper_command(
    page_url,
    download_directory,
    status_path,
):
    if "__compiled__" in globals():
        command = [sys.executable]
    else:
        command = [sys.executable, str(Path(__file__).resolve())]

    return command + [
        MEGA_DOWNLOAD_HELPER_FLAG,
        page_url,
        str(download_directory),
        str(status_path),
    ]


def download_mega_mod(
    download,
    max_bytes,
    progress_callback=None,
):
    if not isinstance(download, dict):
        print(
            "Failed to download MEGA mod: download metadata must be a "
            "dictionary."
        )
        return None

    if download.get("method") != "mega":
        print(
            "Failed to download MEGA mod: download method must be "
            "'mega'."
        )
        return None

    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes <= 0
    ):
        print(
            "Failed to download MEGA mod: max_bytes must be a positive "
            "integer."
        )
        return None

    page_url = download.get("page_url")
    expected_file_name = download.get("file_name")
    expected_sha256 = download.get("sha256")

    try:
        parse_mega_public_file_url(page_url)
    except MegaFolderLinkError as error:
        print(f"Failed to download MEGA mod: {error}")
        return None
    except (MegaNzError, TypeError, ValueError) as error:
        print(
            "Failed to download MEGA mod: invalid public file URL: "
            f"{error}"
        )
        return None

    if (
        not isinstance(expected_file_name, str)
        or not expected_file_name
        or "/" in expected_file_name
        or "\\" in expected_file_name
        or "\x00" in expected_file_name
        or expected_file_name in (".", "..")
        or not expected_file_name.lower().endswith(
            (".zip", ".7z")
        )
    ):
        print(
            "Failed to download MEGA mod: expected file_name must be a "
            "safe ZIP or 7z filename."
        )
        return None

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_sha256
        )
    ):
        print(
            "Failed to download MEGA mod: expected SHA-256 is invalid."
        )
        return None

    download_directory = None
    status_path = None
    standalone_path = None
    process = None
    download_verified = False
    status_descriptor = None
    standalone_descriptor = None

    try:
        download_directory = Path(
            tempfile.mkdtemp(
                prefix="ddlc-mega-download-",
            )
        ).resolve(strict=True)
        status_descriptor, status_name = tempfile.mkstemp(
            prefix="ddlc-mega-status-",
            suffix=".json",
        )
        status_path = Path(status_name)
        os.close(status_descriptor)
        status_descriptor = None
        status_path = status_path.resolve(
            strict=True
        )

        helper_environment = os.environ.copy()

        for variable_name in ("TMPDIR", "TEMP", "TMP"):
            helper_environment[variable_name] = str(
                download_directory
            )

        if os.name == "nt":
            try:
                ca_bundle_path = Path(requests.certs.where()).resolve(
                    strict=True
                )
                if not ca_bundle_path.is_file():
                    raise ValueError("The CA bundle path is not a regular file.")
            except (OSError, RuntimeError, TypeError, ValueError) as error:
                print(
                    "Failed to download MEGA mod: MEGA TLS certificate "
                    f"bundle could not be located. {error}"
                )
                return None

            helper_environment["SSL_CERT_FILE"] = str(ca_bundle_path)

        process = subprocess.Popen(
            build_mega_helper_command(
                page_url,
                download_directory,
                status_path,
            ),
            env=helper_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )

        while process.poll() is None:
            current_bytes, _ = (
                _inspect_mega_download_directory(
                    download_directory
                )
            )

            if current_bytes > max_bytes:
                _stop_mega_helper_process(process)
                raise DownloadSizeLimitExceeded(
                    "MEGA download data exceeds the "
                    f"{max_bytes}-byte limit."
                )

            if progress_callback is not None:
                progress_callback(
                    current_bytes,
                    None,
                )

            time.sleep(0.1)

        return_code = process.wait()
        current_bytes, downloaded_files = (
            _inspect_mega_download_directory(
                download_directory
            )
        )

        if current_bytes > max_bytes:
            raise DownloadSizeLimitExceeded(
                "MEGA download data exceeds the "
                f"{max_bytes}-byte limit."
            )

        if progress_callback is not None:
            progress_callback(
                current_bytes,
                None,
            )

        helper_status = _read_mega_helper_status(
            status_path
        )

        if return_code != 0 or not helper_status["success"]:
            print(
                "Failed to download MEGA mod: "
                f"{helper_status['message']}"
            )
            return None

        expected_path = (
            download_directory / expected_file_name
        )

        if len(downloaded_files) != 1:
            print(
                "Failed to download MEGA mod: expected exactly one "
                "downloaded file."
            )
            return None

        if downloaded_files[0] != expected_path:
            print(
                "Failed to download MEGA mod: downloaded filename does "
                f"not match the registry filename {expected_file_name!r}."
            )
            return None

        expected_status = expected_path.lstat()

        if stat.S_ISLNK(expected_status.st_mode):
            print(
                "Failed to download MEGA mod: downloaded archive is a "
                "symbolic link."
            )
            return None

        if not stat.S_ISREG(expected_status.st_mode):
            print(
                "Failed to download MEGA mod: downloaded archive is not "
                "a regular file."
            )
            return None

        if expected_status.st_size > max_bytes:
            raise DownloadSizeLimitExceeded(
                "MEGA downloaded archive exceeds the "
                f"{max_bytes}-byte limit."
            )

        verification_result = verify_file_sha256(
            expected_path,
            expected_sha256,
        )

        if verification_result is not True:
            if verification_result is False:
                print(
                    "Failed to download MEGA mod: checksum verification "
                    "failed."
                )
            else:
                print(
                    "Failed to download MEGA mod: checksum verification "
                    "could not be completed."
                )

            return None

        archive_suffix = Path(
            expected_file_name
        ).suffix
        standalone_descriptor, standalone_name = tempfile.mkstemp(
            prefix="ddlc-mega-verified-",
            suffix=archive_suffix,
        )
        standalone_path = Path(standalone_name)
        os.close(standalone_descriptor)
        standalone_descriptor = None
        shutil.move(
            expected_path,
            standalone_path,
        )

        shutil.rmtree(download_directory)
        status_path.unlink()
        download_verified = True
        print(
            "MEGA mod downloaded and checksum verified successfully: "
            f"{standalone_path}"
        )
        return standalone_path
    except DownloadSizeLimitExceeded as error:
        print(f"Failed to download MEGA mod: {error}")
        return None
    except UnsafeMegaDownloadOutputError as error:
        print(f"Failed to download MEGA mod: {error}")
        return None
    except (
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        shutil.Error,
        subprocess.SubprocessError,
        UnicodeDecodeError,
        ValueError,
    ) as error:
        print(f"Failed to download MEGA mod: {error}")
        return None
    finally:
        for descriptor in (
            status_descriptor,
            standalone_descriptor,
        ):
            if descriptor is None:
                continue

            try:
                os.close(descriptor)
            except OSError as error:
                print(
                    "Failed to close a MEGA temporary file: "
                    f"{error}"
                )

        try:
            _stop_mega_helper_process(process)
        except (OSError, subprocess.SubprocessError) as error:
            print(
                "Failed to stop or reap the MEGA helper process: "
                f"{error}"
            )

        if download_directory is not None:
            try:
                shutil.rmtree(
                    download_directory,
                    ignore_errors=False,
                )
            except FileNotFoundError:
                pass
            except OSError as error:
                print(
                    "Failed to clean up private MEGA download "
                    f"directory {download_directory}: {error}"
                )

        if status_path is not None:
            try:
                status_path.unlink(missing_ok=True)
            except OSError as error:
                print(
                    "Failed to clean up MEGA helper status file "
                    f"{status_path}: {error}"
                )

        if standalone_path is not None and not download_verified:
            try:
                standalone_path.unlink(missing_ok=True)
            except OSError as error:
                print(
                    "Failed to clean up unverified MEGA archive "
                    f"{standalone_path}: {error}"
                )


def download_registry_mod(
    download,
    max_bytes,
    progress_callback=None,
):
    if not isinstance(download, dict):
        print(
            "Failed to download registry mod: download metadata must "
            "be a dictionary."
        )
        return None

    download_method = download.get("method")

    if download_method == "mediafire":
        return download_mediafire_mod(
            download,
            max_bytes,
            progress_callback=progress_callback,
        )

    if download_method == "google_drive":
        return download_google_drive_mod(
            download,
            max_bytes,
            progress_callback=progress_callback,
        )

    if download_method == "mega":
        return download_mega_mod(
            download,
            max_bytes,
            progress_callback=progress_callback,
        )

    if download_method == "direct":
        return download_direct_mod(
            download,
            max_bytes,
            progress_callback=progress_callback,
        )

    print(
        "Failed to download registry mod: unsupported download method "
        f"{download_method!r}."
    )
    return None


def download_and_install_mediafire_mod(
    download,
    max_bytes,
    profile_combo=None,
):
    temp_path = download_mediafire_mod(
        download,
        max_bytes,
    )

    if temp_path is None:
        return

    try:
        install_mod_archive(
            temp_path,
            profile_combo,
            logical_file_name=download["file_name"],
        )
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError as error:
            print(
                "Failed to clean up verified temporary mod download "
                f"{temp_path}: {error}"
            )


def sanitize_registry_install_metadata(registry_metadata):
    if not isinstance(registry_metadata, dict):
        return None

    mod_id = registry_metadata.get("id")

    if not isinstance(mod_id, str) or not mod_id:
        return None

    valid_id_characters = set(
        "abcdefghijklmnopqrstuvwxyz0123456789-"
    )

    if any(
        character not in valid_id_characters
        for character in mod_id
    ):
        return None

    if (
        mod_id.startswith("-")
        or mod_id.endswith("-")
        or "--" in mod_id
    ):
        return None

    file_name = registry_metadata.get("file_name")

    if (
        not isinstance(file_name, str)
        or not file_name.strip()
        or "/" in file_name
        or "\\" in file_name
        or "\x00" in file_name
        or file_name in (".", "..")
        or not file_name.lower().endswith((".zip", ".7z"))
    ):
        return None

    expected_sha256 = registry_metadata.get("sha256")

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_sha256
        )
    ):
        return None

    sanitized_metadata = {
        "id": mod_id,
        "file_name": file_name,
        "sha256": expected_sha256,
    }

    if "version" in registry_metadata:
        version = registry_metadata["version"]

        if not isinstance(version, str) or not version.strip():
            return None

        sanitized_metadata["version"] = version

    return sanitized_metadata


def create_registry_install_metadata(mod):
    if not isinstance(mod, dict):
        return None

    download = mod.get("download")

    if not isinstance(download, dict):
        return None

    registry_metadata = {
        "id": mod.get("id"),
        "file_name": download.get("file_name"),
        "sha256": download.get("sha256"),
    }

    if "version" in mod:
        registry_metadata["version"] = mod["version"]

    return sanitize_registry_install_metadata(
        registry_metadata
    )


ONLINE_MOD_STATUS_LABELS = {
    "not_installed": "Not installed",
    "installed": "Installed",
    "update_available": "Update available",
    "archive_changed": "Archive changed",
    "installed_untracked": "Installed",
}


def get_online_mod_install_status(mod):
    result = {
        "status": "installed_untracked",
        "registry_version": None,
        "installed_version": None,
    }
    current_registry_metadata = create_registry_install_metadata(
        mod
    )

    if current_registry_metadata is None:
        return result

    result["registry_version"] = (
        current_registry_metadata.get("version")
    )

    try:
        profiles_folder = get_profiles_folder_path()
        profiles_status = profiles_folder.lstat()
    except FileNotFoundError:
        result["status"] = "not_installed"
        return result
    except (OSError, RuntimeError, ValueError):
        return result

    if (
        stat.S_ISLNK(profiles_status.st_mode)
        or not stat.S_ISDIR(profiles_status.st_mode)
    ):
        return result

    profile_folder = (
        profiles_folder / current_registry_metadata["id"]
    )

    try:
        profile_status = profile_folder.lstat()
    except FileNotFoundError:
        result["status"] = "not_installed"
        return result
    except (OSError, RuntimeError, ValueError):
        return result

    if (
        stat.S_ISLNK(profile_status.st_mode)
        or not stat.S_ISDIR(profile_status.st_mode)
    ):
        return result

    incomplete_marker = (
        profile_folder / ".ddlc_launcher_incomplete"
    )
    sidecar_marker = profiles_folder / (
        f".{current_registry_metadata['id']}.ddlc_launcher_incomplete"
    )

    try:
        incomplete_marker.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return result
    else:
        return result

    try:
        sidecar_marker.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        return result
    else:
        return result

    metadata_path = (
        profile_folder / ".ddlc_launcher_profile.json"
    )

    try:
        metadata_status = metadata_path.lstat()
    except (FileNotFoundError, OSError):
        return result

    if (
        stat.S_ISLNK(metadata_status.st_mode)
        or not stat.S_ISREG(metadata_status.st_mode)
    ):
        return result

    try:
        with open(metadata_path, "rb") as metadata_file:
            metadata_data = metadata_file.read(64 * 1024 + 1)

        if len(metadata_data) > 64 * 1024:
            return result

        profile_metadata = json.loads(
            metadata_data.decode("utf-8")
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return result

    if not isinstance(profile_metadata, dict):
        return result

    if (
        profile_metadata.get("format_version") != 1
        or profile_metadata.get("installation_type")
        not in (
            "legacy_overlay",
            "game_folder_overlay",
            "packaged_runtime",
        )
    ):
        return result

    installed_registry_metadata = (
        sanitize_registry_install_metadata(
            profile_metadata.get("registry")
        )
    )

    if (
        installed_registry_metadata is None
        or installed_registry_metadata["id"]
        != current_registry_metadata["id"]
    ):
        return result

    installed_version = installed_registry_metadata.get(
        "version"
    )
    registry_version = current_registry_metadata.get(
        "version"
    )
    result["installed_version"] = installed_version

    if (
        installed_version is not None
        and registry_version is not None
        and installed_version != registry_version
    ):
        result["status"] = "update_available"
    elif (
        installed_registry_metadata["sha256"]
        == current_registry_metadata["sha256"]
    ):
        result["status"] = "installed"
    else:
        result["status"] = "archive_changed"

    return result


def inspect_update_recovery_state(mod_id):
    """Inspect update leftovers without creating or changing profile data.

    Complete here means a real directory with no incomplete markers; it
    does not validate its contents or establish which copy is authoritative.
    """
    result = {
        "state": "clean",
        "stable_exists": False,
        "staging_exists": False,
        "backup_exists": False,
        "stable_complete": False,
        "stable_incomplete": False,
        "staging_incomplete": False,
        "backup_incomplete": False,
        "stable_sidecar_exists": False,
        "staging_sidecar_exists": False,
        "backup_sidecar_exists": False,
    }

    if (
        not _is_safe_managed_profile_name(mod_id)
        or mod_id == "vanilla"
    ):
        result["state"] = "inspection_error"
        return result

    try:
        profiles_folder = get_profiles_folder_path()
        profiles_status = profiles_folder.lstat()
    except FileNotFoundError:
        return result
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not inspect update recovery profiles: {error}")
        result["state"] = "inspection_error"
        return result

    def require_readable_directory(path, path_status):
        if (
            stat.S_ISLNK(path_status.st_mode)
            or not stat.S_ISDIR(path_status.st_mode)
        ):
            raise OSError(f"Not a real recovery directory: {path}")

        resolved_path = path.resolve(strict=True)
        # Opening an iterator also checks directory readability, even when
        # the three child paths and their markers are all absent.
        with os.scandir(path):
            pass
        return resolved_path

    def inspect_marker(marker_path):
        try:
            marker_status = marker_path.lstat()
        except FileNotFoundError:
            return False

        if (
            stat.S_ISLNK(marker_status.st_mode)
            or not stat.S_ISREG(marker_status.st_mode)
        ):
            raise OSError(
                f"Not a regular recovery marker: {marker_path}"
            )

        return True

    try:
        resolved_profiles_folder = require_readable_directory(
            profiles_folder,
            profiles_status,
        )
        inspected_directories = [(profiles_folder, profiles_status)]

        for role, directory_name in (
            ("stable", mod_id),
            ("staging", f"{mod_id}-update-staging"),
            ("backup", f"{mod_id}-update-backup"),
        ):
            profile_path = profiles_folder / directory_name

            try:
                profile_status = profile_path.lstat()
            except FileNotFoundError:
                profile_status = None
            else:
                result[f"{role}_exists"] = True
                resolved_profile = require_readable_directory(
                    profile_path,
                    profile_status,
                )

                if resolved_profile.parent != resolved_profiles_folder:
                    raise OSError(
                        f"Recovery profile is outside profiles: {profile_path}"
                    )

                inspected_directories.append((profile_path, profile_status))
                result[f"{role}_incomplete"] = inspect_marker(
                    profile_path / ".ddlc_launcher_incomplete"
                )

            sidecar_exists = inspect_marker(
                profiles_folder
                / f".{directory_name}.ddlc_launcher_incomplete"
            )
            result[f"{role}_sidecar_exists"] = sidecar_exists
            result[f"{role}_incomplete"] |= sidecar_exists

        # Fail conservatively if a checked directory disappeared or was
        # replaced while its children were being inspected.
        for directory_path, original_status in inspected_directories:
            current_status = directory_path.lstat()

            if (
                not stat.S_ISDIR(current_status.st_mode)
                or (current_status.st_dev, current_status.st_ino)
                != (original_status.st_dev, original_status.st_ino)
            ):
                raise OSError(
                    f"Recovery directory changed during inspection: {directory_path}"
                )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not inspect update recovery for {mod_id!r}: {error}")
        result["state"] = "inspection_error"
        return result

    stable_exists = result["stable_exists"]
    staging_exists = result["staging_exists"]
    backup_exists = result["backup_exists"]
    result["stable_complete"] = (
        stable_exists and not result["stable_incomplete"]
    )
    orphan_sidecar = any(
        result[f"{role}_sidecar_exists"]
        and not result[f"{role}_exists"]
        for role in ("stable", "staging", "backup")
    )

    if orphan_sidecar and (
        staging_exists
        or backup_exists
        or result["staging_sidecar_exists"]
        or result["backup_sidecar_exists"]
    ):
        result["state"] = "complex_recovery_state"
    elif not staging_exists and not backup_exists:
        # An incomplete ordinary install alone is not an update leftover.
        result["state"] = "clean"
    elif staging_exists and backup_exists and stable_exists:
        result["state"] = "complex_recovery_state"
    elif stable_exists and result["stable_incomplete"] and backup_exists:
        result["state"] = "new_stable_incomplete_with_backup"
    elif result["stable_complete"] and staging_exists and not backup_exists:
        result["state"] = "abandoned_staging"
    elif result["stable_complete"] and backup_exists and not staging_exists:
        result["state"] = "leftover_backup"
    elif not stable_exists and backup_exists and staging_exists:
        result["state"] = "interrupted_before_swap_complete"
    elif not stable_exists and backup_exists and not staging_exists:
        result["state"] = "stable_missing_with_backup"
    else:
        result["state"] = "complex_recovery_state"

    return result


ONLINE_MOD_RECOVERY_MESSAGES = {
    "abandoned_staging": (
        "A leftover update staging profile was found.\n"
        "The installed profile has not been automatically changed or deleted."
    ),
    "leftover_backup": (
        "A leftover update backup was found.\n"
        "The launcher has preserved the available profile data."
    ),
    "interrupted_before_swap_complete": (
        "The normal installed profile is missing, and update staging and "
        "backup data exist.\nNo automatic recovery has been attempted."
    ),
    "stable_missing_with_backup": (
        "The normal installed profile is missing, but update backup data "
        "exists.\nNo automatic recovery has been attempted."
    ),
    "new_stable_incomplete_with_backup": (
        "An interrupted update transaction may be present.\n"
        "The launcher has preserved the available profile data."
    ),
}


UPDATE_RECOVERY_ACTIONS = {
    "discard_staging": {
        "state": "abandoned_staging",
        "confirmation": (
            "Discard leftover update staging data for {mod_name}?\n\n"
            "The installed profile will not be changed.\n"
            "Only the hidden incomplete update staging data will be removed."
        ),
        "success": "Abandoned update staging data was removed.",
    },
    "remove_backup": {
        "state": "leftover_backup",
        "confirmation": (
            "Remove leftover update backup for {mod_name}?\n\n"
            "The installed profile will be kept.\n"
            "The hidden old update backup will be permanently removed."
        ),
        "success": "Leftover update backup was removed.",
    },
    "restore_backup": {
        "state": "stable_missing_with_backup",
        "confirmation": (
            "Restore the previous installed profile for {mod_name}?\n\n"
            "The hidden update backup will be restored as the normal profile.\n"
            "No update staging data will be used."
        ),
        "success": "The previous installed profile was restored.",
    },
    "restore_backup_keep_staging": {
        "state": "interrupted_before_swap_complete",
        "confirmation": (
            "Restore the previous installed profile for {mod_name}?\n\n"
            "The previous profile will be restored from the hidden backup.\n"
            "The unfinished update staging data will be preserved for "
            "separate cleanup."
        ),
        "success": (
            "The previous installed profile was restored. Update staging "
            "data was preserved for separate cleanup."
        ),
    },
}


def get_update_recovery_action(state):
    return next(
        (
            action for action, details in UPDATE_RECOVERY_ACTIONS.items()
            if details["state"] == state
        ),
        None,
    )


def format_online_mod_list_text(mod, install_status, recovery_state=None):
    mod_name = mod["name"]

    if recovery_state is not None and recovery_state["state"] != "clean":
        return f"{mod_name} — Recovery needed"

    status = install_status["status"]

    if status == "not_installed":
        return mod_name

    status_label = ONLINE_MOD_STATUS_LABELS.get(
        status,
        "Installed",
    )
    return f"{mod_name} — {status_label}"


class ModRegistryFetchWorker(QObject):
    finished = Signal(object)

    @Slot()
    def fetch(self):
        result = fetch_mod_registry()
        self.finished.emit(result)


class ModDownloadWorker(QObject):
    finished = Signal(object)
    progress = Signal(object, object)

    def __init__(self, download, max_bytes):
        super().__init__()
        self.download = download
        self.max_bytes = max_bytes

    @Slot()
    def download_mod(self):
        result = download_registry_mod(
            self.download,
            self.max_bytes,
            progress_callback=self.progress.emit,
        )
        self.finished.emit(result)


class OnlineModLibraryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.operation_in_progress = False
        self.registry_thread = None
        self.registry_worker = None
        self.download_thread = None
        self.download_worker = None

    def reject(self):
        if self.operation_in_progress:
            return

        super().reject()


class ModRegistryResultHandler(QObject):
    def __init__(
        self,
        status_label,
        progress_bar,
        mod_list,
        details_label,
        install_selected_mod_button,
        update_selected_mod_button,
        resolve_update_data_button,
        view_mod_page_button,
        profile_combo,
        max_download_bytes,
        search_field,
        status_filter,
        parent=None,
    ):
        super().__init__(parent)
        self.dialog = parent
        self.status_label = status_label
        self.progress_bar = progress_bar
        self.mod_list = mod_list
        self.details_label = details_label
        self.install_selected_mod_button = (
            install_selected_mod_button
        )
        self.update_selected_mod_button = (
            update_selected_mod_button
        )
        self.resolve_update_data_button = resolve_update_data_button
        self.view_mod_page_button = view_mod_page_button
        self.profile_combo = profile_combo
        self.max_download_bytes = max_download_bytes
        self.search_field = search_field
        self.status_filter = status_filter
        self.registry_loaded = False
        self.registry_mods = []
        self.pending_operation = None
        self.pending_mod_id = None
        self.pending_mod_name = None
        self.pending_file_name = None
        self.pending_registry_metadata = None
        self.pending_staging_directory_name = None
        self.mod_install_statuses = {}
        self.mod_recovery_states = {}

    def reset_download_progress(self):
        self.progress_bar.hide()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

    def get_mod_install_status(self, mod):
        mod_id = mod.get("id")
        install_status = self.mod_install_statuses.get(
            mod_id
        )

        if install_status is None:
            install_status = get_online_mod_install_status(mod)
            self.mod_install_statuses[mod_id] = install_status

        return install_status

    def get_mod_recovery_state(self, mod):
        mod_id = mod.get("id")
        recovery_state = self.mod_recovery_states.get(mod_id)

        if recovery_state is None:
            recovery_state = inspect_update_recovery_state(mod_id)
            self.mod_recovery_states[mod_id] = recovery_state

        return recovery_state

    def refresh_mod_status(self, mod):
        install_status = get_online_mod_install_status(mod)
        self.mod_install_statuses[mod["id"]] = install_status
        recovery_state = inspect_update_recovery_state(mod["id"])
        self.mod_recovery_states[mod["id"]] = recovery_state
        return install_status

    def refresh_mod_install_status(self, item):
        mod = item.data(Qt.ItemDataRole.UserRole)
        install_status = self.refresh_mod_status(mod)
        item.setText(
            format_online_mod_list_text(
                mod,
                install_status,
                self.get_mod_recovery_state(mod),
            )
        )
        return install_status

    def update_filter_controls(self):
        enabled = self.registry_loaded and not self.dialog.operation_in_progress
        self.search_field.setEnabled(enabled)
        self.status_filter.setEnabled(enabled)

    @Slot()
    def apply_mod_filters(self):
        if self.registry_loaded and not self.dialog.operation_in_progress:
            self.rebuild_visible_mod_list()

    def rebuild_visible_mod_list(self, preserve_status=False):
        if not self.registry_loaded:
            return

        current = self.mod_list.currentItem()
        selected_id = (
            current.data(Qt.ItemDataRole.UserRole).get("id")
            if current is not None else None
        )
        search = self.search_field.text().strip().casefold()
        selected_filter = self.status_filter.currentText()
        recovery_mods = []
        ordinary_mods = []

        for mod in self.registry_mods:
            if search and not any(
                search in mod[field].casefold()
                for field in ("name", "author", "description")
            ):
                continue

            install_status = self.get_mod_install_status(mod)
            recovery_state = self.get_mod_recovery_state(mod)
            status = install_status["status"]
            needs_recovery = recovery_state["state"] != "clean"
            matches_filter = (
                selected_filter == "All"
                or (
                    selected_filter == "Installed"
                    and status in (
                        "installed", "installed_untracked", "archive_changed"
                    )
                )
                or (selected_filter == "Not installed" and status == "not_installed")
                or (selected_filter == "Update available" and status == "update_available")
                or (selected_filter == "Recovery needed" and needs_recovery)
            )

            if matches_filter:
                group = recovery_mods if needs_recovery else ordinary_mods
                group.append((mod, install_status, recovery_state))

        signals_blocked = self.mod_list.blockSignals(True)
        try:
            self.mod_list.clear()
            selected_row = 0

            for mod, install_status, recovery_state in recovery_mods + ordinary_mods:
                row = self.mod_list.count()
                self.mod_list.addItem(
                    format_online_mod_list_text(mod, install_status, recovery_state)
                )
                self.mod_list.item(row).setData(Qt.ItemDataRole.UserRole, mod)
                if mod["id"] == selected_id:
                    selected_row = row

            if self.mod_list.count():
                self.mod_list.setCurrentRow(selected_row)
        finally:
            self.mod_list.blockSignals(signals_blocked)

        self.show_mod_details(self.mod_list.currentItem(), None)
        if not preserve_status and not self.dialog.operation_in_progress:
            shown = self.mod_list.count()
            total = len(self.registry_mods)
            noun = "mod" if total == 1 else "mods"
            if shown == total:
                self.status_label.setText(f"{total} {noun} available")
            else:
                self.status_label.setText(f"{shown} of {total} {noun} shown")

    def block_operation_for_update_recovery(self, item):
        mod = item.data(Qt.ItemDataRole.UserRole)
        recovery_state = inspect_update_recovery_state(mod["id"])
        self.mod_recovery_states[mod["id"]] = recovery_state
        item.setText(
            format_online_mod_list_text(
                mod,
                self.get_mod_install_status(mod),
                recovery_state,
            )
        )

        if recovery_state["state"] == "clean":
            return False

        self.show_mod_details(item, None)
        self.status_label.setText(
            "Cannot start operation: unresolved update recovery data exists."
        )
        self.rebuild_visible_mod_list(preserve_status=True)
        return True

    def update_selected_mod_actions(self):
        current = self.mod_list.currentItem()

        if current is None:
            self.install_selected_mod_button.setEnabled(False)
            self.update_selected_mod_button.setEnabled(False)
            self.resolve_update_data_button.setEnabled(False)
            self.view_mod_page_button.setEnabled(False)
            return

        mod = current.data(
            Qt.ItemDataRole.UserRole
        )
        download = mod.get("download")
        install_status = self.get_mod_install_status(mod)
        controls_enabled = not self.dialog.operation_in_progress
        recovery_state = self.get_mod_recovery_state(mod)["state"]
        recovery_clean = recovery_state == "clean"
        can_install = (
            recovery_clean
            and isinstance(download, dict)
            and download.get("method")
            in ("mediafire", "google_drive", "mega", "direct")
            and install_status["status"] == "not_installed"
        )
        can_update = (
            recovery_clean
            and isinstance(download, dict)
            and download.get("method")
            in ("mediafire", "google_drive", "mega", "direct")
            and install_status["status"] == "update_available"
        )

        self.install_selected_mod_button.setEnabled(
            controls_enabled and can_install
        )
        self.update_selected_mod_button.setEnabled(
            controls_enabled and can_update
        )
        self.resolve_update_data_button.setEnabled(
            controls_enabled
            and get_update_recovery_action(recovery_state) is not None
        )
        self.view_mod_page_button.setEnabled(
            controls_enabled
        )

    @Slot(object)
    def handle_result(self, registry):
        if registry is None:
            self.registry_loaded = False
            self.registry_mods = []
            self.update_filter_controls()
            self.status_label.setText(
                "Could not load the mod registry."
            )
            self.mod_list.setEnabled(False)
            self.install_selected_mod_button.setEnabled(False)
            self.update_selected_mod_button.setEnabled(False)
            self.resolve_update_data_button.setEnabled(False)
            self.view_mod_page_button.setText(
                "View Mod Page"
            )
            self.view_mod_page_button.setEnabled(False)
            return

        self.registry_loaded = True
        self.registry_mods = registry["mods"]
        self.install_selected_mod_button.setEnabled(False)
        self.update_selected_mod_button.setEnabled(False)
        self.resolve_update_data_button.setEnabled(False)
        self.view_mod_page_button.setText(
            "View Mod Page"
        )
        self.view_mod_page_button.setEnabled(False)
        self.mod_install_statuses.clear()
        self.mod_recovery_states.clear()
        self.rebuild_visible_mod_list()
        self.mod_list.setEnabled(False)

    @Slot()
    def handle_registry_thread_finished(self):
        self.dialog.registry_thread = None
        self.dialog.registry_worker = None
        self.dialog.operation_in_progress = False
        self.mod_list.setEnabled(self.registry_loaded)
        self.update_filter_controls()
        self.rebuild_visible_mod_list()

    @Slot(object, object)
    def show_mod_details(self, current, previous):
        del previous

        if current is None:
            self.install_selected_mod_button.setEnabled(False)
            self.update_selected_mod_button.setEnabled(False)
            self.resolve_update_data_button.setEnabled(False)
            self.view_mod_page_button.setText(
                "View Mod Page"
            )
            self.view_mod_page_button.setEnabled(False)
            self.details_label.setText(
                "No mods match the current search and filter."
                if self.registry_loaded and not self.mod_list.count()
                else "Select a mod to see its details."
            )
            return

        mod = current.data(
            Qt.ItemDataRole.UserRole
        )
        self.view_mod_page_button.setText(
            f"View Mod Page ({mod['page_source']})"
        )
        self.update_selected_mod_actions()
        install_status = self.get_mod_install_status(mod)
        status = install_status["status"]
        status_label = ONLINE_MOD_STATUS_LABELS.get(
            status,
            "Installed",
        )
        details = [
            mod["name"],
            f"By {mod['author']}",
        ]

        if "version" in mod:
            details.append(
                f"Registry version: {mod['version']}"
            )

        installed_version = install_status.get(
            "installed_version"
        )

        if installed_version is not None:
            details.append(
                f"Installed version: {installed_version}"
            )

        details.append(f"Status: {status_label}")

        if status == "update_available":
            details.append(
                "A different registry version is available."
            )
        elif status == "archive_changed":
            details.append(
                "The registry archive differs from the installed "
                "verified archive."
            )
        elif status == "installed_untracked":
            details.append(
                "Update status: Unknown"
            )
            details.append("")
            details.append(
                "This mod was installed before version tracking was "
                "added."
            )

        recovery_state = self.get_mod_recovery_state(mod)["state"]

        if recovery_state != "clean":
            recovery_message = ONLINE_MOD_RECOVERY_MESSAGES.get(recovery_state)
            recovery_heading = "Update recovery: Attention required"

            if recovery_message is None:
                recovery_heading = "Update recovery: Manual attention required"
                recovery_message = (
                    "The launcher found an update state it cannot safely "
                    "resolve automatically.\nNo files were changed."
                )

            details.extend(("", recovery_heading, "", recovery_message))

            if get_update_recovery_action(recovery_state) is None:
                details.extend((
                    "",
                    "Automatic recovery is unavailable for this state.",
                    "The launcher preserved the available data to avoid "
                    "losing saves or profile settings.",
                ))

        details.extend(
            (
                "",
                mod["description"],
            )
        )
        self.details_label.setText(
            "\n".join(details)
        )

    @Slot()
    def open_selected_mod_page(self):
        current = self.mod_list.currentItem()

        if current is None:
            self.view_mod_page_button.setEnabled(False)
            return

        mod = current.data(
            Qt.ItemDataRole.UserRole
        )
        page_url = mod["page_url"]
        QDesktopServices.openUrl(
            QUrl(page_url)
        )

    @Slot()
    def resolve_selected_update_data(self):
        if self.dialog.operation_in_progress:
            return

        current = self.mod_list.currentItem()

        if current is None:
            self.resolve_update_data_button.setEnabled(False)
            return

        mod = current.data(Qt.ItemDataRole.UserRole)
        self.refresh_mod_install_status(current)
        self.show_mod_details(current, None)
        action = get_update_recovery_action(
            self.get_mod_recovery_state(mod)["state"]
        )

        if action is None:
            self.status_label.setText(
                "Automatic recovery is unavailable for this state. "
                "No files were changed."
            )
            self.rebuild_visible_mod_list(preserve_status=True)
            return

        action_details = UPDATE_RECOVERY_ACTIONS[action]
        self.dialog.operation_in_progress = True
        self.update_filter_controls()
        self.mod_list.setEnabled(False)
        self.update_selected_mod_actions()

        try:
            confirmation = QMessageBox.question(
                self.dialog,
                "Resolve Update Data",
                action_details["confirmation"].format(mod_name=mod["name"]),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )

            if confirmation != QMessageBox.StandardButton.Yes:
                return

            success = resolve_update_recovery(mod["id"], action)

            if success:
                if self.profile_combo is not None:
                    refresh_profile_combo(self.profile_combo)
                self.status_label.setText(action_details["success"])
            else:
                self.status_label.setText(
                    "Recovery could not be completed. Existing recovery "
                    "data was preserved where possible."
                )
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Could not complete recovery UI refresh for {mod['id']!r}: {error}")
            self.status_label.setText(
                "Recovery could not be completed. Existing recovery "
                "data was preserved where possible."
            )
        finally:
            self.dialog.operation_in_progress = False
            self.mod_list.setEnabled(self.registry_loaded)
            self.update_filter_controls()
            self.refresh_mod_status(mod)
            self.rebuild_visible_mod_list(preserve_status=True)

    @Slot()
    def install_selected_mod(self):
        if self.dialog.operation_in_progress:
            return

        current = self.mod_list.currentItem()

        if current is None:
            self.install_selected_mod_button.setEnabled(False)
            return

        mod = current.data(
            Qt.ItemDataRole.UserRole
        )

        if self.block_operation_for_update_recovery(current):
            return

        download = mod.get("download")
        install_status = self.get_mod_install_status(mod)

        if (
            not isinstance(download, dict)
            or download.get("method")
            not in ("mediafire", "google_drive", "mega", "direct")
            or install_status["status"] != "not_installed"
        ):
            self.install_selected_mod_button.setEnabled(False)
            return

        registry_metadata = create_registry_install_metadata(mod)

        if registry_metadata is None:
            self.install_selected_mod_button.setEnabled(False)
            self.status_label.setText(
                "Cannot install mod: registry tracking metadata is "
                "invalid."
            )
            return

        self.dialog.operation_in_progress = True
        self.update_filter_controls()
        self.pending_operation = "install"
        self.pending_mod_id = mod["id"]
        self.pending_mod_name = mod["name"]
        self.pending_file_name = download["file_name"]
        self.pending_registry_metadata = registry_metadata
        self.pending_staging_directory_name = None
        self.resolve_update_data_button.setEnabled(False)
        self.status_label.setText(
            f"Downloading {mod['name']}..."
        )
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.mod_list.setEnabled(False)
        self.install_selected_mod_button.setEnabled(False)
        self.update_selected_mod_button.setEnabled(False)
        self.view_mod_page_button.setEnabled(False)

        self.start_download(download)

    @Slot()
    def update_selected_mod(self):
        if self.dialog.operation_in_progress:
            return

        current = self.mod_list.currentItem()

        if current is None:
            self.update_selected_mod_button.setEnabled(False)
            return

        mod = current.data(
            Qt.ItemDataRole.UserRole
        )

        if self.block_operation_for_update_recovery(current):
            return

        download = mod.get("download")
        install_status = self.refresh_mod_install_status(current)

        if (
            not isinstance(download, dict)
            or download.get("method")
            not in ("mediafire", "google_drive", "mega", "direct")
            or install_status["status"] != "update_available"
        ):
            self.update_selected_mod_button.setEnabled(False)
            self.rebuild_visible_mod_list(preserve_status=True)
            return

        registry_metadata = create_registry_install_metadata(mod)

        if registry_metadata is None:
            self.update_selected_mod_button.setEnabled(False)
            self.status_label.setText(
                "Cannot update mod: registry tracking metadata is "
                "invalid."
            )
            return

        mod_id = mod["id"]
        staging_directory_name = f"{mod_id}-update-staging"
        backup_directory_name = f"{mod_id}-update-backup"

        try:
            profiles_folder = get_profiles_folder()
            resolved_profiles_folder = profiles_folder.resolve(
                strict=True
            )
            stable_profile = profiles_folder / mod_id
            stable_status = stable_profile.lstat()
            resolved_stable_profile = stable_profile.resolve(
                strict=True
            )
        except (OSError, RuntimeError, ValueError) as error:
            print(
                "Cannot start update: stable profile could not be "
                f"verified: {error}"
            )
            self.update_selected_mod_button.setEnabled(False)
            self.status_label.setText(
                "Cannot start update: the installed profile could "
                "not be verified."
            )
            return

        if (
            registry_metadata["id"] != mod_id
            or stable_profile.name != mod_id
            or stat.S_ISLNK(stable_status.st_mode)
            or not stat.S_ISDIR(stable_status.st_mode)
            or resolved_stable_profile.parent
            != resolved_profiles_folder
        ):
            print(
                "Cannot start update: the stable physical profile "
                f"does not match registry mod ID {mod_id!r}: "
                f"{stable_profile}"
            )
            self.update_selected_mod_button.setEnabled(False)
            self.status_label.setText(
                "Cannot start update: the installed profile does not "
                "match the registry mod ID."
            )
            return

        for leftover_path in (
            profiles_folder / staging_directory_name,
            profiles_folder / backup_directory_name,
        ):
            try:
                leftover_path.lstat()
            except FileNotFoundError:
                continue
            except OSError as error:
                print(
                    "Cannot start update: failed to inspect update "
                    f"path {leftover_path}: {error}"
                )
                self.update_selected_mod_button.setEnabled(False)
                self.block_operation_for_update_recovery(current)
                self.status_label.setText(
                    "Cannot start operation: unresolved update recovery data exists."
                )
                return
            else:
                print(
                    "Cannot start update: leftover update staging/"
                    f"backup data exists: {leftover_path}"
                )
                self.update_selected_mod_button.setEnabled(False)
                self.block_operation_for_update_recovery(current)
                self.status_label.setText(
                    "Cannot start operation: unresolved update recovery data exists."
                )
                return

        confirmation_lines = [
            f"Update {mod['name']}?",
            "",
        ]
        installed_version = install_status.get(
            "installed_version"
        )
        registry_version = install_status.get(
            "registry_version"
        )

        if installed_version is not None:
            confirmation_lines.append(
                f"Installed version: {installed_version}"
            )

        if registry_version is not None:
            confirmation_lines.append(
                f"Registry version: {registry_version}"
            )

        if (
            installed_version is not None
            or registry_version is not None
        ):
            confirmation_lines.append("")

        confirmation_lines.append(
            "The launcher will download the verified new release, "
            "build it in a hidden staging profile, preserve saves and "
            "profile settings, and replace the old version only after "
            "the new installation is ready."
        )
        confirmation_result = QMessageBox.question(
            self.dialog,
            "Confirm Mod Update",
            "\n".join(confirmation_lines),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if confirmation_result != QMessageBox.StandardButton.Yes:
            return

        if self.block_operation_for_update_recovery(current):
            return

        self.dialog.operation_in_progress = True
        self.update_filter_controls()
        self.pending_operation = "update"
        self.pending_mod_id = mod_id
        self.pending_mod_name = mod["name"]
        self.pending_file_name = download["file_name"]
        self.pending_registry_metadata = registry_metadata
        self.pending_staging_directory_name = (
            staging_directory_name
        )
        self.resolve_update_data_button.setEnabled(False)
        self.status_label.setText(
            f"Downloading update for {mod['name']}..."
        )
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.mod_list.setEnabled(False)
        self.install_selected_mod_button.setEnabled(False)
        self.update_selected_mod_button.setEnabled(False)
        self.view_mod_page_button.setEnabled(False)

        self.start_download(download)

    def start_download(self, download):
        thread = QThread()
        worker = ModDownloadWorker(
            download,
            self.max_download_bytes,
        )
        worker.moveToThread(thread)

        thread.started.connect(
            worker.download_mod
        )
        worker.progress.connect(
            self.handle_download_progress
        )
        worker.finished.connect(
            self.handle_download_result
        )
        worker.finished.connect(
            thread.quit
        )
        worker.finished.connect(
            worker.deleteLater
        )
        thread.finished.connect(
            self.handle_download_thread_finished
        )
        thread.finished.connect(
            thread.deleteLater
        )

        self.dialog.download_thread = thread
        self.dialog.download_worker = worker

        thread.start()

    @Slot(object, object)
    def handle_download_progress(
        self,
        received_bytes,
        total_bytes,
    ):
        received_megabytes = received_bytes / (1024 ** 2)

        if self.pending_operation == "update":
            progress_prefix = (
                f"Downloading update for {self.pending_mod_name}..."
            )
        else:
            progress_prefix = (
                f"Downloading {self.pending_mod_name}..."
            )

        if total_bytes is not None and total_bytes > 0:
            percentage = (
                received_bytes * 100
            ) // total_bytes
            percentage = max(
                0,
                min(100, percentage),
            )
            total_megabytes = total_bytes / (1024 ** 2)
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(percentage)
            self.status_label.setText(
                f"{progress_prefix} "
                f"{percentage}% ({received_megabytes:.1f} MB / "
                f"{total_megabytes:.1f} MB)"
            )
        else:
            self.progress_bar.setRange(0, 0)
            self.status_label.setText(
                f"{progress_prefix} "
                f"{received_megabytes:.1f} MB downloaded"
            )

    @Slot(object)
    def handle_download_result(self, temp_path):
        if temp_path is None:
            self.reset_download_progress()
            self.status_label.setText(
                "Download failed."
            )
            return

        self.reset_download_progress()

        if self.pending_operation == "update":
            self.handle_verified_update_download(temp_path)
            return

        self.status_label.setText(
            "Download verified. Installing..."
        )

        try:
            install_result = install_mod_archive(
                temp_path,
                self.profile_combo,
                profile_name_override=self.pending_mod_id,
                logical_file_name=self.pending_file_name,
                registry_metadata=self.pending_registry_metadata,
            )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as error:
                print(
                    "Failed to clean up verified temporary mod "
                    f"download {temp_path}: {error}"
                )

        if install_result is True:
            self.status_label.setText(
                "Installed successfully."
            )
        else:
            self.status_label.setText(
                "Installation failed."
            )

    def handle_verified_update_download(self, temp_path):
        self.status_label.setText(
            "Download verified. Building update..."
        )

        try:
            install_result = install_mod_archive(
                temp_path,
                profile_combo=None,
                profile_name_override=self.pending_mod_id,
                logical_file_name=self.pending_file_name,
                registry_metadata=self.pending_registry_metadata,
                profile_directory_name_override=(
                    self.pending_staging_directory_name
                ),
                keep_profile_incomplete=True,
            )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as error:
                print(
                    "Failed to clean up verified temporary mod "
                    f"download {temp_path}: {error}"
                )

        if install_result is not True:
            staging_path = get_profiles_folder_path() / (
                self.pending_staging_directory_name
            )

            try:
                staging_path.lstat()
            except FileNotFoundError:
                pass
            except OSError as error:
                print(
                    "Failed to inspect preserved update staging path "
                    f"{staging_path}: {error}"
                )
            else:
                print(
                    "Incomplete update staging data was preserved: "
                    f"{staging_path}"
                )

            self.status_label.setText(
                "Update could not be prepared. The installed version "
                "was not replaced."
            )
            return

        self.status_label.setText(
            "Update prepared. Applying update..."
        )
        commit_result = commit_staged_profile_update(
            self.pending_mod_id,
            self.pending_staging_directory_name,
        )
        refresh_profile_combo(self.profile_combo)

        if commit_result is True:
            self.status_label.setText(
                "Updated successfully."
            )
        else:
            self.status_label.setText(
                "Update could not be completed. Update/backup data "
                "was preserved where possible. See the console for "
                "details."
            )

    @Slot()
    def handle_download_thread_finished(self):
        self.dialog.download_thread = None
        self.dialog.download_worker = None
        self.dialog.operation_in_progress = False
        for mod in self.registry_mods:
            if mod["id"] == self.pending_mod_id:
                self.refresh_mod_status(mod)
                break

        self.pending_operation = None
        self.pending_mod_id = None
        self.pending_mod_name = None
        self.pending_file_name = None
        self.pending_registry_metadata = None
        self.pending_staging_directory_name = None
        self.reset_download_progress()
        self.mod_list.setEnabled(self.registry_loaded)
        self.update_filter_controls()
        self.rebuild_visible_mod_list(preserve_status=True)


def open_online_mod_library(
    parent,
    profile_combo,
    max_download_bytes,
):
    dialog = OnlineModLibraryDialog(parent)
    dialog.setWindowTitle("Online Mod Library")
    dialog.resize(550, 400)

    layout = QVBoxLayout(dialog)

    status_label = QLabel(
        "Loading mod registry..."
    )
    layout.addWidget(status_label)

    progress_bar = QProgressBar()
    progress_bar.setRange(0, 100)
    progress_bar.setValue(0)
    progress_bar.hide()
    layout.addWidget(progress_bar)

    search_field = QLineEdit()
    search_field.setPlaceholderText("Search mods...")
    search_field.setEnabled(False)
    layout.addWidget(search_field)

    status_filter = QComboBox()
    status_filter.addItems((
        "All", "Installed", "Not installed", "Update available", "Recovery needed"
    ))
    status_filter.setEnabled(False)
    layout.addWidget(status_filter)

    mod_list = QListWidget()
    mod_list.setEnabled(False)
    layout.addWidget(mod_list)

    details_label = QLabel(
        "Select a mod to see its details."
    )
    details_label.setWordWrap(True)
    layout.addWidget(details_label)

    install_selected_mod_button = QPushButton(
        "Install Selected Mod"
    )
    install_selected_mod_button.setEnabled(False)
    layout.addWidget(install_selected_mod_button)

    update_selected_mod_button = QPushButton(
        "Update Selected Mod"
    )
    update_selected_mod_button.setEnabled(False)
    layout.addWidget(update_selected_mod_button)

    resolve_update_data_button = QPushButton("Resolve Update Data")
    resolve_update_data_button.setEnabled(False)
    layout.addWidget(resolve_update_data_button)

    view_mod_page_button = QPushButton(
        "View Mod Page"
    )
    view_mod_page_button.setEnabled(False)
    layout.addWidget(view_mod_page_button)

    button_box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Close
    )
    button_box.rejected.connect(
        dialog.reject
    )
    layout.addWidget(button_box)

    result_handler = ModRegistryResultHandler(
        status_label,
        progress_bar,
        mod_list,
        details_label,
        install_selected_mod_button,
        update_selected_mod_button,
        resolve_update_data_button,
        view_mod_page_button,
        profile_combo,
        max_download_bytes,
        search_field,
        status_filter,
        dialog,
    )
    search_field.textChanged.connect(result_handler.apply_mod_filters)
    status_filter.currentTextChanged.connect(result_handler.apply_mod_filters)
    mod_list.currentItemChanged.connect(
        result_handler.show_mod_details
    )
    view_mod_page_button.clicked.connect(
        result_handler.open_selected_mod_page
    )
    install_selected_mod_button.clicked.connect(
        result_handler.install_selected_mod
    )
    update_selected_mod_button.clicked.connect(
        result_handler.update_selected_mod
    )
    resolve_update_data_button.clicked.connect(
        result_handler.resolve_selected_update_data
    )

    thread = QThread()
    worker = ModRegistryFetchWorker()
    worker.moveToThread(thread)

    thread.started.connect(
        worker.fetch
    )
    worker.finished.connect(
        result_handler.handle_result
    )
    worker.finished.connect(
        thread.quit
    )
    worker.finished.connect(
        worker.deleteLater
    )
    thread.finished.connect(
        result_handler.handle_registry_thread_finished
    )
    thread.finished.connect(
        thread.deleteLater
    )

    dialog.registry_thread = thread
    dialog.registry_worker = worker
    dialog.registry_result_handler = result_handler

    dialog.operation_in_progress = True
    thread.start()
    dialog.exec()


def open_selected_profile_folder(profile_combo):
    profile_path = profile_combo.currentData()

    if not profile_path:
        QMessageBox.warning(
            None,
            "No Profile Selected",
            "Please select a profile first.",
        )
        return

    profile_folder = Path(profile_path)

    if not profile_folder.is_dir():
        QMessageBox.warning(
            None,
            "Profile Not Found",
            "The selected profile no longer exists.",
        )
        return

    QDesktopServices.openUrl(
        QUrl.fromLocalFile(str(profile_folder))
    )


def _resolve_managed_profiles_directory():
    profiles_folder = get_profiles_folder_path()
    folder_status = profiles_folder.lstat()
    if stat.S_ISLNK(folder_status.st_mode) or not stat.S_ISDIR(folder_status.st_mode):
        raise ValueError("The managed profiles path is not a real directory.")
    return profiles_folder.resolve(strict=True)


def _resolve_managed_profile_directory(profile_folder, resolved_profiles=None):
    profile_folder = Path(profile_folder)
    if resolved_profiles is None:
        resolved_profiles = _resolve_managed_profiles_directory()

    profile_status = profile_folder.lstat()
    if stat.S_ISLNK(profile_status.st_mode) or not stat.S_ISDIR(profile_status.st_mode):
        raise ValueError("The profile path is not a real directory.")

    resolved_profile = profile_folder.resolve(strict=True)
    if resolved_profile.parent != resolved_profiles:
        raise ValueError("The profile is not a direct child of managed profiles.")
    return resolved_profile


def _profile_has_incomplete_marker(profile_folder):
    for marker in (
        profile_folder / ".ddlc_launcher_incomplete",
        profile_folder.parent / f".{profile_folder.name}.ddlc_launcher_incomplete",
    ):
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        return True
    return False


def _read_profile_metadata(metadata_path):
    """Read optional metadata without following links or changing files."""
    try:
        metadata_status = metadata_path.lstat()
    except FileNotFoundError:
        return None

    if stat.S_ISLNK(metadata_status.st_mode) or not stat.S_ISREG(metadata_status.st_mode):
        raise ValueError("Profile metadata is not a regular non-symlink file.")
    if metadata_status.st_size > 64 * 1024:
        raise ValueError("Profile metadata exceeds 64 KiB.")

    with open(metadata_path, "rb") as metadata_file:
        metadata_data = metadata_file.read(64 * 1024 + 1)
    if len(metadata_data) > 64 * 1024:
        raise ValueError("Profile metadata exceeds 64 KiB.")

    metadata = json.loads(metadata_data.decode("utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Profile metadata is not a dictionary.")
    version = metadata.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("Profile metadata format_version must be integer 1.")
    return metadata


def _check_packaged_profile_launchers(profile_folder, metadata):
    """Validate current-platform launcher paths without selecting or repairing one."""
    if sys.platform.startswith("linux"):
        current_platform = "linux"
    elif sys.platform.startswith("win"):
        current_platform = "windows"
    else:
        raise ValueError("Packaged-runtime profiles are unsupported on this platform.")

    launchers = metadata.get("launchers")
    if not isinstance(launchers, dict):
        raise ValueError("Packaged-runtime launchers must be a dictionary.")
    platform_launchers = launchers.get(current_platform)
    if not isinstance(platform_launchers, list) or not platform_launchers:
        raise ValueError("No valid current-platform launcher list is present.")

    for launcher_name in platform_launchers:
        if not isinstance(launcher_name, str) or not launcher_name.strip():
            raise ValueError("A packaged-runtime launcher path is empty or invalid.")
        if (
            "\x00" in launcher_name
            or PurePosixPath(launcher_name).is_absolute()
            or PureWindowsPath(launcher_name).root
            or PureWindowsPath(launcher_name).drive
        ):
            raise ValueError("A packaged-runtime launcher path is not relative.")

        launcher = (profile_folder / launcher_name).resolve(strict=True)
        if not path_is_inside_profile(profile_folder, launcher):
            raise ValueError("A packaged-runtime launcher resolves outside its profile.")
        if not stat.S_ISREG(launcher.lstat().st_mode):
            raise ValueError("A packaged-runtime launcher is not a regular file.")


def inspect_profile_health(profile_folder):
    """Inspect managed profile health without creating, repairing or deleting data."""
    profile_folder = Path(profile_folder)
    result = {
        "status": "ready",
        "profile_type": "Vanilla" if profile_folder.name == "Vanilla" else "Unknown",
        "saves_status": "Unknown",
        "display_name": profile_folder.name,
        "issues": [],
    }

    def add_issue(severity, message):
        if severity == "error" or result["status"] == "ready":
            result["status"] = severity
        # Keep blocking issues before optional metadata warnings for Play.
        if severity == "error":
            result["issues"].insert(0, message)
        else:
            result["issues"].append(message)

    try:
        profile_folder = _resolve_managed_profile_directory(profile_folder)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not inspect managed profile path {profile_folder}: {error}")
        add_issue("error", "Profile is missing or its managed folder path is unsafe.")
        return result

    try:
        if _profile_has_incomplete_marker(profile_folder):
            add_issue("error", "Profile is incomplete or contains unfinished update data.")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not inspect incomplete markers for {profile_folder}: {error}")
        add_issue("error", "Profile completion status could not be verified.")

    if profile_folder.name != "Vanilla":
        try:
            metadata = _read_profile_metadata(
                profile_folder / ".ddlc_launcher_profile.json"
            )
            if metadata is None:
                result["profile_type"] = "Legacy overlay"
            else:
                installation_type = metadata.get("installation_type")
                if not isinstance(installation_type, str):
                    raise ValueError("Profile installation_type must be a string.")
                profile_type = {
                    "legacy_overlay": "Legacy overlay",
                    "game_folder_overlay": "Game-folder overlay",
                    "packaged_runtime": "Packaged runtime",
                }.get(installation_type)
                if profile_type is None:
                    raise ValueError("Profile installation_type is invalid.")
                result["profile_type"] = profile_type
                if profile_type == "Packaged runtime":
                    try:
                        _check_packaged_profile_launchers(profile_folder, metadata)
                    except (OSError, RuntimeError, ValueError) as error:
                        print(f"Invalid packaged-runtime metadata for {profile_folder}: {error}")
                        add_issue(
                            "error",
                            "Packaged-runtime launcher metadata is invalid or a launcher is missing or unsafe.",
                        )
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Invalid profile launcher metadata for {profile_folder}: {error}")
            add_issue("error", "Profile launcher metadata is invalid.")

    try:
        user_metadata = _read_profile_metadata(
            profile_folder / ".ddlc_launcher_user.json"
        )
        if user_metadata is not None:
            display_name = user_metadata.get("display_name")
            if isinstance(display_name, str) and display_name.strip():
                result["display_name"] = display_name.strip()
            else:
                raise ValueError("User metadata display_name is empty or invalid.")
            if not isinstance(user_metadata.get("notes"), str):
                raise ValueError("User metadata notes must be a string.")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Invalid profile user metadata for {profile_folder}: {error}")
        add_issue("warning", "User profile metadata is invalid.")

    try:
        saves_status = (profile_folder / "saves").lstat()
    except FileNotFoundError:
        result["saves_status"] = "None"
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not inspect profile saves for {profile_folder}: {error}")
        result["saves_status"] = "Invalid"
        add_issue("error", "Profile saves path could not be verified.")
    else:
        if stat.S_ISLNK(saves_status.st_mode) or not stat.S_ISDIR(saves_status.st_mode):
            result["saves_status"] = "Invalid"
            add_issue("error", "Profile saves path is invalid; a real directory is required.")
        else:
            result["saves_status"] = "Present"

    return result


def get_profile_display_name(profile_folder):
    profile_folder = Path(profile_folder)

    try:
        user_metadata = _read_profile_metadata(
            profile_folder / ".ddlc_launcher_user.json"
        )
    except (OSError, RuntimeError, ValueError):
        return profile_folder.name

    if not isinstance(user_metadata, dict):
        return profile_folder.name

    display_name = user_metadata.get("display_name")

    if not isinstance(display_name, str):
        return profile_folder.name

    display_name = display_name.strip()

    if not display_name:
        return profile_folder.name

    return display_name


def get_profile_notes(profile_folder):
    profile_folder = Path(profile_folder)

    try:
        user_metadata = _read_profile_metadata(
            profile_folder / ".ddlc_launcher_user.json"
        )
    except (OSError, RuntimeError, ValueError):
        return ""

    if not isinstance(user_metadata, dict):
        return ""

    notes = user_metadata.get("notes")

    if not isinstance(notes, str):
        return ""

    return notes


def save_profile_display_name(profile_folder, display_name):
    profile_folder = Path(profile_folder)

    try:
        profiles_folder = get_profiles_folder()
        resolved_profiles_folder = profiles_folder.resolve(
            strict=True
        )
        profile_status = profile_folder.lstat()
    except FileNotFoundError:
        print(
            "Cannot save profile display name: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to inspect profile display name target: {error}")
        return False

    if stat.S_ISLNK(profile_status.st_mode):
        print(
            "Cannot save profile display name: symbolic-link profiles "
            f"are not allowed: {profile_folder}"
        )
        return False

    try:
        resolved_profile = profile_folder.resolve(
            strict=True
        )
    except FileNotFoundError:
        print(
            "Cannot save profile display name: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to resolve profile display name target: {error}")
        return False

    if resolved_profile.parent != resolved_profiles_folder:
        print(
            "Cannot save profile display name: the requested path is "
            "not a direct child of the managed profiles directory: "
            f"{profile_folder}"
        )
        return False

    if resolved_profile == resolved_profiles_folder / "Vanilla":
        print(
            "Cannot save profile display name: Vanilla is protected."
        )
        return False

    if not stat.S_ISDIR(profile_status.st_mode):
        print(
            "Cannot save profile display name: the requested profile "
            f"is not a directory: {profile_folder}"
        )
        return False

    if not isinstance(display_name, str):
        print(
            "Cannot save profile display name: the display name must "
            "be a string."
        )
        return False

    display_name = display_name.strip()

    if not display_name:
        print(
            "Cannot save profile display name: the display name cannot "
            "be empty."
        )
        return False

    metadata_path = (
        resolved_profile / ".ddlc_launcher_user.json"
    )

    try:
        metadata_status = metadata_path.lstat()
    except FileNotFoundError:
        metadata_exists = False
    except OSError as error:
        print(f"Failed to inspect profile user metadata: {error}")
        return False
    else:
        metadata_exists = True

        if stat.S_ISLNK(metadata_status.st_mode):
            print(
                "Cannot save profile display name: the user metadata "
                f"path is a symbolic link: {metadata_path}"
            )
            return False

        if not stat.S_ISREG(metadata_status.st_mode):
            print(
                "Cannot save profile display name: the user metadata "
                f"path is not a regular file: {metadata_path}"
            )
            return False

    existing_metadata = {}

    if metadata_exists:
        try:
            with open(
                metadata_path,
                "r",
                encoding="utf-8",
            ) as metadata_file:
                existing_metadata = json.load(metadata_file)
        except (
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as error:
            print(
                "Failed to read existing profile user metadata: "
                f"{error}"
            )
            return False

        if not isinstance(existing_metadata, dict):
            print(
                "Cannot save profile display name: existing user "
                "metadata is not a dictionary."
            )
            return False

    user_metadata = {
        "format_version": 1,
        "display_name": display_name,
    }
    existing_notes = existing_metadata.get("notes")

    if isinstance(existing_notes, str):
        user_metadata["notes"] = existing_notes

    try:
        with open(
            metadata_path,
            "w",
            encoding="utf-8",
        ) as metadata_file:
            json.dump(
                user_metadata,
                metadata_file,
                indent=2,
            )
            metadata_file.write("\n")
    except OSError as error:
        print(f"Failed to save profile display name: {error}")
        return False

    print(
        "Profile display name saved successfully: "
        f"{metadata_path}"
    )
    return True


def save_profile_notes(profile_folder, notes):
    profile_folder = Path(profile_folder)

    try:
        profiles_folder = get_profiles_folder()
        resolved_profiles_folder = profiles_folder.resolve(
            strict=True
        )
        profile_status = profile_folder.lstat()
    except FileNotFoundError:
        print(
            "Cannot save profile notes: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to inspect profile notes target: {error}")
        return False

    if stat.S_ISLNK(profile_status.st_mode):
        print(
            "Cannot save profile notes: symbolic-link profiles are "
            f"not allowed: {profile_folder}"
        )
        return False

    try:
        resolved_profile = profile_folder.resolve(
            strict=True
        )
    except FileNotFoundError:
        print(
            "Cannot save profile notes: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to resolve profile notes target: {error}")
        return False

    if resolved_profile.parent != resolved_profiles_folder:
        print(
            "Cannot save profile notes: the requested path is not a "
            "direct child of the managed profiles directory: "
            f"{profile_folder}"
        )
        return False

    if resolved_profile == resolved_profiles_folder / "Vanilla":
        print("Cannot save profile notes: Vanilla is protected.")
        return False

    if not stat.S_ISDIR(profile_status.st_mode):
        print(
            "Cannot save profile notes: the requested profile is not "
            f"a directory: {profile_folder}"
        )
        return False

    if not isinstance(notes, str):
        print(
            "Cannot save profile notes: notes must be a string."
        )
        return False

    metadata_path = (
        resolved_profile / ".ddlc_launcher_user.json"
    )

    try:
        metadata_status = metadata_path.lstat()
    except FileNotFoundError:
        metadata_exists = False
    except OSError as error:
        print(f"Failed to inspect profile user metadata: {error}")
        return False
    else:
        metadata_exists = True

        if stat.S_ISLNK(metadata_status.st_mode):
            print(
                "Cannot save profile notes: the user metadata path is "
                f"a symbolic link: {metadata_path}"
            )
            return False

        if not stat.S_ISREG(metadata_status.st_mode):
            print(
                "Cannot save profile notes: the user metadata path is "
                f"not a regular file: {metadata_path}"
            )
            return False

    existing_metadata = {}

    if metadata_exists:
        try:
            with open(
                metadata_path,
                "r",
                encoding="utf-8",
            ) as metadata_file:
                existing_metadata = json.load(metadata_file)
        except (
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as error:
            print(
                "Failed to read existing profile user metadata: "
                f"{error}"
            )
            return False

        if not isinstance(existing_metadata, dict):
            print(
                "Cannot save profile notes: existing user metadata is "
                "not a dictionary."
            )
            return False

    user_metadata = {
        "format_version": 1,
    }
    existing_display_name = existing_metadata.get(
        "display_name"
    )

    if (
        isinstance(existing_display_name, str)
        and existing_display_name.strip()
    ):
        user_metadata["display_name"] = existing_display_name

    user_metadata["notes"] = notes

    try:
        with open(
            metadata_path,
            "w",
            encoding="utf-8",
        ) as metadata_file:
            json.dump(
                user_metadata,
                metadata_file,
                indent=2,
            )
            metadata_file.write("\n")
    except OSError as error:
        print(f"Failed to save profile notes: {error}")
        return False

    print(
        "Profile notes saved successfully: "
        f"{metadata_path}"
    )
    return True


def save_profile_user_metadata(
    profile_folder,
    display_name,
    notes,
):
    profile_folder = Path(profile_folder)

    try:
        profiles_folder = get_profiles_folder()
        resolved_profiles_folder = profiles_folder.resolve(
            strict=True
        )
        profile_status = profile_folder.lstat()
    except FileNotFoundError:
        print(
            "Cannot save profile user metadata: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to inspect profile user metadata target: {error}")
        return False

    if stat.S_ISLNK(profile_status.st_mode):
        print(
            "Cannot save profile user metadata: symbolic-link profiles "
            f"are not allowed: {profile_folder}"
        )
        return False

    try:
        resolved_profile = profile_folder.resolve(
            strict=True
        )
    except FileNotFoundError:
        print(
            "Cannot save profile user metadata: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to resolve profile user metadata target: {error}")
        return False

    if resolved_profile.parent != resolved_profiles_folder:
        print(
            "Cannot save profile user metadata: the requested path is "
            "not a direct child of the managed profiles directory: "
            f"{profile_folder}"
        )
        return False

    if resolved_profile == resolved_profiles_folder / "Vanilla":
        print(
            "Cannot save profile user metadata: Vanilla is protected."
        )
        return False

    if not stat.S_ISDIR(profile_status.st_mode):
        print(
            "Cannot save profile user metadata: the requested profile "
            f"is not a directory: {profile_folder}"
        )
        return False

    if not isinstance(display_name, str):
        print(
            "Cannot save profile user metadata: the display name must "
            "be a string."
        )
        return False

    display_name = display_name.strip()

    if not display_name:
        print(
            "Cannot save profile user metadata: the display name "
            "cannot be empty."
        )
        return False

    if not isinstance(notes, str):
        print(
            "Cannot save profile user metadata: notes must be a string."
        )
        return False

    metadata_path = (
        resolved_profile / ".ddlc_launcher_user.json"
    )

    try:
        metadata_status = metadata_path.lstat()
    except FileNotFoundError:
        metadata_exists = False
    except OSError as error:
        print(f"Failed to inspect profile user metadata: {error}")
        return False
    else:
        metadata_exists = True

        if stat.S_ISLNK(metadata_status.st_mode):
            print(
                "Cannot save profile user metadata: the metadata path "
                f"is a symbolic link: {metadata_path}"
            )
            return False

        if not stat.S_ISREG(metadata_status.st_mode):
            print(
                "Cannot save profile user metadata: the metadata path "
                f"is not a regular file: {metadata_path}"
            )
            return False

    if metadata_exists:
        try:
            with open(
                metadata_path,
                "r",
                encoding="utf-8",
            ) as metadata_file:
                existing_metadata = json.load(metadata_file)
        except (
            OSError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as error:
            print(
                "Failed to read existing profile user metadata: "
                f"{error}"
            )
            return False

        if not isinstance(existing_metadata, dict):
            print(
                "Cannot save profile user metadata: existing metadata "
                "is not a dictionary."
            )
            return False

    user_metadata = {
        "format_version": 1,
        "display_name": display_name,
        "notes": notes,
    }

    try:
        with open(
            metadata_path,
            "w",
            encoding="utf-8",
        ) as metadata_file:
            json.dump(
                user_metadata,
                metadata_file,
                indent=2,
            )
            metadata_file.write("\n")
    except OSError as error:
        print(f"Failed to save profile user metadata: {error}")
        return False

    print(
        "Profile user metadata saved successfully: "
        f"{metadata_path}"
    )
    return True


def update_profile_info(profile_combo, profile_info_label):
    profile_path = profile_combo.currentData()

    if not profile_path:
        profile_info_label.setText(
            "No profile selected"
        )
        return

    health = inspect_profile_health(Path(profile_path))
    lines = [
        f"Name: {health['display_name']}",
        f"Type: {health['profile_type']}",
        f"Saves: {health['saves_status']}",
        f"Health: {health['status'].title()}",
    ]
    lines.extend(f"Issue: {issue}" for issue in health["issues"])
    profile_info_label.setText("\n".join(lines))


def edit_selected_profile(profile_combo, profile_info_label):
    profile_path = profile_combo.currentData()

    if not profile_path:
        QMessageBox.warning(
            None,
            "No Profile Selected",
            "Please select a profile first.",
        )
        return

    profile_folder = Path(profile_path)

    if not profile_folder.is_dir():
        QMessageBox.warning(
            None,
            "Profile Not Found",
            "The selected profile no longer exists.",
        )
        return

    if profile_folder.name == "Vanilla":
        QMessageBox.warning(
            None,
            "Vanilla Profile Protected",
            "The Vanilla profile cannot be edited.",
        )
        return

    dialog = QDialog(
        profile_combo.window()
    )
    dialog.setWindowTitle(
        "Edit Profile"
    )

    form_layout = QFormLayout(dialog)
    display_name_edit = QLineEdit(
        get_profile_display_name(profile_folder)
    )
    folder_name_label = QLabel(
        profile_folder.name
    )
    notes_edit = QPlainTextEdit()
    notes_edit.setPlainText(
        get_profile_notes(profile_folder)
    )
    notes_edit.setMinimumHeight(100)

    form_layout.addRow(
        "Display name:",
        display_name_edit,
    )
    form_layout.addRow(
        "Folder name:",
        folder_name_label,
    )
    form_layout.addRow(
        "Notes:",
        notes_edit,
    )

    button_box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save
        | QDialogButtonBox.StandardButton.Cancel
    )

    form_layout.addRow(button_box)
    button_box.rejected.connect(
        dialog.reject
    )

    def save_profile_edits():
        if not save_profile_user_metadata(
            profile_folder,
            display_name_edit.text(),
            notes_edit.toPlainText(),
        ):
            QMessageBox.warning(
                dialog,
                "Profile Edit Failed",
                "The profile changes could not be saved.",
            )
            return

        current_index = profile_combo.currentIndex()

        if current_index >= 0:
            profile_combo.setItemText(
                current_index,
                get_profile_display_name(profile_folder),
            )

        update_profile_info(
            profile_combo,
            profile_info_label,
        )
        dialog.accept()

    button_box.accepted.connect(
        save_profile_edits
    )

    dialog.exec()


def detect_ddlc_version(ddlc_folder):
    game_folder = ddlc_folder / "game"

    if not game_folder.is_dir():
        return None

    required_files = {
        "audio.rpa",
        "images.rpa",
        "scripts.rpa",
        "fonts.rpa",
    }

    game_files = {
        path.name
        for path in game_folder.iterdir()
        if path.is_file()
    }

    if required_files.issubset(game_files):
        return "1.1.1"

    return None


def locate_ddlc(ddlc_status, settings):
    folder_path = QFileDialog.getExistingDirectory(
        None,
        "Select your DDLC installation",
    )

    if not folder_path:
        return

    ddlc_folder = Path(folder_path)
    version = detect_ddlc_version(ddlc_folder)

    if version is None:
        QMessageBox.warning(
            None,
            "DDLC Not Recognized",
            "This does not appear to be a supported DDLC installation.",
        )

        print(f"DDLC not recognized: {ddlc_folder}")
        return

    settings.setValue(
        "ddlc_path",
        str(ddlc_folder),
    )

    ddlc_status.setText(
        f"✓ Doki Doki Literature Club!\n"
        f"Version {version}\n"
        f"Ready to use"
    )

    print(f"DDLC detected: {ddlc_folder}")
    print(f"DDLC version: {version}")


def install_mod(profile_combo=None):
    file_path, _ = QFileDialog.getOpenFileName(
        None,
        "Select a DDLC Mod",
        "",
        "Archive files (*.zip *.7z)",
    )

    if not file_path:
        return

    install_mod_archive(
        file_path,
        profile_combo,
    )


def prepare_7z_archive_as_zip(
    file_path,
    logical_file_name=None,
):
    file_path = Path(file_path)

    try:
        is_7z_archive = py7zr.is_7zfile(file_path)
    except (OSError, TypeError) as error:
        print(f"Failed to inspect 7z archive: {error}")
        return None

    if not is_7z_archive:
        print("Failed to prepare 7z archive: invalid 7z archive.")
        return None

    try:
        temporary_root = Path(
            tempfile.mkdtemp(
                prefix="ddlc-7z-convert-",
            )
        )
    except OSError as error:
        print(f"Failed to create temporary 7z workspace: {error}")
        return None

    conversion_complete = False
    staging_folder = temporary_root / "staging"
    seven_zip_errors = (
        py7zr.exceptions.ArchiveError,
        py7zr.exceptions.AbsolutePathError,
        ValueError,
    )

    try:
        try:
            staging_folder.mkdir()
        except OSError as error:
            print(f"Failed to create 7z staging directory: {error}")
            return None

        try:
            with py7zr.SevenZipFile(
                file_path,
                mode="r",
            ) as archive:
                if archive.needs_password():
                    print(
                        "Password-protected 7z archives are not "
                        "supported."
                    )
                    return None

                members = archive.list()
        except py7zr.exceptions.PasswordRequired:
            print(
                "Password-protected 7z archives are not supported."
            )
            return None
        except seven_zip_errors as error:
            print(f"Failed to read 7z archive metadata: {error}")
            return None
        except OSError as error:
            print(f"Failed to read 7z archive: {error}")
            return None

        try:
            resolved_staging_folder = staging_folder.resolve(
                strict=True
            )
        except (OSError, RuntimeError) as error:
            print(f"Failed to resolve 7z staging directory: {error}")
            return None

        total_uncompressed_size = 0

        for member in members:
            member_name = member.filename

            if not isinstance(member_name, str):
                print(
                    "Rejected 7z member with a non-string name: "
                    f"{member_name!r}"
                )
                return None

            normalized_name = member_name.replace("\\", "/")

            if "\x00" in normalized_name:
                print(
                    "Rejected 7z member with NUL in its name: "
                    f"{member_name!r}"
                )
                return None

            posix_member_path = PurePosixPath(normalized_name)
            windows_member_path = PureWindowsPath(normalized_name)

            if posix_member_path.is_absolute():
                print(
                    "Rejected 7z member with an absolute path: "
                    f"{member_name}"
                )
                return None

            if (
                windows_member_path.is_absolute()
                or windows_member_path.drive
            ):
                print(
                    "Rejected 7z member with an absolute or "
                    f"drive-prefixed path: {member_name}"
                )
                return None

            if ".." in posix_member_path.parts:
                print(
                    "Rejected 7z member with path traversal: "
                    f"{member_name}"
                )
                return None

            destination = staging_folder.joinpath(
                *posix_member_path.parts
            )

            try:
                resolved_destination = destination.resolve(
                    strict=False
                )
            except (OSError, RuntimeError) as error:
                print(
                    "Rejected 7z member whose destination could not "
                    f"be resolved ({member_name}): {error}"
                )
                return None

            if (
                resolved_destination == resolved_staging_folder
                or resolved_staging_folder
                not in resolved_destination.parents
            ):
                print(
                    "Rejected 7z member with an unsafe destination: "
                    f"{member_name}"
                )
                return None

            if member.is_symlink:
                print(
                    "Rejected symbolic-link 7z member: "
                    f"{member_name}"
                )
                return None

            if not member.is_file and not member.is_directory:
                print(
                    "Rejected unsupported 7z member type: "
                    f"{member_name}"
                )
                return None

            member_size = member.uncompressed

            if (
                not isinstance(member_size, int)
                or isinstance(member_size, bool)
                or member_size < 0
            ):
                print(
                    "Rejected 7z member with an invalid uncompressed "
                    f"size: {member_name}"
                )
                return None

            total_uncompressed_size += member_size

        try:
            available_temp_space = shutil.disk_usage(
                temporary_root
            ).free
        except OSError as error:
            print(
                "Failed to check temporary-storage disk space for "
                f"7z conversion: {error}"
            )
            return None

        required_temp_space = total_uncompressed_size * 2

        if required_temp_space > available_temp_space:
            print(
                "Cannot prepare 7z archive: temporary storage needs "
                "approximately twice the advertised uncompressed "
                f"size ({required_temp_space} bytes required, "
                f"{available_temp_space} bytes available)."
            )
            return None

        try:
            with py7zr.SevenZipFile(
                file_path,
                mode="r",
                max_extract_size=total_uncompressed_size,
            ) as archive:
                if archive.needs_password():
                    print(
                        "Password-protected 7z archives are not "
                        "supported."
                    )
                    return None

                archive.extractall(
                    path=staging_folder,
                )
        except py7zr.exceptions.PasswordRequired:
            print(
                "Password-protected 7z archives are not supported."
            )
            return None
        except seven_zip_errors as error:
            print(f"Failed to extract 7z archive: {error}")
            return None
        except OSError as error:
            print(f"Failed to extract 7z archive: {error}")
            return None

        verified_entries = []
        directories_to_check = [staging_folder]

        try:
            while directories_to_check:
                current_directory = directories_to_check.pop()

                with os.scandir(current_directory) as entries:
                    for entry in entries:
                        extracted_path = Path(entry.path)
                        extracted_status = extracted_path.lstat()

                        if stat.S_ISLNK(extracted_status.st_mode):
                            print(
                                "Rejected extracted symbolic link: "
                                f"{extracted_path}"
                            )
                            return None

                        is_directory = stat.S_ISDIR(
                            extracted_status.st_mode
                        )
                        is_regular_file = stat.S_ISREG(
                            extracted_status.st_mode
                        )

                        if not is_directory and not is_regular_file:
                            print(
                                "Rejected unsupported extracted "
                                f"filesystem object: {extracted_path}"
                            )
                            return None

                        resolved_extracted_path = extracted_path.resolve(
                            strict=True
                        )

                        if (
                            resolved_extracted_path
                            == resolved_staging_folder
                            or resolved_staging_folder
                            not in resolved_extracted_path.parents
                        ):
                            print(
                                "Rejected extracted path outside the "
                                f"staging directory: {extracted_path}"
                            )
                            return None

                        verified_entries.append(
                            (extracted_path, is_directory)
                        )

                        if is_directory:
                            directories_to_check.append(
                                extracted_path
                            )
        except (OSError, RuntimeError) as error:
            print(f"Failed to verify extracted 7z contents: {error}")
            return None

        if logical_file_name is None:
            source_name = file_path.name
        else:
            source_name = str(logical_file_name)

        source_basename = PureWindowsPath(source_name).name
        source_basename = PurePosixPath(source_basename).name
        converted_stem = Path(source_basename).stem

        if (
            not converted_stem
            or converted_stem in (".", "..")
            or "\x00" in converted_stem
        ):
            converted_stem = "converted-archive"

        converted_zip_path = (
            temporary_root / f"{converted_stem}.zip"
        )

        try:
            with zipfile.ZipFile(
                converted_zip_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
                strict_timestamps=False,
            ) as converted_archive:
                for extracted_path, is_directory in sorted(
                    verified_entries,
                    key=lambda item: item[0].relative_to(
                        staging_folder
                    ).as_posix(),
                ):
                    archive_name = extracted_path.relative_to(
                        staging_folder
                    ).as_posix()

                    if is_directory:
                        archive_name += "/"

                    converted_archive.write(
                        extracted_path,
                        arcname=archive_name,
                    )
        except (OSError, RuntimeError, zipfile.LargeZipFile) as error:
            print(f"Failed to create converted ZIP archive: {error}")
            return None

        try:
            shutil.rmtree(staging_folder)
        except OSError as error:
            print(
                "Failed to remove 7z staging directory before "
                f"installation: {error}"
            )
            return None

        conversion_complete = True
        return converted_zip_path, temporary_root
    finally:
        if not conversion_complete:
            try:
                shutil.rmtree(temporary_root)
            except OSError as error:
                print(
                    "Failed to clean up temporary 7z conversion "
                    f"directory {temporary_root}: {error}"
                )


def validate_incomplete_profile_marker(marker_path):
    try:
        marker_status = marker_path.lstat()
    except OSError as error:
        print(
            "Failed to verify incomplete-profile marker: "
            f"{error}"
        )
        return False

    if (
        stat.S_ISLNK(marker_status.st_mode)
        or not stat.S_ISREG(marker_status.st_mode)
    ):
        print(
            "Cannot keep staging profile hidden: the incomplete-profile "
            "marker is not a regular file."
        )
        return False

    return True


def install_mod_archive(
    file_path,
    profile_combo=None,
    profile_name_override=None,
    logical_file_name=None,
    registry_metadata=None,
    profile_directory_name_override=None,
    keep_profile_incomplete=False,
):
    if profile_name_override is not None:
        if not isinstance(profile_name_override, str):
            print(
                "Cannot install mod archive: profile name override "
                "must be a string."
            )
            return False

        if not profile_name_override:
            print(
                "Cannot install mod archive: profile name override "
                "must not be empty."
            )
            return False

        valid_profile_name_characters = set(
            "abcdefghijklmnopqrstuvwxyz0123456789-"
        )

        if any(
            character not in valid_profile_name_characters
            for character in profile_name_override
        ):
            print(
                "Cannot install mod archive: profile name override may "
                "contain only lowercase ASCII letters, digits, and "
                "hyphens."
            )
            return False

        if (
            profile_name_override.startswith("-")
            or profile_name_override.endswith("-")
        ):
            print(
                "Cannot install mod archive: profile name override must "
                "begin and end with a letter or digit."
            )
            return False

        if "--" in profile_name_override:
            print(
                "Cannot install mod archive: profile name override must "
                "not contain consecutive hyphens."
            )
            return False

    if profile_directory_name_override is not None:
        if not isinstance(profile_directory_name_override, str):
            print(
                "Cannot install mod archive: profile directory name "
                "override must be a string."
            )
            return False

        if not profile_directory_name_override:
            print(
                "Cannot install mod archive: profile directory name "
                "override must not be empty."
            )
            return False

        valid_profile_directory_characters = set(
            "abcdefghijklmnopqrstuvwxyz0123456789-"
        )

        if any(
            character not in valid_profile_directory_characters
            for character in profile_directory_name_override
        ):
            print(
                "Cannot install mod archive: profile directory name "
                "override may contain only lowercase ASCII letters, "
                "digits, and hyphens."
            )
            return False

        if (
            profile_directory_name_override.startswith("-")
            or profile_directory_name_override.endswith("-")
        ):
            print(
                "Cannot install mod archive: profile directory name "
                "override must begin and end with a letter or digit."
            )
            return False

        if "--" in profile_directory_name_override:
            print(
                "Cannot install mod archive: profile directory name "
                "override must not contain consecutive hyphens."
            )
            return False

    if not isinstance(keep_profile_incomplete, bool):
        print(
            "Cannot install mod archive: keep_profile_incomplete must "
            "be a boolean."
        )
        return False

    if registry_metadata is not None:
        registry_metadata = sanitize_registry_install_metadata(
            registry_metadata
        )

        if registry_metadata is None:
            print(
                "Cannot install mod archive: registry metadata is "
                "invalid."
            )
            return False

        if (
            profile_name_override is not None
            and registry_metadata["id"] != profile_name_override
        ):
            print(
                "Cannot install mod archive: registry metadata ID "
                "does not match the profile name override."
            )
            return False

    if not zipfile.is_zipfile(file_path):
        try:
            is_7z_archive = py7zr.is_7zfile(file_path)
        except (OSError, TypeError) as error:
            print(f"Error: Could not inspect archive: {error}")
            return False

        if not is_7z_archive:
            print("Error: This file is not a valid ZIP or 7z archive.")
            return False

        prepared_archive = prepare_7z_archive_as_zip(
            file_path,
            logical_file_name=logical_file_name,
        )

        if prepared_archive is None:
            return False

        converted_zip_path, temporary_root = prepared_archive

        try:
            install_result = install_mod_archive(
                converted_zip_path,
                profile_combo,
                profile_name_override=profile_name_override,
                registry_metadata=registry_metadata,
                profile_directory_name_override=(
                    profile_directory_name_override
                ),
                keep_profile_incomplete=keep_profile_incomplete,
            )
        finally:
            try:
                shutil.rmtree(temporary_root)
            except OSError as error:
                print(
                    "Failed to clean up temporary converted 7z "
                    f"archive directory {temporary_root}: {error}"
                )

        return install_result

    print(f"Selected mod: {file_path}")

    try:
        with zipfile.ZipFile(
            file_path,
            "r",
        ) as archive:

            items = archive.infolist()

            archive_files = [
                item
                for item in items
                if not item.is_dir()
            ]
            archive_file_count = len(archive_files)
            unix_mode_entries = []
            executable_entries = []
            symbolic_link_entries = []

            for item in items:
                if item.create_system != 3:
                    continue

                unix_mode = (item.external_attr >> 16) & 0xFFFF

                if unix_mode == 0:
                    continue

                unix_mode_entries.append((item, unix_mode))

                if stat.S_ISLNK(unix_mode):
                    symbolic_link_entries.append((item, unix_mode))

                if (
                    not item.is_dir()
                    and unix_mode
                    & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                ):
                    executable_entries.append((item, unix_mode))

            total_compressed_size = sum(
                item.compress_size
                for item in archive_files
            )
            total_uncompressed_size = sum(
                item.file_size
                for item in archive_files
            )

            if total_compressed_size > 0:
                compression_ratio = (
                    total_uncompressed_size
                    / total_compressed_size
                )
            else:
                compression_ratio = 0.0

            largest_file = max(
                archive_files,
                key=lambda item: item.file_size,
                default=None,
            )

            def format_size(size):
                units = (
                    "B",
                    "KB",
                    "MB",
                    "GB",
                    "TB",
                )
                display_size = float(size)

                for unit in units:
                    if display_size < 1024 or unit == units[-1]:
                        return f"{display_size:.2f} {unit}"

                    display_size /= 1024

            profile_data_folder = Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppDataLocation
                )
            ) / "profiles"
            disk_check_path = profile_data_folder

            while not disk_check_path.exists():
                disk_check_path = disk_check_path.parent

            available_disk_space = shutil.disk_usage(
                disk_check_path
            ).free
            archive_fits_available_space = (
                total_uncompressed_size
                <= available_disk_space
            )

            unsafe_paths = []

            for item in items:
                normalized_name = item.filename.replace(
                    "\\",
                    "/",
                )
                path_parts = normalized_name.split("/")
                has_drive_letter = (
                    len(normalized_name) >= 3
                    and normalized_name[0].isalpha()
                    and normalized_name[1] == ":"
                    and normalized_name[2] == "/"
                )

                if (
                    normalized_name.startswith("/")
                    or ".." in path_parts
                    or has_drive_letter
                ):
                    unsafe_paths.append(
                        item.filename
                    )

            print("\nZIP contents:")

            for item in items:
                print(f" - {item.filename}")

            print("\nArchive file metadata:")
            print(
                " - Entries with Unix mode metadata: "
                f"{len(unix_mode_entries)}"
            )
            print(
                f" - Executable entries: {len(executable_entries)}"
            )

            for item, unix_mode in executable_entries:
                print(
                    f"   → {item.filename} "
                    f"({stat.filemode(unix_mode)})"
                )

            print(
                " - Symbolic-link entries: "
                f"{len(symbolic_link_entries)}"
            )

            for item, unix_mode in symbolic_link_entries:
                print(
                    f"   → {item.filename} "
                    f"({stat.filemode(unix_mode)})"
                )

            file_names = [
                item.filename.replace("\\", "/").lstrip("/")
                for item in items
                if not item.is_dir()
            ]

            renpy_extensions = (
                ".rpy",
                ".rpyc",
                ".rpa",
            )

            renpy_files = [
                name
                for name in file_names
                if name.lower().endswith(
                    renpy_extensions
                )
            ]

            game_locations = []

            for name in file_names:
                parts = [
                    part
                    for part in name.split("/")
                    if part
                ]

                for index, part in enumerate(parts):
                    if (
                        part.lower() == "game"
                        and index < len(parts) - 1
                    ):
                        game_location = "/".join(
                            parts[:index + 1]
                        )

                        if game_location not in game_locations:
                            game_locations.append(
                                game_location
                            )

            candidate_mod_roots = []

            for location in game_locations:
                if "/" in location:
                    mod_root = location.rsplit(
                        "/",
                        1,
                    )[0]
                else:
                    mod_root = "<ZIP root>"

                if mod_root not in candidate_mod_roots:
                    candidate_mod_roots.append(
                        mod_root
                    )

            def archive_item_is_regular_file(item):
                if item.is_dir():
                    return False

                if item.create_system != 3:
                    return True

                unix_mode = (item.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)

                return file_type in (0, stat.S_IFREG)

            if not candidate_mod_roots:
                for item in items:
                    item_name = item.filename.replace(
                        "\\",
                        "/",
                    ).lstrip("/")
                    item_parts = [
                        part
                        for part in item_name.split("/")
                        if part
                    ]

                    if (
                        not item_parts
                        or item_parts[-1] != "scripts.rpa"
                        or not archive_item_is_regular_file(item)
                    ):
                        continue

                    scripts_root_parts = item_parts[:-1]

                    if scripts_root_parts:
                        mod_root = "/".join(scripts_root_parts)
                    else:
                        mod_root = "<ZIP root>"

                    if mod_root not in candidate_mod_roots:
                        candidate_mod_roots.append(
                            mod_root
                        )

            print("\nMod analysis:")

            if unsafe_paths:
                print(" - Archive path safety: Unsafe")

                for unsafe_path in unsafe_paths:
                    print(f"   → {unsafe_path}")
            else:
                print(" - Archive path safety: Safe")

            print(
                f" - Archive files: {archive_file_count}"
            )
            print(
                " - Total compressed size: "
                f"{format_size(total_compressed_size)}"
            )
            print(
                " - Total uncompressed size: "
                f"{format_size(total_uncompressed_size)}"
            )
            print(
                " - Overall compression ratio: "
                f"{compression_ratio:.2f}x"
            )
            print(
                " - Available disk space: "
                f"{format_size(available_disk_space)}"
            )
            print(
                " - Archive fits available space: "
                f"{'Yes' if archive_fits_available_space else 'No'}"
            )

            if largest_file is not None:
                print(
                    " - Largest uncompressed file: "
                    f"{largest_file.filename} "
                    f"({format_size(largest_file.file_size)})"
                )
            else:
                print(" - Largest uncompressed file: None")

            if game_locations:
                print(" - game/ folder: Yes")

                for location in game_locations:
                    print(f"   → {location}")
            else:
                print(" - game/ folder: No")

            candidate_installation_types = []

            if candidate_mod_roots:
                print(" - Candidate mod root(s):")

                for mod_root in candidate_mod_roots:
                    print(f"   → {mod_root}")

                    if mod_root == "<ZIP root>":
                        root_parts = []
                    else:
                        root_parts = mod_root.split("/")

                    planned_overlay_items = []
                    launcher_files = []
                    has_game_renpy_content = False
                    direct_scripts_rpa_count = 0
                    has_game_directory = False
                    has_renpy_directory = False
                    has_lib_directory = False

                    for item in items:
                        item_name = item.filename.replace(
                            "\\",
                            "/",
                        ).lstrip("/")
                        item_parts = [
                            part
                            for part in item_name.split("/")
                            if part
                        ]

                        if (
                            item_parts[:len(root_parts)]
                            != root_parts
                        ):
                            continue

                        relative_parts = item_parts[
                            len(root_parts):
                        ]

                        if (
                            relative_parts == ["scripts.rpa"]
                            and archive_item_is_regular_file(item)
                        ):
                            direct_scripts_rpa_count += 1

                        if (
                            len(relative_parts) > 1
                            and relative_parts[0].lower() == "game"
                            and not item.is_dir()
                            and item_name.lower().endswith(
                                renpy_extensions
                            )
                        ):
                            has_game_renpy_content = True

                        if (
                            len(relative_parts) == 1
                            and not item.is_dir()
                            and relative_parts[0].lower().endswith(
                                (".sh", ".exe")
                            )
                        ):
                            launcher_files.append(
                                item_name
                            )

                        if relative_parts:
                            is_directory = (
                                len(relative_parts) > 1
                                or item.is_dir()
                            )

                            overlay_item = relative_parts[0]

                            if is_directory:
                                overlay_item += "/"

                            if overlay_item not in planned_overlay_items:
                                planned_overlay_items.append(
                                    overlay_item
                                )

                            if is_directory:
                                if relative_parts[0].lower() == "game":
                                    has_game_directory = True
                                elif relative_parts[0].lower() == "renpy":
                                    has_renpy_directory = True
                                elif relative_parts[0].lower() == "lib":
                                    has_lib_directory = True

                    is_game_folder_overlay_candidate = (
                        len(candidate_mod_roots) == 1
                        and not unsafe_paths
                        and direct_scripts_rpa_count == 1
                        and not launcher_files
                        and not has_game_directory
                        and not has_renpy_directory
                        and not has_lib_directory
                    )

                    if launcher_files:
                        print("     - Launcher files:")

                        for launcher_file in launcher_files:
                            print(f"       → {launcher_file}")
                    else:
                        print("     - Launcher files: None")

                    print(
                        "     - renpy/ directory: "
                        f"{'Yes' if has_renpy_directory else 'No'}"
                    )
                    print(
                        "     - lib/ directory: "
                        f"{'Yes' if has_lib_directory else 'No'}"
                    )

                    if planned_overlay_items:
                        if is_game_folder_overlay_candidate:
                            print(
                                "     - Planned game-folder overlay:"
                            )
                        else:
                            print("     - Planned profile overlay:")

                        for overlay_item in planned_overlay_items:
                            print(f"       → {overlay_item}")
                    else:
                        print("     - Planned profile overlay: None")

                    if has_game_renpy_content:
                        if (
                            launcher_files
                            and (
                                has_renpy_directory
                                or has_lib_directory
                            )
                        ):
                            candidate_installation_types.append(
                                "DDLC packaged-runtime candidate"
                            )
                        else:
                            candidate_installation_types.append(
                                "DDLC legacy overlay candidate"
                            )
                    elif is_game_folder_overlay_candidate:
                        candidate_installation_types.append(
                            "DDLC game-folder overlay candidate"
                        )
                    else:
                        candidate_installation_types.append(
                            "Custom / unknown"
                        )
            else:
                print(" - Candidate mod root(s): None")

            print(
                f" - Ren'Py files: {len(renpy_files)}"
            )

            looks_like_renpy_mod = (
                (
                    bool(game_locations)
                    and bool(renpy_files)
                )
                or (
                    "DDLC game-folder overlay candidate"
                    in candidate_installation_types
                )
            )

            print(
                " - Looks like a DDLC/Ren'Py mod: "
                f"{'Yes' if looks_like_renpy_mod else 'No'}"
            )

            if (
                candidate_installation_types
                and len(set(candidate_installation_types)) == 1
            ):
                installation_type = (
                    candidate_installation_types[0]
                )
            else:
                installation_type = "Custom / unknown"

            print(
                f" - Installation type: {installation_type}"
            )

        can_create_mod_profile = (
            not unsafe_paths
            and archive_fits_available_space
            and len(candidate_mod_roots) == 1
            and installation_type in (
                "DDLC legacy overlay candidate",
                "DDLC packaged-runtime candidate",
                "DDLC game-folder overlay candidate",
            )
        )

        if not can_create_mod_profile:
            return False

        mod_root = candidate_mod_roots[0]

        if profile_name_override is not None:
            logical_profile_name = profile_name_override
        elif mod_root == "<ZIP root>":
            logical_profile_name = Path(file_path).stem
        else:
            logical_profile_name = mod_root.rsplit(
                "/",
                1,
            )[-1]

        if profile_directory_name_override is not None:
            physical_profile_name = (
                profile_directory_name_override
            )
        else:
            physical_profile_name = logical_profile_name

        settings = QSettings(
            "Epicfrisk",
            "DDLC Mod Launcher",
        )
        saved_path = settings.value(
            "ddlc_path",
            "",
        )

        if not saved_path:
            print(
                "Cannot create mod profile base: "
                "DDLC is not selected."
            )
            return False

        ddlc_folder = Path(saved_path)

        if not ddlc_folder.is_dir():
            print(
                "Cannot create mod profile base: "
                "DDLC folder does not exist."
            )
            return False

        try:
            profiles_folder = get_profiles_folder()
            mod_profile = profiles_folder / physical_profile_name

            if mod_profile.exists():
                print(
                    f"Mod profile already exists: "
                    f"{mod_profile}"
                )
                return False

            sidecar_marker_path = profiles_folder / (
                f".{physical_profile_name}.ddlc_launcher_incomplete"
            )

            try:
                sidecar_marker_path.touch()
            except OSError as error:
                print(
                    "Failed to create profile guard marker: "
                    f"{error}"
                )
                return False

            print(f"Creating mod profile base: {mod_profile}")
            print("Copying clean DDLC files...")

            shutil.copytree(
                ddlc_folder,
                mod_profile,
            )

        except OSError as error:
            print(
                f"Failed to create mod profile base: "
                f"{error}"
            )
            return False

        marker_path = (
            mod_profile / ".ddlc_launcher_incomplete"
        )

        try:
            marker_path.touch()
        except OSError as error:
            print(
                "Failed to create incomplete-profile marker: "
                f"{error}"
            )
            print(
                "Mod profile may be incomplete: "
                f"{mod_profile}"
            )
            return False

        try:
            sidecar_marker_path.unlink(
                missing_ok=True
            )
        except OSError as error:
            print(
                "Failed to remove profile guard marker: "
                f"{error}"
            )
            print(
                "Mod profile may be incomplete: "
                f"{mod_profile}"
            )
            return False

        print("Mod profile base created successfully.")

        if installation_type == "DDLC packaged-runtime candidate":
            if symbolic_link_entries:
                print(
                    "Packaged-runtime installation currently does not "
                    "support symbolic links."
                )

                for item, _ in symbolic_link_entries:
                    print(f" - {item.filename}")

                return False

            try:
                remaining_disk_space = shutil.disk_usage(
                    mod_profile
                ).free
            except OSError as error:
                print(
                    "Failed to verify disk space before installing "
                    f"packaged-runtime files: {error}"
                )
                print(
                    "Mod profile is only a clean/incomplete base: "
                    f"{mod_profile}"
                )
                return False

            if total_uncompressed_size > remaining_disk_space:
                print(
                    "Cannot install packaged-runtime files: not enough "
                    "disk space remains after creating the clean profile."
                )
                print(
                    "Mod profile is only a clean/incomplete base: "
                    f"{mod_profile}"
                )
                return False

            if mod_root == "<ZIP root>":
                root_parts = []
            else:
                root_parts = mod_root.split("/")

            if has_lib_directory:
                try:
                    shutil.rmtree(mod_profile / "lib")
                except OSError as error:
                    print(
                        "Failed to remove clean DDLC lib directory: "
                        f"{error}"
                    )
                    print(
                        "Mod profile remains incomplete: "
                        f"{mod_profile}"
                    )
                    return False

            print("Installing packaged-runtime files into profile...")

            try:
                resolved_profile = mod_profile.resolve()

                with zipfile.ZipFile(
                    file_path,
                    "r",
                ) as archive:

                    for item in archive.infolist():
                        item_name = item.filename.replace(
                            "\\",
                            "/",
                        ).lstrip("/")
                        item_parts = [
                            part
                            for part in item_name.split("/")
                            if part
                        ]

                        if (
                            item_parts[:len(root_parts)]
                            != root_parts
                        ):
                            continue

                        relative_parts = item_parts[
                            len(root_parts):
                        ]

                        if not relative_parts:
                            continue

                        destination = mod_profile.joinpath(
                            *relative_parts
                        )
                        resolved_destination = destination.resolve()

                        if (
                            resolved_destination != resolved_profile
                            and resolved_profile
                            not in resolved_destination.parents
                        ):
                            print(
                                "Failed to install packaged-runtime files: "
                                f"unsafe destination for {item.filename}"
                            )
                            print(
                                "Mod profile may be incomplete: "
                                f"{mod_profile}"
                            )
                            return False

                        if item.is_dir():
                            destination.mkdir(
                                parents=True,
                                exist_ok=True,
                            )
                        else:
                            destination.parent.mkdir(
                                parents=True,
                                exist_ok=True,
                            )

                            with archive.open(item, "r") as source:
                                with open(destination, "wb") as output:
                                    shutil.copyfileobj(
                                        source,
                                        output,
                                    )

                            if (
                                os.name != "nt"
                                and item.create_system == 3
                            ):
                                unix_mode = (
                                    item.external_attr >> 16
                                ) & 0xFFFF

                                if (
                                    unix_mode != 0
                                    and not stat.S_ISLNK(unix_mode)
                                ):
                                    permission_bits = (
                                        stat.S_IMODE(unix_mode)
                                        & (
                                            stat.S_IRWXU
                                            | stat.S_IRWXG
                                            | stat.S_IRWXO
                                        )
                                    )

                                    if permission_bits != 0:
                                        os.chmod(
                                            destination,
                                            permission_bits,
                                        )

            except (
                OSError,
                RuntimeError,
                NotImplementedError,
                zipfile.BadZipFile,
            ) as error:
                print(
                    "Failed to install packaged-runtime files: "
                    f"{error}"
                )
                print(
                    "Mod profile may be incomplete: "
                    f"{mod_profile}"
                )
                return False

            installed_launcher_files = [
                "/".join(
                    launcher_file.split("/")[len(root_parts):]
                )
                for launcher_file in launcher_files
            ]

            if sys.platform.startswith("linux"):
                for launcher_file in installed_launcher_files:
                    if not launcher_file.lower().endswith(".sh"):
                        continue

                    installed_launcher = (
                        mod_profile / launcher_file
                    )

                    if not repair_linux_launcher_permissions(
                        mod_profile,
                        installed_launcher,
                    ):
                        return False

            profile_metadata = {
                "format_version": 1,
                "installation_type": "packaged_runtime",
                "launchers": {
                    "linux": sorted(
                        launcher_file
                        for launcher_file in installed_launcher_files
                        if launcher_file.lower().endswith(".sh")
                    ),
                    "windows": sorted(
                        launcher_file
                        for launcher_file in installed_launcher_files
                        if launcher_file.lower().endswith(".exe")
                    ),
                },
            }

            if registry_metadata is not None:
                profile_metadata["registry"] = registry_metadata

            metadata_path = (
                mod_profile / ".ddlc_launcher_profile.json"
            )

            try:
                with open(
                    metadata_path,
                    "w",
                    encoding="utf-8",
                ) as metadata_file:
                    json.dump(
                        profile_metadata,
                        metadata_file,
                        indent=2,
                    )
                    metadata_file.write("\n")
            except OSError as error:
                print(
                    "Failed to write packaged-runtime launcher "
                    f"metadata: {error}"
                )
                print(
                    "Mod profile remains incomplete: "
                    f"{mod_profile}"
                )
                return False

            if keep_profile_incomplete:
                if not validate_incomplete_profile_marker(marker_path):
                    print(
                        "Staging profile may not be safely hidden: "
                        f"{mod_profile}"
                    )
                    return False

                print("Packaged-runtime files installed successfully.")
                print(
                    "Staging profile built successfully and remains "
                    f"hidden/incomplete: {mod_profile}"
                )
                return True

            try:
                marker_path.unlink(
                    missing_ok=True
                )
                sidecar_marker_path.unlink(
                    missing_ok=True
                )
            except OSError as error:
                print(
                    "Failed to remove incomplete-profile markers: "
                    f"{error}"
                )
                print(
                    "Mod profile remains hidden/incomplete: "
                    f"{mod_profile}"
                )
                return False

            print("Packaged-runtime files installed successfully.")

            restore_result = restore_profile_saves(mod_profile)

            if not restore_result:
                QMessageBox.warning(
                    None,
                    "Save Restore Failed",
                    "The mod was installed successfully, but its preserved "
                    "saves could not be restored. The preserved saves were "
                    "left untouched.",
                )

            if profile_combo is not None:
                refresh_profile_combo(
                    profile_combo,
                    mod_profile,
                )

            return True

        if installation_type not in (
            "DDLC legacy overlay candidate",
            "DDLC game-folder overlay candidate",
        ):
            return False

        try:
            remaining_disk_space = shutil.disk_usage(
                mod_profile
            ).free
        except OSError as error:
            print(
                "Failed to verify disk space before installing "
                f"mod files: {error}"
            )
            print(
                "Mod profile is only a clean/incomplete base: "
                f"{mod_profile}"
            )
            return False

        if total_uncompressed_size > remaining_disk_space:
            print(
                "Cannot install mod files: not enough disk space "
                "remains after creating the clean profile."
            )
            print(
                "Mod profile is only a clean/incomplete base: "
                f"{mod_profile}"
            )
            return False

        if mod_root == "<ZIP root>":
            root_parts = []
        else:
            root_parts = mod_root.split("/")

        game_folder_overlay = (
            installation_type
            == "DDLC game-folder overlay candidate"
        )

        if game_folder_overlay:
            print(
                "Installing mod files into the profile game folder..."
            )
        else:
            print("Installing mod files into profile...")

        try:
            resolved_profile = mod_profile.resolve()

            if game_folder_overlay:
                install_root = mod_profile / "game"
                install_root_status = install_root.lstat()

                if stat.S_ISLNK(install_root_status.st_mode):
                    print(
                        "Failed to install game-folder overlay: the "
                        "profile game directory is a symbolic link."
                    )
                    return False

                if not stat.S_ISDIR(install_root_status.st_mode):
                    print(
                        "Failed to install game-folder overlay: the "
                        "profile game path is not a directory."
                    )
                    return False

                resolved_install_root = install_root.resolve(
                    strict=True
                )

                if (
                    resolved_install_root == resolved_profile
                    or resolved_profile
                    not in resolved_install_root.parents
                ):
                    print(
                        "Failed to install game-folder overlay: the "
                        "profile game directory is unsafe."
                    )
                    return False
            else:
                install_root = mod_profile
                resolved_install_root = resolved_profile

            with zipfile.ZipFile(
                file_path,
                "r",
            ) as archive:

                for item in archive.infolist():
                    item_name = item.filename.replace(
                        "\\",
                        "/",
                    ).lstrip("/")
                    item_parts = [
                        part
                        for part in item_name.split("/")
                        if part
                    ]

                    if (
                        item_parts[:len(root_parts)]
                        != root_parts
                    ):
                        continue

                    relative_parts = item_parts[
                        len(root_parts):
                    ]

                    if not relative_parts:
                        continue

                    destination = install_root.joinpath(
                        *relative_parts
                    )
                    resolved_destination = destination.resolve()

                    if (
                        resolved_destination != resolved_install_root
                        and resolved_install_root
                        not in resolved_destination.parents
                    ):
                        print(
                            "Failed to install mod files: "
                            f"unsafe destination for {item.filename}"
                        )
                        print(
                            "Mod profile may be incomplete: "
                            f"{mod_profile}"
                        )
                        return False

                    if item.is_dir():
                        destination.mkdir(
                            parents=True,
                            exist_ok=True,
                        )
                    else:
                        destination.parent.mkdir(
                            parents=True,
                            exist_ok=True,
                        )

                        with archive.open(item, "r") as source:
                            with open(destination, "wb") as output:
                                shutil.copyfileobj(
                                    source,
                                    output,
                                )

        except (
            OSError,
            RuntimeError,
            NotImplementedError,
            zipfile.BadZipFile,
        ) as error:
            print(f"Failed to install mod files: {error}")
            print(
                "Mod profile may be incomplete: "
                f"{mod_profile}"
            )
            return False

        if registry_metadata is not None:
            if game_folder_overlay:
                metadata_installation_type = (
                    "game_folder_overlay"
                )
            else:
                metadata_installation_type = "legacy_overlay"

            profile_metadata = {
                "format_version": 1,
                "installation_type": metadata_installation_type,
                "registry": registry_metadata,
            }
            metadata_path = (
                mod_profile / ".ddlc_launcher_profile.json"
            )

            try:
                metadata_status = metadata_path.lstat()
            except FileNotFoundError:
                pass
            except OSError as error:
                print(
                    "Failed to inspect online-install profile "
                    f"metadata: {error}"
                )
                print(
                    "Mod profile remains incomplete: "
                    f"{mod_profile}"
                )
                return False
            else:
                if (
                    stat.S_ISLNK(metadata_status.st_mode)
                    or not stat.S_ISREG(metadata_status.st_mode)
                ):
                    print(
                        "Cannot write online-install profile metadata: "
                        "the metadata path is not a regular file."
                    )
                    print(
                        "Mod profile remains incomplete: "
                        f"{mod_profile}"
                    )
                    return False

            try:
                with open(
                    metadata_path,
                    "w",
                    encoding="utf-8",
                ) as metadata_file:
                    json.dump(
                        profile_metadata,
                        metadata_file,
                        indent=2,
                    )
                    metadata_file.write("\n")
            except OSError as error:
                print(
                    "Failed to write online-install profile metadata: "
                    f"{error}"
                )
                print(
                    "Mod profile remains incomplete: "
                    f"{mod_profile}"
                )
                return False

        if keep_profile_incomplete:
            if not validate_incomplete_profile_marker(marker_path):
                print(
                    "Staging profile may not be safely hidden: "
                    f"{mod_profile}"
                )
                return False

            print("Mod files installed successfully.")
            print(
                "Staging profile built successfully and remains "
                f"hidden/incomplete: {mod_profile}"
            )
            return True

        try:
            marker_path.unlink(
                missing_ok=True
            )
            sidecar_marker_path.unlink(
                missing_ok=True
            )
        except OSError as error:
            print(
                "Failed to remove incomplete-profile markers: "
                f"{error}"
            )
            print(
                "Mod profile remains hidden/incomplete: "
                f"{mod_profile}"
            )
            return False

        print("Mod files installed successfully.")

        restore_result = restore_profile_saves(mod_profile)

        if not restore_result:
            QMessageBox.warning(
                None,
                "Save Restore Failed",
                "The mod was installed successfully, but its preserved "
                "saves could not be restored. The preserved saves were "
                "left untouched.",
            )

        if profile_combo is not None:
            refresh_profile_combo(
                profile_combo,
                mod_profile,
            )

        return True

    except zipfile.BadZipFile:
        print("Error: The ZIP archive is corrupted.")
        return False


def path_is_inside_profile(resolved_profile, resolved_path):
    return (
        resolved_path != resolved_profile
        and resolved_profile in resolved_path.parents
    )


def find_matching_linux_runtimes(
    resolved_profile,
    launcher_stem,
):
    matching_runtime_files = []
    seen_runtime_files = set()

    for runtime_file in (
        resolved_profile / "lib"
    ).glob("*linux-*/*"):
        if runtime_file.name != launcher_stem:
            continue

        try:
            resolved_runtime = runtime_file.resolve(
                strict=True
            )
        except FileNotFoundError:
            continue

        if not path_is_inside_profile(
            resolved_profile,
            resolved_runtime,
        ):
            continue

        runtime_mode = resolved_runtime.stat().st_mode

        if not stat.S_ISREG(runtime_mode):
            continue

        if resolved_runtime in seen_runtime_files:
            continue

        seen_runtime_files.add(resolved_runtime)
        matching_runtime_files.append(
            (resolved_runtime, runtime_mode)
        )

    return matching_runtime_files


def repair_linux_launcher_permissions(
    profile_folder,
    launcher,
):
    try:
        resolved_profile = Path(profile_folder).resolve(
            strict=True
        )
        resolved_launcher = Path(launcher).resolve(
            strict=True
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "Failed to resolve packaged-runtime Linux launcher for "
            f"permission repair: {error}"
        )
        return False

    if not path_is_inside_profile(
        resolved_profile,
        resolved_launcher,
    ):
        print(
            "Cannot repair packaged-runtime Linux launcher outside "
            f"the profile: {resolved_launcher}"
        )
        return False

    try:
        launcher_mode = resolved_launcher.stat().st_mode
    except OSError as error:
        print(
            "Failed to inspect packaged-runtime Linux launcher "
            f"permissions: {error}"
        )
        return False

    if not stat.S_ISREG(launcher_mode):
        print(
            "Packaged-runtime Linux launcher is not a regular file: "
            f"{resolved_launcher}"
        )
        return False

    execute_permissions = (
        stat.S_IXUSR
        | stat.S_IXGRP
        | stat.S_IXOTH
    )

    try:
        os.chmod(
            resolved_launcher,
            stat.S_IMODE(launcher_mode) | execute_permissions,
        )
        matching_runtime_files = find_matching_linux_runtimes(
            resolved_profile,
            resolved_launcher.stem,
        )

        for runtime_file, runtime_mode in matching_runtime_files:
            os.chmod(
                runtime_file,
                stat.S_IMODE(runtime_mode) | execute_permissions,
            )
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "Failed to repair packaged-runtime Linux execute "
            f"permissions: {error}"
        )
        return False

    return True


LAUNCHER_DISCOVERY_FAILURE_MESSAGES = {
    "launcher_not_found": (
        "Launcher Not Found",
        "DokDoki Launcher could not find a DDLC launcher inside this profile.\n"
        "The profile files may be incomplete or damaged.",
    ),
    "metadata_invalid": (
        "Invalid Profile Data",
        "This profile's launcher metadata could not be used safely by DokDoki Launcher.",
    ),
    "packaged_launcher_missing": (
        "Launcher File Missing",
        "This profile's launcher metadata points to a launcher file that is missing.\n"
        "DokDoki Launcher cannot start it. The profile may need to be reinstalled.",
    ),
    "packaged_launcher_ambiguous": (
        "Launcher Selection Failed",
        "This profile contains multiple possible launchers and DokDoki Launcher "
        "could not safely determine which one to start.",
    ),
    "permission_repair_failed": (
        "Launcher Permission Error",
        "DokDoki Launcher found the launcher, but its Linux executable "
        "permissions could not be prepared.",
    ),
    "unsupported_platform": (
        "Unsupported Platform",
        "DokDoki Launcher cannot launch this profile on the current operating system.",
    ),
    "launcher_inspection_failed": (
        "Launcher Check Failed",
        "DokDoki Launcher could not safely verify this profile's launcher.",
    ),
}


def find_ddlc_launcher(ddlc_folder):
    """Return (launcher, platform, failure_reason) using the existing selection order."""
    metadata_path = (
        ddlc_folder / ".ddlc_launcher_profile.json"
    )

    try:
        metadata_path.lstat()
    except FileNotFoundError:
        metadata_exists = False
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "Failed to check profile launcher metadata: "
            f"{error}"
        )
        return None, None, "launcher_inspection_failed"
    else:
        metadata_exists = True

    if metadata_exists:
        try:
            with open(
                metadata_path,
                "r",
                encoding="utf-8",
            ) as metadata_file:
                profile_metadata = json.load(metadata_file)
        except OSError as error:
            print(
                "Failed to read profile launcher metadata: "
                f"{error}"
            )
            return None, None, "launcher_inspection_failed"
        except (ValueError, RuntimeError) as error:
            print(f"Invalid profile launcher metadata: {error}")
            return None, None, "metadata_invalid"

        try:
            format_version = profile_metadata["format_version"]
            installation_type = profile_metadata[
                "installation_type"
            ]
        except (TypeError, KeyError) as error:
            print(
                "Invalid profile launcher metadata: "
                f"missing or malformed field {error}."
            )
            return None, None, "metadata_invalid"

        if format_version != 1:
            print(
                "Invalid profile launcher metadata: unsupported "
                "format version."
            )
            return None, None, "metadata_invalid"

        if installation_type in (
            "legacy_overlay",
            "game_folder_overlay",
        ):
            metadata_exists = False
        elif installation_type != "packaged_runtime":
            print(
                "Invalid profile launcher metadata: unsupported "
                "installation type."
            )
            return None, None, "metadata_invalid"

    if metadata_exists:
        if sys.platform.startswith("linux"):
            current_platform = "linux"
        elif sys.platform.startswith("win"):
            current_platform = "windows"
        else:
            print(
                "Cannot use packaged-runtime launcher metadata: "
                f"unsupported platform {sys.platform}."
            )
            return None, None, "unsupported_platform"

        try:
            platform_launchers = profile_metadata[
                "launchers"
            ][current_platform]
        except (TypeError, KeyError) as error:
            print(
                "Invalid packaged-runtime launcher metadata: "
                f"missing or malformed field {error}."
            )
            return None, None, "metadata_invalid"

        if not isinstance(platform_launchers, list):
            print(
                "Invalid packaged-runtime launcher metadata: "
                f"{current_platform} launchers must be a list."
            )
            return None, None, "metadata_invalid"

        if not platform_launchers:
            print(
                "Cannot select packaged-runtime launcher: "
                f"no {current_platform} launchers are listed."
            )
            return None, None, "metadata_invalid"

        try:
            resolved_profile = ddlc_folder.resolve(
                strict=True
            )
        except (OSError, RuntimeError, ValueError) as error:
            print(
                "Failed to resolve packaged-runtime profile: "
                f"{error}"
            )
            return None, None, "launcher_inspection_failed"

        validated_launchers = []
        seen_launchers = set()

        for launcher_name in platform_launchers:
            if not isinstance(launcher_name, str):
                print(
                    "Invalid packaged-runtime launcher metadata: "
                    "launcher path must be a string."
                )
                return None, None, "metadata_invalid"

            launcher_relative_path = Path(launcher_name)

            if launcher_relative_path.is_absolute():
                print(
                    "Unsafe packaged-runtime launcher path: "
                    f"{launcher_name}"
                )
                return None, None, "metadata_invalid"

            launcher = ddlc_folder / launcher_relative_path

            try:
                resolved_launcher = launcher.resolve(
                    strict=True
                )
            except FileNotFoundError:
                print(
                    "Packaged-runtime launcher does not exist: "
                    f"{launcher}"
                )
                return None, None, "packaged_launcher_missing"
            except (OSError, RuntimeError, ValueError) as error:
                print(
                    "Failed to resolve packaged-runtime launcher "
                    f"{launcher_name}: {error}"
                )
                return None, None, "launcher_inspection_failed"

            if not path_is_inside_profile(
                resolved_profile,
                resolved_launcher,
            ):
                print(
                    "Unsafe packaged-runtime launcher path: "
                    f"{launcher_name}"
                )
                return None, None, "metadata_invalid"

            try:
                launcher_mode = resolved_launcher.stat().st_mode
            except OSError as error:
                print(
                    "Failed to inspect packaged-runtime launcher "
                    f"{resolved_launcher}: {error}"
                )
                return None, None, "launcher_inspection_failed"

            if not stat.S_ISREG(launcher_mode):
                print(
                    "Packaged-runtime launcher is not a regular "
                    f"file: {resolved_launcher}"
                )
                return None, None, "metadata_invalid"

            if resolved_launcher not in seen_launchers:
                seen_launchers.add(resolved_launcher)
                validated_launchers.append(resolved_launcher)

        selected_launcher = None

        if len(validated_launchers) == 1:
            selected_launcher = validated_launchers[0]
        else:
            python_launcher_matches = []

            for launcher in validated_launchers:
                python_launcher = launcher.with_suffix(".py")

                try:
                    resolved_python_launcher = python_launcher.resolve(
                        strict=True
                    )
                except FileNotFoundError:
                    continue
                except (OSError, RuntimeError, ValueError) as error:
                    print(
                        "Failed to inspect matching Ren'Py launcher "
                        f"for {launcher}: {error}"
                    )
                    return None, None, "launcher_inspection_failed"

                if not path_is_inside_profile(
                    resolved_profile,
                    resolved_python_launcher,
                ):
                    continue

                try:
                    python_launcher_mode = (
                        resolved_python_launcher.stat().st_mode
                    )
                except OSError as error:
                    print(
                        "Failed to inspect matching Ren'Py launcher "
                        f"{resolved_python_launcher}: {error}"
                    )
                    return None, None, "launcher_inspection_failed"

                if stat.S_ISREG(python_launcher_mode):
                    python_launcher_matches.append(launcher)

            if len(python_launcher_matches) == 1:
                selected_launcher = python_launcher_matches[0]

            if (
                selected_launcher is None
                and current_platform == "linux"
            ):
                runtime_launcher_matches = []

                try:
                    for launcher in validated_launchers:
                        if launcher.suffix.lower() != ".sh":
                            continue

                        matching_runtimes = (
                            find_matching_linux_runtimes(
                                resolved_profile,
                                launcher.stem,
                            )
                        )

                        if matching_runtimes:
                            runtime_launcher_matches.append(
                                launcher
                            )
                except (OSError, RuntimeError, ValueError) as error:
                    print(
                        "Failed to inspect packaged-runtime Linux "
                        f"runtimes: {error}"
                    )
                    return None, None, "launcher_inspection_failed"

                if len(runtime_launcher_matches) == 1:
                    selected_launcher = runtime_launcher_matches[0]

            if selected_launcher is None:
                if current_platform == "linux":
                    conventional_name = "DDLC.sh"
                else:
                    conventional_name = "DDLC.exe"

                conventional_launchers = [
                    launcher
                    for launcher in validated_launchers
                    if launcher.name == conventional_name
                ]

                if len(conventional_launchers) == 1:
                    selected_launcher = conventional_launchers[0]

        if selected_launcher is None:
            print(
                "Cannot select packaged-runtime launcher: multiple "
                f"{current_platform} launchers remain ambiguous."
            )
            print("Candidate launcher paths:")

            for launcher in validated_launchers:
                print(f" - {launcher}")

            return None, None, "packaged_launcher_ambiguous"

        if (
            current_platform == "linux"
            and not repair_linux_launcher_permissions(
                resolved_profile,
                selected_launcher,
            )
        ):
            return None, None, "permission_repair_failed"

        return selected_launcher, current_platform, None

    try:
        if sys.platform.startswith("linux"):
            preferred = [
                ddlc_folder / "DDLC.sh",
                ddlc_folder / f"{ddlc_folder.name}.sh",
            ]

            for launcher in preferred:
                if launcher.is_file():
                    return launcher, "linux", None

            linux_launchers = sorted(
                ddlc_folder.glob("*.sh")
            )

            if linux_launchers:
                return linux_launchers[0], "linux", None

            windows_launchers = sorted(
                ddlc_folder.glob("*.exe")
            )

            if windows_launchers:
                return windows_launchers[0], "windows", None

        elif sys.platform.startswith("win"):
            preferred = [
                ddlc_folder / "DDLC.exe",
                ddlc_folder / f"{ddlc_folder.name}.exe",
            ]

            for launcher in preferred:
                if launcher.is_file():
                    return launcher, "windows", None

            windows_launchers = sorted(
                ddlc_folder.glob("*.exe")
            )

            if windows_launchers:
                return windows_launchers[0], "windows", None

            linux_launchers = sorted(
                ddlc_folder.glob("*.sh")
            )

            if linux_launchers:
                return linux_launchers[0], "linux", None
        else:
            print(f"Cannot discover a launcher on unsupported platform {sys.platform}.")
            return None, None, "unsupported_platform"
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Failed to inspect legacy profile launchers in {ddlc_folder}: {error}")
        return None, None, "launcher_inspection_failed"

    print(f"No DDLC launcher found in profile: {ddlc_folder}")
    return None, None, "launcher_not_found"


def play_profile(profile_folder, save_folder):
    launcher, launcher_platform, failure_reason = find_ddlc_launcher(
        profile_folder
    )

    if launcher is None:
        title, message = LAUNCHER_DISCOVERY_FAILURE_MESSAGES.get(
            failure_reason,
            LAUNCHER_DISCOVERY_FAILURE_MESSAGES["launcher_inspection_failed"],
        )
        QMessageBox.warning(
            None,
            title,
            message,
        )
        return

    if sys.platform.startswith("linux"):
        current_platform = "linux"
    elif sys.platform.startswith("win"):
        current_platform = "windows"
    else:
        current_platform = None

    if launcher_platform != current_platform:
        platform_names = {"linux": "Linux", "windows": "Windows"}
        launcher_platform_name = platform_names.get(launcher_platform, "another platform")
        current_platform_name = platform_names.get(current_platform, "an unsupported operating system")
        QMessageBox.warning(
            None,
            "Different Platform",
            f"This profile contains a {launcher_platform_name} launcher, "
            f"but this computer is running {current_platform_name}.\n\n"
            "DokDoki Launcher cannot start it on this operating system.",
        )

        print(
            f"Platform mismatch: profile={launcher_platform}, "
            f"system={current_platform}"
        )
        return

    save_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    environment = os.environ.copy()
    environment["RENPY_PATH_TO_SAVES"] = str(
        save_folder
    )

    print(f"Launching profile: {profile_folder}")
    print(f"Save location: {save_folder}")

    try:
        subprocess.Popen(
            [str(launcher)],
            cwd=str(profile_folder),
            env=environment,
            shell=False,
        )

    except PermissionError:
        QMessageBox.warning(
            None,
            "Launcher Permission Error",
            "DokDoki Launcher could not start this profile because its launcher does not have permission "
            "to run on this system.",
        )

        print(f"Permission denied: {launcher}")

    except OSError as error:
        QMessageBox.warning(
            None,
            "Launch Failed",
            "DokDoki Launcher could not start this profile. "
            "Its launcher may be missing, damaged, or inaccessible.",
        )

        print(f"Launch error: {error}")


def play_selected_profile(profile_combo):
    profile_path = profile_combo.currentData()

    if not profile_path:
        QMessageBox.warning(
            None,
            "No Profile Selected",
            "Please select a profile first.",
        )
        return

    profile_folder = Path(profile_path)

    health = inspect_profile_health(profile_folder)
    if health["status"] == "error":
        QMessageBox.warning(
            None,
            "Profile Needs Attention",
            f"{health['issues'][0]}\n\nNo files were changed.",
        )
        return

    save_folder = profile_folder / "saves"

    play_profile(
        profile_folder,
        save_folder,
    )


def get_profiles_folder_path():
    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )

    return Path(app_data) / "profiles"


def get_profiles_folder():
    profiles_folder = get_profiles_folder_path()

    profiles_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    return profiles_folder


def _is_safe_managed_profile_name(profile_name):
    if not isinstance(profile_name, str) or not profile_name:
        return False

    valid_characters = set(
        "abcdefghijklmnopqrstuvwxyz0123456789-"
    )

    if any(
        character not in valid_characters
        for character in profile_name
    ):
        return False

    return not (
        profile_name.startswith("-")
        or profile_name.endswith("-")
        or "--" in profile_name
    )


def _read_tracked_update_profile_metadata(
    profile_folder,
    expected_mod_id,
    profile_description,
):
    metadata_path = (
        profile_folder / ".ddlc_launcher_profile.json"
    )

    try:
        metadata_status = metadata_path.lstat()
    except OSError as error:
        print(
            f"Cannot commit profile update: {profile_description} "
            f"metadata could not be inspected: {error}"
        )
        return None

    if (
        stat.S_ISLNK(metadata_status.st_mode)
        or not stat.S_ISREG(metadata_status.st_mode)
    ):
        print(
            f"Cannot commit profile update: {profile_description} "
            "metadata is not a regular file."
        )
        return None

    try:
        with open(metadata_path, "rb") as metadata_file:
            metadata_data = metadata_file.read(64 * 1024 + 1)

        if len(metadata_data) > 64 * 1024:
            print(
                f"Cannot commit profile update: {profile_description} "
                "metadata exceeds the 64 KiB size limit."
            )
            return None

        profile_metadata = json.loads(
            metadata_data.decode("utf-8")
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        print(
            f"Cannot commit profile update: {profile_description} "
            f"metadata could not be read: {error}"
        )
        return None

    if not isinstance(profile_metadata, dict):
        print(
            f"Cannot commit profile update: {profile_description} "
            "metadata is not a dictionary."
        )
        return None

    format_version = profile_metadata.get("format_version")

    if (
        not isinstance(format_version, int)
        or isinstance(format_version, bool)
        or format_version != 1
    ):
        print(
            f"Cannot commit profile update: {profile_description} "
            "metadata has an unsupported format version."
        )
        return None

    if profile_metadata.get("installation_type") not in (
        "legacy_overlay",
        "game_folder_overlay",
        "packaged_runtime",
    ):
        print(
            f"Cannot commit profile update: {profile_description} "
            "metadata has an unsupported installation type."
        )
        return None

    registry_metadata = sanitize_registry_install_metadata(
        profile_metadata.get("registry")
    )

    if registry_metadata is None:
        print(
            f"Cannot commit profile update: {profile_description} "
            "does not have valid tracked registry metadata."
        )
        return None

    if registry_metadata["id"] != expected_mod_id:
        print(
            f"Cannot commit profile update: {profile_description} "
            "registry ID does not match the stable mod ID."
        )
        return None

    return profile_metadata


def _validate_update_recovery_action(mod_id, action):
    """Read-only preflight; the detector remains the source of classification."""
    if not _is_safe_managed_profile_name(mod_id) or mod_id == "vanilla":
        raise ValueError("Recovery requires a safe non-Vanilla mod ID.")

    if not isinstance(action, str) or action not in UPDATE_RECOVERY_ACTIONS:
        raise ValueError("Unsupported update recovery action.")

    expected_state = UPDATE_RECOVERY_ACTIONS[action]["state"]
    recovery = inspect_update_recovery_state(mod_id)

    if recovery["state"] != expected_state:
        raise ValueError(
            f"Recovery state changed: expected {expected_state}, "
            f"found {recovery['state']}."
        )

    profiles_folder = get_profiles_folder_path()
    snapshots = {}

    def read_status(path):
        try:
            status = path.lstat()
        except FileNotFoundError:
            status = None

        snapshots[path] = status
        return status

    def require_directory(path, expected_name=None):
        status = read_status(path)

        if (
            status is None
            or stat.S_ISLNK(status.st_mode)
            or not stat.S_ISDIR(status.st_mode)
        ):
            raise OSError(f"Recovery path is not a real directory: {path}")

        resolved = path.resolve(strict=True)

        if expected_name is not None and (
            path.name != expected_name
            or resolved.name != expected_name
            or resolved.parent != resolved_profiles_folder
            or resolved == resolved_profiles_folder
        ):
            raise OSError(f"Recovery path is not the expected direct child: {path}")

        return resolved

    resolved_profiles_folder = require_directory(profiles_folder)
    paths = {
        "stable": profiles_folder / mod_id,
        "staging": profiles_folder / f"{mod_id}-update-staging",
        "backup": profiles_folder / f"{mod_id}-update-backup",
    }

    # Preserve the existing protection for the user's selected clean DDLC.
    settings = QSettings("Epicfrisk", "DDLC Mod Launcher")
    saved_ddlc_path = settings.value("ddlc_path", "")
    resolved_ddlc_folder = (
        Path(saved_ddlc_path).resolve(strict=True) if saved_ddlc_path else None
    )

    def inspect_marker(path):
        status = read_status(path)

        if status is not None and (
            stat.S_ISLNK(status.st_mode) or not stat.S_ISREG(status.st_mode)
        ):
            raise OSError(f"Recovery marker is not a regular file: {path}")

        return status is not None

    markers = {}
    sidecars = {}

    for role, path in paths.items():
        status = read_status(path)

        if (status is not None) != recovery[f"{role}_exists"]:
            raise OSError(f"Recovery path changed during preflight: {path}")

        if status is not None:
            resolved = require_directory(path, path.name)

            if resolved_ddlc_folder is not None and (
                resolved == resolved_ddlc_folder
                or resolved in resolved_ddlc_folder.parents
                or resolved_ddlc_folder in resolved.parents
            ):
                raise OSError(f"Recovery would affect the original DDLC: {path}")

            markers[role] = inspect_marker(path / ".ddlc_launcher_incomplete")
        else:
            markers[role] = False

        sidecars[role] = inspect_marker(
            profiles_folder / f".{path.name}.ddlc_launcher_incomplete"
        )

    def require_metadata(role, optional=False):
        path = paths[role]
        metadata_status = read_status(path / ".ddlc_launcher_profile.json")

        if optional and metadata_status is None:
            return

        if _read_tracked_update_profile_metadata(
            path, mod_id, f"recovery {role} profile"
        ) is None:
            raise OSError(f"Recovery profile has invalid tracked metadata: {path}")

    if action == "discard_staging":
        require_metadata("stable")

        if not (markers["staging"] or sidecars["staging"]):
            raise OSError(f"Recovery staging has no incomplete marker: {paths['staging']}")

        require_metadata("staging", optional=True)
        staging_saves = paths["staging"] / "saves"

        if read_status(staging_saves) is not None:
            raise OSError(
                f"Recovery cannot delete staging data containing saves: {staging_saves}"
            )
    else:
        require_metadata("backup")

        if not markers["backup"]:
            raise OSError(f"Recovery backup has no transaction marker: {paths['backup']}")

        if action == "remove_backup":
            require_metadata("stable")
            backup_saves = paths["backup"] / "saves"

            if read_status(backup_saves) is not None:
                raise OSError(f"Recovery cannot delete a backup containing saves: {backup_saves}")

    return profiles_folder, resolved_profiles_folder, paths, snapshots


def resolve_update_recovery(mod_id, action):
    """Perform one explicitly confirmed action, with fresh backend validation."""
    try:
        profiles_folder, resolved_profiles_folder, paths, snapshots = (
            _validate_update_recovery_action(mod_id, action)
        )

        def require_unchanged(path, expected_status):
            try:
                current = path.lstat()
            except FileNotFoundError:
                current = None

            def fingerprint(status):
                if status is None:
                    return None
                return (
                    status.st_dev, status.st_ino, status.st_mode,
                    status.st_size, status.st_mtime_ns,
                )

            if fingerprint(current) != fingerprint(expected_status):
                raise OSError(f"Recovery path changed before modification: {path}")

        # Repeat all state, marker, metadata and saves checks immediately
        # before mutation, and reject even same-state directory replacement.
        _, current_resolved_folder, _, current_snapshots = (
            _validate_update_recovery_action(mod_id, action)
        )

        if current_resolved_folder != resolved_profiles_folder:
            raise OSError(f"Managed profiles location changed: {profiles_folder}")

        if current_snapshots.keys() != snapshots.keys():
            raise OSError("Recovery objects changed during preflight.")

        for path, expected_status in snapshots.items():
            require_unchanged(path, expected_status)

        def require_exact_directory(role, expected_status):
            path = paths[role]
            expected_name = (
                mod_id if role == "stable" else f"{mod_id}-update-{role}"
            )
            require_unchanged(profiles_folder, snapshots[profiles_folder])
            require_unchanged(path, expected_status)
            status = path.lstat()
            resolved = path.resolve(strict=True)

            if (
                stat.S_ISLNK(status.st_mode)
                or not stat.S_ISDIR(status.st_mode)
                or path.name != expected_name
                or resolved.name != expected_name
                or resolved.parent != resolved_profiles_folder
            ):
                raise OSError(f"Unsafe recovery directory before modification: {path}")

            return resolved

        def require_profiles_identity():
            current_profiles_status = profiles_folder.lstat()
            original_profiles_status = snapshots[profiles_folder]
            if (
                stat.S_ISLNK(current_profiles_status.st_mode)
                or not stat.S_ISDIR(current_profiles_status.st_mode)
                or (current_profiles_status.st_dev, current_profiles_status.st_ino)
                != (original_profiles_status.st_dev, original_profiles_status.st_ino)
                or profiles_folder.resolve(strict=True) != resolved_profiles_folder
            ):
                raise OSError(
                    "Managed profiles directory changed before marker removal: "
                    f"{profiles_folder}"
                )

        def remove_sidecar(role):
            marker = profiles_folder / (
                f".{paths[role].name}.ddlc_launcher_incomplete"
            )
            require_profiles_identity()

            require_unchanged(marker, snapshots[marker])

            if snapshots[marker] is not None:
                if not validate_incomplete_profile_marker(marker):
                    raise OSError(f"Unsafe recovery sidecar: {marker}")
                marker.unlink()

        if action in ("discard_staging", "remove_backup"):
            role = "staging" if action == "discard_staging" else "backup"
            target = require_exact_directory(role, snapshots[paths[role]])
            require_unchanged(target / "saves", None)
            shutil.rmtree(target)
            remove_sidecar(role)
            print(f"Removed validated update recovery directory: {target}")
        else:
            backup = require_exact_directory("backup", snapshots[paths["backup"]])
            stable = paths["stable"]
            require_unchanged(stable, None)
            backup.rename(stable)
            print(f"Restored update backup {backup} to {stable}")

            # Renaming changes the profiles folder, so only its identity/type
            # is checked now; the restored directory must be the same source.
            backup_status = snapshots[paths["backup"]]

            def require_restored_directory():
                require_profiles_identity()
                restored_status = stable.lstat()
                if (
                    stat.S_ISLNK(restored_status.st_mode)
                    or not stat.S_ISDIR(restored_status.st_mode)
                    or (restored_status.st_dev, restored_status.st_ino)
                    != (backup_status.st_dev, backup_status.st_ino)
                    or stable.resolve(strict=True).parent != resolved_profiles_folder
                ):
                    raise OSError(
                        f"Restored stable profile could not be verified: {stable}"
                    )

            require_restored_directory()

            restored_marker = stable / ".ddlc_launcher_incomplete"
            require_unchanged(
                restored_marker,
                snapshots[paths["backup"] / ".ddlc_launcher_incomplete"],
            )
            if not validate_incomplete_profile_marker(restored_marker):
                raise OSError(f"Restored transaction marker is unsafe: {restored_marker}")

            # Keep the internal marker until all sidecar cleanup succeeds.
            # Any failure leaves the restored directory hidden, never deleted.
            remove_sidecar("stable")
            remove_sidecar("backup")
            require_restored_directory()
            require_unchanged(
                restored_marker,
                snapshots[paths["backup"] / ".ddlc_launcher_incomplete"],
            )
            if not validate_incomplete_profile_marker(restored_marker):
                raise OSError(f"Restored transaction marker is unsafe: {restored_marker}")
            restored_marker.unlink()
            print(f"Removed restored transaction marker: {restored_marker}")

        return True
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        print(
            f"Update recovery failed for {mod_id!r}, action {action!r}: {error}. "
            "Existing data was preserved where possible."
        )
        return False


def _read_optional_update_metadata_bytes(
    metadata_path,
    metadata_description,
):
    try:
        metadata_status = metadata_path.lstat()
    except FileNotFoundError:
        return True, False, None
    except OSError as error:
        print(
            f"Cannot commit profile update: {metadata_description} "
            f"could not be inspected: {error}"
        )
        return False, False, None

    if (
        stat.S_ISLNK(metadata_status.st_mode)
        or not stat.S_ISREG(metadata_status.st_mode)
    ):
        print(
            f"Cannot commit profile update: {metadata_description} "
            "is not a regular file."
        )
        return False, False, None

    try:
        with open(metadata_path, "rb") as metadata_file:
            metadata_data = metadata_file.read(64 * 1024 + 1)
    except OSError as error:
        print(
            f"Cannot commit profile update: {metadata_description} "
            f"could not be read: {error}"
        )
        return False, False, None

    if len(metadata_data) > 64 * 1024:
        print(
            f"Cannot commit profile update: {metadata_description} "
            "exceeds the 64 KiB size limit."
        )
        return False, False, None

    return True, True, metadata_data


def _replace_update_metadata_bytes(metadata_path, metadata_data):
    temporary_path = None
    file_descriptor = None

    try:
        try:
            destination_status = metadata_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if (
                stat.S_ISLNK(destination_status.st_mode)
                or not stat.S_ISREG(destination_status.st_mode)
            ):
                raise OSError(
                    "metadata destination is not a regular file"
                )

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{metadata_path.name}.",
            suffix=".tmp",
            dir=metadata_path.parent,
        )
        temporary_path = Path(temporary_name)

        with os.fdopen(file_descriptor, "wb") as temporary_file:
            file_descriptor = None
            temporary_file.write(metadata_data)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, metadata_path)
        temporary_path = None
        return True
    except OSError as error:
        print(
            "Failed to preserve profile user metadata atomically: "
            f"{error}"
        )
        return False
    finally:
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError as error:
                print(
                    "Failed to close temporary profile metadata file: "
                    f"{error}"
                )

        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as error:
                print(
                    "Failed to clean up temporary profile metadata "
                    f"file {temporary_path}: {error}"
                )


def commit_staged_profile_update(
    mod_id,
    staging_directory_name,
):
    if not _is_safe_managed_profile_name(mod_id):
        print(
            "Cannot commit profile update: mod ID is not a safe "
            "managed profile name."
        )
        return False

    if not _is_safe_managed_profile_name(
        staging_directory_name
    ):
        print(
            "Cannot commit profile update: staging directory name is "
            "not a safe managed profile name."
        )
        return False

    if staging_directory_name == mod_id:
        print(
            "Cannot commit profile update: staging directory name must "
            "differ from the stable mod ID."
        )
        return False

    if mod_id == "vanilla" or staging_directory_name == "vanilla":
        print("Cannot commit profile update: Vanilla is protected.")
        return False

    backup_directory_name = f"{mod_id}-update-backup"

    if not _is_safe_managed_profile_name(
        backup_directory_name
    ):
        print(
            "Cannot commit profile update: backup directory name is "
            "not safe."
        )
        return False

    try:
        profiles_folder = get_profiles_folder()
        profiles_status = profiles_folder.lstat()
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "Cannot commit profile update: managed profiles directory "
            f"could not be inspected: {error}"
        )
        return False

    if (
        stat.S_ISLNK(profiles_status.st_mode)
        or not stat.S_ISDIR(profiles_status.st_mode)
    ):
        print(
            "Cannot commit profile update: managed profiles path is "
            "not a real directory."
        )
        return False

    try:
        resolved_profiles_folder = profiles_folder.resolve(
            strict=True
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "Cannot commit profile update: managed profiles directory "
            f"could not be resolved: {error}"
        )
        return False

    stable_profile = profiles_folder / mod_id
    staging_profile = profiles_folder / staging_directory_name
    backup_profile = profiles_folder / backup_directory_name

    try:
        stable_status = stable_profile.lstat()
        staging_status = staging_profile.lstat()
    except FileNotFoundError as error:
        print(
            "Cannot commit profile update: stable or staging profile "
            f"does not exist: {error}"
        )
        return False
    except OSError as error:
        print(
            "Cannot commit profile update: stable or staging profile "
            f"could not be inspected: {error}"
        )
        return False

    for profile_path, profile_status, profile_description in (
        (stable_profile, stable_status, "stable profile"),
        (staging_profile, staging_status, "staging profile"),
    ):
        if (
            stat.S_ISLNK(profile_status.st_mode)
            or not stat.S_ISDIR(profile_status.st_mode)
        ):
            print(
                f"Cannot commit profile update: {profile_description} "
                "is not a real directory."
            )
            return False

        try:
            resolved_profile = profile_path.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            print(
                f"Cannot commit profile update: {profile_description} "
                f"could not be resolved: {error}"
            )
            return False

        if resolved_profile.parent != resolved_profiles_folder:
            print(
                f"Cannot commit profile update: {profile_description} "
                "is not a direct child of the managed profiles "
                "directory."
            )
            return False

    try:
        backup_profile.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        print(
            "Cannot commit profile update: backup path could not be "
            f"inspected: {error}"
        )
        return False
    else:
        print(
            "Cannot commit profile update: backup path already exists: "
            f"{backup_profile}"
        )
        return False

    stable_marker = (
        stable_profile / ".ddlc_launcher_incomplete"
    )
    stable_sidecar_marker = profiles_folder / (
        f".{mod_id}.ddlc_launcher_incomplete"
    )
    staging_marker = (
        staging_profile / ".ddlc_launcher_incomplete"
    )

    for marker_path, marker_description in (
        (stable_marker, "stable incomplete marker"),
        (stable_sidecar_marker, "stable sidecar marker"),
    ):
        try:
            marker_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            print(
                f"Cannot commit profile update: {marker_description} "
                f"could not be inspected: {error}"
            )
            return False
        else:
            print(
                f"Cannot commit profile update: {marker_description} "
                "already exists."
            )
            return False

    if not validate_incomplete_profile_marker(staging_marker):
        print(
            "Cannot commit profile update: staging profile is not "
            "intentionally marked incomplete."
        )
        return False

    if _read_tracked_update_profile_metadata(
        stable_profile,
        mod_id,
        "stable profile",
    ) is None:
        return False

    if _read_tracked_update_profile_metadata(
        staging_profile,
        mod_id,
        "staging profile",
    ) is None:
        return False

    stable_saves = stable_profile / "saves"
    staging_saves = staging_profile / "saves"
    stable_saves_exist = False

    try:
        stable_saves_status = stable_saves.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        print(
            "Cannot commit profile update: stable saves could not be "
            f"inspected: {error}"
        )
        return False
    else:
        if (
            stat.S_ISLNK(stable_saves_status.st_mode)
            or not stat.S_ISDIR(stable_saves_status.st_mode)
        ):
            print(
                "Cannot commit profile update: stable saves path is "
                "not a real directory."
            )
            return False

        stable_saves_exist = True

    if stable_saves_exist:
        try:
            staging_saves.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            print(
                "Cannot commit profile update: staging saves path "
                f"could not be inspected: {error}"
            )
            return False
        else:
            print(
                "Cannot commit profile update: staging profile already "
                "contains a saves filesystem object."
            )
            return False

    stable_user_metadata = (
        stable_profile / ".ddlc_launcher_user.json"
    )
    staging_user_metadata = (
        staging_profile / ".ddlc_launcher_user.json"
    )
    (
        user_metadata_ok,
        stable_user_metadata_exists,
        stable_user_metadata_data,
    ) = _read_optional_update_metadata_bytes(
        stable_user_metadata,
        "stable profile user metadata",
    )

    if not user_metadata_ok:
        return False

    (
        staging_user_metadata_ok,
        staging_user_metadata_exists,
        staging_user_metadata_data,
    ) = _read_optional_update_metadata_bytes(
        staging_user_metadata,
        "staging profile user metadata",
    )

    if not staging_user_metadata_ok:
        return False

    transaction_started = False
    old_profile_moved = False
    staging_profile_moved = False
    saves_moved = False
    user_metadata_changed = False

    def rollback_update(failure_message):
        print(f"Profile update failed: {failure_message}")
        print("Attempting profile update rollback...")
        rollback_complete = True
        can_restore_old_profile = True

        if saves_moved:
            rollback_saves_source = stable_profile / "saves"
            rollback_saves_destination = backup_profile / "saves"

            try:
                source_status = rollback_saves_source.lstat()

                if (
                    stat.S_ISLNK(source_status.st_mode)
                    or not stat.S_ISDIR(source_status.st_mode)
                ):
                    raise OSError(
                        "new stable saves path is not a real directory"
                    )

                try:
                    rollback_saves_destination.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise OSError(
                        "backup saves destination already exists"
                    )

                rollback_saves_source.rename(
                    rollback_saves_destination
                )
                print("Rollback restored saves to the old backup.")
            except OSError as error:
                print(
                    "Rollback could not restore saves to the old "
                    f"backup: {error}"
                )
                rollback_complete = False
                can_restore_old_profile = False

        staging_returned = not staging_profile_moved

        if staging_profile_moved and can_restore_old_profile:
            try:
                try:
                    staging_profile.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise OSError(
                        "original staging path already exists"
                    )

                stable_profile.rename(staging_profile)
                staging_returned = True
                print(
                    "Rollback returned the new profile to its staging "
                    "directory."
                )
            except OSError as error:
                print(
                    "Rollback could not return the new profile to its "
                    f"staging directory: {error}"
                )
                rollback_complete = False
                can_restore_old_profile = False

        if (
            user_metadata_changed
            and staging_returned
            and staging_profile_moved
        ):
            restored_staging_user_metadata = True

            if staging_user_metadata_exists:
                restored_staging_user_metadata = (
                    _replace_update_metadata_bytes(
                        staging_user_metadata,
                        staging_user_metadata_data,
                    )
                )
            else:
                try:
                    restored_status = staging_user_metadata.lstat()

                    if (
                        stat.S_ISLNK(restored_status.st_mode)
                        or not stat.S_ISREG(restored_status.st_mode)
                    ):
                        raise OSError(
                            "staging user metadata is unsafe"
                        )

                    staging_user_metadata.unlink()
                except FileNotFoundError:
                    pass
                except OSError as error:
                    print(
                        "Rollback could not remove staging user "
                        f"metadata: {error}"
                    )
                    restored_staging_user_metadata = False

            if not restored_staging_user_metadata:
                rollback_complete = False
            else:
                print(
                    "Rollback restored the staging profile's original "
                    "user-metadata state."
                )

        old_profile_restored = not old_profile_moved

        if old_profile_moved and can_restore_old_profile:
            try:
                try:
                    stable_profile.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise OSError(
                        "stable profile destination already exists"
                    )

                backup_profile.rename(stable_profile)
                old_profile_restored = True
                print("Rollback restored the old stable profile.")
            except OSError as error:
                print(
                    "Rollback could not restore the old stable profile: "
                    f"{error}"
                )
                rollback_complete = False
                can_restore_old_profile = False

        if transaction_started and old_profile_restored:
            restored_stable_marker = (
                stable_profile / ".ddlc_launcher_incomplete"
            )

            try:
                marker_status = restored_stable_marker.lstat()

                if (
                    stat.S_ISLNK(marker_status.st_mode)
                    or not stat.S_ISREG(marker_status.st_mode)
                ):
                    raise OSError(
                        "restored stable marker is not a regular file"
                    )

                restored_stable_marker.unlink()
                print(
                    "Rollback made the original stable profile visible "
                    "again."
                )
            except OSError as error:
                print(
                    "Rollback could not remove the temporary marker "
                    f"from the old stable profile: {error}"
                )
                rollback_complete = False

        if rollback_complete:
            print("Profile update rollback completed successfully.")
        else:
            print(
                "Profile update rollback was incomplete. No profile "
                "was deleted."
            )
            print(f"Stable path: {stable_profile}")
            print(f"Staging path: {staging_profile}")
            print(f"Backup path: {backup_profile}")

        return False

    marker_descriptor = None

    try:
        marker_descriptor = os.open(
            stable_marker,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        transaction_started = True
        os.close(marker_descriptor)
        marker_descriptor = None

        if not validate_incomplete_profile_marker(stable_marker):
            return rollback_update(
                "the temporary stable-profile marker could not be "
                "verified"
            )

        stable_profile.rename(backup_profile)
        old_profile_moved = True
        print(f"Moved old stable profile to backup: {backup_profile}")

        staging_profile.rename(stable_profile)
        staging_profile_moved = True
        print(f"Moved staging profile into place: {stable_profile}")

        if stable_user_metadata_exists:
            new_stable_user_metadata = (
                stable_profile / ".ddlc_launcher_user.json"
            )

            if not _replace_update_metadata_bytes(
                new_stable_user_metadata,
                stable_user_metadata_data,
            ):
                return rollback_update(
                    "the old profile user metadata could not be "
                    "preserved"
                )

            user_metadata_changed = True
            print("Preserved old profile user metadata.")
        else:
            new_stable_user_metadata = (
                stable_profile / ".ddlc_launcher_user.json"
            )

            try:
                new_user_metadata_status = (
                    new_stable_user_metadata.lstat()
                )
            except FileNotFoundError:
                if staging_user_metadata_exists:
                    user_metadata_changed = True
            else:
                if (
                    stat.S_ISLNK(new_user_metadata_status.st_mode)
                    or not stat.S_ISREG(
                        new_user_metadata_status.st_mode
                    )
                ):
                    raise OSError(
                        "new profile user metadata is not a regular "
                        "file"
                    )

                new_stable_user_metadata.unlink()
                user_metadata_changed = True
                print(
                    "Removed staging profile user metadata because "
                    "the old stable profile had none."
                )

        if stable_saves_exist:
            backup_saves = backup_profile / "saves"
            new_stable_saves = stable_profile / "saves"

            try:
                backup_saves_status = backup_saves.lstat()

                if (
                    stat.S_ISLNK(backup_saves_status.st_mode)
                    or not stat.S_ISDIR(backup_saves_status.st_mode)
                ):
                    raise OSError(
                        "backup saves path is not a real directory"
                    )

                try:
                    new_stable_saves.lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise OSError(
                        "new stable saves destination already exists"
                    )

                backup_saves.rename(new_stable_saves)
                saves_moved = True
                print("Transferred saves into the new stable profile.")
            except OSError as error:
                return rollback_update(
                    f"saves could not be transferred: {error}"
                )

        new_stable_marker = (
            stable_profile / ".ddlc_launcher_incomplete"
        )

        if not validate_incomplete_profile_marker(new_stable_marker):
            return rollback_update(
                "the new stable profile incomplete marker is unsafe"
            )

        new_stable_marker.unlink()
        print("Profile update committed successfully.")
    except (OSError, RuntimeError, ValueError) as error:
        if transaction_started:
            return rollback_update(str(error))

        print(
            "Cannot commit profile update: transaction could not be "
            f"started: {error}"
        )
        return False
    finally:
        if marker_descriptor is not None:
            try:
                os.close(marker_descriptor)
            except OSError as error:
                print(
                    "Failed to close temporary incomplete marker: "
                    f"{error}"
                )

    try:
        backup_status = backup_profile.lstat()

        if (
            stat.S_ISLNK(backup_status.st_mode)
            or not stat.S_ISDIR(backup_status.st_mode)
        ):
            raise OSError(
                "backup path is no longer a real directory"
            )

        resolved_backup = backup_profile.resolve(strict=True)

        if resolved_backup.parent != resolved_profiles_folder:
            raise OSError(
                "backup path is outside the managed profiles directory"
            )

        shutil.rmtree(backup_profile)
        print("Removed old profile backup after successful update.")
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "Profile update succeeded, but the hidden old backup could "
            f"not be removed: {error}"
        )
        print(f"Hidden backup path: {backup_profile}")

    return True


def get_preserved_saves_folder():
    app_data = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )

    preserved_saves_folder = Path(app_data) / "preserved_saves"

    preserved_saves_folder.mkdir(
        parents=True,
        exist_ok=True,
    )

    return preserved_saves_folder


def preserve_profile_saves(profile_folder):
    profile_folder = Path(profile_folder)

    try:
        profiles_folder = get_profiles_folder()
        resolved_profiles_folder = profiles_folder.resolve(
            strict=True
        )
        profile_status = profile_folder.lstat()
    except FileNotFoundError:
        print(
            "Cannot preserve profile saves: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to inspect profile saves: {error}")
        return False

    if stat.S_ISLNK(profile_status.st_mode):
        print(
            "Cannot preserve profile saves: symbolic-link profiles "
            f"are not allowed: {profile_folder}"
        )
        return False

    try:
        resolved_profile = profile_folder.resolve(
            strict=True
        )
    except FileNotFoundError:
        print(
            "Cannot preserve profile saves: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to resolve profile for save preservation: {error}")
        return False

    if resolved_profile.parent != resolved_profiles_folder:
        print(
            "Cannot preserve profile saves: the requested path is not "
            "a direct child of the managed profiles directory: "
            f"{profile_folder}"
        )
        return False

    if resolved_profile == resolved_profiles_folder / "Vanilla":
        print("Cannot preserve profile saves: Vanilla is protected.")
        return False

    if not stat.S_ISDIR(profile_status.st_mode):
        print(
            "Cannot preserve profile saves: the requested profile is "
            f"not a directory: {profile_folder}"
        )
        return False

    source_saves = profile_folder / "saves"

    try:
        saves_status = source_saves.lstat()
    except FileNotFoundError:
        print(f"No isolated saves to preserve: {source_saves}")
        return True
    except OSError as error:
        print(f"Failed to inspect isolated saves: {error}")
        return False

    if stat.S_ISLNK(saves_status.st_mode):
        print(
            "Cannot preserve profile saves: the saves path is a "
            f"symbolic link: {source_saves}"
        )
        return False

    if not stat.S_ISDIR(saves_status.st_mode):
        print(
            "Cannot preserve profile saves: the saves path is not a "
            f"directory: {source_saves}"
        )
        return False

    try:
        preserved_saves_folder = get_preserved_saves_folder()
    except OSError as error:
        print(f"Failed to prepare preserved-saves folder: {error}")
        return False

    destination = (
        preserved_saves_folder / resolved_profile.name
    )

    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"Failed to inspect preserved-saves destination: {error}")
        return False
    else:
        print(
            "Cannot preserve profile saves: the destination already "
            f"exists: {destination}"
        )
        return False

    try:
        shutil.move(source_saves, destination)
    except (OSError, shutil.Error) as error:
        print(f"Failed to preserve profile saves: {error}")
        return False

    print(f"Profile saves preserved successfully: {destination}")
    return True


def restore_profile_saves(profile_folder):
    profile_folder = Path(profile_folder)

    try:
        profiles_folder = get_profiles_folder()
        resolved_profiles_folder = profiles_folder.resolve(
            strict=True
        )
        profile_status = profile_folder.lstat()
    except FileNotFoundError:
        print(
            "Cannot restore profile saves: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to inspect profile for save restoration: {error}")
        return False

    if stat.S_ISLNK(profile_status.st_mode):
        print(
            "Cannot restore profile saves: symbolic-link profiles "
            f"are not allowed: {profile_folder}"
        )
        return False

    try:
        resolved_profile = profile_folder.resolve(
            strict=True
        )
    except FileNotFoundError:
        print(
            "Cannot restore profile saves: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to resolve profile for save restoration: {error}")
        return False

    if resolved_profile.parent != resolved_profiles_folder:
        print(
            "Cannot restore profile saves: the requested path is not "
            "a direct child of the managed profiles directory: "
            f"{profile_folder}"
        )
        return False

    if resolved_profile == resolved_profiles_folder / "Vanilla":
        print("Cannot restore profile saves: Vanilla is protected.")
        return False

    if not stat.S_ISDIR(profile_status.st_mode):
        print(
            "Cannot restore profile saves: the requested profile is "
            f"not a directory: {profile_folder}"
        )
        return False

    try:
        preserved_saves_folder = get_preserved_saves_folder()
    except OSError as error:
        print(f"Failed to prepare preserved-saves folder: {error}")
        return False

    preserved_saves = (
        preserved_saves_folder / resolved_profile.name
    )

    try:
        preserved_status = preserved_saves.lstat()
    except FileNotFoundError:
        print(f"No preserved saves to restore: {preserved_saves}")
        return True
    except OSError as error:
        print(f"Failed to inspect preserved saves: {error}")
        return False

    if stat.S_ISLNK(preserved_status.st_mode):
        print(
            "Cannot restore profile saves: the preserved saves path "
            f"is a symbolic link: {preserved_saves}"
        )
        return False

    if not stat.S_ISDIR(preserved_status.st_mode):
        print(
            "Cannot restore profile saves: the preserved saves path "
            f"is not a directory: {preserved_saves}"
        )
        return False

    destination = resolved_profile / "saves"

    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        print(f"Failed to inspect save restoration destination: {error}")
        return False
    else:
        print(
            "Cannot restore profile saves: the destination already "
            f"exists: {destination}"
        )
        return False

    try:
        shutil.move(preserved_saves, destination)
    except (OSError, shutil.Error) as error:
        print(f"Failed to restore profile saves: {error}")
        return False

    print(f"Profile saves restored successfully: {destination}")
    return True


def remove_profile(profile_folder):
    profile_folder = Path(profile_folder)

    try:
        profiles_folder = get_profiles_folder()
        resolved_profiles_folder = profiles_folder.resolve(
            strict=True
        )
        profile_status = profile_folder.lstat()
        resolved_profile = profile_folder.resolve(
            strict=True
        )
    except FileNotFoundError:
        print(
            "Cannot remove profile: the requested profile "
            f"does not exist: {profile_folder}"
        )
        return False
    except (OSError, RuntimeError) as error:
        print(f"Failed to inspect profile for removal: {error}")
        return False

    if stat.S_ISLNK(profile_status.st_mode):
        print(
            "Cannot remove profile: symbolic links are not allowed: "
            f"{profile_folder}"
        )
        return False

    if resolved_profile == resolved_profiles_folder:
        print(
            "Cannot remove profile: the managed profiles directory "
            "cannot be deleted."
        )
        return False

    if resolved_profile.parent != resolved_profiles_folder:
        print(
            "Cannot remove profile: the requested path is not a "
            "direct child of the managed profiles directory: "
            f"{profile_folder}"
        )
        return False

    if resolved_profile == resolved_profiles_folder / "Vanilla":
        print("Cannot remove profile: Vanilla is protected.")
        return False

    if not stat.S_ISDIR(profile_status.st_mode):
        print(
            "Cannot remove profile: the requested path is not a "
            f"directory: {profile_folder}"
        )
        return False

    settings = QSettings(
        "Epicfrisk",
        "DDLC Mod Launcher",
    )
    saved_ddlc_path = settings.value(
        "ddlc_path",
        "",
    )

    if saved_ddlc_path:
        try:
            resolved_ddlc_folder = Path(saved_ddlc_path).resolve(
                strict=True
            )
        except (OSError, RuntimeError, TypeError) as error:
            print(
                "Failed to resolve the saved original DDLC "
                f"installation: {error}"
            )
            return False

        if resolved_profile == resolved_ddlc_folder:
            print(
                "Cannot remove profile: the original DDLC "
                "installation is protected."
            )
            return False

    try:
        shutil.rmtree(resolved_profile)
    except OSError as error:
        print(f"Failed to remove profile: {error}")
        return False

    print(f"Profile removed successfully: {resolved_profile}")
    return True


def remove_selected_profile(profile_combo, keep_saves):
    profile_path = profile_combo.currentData()

    if not profile_path:
        QMessageBox.warning(
            None,
            "No Profile Selected",
            "Please select a profile to remove.",
        )
        return

    profile_folder = Path(profile_path)

    if profile_folder.name == "Vanilla":
        QMessageBox.warning(
            None,
            "Vanilla Profile Protected",
            "The Vanilla profile is protected and cannot be removed.",
        )
        return

    if keep_saves:
        confirmation_text = (
            f"Permanently remove the profile "
            f"'{profile_folder.name}'?\n\n"
            "The mod and profile files will be permanently deleted, "
            "but its isolated saves will be moved to the launcher's "
            "preserved-saves folder and kept. The original DDLC "
            "installation will not be affected."
        )
    else:
        confirmation_text = (
            f"Permanently remove the profile "
            f"'{profile_folder.name}'?\n\n"
            "This will delete the mod files and all isolated saves for "
            "this profile. The original DDLC installation will not be "
            "affected."
        )

    confirmation = QMessageBox.question(
        None,
        "Remove Profile",
        confirmation_text,
        (
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        ),
        QMessageBox.StandardButton.No,
    )

    if confirmation != QMessageBox.StandardButton.Yes:
        return

    saves_were_preserved = False

    if keep_saves:
        had_saves = (profile_folder / "saves").is_dir()

        if not preserve_profile_saves(profile_folder):
            QMessageBox.warning(
                None,
                "Save Preservation Failed",
                "The profile was not removed because its saves "
                "could not be preserved.",
            )
            return

        saves_were_preserved = had_saves

    if not remove_profile(profile_folder):
        if keep_saves and saves_were_preserved:
            preserved_saves = (
                get_preserved_saves_folder()
                / profile_folder.name
            )
            original_saves = profile_folder / "saves"

            if preserved_saves.is_dir():
                if (
                    profile_folder.is_dir()
                    and not original_saves.exists()
                    and not original_saves.is_symlink()
                ):
                    try:
                        shutil.move(
                            preserved_saves,
                            original_saves,
                        )
                    except (OSError, shutil.Error) as error:
                        print(
                            "Failed to restore preserved saves after "
                            f"profile removal failed: {error}"
                        )
                        QMessageBox.warning(
                            None,
                            "Profile Removal Failed",
                            "Profile removal failed. The saves are still "
                            "preserved in the preserved-saves folder, "
                            "but they could not automatically be moved "
                            "back.",
                        )
                        return

                    print(
                        "Profile saves restored after failed profile "
                        f"removal: {original_saves}"
                    )
                else:
                    QMessageBox.warning(
                        None,
                        "Profile Removal Failed",
                        "Profile removal failed. The saves are still "
                        "preserved in the preserved-saves folder, but "
                        "they could not automatically be moved back."
                    )
                    return

        QMessageBox.warning(
            None,
            "Profile Removal Failed",
            "The selected profile could not be removed.",
        )
        return

    refresh_profile_combo(
        profile_combo,
        get_profiles_folder() / "Vanilla",
    )


def get_available_profiles():
    try:
        profiles_folder = _resolve_managed_profiles_directory()
        candidates = list(profiles_folder.iterdir())
    except FileNotFoundError:
        return []
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not discover managed profiles: {error}")
        return []

    profiles = []
    for path in candidates:
        try:
            resolved_profile = _resolve_managed_profile_directory(path, profiles_folder)
            if _profile_has_incomplete_marker(resolved_profile):
                continue
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Skipping unsafe or unreadable profile candidate {path}: {error}")
            continue
        profiles.append(path)

    return sorted(
        profiles,
        key=lambda path: path.name.lower(),
    )


def refresh_profile_combo(profile_combo, preferred_profile=None):
    current_profile = profile_combo.currentData()
    current_profile_path = (
        str(current_profile)
        if current_profile
        else None
    )
    preferred_profile_path = (
        str(preferred_profile)
        if preferred_profile is not None
        else None
    )

    profile_combo.clear()

    current_index = -1
    preferred_index = -1
    vanilla_index = -1

    for profile in get_available_profiles():
        profile_path = str(profile)

        profile_combo.addItem(
            get_profile_display_name(profile),
            profile_path,
        )

        index = profile_combo.count() - 1

        if profile_path == current_profile_path:
            current_index = index

        if profile_path == preferred_profile_path:
            preferred_index = index

        if profile.name == "Vanilla":
            vanilla_index = index

    if preferred_index >= 0:
        profile_combo.setCurrentIndex(
            preferred_index
        )
    elif current_index >= 0:
        profile_combo.setCurrentIndex(
            current_index
        )
    elif vanilla_index >= 0:
        profile_combo.setCurrentIndex(
            vanilla_index
        )


def create_vanilla_profile(settings):
    saved_path = settings.value(
        "ddlc_path",
        "",
    )

    if not saved_path:
        print(
            "Cannot create Vanilla profile: "
            "DDLC is not selected."
        )
        return None

    ddlc_folder = Path(saved_path)

    if not ddlc_folder.is_dir():
        print(
            "Cannot create Vanilla profile: "
            "DDLC folder does not exist."
        )
        return None

    profiles_folder = get_profiles_folder()
    vanilla_profile = profiles_folder / "Vanilla"

    if vanilla_profile.exists():
        print(
            f"Vanilla profile already exists: "
            f"{vanilla_profile}"
        )
        return vanilla_profile

    print(
        f"Creating Vanilla profile: "
        f"{vanilla_profile}"
    )
    print("Copying DDLC files...")

    try:
        shutil.copytree(
            ddlc_folder,
            vanilla_profile,
        )

    except OSError as error:
        print(
            f"Failed to create Vanilla profile: "
            f"{error}"
        )
        return None

    print("Vanilla profile created successfully.")

    return vanilla_profile


def play_ddlc(settings):
    saved_path = settings.value(
        "ddlc_path",
        "",
    )

    if not saved_path:
        QMessageBox.warning(
            None,
            "DDLC Not Installed",
            "Please locate your DDLC installation first.",
        )
        return

    ddlc_folder = Path(saved_path)

    version = detect_ddlc_version(
        ddlc_folder
    )

    if version is None:
        QMessageBox.warning(
            None,
            "DDLC Not Recognized",
            "The saved DDLC installation is no longer valid.",
        )
        return

    profiles_folder = get_profiles_folder()
    vanilla_profile = profiles_folder / "Vanilla"
    vanilla_saves = vanilla_profile / "saves"

    if not vanilla_profile.is_dir():
        print("Vanilla profile does not exist.")
        print("Creating Vanilla profile...")

        vanilla_profile = create_vanilla_profile(
            settings
        )

        if vanilla_profile is None:
            QMessageBox.warning(
                None,
                "Profile Creation Failed",
                "The Vanilla profile could not be created.",
            )
            return

        vanilla_saves = vanilla_profile / "saves"

    play_profile(
        vanilla_profile,
        vanilla_saves,
    )


def main():
    app = QApplication(sys.argv)

    app.setOrganizationName("Epicfrisk")
    app.setApplicationName(
        "DDLC Mod Launcher"
    )
    app.setApplicationDisplayName("DokDoki Launcher")

    settings = QSettings(
        "Epicfrisk",
        "DDLC Mod Launcher",
    )

    create_vanilla_profile(settings)

    available_profiles = get_available_profiles()

    print("Available profiles:")

    for profile in available_profiles:
        print(f" - {profile.name}")

    window = QWidget()

    window.setWindowTitle(
        "DokDoki Launcher"
    )

    window.resize(
        500,
        350,
    )

    layout = QVBoxLayout()

    title = QLabel(
        "DokDoki Launcher"
    )

    layout.addWidget(title)

    ddlc_label = QLabel(
        "DDLC Installation"
    )

    layout.addWidget(ddlc_label)

    ddlc_status = QLabel(
        "No DDLC installation selected"
    )

    layout.addWidget(ddlc_status)

    profile_label = QLabel(
        "Profile"
    )

    layout.addWidget(profile_label)

    profile_combo = QComboBox()
    vanilla_index = -1

    for profile in available_profiles:
        profile_combo.addItem(
            get_profile_display_name(profile),
            str(profile),
        )

        if profile.name == "Vanilla":
            vanilla_index = profile_combo.count() - 1

    if vanilla_index >= 0:
        profile_combo.setCurrentIndex(
            vanilla_index
        )

    layout.addWidget(profile_combo)

    profile_info_label = QLabel()
    profile_info_label.setWordWrap(True)

    layout.addWidget(profile_info_label)

    update_profile_info(
        profile_combo,
        profile_info_label,
    )

    profile_combo.currentIndexChanged.connect(
        lambda _: update_profile_info(
            profile_combo,
            profile_info_label,
        )
    )

    saved_path = settings.value(
        "ddlc_path",
        "",
    )

    if saved_path:
        saved_ddlc_folder = Path(
            saved_path
        )

        version = detect_ddlc_version(
            saved_ddlc_folder
        )

        if version is not None:
            ddlc_status.setText(
                f"✓ Doki Doki Literature Club!\n"
                f"Version {version}\n"
                f"Ready to use"
            )

            print(
                f"Saved DDLC installation: "
                f"{saved_ddlc_folder}"
            )

        else:
            ddlc_status.setText(
                "DDLC installation needs to be selected again"
            )

    ddlc_button = QPushButton(
        "Change Installation"
    )

    ddlc_button.clicked.connect(
        lambda: locate_ddlc(
            ddlc_status,
            settings,
        )
    )

    layout.addWidget(
        ddlc_button
    )

    install_button = QPushButton(
        "Install Mod"
    )

    install_button.clicked.connect(
        lambda: install_mod(
            profile_combo
        )
    )

    layout.addWidget(
        install_button
    )

    online_mod_library_button = QPushButton(
        "Online Mod Library"
    )

    online_mod_library_button.clicked.connect(
        lambda: open_online_mod_library(
            window,
            profile_combo,
            4 * 1024 ** 3,
        )
    )

    layout.addWidget(
        online_mod_library_button
    )

    play_button = QPushButton(
        "Play"
    )

    play_button.clicked.connect(
        lambda: play_selected_profile(
            profile_combo
        )
    )

    layout.addWidget(
        play_button
    )

    edit_button = QPushButton(
        "Edit Profile"
    )

    edit_button.clicked.connect(
        lambda: edit_selected_profile(
            profile_combo,
            profile_info_label,
        )
    )

    layout.addWidget(
        edit_button
    )

    keep_saves_checkbox = QCheckBox(
        "Keep saves when removing profiles"
    )
    keep_saves_on_remove = settings.value(
        "keep_saves_on_remove",
        True,
        type=bool,
    )

    keep_saves_checkbox.setChecked(
        keep_saves_on_remove
    )

    keep_saves_checkbox.toggled.connect(
        lambda checked: settings.setValue(
            "keep_saves_on_remove",
            checked,
        )
    )

    remove_button = QPushButton(
        "Remove Profile"
    )

    remove_button.clicked.connect(
        lambda: remove_selected_profile(
            profile_combo,
            keep_saves_checkbox.isChecked(),
        )
    )

    layout.addWidget(
        remove_button
    )

    layout.addWidget(
        keep_saves_checkbox
    )

    folder_button = QPushButton(
        "Open Profile Folder"
    )

    folder_button.clicked.connect(
        lambda: open_selected_profile_folder(
            profile_combo
        )
    )

    layout.addWidget(
        folder_button
    )

    window.setLayout(layout)

    window.show()

    sys.exit(
        app.exec()
    )


if __name__ == "__main__":
    if (
        len(sys.argv) >= 2
        and sys.argv[1] == MEGA_DOWNLOAD_HELPER_FLAG
    ):
        sys.exit(
            run_mega_download_helper(
                sys.argv[2:]
            )
        )

    main()
