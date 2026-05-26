# Nix derivation that produces a directory tree containing the LV2 bundles
# Earshot's analyzer chain needs. Build with:
#
#   nix-build bootstrap.nix -o ./out-bundles
#   cp -rL out-bundles/lib/lv2/* ./       # -L to dereference symlinks
#
# This pulls the LV2 packages from nixpkgs, links the .lv2 directories
# from each package's output into a single tree at $out/lib/lv2/, which
# you can then copy into this directory so the bundles travel with the
# repo (or symlink, if you prefer that the Nix store remains the source
# of truth for the binaries).
{ pkgs ? import <nixpkgs> { } }:

pkgs.symlinkJoin {
  name = "earshot-lv2-bundles";
  paths = with pkgs; [
    x42-plugins        # meters.lv2 — LUFS, true peak, dpm, etc.
    calf               # SpectrumAnalyzer + many more (LV2 + LADSPA + LADSPA UI)
    lsp-plugins        # comp_delay_x2_stereo for the delay tower
  ];

  # The output structure is $out/lib/lv2/<bundle>.lv2/manifest.ttl
  # plus shared .so files. Earshot's loader looks at $LV2_PATH/<bundle>.lv2/manifest.ttl.
}
