#!/usr/bin/env python3
"""
Generate Morel_Support_iOS.xcodeproj/project.pbxproj by walking the Swift source tree.
Run from ~/Morel_Support_iOS/: python3 generate_project.py
"""

from __future__ import annotations
import hashlib, os, textwrap
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
PROJECT_NAME   = "Morel_Support_iOS"
BUNDLE_ID      = "com.ericdong.morelsupport"
DEPLOYMENT_TGT = "17.0"
SWIFT_VERSION  = "5.9"
PRODUCT_NAME   = "Morel Support"

ROOT            = Path(__file__).parent
APP_ROOT        = ROOT / PROJECT_NAME
XCODEPROJ_DIR   = ROOT / f"{PROJECT_NAME}.xcodeproj"
PBXPROJ_PATH    = XCODEPROJ_DIR / "project.pbxproj"

# Frameworks to link
FRAMEWORKS = [
    "Foundation.framework",
    "UIKit.framework",
    "SwiftUI.framework",
    "MapKit.framework",
    "CoreLocation.framework",
]


# ── Deterministic UUID from path string ────────────────────────────────────────
def uid(s: str) -> str:
    """24-char uppercase hex UUID derived from MD5(s)."""
    return hashlib.md5(s.encode()).hexdigest()[:24].upper()


# ── Collect source files ───────────────────────────────────────────────────────
def collect_sources() -> list[Path]:
    sources = []
    for ext in ("*.swift",):
        sources.extend(sorted(APP_ROOT.rglob(ext)))
    # Also include Info.plist
    plist = APP_ROOT / "Info.plist"
    if plist.exists():
        sources.append(plist)
    return sources


def collect_assets() -> list[Path]:
    assets = []
    for p in sorted(APP_ROOT.rglob("*.xcassets")):
        assets.append(p)
    return assets


# ── Fixed UUIDs for structural items ──────────────────────────────────────────
PROJ_UID           = uid("PROJECT")
TARGET_UID         = uid("TARGET_APP")
PRODUCT_REF_UID    = uid("PRODUCT_APP_REF")
MAIN_GROUP_UID     = uid("MAIN_GROUP")
PRODUCTS_GROUP_UID = uid("PRODUCTS_GROUP")
FW_GROUP_UID       = uid("FRAMEWORKS_GROUP")
APP_GROUP_UID      = uid("APP_SOURCE_GROUP")

# Build phases
SOURCES_PHASE_UID  = uid("SOURCES_PHASE")
FW_PHASE_UID       = uid("FW_PHASE")
RES_PHASE_UID      = uid("RESOURCES_PHASE")

# Build configs
PROJ_DEBUG_UID     = uid("PROJ_CFG_DEBUG")
PROJ_RELEASE_UID   = uid("PROJ_CFG_RELEASE")
TGT_DEBUG_UID      = uid("TGT_CFG_DEBUG")
TGT_RELEASE_UID    = uid("TGT_CFG_RELEASE")
PROJ_CFGLIST_UID   = uid("PROJ_CFGLIST")
TGT_CFGLIST_UID    = uid("TGT_CFGLIST")


def fw_file_uid(fw: str) -> str:
    return uid(f"FW_FILE_{fw}")

def fw_build_uid(fw: str) -> str:
    return uid(f"FW_BUILD_{fw}")

def fw_ref_uid(fw: str) -> str:
    return uid(f"FW_REF_{fw}")

def src_file_uid(p: Path) -> str:
    return uid(f"FILE_{p.relative_to(ROOT)}")

def src_build_uid(p: Path) -> str:
    return uid(f"BUILD_{p.relative_to(ROOT)}")

def group_uid(p: Path) -> str:
    return uid(f"GROUP_{p.relative_to(ROOT)}")


