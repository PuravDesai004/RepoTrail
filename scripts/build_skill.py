"""Build/check the explicit portable skill ZIP and its bundled core runtime wheel."""

import argparse
import hashlib
import io
import json
import shutil
import tomllib
import zipfile
from email import message_from_bytes
from pathlib import Path

SKILL_FILES = [
    "SKILL.md",
    "README.md",
    "install.py",
    "LICENSE",
    "agents/openai.yaml",
    "references/commands.md",
    "references/report.md",
    "scripts/repotrail.py",
    "scripts/bundle_support.py",
    "runtime/requirements-core.txt",
    "runtime/manifest.json",
]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def zip_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    return output.getvalue()


def check_wheel(project, wheel, version):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) and archive.testzip() is None
        expected = {
            "repotrail/" + path.name: path.read_bytes()
            for path in (project / "src/repotrail").glob("*.py")
        }
        actual = {name for name in names if name.startswith("repotrail/") and name.endswith(".py")}
        assert actual == set(expected), "Bundled wheel modules differ from source."
        for name, raw in expected.items():
            assert archive.read(name) == raw, f"Rebuild wheel: {name} changed."
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = message_from_bytes(archive.read(metadata_name))
        assert metadata["Name"] == "repotrail" and metadata["Version"] == version
        assert (
            metadata.get_payload(decode=True).decode("utf-8").replace("\r\n", "\n").strip()
            == (project / "README.md").read_text(encoding="utf-8").strip()
        ), "Rebuild wheel after README changes."


def build(project, wheel):
    version = tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"]
    check_wheel(project, wheel, version)
    skill = project / "skills/repotrail"
    shutil.copyfile(project / "LICENSE", skill / "LICENSE")
    name = f"repotrail-{version}-py3-none-any.whl"
    # Zip metadata is normalized; RECORD/content bytes remain the original wheel bytes.
    with zipfile.ZipFile(wheel) as archive:
        runtime = zip_bytes({entry: archive.read(entry) for entry in archive.namelist()})
    (skill / "runtime" / name).write_bytes(runtime)
    (skill / "runtime/manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "wheel": name,
                "sha256": digest(runtime),
                "python": "3.14",
                "default_mode": "current_agent_model",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    entries = {path: (skill / path).read_bytes() for path in [*SKILL_FILES, "runtime/" + name]}
    manifest = {
        "schema_version": 1,
        "name": "repotrail",
        "version": version,
        "files": [{"path": path, "sha256": digest(raw)} for path, raw in sorted(entries.items())],
    }
    manifest_raw = (json.dumps(manifest, indent=2) + "\n").encode()
    (skill / "BUNDLE_MANIFEST.json").write_bytes(manifest_raw)
    entries["BUNDLE_MANIFEST.json"] = manifest_raw
    destination = project / "downloads/repotrail-skill.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(zip_bytes({"repotrail/" + path: raw for path, raw in entries.items()}))
    destination.with_suffix(".zip.sha256").write_text(
        digest(destination.read_bytes()) + "  repotrail-skill.zip\n", encoding="utf-8", newline="\n"
    )
    check(project)


def check(project):
    skill = project / "skills/repotrail"
    manifest = json.loads((skill / "BUNDLE_MANIFEST.json").read_text())
    version = tomllib.loads((project / "pyproject.toml").read_text())["project"]["version"]
    wheel_name = f"repotrail-{version}-py3-none-any.whl"
    expected = set(SKILL_FILES) | {"runtime/" + wheel_name}
    assert manifest["version"] == version
    assert {entry["path"] for entry in manifest["files"]} == expected
    check_wheel(project, skill / "runtime" / wheel_name, version)
    entries = {entry["path"]: (skill / entry["path"]).read_bytes() for entry in manifest["files"]}
    for entry in manifest["files"]:
        assert digest(entries[entry["path"]]) == entry["sha256"], entry["path"]
    entries["BUNDLE_MANIFEST.json"] = (skill / "BUNDLE_MANIFEST.json").read_bytes()
    archive_path = project / "downloads/repotrail-skill.zip"
    raw = archive_path.read_bytes()
    assert raw == zip_bytes({"repotrail/" + name: content for name, content in entries.items()})
    assert archive_path.with_suffix(".zip.sha256").read_text().split()[0] == digest(raw)
    assert len(raw) < 8 * 1024 * 1024
    print(
        json.dumps(
            {
                "skill_version": version,
                "files": len(entries),
                "bytes": len(raw),
                "sha256": digest(raw),
                "integrity": "passed",
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    if args.check:
        check(project)
    else:
        if args.wheel is None:
            parser.error("Build a current wheel, then pass --wheel PATH.")
        build(project, args.wheel.resolve())


if __name__ == "__main__":
    main()
