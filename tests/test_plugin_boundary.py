from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = PROJECT_ROOT / "hermes-agent"
MINIMUM_SDK_VERSION = "1.5.3"


def test_pyproject_matches_plugin_runtime_and_release_contract() -> None:
    project_data = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    import yaml

    manifest = yaml.safe_load(
        (PROJECT_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    )

    assert project_data["project"]["version"] == manifest["version"] == "1.0.1"
    assert project_data["project"]["requires-python"] == ">=3.11,<3.15"
    assert "python-trueconf-bot>=1.5.3,<2" in project_data["project"]["dependencies"]
    assert "mistune>=3,<4" in project_data["project"]["dependencies"]
    assert manifest["python_dependencies"] == [
        "mistune>=3,<4",
        "python-trueconf-bot>=1.5.3,<2",
    ]
    assert "hermes-agent==0.21.0" not in project_data["dependency-groups"]["dev"]
    assert project_data["tool"]["uv"]["package"] is False
    assert project_data["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


def run_hermes_probe(
    tmp_path: Path,
    program: str,
    *,
    config_text: str = "plugins:\n  enabled:\n    - trueconf-platform\n",
) -> subprocess.CompletedProcess[str]:
    hermes_home = tmp_path / "hermes-home"
    plugin_dir = hermes_home / "plugins" / "trueconf"
    plugin_dir.parent.mkdir(parents=True)
    plugin_dir.symlink_to(PROJECT_ROOT, target_is_directory=True)
    (hermes_home / "config.yaml").write_text(config_text, encoding="utf-8")

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["TRUECONF_PLUGIN_ROOT"] = str(PROJECT_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(HERMES_ROOT), env.get("PYTHONPATH", "")) if part
    )
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_clean_hermes_discovers_trueconf_platform(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
from hermes_cli.plugins import discover_plugins
from gateway.config import Platform
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
print(json.dumps({
    "registered": platform_registry.is_registered("trueconf"),
    "platform": Platform("trueconf").value,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "registered": True,
        "platform": "trueconf",
    }


def test_registration_exposes_the_approved_platform_contract(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
print(json.dumps({
    "name": entry.name,
    "label": entry.label,
    "source": entry.source,
    "required_env": entry.required_env,
    "install_hint": entry.install_hint,
    "allowed_users_env": entry.allowed_users_env,
    "allow_all_env": entry.allow_all_env,
    "max_message_length": entry.max_message_length,
    "emoji": entry.emoji,
    "has_adapter_factory": callable(entry.adapter_factory),
    "has_check": callable(entry.check_fn),
    "has_validator": callable(entry.validate_config),
    "has_connected_check": callable(entry.is_connected),
    "has_env_bridge": callable(entry.env_enablement_fn),
    "has_yaml_bridge": callable(entry.apply_yaml_config_fn),
    "has_target_parser": callable(entry.parse_target_ref_fn),
    "cron_home_env": entry.cron_deliver_env_var,
    "has_standalone_sender": callable(entry.standalone_sender_fn),
    "has_setup": callable(entry.setup_fn),
    "hint_uses_markdown": "Write TrueConf Markdown" in entry.platform_hint,
    "hint_has_underline": "__underline__" in entry.platform_hint,
    "hint_keeps_commands_bare": "commands such as /start as bare text" in entry.platform_hint,
    "hint_distinguishes_code": (
        "Backtick code becomes bold-italic" in entry.platform_hint
        and "fenced blocks become italic" in entry.platform_hint
    ),
    "hint_describes_nested_lists": (
        "Nested lists keep indentation" in entry.platform_hint
        and "per-level markers" in entry.platform_hint
    ),
    "hint_describes_tables": (
        "pipe-separated rows" in entry.platform_hint
        and "labeled blocks" in entry.platform_hint
    ),
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "name": "trueconf",
        "label": "TrueConf",
        "source": "plugin",
        "required_env": ["TRUECONF_SERVER", "TRUECONF_USERNAME", "TRUECONF_PASSWORD"],
        "install_hint": "Install mistune>=3,<4 and python-trueconf-bot>=1.5.3,<2",
        "allowed_users_env": "TRUECONF_ALLOWED_USERS",
        "allow_all_env": "TRUECONF_ALLOW_ALL_USERS",
        "max_message_length": 4096,
        "emoji": "📹",
        "has_adapter_factory": True,
        "has_check": True,
        "has_validator": True,
        "has_connected_check": True,
        "has_env_bridge": True,
        "has_yaml_bridge": True,
        "has_target_parser": True,
        "cron_home_env": "TRUECONF_HOME_CHANNEL",
        "has_standalone_sender": True,
        "has_setup": True,
        "hint_uses_markdown": True,
        "hint_has_underline": True,
        "hint_keeps_commands_bare": True,
        "hint_distinguishes_code": True,
        "hint_describes_nested_lists": True,
        "hint_describes_tables": True,
    }


def test_manifest_masks_password_during_install() -> None:
    import yaml

    manifest = yaml.safe_load(
        (PROJECT_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    )
    password = next(
        item for item in manifest["requires_env"] if item["name"] == "TRUECONF_PASSWORD"
    )

    assert password["secret"] is True
    assert "password" not in password


def test_manifest_prompts_for_tls_verification_during_install() -> None:
    import yaml

    manifest = yaml.safe_load(
        (PROJECT_ROOT / "plugin.yaml").read_text(encoding="utf-8")
    )
    verify_ssl = next(
        item
        for item in manifest["requires_env"]
        if item["name"] == "TRUECONF_VERIFY_SSL"
    )

    assert "true" in verify_ssl["description"]
    assert "false" in verify_ssl["description"]
    assert "CA bundle" in verify_ssl["description"]
    assert verify_ssl["secret"] is False


def test_operator_documentation_covers_stage_9_gate() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    required_operator_topics = (
        "hermes plugins install",
        MINIMUM_SDK_VERSION,
        "TRUECONF_USERNAME",
        "TRUECONF_PASSWORD",
        "TRUECONF_VERIFY_SSL",
        "TRUECONF_ALLOWED_USERS",
        "TRUECONF_ALLOW_ALL_USERS",
        "TRUECONF_HOME_CHANNEL",
        "verify_ssl: false",
        "parse_mode: html",
        "require_mention: true",
        "Migrate from a historical TrueConf integration",
        "Known limitations",
        "Troubleshooting",
        "~/.hermes/logs/",
        "hermes plugins remove trueconf-platform",
    )
    assert all(topic in readme for topic in required_operator_topics)
    assert "TRUECONF_PASSWORD:" not in readme


def test_register_adds_exactly_one_trueconf_platform(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import importlib.util
import json
from pathlib import Path

plugin_root = Path(__import__("os").environ["TRUECONF_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location(
    "trueconf_boundary_probe",
    plugin_root / "__init__.py",
    submodule_search_locations=[str(plugin_root)],
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class RecordingContext:
    def __init__(self):
        self.calls = []

    def register_platform(self, **kwargs):
        self.calls.append(kwargs)

ctx = RecordingContext()
module.register(ctx)
print(json.dumps({
    "count": len(ctx.calls),
    "names": [call["name"] for call in ctx.calls],
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"count": 1, "names": ["trueconf"]}


def test_missing_sdk_has_passive_probe_and_lazy_installer(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import importlib.util
import json
import sys

real_find_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, *args, **kwargs: (
    None if name == "trueconf" else real_find_spec(name, *args, **kwargs)
)

from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
print(json.dumps({
    "available": entry.check_fn(),
    "install_hint": entry.install_hint,
    "has_installer": entry.ensure_deps_fn is not None,
    "sdk_imported": "trueconf" in sys.modules,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "available": False,
        "install_hint": "Install mistune>=3,<4 and python-trueconf-bot>=1.5.3,<2",
        "has_installer": True,
        "sdk_imported": False,
    }


def test_missing_formatter_dependency_is_detected_without_import(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import importlib.util
import json
import sys

real_find_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, *args, **kwargs: (
    None if name == "mistune" else real_find_spec(name, *args, **kwargs)
)

from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
print(json.dumps({
    "available": entry.check_fn(),
    "formatter_imported": "mistune" in sys.modules,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "available": False,
        "formatter_imported": False,
    }


def test_lazy_installer_uses_hermes_dependency_pipeline(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
from types import SimpleNamespace

from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry
import tools.lazy_deps

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
availability = iter((False, True))
entry.ensure_deps_fn.__globals__["_runtime_dependencies_available"] = lambda: next(availability)
calls = []

def install_specs(specs, *, timeout):
    calls.append({"specs": specs, "timeout": timeout})
    return SimpleNamespace(ok=True, reason="")

tools.lazy_deps.install_specs = install_specs
print(json.dumps({
    "installed": entry.ensure_deps_fn(),
    "calls": calls,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "installed": True,
        "calls": [
            {
                "specs": ["mistune>=3,<4", "python-trueconf-bot>=1.5.3,<2"],
                "timeout": 300,
            }
        ],
    }


def test_first_start_refreshes_typing_extensions_after_lazy_install(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
from types import SimpleNamespace
import typing_extensions
from hermes_cli.plugins import discover_plugins
from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry
import tools.lazy_deps

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
# Model Hermes holding the pre-upgrade module while pip has updated its file.
del typing_extensions.sentinel
calls = []
def install_specs(specs, *, timeout):
    calls.append(specs)
    return SimpleNamespace(ok=True, reason="")
tools.lazy_deps.install_specs = install_specs
entry.validate_config = lambda config: True
adapter = platform_registry.create_adapter("trueconf", PlatformConfig())
print(json.dumps({
    "created": adapter is not None,
    "sentinel_available": hasattr(typing_extensions, "sentinel"),
    "installs": len(calls),
}))
""",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "created": True,
        "sentinel_available": True,
        "installs": 1,
    }, result.stderr


def test_outdated_sdk_is_reported_as_unavailable(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import importlib.metadata
import json

real_version = importlib.metadata.version
importlib.metadata.version = lambda name: (
    "1.4.9" if name == "python-trueconf-bot" else real_version(name)
)

from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
print(json.dumps({
    "available": entry.check_fn(),
    "install_hint": entry.install_hint,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "available": False,
        "install_hint": "Install mistune>=3,<4 and python-trueconf-bot>=1.5.3,<2",
    }


def test_supported_sdk_version_is_available_without_importing_it(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import sys

from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
print(json.dumps({
    "available": entry.check_fn(),
    "sdk_imported": "trueconf" in sys.modules,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "available": True,
        "sdk_imported": False,
    }


def test_next_major_sdk_is_reported_as_unavailable(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import importlib.metadata
import json

real_version = importlib.metadata.version
importlib.metadata.version = lambda name: (
    "2.0.0" if name == "python-trueconf-bot" else real_version(name)
)

from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
entry = platform_registry.get("trueconf")
print(json.dumps({
    "available": entry.check_fn(),
    "install_hint": entry.install_hint,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "available": False,
        "install_hint": "Install mistune>=3,<4 and python-trueconf-bot>=1.5.3,<2",
    }


def test_target_parser_accepts_one_case_preserving_chat_id(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
parse = platform_registry.get("trueconf").parse_target_ref_fn
values = [
    "5559db793777b228a585e293fc64ae8991cec437",
    "Case-Sensitive-Chat-ID",
    "",
    " spaced",
    "spaced ",
]
print(json.dumps({value: parse(value) for value in values}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "5559db793777b228a585e293fc64ae8991cec437": [
            "5559db793777b228a585e293fc64ae8991cec437",
            None,
        ],
        "Case-Sensitive-Chat-ID": ["Case-Sensitive-Chat-ID", None],
        "": None,
        " spaced": None,
        "spaced ": None,
    }


def test_public_config_hooks_seed_only_supported_values(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import os
from hermes_cli.plugins import discover_plugins
from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
    "TRUECONF_SERVER": "video.example.com",
    "TRUECONF_VERIFY_SSL": "false",
    "TRUECONF_HOME_CHANNEL": "Home-Chat-ID",
})
discover_plugins(force=True)
entry = platform_registry.get("trueconf")
seed = entry.env_enablement_fn()
yaml_config = {"platforms": {"trueconf": {
    "server": "yaml.example.com",
    "port": 8443,
    "https": True,
    "verify_ssl": "/etc/ssl/cert.pem",
    "parse_mode": "HTML",
    "require_mention": False,
    "allow_from": ["Direct-User-ID"],
    "group_allow_from": ["Group-User-ID"],
    "allowed_chats": ["Scoped-Group-ID", "Scoped-Channel-ID"],
    "group_allowed_chats": ["Trusted-Group-ID", "Trusted-Channel-ID"],
    "free_response_chats": ["Agent-Room-ID"],
    "observe_unmentioned_group_messages": True,
    "observe_context_limit": 20,
}}}
bridged = entry.apply_yaml_config_fn(
    yaml_config,
    yaml_config["platforms"]["trueconf"],
)
configured = entry.is_connected(PlatformConfig(
    enabled=True,
    extra={"server": "yaml.example.com"},
))
print(json.dumps({
    "seed": seed,
    "bridged": bridged,
    "configured": configured,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "seed": {
            "server": "video.example.com",
            "verify_ssl": False,
            "home_channel": {
                "chat_id": "Home-Chat-ID",
                "name": "TrueConf home",
            },
        },
        "bridged": {
            "server": "yaml.example.com",
            "port": 8443,
            "https": True,
            "verify_ssl": "/etc/ssl/cert.pem",
            "parse_mode": "html",
            "require_mention": False,
            "allow_from": ["Direct-User-ID"],
            "group_allow_from": ["Group-User-ID"],
            "allowed_chats": ["Scoped-Group-ID", "Scoped-Channel-ID"],
            "group_allowed_chats": ["Trusted-Group-ID", "Trusted-Channel-ID"],
            "free_response_chats": ["Agent-Room-ID"],
            "observe_unmentioned_group_messages": True,
            "observe_context_limit": 20,
        },
        "configured": True,
    }


def test_env_tls_verification_accepts_true_false_and_ca_bundle(
    tmp_path: Path,
) -> None:
    ca_bundle = tmp_path / "trueconf-ca.pem"
    ca_bundle.write_text("fixture", encoding="utf-8")
    result = run_hermes_probe(
        tmp_path,
        f"""\
import json
import os
from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

os.environ.update({{
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
    "TRUECONF_SERVER": "video.example.com",
}})
discover_plugins(force=True)
seed = platform_registry.get("trueconf").env_enablement_fn
values = {{}}
for raw in ("true", "FALSE", {str(ca_bundle)!r}):
    os.environ["TRUECONF_VERIFY_SSL"] = raw
    values[raw] = seed()["verify_ssl"]
print(json.dumps(values))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "true": True,
        "FALSE": False,
        str(ca_bundle): str(ca_bundle),
    }


def test_env_only_configuration_enables_trueconf_and_exposes_cron_home(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import os

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
    "TRUECONF_SERVER": "env.example.com",
    "TRUECONF_HOME_CHANNEL": "Home-Chat-ID",
})

from gateway.config import Platform, load_gateway_config
from cron.scheduler import cron_delivery_targets, _resolve_single_delivery_target

config = load_gateway_config()
platform_config = config.platforms[Platform("trueconf")]
target = next(item for item in cron_delivery_targets() if item["id"] == "trueconf")
resolved = _resolve_single_delivery_target({"name": "fixture"}, "trueconf")
print(json.dumps({
    "enabled": platform_config.enabled,
    "server": platform_config.extra["server"],
    "home": platform_config.home_channel.chat_id,
    "target": target,
    "resolved": resolved,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "enabled": True,
        "server": "env.example.com",
        "home": "Home-Chat-ID",
        "target": {
            "id": "trueconf",
            "name": "Trueconf",
            "home_target_set": True,
            "home_env_var": "TRUECONF_HOME_CHANNEL",
        },
        "resolved": {
            "platform": "trueconf",
            "chat_id": "Home-Chat-ID",
            "thread_id": None,
        },
    }


def test_env_bridge_preserves_raw_home_chat_written_by_sethome(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import os

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
    "TRUECONF_SERVER": "env.example.com",
    "TRUECONF_HOME_CHANNEL": "5559db793777b228a585e293fc64ae8991cec437",
})

from hermes_cli.plugins import discover_plugins
from gateway.platform_registry import platform_registry

discover_plugins(force=True)
seed = platform_registry.get("trueconf").env_enablement_fn()
print(json.dumps(seed))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["home_channel"]["chat_id"] == (
        "5559db793777b228a585e293fc64ae8991cec437"
    )


def test_canonical_yaml_home_channel_reaches_platform_config(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import os

os.environ.pop("TRUECONF_HOME_CHANNEL", None)
os.environ.pop("TRUECONF_HOME_CHANNEL_NAME", None)
os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
    "TRUECONF_SERVER": "env.example.com",
})

from gateway.config import Platform, load_gateway_config

home = load_gateway_config().platforms[Platform("trueconf")].home_channel
print(json.dumps({"chat_id": home.chat_id, "name": home.name}))
""",
        config_text="""plugins:
  enabled:
    - trueconf-platform
platforms:
  trueconf:
    enabled: true
    home_channel:
      platform: trueconf
      chat_id: YAML-Home-Chat-ID
      name: YAML home
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "chat_id": "YAML-Home-Chat-ID",
        "name": "YAML home",
    }


def test_wizard_tls_write_is_effective_with_canonical_platform_block(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import os

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
})

from hermes_cli.config import write_platform_config_field
write_platform_config_field("trueconf", "verify_ssl", False, raw=True)

from gateway.config import Platform, load_gateway_config
platform_config = load_gateway_config().platforms[Platform("trueconf")]
print(json.dumps({
    "verify_ssl": platform_config.extra["verify_ssl"],
}))
""",
        config_text="""plugins:
  enabled:
    - trueconf-platform
platforms:
  trueconf:
    enabled: true
    server: video.example.com
    verify_ssl: true
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"verify_ssl": False}


def test_top_level_trueconf_block_is_rejected_with_canonical_path_guidance(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import os

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
})

from gateway.config import Platform, load_gateway_config
from gateway.platform_registry import platform_registry

platform_config = load_gateway_config().platforms[Platform("trueconf")]
print(json.dumps({
    "valid": platform_registry.get("trueconf").validate_config(platform_config),
    "error": platform_config.extra.get("_trueconf_config_error"),
}))
""",
        config_text="""plugins:
  enabled:
    - trueconf-platform
trueconf:
  enabled: true
  server: video.example.com
  verify_ssl: false
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "valid": False,
        "error": "move the top-level trueconf block to platforms.trueconf",
    }


def test_yaml_behavior_and_explicit_disable_preserve_precedence(tmp_path: Path) -> None:
    enabled_result = run_hermes_probe(
        tmp_path / "enabled",
        """
import json
import os

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
    "TRUECONF_SERVER": "env.example.com",
    "TRUECONF_HOME_CHANNEL": "Env-Home-Chat-ID",
})

from gateway.config import Platform, load_gateway_config

platform_config = load_gateway_config().platforms[Platform("trueconf")]
print(json.dumps({
    "enabled": platform_config.enabled,
    "extra": platform_config.extra,
    "home": platform_config.home_channel.chat_id,
}))
""",
        config_text="""plugins:
  enabled:
    - trueconf-platform
platforms:
  trueconf:
    enabled: true
    server: yaml.example.com
    port: 8443
    https: false
    verify_ssl: false
    parse_mode: html
    require_mention: false
    allowed_chats:
      - Scoped-Group-ID
      - Scoped-Channel-ID
    group_allowed_chats:
      - Trusted-Group-ID
      - Trusted-Channel-ID
    free_response_chats:
      - Agent-Room-ID
    observe_unmentioned_group_messages: true
    observe_context_limit: 20
    home_channel:
      platform: trueconf
      chat_id: YAML-Home-Chat-ID
      name: YAML home
""",
    )
    disabled_result = run_hermes_probe(
        tmp_path / "disabled",
        """
import json
import os

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
    "TRUECONF_SERVER": "env.example.com",
})

from gateway.config import Platform, load_gateway_config

platform_config = load_gateway_config().platforms[Platform("trueconf")]
print(json.dumps({"enabled": platform_config.enabled}))
""",
        config_text="""plugins:
  enabled:
    - trueconf-platform
platforms:
  trueconf:
    enabled: false
    server: yaml.example.com
""",
    )

    assert enabled_result.returncode == 0, enabled_result.stderr
    enabled = json.loads(enabled_result.stdout)
    assert enabled["enabled"] is True
    assert enabled["home"] == "Env-Home-Chat-ID"
    assert {
        key: enabled["extra"][key]
        for key in (
            "server",
            "port",
            "https",
            "verify_ssl",
            "parse_mode",
            "require_mention",
            "allowed_chats",
            "group_allowed_chats",
            "free_response_chats",
            "observe_unmentioned_group_messages",
            "observe_context_limit",
        )
    } == {
        "server": "env.example.com",
        "port": 8443,
        "https": False,
        "verify_ssl": False,
        "parse_mode": "html",
        "require_mention": False,
        "allowed_chats": ["Scoped-Group-ID", "Scoped-Channel-ID"],
        "group_allowed_chats": ["Trusted-Group-ID", "Trusted-Channel-ID"],
        "free_response_chats": ["Agent-Room-ID"],
        "observe_unmentioned_group_messages": True,
        "observe_context_limit": 20,
    }
    assert disabled_result.returncode == 0, disabled_result.stderr
    assert json.loads(disabled_result.stdout) == {"enabled": False}


def test_yaml_password_is_rejected_with_secret_store_guidance(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import json
import logging
import os
from hermes_cli.plugins import discover_plugins
from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "real-secret",
})
logging.basicConfig(level=logging.WARNING)
discover_plugins(force=True)
entry = platform_registry.get("trueconf")
yaml_config = {"platforms": {"trueconf": {
    "server": "video.example.com",
    "password": "must-not-cross-the-bridge",
}}}
bridged = entry.apply_yaml_config_fn(
    yaml_config,
    yaml_config["platforms"]["trueconf"],
)
valid = entry.validate_config(PlatformConfig(enabled=True, extra=bridged))
print(json.dumps({
    "valid": valid,
    "password_preserved": "password" in bridged,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "valid": False,
        "password_preserved": False,
    }
    assert "secret environment/store" in result.stderr


def test_public_validator_checks_local_config_without_the_sdk(tmp_path: Path) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import importlib.util
import json
import os

real_find_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, *args, **kwargs: (
    None if name == "trueconf" else real_find_spec(name, *args, **kwargs)
)

from hermes_cli.plugins import discover_plugins
from gateway.config import PlatformConfig
from gateway.platform_registry import platform_registry

os.environ.update({
    "TRUECONF_USERNAME": "bot",
    "TRUECONF_PASSWORD": "secret",
})
discover_plugins(force=True)
validate = platform_registry.get("trueconf").validate_config

def config(**extra):
    return PlatformConfig(enabled=True, extra=extra)

results = {
    "valid": validate(config(server="video.example.com")),
    "scheme": validate(config(server="https://video.example.com")),
    "path": validate(config(server="video.example.com/api")),
    "low_port": validate(config(server="video.example.com", port=0)),
    "high_port": validate(config(server="video.example.com", port=65536)),
    "bool_port": validate(config(server="video.example.com", port=True)),
    "bad_https": validate(config(server="video.example.com", https="yes")),
    "bad_tls": validate(config(server="video.example.com", verify_ssl="missing-ca.pem")),
    "bad_mode": validate(config(server="video.example.com", parse_mode="rtf")),
    "markdown_mode": validate(config(server="video.example.com", parse_mode="markdown")),
    "text_mode": validate(config(server="video.example.com", parse_mode="text")),
    "bad_group_chats_scalar": validate(config(
        server="video.example.com", group_allowed_chats="Trusted-Group-ID"
    )),
    "bad_group_chats_entry": validate(config(
        server="video.example.com", group_allowed_chats=["Trusted-Group-ID", ""]
    )),
    "bad_allowed_chats_scalar": validate(config(
        server="video.example.com", allowed_chats="Scoped-Group-ID"
    )),
    "bad_allowed_chats_entry": validate(config(
        server="video.example.com", allowed_chats=["Scoped-Group-ID", ""]
    )),
    "bad_free_response_scalar": validate(config(
        server="video.example.com", free_response_chats="Agent-Room-ID"
    )),
    "bad_free_response_entry": validate(config(
        server="video.example.com", free_response_chats=["Agent-Room-ID", ""]
    )),
    "bad_observe_flag": validate(config(
        server="video.example.com", observe_unmentioned_group_messages="yes"
    )),
    "bad_observe_bool_limit": validate(config(
        server="video.example.com", observe_context_limit=True
    )),
    "bad_observe_low_limit": validate(config(
        server="video.example.com", observe_context_limit=-1
    )),
    "bad_observe_high_limit": validate(config(
        server="video.example.com", observe_context_limit=101
    )),
    "zero_observe_limit": validate(config(
        server="video.example.com", observe_context_limit=0
    )),
}
os.environ["TRUECONF_PASSWORD"] = ""
results["missing_secret"] = validate(config(server="video.example.com"))
print(json.dumps(results))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "valid": True,
        "scheme": False,
        "path": False,
        "low_port": False,
        "high_port": False,
        "bool_port": False,
        "bad_https": False,
        "markdown_mode": False,
        "text_mode": False,
        "bad_tls": False,
        "bad_mode": False,
        "bad_group_chats_scalar": False,
        "bad_group_chats_entry": False,
        "bad_allowed_chats_scalar": False,
        "bad_allowed_chats_entry": False,
        "bad_free_response_scalar": False,
        "bad_free_response_entry": False,
        "bad_observe_flag": False,
        "bad_observe_bool_limit": False,
        "bad_observe_low_limit": False,
        "bad_observe_high_limit": False,
        "zero_observe_limit": True,
        "missing_secret": False,
    }


def test_import_has_no_network_install_logging_or_filesystem_side_effects(
    tmp_path: Path,
) -> None:
    result = run_hermes_probe(
        tmp_path,
        """
import importlib.util
import json
import logging
import socket
import subprocess
import sys
from pathlib import Path

working_dir = Path.cwd()
before_files = sorted(str(path.relative_to(working_dir)) for path in working_dir.rglob("*"))
before_handlers = [id(handler) for handler in logging.getLogger().handlers]
network_calls = []
install_calls = []

def blocked_connect(*args, **kwargs):
    network_calls.append((args, kwargs))
    raise AssertionError("network access during plugin import")

def blocked_process(*args, **kwargs):
    install_calls.append((args, kwargs))
    raise AssertionError("process launch during plugin import")

socket.socket.connect = blocked_connect
subprocess.Popen = blocked_process

plugin_root = Path(__import__("os").environ["TRUECONF_PLUGIN_ROOT"])
spec = importlib.util.spec_from_file_location(
    "trueconf_import_probe",
    plugin_root / "__init__.py",
    submodule_search_locations=[str(plugin_root)],
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

after_files = sorted(str(path.relative_to(working_dir)) for path in working_dir.rglob("*"))
after_handlers = [id(handler) for handler in logging.getLogger().handlers]
print(json.dumps({
    "files_unchanged": after_files == before_files,
    "handlers_unchanged": after_handlers == before_handlers,
    "network_calls": len(network_calls),
    "process_calls": len(install_calls),
    "sdk_imported": "trueconf" in sys.modules,
}))
""",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "files_unchanged": True,
        "handlers_unchanged": True,
        "network_calls": 0,
        "process_calls": 0,
        "sdk_imported": False,
    }


def test_distributed_plugin_passes_install_security_scan(tmp_path: Path) -> None:
    from tools.plugin_guard import (
        format_scan_report,
        scan_plugin,
        should_allow_plugin_install,
    )

    package_dir = tmp_path / "plugin"
    package_dir.mkdir()
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=PROJECT_ROOT,
        )
        .decode()
        .split("\0")
    )
    for relative_path in paths:
        if not relative_path:
            continue
        source_path = PROJECT_ROOT / relative_path
        if not source_path.is_file():
            continue
        destination = package_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)

    result = scan_plugin(
        package_dir,
        source="https://github.com/TrueConf/trueconf-hermes-agent-plugin.git",
    )
    allowed, reason = should_allow_plugin_install(result, force=False)
    assert allowed is True, f"{reason}\n{format_scan_report(result)}"
