#!/usr/bin/env python3
"""Replace the test Appliance using a built revision and Compose resource values.

Existing authentication, ports, GPU configuration and volumes remain in memory
and are passed over the local Docker socket, never printed or put in argv.
"""
from __future__ import annotations

import argparse
import hashlib
from contextlib import closing
from datetime import datetime, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
from urllib.parse import quote


PRODUCT_PORT = "5800/tcp"
BACKUP_MEDIA_POLICY = Path('/etc/infoos/backup-media-policy.json')
ARCHIVE_ENV_KEYS = (
    "INFOOS_TASK_EVENT_HOT_WINDOW_DAYS",
    "INFOOS_TASK_EVENT_CRITICAL_HOT_WINDOW_DAYS",
    "INFOOS_TASK_EVENT_ARCHIVE_BATCH_SIZE",
    "INFOOS_TASK_EVENT_ARCHIVE_MAX_ROWS",
    "INFOOS_TASK_EVENT_ARCHIVE_MAX_SECONDS",
)
PRIVATE_BIND_NETWORKS = tuple(ipaddress.IPv4Network(cidr) for cidr in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


ENV_OVERRIDE_KEYS = frozenset({
    "INFOOS_PUBLIC_BASE_URL", "INFOOS_EDGE_READER_SECRET",
})


def read_env_overrides(path: Path | None) -> dict[str, str]:
    """Read a private operator file without putting its contents in diagnostics."""
    if path is None:
        return {}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("Environment override file must be a regular file")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise ValueError("Environment override file permissions must be 0600")
            if metadata.st_uid not in {0, os.getuid()}:
                raise ValueError("Environment override file owner must be root or current user")
            overrides = {}
            for number, line in enumerate(stream, 1):
                line = line.rstrip("\r\n")
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                    raise ValueError(f"Invalid environment override syntax at line {number}")
                if key not in ENV_OVERRIDE_KEYS:
                    raise ValueError(f"Environment override key is not allowed: {key}")
                if key in overrides:
                    raise ValueError(f"Duplicate environment override key: {key}")
                if "\x00" in value:
                    raise ValueError(f"Invalid environment override value at line {number}")
                overrides[key] = value
            return overrides
    except (OSError, UnicodeError):
        raise ValueError("Cannot read private environment override file") from None


def preview_env_overrides(overrides: dict[str, str]) -> dict[str, str]:
    return {key: "<redacted>" if key == "INFOOS_EDGE_READER_SECRET" else value
            for key, value in overrides.items()}


def rewrite_port_bindings(port_bindings: dict, addresses: list[str] | None) -> dict:
    """Return the existing bindings, or bind the product port to approved IPv4 hosts."""
    if not addresses:
        return port_bindings
    seen = set()
    for address in addresses:
        try:
            ip = ipaddress.IPv4Address(address)
        except ipaddress.AddressValueError as exc:
            raise ValueError(f"Invalid IPv4 bind address: {address}") from exc
        if str(ip) != address or not (
            address == "127.0.0.1" or any(ip in network for network in PRIVATE_BIND_NETWORKS)
        ):
            raise ValueError(f"Bind address must be 127.0.0.1 or RFC1918 IPv4: {address}")
        if address in seen:
            raise ValueError(f"Duplicate bind address: {address}")
        seen.add(address)
    if set(port_bindings) != {PRODUCT_PORT}:
        raise ValueError("Only the product port 5800/tcp may be published")
    existing = port_bindings[PRODUCT_PORT]
    ports = {binding["HostPort"] for binding in existing}
    if len(ports) != 1 or not next(iter(ports)):
        raise ValueError("Existing product port must have one HostPort")
    host_port = next(iter(ports))
    return {PRODUCT_PORT: [{"HostIp": address, "HostPort": host_port} for address in addresses]}


def docker_json(*args):
    return json.loads(subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout)


# Base-image settings that decide which nginx include files get rendered.
NGINX_ENV_KEYS = ("USER_ID", "GROUP_ID", "SECURE_CONNECTION", "SECURE_CONNECTION_VNC_METHOD",
                  "WEB_AUTHENTICATION", "WEB_LISTENING_PORT", "VNC_LISTENING_PORT", "WEB_LOCALHOST_ONLY",
                  "WEB_AUDIO", "WEB_FILE_MANAGER", "WEB_NOTIFICATION", "WEB_TERMINAL")
NGINX_PREFLIGHT = (
    "export PATH=/opt/base/bin:/opt/base/sbin:$PATH; "
    "for s in 09-var-dirs.sh 10-certs.sh 10-nginx.sh; do sh /etc/cont-init.d/$s >/dev/null 2>&1 || exit 97; done; "
    "nginx -t -c /opt/base/etc/nginx/nginx.conf"
)


def nginx_preflight(image: str, environment: dict[str, str]) -> None:
    """Render the base image's nginx config in a throwaway container and run ``nginx -t``.

    Static tests cannot see directives this nginx build rejects, and a config
    error only shows up as a crash loop after the old container is stopped.
    """
    command = ["docker", "run", "--rm", "--network", "none"]
    for key in NGINX_ENV_KEYS:
        if key in environment:
            command += ["-e", f"{key}={environment[key]}"]
    result = subprocess.run([*command, "--entrypoint", "sh", image, "-c", NGINX_PREFLIGHT],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"nginx preflight failed (exit {result.returncode}); nothing was changed.\n"
                         f"{(result.stderr or result.stdout).strip()[-2000:]}")


def data_mount_from_inspect(details: dict) -> Path:
    """Resolve the Appliance data mount from Docker inspect metadata."""
    mounts = [mount for mount in details["Mounts"] if mount["Destination"] == "/var/lib/infoos/data"]
    if len(mounts) != 1 or mounts[0]["Type"] not in {"volume", "bind"}:
        raise ValueError("Appliance must have one data volume or bind mount")
    return Path(mounts[0]["Source"])


def container_data_mount(container: str) -> tuple[Path, str]:
    """Resolve the running Appliance's data mount without reading its environment."""
    details = docker_json("inspect", container)[0]
    revision = (details.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision")
    return data_mount_from_inspect(details), revision or "unknown"


def apply_archive_env_overrides(existing: dict[str, str], configured: dict[str, str], supplied: dict[str, str]) -> None:
    """Keep deployed values unless the operator supplied an archive override."""
    for key in ARCHIVE_ENV_KEYS:
        if key in supplied:
            existing[key] = str(configured[key])


def latest_linked_backup(root: Path, volumes: dict[str, str | None]) -> Path | None:
    if not root.is_dir():
        return None
    for candidate in sorted(root.iterdir(), reverse=True):
        receipt = candidate / "receipt.json"
        if candidate.name.startswith(".pending-") or candidate.is_symlink() or not receipt.is_file():
            continue
        try:
            metadata = json.loads(receipt.read_text())
        except (OSError, ValueError):
            continue
        if (
            metadata.get("backup_format") in {"linked-files-v1", "linked-files-v2", "linked-files-selective-v1", "selective-media-pruned-v1"}
            and metadata.get("volumes") == volumes
            and all((candidate / name).is_dir() for name in ("assets", "state"))
            and (metadata.get("backup_format") in {"linked-files-v1", "selective-media-pruned-v1"}
                 or (candidate / "archive").is_dir())
            and (candidate / "infoos.sqlite3").is_file()
        ):
            return candidate
    return None


def write_sqlite_recovery_point(database: Path, destination: Path, receipt: dict) -> Path:
    """Write a zstd-compressed online SQLite backup plus receipt into a 0700 directory."""
    pending = destination.with_name(f".pending-{destination.name}")
    if destination.exists() or pending.exists():
        raise SystemExit("backup destination already exists")
    pending.mkdir(parents=True, mode=0o700)
    os.chmod(pending, 0o700)
    try:
        copy = pending / "infoos.sqlite3"
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(copy)) as target:
                source.backup(target)
                target.execute("PRAGMA journal_mode=DELETE")
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("SQLite recovery point failed quick_check")
        for suffix in ("-wal", "-shm"):
            copy.with_name(copy.name + suffix).unlink(missing_ok=True)
        subprocess.run(["zstd", "-q", "-T0", "-3", "--rm", str(copy)], check=True)
        (pending / "receipt.json").write_text(json.dumps(receipt, indent=2))
        pending.rename(destination)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise
    return destination


def validate_media_exclusions(files: object) -> dict:
    """Validate policy/manifest JSON at the file boundary; paths are rsync literals."""
    if not isinstance(files, dict):
        raise ValueError("Invalid approved media entries")
    for relative, approved in files.items():
        if (not isinstance(relative, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', relative)
                or Path(relative).is_absolute() or '..' in Path(relative).parts
                or Path(relative).as_posix() != relative
                or Path(relative).suffix.lower() not in {'.mp4', '.webm', '.mov', '.mkv', '.m4v'}):
            raise ValueError("Invalid approved media path")
        if (not isinstance(approved, dict) or type(approved.get('size')) is not int or approved['size'] < 0
                or any(not isinstance(approved.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', approved[key])
                       for key in ('sha256', 'transcript_sha256'))):
            raise ValueError("Invalid approved media evidence")
    return files


def backup_media_exclusions(data: Path, *, database: Path | None = None,
                            policy_path: Path | None = None, cached: dict | None = None) -> dict:
    """Omit only approved video versions whose transcript is in the backed-up DB."""
    policy_path = policy_path or BACKUP_MEDIA_POLICY
    if not policy_path.exists():
        return {}
    policy = json.loads(policy_path.read_text())
    if not isinstance(policy, dict) or policy.get('format') != 'infoos-speech-media-exclusions-v1':
        raise ValueError('Unrecognized backup media exclusion policy')
    files = validate_media_exclusions(policy.get('files'))
    result = {}
    with closing(sqlite3.connect(f"file:{database or data / 'infoos.sqlite3'}?mode=ro", uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON')
        for relative, approved in files.items():
            source = data / 'assets' / relative
            if not source.is_file() or source.is_symlink() or source.resolve() != source.absolute():
                continue
            facts = source.stat()
            fingerprint = [facts.st_dev, facts.st_ino, facts.st_size, facts.st_mtime_ns, facts.st_ctime_ns]
            if facts.st_size != approved['size']:
                continue
            rows = conn.execute('''select b.body from card_assets a join card_content_blocks b
                on b.card_id=a.card_id and b.source_url='asset:'||a.asset_id
                where a.local_path in (?,?) and a.asset_kind='video' and b.block_kind='video_transcript'
                and b.status in ('enriched','preserved','done')''',
                (relative, '/var/lib/infoos/data/assets/' + relative))
            if not any(body.strip() and hashlib.sha256(body.encode()).hexdigest() == approved['transcript_sha256']
                       for (body,) in rows):
                continue
            old = (cached or {}).get(relative)
            if old and old['fingerprint'] == fingerprint and old['sha256'] == approved['sha256']:
                checksum = old['sha256']
            else:
                checksum = hashlib.sha256()
                with source.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        checksum.update(block)
                checksum = checksum.hexdigest()
            after = source.stat()
            if fingerprint != [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns]:
                continue
            if checksum == approved['sha256']:
                result[relative] = {**approved, 'fingerprint': fingerprint}
    return result


def sync_snapshot(source: Path, destination: Path, previous: Path | None,
                  *, media_exclusions: dict | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    command = ["rsync", "-aHAX", "--numeric-ids", "--delete"]
    if previous is not None:
        command.append(f"--link-dest={previous}")
    with tempfile.NamedTemporaryFile(mode='w') as exclusions:
        if media_exclusions:
            exclusions.write(''.join('/' + relative + '\n' for relative in sorted(media_exclusions)))
            exclusions.flush()
            command.extend(['--delete-excluded', '--exclude-from=' + exclusions.name])
        subprocess.run([*command, f"{source}/", f"{destination}/"], check=True)


def sync_optional_archive(source: Path, destination: Path, previous: Path | None) -> None:
    """Include the archive tree even when upgrading a volume without one."""
    if source.is_dir():
        sync_snapshot(source, destination, previous if previous and previous.is_dir() else None)
    else:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True, mode=0o700)


class DockerConnection(http.client.HTTPConnection):
    def __init__(self):
        super().__init__("localhost", timeout=240)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="infoos-acceptance")
    parser.add_argument("--environment", required=True, choices=["test"])
    parser.add_argument("--image", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--update-kind",
        required=True,
        choices=("code-only", "data-migration"),
        help="code-only reuses the existing volumes; data-migration takes a consistent volume backup",
    )
    parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/infoos"))
    parser.add_argument("--bind-address", action="append", dest="bind_addresses")
    parser.add_argument("--env-override-file", type=Path, help="0600 KEY=VALUE file; only public URL and reader secret")
    parser.add_argument("--drop-env", action="append", default=[],
                        choices=("WEB_AUTHENTICATION_USERNAME", "WEB_AUTHENTICATION_PASSWORD"),
                        help="Remove a bootstrap variable from the replacement container; repeatable")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        env_overrides = read_env_overrides(args.env_override_file)
    except ValueError as exc:
        parser.error(str(exc))
    old = docker_json("inspect", args.container)[0]
    image = docker_json("image", "inspect", args.image)[0]
    if image["Config"]["Labels"].get("org.opencontainers.image.revision") != args.revision:
        raise SystemExit("Image revision differs from the approved source revision")
    compose_env = dict(os.environ, INFOOS_ADMIN_USERNAME="preserved", INFOOS_ADMIN_PASSWORD="preserved",
                       INFOOS_SOURCE_COMMIT=args.revision, INFOOS_IMAGE=args.image)
    compose = json.loads(subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "-f", str(Path(__file__).with_name("compose.yaml")), "config", "--format", "json"],
        env=compose_env, capture_output=True, text=True, check=True,
    ).stdout)["services"]["infoos"]
    seconds = int(sum(float(value) * {"h":3600,"m":60,"s":1}[unit]
                      for value, unit in re.findall(r"([0-9.]+)(h|m|s)", compose["stop_grace_period"])))
    host_config = dict(old["HostConfig"])
    old_port_bindings = host_config.get("PortBindings")
    try:
        if args.bind_addresses:
            host_config["PortBindings"] = rewrite_port_bindings(old_port_bindings or {}, args.bind_addresses)
    except ValueError as exc:
        parser.error(str(exc))
    host_config["Binds"] = None
    host_config["Mounts"] = [
        {"Type":mount["Type"], "Source":mount.get("Name") or mount["Source"],
         "Target":mount["Destination"], "ReadOnly":not mount["RW"]}
        for mount in old["Mounts"] if mount["Type"] in {"volume","bind"}
    ]
    for field, name in (("Memory","mem_limit"),("MemoryReservation","mem_reservation"),
                        ("MemorySwap","memswap_limit"),("ShmSize","shm_size")):
        host_config[field] = int(compose[name])
    host_config["LogConfig"] = {"Type":compose["logging"]["driver"],"Config":compose["logging"]["options"]}
    config = {key:old["Config"][key] for key in ("Env","User","WorkingDir","Cmd","Entrypoint","ExposedPorts") if key in old["Config"]}
    labels = dict(old["Config"].get("Labels") or {})
    labels.update(image["Config"].get("Labels") or {})
    config.update(Image=args.image, HostConfig=host_config, StopTimeout=seconds, Labels=labels)
    environment = dict(item.split("=",1) for item in config.get("Env") or [])
    for key in ("SERVICES_GRACETIME","INFOOS_MEMORY_SOFT_LIMIT_MIB","INFOOS_RESOURCE_MIN_AVAILABLE_MIB"):
        environment[key] = str(compose["environment"][key])
    apply_archive_env_overrides(environment, compose["environment"], os.environ)
    environment.update(env_overrides)
    dropped_env = set(args.drop_env) | ({"INFOOS_CLOUD_AI_WORKERS", "INFOOS_LLM_CONFIG"} & environment.keys())
    for key in dropped_env:
        environment.pop(key, None)
    config["Env"] = [f"{key}={value}" for key,value in environment.items()]
    nginx_preflight(args.image, environment)
    mounts = {m["Destination"]:m for m in old["Mounts"]}
    data = data_mount_from_inspect(old)
    state = Path(mounts["/var/lib/infoos/state"]["Source"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = (
        args.backup_root / f"{stamp}-{args.revision[:12]}"
        if args.update_kind == "data-migration"
        else None
    )
    sqlite_recovery = (
        args.backup_root / f"{stamp}-{args.revision[:12]}-sqlite"
        if args.update_kind == "code-only"
        else None
    )
    pending_backup = args.backup_root / f".pending-{stamp}-{args.revision[:12]}" if backup else None
    rollback = f"{args.container}-rollback-{stamp}"
    volumes = {key:mounts[key].get("Name") for key in ("/var/lib/infoos/data","/var/lib/infoos/state")}
    previous_backup = latest_linked_backup(args.backup_root, volumes) if backup else None
    media_exclusions = backup_media_exclusions(data) if backup else {}
    plan = {"environment":args.environment,"container":args.container,"revision":args.revision,
            "image_id":image["Id"],"memory":host_config["Memory"],"stop_timeout":seconds,
            "logging":host_config["LogConfig"],"update_kind":args.update_kind,
            "backup":str(backup) if backup is not None else None,"rollback_container":rollback,
            "backup_format":("linked-files-selective-v1" if media_exclusions else "linked-files-v2") if backup else None,
            "excluded_media_count":len(media_exclusions),
            "sqlite_recovery":str(sqlite_recovery) if sqlite_recovery else None,
            "port_bindings":{"old":old_port_bindings,"new":host_config.get("PortBindings")},
            "previous_backup":str(previous_backup) if previous_backup else None,
            "volumes":volumes, "env_overrides":preview_env_overrides(env_overrides),
            "env_removed":sorted(dropped_env), "nginx_preflight":"ok"}
    print(json.dumps(plan),flush=True)
    if not args.apply:
        return
    if sqlite_recovery is not None:
        # Code-only updates reuse the live volumes; keep a compact online SQLite restore point.
        write_sqlite_recovery_point(data / "infoos.sqlite3", sqlite_recovery,
                                    dict(plan, backup_format="sqlite-zstd-v1"))
        print(json.dumps({"sqlite_recovery_complete":str(sqlite_recovery)}),flush=True)
    if backup is not None:
        if backup.exists() or pending_backup.exists():
            raise SystemExit("backup destination already exists")
        pending_backup.mkdir(parents=True, mode=0o700)
        os.chmod(pending_backup,0o700)
        try:
            # Copy most bytes while the old container is still serving traffic.
            sync_snapshot(data / "assets", pending_backup / "assets", previous_backup / "assets" if previous_backup else None, media_exclusions=media_exclusions)
            sync_optional_archive(data / "archive", pending_backup / "archive", previous_backup / "archive" if previous_backup else None)
            sync_snapshot(state, pending_backup / "state", previous_backup / "state" if previous_backup else None)
        except Exception:
            shutil.rmtree(pending_backup)
            raise
    try:
        subprocess.run(["docker","stop","--time",str(seconds),args.container],check=True,stdout=subprocess.DEVNULL)
    except Exception:
        if pending_backup is not None:
            shutil.rmtree(pending_backup)
        raise
    print("old_container_stopped",flush=True)
    renamed = created = False
    try:
        if backup is not None:
            # The final pass picks up changes made during the live pre-copy.
            with closing(sqlite3.connect(f"file:{data / 'infoos.sqlite3'}?mode=ro",uri=True)) as source:
                with closing(sqlite3.connect(pending_backup / "infoos.sqlite3")) as target:
                    source.backup(target)
            media_exclusions = backup_media_exclusions(data, database=pending_backup / "infoos.sqlite3",
                                                       cached=media_exclusions)
            plan.update(backup_format="linked-files-selective-v1" if media_exclusions else "linked-files-v2",
                        excluded_media_count=len(media_exclusions))
            sync_snapshot(data / "assets", pending_backup / "assets", previous_backup / "assets" if previous_backup else None, media_exclusions=media_exclusions)
            sync_optional_archive(data / "archive", pending_backup / "archive", previous_backup / "archive" if previous_backup else None)
            sync_snapshot(state, pending_backup / "state", previous_backup / "state" if previous_backup else None)
            if media_exclusions:
                (pending_backup / 'excluded-media.json').write_text(json.dumps(media_exclusions, indent=2))
            (pending_backup / "receipt.json").write_text(json.dumps(plan,indent=2))
            pending_backup.rename(backup)
            print("consistent_backup_complete",flush=True)
        else:
            print("code_only_update; existing volumes reused with a SQLite-only recovery point",flush=True)
        subprocess.run(["docker","rename",args.container,rollback],check=True)
        renamed = True
        version = subprocess.run(["docker","version","--format","{{.Server.APIVersion}}"],
                                 capture_output=True,text=True,check=True).stdout.strip()
        connection = DockerConnection()
        connection.request("POST",f"/v{version}/containers/create?name={quote(args.container)}",
                           body=json.dumps(config),headers={"Content-Type":"application/json"})
        response = connection.getresponse();payload=response.read();connection.close()
        if response.status != 201:
            raise RuntimeError(f"Docker create failed with status {response.status}")
        created = True
        subprocess.run(["docker","start",args.container],check=True,stdout=subprocess.DEVNULL)
        print(json.dumps({"started":args.container,"rollback":rollback,
                          "backup":str(backup) if backup is not None else None}),flush=True)
    except Exception:
        if created:
            subprocess.run(["docker","stop","--time",str(seconds),args.container],stdout=subprocess.DEVNULL,check=False)
            subprocess.run(["docker","rename",args.container,f"{args.container}-failed-{stamp}"],check=True)
        if renamed:
            subprocess.run(["docker","rename",rollback,args.container],check=True)
        subprocess.run(["docker","start",args.container],check=True,stdout=subprocess.DEVNULL)
        if pending_backup is not None and pending_backup.exists():
            shutil.rmtree(pending_backup, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
