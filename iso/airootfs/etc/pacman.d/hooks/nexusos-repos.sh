# remove from airootfs!
# Run by nexusos-repos.hook during the ISO build. pacman and pacman-mirrorlist
# own both files, so they are edited once installed instead of shipped in
# airootfs. archinstall copies them to the systems installed from this image.
# The build fills in the @...@ values.

# The Arch Linux Archive date the nexusos packages were built and tested on,
# not live mirrors: an update moves the date and the repository together.
cat > /etc/pacman.d/mirrorlist <<'END'
# NexusOS follows a dated Arch Linux Archive snapshot.
Server = https://archive.archlinux.org/repos/@SNAPSHOT@/$repo/os/$arch
END

# Above [core], as derivatives do, so a NexusOS build wins over Arch's.
grep -q '^\[nexusos\]' /etc/pacman.conf ||
  sed -i '/^\[core\]$/i [nexusos]\nSigLevel = @SIGLEVEL@\nServer = file:///var/lib/nexusos/repo\n' /etc/pacman.conf
