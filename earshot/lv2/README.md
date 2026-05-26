# Earshot bundled LV2 plugins

This directory is the canonical, deterministic location for the LV2
plugin bundles Earshot's analyzer chain uses. The `earshot_setup_analyzer_chain`
MCP tool prepends this path to `LV2_PATH` before calling `self.carla.load_plugin(URI, LV2)`,
so Carla resolves the plugin URIs from these bundled .lv2 directories regardless
of what the user has installed system-wide.

## What goes here

Each entry below is one `.lv2` bundle directory. Carla looks for `manifest.ttl`
inside each bundle to discover plugin URIs.

| Bundle dir | Provides | Canonical URI |
|---|---|---|
| `meters.lv2/` | x42 EBU R128 meter, peak/dpm, dynamic range | `http://gareus.org/oss/lv2/meters#EBUr128`, `http://gareus.org/oss/lv2/meters#dpm_stereo`, etc. |
| `Calf.lv2/` | Calf SpectrumAnalyzer + others | `http://calf.sourceforge.net/plugins/SpectrumAnalyzer` |
| `lsp-plugins.lv2/` | LSP compensation delay (used as the delay tower) | `http://lsp-plug.in/plugins/lv2/comp_delay_x2_stereo` |

(URIs above are the ones the default setup tool requests. If a bundle provides
a different URI, pass an override via the `plugin_uri_overrides` argument to
`earshot_setup_analyzer_chain`.)

## How to populate this directory

Earshot ships the loader, not the binaries — LV2 plugin builds are
architecture-specific, so committing binaries to git is not portable.
Use one of the following to populate this directory:

### Option A — NixOS (recommended for the project lead's environment)

There's a `bootstrap.nix` derivation in this directory. Run:

    nix-build bootstrap.nix -o ./out-bundles
    cp -r out-bundles/lib/lv2/* ./

This pulls `x42-plugins`, `calf-lv2`, and `lsp-plugins` from nixpkgs, builds them,
and copies the .lv2 bundles into this directory.

### Option B — Debian / Ubuntu

    sudo apt install x42-plugins calf-plugins lsp-plugins
    cp -r /usr/lib/lv2/meters.lv2 ./
    cp -r /usr/lib/lv2/Calf.lv2 ./
    cp -r /usr/lib/lv2/lsp-plugins.lv2 ./

### Option C — Build from source

Clone the upstream repos and build each plugin manually:

- x42-plugins:  https://github.com/x42/meters.lv2
- Calf: https://github.com/calf-studio-gear/calf
- LSP Plugins: https://github.com/sadko4u/lsp-plugins

Copy the built .lv2 directories here.

## Verifying

After populating, run a quick smoke check:

    ls *.lv2/manifest.ttl

You should see at least three `manifest.ttl` files. The `earshot_setup_analyzer_chain`
tool will then load them programmatically and report success/failure per plugin.

## Why bundled URIs aren't asserted as a factual claim

The URIs in the table above are the ones the tool requests by default. They
match upstream conventions for the named projects, but Earshot doesn't
hard-code "these MUST be the URIs" — if a bundle provides a different URI,
the tool's structured failure response will tell you so, and you pass an
override map. The tool stays honest: it tries what's documented, reports
what worked, reports what didn't.