# ── Build PBXFileReference section ────────────────────────────────────────────
def pbx_file_references(sources: list[Path], assets: list[Path]) -> str:
    lines = ["\t\t/* Begin PBXFileReference section */"]

    # App product
    lines.append(f"\t\t{PRODUCT_REF_UID} /* {PROJECT_NAME}.app */ = "
                 f"{{isa = PBXFileReference; explicitFileType = wrapper.application; "
                 f"includeInIndex = 0; path = {PROJECT_NAME}.app; sourceTree = BUILT_PRODUCTS_DIR; }};")

    # Framework references
    for fw in FRAMEWORKS:
        lines.append(f"\t\t{fw_ref_uid(fw)} /* {fw} */ = "
                     f"{{isa = PBXFileReference; lastKnownFileType = wrapper.framework; "
                     f"name = {fw}; path = System/Library/Frameworks/{fw}; sourceTree = SDKROOT; }};")

    # Source files
    for p in sources:
        rel = p.relative_to(APP_ROOT)
        if p.suffix == ".swift":
            ftype = "sourcecode.swift"
        elif p.name == "Info.plist":
            ftype = "text.plist.xml"
        else:
            ftype = "file"
        lines.append(f"\t\t{src_file_uid(p)} /* {p.name} */ = "
                     f"{{isa = PBXFileReference; lastKnownFileType = {ftype}; "
                     f"name = {p.name}; path = {rel.as_posix()}; sourceTree = \"<group>\"; }};")

    # Asset catalogs
    for a in assets:
        rel = a.relative_to(APP_ROOT)
        lines.append(f"\t\t{src_file_uid(a)} /* {a.name} */ = "
                     f"{{isa = PBXFileReference; lastKnownFileType = folder.assetcatalog; "
                     f"name = {a.name}; path = {rel.as_posix()}; sourceTree = \"<group>\"; }};")

    lines.append("\t\t/* End PBXFileReference section */")
    return "\n".join(lines)


# ── Build PBXBuildFile section ─────────────────────────────────────────────────
def pbx_build_files(swift_files: list[Path], assets: list[Path]) -> str:
    lines = ["\t\t/* Begin PBXBuildFile section */"]

    for fw in FRAMEWORKS:
        lines.append(f"\t\t{fw_build_uid(fw)} /* {fw} in Frameworks */ = "
                     f"{{isa = PBXBuildFile; fileRef = {fw_ref_uid(fw)} /* {fw} */; }};")

    for p in swift_files:
        if p.suffix == ".swift":
            lines.append(f"\t\t{src_build_uid(p)} /* {p.name} in Sources */ = "
                         f"{{isa = PBXBuildFile; fileRef = {src_file_uid(p)} /* {p.name} */; }};")

    for a in assets:
        lines.append(f"\t\t{src_build_uid(a)} /* {a.name} in Resources */ = "
                     f"{{isa = PBXBuildFile; fileRef = {src_file_uid(a)} /* {a.name} */; }};")

    lines.append("\t\t/* End PBXBuildFile section */")
    return "\n".join(lines)


# ── Build PBXGroup section ─────────────────────────────────────────────────────
def pbx_groups(sources: list[Path], assets: list[Path]) -> str:
    # Collect subdirectories
    subdirs: dict[Path, list[Path]] = {}
    for p in sources:
        if p.suffix == ".swift":
            parent = p.parent
            subdirs.setdefault(parent, []).append(p)

    lines = ["\t\t/* Begin PBXGroup section */"]

    # Main group
    children_main = [APP_GROUP_UID, PRODUCTS_GROUP_UID]
    lines.append(f"\t\t{MAIN_GROUP_UID} = {{")
    lines.append(f"\t\t\tisa = PBXGroup;")
    lines.append(f"\t\t\tchildren = (")
    for c in children_main:
        lines.append(f"\t\t\t\t{c},")
    lines.append(f"\t\t\t);")
    lines.append(f"\t\t\tsourceTree = \"<group>\";")
    lines.append(f"\t\t}};")

    # Products group
    lines.append(f"\t\t{PRODUCTS_GROUP_UID} /* Products */ = {{")
    lines.append(f"\t\t\tisa = PBXGroup;")
    lines.append(f"\t\t\tchildren = ({PRODUCT_REF_UID} /* {PROJECT_NAME}.app */,);")
    lines.append(f"\t\t\tname = Products;")
    lines.append(f"\t\t\tsourceTree = \"<group>\";")
    lines.append(f"\t\t}};")

    # App source group (top-level, contains subdir groups + assets)
    app_children: list[str] = []
    for subdir in sorted(subdirs.keys()):
        app_children.append(group_uid(subdir))
    for a in assets:
        app_children.append(src_file_uid(a))
    # Info.plist
    plist = APP_ROOT / "Info.plist"
    if plist.exists():
        app_children.append(src_file_uid(plist))

    lines.append(f"\t\t{APP_GROUP_UID} /* {PROJECT_NAME} */ = {{")
    lines.append(f"\t\t\tisa = PBXGroup;")
    lines.append(f"\t\t\tchildren = (")
    for c in app_children:
        lines.append(f"\t\t\t\t{c},")
    lines.append(f"\t\t\t);")
    lines.append(f"\t\t\tname = {PROJECT_NAME};")
    lines.append(f"\t\t\tpath = {PROJECT_NAME};")
    lines.append(f"\t\t\tsourceTree = \"<group>\";")
    lines.append(f"\t\t}};")

    # Subdir groups
    for subdir, files in sorted(subdirs.items()):
        g_uid = group_uid(subdir)
        lines.append(f"\t\t{g_uid} /* {subdir.name} */ = {{")
        lines.append(f"\t\t\tisa = PBXGroup;")
        lines.append(f"\t\t\tchildren = (")
        for f in sorted(files):
            lines.append(f"\t\t\t\t{src_file_uid(f)} /* {f.name} */,")
        lines.append(f"\t\t\t);")
        lines.append(f"\t\t\tname = {subdir.name};")
        lines.append(f"\t\t\tsourceTree = \"<group>\";")
        lines.append(f"\t\t}};")

    lines.append("\t\t/* End PBXGroup section */")
    return "\n".join(lines)


