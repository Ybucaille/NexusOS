# remove from airootfs!
# Run by nexusos-archinstall.hook during the ISO build. archinstall 4.5 reads
# subvolume names from a configuration file as strings and compares them with
# Path("@"): the default layout is never recognised and the snapper setting is
# dropped (its menus build Paths). Reading them as Paths sets snapper up on an
# unattended install too. iso-in-container.sh checks the image got the fix.
sed -i "s/mods.append(cls(entry\['name'\], mountpoint))/mods.append(cls(Path(entry['name']), mountpoint))/" \
  /usr/lib/python3*/site-packages/archinstall/lib/models/device.py
