# Appended to the profiledef.sh of archiso's releng profile.
iso_name="nexusos"
iso_label="NEXUSOS_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="NexusOS <https://github.com/Ybucaille/NexusOS>"
iso_application="NexusOS live"
install_dir="nexusos"
# zstd instead of xz: the desktop makes the image several times larger and xz
# would dominate the build time.
airootfs_image_tool_options=('-comp' 'zstd' '-Xcompression-level' '15' '-b' '1M')
file_permissions+=(
  ["/usr/local/bin/nexusos-live"]="0:0:755"
  ["/usr/local/bin/nexusos-install"]="0:0:755"
  ["/etc/sudoers.d"]="0:0:750"
  ["/etc/sudoers.d/nexusos-live"]="0:0:440"
)