# ── Build phase sections ───────────────────────────────────────────────────────
def pbx_sources_build_phase(swift_files: list[Path]) -> str:
    lines = ["\t\t/* Begin PBXSourcesBuildPhase section */",
             f"\t\t{SOURCES_PHASE_UID} /* Sources */ = {{",
             "\t\t\tisa = PBXSourcesBuildPhase;",
             "\t\t\tbuildActionMask = 2147483647;",
             "\t\t\tfiles = ("]
    for p in swift_files:
        if p.suffix == ".swift":
            lines.append(f"\t\t\t\t{src_build_uid(p)} /* {p.name} in Sources */,")
    lines += ["\t\t\t);", "\t\t\trunOnlyForDeploymentPostprocessing = 0;", "\t\t};",
              "\t\t/* End PBXSourcesBuildPhase section */"]
    return "\n".join(lines)


def pbx_frameworks_build_phase() -> str:
    lines = ["\t\t/* Begin PBXFrameworksBuildPhase section */",
             f"\t\t{FW_PHASE_UID} /* Frameworks */ = {{",
             "\t\t\tisa = PBXFrameworksBuildPhase;",
             "\t\t\tbuildActionMask = 2147483647;",
             "\t\t\tfiles = ("]
    for fw in FRAMEWORKS:
        lines.append(f"\t\t\t\t{fw_build_uid(fw)} /* {fw} in Frameworks */,")
    lines += ["\t\t\t);", "\t\t\trunOnlyForDeploymentPostprocessing = 0;", "\t\t};",
              "\t\t/* End PBXFrameworksBuildPhase section */"]
    return "\n".join(lines)


def pbx_resources_build_phase(assets: list[Path]) -> str:
    lines = ["\t\t/* Begin PBXResourcesBuildPhase section */",
             f"\t\t{RES_PHASE_UID} /* Resources */ = {{",
             "\t\t\tisa = PBXResourcesBuildPhase;",
             "\t\t\tbuildActionMask = 2147483647;",
             "\t\t\tfiles = ("]
    for a in assets:
        lines.append(f"\t\t\t\t{src_build_uid(a)} /* {a.name} in Resources */,")
    lines += ["\t\t\t);", "\t\t\trunOnlyForDeploymentPostprocessing = 0;", "\t\t};",
              "\t\t/* End PBXResourcesBuildPhase section */"]
    return "\n".join(lines)


