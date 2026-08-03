"""Lexical no-follow custody for continuous authoritative filesystem paths.

The module deliberately never uses :meth:`Path.resolve` to establish
authority.  Paths are assembled lexically beneath a caller-supplied trusted
root, every existing component is opened or inspected without following a
reparse point, and directory identities are retained while publication is in
progress.

Windows has no single ``openat(..., O_NOFOLLOW)`` equivalent for the complete
create/write/replace sequence.  CERA therefore uses a bounded fail-closed
protocol on Windows:

* ``CreateFileW(FILE_FLAG_OPEN_REPARSE_POINT)`` and
  ``GetFileInformationByHandle[Ex]`` prove component attributes and identity;
* directory handles opened without ``FILE_SHARE_DELETE`` pin the trusted root
  and parent chain against rename/replacement during publication;
* temporary files are created exclusively, identities are checked before and
  after writing, the complete chain is revalidated immediately before
  ``os.replace``, and the promoted file must retain the temporary identity.

Any unavailable identity primitive, unexpected reparse attribute, alias, or
race is an error.  There is no resolve-then-check downgrade.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import stat
from typing import ClassVar, Iterator

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256


NO_FOLLOW_CUSTODY_POLICY_VERSION = "cera.continuous_no_follow_custody_policy.v1"
NO_FOLLOW_CUSTODY_POLICY_SHA256 = canonical_sha256(
    {
        "policy_version": NO_FOLLOW_CUSTODY_POLICY_VERSION,
        "path_construction": "lexical_relative_to_trusted_root",
        "component_inspection": "component_by_component_no_follow",
        "windows_identity": (
            "CreateFileW_OPEN_REPARSE_POINT_GetFileInformationByHandleEx"
        ),
        "windows_race_protocol": (
            "locked_directory_chain_exclusive_temp_revalidate_replace_identity"
        ),
        "posix_identity": "lstat_and_open_O_NOFOLLOW_where_available",
        "reject_reparse_symlink_junction_mount": True,
        "resolve_is_not_authority": True,
    }
)


def lexical_absolute(path: Path) -> Path:
    """Return an absolute lexical path without resolving aliases."""

    if not isinstance(path, Path):
        path = Path(path)
    return Path(os.path.abspath(os.fspath(path)))


def normalized_relative_path(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ContractValidationError("no-follow relative path is empty")
    value = relative_path.replace("\\", "/")
    candidate = Path(value)
    parts = tuple(part for part in value.split("/") if part)
    if (
        candidate.is_absolute()
        or candidate.drive
        or not parts
        or any(part in {".", ".."} for part in parts)
        or value.startswith("/")
        or "//" in value
    ):
        raise ContractValidationError("no-follow relative path is not lexical")
    return "/".join(parts)


def lexical_target(trusted_root: Path, relative_path: str) -> Path:
    root = lexical_absolute(trusted_root)
    normalized = normalized_relative_path(relative_path)
    return root.joinpath(*normalized.split("/"))


@dataclass(frozen=True, slots=True)
class NoFollowPathIdentityV1:
    """Privacy-safe identity obtained from a no-follow OS object handle."""

    SCHEMA_VERSION: ClassVar[str] = "cera.no_follow_path_identity.v1"

    schema_version: str
    object_kind: str
    object_identity_sha256: str
    link_count: int
    reparse_tag: int
    no_follow_verified: bool
    verification_mode: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("no-follow identity schema changed")
        if self.object_kind not in {"directory", "file"}:
            raise ContractValidationError("no-follow object kind is invalid")
        if not re_is_sha256(self.object_identity_sha256):
            raise ContractValidationError("no-follow object identity is invalid")
        if type(self.link_count) is not int or self.link_count < 1:
            raise ContractValidationError("no-follow link count is invalid")
        if type(self.reparse_tag) is not int or self.reparse_tag < 0:
            raise ContractValidationError("no-follow reparse tag is invalid")
        if self.no_follow_verified is not True:
            raise ContractValidationError("no-follow identity is not verified")
        if self.verification_mode not in {
            "windows_open_reparse_point_handle_identity",
            "posix_lstat_open_nofollow_identity",
        }:
            raise ContractValidationError("no-follow verification mode is invalid")
        if self.reparse_tag != 0:
            raise ContractValidationError("no-follow identity is a reparse point")


@dataclass(frozen=True, slots=True)
class NoFollowTargetCustodyV1:
    """Stable parent-chain evidence for one lexical publication target."""

    SCHEMA_VERSION: ClassVar[str] = "cera.no_follow_target_custody.v1"

    schema_version: str
    path_policy_sha256: str
    trusted_root_identity_sha256: str
    verified_parent_identity_sha256: str
    lexical_relative_path: str
    verified_parent_relative_path: str
    component_manifest_sha256: str
    verification_mode: str
    all_existing_components_no_follow: bool
    all_existing_components_non_reparse: bool
    verification_generation_sha256: str
    custody_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("no-follow target custody schema changed")
        if self.path_policy_sha256 != NO_FOLLOW_CUSTODY_POLICY_SHA256:
            raise ContractValidationError("no-follow target policy changed")
        for field_name in (
            "trusted_root_identity_sha256",
            "verified_parent_identity_sha256",
            "component_manifest_sha256",
            "verification_generation_sha256",
            "custody_sha256",
        ):
            if not re_is_sha256(getattr(self, field_name)):
                raise ContractValidationError(
                    f"no-follow target {field_name} is invalid"
                )
        if normalized_relative_path(self.lexical_relative_path) != self.lexical_relative_path:
            raise ContractValidationError("no-follow target relative path changed")
        if self.verified_parent_relative_path:
            if (
                normalized_relative_path(self.verified_parent_relative_path)
                != self.verified_parent_relative_path
            ):
                raise ContractValidationError("no-follow parent path changed")
            expected_parent = self.lexical_relative_path.rsplit("/", 1)[0]
            if expected_parent != self.verified_parent_relative_path:
                raise ContractValidationError("no-follow parent binding changed")
        elif "/" in self.lexical_relative_path:
            raise ContractValidationError("no-follow root parent binding changed")
        if self.verification_mode not in {
            "windows_open_reparse_point_handle_identity",
            "posix_lstat_open_nofollow_identity",
        }:
            raise ContractValidationError("no-follow target mode is invalid")
        if (
            self.all_existing_components_no_follow is not True
            or self.all_existing_components_non_reparse is not True
        ):
            raise ContractValidationError("no-follow target component proof failed")
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "path_policy_sha256": self.path_policy_sha256,
            "trusted_root_identity_sha256": self.trusted_root_identity_sha256,
            "verified_parent_identity_sha256": self.verified_parent_identity_sha256,
            "lexical_relative_path": self.lexical_relative_path,
            "verified_parent_relative_path": self.verified_parent_relative_path,
            "component_manifest_sha256": self.component_manifest_sha256,
            "verification_mode": self.verification_mode,
            "all_existing_components_no_follow": (
                self.all_existing_components_no_follow
            ),
            "all_existing_components_non_reparse": (
                self.all_existing_components_non_reparse
            ),
            "verification_generation_sha256": self.verification_generation_sha256,
        }
        if self.custody_sha256 != canonical_sha256(payload):
            raise ContractValidationError("no-follow target custody binding changed")


@dataclass(frozen=True, slots=True)
class NoFollowLeafEvidence:
    path: Path
    status: str
    identity: NoFollowPathIdentityV1 | None


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _FILE_READ_ATTRIBUTES = 0x0080
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    class _FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _CreateFileW = _kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _GetFileInformationByHandle = _kernel32.GetFileInformationByHandle
    _GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _GetFileInformationByHandle.restype = wintypes.BOOL
    _GetFileInformationByHandleEx = _kernel32.GetFileInformationByHandleEx
    _GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _GetFileInformationByHandleEx.restype = wintypes.BOOL
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL


def _verification_mode() -> str:
    return (
        "windows_open_reparse_point_handle_identity"
        if os.name == "nt"
        else "posix_lstat_open_nofollow_identity"
    )


def _identity_payload(kind: str, raw_identity: tuple[int, ...]) -> str:
    return canonical_sha256(
        {
            "object_kind": kind,
            "raw_identity": raw_identity,
            "verification_mode": _verification_mode(),
        }
    )


def _windows_open_handle(
    path: Path,
    *,
    desired_access: int,
    share_delete: bool,
    create_new: bool = False,
) -> int:
    share = _FILE_SHARE_READ | _FILE_SHARE_WRITE
    if share_delete:
        share |= _FILE_SHARE_DELETE
    handle = _CreateFileW(
        str(path),
        desired_access,
        share,
        None,
        _CREATE_NEW if create_new else _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL
        | _FILE_FLAG_BACKUP_SEMANTICS
        | _FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error), str(path))
    return int(handle)


def _windows_identity_from_handle(handle: int) -> NoFollowPathIdentityV1:
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _GetFileInformationByHandle(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))
    tag_information = _FILE_ATTRIBUTE_TAG_INFO()
    if not _GetFileInformationByHandleEx(
        handle,
        _FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(tag_information),
        ctypes.sizeof(tag_information),
    ):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))
    attributes = int(tag_information.FileAttributes)
    reparse_tag = (
        int(tag_information.ReparseTag)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT
        else 0
    )
    kind = "directory" if attributes & _FILE_ATTRIBUTE_DIRECTORY else "file"
    identity = NoFollowPathIdentityV1(
        schema_version=NoFollowPathIdentityV1.SCHEMA_VERSION,
        object_kind=kind,
        object_identity_sha256=_identity_payload(
            kind,
            (
                int(information.dwVolumeSerialNumber),
                int(information.nFileIndexHigh),
                int(information.nFileIndexLow),
            ),
        ),
        link_count=int(information.nNumberOfLinks),
        reparse_tag=reparse_tag,
        no_follow_verified=True,
        verification_mode=_verification_mode(),
    )
    if identity.object_kind == "file" and identity.link_count != 1:
        raise StateConflictError("no-follow file has an aliased hard-link identity")
    return identity


def _close_windows_handle(handle: int) -> None:
    if not _CloseHandle(handle):
        error = ctypes.get_last_error()
        raise OSError(error, os.strerror(error))


def inspect_no_follow(path: Path, *, missing_ok: bool = False) -> NoFollowPathIdentityV1 | None:
    """Inspect exactly ``path`` without traversing a leaf alias."""

    path = lexical_absolute(path)
    if os.name == "nt":
        try:
            handle = _windows_open_handle(
                path,
                desired_access=_FILE_READ_ATTRIBUTES,
                share_delete=True,
            )
        except OSError as exc:
            if missing_ok and (
                getattr(exc, "winerror", None) in {2, 3}
                or exc.errno in {2, 3}
            ):
                return None
            raise StateConflictError(
                "no-follow path identity could not be proven"
            ) from exc
        try:
            try:
                identity = _windows_identity_from_handle(handle)
                if identity.object_kind == "file" and identity.link_count != 1:
                    raise StateConflictError(
                        "no-follow file has an aliased hard-link identity"
                    )
                return identity
            except (OSError, ContractValidationError) as exc:
                raise StateConflictError(
                    "no-follow path identity or reparse status could not be proven"
                ) from exc
        finally:
            _close_windows_handle(handle)

    try:
        value = os.lstat(path)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise StateConflictError("no-follow path component is unavailable")
    mode = value.st_mode
    if stat.S_ISLNK(mode):
        raise StateConflictError("no-follow path component is a symbolic link")
    if stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISREG(mode):
        kind = "file"
    else:
        raise StateConflictError("no-follow path component kind is unsupported")
    identity = NoFollowPathIdentityV1(
        schema_version=NoFollowPathIdentityV1.SCHEMA_VERSION,
        object_kind=kind,
        object_identity_sha256=_identity_payload(
            kind, (int(value.st_dev), int(value.st_ino))
        ),
        link_count=int(value.st_nlink),
        reparse_tag=0,
        no_follow_verified=True,
        verification_mode=_verification_mode(),
    )
    if identity.object_kind == "file" and identity.link_count != 1:
        raise StateConflictError("no-follow file has an aliased hard-link identity")
    return identity


def _parent_relative(relative_path: str) -> str:
    normalized = normalized_relative_path(relative_path)
    return normalized.rsplit("/", 1)[0] if "/" in normalized else ""


def _component_manifest(
    trusted_root: Path, parent_relative: str
) -> tuple[NoFollowPathIdentityV1, str, tuple[dict[str, object], ...]]:
    root = lexical_absolute(trusted_root)
    root_identity = inspect_no_follow(root)
    if root_identity is None or root_identity.object_kind != "directory":
        raise StateConflictError("trusted no-follow root is not a directory")
    manifest: list[dict[str, object]] = [
        {
            "relative_path": ".",
            "identity_sha256": root_identity.object_identity_sha256,
            "object_kind": root_identity.object_kind,
            "reparse_tag": root_identity.reparse_tag,
            "link_count": root_identity.link_count,
            "no_follow_verified": root_identity.no_follow_verified,
        }
    ]
    parent_identity = root_identity
    current = root
    if parent_relative:
        normalized_parent = normalized_relative_path(parent_relative)
        for index, part in enumerate(normalized_parent.split("/"), start=1):
            current = current / part
            identity = inspect_no_follow(current)
            if identity is None or identity.object_kind != "directory":
                raise StateConflictError(
                    "no-follow publication parent component is unavailable"
                )
            parent_identity = identity
            manifest.append(
                {
                    "relative_path": "/".join(
                        normalized_parent.split("/")[:index]
                    ),
                    "identity_sha256": identity.object_identity_sha256,
                    "object_kind": identity.object_kind,
                    "reparse_tag": identity.reparse_tag,
                    "link_count": identity.link_count,
                    "no_follow_verified": identity.no_follow_verified,
                }
            )
    return root_identity, parent_identity.object_identity_sha256, tuple(manifest)


def capture_target_custody(
    trusted_root: Path, relative_path: str
) -> NoFollowTargetCustodyV1:
    normalized = normalized_relative_path(relative_path)
    parent_relative = _parent_relative(normalized)
    root_identity, parent_identity_sha256, manifest = _component_manifest(
        trusted_root, parent_relative
    )
    component_manifest_sha256 = canonical_sha256(manifest)
    generation = canonical_sha256(
        {
            "trusted_root_identity_sha256": root_identity.object_identity_sha256,
            "verified_parent_identity_sha256": parent_identity_sha256,
            "lexical_relative_path": normalized,
            "component_manifest_sha256": component_manifest_sha256,
            "verification_mode": _verification_mode(),
        }
    )
    payload = {
        "schema_version": NoFollowTargetCustodyV1.SCHEMA_VERSION,
        "path_policy_sha256": NO_FOLLOW_CUSTODY_POLICY_SHA256,
        "trusted_root_identity_sha256": root_identity.object_identity_sha256,
        "verified_parent_identity_sha256": parent_identity_sha256,
        "lexical_relative_path": normalized,
        "verified_parent_relative_path": parent_relative,
        "component_manifest_sha256": component_manifest_sha256,
        "verification_mode": _verification_mode(),
        "all_existing_components_no_follow": True,
        "all_existing_components_non_reparse": True,
        "verification_generation_sha256": generation,
    }
    return NoFollowTargetCustodyV1(
        **payload,
        custody_sha256=canonical_sha256(payload),
    )


def verify_target_custody(
    trusted_root: Path, expected: NoFollowTargetCustodyV1
) -> None:
    if capture_target_custody(trusted_root, expected.lexical_relative_path) != expected:
        raise StateConflictError("no-follow publication parent identity changed")


def inspect_leaf(
    trusted_root: Path,
    relative_path: str,
    *,
    expected_kind: str = "file",
) -> NoFollowLeafEvidence:
    custody = capture_target_custody(trusted_root, relative_path)
    path = lexical_target(trusted_root, custody.lexical_relative_path)
    identity = inspect_no_follow(path, missing_ok=True)
    if identity is None:
        return NoFollowLeafEvidence(path=path, status="missing", identity=None)
    if identity.object_kind != expected_kind:
        raise StateConflictError("no-follow publication leaf kind changed")
    return NoFollowLeafEvidence(
        path=path,
        status=f"ordinary_{expected_kind}",
        identity=identity,
    )


def _open_locked_directory(path: Path, expected: NoFollowPathIdentityV1):
    if os.name == "nt":
        handle = None
        try:
            handle = _windows_open_handle(
                path,
                desired_access=_FILE_READ_ATTRIBUTES,
                share_delete=False,
            )
            actual = _windows_identity_from_handle(handle)
        except (OSError, ContractValidationError) as exc:
            if handle is not None:
                _close_windows_handle(handle)
            raise StateConflictError(
                "no-follow directory lock could not be established"
            ) from exc
        except StateConflictError:
            if handle is not None:
                _close_windows_handle(handle)
            raise
        if actual != expected or actual.object_kind != "directory":
            _close_windows_handle(handle)
            raise StateConflictError("no-follow directory changed while locking")
        return handle
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        value = os.fstat(descriptor)
    except OSError as exc:
        raise StateConflictError(
            "no-follow directory lock could not be established"
        ) from exc
    actual = _identity_payload("directory", (int(value.st_dev), int(value.st_ino)))
    if actual != expected.object_identity_sha256:
        os.close(descriptor)
        raise StateConflictError("no-follow directory changed while locking")
    return descriptor


def _close_locked_directory(handle) -> None:
    if os.name == "nt":
        _close_windows_handle(handle)
    else:
        os.close(handle)


@contextmanager
def locked_directory_chain(
    trusted_root: Path,
    parent_relative_path: str,
    *,
    create_missing: bool,
) -> Iterator[tuple[NoFollowPathIdentityV1, ...]]:
    """Lock root through parent, optionally creating missing directories safely."""

    root = lexical_absolute(trusted_root)
    parts = (
        normalized_relative_path(parent_relative_path).split("/")
        if parent_relative_path
        else []
    )
    handles: list[object] = []
    identities: list[NoFollowPathIdentityV1] = []
    current = root
    try:
        root_identity = inspect_no_follow(root)
        if root_identity is None or root_identity.object_kind != "directory":
            raise StateConflictError("trusted no-follow root is unavailable")
        handles.append(_open_locked_directory(root, root_identity))
        identities.append(root_identity)
        for part in parts:
            current = current / part
            identity = inspect_no_follow(current, missing_ok=True)
            if identity is None:
                if not create_missing:
                    raise StateConflictError(
                        "no-follow publication parent component is unavailable"
                    )
                try:
                    os.mkdir(current)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise StateConflictError(
                        "no-follow publication parent could not be created"
                    ) from exc
                identity = inspect_no_follow(current)
            if identity is None or identity.object_kind != "directory":
                raise StateConflictError(
                    "no-follow publication parent component is unsafe"
                )
            handles.append(_open_locked_directory(current, identity))
            identities.append(identity)
        yield tuple(identities)
    finally:
        for handle in reversed(handles):
            _close_locked_directory(handle)


def ensure_parent_chain(trusted_root: Path, relative_path: str) -> None:
    with locked_directory_chain(
        trusted_root,
        _parent_relative(relative_path),
        create_missing=True,
    ):
        pass


def safe_read_bytes(
    trusted_root: Path, relative_path: str
) -> tuple[bytes, NoFollowPathIdentityV1]:
    evidence = inspect_leaf(trusted_root, relative_path)
    if evidence.identity is None:
        raise StateConflictError("no-follow file is unavailable")
    path = evidence.path
    if os.name == "nt":
        import msvcrt

        handle = None
        try:
            handle = _windows_open_handle(
                path,
                desired_access=_GENERIC_READ | _FILE_READ_ATTRIBUTES,
                share_delete=True,
            )
            identity = _windows_identity_from_handle(handle)
            if identity != evidence.identity or identity.object_kind != "file":
                _close_windows_handle(handle)
                handle = None
                raise StateConflictError("no-follow file changed before reading")
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            handle = None
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read()
            return data, identity
        except StateConflictError:
            if handle is not None:
                _close_windows_handle(handle)
            raise
        except OSError as exc:
            if handle is not None:
                _close_windows_handle(handle)
            raise StateConflictError("no-follow file read failed closed") from exc
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            value = os.fstat(stream.fileno())
            identity = NoFollowPathIdentityV1(
                schema_version=NoFollowPathIdentityV1.SCHEMA_VERSION,
                object_kind="file",
                object_identity_sha256=_identity_payload(
                    "file", (int(value.st_dev), int(value.st_ino))
                ),
                link_count=int(value.st_nlink),
                reparse_tag=0,
                no_follow_verified=True,
                verification_mode=_verification_mode(),
            )
            if identity != evidence.identity:
                raise StateConflictError("no-follow file changed before reading")
            return stream.read(), identity
    except StateConflictError:
        raise
    except OSError as exc:
        raise StateConflictError("no-follow file read failed closed") from exc


def safe_create_new_bytes(
    trusted_root: Path,
    relative_path: str,
    data: bytes,
) -> NoFollowPathIdentityV1:
    """Create one ordinary file exclusively and return its verified identity."""

    if not isinstance(data, bytes):
        raise ContractValidationError("no-follow publication bytes are invalid")
    path = lexical_target(trusted_root, relative_path)
    if inspect_leaf(trusted_root, relative_path).status != "missing":
        raise StateConflictError("no-follow temporary path is occupied")
    if os.name == "nt":
        import msvcrt

        handle = None
        try:
            handle = _windows_open_handle(
                path,
                desired_access=_GENERIC_WRITE | _FILE_READ_ATTRIBUTES,
                share_delete=True,
                create_new=True,
            )
            identity = _windows_identity_from_handle(handle)
            if identity.object_kind != "file":
                _close_windows_handle(handle)
                handle = None
                raise StateConflictError("no-follow created object is not a file")
            descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            handle = None
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            if handle is not None:
                _close_windows_handle(handle)
            raise StateConflictError("no-follow temporary path is occupied") from exc
        except StateConflictError:
            if handle is not None:
                _close_windows_handle(handle)
            raise
        except OSError as exc:
            if handle is not None:
                _close_windows_handle(handle)
            if getattr(exc, "winerror", None) in {80, 183}:
                raise StateConflictError("no-follow temporary path is occupied") from exc
            raise StateConflictError("no-follow exclusive file creation failed") from exc
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise StateConflictError("no-follow temporary path is occupied") from exc
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EMLINK}:
                raise StateConflictError("no-follow temporary path is an alias") from exc
            raise StateConflictError("no-follow exclusive file creation failed") from exc
    created = inspect_leaf(trusted_root, relative_path)
    if created.identity is None:
        raise StateConflictError("no-follow created file identity was lost")
    return created.identity


def safe_replace(
    trusted_root: Path,
    *,
    temporary_relative_path: str,
    final_relative_path: str,
    expected_temporary_identity: NoFollowPathIdentityV1,
) -> NoFollowPathIdentityV1:
    """Revalidate and atomically replace, retaining exact temp-file identity."""

    temporary = inspect_leaf(trusted_root, temporary_relative_path)
    final = inspect_leaf(trusted_root, final_relative_path)
    if temporary.identity != expected_temporary_identity:
        raise StateConflictError("no-follow temporary file identity changed")
    if final.identity is not None and final.identity.object_kind != "file":
        raise StateConflictError("no-follow final path is not an ordinary file")
    os.replace(temporary.path, final.path)
    promoted = inspect_leaf(trusted_root, final_relative_path)
    remaining_temporary = inspect_leaf(trusted_root, temporary_relative_path)
    if (
        promoted.identity != expected_temporary_identity
        or remaining_temporary.identity is not None
    ):
        raise StateConflictError("no-follow atomic replacement identity changed")
    return expected_temporary_identity


def safe_replace_directory(
    trusted_root: Path,
    *,
    source_relative_path: str,
    target_relative_path: str,
    expected_source_identity: NoFollowPathIdentityV1,
    expected_target_identity: NoFollowPathIdentityV1 | None = None,
) -> NoFollowPathIdentityV1:
    """Move one ordinary directory and prove object identity across the rename."""

    source = inspect_leaf(
        trusted_root, source_relative_path, expected_kind="directory"
    )
    target = inspect_leaf(
        trusted_root, target_relative_path, expected_kind="directory"
    )
    if source.identity != expected_source_identity:
        raise StateConflictError("no-follow source directory identity changed")
    if target.identity != expected_target_identity:
        raise StateConflictError("no-follow target directory identity changed")
    os.replace(source.path, target.path)
    promoted = inspect_leaf(
        trusted_root, target_relative_path, expected_kind="directory"
    )
    remaining = inspect_leaf(
        trusted_root, source_relative_path, expected_kind="directory"
    )
    if promoted.identity != expected_source_identity or remaining.identity is not None:
        raise StateConflictError("no-follow directory replacement identity changed")
    return expected_source_identity


def unlink_if_identity(
    trusted_root: Path,
    relative_path: str,
    expected_identity: NoFollowPathIdentityV1,
) -> None:
    try:
        leaf = inspect_leaf(trusted_root, relative_path)
    except StateConflictError:
        return
    if leaf.identity == expected_identity:
        try:
            leaf.path.unlink()
        except OSError:
            pass


def validate_tree_no_follow(
    trusted_root: Path, relative_directory: str
) -> str:
    """Reject every alias/reparse/mount below a lexical directory tree.

    The returned manifest hash is identity-only evidence used for race
    detection.  Content authority remains with the caller's typed manifests.
    """

    normalized = normalized_relative_path(relative_directory)
    root = lexical_absolute(trusted_root)
    starting = inspect_leaf(root, normalized, expected_kind="directory")
    if starting.identity is None:
        raise StateConflictError("no-follow tree root is unavailable")
    manifest: list[dict[str, object]] = []

    def visit(directory_relative: str) -> None:
        directory_path = lexical_target(root, directory_relative)
        with locked_directory_chain(
            root, directory_relative, create_missing=False
        ):
            try:
                with os.scandir(directory_path) as iterator:
                    entries = sorted(
                        iterator, key=lambda value: value.name.casefold()
                    )
            except OSError as exc:
                raise StateConflictError(
                    "no-follow tree directory could not be enumerated"
                ) from exc
            for entry in entries:
                child_relative = directory_relative + "/" + entry.name
                identity = inspect_no_follow(Path(entry.path))
                if identity is None:
                    raise StateConflictError(
                        "no-follow tree component identity was lost"
                    )
                if os.name != "nt" and identity.object_kind == "directory":
                    child_stat = os.lstat(entry.path)
                    parent_stat = os.lstat(directory_path)
                    if child_stat.st_dev != parent_stat.st_dev or os.path.ismount(
                        entry.path
                    ):
                        raise StateConflictError(
                            "no-follow tree component is a mount-point alias"
                        )
                manifest.append(
                    {
                        "relative_path": child_relative,
                        "object_kind": identity.object_kind,
                        "object_identity_sha256": identity.object_identity_sha256,
                            "reparse_tag": identity.reparse_tag,
                            "link_count": identity.link_count,
                        "no_follow_verified": identity.no_follow_verified,
                    }
                )
                if identity.object_kind == "directory":
                    visit(child_relative)

    visit(normalized)
    return canonical_sha256(tuple(manifest))