# ── Build configurations ───────────────────────────────────────────────────────
def pbx_build_configurations() -> str:
    common_proj = textwrap.dedent(f"""\
        ALWAYS_SEARCH_USER_PATHS = NO;
        CLANG_ENABLE_MODULES = YES;
        CLANG_ENABLE_OBJC_ARC = YES;
        COPY_PHASE_STRIP = NO;
        ENABLE_STRICT_OBJC_MSGSEND = YES;
        GCC_C_LANGUAGE_STANDARD = gnu11;
        IPHONEOS_DEPLOYMENT_TARGET = {DEPLOYMENT_TGT};
        MTL_ENABLE_DEBUG_INFO = INCLUDE_SOURCE;
        ONLY_ACTIVE_ARCH = YES;
        SDKROOT = iphoneos;
        SWIFT_VERSION = {SWIFT_VERSION};
    """).strip()

    common_tgt = textwrap.dedent(f"""\
        ALWAYS_EMBED_SWIFT_STANDARD_LIBRARIES = YES;
        ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;
        ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME = AccentColor;
        CODE_SIGN_STYLE = Automatic;
        CURRENT_PROJECT_VERSION = 1;
        DEVELOPMENT_TEAM = "";
        GENERATE_INFOPLIST_FILE = NO;
        INFOPLIST_FILE = {PROJECT_NAME}/Info.plist;
        IPHONEOS_DEPLOYMENT_TARGET = {DEPLOYMENT_TGT};
        LD_RUNPATH_SEARCH_PATHS = "$(inherited) @executable_path/Frameworks";
        MARKETING_VERSION = 1.0;
        PRODUCT_BUNDLE_IDENTIFIER = {BUNDLE_ID};
        PRODUCT_NAME = "$(TARGET_NAME)";
        SWIFT_EMIT_LOC_STRINGS = YES;
        SWIFT_VERSION = {SWIFT_VERSION};
        TARGETED_DEVICE_FAMILY = "1,2";
    """).strip()

    def fmt_settings(s: str, uid: str, name: str) -> list[str]:
        lines = [f"\t\t{uid} /* {name} */ = {{",
                 "\t\t\tisa = XCBuildConfiguration;",
                 "\t\t\tbuildSettings = {"]
        for line in s.splitlines():
            lines.append(f"\t\t\t\t{line}")
        lines += ["\t\t\t};", f'\t\t\tname = {name};', "\t\t};"]
        return lines

    parts = ["\t\t/* Begin XCBuildConfiguration section */"]
    parts += fmt_settings(common_proj + "\n\t\t\t\tDEBUG_INFORMATION_FORMAT = dwarf;",
                          PROJ_DEBUG_UID, "Debug")
    parts += fmt_settings(common_proj + "\n\t\t\t\tDEBUG_INFORMATION_FORMAT = \"dwarf-with-dsym\";",
                          PROJ_RELEASE_UID, "Release")
    parts += fmt_settings(common_tgt + "\n\t\t\t\tDEBUG_INFORMATION_FORMAT = dwarf;",
                          TGT_DEBUG_UID, "Debug")
    parts += fmt_settings(common_tgt + "\n\t\t\t\tDEBUG_INFORMATION_FORMAT = \"dwarf-with-dsym\";",
                          TGT_RELEASE_UID, "Release")
    parts.append("\t\t/* End XCBuildConfiguration section */")
    return "\n".join(parts)


def pbx_config_lists() -> str:
    lines = ["\t\t/* Begin XCConfigurationList section */",
             f"\t\t{PROJ_CFGLIST_UID} /* Build configuration list for PBXProject \"{PROJECT_NAME}\" */ = {{",
             "\t\t\tisa = XCConfigurationList;",
             "\t\t\tbuildConfigurations = (",
             f"\t\t\t\t{PROJ_DEBUG_UID} /* Debug */,",
             f"\t\t\t\t{PROJ_RELEASE_UID} /* Release */,",
             "\t\t\t);",
             "\t\t\tdefaultConfigurationIsVisible = 0;",
             "\t\t\tdefaultConfigurationName = Release;",
             "\t\t};",
             f"\t\t{TGT_CFGLIST_UID} /* Build configuration list for PBXNativeTarget \"{PROJECT_NAME}\" */ = {{",
             "\t\t\tisa = XCConfigurationList;",
             "\t\t\tbuildConfigurations = (",
             f"\t\t\t\t{TGT_DEBUG_UID} /* Debug */,",
             f"\t\t\t\t{TGT_RELEASE_UID} /* Release */,",
             "\t\t\t);",
             "\t\t\tdefaultConfigurationIsVisible = 0;",
             "\t\t\tdefaultConfigurationName = Release;",
             "\t\t};",
             "\t\t/* End XCConfigurationList section */"]
    return "\n".join(lines)


# ── Assemble project.pbxproj ───────────────────────────────────────────────────
def generate(sources: list[Path], assets: list[Path]) -> str:
    swift_files = [p for p in sources if p.suffix == ".swift"]
    plist_files = [p for p in sources if p.name == "Info.plist"]

    sections = [
        "// !$*UTF8*$!",
        "{",
        "\tarchiveVersion = 1;",
        "\tclasses = {",
        "\t};",
        "\tobjectVersion = 56;",
        "\tobjects = {",
        "",
        pbx_build_files(swift_files, assets),
        "",
        pbx_file_references(swift_files + plist_files, assets),
        "",
        pbx_frameworks_build_phase(),
        "",
        pbx_groups(swift_files, assets),
        "",
        # PBXNativeTarget
        "\t\t/* Begin PBXNativeTarget section */",
        f"\t\t{TARGET_UID} /* {PROJECT_NAME} */ = {{",
        "\t\t\tisa = PBXNativeTarget;",
        f"\t\t\tbuildConfigurationList = {TGT_CFGLIST_UID} /* Build configuration list for PBXNativeTarget \"{PROJECT_NAME}\" */;",
        "\t\t\tbuildPhases = (",
        f"\t\t\t\t{SOURCES_PHASE_UID} /* Sources */,",
        f"\t\t\t\t{FW_PHASE_UID} /* Frameworks */,",
        f"\t\t\t\t{RES_PHASE_UID} /* Resources */,",
        "\t\t\t);",
        "\t\t\tbuildRules = ();",
        "\t\t\tdependencies = ();",
        f'\t\t\tname = "{PROJECT_NAME}";',
        f"\t\t\tpackageProductDependencies = ();",
        f"\t\t\tproductName = {PROJECT_NAME};",
        f"\t\t\tproductReference = {PRODUCT_REF_UID} /* {PROJECT_NAME}.app */;",
        "\t\t\tproductType = \"com.apple.product-type.application\";",
        "\t\t};",
        "\t\t/* End PBXNativeTarget section */",
        "",
        # PBXProject
        "\t\t/* Begin PBXProject section */",
        f"\t\t{PROJ_UID} /* Project object */ = {{",
        "\t\t\tisa = PBXProject;",
        "\t\t\tattributes = {",
        "\t\t\t\tBuildIndependentTargetsInParallel = 1;",
        f"\t\t\t\tLastSwiftUpdateCheck = 1500;",
        f"\t\t\t\tLastUpgradeCheck = 1500;",
        "\t\t\t\tTargetAttributes = {",
        f"\t\t\t\t\t{TARGET_UID} = {{",
        "\t\t\t\t\t\tCreatedOnToolsVersion = 15.0;",
        "\t\t\t\t\t};",
        "\t\t\t\t};",
        "\t\t\t};",
        f"\t\t\tbuildConfigurationList = {PROJ_CFGLIST_UID} /* Build configuration list for PBXProject \"{PROJECT_NAME}\" */;",
        "\t\t\tcompatibilityVersion = \"Xcode 14.0\";",
        "\t\t\tdevelopmentRegion = en;",
        "\t\t\thasScannedForEncodings = 0;",
        "\t\t\tknownRegions = (en, Base);",
        f"\t\t\tmainGroup = {MAIN_GROUP_UID};",
        "\t\t\tpackageReferences = ();",
        f"\t\t\tproductRefGroup = {PRODUCTS_GROUP_UID} /* Products */;",
        "\t\t\tprojectDirPath = \"\";",
        "\t\t\tprojectRoot = \"\";",
        "\t\t\ttargets = (",
        f"\t\t\t\t{TARGET_UID} /* {PROJECT_NAME} */,",
        "\t\t\t);",
        "\t\t};",
        "\t\t/* End PBXProject section */",
        "",
        pbx_resources_build_phase(assets),
        "",
        pbx_sources_build_phase(swift_files),
        "",
        pbx_build_configurations(),
        "",
        pbx_config_lists(),
        "",
        "\t};",
        f"\trootObject = {PROJ_UID} /* Project object */;",
        "}",
    ]
    return "\n".join(sections) + "\n"


def main():
    sources = collect_sources()
    assets  = collect_assets()

    print(f"Found {len([s for s in sources if s.suffix == '.swift'])} Swift files")
    print(f"Found {len(assets)} asset catalog(s)")

    XCODEPROJ_DIR.mkdir(exist_ok=True)
    content = generate(sources, assets)
    PBXPROJ_PATH.write_text(content)
    print(f"Written: {PBXPROJ_PATH}")
    print(f"\nOpen with: open {ROOT / (PROJECT_NAME + '.xcodeproj')}")


if __name__ == "__main__":
    main()
